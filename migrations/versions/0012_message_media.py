"""Separate media bytes from `messages` (ADR-0016 §4.5).

`messages` keeps the metadata (`data.media`/`data.quote.media`) and the bytes move to
`message_media`, keyed by the message identity so a delete cascades. Nothing was deployed, but the
revision still copies existing rows so an upgrade in place keeps the archive readable.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "message_media",
        sa.Column("thread_id", sa.String(120), nullable=False),
        sa.Column("message_id", sa.String(160), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("mime", sa.String(160), nullable=False, server_default=""),
        sa.Column("bytes_ref", sa.LargeBinary(), nullable=True),
        sa.PrimaryKeyConstraint("thread_id", "message_id", "kind", name="pk_message_media"),
        sa.ForeignKeyConstraint(
            ["thread_id", "message_id"],
            ["messages.thread_id", "messages.msg_id"],
            ondelete="CASCADE",
            name="fk_message_media_message",
        ),
    )

    for kind, column, meta in (
        ("media", "media_ref", "media"),
        ("quote", "quote_media_ref", "quote"),
    ):
        op.execute(
            sa.text(
                f"""
                INSERT INTO message_media (thread_id, message_id, kind, mime, bytes_ref)
                SELECT thread_id, msg_id, :kind,
                       coalesce(data -> '{meta}' ->> 'mimetype', ''), {column}
                  FROM messages
                 WHERE {column} IS NOT NULL
                """
            ).bindparams(kind=kind)
        )

    op.drop_column("messages", "media_ref")
    op.drop_column("messages", "quote_media_ref")


def downgrade() -> None:
    op.add_column("messages", sa.Column("media_ref", sa.LargeBinary(), nullable=True))
    op.add_column("messages", sa.Column("quote_media_ref", sa.LargeBinary(), nullable=True))
    op.execute(
        """
        UPDATE messages AS m
           SET media_ref = b.bytes_ref
          FROM message_media AS b
         WHERE b.thread_id = m.thread_id AND b.message_id = m.msg_id AND b.kind = 'media'
        """
    )
    op.execute(
        """
        UPDATE messages AS m
           SET quote_media_ref = b.bytes_ref
          FROM message_media AS b
         WHERE b.thread_id = m.thread_id AND b.message_id = m.msg_id AND b.kind = 'quote'
        """
    )
    op.drop_table("message_media")
