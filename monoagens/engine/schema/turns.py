"""Turns, their step log, and outbound message rows (ADR-0003)."""


from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    Float,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Table,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

from .base import metadata

# ---------------------------------------------------------------- turns
# ADR-0003 §6: one row per incoming message; `id` is the namespaced identity
# `<gateway>:<thread>:<message_id>`, and `message_id` keeps the raw transport id a resume
# needs to re-read the row from the archive (ADR-0001 §4.3).

TURN_STATUSES = ("received", "running", "sending", "delivered", "interrupted", "expired")
TURN_STEPS = ("received", "decided", "drafted", "reviewed", "sending", "delivered")
TURN_TERMINAL = frozenset({"delivered", "interrupted", "expired"})
# A step row is written `running` before the tool runs; a crash leaves it there and the next
# claim turns it into `interrupted`, so a side-effecting tool is never executed twice.
TURN_STEP_STATUSES = ("running", "executed", "failed", "interrupted")

turns = Table(
    "turns",
    metadata,
    Column("id", String(160), primary_key=True),
    Column("message_id", String(160), nullable=False, default="", server_default=""),
    Column("thread_id", String(120), nullable=False),
    Column("person_id", String(80), nullable=False, default=""),
    Column("status", String(20), nullable=False, default="received"),
    Column("step", String(20), nullable=False, default="received"),
    Column("app", String(40), nullable=False, default="", server_default=""),
    Column("attempt", Integer, nullable=False, default=0, server_default="0"),
    Column("lease_owner", String(80), nullable=False, default="", server_default=""),
    Column("lease_until", Float, nullable=False, default=0.0, server_default="0"),
    Column("generation", BigInteger, nullable=False, default=0, server_default="0"),
    # ADR-0003 §11: `action` turns are never consolidated away by the backlog selector.
    Column("category", String(20), nullable=False, default="chat", server_default="chat"),
    # ADR-0001/ADR-0003: the body's percept hints ride on the turn so a resume can restore them.
    Column("hints", JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")),
    Column("decision_json", JSONB, nullable=True),
    Column("draft_text", Text, nullable=True),
    Column("reviewed_text", Text, nullable=True),
    Column("planned_chunks", Integer, nullable=False, default=0, server_default="0"),
    Column("delivered_chunks", Integer, nullable=False, default=0, server_default="0"),
    Column("shutdown_epoch", BigInteger, nullable=False, default=0, server_default="0"),
    Column("boot_epoch", BigInteger, nullable=False, default=0, server_default="0"),
    Column("last_error", Text, nullable=False, default="", server_default=""),
    Column("created_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
    CheckConstraint(
        "status IN ('received','running','sending','delivered','interrupted','expired')",
        name="ck_turns_status",
    ),
    CheckConstraint(
        "step IN ('received','decided','drafted','reviewed','sending','delivered')",
        name="ck_turns_step",
    ),
)
Index("ix_turns_status_lease", turns.c.status, turns.c.lease_until)
Index("ix_turns_thread_created", turns.c.thread_id, turns.c.created_at)

# `turn_steps` is the one step log named by ADR-0009/0011/0016: it carries the columns
# ADR-0003 §6 assigns to `turn_actions` plus the checkpoint name and its timestamps.
turn_steps = Table(
    "turn_steps",
    metadata,
    Column("turn_id", String(160), nullable=False),
    Column("step_index", Integer, nullable=False),
    Column("step", String(20), nullable=False, default="drafted", server_default="drafted"),
    Column("tool", Text, nullable=False),
    Column("target", String(16), nullable=False, default="", server_default=""),
    Column(
        "approval_class", String(16), nullable=False, default="none", server_default="none"
    ),
    Column("args_hash", String(64), nullable=False),
    Column("result", Text, nullable=True),
    Column("status", String(20), nullable=False, default="executed"),
    Column("started_at", Float, nullable=False, default=0.0, server_default="0"),
    Column("finished_at", Float, nullable=True),
    PrimaryKeyConstraint("turn_id", "step_index", name="pk_turn_steps"),
    CheckConstraint(
        "status IN ('running','executed','failed','interrupted')",
        name="ck_turn_steps_status",
    ),
)

outbound_messages = Table(
    "outbound_messages",
    metadata,
    Column("scope_key", Text, nullable=False),
    Column("chunk_index", Integer, nullable=False),
    Column("gateway", String(40), nullable=False, default=""),
    Column("thread_id", String(120), nullable=False, default=""),
    Column("provider_id", Text, nullable=False, default="", server_default=""),
    Column("status", String(20), nullable=False, default="intent", server_default="intent"),
    Column("generation", BigInteger, nullable=False, default=0, server_default="0"),
    PrimaryKeyConstraint("scope_key", "chunk_index", name="pk_outbound_messages"),
    CheckConstraint(
        "status IN ('intent','sent','unknown')", name="ck_outbound_messages_status"
    ),
)

