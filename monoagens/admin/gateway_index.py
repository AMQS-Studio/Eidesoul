"""Admin view of the gateway index: one discovery implementation (ADR-0046 §4.5)."""


import importlib.util

from ..gateways import GatewayInfo, gateway_index

__all__ = ["GatewayInfo", "gateway_index", "installed"]


def installed(name: str) -> bool:
    info = gateway_index().get(name)
    if info is None:
        return False
    try:
        return importlib.util.find_spec(info.module) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False
