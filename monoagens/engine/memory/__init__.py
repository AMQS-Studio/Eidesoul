"""MemoryBackend: the canonical memory port, its result types, and the write-side contracts."""


from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class MemoryHit:
    id: str
    text: str
    score: float = 0.0
    scope: str = "person"
    person_id: str = ""
    thread_id: str = ""
    created_at: float | None = None
    metadata: dict = field(default_factory=dict)
    level: int = 2
    owner_id: str = ""
    subject_id: str = ""
    confirmed: bool = True
    vetoed: bool = False
    kind: str = "fact"
    trust: int = 1
    state: str = "ok"
    privacy_evidence: tuple[dict, ...] = ()
    privacy_policy: str = ""


@dataclass(frozen=True)
class MemoryContext:
    """Rendered memory block plus the exact hits the model was allowed to see."""

    text: str = ""
    hits: tuple[MemoryHit, ...] = ()


class MemoryBackend(Protocol):
    available: bool
    reason: str

    async def add(
        self,
        text: str,
        *,
        scope: str = "person",
        kind: str = "fact",
        person_id: str = "",
        thread_id: str = "",
        audience: str = "",
        source: str = "",
        metadata: dict | None = None,
        supersedes: str | None = None,
        infer: bool = False,
        level: int = 2,
        owner_id: str = "",
        subject_id: str = "",
        confirmed: bool = True,
    ) -> list[str]: ...

    async def capture(
        self,
        messages: list[dict],
        *,
        person_id: str,
        thread_id: str,
        audience: str,
        source: str = "turn",
        level: int = 2,
        owner_id: str = "",
        subject_id: str = "",
        confirmed: bool = True,
        language: str,
    ) -> list[str]: ...

    async def search(
        self,
        query: str,
        *,
        person_id: str = "",
        thread_id: str = "",
        limit: int = 12,
        scopes: tuple[str, ...] = ("global", "person", "thread"),
        max_level: int = 4,
    ) -> list[MemoryHit]: ...

    async def list(
        self,
        *,
        scope: str,
        person_id: str = "",
        thread_id: str = "",
        limit: int = 20,
    ) -> list[MemoryHit]: ...

    def get(self, memory_id: str) -> MemoryHit | None: ...

    async def forget(
        self,
        memory_id: str,
        *,
        requester_id: str = "",
        thread_id: str = "",
        allow_global: bool = False,
    ) -> bool: ...

    async def set_privacy(
        self,
        memory_id: str,
        *,
        level: int | None = None,
        confirmed: bool | None = None,
        subject_id: str | None = None,
        vetoed: bool | None = None,
        actor: str = "",
    ) -> bool: ...

    async def veto(self, memory_id: str, *, subject_id: str, active: bool = True) -> bool: ...

    async def context(
        self,
        *,
        person_id: str,
        thread_id: str,
        query: str,
        direct: bool | None = None,
        mood=None,
        max_items: int | None = None,
        max_chars: int | None = None,
    ) -> MemoryContext: ...

    async def close(self) -> None: ...


class MemoryExtractor(Protocol):
    """An optional extractor turns a conversation into structured facts (ADR-0001)."""

    available: bool

    async def capture(
        self,
        messages: list[dict],
        *,
        person_id: str,
        thread_id: str,
        audience: str,
        source: str = "turn",
        level: int = 2,
        owner_id: str = "",
        subject_id: str = "",
        confirmed: bool = True,
    ) -> list[MemoryHit]: ...


class MemoryIndex(Protocol):
    """The derived semantic index; rebuildable from canonical and never read as truth."""

    available: bool

    async def add(
        self,
        text: str,
        *,
        scope: str,
        kind: str,
        person_id: str,
        thread_id: str,
        audience: str,
        source: str,
        metadata: dict | None = None,
        infer: bool = False,
        level: int = 2,
        owner_id: str = "",
        subject_id: str = "",
        confirmed: bool = True,
    ) -> list[str]: ...

    async def delete(self, external_id: str) -> bool: ...

    def purge_library(self, external_id: str, text: str) -> int: ...

    async def close(self) -> None: ...


class CaptureJobSink(Protocol):
    """W3.2/ADR-0003 writes `cognition_jobs` on the open intake transaction."""

    def enqueue(self, conn, candidate: object) -> None: ...


def memory_status(backend) -> str:
    if getattr(backend, "available", False):
        return str(getattr(backend, "label", "postgres"))
    return f"unavailable: {getattr(backend, 'reason', 'unknown')}"


async def build_memory(config, *, db=None) -> MemoryBackend:
    """Build the one PostgreSQL-backed memory store: native hybrid retrieval, no sidecar index."""
    from .store import MemoryStore

    return MemoryStore(db, config)
