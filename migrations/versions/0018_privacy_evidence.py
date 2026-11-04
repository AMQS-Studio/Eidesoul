"""persist privacy evidence and the policy version on a memory

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-04

ADR-0038 §4.2/§4.6: the evidence array and the policy version are part of the decision record, so
a reviewer can reconstruct a level without calling a model again. The readable view is replaced
because it was created with `SELECT *` and must expose the appended columns.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "memories",
        sa.Column(
            "privacy_evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "memories",
        sa.Column(
            "privacy_policy", sa.String(40), nullable=False, server_default=""
        ),
    )
    op.execute(
        "CREATE OR REPLACE VIEW memories_readable AS "
        "SELECT * FROM memories WHERE state = 'ok' AND vetoed = false"
    )


def downgrade() -> None:
    op.execute(
        "CREATE OR REPLACE VIEW memories_readable AS "
        "SELECT * FROM memories WHERE state = 'ok' AND vetoed = false"
    )
    op.drop_column("memories", "privacy_policy")
    op.drop_column("memories", "privacy_evidence")
