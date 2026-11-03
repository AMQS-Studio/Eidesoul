"""Command /memori: add/search/list/delete person, thread, or global memory (no LLM)."""


import re

from ..i18n import t
from ..memory import MemoryHit
from ..memory.privacy import PrivacyPolicy, clamp_level
from ..registry import Handler
from . import CommandContext

SCOPES = ("person", "thread", "global")
ID = re.compile(r"^#?[0-9a-f]{4,32}(?:-[0-9a-f]{4,12}){0,4}$", re.IGNORECASE)


SCOPE_KEYS = {"person": "scope_person", "thread": "scope_chat", "global": "scope_global"}


def _line(hit: MemoryHit, lang: str) -> str:
    mark = "" if hit.confirmed else "?"
    if str(hit.metadata.get("dispute_state") or "") == "open":
        mark += "!"
    scope = t(f"command.memori.{SCOPE_KEYS[hit.scope]}", lang)
    return t(
        "command.memori.line", lang, scope=scope, level=hit.level, mark=mark, text=hit.text
    )


def _scope(raw: str) -> tuple[str, str]:
    head, _, rest = raw.partition(" ")
    if head.lower() in SCOPES:
        return head.lower(), rest.strip()
    return "person", raw.strip()


async def _memori(ctx: CommandContext) -> str:
    lang = ctx.lang
    memory = ctx.agent.memory
    if memory is None or not getattr(memory, "available", False):
        reason = getattr(memory, "reason", "unavailable")
        return t("command.memori.unavailable", lang, reason=reason)
    config = ctx.agent.config.memory.privacy
    scope, raw = _scope(ctx.args.strip())
    inbound = ctx.inbound
    requester = (inbound.actor if inbound is not None else "") or ctx.sender
    direct = bool(inbound is not None and inbound.hints.get("dm"))
    memory_admin = any(role.memory_admin for role in ctx.roles)
    if scope == "person" and not requester:
        return t("command.memori.no_sender", lang)
    if scope == "thread" and not ctx.thread_id:
        return t("command.memori.no_chat", lang)
    action, _, rest = raw.partition(" ")
    action = action.lower()
    thread_id = ctx.thread_id if scope == "thread" else ""
    person_id = requester if scope == "person" else ""
    policy = PrivacyPolicy(config)

    if not raw or action in {"daftar", "list"}:
        rows = await memory.list(
            scope=scope, person_id=person_id, thread_id=thread_id, limit=50
        )
        shown = [row for row in rows if policy.show(row, direct=direct)]
        label = t(f"command.memori.{SCOPE_KEYS[scope]}", lang)
        return (
            "\n".join(
                [t("command.memori.list_head", lang, scope=label), *(_line(row, lang) for row in shown)]
            )
            if shown
            else t("command.memori.none", lang)
        )

    if action in {"cari", "search"}:
        rows = await memory.search(
            rest.strip(),
            person_id=person_id,
            thread_id=thread_id,
            scopes=(scope,),
            limit=50,
        )
        shown = [row for row in rows if policy.show(row, direct=direct)]
        return (
            "\n".join([t("command.memori.results", lang), *(_line(row, lang) for row in shown)])
            if shown
            else t("command.memori.no_match", lang)
        )

    if action in {"hapus", "forget", "lupakan"}:
        if scope == "global" and not memory_admin:
            return t("command.memori.global_denied", lang)
        target = rest.strip()
        if target.isdigit() or ID.match(target):
            removed = await memory.forget(
                target,
                requester_id=requester,
                thread_id=ctx.thread_id,
                allow_global=memory_admin,
            )
            return (
                t("command.memori.deleted_one", lang)
                if removed
                else t("command.memori.no_delete_own", lang)
            )
        rows = await memory.search(
            target, person_id=person_id, thread_id=thread_id, scopes=(scope,), limit=50
        )
        removed = 0
        for row in rows:
            if not policy.show(row, direct=direct):
                continue
            if await memory.forget(
                row.id,
                requester_id=requester,
                thread_id=ctx.thread_id,
                allow_global=memory_admin,
            ):
                removed += 1
        return (
            t("command.memori.deleted_many", lang, count=removed)
            if removed
            else t("command.memori.no_delete", lang)
        )

    text = (rest if action in {"tambah", "add", "simpan"} else raw).strip()
    if not text:
        return t("command.memori.usage", lang)
    if scope == "global" and not memory_admin:
        return t("command.memori.global_denied", lang)
    try:
        ids = await memory.add(
            text,
            scope=scope,
            kind="note",
            person_id=person_id,
            thread_id=thread_id,
            source="command",
            level=clamp_level(config.default_level, 2),
            owner_id=requester,
            subject_id=requester,
            confirmed=True,
        )
    except ValueError as exc:
        return f"error: {exc}"
    if not ids:
        return t("command.memori.not_saved", lang)
    return t(
        "command.memori.saved",
        lang,
        id=ids[0],
        scope=scope,
        level=config.default_level,
    )


COMMAND = Handler(
    name="memori",
    description="🧠 Add, search, list, or delete person/chat/global memories.",
    fn=_memori,
    aliases=("memory", "ingat"),
    usage="[person|chat|global] [add|search|delete|list] <text>",
)
