"""Per-chat transcript archive in the Database; media is stored as metadata + bytes."""


import json
import logging
import re
import time
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import Text, and_, case, cast, func, literal, or_, select
from sqlalchemy import text as text_
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ..config import SearchConfig
from ..db import Database
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

log = logging.getLogger(__name__)


class RouteConflict(RuntimeError):
    """A second body claimed a thread another body already owns (ADR-0032 §4.7)."""

    def __init__(self, thread: str, owner: str, claimant: str) -> None:
        super().__init__(f"thread '{thread}' is owned by '{owner}', not '{claimant}'")
        self.thread = thread
        self.owner = owner
        self.claimant = claimant

# Facts ride in the `data` blob but never make a row richer: the upsert keeps the old content
# when the incoming row carries nothing but these keys.
FACT_KEYS = ("sender_kind",)


def _media_json(media: Media) -> dict:
    return {
        "kind": media.kind,
        "mimetype": media.mimetype,
        "filename": media.filename,
        "size": int(media.size or 0),
    }


def _media_from(value, ref) -> Media | None:
    if not isinstance(value, dict):
        return None
    return Media(
        kind=str(value.get("kind") or ""),
        mimetype=str(value.get("mimetype") or ""),
        filename=str(value.get("filename") or ""),
        size=int(value.get("size") or 0),
        proto=None,
        ref=bytes(ref) if ref else None,
    )


def _data(message: Message) -> dict:
    data: dict = {}
    if message.media is not None:
        data["media"] = _media_json(message.media)
    if message.quote is not None:
        quote = {
            "id": message.quote.id,
            "sender_name": message.quote.sender_name,
            "sender_id": message.quote.sender_id,
            "text": message.quote.text,
            "when": message.quote.when.timestamp() if message.quote.when else None,
        }
        if message.quote.media is not None:
            quote["media"] = _media_json(message.quote.media)
        data["quote"] = quote
    if message.view_once:
        data["view_once"] = True
    if message.unreadable:
        data["unreadable"] = True
    if message.sender_kind:
        data["sender_kind"] = message.sender_kind
    return data


def _blob(media: Media | None) -> bytes | None:
    if media is None:
        return None
    if media.ref:
        return bytes(media.ref)
    proto = getattr(media, "proto", None)
    if proto is None:
        return None
    try:
        return proto.SerializeToString()
    except (AttributeError, TypeError):
        return None


def _like_terms(query: str) -> list[str]:
    return re.findall(r"\w+", query, re.UNICODE) or [query.strip()]


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

    # ------------------------------------------------------------ write

    def _row(self, thread_id: str, message: Message) -> dict:
        return {
            "thread_id": thread_id,
            "msg_id": message.id or f"tmp-{time.time_ns()}",
            "ts": message.when.timestamp(),
            "sender_id": message.sender_id or "",
            "sender_name": message.sender_name or "",
            "sender_address": message.sender_address or "",
            "text": message.text or "",
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
        """The bytes that no longer live on the row (ADR-0016 §4.5)."""
        rows: list[dict] = []
        quote = message.quote
        for kind, media in (
            ("media", message.media),
            ("quote", quote.media if quote is not None else None),
        ):
            blob = _blob(media)
            if blob is None:
                continue
            rows.append(
                {
                    "thread_id": thread_id,
                    "message_id": message.id,
                    "kind": kind,
                    "mime": str(getattr(media, "mimetype", "") or ""),
                    "bytes_ref": blob,
                }
            )
        return rows

    @staticmethod
    def _content_data(excluded):
        """The incoming `data` without its fact keys; NULL when the row carries no content."""
        stripped = cast(excluded.data, JSONB)
        for key in FACT_KEYS:
            stripped = stripped - cast(literal(key), Text)
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

    def add_many(self, items: list[tuple[str, Message]]) -> int:
        rows = [self._row(thread_id, message) for thread_id, message in items]
        if not rows:
            return 0
        with self.db.connect() as conn:
            self._upsert_messages(conn, rows)
            self._upsert_media(conn, items)
        return len(rows)

    def _upsert_media(self, conn, items: list[tuple[str, Message]]) -> None:
        rows = [row for thread_id, message in items for row in self._media_rows(thread_id, message)]
        if not rows:
            return
        statement = pg_insert(message_media).values(rows)
        conn.execute(
            statement.on_conflict_do_update(
                index_elements=["thread_id", "message_id", "kind"],
                set_={
                    "mime": statement.excluded.mime,
                    "bytes_ref": func.coalesce(
                        statement.excluded.bytes_ref, message_media.c.bytes_ref
                    ),
                },
            )
        )

    def intake(
        self,
        thread_id: str,
        message: Message,
        person_id: str = "",
        *,
        language: str = "id",
        audience: str = "private",
        category: str = "chat",
        gateway: str = "",
    ) -> bool:
        """The one intake transaction (ADR-0011 §4.1): message + turn + memory journal + candidate.

        `MemoryStore.intake` writes `memory_events` + `memories(pending, level NULL)` after hard
        exclusion on the same connection; `job_sink` is the W3.2/ADR-0003 seam that inserts the
        `capture` job in this transaction. Any failure rolls all five writes back together, so a
        turn is never processed from a partial intake. A duplicate message id is idempotent: the
        existing turn is kept, no second candidate is written, and `False` is returned.
        """
        now = time.time()
        row = self._row(thread_id, message)
        with self.db.connect() as conn:
            self._route(conn, str(thread_id), str(gateway or ""), audience)
            self._upsert_messages(conn, [row])
            self._upsert_media(conn, [(thread_id, message)])
            created = conn.execute(
                pg_insert(turns)
                .values(
                    id=str(message.id or row["msg_id"]),
                    thread_id=str(thread_id),
                    person_id=str(person_id or "")[:80],
                    status="received",
                    step="received",
                    category="action" if str(category) == "action" else "chat",
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
                message_id=str(message.id or row["msg_id"]),
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
        when = datetime.now()
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
                message_media.c.kind,
                message_media.c.bytes_ref,
            ).where(
                and_(
                    message_media.c.thread_id.in_({thread for thread, _ in pairs}),
                    message_media.c.message_id.in_({message for _, message in pairs}),
                )
            )
        )
        blobs: dict[tuple[str, str, str], bytes] = {}
        for item in found:
            if item.get("bytes_ref") is None:
                continue
            key = (str(item["thread_id"]), str(item["kind"]), str(item["message_id"]))
            blobs[key] = bytes(item["bytes_ref"])
        return [self._decode(row, blobs) for row in rows]

    def _decode(
        self, row: dict, blobs: dict[tuple[str, str, str], bytes] | None = None
    ) -> Message:
        blobs = blobs or {}
        thread_id = str(row["thread_id"])
        message_id = str(row["msg_id"])
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
                    quote_data.get("media"), blobs.get((thread_id, "quote", message_id))
                ),
                when=datetime.fromtimestamp(float(when)) if when else None,
            )
        author = str(row.get("author") or AUTHOR_UNKNOWN)
        if author not in AUTHOR_RANK:
            author = AUTHOR_UNKNOWN
        return Message(
            id=str(row["msg_id"]),
            sender_name=str(row["sender_name"]),
            sender_id=str(row["sender_id"]),
            when=datetime.fromtimestamp(float(row["ts"])),
            text=str(row["text"]),
            media=_media_from(data.get("media"), blobs.get((thread_id, "media", message_id))),
            quote=quote,
            outgoing=bool(row["outgoing"]),
            sender_address=str(row["sender_address"]),
            sender_kind=str(data.get("sender_kind") or ""),
            author=author,
            visibility=clamp_visibility(row.get("visibility")),
            view_once=bool(data.get("view_once")),
            unreadable=bool(data.get("unreadable")),
            deleted=bool(row.get("deleted")),
            deleted_by=str(row.get("deleted_by") or ""),
            deleted_admin=bool(row.get("deleted_admin")),
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

    def dm_thread(self, person_id: str) -> str:
        """The one direct thread a person holds, or `""` (ADR-0010 §4.7 question target).

        A DM is a `threads.kind='dm'` row whose `thread_people` membership is exactly this person.
        The body sets `kind`; the core never parses a transport address to decide.
        """
        who = str(person_id or "").strip()
        if not who:
            return ""
        found = self.db.fetch_one(
            text_(
                "SELECT t.thread_id FROM threads AS t "
                "JOIN thread_people AS p ON p.thread_id = t.thread_id "
                "WHERE t.kind = 'dm' AND p.person_id = :person "
                "GROUP BY t.thread_id HAVING count(*) = 1 LIMIT 1"
            ),
            {"person": who},
        )
        return str(found["thread_id"]) if found else ""

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
