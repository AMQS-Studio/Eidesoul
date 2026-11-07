"""Fences, events, and efficiency columns (ADR-0002 §4.4, ADR-0010 §4.7, ADR-0001 §4.3-§4.6).

One revision carries every column the remaining ADRs need, so the chain stays append-only:

- `cognition_jobs.generation` — the lease generation a claim was made under (ADR-0002 §4.4).
- `memories.dispute_asked_at` — the per-subject question budget (ADR-0010 §4.7).
- `threads.gateway` — the durable route a thread was first seen on (ADR-0001 §4.6).
- `turns.category` — `action` turns are never consolidated away (ADR-0003 §11).
- `skills.priority` — the efficiency ladder's ordering (ADR-0001 §4.6).
- `automation_runs.payload` — what one occurrence carries (ADR-0001 §4.4).
- the partial index the message sink reads (ADR-0001 §4.3).

`memories_readable` is `SELECT *` over `memories`, so the new column is appended with
`CREATE OR REPLACE VIEW`; without it the view would silently miss `dispute_asked_at` on an
upgraded database while a fresh `create_all` database has it.
"""


import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "cognition_jobs",
        sa.Column("generation", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.add_column("memories", sa.Column("dispute_asked_at", sa.Float(), nullable=True))
    op.create_index(
        "ix_memories_subject_asked", "memories", ["subject_id", "dispute_asked_at"]
    )
    op.add_column(
        "threads",
        sa.Column("gateway", sa.String(40), nullable=False, server_default=""),
    )
    op.add_column(
        "turns",
        sa.Column("category", sa.String(20), nullable=False, server_default="chat"),
    )
    op.add_column(
        "skills",
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "automation_runs",
        sa.Column(
            "payload",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.execute(
        "CREATE INDEX ix_automations_message ON automations ((event->>'kind')) "
        "WHERE state = 'active'"
    )
    op.execute(
        "CREATE OR REPLACE VIEW memories_readable AS "
        "SELECT * FROM memories WHERE state = 'ok' AND vetoed = false"
    )


def downgrade() -> None:
    # `SELECT *` was expanded into a column list when the view was created, so the view depends
    # on `dispute_asked_at`; drop it before the column and recreate it for revision 0012.
    op.execute("DROP VIEW IF EXISTS memories_readable")
    op.execute("DROP INDEX IF EXISTS ix_automations_message")
    op.drop_column("automation_runs", "payload")
    op.drop_column("skills", "priority")
    op.drop_column("turns", "category")
    op.drop_column("threads", "gateway")
    op.drop_index("ix_memories_subject_asked", table_name="memories")
    op.drop_column("memories", "dispute_asked_at")
    op.drop_column("cognition_jobs", "generation")
    op.execute(
        "CREATE VIEW memories_readable AS "
        "SELECT * FROM memories WHERE state = 'ok' AND vetoed = false"
    )
