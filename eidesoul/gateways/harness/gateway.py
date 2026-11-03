"""Harness body: a non-chat task arrives as a percept and the turn's actions come back."""


import logging
import time
from datetime import datetime
from pathlib import Path

from ...engine.config import Config, GatewaySettings
from ...engine.inbound import TASK, Action, CoreDefaults, Percept
from ...engine.registry import Handler, discover
from ...engine.tools import FAMILIES
from ...engine.turn.context import Message

log = logging.getLogger(__name__)


class HarnessGateway(CoreDefaults):
    """One configured task per run; the engine has no branch for this body."""

    name = "harness"
    backlog_seconds = 0.0

    def __init__(
        self,
        config: Config,
        db,
        history,
        windows,
        *,
        settings: GatewaySettings | None = None,
    ) -> None:
        raw = settings or config.gateways.get("harness") or GatewaySettings("harness")
        options = dict(raw.options or {})
        self.config = config
        self.db = db
        self.history = history
        self.windows = windows
        self.task = str(options.get("task") or "").strip()
        self.thread = str(options.get("thread") or "harness:task")
        self.actor = str(options.get("actor") or "harness")
        self.actions: list[Action] = []
        self.sent: list[str] = []
        base = Path(__file__).parent
        self._tools = discover(__package__ + ".tools", base / "tools", "TOOL", "tool", FAMILIES)
        self._commands = discover(
            __package__ + ".commands", base / "commands", "COMMAND", "command"
        )

    def tools(self) -> list[Handler]:
        return list(self._tools)

    def commands(self) -> list[Handler]:
        return list(self._commands)

    async def run(self, agent) -> list[Action]:
        if not self.task:
            log.info("harness: no task configured; nothing to run")
            return []
        percept = Percept(
            source=self.name,
            thread=self.thread,
            actor=self.actor,
            kind=TASK,
            payload=Message(
                id=f"task-{time.time_ns()}",
                sender_name=self.name,
                sender_id=self.actor,
                when=datetime.now(),
                text=self.task,
                sender_address=self.actor,
            ),
            hints={"dm": True},
        )
        self.actions = list(await agent.handle(percept, self))
        return self.actions

    async def stop(self) -> None:
        return None

    async def send_text(self, thread: str, text: str, *, reply: Percept | None = None) -> str:
        self.sent.append(str(text or ""))
        return f"harness-{len(self.sent)}"

    def render(self, text: str) -> str:
        return str(text or "")

    def prompt_hints(self) -> str:
        return (
            "## Harness interface\n"
            "- You are completing one task on the host; answer with a short plain-text report.\n"
            "- The `run` tool executes a shell command in the workspace; the `inspect` command "
            "shows the task and the actions this turn produced."
        )

    def self_handles(self) -> set[str]:
        return set()
