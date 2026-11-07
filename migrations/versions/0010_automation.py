"""automation: `automations`, `automation_runs`, `automation_state_log` (ADR-0001 §4.1/§4.10/§4.13).

Revision 10 lands one declarative row per promise (`event`, `condition`, `steps`, `owner`,
`policy`, `state`, `next_occurrence`) and one row per occurrence (`automation_runs`). The
occurrence key is `hash(automation_id, scheduled_at_utc)` and is unique, so a duplicate tick or
a takeover cannot double a run. `automation_state_log` records transitions only (ADR-0002 §4.9),
never a run; `automation_runs` is the run record and never holds another room's content.

`quiet_hours`, `budget_per_day`, the kill switch, and the catch-up window live in the config
block (ADR-0001 §5); the run row keeps the decision and its reason so a run can be audited.
"""


import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "automations",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("owner", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "event",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "condition",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{\"all\":[]}'::jsonb"),
        ),
        sa.Column(
            "steps",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "policy",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("state", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "owner_roles_at_creation",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("approval_id", sa.BigInteger(), nullable=True),
        sa.Column("next_occurrence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("fire_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ends_at", sa.Float(), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.CheckConstraint(
            "state IN ('draft','active','paused','stopped','expired')",
            name="ck_automations_state",
        ),
    )
    op.create_index(
        "ix_automations_due", "automations", ["state", "next_occurrence"]
    )
    op.create_index(
        "ix_automations_owner", "automations", ["owner", "state"]
    )
    op.create_table(
        "automation_runs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("automation_id", sa.BigInteger(), nullable=False),
        sa.Column("occurrence_key", sa.Text(), nullable=False),
        sa.Column("scheduled_at", sa.Float(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.Float(), nullable=False, server_default="0"),
        sa.Column("claimed_by", sa.Text(), nullable=False, server_default=""),
        sa.Column("claimed_at", sa.Float(), nullable=True),
        sa.Column("lease_until", sa.Float(), nullable=False, server_default="0"),
        sa.Column("generation", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("step_index", sa.Integer(), nullable=False, server_default="-1"),
        sa.Column("deferred", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("detail", sa.Text(), nullable=False, server_default=""),
        sa.Column("apology_sent_at", sa.Float(), nullable=True),
        sa.Column("started_at", sa.Float(), nullable=True),
        sa.Column("finished_at", sa.Float(), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued','claimed','running','succeeded','failed','skipped',"
            "'missed','denied','interrupted','cancelled')",
            name="ck_automation_runs_status",
        ),
        sa.UniqueConstraint("occurrence_key", name="uq_automation_runs_occurrence"),
    )
    op.create_index(
        "ix_automation_runs_claim",
        "automation_runs",
        ["status", "scheduled_at"],
    )
    op.create_index(
        "ix_automation_runs_automation",
        "automation_runs",
        ["automation_id", "scheduled_at"],
    )
    op.create_table(
        "automation_state_log",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("automation_id", sa.BigInteger(), nullable=False),
        sa.Column("from_state", sa.Text(), nullable=False),
        sa.Column("to_state", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("actor", sa.Text(), nullable=False, server_default=""),
        sa.Column("at", sa.Float(), nullable=False),
    )
    op.create_index(
        "ix_automation_state_log",
        "automation_state_log",
        ["automation_id", sa.text("at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_automation_state_log", table_name="automation_state_log")
    op.drop_table("automation_state_log")
    op.drop_index("ix_automation_runs_automation", table_name="automation_runs")
    op.drop_index("ix_automation_runs_claim", table_name="automation_runs")
    op.drop_table("automation_runs")
    op.drop_index("ix_automations_owner", table_name="automations")
    op.drop_index("ix_automations_due", table_name="automations")
    op.drop_table("automations")
