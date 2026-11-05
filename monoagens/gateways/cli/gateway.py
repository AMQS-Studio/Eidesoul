"""CLI gateway: a local REPL that uses the same soul and DB."""


import asyncio
import getpass
import logging
import sys
import threading
import time
from datetime import datetime

from ...engine.access.identity import IdentityStore
from ...engine.inbound import MESSAGE, Action, CoreDefaults, GatewayError, Percept
from ...engine.registry import Handler
from ...engine.turn.context import Message
from . import policy
from .config import CliSettings

log = logging.getLogger(__name__)


class CliGateway(CoreDefaults):
    """Core only: a terminal has no files, stickers, presence indicator, or directory."""

    name = "cli"
    backlog_seconds = 0.0

    def __init__(self, config, db, history, windows, *, settings=None) -> None:
        from ...engine.config import GatewaySettings

        raw = settings or config.gateways.get("cli") or GatewaySettings("cli")
        self.config = config
        self.db = db
        self.history = history
        self.windows = windows
        self.settings = CliSettings.from_mapping(raw.enabled, raw.options)
        self.counter = 0
        self.stopped = False
        self.agent = None
        self._lines: asyncio.Queue[str] | None = None
        self._stop = asyncio.Event()

    def tools(self) -> list[Handler]:
        return []

    def commands(self) -> list[Handler]:
        return []

    def access_refusal(self, user: str) -> str:
        """ADR-0046 §4.3: a role enters the terminal only with its own `gateways.cli` block."""
        person = IdentityStore(self.db).resolve(self.name, user)
        role, block = self.config.roles.policy_for(person, self.name)
        if policy.allowed(block):
            return ""
        reason = "policy_missing" if block is None else "denied"
        log.info(
            "gateway_policy_%s gateway=%s role=%s user=%s",
            "missing" if reason == "policy_missing" else "denied",
            self.name,
            role.name,
            user,
        )
        return reason

    def _pump(self, loop: asyncio.AbstractEventLoop, lines: asyncio.Queue[str]) -> None:
        """Read the terminal on a daemon thread: a blocked read never holds asyncio shutdown."""
        while True:
            try:
                line = sys.stdin.readline()
            except (EOFError, KeyboardInterrupt):
                line = ""
            try:
                loop.call_soon_threadsafe(lines.put_nowait, line)
            except RuntimeError:  # the loop is already closed
                return
            if not line:
                return

    async def run(self, agent) -> None:
        self.agent = agent
        print(
            f"MonoAgens console — mode: {agent.mode}; identity: {self.settings.user or 'local'}; "
            "type /help, /quit to exit.",
            flush=True,
        )
        loop = asyncio.get_running_loop()
        lines: asyncio.Queue[str] = asyncio.Queue()
        self._lines = lines
        threading.Thread(
            target=self._pump, args=(loop, lines), name="cli-stdin", daemon=True
        ).start()
        while not self.stopped:
            line = await lines.get()
            if not line:
                # A closed stdin (a service, /dev/null, Ctrl-D) leaves the body idle; it must
                # not end the gateway task, which the supervisor would read as a full stop.
                log.info("cli: stdin closed; console idle")
                await self._stop.wait()
                return
            text = line.rstrip("\n")
            if text.strip() in {"/quit", "/exit"}:
                break
            if not text.strip():
                continue
            self.counter += 1
            user = self.settings.user or "local"
            hints: dict[str, object] = {"dm": True}
            inbound = Percept(
                source=self.name,
                thread=self.settings.thread_id,
                actor=user,
                kind=MESSAGE,
                payload=Message(
                    id=f"cli-{time.time_ns()}",
                    sender_name=user or getpass.getuser(),
                    sender_id=user,
                    when=datetime.now(),
                    text=text,
                    sender_address=user,
                ),
                hints=hints,
            )
            try:
                if self.access_refusal(user):
                    continue
                await agent.handle(inbound, self)
            except Exception:
                log.exception("console turn failed")

    async def stop(self) -> None:
        self.stopped = True
        self._stop.set()
        if self._lines is not None:
            self._lines.put_nowait("")

    async def dispatch(self, action: Action) -> str:
        """Message Dispatcher: this body's own verbs, one refusal for anything else."""
        match action.capability:
            case "send_text":
                return await self.send_text(action.address.thread, str(action.payload or ""))
            case _:
                raise GatewayError(
                    f"body '{self.name}' does not support '{action.capability}'"
                )

    async def send_text(self, thread: str, text: str, *, reply: Percept | None = None) -> str:
        self.counter += 1
        print(f"\nMonoAgens: {text}\n", flush=True)
        return f"cli-{self.counter}"

    def render(self, text: str) -> str:
        return str(text or "")

    def prompt_section(self) -> str:
        return (
            "## CLI interface\n"
            "- You are answering in a local terminal: plain text, no WhatsApp formatting, no "
            "mentions, no quoting.\n"
            "- Stay brief; the operator can see tool results only through your answer.\n"
            "- Files are not transferable here: a file becomes the file name and, when it is "
            "text, its contents."
        )

    def self_handles(self) -> set[str]:
        return set()

    def chat_name(self, thread: str) -> str:
        return self.settings.chat_name

    def private_thread(self, person_id: str) -> str:
        """The console has exactly one thread; it is this person's private thread."""
        return self.settings.thread_id
