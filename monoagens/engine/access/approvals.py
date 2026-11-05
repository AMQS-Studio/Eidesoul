"""Approval queue: sensitive actions wait for /approve, bound to chat+sender+TTL."""


import json
import time
from dataclasses import dataclass, field
from uuid import uuid4

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError

from .. import io
from ..config import ApprovalConfig
from ..db import Database
from ..jobs.lease import FencedStore, Superseded, fenced_insert
from ..schema import approvals


@dataclass(frozen=True)
class Approval:
    id: int
    created_at: float
    expires_at: float
    thread_id: str
    sender: str
    gateway: str
    kind: str
    tool: str
    args: dict
    payload: dict
    summary: str
    diff: str
    status: str
    decided_by: str
    decided_at: float | None
    result: str
    approval_class: str = "authorization"
    scope_proof: dict = field(default_factory=dict)
    action_id: str = ""
    origin_gateway: str = ""
    origin_thread: str = ""
    batch_id: str = ""

    @property
    def roles(self) -> tuple[str, ...]:
        found = self.payload.get("roles") if isinstance(self.payload, dict) else None
        return tuple(str(item) for item in found) if isinstance(found, list) else ()


class ApprovalStore(FencedStore):
    def __init__(self, db: Database, config: ApprovalConfig) -> None:
        self.db = db
        self.configure(config)

    def configure(self, config: ApprovalConfig) -> None:
        self.config = config

    def _approval(self, row: dict) -> Approval:
        return Approval(
            id=int(row["id"]),
            created_at=float(row["created_at"]),
            expires_at=float(row["expires_at"]),
            thread_id=str(row["thread_id"]),
            sender=str(row["sender"]),
            gateway=str(row["gateway"]),
            kind=str(row["kind"]),
            tool=str(row["tool"]),
            args=row["args"] if isinstance(row["args"], dict) else {},
            payload=row["payload"] if isinstance(row["payload"], dict) else {},
            summary=str(row["summary"]),
            diff=str(row["diff"]),
            status=str(row["status"]),
            decided_by=str(row["decided_by"]),
            decided_at=float(row["decided_at"]) if row["decided_at"] else None,
            result=str(row["result"]),
            approval_class=str(row.get("approval_class") or "authorization"),
            scope_proof=row["scope_proof"] if isinstance(row.get("scope_proof"), dict) else {},
            action_id=str(row.get("action_id") or ""),
            origin_gateway=str(row.get("origin_gateway") or ""),
            origin_thread=str(row.get("origin_thread") or ""),
            batch_id=str(row.get("batch_id") or ""),
        )

    @staticmethod
    def _fingerprint(kind: str, tool: str, args: dict, payload: dict) -> str:
        return json.dumps(
            {"kind": kind, "tool": tool, "args": args, "payload": payload},
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )

    async def create(
        self,
        *,
        kind: str,
        thread_id: str,
        sender: str,
        gateway: str = "",
        tool: str = "",
        args: dict | None = None,
        payload: dict | None = None,
        summary: str = "",
        diff: str = "",
        roles: tuple[str, ...] = (),
        approval_class: str = "authorization",
        scope_proof: dict | None = None,
        action_id: str = "",
        origin_gateway: str = "",
        origin_thread: str = "",
        batch_id: str = "",
    ) -> Approval:
        return await io.run(
            self._create_sync,
            kind=kind,
            thread_id=thread_id,
            sender=sender,
            gateway=gateway,
            tool=tool,
            args=dict(args or {}),
            payload=dict(payload or {}),
            summary=summary,
            diff=diff,
            roles=roles,
            approval_class=approval_class,
            scope_proof=dict(scope_proof or {}),
            action_id=str(action_id or "").strip(),
            origin_gateway=str(origin_gateway or "").strip(),
            origin_thread=str(origin_thread or "").strip(),
            batch_id=str(batch_id or "").strip(),
        )

    def _create_sync(
        self,
        *,
        kind: str,
        thread_id: str,
        sender: str,
        gateway: str,
        tool: str,
        args: dict,
        payload: dict,
        summary: str,
        diff: str,
        roles: tuple[str, ...],
        approval_class: str,
        scope_proof: dict,
        action_id: str,
        origin_gateway: str,
        origin_thread: str,
        batch_id: str,
    ) -> Approval:
        self.expire()
        if roles:
            payload["roles"] = list(roles)
        wanted = self._fingerprint(kind, tool, args, payload)
        for existing in self.pending(thread_id, sender):
            same = self._fingerprint(
                existing.kind, existing.tool, existing.args, existing.payload
            )
            if same == wanted:
                return existing
        if action_id:
            # ADR-0004 §4.2: one row per outbound action id, whatever its status.
            found = self.find_action(action_id)
            if found is not None:
                return found
        now = time.time()
        row = {
            "created_at": now,
            "expires_at": now + float(self.config.ttl_seconds),
            "thread_id": thread_id,
            "sender": sender,
            "gateway": gateway,
            "kind": kind,
            "tool": tool,
            "args": args,
            "payload": payload,
            "summary": summary,
            "diff": diff,
            "status": "pending",
            "decided_by": "",
            "decided_at": None,
            "result": "",
            "approval_class": "consent" if approval_class == "consent" else "authorization",
            "scope_proof": scope_proof,
            "action_id": action_id,
            "origin_gateway": origin_gateway or gateway,
            "origin_thread": origin_thread or thread_id,
            "batch_id": batch_id or self._next_batch(thread_id, sender),
        }
        try:
            with self.db.connect() as conn:
                fence = self.fence_clause()
                found_id = conn.execute(
                    fenced_insert(approvals, row, fence).returning(approvals.c.id)
                ).first()
                if found_id is None and fence is not None:
                    raise Superseded("lease generation moved on: approval refused")
                if found_id is None:  # pragma: no cover - an unfenced insert always returns
                    raise RuntimeError("the new approval got no primary key")
                new_id = int(found_id[0])
        except IntegrityError:
            # A concurrent caller staged the same action id first; that row is the answer.
            found = self.find_action(action_id) if action_id else None
            if found is None:
                raise
            return found
        found = self.get(new_id)
        if found is None:  # pragma: no cover - a new row is always readable
            raise RuntimeError("the new approval could not be read back")
        return found

    def _next_batch(self, thread_id: str, sender: str) -> str:
        """One conversational workflow stages its rows under the same batch (ADR-0006 §4.5)."""
        rows = self.pending(thread_id, sender)
        if rows and rows[-1].batch_id:
            return rows[-1].batch_id
        return f"batch-{uuid4().hex[:12]}"

    def consent_allowed(self, sender: str) -> bool:
        """ADR-0007 §5: `consent_max_per_hour` bounds self-confirmation, never a permission."""
        limit = int(self.config.consent_max_per_hour)
        if limit <= 0:
            return True
        since = time.time() - 3600
        found = self.db.fetch_all(
            select(approvals.c.id).where(
                approvals.c.sender == sender,
                approvals.c.approval_class == "consent",
                approvals.c.created_at >= since,
            )
        )
        return len(found) < limit

    def get(self, approval_id: int | str) -> Approval | None:
        try:
            key = int(approval_id)
        except (TypeError, ValueError):
            return None
        found = self.db.fetch_one(select(approvals).where(approvals.c.id == key))
        return self._approval(found) if found else None

    def pending(self, thread_id: str, sender: str) -> list[Approval]:
        """Rows waiting for this person, staged in this room or targeting it (ADR-0004 §4.2)."""
        now = time.time()
        statement = (
            select(approvals)
            .where(
                or_(
                    approvals.c.thread_id == thread_id,
                    approvals.c.origin_thread == thread_id,
                ),
                approvals.c.sender == sender,
                approvals.c.status == "pending",
                approvals.c.expires_at >= now,
            )
            .order_by(approvals.c.created_at, approvals.c.id)
        )
        return [self._approval(row) for row in self.db.fetch_all(statement)]

    def latest(self, thread_id: str, sender: str) -> Approval | None:
        found = self.pending(thread_id, sender)
        return found[-1] if found else None

    def find_action(self, action_id: str, *, limit: int = 50) -> Approval | None:
        """The row for an outbound correlation id, whatever its status (ADR-0004 §4.2)."""
        key = str(action_id or "").strip()
        if not key:
            return None
        found = self.db.fetch_one(
            select(approvals)
            .where(approvals.c.action_id == key)
            .order_by(approvals.c.id.desc())
            .limit(1)
        )
        return self._approval(found) if found else None

    def batch(self, thread_id: str, sender: str, batch_id: str = "") -> list[Approval]:
        """Every pending row of one workflow batch (the newest batch when none is named)."""
        rows = self.pending(thread_id, sender)
        if not rows:
            return []
        wanted = str(batch_id or "").strip() or rows[-1].batch_id
        return [row for row in rows if row.batch_id == wanted]

    def supersede(self, approval_ids, by: str, reason: str = "") -> int:
        """Retire staged rows whose action the user asked to change (ADR-0006 §4.5)."""
        changed = 0
        for approval_id in approval_ids:
            if self.mark(approval_id, "superseded", by, reason):
                changed += 1
        return changed

    def count_pending(self) -> int:
        found = self.db.fetch_all(
            select(approvals.c.id).where(
                approvals.c.status == "pending", approvals.c.expires_at >= time.time()
            )
        )
        return len(found)

    def mark(self, approval_id: int | str, status: str, by: str, result: str = "") -> bool:
        try:
            key = int(approval_id)
        except (TypeError, ValueError):
            return False
        fence = self.fence_clause()
        statement = (
            update(approvals)
            .where(approvals.c.id == key, approvals.c.status == "pending")
            .values(
                status=status,
                decided_by=by,
                decided_at=time.time(),
                result=str(result or ""),
            )
        )
        if fence is not None:
            statement = statement.where(fence)
        with self.db.connect() as conn:
            outcome = conn.execute(statement)
            return int(outcome.rowcount or 0) == 1

    def expire(self) -> int:
        fence = self.fence_clause()
        statement = (
            update(approvals)
            .where(approvals.c.status == "pending", approvals.c.expires_at < time.time())
            .values(status="expired", decided_at=time.time())
        )
        if fence is not None:
            statement = statement.where(fence)
        with self.db.connect() as conn:
            outcome = conn.execute(statement)
            return int(outcome.rowcount or 0)
