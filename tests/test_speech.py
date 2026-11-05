"""Speech seam: HTTP adapters, provider selection, and the realtime refusal (P3.4 §4)."""

import asyncio
import json
from pathlib import Path

import httpx
import pytest
import yaml
from support import test_dsn as db_url

from monoagens.engine.config import ConfigError, load_config
from monoagens.providers.base import ProviderError
from monoagens.providers.speech import (
    DeepgramSTT,
    ElevenLabsTTS,
    GeminiLiveSession,
    OpenAICompatibleSTT,
    OpenAICompatibleTTS,
    RealtimeVoiceSession,
    SpeechTarget,
    STTProvider,
    TTSProvider,
    build_realtime,
    build_stt,
    build_tts,
)

SOUL = """\
name: MonoAgens
language: en
locale: en-US
timezone: UTC
pronouns: I
persona: p
"""


def invoke(build, handler, call):
    """Build the adapter on a MockTransport and return `(provider, result)`."""

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = build(client)
            return provider, await call(provider)

    return asyncio.run(run())


def test_openai_stt_maps_the_request_and_response() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = request.content
        return httpx.Response(200, json={"text": "  halo dunia "})

    async def call(provider):
        return await provider.transcribe(b"OggS-audio", mimetype="audio/ogg")

    target = SpeechTarget(provider="openai", api_key="sk-key", language="id")
    provider, text = invoke(
        lambda client: build_stt(target, client=client), handler, call
    )
    assert isinstance(provider, OpenAICompatibleSTT)
    assert isinstance(provider, STTProvider)

    assert text == "halo dunia"
    assert seen["url"] == "https://api.openai.com/v1/audio/transcriptions"
    assert seen["auth"] == "Bearer sk-key"
    assert b'name="model"' in seen["body"]
    assert b"whisper-1" in seen["body"]
    assert b'name="language"' in seen["body"]
    assert b'filename="audio.ogg"' in seen["body"]
    assert b"OggS-audio" in seen["body"]


def test_local_tts_uses_the_openai_audio_shape() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=b"ID3-audio")

    async def call(provider):
        return await provider.synthesize("halo", language="id")

    target = SpeechTarget(
        provider="kokoro",
        base_url="http://tts.local:8880/v1",
        api_key="local",
        model="kokoro",
        voice="af_heart",
    )
    provider, audio = invoke(
        lambda client: build_tts(target, client=client), handler, call
    )
    assert isinstance(provider, OpenAICompatibleTTS)
    assert isinstance(provider, TTSProvider)

    assert audio == b"ID3-audio"
    assert seen["url"] == "http://tts.local:8880/v1/audio/speech"
    assert seen["body"] == {
        "input": "halo",
        "voice": "af_heart",
        "response_format": "mp3",
        "model": "kokoro",
        "language": "id",
    }


def test_elevenlabs_tts_maps_its_own_path_and_key() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["key"] = request.headers.get("xi-api-key")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=b"mp3-bytes")

    async def call(provider):
        return await provider.synthesize("halo dunia")

    target = SpeechTarget(provider="elevenlabs", api_key="xi-key", voice="voice-9")
    provider, audio = invoke(
        lambda client: build_tts(target, client=client), handler, call
    )
    assert isinstance(provider, ElevenLabsTTS)

    assert audio == b"mp3-bytes"
    assert seen["url"] == "https://api.elevenlabs.io/v1/text-to-speech/voice-9"
    assert seen["key"] == "xi-key"
    assert seen["body"] == {"text": "halo dunia", "model_id": "eleven_multilingual_v2"}


def test_deepgram_stt_maps_query_headers_and_nested_response() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["params"] = dict(request.url.params)
        seen["auth"] = request.headers.get("authorization")
        seen["type"] = request.headers.get("content-type")
        seen["body"] = request.content
        return httpx.Response(
            200,
            json={
                "results": {
                    "channels": [{"alternatives": [{"transcript": "halo dunia"}]}]
                }
            },
        )

    async def call(provider):
        return await provider.transcribe(b"RIFF-audio", mimetype="audio/wav")

    target = SpeechTarget(provider="deepgram", api_key="dg-key", language="id")
    provider, text = invoke(
        lambda client: build_stt(target, client=client), handler, call
    )
    assert isinstance(provider, DeepgramSTT)

    assert text == "halo dunia"
    assert str(seen["url"]).startswith("https://api.deepgram.com/v1/listen?")
    assert seen["params"]["model"] == "nova-3"
    assert seen["params"]["language"] == "id"
    assert seen["params"]["smart_format"] == "true"
    assert seen["auth"] == "Token dg-key"
    assert seen["type"] == "audio/wav"
    assert seen["body"] == b"RIFF-audio"


def test_a_missing_api_key_fails_only_that_provider(monkeypatch) -> None:
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-key")

    with pytest.raises(ProviderError, match="DEEPGRAM_API_KEY"):
        build_stt(SpeechTarget(provider="deepgram"))

    provider = build_tts(SpeechTarget(provider="openai"))
    assert isinstance(provider, OpenAICompatibleTTS)


def test_a_provider_error_keeps_the_status_code() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    async def call(provider):
        return await provider.synthesize("halo")

    target = SpeechTarget(provider="openai", api_key="nope")

    with pytest.raises(ProviderError) as caught:
        invoke(lambda client: build_tts(target, client=client), handler, call)

    assert caught.value.status_code == 401
    assert "bad key" in str(caught.value)


def test_an_unconfigured_realtime_session_raises_provider_error() -> None:
    session = build_realtime(SpeechTarget(provider="gemini_live"))
    assert isinstance(session, GeminiLiveSession)
    assert isinstance(session, RealtimeVoiceSession)

    with pytest.raises(ProviderError, match="not configured"):
        asyncio.run(session.send_audio(b"\x00\x01"))
    with pytest.raises(ProviderError, match="not configured"):
        asyncio.run(session.receive())
    asyncio.run(session.close())


def test_a_configured_realtime_session_waits_for_a_body_transport() -> None:
    session = build_realtime(
        SpeechTarget(provider="gemini_live", api_key="k", model="gemini-live")
    )

    with pytest.raises(ProviderError, match="not wired"):
        asyncio.run(session.send_audio(b"\x00\x01"))


def test_an_unknown_realtime_provider_is_refused() -> None:
    with pytest.raises(ProviderError, match="unknown realtime speech provider"):
        build_realtime(SpeechTarget(provider="acme"))


def write_folder(tmp_path: Path, *, speech: dict, soul: str = SOUL) -> None:
    body = {
        "database": {"url_env": "", "url": db_url()},
        "workspace": ".monoagens/workspace",
        "roles_file": "roles.yaml",
        "soul_file": "soul.yaml",
        "engine": {
            "default_role": "guest",
            "llm": {
                "provider": "custom",
                "base_url": "http://x",
                "api_key_env": "K",
                "model": "m",
            },
            "speech": speech,
        },
        "gateways": {"whatsapp": {"enabled": True}},
    }
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(body, sort_keys=False), encoding="utf-8"
    )
    (tmp_path / "roles.yaml").write_text(
        "roles:\n  guest:\n    name: guest\n    tools: []\n", encoding="utf-8"
    )
    (tmp_path / "soul.yaml").write_text(soul, encoding="utf-8")


SPEECH = {
    "timeout_seconds": 12,
    "stt": {
        "provider": "deepgram",
        "api_key_env": "DG_KEY",
        "language": "id",
    },
    "tts": {
        "provider": "elevenlabs",
        "voice": "voice-9",
        "model": "eleven_turbo_v2_5",
    },
}
SELECTED_SOUL = SOUL + """
voice:
  speech:
    enabled: true
    stt: deepgram
    tts: elevenlabs
"""


@pytest.fixture(autouse=True)
def _keys(monkeypatch):
    monkeypatch.setenv("K", "x")
    monkeypatch.setenv("DG_KEY", "dg-key")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "xi-key")


def test_soul_voice_speech_selects_the_configured_provider(tmp_path) -> None:
    write_folder(tmp_path, speech=SPEECH, soul=SELECTED_SOUL)

    config = load_config(tmp_path / "config.yaml", require_key=False)

    assert config.speech.enabled is True
    assert config.speech.timeout_seconds == 12.0
    assert config.speech.stt.provider == "deepgram"
    assert config.speech.stt.model == "nova-3"
    assert config.speech.stt.api_key_env == "DG_KEY"
    assert config.speech.stt.language == "id"
    assert config.speech.tts.provider == "elevenlabs"
    assert config.speech.tts.voice == "voice-9"
    assert config.speech.tts.model == "eleven_turbo_v2_5"
    assert config.soul.voice.speech.enabled is True
    assert config.soul.voice.speech.stt == "deepgram"
    assert config.soul.voice.speech.tts == "elevenlabs"

    assert isinstance(config.speech.stt_provider(), DeepgramSTT)
    assert isinstance(config.speech.tts_provider(), ElevenLabsTTS)


def test_an_unknown_engine_speech_provider_is_a_config_error(tmp_path) -> None:
    write_folder(
        tmp_path,
        speech={"stt": {"provider": "acme", "base_url": "http://x"}},
    )

    with pytest.raises(ConfigError, match=r"engine\.speech\.stt\.provider 'acme'"):
        load_config(tmp_path / "config.yaml", require_key=False)


def test_a_local_speech_provider_needs_an_endpoint(tmp_path) -> None:
    write_folder(tmp_path, speech={"tts": {"provider": "piper"}})

    with pytest.raises(ConfigError, match=r"engine\.speech\.tts\.base_url"):
        load_config(tmp_path / "config.yaml", require_key=False)


def test_soul_may_only_select_a_configured_provider(tmp_path) -> None:
    write_folder(
        tmp_path,
        speech={"stt": {"provider": "openai"}},
        soul=SOUL + "\nvoice:\n  speech:\n    enabled: true\n    stt: deepgram\n",
    )

    with pytest.raises(ConfigError, match=r"soul\.voice\.speech\.stt"):
        load_config(tmp_path / "config.yaml", require_key=False)


def test_soul_rejects_an_unknown_speech_provider_name(tmp_path) -> None:
    write_folder(
        tmp_path,
        speech={"stt": {"provider": "deepgram"}},
        soul=SOUL + "\nvoice:\n  speech:\n    enabled: true\n    stt: acme\n",
    )

    with pytest.raises(ConfigError, match="not a known provider"):
        load_config(tmp_path / "config.yaml", require_key=False)


def test_unknown_speech_keys_are_rejected(tmp_path) -> None:
    write_folder(
        tmp_path,
        speech={"stt": {"provider": "deepgram", "bogus": True}},
    )

    with pytest.raises(ConfigError, match=r"engine\.speech\.stt\.bogus"):
        load_config(tmp_path / "config.yaml", require_key=False)


def test_unknown_soul_speech_keys_are_rejected() -> None:
    from monoagens.engine.soul import load_soul

    with pytest.raises(ValueError, match=r"voice\.speech\.bogus"):
        load_soul(
            yaml.safe_load(SOUL + "\nvoice:\n  speech:\n    enabled: true\n    bogus: x\n")
        )


def test_speech_defaults_are_off(tmp_path) -> None:
    write_folder(tmp_path, speech={})

    config = load_config(tmp_path / "config.yaml", require_key=False)

    assert config.speech.enabled is False
    assert config.speech.stt.provider == ""
    assert config.speech.tts.provider == ""
    assert config.soul.voice.speech.enabled is False


def test_a_brand_spelling_with_hyphens_resolves() -> None:
    provider = build_tts(SpeechTarget(provider="GPT-SoVITS", base_url="http://speech.local"))

    assert isinstance(provider, OpenAICompatibleTTS)
