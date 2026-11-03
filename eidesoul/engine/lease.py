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
from dataclasses import dataclass

from sqlalchemy import text

from .db import Database

log = logging.getLogger(__name__)

TTL = 30.0

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

    def current_generation(self) -> int | None:
        """The live generation in the database, or `None` when it cannot answer."""
        try:
            found = self.db.fetch_one(text("SELECT generation FROM engine_lease WHERE id = 1"))
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
            with self.db.connect() as conn:
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
