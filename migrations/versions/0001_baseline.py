"""baseline: the schema.py state at ADR-0002 revision 1 (29 tables).

Nothing is deployed at 0.0.0.devN, so this baseline is the frozen shape of
`monoagens/engine/schema.py` when the migration scheme landed. The rename of
ADR-0000 §4.4 rewrites this baseline rather than adding a revision.
"""


import baseline
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

SCHEMA_VERSION = "1"


def upgrade() -> None:
    bind = op.get_bind()
    baseline.metadata.create_all(bind)
    present = bind.execute(
        baseline.schema_meta.select().where(baseline.schema_meta.c.key == "version")
    ).first()
    if present is None:
        bind.execute(
            baseline.schema_meta.insert().values(key="version", value=SCHEMA_VERSION)
        )
    control = bind.execute(
        baseline.engine_control.select().where(baseline.engine_control.c.id == 1)
    ).first()
    if control is None:
        bind.execute(
            baseline.engine_control.insert().values(
                id=1, frozen=False, frozen_by="", frozen_at=0.0
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    baseline.metadata.drop_all(bind)
