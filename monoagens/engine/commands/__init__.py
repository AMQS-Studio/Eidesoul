"""Command registry: one module per command with `COMMAND = Handler(...)`."""


from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..access.roles import Role
from ..registry import Handler, Registry, discover


@dataclass(frozen=True)
class CommandContext:
    agent: Any
    gateway: Any
    roles: tuple[Role, ...]
    thread_id: str
    args: str
    commands: tuple[Handler, ...]
    tools: tuple[Handler, ...]
    sender: str = ""
    direct: bool = True
    inbound: Any = None

    @property
    def role(self) -> Role | None:
        return self.roles[0] if self.roles else None

    @property
    def lang(self) -> str:
        return str(self.agent.soul.language)

    def role_names(self) -> str:
        return ", ".join(role.name for role in self.roles) or "-"


REGISTRY = Registry(
    discover(__name__, Path(__file__).parent, "COMMAND", "command"),
    "command",
)
