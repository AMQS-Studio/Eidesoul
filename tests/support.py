"""Shared fixture builder for tests; not runtime behavior."""


import hashlib
import math
import os
import re
import uuid
from dataclasses import replace
from pathlib import Path

from eidesoul.engine.access.roles import Role, RoleRegistry
from eidesoul.engine.config import (
    AffectConfig,
    ApprovalConfig,
    Config,
    DatabaseConfig,
    HumanizeConfig,
    LLMConfig,
    Paths,
    SandboxConfig,
    StyleConfig,
)
from eidesoul.engine.db import Database
from eidesoul.engine.inbound import CoreDefaults
from eidesoul.engine.memory import MemoryContext, MemoryHit
from eidesoul.engine.soul import Soul, SoulStyle

EMBED_DIM = 384
TOKEN = re.compile(r"[0-9a-z]+")


def test_dsn() -> str:
    """The one test database; unset is a loud failure from conftest, never a fallback."""
    return os.environ["EIDESOL_TEST_DATABASE_URL"]


def stub_vector(text: str) -> list[float]:
    """Deterministic 384-dim bag-of-token-hashes vector: no model, no network (ADR-0029 §4.3)."""
    vector = [0.0] * EMBED_DIM
    for token in TOKEN.findall(str(text or "").lower()):
        digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
        index = int.from_bytes(digest[:4], "big") % EMBED_DIM
        vector[index] += 1.0 if digest[4] % 2 else -1.0
    norm = math.sqrt(sum(value * value for value in vector))
    return [value / norm for value in vector] if norm else vector


class StubEncoder:
    """The embedder every test sees: the engine's `.encode` and Mem0's `.embed` contracts."""

    def encode(self, texts, *, convert_to_numpy=True, show_progress_bar=False, **kwargs):
        return [stub_vector(text) for text in texts]

    def embed(self, text, memory_action=None):
        return stub_vector(text)

    def embed_batch(self, texts, memory_action=None):
        return [stub_vector(text) for text in texts]


def role(name: str, **kwargs) -> Role:
    return Role(name=name, **kwargs)


def default_soul(**overrides) -> Soul:
    """Fixture soul: the shipped packs stay selected so agent tests keep real data."""
    body = {
        "name": "Eidesoul",
        "persona": "p",
        "style": SoulStyle(
            patterns=("jawatimur",),
            facts=("indonesia",),
            max_patterns=2,
            max_facts=3,
        ),
    }
    body.update(overrides)
    return Soul(**body)


def build_config(
    root: str | Path,
    *,
    roles: dict[str, Role] | None = None,
    default_role: str = "member",
    workspace: str | Path | None = None,
    soul: Soul | None = None,
    database_config: DatabaseConfig | None = None,
    approval: ApprovalConfig | None = None,
    **overrides,
) -> Config:
    base = Path(root)
    role_map = dict(roles or {"member": Role("member")})
    if not role_map:
        raise ValueError("roles kosong")
    if default_role not in role_map:
        default_role = "member" if "member" in role_map else next(iter(role_map))
    workspace_path = Path(workspace) if workspace is not None else base / "workspace"
    return Config(
        paths=Paths(
            config=base / "config.yaml",
            roles=base / "roles.yaml",
            soul_yaml=base / "soul.yaml",
            workspace=workspace_path,
        ),
        database=database_config or DatabaseConfig(url=test_dsn(), url_env=""),
        documents=None,
        llm=LLMConfig(base_url="http://x", model="m", api_key_env="K"),
        workspace=workspace_path,
        approval=approval or ApprovalConfig(),
        roles=RoleRegistry(by_name=role_map, default=default_role),
        default_role=default_role,
        gateways={},
        soul=soul or default_soul(),
        sandbox=SandboxConfig(),
        mcp={},
        **overrides,
    )


def database() -> Database:
    """The session-migrated Postgres store; conftest truncates it before every test."""
    return Database(test_dsn())


class FakeMem0:
    """Fake AsyncMemory: just enough for the adapter contract (not Mem0 behavior)."""

    available = True

    def __init__(self) -> None:
        self.items: dict[str, dict] = {}
        self.calls: list[tuple[str, dict]] = []
        self.counter = 0

    @staticmethod
    def _match(item: dict, filters: dict | None) -> bool:
        if not filters:
            return True
        return all(item.get(key) == value for key, value in filters.items())

    async def add(self, messages, *, metadata=None, infer=True, **identity):
        self.calls.append(
            ("add", {"messages": messages, "metadata": metadata, "infer": infer, **identity})
        )
        if isinstance(messages, list):
            text = " ".join(str(item.get("content") or "") for item in messages)
        else:
            text = str(messages)
        self.counter += 1
        item = {
            "id": f"m{self.counter}",
            "memory": text,
            "metadata": dict(metadata or {}),
            "created_at": 1_700_000_000 + len(self.items),
            **identity,
        }
        self.items[item["id"]] = item
        return {"results": [item]}

    async def search(self, query, *, top_k=20, filters=None, **_kwargs):
        self.calls.append(("search", {"query": query, "filters": filters, "top_k": top_k}))
        found = [dict(item) for item in self.items.values() if self._match(item, filters)]
        return {"results": found[:top_k]}

    async def get_all(self, *, filters=None, top_k=20):
        self.calls.append(("get_all", {"filters": filters, "top_k": top_k}))
        found = [dict(item) for item in self.items.values() if self._match(item, filters)]
        return {"results": found[:top_k]}

    async def get(self, memory_id):
        return dict(self.items[memory_id]) if memory_id in self.items else None

    async def delete(self, memory_id):
        self.items.pop(memory_id, None)
        return {"message": "deleted"}

    async def update(self, memory_id, *, metadata=None, **_kwargs):
        self.calls.append(("update", {"id": memory_id, "metadata": metadata}))
        item = self.items.get(memory_id)
        if item is None:
            return {"message": "not found"}
        if isinstance(metadata, dict):
            item["metadata"] = dict(metadata)
        return item

    async def close(self):
        return None


class FakeGateway(CoreDefaults):
    """Core only: text delivery and the catalog, no media and no directory (ADR-0018 §4.1)."""

    name = "fake"
    backlog_seconds = 0.0

    def __init__(self, *, name: str = "fake", backlog_seconds: float = 0.0, tools=(), commands=()):
        self.name = name
        self.backlog_seconds = backlog_seconds
        self.sent: list[tuple[str, str, object]] = []
        self._tools = list(tools)
        self._commands = list(commands)

    def tools(self):
        return list(self._tools)

    def commands(self):
        return list(self._commands)

    async def run(self, agent):
        return None

    async def stop(self):
        return None

    async def send_text(self, thread: str, text: str, *, reply=None) -> str:
        self.sent.append((thread, text, reply))
        return f"msg-{len(self.sent)}"

    def render(self, text: str) -> str:
        return str(text or "")

    def prompt_hints(self) -> str:
        return "## Fake interface"

    def self_handles(self) -> set[str]:
        return {"628567770837"}

    def chat_name(self, thread: str) -> str:
        return "Chat"


class RichFakeGateway(FakeGateway):
    """Core + `Rich`: records files, stickers, typing, and media preparation."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.files: list[tuple[str, Path, str]] = []
        self.stickers: list[tuple[str, str]] = []
        self.typing_events: list[tuple[str, bool]] = []
        self.media: list[tuple[str, object]] = []

    async def send_file(self, thread: str, path: Path, caption: str = "", *, reply=None) -> str:
        self.files.append((thread, Path(path), caption))
        return f"file-{len(self.files)}"

    async def send_sticker(self, thread: str, message_id: str):
        self.stickers.append((thread, message_id))
        return "(sticker: x)"

    async def typing(self, thread: str, composing: bool) -> None:
        self.typing_events.append((thread, composing))

    async def prepare_media(self, thread: str, message) -> None:
        self.media.append((thread, message.id))


class DirectoryFakeGateway(FakeGateway):
    """Core + `Directory`: target resolution and membership evidence."""

    def __init__(self, *, targets=None, members=None, **kwargs):
        super().__init__(**kwargs)
        self.targets: dict[str, tuple[str, str]] = dict(targets or {})
        self.members = members

    def resolve_target(self, query: str):
        target = self.targets.get(str(query or "").strip().casefold())
        return (target, []) if target else (None, [])

    def participants(self, thread: str) -> set[str] | None:
        return self.members(thread) if callable(self.members) else self.members


class RichDirectoryFakeGateway(RichFakeGateway):
    """Core + `Rich` + `Directory`: the default agent fixture."""

    resolve_target = DirectoryFakeGateway.resolve_target
    participants = DirectoryFakeGateway.participants

    def __init__(self, *, targets=None, members=None, **kwargs):
        super().__init__(**kwargs)
        self.targets: dict[str, tuple[str, str]] = dict(targets or {})
        self.members = members


class FakeMemory:
    """Fake MemoryBackend for agent tests (no Mem0)."""

    available = True
    reason = ""

    def __init__(self, block: str = "") -> None:
        self.block = block
        self.context_calls: list[dict] = []
        self.captures: list[dict] = []
        self.added: list[dict] = []
        self.hits: dict[str, MemoryHit] = {}

    async def add(self, text: str, **kwargs):
        self.added.append({"text": text, **kwargs})
        key = f"m{len(self.added)}"
        self.hits[key] = MemoryHit(
            id=key,
            text=text,
            scope=kwargs.get("scope", "person"),
            person_id=kwargs.get("person_id", ""),
            thread_id=kwargs.get("thread_id", ""),
            level=kwargs.get("level", 2),
            owner_id=kwargs.get("owner_id", ""),
            subject_id=kwargs.get("subject_id", ""),
            confirmed=kwargs.get("confirmed", True),
        )
        return [key]

    async def capture(self, messages, **kwargs):
        self.captures.append({"messages": messages, **kwargs})
        text = " ".join(str(item.get("content") or "") for item in messages).strip()
        key = f"c{len(self.captures)}"
        self.hits[key] = MemoryHit(
            id=key,
            text=text,
            scope="person",
            person_id=kwargs.get("person_id", ""),
            thread_id=kwargs.get("thread_id", ""),
            level=kwargs.get("level", 2),
            owner_id=kwargs.get("owner_id", ""),
            subject_id=kwargs.get("subject_id", ""),
            confirmed=kwargs.get("confirmed", True),
        )
        return [key]

    async def search(self, query: str, **kwargs):
        return list(self.hits.values())

    async def list(self, **kwargs):
        return list(self.hits.values())

    async def get(self, memory_id: str):
        return self.hits.get(memory_id)

    async def forget(self, memory_id: str, **kwargs):
        return self.hits.pop(memory_id, None) is not None

    async def set_privacy(self, memory_id: str, **kwargs):
        hit = self.hits.get(memory_id)
        if hit is None:
            return False
        self.hits[memory_id] = replace(
            hit,
            level=hit.level if kwargs.get("level") is None else kwargs["level"],
            confirmed=(
                hit.confirmed if kwargs.get("confirmed") is None else bool(kwargs["confirmed"])
            ),
            subject_id=(
                hit.subject_id if kwargs.get("subject_id") is None else kwargs["subject_id"]
            ),
            vetoed=hit.vetoed if kwargs.get("vetoed") is None else bool(kwargs["vetoed"]),
        )
        return True

    async def veto(self, memory_id: str, *, subject_id: str, active: bool = True):
        hit = self.hits.get(memory_id)
        if hit is None or hit.subject_id != subject_id:
            return False
        return await self.set_privacy(memory_id, vetoed=active)

    async def context(self, **kwargs):
        self.context_calls.append(kwargs)
        return MemoryContext(text=self.block, hits=tuple(self.hits.values()))

    async def close(self):
        return None


class FakeLLM:
    def __init__(self, answer: str = "halo juga", decision: dict | None = None):
        self.answer = answer
        self.decision = decision or {
            "addressed": "me",
            "act": "reply",
            "mode": "daily",
            "tone": "neutral",
            "bubbles": 1,
            "appraisal": {"label": "neutral", "dP": 0.0, "dA": 0.0, "dD": 0.0, "note": ""},
            "relationship": {"trust": 0.0, "affection": 0.0, "respect": 0.0, "annoyance": 0.0},
            "reason": "test",
        }
        self.system = ""
        self.ctx = None
        self.calls = 0
        self.decisions = 0
        self.sessions: list[str] = []

    async def complete(self, system, user, **kwargs):
        import json

        self.decisions += 1
        return json.dumps(self.decision)

    async def run(self, system, content, specs, ctx, session=None, **kwargs):
        self.system = system
        self.ctx = ctx
        self.calls += 1
        self.sessions.append(session or "")
        return self.answer

    async def digest(self, chat_name, transcript, topic, people):
        return {"topic": "", "keywords": [], "unresolved": [], "facts": []}

    async def research_summary(self, query, results):
        return ""


def make_agent(
    tmp_path,
    *,
    answer: str = "halo juga",
    decision: dict | None = None,
    roles: dict[str, Role] | None = None,
    default_role: str = "member",
    memory=None,
    gateway=None,
    gateways=None,
    affect: bool = False,
    humanize=None,
    approvals=None,
    soul=None,
    currents=None,
):
    """Build a full Agent on the test store with the rich directory fake."""
    from types import SimpleNamespace

    from eidesoul.engine.access.approvals import ApprovalStore
    from eidesoul.engine.agent import Agent
    from eidesoul.engine.memory.ledger import ActionLedger
    from eidesoul.engine.stores.affect import AffectStore
    from eidesoul.engine.stores.history import HistoryStore
    from eidesoul.engine.stores.recipes import RecipeStore
    from eidesoul.engine.stores.skills import SkillStore
    from eidesoul.engine.stores.style import StyleStore
    from eidesoul.engine.turn.context import WorkingMemory

    db = database()
    config = build_config(
        tmp_path,
        roles=roles
        or {
            "member": Role(
                "member",
                tools=("read", "memory", "chat_history", "recipe", "send"),
                commands=("*",),
            )
        },
        default_role=default_role,
        soul=soul,
        affect=AffectConfig(enabled=affect),
        style=StyleConfig(enabled=False),
        humanize=humanize or HumanizeConfig(enabled=False),
        approval=approvals or ApprovalConfig(),
    )
    config.workspace.mkdir(parents=True, exist_ok=True)
    (config.workspace / "public").mkdir(parents=True, exist_ok=True)
    (config.workspace / "scratch").mkdir(parents=True, exist_ok=True)
    gateway = gateway or RichDirectoryFakeGateway()
    history = HistoryStore(db, config.workspace, search=config.search)
    windows = WorkingMemory(store=history)
    ledger = ActionLedger(db)
    skills = SkillStore(db, config.skills)
    recipes = RecipeStore(db)
    affect_store = AffectStore(db, config.affect, soul=config.soul.persona) if affect else None
    style = StyleStore(db, config.style) if config.style.enabled else None
    memory = memory if memory is not None else FakeMemory()
    llm = FakeLLM(answer=answer, decision=decision)
    agent = Agent(
        config,
        db,
        llm=llm,
        hub=None,
        windows=windows,
        history=history,
        memory=memory,
        ledger=ledger,
        skills=skills,
        recipes=recipes,
        affect=affect_store,
        style=style,
        currents=currents,
        gateways=tuple(gateways) if gateways is not None else (gateway,),
        approvals=ApprovalStore(db, config.approval),
    )
    agent.lease = None
    agent.mode = "primary"
    return SimpleNamespace(
        agent=agent,
        gateway=gateway,
        config=config,
        db=db,
        llm=llm,
        history=history,
        windows=windows,
        memory=memory,
        ledger=ledger,
        skills=skills,
        recipes=recipes,
        affect=affect_store,
        style=style,
    )


def inbound_message(
    text: str,
    *,
    thread: str = "chat",
    dm: bool = True,
    sender: str = "628111",
    sender_name: str = "Budi",
    person_id: str = "628111",
    mention: bool = False,
    reply: bool = False,
    role_override: tuple[str, ...] = (),
    message_id: str = "",
):
    from datetime import datetime

    from eidesoul.engine.inbound import MESSAGE, Percept
    from eidesoul.engine.turn.context import Message

    hints: dict[str, object] = {"dm": dm, "mention": mention, "reply": reply}
    if role_override:
        hints["roles"] = role_override
    return Percept(
        source="fake",
        thread=thread,
        actor=person_id or sender,
        kind=MESSAGE,
        payload=Message(
            id=message_id or f"m-{uuid.uuid4().hex[:12]}",
            sender_name=sender_name,
            sender_id=sender,
            when=datetime.now(),
            text=text,
            sender_address=f"{sender}@s.whatsapp.net",
        ),
        hints=hints,
    )


def make_runtime(
    tmp_path,
    monkeypatch,
    *,
    engine_extra: str = "",
    role_tools='["*"]',
    settings_approval: bool = True,
):
    """Bootstrap like `eidesoul run` but without a real gateway/LLM."""
    from types import SimpleNamespace

    from eidesoul.engine.access.approvals import ApprovalStore
    from eidesoul.engine.agent import Agent
    from eidesoul.engine.config import load_config, load_host
    from eidesoul.engine.memory.ledger import ActionLedger
    from eidesoul.engine.stores.documents import DocumentStore
    from eidesoul.engine.stores.history import HistoryStore
    from eidesoul.engine.stores.recipes import RecipeStore
    from eidesoul.engine.stores.skills import SkillStore
    from eidesoul.engine.turn.context import WorkingMemory

    base = Path(tmp_path)
    monkeypatch.setenv("K", "x")
    monkeypatch.setenv("EIDESOUL_DATABASE_URL", test_dsn())
    (base / "config.yaml").write_text(
        "database:\n  url_env: EIDESOUL_DATABASE_URL\nworkspace: .eidesoul/workspace\n"
        "roles_file: roles.yaml\nsoul_file: soul.yaml\n"
        "engine:\n"
        "  llm:\n    provider: custom\n    base_url: http://x\n    api_key_env: K\n    model: m\n"
        "  default_role: member\n"
        "  approval:\n"
        f"    mode: yolo\n    settings: {str(settings_approval).lower()}\n"
        f"{engine_extra}"
        "gateways: {}\n",
        encoding="utf-8",
    )
    (base / "roles.yaml").write_text(
        "roles:\n"
        f"  member:\n    name: member\n    tools: {role_tools}\n    commands: [\"*\"]\n",
        encoding="utf-8",
    )
    (base / "soul.yaml").write_text(
        "name: Eidesoul\nlanguage: id\npronouns: aku\npersona: Kamu Eidesoul yang hidup di chat.\n",
        encoding="utf-8",
    )

    _host, paths = load_host(base / "config.yaml")
    db = database()
    documents = DocumentStore(db, paths)
    documents.seed()
    config = load_config(paths.config, documents=documents, require_key=False)
    config.workspace.mkdir(parents=True, exist_ok=True)
    history = HistoryStore(db, config.workspace, search=config.search)
    windows = WorkingMemory(store=history)
    gateway = FakeGateway()
    llm = FakeLLM()
    memory = FakeMemory()
    agent = Agent(
        config,
        db,
        llm=llm,
        hub=None,
        windows=windows,
        history=history,
        memory=memory,
        ledger=ActionLedger(db),
        skills=SkillStore(db, config.skills),
        recipes=RecipeStore(db),
        gateways=(gateway,),
        documents=documents,
        approvals=ApprovalStore(db, config.approval),
    )
    agent.mode = "primary"
    return SimpleNamespace(
        agent=agent,
        gateway=gateway,
        config=config,
        db=db,
        documents=documents,
        paths=paths,
        llm=llm,
        memory=memory,
        history=history,
        windows=windows,
        base=base,
    )
