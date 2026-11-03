"""Bounded autonomy: summarise topics and research while idle.

Every access outside the emotion effects is limited: the only tool is `web_search`, memory is
limited to the same chat + global, and it never sends a message to any chat.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import date, datetime

from . import guard
from .config import Config
from .context import keyword_set
from .currents import refresh
from .roles import Role
from .tools import ToolContext, execute
from .tools.web_search import TOOL as WEB_SEARCH

log = logging.getLogger(__name__)

TRANSCRIPT_CHARS = 300
AMBIENT_ROLE = ("web_search",)
DRIFT_FLOOR = 0.2


def drift(old: str, new: str, language: str = "id") -> bool:
    """True when the new summary shares almost none of the old summary's keywords."""
    fresh = keyword_set(new, language)
    if not fresh:
        return False
    return len(keyword_set(old, language) & fresh) / len(fresh) < DRIFT_FLOOR


class Ambient:
    """Single background task loop; every failure is swallowed so the bot keeps replying."""

    def __init__(
        self,
        config: Config,
        llm=None,
        affect=None,
        style=None,
        memory=None,
        history=None,
        windows=None,
        currents=None,
    ):
        self.config = config
        self.settings = config.ambient
        self.llm = llm
        self.affect = affect
        self.style = style
        self.memory = memory
        self.history = history
        self.windows = windows
        self.currents = currents
        self.known_threads: set[str] = set()
        self.busy: set[str] = set()
        self.tasks: set[asyncio.Task] = set()
        self._task: asyncio.Task | None = None

    # ------------------------------------------------------------------ lifecycle

    def start(self) -> None:
        if self._task is None:
            self.prime()
            self._task = asyncio.create_task(self._loop())

    def prime(self) -> None:
        """Old threads keep ticking after a restart, not only the threads seen since then."""
        if self.history is None:
            return
        try:
            threads = self.history.recent_threads(50)
        except Exception:
            log.debug("ambient: failed to read the recent threads", exc_info=True)
            return
        self.known_threads.update(str(thread_id) for thread_id in threads if str(thread_id))

    async def stop(self) -> None:
        pending = [task for task in (self._task, *self.tasks) if task is not None]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._task = None
        self.tasks.clear()

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    def note(self, thread_id: str, message) -> None:
        """A message passed by: just record the active chat. Appraisal lives in AffectStore."""
        if not thread_id:
            return
        self.known_threads.add(thread_id)

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(max(10, int(self.settings.tick_seconds)))
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # autonomy must not kill the bot
                log.exception("ambient tick failed")

    # ------------------------------------------------------------------ tick

    async def _tick(self, now: float | None = None) -> None:
        moment = time.time() if now is None else float(now)
        await self._context(moment)
        if self.affect is None or self.history is None or self.llm is None:
            return
        settings = self.settings
        threads = sorted(self.known_threads)
        for thread_id in threads:
            try:  # heartbeat: mood decays and drives grow even without messages
                self.affect.pulse(thread_id)
            except Exception:
                log.debug("heartbeat pulse failed chat=%s", thread_id, exc_info=True)
        for thread_id in threads:
            if thread_id in self.busy:
                continue
            try:
                topic = self.affect.topic(thread_id)
                pending = self.affect.pending(thread_id)
            except Exception:
                log.debug("ambient: topic failed chat=%s", thread_id, exc_info=True)
                continue
            if pending >= int(settings.digest_every) and (
                moment - float(topic["last_digest"]) >= float(settings.digest_cooldown)
            ):
                self.busy.add(thread_id)
                self._spawn(self._digest(thread_id))
                log.info("ambient digest started chat=%s pending=%d", thread_id, pending)
        for thread_id in threads:
            if await self._research_candidate(thread_id, moment):
                await self._research(thread_id)
                break

    async def _research_candidate(self, thread_id: str, moment: float) -> bool:
        settings = self.settings
        try:
            topic = self.affect.topic(thread_id)
        except Exception:
            return False
        if moment - float(topic["last_message"]) < int(settings.idle_minutes) * 60:
            return False
        if not [item for item in topic["unresolved"] if str(item).strip()]:
            return False
        if moment - float(topic["last_research"]) < int(settings.research_cooldown):
            return False
        return int(topic["research_count"]) < int(settings.research_per_day)

    # ------------------------------------------------------------------ context

    async def _context(self, moment: float) -> None:
        """Refresh the stalest context; only the primary process reaches this code."""
        settings = self.settings
        if self.currents is None or self.llm is None or not settings.context_enabled:
            return
        if "__context" in self.busy:
            return
        try:
            last = float(self.currents.load().get("updated_at") or 0.0)
        except Exception:
            log.debug("ambient: failed to read currents", exc_info=True)
            return
        if moment - last < int(settings.context_hours) * 3600:
            return
        self.busy.add("__context")
        try:
            updated = await refresh(
                self.currents,
                self._search,
                self.llm.research_summary,
                topics=int(settings.context_topics),
                results=int(settings.context_results),
                now=moment,
            )
            log.info("ambient context: %d topics updated", updated)
        except Exception:  # background context must not stop the loop
            log.exception("ambient context failed")
        finally:
            self.busy.discard("__context")

    async def _search(self, query: str, results: int) -> str:
        return await execute(
            {"web_search": WEB_SEARCH},
            "web_search",
            {"query": query, "max_results": int(results)},
            self._tool_ctx(""),
        )

    # ------------------------------------------------------------------ actions

    async def _digest(self, thread_id: str) -> None:
        try:
            if self.affect is None or self.history is None or self.llm is None:
                return
            topic = self.affect.topic(thread_id)
            rows = [
                row
                for row in self._recent(thread_id, int(self.settings.history_window))
                if not getattr(row, "outgoing", False)
            ]
            if not rows:
                self.affect.mark_digest(thread_id)
                return
            transcript = "\n".join(self._line(row) for row in rows)
            people = ", ".join(
                str(item["name"]) for item in self.affect.known_people(thread_id, 8) if item["name"]
            )
            name = ""
            if self.windows is not None:
                try:
                    name = self.windows.chat_name(thread_id)
                except Exception:
                    name = ""
            data = await self.llm.digest(name, transcript, str(topic["summary"]), people)
            if not isinstance(data, dict):
                return
            summary = str(data.get("topic") or "")
            keywords = [str(item) for item in data.get("keywords") or []]
            unresolved = [str(item) for item in data.get("unresolved") or []]
            self._close_era(thread_id, topic, summary)
            if summary or keywords or unresolved:
                self.affect.set_topic(thread_id, summary, keywords, unresolved)
            self.affect.mark_digest(thread_id)
            for fact in data.get("facts") or []:
                if not isinstance(fact, dict):
                    continue
                text = str(fact.get("text") or "").strip()
                if not text or guard.SENSITIVE.search(text) or guard.is_injection(
                    text, self.config.soul.language
                ):
                    continue
                person = str(fact.get("person") or "").strip()
                person_id = self.affect.person_by_name(thread_id, person) if person else None
                if person_id:
                    self.affect.note_person(person_id, text, self.config.soul.language)
                    continue
                if self.memory is None:
                    continue
                try:
                    await self.memory.add(
                        text,
                        scope="thread",
                        kind=str(fact.get("kind") or "note"),
                        thread_id=thread_id,
                        source=f"ambient:{thread_id}",
                    )
                except ValueError:
                    continue
            log.info("ambient digest finished chat=%s", thread_id)
        except asyncio.CancelledError:
            raise
        except Exception:  # a failed digest must not stop the loop
            log.exception("ambient digest failed chat=%s", thread_id)
        finally:
            self.busy.discard(thread_id)

    def _close_era(self, thread_id: str, topic: dict, summary: str) -> None:
        """Archive the old topic as a dated era once the digest moved to another subject."""
        previous = str(topic["summary"]).strip()
        fresh = " ".join(str(summary or "").split())
        if not previous or not fresh or not drift(previous, fresh, self.config.soul.language):
            return
        self.affect.archive_era(
            thread_id,
            previous,
            list(topic["keywords"]),
            list(topic["unresolved"]),
            started_at=float(topic["last_message"]),
            ended_at=time.time(),
        )

    async def _research(self, thread_id: str) -> None:
        if self.affect is None or self.llm is None:
            return
        topic = self.affect.topic(thread_id)
        pending = [str(item).strip() for item in topic["unresolved"] if str(item).strip()]
        summary = str(topic["summary"]).strip()
        query = pending[0] if pending else (f"topic: {summary}" if summary else "")
        if not query:
            return
        try:
            results = await execute(
                {"web_search": WEB_SEARCH},
                "web_search",
                {"query": query, "max_results": int(self.settings.research_results)},
                self._tool_ctx(thread_id),
            )
        except Exception:  # no tool may kill autonomy
            log.exception("ambient research failed chat=%s", thread_id)
            return
        if not results or results.startswith("error:"):
            log.info("ambient research empty chat=%s: %s", thread_id, results[:120])
            return
        text = await self.llm.research_summary(query, results)
        if not text:
            return
        if self.memory is not None:
            try:
                await self.memory.add(
                    f"[research {date.today().isoformat()}] {text}",
                    scope="thread",
                    kind="note",
                    thread_id=thread_id,
                    source=f"ambient:{thread_id}",
                )
            except ValueError:
                return
        self.affect.mark_research(thread_id)
        log.info("ambient research finished chat=%s query=%s", thread_id, query[:80])

    # ------------------------------------------------------------------ util

    def _recent(self, thread_id: str, limit: int) -> list:
        if self.history is None:
            return []
        try:
            rows = self.history.recent(thread_id, limit)
        except Exception:
            log.debug("ambient: archive failed chat=%s", thread_id, exc_info=True)
            return []
        return [row for row in rows if str(getattr(row, "text", "") or "").strip()]

    @staticmethod
    def _line(row) -> str:
        body = " ".join(str(getattr(row, "text", "") or "").split())
        stamp = ""
        when = getattr(row, "when", None)
        if isinstance(when, datetime):
            stamp = when.strftime("%Y-%m-%d %H:%M")
        who = getattr(row, "sender_name", "") or getattr(row, "sender_id", "") or "-"
        return f"[{stamp or '-'}] {who}: {body[:TRANSCRIPT_CHARS]}"

    def _tool_ctx(self, thread_id: str) -> ToolContext:
        role = Role(name="ambient", tools=AMBIENT_ROLE)
        return ToolContext(self.config, (role,), AMBIENT_ROLE, thread_id=thread_id)
