"""CLI gateway descriptor: core-only, no extra, no onboarding, no schema."""


from ..descriptor import GatewayDescriptor

GATEWAY = GatewayDescriptor(name="cli", capabilities=())
