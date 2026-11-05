"""Explicit per-turn state (ADR-0037 §4.3).

`TurnState` carries what belongs to one turn and nothing else: the percept the body handed in,
the identity and role policy resolved for it, the memories the draft read, the draft itself,
the attachments the message came with, and the checkpoints the store already accepted. It is
created by `TurnRunner.run`, passed through the phases, and never shared across threads.
"""


from dataclasses import dataclass, field

from ..access.roles import Role
from ..inbound import GatewayCore, Percept
from ..memory import MemoryHit
from .context import Media, Message


@dataclass(slots=True)
class TurnState:
    """What one turn carries between its phases; mutable by design, never shared."""

    percept: Percept
    gateway: GatewayCore
    turn_id: str
    thread_id: str
    sender_id: str
    role_policy: tuple[Role, ...]
    started_at: float
    memories: list[MemoryHit] = field(default_factory=list)
    draft: str = ""
    attachments: tuple[Media, ...] = ()
    checkpoints: dict[str, float] = field(default_factory=dict)

    @property
    def message(self) -> Message:
        """The percept payload: intake guarantees a message reached the runner."""
        return self.percept.payload


def sender_of(inbound: Percept) -> str:
    """The transport sender id, or the thread when the body could not name one."""
    payload = inbound.payload
    return payload.sender_id or payload.sender_address or inbound.thread


def actor_of(inbound: Percept) -> str:
    """The canonical person id; approvals and memory key on it, never a transport id."""
    return str(inbound.actor or sender_of(inbound))
