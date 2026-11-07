"""Command /thinking: inspect or switch this body's raw reasoning stream (ADR-0010 §4.1).

The switch has two halves: the live body renders (or stops rendering) the stream at once, and
config.yaml keeps the operator's choice for the next boot. A missing role or an absent body is
refused in the catalog's language, never with an internal string.
"""


from .. import io
from ..access.roles import PROTECTED, privileged
from ..i18n import t
from ..registry import Handler
from ..tools.settings import write_gateway_flag
from ..turn.progress import apply_gateway_flag, progress_flag
from . import CommandContext

FLAG = "show_thinking"
KEY = "thinking"


def _state(lang: str, enabled: bool) -> str:
    return t("common.on" if enabled else "common.off", lang)


async def toggle(ctx: CommandContext, flag: str, key: str) -> str:
    """Shared body of `/thinking` and `/steps`: report, then persist and apply one switch."""
    lang = ctx.lang
    if not any(role.name == PROTECTED or privileged(role) for role in ctx.roles):
        return t(f"command.{key}.denied", lang)
    gateway = ctx.gateway
    name = str(getattr(gateway, "name", "") or "")
    if gateway is None or not name:
        return t(f"command.{key}.no_gateway", lang)
    raw = ctx.args.strip().lower()
    enabled_now = progress_flag(gateway, flag)
    if not raw:
        return t(
            f"command.{key}.current",
            lang,
            state=_state(lang, enabled_now),
            gateway=name,
        )
    if raw not in ("on", "off"):
        return t(f"command.{key}.usage", lang)
    enabled = raw == "on"
    await io.run(write_gateway_flag, ctx.agent, name, flag, enabled)
    apply_gateway_flag(gateway, flag, enabled)
    return t(f"command.{key}.set", lang, state=_state(lang, enabled), gateway=name)


async def thinking_command(ctx: CommandContext) -> str:
    return await toggle(ctx, FLAG, KEY)


COMMAND = Handler(
    name="thinking",
    description="Show or switch the raw reasoning stream of this gateway (administrator).",
    fn=thinking_command,
    usage="[on|off]",
)
