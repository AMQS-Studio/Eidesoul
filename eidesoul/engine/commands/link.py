"""Command /link: attach another gateway account to the same person."""


import re
import time

from ..access.identity import mask_address
from ..i18n import t
from ..registry import Handler
from ..runtime import human_duration
from . import CommandContext

CODE = re.compile(r"^[A-Za-z0-9]{6}$")
CODE_MINUTES = 10
CODE_ATTEMPTS = 5


def _enabled(ctx: CommandContext) -> list[str]:
    settings = ctx.agent.config.gateways
    return sorted(name for name, item in settings.items() if item.enabled)


def _status(ctx: CommandContext) -> str:
    lang = ctx.lang
    identity = ctx.agent.identity
    person = ctx.inbound.actor if ctx.inbound is not None else ""
    row = identity.person(person) or {}
    linked = {str(item["gateway"]): item for item in identity.links(person)}
    now = time.time()
    lines = [t("command.link.title", lang, name=row.get("name") or "-", id=person or "-")]
    for gateway in _enabled(ctx):
        found = linked.get(gateway)
        if found is None:
            lines.append(t("command.link.unlinked", lang, gateway=gateway))
            continue
        lines.append(
            t(
                "command.link.linked",
                lang,
                gateway=gateway,
                address=mask_address(str(found["address"])),
                seen=human_duration(now - float(found["seen_at"] or 0.0)),
            )
        )
    lines.extend([t("command.link.hint", lang), t("command.link.example", lang)])
    return "\n".join(lines)


async def link(ctx: CommandContext) -> str:
    lang = ctx.lang
    inbound = ctx.inbound
    address = ""
    if inbound is not None:
        address = inbound.payload.sender_id or inbound.payload.sender_address
    if inbound is None or ctx.gateway is None or not address:
        return t("command.link.no_gateway", lang)
    identity = ctx.agent.identity
    person = inbound.actor
    args = ctx.args.strip()
    if not args:
        return _status(ctx)
    if CODE.fullmatch(args):
        code = args.upper()
        target = identity.code_target(code)
        status, merged = identity.finish_code(
            code=code,
            person_id=person,
            gateway=ctx.gateway.name,
            address=address,
        )
        if merged and target and ctx.agent.style is not None:
            ctx.agent.style.merge_people(merged, target)
        if status != "ok":
            return t(f"command.link.{status}", lang, gateway=ctx.gateway.name)
        owner = target or person
        name = (identity.person(owner) or {}).get("name") or "-"
        return t(
            "command.link.done",
            lang,
            gateway=ctx.gateway.name,
            address=mask_address(address),
            name=name,
            id=owner,
        )
    names = _enabled(ctx)
    gateway = args.casefold()
    if gateway not in names:
        return t(
            "command.link.unknown_gateway", lang, name=args, names=", ".join(names) or "-"
        )
    code = identity.start_code(person, gateway)
    return t(
        "command.link.start",
        lang,
        gateway=gateway,
        code=code,
        minutes=CODE_MINUTES,
        attempts=CODE_ATTEMPTS,
    )


COMMAND = Handler(
    name="link",
    description="🔗 Attach another gateway account to this identity.",
    fn=link,
    aliases=("tautkan",),
    usage="[<gateway> | <code>]",
)
