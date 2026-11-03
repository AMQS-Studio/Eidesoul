"""WhatsApp CLI: history backfill through a throwaway device (transport-owned)."""


import asyncio
import contextlib
import logging

from ...engine.config import (
    ConfigError,
    drop_session,
    load_config,
    load_host,
    resolve_database_url,
    secure_db,
)
from ...engine.db import Database
from ...engine.stores.documents import DocumentStore
from ...engine.stores.history import HistoryStore
from .. import ensure_schemas
from . import directory, history_sync, ingress
from .config import WhatsAppSettings

log = logging.getLogger(__name__)


async def backfill(config_path: str | None) -> int:
    """Pair a throwaway device for history sync, then log it out; the primary is untouched."""
    from neonize.aioze.client import NewAClient
    from neonize.aioze.events import ConnectedEv, HistorySyncEv

    host, paths = load_host(config_path)
    url = resolve_database_url(host.database)
    if not url:
        raise ConfigError("database URL is empty")
    db = Database(url, pool_size=host.database.pool_size, pool_timeout=host.database.pool_timeout)
    db.ensure()
    ensure_schemas(db)
    documents = DocumentStore(db, paths, sync=host.sync_documents)
    documents.seed()
    config = load_config(paths.config, documents=documents, require_gateway=True)
    raw = config.gateways.get("whatsapp")
    if raw is None:
        raise ConfigError("gateway 'whatsapp' is not configured in config.yaml")
    settings = WhatsAppSettings.from_mapping(raw.enabled, raw.options, paths.config.parent)
    history = HistoryStore(db, config.workspace)
    session = settings.session_db.parent / "backfill.db"
    drop_session(session)
    client = NewAClient(str(session), props=history_sync.history_props(True))
    done = asyncio.Event()
    paired = False

    @client.event(ConnectedEv)
    async def on_connected(accepted: NewAClient, ev) -> None:
        nonlocal paired
        paired = True
        secure_db(session)
        named = await directory.sync_group_names(accepted, db)
        log.info("backfill: synced group names: %d", named)
        log.info("backfill connected; waiting for history sync from the phone")

    @client.event(HistorySyncEv)
    async def on_history(accepted: NewAClient, ev) -> None:
        if ev.Data is None:
            return
        count, thread_ids, finished = history_sync.ingest_sync(
            db, history, ev.Data, ingress.self_jid(accepted.me)
        )
        log.info(
            "backfill: %d messages, %d threads, sync=%s progress=%d%s",
            count,
            len(thread_ids),
            ev.Data.syncType,
            ev.Data.progress,
            " (done)" if finished else "",
        )
        if finished:
            done.set()

    try:
        await client.connect()
        secure_db(session)
        try:
            await asyncio.wait_for(done.wait(), 900)
        except TimeoutError:
            log.warning("backfill timed out after 900 seconds")
    finally:
        logged_out = False
        if paired:
            try:
                await client.logout()
                logged_out = True
            except Exception:
                log.exception("failed to log out the backfill device")
        with contextlib.suppress(Exception):
            await client.stop()
        if logged_out:
            drop_session(session)
        db.dispose()
    return 0 if paired and done.is_set() else 1


