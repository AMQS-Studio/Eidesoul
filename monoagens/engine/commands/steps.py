"""Command /steps: inspect or switch this body's tool-step stream (ADR-0010 §4.1).

The switch behaves exactly like `/thinking`: the live body applies it at once and config.yaml
keeps it for the next boot. The shared body lives in `thinking.py`, next to the first caller.
"""


from ..registry import Handler
from . import CommandContext
from .thinking import toggle

FLAG = "show_steps"
KEY = "steps"


async def steps_command(ctx: CommandContext) -> str:
    return await toggle(ctx, FLAG, KEY)


COMMAND = Handler(
    name="steps",
    description="Show or switch the tool-step stream of this gateway (administrator).",
    fn=steps_command,
    usage="[on|off]",
)
