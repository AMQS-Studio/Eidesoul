"""search: `messages.text_tsv`, its GIN index, and the trigram net (ADR-0002 §4.1-§4.6).

Revision 9 replaces `ILIKE` over the whole table with a generated `tsvector` and `pg_trgm` as the
second net. The column is derived by Postgres, so no application write path changes and a row
can never disagree with its index. `pg_trgm` is required: when the extension cannot be created,
boot fails with a clear message instead of silently degrading to `LIKE` (ADR-0002 §4.6).

The trigram index on `people.name` is the same net for a person's display name; the ADR calls the
column `display_name`, the canonical schema names it `name` (ADR-0002 §4.1).
"""


import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

TSV_EXPRESSION = (
    "to_tsvector('simple', coalesce(text, '') || ' ' || coalesce(sender_name, ''))"
)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.add_column(
        "messages",
        sa.Column(
            "text_tsv",
            postgresql.TSVECTOR(),
            sa.Computed(TSV_EXPRESSION, persisted=True),
        ),
    )
    op.create_index(
        "ix_messages_text_tsv", "messages", ["text_tsv"], postgresql_using="gin"
    )
    op.create_index(
        "ix_messages_text_trgm",
        "messages",
        ["text"],
        postgresql_using="gin",
        postgresql_ops={"text": "gin_trgm_ops"},
    )
    op.create_index(
        "ix_people_name_trgm",
        "people",
        ["name"],
        postgresql_using="gin",
        postgresql_ops={"name": "gin_trgm_ops"},
    )


def downgrade() -> None:
    op.drop_index("ix_people_name_trgm", table_name="people")
    op.drop_index("ix_messages_text_trgm", table_name="messages")
    op.drop_index("ix_messages_text_tsv", table_name="messages")
    op.drop_column("messages", "text_tsv")
