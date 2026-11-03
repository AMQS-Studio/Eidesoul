"""turn lifecycle: turns, turn_steps, outbound_messages, and the shutting_down flag.

Revision 3 lands ADR-0013 §6 (with `thread_id`, the rename from ADR-0000 §4.4), the
delivery ledger of ADR-0011 §4.3, and `engine_control.shutting_down` used by the neat
shutdown of ADR-0011 §4.4. `turn_steps` is the canonical step log (ADR-0009/0011/0016)
carrying the columns ADR-0013 assigns to `turn_actions`.
"""


import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "engine_control",
        sa.Column("shutting_down", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.create_table(
        "turns",
        sa.Column("id", sa.String(160), primary_key=True),
        sa.Column("thread_id", sa.String(120), nullable=False),
        sa.Column("person_id", sa.String(80), nullable=False, server_default=""),
        sa.Column("status", sa.String(20), nullable=False, server_default="received"),
        sa.Column("step", sa.String(20), nullable=False, server_default="received"),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_owner", sa.String(80), nullable=False, server_default=""),
        sa.Column("lease_until", sa.Float(), nullable=False, server_default="0"),
        sa.Column("generation", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("decision_json", postgresql.JSONB(), nullable=True),
        sa.Column("draft_text", sa.Text(), nullable=True),
        sa.Column("reviewed_text", sa.Text(), nullable=True),
        sa.Column("planned_chunks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("delivered_chunks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("shutdown_epoch", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("boot_epoch", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.CheckConstraint(
            "status IN ('received','running','sending','delivered','interrupted','expired')",
            name="ck_turns_status",
        ),
        sa.CheckConstraint(
            "step IN ('received','decided','drafted','reviewed','sending','delivered')",
            name="ck_turns_step",
        ),
    )
    op.create_index("ix_turns_status_lease", "turns", ["status", "lease_until"])
    op.create_index("ix_turns_thread_created", "turns", ["thread_id", "created_at"])

    op.create_table(
        "turn_steps",
        sa.Column("turn_id", sa.String(160), nullable=False),
        sa.Column("step_index", sa.Integer(), nullable=False),
        sa.Column("step", sa.String(20), nullable=False, server_default="drafted"),
        sa.Column("tool", sa.Text(), nullable=False),
        sa.Column("args_hash", sa.String(64), nullable=False),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="executed"),
        sa.Column("started_at", sa.Float(), nullable=False, server_default="0"),
        sa.Column("finished_at", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("turn_id", "step_index", name="pk_turn_steps"),
        sa.CheckConstraint("status IN ('executed','failed')", name="ck_turn_steps_status"),
    )

    op.create_table(
        "outbound_messages",
        sa.Column("scope_key", sa.Text(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("gateway", sa.String(40), nullable=False, server_default=""),
        sa.Column("thread_id", sa.String(120), nullable=False, server_default=""),
        sa.Column("provider_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(20), nullable=False, server_default="intent"),
        sa.Column("generation", sa.BigInteger(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("scope_key", "chunk_index", name="pk_outbound_messages"),
        sa.CheckConstraint(
            "status IN ('intent','sent','unknown')", name="ck_outbound_messages_status"
        ),
    )


def downgrade() -> None:
    op.drop_table("outbound_messages")
    op.drop_table("turn_steps")
    op.drop_index("ix_turns_thread_created", table_name="turns")
    op.drop_index("ix_turns_status_lease", table_name="turns")
    op.drop_table("turns")
    op.drop_column("engine_control", "shutting_down")
