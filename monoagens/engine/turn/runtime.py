"""The typed dependencies of one turn pipeline (ADR-0007 §4.3).

`TurnRuntime` holds the immutable references a turn needs: the config and soul, the stores, the
model, the facade callbacks the `Agent` owns, and the two accessors that change while the
process runs (`lease`, `clock`). Per-turn mutable state lives in `turn/state.py`.

Neither class imports `agent.py`; the facade is passed in as callables, so pyright checks every
dependency instead of a `__getattr__` fallback.
"""


from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from ..access.approvals import ApprovalStore
from ..access.identity import IdentityStore
from ..access.privacy import PrivacyService
from ..access.roles import Role
from ..config import Config
from ..inbound import Action, Address, GatewayCore, Percept, Receipt
from ..llm import LLM
from ..memory import MemoryBackend
from ..memory.ledger import ActionLedger, OutboundLedger
from ..registry import Registry
from ..runtime import RuntimeFacts
from ..soul import Soul
from ..stores.affect import AffectStore
from ..stores.history import HistoryStore
from ..stores.recipes import RecipeStore
from ..stores.skills import SkillStore
from ..stores.style import StyleStore
from ..stores.turns import TurnStore
from .context import WorkingMemory


class DispatchFn(Protocol):
    """`Agent.dispatch`: the one outbound door, never a body call."""

    async def __call__(
        self,
        subject: str,
        address: Address,
        action: Action,
        *,
        origin: Address,
        reason: str = "",
    ) -> Receipt: ...


class LiveSettings(Protocol):
    """The three `Agent` attributes `/reload` swaps: they must be read live, not snapshotted."""

    config: Config
    soul: Soul
    llm: LLM | None


class BodyLookup(Protocol):
    """A body by name, or the body that owns a thread; `None` when nobody claims it."""

    def __call__(self, key: str, /) -> GatewayCore | None: ...


class RolePolicyFn(Protocol):
    """Identity selects roles; a body may never name one (ADR-0006 §4.4)."""

    def __call__(self, inbound: Percept) -> tuple[Role, ...]: ...


ToolContextFn = Callable[..., Any]
SpecsFn = Callable[[tuple[Role, ...], GatewayCore | None], list[Any]]
CommandFn = Callable[
    [str, str, str, str, tuple[Role, ...], GatewayCore, Percept], Awaitable[str]
]
ApproveFn = Callable[..., Awaitable[tuple[str, bool]]]
LeaseAccessor = Callable[[], object | None]
Clock = Callable[[], float]


@dataclass(frozen=True, slots=True)
class TurnRuntime:
    """Everything one turn pipeline reads; built once by the `Agent` facade."""

    _live: LiveSettings
    history: HistoryStore
    turns: TurnStore
    memory: MemoryBackend
    skills: SkillStore | None
    recipes: RecipeStore | None
    affect: AffectStore | None
    style: StyleStore | None
    approvals: ApprovalStore
    outbox: OutboundLedger
    windows: WorkingMemory
    ledger: ActionLedger | None
    identity: IdentityStore
    privacy: PrivacyService
    tool_index: Any
    commands: Registry
    vibe: Any
    ambient: Any
    plugin_parts: Sequence[Any]
    facts: RuntimeFacts
    backlog: dict[str, int]
    boot_epoch: int
    dispatch: DispatchFn
    body_of: BodyLookup
    lease: LeaseAccessor
    clock: Clock
    mode: Callable[[], str]
    draining: Callable[[], bool]
    accepting: Callable[[], bool]
    shutdown_epoch: Callable[[], int]
    role_policy: RolePolicyFn
    tool_context: ToolContextFn
    specs: SpecsFn
    command: CommandFn
    approve: ApproveFn

    @property
    def config(self) -> Config:
        return self._live.config

    @property
    def soul(self) -> Soul:
        return self._live.soul

    @property
    def llm(self) -> LLM | None:
        return self._live.llm

