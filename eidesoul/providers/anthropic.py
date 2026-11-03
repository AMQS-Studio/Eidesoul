"""Claude through Anthropic's OpenAI-SDK compatibility layer.

The layer is documented upstream as test-oriented rather than production grade, and its
caveats are real: tools are supported but `strict` is ignored; `response_format` accepts only
`json_schema` with `strict: true`; system/developer messages are hoisted and joined into one
top-level `system`; audio and file content parts are rejected with 400; prompt caching and
thinking summaries are unavailable. Personal or service-account keys that are not scoped to a
workspace additionally need an `anthropic-workspace-id` header, and no config knob exists for
that here. Native features need an adapter, not a data entry.
"""


from .base import ProviderDefaults

ANTHROPIC = ProviderDefaults("https://api.anthropic.com/v1/", "ANTHROPIC_API_KEY")
