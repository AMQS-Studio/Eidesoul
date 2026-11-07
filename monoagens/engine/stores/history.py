"""Per-chat transcript archive in the Database; media is stored as metadata + bytes."""


import json
import logging
import time
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import Text, and_, case, cast, func, literal, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ..config import SearchConfig
from ..db import Database
from ..guard.patterns import redact_credentials
from ..memory import CaptureJobSink
from ..memory.store import MemoryStore
from ..schema import memories_readable, message_media, messages, threads, turns
from ..turn.context import (
    AUTHOR_RANK,
    AUTHOR_UNKNOWN,
    Media,
    Message,
    Quote,
    clamp_visibility,
)
from .attachments import AttachmentStore
from .history_codec import FACT_KEYS, _blob, _data, _like_terms, _media_from
from .turns import turn_identity

log = logging.getLogger(__name__)


class RouteConflict(RuntimeError):
    """A second body claimed a thread another body already owns (ADR-0032 §4.7)."""

    def __init__(self, thread: str, owner: str, claimant: str) -> None:
        super().__init__(f"thread '{thread}' is owned by '{owner}', not '{claimant}'")
        self.thread = thread
        self.owner = owner
        self.claimant = claimant

class HistoryStore:
    """One row per (chat, message id); an upsert keeps the richer content."""

    def __init__(
        self,
        db: Database,
        workspace=None,
        *,
        memories: MemoryStore | None = None,
        job_sink: CaptureJobSink | None = None,
        search: SearchConfig | None = None,
        event_sink=None,
    ):
        self.db = db
        self.workspace = workspace
        self.memories = memories or MemoryStore(db)
        self.job_sink = job_sink
        # ADR-0014 §4.3: the message-event sink writes inside the intake transaction.
        self.event_sink = event_sink
        self.search_config = search or SearchConfig()
        # ADR-0001 §4.2: the durable handle of every attachment, written by the same intake.
        self.attachments = AttachmentStore(db)

    # ------------------------------------------------------------ write

    def _row(self, thread_id: str, message: Message) -> dict:
        return {
            "thread_id": thread_id,
            "msg_id": message.id or f"tmp-{time.time_ns()}",
            "ts": message.when.timestamp(),
            "sender_id": message.sender_id or "",
            "sender_name": message.sender_name or "",
            "sender_address": message.sender_address or "",
            # L5 material is never persisted: the archive keeps the redacted shape, the live
            # turn keeps the raw text in its in-memory window.
            "text": redact_credentials(message.text or ""),
            "outgoing": bool(message.outgoing),
            "author": message.author,
            "visibility": clamp_visibility(message.visibility),
            "deleted": bool(message.deleted),
            "deleted_by": message.deleted_by or "",
            "deleted_admin": bool(message.deleted_admin),
            "data": _data(message),
        }

    @staticmethod
    def _media_rows(thread_id: str, message: Message) -> list[dict]:
        """The bytes that no longer live on the row (ADR-0016 §4.5), numbered per message.

        Every attachment gets a row even when the body carried no bytes, so the row set mirrors
        the envelope and `seq` is the attachment's place in it. A quote's bytes use the next
        free number: `kind` says what the row is, `seq` is the identity.
        """
        rows: list[dict] = []
        for seq, media in enumerate(message.attachments):
            if media is None:
                continue
            rows.append(
                {
                    "thread_id": thread_id,
                    "message_id": message.id,
                    "seq": seq,
                    "kind": "media",
                    "mime": str(media.mimetype or ""),
                    "bytes_ref": _blob(media),
                }
            )
        quote = message.quote
        quote_media = quote.media if quote is not None else None
        blob = _blob(quote_media)
        if blob is not None:
            rows.append(
                {
                    "thread_id": thread_id,
                    "message_id": message.id,
                    "seq": len(message.attachments),
                    "kind": "quote",
                    "mime": str(getattr(quote_media, "mimetype", "") or ""),
                    "bytes_ref": blob,
                }
            )
        return rows

    @staticmethod
    def _content_data(excluded):
        """The incoming `data` without its fact keys; NULL when the row carries no content."""
        stripped = cast(excluded.data, JSONB)
        for key in FACT_KEYS:
            stripped = stripped.op("-")(cast(literal(key), Text))
        return func.nullif(cast(stripped, Text), "{}")

    @staticmethod
    def _merge(stmt) -> dict:
        table = messages
        excluded = stmt.excluded
        data = case(
            (HistoryStore._content_data(excluded).is_(None), table.c.data),
            else_=excluded.data,
        )
        new_rank = case(AUTHOR_RANK, value=excluded.author, else_=0)
        old_rank = case(AUTHOR_RANK, value=table.c.author, else_=0)
        keep_new = new_rank >= old_rank
        removed = excluded.deleted.is_(True)
        return {
            "sender_name": case(
                (
                    keep_new,
                    func.coalesce(func.nullif(excluded.sender_name, ""), table.c.sender_name),
                ),
                else_=table.c.sender_name,
            ),
            "sender_id": case(
                (keep_new, func.coalesce(func.nullif(excluded.sender_id, ""), table.c.sender_id)),
                else_=table.c.sender_id,
            ),
            "sender_address": case(
                (
                    keep_new,
                    func.coalesce(func.nullif(excluded.sender_address, ""), table.c.sender_address),
                ),
                else_=table.c.sender_address,
            ),
            "author": case((keep_new, excluded.author), else_=table.c.author),
            "visibility": case(
                (excluded.visibility > table.c.visibility, excluded.visibility),
                else_=table.c.visibility,
            ),
            "text": func.coalesce(func.nullif(excluded.text, ""), table.c.text),
            "deleted": or_(table.c.deleted, excluded.deleted),
            "deleted_by": case((removed, excluded.deleted_by), else_=table.c.deleted_by),
            "deleted_admin": case((removed, excluded.deleted_admin), else_=table.c.deleted_admin),
            "data": data,
        }

    def add(self, thread_id: str, message: Message) -> None:
        self.add_many([(thread_id, message)])

    def _upsert_messages(self, conn, rows: list[dict]) -> int:
        if not rows:
            return 0
        # FK parent (ADR-0016 §4.5): every message's thread exists before its children land.
        for thread_id in dict.fromkeys(str(row["thread_id"]) for row in rows):
            conn.execute(
                pg_insert(threads)
                .values(thread_id=thread_id, name="", reset_ts=0.0)
                .on_conflict_do_nothing(index_elements=["thread_id"])
            )
        self.db.upsert(
            conn,
            messages,
            rows,
            ["thread_id", "msg_id"],
            update=self._merge,
        )
        return len(rows)

    @staticmethod
    def _unique(items: list, key) -> list:
        """Keep first-seen order but the last value per key (history sync repeats rows)."""
        found: dict = {}
        for item in items:
            found[key(item)] = item
        return list(found.values())

    def add_many(self, items: list[tuple[str, Message]]) -> int:
        # WhatsApp HistorySync repeats (thread_id, message id) inside one batch; a repeated
        # primary key makes PostgreSQL raise CardinalityViolation on the multi-row upsert.
        items = self._unique(
            items, lambda item: (str(item[0]), str(item[1].id or ""))
        )
        rows = [self._row(thread_id, message) for thread_id, message in items]
        if not rows:
            return 0
        with self.db.connect() as conn:
            self._upsert_messages(conn, rows)
            self._upsert_media(conn, items)
            self._store_attachments(conn, items)
        return len(rows)

    def _upsert_media(self, conn, items: list[tuple[str, Message]]) -> None:
        rows = [row for thread_id, message in items for row in self._media_rows(thread_id, message)]
        rows = self._unique(
            rows,
            lambda row: (str(row["thread_id"]), str(row["message_id"]), int(row["seq"])),
        )
        if not rows:
            return
        statement = pg_insert(message_media).values(rows)
        conn.execute(
            statement.on_conflict_do_update(
                index_elements=["thread_id", "message_id", "seq"],
                set_={
                    "kind": statement.excluded.kind,
                    "mime": statement.excluded.mime,
                    "bytes_ref": func.coalesce(
                        statement.excluded.bytes_ref, message_media.c.bytes_ref
                    ),
                },
            )
        )

    def _route_gateway(self, conn, thread_id: str) -> str:
        found = conn.execute(
            select(threads.c.gateway).where(threads.c.thread_id == str(thread_id))
        ).scalar()
        return str(found or "")

    def _store_attachments(
        self, conn, items: list[tuple[str, Message]], gateway: str | None = None
    ) -> int:
        """Give every attachment a durable handle in the intake transaction (ADR-0001 §4.2)."""
        groups: dict[str, list[tuple[str, Message]]] = {}
        for thread_id, message in items:
            if not message.attachments:
                continue
            name = gateway if gateway is not None else self._route_gateway(conn, thread_id)
            groups.setdefault(str(name or ""), []).append((thread_id, message))
        if not groups:
            return 0
        return sum(
            self.attachments.save_many(conn, gateway=name, items=group)
            for name, group in groups.items()
        )

    def intake(
        self,
        thread_id: str,
        message: Message,
        person_id: str = "",
        *,
        language: str,
        audience: str = "private",
        category: str = "chat",
        gateway: str = "",
        app: str = "",
        hints: dict | None = None,
    ) -> bool:
        """The one intake transaction (ADR-0011 §4.1): message + turn + memory journal + candidate.

        `MemoryStore.intake` writes `memory_events` + `memories(pending, level NULL)` after hard
        exclusion on the same connection; `job_sink` is the W3.2/ADR-0003 seam that inserts the
        `capture` job in this transaction. Any failure rolls all five writes back together, so a
        turn is never processed from a partial intake. A duplicate message id is idempotent: the
        existing turn is kept, no second candidate is written, and `False` is returned.

        `hints` is the body's percept evidence (`dm`, `mention`, `reply`, `app`); it rides on
        the turn row so a resumed turn reconstructs the same `Percept` (ADR-0001/ADR-0003).
        """
        now = time.time()
        row = self._row(thread_id, message)
        message_id = str(message.id or row["msg_id"])
        # ADR-0001 §4.3: the turn is keyed by the namespaced identity, so two bodies that happen
        # to use the same message id never own the same turn; the raw id is kept beside it.
        turn_id = turn_identity(gateway, thread_id, message_id)
        with self.db.connect() as conn:
            self._route(conn, str(thread_id), str(gateway or ""), audience)
            self._upsert_messages(conn, [row])
            self._upsert_media(conn, [(thread_id, message)])
            self._store_attachments(conn, [(thread_id, message)], gateway=str(gateway or ""))
            created = conn.execute(
                pg_insert(turns)
                .values(
                    id=turn_id,
                    message_id=message_id,
                    thread_id=str(thread_id),
                    person_id=str(person_id or "")[:80],
                    status="received",
                    step="received",
                    app=str(app or "").strip().lower(),
                    category="action" if str(category) == "action" else "chat",
                    hints=dict(hints or {}),
                    attempt=0,
                    lease_owner="",
                    lease_until=0.0,
                    generation=0,
                    planned_chunks=0,
                    delivered_chunks=0,
                    shutdown_epoch=0,
                    boot_epoch=0,
                    last_error="",
                    created_at=now,
                    updated_at=now,
                )
                .on_conflict_do_nothing(index_elements=["id"])
                .returning(turns.c.id)
            ).scalar()
            if created is None:
                return False
            candidate = self.memories.intake(
                conn,
                thread_id=str(thread_id),
                person_id=str(person_id or ""),
                message_id=message_id,
                text=str(message.text or ""),
                language=language,
                audience=audience,
                asserted_at=message.when.timestamp() if message.when else now,
            )
            if candidate is not None and self.job_sink is not None:
                self.job_sink.enqueue(conn, candidate)
            if self.event_sink is not None:
                self.event_sink.enqueue_message(
                    conn,
                    thread_id=str(thread_id),
                    gateway=str(gateway or ""),
                    message=message,
                    person_id=str(person_id or ""),
                    now=now,
                )
        return True

    def _route(self, conn, thread_id: str, gateway: str, audience: str) -> None:
        """ADR-0014 §4.6: the first body that sees a thread owns its durable route.

        A second body claiming the same thread is refused before any row is written
        (ADR-0032 §4.7); storing the message under the wrong body is the failure this removes.
        """
        if gateway:
            owner = conn.execute(
                select(threads.c.gateway).where(threads.c.thread_id == str(thread_id))
            ).scalar()
            if owner and str(owner) != str(gateway):
                # A structured diagnostic, never a silent drop (ADR-0032 §4.7).
                log.error(
                    "route conflict thread=%s owner=%s claimant=%s: the message is refused",
                    thread_id,
                    owner,
                    gateway,
                )
                raise RouteConflict(str(thread_id), str(owner), str(gateway))
        insert = pg_insert(threads).values(
            thread_id=thread_id,
            name="",
            reset_ts=0.0,
            kind="group" if str(audience) == "group" else "dm",
            gateway=gateway,
        )
        conn.execute(
            insert.on_conflict_do_update(
                index_elements=["thread_id"],
                set_={"gateway": insert.excluded.gateway},
                where=threads.c.gateway == "",
            )
        )

    def gateway_of(self, thread_id: str) -> str:
        """The body that owns this thread's route; "" when no body claimed it yet."""
        found = self.db.fetch_one(
            select(threads.c.gateway).where(threads.c.thread_id == str(thread_id))
        )
        return str((found or {}).get("gateway") or "")

    def add_notice(
        self, thread_id: str, text: str, *, kind: str, message_id: str = ""
    ) -> None:
        """Record a `notice:*` bot message; `kind` rides in `messages.data`, never `turns`."""
        when = datetime.now(UTC)
        message = Message(
            id=str(message_id or f"notice-{time.time_ns()}"),
            sender_name="",
            sender_id="",
            when=when,
            text=str(text or ""),
            outgoing=True,
            author="bot",
        )
        row = self._row(thread_id, message)
        row["data"] = {**(row["data"] or {}), "kind": str(kind)}
        with self.db.connect() as conn:
            self._upsert_messages(conn, [row])

    def set_visibility(self, thread_id: str, message_ids: Sequence[str], level: int) -> int:
        """Raise the privacy mark of archive rows; visibility may only go up, never down."""
        value = clamp_visibility(level)
        changed = 0
        for message_id in dict.fromkeys(str(item) for item in message_ids if item):
            entry = self.find(thread_id, message_id)
            if entry is None or entry.visibility >= value:
                continue
            entry.visibility = value
            self.add(thread_id, entry)
            changed += 1
        return changed

    def mark_deleted(
        self, thread_id: str, message_id: str, by_address: str, by_name: str = ""
    ) -> bool:
        entry = self.find(thread_id, message_id)
        if entry is None or not entry.mark_deleted(by_address, by_name):
            return False
        self.add(thread_id, entry)
        return True

    def replace_text(self, thread_id: str, message_id: str, text: str) -> bool:
        """Rewrite one archived row's text after an inbound edit; False when it is unknown."""
        entry = self.find(thread_id, message_id)
        if entry is None:
            return False
        entry.text = str(text or "")
        self.add(thread_id, entry)
        return True

    def set_chat_name(self, thread_id: str, name: str) -> None:
        if not thread_id:
            return
        value = " ".join(str(name or "").split())[:200]
        if not value:
            return
        row = {
            "thread_id": thread_id,
            "name": value,
            "reset_ts": 0.0,
        }
        with self.db.connect() as conn:
            self.db.upsert(
                conn, threads, [row], ["thread_id"], update={"name": value}
            )

    # ------------------------------------------------------------ read

    def _decode_rows(self, rows: Sequence[dict]) -> list[Message]:
        """Decode rows and attach the media bytes that live in `message_media`."""
        if not rows:
            return []
        pairs = {(str(row["thread_id"]), str(row["msg_id"])) for row in rows}
        found = self.db.fetch_all(
            select(
                message_media.c.thread_id,
                message_media.c.message_id,
                message_media.c.seq,
                message_media.c.kind,
                message_media.c.bytes_ref,
            ).where(
                and_(
                    message_media.c.thread_id.in_({thread for thread, _ in pairs}),
                    message_media.c.message_id.in_({message for _, message in pairs}),
                )
            )
        )
        # One blob per (thread, message, kind, seq); a NULL reference still marks the attachment.
        blobs: dict[tuple[str, str, str], dict[tuple[int, str], bytes | None]] = {}
        for item in found:
            key = (str(item["thread_id"]), str(item["kind"]), str(item["message_id"]))
            body = item.get("bytes_ref")
            blobs.setdefault(key, {})[(int(item["seq"]), str(item["kind"]))] = (
                bytes(body) if body is not None else None
            )
        return [self._decode(row, blobs) for row in rows]

    def _decode(
        self,
        row: dict,
        blobs: dict[tuple[str, str, str], dict[tuple[int, str], bytes | None]] | None = None,
    ) -> Message:
        blobs = blobs or {}
        thread_id = str(row["thread_id"])
        message_id = str(row["msg_id"])
        media_rows = blobs.get((thread_id, "media", message_id), {})
        quote_rows = blobs.get((thread_id, "quote", message_id), {})
        try:
            data = row.get("data") or {}
        except AttributeError:
            data = {}
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except (TypeError, ValueError):
                data = {}
        if not isinstance(data, dict):
            data = {}
        quote_data = data.get("quote")
        quote = None
        if isinstance(quote_data, dict):
            when = quote_data.get("when")
            quote = Quote(
                id=str(quote_data.get("id") or ""),
                sender_name=str(quote_data.get("sender_name") or ""),
                sender_id=str(quote_data.get("sender_id") or ""),
                text=str(quote_data.get("text") or ""),
                media=_media_from(
                    quote_data.get("media"),
                    next(iter(quote_rows.values()), None),
                ),
                when=datetime.fromtimestamp(float(when)) if when else None,
            )
        author = str(row.get("author") or AUTHOR_UNKNOWN)
        if author not in AUTHOR_RANK:
            author = AUTHOR_UNKNOWN
        notice = str(data.get("kind") or "")
        if not notice.startswith("notice:"):
            notice = ""
        return Message(
            id=str(row["msg_id"]),
            sender_name=str(row["sender_name"]),
            sender_id=str(row["sender_id"]),
            when=datetime.fromtimestamp(float(row["ts"])),
            text=str(row["text"]),
            attachments=self._attachments(data, media_rows),
            quote=quote,
            outgoing=bool(row["outgoing"]),
            sender_address=str(row["sender_address"]),
            sender_kind=str(data.get("sender_kind") or ""),
            author=author,
            visibility=clamp_visibility(row.get("visibility")),
            view_once=bool(data.get("view_once")),
            unreadable=bool(data.get("unreadable")),
            deleted=bool(row.get("deleted")),
            deleted_by=str(row.get("deleted_by")),
            deleted_admin=bool(row.get("deleted_admin")),
            notice=notice,
        )

    @staticmethod
    def _attachments(
        data: dict, media_rows: dict[tuple[int, str], bytes | None]
    ) -> tuple[Media, ...]:
        """The envelope of one archived message, in `seq` order (ADR-0001 §4.2)."""
        metas = data.get("attachments")
        if not isinstance(metas, list):
            # An archive written before the envelope: the single `media` key is the envelope.
            first = data.get("media")
            metas = [first] if isinstance(first, dict) else []
        found: list[Media] = []
        for seq, meta in enumerate(metas):
            if not isinstance(meta, dict):
                continue
            media = _media_from(meta, media_rows.get((seq, "media")))
            if media is not None:
                found.append(media)
        if found or not media_rows:
            return tuple(found)
        # Metadata lost but the bytes remain: the rows still describe the attachments.
        return tuple(
            media
            for media in (
                _media_from({}, body)
                for _, body in sorted(media_rows.items())
                if body is not None
            )
            if media is not None
        )

    def chat_name(self, thread_id: str) -> str:
        if not thread_id:
            return ""
        found = self.db.fetch_one(
            select(threads.c.name).where(threads.c.thread_id == thread_id)
        )
        return str(found["name"]) if found else ""

    def recent(self, thread_id: str, limit: int) -> list[Message]:
        statement = (
            select(messages)
            .where(messages.c.thread_id == thread_id)
            .order_by(messages.c.ts.desc(), messages.c.id.desc())
            .limit(max(1, int(limit)))
        )
        return self._decode_rows(list(reversed(self.db.fetch_all(statement))))

    def find(self, thread_id: str, msg_id: str) -> Message | None:
        if not msg_id:
            return None
        statement = (
            select(messages)
            .where(and_(messages.c.thread_id == thread_id, messages.c.msg_id == msg_id))
            .order_by(messages.c.id.desc())
            .limit(1)
        )
        found = self.db.fetch_one(statement)
        return self._decode_rows([found])[0] if found else None

    def search(
        self,
        thread_id: str,
        query: str = "",
        *,
        before: float | None = None,
        after: float | None = None,
        limit: int = 20,
    ) -> list[Message]:
        limit = max(1, min(int(limit or 20), 50))
        clauses = [messages.c.thread_id == thread_id]
        if after is not None:
            clauses.append(messages.c.ts >= float(after))
        if before is not None:
            clauses.append(messages.c.ts < float(before))
        if not query.strip():
            return self._latest(clauses, limit)
        for rows in (
            self._fts(query, clauses, limit),
            self._trgm(query, clauses, limit),
            self._like(query, clauses, limit),
        ):
            if rows:
                return rows
        return []

    def _latest(self, clauses: list, limit: int) -> list[Message]:
        statement = (
            select(messages)
            .where(and_(*clauses))
            .order_by(messages.c.ts.desc(), messages.c.id.desc())
            .limit(limit)
        )
        return self._decode_rows(self.db.fetch_all(statement))

    def _fts(self, query: str, clauses: list, limit: int) -> list[Message]:
        """ADR-0026 §4.1: `websearch_to_tsquery('simple')`, ranked, deterministic tiebreak."""
        if not self.search_config.fts:
            return []
        tsquery = func.websearch_to_tsquery(self.search_config.lang, query)
        rank = func.ts_rank_cd(messages.c.text_tsv, tsquery)
        statement = (
            select(messages)
            .where(and_(*clauses, messages.c.text_tsv.op("@@")(tsquery)))
            .order_by(rank.desc(), messages.c.ts.desc(), messages.c.id.desc())
            .limit(limit)
        )
        return self._decode_rows(self.db.fetch_all(statement))

    def _trgm(self, query: str, clauses: list, limit: int) -> list[Message]:
        """ADR-0026 §4.3: the second net for affixes and typos, only when FTS found nothing."""
        if not self.search_config.trgm:
            return []
        terms = _like_terms(query)
        threshold = float(self.search_config.trgm_threshold)
        scores = [
            func.greatest(
                func.word_similarity(term, func.coalesce(messages.c.text, "")),
                func.word_similarity(term, func.coalesce(messages.c.sender_name, "")),
            )
            for term in terms
        ]
        best = func.greatest(*scores) if len(scores) > 1 else scores[0]
        statement = (
            select(messages)
            .where(and_(*clauses, best >= threshold))
            .order_by(best.desc(), messages.c.ts.desc(), messages.c.id.desc())
            .limit(limit)
        )
        return self._decode_rows(self.db.fetch_all(statement))

    def _like(self, query: str, clauses: list, limit: int) -> list[Message]:
        """Only when the operator disabled both nets (ADR-0026 §6 `search_backend=like`)."""
        if self.search_config.fts or self.search_config.trgm:
            return []
        terms = [f"%{term}%" for term in _like_terms(query)]
        statement = (
            select(messages)
            .where(
                and_(
                    *clauses,
                    *[
                        messages.c.text.ilike(term) | messages.c.sender_name.ilike(term)
                        for term in terms
                    ],
                )
            )
            .order_by(messages.c.ts.desc(), messages.c.id.desc())
            .limit(limit)
        )
        return self._decode_rows(self.db.fetch_all(statement))

    def search_backend(self) -> str:
        """The active backend for `/status` (ADR-0026 §6); never a silent fallback."""
        if self.search_config.fts:
            return "fts+trgm" if self.search_config.trgm else "fts"
        return "trgm" if self.search_config.trgm else "like"

    def rooms_of(self, person_id: str) -> set[str]:
        """Rooms where the person is recorded as present (ADR-0007 §4.5 layer 2).

        The intersection of `messages.sender_id` and `memories.owner_id/subject_id`; a weaker
        fallback than the gateway's `participants`, used only when the body cannot answer.
        """
        who = str(person_id or "").strip()
        if not who:
            return set()
        sent = {
            str(row["thread_id"])
            for row in self.db.fetch_all(
                select(messages.c.thread_id)
                .where(and_(messages.c.sender_id == who, messages.c.thread_id != ""))
                .distinct()
            )
        }
        remembered = {
            str(row["thread_id"])
            for row in self.db.fetch_all(
                select(memories_readable.c.thread_id)
                .where(
                    and_(
                        or_(
                            memories_readable.c.owner_id == who,
                            memories_readable.c.subject_id == who,
                        ),
                        memories_readable.c.thread_id != "",
                    )
                )
                .distinct()
            )
        }
        return {thread for thread in sent | remembered if thread}

    def message_count(self) -> int:
        return int(self.db.scalar(select(func.count()).select_from(messages)) or 0)

    def recent_threads(self, limit: int = 50) -> list[str]:
        statement = (
            select(messages.c.thread_id, func.max(messages.c.ts).label("last"))
            .group_by(messages.c.thread_id)
            .order_by(func.max(messages.c.ts).desc())
            .limit(max(1, int(limit)))
        )
        return [str(row["thread_id"]) for row in self.db.fetch_all(statement) if row["thread_id"]]

    def _human_rows(self, limit: int, days: int) -> list[dict]:
        size = max(1, min(int(limit or 2000), 20_000))
        cutoff = time.time() - max(1, int(days)) * 86400
        statement = (
            select(
                messages.c.thread_id,
                messages.c.sender_id,
                messages.c.sender_name,
                messages.c.sender_address.label("sender_address"),
                messages.c.text,
                messages.c.ts,
            )
            .where(
                and_(
                    messages.c.outgoing.is_(False),
                    messages.c.text != "",
                    messages.c.ts >= cutoff,
                    ~select(message_media.c.message_id)
                    .where(
                        message_media.c.thread_id == messages.c.thread_id,
                        message_media.c.message_id == messages.c.msg_id,
                    )
                    .exists(),
                )
            )
            .order_by(messages.c.ts.desc(), messages.c.id.desc())
            .limit(size)
        )
        return self.db.fetch_all(statement)

    def people_seed(self, limit: int = 5000, days: int = 90) -> list[dict]:
        return self._human_rows(limit, days)

    def style_sample(self, limit: int = 2000) -> list[dict]:
        return self._human_rows(limit, 365)

    def close(self) -> None:
        """Shutdown compatibility hook; the Database is owned by the runtime."""
