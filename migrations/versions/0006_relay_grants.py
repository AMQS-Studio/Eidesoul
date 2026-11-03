"""relay consent: one grant per (task, from, to) before another person's answer is forwarded.

Revision 6 lands `relay_grants` exactly as ADR-0023 §4.1 / ADR-0016 §4.9: the row records the
decision (`full|summary|anonymous|none`), its scope, expiry, revocation, and how it was decided.
It never holds message content. `mode='none'` is a stored decision, not a missing row, so a
refusal stays auditable; the unique index keeps one grant per task and pair of people.

The consent question itself is an `approvals` row of kind `consent` (ADR-0007 §4.7) and needs no
DDL here.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "relay_grants",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("task_id", sa.String(120), nullable=False),
        sa.Column("from_person", sa.String(80), nullable=False),
        sa.Column("to_person", sa.String(80), nullable=False),
        sa.Column("mode", sa.String(20), nullable=False, server_default="none"),
        sa.Column("scope_ref", sa.String(160), nullable=False, server_default=""),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("expires_at", sa.Float(), nullable=True),
        sa.Column("revoked_at", sa.Float(), nullable=True),
        sa.Column("decided_via", sa.String(40), nullable=False, server_default=""),
        sa.CheckConstraint(
            "mode IN ('full','summary','anonymous','none')",
            name="ck_relay_grants_mode",
        ),
    )
    op.create_index(
        "ux_relay_grant",
        "relay_grants",
        ["task_id", "from_person", "to_person"],
        unique=True,
    )
    op.create_index("ix_relay_grants_expiry", "relay_grants", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_relay_grants_expiry", table_name="relay_grants")
    op.drop_index("ux_relay_grant", table_name="relay_grants")
    op.drop_table("relay_grants")
