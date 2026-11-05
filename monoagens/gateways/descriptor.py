"""A gateway's own descriptor: onboarding command, extra, schema, and capabilities (ADR-0046 §4.5).

The core never reads these fields; the gateway package declares them and the admin surface
indexes them.
"""


from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GatewayDescriptor:
    name: str = ""
    extra: str = ""
    onboarding: str = ""
    schema: str = ""
    capabilities: tuple[str, ...] = ()
