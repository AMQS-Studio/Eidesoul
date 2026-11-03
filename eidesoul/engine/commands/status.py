"""Command /status: one operator card built from real rows (ADR-0019 §4.5).

Raw numbers are for the protected role only; every other role receives rough buckets. A
counter whose table is empty or unreadable reports `0`, never an invented value. Message
content, memory text, the DSN, and keys never appear here (ADR-0015 §4.7).
"""

from __future__ import annotations

import time
from statistics import median

from sqlalchemy import text

from .. import db as database
from ..i18n import t
from ..lexicon import warnings as lexicon_warnings
from ..registry import Handler
from ..roles import PROTECTED
from ..runtime import human_duration
from . import CommandContext

WINDOW_SECONDS = 86400


def _ago(stamp: float, lang: str) -> str:
    if not stamp:
        return "-"
    return t("command.status.ago", lang, duration=human_duration(time.time() - float(stamp)))


def _chat_label(ctx: CommandContext, lang: str) -> str:
    key = "chat_dm" if ctx.direct else "chat_group"
    kind = t(f"command.status.{key}", lang)
    name = ctx.gateway.chat_name(ctx.thread_id) if ctx.gateway is not None else ""
    label = t("command.status.chat_label", lang, kind=kind, name=name) if name else kind
    return t("command.status.chat_roles", lang, label=label, roles=ctx.role_names())


def _last_change(agent, lang: str) -> str:
    from sqlalchemy import select

    from ..schema import config_changes

    try:
        found = agent.db.fetch_one(
            select(config_changes).order_by(config_changes.c.id.desc()).limit(1)
        )
    except Exception:
        return "-"
    if not found:
        return "-"
    return t(
        "command.status.changed",
        lang,
        ago=_ago(found["ts"], lang),
        actor=found["actor"] or "-",
    )


def _is_administrator(roles) -> bool:
    return any(role.name == PROTECTED or role.protected for role in roles)


def percentile(values: list[float], share: float) -> float:
    """Nearest-rank percentile; an empty window reports 0 rather than a guess."""
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    index = min(len(ordered) - 1, max(0, int(round(share * (len(ordered) - 1)))))
    return ordered[index]


def latency(values: list[float]) -> tuple[int, int, float]:
    """`(p50, p95, mean)` in milliseconds from `turns` rows (ADR-0019 §4.4)."""
    if not values:
        return 0, 0, 0.0
    return (
        int(round(percentile(values, 0.50))),
        int(round(percentile(values, 0.95))),
        float(median(values)),
    )


def _turn_counts(agent, lang: str) -> str:
    counts = {"received": 0, "running": 0, "delivered": 0, "interrupted": 0, "expired": 0}
    try:
        rows = agent.db.fetch_all(
            text("SELECT status, count(*) AS count FROM turns GROUP BY status")
        )
    except Exception:
        rows = []
    for row in rows:
        status = str(row["status"])
        if status in counts:
            counts[status] = int(row["count"])
        elif status == "sending":
            counts["running"] += int(row["count"])
    return t("command.status.turns", lang, **counts)


def _turn_latency(agent, now: float) -> list[float]:
    window = int(agent.config.metrics.window_seconds)
    try:
        rows = agent.db.fetch_all(
            text(
                "SELECT created_at, updated_at FROM turns "
                "WHERE status = 'delivered' AND updated_at >= :since"
            ).bindparams(since=now - window)
        )
    except Exception:
        return []
    return [
        max(0.0, (float(row["updated_at"]) - float(row["created_at"])) * 1000.0)
        for row in rows
        if row["created_at"] is not None and row["updated_at"] is not None
    ]


def _cost(agent, now: float) -> dict[str, int]:
    try:
        row = agent.db.fetch_one(
            text(
                "SELECT count(*) AS calls, "
                "coalesce(sum(tokens_in), 0) AS tokens_in, "
                "coalesce(sum(tokens_out), 0) AS tokens_out, "
                "count(DISTINCT turn_id) AS turns "
                "FROM llm_calls WHERE ts >= :since"
            ),
            {"since": now - WINDOW_SECONDS},
        )
    except Exception:
        row = None
    if not row:
        return {"calls": 0, "tokens_in": 0, "tokens_out": 0, "turns": 0, "per_turn": 0}
    tokens = int(row.get("tokens_in") or 0) + int(row.get("tokens_out") or 0)
    turns = int(row.get("turns") or 0)
    return {
        "calls": int(row.get("calls") or 0),
        "tokens_in": int(row.get("tokens_in") or 0),
        "tokens_out": int(row.get("tokens_out") or 0),
        "turns": turns,
        "per_turn": int(tokens / turns) if turns else 0,
    }


def _corpus_notices(agent, now: float) -> int:
    try:
        row = agent.db.fetch_one(
            text(
                "SELECT count(*) AS count FROM messages WHERE ts >= :since "
                "AND coalesce(data->>'kind', '') LIKE 'notice:%'"
            ),
            {"since": now - WINDOW_SECONDS},
        )
    except Exception:
        return 0
    return int(row["count"] or 0) if row else 0


def _generation(agent) -> str:
    lease = getattr(agent, "lease", None)
    value = getattr(lease, "generation", None)
    return str(int(value)) if isinstance(value, int) else "-"


def _rough(ctx: CommandContext, agent, lang: str, now: float) -> str:
    turns = len(_turn_latency(agent, now))
    queue = int((getattr(agent.runtime, "cognition", {}) or {}).get("queue.queued", 0))
    activity = "quiet" if turns == 0 else "steady" if turns < 20 else "busy"
    depth = "empty" if queue == 0 else "short" if queue < 100 else "long"
    return "\n".join(
        [
            t("command.status.title", lang),
            t("command.status.mode", lang, value=agent.runtime.mode),
            t("command.status.uptime", lang, value=agent.runtime.uptime),
            t("command.status.rough", lang, turns=activity, queue=depth),
        ]
    )


async def status(ctx: CommandContext) -> str:
    agent = ctx.agent
    now = time.time()
    if not _is_administrator(ctx.roles):
        return _rough(ctx, agent, ctx.lang, now)
    lines = [
        *_head_lines(ctx, agent, ctx.lang),
        *_metrics_lines(agent, ctx.lang, now),
        *_body_lines(ctx, agent, ctx.lang, now),
        *_file_lines(ctx, agent, ctx.lang),
    ]
    return "\n".join(lines)


def _head_lines(ctx: CommandContext, agent, lang: str) -> list[str]:
    config = agent.config
    runtime = agent.runtime
    return [
        t("command.status.title", lang),
        t("command.status.mode", lang, value=runtime.mode),
        t("command.status.generation", lang, value=_generation(agent)),
        t("command.status.lease", lang, value=runtime.lease_label()),
        t("command.status.uptime", lang, value=runtime.uptime),
        t("command.status.surface", lang, value=runtime.surface()),
        t("command.status.model", lang, value=config.llm.model),
        t("command.status.database", lang, value=agent.db.label),
        t("command.status.schema", lang, value=database.SCHEMA_VERSION),
        t("command.status.chat", lang, value=_chat_label(ctx, lang)),
        _turn_counts(agent, lang),
        t(
            "command.status.timers",
            lang,
            timeout=int(runtime.turn_timeout_total),
            standby=int(runtime.standby_refused),
            notices=int(runtime.notice_failed),
        ),
    ]


def _metrics_lines(agent, lang: str, now: float) -> list[str]:
    if not agent.config.metrics.enabled:
        return []
    samples = _turn_latency(agent, now)
    p50, p95, _mean = latency(samples)
    cost = _cost(agent, now)
    return [
        t("command.status.latency", lang, p50=p50, p95=p95, turns=len(samples)),
        t(
            "command.status.cost",
            lang,
            calls=cost["calls"],
            tokens_in=cost["tokens_in"],
            tokens_out=cost["tokens_out"],
            per_turn=cost["per_turn"],
        ),
    ]


def _body_lines(ctx: CommandContext, agent, lang: str, now: float) -> list[str]:
    from ..automation import automation_counters
    from ..memory import memory_status

    lines: list[str] = []
    store = agent.history
    if store is not None:
        try:
            total = store.message_count()
            window = len(agent.windows.get(ctx.thread_id))
            lines.append(t("command.status.archive", lang, total=total, window=window))
            lines.append(t("command.status.search", lang, value=store.search_backend()))
        except Exception:
            lines.append(t("command.status.archive_unreadable", lang))
    cognition = getattr(agent.runtime, "cognition", {}) or {}
    if cognition:
        lines.append(
            t(
                "command.status.cognition",
                lang,
                queued=cognition.get("queue.queued", 0),
                claimed=cognition.get("queue.claimed", 0),
                done=cognition.get("queue.done", 0),
                dead=cognition.get("queue.dead", 0),
                age=cognition.get("queue.queued_age_seconds", 0),
            )
        )
    automation = automation_counters(agent, now)
    lines.append(
        t(
            "command.status.automation",
            lang,
            active=automation.get("active", 0),
            runs=automation.get("runs_total", 0),
            runs_today=automation.get("runs_today", 0),
            queued=automation.get("queued", 0),
            missed=automation.get("missed", 0),
            denied=automation.get("denied", 0),
        )
    )
    warnings = lexicon_warnings()
    lines.append(
        t(
            "command.status.lexicon",
            lang,
            count=len(warnings),
            detail=(": " + warnings[0][:120]) if warnings else "",
        )
    )
    lines.append(t("command.status.corpus", lang, count=_corpus_notices(agent, now)))
    lines.append(t("command.status.memory", lang, value=memory_status(agent.memory)))
    backlog = _memory_backlog(agent)
    if backlog:
        lines.append(
            t(
                "command.status.memory_backlog",
                lang,
                pending=backlog.get("pending", 0),
                staged=backlog.get("staged", 0),
            )
        )
    lines.append(t("command.status.approvals", lang, value=agent.approvals.count_pending()))
    if agent.runtime.notice_failed:
        lines.append(
            t(
                "command.status.notice_failed",
                lang,
                count=agent.runtime.notice_failed,
                error=agent.runtime.last_notice_error or "-",
            )
        )
    skills = len(agent.skills.names()) if agent.skills is not None else 0
    lines.append(t("command.status.skills", lang, value=skills))
    people = 0
    if agent.affect is not None:
        try:
            people = int(agent.affect.stats().get("people", 0))
        except Exception:
            people = 0
    lines.append(t("command.status.people", lang, people=people, actions=agent.ledger.count()))
    return lines


def _file_lines(ctx: CommandContext, agent, lang: str) -> list[str]:
    config = agent.config
    soul = config.soul
    privacy = config.memory.privacy
    return [
        t(
            "command.status.soul",
            lang,
            chars=len(soul.persona),
            mirror=soul.source or "-",
            language=soul.language,
        ),
        t("command.status.language", lang, language=soul.language),
        t(
            "command.status.privacy",
            lang,
            enabled=privacy.enabled,
            auditor=privacy.auditor,
        ),
        t("command.status.engine", lang, path=config.paths.config),
        t(
            "command.status.config",
            lang,
            path=config.paths.config,
            roles=config.paths.roles,
            change=_last_change(agent, lang),
        ),
    ]


def _memory_backlog(agent) -> dict[str, int]:
    backlog = getattr(agent.memory, "backlog", None)
    if backlog is None:
        return {}
    try:
        found = backlog()
    except Exception:
        return {}
    return found if isinstance(found, dict) else {}


COMMAND = Handler(
    name="status",
    description="⚙️ Summary of mode, lease, model, archive, memory, approvals, and config.",
    fn=status,
)
