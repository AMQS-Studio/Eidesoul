"""Derived procedures (ADR-0020): retrieval, append-only revisions, and usage-based decay.

A skill is not a tool and never enters the prompt as an index: `retrieve()` injects at most
`max_chars` of procedures that the requester may read in this scope and whose tools the role
actually holds. Every write appends a `skill_revisions` row, so the previous version can be
restored; `global` is refused unless the source is T0 or an approval marked it.
"""


import logging
import re
import time
from dataclasses import dataclass
from pathlib import PurePosixPath

import yaml
from sqlalchemy import delete, select, update

from .. import embed
from ..db import Database
from ..guard import SENSITIVE, is_injection
from ..jobs.lease import FencedStore, Superseded, fenced_insert
from ..schema import episodes, skill_files, skill_revisions, skills

log = logging.getLogger(__name__)

NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
APP = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
MAX_CONTENT = 18_000
MAX_DESCRIPTION = 200
SCOPES = ("thread", "person", "global")
TRUST = ("T0", "T1", "T2", "T3")
DAY = 86400.0


def _clip(text: str, limit: int) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _key(app: str, name: str) -> str:
    """The physical `skills.name`: the app namespace and the logical name, never one alone."""
    namespace = str(app or "").strip().lower()
    slug = str(name or "").strip().lower()
    return f"{namespace}:{slug}" if namespace else slug


def _logical(key: str) -> str:
    """The owner-facing name behind a physical key; `app:deploy` reads back as `deploy`."""
    text = str(key or "")
    return text.split(":", 1)[1] if ":" in text else text


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Read the YAML frontmatter; a file without frontmatter is not a skill."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    try:
        data = yaml.safe_load(text[3:end]) or {}
    except yaml.YAMLError:
        return {}, text
    if not isinstance(data, dict):
        return {}, text
    return data, text[end + 4 :].lstrip("\n")


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    body: str
    scope: str = "thread"
    trust: str = "T2"
    revision: int = 1
    priority: int = 0
    uses: int = 0
    last_used: float | None = None
    stated_by: str = ""
    source_episode: str = ""
    app: str = ""


class SkillStore(FencedStore):
    def __init__(self, db: Database, config=None):
        self.db = db
        self.config = config
        self._vectors: dict[tuple[str, int], list[float]] = {}

    def _fence(self):
        """Every procedure mutation carries the live lease (ADR-0012 §4.3, ADR-0020)."""
        return self.fence_clause()

    # ------------------------------------------------------------ query

    def _stored_key(self, name: str, app: str | None = None) -> str:
        """Resolve a logical name to the physical row key; the bare key wins when it exists."""
        slug = str(name or "").strip().lower()
        if app is not None:
            return _key(app, slug)
        rows = self.db.fetch_all(
            select(skills.c.name, skills.c.archived).order_by(skills.c.name)
        )
        live = [str(row["name"]) for row in rows if not bool(row["archived"])]
        keys = live or [str(row["name"]) for row in rows]
        if slug in keys:
            return slug
        for key in keys:
            if _logical(key) == slug:
                return key
        return slug

    def _evidence_rows(self, signature: str, *, limit: int = 5) -> list[dict]:
        """The skill rows distilled from the episodes of one signature, newest first."""
        if not str(signature or ""):
            return []
        episode_ids = [
            str(row["id"])
            for row in self.db.fetch_all(
                select(episodes.c.id).where(episodes.c.signature == str(signature))
            )
        ]
        # ADR-0009 §4.5: a distilled skill may store the bare episode id or the `distill:`
        # namespace; both point at the same evidence.
        wanted = episode_ids + [f"distill:{episode_id}" for episode_id in episode_ids]
        if not wanted:
            return []
        rows = self.db.fetch_all(
            select(skills)
            .where(
                skills.c.archived.is_(False),
                skills.c.source_episode.in_(wanted),
            )
            .order_by(skills.c.updated_at.desc())
            .limit(max(1, int(limit)))
        )
        return [dict(row) for row in rows]

    def evidence_for_signature(self, signature: str, *, limit: int = 5) -> list[str]:
        """The skills distilled from the episodes of one signature, newest first."""
        return [
            _logical(str(row["name"])) for row in self._evidence_rows(signature, limit=limit)
        ]

    def evidence_skills(self, signature: str, *, limit: int = 5) -> list[Skill]:
        """The evidence rows with their app namespace, so a caller never guesses it."""
        return [self._skill_row(row) for row in self._evidence_rows(signature, limit=limit)]

    def _row_exists(self, key: str) -> bool:
        """Whether this physical key exists, regardless of the current fence generation."""
        return (
            self.db.fetch_one(select(skills.c.name).where(skills.c.name == key)) is not None
        )

    def set_priority(self, name: str, priority: int, *, app: str | None = None) -> int:
        """Set the retrieval rank of one procedure (ADR-0020 §4.6); returns the new value."""
        fence = self._fence()
        key = self._stored_key(name, app)
        statement = (
            update(skills)
            .where(skills.c.name == key)
            .values(priority=int(priority), updated_at=time.time())
        )
        if fence is not None:
            statement = statement.where(fence)
        with self.db.connect() as conn:
            result = conn.execute(statement)
        if fence is not None and not int(result.rowcount or 0) and self._row_exists(key):
            raise Superseded("lease generation moved on: skill priority refused")
        return int(priority)

    def record_notice(self, name: str, summary: str) -> int:
        """One operator-visible ledger row for a ladder action; never a chat message."""
        from ..memory.ledger import ActionLedger

        return ActionLedger(self.db).log_action(
            "skill", str(name or ""), "skill", str(summary or "")[:300]
        )

    def names(self) -> list[str]:
        rows = self.db.fetch_all(
            select(skills.c.name).where(skills.c.archived.is_(False)).order_by(skills.c.name)
        )
        return [_logical(row["name"]) for row in rows]

    def get(self, name: str, *, app: str | None = None) -> Skill | None:
        slug = str(name or "").strip().lower()
        if not slug:
            return None
        row = self.db.fetch_one(
            select(skills).where(skills.c.name == self._stored_key(slug, app))
        )
        if row is None or bool(row["archived"]):
            return None
        return self._skill_row(row)

    def catalog(self) -> str:
        """Owner-facing listing; never part of a prompt."""
        rows = self.db.fetch_all(
            select(skills.c.name, skills.c.description, skills.c.scope, skills.c.uses)
            .where(skills.c.archived.is_(False))
            .order_by(skills.c.name)
        )
        if not rows:
            return ""
        lines = [
            f"- {_logical(row['name'])} [{row['scope']}] {_clip(row['description'], 120)}"
            for row in rows
        ]
        return "Procedures:\n" + "\n".join(lines)

    # ----------------------------------------------------------- retrieval

    def _visible(
        self, skill: Skill, *, app: str, thread_id: str, person_id: str
    ) -> bool:
        """ADR-0035: app first, then the ADR-0006 audience ceiling."""
        if skill.app and skill.app != str(app or ""):
            return False
        if skill.scope == "global":
            return True
        if skill.scope == "thread":
            return not skill.stated_by or skill.stated_by == thread_id
        return bool(person_id) and skill.stated_by == person_id

    @staticmethod
    def _skill_row(row) -> Skill:
        return Skill(
            name=_logical(row["name"]),
            description=str(row["description"] or ""),
            body=str(row["body"] or ""),
            scope=str(row["scope"] or "thread"),
            trust=str(row["trust"] or "T2"),
            revision=int(row["revision"] or 1),
            priority=int(row["priority"] or 0),
            uses=int(row["uses"] or 0),
            last_used=float(row["last_used"]) if row["last_used"] is not None else None,
            stated_by=str(row["stated_by"] or ""),
            source_episode=str(row["source_episode"] or ""),
            app=str(row["app"] or ""),
        )

    def _mentions(self, body: str, known: tuple[str, ...]) -> set[str]:
        found: set[str] = set()
        for name in known:
            if re.search(rf"\b{re.escape(name)}\b", body):
                found.add(name)
        return found

    def _vector(self, skill: Skill) -> list[float]:
        key = (_key(skill.app, skill.name), skill.revision)
        found = self._vectors.get(key)
        if found is not None:
            return found
        vectors = embed.encode([f"{skill.description}\n{skill.body[:800]}"])
        vector = vectors[0] if vectors else []
        self._vectors[key] = vector
        return vector

    def retrieve(
        self,
        query: str,
        *,
        thread_id: str = "",
        person_id: str = "",
        held_tools: tuple[str, ...] = (),
        catalog_tools: tuple[str, ...] = (),
        max_chars: int = 800,
        limit: int = 3,
        app: str = "",
    ) -> str:
        """ADR-0020 §4.6: relevance inside the readable scope, bounded by `max_chars`."""
        if self.db is None or not str(query or "").strip():
            return ""
        app = str(app or "").strip().lower()
        rows = self.db.fetch_all(select(skills).where(skills.c.archived.is_(False)))
        if not rows:
            return ""
        held = set(held_tools) if held_tools else None
        known = tuple(catalog_tools)
        vector = embed.encode([query])
        if not vector:
            return ""
        scored: list[tuple[float, Skill]] = []
        for row in rows:
            skill = self._skill_row(row)
            if not self._visible(
                skill, app=app, thread_id=thread_id, person_id=person_id
            ):
                continue
            if known:
                needed = self._mentions(skill.body, known)
                if held is not None and not needed <= held:
                    continue
            skill_vector = self._vector(skill)
            if not skill_vector:
                continue
            scored.append((embed.cosine(vector[0], skill_vector), skill))
        return self._inject(scored, max_chars=max_chars, limit=limit)

    def _inject(self, scored: list[tuple[float, Skill]], *, max_chars: int, limit: int) -> str:
        # ADR-0020 §4.6: the efficiency ladder's priority outranks score, then usage.
        scored.sort(
            key=lambda item: (item[1].priority, item[0], item[1].uses), reverse=True
        )
        lines: list[str] = []
        used = 0
        for score, skill in scored[: max(1, int(limit))]:
            if score <= 0:
                continue
            block = f"### {skill.name}\n{skill.body.strip()}"
            if used + len(block) + 2 > max_chars:
                continue
            lines.append(block)
            used += len(block) + 2
            self._record_use(_key(skill.app, skill.name))
        if not lines:
            return ""
        return "Relevant procedures (reference, not permission):\n" + "\n\n".join(lines)

    # ------------------------------------------------------------- usage

    def _record_use(self, key: str) -> None:
        """Injection raises `uses`/`last_used`; the TTL renewal signal of ADR-0005."""
        now = time.time()
        fence = self._fence()
        statement = (
            update(skills)
            .where(skills.c.name == str(key))
            .values(
                uses=skills.c.uses + 1,
                last_used=now,
                updated_at=now,
            )
        )
        if fence is not None:
            statement = statement.where(fence)
        with self.db.connect() as conn:
            conn.execute(statement)

    def decay(self, *, days: float | None = None, now: float | None = None) -> int:
        """Unused procedures pass `use_renewal_days` and are archived (restorable)."""
        limit_days = float(days if days is not None else self._setting("use_renewal_days", 30))
        if limit_days <= 0:
            return 0
        moment = time.time() if now is None else float(now)
        cutoff = moment - limit_days * DAY
        fence = self._fence()
        statement = (
            update(skills)
            .where(
                skills.c.archived.is_(False),
                skills.c.trust != "T0",
                (skills.c.last_used.is_(None)) | (skills.c.last_used <= cutoff),
                skills.c.created_at <= cutoff,
            )
            .values(archived=True, updated_at=moment)
        )
        if fence is not None:
            statement = statement.where(fence)
        with self.db.connect() as conn:
            result = conn.execute(statement)
        return int(result.rowcount or 0)

    def _setting(self, key: str, default):
        return getattr(self.config, key, default) if self.config is not None else default

    # ------------------------------------------------------------- api

    def view(self, name: str, *, app: str | None = None) -> str:
        skill = self.get(name, app=app)
        if skill is None:
            available = ", ".join(self.names()) or "(none)"
            raise KeyError(f"skill not found: {name}; available: {available}")
        self._record_use(_key(skill.app, skill.name))
        return f'Skill "{skill.name}": {skill.description}\n\n{skill.body}'

    def read(self, name: str, file: str, *, app: str | None = None) -> str:
        skill = self.get(name, app=app)
        if skill is None:
            raise KeyError(f"skill not found: {name}")
        relative = str(file or "").strip()
        if not relative:
            raise ValueError("empty file")
        path = PurePosixPath(relative)
        if path.is_absolute() or ".." in path.parts:
            raise PermissionError(f"file outside the skill: {relative}")
        clean = "/".join(part for part in path.parts if part not in ("", "."))
        if not clean:
            raise ValueError("empty file")
        row = self.db.fetch_one(
            select(skill_files.c.content).where(
                skill_files.c.skill == _key(skill.app, skill.name),
                skill_files.c.path == clean,
            )
        )
        if row is None:
            raise FileNotFoundError(f"file not found: {relative}")
        content = bytes(row["content"] or b"")
        if len(content) > MAX_CONTENT:
            raise ValueError(f"file too large: {relative}")
        return f"{clean}:\n{content.decode('utf-8', errors='replace')}"

    def declared_app(self, gateway: str, hint: object = "") -> str:
        """ADR-0053 §4.1.4: a hint selects a corpus only when its gateway declares that app."""
        wanted = str(hint or "").strip().lower()
        if not wanted:
            return ""
        gateways = getattr(self.config, "gateways", None) or {}
        settings = gateways.get(str(gateway or ""))
        if settings is None:
            # A body with no host config entry (console, tests, an ad-hoc gateway) declares
            # nothing to contradict; the hint is the only namespace information available.
            return wanted
        options = getattr(settings, "options", None) or {}
        declared = str(options.get("app") or "").strip().lower()
        return wanted if declared and declared == wanted else ""

    def _check(
        self,
        name: str,
        description: str,
        body: str,
        *,
        scope: str,
        trust: str,
        stated_by: str,
        approved: bool,
        language: str,
        app: str = "",
        gateway: str = "",
    ) -> tuple[str, str, str, str, str, str]:
        slug = str(name or "").strip().lower()
        if not NAME.fullmatch(slug):
            raise ValueError("a skill name must be lowercase [a-z0-9_-], max 64 chars")
        desc = _clip(description, MAX_DESCRIPTION)
        text = str(body or "").strip()
        if not desc or not text:
            raise ValueError("description and body are required")
        limit = int(self._setting("max_body_chars", MAX_CONTENT))
        if len(text) > limit:
            raise ValueError("skill body is too large")
        if SENSITIVE.search(text) or SENSITIVE.search(desc):
            raise ValueError("a procedure never contains a secret/credential")
        if is_injection(text, language=language):
            raise ValueError("a procedure never contains an instruction to the system")
        wanted = str(scope or "thread").strip().lower()
        if wanted not in SCOPES:
            raise ValueError(f"unknown scope: {scope}")
        namespace = str(app or "").strip().lower()
        if namespace and not APP.fullmatch(namespace):
            raise ValueError(f"unknown app: {app}")
        if gateway:
            namespace = self.declared_app(gateway, namespace)
        if len(_key(namespace, slug)) > 80:
            raise ValueError("a skill name is too long for its app namespace")
        tier = str(trust or "T2").strip().upper()
        if tier not in TRUST:
            tier = "T2"
        if wanted == "global" and tier != "T0" and not approved:
            raise ValueError("global needs a T0 source or an approval (ADR-0020 §4.4)")
        return slug, desc, text, wanted, tier, namespace

    def save(
        self,
        name: str,
        description: str,
        body: str,
        *,
        overwrite: bool = False,
        scope: str = "thread",
        via: str = "command",
        stated_by: str = "",
        trust: str = "T2",
        source_episode: str = "",
        approved: bool = False,
        language: str,
        app: str = "",
        gateway: str = "",
    ) -> str:
        """Owner/import write: one row plus one append-only revision, never a silent overwrite.

        The row key is `(app, name)`; a gateway-supplied `app` is kept only when that gateway
        declares it in its configuration (ADR-0053 §4.1.4).
        """
        slug, desc, text, wanted, tier, namespace = self._check(
            name,
            description,
            body,
            scope=scope,
            trust=trust,
            stated_by=stated_by,
            approved=approved,
            language=language,
            app=app,
            gateway=gateway,
        )
        key = _key(namespace, slug)
        now = time.time()
        fence = self._fence()
        with self.db.connect() as conn:
            existing = conn.execute(
                select(skills.c.archived, skills.c.revision).where(skills.c.name == key)
            ).mappings().first()
            if existing is not None and not existing["archived"] and not overwrite:
                raise FileExistsError(f"skill already exists: {slug} (use overwrite=true)")
            revision = int(existing["revision"] or 0) + 1 if existing is not None else 1
            row = {
                "name": key,
                "description": desc,
                "body": text,
                "archived": False,
                "created_at": now,
                "updated_at": now,
                "scope": wanted,
                "app": namespace,
                "via": str(via or "command"),
                "stated_by": str(stated_by or "") or None,
                "source_episode": str(source_episode or "") or None,
                "uses": 0,
                "last_used": None,
                "revision": revision,
                "trust": tier,
            }
            changes = {
                "description": desc,
                "body": text,
                "archived": False,
                "updated_at": now,
                "scope": wanted,
                "app": namespace,
                "via": str(via or "command"),
                "stated_by": str(stated_by or "") or None,
                "source_episode": str(source_episode or "") or None,
                "revision": revision,
                "trust": tier,
            }
            statement = fenced_insert(skills, row, fence)
            # An existing live row is refused above; the conflict clause only has to replace an
            # archived row or an overwrite. A racing creator must lose with FileExistsError, so
            # the non-overwrite path uses DO NOTHING and reports the conflict instead.
            if overwrite or (existing is not None and existing["archived"]):
                statement = statement.on_conflict_do_update(
                    index_elements=["name"], set_=changes
                )
            else:
                statement = statement.on_conflict_do_nothing(index_elements=["name"])
            created = conn.execute(statement.returning(skills.c.name)).first()
            if created is None:
                found = conn.execute(
                    select(skills.c.archived, skills.c.revision).where(skills.c.name == key)
                ).mappings().first()
                # No row means the fence filtered the insert (the lease moved on); a row means
                # another writer won the race this statement was meant to lose.
                if found is None or (
                    bool(found["archived"]) and int(found["revision"] or 0) != revision
                ):
                    raise Superseded("lease generation moved on: skill write refused")
                raise FileExistsError(f"skill already exists: {slug} (use overwrite=true)")
            added = conn.execute(
                fenced_insert(
                    skill_revisions,
                    {
                        "skill": key,
                        "revision": revision,
                        "description": desc,
                        "body": text,
                        "created_at": now,
                        "source_episode": str(source_episode or "") or None,
                    },
                    fence,
                ).returning(skill_revisions.c.skill)
            ).first()
            if fence is not None and added is None:
                raise Superseded("lease generation moved on: skill revision refused")
        self._vectors.pop((key, revision - 1), None)
        return slug

    def revise(
        self,
        name: str,
        description: str,
        body: str,
        *,
        source_episode: str = "",
        scope: str = "thread",
        stated_by: str = "",
        trust: str = "T2",
        language: str,
        app: str | None = None,
    ) -> str:
        """A derived update: the new revision is appended, the old one stays readable."""
        if app is None:
            current = self.get(name)
            app = current.app if current is not None else ""
        return self.save(
            name,
            description,
            body,
            overwrite=True,
            scope=scope,
            via="reflection",
            stated_by=stated_by,
            trust=trust,
            source_episode=source_episode,
            language=language,
            app=app,
        )

    def revisions(self, name: str, *, app: str | None = None) -> list[dict]:
        rows = self.db.fetch_all(
            select(skill_revisions)
            .where(skill_revisions.c.skill == self._stored_key(name, app))
            .order_by(skill_revisions.c.revision.desc())
        )
        return [dict(row) for row in rows]

    def restore(
        self, name: str, revision: int, *, language: str, app: str | None = None
    ) -> str:
        """Roll back to a stored revision by appending it again (never a destructive update)."""
        slug = str(name or "").strip().lower()
        key = self._stored_key(slug, app)
        row = self.db.fetch_one(
            select(skill_revisions).where(
                skill_revisions.c.skill == key,
                skill_revisions.c.revision == int(revision),
            )
        )
        if row is None:
            raise KeyError(f"revision not found: {slug}@{revision}")
        current = self.get(slug, app=app)
        return self.save(
            slug,
            str(row["description"] or ""),
            str(row["body"] or ""),
            overwrite=True,
            via="command",
            source_episode=str(row["source_episode"] or ""),
            language=language,
            app=current.app if current is not None else "",
        )

    def archive(self, name: str, *, app: str | None = None) -> str:
        """Retire a procedure (reversible); the owner can restore it."""
        skill = self.get(name, app=app)
        if skill is None:
            raise KeyError(f"skill not found: {name}")
        fence = self._fence()
        key = _key(skill.app, skill.name)
        statement = (
            update(skills)
            .where(skills.c.name == key)
            .values(archived=True, updated_at=time.time())
        )
        if fence is not None:
            statement = statement.where(fence)
        with self.db.connect() as conn:
            result = conn.execute(statement)
        if fence is not None and not int(result.rowcount or 0) and self._row_exists(key):
            raise Superseded("lease generation moved on: skill archive refused")
        return skill.name

    def restore_archived(self, name: str, *, app: str | None = None) -> str:
        slug = str(name or "").strip().lower()
        key = self._stored_key(slug, app)
        fence = self._fence()
        statement = (
            update(skills)
            .where(skills.c.name == key)
            .values(archived=False, updated_at=time.time())
        )
        if fence is not None:
            statement = statement.where(fence)
        with self.db.connect() as conn:
            result = conn.execute(statement)
        if not int(result.rowcount or 0):
            if fence is not None and self._row_exists(key):
                raise Superseded("lease generation moved on: skill restore refused")
            raise KeyError(f"skill not found: {slug}")
        return slug

    def pin(self, name: str, *, app: str | None = None) -> str:
        """Only the owner pins: a T0 procedure stops decaying."""
        slug = str(name or "").strip().lower()
        key = self._stored_key(slug, app)
        fence = self._fence()
        statement = (
            update(skills)
            .where(skills.c.name == key)
            .values(trust="T0", updated_at=time.time())
        )
        if fence is not None:
            statement = statement.where(fence)
        with self.db.connect() as conn:
            result = conn.execute(statement)
        if not int(result.rowcount or 0):
            if fence is not None and self._row_exists(key):
                raise Superseded("lease generation moved on: skill pin refused")
            raise KeyError(f"skill not found: {slug}")
        return slug

    def forget(self, name: str, *, app: str | None = None) -> str:
        """Delete a procedure and its revisions; the files go with it."""
        slug = str(name or "").strip().lower()
        key = self._stored_key(slug, app)
        fence = self._fence()
        files = delete(skill_files).where(skill_files.c.skill == key)
        statement = delete(skills).where(skills.c.name == key)
        if fence is not None:
            files = files.where(fence)
            statement = statement.where(fence)
        with self.db.connect() as conn:
            conn.execute(files)
            result = conn.execute(statement)
        if not int(result.rowcount or 0):
            raise KeyError(f"skill not found: {slug}")
        return slug

    def close(self) -> None:
        """No handle needs closing; provided for lifecycle symmetry."""
