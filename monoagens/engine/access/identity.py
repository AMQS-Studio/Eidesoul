"""Person identity: one opaque `per_…` id per human, gateway addresses mapped in a table.

A gateway hands the core an opaque address; the core resolves it to a person id once, and every
store (roles, memory, affect, style, approvals) keys on that id. A second gateway attaches to the
same person through the short code `/link` hands out.
"""


import base64
import logging
import re
import secrets
import time
from collections.abc import Sequence

from sqlalchemy import delete, insert, or_, select, update
from sqlalchemy.exc import IntegrityError

from ..db import Database
from ..schema import (
    approvals,
    people,
    person_link_codes,
    person_links,
    relay_grants,
    thread_people,
)
from .permissions import normalize

log = logging.getLogger(__name__)

ID_PREFIX = "per_"
ID_PATTERN = re.compile(r"^per_[a-z2-7]{10}$")
ID_BYTES = 7
ID_CHARS = 10
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no I/O/0/1
CODE_LENGTH = 6
CODE_TTL = 600.0
CODE_ATTEMPTS = 5
CODE_RETRIES = 3
MASK_SHORT = 8
MASK_HIDDEN = 4
LINK_WINDOW = 900.0
LINK_FAILURES = 5


def new_person_id() -> str:
    """Opaque core id: `per_` plus ten base32 characters (a-z, 2-7)."""
    raw = base64.b32encode(secrets.token_bytes(ID_BYTES)).decode("ascii")
    return ID_PREFIX + raw.lower().rstrip("=")[:ID_CHARS]


def is_person_id(value: str) -> bool:
    return bool(ID_PATTERN.fullmatch(str(value or "").strip()))


def new_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def mask_address(value: str) -> str:
    """Readable address: short values stay, long ones hide the middle."""
    text = str(value or "")
    if len(text) <= MASK_SHORT:
        return text
    return f"{text[:3]}{'•' * MASK_HIDDEN}{text[-4:]}"


def _blank(value: object) -> bool:
    """A field that carries no information yet: empty text/list, or a zero number."""
    if value is None:
        return True
    if isinstance(value, bool):
        return not value
    if isinstance(value, (int, float)):
        return value == 0
    if isinstance(value, (str, list, tuple, dict, set)):
        return not value
    return False


class LinkThrottle:
    """Failed `/link` code attempts per (actor, thread) in a process-local window.

    The window is per process because a link code is a short-lived secret: five wrong attempts in
    fifteen minutes close the path even when the codes themselves do not exist.
    """

    def __init__(self, *, limit: int = LINK_FAILURES, window: float = LINK_WINDOW) -> None:
        self.limit = max(1, int(limit))
        self.window = max(1.0, float(window))
        self._failed: dict[tuple[str, str], list[float]] = {}

    @staticmethod
    def _key(actor: str, thread: str) -> tuple[str, str]:
        return (str(actor or ""), str(thread or ""))

    def _recent(self, key: tuple[str, str], now: float) -> list[float]:
        found = [item for item in self._failed.get(key, ()) if now - item < self.window]
        if found:
            self._failed[key] = found
        else:
            self._failed.pop(key, None)
        return found

    def blocked(self, actor: str, thread: str, *, now: float | None = None) -> bool:
        moment = time.time() if now is None else float(now)
        return len(self._recent(self._key(actor, thread), moment)) >= self.limit

    def fail(self, actor: str, thread: str, *, now: float | None = None) -> None:
        moment = time.time() if now is None else float(now)
        key = self._key(actor, thread)
        found = self._recent(key, moment)
        found.append(moment)
        self._failed[key] = found

    def clear(self, actor: str, thread: str) -> None:
        self._failed.pop(self._key(actor, thread), None)


class IdentityStore:
    """`people` holds the person, `person_links` maps a gateway address to it."""

    def __init__(self, db: Database, *, roles=None) -> None:
        self.db = db
        # Optional role registry: a merge must never fold two people whose roles differ.
        self.roles = roles
        self.link_throttle = LinkThrottle()

    # ---------------------------------------------------------------- read

    def person(self, person_id: str) -> dict | None:
        pid = str(person_id or "").strip()
        if not pid:
            return None
        return self.db.fetch_one(select(people).where(people.c.person_id == pid))

    def links(self, person_id: str) -> list[dict]:
        pid = str(person_id or "").strip()
        if not pid:
            return []
        return self.db.fetch_all(
            select(person_links)
            .where(person_links.c.person_id == pid)
            .order_by(person_links.c.gateway)
        )

    def legacy_count(self) -> int:
        """Rows in `people` whose id is a raw address, not a `per_…` id."""
        rows = self.db.fetch_all(select(people.c.person_id))
        return sum(1 for row in rows if not is_person_id(str(row["person_id"])))

    # ------------------------------------------------------------- resolve

    def resolve(self, gateway: str, address: str) -> str:
        """Person id for a gateway address, linking a fresh `people` row on first sight."""
        name = str(gateway or "").strip()
        value = str(address or "").strip()
        if not value:
            raise ValueError("address is empty")
        # ADR-0001 §4.1: a resumed turn hands back a `per_…` id; keep it instead of minting a
        # second person (and a `person_links` row keyed on the opaque id).
        if value.startswith(ID_PREFIX) and self.person(value) is not None:
            return value
        found = self._link(name, value)
        if found is not None:
            self._touch(name, value)
            return str(found["person_id"])
        error: IntegrityError | None = None
        for _ in range(CODE_RETRIES):
            now = time.time()
            person_id = new_person_id()
            try:
                with self.db.connect() as conn:
                    conn.execute(insert(people).values(person_id=person_id, name=""))
                    conn.execute(
                        insert(person_links).values(
                            gateway=name,
                            address=value,
                            person_id=person_id,
                            created_at=now,
                            seen_at=now,
                            linked_by="auto",
                        )
                    )
                return person_id
            except IntegrityError as exc:  # another instance won the race
                error = exc
                found = self._link(name, value)
                if found is not None:
                    self._touch(name, value)
                    return str(found["person_id"])
        log.error("identity: no person id could be allocated gateway=%s", name)
        raise error if error is not None else RuntimeError("identity: resolve failed")

    # ---------------------------------------------------------------- link

    def link(self, person_id: str, gateway: str, address: str, *, actor: str = "") -> str:
        """Status: ok (inserted or already this person), taken, or missing."""
        pid = str(person_id or "").strip()
        name = str(gateway or "").strip()
        value = str(address or "").strip()
        if not pid or not name or not value or self.person(pid) is None:
            return "missing"
        found = self._link(name, value)
        if found is not None:
            if str(found["person_id"]) != pid:
                return "taken"
            self._touch(name, value)
            return "ok"
        now = time.time()
        try:
            self._insert(name, value, pid, actor=actor or "admin", now=now)
        except IntegrityError:
            found = self._link(name, value)
            return "ok" if found is not None and str(found["person_id"]) == pid else "taken"
        return "ok"

    def unlink(
        self, person_id: str, gateway: str, address: str = ""
    ) -> bool:
        """Remove a link and revoke everything that kept acting for that address.

        A pending approval addressed to the gateway thread, or created by this person on that
        gateway, is marked `revoked`; an active relay grant involving the person is revoked too.
        The identity row itself stays (it may still hold other gateway links).
        """
        pid = str(person_id or "").strip()
        name = str(gateway or "").strip()
        if not pid or not name:
            return False
        condition = (person_links.c.person_id == pid) & (person_links.c.gateway == name)
        value = str(address or "").strip()
        if value:
            condition = condition & (person_links.c.address == value)
        with self.db.connect() as conn:
            addresses = [
                str(row)
                for row in conn.execute(select(person_links.c.address).where(condition)).scalars()
            ]
            if not addresses:
                return False
            conn.execute(delete(person_links).where(condition))
            self._revoke_for_identity(conn, pid, name, addresses, now=time.time())
            return True

    def _revoke_for_identity(
        self,
        conn,
        person_id: str,
        gateway: str,
        addresses: Sequence[str],
        *,
        now: float,
    ) -> None:
        """Close pending approvals and relay grants tied to the unlinked identity."""
        threads = tuple(dict.fromkeys(str(item) for item in addresses if str(item)))
        pending = (approvals.c.status == "pending") & (approvals.c.gateway == gateway)
        if threads:
            pending = pending & (
                (approvals.c.sender == person_id) | approvals.c.thread_id.in_(threads)
            )
        else:
            pending = pending & (approvals.c.sender == person_id)
        conn.execute(
            update(approvals)
            .where(pending)
            .values(
                status="revoked",
                decided_by=person_id,
                decided_at=now,
                result="identity unlinked",
            )
        )
        conn.execute(
            update(relay_grants)
            .where(
                relay_grants.c.revoked_at.is_(None),
                or_(
                    relay_grants.c.from_person == person_id,
                    relay_grants.c.to_person == person_id,
                ),
            )
            .values(revoked_at=now)
        )

    # --------------------------------------------------------------- codes

    def start_code(self, person_id: str, gateway: str, *, now: float | None = None) -> str:
        """Six characters valid for ten minutes on that one gateway."""
        pid = str(person_id or "").strip()
        name = str(gateway or "").strip()
        if not pid or not name:
            raise ValueError("person_id and gateway are required")
        moment = time.time() if now is None else float(now)
        with self.db.connect() as conn:
            conn.execute(
                delete(person_link_codes).where(
                    (person_link_codes.c.person_id == pid)
                    & (person_link_codes.c.gateway == name)
                    & (person_link_codes.c.used_at == 0.0)
                )
            )
        for _ in range(CODE_RETRIES):
            code = new_code()
            if self._code(code) is not None:
                continue
            try:
                with self.db.connect() as conn:
                    conn.execute(
                        insert(person_link_codes).values(
                            code=code,
                            person_id=pid,
                            gateway=name,
                            created_at=moment,
                            expires_at=moment + CODE_TTL,
                            attempts=0,
                            used_at=0.0,
                        )
                    )
                return code
            except IntegrityError:
                continue
        raise RuntimeError("identity: no link code could be allocated")

    def code_target(self, code: str) -> str:
        """Identity the code was issued for; "" when the code is unknown."""
        row = self._code(str(code or "").strip().upper())
        return "" if row is None else str(row["person_id"])

    def finish_code(
        self,
        code: str,
        *,
        person_id: str,
        gateway: str,
        address: str,
        now: float | None = None,
    ) -> tuple[str, str]:
        """Consume a code: status plus the provisional person folded into the code's identity.

        The code row and the address row are locked in one transaction, in that order, so two
        simultaneous redemptions cannot both consume the code or both move the address. The code,
        not the sender, names the identity that survives: the new gateway arrives with a
        provisional identity of its own, and that one is folded in.
        """
        moment = time.time() if now is None else float(now)
        raw = str(code or "").strip().upper()
        pid = str(person_id or "").strip()
        name = str(gateway or "").strip()
        value = str(address or "").strip()
        if not raw or not value:
            return ("missing", "")
        with self.db.connect() as conn:
            row = conn.execute(
                select(person_link_codes)
                .where(person_link_codes.c.code == raw)
                .with_for_update()
            ).mappings().first()
            row = None if row is None else dict(row)
            status = self._code_status(
                conn, row, raw, moment=moment, person_id=pid, gateway=name
            )
            if status or row is None:
                return (status or "missing", "")
            conn.execute(
                select(person_links)
                .where(
                    (person_links.c.gateway == name) & (person_links.c.address == value)
                )
                .with_for_update()
            )
            merged_from, status = self._attach(
                conn, name, value, str(row["person_id"])
            )
            if status:
                return (status, "")
            conn.execute(
                update(person_link_codes)
                .where(person_link_codes.c.code == raw)
                .values(used_at=moment)
            )
        return ("ok", merged_from)

    def _code_status(
        self,
        conn,
        row: dict | None,
        raw: str,
        *,
        moment: float,
        person_id: str,
        gateway: str,
    ) -> str:
        """Why this code cannot be used yet, or "" when it can."""
        if row is None:
            return "missing"
        if float(row["used_at"] or 0.0) > 0.0:
            return "used"
        if moment > float(row["expires_at"]):
            return "expired"
        if int(row["attempts"] or 0) >= CODE_ATTEMPTS:
            return "too_many"
        if str(row["gateway"]) != gateway:
            self._bump(conn, raw)
            return "wrong_gateway"
        owner = str(row["person_id"])
        if person_id != owner and len(self._links_of(conn, person_id)) > 1:
            # an established identity cannot be folded into another one by typing a code
            self._bump(conn, raw)
            return "wrong_person"
        if self._person_row(conn, owner) is None:
            return "wrong_person"
        return ""

    def _attach(
        self, conn, gateway: str, address: str, person_id: str
    ) -> tuple[str, str]:
        """Point the address at this person; returns (merged_from, status)."""
        other = self._link_row(conn, gateway, address)
        if other is None:
            try:
                with conn.begin_nested():
                    conn.execute(
                        insert(person_links).values(
                            gateway=gateway,
                            address=address,
                            person_id=person_id,
                            created_at=time.time(),
                            seen_at=time.time(),
                            linked_by="link",
                        )
                    )
            except IntegrityError:
                found = self._link_row(conn, gateway, address)
                mine = found is not None and str(found["person_id"]) == person_id
                return ("", "" if mine else "taken")
            return ("", "")
        owner = str(other["person_id"])
        if owner == person_id:
            self._touch_row(conn, gateway, address)
            return ("", "")
        if len(self._links_of(conn, owner)) > 1:
            return ("", "taken")
        if self._roles_conflict(conn, owner, person_id):
            return ("", "role_conflict")
        self.merge(owner, person_id, conn=conn)
        self._move_row(conn, gateway, address, person_id)
        return (owner, "")

    def _roles_conflict(self, conn, left: str, right: str) -> bool:
        """Both identities carry explicit roles, and those role sets differ."""
        if self.roles is None:
            return False
        first = self._role_names(conn, left)
        second = self._role_names(conn, right)
        return bool(first and second and first != second)

    def _role_names(self, conn, person_id: str) -> frozenset[str]:
        """Roles that name this identity or any address linked to it."""
        roles = self.roles
        if roles is None:
            return frozenset()
        named = {normalize(person_id)}
        named.update(
            normalize(str(row["address"])) for row in self._links_of(conn, person_id)
        )
        return frozenset(
            role.name
            for role in roles.by_name.values()
            if any(normalize(user) in named for user in role.users)
        )

    # --------------------------------------------------------------- merge

    def merge(self, old: str, new: str, *, conn=None) -> None:
        """Fold a provisional identity into `new`; the style blob is merged by the caller.

        A legacy id may own rows without a `people` row, so the fold runs on every table and
        deleting the missing row is a no-op.
        """
        if conn is None:
            with self.db.connect() as own:
                self.merge(old, new, conn=own)
            return
        source = str(old or "").strip()
        target = str(new or "").strip()
        if not source or not target or source == target:
            return
        current = {
            str(row["thread_id"]): row
            for row in conn.execute(
                select(thread_people).where(thread_people.c.person_id == target)
            ).mappings()
        }
        rows = conn.execute(
            select(thread_people).where(thread_people.c.person_id == source)
        ).mappings().all()
        for row in rows:
            chat = str(row["thread_id"])
            known = current.get(chat)
            merged = {
                "thread_id": chat,
                "person_id": target,
                "last_seen": max(
                    float(row["last_seen"] or 0.0),
                    float(known["last_seen"]) if known else 0.0,
                ),
                "interactions": int(row["interactions"] or 0)
                + (int(known["interactions"]) if known else 0),
            }
            self.db.upsert(conn, thread_people, [merged], ["thread_id", "person_id"])
        conn.execute(delete(thread_people).where(thread_people.c.person_id == source))
        conn.execute(
            update(person_links)
            .where(person_links.c.person_id == source)
            .values(person_id=target)
        )
        conn.execute(
            delete(person_link_codes).where(
                (person_link_codes.c.person_id == source)
                & (person_link_codes.c.used_at == 0.0)
            )
        )
        self._carry(conn, source, target)
        conn.execute(delete(people).where(people.c.person_id == source))

    @staticmethod
    def _carry(conn, source: str, target: str) -> None:
        """Fill the target's empty fields from the source row before deleting it."""
        held = conn.execute(select(people).where(people.c.person_id == source)).mappings().first()
        kept = conn.execute(select(people).where(people.c.person_id == target)).mappings().first()
        if held is None or kept is None:
            return
        values = {
            column.name: (
                held[column.name] if _blank(kept[column.name]) else kept[column.name]
            )
            for column in people.columns
            if column.name != "person_id"
        }
        conn.execute(update(people).where(people.c.person_id == target).values(**values))

    # ------------------------------------------------------------- helpers

    def _person_row(self, conn, person_id: str) -> dict | None:
        row = conn.execute(
            select(people).where(people.c.person_id == str(person_id or "").strip())
        ).mappings().first()
        return None if row is None else dict(row)

    def _links_of(self, conn, person_id: str) -> list[dict]:
        return [
            dict(row)
            for row in conn.execute(
                select(person_links).where(
                    person_links.c.person_id == str(person_id or "").strip()
                )
            ).mappings()
        ]

    def _link_row(self, conn, gateway: str, address: str) -> dict | None:
        row = conn.execute(
            select(person_links).where(
                (person_links.c.gateway == gateway) & (person_links.c.address == address)
            )
        ).mappings().first()
        return None if row is None else dict(row)

    def _link(self, gateway: str, address: str) -> dict | None:
        return self.db.fetch_one(
            select(person_links).where(
                (person_links.c.gateway == gateway) & (person_links.c.address == address)
            )
        )

    def _insert(
        self, gateway: str, address: str, person_id: str, *, actor: str, now: float
    ) -> None:
        with self.db.connect() as conn:
            conn.execute(
                insert(person_links).values(
                    gateway=gateway,
                    address=address,
                    person_id=person_id,
                    created_at=now,
                    seen_at=now,
                    linked_by=actor,
                )
            )

    def _touch(self, gateway: str, address: str, now: float | None = None) -> None:
        moment = time.time() if now is None else float(now)
        with self.db.connect() as conn:
            self._touch_row(conn, gateway, address, now=moment)

    @staticmethod
    def _touch_row(conn, gateway: str, address: str, *, now: float | None = None) -> None:
        moment = time.time() if now is None else float(now)
        conn.execute(
            update(person_links)
            .where((person_links.c.gateway == gateway) & (person_links.c.address == address))
            .values(seen_at=moment)
        )

    def _move_row(
        self, conn, gateway: str, address: str, person_id: str, *, now: float | None = None
    ) -> None:
        self._touch_row(conn, gateway, address, now=now)
        conn.execute(
            update(person_links)
            .where((person_links.c.gateway == gateway) & (person_links.c.address == address))
            .values(person_id=person_id)
        )

    def _code(self, code: str) -> dict | None:
        if not code:
            return None
        row = self.db.fetch_one(
            select(person_link_codes).where(person_link_codes.c.code == code)
        )
        return None if row is None else dict(row)

    @staticmethod
    def _bump(conn, code: str) -> None:
        conn.execute(
            update(person_link_codes)
            .where(person_link_codes.c.code == code)
            .values(attempts=person_link_codes.c.attempts + 1)
        )
