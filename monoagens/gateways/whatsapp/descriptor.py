"""WhatsApp gateway descriptor (ADR-0002 §4.5): extra, onboarding, schema, and capabilities."""


from ..descriptor import GatewayDescriptor

GATEWAY = GatewayDescriptor(
    name="whatsapp",
    extra="whatsapp",
    onboarding="pair",
    schema="monoagens.gateways.whatsapp.schema",
    capabilities=("send", "send_to", "chat_history"),
)
