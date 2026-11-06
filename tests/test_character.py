"""Character layer contract: lean triggers, and the original draft wins when unsure."""


import asyncio
from dataclasses import replace

from support import build_config

from monoagens.engine.character.character import (
    _numeric_signature,
    needs_polish,
    polish,
    unwrap_quotes,
)
from monoagens.engine.soul import CharacterConfig

NATURAL = "capeknya di bagian mana?"


class StubLLM:
    def __init__(self, reply: str):
        self.reply = reply
        self.calls = 0
        self.kwargs: dict = {}

    async def complete(self, system, user, **kwargs):
        self.calls += 1
        self.kwargs = kwargs
        return self.reply


def test_wrapped_quotes_are_unwrapped_without_backtracking() -> None:
    assert unwrap_quotes('"halo"') == "halo"
    assert unwrap_quotes("`halo`") == "halo"
    assert unwrap_quotes('"a"b"') == 'a"b'          # inner quote marks stay
    assert unwrap_quotes('"abc') == '"abc'          # unclosed: unchanged
    assert unwrap_quotes("' ") == "' "                # no closing mark: unchanged
    assert unwrap_quotes('""') == ""
    assert unwrap_quotes('"' + "a" * 50_000) == '"' + "a" * 50_000


def config_with(tmp_path, **character):
    settings = CharacterConfig(enabled=True, **character)
    config = build_config(tmp_path)
    voice = replace(config.soul.voice, character=settings)
    return replace(config, soul=replace(config.soul, voice=voice))


def run_polish(config, llm, draft: str) -> str:
    return asyncio.run(
        polish(llm, config, draft, latest="halo", direct=True, tone="warm", session="s")
    )


def long_draft() -> str:
    return (
        "Sure! Here are suggestions for your event: "
        "plan the rundown first, then check the catering."
    )


def test_short_natural_reply_needs_no_polish() -> None:
    assert needs_polish(NATURAL, language="id") is False
    assert needs_polish("", language="id") is False


def test_assistant_tell_markdown_and_lengths_trigger_polish() -> None:
    assert needs_polish("Sure, I can help", language="en") is True
    assert needs_polish("- susun rundown\n- cek konsumsi", language="id") is True
    assert needs_polish(
        "yeah. how are you? let me know later. i will wait. do not forget.", language="en"
    ) is True
    assert needs_polish("a" * 241, language="id") is True
    assert needs_polish("x" * 401, language="id") is True


def test_disabled_layer_returns_the_draft_without_calling_the_model(tmp_path) -> None:
    llm = StubLLM("versi karakter")
    config = config_with(tmp_path)
    config = replace(
        config,
        soul=replace(
            config.soul,
            voice=replace(
                config.soul.voice, character=CharacterConfig(enabled=False)
            ),
        ),
    )

    assert run_polish(config, llm, long_draft()) == long_draft()
    assert llm.calls == 0


def test_natural_draft_is_returned_without_calling_the_model(tmp_path) -> None:
    llm = StubLLM("versi karakter")

    assert run_polish(config_with(tmp_path), llm, NATURAL) == NATURAL
    assert llm.calls == 0


def test_model_answer_replaces_a_robotic_draft(tmp_path) -> None:
    llm = StubLLM('FINAL: "rundown dulu, konsumsi belakangan"')

    assert (
        run_polish(config_with(tmp_path), llm, long_draft())
        == "rundown dulu, konsumsi belakangan"
    )
    assert llm.calls == 1
    assert llm.kwargs["max_tokens"] == 500


def test_empty_answer_falls_back_to_the_draft(tmp_path) -> None:
    assert run_polish(config_with(tmp_path), StubLLM(""), long_draft()) == long_draft()


def test_answer_that_still_sounds_like_an_assistant_falls_back(tmp_path) -> None:
    llm = StubLLM("Sure! Hope this helps")

    assert run_polish(config_with(tmp_path), llm, long_draft()) == long_draft()


def test_answer_that_grows_the_reply_falls_back(tmp_path) -> None:
    llm = StubLLM("wkwk " + "panjang " * 120)

    assert run_polish(config_with(tmp_path), llm, long_draft()) == long_draft()


def test_fenced_draft_is_never_rewritten(tmp_path) -> None:
    llm = StubLLM("versi karakter")
    draft = "Tentu, ini contohnya:\n```\nsatu --- dua\n```\nsekian laporan lengkapnya ya"

    assert run_polish(config_with(tmp_path), llm, draft) == draft
    assert llm.calls == 0


def test_numeric_signature_collects_numbers_times_dates_and_amounts() -> None:
    signature = _numeric_signature(
        "Rapat 2026-11-05 jam 14:30, biaya Rp50.000 naik 2,5% untuk 3 orang."
    )

    assert signature == ("14:30", "2,5%", "2026-11-05", "3", "rp50.000")


def test_a_rewrite_that_moves_a_time_is_discarded(tmp_path) -> None:
    draft = "Tentu! Rapat kita jam 14:30 di lantai 3, jangan lupa bawa draf."
    rewrite = "rapat jam 15:30 di lantai 3, jangan lupa bawa draf"

    assert run_polish(config_with(tmp_path), StubLLM(rewrite), draft) == draft


def test_a_rewrite_that_turns_a_percentage_into_words_is_discarded(tmp_path) -> None:
    draft = "Tentu! Diskonnya 50% untuk semua pesanan hari ini ya."
    rewrite = "diskonnya separuh untuk semua pesanan hari ini ya"

    assert run_polish(config_with(tmp_path), StubLLM(rewrite), draft) == draft


def test_a_rewrite_that_drops_a_number_is_discarded(tmp_path) -> None:
    draft = "Tentu! Aku pesankan 3 kursi untuk acara besok pagi."
    rewrite = "aku pesankan kursi untuk acara besok pagi"

    assert run_polish(config_with(tmp_path), StubLLM(rewrite), draft) == draft


def test_a_rewrite_that_invents_a_number_is_discarded(tmp_path) -> None:
    draft = "Tentu! Aku pesankan kursi untuk acara besok pagi."
    rewrite = "aku pesankan 12 kursi untuk acara besok pagi"

    assert run_polish(config_with(tmp_path), StubLLM(rewrite), draft) == draft


def test_a_rewrite_that_keeps_every_number_is_accepted(tmp_path) -> None:
    draft = "Sure! Meeting at 14:30, 50% discount today."
    rewrite = "meeting at 14:30, 50% discount today"

    assert run_polish(config_with(tmp_path), StubLLM(rewrite), draft) == rewrite
