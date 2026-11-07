"""WhatsApp-only gateway tables (contacts/LID), without importing Neonize."""


from sqlalchemy import Column, Float, MetaData, String, Table

metadata = MetaData()

wa_contacts = Table(
    "wa_contacts",
    metadata,
    Column("jid", String(160), primary_key=True),
    Column("name", String(200), nullable=False, default=""),
    Column("push_name", String(200), nullable=False, default=""),
    Column("updated_at", Float, nullable=False, default=0.0),
)

wa_lid_map = Table(
    "wa_lid_map",
    metadata,
    Column("lid", String(160), primary_key=True),
    Column("pn", String(160), nullable=False, default=""),
)

wa_group_members = Table(
    "wa_group_members",
    metadata,
    Column("thread_id", String(160), primary_key=True),
    Column("member", String(160), primary_key=True),  # digits of the PN, JID, or LID
    Column("name", String(200), nullable=False, default=""),
    Column("updated_at", Float, nullable=False, default=0.0),
)


def ensure_schema(db) -> None:
    metadata.create_all(db.engine)
