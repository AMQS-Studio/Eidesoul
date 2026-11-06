"""People, rooms, skills, recipes, and engine state tables."""


from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    PrimaryKeyConstraint,
    String,
    Table,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

from .base import metadata

people = Table(
    "people",
    metadata,
    Column("person_id", String(80), primary_key=True),
    Column("name", String(160), nullable=False, default=""),
    Column("aliases", JSONB, nullable=False, default=list),
    Column("stage", String(40), nullable=False, default=""),
    Column("trust", Float, nullable=False, default=0.0),
    Column("affection", Float, nullable=False, default=0.0),
    Column("respect", Float, nullable=False, default=0.0),
    Column("annoyance", Float, nullable=False, default=0.0),
    Column("familiarity", Float, nullable=False, default=0.0),
    Column("interactions", Integer, nullable=False, default=0),
    Column("appease", Float, nullable=False, default=0.0),
    Column("last_seen", Float, nullable=False, default=0.0),
    Column("window_start", Float, nullable=False, default=0.0),
    Column("window_trust", Float, nullable=False, default=0.0),
    Column("window_affection", Float, nullable=False, default=0.0),
    Column("window_respect", Float, nullable=False, default=0.0),
    Column("window_annoyance", Float, nullable=False, default=0.0),
    Column("notes", JSONB, nullable=False, default=list),
)
# ADR-0026 §4.3: the trigram net for a person's display name (the column is `name`).
Index(
    "ix_people_name_trgm",
    people.c.name,
    postgresql_using="gin",
    postgresql_ops={"name": "gin_trgm_ops"},
)

topics = Table(
    "topics",
    metadata,
    Column("thread_id", String(120), primary_key=True),
    Column("summary", Text, nullable=False, default=""),
    Column("keywords", JSONB, nullable=False, default=list),
    Column("unresolved", JSONB, nullable=False, default=list),
    Column("pending", Integer, nullable=False, default=0),
    Column("last_message", Float, nullable=False, default=0.0),
    Column("last_digest", Float, nullable=False, default=0.0),
    Column("last_research", Float, nullable=False, default=0.0),
    Column("research_day", String(20), nullable=False, default=""),
    Column("research_count", Integer, nullable=False, default=0),
)

thread_eras = Table(
    "thread_eras",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column(
        "thread_id",
        String(120),
        ForeignKey("threads.thread_id", ondelete="CASCADE", name="fk_thread_eras_thread"),
        nullable=False,
    ),
    Column("started_at", Float, nullable=False),
    Column("ended_at", Float, nullable=False),
    Column("summary", Text, nullable=False, default=""),
    Column("keywords", JSONB, nullable=False, default=list),
    Column("unresolved", JSONB, nullable=False, default=list),
)
Index("ix_thread_eras_thread", thread_eras.c.thread_id, thread_eras.c.ended_at)

drives = Table(
    "drives",
    metadata,
    Column("thread_id", String(120), primary_key=True),
    Column("energy", Float, nullable=False, default=0.8),
    Column("boredom", Float, nullable=False, default=0.2),
    Column("loneliness", Float, nullable=False, default=0.2),
    Column("last_pulse", Float, nullable=False, default=0.0),
)

thread_people = Table(
    "thread_people",
    metadata,
    Column("thread_id", String(120), nullable=False),
    Column(
        "person_id",
        String(80),
        ForeignKey("people.person_id", ondelete="CASCADE", name="fk_thread_people_person"),
        nullable=False,
    ),
    Column("last_seen", Float, nullable=False, default=0.0),
    Column("interactions", Integer, nullable=False, default=0),
    PrimaryKeyConstraint("thread_id", "person_id", name="pk_thread_people"),
)
Index("ix_thread_people_person", thread_people.c.person_id)

person_links = Table(
    "person_links",
    metadata,
    Column("gateway", String(40), primary_key=True),
    Column("address", String(160), primary_key=True),
    Column(
        "person_id",
        String(80),
        ForeignKey("people.person_id", ondelete="CASCADE", name="fk_person_links_person"),
        nullable=False,
    ),
    Column("created_at", Float, nullable=False),
    Column("seen_at", Float, nullable=False, default=0.0),
    Column("linked_by", String(120), nullable=False, default=""),
)
Index("ix_person_links_person", person_links.c.person_id)

person_link_codes = Table(
    "person_link_codes",
    metadata,
    Column("code", String(16), primary_key=True),
    Column("person_id", String(80), nullable=False),
    Column("gateway", String(40), nullable=False),
    Column("created_at", Float, nullable=False),
    Column("expires_at", Float, nullable=False),
    Column("attempts", Integer, nullable=False, default=0),
    Column("used_at", Float, nullable=False, default=0.0),
)

# ADR-0020 §4.5 / ADR-0016 §4.7: a procedure carries scope, provenance, trust, and a revision.
skills = Table(
    "skills",
    metadata,
    Column("name", String(80), primary_key=True),
    Column("description", String(400), nullable=False, default=""),
    Column("body", Text, nullable=False, default=""),
    Column("archived", Boolean, nullable=False, default=False),
    Column("created_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
    Column("scope", String(20), nullable=False, server_default="thread"),
    Column("app", String(40), nullable=False, server_default=""),
    Column("via", String(20), nullable=False, server_default="reflection"),
    Column("stated_by", String(80), nullable=True),
    Column("source_episode", String(160), nullable=True),
    Column("uses", Integer, nullable=False, server_default="0"),
    Column("last_used", Float, nullable=True),
    Column("revision", Integer, nullable=False, server_default="1"),
    Column("trust", String(8), nullable=False, server_default="T2"),
    Column("priority", Integer, nullable=False, server_default="0"),
    CheckConstraint("scope IN ('thread','person','global')", name="ck_skills_scope"),
    CheckConstraint("trust IN ('T0','T1','T2','T3')", name="ck_skills_trust"),
)
Index("ix_skills_scope", skills.c.scope)
Index("ix_skills_app_scope", skills.c.app, skills.c.scope)

# Append-only history (ADR-0020 §4.5): a save/merge never destroys the previous version.
skill_revisions = Table(
    "skill_revisions",
    metadata,
    Column(
        "skill",
        String(80),
        ForeignKey("skills.name", ondelete="CASCADE", name="fk_skill_revisions_skill"),
        nullable=False,
    ),
    Column("revision", Integer, nullable=False),
    Column("description", String(400), nullable=False, server_default=""),
    Column("body", Text, nullable=False),
    Column("created_at", Float, nullable=False),
    Column("source_episode", String(160), nullable=True),
    PrimaryKeyConstraint("skill", "revision", name="pk_skill_revisions"),
)

skill_files = Table(
    "skill_files",
    metadata,
    Column(
        "skill",
        String(80),
        ForeignKey("skills.name", ondelete="CASCADE", name="fk_skill_files_skill"),
        nullable=False,
    ),
    Column("path", String(300), nullable=False),
    Column("content", LargeBinary, nullable=False),
    Column("updated_at", Float, nullable=False),
    PrimaryKeyConstraint("skill", "path", name="pk_skill_files"),
)

# `skill_usage` is retired: ADR-0020 §4.5 moved `uses`/`last_used` onto the `skills` row, so the
# old counter table has no reader. Revision 5 drops it (ADR-0016 §4.3 rule 3 note in the migration).

recipes = Table(
    "recipes",
    metadata,
    Column("name", String(80), primary_key=True),
    Column("body", Text, nullable=False),
    Column("updated_at", Float, nullable=False),
)

config_changes = Table(
    "config_changes",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("ts", Float, nullable=False),
    Column("actor", String(160), nullable=False, default=""),
    Column("file", String(80), nullable=False),
    Column("path", String(200), nullable=False, default=""),
    Column("old", Text, nullable=False, default=""),
    Column("new", Text, nullable=False, default=""),
)

engine_lease = Table(
    "engine_lease",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("instance_id", String(80), nullable=False, default=""),
    Column("host", String(200), nullable=False, default=""),
    Column("pid", Integer, nullable=False, default=0),
    Column("mode", String(20), nullable=False, default="standby"),
    Column("generation", BigInteger, nullable=False, default=0, server_default="0"),
    Column("started_at", Float, nullable=False, default=0.0),
    Column("heartbeat_at", Float, nullable=False, default=0.0),
    CheckConstraint("id = 1", name="ck_engine_lease_single"),
)

engine_control = Table(
    "engine_control",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("frozen", Boolean, nullable=False, default=False),
    Column("frozen_by", String(120), nullable=False, default=""),
    Column("frozen_at", Float, nullable=False, default=0.0),
    Column("shutting_down", Boolean, nullable=False, default=False, server_default=text("false")),
)

