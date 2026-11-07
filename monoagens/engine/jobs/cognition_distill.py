"""Episode distillation: one reusable procedure per admitted episode (ADR-0001)."""

import json
import logging
import re
from typing import TYPE_CHECKING

from .. import io
from ..memory import episodes as memory_episodes
from .cognition_queue import TOOL, Job

if TYPE_CHECKING:
    pass

from typing import Any

from ..config import Config
from ..db import Database

log = logging.getLogger(__name__)
class CognitionDistill:
    """Distill an episode into a skill procedure."""

    config: Config
    db: Database | None
    skills: Any
    llm: Any
    language: str
    known_tools: tuple[str, ...]
    counters: dict[str, int]

    async def _distill(self, job: Job) -> str:
        settings = self.config.skills
        if not settings.enabled or self.skills is None:
            return "skills disabled"
        episode = await io.run(memory_episodes.get, self.db, job.payload_ref)
        if episode is None:
            return "episode missing"
        if episode.distilled_at:
            return "already distilled"
        steps = await io.run(memory_episodes.steps_of, self.db, episode.id)
        ok, reason = memory_episodes.admission(episode, steps)
        if not ok:
            return f"not admitted: {reason}"
        procedure = await self._procedure(episode, steps)
        if procedure is None:
            return "no procedure"
        name, description, body = procedure
        try:
            await io.run(
                self.skills.revise,
                name,
                description,
                body,
                source_episode=episode.id,
                scope="thread",
                stated_by=episode.thread_id,
                trust="T2",
                language=self.language,
                app=episode.app,
            )
        except (ValueError, FileExistsError) as exc:
            return f"procedure refused: {exc}"
        await io.run(memory_episodes.mark_distilled, self.db, episode.id)
        self.counters["distilled"] += 1
        return f"procedure {name}"

    async def _procedure(
        self, episode, steps: list[dict]
    ) -> tuple[str, str, str] | None:
        """One procedure per episode: the LLM when present, a deterministic trace otherwise."""
        tools = [str(step.get("tool") or "") for step in steps if str(step.get("tool") or "")]
        name = memory_episodes.slug(episode.signature or "episode")
        description = f"Repeat the {'→'.join(dict.fromkeys(tools)) or 'task'} procedure."
        body = (
            f"Observed in episode {episode.id} ({episode.turns} turns, "
            f"{episode.tool_calls} tool calls).\n\n"
            + "\n".join(f"{index}. {tool}" for index, tool in enumerate(tools, start=1))
        ).strip()
        if self.llm is not None and tools:
            drafted = await self._draft(episode, steps)
            if drafted is not None:
                name, description, body = drafted
        if not self._tools_allowed(body):
            return None
        return name, description[:200], body

    async def _draft(
        self, episode, steps: list[dict]
    ) -> tuple[str, str, str] | None:
        if self.llm is None:
            return None
        payload = {
            "episode": episode.id,
            "turns": episode.turns,
            "tools": [str(step.get("tool") or "") for step in steps],
            "language": self.language,
        }
        prompt = (
            "Turn one finished task into one reusable procedure. Reply ONLY with JSON "
            '{"worth":true,"name":"lowercase-hyphen-name","description":"","body":""}. '
            "The body is a short ordered recipe that mentions only the tools listed. "
            "worth=false when the task is not repeatable."
        )
        try:
            raw = await self.llm.complete(prompt, json.dumps(payload), max_tokens=600)
        except Exception:
            log.debug("distill draft failed", exc_info=True)
            return None
        start, end = str(raw or "").find("{"), str(raw or "").rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            found = json.loads(str(raw)[start : end + 1])
        except (TypeError, ValueError):
            return None
        if not isinstance(found, dict) or not found.get("worth", True):
            return None
        name = str(found.get("name") or "")
        description = str(found.get("description") or "")
        body = str(found.get("body") or "")
        if not name or not description or not body:
            return None
        return name, description, body

    def _tools_allowed(self, body: str) -> bool:
        """A procedure may only mention tools that already exist in the catalog."""
        if not self.known_tools:
            return True
        known = set(self.known_tools)
        body = str(body or "")
        mentioned = set(TOOL.findall(body))
        unknown = {
            word
            for word in mentioned
            if word not in known
            and ("_" in word or f"`{word}`" in body)
            and re.search(rf"\b{re.escape(word)}\(", body)
        }
        return not unknown
