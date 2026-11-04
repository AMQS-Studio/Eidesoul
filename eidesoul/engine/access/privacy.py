"""Agent-facing privacy service: pending confirmations, capture assessment, pre-delivery review.

All level maths and veto precedence live in `memory.privacy.PrivacyPolicy`; this module only
drafts the question, detects mentions locally, and records the pending answer. No memory text
is sent to an external model (ADR-0038 §4.5).
"""


import hashlib
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass

from ..character import lexicon
from ..config import PrivacyConfig
from ..i18n import t
from ..memory import MemoryHit
from ..memory.evidence import POLICY_VERSION
from ..memory.privacy import AuditResult, PrivacyPolicy, clamp_level

log = logging.getLogger(__name__)

KINDS = ("privacy", "disclosure")

@dataclass(frozen=True)
class ReviewResult:
    handled: bool = False
    reply: str = ""


class PrivacyService:
    def __init__(self, agent) -> None:
        self.agent = agent
        self.configure(agent.config.memory.privacy)

    def configure(self, config: PrivacyConfig) -> None:
        self.config = config
        self.policy = PrivacyPolicy(config)

    # ------------------------------------------------------------- helpers

    @property
    def _lang(self) -> str:
        return str(self.agent.soul.language)

    def _text(self, key: str, **kwargs) -> str:
        return t(f"privacy.{key}", self._lang, **kwargs)

    def _risky(self, hit: MemoryHit, *, direct: bool) -> bool:
        limit = self.config.dm_max_level if direct else self.config.group_max_level
        if hit.level > limit or hit.vetoed:
            return True
        return hit.level >= self.config.ask_level and not hit.confirmed

    # -------------------------------------------------------------- review

    async def review(
        self,
        inbound,
        reply: str,
        hits: Sequence[MemoryHit],
        gateway,
    ) -> ReviewResult:
        if not self.config.enabled or not hits:
            return ReviewResult(False, reply)
        direct = bool(inbound.hints.get("dm"))
        risky = [hit for hit in hits if self._risky(hit, direct=direct)]
        if not risky:
            return ReviewResult(False, reply)
        audit = self._audit(reply, hits, inbound) if self.config.auditor else AuditResult(
            verdict="ask"
        )
        selected = [hit for hit in hits if hit.id in audit.mentions] or risky
        requester = inbound.actor or self.agent._sender(inbound)
        verdicts = [
            self.policy.disclose(hit, requester=requester, direct=direct) for hit in selected
        ]
        self._record(inbound, selected, verdicts, reply=reply, policy=POLICY_VERSION)
        if "deny" in verdicts:
            return ReviewResult(False, audit.reply or self._text("keep"))
        if "ask" in verdicts:
            question = self._question(audit)
            approval = await self.agent.approvals.create(
                kind="disclosure",
                thread_id=inbound.thread,
                sender=self.agent._sender(inbound),
                gateway=getattr(gateway, "name", "") or "",
                tool="disclosure",
                args={"memory_ids": [hit.id for hit in selected]},
                payload={"reply": reply, "question": question},
                summary=question,
            )
            log.info("disclosure pending chat=%s approval=%s", inbound.thread, approval.id)
            await self.agent.send_text(inbound.thread, question, gateway=gateway)
            return ReviewResult(True, "")
        return ReviewResult(False, audit.reply or reply)

    def _record(
        self,
        inbound,
        selected: Sequence[MemoryHit],
        verdicts: Sequence[str],
        *,
        reply: str,
        policy: str,
    ) -> None:
        ledger = getattr(self.agent, "ledger", None)
        if ledger is None:
            return
        ids = ",".join(hit.id for hit in selected)
        levels = ",".join(str(hit.level) for hit in selected)
        digest = hashlib.sha256(str(reply or "").encode("utf-8")).hexdigest()[:16]
        ledger.log_action(
            "privacy",
            inbound.thread,
            "privacy",
            f"ids={ids} levels={levels} verdicts={','.join(verdicts)} "
            f"hash={digest} policy={policy}",
        )

    def _question(self, audit: AuditResult) -> str:
        question = " ".join(str(audit.question or "").split())
        return question or self._text("ask_disclosure")

    def _audit(self, reply: str, hits: Sequence[MemoryHit], inbound) -> AuditResult:
        """Local mention detection: no memory text leaves the host (ADR-0038 §4.5)."""
        words = set(re.findall(r"[\w]{4,}", str(reply or "").lower()))
        mentions: list[str] = []
        for hit in hits:
            tokens = set(re.findall(r"[\w]{4,}", str(hit.text or "").lower()))
            if tokens and len(tokens & words) >= min(2, len(tokens)):
                mentions.append(hit.id)
        return AuditResult(mentions=tuple(mentions), verdict="allow")

    # ------------------------------------------------------------- questions

    async def ask_dispute(self, subject_id: str, pair: dict) -> str:
        """ADR-0010 §4.7: one dispute question per subject per day, in that subject's DM.

        The target is a real DM thread (`threads.kind='dm'`, single member); when the body cannot
        prove one, or the daily budget is spent, nothing is sent and nothing is asked. The answer
        arrives as an ordinary turn, so no approval row is created here.
        """
        agent = self.agent
        subject = str(subject_id or "").strip()
        ids = [str(item) for item in (pair.get("ids") or [])]
        if not subject or not ids:
            return "skipped: no subject"
        budget = int(getattr(agent.config.reflection, "questions_per_day", 1))
        if budget <= 0:
            return "skipped: questions are off"
        thread = str(agent.private_thread(subject) or "")
        if not thread:
            log.info("dispute question skipped: no dm for %s", subject)
            return "skipped: no dm"
        if int(agent.memory.disputes_asked_today(subject)) >= budget:
            log.info("dispute question skipped: daily budget for %s", subject)
            return "skipped: budget"
        text = t(
            "memory.dispute_question",
            self._lang,
            first=str(pair.get("first") or "").strip()[:160],
            second=str(pair.get("second") or "").strip()[:160],
        )
        body = agent.body_of(thread)
        if body is None:
            log.info("dispute question skipped: no body owns %s", thread)
            return "skipped: no dm"
        sent = await agent.send_text(thread, text, gateway=body, subject=subject)
        if not str(sent).startswith("ok:"):
            return f"failed: {sent}"
        agent.memory.mark_dispute_asked(ids)
        log.info("dispute question asked thread=%s subject=%s", thread, subject)
        return "ok: asked"

    async def ask_memory(
        self,
        thread_id: str,
        person_id: str,
        memory_ids: list[str],
        level: int,
        question: str = "",
    ) -> None:
        """ADR-0004/0006: a DM confirmation for a memory the assess left unconfirmed."""
        agent = self.agent
        if not thread_id or not memory_ids:
            return
        body = agent.body_of(thread_id)
        if body is None:
            return
        text = question or self._text("ask_level")
        approval = await agent.approvals.create(
            kind="privacy",
            thread_id=thread_id,
            sender=person_id,
            gateway=str(body.name or ""),
            tool="memory",
            args={"memory_ids": list(memory_ids)},
            payload={"level": int(level), "question": text},
            summary=text,
        )
        log.info("privacy pending thread=%s approval=%s", thread_id, approval.id)
        await agent.send_text(thread_id, text, gateway=body, subject=person_id)

    # ------------------------------------------------------------- pending

    async def handle_pending(self, inbound, gateway, sender: str) -> bool:
        pending = self.agent.approvals.latest(inbound.thread, sender)
        if pending is None or pending.kind not in KINDS:
            return False
        answer = await self._resolve_answer(inbound.payload.text, pending.kind)
        if pending.kind == "privacy":
            await self._resolve_privacy(inbound, gateway, pending, answer, sender)
        else:
            await self._resolve_disclosure(inbound, gateway, pending, answer, sender)
        return True

    async def _resolve_privacy(
        self, inbound, gateway, pending, answer: dict, sender: str
    ) -> None:
        ids = [str(item) for item in pending.args.get("memory_ids") or []]
        kind = answer.get("kind")
        if kind == "no":
            for memory_id in ids:
                await self.agent.memory.forget(
                    memory_id,
                    requester_id=inbound.actor or sender,
                    thread_id=inbound.thread,
                )
            self.agent.approvals.mark(pending.id, "denied", sender, "privacy declined")
            await self.agent.send_text(inbound.thread, self._text("keep"), gateway=gateway)
            return
        level = answer.get("level")
        if level is None and kind == "yes":
            level = pending.payload.get("level")
        if level is None:
            await self.agent.send_text(inbound.thread, self._text("unclear"), gateway=gateway)
            return
        level = clamp_level(level, self.config.default_level)
        for memory_id in ids:
            await self.agent.memory.set_privacy(memory_id, level=level, confirmed=True)
        self.agent.approvals.mark(pending.id, "approved", sender, f"level {level}")
        await self.agent.send_text(
            inbound.thread, self._text("saved", level=level), gateway=gateway
        )

    async def _resolve_disclosure(
        self, inbound, gateway, pending, answer: dict, sender: str
    ) -> None:
        kind = answer.get("kind")
        if kind == "no":
            self.agent.approvals.mark(pending.id, "denied", sender, "disclosure declined")
            await self.agent.send_text(inbound.thread, self._text("keep"), gateway=gateway)
            return
        if kind != "yes":
            await self.agent.send_text(inbound.thread, self._text("unclear"), gateway=gateway)
            return
        self.agent.approvals.mark(pending.id, "approved", sender, "disclosure allowed")
        reply = str(pending.payload.get("reply") or "")
        if not reply.strip():
            await self.agent.send_text(inbound.thread, self._text("empty"), gateway=gateway)
            return
        await self.agent.send_text(inbound.thread, reply, gateway=gateway)

    async def _resolve_answer(self, text: str, kind: str) -> dict:
        return self._regex_answer(str(text or ""))

    def _regex_answer(self, text: str) -> dict:
        words = set(re.findall(r"[a-z]+", text.lower()))
        pack = lexicon.load(self._lang)
        for word, level in pack.level_words.items():
            if word in words:
                return {"kind": "level", "level": level}
        if words and words <= pack.affirmations:
            return {"kind": "yes", "level": None}
        if words and words <= pack.negations:
            return {"kind": "no", "level": None}
        digits = [int(ch) for ch in text if ch.isdigit() and int(ch) <= 4]
        if digits:
            return {"kind": "level", "level": digits[0]}
        return {"kind": "unclear", "level": None}
