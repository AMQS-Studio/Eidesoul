"""WhatsApp directory: contacts, LID, chat names, groups, and target resolution."""


import asyncio
import logging
import sqlite3
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from sqlalchemy import and_, func, select, update
from sqlalchemy import text as text_

from ...engine.db import Database
from ...engine.schema import messages, person_links, threads
from .identity import normalize_jid
from .schema import wa_contacts, wa_group_members, wa_lid_map

log = logging.getLogger(__name__)

GROUP_LIST_TIMEOUT = 30.0
LIST_LIMIT = 20


def _jid(value: str, default: str = "s.whatsapp.net") -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text if "@" in text else f"{text}@{default}"


def _upsert_contact(db: Database, jid: str, name: str, push_name: str) -> None:
    if not jid:
        return
    name = " ".join(str(name or "").split())[:200]
    push_name = " ".join(str(push_name or "").split())[:200]
    if not name and not push_name:
        return
    with db.connect() as conn:
        def merge(stmt):
            return {
                "name": func.coalesce(func.nullif(stmt.excluded.name, ""), wa_contacts.c.name),
                "push_name": func.coalesce(
                    func.nullif(stmt.excluded.push_name, ""), wa_contacts.c.push_name
                ),
                "updated_at": time.time(),
            }

        db.upsert(
            conn,
            wa_contacts,
            [{"jid": jid, "name": name, "push_name": push_name, "updated_at": time.time()}],
            ["jid"],
            update=merge,
        )


def set_lid(db: Database, lid: str, pn: str) -> None:
    if not lid or not pn:
        return
    lid = _jid(lid, "lid")
    pn = _jid(pn)
    with db.connect() as conn:
        db.upsert(conn, wa_lid_map, [{"lid": lid, "pn": pn}], ["lid"], update={"pn": pn})


def contact_name(db: Database, jid: str) -> str:
    if not jid:
        return ""
    direct = db.fetch_one(select(wa_contacts).where(wa_contacts.c.jid == jid))
    if jid.endswith("@lid"):
        mapped = db.fetch_one(
            select(wa_contacts)
            .select_from(wa_contacts.join(wa_lid_map, wa_contacts.c.jid == wa_lid_map.c.pn))
            .where(wa_lid_map.c.lid == jid)
        )
    else:
        mapped = db.fetch_one(
            select(wa_contacts)
            .select_from(wa_contacts.join(wa_lid_map, wa_contacts.c.jid == wa_lid_map.c.lid))
            .where(wa_lid_map.c.pn == jid)
        )
    for row in (direct, mapped):
        if row is None:
            continue
        if row.get("name"):
            return str(row["name"])
    for row in (direct, mapped):
        if row is not None and row.get("push_name"):
            return str(row["push_name"])
    return ""


def pn_jid(db: Database, jid: str) -> str:
    """Number (PN) JID for a LID; "" when it is not in wa_lid_map."""
    if not jid.endswith("@lid"):
        return ""
    found = db.fetch_one(select(wa_lid_map.c.pn).where(wa_lid_map.c.lid == jid))
    return str(found["pn"]) if found is not None and found["pn"] else ""


def refresh_sender_names(db: Database) -> int:
    rows = db.fetch_all(
        select(
            messages.c.id,
            messages.c.sender_address,
            messages.c.sender_name,
            messages.c.sender_id,
        )
        .where(and_(messages.c.sender_address != "", messages.c.outgoing.is_(False)))
    )
    changed = 0
    for row in rows:
        name = contact_name(db, str(row["sender_address"]))
        if not name or name == row["sender_name"]:
            continue
        current = str(row["sender_name"] or "")
        if current and current not in {"", str(row["sender_id"] or "")}:
            continue
        with db.connect() as conn:
            conn.execute(
                update(messages).where(messages.c.id == row["id"]).values(sender_name=name)
            )
        changed += 1
    return changed


def import_contacts(db: Database, session_db: Path | str) -> int:
    """Copy contacts from the Neonize session DB (read-only), then clean up old sender names."""
    path = Path(session_db).expanduser()
    if not path.is_file():
        return 0
    try:
        source = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        source.row_factory = sqlite3.Row
        contacts = source.execute(
            "SELECT their_jid, first_name, full_name, push_name FROM whatsmeow_contacts"
        ).fetchall()
        mappings = source.execute("SELECT lid, pn FROM whatsmeow_lid_map").fetchall()
        source.close()
    except sqlite3.Error:
        log.debug("session contacts could not be read: %s", path, exc_info=True)
        return 0
    for row in mappings:
        set_lid(db, str(row["lid"]), str(row["pn"]))
    with db.connect() as conn:
        conn.execute(wa_lid_map.delete().where(~wa_lid_map.c.lid.like("%@%")))
    for row in contacts:
        _upsert_contact(
            db,
            str(row["their_jid"] or ""),
            str(row["full_name"] or row["first_name"] or ""),
            str(row["push_name"] or ""),
        )
    refresh_sender_names(db)
    return len(contacts)


def chat_name(db: Database, thread_id: str) -> str:
    if not thread_id:
        return ""
    found = db.fetch_one(select(threads.c.name).where(threads.c.thread_id == thread_id))
    name = str(found["name"]) if found else ""
    if name or not thread_id.endswith(("@s.whatsapp.net", "@lid")):
        return name
    fallback = contact_name(db, thread_id)
    if fallback:
        set_chat_name(db, thread_id, fallback)
    return fallback


def set_chat_name(db: Database, thread_id: str, name: str) -> None:
    value = " ".join(str(name or "").split())[:200]
    if not thread_id or not value:
        return
    with db.connect() as conn:
        db.upsert(
            conn,
            threads,
            [
                {
                    "thread_id": thread_id,
                    "name": value,
                    "reset_ts": 0.0,
                    "kind": "group" if thread_id.endswith("@g.us") else "dm",
                }
            ],
            ["thread_id"],
            update={"name": value},
        )


def groups(db: Database, query: str = "", limit: int = LIST_LIMIT) -> list[tuple[str, str]]:
    limit = max(1, min(int(limit or LIST_LIMIT), 50))
    term = str(query or "").strip().casefold()
    rows = db.fetch_all(select(threads).where(threads.c.thread_id.like("%@g.us")))
    last = {
        row["thread_id"]: row["last"]
        for row in db.fetch_all(
            select(messages.c.thread_id, func.max(messages.c.ts).label("last"))
            .where(messages.c.thread_id.like("%@g.us"))
            .group_by(messages.c.thread_id)
        )
    }
    found: dict[str, tuple[str, float]] = {}
    for row in rows:
        thread_id = str(row["thread_id"])
        name = str(row["name"] or "")
        found[thread_id] = (name, float(last.get(thread_id, 0.0)))
    for thread_id, stamp in last.items():
        found.setdefault(str(thread_id), ("", float(stamp)))
    items = [
        (thread_id, name, stamp)
        for thread_id, (name, stamp) in found.items()
        if not term or term in name.casefold() or term in thread_id.casefold()
    ]
    items.sort(key=lambda item: (-item[2], item[0]))
    return [(thread_id, name) for thread_id, name, _stamp in items[:limit]]


def contacts(db: Database, query: str = "", limit: int = LIST_LIMIT) -> list[tuple[str, str]]:
    limit = max(1, min(int(limit or LIST_LIMIT), 50))
    term = str(query or "").strip().casefold()
    rows = db.fetch_all(
        select(wa_contacts, wa_lid_map.c.pn)
        .select_from(wa_contacts.outerjoin(wa_lid_map, wa_lid_map.c.lid == wa_contacts.c.jid))
        .order_by(wa_contacts.c.updated_at.desc())
    )
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        jid = str(row.get("pn") or row["jid"] or "")
        if "@" not in jid:
            jid = f"{jid}@s.whatsapp.net"
        if not jid or jid in seen:
            continue
        name = str(row.get("name") or row.get("push_name") or "")
        if term and term not in name.casefold() and term not in jid.casefold():
            continue
        seen.add(jid)
        found.append((jid, name))
        if len(found) >= limit:
            break
    return found


def targets(db: Database, query: str = "", limit: int = LIST_LIMIT) -> list[tuple[str, str]]:
    return [*groups(db, query, limit), *contacts(db, query, limit)]


def _jid_digits(db: Database, jid) -> str:
    """Digits of a neonize JID, through `wa_lid_map` when it is a LID; "" when unusable."""
    user = str(getattr(jid, "User", "") or "")
    if not user:
        return ""
    server = str(getattr(jid, "Server", "") or "")
    raw = f"{user}@{server}" if server else user
    if raw.endswith("@lid"):
        mapped = pn_jid(db, raw)
        if mapped:
            return normalize_jid(mapped)
    return normalize_jid(raw)


def _member_digits(db: Database, item) -> str:
    """The canonical digits of one `GroupParticipant`: PN first, then JID, then LID."""
    phone = getattr(item, "PhoneNumber", None)
    digits = _jid_digits(db, phone) if phone is not None else ""
    for field_name in ("JID", "LID"):
        if digits:
            break
        value = getattr(item, field_name, None)
        digits = _jid_digits(db, value) if value is not None else ""
    return digits


def store_group(db: Database, thread_id: str, members) -> int:
    """Replace the cached membership of one group from `GroupInfo.Participants`.

    An empty or unresolvable list writes nothing: a partial transport answer never wipes a
    cache that a previous full answer filled.
    """
    thread = str(thread_id or "")
    if not thread:
        return 0
    rows: list[dict] = []
    seen: set[str] = set()
    for item in members or ():
        digits = _member_digits(db, item)
        if not digits or digits in seen:
            continue
        seen.add(digits)
        name = " ".join(str(getattr(item, "DisplayName", "") or "").split())[:200]
        rows.append(
            {
                "thread_id": thread,
                "member": digits,
                "name": name,
                "updated_at": time.time(),
            }
        )
    if not rows:
        return 0

    def merge(stmt):
        return {
            "name": func.coalesce(
                func.nullif(stmt.excluded.name, ""), wa_group_members.c.name
            ),
            "updated_at": time.time(),
        }

    with db.connect() as conn:
        conn.execute(
            wa_group_members.delete().where(wa_group_members.c.thread_id == thread)
        )
        db.upsert(conn, wa_group_members, rows, ["thread_id", "member"], update=merge)
    return len(rows)


def apply_group_event(db: Database, thread_id: str, *, joined=(), left=()) -> None:
    """Apply `GroupInfoEvent.Join` / `.Leave` deltas to the cached membership."""
    thread = str(thread_id or "")
    if not thread:
        return
    leave = [digits for digits in (_jid_digits(db, jid) for jid in left) if digits]
    join = [
        {"thread_id": thread, "member": digits, "name": "", "updated_at": time.time()}
        for digits in dict.fromkeys(_jid_digits(db, jid) for jid in joined)
        if digits
    ]
    with db.connect() as conn:
        if leave:
            conn.execute(
                wa_group_members.delete().where(
                    and_(
                        wa_group_members.c.thread_id == thread,
                        wa_group_members.c.member.in_(leave),
                    )
                )
            )
        if join:
            db.upsert(
                conn,
                wa_group_members,
                join,
                ["thread_id", "member"],
                update={
                    "name": wa_group_members.c.name,
                    "updated_at": join[0]["updated_at"],
                },
            )


def participants(db: Database, thread_id: str) -> set[str] | None:
    """Person ids the transport can prove are in the thread; `None` when it cannot.

    A direct conversation proves the person linked to the thread's address. A group proves the
    people linked to the members the transport reported (`wa_group_members`); a group with no
    cached membership stays `None`, so the core fails closed.
    """
    if not thread_id:
        return None
    rows = db.fetch_all(select(person_links).where(person_links.c.gateway == "whatsapp"))
    if thread_id.endswith("@g.us"):
        cached = db.fetch_all(
            select(wa_group_members.c.member).where(
                wa_group_members.c.thread_id == thread_id
            )
        )
        if not cached:
            return None
        members = {str(row["member"]) for row in cached}
        found = {
            str(row["person_id"])
            for row in rows
            if normalize_jid(str(row["address"] or "")) in members
        }
        return found or None
    digits = thread_id.partition("@")[0]
    found = {
        str(row["person_id"])
        for row in rows
        if str(row["address"] or "").partition("@")[0] == digits
    }
    return found if found else None


def group_name(info) -> str:
    return " ".join(str(getattr(getattr(info, "GroupName", None), "Name", "") or "").split())


async def sync_groups(
    client, db: Database, timeout_seconds: float = GROUP_LIST_TIMEOUT
) -> int:
    """Cache every joined group's name and membership; returns the named-group count."""
    getter: Callable[[], Awaitable[list]] | None = getattr(client, "get_joined_groups", None)
    if not callable(getter):
        return 0
    try:
        found = await asyncio.wait_for(getter(), timeout_seconds)
    except Exception:
        log.warning("failed to fetch the group list", exc_info=True)
        return 0
    saved = 0
    for info in found:
        if not getattr(info.JID, "User", ""):
            continue
        thread = f"{info.JID.User}@{info.JID.Server}"
        name = group_name(info)
        if name:
            set_chat_name(db, thread, name)
            saved += 1
        store_group(db, thread, getattr(info, "Participants", ()))
    return saved


def label(thread_id: str, name: str) -> str:
    return f"{name} ({thread_id})" if name else thread_id


def private_thread(db, person_id: str) -> str:
    """The one direct thread this person holds, or "" (ADR-0001 §4.5: a body fact)."""
    who = str(person_id or "").strip()
    if not who:
        return ""
    found = db.fetch_one(
        text_(
            "SELECT t.thread_id FROM threads AS t "
            "JOIN thread_people AS p ON p.thread_id = t.thread_id "
            "WHERE t.kind = 'dm' AND p.person_id = :person "
            "GROUP BY t.thread_id HAVING count(*) = 1 LIMIT 1"
        ),
        {"person": who},
    )
    return str(found["thread_id"]) if found else ""
