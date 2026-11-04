#!/usr/bin/env python3
"""Ablate MonoAgens behavior from the database archive: gate replay, prompt budget, decisions.

Read-only harness: it sends no messages, never calls `handle`, and writes no state.
Run from the repo root: `.venv/bin/python scripts/ablate.py --last 100`.
"""


import argparse
import asyncio
import json
import os
import sys
from contextlib import suppress
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ablate_engine import ABLATABLE, classify, decision_shift, prompt_budget, replay_metrics

from monoagens.engine.config import load_config, load_env, load_host, resolve_database_url
from monoagens.engine.db import Database
from monoagens.engine.llm import LLM
from monoagens.engine.memory import build_memory
from monoagens.engine.stores.affect import AffectStore
from monoagens.engine.stores.documents import DocumentStore
from monoagens.engine.stores.history import HistoryStore
from monoagens.engine.stores.skills import SkillStore
from monoagens.engine.stores.style import StyleStore
from monoagens.engine.turn.prompts import CORE_PARTS


def open_stores(config, db, memory) -> dict:
    stores: dict = {"memory": memory}
    with suppress(Exception):
        stores["skills"] = SkillStore(db, config.skills.max_index_chars)
    voice = config.soul.voice
    if voice.enabled and voice.affect.enabled:
        with suppress(Exception):
            stores["affect"] = AffectStore(db, voice.affect, soul=config.soul.persona)
    if voice.enabled and voice.style.enabled:
        with suppress(Exception):
            stores["style"] = StyleStore(db, voice.style, language=config.soul.language)
    return stores


def blocks_for(chat: str, stores: dict):
    memory = stores.get("memory")
    skills = stores.get("skills")
    affect = stores.get("affect")
    style = stores.get("style")

    def build(message) -> dict:
        blocks = dict.fromkeys((part.name for part in CORE_PARTS), "")
        if memory is not None and getattr(memory, "available", False):
            with suppress(Exception):
                blocks["memory"] = asyncio.run(
                    memory.context(
                        person_id=message.sender_id or "",
                        thread_id=chat,
                        query=message.text,
                    )
                )
            with suppress(Exception):
                blocks["actions"] = ""  # ledger is separate; this ablation targets prompt blocks
        if skills is not None:
            with suppress(Exception):
                blocks["skills"] = skills.index()
        if affect is not None:
            with suppress(Exception):
                blocks["topic"] = affect.topic_block(chat)
        if style is not None:
            with suppress(Exception):
                blocks["style"] = style.profile(chat)
            with suppress(Exception):
                blocks["person_style"] = style.person_profile(message.sender_id)
        return blocks

    return build


def report(chat: str, replay: dict, budget: dict, shift: dict | None) -> str:
    lines = [
        f"chat    : {chat}",
        (
            "replay  : "
            f"incoming={replay['incoming']} called={replay['called']} "
            f"replied={replay['replied']} "
            f"false_positive={replay['false_positive']} missed={replay['missed']} "
            f"unprompted_rate={replay['unprompted_rate']:.3f}"
        ),
        (
            "prompt  : "
            f"samples={budget['samples']} before_mean={budget['before_mean']:.0f} "
            f"after_mean={budget['after_mean']:.0f} saved_mean={budget['saved_mean']:.0f}"
        ),
        "dropped : "
        + (", ".join(f"{name}={count}" for name, count in budget["dropped"].items()) or "-"),
    ]
    if shift is not None:
        lines.append(
            "decision: "
            f"samples={shift['samples']} addressed_changed={shift['addressed_changed']} "
            f"act_changed={shift['act_changed']} tone_changed={shift['tone_changed']}"
        )
        for example in shift["examples"]:
            lines.append(f"  {example['text'][:80]!r}: {example['before']} -> {example['after']}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ablate MonoAgens behavior from the database archive."
    )
    parser.add_argument("--config", default=None, help="path to config.yaml")
    parser.add_argument("--chat", default=None, help="chat JID (default: the latest chat)")
    parser.add_argument(
        "--last", type=int, default=200, help="number of archived messages (default 200)"
    )
    parser.add_argument(
        "--llm-sample", type=int, default=0, help="number of messages to replay the LLM decision"
    )
    parser.add_argument(
        "--ablate", default="emotion,vibe,away", help="gate blocks to ablate (emotion,vibe,away)"
    )
    parser.add_argument(
        "--json", type=Path, default=None, help="write the full result to a JSON path"
    )
    args = parser.parse_args()

    names = tuple(name.strip() for name in args.ablate.split(",") if name.strip())
    unknown = [name for name in names if name not in ABLATABLE]
    if unknown:
        raise SystemExit(f"blocks cannot be ablated: {', '.join(unknown)}")
    if args.llm_sample < 0 or args.last <= 0:
        raise SystemExit("--last must be > 0 and --llm-sample must not be negative")

    load_env()
    host, paths = load_host(args.config)
    url = resolve_database_url(host.database)
    if not url:
        raise SystemExit("database URL is empty")
    config = load_config(args.config, require_key=False)
    db = Database(url, pool_size=host.database.pool_size, pool_timeout=host.database.pool_timeout)
    documents = DocumentStore(db, paths, sync=host.sync_documents)
    documents.seed()
    history = HistoryStore(db, config.workspace)
    chat = args.chat or next(iter(history.recent_threads(1)), "")
    if not chat:
        db.dispose()
        raise SystemExit("empty archive: there is no chat to ablate")
    messages = history.recent(chat, args.last)
    if not messages:
        db.dispose()
        raise SystemExit(f"chat {chat} has no messages in the archive")
    rows = classify(messages, bot_name=config.soul.name)
    memory = asyncio.run(build_memory(config, db=db))
    stores = open_stores(config, db, memory)
    try:
        budget = prompt_budget(
            config,
            messages,
            sample=0,
            blocks_for=blocks_for(chat, stores),
        )
        shift = None
        if args.llm_sample > 0:
            if not os.environ.get(config.llm.api_key_env):
                print(
                    f"skipping decision_shift: env {config.llm.api_key_env} is not set",
                    flush=True,
                )
            else:
                shift = asyncio.run(
                    decision_shift(
                        LLM(config),
                        config,
                        rows,
                        sample=args.llm_sample,
                        ablate=names,
                        thread_id=chat,
                        history=messages,
                        affect=stores.get("affect"),
                        direct=not chat.endswith("@g.us"),
                    )
                )
        print(report(chat, replay_metrics(rows), budget, shift))
        if args.json is not None:
            payload = {
                "chat": chat,
                "replay": replay_metrics(rows),
                "prompt_budget": budget,
                "decision_shift": shift,
            }
            text = json.dumps(payload, ensure_ascii=False, indent=2)
            args.json.write_text(text, encoding="utf-8")
            print(f"json    : {args.json}")
    finally:
        asyncio.run(memory.close())
        db.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
