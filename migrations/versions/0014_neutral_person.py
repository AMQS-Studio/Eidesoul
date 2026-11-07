"""drop the person gender columns

Revision ID: 0014_neutral_person
Revises: 0013_fences_events_efficiency
Create Date: 2026-11-04

ADR-0001 §4.8: a person's gender is never inferred, stored, or gated. The soul's own
`gender` lives in the soul document and is untouched.
"""

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

COLUMNS = ("gender", "gender_confidence", "gender_source")


def upgrade() -> None:
    for column in COLUMNS:
        op.drop_column("people", column)


def downgrade() -> None:
    import sqlalchemy as sa

    op.add_column("people", sa.Column("gender", sa.String(12), nullable=False, server_default=""))
    op.add_column(
        "people",
        sa.Column("gender_confidence", sa.Float(), nullable=False, server_default="0"),
    )
    op.add_column(
        "people", sa.Column("gender_source", sa.String(16), nullable=False, server_default="")
    )
