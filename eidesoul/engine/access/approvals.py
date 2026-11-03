"""Approval queue: sensitive actions wait for /approve, bound to chat+sender+TTL."""


import json
import time
from dataclasses import dataclass, field

from sqlalchemy import select, update

from .. import io
from ..config import ApprovalConfig
from ..db import Database
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

    @property
    def roles(self) -> tuple[str, ...]:
        found = self.payload.get("roles") if isinstance(self.payload, dict) else None
        return tuple(str(item) for item in found) if isinstance(found, list) else ()


class ApprovalStore:
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
        }
        with self.db.connect() as conn:
            result = conn.execute(approvals.insert().values(row))
            new_id = int(result.inserted_primary_key[0])
        found = self.get(new_id)
        if found is None:  # pragma: no cover - a new row is always readable
            raise RuntimeError("the new approval could not be read back")
        return found

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
        now = time.time()
        statement = (
            select(approvals)
            .where(
                approvals.c.thread_id == thread_id,
                approvals.c.sender == sender,
                approvals.c.status == "pending",
                approvals.c.expires_at >= now,
            )
            .order_by(approvals.c.created_at)
        )
        return [self._approval(row) for row in self.db.fetch_all(statement)]

    def latest(self, thread_id: str, sender: str) -> Approval | None:
        found = self.pending(thread_id, sender)
        return found[-1] if found else None

    def find_action(self, action_id: str, *, limit: int = 50) -> Approval | None:
        """The newest `send` row for a correlation id, whatever its status (ADR-0032 §4.3)."""
        if not str(action_id or "").strip():
            return None
        found = self.db.fetch_all(
            select(approvals)
            .where(approvals.c.kind == "send")
            .order_by(approvals.c.created_at.desc())
            .limit(max(1, int(limit)))
        )
        for row in found:
            payload = row["payload"] if isinstance(row.get("payload"), dict) else {}
            if str(payload.get("action_id") or "") == str(action_id):
                return self._approval(row)
        return None

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
        with self.db.connect() as conn:
            outcome = conn.execute(
                update(approvals)
                .where(approvals.c.id == key, approvals.c.status == "pending")
                .values(
                    status=status,
                    decided_by=by,
                    decided_at=time.time(),
                    result=str(result or ""),
                )
            )
            return int(outcome.rowcount or 0) == 1

    def expire(self) -> int:
        with self.db.connect() as conn:
            outcome = conn.execute(
                update(approvals)
                .where(approvals.c.status == "pending", approvals.c.expires_at < time.time())
                .values(status="expired", decided_at=time.time())
            )
            return int(outcome.rowcount or 0)
