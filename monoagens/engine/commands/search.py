"""Command /cari: search old messages in this chat archive without LLM."""


from ..i18n import t
from ..registry import Handler
from . import CommandContext

MAX_RESULTS = 10
TEXT_LIMIT = 180


def _clip(text: str) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= TEXT_LIMIT else flat[: TEXT_LIMIT - 1] + "…"


def _line(row, lang: str) -> str:
    missing = "attachment" if row.media is not None else "empty"
    text = _clip(row.text) or t(f"command.search.{missing}", lang)
    who = row.sender_name or row.sender_id or "?"
    stamp = row.when.strftime("%Y-%m-%d %H:%M") if row.when else "?"
    return t("command.search.line", lang, stamp=stamp, who=who, text=text)


async def search(ctx: CommandContext) -> str:
    lang = ctx.lang
    query = ctx.args.strip()
    if not query:
        return t("command.search.usage", lang)
    store = ctx.agent.history
    if store is None:
        return t("command.search.unavailable", lang)
    try:
        rows = store.search(ctx.thread_id, query, limit=MAX_RESULTS)
    except Exception:
        return t("command.search.failed", lang)
    if not rows:
        return t("command.search.none", lang, query=query)
    lines = [t("command.search.head", lang, query=query)]
    lines.extend(_line(row, lang) for row in rows)
    return "\n".join(lines)


COMMAND = Handler(
    name="search",
    description="Search old messages in this chat archive (no LLM).",
    fn=search,
    aliases=(),
    usage="<word>",
)
