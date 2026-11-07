"""Slack gateway descriptor (ADR-0002 §4.5): the install extra and no body-only tools."""


from ..descriptor import GatewayDescriptor

GATEWAY = GatewayDescriptor(name="slack", extra="slack")
