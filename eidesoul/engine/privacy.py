"""Agent-facing privacy service: pending confirmations, capture assessment, pre-delivery review.

All level maths and veto precedence live in `memory.privacy.PrivacyPolicy`; this module only
talks to the LLM for drafting a level/question and for spotting which memory items a draft
mentions. Confirmation questions are phrased by the model in the soul's language, with a
short fallback when the LLM is unavailable.
"""


import json
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass

from .character import lexicon
from .config import PrivacyConfig
from .i18n import t
from .memory import MemoryHit
from .memory.privacy import AuditResult, PrivacyPolicy, clamp_level

log = logging.getLogger(__name__)

KINDS = ("privacy", "disclosure")
# Agreement tokens are shared accept-words, not a negation list; the pack schema carries only the
# negative side (`negations`).
YES = ("ya", "yes", "y", "ok", "oke", "boleh", "sip", "gas", "lanjut", "silakan")
LEVEL_WORDS = (
    ("rahasia", 4),
    ("secret", 4),
    ("sensitif", 3),
    ("sensitive", 3),
    ("aib", 3),
    ("personal", 2),
    ("pribadi", 2),
    ("biasa", 2),
    ("umum", 0),
    ("publik", 0),
    ("public", 0),
)

ANSWER_PROMPT = """Classify the sender's answer to a pending confirmation.
Reply ONLY with JSON: {"kind":"yes|no|level|unclear","level":0,"reason":""}
- "level" when they name a privacy level (0-4, or public/team/personal/sensitive/secret).
- "yes" when they agree; "no" when they refuse; "unclear" otherwise.
"level" is 0-4 only for kind=level, otherwise 0."""

AUDIT_PROMPT = """You audit one draft reply against memory entries the model was shown.
Reply ONLY with JSON:
{"mentions":["memory-id"],"verdict":"allow|ask|redact|deny","reply":"","question":"","reason":""}
Rules:
- "mentions" lists only the IDs of entries the draft actually uses or paraphrases. Never
  invent IDs; use an empty list when the draft uses none.
- Do not repeat any secret in "reason".
- "reply" may hold a safer rewrite when verdict is "redact"; otherwise "".
- "question": when the deterministic verdicts include "ask", one short question in the
  soul's language asking permission to share; otherwise "".
- The final decision is made deterministically from the entry levels and ownership; your job
  is only mention detection and a safe rewrite."""


@dataclass(frozen=True)
class ReviewResult:
    handled: bool = False
    reply: str = ""


def _json(text: str) -> dict | None:
    raw = str(text or "").strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        body = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return None
    return body if isinstance(body, dict) else None


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
        return str(self.agent.soul.language or "id")

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
        if self.config.auditor and self.agent.llm is not None:
            audit = await self._audit(reply, hits, inbound)
        else:
            audit = AuditResult(verdict="ask")
        selected = [hit for hit in hits if hit.id in audit.mentions] or risky
        requester = inbound.actor or self.agent._sender(inbound)
        verdicts = [
            self.policy.disclose(hit, requester=requester, direct=direct) for hit in selected
        ]
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

    def _question(self, audit: AuditResult) -> str:
        question = " ".join(str(audit.question or "").split())
        return question or self._text("ask_disclosure")

    async def _audit(
        self, reply: str, hits: Sequence[MemoryHit], inbound
    ) -> AuditResult:
        requester = inbound.actor or self.agent._sender(inbound)
        lines = []
        for hit in hits:
            verdict = self.policy.disclose(
                hit, requester=requester, direct=bool(inbound.hints.get("dm"))
            )
            lines.append(
                f"- id={hit.id} level={hit.level} owner={hit.owner_id or '-'} "
                f"subject={hit.subject_id or '-'} confirmed={hit.confirmed} "
                f"vetoed={hit.vetoed} policy={verdict} text={hit.text}"
            )
        user = (
            f"Draft reply:\n{reply}\n\nMemory entries:\n" + "\n".join(lines)
            + f"\n\nSoul language: {self._lang}"
        )
        raw = await self.agent.llm.complete(AUDIT_PROMPT, user, max_tokens=300)
        body = _json(raw)
        if body is None:
            return AuditResult(verdict="ask")
        mentions = tuple(
            str(item) for item in body.get("mentions") or [] if isinstance(item, str)
        )
        verdict = str(body.get("verdict") or "").strip().lower()
        return AuditResult(
            mentions=mentions,
            verdict=verdict if verdict in {"allow", "ask", "redact", "deny"} else "ask",
            reply=str(body.get("reply") or "").strip(),
            question=str(body.get("question") or "").strip(),
            reason=str(body.get("reason") or "")[:120],
        )

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
        thread = str(agent.history.dm_thread(subject) or "")
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
        sent = await agent.send_text(thread, text, gateway=agent._gateway_for(thread))
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
        gateway = agent._turn_gateways.get(thread_id) or (
            agent.gateways[0] if agent.gateways else None
        )
        if gateway is None:
            return
        text = question or self._text("ask_level")
        approval = await agent.approvals.create(
            kind="privacy",
            thread_id=thread_id,
            sender=person_id,
            gateway=getattr(gateway, "name", "") or "",
            tool="memory",
            args={"memory_ids": list(memory_ids)},
            payload={"level": int(level), "question": text},
            summary=text,
        )
        log.info("privacy pending thread=%s approval=%s", thread_id, approval.id)
        await agent.send_text(thread_id, text, gateway=gateway)

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
        if self.agent.llm is not None:
            raw = await self.agent.llm.complete(
                ANSWER_PROMPT, f"Pending kind: {kind}\nAnswer:\n{text}", max_tokens=80
            )
            body = _json(raw)
            if body is not None:
                parsed = str(body.get("kind") or "").strip().lower()
                if parsed in {"yes", "no", "level", "unclear"}:
                    level = None
                    if parsed == "level":
                        try:
                            number = int(body.get("level"))
                        except (TypeError, ValueError):
                            number = -1
                        level = number if 0 <= number <= 4 else None
                        if level is None:
                            parsed = "unclear"
                    return {"kind": parsed, "level": level}
        return self._regex_answer(str(text or ""))

    def _regex_answer(self, text: str) -> dict:
        words = set(re.findall(r"[a-z]+", text.lower()))
        for word, level in LEVEL_WORDS:
            if word in words:
                return {"kind": "level", "level": level}
        if words & set(YES):
            return {"kind": "yes", "level": None}
        if words & lexicon.load(self._lang).negations:
            return {"kind": "no", "level": None}
        digits = [int(ch) for ch in text if ch.isdigit() and int(ch) <= 4]
        if digits:
            return {"kind": "level", "level": digits[0]}
        return {"kind": "unclear", "level": None}
