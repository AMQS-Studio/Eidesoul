"""P0-3 foreign keys, checks, and indexes (ADR-0002 §4.5).

Nothing was deployed when these landed, but the chain still treats them as a normal revision so a
database created from revision 1 can be upgraded in place. `message_media` lands separately in
revision 12 because it moves bytes, not just declarations.
"""


from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_check_constraint(
        "ck_messages_visibility", "messages", "visibility >= 0 AND visibility <= 4"
    )

    op.create_foreign_key(
        "fk_thread_eras_thread",
        "thread_eras",
        "threads",
        ["thread_id"],
        ["thread_id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_thread_people_person",
        "thread_people",
        "people",
        ["person_id"],
        ["person_id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_person_links_person",
        "person_links",
        "people",
        ["person_id"],
        ["person_id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_skill_revisions_skill",
        "skill_revisions",
        "skills",
        ["skill"],
        ["name"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_skill_files_skill",
        "skill_files",
        "skills",
        ["skill"],
        ["name"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_google_identity_person",
        "google_identity",
        "people",
        ["person_id"],
        ["person_id"],
        ondelete="CASCADE",
    )

    op.create_index("ix_messages_ts", "messages", ["ts"])
    op.create_index(
        "ix_approvals_thread_sender_status_expires",
        "approvals",
        ["thread_id", "sender", "status", "expires_at"],
    )
    op.create_index(
        "ix_approvals_status_expires", "approvals", ["status", "expires_at"]
    )
    op.create_index("ix_thread_people_person", "thread_people", ["person_id"])


def downgrade() -> None:
    op.drop_index("ix_thread_people_person", table_name="thread_people")
    op.drop_index("ix_approvals_status_expires", table_name="approvals")
    op.drop_index("ix_approvals_thread_sender_status_expires", table_name="approvals")
    op.drop_index("ix_messages_ts", table_name="messages")

    for name, table in (
        ("fk_google_identity_person", "google_identity"),
        ("fk_skill_files_skill", "skill_files"),
        ("fk_skill_revisions_skill", "skill_revisions"),
        ("fk_person_links_person", "person_links"),
        ("fk_thread_people_person", "thread_people"),
        ("fk_thread_eras_thread", "thread_eras"),
    ):
        op.drop_constraint(name, table, type_="foreignkey")

    op.drop_constraint("ck_messages_visibility", "messages", type_="check")

