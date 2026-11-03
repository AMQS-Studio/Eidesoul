"""Command /whoami: sender identity, role, chat, and surface."""


from ..i18n import t
from ..registry import Handler
from . import CommandContext


def _chat_label(ctx: CommandContext, lang: str) -> str:
    kind = t(f"command.whoami.{_kind_key(ctx)}", lang)
    name = ctx.gateway.chat_name(ctx.thread_id) if ctx.gateway is not None else ""
    if name:
        return t("command.whoami.chat_label", lang, kind=kind, name=name, id=ctx.thread_id)
    return t("command.whoami.chat_plain", lang, kind=kind, id=ctx.thread_id)


def _kind_key(ctx: CommandContext) -> str:
    return "dm" if ctx.direct else "group"


async def whoami(ctx: CommandContext) -> str:
    lang = ctx.lang
    roles = ", ".join(role.name.capitalize() for role in ctx.roles) or "-"
    person_id = ctx.inbound.actor if ctx.inbound is not None else ""
    person = ctx.agent.identity.person(person_id) or {}
    lines = [
        t("command.whoami.user", lang, who=ctx.sender or ctx.thread_id),
        t("command.whoami.role", lang, roles=roles),
        t(
            "command.whoami.identity",
            lang,
            name=person.get("name") or "-",
            id=person_id or "-",
        ),
        t("command.whoami.chat", lang, chat=_chat_label(ctx, lang)),
        t(
            "command.whoami.surface",
            lang,
            surface=ctx.gateway.name if ctx.gateway is not None else "-",
        ),
    ]
    handles = sorted(ctx.gateway.self_handles()) if ctx.gateway is not None else []
    if handles:
        lines.append(
            t(
                "command.whoami.bot",
                lang,
                name=ctx.agent.config.soul.name,
                handles=", ".join(handles),
            )
        )
    return "\n".join(lines)


COMMAND = Handler(
    name="whoami",
    description="👤 Your identity, role, chat, and surface.",
    fn=whoami,
    aliases=("id",),
)
