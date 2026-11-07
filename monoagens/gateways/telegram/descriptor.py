"""Telegram gateway descriptor (ADR-0046 §4.5): the install extra and no body-only tools."""


from ..descriptor import GatewayDescriptor

GATEWAY = GatewayDescriptor(name="telegram", extra="telegram")
