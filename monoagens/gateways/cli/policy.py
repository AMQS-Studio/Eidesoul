"""CLI gateway policy: a role may use the terminal only with `gateways.cli.dm: true`.

The terminal is a direct conversation, so the only decision is whether this role may enter it.
The block is opaque to the core; the gateway validates its own vocabulary (ADR-0046 §4.2).
"""


from collections.abc import Mapping


def allowed(block: object) -> bool:
    return isinstance(block, Mapping) and block.get("dm") is True
