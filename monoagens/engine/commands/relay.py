"""Command /relay: the content owner manages who may forward an answer (ADR-0023 §4.5).

A grant is per target scope and expires; it never carries message content, only the decision, its
mode, and the room it covers. `/relay revoke` stops the next report; one already sent stays sent.
"""


import shlex
import time

from ..access.relay import DEFAULT_EXPIRES_DAYS, MODES, RelayRefused, RelayStore
from ..i18n import t
from ..registry import Handler
from . import CommandContext

USAGE_MODES = ", ".join(MODES)
DAY_SECONDS = 86400.0


def _until(value: float | None) -> str:
    return (
        time.strftime("%Y-%m-%d %H:%M", time.gmtime(float(value))) if value is not None else "-"
    )


def _rows(grants) -> str:
    return "\n".join(
        f"- {grant.to_person} · {grant.mode} · {grant.scope_ref or '-'}"
        f" · until {_until(grant.expires_at)}"
        for grant in grants
    )


def _days(value: str) -> float | None:
    if not value:
        return DEFAULT_EXPIRES_DAYS
    if value.lower() in {"never", "forever", "0"}:
        return None
    found = float(value)
    if found <= 0:
        raise ValueError("days must be positive")
    return found


def _grant(store: RelayStore, ctx: CommandContext, parts: list[str]) -> str:
    lang = ctx.lang
    if len(parts) < 2:
        return t("command.relay.usage", lang)
    mode = parts[2].lower() if len(parts) > 2 else "summary"
    scope = parts[3] if len(parts) > 3 else str(ctx.thread_id)
    try:
        days = _days(parts[4] if len(parts) > 4 else "")
        grant = store.grant(
            from_person=str(ctx.sender),
            to_person=parts[1],
            by=str(ctx.sender),
            mode=mode,
            scope_ref=scope,
            days=days,
        )
    except RelayRefused as exc:
        return t("command.relay.refused", lang, reason=str(exc))
    except ValueError as exc:
        return t("command.relay.refused", lang, reason=str(exc))
    return t(
        "command.relay.granted",
        lang,
        to=grant.to_person,
        scope=grant.scope_ref or "-",
        mode=grant.mode,
        until=_until(grant.expires_at),
    )


def _revoke(store: RelayStore, ctx: CommandContext, parts: list[str]) -> str:
    lang = ctx.lang
    if len(parts) < 2:
        return t("command.relay.usage", lang)
    scope = parts[2] if len(parts) > 2 else str(ctx.thread_id)
    try:
        store.revoke(
            task_id=f"scope:{scope}",
            from_person=str(ctx.sender),
            to_person=parts[1],
            by=str(ctx.sender),
        )
    except RelayRefused as exc:
        return t("command.relay.refused", lang, reason=str(exc))
    return t("command.relay.revoked", lang, to=parts[1], scope=scope)


async def relay(ctx: CommandContext) -> str:
    lang = ctx.lang
    agent = ctx.agent
    store = RelayStore(agent.db)
    parts = shlex.split(str(ctx.args or "").strip())
    action = parts[0].lower() if parts else "list"
    if action == "grant":
        return _grant(store, ctx, parts)
    if action == "revoke":
        return _revoke(store, ctx, parts)
    if action != "list":
        return t("command.relay.usage", lang)
    grants = store.listing(from_person=str(ctx.sender))
    if not grants:
        return t("command.relay.empty", lang)
    return t("command.relay.list", lang, rows=_rows(grants))


COMMAND = Handler(
    name="relay",
    description=(
        "Manage relay grants: let another room carry your answer for one scope, list them, "
        f"or revoke one. Modes: {USAGE_MODES}."
    ),
    usage=(
        "/relay list | /relay grant <person> [mode] [scope] [days]"
        " | /relay revoke <person> [scope]"
    ),
    fn=relay,
)
