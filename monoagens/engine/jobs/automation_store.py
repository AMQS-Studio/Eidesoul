"""Automation store: the row, its runs, and the transition log (ADR-0001).

Persistence and scheduling only; no delivery lives here. A run is claimed with `SKIP LOCKED`
and fenced with the lease generation, and the occurrence key makes a duplicate tick, restart,
or takeover unable to double a run.
"""

import logging
import random
import re
import time
from datetime import datetime, timedelta
from datetime import time as clock_time

from sqlalchemy import ColumnElement, TextClause, func, select, update

from ..config import AutomationConfig
from ..db import Database
from ..schema import (
    AUTOMATION_STATES,
    AUTOMATION_TERMINAL,
    automation_runs,
    automation_state_log,
    automations,
)
from .automation_manifest import (
    _mapping,
    _rooms_of,
    authority_errors,
    describe,
    validate_manifest,
)
from .lease import FencedStore, Superseded, fenced_insert
from .schedule import (
    DAY,
    ManifestError,
    _date,
    _first_occurrence,
    next_after,
    occurrence_key,
    zone,
)

log = logging.getLogger(__name__)

DETAIL_CHARS = 2000
_RETRY_AFTER = re.compile(r"retry[- ]after[: ]+(\d+)", re.IGNORECASE)
MATERIALIZE_CAP = 100


class AutomationStore(FencedStore):
    """The row, its runs, and the transition log; no delivery lives here."""

    def __init__(self, db: Database, config: AutomationConfig) -> None:
        self.db = db
        self.config = config

    def _live(self, generation: int = 0):
        """The predicate a scheduler mutation carries: the bound lease, else the caller's."""
        return self.guard_clause(generation)

    # ---------------------------------------------------------------- read

    def get(self, automation_id: int | str) -> dict | None:
        return self.db.fetch_one(
            select(automations).where(automations.c.id == int(automation_id))
        )

    def runs(self, automation_id: int | str, limit: int = 50) -> list[dict]:
        return self.db.fetch_all(
            select(automation_runs)
            .where(automation_runs.c.automation_id == int(automation_id))
            .order_by(automation_runs.c.id.desc())
            .limit(max(1, int(limit)))
        )

    def transitions(self, automation_id: int | str) -> list[dict]:
        return self.db.fetch_all(
            select(automation_state_log)
            .where(automation_state_log.c.automation_id == int(automation_id))
            .order_by(automation_state_log.c.id)
        )

    def by_owner(self, owner: str, *, states: tuple[str, ...] = ()) -> list[dict]:
        """Every row one person owns, newest first; `/task list` reads exactly this."""
        statement = select(automations).where(automations.c.owner == str(owner))
        if states:
            statement = statement.where(automations.c.state.in_(tuple(states)))
        return self.db.fetch_all(statement.order_by(automations.c.id.desc()))

    # --------------------------------------------------------------- write

    async def propose(self, agent, manifest: dict, *, thread_id: str, gateway=None):
        """Create the approval that must exist before a row is stored (ADR-0001 §4.8)."""
        body = validate_manifest(manifest, self.config)
        summary = describe(body)
        approval = await agent.approvals.create(
            kind="automation",
            thread_id=str(thread_id),
            sender=str(body["owner"]),
            gateway=getattr(gateway, "name", "") or "",
            tool="",
            args={"manifest": body},
            payload={"manifest": body, "summary": summary},
            summary=summary,
            roles=tuple(role.name for role in agent.config.roles.for_person(body["owner"])),
        )
        return approval, summary

    def create(
        self,
        manifest: dict,
        *,
        approval,
        agent,
        now: float | None = None,
        approved_now: bool = False,
    ) -> int:
        """Store the row only after an approval; authority is checked here too.

        `approved_now` is set by `/approve` itself: the command is the approval act, and the
        row is not stored until that command runs. Every other caller must present an
        approval whose stored status is already `approved`.
        """
        body = validate_manifest(manifest, self.config)
        status = getattr(approval, "status", "")
        if approval is None or (status != "approved" and not approved_now):
            raise ManifestError("an automation needs an approved approval before it is stored")
        if str(getattr(approval, "kind", "")) != "automation":
            raise ManifestError("the approval is not an automation approval")
        if str(getattr(approval, "sender", "")) != body["owner"]:
            raise ManifestError("the approval belongs to another person")
        roles = agent.config.roles.for_person(body["owner"])
        rooms = _rooms_of(agent, body["owner"], body)
        errors = authority_errors(
            body,
            roles=roles,
            rooms=rooms,
            recipes=getattr(agent, "recipes", None),
            config=self.config,
            workspace=agent.config.workspace,
            memory_admin=any(role.memory_admin for role in roles),
        )
        if errors:
            raise ManifestError("; ".join(errors))
        active = self.db.fetch_all(
            select(automations.c.id).where(
                automations.c.owner == body["owner"], automations.c.state == "active"
            )
        )
        if len(active) >= int(self.config.max_active_per_person):
            raise ManifestError(
                f"at most {self.config.max_active_per_person} active automations per person"
            )
        moment = time.time() if now is None else float(now)
        event = body["event"]
        created = moment
        first = _first_occurrence(event, moment, created)
        ends = _until_epoch(event)
        row = {
            "owner": body["owner"],
            "name": body["name"],
            "event": event,
            "condition": body["condition"],
            "steps": body["steps"],
            "policy": body["policy"],
            "state": "active",
            "reason": "",
            "owner_roles_at_creation": [role.name for role in roles],
            "approval_id": int(getattr(approval, "id", 0) or 0),
            "next_occurrence": float(first if first is not None else 0.0),
            "fire_count": 0,
            "ends_at": ends,
            "created_at": created,
            "updated_at": moment,
        }
        fence = self.fence_clause()
        with self.db.connect() as conn:
            found = conn.execute(
                fenced_insert(automations, row, fence).returning(automations.c.id)
            )
            new_id = found.scalar()
            if new_id is None and fence is not None:
                raise Superseded("lease generation moved on: automation refused")
            if new_id is None:  # pragma: no cover - the insert always returns its own row
                raise RuntimeError("the new automation could not be read back")
            automation_id = int(new_id)
            conn.execute(
                fenced_insert(
                    automation_state_log,
                    {
                        "automation_id": automation_id,
                        "from_state": "draft",
                        "to_state": "active",
                        "reason": "approved",
                        "actor": str(body["owner"]),
                        "at": moment,
                    },
                    fence,
                )
            )
        log.info(
            "automation created id=%s owner=%s next=%s",
            automation_id,
            body["owner"],
            row["next_occurrence"],
        )
        return automation_id

    def set_state(
        self,
        automation_id: int | str,
        state: str,
        *,
        reason: str = "",
        actor: str = "",
        now: float | None = None,
    ) -> dict | None:
        """One legal transition, one log row; terminal states never reopen (ADR-0001 §4.13)."""
        target = str(state or "").strip().lower()
        if target not in AUTOMATION_STATES:
            raise ManifestError(f"automation state '{target}' does not exist")
        row = self.get(automation_id)
        if row is None:
            return None
        current = str(row["state"])
        if current == target:
            return row
        if current in AUTOMATION_TERMINAL:
            raise ManifestError(f"state '{current}' is terminal; create a new automation")
        legal = {
            "draft": {"active", "stopped"},
            "active": {"paused", "stopped", "expired"},
            "paused": {"active", "stopped", "expired"},
            "stopped": set(),
            "expired": set(),
        }[current]
        if target not in legal:
            raise ManifestError(f"illegal transition {current} -> {target}")
        moment = time.time() if now is None else float(now)
        fence = self.fence_clause()
        statement = (
            update(automations)
            .where(automations.c.id == int(automation_id))
            .values(state=target, reason=str(reason or ""), updated_at=moment)
        )
        if fence is not None:
            statement = statement.where(fence)
        with self.db.connect() as conn:
            conn.execute(statement)
            conn.execute(
                fenced_insert(
                    automation_state_log,
                    {
                        "automation_id": int(automation_id),
                        "from_state": current,
                        "to_state": target,
                        "reason": str(reason or ""),
                        "actor": str(actor or ""),
                        "at": moment,
                    },
                    fence,
                )
            )
        log.info(
            "automation %s %s -> %s (%s)", automation_id, current, target, reason or "-"
        )
        return self.get(automation_id)

    # ---------------------------------------------------------- occurrences

    def due_automations(self, now: float, limit: int = 200) -> list[dict]:
        """Clock-driven rows only: a message automation is fed by the intake sink."""
        return self.db.fetch_all(
            select(automations)
            .where(
                automations.c.state == "active",
                automations.c.next_occurrence > 0,
                automations.c.next_occurrence <= float(now),
                func.coalesce(automations.c.event["kind"].astext, "") != "message",
            )
            .order_by(automations.c.next_occurrence)
            .limit(max(1, int(limit)))
        )

    def enqueue_message(
        self,
        conn,
        *,
        thread_id: str,
        gateway: str,
        message,
        person_id: str,
        now: float,
    ) -> int:
        """ADR-0001 §4.3: one queued run per active message automation that matches.

        The sink runs inside the intake transaction, so the run and the message commit together
        or not at all. The occurrence key is the message id: a redelivered message inserts
        nothing, and the payload carries what the condition and the steps read.
        """
        rows = conn.execute(
            select(automations).where(
                automations.c.state == "active",
                automations.c.event["kind"].astext == "message",
            )
        ).mappings().all()
        created = 0
        for row in rows:
            event = row["event"] if isinstance(row["event"], dict) else {}
            rooms = event.get("thread") or []
            if rooms and str(thread_id) not in {str(item) for item in rooms}:
                continue
            sender = str(event.get("sender") or "")
            if sender and str(person_id) != sender:
                continue
            found = conn.execute(
                fenced_insert(
                    automation_runs,
                    {
                        "automation_id": int(row["id"]),
                        "occurrence_key": f"message:{int(row['id'])}:{message.id!s}",
                        "scheduled_at": (
                            float(message.when.timestamp()) if message.when else float(now)
                        ),
                        "status": "queued",
                        "payload": {
                            "message_id": str(message.id),
                            "thread": str(thread_id),
                            "gateway": str(gateway),
                            "person_id": str(person_id),
                        },
                        "created_at": now,
                        "updated_at": now,
                    },
                    self.fence_clause(),
                )
                .on_conflict_do_nothing(index_elements=["occurrence_key"])
                .returning(automation_runs.c.id)
            ).scalar()
            if found is not None:
                created += 1
        return created

    def materialize(self, now: float | None = None) -> dict[str, int]:
        """Turn every due schedule into its own run row; a duplicate never doubles."""
        moment = time.time() if now is None else float(now)
        report = {"materialized": 0, "missed": 0, "expired": 0, "duplicate": 0}
        for row in self.due_automations(moment):
            self._materialize_one(row, moment, report)
        return report

    def _materialize_one(self, row: dict, now: float, report: dict[str, int]) -> None:
        event = row["event"] if isinstance(row["event"], dict) else {}
        current = float(row["next_occurrence"] or 0.0)
        fire = int(row["fire_count"] or 0)
        count = int(event.get("count") or 0)
        created = float(row["created_at"])
        stale = False
        processed = 0
        while current and current <= now and processed < MATERIALIZE_CAP:
            processed += 1
            if count and fire >= count:
                current = 0.0
                break
            spent, stale = self._enqueue_occurrence(row, current, now, report)
            fire += spent
            if stale:
                current = 0.0
                break
            found = next_after(event, current, anchor=created)
            current = float(found) if found is not None else 0.0
            if count and fire >= count:
                current = 0.0
        self._store_schedule(row["id"], current, fire, now)
        if stale:
            self.set_state(row["id"], "paused", reason="stale", actor="engine", now=now)
        elif not current:
            report["expired"] += int(self.maybe_expire(row["id"], now=now))

    def _enqueue_occurrence(
        self, row: dict, current: float, now: float, report: dict[str, int]
    ) -> tuple[int, bool]:
        """One occurrence row; returns `(spent, stale)` and never doubles a key."""
        late = now - current
        max_stale = int(self.config.resilience.max_stale_days)
        if max_stale and late > max_stale * DAY:
            inserted = self._insert_run(row, current, "missed", "past max_stale_days", now)
            report["missed" if inserted else "duplicate"] += 1
            return (1 if inserted else 0), True
        catchup = int(self.config.resilience.catchup_minutes) * 60
        status = "missed" if late > catchup else "queued"
        detail = "catch-up window missed" if status == "missed" else ""
        inserted = self._insert_run(row, current, status, detail, now)
        report["missed" if status == "missed" else "materialized"] += int(inserted)
        report["duplicate"] += int(not inserted)
        return int(inserted), False

    def _store_schedule(
        self, automation_id: int | str, current: float, fire: int, now: float
    ) -> None:
        fence = self.fence_clause()
        statement = (
            update(automations)
            .where(automations.c.id == int(automation_id))
            .values(next_occurrence=current, fire_count=fire, updated_at=now)
        )
        if fence is not None:
            statement = statement.where(fence)
        with self.db.connect() as conn:
            conn.execute(statement)

    def _insert_run(
        self, automation: dict, scheduled_at: float, status: str, detail: str, now: float
    ) -> bool:
        row = {
            "automation_id": int(automation["id"]),
            "occurrence_key": occurrence_key(automation["id"], scheduled_at),
            "scheduled_at": float(scheduled_at),
            "status": status,
            "detail": detail,
            "created_at": now,
            "updated_at": now,
        }
        with self.db.connect() as conn:
            found = conn.execute(
                fenced_insert(automation_runs, row, self.fence_clause())
                .on_conflict_do_nothing(index_elements=["occurrence_key"])
                .returning(automation_runs.c.id)
            ).scalar()
        return found is not None

    # ---------------------------------------------------------------- claim

    def claim(
        self,
        now: float,
        *,
        worker: str,
        generation: int = 0,
        limit: int = 5,
    ) -> list[dict]:
        lease_until = now + float(self.config.resilience.stale_claim_seconds)
        fence = self._live(generation)
        claimed: list[dict] = []
        with self.db.connect() as conn:
            rows = conn.execute(
                select(automation_runs)
                .where(
                    automation_runs.c.status == "queued",
                    automation_runs.c.scheduled_at <= float(now),
                    automation_runs.c.next_attempt_at <= float(now),
                )
                .order_by(automation_runs.c.scheduled_at, automation_runs.c.id)
                .limit(max(1, int(limit)))
                .with_for_update(skip_locked=True)
            ).mappings().all()
            for row in rows:
                statement = (
                    update(automation_runs)
                    .where(
                        automation_runs.c.id == int(row["id"]),
                        automation_runs.c.status == "queued",
                    )
                    .values(
                        status="claimed",
                        claimed_by=str(worker),
                        claimed_at=now,
                        lease_until=lease_until,
                        attempts=int(row["attempts"]) + 1,
                        generation=int(generation),
                        updated_at=now,
                    )
                )
                if fence is not None:
                    statement = statement.where(fence)
                if not conn.execute(statement).rowcount:
                    continue
                body = dict(row)
                body.update(
                    status="claimed",
                    claimed_by=str(worker),
                    claimed_at=now,
                    lease_until=lease_until,
                    attempts=int(row["attempts"]) + 1,
                    generation=int(generation),
                )
                claimed.append(body)
        return claimed

    def reap(self, now: float | None = None) -> int:
        """A claim from a dead process goes back to the queue; attempts stay counted.

        Only `claimed`/`running` rows are touched, and each update repeats the row's own
        `claimed_by`/`generation`, so a claim another instance took meanwhile is never
        interrupted or released by this pass (ADR-0002 §4.3).
        """
        moment = time.time() if now is None else float(now)
        stale = self.db.fetch_all(
            select(automation_runs).where(
                automation_runs.c.status.in_(("claimed", "running")),
                automation_runs.c.lease_until < moment,
            )
        )
        fence = self.fence_clause()
        released = 0
        for row in stale:
            where = [
                automation_runs.c.id == int(row["id"]),
                automation_runs.c.status.in_(("claimed", "running")),
                automation_runs.c.claimed_by == str(row["claimed_by"]),
                automation_runs.c.generation == int(row["generation"]),
            ]
            if fence is not None:
                where.append(fence)
            if int(row["attempts"]) >= int(self.config.retry.max_attempts):
                values = {
                    "status": "failed",
                    "detail": "stale claim: attempts exhausted",
                    "finished_at": moment,
                    "lease_until": 0,
                    "updated_at": moment,
                }
            else:
                values = {"status": "queued", "next_attempt_at": moment, "updated_at": moment}
            with self.db.connect() as conn:
                released += int(
                    conn.execute(
                        update(automation_runs).where(*where).values(**values)
                    ).rowcount
                    or 0
                )
        if released:
            log.info("automation reaper released %d stale claim(s)", released)
        return released

    def interrupt(self, now: float | None = None, *, worker: str = "", generation: int = 0) -> int:
        """Clean shutdown: only this instance's in-flight runs are `interrupted`.

        A takeover must not let the deposed instance close the new primary's runs, so the
        update is scoped by `claimed_by`/`generation` and carries the live lease predicate.
        """
        moment = time.time() if now is None else float(now)
        where: list[ColumnElement[bool] | TextClause] = [
            automation_runs.c.status.in_(("claimed", "running"))
        ]
        if worker:
            where.append(automation_runs.c.claimed_by == str(worker))
        if generation:
            where.append(automation_runs.c.generation == int(generation))
        fence = self._live(generation)
        if fence is not None:
            where.append(fence)
        with self.db.connect() as conn:
            outcome = conn.execute(
                update(automation_runs)
                .where(*where)
                .values(
                    status="interrupted",
                    detail="clean shutdown",
                    finished_at=moment,
                    updated_at=moment,
                )
            )
            return int(outcome.rowcount or 0)

    def resume_interrupted(self, now: float | None = None) -> int:
        """Next boot: interrupted runs inside the window resume; older ones are missed."""
        moment = time.time() if now is None else float(now)
        window = int(self.config.resilience.catchup_minutes) * 60
        rows = self.db.fetch_all(
            select(automation_runs).where(automation_runs.c.status == "interrupted")
        )
        fence = self.fence_clause()
        resumed = 0
        for row in rows:
            if moment - float(row["scheduled_at"]) <= window:
                statement = (
                    update(automation_runs)
                    .where(automation_runs.c.id == int(row["id"]))
                    .values(
                        status="queued",
                        next_attempt_at=0,
                        claimed_at=None,
                        claimed_by="",
                        lease_until=0,
                        updated_at=moment,
                    )
                )
                if fence is not None:
                    statement = statement.where(fence)
                with self.db.connect() as conn:
                    resumed += int(conn.execute(statement).rowcount or 0)
            else:
                self.finish(row["id"], "missed", "interrupted: window missed", now=moment)
        return resumed

    def finish(
        self,
        run_id: int | str,
        status: str,
        detail: str = "",
        *,
        now: float | None = None,
    ) -> None:
        moment = time.time() if now is None else float(now)
        values: dict = {
            "status": str(status),
            "detail": str(detail)[:DETAIL_CHARS],
            "finished_at": moment,
            "updated_at": moment,
            "lease_until": 0,
        }
        statement = update(automation_runs).where(automation_runs.c.id == int(run_id))
        fence = self.fence_clause()
        if fence is not None:
            statement = statement.where(fence)
        with self.db.connect() as conn:
            conn.execute(statement.values(**values))

    def retry(self, run: dict, error: str, *, now: float | None = None, step: int = -1) -> str:
        """Transient failure: back off, or `failed` once attempts are exhausted."""
        moment = time.time() if now is None else float(now)
        attempts = max(1, int(run.get("attempts") or 1))
        if attempts >= int(self.config.retry.max_attempts):
            self.finish(run["id"], "failed", f"attempts exhausted: {error}", now=moment)
            return "failed"
        delay = min(
            float(self.config.retry.max_backoff_seconds),
            float(self.config.retry.backoff_seconds) * (2 ** (attempts - 1)),
        )
        if self.config.retry.respect_retry_after:
            found = _RETRY_AFTER.search(str(error))
            if found:
                delay = max(delay, float(found.group(1)))
        if self.config.retry.jitter:
            delay *= 1.0 + random.uniform(0.0, 0.2)
        statement = (
            update(automation_runs)
            .where(automation_runs.c.id == int(run["id"]))
            .values(
                status="queued",
                next_attempt_at=moment + delay,
                step_index=int(step),
                detail=str(error)[:DETAIL_CHARS],
                updated_at=moment,
            )
        )
        fence = self.fence_clause()
        if fence is not None:
            statement = statement.where(fence)
        with self.db.connect() as conn:
            conn.execute(statement)
        return "retry"

    def budget_used(
        self, automation: dict, scheduled_at: float, *, exclude_run: int | None = None
    ) -> int:
        """Runs already spent on the occurrence's local day; skipped/missed do not count.

        Only an executed outcome spends the budget (ADR-0001 §4.13: `budget_per_day` counts
        runs, not attempts); the currently claimed run is excluded until it finishes.
        """
        event = _mapping(automation.get("event"))
        moment = datetime.fromtimestamp(float(scheduled_at), zone(event.get("tz")))
        start = datetime.combine(
            moment.date(), clock_time(0, 0), tzinfo=moment.tzinfo
        ).timestamp()
        statement = select(func.count()).select_from(automation_runs).where(
            automation_runs.c.automation_id == int(automation["id"]),
            automation_runs.c.scheduled_at >= start,
            automation_runs.c.scheduled_at < start + DAY,
            automation_runs.c.status.in_(("succeeded", "failed", "interrupted")),
        )
        if exclude_run is not None:
            statement = statement.where(automation_runs.c.id != int(exclude_run))
        return int(self.db.scalar(statement) or 0)

    def pending_runs(self, automation_id: int | str) -> int:
        return int(
            self.db.scalar(
                select(func.count())
                .select_from(automation_runs)
                .where(
                    automation_runs.c.automation_id == int(automation_id),
                    automation_runs.c.status.in_(("queued", "claimed", "running")),
                )
            )
            or 0
        )

    def maybe_expire(self, automation_id: int | str, *, now: float | None = None) -> bool:
        """A finished schedule expires after its last run, never before it (ADR-0001 §4.13)."""
        row = self.get(automation_id)
        if row is None or row["state"] != "active":
            return False
        if int(row["next_occurrence"] or 0) or self.pending_runs(automation_id):
            return False
        self.set_state(
            automation_id, "expired", reason="schedule exhausted", actor="engine", now=now
        )
        return True

    def mark_apologized(self, run_id: int | str, *, now: float | None = None) -> None:
        """The apology is a timestamp, not a new reason: `detail` keeps why it was missed."""
        moment = time.time() if now is None else float(now)
        statement = (
            update(automation_runs)
            .where(automation_runs.c.id == int(run_id))
            .values(apology_sent_at=moment, updated_at=moment)
        )
        fence = self.fence_clause()
        if fence is not None:
            statement = statement.where(fence)
        with self.db.connect() as conn:
            conn.execute(statement)

    def missed_apologies(self, limit: int = 50) -> list[dict]:
        return self.db.fetch_all(
            select(automation_runs)
            .where(
                automation_runs.c.status == "missed",
                automation_runs.c.apology_sent_at.is_(None),
            )
            .order_by(automation_runs.c.scheduled_at)
            .limit(max(1, int(limit)))
        )

    # ---------------------------------------------------------------- stats

    def stats(self, now: float | None = None) -> dict[str, int]:
        """Counters for `/status`: real rows, zero when nothing happened."""
        moment = time.time() if now is None else float(now)
        runs = self.db.fetch_all(
            select(automation_runs.c.status, func.count().label("count")).group_by(
                automation_runs.c.status
            )
        )
        states = self.db.fetch_all(
            select(automations.c.state, func.count().label("count")).group_by(
                automations.c.state
            )
        )
        today = self.db.scalar(
            select(func.count())
            .select_from(automation_runs)
            .where(
                automation_runs.c.scheduled_at >= moment - DAY,
                automation_runs.c.status.in_(("succeeded", "failed", "running", "claimed")),
            )
        )
        found = {f"runs.{row['status']}": int(row["count"]) for row in runs}
        found.update({f"states.{row['state']}": int(row["count"]) for row in states})
        found["runs_total"] = sum(int(row["count"]) for row in runs)
        found["runs_today"] = int(today or 0)
        found["missed"] = sum(
            int(row["count"]) for row in runs if str(row["status"]) == "missed"
        )
        found["denied"] = sum(
            int(row["count"]) for row in runs if str(row["status"]) == "denied"
        )
        found["queued"] = sum(
            int(row["count"]) for row in runs if str(row["status"]) == "queued"
        )
        found["active"] = sum(
            int(row["count"]) for row in states if str(row["state"]) == "active"
        )
        return found


def _until_epoch(event: dict) -> float | None:
    until = _date(event.get("until"), "until")
    if until is None:
        return None
    zoneinfo = zone(event.get("tz"))
    end = datetime.combine(until + timedelta(days=1), clock_time(0, 0), tzinfo=zoneinfo)
    return end.timestamp() - 1.0
