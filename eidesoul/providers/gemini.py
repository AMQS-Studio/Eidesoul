"""Gemini through Google's OpenAI-compatibility shim, not the native `generateContent` API.

Google serves `/v1beta/openai/` with the OpenAI wire, so the same client works. Native
`generateContent` is a different protocol and would need an adapter, not an entry here.
"""

from __future__ import annotations

from .base import ProviderDefaults

GOOGLE = ProviderDefaults(
    "https://generativelanguage.googleapis.com/v1beta/openai/",
    "GEMINI_API_KEY",
)
