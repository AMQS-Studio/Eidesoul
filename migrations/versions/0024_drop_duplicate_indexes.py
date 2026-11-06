"""drop indexes the table constraints already cover

Revision ID: 0024
Revises: 0023
Create Date: 2026-11-07

Each dropped index repeated the leading columns of a unique or primary-key index on the same
table, so the planner could never prefer it: pure write cost. Dropping them keeps the
constraint indexes that already answer the same lookups.
"""

from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("ix_attachments_message", table_name="attachments")
    op.drop_index("ix_memory_index_external", table_name="memory_index")
    op.drop_index("ix_skill_revisions_skill", table_name="skill_revisions")


def downgrade() -> None:
    op.create_index("ix_attachments_message", "attachments", ["thread_id", "message_id"])
    op.create_index("ix_memory_index_external", "memory_index", ["backend", "external_id"])
    op.create_index("ix_skill_revisions_skill", "skill_revisions", ["skill", "revision"])
