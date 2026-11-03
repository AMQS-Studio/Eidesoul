"""approval class: `approvals.approval_class` and the `scope_proof` snapshot (ADR-0007 §4.7).

Revision 7 lands ADR-0016 §4.8 exactly: the class is derived from the action target
(`consent` inside the requester's scope, `authorization` outside it), and `scope_proof` keeps the
deterministic target list + role-root snapshot computed when the approval was requested so
execution can re-check it. Neither column grants a permission: `/approve` still recomputes the
sender's role and the tool grant.

The default `authorization` is the safe value for every row that predates this revision: a
consent approval must always be proven, never inherited.
"""


import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "approvals",
        sa.Column(
            "approval_class",
            sa.String(20),
            nullable=False,
            server_default="authorization",
        ),
    )
    op.add_column(
        "approvals",
        sa.Column(
            "scope_proof",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.create_check_constraint(
        "ck_approvals_class",
        "approvals",
        "approval_class IN ('consent','authorization')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_approvals_class", "approvals", type_="check")
    op.drop_column("approvals", "scope_proof")
    op.drop_column("approvals", "approval_class")
