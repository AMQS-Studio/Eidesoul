"""The episode ladder and dispute question (ADR-0020): compare, decide, apply, propose."""

import json
import logging
import time
from typing import TYPE_CHECKING

from sqlalchemy import select

from .. import io
from ..memory import efficiency
from ..schema import actions
from .cognition_queue import DAY, Job, _json_object

if TYPE_CHECKING:
    pass

import asyncio
from typing import Any

from ..config import Config
from ..db import Database
from ..memory import reflect as memory_reflect

log = logging.getLogger(__name__)
class CognitionLadder:
    """The deterministic ladder and its one dispute question."""

    config: Config
    db: Database | None
    memory: Any
    skills: Any
    llm: Any
    language: str
    counters: dict[str, int]
    ask_dispute: Any
    propose: Any
    private_thread: Any

    LADDER_ACTIONS = ("keep", "merge", "promote", "archive", "propose_automation")

    LADDER_BATCH = 5

    LADDER_PROMPT = (
        "You compare episodes of the same procedure and pick one ladder action. Reply ONLY with "
        "JSON {\"action\":\"keep|merge|promote|archive|propose_automation\",\"skill\":\"\","
        "\"reason\":\"\",\"refs\":[],\"manifest\":null}. Choose keep when the evidence is "
        "unclear, merge when two procedures are the same job, promote when the procedure "
        "clearly saves work, archive when it keeps failing, and propose_automation when the "
        "same steps repeat on a schedule. Never invent a skill name; use only the ones given."
    )

    async def _reflect(self, job: Job) -> str:
        settings = self.config.reflection
        if not settings.enabled:
            return "reflection disabled"
        # `run_pass` is a coroutine (it awaits the ruling model); `io.run` would hand back an
        # un-awaited coroutine, so the pass is awaited directly under the same budget.
        report = await asyncio.wait_for(
            memory_reflect.run_pass(
                self.memory,
                scope=job.scope,
                llm=self.llm,
                language=self.language,
                max_items=int(settings.items_per_pass),
                max_writes=int(settings.writes_per_pass),
                min_candidates=int(settings.min_candidates),
                dispute_ttl_days=float(settings.dispute_ttl_days),
                derive_depth=int(settings.derive_depth),
            ),
            timeout=float(settings.seconds_per_pass),
        )
        self.counters["reflected"] += 1
        await self._ladder()
        await self._ask_dispute(report)
        return (
            f"candidates {report.candidates} writes {report.writes} "
            f"disputes {report.disputes} promoted {report.staged_promoted} "
            f"stale {report.stale} {report.skipped}".strip()
        )

    async def _ladder(self) -> None:
        """ADR-0020 §4.6: compare eligible signatures and apply one deterministic action."""
        settings = self.config.skills
        if not getattr(settings, "efficiency_enabled", False) or self.skills is None:
            return
        if self.db is None or self.llm is None:
            return
        moment = time.time()
        window_days = float(getattr(settings, "efficiency_window_days", 30.0))
        signatures = await io.run(
            efficiency.signatures, self.db, window_days=window_days, now=moment
        )
        for signature in signatures[: self.LADDER_BATCH]:
            found = await io.run(
                efficiency.evidence, self.db, signature, window_days=window_days, now=moment
            )
            if not efficiency.eligible(
                found,
                min_episodes=int(getattr(settings, "efficiency_min_evidence_episodes", 2)),
            ):
                continue
            # The rows keep each skill's app namespace; a bare logical name would let the
            # ladder read and then mutate a same-named row of another app.
            evidence = await io.run(self.skills.evidence_skills, signature)
            if not evidence:
                continue
            names = list(dict.fromkeys(item.name for item in evidence))
            decided = await self._ladder_decision(found, names)
            if decided is None or str(decided.get("action")) == "keep":
                continue
            await self._ladder_apply(str(decided.get("action")), decided, evidence, found)

    async def _ladder_decision(self, found, names: list[str]) -> dict | None:
        if self.llm is None:
            return None
        payload = {
            "signature": found.signature,
            "episodes": found.as_dict(),
            "skills": [str(name) for name in names],
            "language": self.language,
        }
        try:
            raw = await self.llm.complete(
                self.LADDER_PROMPT, json.dumps(payload), max_tokens=500
            )
        except Exception:
            log.debug("ladder decision failed", exc_info=True)
            return None
        body = _json_object(raw)
        if body is None:
            return None
        action = str(body.get("action") or "").strip().lower()
        skill = str(body.get("skill") or "").strip().lower()
        if action not in self.LADDER_ACTIONS:
            return None
        if action != "keep" and skill not in names:
            return None
        body["action"] = action
        body["skill"] = skill
        return body

    async def _ladder_apply(
        self, action: str, decided: dict, skills: list, found
    ) -> None:
        if self.skills is None:
            return
        skill = str(decided.get("skill") or "")
        reason = str(decided.get("reason") or "")[:200]
        current = next((item for item in skills if item.name == skill), None)
        if current is None and action in ("promote", "merge", "archive"):
            return
        if action == "promote" and current is not None:
            # `app` comes from the evidence row itself: the same name may exist in another app.
            await io.run(
                self.skills.revise,
                current.name,
                current.description,
                current.body,
                scope=current.scope,
                stated_by=current.stated_by,
                trust=current.trust,
                language=self.language,
                app=current.app,
            )
            await io.run(
                self.skills.set_priority,
                current.name,
                current.priority + 1,
                app=current.app,
            )
            await io.run(self.skills.record_notice, current.name, f"promoted: {reason}")
            self.counters["distilled"] += 1
            return
        if action == "merge" and current is not None:
            survivor = current
            others = list(
                dict.fromkeys(item.name for item in skills if item.name != skill)
            )
            merged = survivor.body + "\n\n" + "\n".join(
                f"# {item}" for item in others
            )
            await io.run(
                self.skills.revise,
                survivor.name,
                survivor.description,
                merged[: int(getattr(self.config.skills, "max_body_chars", 18000))],
                scope=survivor.scope,
                stated_by=survivor.stated_by,
                trust=survivor.trust,
                language=self.language,
                app=survivor.app,
            )
            await io.run(self.skills.record_notice, survivor.name, f"merged: {reason}")
            return
        if action == "archive" and current is not None:
            try:
                await io.run(self.skills.archive, skill, app=current.app)
            except KeyError:
                return
            await io.run(self.skills.record_notice, skill, f"archived: {reason}")
            return
        if action == "propose_automation":
            await self._ladder_propose(skill, decided, found, reason)

    async def _ladder_propose(
        self, skill: str, decided: dict, found, reason: str
    ) -> None:
        """Ladder step 8: one approval, nothing stored until the owner approves it."""
        settings = self.config.skills
        if self.skills is None:
            return
        if not getattr(settings, "ladder_enabled", False) or self.propose is None:
            return
        if await io.run(self._proposal_recent, skill):
            await io.run(
                self.skills.record_notice, skill, f"proposal skipped (cooldown): {reason}"
            )
            return
        manifest = decided.get("manifest")
        if not isinstance(manifest, dict):
            await io.run(self.skills.record_notice, skill, "proposal skipped (no manifest)")
            return
        person = found.person_ids[0] if found.person_ids else ""
        thread = ""
        if person and self.private_thread is not None:
            thread = await io.run(self.private_thread, person)
        if not thread:
            await io.run(self.skills.record_notice, skill, "proposal skipped (no dm)")
            return
        try:
            await self.propose(manifest, thread)
        except Exception as exc:
            await io.run(
                self.skills.record_notice, skill, f"proposal refused: {type(exc).__name__}"
            )
            return
        await io.run(self.skills.record_notice, skill, f"proposal:{skill}: {reason}")

    def _proposal_recent(self, skill: str) -> bool:
        """Cooldown: the same skill was proposed inside `proposal_cooldown_days`."""
        if self.db is None:
            return False
        days = float(getattr(self.config.skills, "ladder_proposal_cooldown_days", 14.0))
        if days <= 0:
            return False
        cutoff = time.time() - days * DAY
        found = self.db.fetch_one(
            select(actions.c.id)
            .where(
                actions.c.kind == "skill",
                actions.c.ts >= cutoff,
                actions.c.summary.like(f"proposal:{skill}:%"),
            )
            .limit(1)
        )
        return found is not None

    async def _ask_dispute(self, report) -> None:
        """One question per pass for the first opened pair that names a subject (ADR-0010 §4.7)."""
        if self.ask_dispute is None:
            return
        for pair in getattr(report, "opened", ()) or ():
            if not str(pair.get("subject_id") or "").strip():
                continue
            try:
                await self.ask_dispute(str(pair["subject_id"]), pair)
            except Exception:
                log.warning("dispute question failed", exc_info=True)
            return
