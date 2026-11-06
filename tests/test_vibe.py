"""Room vibe contract: VAD from Indonesian labels, attack/decay dynamics, and the prompt block."""


import asyncio
import json
import time
from pathlib import Path

import pytest
from support import build_config, database, role

from monoagens.engine.access.roles import Role
from monoagens.engine.config import Config
from monoagens.engine.soul import VibeConfig
from monoagens.engine.stores.vibe import (
    PrototypeSource,
    VibeRunner,
    VibeState,
    advance,
    load_bank,
)
from monoagens.engine.turn.decision import build_context
from monoagens.engine.turn.prompts import system_prompt


def make_config() -> Config:
    return build_config(Path("/tmp"), roles={"member": role("member")})


def test_vad_mapping_is_negative_for_anger() -> None:
    point = VibeRunner.vad({"anger": 0.9, "joy": 0.1})

    assert point is not None
    assert point[0] < 0.3
    assert point[1] > 0.7


def test_vad_below_the_floor_is_neutral() -> None:
    assert VibeRunner.vad({"joy": 0.5, "anger": 0.2}) is None


def test_vad_keeps_a_strong_negative_that_co_occurs_with_its_neighbour() -> None:
    """P0-5: anger + disgust agree, so the group margin must not cancel them."""
    point = VibeRunner.vad({"anger": 0.72, "disgust": 0.60, "fear": 0.46})

    assert point is not None
    assert point[0] < 0.35
    assert point[1] > 0.65


def test_vad_sharpening_reaches_the_positive_band() -> None:
    point = VibeRunner.vad(
        {"joy": 0.95, "trust": 0.57, "anticipation": 0.45, "anger": 0.31}
    )

    assert point is not None
    assert point[0] >= 0.60


def test_vad_ambiguous_top_two_is_neutral() -> None:
    assert VibeRunner.vad({"joy": 0.90, "anger": 0.89}) is None


def test_attack_and_baseline_move_gradually(tmp_path) -> None:
    runner = VibeRunner(
        database(),
        VibeConfig(
            attack=0.5,
            baseline_influence=0.1,
            hold_seconds=0,
        ),
        classifier=lambda _text: {"joy": 0.9, "trust": 0.1},
    )
    runner._running = True

    asyncio.run(runner.observe("chat", "senang"))

    state = runner._state("chat")
    assert 0.5 < state.baseline[0] < 0.9  # the baseline mood does not jump
    assert state.current[0] > state.baseline[0]


def test_describe_uses_words(tmp_path) -> None:
    runner = VibeRunner(database(), VibeConfig())
    state = VibeState(current=[0.2, 0.8, 0.7], baseline=[0.2, 0.8, 0.7])

    text = runner._describe(state)

    assert "negative, energetic, confident" in text
    assert "never cite numbers" in text


def test_describe_follows_current_not_slow_baseline(tmp_path) -> None:
    runner = VibeRunner(database(), VibeConfig())
    state = VibeState(current=[0.1, 0.9, 0.2], baseline=[0.5, 0.4, 0.5])

    # the baseline is still neutral, but the latest message must show up in the prompt block.
    assert "negative, energetic, pressured" in runner._describe(state)


def test_advance_pulls_current_to_baseline() -> None:
    now = time.time()
    state = VibeState(
        current=[0.9, 0.9, 0.9],
        baseline=[0.8, 0.8, 0.8],
        last_tick=now - 120,
        hold_until=0.0,
    )

    advance(state, now, VibeConfig())

    assert state.current[0] < 0.9  # current chases the baseline
    assert state.baseline[0] < 0.8  # baseline chases neutral


def test_vibe_block_in_prompts() -> None:
    block = "Vibe ruangan: netral, tenang, seimbang."

    from monoagens.engine.turn.prompts import PromptContext

    config = make_config()
    roles = (Role("member"),)
    context = PromptContext(config=config, roles=roles, vibe=block)
    assert block in system_prompt(config, roles, context=context)
    assert "Vibe ruangan" in build_context(vibe=block)


def test_observe_is_silent_when_not_running(tmp_path) -> None:
    runner = VibeRunner(database(), VibeConfig(), classifier=lambda _t: {})

    assert asyncio.run(runner.observe("chat", "halo")) == ""


def test_start_reports_availability(tmp_path) -> None:
    ready = VibeRunner(
        database(), VibeConfig(), classifier=lambda _t: {"joy": 1.0}
    )
    assert asyncio.run(ready.start()) is True  # classifier injected: no torch needed
    asyncio.run(ready.close())


def test_start_disables_vibe_without_an_embedder(tmp_path) -> None:
    runner = VibeRunner(database(), VibeConfig())
    runner._source = PrototypeSource(bank={})

    assert asyncio.run(runner.start()) is False
    assert runner._failed is True


def test_load_bank_merges_files_and_drops_odd_rows(tmp_path) -> None:
    (tmp_path / "id.json").write_text(
        json.dumps(
            {
                "prototypes": {
                    "Joy": ["wkwk seneng banget"],
                    "nonsense": ["halo halo"],
                    "anger": ["ih"],
                }
            }
        ),
        encoding="utf-8",
    )

    assert load_bank(tmp_path) == {"joy": ("wkwk seneng banget",)}


def test_prototype_source_scores_the_shipped_bank() -> None:
    source = PrototypeSource()

    assert source.scores("") == {}  # no text, no score
    top = max(source.scores("you are being ridiculous").items(), key=lambda kv: kv[1])[0]
    assert top == "anger"
    assert VibeRunner.vad(source.scores("the report is on the table")) is None
    assert VibeRunner.vad(source.scores("i want to cry")) is not None


def test_state_persists_to_database() -> None:
    db = database()
    runner = VibeRunner(
        db,
        VibeConfig(attack=0.5, baseline_influence=0.1, hold_seconds=0),
        classifier=lambda _text: {"joy": 0.9, "trust": 0.1},
    )
    runner._running = True
    asyncio.run(runner.observe("chat", "senang"))
    expected = list(runner._state("chat").current)
    asyncio.run(runner.close())

    reloaded = VibeRunner(db, VibeConfig())

    assert reloaded._state("chat").current == pytest.approx(expected)
