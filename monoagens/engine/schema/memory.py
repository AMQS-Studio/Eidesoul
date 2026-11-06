"""Memory rows, the readable view, and the memory bookkeeping tables (ADR-0004)."""


from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Computed,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
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

from .base import MEMORY_DIM, MEMORY_TSV, Vector, metadata

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
    # ADR-0051 §4.1: the native retrieval index is derived by Postgres, never by a writer.
    Column("embedding", Vector(MEMORY_DIM), nullable=True),
    Column("search_tsv", TSVECTOR, Computed(MEMORY_TSV, persisted=True)),
    Column("state", String(20), nullable=False, server_default="pending"),
    Column("level", SmallInteger, nullable=True),
    Column("confirmed", Boolean, nullable=True),
    Column("privacy_evidence", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    Column("privacy_policy", String(40), nullable=False, server_default=""),
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
    Column("dispute_asked_at", Float, nullable=True),
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
# ADR-0051 §4.1/§4.2: cosine HNSW and lexical GIN are the two arms of hybrid retrieval.
Index(
    "ix_memories_embedding_hnsw",
    memories.c.embedding,
    postgresql_using="hnsw",
    postgresql_ops={"embedding": "vector_cosine_ops"},
    postgresql_with={"m": 16, "ef_construction": 64},
)
Index("ix_memories_search_tsv", memories.c.search_tsv, postgresql_using="gin")
# ADR-0010 §4.7: the per-subject question budget reads by subject and ask time.
Index("ix_memories_subject_asked", memories.c.subject_id, memories.c.dispute_asked_at)
Index(
    "memories_supersede_pair",
    memories.c.id,
    memories.c.supersedes,
    unique=True,
    postgresql_where=memories.c.superseded_by.is_not(None),
)

# ADR-0004 §5.3/ADR-0051 §4.2: the only read path for prompts, recall, and disclosure.
# A row without a level or without an active state never leaves this view.
READABLE_VIEW = "memories_readable"
READABLE_VIEW_SQL = (
    f"CREATE VIEW {READABLE_VIEW} AS "
    "SELECT * FROM memories "
    "WHERE state = 'ok' AND vetoed = false AND level IS NOT NULL"
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
    # `SELECT *` in READABLE_VIEW_SQL exposes these derived columns too; the read-only
    # selectable mirrors the view exactly so a select never builds SQL the view cannot answer.
    column("embedding", Vector(MEMORY_DIM)),
    column("search_tsv", TSVECTOR),
    column("state", String(20)),
    column("level", SmallInteger),
    column("confirmed", Boolean),
    column("privacy_evidence", JSONB),
    column("privacy_policy", String(40)),
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
    column("dispute_asked_at", Float),
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
    Column("backend", String(40), nullable=False, server_default="postgres"),
    Column("external_id", Text, nullable=False),
    Column("model", Text, nullable=False, server_default=""),
    Column("dim", Integer, nullable=False, server_default="384"),
    Column("updated_at", Float, nullable=False),
    UniqueConstraint("backend", "external_id", name="uq_memory_index_external"),
)

