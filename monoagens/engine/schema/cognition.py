"""The cognition queue and the episode record (ADR-0003, ADR-0001)."""


from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    Float,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

from .base import metadata

# ---------------------------------------------------------------- cognition
# ADR-0003 §5.4: one queue for every label/batch job; `kind` names the work, `scope` the
# isolation unit (`global`, `person:<id>`, `thread:<id>`). Canonical writes still go through
# `MemoryStore`; this table only holds the work order.

COGNITION_KINDS = ("capture", "consolidate", "reflect", "backfill", "audit", "distill")
COGNITION_STATUSES = ("queued", "claimed", "done", "failed", "dead")

cognition_jobs = Table(
    "cognition_jobs",
    metadata,
    Column("id", Uuid(as_uuid=False), primary_key=True),
    Column("kind", String(20), nullable=False),
    Column("scope", String(160), nullable=False, server_default="global"),
    Column("payload_ref", Text, nullable=False, server_default=""),
    Column("status", String(20), nullable=False, server_default="queued"),
    Column("claimed_by", Text, nullable=False, server_default=""),
    Column("lease_until", Float, nullable=False, server_default="0"),
    Column("attempts", Integer, nullable=False, server_default="0"),
    Column("next_attempt_at", Float, nullable=False, server_default="0"),
    Column("result_ref", Text, nullable=False, server_default=""),
    Column("idempotency_key", Text, nullable=False),
    # ADR-0002 §4.4: the lease generation the job was claimed under; a newer generation wins.
    Column("generation", BigInteger, nullable=False, server_default="0"),
    Column("created_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
    CheckConstraint(
        "kind IN ('capture','consolidate','reflect','backfill','audit','distill')",
        name="ck_cognition_jobs_kind",
    ),
    CheckConstraint(
        "status IN ('queued','claimed','done','failed','dead')",
        name="ck_cognition_jobs_status",
    ),
    UniqueConstraint("idempotency_key", name="uq_cognition_jobs_key"),
)
Index("ix_cognition_jobs_claim", cognition_jobs.c.status, cognition_jobs.c.next_attempt_at)
Index("ix_cognition_jobs_kind", cognition_jobs.c.kind, cognition_jobs.c.status)
Index("ix_cognition_jobs_scope", cognition_jobs.c.scope)

# ADR-0001 §4.2: the recorded result of an episode (id = the first turn); the signals are
# computed from `turns`/`turn_steps`/`outbound_messages`, only the closure is stored.
EPISODE_STATUSES = ("open", "closed")
EPISODE_SIGNALS = ("idle", "marker", "topic", "commitment", "shutdown", "artifact")

episodes = Table(
    "episodes",
    metadata,
    Column("id", String(160), primary_key=True),
    Column("thread_id", String(120), nullable=False, server_default=""),
    Column("person_id", String(80), nullable=False, server_default=""),
    Column("status", String(20), nullable=False, server_default="open"),
    Column("app", String(40), nullable=False, server_default=""),
    Column("close_signal", String(20), nullable=False, server_default=""),
    Column("signature", Text, nullable=False, server_default=""),
    Column("turns", Integer, nullable=False, server_default="0"),
    Column("tool_calls", Integer, nullable=False, server_default="0"),
    Column("refs", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    Column("distilled_at", Float, nullable=False, server_default="0"),
    Column("started_at", Float, nullable=False),
    Column("closed_at", Float, nullable=True),
    CheckConstraint("status IN ('open','closed')", name="ck_episodes_status"),
)
Index("ix_episodes_thread", episodes.c.thread_id, episodes.c.status)
Index("ix_episodes_closed", episodes.c.status, episodes.c.closed_at)

