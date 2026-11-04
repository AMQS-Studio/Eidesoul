"""scope skills to an application namespace

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-04

ADR-0035: app is provenance above audience. A turn records the body's `app` hint, an episode
copies it from its first turn, and a skill carries the corpus it belongs to. The empty string
means shared, so existing rows backfill without a table rewrite.
"""

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("turns", "episodes", "skills"):
        op.add_column(
            table,
            sa.Column("app", sa.String(40), nullable=False, server_default=""),
        )
    op.create_index("ix_skills_app_scope", "skills", ["app", "scope"])


def downgrade() -> None:
    op.drop_index("ix_skills_app_scope", table_name="skills")
    for table in ("skills", "episodes", "turns"):
        op.drop_column(table, "app")
