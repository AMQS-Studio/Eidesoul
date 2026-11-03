"""Relay consent: forward another person's answer only with a grant from that person (ADR-0023).

One row per `(task_id, from_person, to_person)` in `relay_grants`; the row records the decision
(`full|summary|anonymous|none`), its scope, its expiry, a revocation, and how it was decided — it
never holds the message content. `mode='none'` is a stored decision, so a refusal stays auditable
and the default is safe: no answer means status only, never content.

The question itself uses the existing approval path: an `approvals` row of kind `consent` (the
class from ADR-0007 §4.7) bound to the content owner's DM. `/approve <id> <mode>` records the
grant, `/deny` leaves it at `none`. Only `from_person` may decide or revoke; neither the requester
nor an administrator can answer in someone else's name.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from sqlalchemy import select, update

from .db import Database
from .i18n import t
from .schema import relay_grants

MODES = ("full", "summary", "anonymous", "none")
DEFAULT_EXPIRES_DAYS = 30.0
DAY_SECONDS = 86400.0
CONSENT = "consent"


class RelayRefused(PermissionError):
    """A relay decision taken by someone other than the content owner."""


@dataclass(frozen=True)
class RelayGrant:
    id: int
    task_id: str
    from_person: str
    to_person: str
    mode: str
    scope_ref: str
    created_at: float
    expires_at: float | None
    revoked_at: float | None
    decided_via: str

    def carries_content(self) -> bool:
        """`none` means status only; every other mode allows the covered content."""
        return self.mode != "none"

    def expired(self, now: float | None = None) -> bool:
        moment = time.time() if now is None else float(now)
        return self.expires_at is not None and float(self.expires_at) <= moment


def _text(value: object, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} is required")
    return text


def _mode(value: object) -> str:
    mode = str(value or "").strip().lower()
    if mode not in MODES:
        raise ValueError(f"mode must be one of {', '.join(MODES)}")
    return mode


class RelayStore:
    """Reads and writes `relay_grants`; every authorization read goes through `check`."""

    def __init__(self, db: Database) -> None:
        self.db = db

    # ------------------------------------------------------------- reads

    def _grant(self, row: dict) -> RelayGrant:
        return RelayGrant(
            id=int(row["id"]),
            task_id=str(row["task_id"]),
            from_person=str(row["from_person"]),
            to_person=str(row["to_person"]),
            mode=str(row["mode"]),
            scope_ref=str(row["scope_ref"]),
            created_at=float(row["created_at"]),
            expires_at=float(row["expires_at"]) if row["expires_at"] is not None else None,
            revoked_at=float(row["revoked_at"]) if row["revoked_at"] is not None else None,
            decided_via=str(row["decided_via"]),
        )

    def get(self, *, task_id: str, from_person: str, to_person: str) -> RelayGrant | None:
        """The raw row, including an expired or revoked grant (audit and tests)."""
        row = self.db.fetch_one(
            select(relay_grants).where(
                relay_grants.c.task_id == str(task_id),
                relay_grants.c.from_person == str(from_person),
                relay_grants.c.to_person == str(to_person),
            )
        )
        return self._grant(row) if row is not None else None

    def check(
        self,
        *,
        task_id: str,
        from_person: str,
        to_person: str,
        now: float | None = None,
    ) -> tuple[RelayGrant | None, str]:
        """`(grant, "")` when the relay is allowed, `(None, reason)` otherwise.

        Expiry and revocation are enforced here, on every read: a stale row never authorizes a
        send, and `none` is refused exactly like a missing row.
        """
        grant = self.get(task_id=task_id, from_person=from_person, to_person=to_person)
        if grant is None:
            return None, "no relay grant for this task"
        if grant.revoked_at is not None:
            return None, "the relay grant was revoked"
        if grant.expired(now):
            return None, "the relay grant has expired"
        if not grant.carries_content():
            return None, "the content owner chose not to forward the content"
        return grant, ""

    def active(
        self, *, task_id: str, from_person: str, to_person: str, now: float | None = None
    ) -> RelayGrant | None:
        return self.check(
            task_id=task_id, from_person=from_person, to_person=to_person, now=now
        )[0]

    # ------------------------------------------------------------ writes

    def _upsert(
        self,
        *,
        task_id: str,
        from_person: str,
        to_person: str,
        scope_ref: str,
        expires_at: float | None,
        now: float,
        mode: str = "none",
        decided_via: str = "",
    ) -> None:
        row = {
            "task_id": task_id,
            "from_person": from_person,
            "to_person": to_person,
            "mode": mode,
            "scope_ref": scope_ref,
            "created_at": now,
            "expires_at": expires_at,
            "revoked_at": None,
            "decided_via": decided_via,
        }
        with self.db.connect() as conn:
            self.db.upsert(
                conn,
                relay_grants,
                [row],
                ["task_id", "from_person", "to_person"],
                update={
                    "mode": mode,
                    "decided_via": decided_via,
                    "scope_ref": scope_ref,
                    "expires_at": expires_at,
                },
            )

    async def ask(
        self,
        agent,
        *,
        task_id: str,
        from_person: str,
        to_person: str,
        scope_ref: str = "",
        thread_id: str = "",
        gateway=None,
        question: str = "",
        to_label: str = "",
        topic: str = "",
        expires_at: float | None = None,
        now: float | None = None,
    ):
        """Ask the content owner, once per task, through the existing consent approval.

        The row is created immediately with `mode='none'`, so an unanswered question can never be
        read as consent. Returns the approval, or `None` when a decision already exists.
        """
        task = _text(task_id, "task_id")
        owner = _text(from_person, "from_person")
        recipient = _text(to_person, "to_person")
        existing = self.get(task_id=task, from_person=owner, to_person=recipient)
        if existing is not None and existing.decided_via:
            return None
        thread = str(thread_id or owner)
        for waiting in agent.approvals.pending(thread, owner):
            if waiting.tool == "relay" and waiting.args.get("task_id") == task:
                return waiting
        moment = time.time() if now is None else float(now)
        deadline = (
            float(expires_at)
            if expires_at is not None
            else moment + DEFAULT_EXPIRES_DAYS * DAY_SECONDS
        )
        self._upsert(
            task_id=task,
            from_person=owner,
            to_person=recipient,
            scope_ref=str(scope_ref or ""),
            expires_at=deadline,
            now=moment,
        )
        lang = str(getattr(getattr(agent, "soul", None), "language", "") or "id")
        text = question or t(
            "relay.ask",
            lang,
            to=to_label or recipient,
            topic=topic or scope_ref or task,
        )
        approval = await agent.approvals.create(
            kind=CONSENT,
            thread_id=str(thread_id or owner),
            sender=owner,
            gateway=getattr(gateway, "name", "") or "",
            tool="relay",
            args={
                "task_id": task,
                "from_person": owner,
                "to_person": recipient,
                "scope_ref": str(scope_ref or ""),
            },
            payload={
                "task_id": task,
                "from_person": owner,
                "to_person": recipient,
                "scope_ref": str(scope_ref or ""),
                "modes": list(MODES),
                "question": text,
            },
            summary=text,
        )
        if thread_id:
            await agent.send_text(str(thread_id), text, gateway=gateway)
        return approval

    def decide(
        self,
        *,
        approval,
        mode: str,
        by: str,
        via: str = CONSENT,
        now: float | None = None,
    ) -> RelayGrant:
        """Record the owner's answer; only the content owner may decide (ADR-0023 §4.5)."""
        chosen = _mode(mode)
        payload = approval.payload if isinstance(approval.payload, dict) else {}
        task = _text(payload.get("task_id"), "task_id")
        owner = _text(payload.get("from_person"), "from_person")
        recipient = _text(payload.get("to_person"), "to_person")
        if str(approval.sender or "").strip() != owner:
            raise RelayRefused("the approval was not addressed to the content owner")
        if str(by or "").strip() != owner:
            raise RelayRefused("only the content owner can decide a relay grant")
        existing = self.get(task_id=task, from_person=owner, to_person=recipient)
        if existing is not None and existing.revoked_at is not None:
            raise RelayRefused("the relay grant was revoked")
        moment = time.time() if now is None else float(now)
        self._upsert(
            task_id=task,
            from_person=owner,
            to_person=recipient,
            scope_ref=str(payload.get("scope_ref") or ""),
            expires_at=(
                existing.expires_at
                if existing is not None and existing.expires_at is not None
                else moment + DEFAULT_EXPIRES_DAYS * DAY_SECONDS
            ),
            now=moment,
            mode=chosen,
            decided_via=via,
        )
        found = self.get(task_id=task, from_person=owner, to_person=recipient)
        if found is None:  # pragma: no cover - the upsert just wrote the row
            raise RuntimeError("the relay grant could not be read back")
        return found

    def revoke(
        self,
        *,
        task_id: str,
        from_person: str,
        to_person: str,
        by: str,
        now: float | None = None,
    ) -> bool:
        """Stop the next report; an already-sent report is not withdrawn (ADR-0023 §4.4)."""
        if str(by or "").strip() != str(from_person or "").strip():
            raise RelayRefused("only the content owner can revoke a relay grant")
        moment = time.time() if now is None else float(now)
        with self.db.connect() as conn:
            outcome = conn.execute(
                update(relay_grants)
                .where(
                    relay_grants.c.task_id == str(task_id),
                    relay_grants.c.from_person == str(from_person),
                    relay_grants.c.to_person == str(to_person),
                    relay_grants.c.revoked_at.is_(None),
                )
                .values(revoked_at=moment)
            )
            return int(outcome.rowcount or 0) == 1
