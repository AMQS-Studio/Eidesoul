"""persist percept hints on a turn

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-05

ADR-0001 §4.1/ADR-0003 §4.1: a resumed turn must reconstruct the body's `Percept` with the
original `dm`, `mention`, `reply`, and `app` hints, so they ride on the turn row. The
`(thread, person)` index the lookup needs already ships with revision 0011.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "turns",
        sa.Column(
            "hints",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("turns", "hints")
