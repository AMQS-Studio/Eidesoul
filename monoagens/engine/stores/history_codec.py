"""Row codec for the transcript archive: message envelopes and media references.

The archive stores the envelope as one JSONB `data` column plus the attachment bytes; these
functions turn a `Message` into those values and back. They hold no connection and no SQL, so
`history.py` can keep the store itself focused on the write and read paths.
"""


import re

from ..guard.patterns import redact_credentials
from ..turn.context import Media, Message

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
    if message.attachments:
        # The old single-attachment key stays for readers of an older archive; the envelope is
        # the list every attachment of this message is decoded from.
        data["media"] = _media_json(message.attachments[0])
        data["attachments"] = [_media_json(item) for item in message.attachments]
    if message.quote is not None:
        quote = {
            "id": message.quote.id,
            "sender_name": message.quote.sender_name,
            "sender_id": message.quote.sender_id,
            "text": redact_credentials(message.quote.text or ""),
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
