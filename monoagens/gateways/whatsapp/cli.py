"""WhatsApp CLI: history backfill through a throwaway device (transport-owned)."""


import asyncio
import contextlib
import logging
import sys

import segno
import yaml

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


async def pair(config_path: str | None) -> int:
    """Pair the primary WhatsApp session in the foreground; the QR stays on this terminal."""
    from neonize.aioze.client import NewAClient
    from neonize.aioze.events import ConnectedEv

    host, paths = load_host(config_path)
    url = resolve_database_url(host.database)
    if not url:
        raise ConfigError("database URL is empty")
    db = Database(url, pool_size=host.database.pool_size, pool_timeout=host.database.pool_timeout)
    db.ensure()
    ensure_schemas(db)
    body = yaml.safe_load(paths.config.read_text(encoding="utf-8")) or {}
    raw = (body.get("gateways") or {}).get("whatsapp") or {}
    if not isinstance(raw, dict) or raw.get("enabled") is not True:
        raise ConfigError("gateway 'whatsapp' is not enabled in config.yaml")
    settings = WhatsAppSettings.from_mapping(True, raw, paths.config.parent)
    session = settings.session_db
    done = asyncio.Event()
    client = NewAClient(str(session), props=history_sync.history_props(False))

    async def on_qr(accepted: NewAClient, data: bytes) -> None:
        print("\nScan this QR in WhatsApp > Linked devices:\n", flush=True)
        rendered = segno.make_qr(data).terminal(compact=True)
        if rendered:
            print(rendered, flush=True)
        sys.stdout.flush()

    @client.event(ConnectedEv)
    async def on_connected(accepted: NewAClient, ev) -> None:
        secure_db(session)
        print(f"\nWhatsApp paired: {session}\n", flush=True)
        done.set()

    client.event.qr(on_qr)
    try:
        await client.connect()
        await asyncio.wait_for(done.wait(), 300)
    except TimeoutError:
        print("\nWhatsApp pairing timed out after 300 seconds.\n", file=sys.stderr, flush=True)
        return 1
    finally:
        with contextlib.suppress(Exception):
            await client.stop()
        db.dispose()
    return 0


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
    documents.sync()
    config = load_config(paths.config, require_gateway=True)
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
        named = await directory.sync_groups(accepted, db)
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


