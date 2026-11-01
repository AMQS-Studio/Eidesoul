"""Command /gender: manual target gender for toxicity rules and adult memes."""

from __future__ import annotations

from ..i18n import t
from ..registry import Handler
from ..target import parse_gender
from . import CommandContext


async def gender(ctx: CommandContext) -> str:
    lang = ctx.lang
    parts = ctx.args.split()
    if len(parts) < 2:
        return t("command.gender.usage", lang)
    label = parse_gender(parts[-1])
    if label is None:
        return t("command.gender.usage", lang)
    name = " ".join(parts[:-1]).strip()
    affect = ctx.agent.affect
    person_id = affect.person_by_name(ctx.chat_id, name) if affect is not None else None
    if not person_id:
        return t("command.gender.unknown", lang, name=name)
    affect.set_gender(person_id, label, 1.0, "manual")
    return t("command.gender.saved", lang, name=name, gender=label)


COMMAND = Handler(
    name="gender",
    description="🧭 Set the target gender for toxicity rules (/gender <name> male|female|unknown).",
    fn=gender,
    usage="/gender <name> <male|female|unknown>",
)
