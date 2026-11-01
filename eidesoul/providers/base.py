"""One provider record: endpoint, key variable, and the quirks that shape a request.

The record deliberately carries no model: a model id is an operator choice made at
`eidesoul init` time, not a fact the code should pin. The two quirk fields replace
per-provider branches elsewhere in the engine, so no module compares a provider name.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderDefaults:
    base_url: str
    api_key_env: str
    session_header: str = ""  # per-request session header, "" = none
    api_key_optional: bool = False  # a local endpoint accepts any non-empty key
    models_url: str = ""  # "" = {base_url}/models; set it when the list lives elsewhere
