"""The one MetaData, the pgvector shim, and the core archive tables (ADR-0029)."""


from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Computed,
    Float,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    PrimaryKeyConstraint,
    String,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.types import UserDefinedType

metadata = MetaData()

MEMORY_DIM = 384
MEMORY_TSV = "to_tsvector('simple', coalesce(text, ''))"


class Vector(UserDefinedType):
    """A pgvector column without the optional Python package: `vector(384)` in DDL.

    The dialect has no vector codec, so the value crosses the wire as its text form; a list
    binds as `[v1,...,vn]` and a read comes back as a list of floats.
    """

    cache_ok = True

    def __init__(self, dim: int = MEMORY_DIM) -> None:
        self.dim = int(dim)

    def get_col_spec(self, **kw) -> str:
        return f"vector({self.dim})"

    def bind_processor(self, dialect):
        def process(value):
            if value is None:
                return None
            return "[" + ",".join(f"{float(item):.6f}" for item in value) + "]"

        return process

    def result_processor(self, dialect, coltype):
        def process(value):
            if value is None or isinstance(value, list):
                return value
            return [float(item) for item in str(value).strip().strip("[]").split(",") if item]

        return process

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
    CheckConstraint("visibility >= 0 AND visibility <= 4", name="ck_messages_visibility"),
)
# Media blobs separated from the row (ADR-0016 §4.5); the message keeps its metadata in `data`.
# `seq` numbers the attachments of one message, so a multi-attachment envelope keeps every
# row: `kind` is the role of the row ("media" or "quote"), never part of the identity.
message_media = Table(
    "message_media",
    metadata,
    Column("thread_id", String(120), nullable=False),
    Column("message_id", String(160), nullable=False),
    Column("seq", Integer, nullable=False, server_default="0"),
    Column("kind", String(20), nullable=False),
    Column("mime", String(160), nullable=False, server_default=""),
    Column("bytes_ref", LargeBinary, nullable=True),
    PrimaryKeyConstraint("thread_id", "message_id", "seq", name="pk_message_media"),
    ForeignKeyConstraint(
        ["thread_id", "message_id"],
        ["messages.thread_id", "messages.msg_id"],
        ondelete="CASCADE",
        name="fk_message_media_message",
    ),
)

# The durable handle of one attachment (ADR-0001 §4.2): `send_file` forwards `att_…` by id,
# so a file that arrived in one body can leave through another without reading the archive.
attachments = Table(
    "attachments",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("gateway", String(40), nullable=False, server_default=""),
    Column("thread_id", String(120), nullable=False, server_default=""),
    Column("message_id", String(160), nullable=False, server_default=""),
    Column("seq", Integer, nullable=False, server_default="0"),
    Column("kind", String(20), nullable=False, server_default=""),
    Column("mime", String(160), nullable=False, server_default=""),
    Column("filename", String(240), nullable=False, server_default=""),
    Column("path", Text, nullable=False, server_default=""),
    Column("size_bytes", BigInteger, nullable=False, server_default="0"),
    Column("sha256", String(64), nullable=False, server_default=""),
    Column("created_at", Float, nullable=False),
    UniqueConstraint("thread_id", "message_id", "seq", name="uq_attachments_message_seq"),
)
Index("ix_attachments_message", attachments.c.thread_id, attachments.c.message_id)
Index("ix_messages_ts", messages.c.ts)
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
    # ADR-0014 §4.6: the first body that sees a thread owns its route; "" means unclaimed.
    Column("gateway", String(40), nullable=False, default="", server_default=""),
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
    # ADR-0004 §4.2 / ADR-0006 §4.5: one row per outbound action id, the room that asked, and
    # the batch a multi-step workflow stages together. `action_id` is unique only when set.
    Column("action_id", Text, nullable=False, default="", server_default=""),
    Column("origin_gateway", String(40), nullable=False, default="", server_default=""),
    Column("origin_thread", String(120), nullable=False, default="", server_default=""),
    Column("batch_id", String(80), nullable=False, default="", server_default=""),
    CheckConstraint(
        "approval_class IN ('consent','authorization')", name="ck_approvals_class"
    ),
)
Index("ix_approvals_status", approvals.c.status, approvals.c.created_at)
Index(
    "ix_approvals_thread_sender_status_expires",
    approvals.c.thread_id,
    approvals.c.sender,
    approvals.c.status,
    approvals.c.expires_at,
)
Index("ix_approvals_status_expires", approvals.c.status, approvals.c.expires_at)
Index(
    "ix_approvals_action_id",
    approvals.c.action_id,
    unique=True,
    postgresql_where=text("action_id <> ''"),
)
Index("ix_approvals_batch", approvals.c.batch_id)

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

