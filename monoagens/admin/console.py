"""Local console: stdin -> Percept, the Agent's reply -> stdout, `/quit` exits.

Not a gateway: no config block, no role policy, no transport. `monoagens console` builds it,
passes it to the Agent as the only body of that process, and runs it instead of the supervisor.
A closed stdin ends the session; a failed turn is logged, never raised.
"""


import asyncio
import logging
import sys
import threading
import time
from datetime import UTC, datetime

from ..engine.inbound import MESSAGE, Action, Address, CoreDefaults, GatewayError, Percept
from ..engine.registry import Handler
from ..engine.turn.context import Message

log = logging.getLogger(__name__)

THREAD = "console:local"
ADDRESS = Address("console", "local")


class Console(CoreDefaults):
    """The core-only body of a terminal session: one thread, plain text."""

    name = "console"
    backlog_seconds = 0.0

    def __init__(self, *, user: str = "local") -> None:
        self.user = str(user or "local")
        self.thread = THREAD
        self.address = ADDRESS
        self.counter = 0
        self.stopped = False
        self._lines: asyncio.Queue[str] | None = None
        self._stop = asyncio.Event()

    def tools(self) -> list[Handler]:
        return []

    def commands(self) -> list[Handler]:
        return []

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

    def _percept(self, text: str) -> Percept:
        return Percept(
            source=self.name,
            thread=THREAD,
            actor=self.user,
            kind=MESSAGE,
            payload=Message(
                id=f"console-{time.time_ns()}",
                sender_name=self.user,
                sender_id=self.user,
                when=datetime.now(UTC),
                text=text,
                sender_address=ADDRESS.thread,
            ),
            hints={"dm": True},
        )

    async def run(self, agent) -> None:
        print(
            f"MonoAgens console — mode: {agent.mode}; identity: {self.user}; "
            "type /help, /quit to exit.",
            flush=True,
        )
        loop = asyncio.get_running_loop()
        lines: asyncio.Queue[str] = asyncio.Queue()
        self._lines = lines
        threading.Thread(
            target=self._pump, args=(loop, lines), name="console-stdin", daemon=True
        ).start()
        while not self.stopped:
            line = await lines.get()
            if not line:
                # A closed stdin (a service, /dev/null, Ctrl-D) ends the session instead of
                # parking a process that nobody can type into.
                log.info("console: stdin closed; exiting")
                self.stopped = True
                self._stop.set()
                return
            text = line.rstrip("\n")
            if text.strip() in {"/quit", "/exit"}:
                break
            if not text.strip():
                continue
            try:
                await agent.handle(self._percept(text), self)
            except Exception:
                log.exception("console turn failed")

    async def stop(self) -> None:
        self.stopped = True
        self._stop.set()
        if self._lines is not None:
            self._lines.put_nowait("")

    async def dispatch(self, action: Action) -> str:
        """Message Dispatcher: this body's own verbs, one refusal for anything else."""
        if action.capability == "send_text":
            return await self.send_text(action.address.thread, str(action.payload or ""))
        raise GatewayError(f"body '{self.name}' does not support '{action.capability}'")

    async def send_text(self, thread: str, text: str, *, reply: Percept | None = None) -> str:
        self.counter += 1
        print(f"\nMonoAgens: {text}\n", flush=True)
        return f"console-{self.counter}"

    def render(self, text: str) -> str:
        return str(text or "")

    def prompt_section(self) -> str:
        return (
            "## Console interface\n"
            "- You are answering in a local terminal: plain text, no WhatsApp formatting, no "
            "mentions, no quoting.\n"
            "- Stay brief; the operator can see tool results only through your answer.\n"
            "- Files are not transferable here: a file becomes the file name and, when it is "
            "text, its contents."
        )

    def self_handles(self) -> set[str]:
        return set()

    def chat_name(self, thread: str) -> str:
        return "Terminal"

    def private_thread(self, person_id: str) -> str:
        """The console has exactly one thread; it is this person's private thread."""
        return THREAD
