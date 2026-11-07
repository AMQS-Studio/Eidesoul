"""native hybrid memory retrieval: embedding, search_tsv, and the readable view

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-05

ADR-0007 §4.1/§4.2: `memories` carries its own retrieval index. The embedding is the shared
384-dimension MiniLM vector of `text`; `search_tsv` is derived by Postgres so a row can never
disagree with its lexical index. The readable view is recreated last: an existing `SELECT *`
view pins the column list, so the new columns only reach it through a fresh definition.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.types import UserDefinedType

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

TSV_EXPRESSION = "to_tsvector('simple', coalesce(text, ''))"
VIEW_SQL = (
    "CREATE VIEW memories_readable AS SELECT * FROM memories "
    "WHERE state = 'ok' AND vetoed = false AND level IS NOT NULL"
)
OLD_VIEW_SQL = (
    "CREATE VIEW memories_readable AS SELECT * FROM memories "
    "WHERE state = 'ok' AND vetoed = false"
)


class Vector(UserDefinedType):
    """The pgvector type as DDL only; the application owns its bind/result codec."""

    cache_ok = True

    def get_col_spec(self, **kw) -> str:
        return "vector(384)"


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.add_column("memories", sa.Column("embedding", Vector(), nullable=True))
    op.execute(
        "CREATE INDEX ix_memories_embedding_hnsw ON memories "
        "USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)"
    )
    op.add_column(
        "memories",
        sa.Column(
            "search_tsv",
            postgresql.TSVECTOR(),
            sa.Computed(TSV_EXPRESSION, persisted=True),
        ),
    )
    op.create_index(
        "ix_memories_search_tsv", "memories", ["search_tsv"], postgresql_using="gin"
    )
    op.execute("DROP VIEW IF EXISTS memories_readable")
    op.execute(VIEW_SQL)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS memories_readable")
    op.drop_index("ix_memories_search_tsv", table_name="memories")
    op.drop_column("memories", "search_tsv")
    op.drop_index("ix_memories_embedding_hnsw", table_name="memories")
    op.drop_column("memories", "embedding")
    op.execute(OLD_VIEW_SQL)
