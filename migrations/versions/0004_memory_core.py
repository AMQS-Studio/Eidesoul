"""memory core: canonical `memories`, the journal, uses, tombstones, the index map, and the view.

Revision 4 lands the whole memory spine named by ADR-0001, ADR-0004, ADR-0005, and ADR-0008;
ADR-0016 §4.2-§4.3 owns the final column shape, CHECKs, and indexes (with `thread_id` and
`scope='thread'` from ADR-0000 §4.4).

Nothing is deployed at 0.0.0.devN, so there is no intermediate `memories` shape to upgrade:
splitting these tables across revisions 4-7 would create states that no ADR accepts (a
`memories` row with no `state`, a levelless `superseded` row, provenance added after retention).
The genuinely separate mechanisms stay out of this revision: the labeling/assess trigger
(ADR-0002), `cognition_jobs` and its worker (ADR-0003), and `staged` promotion batching are code,
not DDL. ADR-0010's dispute columns already live in §4.2 and are therefore created here.
"""


import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=False)
EMPTY_REFS = sa.text("'[]'::jsonb")


def upgrade() -> None:
    op.create_table(
        "memories",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("scope", sa.String(20), nullable=False, server_default="person"),
        sa.Column("person_id", sa.String(80), nullable=False, server_default=""),
        sa.Column("thread_id", sa.String(120), nullable=False, server_default=""),
        sa.Column("kind", sa.String(20), nullable=False, server_default="fact"),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("state", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("level", sa.SmallInteger(), nullable=True),
        sa.Column("confirmed", sa.Boolean(), nullable=True),
        sa.Column("label_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("label_after", sa.Float(), nullable=False, server_default="0"),
        sa.Column("owner_id", sa.String(80), nullable=False, server_default=""),
        sa.Column("subject_id", sa.String(80), nullable=False, server_default=""),
        sa.Column("via", sa.String(20), nullable=False, server_default="turn"),
        sa.Column("refs", postgresql.JSONB(), nullable=False, server_default=EMPTY_REFS),
        sa.Column("trust", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("audience", sa.String(20), nullable=False, server_default="private"),
        sa.Column("source", sa.String(40), nullable=False, server_default="turn"),
        sa.Column("vetoed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("vetoed_by", sa.String(80), nullable=False, server_default=""),
        sa.Column("vetoed_at", sa.Float(), nullable=False, server_default="0"),
        sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("durability", sa.String(20), nullable=False, server_default="normal"),
        sa.Column("category", sa.String(20), nullable=False, server_default="general"),
        sa.Column("use_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("distinct_use_days", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_used_at", sa.Float(), nullable=False, server_default="0"),
        sa.Column("max_lifetime_at", sa.Float(), nullable=True),
        sa.Column("expires_at", sa.Float(), nullable=True),
        sa.Column("supersedes", UUID, nullable=True),
        sa.Column("superseded_by", UUID, nullable=True),
        sa.Column("due_at", sa.Float(), nullable=True),
        sa.Column("task_status", sa.String(20), nullable=True),
        sa.Column("dispute_state", sa.String(20), nullable=False, server_default="none"),
        sa.Column("disputed_with", UUID, nullable=True),
        sa.Column("dispute_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("disputed_at", sa.Float(), nullable=False, server_default="0"),
        sa.Column("asserted_at", sa.Float(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.CheckConstraint(
            "state IN ('pending','ok','staged','failed','not_worth','superseded')",
            name="ck_memories_state",
        ),
        sa.CheckConstraint(
            "(state IN ('ok','staged','superseded')) = (level IS NOT NULL)",
            name="ck_memories_level_state",
        ),
        sa.CheckConstraint("level IS NULL OR level BETWEEN 0 AND 4", name="ck_memories_level"),
        sa.CheckConstraint("state <> 'ok' OR confirmed IS NOT NULL", name="ck_memories_confirmed"),
        sa.CheckConstraint("scope IN ('global','person','thread')", name="ck_memories_scope"),
        sa.CheckConstraint(
            "via IN ('turn','tool','command','reflection','ambient','automation','soul_doc')",
            name="ck_memories_via",
        ),
        sa.CheckConstraint(
            "dispute_state IN ('none','open','resolved','stale')", name="ck_memories_dispute"
        ),
    )
    op.create_index("memories_live", "memories", ["scope", "person_id", "state"])
    op.create_index("memories_thread", "memories", ["thread_id", "state"])
    op.create_index(
        "memories_expiry",
        "memories",
        ["expires_at"],
        postgresql_where=sa.text("expires_at IS NOT NULL"),
    )
    op.create_index(
        "memories_refs_gin", "memories", ["refs"], postgresql_using="gin"
    )
    op.create_index(
        "memories_supersede_pair",
        "memories",
        ["id", "supersedes"],
        unique=True,
        postgresql_where=sa.text("superseded_by IS NOT NULL"),
    )
    op.execute(
        "CREATE VIEW memories_readable AS "
        "SELECT * FROM memories WHERE state = 'ok' AND vetoed = false"
    )

    op.create_table(
        "memory_events",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "memory_id",
            UUID,
            sa.ForeignKey("memories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("thread_id", sa.String(120), nullable=False, server_default=""),
        sa.Column("person_id", sa.String(80), nullable=False, server_default=""),
        sa.Column("message_id", sa.String(160), nullable=False, server_default=""),
        sa.Column("kind", sa.String(20), nullable=False, server_default="message"),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
    )
    op.create_index("ix_memory_events_memory", "memory_events", ["memory_id"])
    op.create_index("ix_memory_events_thread", "memory_events", ["thread_id", "created_at"])

    op.create_table(
        "memory_uses",
        sa.Column(
            "memory_id",
            UUID,
            sa.ForeignKey("memories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("turn_id", sa.String(160), nullable=False),
        sa.PrimaryKeyConstraint("memory_id", "day", "turn_id", name="pk_memory_uses"),
    )
    op.create_index("ix_memory_uses_turn", "memory_uses", ["turn_id"])

    op.create_table(
        "memory_tombstones",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("level", sa.SmallInteger(), nullable=True),
        sa.Column("scope", sa.String(20), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("reason", sa.String(20), nullable=False),
        sa.Column("deleted_at", sa.Float(), nullable=False),
    )
    op.create_index("ix_memory_tombstones_deleted", "memory_tombstones", ["deleted_at"])

    op.create_table(
        "memory_index",
        sa.Column(
            "memory_id",
            UUID,
            sa.ForeignKey("memories.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("backend", sa.String(40), nullable=False, server_default="mem0"),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False, server_default=""),
        sa.Column("dim", sa.Integer(), nullable=False, server_default="384"),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.UniqueConstraint("backend", "external_id", name="uq_memory_index_external"),
    )
    op.create_index("ix_memory_index_external", "memory_index", ["backend", "external_id"])


def downgrade() -> None:
    op.drop_index("ix_memory_index_external", table_name="memory_index")
    op.drop_table("memory_index")
    op.drop_index("ix_memory_tombstones_deleted", table_name="memory_tombstones")
    op.drop_table("memory_tombstones")
    op.drop_index("ix_memory_uses_turn", table_name="memory_uses")
    op.drop_table("memory_uses")
    op.drop_index("ix_memory_events_thread", table_name="memory_events")
    op.drop_index("ix_memory_events_memory", table_name="memory_events")
    op.drop_table("memory_events")
    op.execute("DROP VIEW memories_readable")
    op.drop_index("memories_supersede_pair", table_name="memories")
    op.drop_index("memories_refs_gin", table_name="memories")
    op.drop_index("memories_expiry", table_name="memories")
    op.drop_index("memories_thread", table_name="memories")
    op.drop_index("memories_live", table_name="memories")
    op.drop_table("memories")
