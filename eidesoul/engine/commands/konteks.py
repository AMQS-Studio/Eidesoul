"""Command /konteks: show or refresh the Indonesia (+62) context."""


import time

from ..i18n import t
from ..jobs.currents import refresh
from ..registry import Handler
from ..tools import execute
from . import CommandContext

REFRESH_WORDS = ("refresh", "segarkan", "update", "perbarui")


def _search(ctx: CommandContext):
    specs = {spec.name: spec for spec in ctx.tools}

    async def search(query: str, results: int) -> str:
        if "web_search" not in specs:
            return "error: web_search not allowed for this role"
        tool_ctx = ctx.agent.tool_context(
            ctx.thread_id,
            ctx.sender,
            ctx.roles,
            ctx.gateway,
            request=query,
            person_id=getattr(ctx.inbound, "actor", "") if ctx.inbound else "",
            direct=bool((getattr(ctx.inbound, "hints", None) or {}).get("dm", False)),
        )
        return await execute(
            specs, "web_search", {"query": query, "max_results": int(results)}, tool_ctx
        )

    return search


async def konteks(ctx: CommandContext) -> str:
    lang = ctx.lang
    currents = ctx.agent.currents
    if currents is None:
        return t("command.konteks.failed", lang)
    if ctx.args.strip().lower() in REFRESH_WORDS:
        llm = ctx.agent.llm
        if llm is None:
            return t("command.konteks.failed", lang)
        try:
            app = ctx.agent.config.ambient
            count = await refresh(
                currents,
                _search(ctx),
                llm.research_summary,
                topics=max(4, int(app.context_topics)),
                results=int(app.context_results),
            )
        except (OSError, RuntimeError, ValueError):
            return t("command.konteks.failed", lang)
        block = currents.block(time.time(), 6)
        body = block or t("command.konteks.empty", lang)
        return "\n".join(
            (
                t("command.konteks.header", lang),
                body,
                t("command.konteks.updated", lang, count=count),
            )
        )
    block = currents.block(time.time(), 6)
    return block or t("command.konteks.empty", lang)


COMMAND = Handler(
    name="konteks",
    description="🇮🇩 Refresh the Indonesia (+62) context now.",
    fn=konteks,
    aliases=("context",),
    usage="/konteks [refresh]",
)
