"""Reply text splitting and the one outbound path: chunking, pacing, notices, media.

The transport dialect stays in the body (`render`); everything a chat reply needs before and after
that call lives in `Delivery` (ADR-0031 §4.2).
"""


import asyncio
import contextlib
import logging
import random
import re
import time
from datetime import datetime
from pathlib import Path

from ..i18n import t
from ..inbound import (
    AUTHORIZATION,
    CONSENT,
    Action,
    GatewayError,
    Percept,
    Rich,
    StandbyError,
)
from ..runtime import ACTIVE_GENERATION
from .context import AUTHOR_BOT, TEXT_EXT, Message, clean

log = logging.getLogger(__name__)

MAX_TEXT = 10_000
FILE_TEXT_SIZE = 512 * 1024
FILE_TEXT_CHARS = 4000

BLOCK = re.compile(r"<(reasoning|thought|tool_call)>.*?</\1>", re.IGNORECASE | re.DOTALL)
FENCE = re.compile(r"(```.*?```)", re.DOTALL)
FENCE_START = "```"
BOLD = re.compile(r"\*\*(.+?)\*\*")
BULLET = re.compile(r"^[-*]\s+", re.MULTILINE)
LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
BUBBLE = re.compile(r"(?m)^\s*-{3,}\s*$")  # a "---" line alone = new bubble; the model's choice
BLANK = re.compile(r"\n\s*\n")
LIST_LINE = re.compile(r"(?m)^\s*(?:[-*•]|\d+[.)]|\d\ufe0f?\u20e3)\s")
CASUAL_BUBBLE_MAX = 300


def strip_blocks(text: str) -> str:
    return BLOCK.sub("", text or "")


def inline_breaks(text: str) -> str:
    """'one --- two' -> a real bubble break; a linear scan, no backtracking on a space run."""
    out: list[str] = []
    index = 0
    size = len(text)
    while index < size:
        space = index
        while space < size and text[space] in " \t":
            space += 1
        if space == index:
            out.append(text[index])
            index += 1
            continue
        dashes = space
        while dashes < size and text[dashes] == "-":
            dashes += 1
        after = dashes
        while after < size and text[after] in " \t":
            after += 1
        if dashes - space >= 3 and after > dashes:
            out.append("\n---\n")
            index = after
            continue
        out.append(text[index:space])
        index = space
    return "".join(out)


def _heading(line: str) -> str:
    """'## Title' -> '*Title*'; other lines are returned as-is."""
    marks = len(line) - len(line.lstrip("#"))
    rest = line[marks:]
    if not 1 <= marks <= 6 or rest[:1] not in (" ", "\t") or len(rest) < 2:
        return line
    body = rest.lstrip(" \t")
    return f"*{body}*"


def clean_prose(chunk: str) -> str:
    """Markdown -> body dialect (a rich body renders it; a plain body keeps identity)."""
    chunk = "\n".join(_heading(line) for line in chunk.split("\n"))
    chunk = BOLD.sub(r"*\1*", chunk)
    chunk = BULLET.sub("• ", chunk)
    return LINK.sub(r"\1 (\2)", chunk)


def _chunks(text: str, limit: int) -> list[str]:
    parts: list[str] = []
    rest = text
    while len(rest) > limit:
        window = rest[:limit]
        cut = window.rfind("\n\n")
        if cut < limit // 2:
            cut = window.rfind("\n")
        if cut < limit // 2:
            cut = limit
        parts.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip("\n")
    if rest.strip():
        parts.append(rest.strip())
    return [part for part in parts if part]


def split_text(text: str, limit: int = 10000) -> list[str]:
    cleaned = re.sub(r"\n{3,}", "\n\n", strip_blocks(text).strip())
    return _chunks(cleaned, limit) if cleaned else []


def human_chunks(text: str, *, max_parts: int = 3) -> list[str]:
    """Text -> bubbles as-is; the model marks boundaries with a `---` line."""
    body = strip_blocks(str(text or "")).strip()
    if not body:
        return []
    parts: list[str] = []
    current = ""
    for piece in FENCE.split(body):
        if not piece.strip():
            continue
        if piece.lstrip().startswith(FENCE_START):
            current = f"{current}\n{piece}".strip() if current else piece.strip()
            continue
        piece = inline_breaks(piece)
        for index, part in enumerate(BUBBLE.split(piece)):
            chunk = part.strip()
            if not chunk:
                continue
            if index == 0:
                current = f"{current}\n{chunk}".strip() if current else chunk
                continue
            if current:
                parts.append(current)
            current = chunk
    if current:
        parts.append(current)
    if len(parts) == 1 and FENCE_START not in body:
        paragraphs = [part.strip() for part in BLANK.split(parts[0]) if part.strip()]
        casual = (
            len(paragraphs) > 1
            and not LIST_LINE.search(parts[0])
            and all(len(part) <= CASUAL_BUBBLE_MAX for part in paragraphs)
        )
        if casual:
            parts = paragraphs
    cap = max(1, int(max_parts))
    if len(parts) > cap:
        parts = [*parts[: cap - 1], "\n\n".join(parts[cap - 1 :])]
    return parts


def typing_delay(text: str, config, rng=random) -> float:
    """Plausible typing delay (seconds) from the bubble length; 0 when the delay is disabled."""
    if float(config.min_delay) == 0 and float(config.max_delay) == 0:
        return 0.0
    seconds = len(str(text or "")) / max(1.0, float(config.cpms))
    bounded = max(float(config.min_delay), min(float(config.max_delay), seconds))
    return bounded + rng.uniform(0, 0.4)


def file_fallback_text(path: Path, caption: str, language: str) -> str:
    """What a body without `Rich` delivers instead of the file (ADR-0018 §4.2)."""
    label = t(
        "agent.file_fallback",
        language,
        name=path.name,
        caption=f" — {caption}" if caption else "",
    )
    if path.suffix.lower() not in TEXT_EXT:
        return label
    try:
        if not path.is_file() or path.stat().st_size > FILE_TEXT_SIZE:
            return label
        body = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return label
    return f"{label}\n{body[:FILE_TEXT_CHARS]}" if body else label


class Delivery:
    """One chat path for every caller: text, file, sticker, typing, notices, and the turn."""

    def __init__(self, agent) -> None:
        self.agent = agent

    def _self_sender_id(self, gateway) -> str:
        return next(iter(sorted(gateway.self_handles())), "")

    async def text(
        self,
        thread_id: str,
        text: str,
        *,
        gateway,
        reply: Percept | None = None,
        kind: str = "",
    ) -> str:
        self.agent._send_gate()
        message_id = await gateway.send_text(thread_id, text, reply=reply)
        self.agent.windows.append(
            thread_id,
            Message(
                id=message_id,
                sender_name=self.agent.soul.name,
                sender_id=self._self_sender_id(gateway),
                when=datetime.now(),
                text=text,
                outgoing=True,
                author=AUTHOR_BOT,
            ),
        )
        if kind:
            with contextlib.suppress(Exception):
                self.agent.history.add_notice(thread_id, text, kind=kind, message_id=message_id)
        return message_id

    async def file(
        self,
        thread_id: str,
        path: Path,
        caption: str = "",
        *,
        gateway,
        reply: Percept | None = None,
        action_id: str = "",
    ) -> str:
        path = Path(path)
        if not isinstance(gateway, Rich):
            self.agent.runtime.miss_rich()
            return await self.text(
                thread_id,
                file_fallback_text(path, caption, self.agent.soul.language),
                gateway=gateway,
                reply=reply,
            )
        self.agent._send_gate()
        message_id = await gateway.send_file(thread_id, path, caption, reply=reply)
        note = f"(file terkirim: {path.name}{f' — {caption}' if caption else ''})"
        self.agent.windows.append(
            thread_id,
            Message(
                id=message_id,
                sender_name=self.agent.soul.name,
                sender_id=self._self_sender_id(gateway),
                when=datetime.now(),
                text=note,
                outgoing=True,
                author=AUTHOR_BOT,
            ),
        )
        return message_id

    async def sticker(
        self, thread_id: str, message_id: str, *, gateway, action_id: str = ""
    ) -> str | None:
        if not isinstance(gateway, Rich):
            self.agent.runtime.miss_rich()
            return None
        self.agent._send_gate()
        label = await gateway.send_sticker(thread_id, message_id)
        if label:
            self.agent.windows.append(
                thread_id,
                Message(
                    id=f"sticker-{time.time_ns()}",
                    sender_name=self.agent.soul.name,
                    sender_id=self._self_sender_id(gateway),
                    when=datetime.now(),
                    text=label,
                    outgoing=True,
                    author=AUTHOR_BOT,
                ),
            )
        return label

    async def typing(self, gateway, thread_id: str, composing: bool) -> None:
        """A body without `Rich` has no presence indicator; skip, never fail."""
        if not isinstance(gateway, Rich):
            self.agent.runtime.miss_rich()
            return
        await gateway.typing(thread_id, composing)

    async def prepare_media(self, gateway, thread_id: str, entry: Message) -> None:
        if not isinstance(gateway, Rich):
            self.agent.runtime.miss_rich()
            return
        await gateway.prepare_media(thread_id, entry)

    async def send_text(self, thread_id: str, text: str, *, gateway) -> str:
        """Tool-facing: render the gateway dialect, split, send, return ok:/error:."""
        if gateway is None:
            return "error: gateway is not available"
        body = clean(str(text or "")).strip()
        if not body:
            return "error: empty text"
        chunks = split_text(gateway.render(body), limit=MAX_TEXT)
        if not chunks:
            return "error: empty text"
        try:
            for chunk in chunks:
                await self.text(thread_id, chunk, gateway=gateway)
        except StandbyError:
            return "error: delivery refused"
        except GatewayError as exc:
            return f"error: failed to send text: {exc}"
        name = gateway.chat_name(thread_id)
        suffix = f" ({len(chunks)} parts)" if len(chunks) > 1 else ""
        return f"ok: text sent to {name or thread_id}{suffix}"

    async def send_file(self, thread_id: str, path: Path, caption: str = "", *, gateway) -> str:
        if gateway is None:
            return "error: gateway is not available"
        try:
            await self.file(thread_id, Path(path), caption, gateway=gateway)
        except StandbyError:
            return "error: delivery refused"
        except GatewayError as exc:
            return f"error: failed to send file: {exc}"
        except OSError as exc:
            return f"error: {type(exc).__name__}: {exc}"
        return f"ok: file sent: {Path(path).name}"

    async def send_reply(
        self,
        *,
        turn_id: str,
        thread_id: str,
        gateway,
        inbound: Percept,
        chunks: list[str],
        pace: bool,
        quote_all: bool,
        generation: int,
        partial: str,
        humanize,
    ) -> tuple[list[str], list[Action], str]:
        """Send the planned chunks of one turn; `stop` is "", "superseded", or "failed"."""
        self.agent.turns.plan_sending(turn_id, len(chunks))
        sent_ids: list[str] = []
        actions: list[Action] = []
        scope_key = self.agent.outbox.turn_scope(turn_id)
        for index, chunk in enumerate(chunks):
            if pace:
                await self.typing(gateway, thread_id, True)
                await asyncio.sleep(typing_delay(chunk, humanize))
            state = self.agent.outbox.begin(
                scope_key,
                index,
                gateway=gateway.name,
                thread_id=thread_id,
                generation=generation,
            )
            if state == "sent":
                continue
            if state == "unknown" and partial == "skip":
                log.info("chunk %d of turn %s is unknown: skipped", index, turn_id)
                continue
            try:
                provider = await self.text(
                    thread_id,
                    chunk,
                    gateway=gateway,
                    reply=inbound if (quote_all or index == 0) else None,
                )
            except StandbyError:
                self.agent.outbox.forget(scope_key, index)
                return sent_ids, actions, "superseded"
            except GatewayError:
                log.exception("failed to send the reply to %s", thread_id)
                self.agent.outbox.mark_unknown(scope_key, index)
                return sent_ids, actions, "failed"
            self.agent.outbox.mark_sent(scope_key, index, provider)
            self.agent.turns.chunk_sent(turn_id)
            sent_ids.append(provider)
            actions.append(
                Action(
                    capability="send_text",
                    target=thread_id,
                    payload=chunk,
                    approval_class=(
                        CONSENT if thread_id == inbound.thread else AUTHORIZATION
                    ),
                )
            )
            if pace and index < len(chunks) - 1:
                await asyncio.sleep(
                    max(0.0, float(humanize.between_delay)) + random.uniform(0, 0.4)
                )
        return sent_ids, actions, ""

    async def notice(
        self,
        thread_id: str,
        gateway,
        key: str,
        *,
        scope_key: str,
        kind: str,
        params: dict | None = None,
    ) -> bool:
        """One catalog notice, ledgered with its `kind`; it never marks a turn delivered."""
        agent = self.agent
        text = t(key, agent.soul.language, **(params or {}))
        retry = agent.config.turns.shutdown.notice_retry
        try:
            status = agent.outbox.begin(
                scope_key,
                0,
                gateway=gateway.name,
                thread_id=thread_id,
                generation=ACTIVE_GENERATION.get(),
            )
        except Exception:
            log.exception("failed to open the notice ledger chat=%s kind=%s", thread_id, kind)
            agent.runtime.fail_notice("notice ledger unavailable")
            return False
        if status == "sent":
            return False
        if status == "unknown":
            # It may already be visible; a notice must never duplicate (ADR-0013 §10).
            return False
        attempts = max(1, int(retry.attempts))
        for attempt in range(attempts):
            try:
                message_id = await self.text(thread_id, text, gateway=gateway, kind=kind)
            except StandbyError:
                agent.outbox.forget(scope_key, 0)
                return False
            except GatewayError as exc:
                agent.runtime.fail_notice(f"{type(exc).__name__}: {exc}")
                log.warning("notice failed chat=%s kind=%s: %s", thread_id, kind, exc)
                if attempt < attempts - 1:
                    await asyncio.sleep(
                        min(
                            max(0.0, float(retry.backoff_seconds)),
                            max(0.0, float(agent.config.llm.retry.retry_after_max_seconds)),
                        )
                    )
                    continue
                agent.outbox.mark_unknown(scope_key, 0)
                return False
            except Exception as exc:
                log.exception("notice send failed chat=%s kind=%s", thread_id, kind)
                agent.runtime.fail_notice(f"{type(exc).__name__}: {exc}")
                with contextlib.suppress(Exception):
                    agent.outbox.mark_unknown(scope_key, 0)
                return False
            agent.outbox.mark_sent(scope_key, 0, message_id)
            return True
        return False

    async def notice_timeout(self, thread_id: str, turn_id: str, gateway) -> None:
        self.agent.runtime.timeout_turn()
        await self.notice(
            thread_id,
            gateway,
            "turn.notice.timeout",
            scope_key=f"notice:{turn_id}:timeout",
            kind="notice:timeout",
        )

    async def notice_interrupted(self, turn_id: str, thread_id: str, gateway) -> None:
        if gateway is None:
            return
        await self.notice(
            thread_id,
            gateway,
            "turn.notice.interrupted",
            scope_key=f"notice:{turn_id}:interrupted",
            kind="notice:interrupted",
        )


def plan_chunks(
    reply: str | None,
    *,
    gateway,
    language: str,
    is_command: bool,
    professional: bool,
    humanize,
) -> tuple[list[str], bool, bool]:
    """The reply as chunks, plus `pace` and `quote_all`: one policy for every caller."""
    if is_command or professional:
        chunks = split_text(gateway.render(reply or "")) or [t("agent.empty_reply", language)]
        return chunks, False, True
    planned = max(1, int(humanize.max_bubbles))
    chunks = [
        gateway.render(chunk) for chunk in human_chunks(reply or "", max_parts=planned)
    ] or [t("agent.empty_reply", language)]
    pace = bool(humanize.enabled)
    return chunks, pace, not pace
