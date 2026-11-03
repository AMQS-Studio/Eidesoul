"""Tables this plugin owns (ADR-0027, ADR-0033 §4.6): core never defines or reads them."""

from sqlalchemy import Column, Float, Integer, MetaData, String, Table, Text

metadata = MetaData()

google_identity = Table(
    "google_identity",
    metadata,
    Column("person_id", String(160), primary_key=True),
    Column("email", String(320), nullable=False),
    Column("subject", String(128), nullable=False, default=""),
    Column("linked_at", Float, nullable=False),
)

google_device = Table(
    "google_device",
    metadata,
    Column("person_id", String(160), primary_key=True),
    Column("device_code", Text, nullable=False),
    Column("user_code", String(32), nullable=False),
    Column("verification_url", String(300), nullable=False),
    Column("expires_at", Float, nullable=False),
    Column("interval", Integer, nullable=False, default=5),
    Column("status", String(20), nullable=False, default="pending"),
    Column("last_error", Text, nullable=False, default=""),
    Column("created_at", Float, nullable=False),
)


def ensure(db) -> None:
    """Create the plugin's tables when the host lacks them; safe to call on every boot."""
    metadata.create_all(db.engine, checkfirst=True)
