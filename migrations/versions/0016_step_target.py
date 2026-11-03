"""record the declared target on each tool step

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-04

ADR-0020 §4.6 / ADR-0033 §4.7: the episode signature reads the target the handler declared at
execution time, so a gateway or plugin tool contributes its own target without a core table.
"""

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "turn_steps",
        sa.Column("target", sa.String(16), nullable=False, server_default=""),
    )
    op.add_column(
        "turn_steps",
        sa.Column(
            "approval_class", sa.String(16), nullable=False, server_default="none"
        ),
    )


def downgrade() -> None:
    op.drop_column("turn_steps", "approval_class")
    op.drop_column("turn_steps", "target")
