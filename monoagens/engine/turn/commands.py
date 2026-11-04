"""Command dispatch preparation: the leading-mention strip, the lookup, and the context.

ADR-0037 §4.3: the command door's front half moves here; the handlers themselves stay in
`commands/` and the registry stays in `registry.py`. The service never imports `agent.py`; the
host is the `Agent` it is bound to.
"""


import contextlib

from ..access.permissions import command_allowed, commands_for, tools_for
from ..access.roles import Role
from ..commands import CommandContext
from ..i18n import t
from ..inbound import GatewayCore, Percept, prompt_section
from .prompts import CORE_PARTS, PromptContext, system_prompt

MENTION_HEAD_RE = r"^@([^\s@]+)\s*"


def command_body(text: str, bot_name: str, handles: set[str]) -> str:
    """Strip a leading bot mention: '@MonoAgens /mood' -> '/mood'."""
    import re

    body = str(text or "").lstrip()
    pattern = re.compile(MENTION_HEAD_RE)
    match = pattern.match(body)
    while match is not None:
        handle = match.group(1).lstrip("+")
        digits = "".join(ch for ch in handle if ch.isdigit()) or handle.lower()
        if handle.casefold() != str(bot_name or "").casefold() and digits not in handles:
            break
        body = body[match.end() :]
        match = pattern.match(body)
    return body


class TurnCommands:
    """Command dispatch: the registry/recipe lookup, the recipe turn, and the command context."""

    def __init__(self, agent) -> None:
        self.agent = agent

    def __getattr__(self, name: str):
        return getattr(self.agent, name)

    async def run(
        self,
        name: str,
        args: str,
        thread_id: str,
        sender: str,
        roles: tuple[Role, ...],
        gateway: GatewayCore,
        inbound: Percept,
    ) -> str:
        if not name:
            return t("agent.help_hint", self.soul.language)
        handler = self.commands.get(name)
        recipe = None
        if handler is None and self.recipes is not None:
            with contextlib.suppress(Exception):
                recipe = self.recipes.get(name)
        if handler is None and recipe is None:
            return t("agent.unknown_command", self.soul.language, name=name)
        names = {role.name for role in roles}
        denied = t(
            "agent.command_denied",
            self.soul.language,
            name=name,
            roles=", ".join(sorted(names)),
        )
        if recipe is not None and not (names & set(recipe.roles)):
            return denied
        if handler is not None and not command_allowed(roles, handler.name):
            return denied
        if recipe is not None:
            from ..stores.recipes import run_recipe

            ctx = self.tool_context(thread_id, sender, roles, gateway, request=args)
            if recipe.kind == "recipe":
                return await run_recipe(recipe, args, ctx)
            if self.llm is None:
                return t("agent.no_llm", self.soul.language)
            specs = self._specs(roles, gateway)
            prompt_context = PromptContext(
                config=self.config,
                roles=roles,
                runtime=self.runtime,
                body_section=prompt_section(gateway),
                tools=self.tool_index.registry(gateway.name),
                commands=self.commands,
                bodies=self.tool_index.other_bodies(gateway.name),
                mode="professional",
            )
            return await self.llm.run(
                system_prompt(
                    self.config,
                    roles,
                    context=prompt_context,
                    parts=(*CORE_PARTS, *self.plugin_parts),
                ),
                f"{recipe.prompt}\n\nArguments: {args}".strip(),
                specs,
                ctx,
                session=f"monoagens-recipe-{thread_id}-{name}",
            )
        ctx = self.context(thread_id, sender, roles, gateway, args, inbound)
        if handler is None or handler.fn is None:  # pragma: no cover - a command is callable
            return t("agent.unknown_command", self.soul.language, name=name)
        return await handler.fn(ctx)


    def context(
        self,
        thread_id: str,
        sender: str,
        roles: tuple[Role, ...],
        gateway: GatewayCore | None,
        args: str = "",
        inbound: Percept | None = None,
    ) -> CommandContext:
        registry = gateway.name if gateway is not None else ""
        specs = tools_for(self.tool_index.registry(registry), roles) + (
            self.hub.specs(roles) if self.hub else []
        )
        return CommandContext(
            agent=self,
            gateway=gateway,
            roles=roles,
            thread_id=thread_id,
            sender=sender,
            direct=bool(inbound.hints.get("dm", False)) if inbound is not None else True,
            args=args,
            commands=tuple(commands_for(self.commands, roles)),
            tools=tuple(specs),
            inbound=inbound,
        )
