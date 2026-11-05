"""Durable attachment handles (ADR-0001 §4.2, ADR-0004 §4.1).

One row per attachment that arrived with a message: the file's metadata, where the body put it,
and a content digest. The id (`att_…`) is the handle a tool forwards, so a file that arrived
through one body can leave through another without reading the archive, and re-storing the same
message keeps the same handle (the id is derived from the message identity and the digest).

The bytes themselves stay in `message_media` (ADR-0016 §4.5); `materialize` writes them back to a
readable path when the original body's file is gone.
"""


import hashlib
import time
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import and_, select

from ..db import Database
from ..schema import attachments, message_media
from ..turn.context import Media, Message

HANDLE_PREFIX = "att_"
HANDLE_CHARS = 24
MEDIA_DIR = "media"
ATTACHMENT_DIR = "attachments"
FILENAME_LIMIT = 120


def is_handle(value: object) -> bool:
    """True for an `att_…` handle the store handed out."""
    return str(value or "").strip().startswith(HANDLE_PREFIX)


def blob_of(media: Media) -> bytes | None:
    """The attachment bytes the body already carries, when it carries any."""
    if media.ref:
        return bytes(media.ref)
    proto = getattr(media, "proto", None)
    if proto is None:
        return None
    try:
        return proto.SerializeToString()
    except (AttributeError, TypeError):
        return None


def digest_of(media: Media, blob: bytes | None = None) -> str:
    """The content digest; a file-backed attachment is read once, never guessed."""
    body = blob_of(media) if blob is None else blob
    if body is None and media.path is not None:
        try:
            body = Path(media.path).read_bytes()
        except OSError:
            body = None
    return hashlib.sha256(body).hexdigest() if body else ""


def handle_for(gateway: str, thread_id: str, message_id: str, seq: int, sha256: str) -> str:
    """A stable handle: the same attachment always answers with the same `att_…` id."""
    key = f"{gateway}|{thread_id}|{message_id}|{int(seq)}|{sha256}"
    return HANDLE_PREFIX + hashlib.sha256(key.encode("utf-8")).hexdigest()[:HANDLE_CHARS]


def safe_filename(name: str, fallback: str = "attachment") -> str:
    """A filename that cannot escape the folder it is written into."""
    cleaned = "".join(
        ch for ch in str(name or "").replace("\\", "/").rsplit("/", 1)[-1] if ch.isprintable()
    )
    cleaned = cleaned.strip(" .")
    return (cleaned or fallback)[:FILENAME_LIMIT]


@dataclass(frozen=True)
class Attachment:
    """One stored attachment row, as a tool reads it."""

    id: str
    gateway: str
    thread_id: str
    message_id: str
    seq: int
    kind: str
    mime: str
    filename: str
    path: str
    size_bytes: int
    sha256: str
    created_at: float

    @property
    def local(self) -> Path | None:
        """The recorded file, when the body's copy is still there."""
        if not self.path:
            return None
        path = Path(self.path)
        return path if path.is_file() else None


def _row(row: dict) -> Attachment:
    return Attachment(
        id=str(row["id"]),
        gateway=str(row["gateway"]),
        thread_id=str(row["thread_id"]),
        message_id=str(row["message_id"]),
        seq=int(row["seq"]),
        kind=str(row["kind"]),
        mime=str(row["mime"]),
        filename=str(row["filename"]),
        path=str(row["path"]),
        size_bytes=int(row["size_bytes"]),
        sha256=str(row["sha256"]),
        created_at=float(row["created_at"]),
    )


class AttachmentStore:
    """Reads and writes `attachments`; the handle is the only thing a caller keeps."""

    def __init__(self, db: Database) -> None:
        self.db = db

    # ------------------------------------------------------------ write

    def values(
        self,
        *,
        gateway: str,
        thread_id: str,
        message_id: str,
        seq: int,
        media: Media,
        blob: bytes | None = None,
        now: float | None = None,
    ) -> dict:
        """The row one attachment becomes; the digest and the handle derive from it."""
        body = blob_of(media) if blob is None else blob
        sha = digest_of(media, body)
        index = max(0, int(seq))
        return {
            "id": handle_for(str(gateway), str(thread_id), str(message_id), index, sha),
            "gateway": str(gateway or "")[:40],
            "thread_id": str(thread_id or "")[:120],
            "message_id": str(message_id or "")[:160],
            "seq": index,
            "kind": str(media.kind or "")[:20],
            "mime": str(media.mimetype or "")[:160],
            "filename": safe_filename(media.filename),
            "path": str(media.path) if media.path is not None else "",
            "size_bytes": int(media.size or (len(body) if body else 0)),
            "sha256": sha,
            "created_at": time.time() if now is None else float(now),
        }

    def save_many(
        self,
        conn,
        *,
        gateway: str,
        items: list[tuple[str, Message]],
        blobs: dict[tuple[str, str], list[bytes | None]] | None = None,
        now: float | None = None,
    ) -> int:
        """Store every attachment of the given messages inside the caller's transaction."""
        rows: list[dict] = []
        for thread_id, message in items:
            for seq, media in enumerate(message.attachments):
                blob = None
                if blobs is not None:
                    found = blobs.get((str(thread_id), str(message.id or ""))) or []
                    blob = found[seq] if seq < len(found) else None
                rows.append(
                    self.values(
                        gateway=gateway,
                        thread_id=thread_id,
                        message_id=str(message.id or ""),
                        seq=seq,
                        media=media,
                        blob=blob,
                        now=now,
                    )
                )
        if not rows:
            return 0
        return self.db.upsert(
            conn,
            attachments,
            rows,
            ["id"],
            update={
                "kind": attachments.c.kind,
                "mime": attachments.c.mime,
                "filename": attachments.c.filename,
                "path": attachments.c.path,
                "size_bytes": attachments.c.size_bytes,
                "sha256": attachments.c.sha256,
            },
        )

    def save(
        self,
        *,
        gateway: str,
        thread_id: str,
        message_id: str,
        seq: int,
        media: Media,
        blob: bytes | None = None,
    ) -> Attachment:
        """Store one attachment outside an intake transaction (a test or a repair path)."""
        row = self.values(
            gateway=gateway,
            thread_id=thread_id,
            message_id=message_id,
            seq=seq,
            media=media,
            blob=blob,
        )
        with self.db.connect() as conn:
            self.db.upsert(
                conn,
                attachments,
                [row],
                ["id"],
                update={
                    "kind": attachments.c.kind,
                    "mime": attachments.c.mime,
                    "filename": attachments.c.filename,
                    "path": attachments.c.path,
                    "size_bytes": attachments.c.size_bytes,
                    "sha256": attachments.c.sha256,
                },
            )
        return _row(row)

    # ------------------------------------------------------------- read

    def get(self, handle: str) -> Attachment | None:
        """The attachment a handle names, or None."""
        found = self.db.fetch_one(
            select(attachments).where(attachments.c.id == str(handle or "").strip())
        )
        return _row(found) if found is not None else None

    def of_message(self, thread_id: str, message_id: str) -> list[Attachment]:
        rows = self.db.fetch_all(
            select(attachments)
            .where(
                and_(
                    attachments.c.thread_id == str(thread_id),
                    attachments.c.message_id == str(message_id),
                )
            )
            .order_by(attachments.c.seq)
        )
        return [_row(row) for row in rows]

    def stored_bytes(self, attachment: Attachment) -> bytes | None:
        found = self.db.fetch_one(
            select(message_media.c.bytes_ref).where(
                and_(
                    message_media.c.thread_id == attachment.thread_id,
                    message_media.c.message_id == attachment.message_id,
                    message_media.c.seq == int(attachment.seq),
                )
            )
        )
        body = found.get("bytes_ref") if found else None
        return bytes(body) if body is not None else None

    def materialize(self, handle: str, workspace: Path) -> Path | None:
        """A readable path for the handle: the body's file, or the stored bytes re-written."""
        attachment = self.get(handle)
        if attachment is None:
            return None
        local = attachment.local
        if local is not None:
            return local
        body = self.stored_bytes(attachment)
        if body is None:
            return None
        folder = Path(workspace) / MEDIA_DIR / ATTACHMENT_DIR
        folder.mkdir(parents=True, exist_ok=True)
        name = safe_filename(attachment.filename)
        path = folder / f"{attachment.id}_{name}"
        path.write_bytes(body)
        return path
