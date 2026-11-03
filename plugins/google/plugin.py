"""Entry point the plugin loader calls exactly once when the plugin is enabled (ADR-0028).

The manifest declares what this returns; the loader validates the names and hands the handlers
to the normal tool/command registries. Permissions still come only from roles.
"""


from eidesoul.engine.registry import Handler

from .tools import google


def register() -> dict[str, list[Handler]]:
    return {"tools": [google.TOOL], "commands": [], "gateways": []}
