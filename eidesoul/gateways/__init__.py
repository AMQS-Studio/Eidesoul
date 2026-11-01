"""Gateways are appendages: transport in and out of the core, never leaking into the engine."""

from __future__ import annotations

import importlib
import logging
from importlib.metadata import entry_points

from ..engine.config import ConfigError

log = logging.getLogger(__name__)

BUILTIN = {
    "whatsapp": "eidesoul.gateways.whatsapp.gateway:WhatsAppGateway",
    "cli": "eidesoul.gateways.cli.gateway:CliGateway",
}


def ensure_schemas(db) -> list[str]:
    """Create gateway-specific tables (pure SQLAlchemy, no transport connection)."""
    done: list[str] = []
    for module_name in ("eidesoul.gateways.whatsapp.schema",):
        try:
            module = importlib.import_module(module_name)
        except ImportError:  # gateway extra is not installed
            continue
        module.ensure_schema(db)
        done.append(module_name)
    return done


def load_gateway(name: str):
    """Gateway class: builtins first, then the `eidesoul.gateways` entry points."""
    target = BUILTIN.get(str(name))
    if target is None:
        try:
            found = entry_points(group="eidesoul.gateways")
        except Exception:
            found = []
        for entry in found:
            if entry.name == name:
                target = entry.value
                break
    if target is None:
        raise ConfigError(f"unknown gateway '{name}'")
    module_name, _, attribute = target.partition(":")
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise ConfigError(
            f"gateway '{name}' needs the transport extra (pip install -e '.[{name}]'): {exc}"
        ) from exc
    return getattr(module, attribute)


def build_gateways(
    config,
    *,
    db,
    history,
    windows,
    only: tuple[str, ...] = (),
    force: tuple[str, ...] = (),
) -> list:
    """Build the enabled gateways from the `gateways` section of config.yaml; `only`/`force` are
    used by the console."""
    found: list = []
    names = list(config.gateways)
    for name in only:
        if name not in names:
            names.append(name)
    for name in names:
        settings = config.gateways.get(name)
        enabled = bool(settings and settings.enabled) or name in force
        if not enabled:
            continue
        if only and name not in only:
            continue
        cls = load_gateway(name)
        found.append(cls(config, db, history, windows, settings=settings))
    if not found:
        raise ConfigError("no gateway is enabled; fill in the `gateways` section of config.yaml")
    return found
