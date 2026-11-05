"""drop the legacy people.role column

Revision ID: 0023
Revises: 0022
Create Date: 2026-11-05

ADR-0006 §4.2: `roles.yaml` is the only RBAC truth. The `people.role` column was a second,
derived answer that could disagree with the registry after a role edit, so it is removed
instead of kept in sync. A person's persona mode lives in `soul.yaml`; the affect stage lives
in `people.stage`.
"""

import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("people", "role")


def downgrade() -> None:
    op.add_column(
        "people",
        sa.Column("role", sa.String(40), nullable=False, server_default=""),
    )
