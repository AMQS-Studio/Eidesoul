#!/usr/bin/env python3
"""Replay scripted scenarios against the production turn prompt.

Scenario source: `tests/data/scenarios.json` (turns, per-speaker target, rubric, bad examples).
The prompt is assembled like production: `system_prompt` + `character_block` with patterns from
the packs selected by `soul.style` in `eidesoul/library/patterns/`, then `regulate` holds back
replies that exceed the level. This script never writes to the scenarios or the catalog; its
output is a transcript plus a pass/fail summary in `workspace/replay-<id>.json`.

    python scripts/replay.py --scenario roast_group --limit 4
    python scripts/replay.py --no-regulate --no-judge --limit 6
    python scripts/replay.py --export workspace/replay-all.json
"""


import argparse
import asyncio
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from _paths import safe_path

from eidesoul.engine.access.roles import Role
from eidesoul.engine.character.character import (
    MARKDOWN,
    assistant_tell,
    effective_limits,
    regulate,
)
from eidesoul.engine.character.patterns import load_lexicon, room_register, select_patterns
from eidesoul.engine.config import load_config, load_env
from eidesoul.engine.llm import LLM
from eidesoul.engine.runtime import RuntimeFacts
from eidesoul.engine.tools import ToolContext
from eidesoul.engine.turn.context import (
    AUTHOR_BOT,
    AUTHOR_UNKNOWN,
    Message,
    build_user_content,
)
from eidesoul.engine.turn.prompts import PromptContext, system_prompt
from eidesoul.engine.turn.reply import clean_prose, human_chunks, strip_blocks

SCENARIOS = ROOT / "tests" / "data" / "scenarios.json"
OUT = ROOT / "workspace"
MAX_WINDOW = 20
MAX_BUBBLES = 3
BUBBLE_MAX = 240
TURN_MAX = 400
SHINGLE = 5
JUDGE_SYSTEM = (
    "You score one WhatsApp reply from Eidesoul, an Indonesian persona, against a rubric. "
    "Answer with "
    'one JSON object only: {"scores": {<dimension>: <1-5>}}.\n'
    "Score every dimension in the payload with an integer 1-5; 5 only when the reply is clearly "
    "good at that dimension. Hard fails are measured elsewhere, so do not report them. No prose."
)
CATEGORY_PASS = {
    "serious_dm": "serious_average_min",
    "romance_creative": "romance_creative_average_min",
    "roast_group": "roast_average_min",
}
JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", str(text or "").lower())


def shingles(text: str) -> set[tuple[str, ...]]:
    found = words(text)
    return {tuple(found[index : index + SHINGLE]) for index in range(len(found) - SHINGLE + 1)}


def message(text: str, *, speaker: str, when: datetime, index: int, author: str = AUTHOR_UNKNOWN):
    return Message(
        id=f"replay-{index}",
        sender_name=speaker,
        sender_id="owner" if speaker == "Muqsith" else speaker.lower(),
        sender_address="",
        when=when,
        text=text,
        outgoing=author == AUTHOR_BOT,
        author=author,
    )


def bubbles_of(answer: str) -> list[str]:
    if not answer:
        return []
    found = [clean_prose(part) for part in human_chunks(answer, max_parts=MAX_BUBBLES)]
    return [part for part in found if part]


def hard_fails(
    answer: str, bubbles: list[str], seen: set, language: str = "id"
) -> list[str]:
    """Deterministic rubric metrics: no LLM needed, cannot be talked around."""
    if not str(answer or "").strip():
        return ["empty_reply"]
    fails: list[str] = []
    tell = assistant_tell(language)
    if tell is not None and tell.search(answer):
        fails.append("assistant_tell")
    if MARKDOWN.search(answer):
        fails.append("markdown_list_or_heading")
    if len(bubbles) > MAX_BUBBLES:
        fails.append("too_many_bubbles")
    if any(len(part) > BUBBLE_MAX for part in bubbles) or len(answer) > TURN_MAX:
        fails.append("too_long")
    if shingles(answer) & seen:
        fails.append("repeated_phrase")
    return fails


def judge_payload(sheet: dict, scenario: dict, transcript: list[str], reply: str) -> str:
    dimensions = "\n".join(
        f"- {item['id']}: {item['question']}" for item in sheet["rubric"]["dimensions"]
    )
    fails = "\n".join(f"- {item}" for item in sheet["rubric"]["hard_fails"])
    return (
        f"Scenario: {scenario['title']} ({scenario['chat']}). Goal: {scenario['goal']}\n"
        f"Transcript so far:\n" + "\n".join(transcript[-12:]) + "\n\n"
        f"Reply to judge:\n{reply}\n\nDimensions:\n{dimensions}\n\nHard fails:\n{fails}"
    )


def parse_judge(raw: str) -> dict | None:
    found = JSON_OBJECT.search(str(raw or ""))
    if not found:
        return None
    try:
        body = json.loads(found.group(0))
    except ValueError:
        return None
    scores = body.get("scores") if isinstance(body.get("scores"), dict) else {}
    return {str(key): value for key, value in scores.items() if isinstance(value, (int, float))}


def judge_verdict(sheet: dict, scenario: dict, scores: dict) -> dict:
    """The rubric decides pass/fail; the model only scores each dimension."""
    rubric = sheet.get("rubric") or {}
    limits = rubric.get("pass") or {}
    values = [float(value) for value in scores.values()]
    if not values:
        return {"scores": scores, "verdict": "unavailable", "average": None, "floor": None}
    key = CATEGORY_PASS.get(scenario["id"], "")
    floor = float(limits.get(key) or limits.get("average_min") or 0)
    average = sum(values) / len(values)
    passed = average >= floor and min(values) >= float(limits.get("dimension_min") or 0)
    return {
        "scores": scores,
        "average": round(average, 2),
        "floor": floor,
        "verdict": "pass" if passed else "fail",
    }


async def judge(llm: LLM, sheet: dict, scenario: dict, transcript: list[str], reply: str) -> dict:
    try:
        raw = await llm.complete(
            JUDGE_SYSTEM,
            judge_payload(sheet, scenario, transcript, reply),
            max_tokens=800,
            temperature=0.0,
            session=f"replay-judge-{scenario['id']}",
        )
    except Exception:
        return judge_verdict(sheet, scenario, {})
    return judge_verdict(sheet, scenario, parse_judge(raw) or {})


async def reply_once(
    config, llm: LLM, scenario: dict, window: list, current, transcript: list
) -> dict:
    recent = [entry.text for entry in window[-8:] if entry.text.strip()]
    limits = effective_limits(config.character)
    style = config.soul.style
    lexicon = load_lexicon(style.patterns)
    register = room_register(
        [entry.text for entry in window[-6:] if entry.text.strip()], lexicon
    )
    patterns = select_patterns(
        style.max_patterns,
        register=register,
        limits=limits,
        language=config.soul.language,
        messages=recent,
        packs=style.patterns,
        registers=style.registers,
        lexicon=lexicon,
    )
    context = PromptContext(
        config=config,
        roles=(Role("owner"),),
        runtime=RuntimeFacts(mode="primary", gateways=("replay",), db_label="replay"),
        body_section=(
            "## Chat interface\n- WhatsApp formatting only: *bold*, _italic_. No headings."
        ),
        sender=current.sender_name,
        direct=scenario["chat"] == "dm",
        mode="daily",
        tone="neutral",
        decision_reason="replay turn; reply",
        query=current.text,
        language=config.soul.language,
        limits=limits,
        patterns=patterns,
    )
    system = system_prompt(config, context.roles, context=context)
    content = await build_user_content(
        config,
        f"replay-{'dm' if scenario['chat'] == 'dm' else 'group'}",
        window[-MAX_WINDOW:],
        current,
        chat_name="replay",
    )
    ctx = ToolContext(config, (Role("owner"),), (), thread_id="replay", sender=current.sender_name)
    answer = strip_blocks(
        str(await llm.run(system, content, [], ctx, session=f"replay-{scenario['id']}"))
    ).strip()
    return {
        "limits": limits,
        "register": register,
        "patterns": patterns,
        "answer": answer,
    }


async def run_scenario(
    config, sheet: dict, scenario: dict, *, limit: int | None, use_judge: bool, use_regulate: bool
) -> dict:
    llm = LLM(config)
    window: list = []
    transcript: list[str] = []
    turns_out: list[dict] = []
    seen: set = set()
    now = datetime(2026, 11, 1, 20, 0, 0)
    index = 0
    turns = scenario["turns"][:limit] if limit else scenario["turns"]
    for number, turn in enumerate(turns, start=1):
        index += 1
        current = message(
            turn["text"],
            speaker=turn["speaker"],
            when=now + timedelta(minutes=number * 2),
            index=index,
        )
        window.append(current)
        transcript.append(f"{turn['speaker']}: {turn['text']}")
        result = await reply_once(config, llm, scenario, window, current, transcript)
        answer = result["answer"]
        if answer and use_regulate:
            answer = await regulate(
                llm,
                config,
                answer,
                latest=current.text,
                session=f"replay-regulate-{scenario['id']}",
            )
        parts = bubbles_of(answer)
        visible = "\n".join(parts) or answer
        fails = (
            hard_fails(answer, parts, seen, config.soul.language)
            if answer
            else ["empty_reply"]
        )
        seen |= shingles(answer)
        for part in parts:
            index += 1
            window.append(
                message(
                    part,
                    speaker="Eidesoul",
                    when=now + timedelta(minutes=number * 2, seconds=15),
                    index=index,
                    author=AUTHOR_BOT,
                )
            )
        transcript.append("Eidesoul: " + (visible or "(empty)"))
        judged = (
            await judge(llm, sheet, scenario, transcript, visible)
            if visible and use_judge
            else None
        )
        turns_out.append(
            {
                "turn": number,
                "speaker": turn["speaker"],
                "text": turn["text"],
                "expect": turn.get("expect") or [],
                "limits": list(result["limits"]),
                "register": result["register"],
                "patterns": result["patterns"],
                "reply": visible,
                "hard_fail": fails,
                "judge": judged,
            }
        )
        flag = "FAIL " + ",".join(fails) if fails else "ok"
        print(f"  {number:>2} {turn['speaker']:<8} {flag:<28} {visible[:70]}")
    hard = sum(len(turn["hard_fail"]) for turn in turns_out)
    return {
        "scenario": scenario["id"],
        "title": scenario["title"],
        "chat": scenario["chat"],
        "turns": turns_out,
        "summary": {
            "turns": len(turns_out),
            "hard_fail": hard,
            "curse_lines": sum(
                1
                for turn in turns_out
                if re.search(r"\b(jancok|asu|goblok|cok)\b", turn["reply"].lower())
            ),
        },
    }


async def main_async(args) -> int:
    load_env()
    config = load_config("config.yaml")
    sheet = json.loads(SCENARIOS.read_text(encoding="utf-8"))
    chosen = [
        item for item in sheet["scenarios"] if not args.scenario or item["id"] == args.scenario
    ]
    if not chosen:
        print(f"unknown scenario: {args.scenario}", file=sys.stderr)
        return 2
    reports = []
    for scenario in chosen:
        print(f"scenario {scenario['id']} ({len(scenario['turns'])} turns)")
        report = await run_scenario(
            config,
            sheet,
            scenario,
            limit=args.limit,
            use_judge=not args.no_judge,
            use_regulate=not args.no_regulate,
        )
        target = (
            safe_path(OUT.parent, Path(args.export))
            if args.export
            else OUT / f"replay-{scenario['id']}.json"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        target.write_text(payload, encoding="utf-8")
        summary = report["summary"]
        print(f"  -> {target} hard_fail={summary['hard_fail']} curses={summary['curse_lines']}")
        reports.append(report)
    total = sum(report["summary"]["hard_fail"] for report in reports)
    print(f"summary scenarios={len(reports)} hard_fail={total}")
    return 1 if total else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay Eidesoul scenarios")
    parser.add_argument("--scenario", help="only one scenario id")
    parser.add_argument("--limit", type=int, help="cap the number of turns per scenario")
    parser.add_argument(
        "--export", help="write the report to this path instead of workspace/replay-<id>.json"
    )
    parser.add_argument("--no-judge", action="store_true", help="skip the per-turn LLM judging")
    parser.add_argument("--no-regulate", action="store_true", help="skip the level check")
    return asyncio.run(main_async(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
