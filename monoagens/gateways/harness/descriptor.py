"""Harness body descriptor: core-only, one shell capability, no onboarding."""


from ..descriptor import GatewayDescriptor

GATEWAY = GatewayDescriptor(name="harness", capabilities=("run",))
