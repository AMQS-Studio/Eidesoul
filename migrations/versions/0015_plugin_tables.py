"""drop the core Google tables

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-04

ADR-0033 §4.6: a plugin owns its tables. The Google plugin creates these itself on first use;
nothing in the engine defines or reads them.
"""

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("google_device")
    op.drop_table("google_identity")


def downgrade() -> None:
    op.create_table(
        "google_identity",
        sa.Column("person_id", sa.String(160), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("subject", sa.String(128), nullable=False, server_default=""),
        sa.Column("linked_at", sa.Float(), nullable=False),
    )
    # 0011 added this foreign key once `people` existed; dropping the whole table in `upgrade`
    # dropped it too, so a downgrade past 0015 must restore it or 0011's own downgrade fails.
    op.create_foreign_key(
        "fk_google_identity_person",
        "google_identity",
        "people",
        ["person_id"],
        ["person_id"],
        ondelete="CASCADE",
    )
    op.create_table(
        "google_device",
        sa.Column("person_id", sa.String(160), primary_key=True),
        sa.Column("device_code", sa.Text(), nullable=False),
        sa.Column("user_code", sa.String(32), nullable=False),
        sa.Column("verification_url", sa.String(300), nullable=False),
        sa.Column("expires_at", sa.Float(), nullable=False),
        sa.Column("interval", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.Float(), nullable=False),
    )
