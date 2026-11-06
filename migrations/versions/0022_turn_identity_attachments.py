"""turns carry a namespaced identity, step rows a running/interrupted state, attachments a handle

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-05

ADR-0001 §4.3 / ADR-0003 §4.3 / ADR-0004 §4.1:

* `turns.id` is the namespaced identity `<gateway>:<thread>:<message_id>`; `message_id` keeps the
  raw transport id the archive and the resume path need, backfilled from the old id.
* `turn_steps.status` gains `running` (written before the tool runs) and `interrupted` (the step a
  crash left unfinished), so a resumed turn never re-executes a side-effecting tool.
* `message_media` gains `seq`, which becomes part of the primary key: one message may carry
  several attachments. Existing rows are numbered per message before the key changes.
* `attachments` is the durable handle (`att_…`) of one attachment, so a file that arrived through
  one body can be forwarded by another without reading the archive.
"""

import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


BACKFILL_MESSAGE_ID = "UPDATE turns SET message_id = id WHERE message_id = ''"

# `ctid` is stable inside this one statement; the ordering only has to be deterministic, and the
# old key made (thread, message, kind) unique.
NUMBER_MEDIA = """
UPDATE message_media AS m
SET seq = ranked.rn - 1
FROM (
    SELECT ctid, row_number() OVER (PARTITION BY thread_id, message_id ORDER BY kind) AS rn
    FROM message_media
) AS ranked
WHERE m.ctid = ranked.ctid
"""

COLLAPSE_MEDIA = """
DELETE FROM message_media AS m
USING message_media AS keep
WHERE m.thread_id = keep.thread_id
  AND m.message_id = keep.message_id
  AND m.kind = keep.kind
  AND m.seq > keep.seq
"""


def upgrade() -> None:
    op.add_column(
        "turns", sa.Column("message_id", sa.String(160), nullable=False, server_default="")
    )
    op.execute(BACKFILL_MESSAGE_ID)

    op.drop_constraint("ck_turn_steps_status", "turn_steps", type_="check")
    op.create_check_constraint(
        "ck_turn_steps_status",
        "turn_steps",
        "status IN ('running','executed','failed','interrupted')",
    )

    op.add_column(
        "message_media", sa.Column("seq", sa.Integer(), nullable=False, server_default="0")
    )
    op.execute(NUMBER_MEDIA)
    op.drop_constraint("pk_message_media", "message_media", type_="primary")
    op.create_primary_key(
        "pk_message_media", "message_media", ["thread_id", "message_id", "seq"]
    )

    op.create_table(
        "attachments",
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column("gateway", sa.String(40), nullable=False, server_default=""),
        sa.Column("thread_id", sa.String(120), nullable=False, server_default=""),
        sa.Column("message_id", sa.String(160), nullable=False, server_default=""),
        sa.Column("seq", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("kind", sa.String(20), nullable=False, server_default=""),
        sa.Column("mime", sa.String(160), nullable=False, server_default=""),
        sa.Column("filename", sa.String(240), nullable=False, server_default=""),
        sa.Column("path", sa.Text(), nullable=False, server_default=""),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("sha256", sa.String(64), nullable=False, server_default=""),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_attachments"),
        sa.UniqueConstraint(
            "thread_id", "message_id", "seq", name="uq_attachments_message_seq"
        ),
    )
    op.create_index("ix_attachments_message", "attachments", ["thread_id", "message_id"])


def downgrade() -> None:
    op.drop_index("ix_attachments_message", table_name="attachments")
    op.drop_table("attachments")

    op.execute(COLLAPSE_MEDIA)
    op.drop_constraint("pk_message_media", "message_media", type_="primary")
    op.create_primary_key(
        "pk_message_media", "message_media", ["thread_id", "message_id", "kind"]
    )
    op.drop_column("message_media", "seq")

    # The old constraint only admits executed/failed. A turn that crashed while a step was
    # running, or that was interrupted, still holds a row the narrower check rejects; settle
    # those rows first so the downgrade never fails on live data.
    op.execute("UPDATE turn_steps SET status = 'failed' WHERE status IN ('running','interrupted')")
    op.drop_constraint("ck_turn_steps_status", "turn_steps", type_="check")
    op.create_check_constraint(
        "ck_turn_steps_status", "turn_steps", "status IN ('executed','failed')"
    )

    op.drop_column("turns", "message_id")
