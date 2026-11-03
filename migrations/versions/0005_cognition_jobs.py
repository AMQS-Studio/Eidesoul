"""cognition queue, episodes, and derived procedures (ADR-0003, ADR-0020).

Revision 5 lands the batch mechanism: `cognition_jobs` (ADR-0003 §5.4, the canonical name;
`memory_outbox` is not a second table), `episodes` (the recorded closure result of ADR-0020
§4.2), and the procedure columns plus `skill_revisions` of ADR-0016 §4.7.

The capture gate (ADR-0002), reflection (ADR-0010), and the worker are code, not DDL; the
dispute columns they write already exist in revision 4.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=False)
EMPTY_REFS = sa.text("'[]'::jsonb")


def upgrade() -> None:
    op.create_table(
        "cognition_jobs",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("scope", sa.String(160), nullable=False, server_default="global"),
        sa.Column("payload_ref", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("claimed_by", sa.Text(), nullable=False, server_default=""),
        sa.Column("lease_until", sa.Float(), nullable=False, server_default="0"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.Float(), nullable=False, server_default="0"),
        sa.Column("result_ref", sa.Text(), nullable=False, server_default=""),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('capture','consolidate','reflect','backfill','audit','distill')",
            name="ck_cognition_jobs_kind",
        ),
        sa.CheckConstraint(
            "status IN ('queued','claimed','done','failed','dead')",
            name="ck_cognition_jobs_status",
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_cognition_jobs_key"),
    )
    op.create_index(
        "ix_cognition_jobs_claim", "cognition_jobs", ["status", "next_attempt_at"]
    )
    op.create_index("ix_cognition_jobs_kind", "cognition_jobs", ["kind", "status"])
    op.create_index("ix_cognition_jobs_scope", "cognition_jobs", ["scope"])

    op.create_table(
        "episodes",
        sa.Column("id", sa.String(160), primary_key=True),
        sa.Column("thread_id", sa.String(120), nullable=False, server_default=""),
        sa.Column("person_id", sa.String(80), nullable=False, server_default=""),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("close_signal", sa.String(20), nullable=False, server_default=""),
        sa.Column("signature", sa.Text(), nullable=False, server_default=""),
        sa.Column("turns", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tool_calls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("refs", postgresql.JSONB(), nullable=False, server_default=EMPTY_REFS),
        sa.Column("distilled_at", sa.Float(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.Float(), nullable=False),
        sa.Column("closed_at", sa.Float(), nullable=True),
        sa.CheckConstraint("status IN ('open','closed')", name="ck_episodes_status"),
    )
    op.create_index("ix_episodes_thread", "episodes", ["thread_id", "status"])
    op.create_index("ix_episodes_closed", "episodes", ["status", "closed_at"])

    op.add_column(
        "skills",
        sa.Column("scope", sa.String(20), nullable=False, server_default="thread"),
    )
    op.add_column(
        "skills",
        sa.Column("via", sa.String(20), nullable=False, server_default="reflection"),
    )
    op.add_column("skills", sa.Column("stated_by", sa.String(80), nullable=True))
    op.add_column("skills", sa.Column("source_episode", sa.String(160), nullable=True))
    op.add_column(
        "skills", sa.Column("uses", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column("skills", sa.Column("last_used", sa.Float(), nullable=True))
    op.add_column(
        "skills", sa.Column("revision", sa.Integer(), nullable=False, server_default="1")
    )
    op.add_column(
        "skills", sa.Column("trust", sa.String(8), nullable=False, server_default="T2")
    )
    op.create_check_constraint(
        "ck_skills_scope", "skills", "scope IN ('thread','person','global')"
    )
    op.create_check_constraint("ck_skills_trust", "skills", "trust IN ('T0','T1','T2','T3')")
    op.create_index("ix_skills_scope", "skills", ["scope"])

    op.create_table(
        "skill_revisions",
        sa.Column(
            "skill",
            sa.String(80),
            sa.ForeignKey("skills.name", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("description", sa.String(400), nullable=False, server_default=""),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("source_episode", sa.String(160), nullable=True),
        sa.PrimaryKeyConstraint("skill", "revision", name="pk_skill_revisions"),
    )
    op.create_index("ix_skill_revisions_skill", "skill_revisions", ["skill", "revision"])

    # ADR-0020 §4.5 moved `uses`/`last_used` onto the `skills` row; the old counter table has
    # no reader left. ADR-0016 §4.3 rule 3: this is a pre-release tree (0.0.0.devN), so the drop
    # needs no release pause; `downgrade` recreates it for a dev rollback.
    op.drop_table("skill_usage")


def downgrade() -> None:
    op.create_table(
        "skill_usage",
        sa.Column("skill", sa.String(80), primary_key=True),
        sa.Column("uses", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last", sa.Float(), nullable=False, server_default="0"),
    )
    op.drop_index("ix_skill_revisions_skill", table_name="skill_revisions")
    op.drop_table("skill_revisions")
    op.drop_index("ix_skills_scope", table_name="skills")
    op.drop_constraint("ck_skills_trust", "skills", type_="check")
    op.drop_constraint("ck_skills_scope", "skills", type_="check")
    for column in (
        "trust",
        "revision",
        "last_used",
        "uses",
        "source_episode",
        "stated_by",
        "via",
        "scope",
    ):
        op.drop_column("skills", column)
    op.drop_index("ix_episodes_closed", table_name="episodes")
    op.drop_index("ix_episodes_thread", table_name="episodes")
    op.drop_table("episodes")
    op.drop_index("ix_cognition_jobs_scope", table_name="cognition_jobs")
    op.drop_index("ix_cognition_jobs_kind", table_name="cognition_jobs")
    op.drop_index("ix_cognition_jobs_claim", table_name="cognition_jobs")
    op.drop_table("cognition_jobs")
