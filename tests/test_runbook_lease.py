"""Runbook regressions: standby fencing, recipe fencing, and the outbound ledger."""

import asyncio
from types import SimpleNamespace

import pytest
from support import database

from monoagens.engine.jobs.lease import FencedStore, Lease, Superseded
from monoagens.engine.memory.ledger import OutboundLedger
from monoagens.engine.stores.recipes import RecipeStore, run_recipe
from monoagens.engine.tools import ToolContext


def test_bound_store_at_generation_zero_is_superseded() -> None:
    store = FencedStore()
    store.bind_lease(Lease(database(), instance_id="standby"))
    with pytest.raises(Superseded, match="generation 0"):
        _ = store.fence


def test_unbound_store_has_no_fence() -> None:
    assert FencedStore().fence is None


def test_ledger_begin_reports_new_then_the_existing_status() -> None:
    ledger = OutboundLedger(database())
    first = ledger.begin("scope-a", 0, gateway="whatsapp", thread_id="chat")
    assert first == "new"
    assert ledger.begin("scope-a", 0, gateway="whatsapp", thread_id="chat") == "intent"


def test_recipe_store_is_fenced() -> None:
    assert issubclass(RecipeStore, FencedStore)
    store = RecipeStore(database())
    store.bind_lease(Lease(database(), instance_id="standby"))
    with pytest.raises(Superseded, match="generation 0"):
        store.save("rekap", "recipe", "d", steps=[{"tool": "echo", "args": {}}])


def test_prompt_recipe_needs_an_llm_runner() -> None:
    store = RecipeStore(database())
    store.save("prompted", "prompt", "d", prompt="lakukan")
    ctx = ToolContext(SimpleNamespace(), (), ())
    with pytest.raises(ValueError, match="prompt recipes need an LLM runner"):
        asyncio.run(run_recipe(store.get("prompted"), "", ctx))
