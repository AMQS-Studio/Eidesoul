"""lease fencing: engine_lease.generation and the single-row CHECK (ADR-0002).

Revision 2 raises the fencing token on every acquisition and pins the lease table to one
row. ADR-0002 §4.5 owns the CHECK; ADR-0002 §5 owns the column and its default.
"""


import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "engine_lease",
        sa.Column("generation", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.create_check_constraint("ck_engine_lease_single", "engine_lease", "id = 1")


def downgrade() -> None:
    op.drop_constraint("ck_engine_lease_single", "engine_lease", type_="check")
    op.drop_column("engine_lease", "generation")
