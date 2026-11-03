"""turn cost: the `llm_calls` metrics table (ADR-0025 §4.5, ADR-0016 §4.6, ADR-0019).

Revision 8 lands the one place tokens are recorded. One row per attempt, including a retry and a
failed call: `ok=false` is data, not a reason to hide the cost. No FK to `turns`,
`cognition_jobs`, or `automation_runs` — the metrics table must never hold up entity deletion,
and a row without a turn id (a failure before intake) is still useful. Prompt or response text
never enters this table; retention is `engine.metrics.retention_days`.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "llm_calls",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("ts", sa.Float(), nullable=False),
        sa.Column("turn_id", sa.String(120), nullable=True),
        sa.Column("job_id", sa.BigInteger(), nullable=True),
        sa.Column("run_id", sa.String(120), nullable=True),
        sa.Column("purpose", sa.String(40), nullable=False),
        sa.Column("model", sa.String(120), nullable=False),
        sa.Column("tokens_in", sa.Integer(), nullable=True),
        sa.Column("tokens_out", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_llm_calls_ts", "llm_calls", ["ts"])
    op.create_index("ix_llm_calls_turn", "llm_calls", ["turn_id"])


def downgrade() -> None:
    op.drop_index("ix_llm_calls_turn", table_name="llm_calls")
    op.drop_index("ix_llm_calls_ts", table_name="llm_calls")
    op.drop_table("llm_calls")
