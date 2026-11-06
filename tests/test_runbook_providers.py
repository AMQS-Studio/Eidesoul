"""Provider wire details that only a real request would otherwise reveal (runbook)."""

from monoagens.providers.anthropic import PROMPT_CACHING_BETA
from monoagens.providers.speech import MIMETYPE_EXTENSIONS, _filename


def test_anthropic_declares_the_prompt_caching_beta() -> None:
    """`cache_control` blocks are ignored unless the beta header names the feature."""
    from monoagens.providers.anthropic import AnthropicClient

    client = AnthropicClient("https://api.anthropic.com", "key")
    assert client.headers()["anthropic-beta"] == PROMPT_CACHING_BETA


def test_audio_filenames_never_fall_back_to_bin_for_audio() -> None:
    assert _filename("audio/aac") == "audio.m4a"
    assert _filename("audio/x-m4a") == "audio.m4a"
    assert _filename("audio/mp4") == "audio.m4a"
    assert _filename("audio/x-wav") == "audio.wav"
    assert _filename("audio/ogg; codecs=opus") == "audio.ogg"
    assert _filename("audio/unknown-container") == "audio.mp3"
    assert "audio/aac" in MIMETYPE_EXTENSIONS
