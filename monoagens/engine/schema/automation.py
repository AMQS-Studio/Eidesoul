"""Automation rows and their run log (ADR-0014)."""


from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Float,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

from .base import metadata

# ---------------------------------------------------------------- automation
# ADR-0014 §4.1/§4.13: the row is the declarative promise; the owner is the authority source.
# `state` is the automation life cycle, `automation_runs.status` is one occurrence.
AUTOMATION_STATES = ("draft", "active", "paused", "stopped", "expired")
AUTOMATION_TERMINAL = frozenset({"stopped", "expired"})
AUTOMATION_RUN_STATUSES = (
    "queued",
    "claimed",
    "running",
    "succeeded",
    "failed",
    "skipped",
    "missed",
    "denied",
    "interrupted",
    "cancelled",
)
AUTOMATION_STEP_KINDS = (
    "collect",
    "sandbox",
    "web_search",
    "compose",
    "send",
    "write",
    "remember",
    "queue_reflection",
    "recipe",
    "relay",
)

automations = Table(
    "automations",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("owner", Text, nullable=False),
    Column("name", Text, nullable=False, server_default=""),
    Column("event", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("condition", JSONB, nullable=False, server_default=text("'{\"all\":[]}'::jsonb")),
    Column("steps", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    Column("policy", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("state", String(20), nullable=False, server_default="draft"),
    Column("reason", Text, nullable=False, server_default=""),
    Column("owner_roles_at_creation", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    Column("approval_id", BigInteger, nullable=True),
    Column("next_occurrence", Float, nullable=False, server_default="0"),
    Column("fire_count", Integer, nullable=False, server_default="0"),
    Column("ends_at", Float, nullable=True),
    Column("created_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
    CheckConstraint(
        "state IN ('draft','active','paused','stopped','expired')",
        name="ck_automations_state",
    ),
)
Index("ix_automations_due", automations.c.state, automations.c.next_occurrence)
# ADR-0014 §4.3: the message sink looks up active message automations only.
Index(
    "ix_automations_message",
    automations.c.event["kind"].astext,
    _table=automations,
    postgresql_where=automations.c.state == "active",
)
Index("ix_automations_owner", automations.c.owner, automations.c.state)

automation_runs = Table(
    "automation_runs",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("automation_id", BigInteger, nullable=False),
    Column("occurrence_key", Text, nullable=False),
    Column("scheduled_at", Float, nullable=False),
    Column("status", String(20), nullable=False, server_default="queued"),
    Column("attempts", Integer, nullable=False, server_default="0"),
    Column("next_attempt_at", Float, nullable=False, server_default="0"),
    Column("claimed_by", Text, nullable=False, server_default=""),
    Column("claimed_at", Float, nullable=True),
    Column("lease_until", Float, nullable=False, server_default="0"),
    Column("generation", BigInteger, nullable=False, server_default="0"),
    Column("step_index", Integer, nullable=False, server_default="-1"),
    Column("deferred", Boolean, nullable=False, server_default=text("false")),
    Column("detail", Text, nullable=False, server_default=""),
    Column("apology_sent_at", Float, nullable=True),
    # ADR-0014 §4.4: what the occurrence carries for the condition and step payloads.
    Column("payload", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("started_at", Float, nullable=True),
    Column("finished_at", Float, nullable=True),
    Column("created_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
    CheckConstraint(
        "status IN ('queued','claimed','running','succeeded','failed','skipped',"
        "'missed','denied','interrupted','cancelled')",
        name="ck_automation_runs_status",
    ),
    UniqueConstraint("occurrence_key", name="uq_automation_runs_occurrence"),
)
Index("ix_automation_runs_claim", automation_runs.c.status, automation_runs.c.scheduled_at)
Index(
    "ix_automation_runs_automation",
    automation_runs.c.automation_id,
    automation_runs.c.scheduled_at,
)

automation_state_log = Table(
    "automation_state_log",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("automation_id", BigInteger, nullable=False),
    Column("from_state", Text, nullable=False),
    Column("to_state", Text, nullable=False),
    Column("reason", Text, nullable=False, server_default=""),
    Column("actor", Text, nullable=False, server_default=""),
    Column("at", Float, nullable=False),
)
Index(
    "ix_automation_state_log",
    automation_state_log.c.automation_id,
    automation_state_log.c.at.desc(),
)

