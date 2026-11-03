"""One MetaData for all soul state; Postgres types only (ADR-0029)."""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Computed,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    PrimaryKeyConstraint,
    SmallInteger,
    String,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
    column,
    table,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR

metadata = MetaData()

documents = Table(
    "documents",
    metadata,
    Column("name", String(120), primary_key=True),
    Column("content", Text, nullable=False),
    Column("updated_at", Float, nullable=False),
)

schema_meta = Table(
    "schema_meta",
    metadata,
    Column("key", String(80), primary_key=True),
    Column("value", Text, nullable=False),
)

messages = Table(
    "messages",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("thread_id", String(120), nullable=False),
    Column("msg_id", String(160), nullable=False),
    Column("ts", Float, nullable=False),
    Column("sender_id", String(80), nullable=False, default=""),
    Column("sender_name", String(160), nullable=False, default=""),
    Column("sender_address", String(160), nullable=False, default=""),
    Column("text", Text, nullable=False, default=""),
    Column("outgoing", Boolean, nullable=False, default=False),
    Column("author", String(20), nullable=False, default="unknown", server_default="unknown"),
    Column("visibility", Integer, nullable=False, default=2, server_default="2"),
    Column("deleted", Boolean, nullable=False, default=False, server_default=text("false")),
    Column("deleted_by", String(160), nullable=False, default="", server_default=""),
    Column("deleted_admin", Boolean, nullable=False, default=False, server_default=text("false")),
    Column("data", JSONB, nullable=False, default=dict),
    Column("media_ref", LargeBinary, nullable=True),
    Column("quote_media_ref", LargeBinary, nullable=True),
    # ADR-0026 §4.1: one generated column, no application-side write path.
    Column(
        "text_tsv",
        TSVECTOR,
        Computed(
            "to_tsvector('simple', coalesce(text, '') || ' ' || coalesce(sender_name, ''))",
            persisted=True,
        ),
    ),
    UniqueConstraint("thread_id", "msg_id", name="uq_messages_thread_msg"),
)
Index("ix_messages_thread_ts", messages.c.thread_id, messages.c.ts, messages.c.id)
Index("ix_messages_sender", messages.c.sender_address)
Index("ix_messages_text_tsv", messages.c.text_tsv, postgresql_using="gin")
Index(
    "ix_messages_text_trgm",
    messages.c.text,
    postgresql_using="gin",
    postgresql_ops={"text": "gin_trgm_ops"},
)

threads = Table(
    "threads",
    metadata,
    Column("thread_id", String(120), primary_key=True),
    Column("name", String(200), nullable=False, default=""),
    Column("reset_ts", Float, nullable=False, default=0.0),
    Column("kind", String(20), nullable=False, default="dm"),
)

state = Table(
    "state",
    metadata,
    Column("key", String(80), primary_key=True),
    Column("value", Text, nullable=False),
    Column("updated_at", Float, nullable=False),
)

actions = Table(
    "actions",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("ts", Float, nullable=False),
    Column("source", String(120), nullable=False),
    Column("source_name", String(160), nullable=False, default=""),
    Column("target", String(160), nullable=False, default=""),
    Column("target_name", String(200), nullable=False, default=""),
    Column("kind", String(40), nullable=False, default="send"),
    Column("summary", Text, nullable=False, default=""),
    Column("request", Text, nullable=False, default=""),
)

approvals = Table(
    "approvals",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("created_at", Float, nullable=False),
    Column("expires_at", Float, nullable=False),
    Column("thread_id", String(120), nullable=False),
    Column("sender", String(160), nullable=False),
    Column("gateway", String(40), nullable=False, default=""),
    Column("kind", String(40), nullable=False),
    Column("tool", String(80), nullable=False, default=""),
    Column("args", JSONB, nullable=False, default=dict),
    Column("payload", JSONB, nullable=False, default=dict),
    Column("summary", Text, nullable=False, default=""),
    Column("diff", Text, nullable=False, default=""),
    Column("status", String(20), nullable=False, default="pending"),
    Column("decided_by", String(160), nullable=False, default=""),
    Column("decided_at", Float, nullable=True),
    Column("result", Text, nullable=False, default=""),
    # ADR-0007 §4.7 / ADR-0016 §4.8: the class is derived from the target, the proof is
    # recomputed at execution; neither column grants a permission.
    Column(
        "approval_class",
        String(20),
        nullable=False,
        default="authorization",
        server_default="authorization",
    ),
    Column("scope_proof", JSONB, nullable=False, default=dict),
    CheckConstraint(
        "approval_class IN ('consent','authorization')", name="ck_approvals_class"
    ),
)
Index("ix_approvals_status", approvals.c.status, approvals.c.created_at)

# ADR-0019 / ADR-0016 §4.6: ids and numbers only, never prompt text; no FK so metrics never
# block entity deletion, and a failure before a turn still leaves a row.
llm_calls = Table(
    "llm_calls",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("ts", Float, nullable=False),
    Column("turn_id", String(120), nullable=True),
    Column("job_id", BigInteger, nullable=True),
    Column("run_id", String(120), nullable=True),
    Column("purpose", String(40), nullable=False),
    Column("model", String(120), nullable=False),
    Column("tokens_in", Integer, nullable=True),
    Column("tokens_out", Integer, nullable=True),
    Column("latency_ms", Integer, nullable=False),
    Column("ok", Boolean, nullable=False),
)
Index("ix_llm_calls_ts", llm_calls.c.ts)
Index("ix_llm_calls_turn", llm_calls.c.turn_id)

# ADR-0023 §4.1 / ADR-0016 §4.9: one row per (task, from, to); the row holds the decision and
# never the message content. `mode='none'` is stored too, so a refusal stays auditable.
relay_grants = Table(
    "relay_grants",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("task_id", String(120), nullable=False),
    Column("from_person", String(80), nullable=False),
    Column("to_person", String(80), nullable=False),
    Column("mode", String(20), nullable=False, default="none", server_default="none"),
    Column("scope_ref", String(160), nullable=False, default="", server_default=""),
    Column("created_at", Float, nullable=False),
    Column("expires_at", Float, nullable=True),
    Column("revoked_at", Float, nullable=True),
    Column("decided_via", String(40), nullable=False, default="", server_default=""),
    CheckConstraint(
        "mode IN ('full','summary','anonymous','none')", name="ck_relay_grants_mode"
    ),
)
Index(
    "ux_relay_grant",
    relay_grants.c.task_id,
    relay_grants.c.from_person,
    relay_grants.c.to_person,
    unique=True,
)
Index("ix_relay_grants_expiry", relay_grants.c.expires_at)

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

people = Table(
    "people",
    metadata,
    Column("person_id", String(80), primary_key=True),
    Column("name", String(160), nullable=False, default=""),
    Column("aliases", JSONB, nullable=False, default=list),
    Column("role", String(40), nullable=False, default=""),
    Column("stage", String(40), nullable=False, default=""),
    Column("trust", Float, nullable=False, default=0.0),
    Column("affection", Float, nullable=False, default=0.0),
    Column("respect", Float, nullable=False, default=0.0),
    Column("annoyance", Float, nullable=False, default=0.0),
    Column("familiarity", Float, nullable=False, default=0.0),
    Column("interactions", Integer, nullable=False, default=0),
    Column("appease", Float, nullable=False, default=0.0),
    Column("last_seen", Float, nullable=False, default=0.0),
    Column("window_start", Float, nullable=False, default=0.0),
    Column("window_trust", Float, nullable=False, default=0.0),
    Column("window_affection", Float, nullable=False, default=0.0),
    Column("window_respect", Float, nullable=False, default=0.0),
    Column("window_annoyance", Float, nullable=False, default=0.0),
    Column("notes", JSONB, nullable=False, default=list),
    Column("gender", String(12), nullable=False, default=""),
    Column("gender_confidence", Float, nullable=False, default=0.0),
    Column("gender_source", String(16), nullable=False, default=""),
)
# ADR-0026 §4.3: the trigram net for a person's display name (the column is `name`).
Index(
    "ix_people_name_trgm",
    people.c.name,
    postgresql_using="gin",
    postgresql_ops={"name": "gin_trgm_ops"},
)

topics = Table(
    "topics",
    metadata,
    Column("thread_id", String(120), primary_key=True),
    Column("summary", Text, nullable=False, default=""),
    Column("keywords", JSONB, nullable=False, default=list),
    Column("unresolved", JSONB, nullable=False, default=list),
    Column("pending", Integer, nullable=False, default=0),
    Column("last_message", Float, nullable=False, default=0.0),
    Column("last_digest", Float, nullable=False, default=0.0),
    Column("last_research", Float, nullable=False, default=0.0),
    Column("research_day", String(20), nullable=False, default=""),
    Column("research_count", Integer, nullable=False, default=0),
)

thread_eras = Table(
    "thread_eras",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("thread_id", String(120), nullable=False),
    Column("started_at", Float, nullable=False),
    Column("ended_at", Float, nullable=False),
    Column("summary", Text, nullable=False, default=""),
    Column("keywords", JSONB, nullable=False, default=list),
    Column("unresolved", JSONB, nullable=False, default=list),
)
Index("ix_thread_eras_thread", thread_eras.c.thread_id, thread_eras.c.ended_at)

drives = Table(
    "drives",
    metadata,
    Column("thread_id", String(120), primary_key=True),
    Column("energy", Float, nullable=False, default=0.8),
    Column("boredom", Float, nullable=False, default=0.2),
    Column("loneliness", Float, nullable=False, default=0.2),
    Column("last_pulse", Float, nullable=False, default=0.0),
)

thread_people = Table(
    "thread_people",
    metadata,
    Column("thread_id", String(120), nullable=False),
    Column("person_id", String(80), nullable=False),
    Column("last_seen", Float, nullable=False, default=0.0),
    Column("interactions", Integer, nullable=False, default=0),
    PrimaryKeyConstraint("thread_id", "person_id", name="pk_thread_people"),
)

person_links = Table(
    "person_links",
    metadata,
    Column("gateway", String(40), primary_key=True),
    Column("address", String(160), primary_key=True),
    Column("person_id", String(80), nullable=False),
    Column("created_at", Float, nullable=False),
    Column("seen_at", Float, nullable=False, default=0.0),
    Column("linked_by", String(120), nullable=False, default=""),
)
Index("ix_person_links_person", person_links.c.person_id)

person_link_codes = Table(
    "person_link_codes",
    metadata,
    Column("code", String(16), primary_key=True),
    Column("person_id", String(80), nullable=False),
    Column("gateway", String(40), nullable=False),
    Column("created_at", Float, nullable=False),
    Column("expires_at", Float, nullable=False),
    Column("attempts", Integer, nullable=False, default=0),
    Column("used_at", Float, nullable=False, default=0.0),
)

# ADR-0020 §4.5 / ADR-0016 §4.7: a procedure carries scope, provenance, trust, and a revision.
skills = Table(
    "skills",
    metadata,
    Column("name", String(80), primary_key=True),
    Column("description", String(400), nullable=False, default=""),
    Column("body", Text, nullable=False, default=""),
    Column("archived", Boolean, nullable=False, default=False),
    Column("created_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
    Column("scope", String(20), nullable=False, server_default="thread"),
    Column("via", String(20), nullable=False, server_default="reflection"),
    Column("stated_by", String(80), nullable=True),
    Column("source_episode", String(160), nullable=True),
    Column("uses", Integer, nullable=False, server_default="0"),
    Column("last_used", Float, nullable=True),
    Column("revision", Integer, nullable=False, server_default="1"),
    Column("trust", String(8), nullable=False, server_default="T2"),
    CheckConstraint("scope IN ('thread','person','global')", name="ck_skills_scope"),
    CheckConstraint("trust IN ('T0','T1','T2','T3')", name="ck_skills_trust"),
)
Index("ix_skills_scope", skills.c.scope)

# Append-only history (ADR-0020 §4.5): a save/merge never destroys the previous version.
skill_revisions = Table(
    "skill_revisions",
    metadata,
    Column(
        "skill",
        String(80),
        ForeignKey("skills.name", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("revision", Integer, nullable=False),
    Column("description", String(400), nullable=False, server_default=""),
    Column("body", Text, nullable=False),
    Column("created_at", Float, nullable=False),
    Column("source_episode", String(160), nullable=True),
    PrimaryKeyConstraint("skill", "revision", name="pk_skill_revisions"),
)
Index("ix_skill_revisions_skill", skill_revisions.c.skill, skill_revisions.c.revision.desc())

skill_files = Table(
    "skill_files",
    metadata,
    Column("skill", String(80), nullable=False),
    Column("path", String(300), nullable=False),
    Column("content", LargeBinary, nullable=False),
    Column("updated_at", Float, nullable=False),
    PrimaryKeyConstraint("skill", "path", name="pk_skill_files"),
)

# `skill_usage` is retired: ADR-0020 §4.5 moved `uses`/`last_used` onto the `skills` row, so the
# old counter table has no reader. Revision 5 drops it (ADR-0016 §4.3 rule 3 note in the migration).

recipes = Table(
    "recipes",
    metadata,
    Column("name", String(80), primary_key=True),
    Column("body", Text, nullable=False),
    Column("updated_at", Float, nullable=False),
)

config_changes = Table(
    "config_changes",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("ts", Float, nullable=False),
    Column("actor", String(160), nullable=False, default=""),
    Column("file", String(80), nullable=False),
    Column("path", String(200), nullable=False, default=""),
    Column("old", Text, nullable=False, default=""),
    Column("new", Text, nullable=False, default=""),
)

engine_lease = Table(
    "engine_lease",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("instance_id", String(80), nullable=False, default=""),
    Column("host", String(200), nullable=False, default=""),
    Column("pid", Integer, nullable=False, default=0),
    Column("mode", String(20), nullable=False, default="standby"),
    Column("generation", BigInteger, nullable=False, default=0, server_default="0"),
    Column("started_at", Float, nullable=False, default=0.0),
    Column("heartbeat_at", Float, nullable=False, default=0.0),
    CheckConstraint("id = 1", name="ck_engine_lease_single"),
)

engine_control = Table(
    "engine_control",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("frozen", Boolean, nullable=False, default=False),
    Column("frozen_by", String(120), nullable=False, default=""),
    Column("frozen_at", Float, nullable=False, default=0.0),
    Column("shutting_down", Boolean, nullable=False, default=False, server_default=text("false")),
)

# ---------------------------------------------------------------- memory
# ADR-0016 §4.2 is the one DDL owner; ADR-0001/0004/0005/0008 own the semantics.
# `deleted` rows never live here: purge removes them and leaves a content-free tombstone.

memories = Table(
    "memories",
    metadata,
    Column("id", Uuid(as_uuid=False), primary_key=True),
    Column("scope", String(20), nullable=False, server_default="person"),
    Column("person_id", String(80), nullable=False, server_default=""),
    Column("thread_id", String(120), nullable=False, server_default=""),
    Column("kind", String(20), nullable=False, server_default="fact"),
    Column("text", Text, nullable=False),
    Column("state", String(20), nullable=False, server_default="pending"),
    Column("level", SmallInteger, nullable=True),
    Column("confirmed", Boolean, nullable=True),
    Column("label_attempts", Integer, nullable=False, server_default="0"),
    Column("last_error", Text, nullable=False, server_default=""),
    Column("label_after", Float, nullable=False, server_default="0"),
    Column("owner_id", String(80), nullable=False, server_default=""),
    Column("subject_id", String(80), nullable=False, server_default=""),
    Column("via", String(20), nullable=False, server_default="turn"),
    Column("refs", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    Column("trust", SmallInteger, nullable=False, server_default="1"),
    Column("audience", String(20), nullable=False, server_default="private"),
    Column("source", String(40), nullable=False, server_default="turn"),
    Column("vetoed", Boolean, nullable=False, server_default=text("false")),
    Column("vetoed_by", String(80), nullable=False, server_default=""),
    Column("vetoed_at", Float, nullable=False, server_default="0"),
    Column("pinned", Boolean, nullable=False, server_default=text("false")),
    Column("durability", String(20), nullable=False, server_default="normal"),
    Column("category", String(20), nullable=False, server_default="general"),
    Column("use_count", Integer, nullable=False, server_default="0"),
    Column("distinct_use_days", Integer, nullable=False, server_default="0"),
    Column("last_used_at", Float, nullable=False, server_default="0"),
    Column("max_lifetime_at", Float, nullable=True),
    Column("expires_at", Float, nullable=True),
    Column("supersedes", Uuid(as_uuid=False), nullable=True),
    Column("superseded_by", Uuid(as_uuid=False), nullable=True),
    Column("due_at", Float, nullable=True),
    Column("task_status", String(20), nullable=True),
    Column("dispute_state", String(20), nullable=False, server_default="none"),
    Column("disputed_with", Uuid(as_uuid=False), nullable=True),
    Column("dispute_reason", Text, nullable=False, server_default=""),
    Column("disputed_at", Float, nullable=False, server_default="0"),
    Column("asserted_at", Float, nullable=False, server_default="0"),
    Column("created_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
    CheckConstraint(
        "state IN ('pending','ok','staged','failed','not_worth','superseded')",
        name="ck_memories_state",
    ),
    CheckConstraint(
        "(state IN ('ok','staged','superseded')) = (level IS NOT NULL)",
        name="ck_memories_level_state",
    ),
    CheckConstraint("level IS NULL OR level BETWEEN 0 AND 4", name="ck_memories_level"),
    CheckConstraint("state <> 'ok' OR confirmed IS NOT NULL", name="ck_memories_confirmed"),
    CheckConstraint("scope IN ('global','person','thread')", name="ck_memories_scope"),
    CheckConstraint(
        "via IN ('turn','tool','command','reflection','ambient','automation','soul_doc')",
        name="ck_memories_via",
    ),
    CheckConstraint(
        "dispute_state IN ('none','open','resolved','stale')", name="ck_memories_dispute"
    ),
)
Index("memories_live", memories.c.scope, memories.c.person_id, memories.c.state)
Index("memories_thread", memories.c.thread_id, memories.c.state)
Index(
    "memories_expiry",
    memories.c.expires_at,
    postgresql_where=memories.c.expires_at.is_not(None),
)
Index("memories_refs_gin", memories.c.refs, postgresql_using="gin")
Index(
    "memories_supersede_pair",
    memories.c.id,
    memories.c.supersedes,
    unique=True,
    postgresql_where=memories.c.superseded_by.is_not(None),
)

# ADR-0004 §5.3: the only read path for prompts, recall, and disclosure.
READABLE_VIEW = "memories_readable"
READABLE_VIEW_SQL = (
    f"CREATE VIEW {READABLE_VIEW} AS "
    "SELECT * FROM memories WHERE state = 'ok' AND vetoed = false"
)

# A read-only selectable for the view: registered nowhere, created by the migration/`create_all`.
memories_readable = table(
    READABLE_VIEW,
    column("id", Uuid(as_uuid=False)),
    column("scope", String(20)),
    column("person_id", String(80)),
    column("thread_id", String(120)),
    column("kind", String(20)),
    column("text", Text),
    column("state", String(20)),
    column("level", SmallInteger),
    column("confirmed", Boolean),
    column("label_attempts", Integer),
    column("last_error", Text),
    column("label_after", Float),
    column("owner_id", String(80)),
    column("subject_id", String(80)),
    column("via", String(20)),
    column("refs", JSONB),
    column("trust", SmallInteger),
    column("audience", String(20)),
    column("source", String(40)),
    column("vetoed", Boolean),
    column("vetoed_by", String(80)),
    column("vetoed_at", Float),
    column("pinned", Boolean),
    column("durability", String(20)),
    column("category", String(20)),
    column("use_count", Integer),
    column("distinct_use_days", Integer),
    column("last_used_at", Float),
    column("max_lifetime_at", Float),
    column("expires_at", Float),
    column("supersedes", Uuid(as_uuid=False)),
    column("superseded_by", Uuid(as_uuid=False)),
    column("due_at", Float),
    column("task_status", String(20)),
    column("dispute_state", String(20)),
    column("disputed_with", Uuid(as_uuid=False)),
    column("dispute_reason", Text),
    column("disputed_at", Float),
    column("asserted_at", Float),
    column("created_at", Float),
    column("updated_at", Float),
)

# ADR-0001: the raw capture journal, immutable, written in the intake transaction.
memory_events = Table(
    "memory_events",
    metadata,
    Column("id", Uuid(as_uuid=False), primary_key=True),
    Column(
        "memory_id",
        Uuid(as_uuid=False),
        ForeignKey("memories.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("thread_id", String(120), nullable=False, server_default=""),
    Column("person_id", String(80), nullable=False, server_default=""),
    Column("message_id", String(160), nullable=False, server_default=""),
    Column("kind", String(20), nullable=False, server_default="message"),
    Column("content", Text, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("created_at", Float, nullable=False),
)
Index("ix_memory_events_memory", memory_events.c.memory_id)
Index("ix_memory_events_thread", memory_events.c.thread_id, memory_events.c.created_at)

# ADR-0005 §4.3: one touch per memory per turn, counted only on a delivered turn.
memory_uses = Table(
    "memory_uses",
    metadata,
    Column(
        "memory_id",
        Uuid(as_uuid=False),
        ForeignKey("memories.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("day", Date, nullable=False),
    Column("turn_id", String(160), nullable=False),
    PrimaryKeyConstraint("memory_id", "day", "turn_id", name="pk_memory_uses"),
)
Index("ix_memory_uses_turn", memory_uses.c.turn_id)

# ADR-0005 §4.6: content-free audit after a purge/forget.
memory_tombstones = Table(
    "memory_tombstones",
    metadata,
    Column("id", Uuid(as_uuid=False), primary_key=True),
    Column("kind", String(20), nullable=False),
    Column("level", SmallInteger, nullable=True),
    Column("scope", String(20), nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("reason", String(20), nullable=False),
    Column("deleted_at", Float, nullable=False),
)
Index("ix_memory_tombstones_deleted", memory_tombstones.c.deleted_at)

# ADR-0001: the canonical row -> derived index entry map; rebuildable, never read as truth.
memory_index = Table(
    "memory_index",
    metadata,
    Column(
        "memory_id",
        Uuid(as_uuid=False),
        ForeignKey("memories.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("backend", String(40), nullable=False, server_default="mem0"),
    Column("external_id", Text, nullable=False),
    Column("model", Text, nullable=False, server_default=""),
    Column("dim", Integer, nullable=False, server_default="384"),
    Column("updated_at", Float, nullable=False),
    UniqueConstraint("backend", "external_id", name="uq_memory_index_external"),
)
Index("ix_memory_index_external", memory_index.c.backend, memory_index.c.external_id)

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

# ADR-0020 §4.2: the recorded result of an episode (id = the first turn); the signals are
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

# ---------------------------------------------------------------- turns
# ADR-0013 §6: one row per incoming message, message id is the idempotency key.

TURN_STATUSES = ("received", "running", "sending", "delivered", "interrupted", "expired")
TURN_STEPS = ("received", "decided", "drafted", "reviewed", "sending", "delivered")
TURN_TERMINAL = frozenset({"delivered", "interrupted", "expired"})

turns = Table(
    "turns",
    metadata,
    Column("id", String(160), primary_key=True),
    Column("thread_id", String(120), nullable=False),
    Column("person_id", String(80), nullable=False, default=""),
    Column("status", String(20), nullable=False, default="received"),
    Column("step", String(20), nullable=False, default="received"),
    Column("attempt", Integer, nullable=False, default=0, server_default="0"),
    Column("lease_owner", String(80), nullable=False, default="", server_default=""),
    Column("lease_until", Float, nullable=False, default=0.0, server_default="0"),
    Column("generation", BigInteger, nullable=False, default=0, server_default="0"),
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
# ADR-0013 §6 assigns to `turn_actions` plus the checkpoint name and its timestamps.
turn_steps = Table(
    "turn_steps",
    metadata,
    Column("turn_id", String(160), nullable=False),
    Column("step_index", Integer, nullable=False),
    Column("step", String(20), nullable=False, default="drafted", server_default="drafted"),
    Column("tool", Text, nullable=False),
    Column("args_hash", String(64), nullable=False),
    Column("result", Text, nullable=True),
    Column("status", String(20), nullable=False, default="executed"),
    Column("started_at", Float, nullable=False, default=0.0, server_default="0"),
    Column("finished_at", Float, nullable=True),
    PrimaryKeyConstraint("turn_id", "step_index", name="pk_turn_steps"),
    CheckConstraint("status IN ('executed','failed')", name="ck_turn_steps_status"),
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

google_identity = Table(
    "google_identity",
    metadata,
    Column("person_id", String(160), primary_key=True),
    Column("email", String(320), nullable=False),
    Column("subject", String(128), nullable=False, default=""),
    Column("linked_at", Float, nullable=False),
)

google_device = Table(
    "google_device",
    metadata,
    Column("person_id", String(160), primary_key=True),
    Column("device_code", Text, nullable=False),
    Column("user_code", String(32), nullable=False),
    Column("verification_url", String(300), nullable=False),
    Column("expires_at", Float, nullable=False),
    Column("interval", Integer, nullable=False, default=5),
    Column("status", String(20), nullable=False, default="pending"),
    Column("last_error", Text, nullable=False, default=""),
    Column("created_at", Float, nullable=False),
)
