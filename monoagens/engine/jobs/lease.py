"""Single primary lease: one heartbeat, other instances stay standby and frozen.

Acquisition is a compare-and-swap on `engine_lease.id = 1`; every takeover raises
`generation`, and renew/release are keyed on the pair (instance_id, generation) so an
instance that was replaced can never write again. Expiry is judged by the database clock
(`clock_timestamp()`), never by the application clock (ADR-0012).
"""


import logging
import os
import socket
import uuid
from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy import Select, Table, literal, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ..db import Database

log = logging.getLogger(__name__)

TTL = 30.0

# The one fence every canonical write carries (ADR-0012 §4.3): the row is written only while
# the calling instance still holds the live generation, judged by the database clock.
FENCE_SQL = (
    "EXISTS (SELECT 1 FROM engine_lease WHERE id = 1 AND instance_id = :fence_owner "
    "AND generation = :fence_gen "
    "AND heartbeat_at >= EXTRACT(EPOCH FROM clock_timestamp()) - :fence_ttl)"
)

# The live generation of the database, or NULL when the heartbeat is stale (no live primary).
LIVE_SQL = (
    "(SELECT generation FROM engine_lease WHERE id = 1 "
    "AND heartbeat_at >= EXTRACT(EPOCH FROM clock_timestamp()) - :fence_ttl)"
)

_INSERT = text(
    """
    INSERT INTO engine_lease
        (id, instance_id, host, pid, mode, generation, started_at, heartbeat_at)
    VALUES (1, '', '', 0, 'standby', 0, 0, 0)
    ON CONFLICT (id) DO NOTHING
    """
)

_ACQUIRE = text(
    """
    UPDATE engine_lease
    SET instance_id = :instance_id,
        host = :host,
        pid = :pid,
        mode = :mode,
        generation = generation + 1,
        started_at = EXTRACT(EPOCH FROM clock_timestamp()),
        heartbeat_at = EXTRACT(EPOCH FROM clock_timestamp())
    WHERE id = 1
      AND (CAST(:takeover AS boolean)
           OR instance_id = :instance_id
           OR heartbeat_at < EXTRACT(EPOCH FROM clock_timestamp()) - :ttl)
    RETURNING instance_id, host, pid, mode, generation, started_at, heartbeat_at
    """
)

_PREVIOUS = text(
    "SELECT instance_id, host, pid, generation FROM engine_lease WHERE id = 1"
)

_RENEW = text(
    """
    UPDATE engine_lease
    SET heartbeat_at = EXTRACT(EPOCH FROM clock_timestamp())
    WHERE id = 1 AND instance_id = :instance_id AND generation = :generation
    RETURNING generation
    """
)

_RELEASE = text(
    """
    UPDATE engine_lease
    SET instance_id = '', heartbeat_at = 0
    WHERE id = 1 AND instance_id = :instance_id AND generation = :generation
    RETURNING generation
    """
)

_HOLDER = text(
    """
    SELECT instance_id, host, pid, mode, generation, started_at, heartbeat_at
    FROM engine_lease WHERE id = 1
    """
)

_FROZEN = text("SELECT frozen FROM engine_control WHERE id = 1")

_FREEZE = text(
    """
    UPDATE engine_control
    SET frozen = true, frozen_by = :by,
        frozen_at = EXTRACT(EPOCH FROM clock_timestamp())
    WHERE id = 1
    """
)

_UNFREEZE = text(
    """
    UPDATE engine_control
    SET frozen = false, frozen_by = '',
        frozen_at = EXTRACT(EPOCH FROM clock_timestamp())
    WHERE id = 1
    """
)

_AUDIT = text(
    """
    INSERT INTO actions
        (ts, source, source_name, target, target_name, kind, summary, request)
    VALUES
        (EXTRACT(EPOCH FROM clock_timestamp()), :source, '', :target, '', 'lease', :summary, '')
    """
)


class Superseded(RuntimeError):
    """A side effect was refused because the live lease generation moved on."""


@dataclass(frozen=True)
class LeaseInfo:
    instance_id: str
    host: str
    pid: int
    mode: str
    started_at: float
    heartbeat_at: float
    generation: int = 0


class Lease:
    def __init__(
        self,
        db: Database,
        instance_id: str | None = None,
        host: str | None = None,
        pid: int | None = None,
        mode: str = "primary",
        ttl: float = TTL,
    ) -> None:
        self.db = db
        self.instance_id = instance_id or uuid.uuid4().hex
        self.host = host or socket.gethostname()
        self.pid = int(pid if pid is not None else os.getpid())
        self.mode = mode
        self.ttl = float(ttl)
        self.generation = 0
        self.guard = LeaseGuard(self)

    # ------------------------------------------------------------ helpers

    @staticmethod
    def _info(row) -> LeaseInfo:
        return LeaseInfo(
            instance_id=str(row["instance_id"]),
            host=str(row["host"]),
            pid=int(row["pid"]),
            mode=str(row["mode"]),
            started_at=float(row["started_at"]),
            heartbeat_at=float(row["heartbeat_at"]),
            generation=int(row["generation"]),
        )

    def _audit(self, conn, summary: str, *, target: str = "") -> None:
        conn.execute(
            _AUDIT,
            {
                "source": self.instance_id[:120],
                "target": str(target)[:120],
                "summary": str(summary)[:300],
            },
        )

    def _control_one(self, statement, params=None) -> dict | None:
        """Heartbeat/gate reads ride the control pool so turn load cannot starve them."""
        with self.db.control() as conn:
            row = conn.execute(statement, params or {}).mappings().first()
            return dict(row) if row is not None else None

    def current_generation(self) -> int | None:
        """The live generation in the database, or `None` when it cannot answer."""
        try:
            found = self._control_one(text("SELECT generation FROM engine_lease WHERE id = 1"))
        except Exception as exc:  # a DB error is a lease failure, not a crash (ADR-0012 §4.5)
            log.warning("lease read failed: %s: %s", type(exc).__name__, exc)
            return None
        return int(found["generation"]) if found is not None else None

    def record_superseded(self, operation: str, live: int | None) -> None:
        """Record a refused side effect in the action ledger (ADR-0012 §4.3)."""
        summary = (
            f"superseded {operation}: held generation {self.generation}, "
            f"live {live if live is not None else 'unknown'}"
        )
        try:
            with self.db.connect() as conn:
                self._audit(conn, summary)
        except Exception as exc:
            log.warning("superseded record failed: %s: %s", type(exc).__name__, exc)

    # ------------------------------------------------------------ primary

    def acquire(self, *, takeover: bool = False) -> LeaseInfo | None:
        """Compare-and-swap the lease; returns the row only for the winner."""
        try:
            with self.db.connect() as conn:
                control = conn.execute(
                    text("SELECT frozen FROM engine_control WHERE id = 1 FOR UPDATE")
                ).scalar()
                # The freeze applies to every instance (including the one that requested it),
                # until an explicit takeover clears it.
                if control is not None and bool(control) and not takeover:
                    return None
                conn.execute(_INSERT)
                previous = conn.execute(_PREVIOUS).mappings().first()
                row = conn.execute(
                    _ACQUIRE,
                    {
                        "instance_id": self.instance_id,
                        "host": self.host,
                        "pid": self.pid,
                        "mode": self.mode,
                        "takeover": bool(takeover),
                        "ttl": self.ttl,
                    },
                ).mappings().first()
                if row is None:
                    return None
                self.generation = int(row["generation"])
                if takeover:
                    conn.execute(_UNFREEZE)
                if previous is not None:
                    old = str(previous["instance_id"] or "")
                    if old and old != self.instance_id:
                        self._audit(
                            conn,
                            f"takeover from {previous['host']} pid {previous['pid']} "
                            f"generation {previous['generation']} -> {self.generation}",
                            target=self.instance_id,
                        )
                return self._info(row)
        except Exception as exc:  # a DB error is a lease failure, not a crash (ADR-0012 §4.5)
            log.warning("lease acquire failed: %s: %s", type(exc).__name__, exc)
            return None

    def renew(self) -> bool:
        """Fresh heartbeat only for the current (instance_id, generation); `False` on any doubt."""
        try:
            with self.db.control() as conn:
                found = conn.execute(
                    _RENEW,
                    {"instance_id": self.instance_id, "generation": self.generation},
                ).first()
            return found is not None
        except Exception as exc:  # a renew failure is counted by the supervisor, never raised
            log.warning("lease renew failed: %s: %s", type(exc).__name__, exc)
            return False

    def release(self) -> None:
        """Give the lease up; only the current generation owner may do this."""
        try:
            with self.db.connect() as conn:
                released = conn.execute(
                    _RELEASE,
                    {"instance_id": self.instance_id, "generation": self.generation},
                ).first()
            if released is not None:
                self.generation = 0
        except Exception as exc:
            log.warning("lease release failed: %s: %s", type(exc).__name__, exc)

    def bind_stores(self, agent) -> None:
        """Wire every store of `agent` to this lease (ADR-0012 §4.3).

        Binding is safe at generation 0: a bound store then refuses mutations with
        `Superseded` instead of writing unfenced as a standby. Re-binding after a takeover
        is idempotent and keeps the same lease object, so the live generation is read anew.
        """
        for name in ("memory", "turns", "skills", "affect", "approvals", "recipes"):
            bind = getattr(getattr(agent, name, None), "bind_lease", None)
            if callable(bind):
                bind(self)

    def holder(self) -> LeaseInfo | None:
        try:
            found = self.db.fetch_one(_HOLDER)
        except Exception as exc:
            log.warning("lease holder read failed: %s: %s", type(exc).__name__, exc)
            return None
        if not found or not found.get("instance_id"):
            return None
        return self._info(found)

    # ------------------------------------------------------------- freeze

    def request_freeze(self, by: str) -> None:
        try:
            with self.db.connect() as conn:
                conn.execute(_FREEZE, {"by": str(by)[:120]})
        except Exception as exc:
            log.warning("freeze request failed: %s: %s", type(exc).__name__, exc)

    def clear_freeze(self) -> None:
        try:
            with self.db.connect() as conn:
                conn.execute(_UNFREEZE)
        except Exception as exc:
            log.warning("freeze clear failed: %s: %s", type(exc).__name__, exc)

    def frozen(self) -> bool:
        try:
            found = self.db.fetch_one(_FROZEN)
        except Exception as exc:
            log.warning("freeze read failed: %s: %s", type(exc).__name__, exc)
            return False
        return bool(found and found["frozen"])


class LeaseGuard:
    """The single port every side effect asks before touching the real world (ADR-0012 §4.3).

    The old holder must not finish a send, a canonical memory write, or a job update after
    being replaced. Call `require` immediately before the transport/write call, never after.
    """

    def __init__(self, lease: Lease) -> None:
        self.lease = lease

    @property
    def generation(self) -> int:
        return self.lease.generation

    def held(self) -> bool:
        live = self.lease.current_generation()
        return live is not None and live == self.lease.generation

    def require(self, operation: str) -> int:
        live = self.lease.current_generation()
        if live is None or live != self.lease.generation:
            self.lease.record_superseded(operation, live)
            raise Superseded(
                f"lease generation moved on: held {self.lease.generation}, live {live}"
            )
        return live


@dataclass(frozen=True)
class Fence:
    """The (instance, generation) pair a canonical write is allowed to run under."""

    owner: str
    generation: int
    ttl: float = TTL

    @property
    def bound(self) -> bool:
        return int(self.generation) > 0

    def params(self) -> dict:
        return {
            "fence_owner": str(self.owner or ""),
            "fence_gen": int(self.generation),
            "fence_ttl": float(self.ttl),
        }

    def clause(self):
        """The shared `EXISTS (...)` predicate, or `None` when no generation is bound."""
        if not self.bound:
            return None
        return text(FENCE_SQL).bindparams(**self.params())


def fence_clause(instance_id, generation, ttl: float = TTL):
    """The atomic lease predicate for a canonical mutation (ADR-0012 §4.3).

    A store with no bound generation (CLI, admin, a test database) gets `None`, so its
    statement carries no predicate at all: the fence is omitted, never faked.
    """
    return Fence(str(instance_id or ""), int(generation or 0), float(ttl)).clause()


def row_clause(column: str, *, ttl: float = TTL, unclaimed: bool = False):
    """Fence a row that records the generation it was claimed under.

    `turns` and `automation_runs` already store the claim generation, so the mutation can
    compare that column with the live generation instead of carrying a bound pair. A deposed
    instance's rows fail the comparison; a database with no lease row is not fenced at all.
    """
    extra = f" OR {column} = 0" if unclaimed else ""
    statement = (
        f"({column} = {LIVE_SQL}{extra} "
        "OR NOT EXISTS (SELECT 1 FROM engine_lease WHERE id = 1))"
    )
    return text(statement).bindparams(fence_ttl=float(ttl))


def caller_clause(generation: int, *, ttl: float = TTL):
    """Fence a caller that knows its generation but not its instance (a worker id)."""
    if not int(generation or 0):
        return None
    return text(f":fence_gen = {LIVE_SQL}").bindparams(
        fence_gen=int(generation), fence_ttl=float(ttl)
    )


def fenced_insert(table: Table, row: Mapping, fence):
    """`INSERT ... SELECT ... WHERE <fence>`: exactly one row, only under a live lease.

    SQL has no `INSERT ... WHERE`, so the values are selected and the predicate gates the
    select; a refused fence inserts nothing. An `INSERT ... SELECT` reports no rowcount, so
    the caller chains `.returning(<column>)` and treats a missing row as the refusal.
    """
    columns = [table.c[name] for name in row]
    selected = select(
        *[literal(row[name], type_=table.c[name].type).label(name) for name in row]
    )
    return pg_insert(table).from_select(columns, _where(selected, fence))


def _where(statement: Select, fence):
    return statement if fence is None else statement.where(fence)


class FencedStore:
    """A store whose canonical mutations carry the live lease predicate once bound.

    Binding holds the `Lease` itself, so a store wired while the instance was a standby starts
    fencing the moment that process wins the lease. An unbound store (CLI, admin, tests) writes
    without a predicate.
    """

    _lease = None

    def bind_lease(self, lease) -> None:
        self._lease = lease

    @property
    def fence(self) -> Fence | None:
        lease = self._lease
        if lease is None:
            return None
        generation = int(getattr(lease, "generation", 0) or 0)
        if generation <= 0:
            raise Superseded("lease not held (generation 0)")
        return Fence(str(getattr(lease, "instance_id", "") or ""), generation)

    def fence_clause(self):
        found = self.fence
        return found.clause() if found is not None else None

    def guard_clause(self, generation: int = 0, *, ttl: float = TTL):
        """The bound predicate, else the caller's own generation against the live row."""
        bound = self.fence_clause()
        return bound if bound is not None else caller_clause(generation, ttl=ttl)


def bind_stores(agent) -> None:
    """Wire every store of one agent to its lease: one primary, one generation (ADR-0012).

    Called once the supervisor has attached the lease; a standby's generation is 0 until it
    wins, so binding early is safe: a bound generation-0 store refuses mutations instead of
    writing unfenced, and the same lease object exposes the new generation after takeover.
    """
    lease = getattr(agent, "lease", None)
    if lease is not None:
        lease.bind_stores(agent)


def lock_data_dir(path) -> object | None:
    """Lock the host-local data dir for `run`; None when the platform lacks fcntl."""
    try:
        import fcntl
    except ImportError:  # pragma: no cover - non-POSIX
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("w", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        raise RuntimeError(f"data dir is in use by another process: {path}") from exc
    handle.write(str(os.getpid()))
    handle.flush()
    return handle  # closing it releases the lock
