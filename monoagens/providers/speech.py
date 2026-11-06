"""Provider-agnostic speech seam: STT, TTS, and one realtime session, plus HTTP adapters.

The turn loop does not consume this module yet: it delivers the seam only. A body (a chat
gateway, the console, or a plugin) reads `engine.speech` plus `soul.voice.speech`, opens the
configured provider here, and passes audio bytes across this boundary.

Two wire shapes cover every configured endpoint. OpenAI and every local server that mirrors
that shape (GPT-SoVITS, F5-TTS, Kokoro, Piper, Faster-Whisper wrappers) share
`/audio/transcriptions` and `/audio/speech`; ElevenLabs TTS and Deepgram STT keep their own
path because their request bodies are theirs. A realtime session is a protocol-level
skeleton: the websocket belongs to a body, and this seam refuses clearly until it is wired.

Provider facts live here, never in engine code: a provider is an endpoint record selected by
name, and the engine only ever compares names it read from the operator's files.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import httpx

from .base import ProviderError

DEFAULT_TIMEOUT = 30.0


@dataclass(frozen=True)
class SpeechDefaults:
    """One speech endpoint record: where it lives and which wire it speaks."""

    base_url: str
    api_key_env: str
    model: str = ""
    wire: str = "openai"
    api_key_optional: bool = False  # a local endpoint needs no key


# STT endpoints. A local wrapper of Faster-Whisper speaks the OpenAI shape; use it by name
# when the operator runs it behind their own `base_url`.
STT_DEFAULTS: dict[str, SpeechDefaults] = {
    "openai": SpeechDefaults("https://api.openai.com/v1", "OPENAI_API_KEY", "whisper-1"),
    "deepgram": SpeechDefaults(
        "https://api.deepgram.com/v1", "DEEPGRAM_API_KEY", "nova-3", wire="deepgram"
    ),
    "faster_whisper": SpeechDefaults("", "SPEECH_API_KEY", api_key_optional=True),
    "local": SpeechDefaults("", "SPEECH_API_KEY", api_key_optional=True),
}

# TTS endpoints. GPT-SoVITS, F5-TTS, Kokoro, and Piper serve their own `base_url` through
# the OpenAI audio shape; ElevenLabs keeps its own path.
TTS_DEFAULTS: dict[str, SpeechDefaults] = {
    "openai": SpeechDefaults("https://api.openai.com/v1", "OPENAI_API_KEY", "gpt-4o-mini-tts"),
    "elevenlabs": SpeechDefaults(
        "https://api.elevenlabs.io/v1",
        "ELEVENLABS_API_KEY",
        "eleven_multilingual_v2",
        wire="elevenlabs",
    ),
    "gpt_sovits": SpeechDefaults("", "SPEECH_API_KEY", api_key_optional=True),
    "f5_tts": SpeechDefaults("", "SPEECH_API_KEY", api_key_optional=True),
    "kokoro": SpeechDefaults("", "SPEECH_API_KEY", api_key_optional=True),
    "piper": SpeechDefaults("", "SPEECH_API_KEY", api_key_optional=True),
    "local": SpeechDefaults("", "SPEECH_API_KEY", api_key_optional=True),
}

REALTIME_DEFAULTS: dict[str, SpeechDefaults] = {
    "gemini_live": SpeechDefaults(
        "https://generativelanguage.googleapis.com/v1beta",
        "GEMINI_API_KEY",
        wire="gemini_live",
    ),
    "openai_realtime": SpeechDefaults(
        "https://api.openai.com/v1", "OPENAI_API_KEY", wire="openai_realtime"
    ),
}

STT_PROVIDERS = frozenset(STT_DEFAULTS)
TTS_PROVIDERS = frozenset(TTS_DEFAULTS)
REALTIME_PROVIDERS = frozenset(REALTIME_DEFAULTS)


def provider_name(value: object) -> str:
    """A provider is an identifier: case and the brand's hyphen spelling never matter."""
    return str(value or "").strip().lower().replace("-", "_")

MIMETYPE_EXTENSIONS = {
    "audio/ogg": "ogg",
    "audio/opus": "ogg",
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/webm": "webm",
    "audio/mp4": "m4a",
    "audio/x-m4a": "m4a",
    "audio/m4a": "m4a",
    "audio/aac": "m4a",
    "audio/flac": "flac",
}


@dataclass(frozen=True)
class SpeechTarget:
    """The resolved choice of one speech role; the caller fills it from config and env."""

    provider: str
    base_url: str = ""
    api_key: str = ""
    api_key_env: str = ""
    model: str = ""
    language: str = ""
    voice: str = ""
    timeout_seconds: float = DEFAULT_TIMEOUT


@runtime_checkable
class STTProvider(Protocol):
    """Audio in, text out; the caller never learns which wire answered."""

    async def transcribe(
        self, audio: bytes, *, language: str = "", mimetype: str = "audio/ogg"
    ) -> str: ...


@runtime_checkable
class TTSProvider(Protocol):
    """Text in, one audio payload out; the caller never learns which wire answered."""

    async def synthesize(
        self, text: str, *, voice: str = "", language: str = ""
    ) -> bytes: ...


@runtime_checkable
class RealtimeVoiceSession(Protocol):
    """A live audio session: the body pumps frames, this seam only names the contract."""

    async def send_audio(self, audio: bytes, *, mimetype: str = "audio/pcm") -> None: ...

    async def receive(self) -> dict: ...

    async def close(self) -> None: ...


def _record(
    provider: str, defaults: Mapping[str, SpeechDefaults], kind: str
) -> SpeechDefaults:
    name = provider_name(provider)
    found = defaults.get(name)
    if found is None:
        known = ", ".join(sorted(defaults))
        raise ProviderError(f"unknown {kind} speech provider '{name}'; available: {known}")
    return found


def _base(name: str, target: SpeechTarget, record: SpeechDefaults, kind: str) -> str:
    found = str(target.base_url or "").strip().rstrip("/") or record.base_url.rstrip("/")
    if not found:
        raise ProviderError(
            f"{kind} provider '{name}' needs a base_url (a local server has no default)"
        )
    return found


def _key(name: str, target: SpeechTarget, record: SpeechDefaults, kind: str) -> str:
    env_name = str(target.api_key_env or "").strip() or record.api_key_env
    found = str(target.api_key or "").strip()
    if not found and env_name:
        found = str(os.environ.get(env_name, "")).strip()
    if found:
        return found
    if record.api_key_optional:
        return ""
    hint = f"set {env_name}" if env_name else "set engine.speech key variable"
    raise ProviderError(f"{kind} provider '{name}' needs an API key: {hint}")


def _model(target: SpeechTarget, record: SpeechDefaults) -> str:
    return str(target.model or "").strip() or record.model


def _timeout(target: SpeechTarget) -> float:
    return max(1.0, float(target.timeout_seconds or DEFAULT_TIMEOUT))


def _auth(api_key: str) -> dict[str, str]:
    return {"authorization": f"Bearer {api_key}"} if api_key else {}


def _error(response: httpx.Response, label: str) -> ProviderError:
    """Status and the provider's own message; never the API key or the audio."""
    try:
        body = response.json()
    except ValueError:
        body = {}
    detail = ""
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, Mapping):
            detail = str(error.get("message") or "")
        elif isinstance(error, str):
            detail = error
        if not detail:
            detail = str(body.get("detail") or body.get("message") or "")
    detail = detail or response.text[:500]
    return ProviderError(
        f"{label} {response.status_code}: {detail}"[:800],
        status_code=response.status_code,
        native=detail,
    )


def _filename(mimetype: str) -> str:
    """A real audio extension, never `.bin`: Whisper rejects unknown suffixes."""
    base = str(mimetype or "").split(";", 1)[0].strip().lower()
    fallback = "mp3" if base.startswith("audio/") else "bin"
    return f"audio.{MIMETYPE_EXTENSIONS.get(base, fallback)}"


class _HttpAdapter:
    """One AsyncClient per adapter unless the caller injects one (tests, a shared pool)."""

    def __init__(self, *, timeout: float, client: httpx.AsyncClient | None = None) -> None:
        self.timeout = float(timeout)
        self._client = client
        self._owned = client is None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and self._owned:
            await self._client.aclose()
            self._client = None


class OpenAICompatibleSTT(_HttpAdapter):
    """`POST {base_url}/audio/transcriptions`, the one shape every local server mirrors."""

    name = "openai-compatible"

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str = "",
        *,
        language: str = "",
        timeout: float = DEFAULT_TIMEOUT,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(timeout=timeout, client=client)
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.language = language

    async def transcribe(
        self, audio: bytes, *, language: str = "", mimetype: str = "audio/ogg"
    ) -> str:
        data = {"response_format": "json"}
        if self.model:
            data["model"] = self.model
        selected = str(language or self.language or "").strip()
        if selected:
            data["language"] = selected
        files = {"file": (_filename(mimetype), bytes(audio), mimetype)}
        response = await self.client.post(
            f"{self.base_url}/audio/transcriptions",
            headers=_auth(self.api_key),
            data=data,
            files=files,
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            raise _error(response, "speech transcription")
        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderError("speech transcription returned invalid JSON") from exc
        text = str((body or {}).get("text") or "").strip()
        if not text:
            raise ProviderError("speech transcription returned no text")
        return text


class OpenAICompatibleTTS(_HttpAdapter):
    """`POST {base_url}/audio/speech`: JSON in, audio bytes out."""

    name = "openai-compatible"

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str = "",
        *,
        voice: str = "",
        language_field: bool = False,
        timeout: float = DEFAULT_TIMEOUT,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(timeout=timeout, client=client)
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.voice = voice
        # A local server may read `language`; the cloud OpenAI endpoint rejects unknown fields.
        self.language_field = bool(language_field)

    async def synthesize(
        self, text: str, *, voice: str = "", language: str = ""
    ) -> bytes:
        body: dict = {
            "input": str(text),
            "voice": str(voice or self.voice or "alloy"),
            "response_format": "mp3",
        }
        if self.model:
            body["model"] = self.model
        if self.language_field and str(language or "").strip():
            body["language"] = str(language).strip()
        response = await self.client.post(
            f"{self.base_url}/audio/speech",
            headers=_auth(self.api_key),
            json=body,
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            raise _error(response, "speech synthesis")
        if not response.content:
            raise ProviderError("speech synthesis returned no audio")
        return response.content


class ElevenLabsTTS(_HttpAdapter):
    """`POST /v1/text-to-speech/{voice_id}`; the voice id is the model selector."""

    name = "elevenlabs"

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str = "",
        *,
        voice: str = "",
        timeout: float = DEFAULT_TIMEOUT,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(timeout=timeout, client=client)
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.voice = voice

    async def synthesize(
        self, text: str, *, voice: str = "", language: str = ""
    ) -> bytes:
        voice_id = str(voice or self.voice or "").strip()
        if not voice_id:
            raise ProviderError(
                "elevenlabs needs a voice id: set engine.speech.tts.voice or pass voice="
            )
        body: dict = {"text": str(text)}
        if self.model:
            body["model_id"] = self.model
        if str(language or "").strip():
            body["language_code"] = str(language).strip()
        response = await self.client.post(
            f"{self.base_url}/text-to-speech/{voice_id}",
            headers={"xi-api-key": self.api_key},
            json=body,
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            raise _error(response, "elevenlabs synthesis")
        if not response.content:
            raise ProviderError("elevenlabs returned no audio")
        return response.content


class DeepgramSTT(_HttpAdapter):
    """`POST /v1/listen` with the raw audio body; the transcript sits in `results.*`."""

    name = "deepgram"

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str = "",
        *,
        language: str = "",
        timeout: float = DEFAULT_TIMEOUT,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(timeout=timeout, client=client)
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model or "nova-3"
        self.language = language

    async def transcribe(
        self, audio: bytes, *, language: str = "", mimetype: str = "audio/ogg"
    ) -> str:
        params = {"model": self.model, "smart_format": "true"}
        selected = str(language or self.language or "").strip()
        if selected:
            params["language"] = selected
        response = await self.client.post(
            f"{self.base_url}/listen",
            headers={"authorization": f"Token {self.api_key}", "content-type": mimetype},
            params=params,
            content=bytes(audio),
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            raise _error(response, "deepgram transcription")
        try:
            body = response.json()
            text = body["results"]["channels"][0]["alternatives"][0]["transcript"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderError("deepgram returned no transcript") from exc
        found = str(text or "").strip()
        if not found:
            raise ProviderError("deepgram returned no transcript")
        return found


class _RealtimeSkeleton:
    """A protocol-level session: the websocket is a body concern (ADR-0044 seam only)."""

    provider = ""
    label = "realtime"

    def __init__(self, target: SpeechTarget) -> None:
        self.target = target

    @property
    def configured(self) -> bool:
        return bool(str(self.target.api_key or "").strip()) and bool(
            str(self.target.model or "").strip()
        )

    def _refusal(self) -> ProviderError:
        if not self.configured:
            return ProviderError(
                f"{self.label} session is not configured: an API key and a model are required"
            )
        return ProviderError(
            f"{self.label} transport is not wired: a body supplies the websocket and pumps "
            "frames through this session"
        )

    async def send_audio(self, audio: bytes, *, mimetype: str = "audio/pcm") -> None:
        raise self._refusal()

    async def receive(self) -> dict:
        raise self._refusal()

    async def close(self) -> None:
        return None


class GeminiLiveSession(_RealtimeSkeleton):
    """Gemini Live: `send_audio`/`receive`/`close`; unconfigured use is a clear refusal."""

    provider = "gemini_live"
    label = "gemini live"


class OpenAIRealtimeSession(_RealtimeSkeleton):
    """OpenAI Realtime: the same skeleton, refused until a body supplies the transport."""

    provider = "openai_realtime"
    label = "openai realtime"


def build_stt(
    target: SpeechTarget, *, client: httpx.AsyncClient | None = None
) -> STTProvider:
    """The STT adapter for the configured provider, or a `ProviderError` saying what is missing."""
    name = provider_name(target.provider)
    record = _record(name, STT_DEFAULTS, "STT")
    base_url = _base(name, target, record, "STT")
    api_key = _key(name, target, record, "STT")
    model = _model(target, record)
    timeout = _timeout(target)
    if record.wire == "deepgram":
        return DeepgramSTT(
            base_url,
            api_key,
            model,
            language=target.language,
            timeout=timeout,
            client=client,
        )
    return OpenAICompatibleSTT(
        base_url,
        api_key,
        model,
        language=target.language,
        timeout=timeout,
        client=client,
    )


def build_tts(
    target: SpeechTarget, *, client: httpx.AsyncClient | None = None
) -> TTSProvider:
    """The TTS adapter for the configured provider, or a `ProviderError` saying what is missing."""
    name = provider_name(target.provider)
    record = _record(name, TTS_DEFAULTS, "TTS")
    base_url = _base(name, target, record, "TTS")
    api_key = _key(name, target, record, "TTS")
    model = _model(target, record)
    timeout = _timeout(target)
    if record.wire == "elevenlabs":
        return ElevenLabsTTS(
            base_url,
            api_key,
            model,
            voice=target.voice,
            timeout=timeout,
            client=client,
        )
    return OpenAICompatibleTTS(
        base_url,
        api_key,
        model,
        voice=target.voice,
        language_field=record.api_key_optional,
        timeout=timeout,
        client=client,
    )


def build_realtime(target: SpeechTarget) -> RealtimeVoiceSession:
    """A realtime session for the named provider; the transport stays a body concern."""
    name = provider_name(target.provider)
    record = _record(name, REALTIME_DEFAULTS, "realtime")
    if record.wire == "gemini_live":
        return GeminiLiveSession(target)
    return OpenAIRealtimeSession(target)


__all__ = [
    "DEFAULT_TIMEOUT",
    "REALTIME_DEFAULTS",
    "REALTIME_PROVIDERS",
    "STT_DEFAULTS",
    "STT_PROVIDERS",
    "TTS_DEFAULTS",
    "TTS_PROVIDERS",
    "DeepgramSTT",
    "ElevenLabsTTS",
    "GeminiLiveSession",
    "OpenAICompatibleSTT",
    "OpenAICompatibleTTS",
    "OpenAIRealtimeSession",
    "RealtimeVoiceSession",
    "STTProvider",
    "SpeechDefaults",
    "SpeechTarget",
    "TTSProvider",
    "build_realtime",
    "build_stt",
    "build_tts",
    "provider_name",
]
