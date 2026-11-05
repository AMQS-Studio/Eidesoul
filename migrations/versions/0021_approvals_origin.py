"""approvals carry their origin, an action id, and a workflow batch

Revision ID: 0021
Revises: 0020
Create Date: 2026-11-05

ADR-0004 §4.2 / ADR-0006 §4.5: a staged action records the room that asked for it
(`origin_gateway`, `origin_thread`), the outbound correlation id it belongs to (`action_id`),
and the batch a multi-step workflow stages together (`batch_id`). `action_id` is unique only
when it is set, so a tool approval without a correlation id may repeat.
"""

import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


BACKFILL_ORIGIN = """
UPDATE approvals
SET origin_gateway = gateway, origin_thread = thread_id
WHERE origin_thread = ''
"""

# The newest send row per correlation id keeps the id; older duplicates stay empty so the
# partial unique index below can be created over existing data.
BACKFILL_ACTION = """
UPDATE approvals AS a
SET action_id = b.action_id
FROM (
    SELECT
        id,
        payload ->> 'action_id' AS action_id,
        row_number() OVER (
            PARTITION BY payload ->> 'action_id' ORDER BY created_at DESC, id DESC
        ) AS rank
    FROM approvals
    WHERE kind = 'send' AND coalesce(payload ->> 'action_id', '') <> ''
) AS b
WHERE a.id = b.id AND b.rank = 1
"""


def upgrade() -> None:
    op.add_column(
        "approvals", sa.Column("action_id", sa.Text(), nullable=False, server_default="")
    )
    op.add_column(
        "approvals",
        sa.Column("origin_gateway", sa.String(40), nullable=False, server_default=""),
    )
    op.add_column(
        "approvals",
        sa.Column("origin_thread", sa.String(120), nullable=False, server_default=""),
    )
    op.add_column(
        "approvals",
        sa.Column("batch_id", sa.String(80), nullable=False, server_default=""),
    )
    op.execute(BACKFILL_ORIGIN)
    op.execute(BACKFILL_ACTION)
    op.create_index(
        "ix_approvals_action_id",
        "approvals",
        ["action_id"],
        unique=True,
        postgresql_where=sa.text("action_id <> ''"),
    )
    op.create_index("ix_approvals_batch", "approvals", ["batch_id"])


def downgrade() -> None:
    op.drop_index("ix_approvals_batch", table_name="approvals")
    op.drop_index("ix_approvals_action_id", table_name="approvals")
    op.drop_column("approvals", "batch_id")
    op.drop_column("approvals", "origin_thread")
    op.drop_column("approvals", "origin_gateway")
    op.drop_column("approvals", "action_id")
