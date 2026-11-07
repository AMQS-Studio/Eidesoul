"""Cognition capture and consolidation handlers (ADR-0002): novelty, assess, label, redact."""

import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING

from .. import claims, io
from ..memory import assess as memory_assess
from ..memory import evidence as privacy_layers
from ..memory import gate, intent
from ..schema import memories, memory_events
from .cognition_queue import Job, _scope_of

if TYPE_CHECKING:
    pass

from typing import Any

from ..config import Config

log = logging.getLogger(__name__)
class CognitionCapture:
    """The capture, consolidate, and reflect job bodies."""

    config: Config
    memory: Any
    llm: Any
    skills: Any
    history: Any
    language: str
    counters: dict[str, int]
    ask: Any

    async def _capture(self, jobs: list[Job]) -> dict[str, str]:
        """Stage 2 + 3 of ADR-0002, batched per scope: novelty, then one assess call."""
        results: dict[str, str] = {}
        capture = self.config.memory.capture
        grouped: dict[str, list[Job]] = {}
        for job in jobs:
            grouped.setdefault(job.scope, []).append(job)
        for scope, group in grouped.items():
            rows: list[tuple[Job, dict]] = []
            for job in group:
                row = await io.run(self.memory.candidate_by_event, job.payload_ref)
                if row is None or str(row.get("state") or "") != "pending":
                    results[job.id] = "no candidate"
                    continue
                rows.append((job, row))
            if not rows:
                continue
            kind, _value = _scope_of(scope)
            person_id = str(rows[0][1].get("person_id") or "") if kind != "thread" else ""
            thread_id = str(rows[0][1].get("thread_id") or "")
            pool = await io.run(
                self.memory.novelty_pool,
                scope=scope,
                person_id=person_id,
                thread_id=thread_id,
                limit=max(50, int(self.config.memory.worker.capture_max_items) * 10),
            )
            novel: list[tuple[Job, dict]] = []
            for job, row in rows:
                found = gate.novelty(
                    str(row.get("text") or ""),
                    pool,
                    threshold=float(capture.novelty_threshold),
                )
                if found.verdict == "duplicate":
                    await io.run(self.memory.mark_not_worth, str(row["id"]), "duplicate")
                    results[job.id] = "duplicate"
                    continue
                if found.verdict == "known":
                    await io.run(
                        self.memory.mark_supersede_candidate,
                        str(row["id"]),
                        found.source_id,
                    )
                    results[job.id] = "supersede candidate"
                    continue
                novel.append((job, row))
            if not novel:
                continue
            items = [
                {"id": str(row["id"]), "text": str(row.get("text") or "")}
                for _, row in novel
            ]
            verdicts = await memory_assess.assess_batch(
                self.llm,
                items,
                language=self.language,
            )
            for job, row in novel:
                results[job.id] = await self._label(row, verdicts.get(str(row["id"])))
        return results

    async def _drop_reason(self, body: str, verdict) -> str:
        """Why this candidate is not worth storing; "" keeps it (note.md §10 §6)."""
        if getattr(self.config.memory, "claim_filter", True) and await claims.detect(
            self.llm, body, self.language
        ):
            return "claim: not evidence"
        return "" if verdict.worth else "assess: not worth"

    def _redact_credential(self, memory_id: str) -> bool:
        """ADR-0002 §4.4: no credential text survives in the row or its audit trail."""
        store = self.memory
        if getattr(store, "db", None) is None:
            return False
        with store.db.connect() as conn:
            store._fence(conn, "_redact_credential")
            statement = (
                memories.update()
                .where(memories.c.id == str(memory_id))
                .values(text=gate.REDACTED_CREDENTIAL)
            )
            fence = store._fence_clause()
            if fence is not None:
                statement = statement.where(fence)
            conn.execute(statement)
            # ADR-0002 §4.4: the journal row can repeat the raw turn, so it is redacted in
            # the same transaction as the canonical row. The hash stays untouched: it is a
            # digest, never the secret, and intake dedupe still keys on it.
            conn.execute(
                memory_events.update()
                .where(memory_events.c.memory_id == str(memory_id))
                .values(content=gate.REDACTED_CREDENTIAL)
            )
        return True

    async def _label(self, row: Mapping, verdict) -> str:
        memory_id = str(row["id"])
        body = str(row.get("text") or (verdict.text if verdict is not None else "") or "")
        evidence, decision = privacy_layers.classify(body, self.language)
        # ADR-0009 §4.1: the deterministic credential gate runs before the verdict, so a
        # secret in a `worth: false` or invalid turn is redacted in both the row and journal.
        if decision.action == "drop":
            await io.run(self._redact_credential, memory_id)
            await io.run(self.memory.mark_not_worth, memory_id, "privacy: credential")
            return "credential dropped"
        if verdict is None or not verdict.valid:
            await io.run(self.memory.fail_candidate, memory_id, "labeling failed")
            return "dropped"
        model_level = getattr(verdict, "level", None)
        model_category = str(getattr(verdict, "category", "") or "").strip().lower()
        if model_level == 5 or model_category == "credential":
            await io.run(self._redact_credential, memory_id)
            await io.run(self.memory.mark_not_worth, memory_id, "privacy: credential")
            return "credential dropped"
        reason = await self._drop_reason(body, verdict)
        if reason:
            await io.run(self.memory.mark_not_worth, memory_id, reason)
            return "not worth"
        group = str(row.get("audience") or "private") == "group"
        owner = str(row.get("owner_id") or "")
        subject = str(verdict.subject or "")
        rules = self.config.memory.privacy.group_capture
        if group and rules.self_only and subject and subject != owner:
            if rules.third_party == "deny":
                await io.run(self.memory.defer_candidate, memory_id, reason="third party")
                return "third party held"
            await io.run(self.memory.mark_not_worth, memory_id, "third party")
            return "third party dropped"
        if group and decision.ask and not rules.ask_in_group:
            await io.run(self.memory.defer_candidate, memory_id, reason="ask in group")
            return "ask pending"
        # ADR-0009 §4.2: the model may raise the deterministic floor, never lower it. A
        # level-4+ memory is always unconfirmed and asks; a level-3 one stays confirmed.
        level = int(decision.level)
        if model_level is not None:
            level = max(level, int(model_level))
        # ADR-0009 §4.2: the category is itself a floor. A `health` verdict that reports a low
        # level still cannot be stored below the level the category requires.
        level = max(level, privacy_layers.LEVELS.get(model_category, 0))
        ask = bool(decision.ask or level >= 4)
        category, durability = await self._remember_hint(row, verdict)
        labelled = await io.run(
            self.memory.label_candidate,
            memory_id,
            level=level,
            confirmed=not ask,
            subject_id=subject or owner,
            category=category,
            durability=durability,
            kind=str(verdict.kind or ""),
            text=body,
            evidence=[item.record() for item in evidence],
            policy=decision.policy_version,
        )
        if not labelled:
            return "label refused"
        self.counters["captured"] += 1
        await self._visibility(row, level)
        if ask and self.ask is not None:
            try:
                await self.ask(
                    str(row.get("thread_id") or ""),
                    owner,
                    [memory_id],
                    level,
                    "",
                )
            except Exception:
                log.debug("ask callback failed", exc_info=True)
        return f"ok L{level}"

    async def _remember_hint(self, row: Mapping, verdict) -> tuple[str, str]:
        """ADR-0005 §4.6: "remember this" raises durability and pins only a core memory.

        The hint never overrides a verdict: a `not worth` candidate is dropped before this runs,
        and the hard exclusion already refused credentials and instructions at intake. The
        embedding runs on the bounded pool, never on the event loop (ADR-0003 §4.1).
        """
        category = str(verdict.category or "")
        durability = str(verdict.durability or "")
        settings = getattr(self.config.memory, "intent", None)
        text = str(verdict.text or row.get("text") or "")
        kind = await io.run(intent.classify, text, self.language, settings)
        if kind != "remember":
            return category, durability
        if durability != "core":
            durability = "core"
        return category, durability

    async def _visibility(self, row: Mapping, level: int) -> None:
        if self.history is None:
            return
        refs = [str(item) for item in row.get("refs") or []]
        ids = [item.split(":", 1)[1] for item in refs if item.startswith("message:")]
        if not ids:
            return
        try:
            await io.run(
                self.history.set_visibility, str(row.get("thread_id") or ""), ids, level
            )
        except Exception:
            log.debug("visibility update failed", exc_info=True)

    async def _consolidate(self, jobs: list[Job]) -> dict[str, str]:
        results: dict[str, str] = {}
        for job in jobs:
            resolved = 0
            rows = await io.run(
                self.memory.supersede_candidates, scope=job.scope, limit=50
            )
            for row in rows:
                marker = next(
                    (
                        item.split(":", 1)[1]
                        for item in row.get("refs") or []
                        if str(item).startswith("supersede:")
                    ),
                    "",
                )
                if marker and await io.run(
                    self.memory.resolve_supersede_candidate, str(row["id"]), marker
                ):
                    resolved += 1
            promoted = await io.run(self.memory.promote_staged, limit=50)
            decayed = 0
            if self.skills is not None:
                decayed = await io.run(
                    self.skills.decay, days=float(self.config.skills.use_renewal_days)
                )
            results[job.id] = f"supersede {resolved} promote {promoted} decay {decayed}"
        return results

