"""Operator-configured fallback targets: the chain tried after the primary model.

ADR-0003 §4.7: the operator names every target; no field here is filled from a code default.
"""

import os
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from openai import APIError, APITimeoutError, AsyncOpenAI

from ...providers import ProviderError, build_client
from .support import USER_AGENT

if TYPE_CHECKING:
    from ..config import Config, LLMTargetConfig


def eligible(exc: Exception) -> bool:
    """A transient or provider failure may try the next target; a caller bug may not."""
    if isinstance(exc, (APITimeoutError, TimeoutError)):
        return True
    if isinstance(exc, (APIError, ProviderError)):
        status = getattr(exc, "status_code", None)
        return status is None or status == 429 or status >= 500
    return False


class FallbackChain:
    """Primary first, then `engine.llm.fallbacks` in operator order."""

    def __init__(self, config: Config, client, kwargs: dict, timeout: float | None):
        self.config = config
        self.client = client
        self.kwargs = kwargs
        self.timeout = timeout
        self._cache: dict[tuple, object] = {}

    def __len__(self) -> int:
        return 1 + len(self.config.llm.fallbacks)

    def __iter__(self) -> Iterator[tuple[Any, Any, dict]]:
        yield self.config.llm, self.client, self.kwargs
        for target in self.config.llm.fallbacks:
            yield target, self._client(target), self._kwargs(target)

    def _client(self, target: LLMTargetConfig):
        """One fallback client, built on first use and kept for the process lifetime."""
        limit = min(float(target.timeout_seconds), float(self.timeout)) if self.timeout else None
        key = (target.provider, target.base_url, target.model, limit)
        found = self._cache.get(key)
        if found is not None:
            return found
        api_key = self._key(target)
        if target.wire == "openai":
            client = AsyncOpenAI(
                base_url=target.base_url,
                api_key=api_key,
                timeout=limit or target.timeout_seconds,
                default_headers={"User-Agent": USER_AGENT},
            )
        else:
            client = build_client(
                target.wire, target.base_url, api_key, timeout=limit or target.timeout_seconds
            )
        self._cache[key] = client
        return client

    @staticmethod
    def _key(target: LLMTargetConfig) -> str:
        """The fallback key from its env var; a local endpoint accepts any non-empty string."""
        found = os.environ.get(target.api_key_env)
        if found:
            return found
        return "local" if target.api_key_optional else ""

    def _kwargs(self, target: LLMTargetConfig) -> dict:
        """The same request aimed at another model: model, effort, tier, and wire keys."""
        body = dict(self.kwargs)
        body["model"] = target.model
        for key, value in (
            ("reasoning_effort", target.reasoning_effort),
            ("service_tier", target.service_tier),
        ):
            if value:
                body[key] = value
            else:
                body.pop(key, None)
        if target.wire == "openai":
            body.pop("safety", None)
        else:
            body["safety"] = self.config.soul.voice.safety
        return body
