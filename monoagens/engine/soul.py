"""The soul: identity, roles, locale, persona, and voice tuning loaded from `soul.yaml`.

The engine owns the mechanisms (affect, style, character, humanize); the soul owns their
settings. `soul.voice` is the one place where behavior is tuned, while `soul.yaml` prose and
examples remain the only character source (ADR-0001).
"""


import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, overload

import yaml

from . import clock
from .i18n import available, catalog, normalize_language, t

log = logging.getLogger(__name__)

PRONOUN_KEY = "soul.pronoun.default"
PROFANITY = ("free", "avoid")
SAFETY = ("inherit", "off")

# Seed for `monoagens init` and the tracked root `soul.yaml`.
DEFAULT_SOUL_YAML = """\
# MonoAgens soul definition. One database = one soul.
name: MonoAgens
description: A persistent agent that can inhabit any body or role.
language: {language}
locale: {locale}
timezone: {timezone}
pronouns: ""
mode: assistant
modes:
  - assistant
  - companion
  - advisor

background: |
  MonoAgens is a persistent agent: it keeps context across conversations, learns from memory,
  and can inhabit an assistant, companion, advisor, or robot body.

persona: |
  ## Personality
  Direct, calm, and curious. Says what it knows, names what it does not, and asks before
  acting outside its scope. Helpful without being performative.

# Voice tuning: behavior lives here, not in engine config; each block governs itself.
voice:
  sampling:
    daily_temperature: 0.9
    professional_temperature: 0.3
    top_p:
    top_k:
    min_p:
  affect:
    enabled: true
  style:
    enabled: true
  character:
    enabled: false
  humanize:
    enabled: true
  profanity: avoid
  safety: inherit

# Behavior rules (ADR-0008 §4.3): a rule becomes `kind=rule` memory only from this document,
# after it is restated on two separate days. One line each, no names and no secrets.
rules:
  - Default to the soul language, or match the sender's language.
  - Prefer evidence over guesses; search when a claim is current, local, numeric, or disputed.
"""


@dataclass(frozen=True)
class SamplingConfig:
    """Behavior sampling; empty optional values mean the provider default (ADR-0003)."""

    daily_temperature: float = 0.9
    professional_temperature: float = 0.3
    top_p: float | None = None
    top_k: int | None = None
    min_p: float | None = None


@dataclass(frozen=True)
class VibeConfig:
    enabled: bool = True
    attack: float = 0.35
    baseline_influence: float = 0.05
    hold_seconds: float = 1.2
    tick_seconds: float = 1.0


@dataclass(frozen=True)
class AffectConfig:
    enabled: bool = True
    volatility: str = "expressive"
    grudge_half_life_hours: float = 1.5
    vibe: VibeConfig = field(default_factory=VibeConfig)


@dataclass(frozen=True)
class StyleConfig:
    enabled: bool = True
    max_chars: int = 800


@dataclass(frozen=True)
class CharacterConfig:
    """Polish only: no level table, no gender target, no second rewrite (ADR-0001)."""

    enabled: bool = False
    max_chars: int = 400
    max_tokens: int = 500
    temperature: float = 0.7


@dataclass(frozen=True)
class HumanizeConfig:
    enabled: bool = True
    cpms: float = 25.0
    min_delay: float = 0.5
    max_delay: float = 5.0
    between_delay: float = 0.7
    max_bubbles: int = 3
    reaction_stickers: tuple[str, ...] = ()


@dataclass(frozen=True)
class VoiceSpeechConfig:
    """P3.4 §2: names only. Endpoints and keys live in `engine.speech`, never in the soul."""

    enabled: bool = False
    stt: str = ""
    tts: str = ""


@dataclass(frozen=True)
class VoiceConfig:
    """Each sub-block governs itself; sampling/profanity/safety are request-level."""

    sampling: SamplingConfig = field(default_factory=SamplingConfig)
    affect: AffectConfig = field(default_factory=AffectConfig)
    style: StyleConfig = field(default_factory=StyleConfig)
    character: CharacterConfig = field(default_factory=CharacterConfig)
    humanize: HumanizeConfig = field(default_factory=HumanizeConfig)
    profanity: str = "avoid"
    safety: str = "inherit"
    # A plain default, not a factory: the provider seam is no `voice_parts` toggle, and the
    # catalog indexes only sub-blocks that expose a factory-built `enabled` switch.
    speech: VoiceSpeechConfig = VoiceSpeechConfig()


@dataclass(frozen=True)
class Soul:
    language: str
    locale: str
    timezone: str
    name: str = "MonoAgens"
    description: str = ""
    pronouns: str = ""
    mode: str = "assistant"
    modes: tuple[str, ...] = ("assistant",)
    background: str = ""
    persona: str = ""
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    examples: tuple[dict[str, str], ...] = ()
    # ADR-0008 §4.3: behavior rules live in the soul document, and only there.
    rules: tuple[str, ...] = ()
    source: Path | None = None

    def __post_init__(self) -> None:
        """Validate required values, then resolve an empty pronoun from the catalog."""
        normalize_language(self.language, allow_any=True)
        if not self.locale.strip():
            raise ValueError("soul locale is required; `monoagens init` writes it")
        clock.zone(self.timezone)
        if not self.pronouns:
            object.__setattr__(self, "pronouns", default_pronoun(self.language))


def default_pronoun(language: str) -> str:
    """`soul.pronoun.default` for one catalog; a bare code falls back to English."""
    code = normalize_language(language, allow_any=True)
    try:
        messages = catalog(code)
    except ValueError:
        messages = catalog("en")
    if PRONOUN_KEY not in messages:
        raise ValueError(f"language catalog '{code}' is missing the required key '{PRONOUN_KEY}'")
    return messages[PRONOUN_KEY]


def language_line(soul: Soul) -> str:
    """Model-facing instruction from the soul catalog, with a language-agnostic fallback."""
    code = normalize_language(soul.language, allow_any=True)
    base = t("soul.reply_language", code) if code in available() else f"Reply in '{code}'."
    return (
        f"{base} Use this as the default language, or match the sender's language "
        "and casual register when they write in another language."
    )


def _text(value: object) -> str:
    return str(value or "").strip()


def _bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("true", "yes", "on", "1"):
            return True
        if text in ("false", "no", "off", "0"):
            return False
    return default


def _choice(value: object, default: str, allowed: tuple[str, ...], key: str) -> str:
    if isinstance(value, bool):
        text = "on" if value else "off"
    else:
        text = _text(value).lower() or default
    if text not in allowed:
        raise ValueError(f"soul {key} must be one of {', '.join(allowed)}")
    return text


@overload
def _number(
    value: object,
    default: float,
    key: str,
    *,
    low: float,
    high: float,
    optional: Literal[False] = False,
) -> float: ...


@overload
def _number(
    value: object,
    default: float,
    key: str,
    *,
    low: float,
    high: float,
    optional: Literal[True],
) -> float | None: ...


def _number(
    value: object,
    default: float,
    key: str,
    *,
    low: float,
    high: float,
    optional: bool = False,
) -> float | None:
    if value in (None, "", "null", "none", "None"):
        return None if optional else float(default)
    if isinstance(value, bool):
        raise ValueError(f"soul {key} must be a number")
    try:
        number = float(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"soul {key} must be a number") from exc
    if not math.isfinite(number) or number < low or number > high:
        raise ValueError(f"soul {key} must be between {low:g} and {high:g}")
    return number


def _integer(value: object, default: int, key: str, *, low: int, high: int) -> int:
    if value in (None, "", "null", "none", "None"):
        return int(default)
    if isinstance(value, bool):
        raise ValueError(f"soul {key} must be an integer")
    try:
        number = int(float(str(value)))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"soul {key} must be an integer") from exc
    if number < low or number > high:
        raise ValueError(f"soul {key} must be between {low} and {high}")
    return number


def _optional_integer(value: object, key: str, *, low: int, high: int) -> int | None:
    if value in (None, "", "null", "none", "None"):
        return None
    return _integer(value, 0, key, low=low, high=high)


def _section(raw: object) -> Mapping[str, object]:
    return raw if isinstance(raw, Mapping) else {}


# ADR-0008 §4.4: a mistyped soul key fails at boot instead of being silently ignored.
SOUL_SCHEMA: dict[str, object] = {
    "name": None,
    "description": None,
    "language": None,
    "locale": None,
    "timezone": None,
    "pronouns": None,
    "mode": None,
    "modes": None,
    "role": None,
    "roles": None,
    "background": None,
    "persona": None,
    "examples": None,
    "rules": None,
    "voice": {
        "sampling": {
            "daily_temperature": None,
            "professional_temperature": None,
            "top_p": None,
            "top_k": None,
            "min_p": None,
        },
        "affect": {
            "enabled": None,
            "volatility": None,
            "grudge_half_life_hours": None,
            "vibe": {
                "enabled": None,
                "attack": None,
                "baseline_influence": None,
                "hold_seconds": None,
                "tick_seconds": None,
            },
        },
        "style": {"enabled": None, "max_chars": None},
        "character": {"enabled": None, "max_chars": None, "max_tokens": None, "temperature": None},
        "humanize": {
            "enabled": None,
            "cpms": None,
            "min_delay": None,
            "max_delay": None,
            "between_delay": None,
            "max_bubbles": None,
            "reaction_stickers": None,
        },
        "profanity": None,
        "safety": None,
        "speech": {"enabled": None, "stt": None, "tts": None},
    },
}


def _unknown_keys(mapping: Mapping, schema: Mapping[str, object], label: str, path: str) -> None:
    """Reject an unknown soul key and name the file; a list entry is not a mapping."""
    found: list[str] = []
    for key, value in mapping.items():
        if key not in schema:
            found.append(f"{label}.{key}")
            continue
        nested = schema[key]
        if isinstance(nested, dict) and isinstance(value, Mapping):
            _unknown_keys(value, nested, f"{label}.{key}", path)
    if found:
        raise ValueError(
            f"{path}: unknown soul key(s): {', '.join(sorted(found))}; remove them or fix "
            "the spelling"
        )


def _sampling(raw: object) -> SamplingConfig:
    body = _section(raw)
    return SamplingConfig(
        daily_temperature=_number(
            body.get("daily_temperature"), 0.9, "voice.sampling.daily_temperature",
            low=0.0, high=2.0,
        ),
        professional_temperature=_number(
            body.get("professional_temperature"), 0.3, "voice.sampling.professional_temperature",
            low=0.0, high=2.0,
        ),
        top_p=_number(body.get("top_p"), 0.0, "voice.sampling.top_p", low=0.0, high=1.0, optional=True),
        top_k=_optional_integer(body.get("top_k"), "voice.sampling.top_k", low=1, high=1000),
        min_p=_number(body.get("min_p"), 0.0, "voice.sampling.min_p", low=0.0, high=1.0, optional=True),
    )


def _affect(raw: object) -> AffectConfig:
    body = _section(raw)
    vibe = _section(body.get("vibe"))
    return AffectConfig(
        enabled=_bool(body.get("enabled"), True),
        volatility=_text(body.get("volatility")) or "expressive",
        grudge_half_life_hours=_number(
            body.get("grudge_half_life_hours"), 1.5,
            "voice.affect.grudge_half_life_hours", low=0.0, high=8760.0,
        ),
        vibe=VibeConfig(
            enabled=_bool(vibe.get("enabled"), True),
            attack=_number(vibe.get("attack"), 0.35, "voice.affect.vibe.attack", low=0.0, high=1.0),
            baseline_influence=_number(
                vibe.get("baseline_influence"), 0.05,
                "voice.affect.vibe.baseline_influence", low=0.0, high=1.0,
            ),
            hold_seconds=_number(
                vibe.get("hold_seconds"), 1.2, "voice.affect.vibe.hold_seconds",
                low=0.0, high=3600.0,
            ),
            tick_seconds=_number(
                vibe.get("tick_seconds"), 1.0, "voice.affect.vibe.tick_seconds",
                low=0.1, high=3600.0,
            ),
        ),
    )


def _style(raw: object) -> StyleConfig:
    body = _section(raw)
    return StyleConfig(
        enabled=_bool(body.get("enabled"), True),
        max_chars=_integer(body.get("max_chars"), 800, "voice.style.max_chars", low=0, high=100_000),
    )


def _character(raw: object) -> CharacterConfig:
    body = _section(raw)
    return CharacterConfig(
        enabled=_bool(body.get("enabled"), False),
        max_chars=_integer(body.get("max_chars"), 400, "voice.character.max_chars", low=80, high=100_000),
        max_tokens=_integer(body.get("max_tokens"), 500, "voice.character.max_tokens", low=1, high=100_000),
        temperature=_number(
            body.get("temperature"), 0.7, "voice.character.temperature", low=0.0, high=2.0
        ),
    )


def _humanize(raw: object) -> HumanizeConfig:
    body = _section(raw)
    stickers = body.get("reaction_stickers")
    if stickers is None:
        sticker_values: tuple[str, ...] = ()
    elif isinstance(stickers, (list, tuple)):
        sticker_values = tuple(_text(item) for item in stickers if _text(item))
    else:
        raise ValueError("soul voice.humanize.reaction_stickers must be a list")
    min_delay = _number(body.get("min_delay"), 0.5, "voice.humanize.min_delay", low=0.0, high=3600.0)
    max_delay = _number(body.get("max_delay"), 5.0, "voice.humanize.max_delay", low=0.0, high=3600.0)
    if max_delay < min_delay:
        raise ValueError("soul voice.humanize.max_delay must be >= min_delay")
    return HumanizeConfig(
        enabled=_bool(body.get("enabled"), True),
        cpms=_number(body.get("cpms"), 25.0, "voice.humanize.cpms", low=0.1, high=10_000.0),
        min_delay=min_delay,
        max_delay=max_delay,
        between_delay=_number(
            body.get("between_delay"), 0.7, "voice.humanize.between_delay", low=0.0, high=3600.0
        ),
        max_bubbles=_integer(body.get("max_bubbles"), 3, "voice.humanize.max_bubbles", low=1, high=20),
        reaction_stickers=sticker_values,
    )


def _voice(raw: object) -> VoiceConfig:
    body = _section(raw)
    return VoiceConfig(
        sampling=_sampling(body.get("sampling")),
        affect=_affect(body.get("affect")),
        style=_style(body.get("style")),
        character=_character(body.get("character")),
        humanize=_humanize(body.get("humanize")),
        profanity=_choice(body.get("profanity"), "avoid", PROFANITY, "voice.profanity"),
        safety=_choice(body.get("safety"), "inherit", SAFETY, "voice.safety"),
        speech=_voice_speech(body.get("speech")),
    )


def _voice_speech(raw: object) -> VoiceSpeechConfig:
    """Names are normalized here; `engine.config` decides whether they are configured."""
    body = _section(raw)
    return VoiceSpeechConfig(
        enabled=_bool(body.get("enabled"), False),
        stt=_text(body.get("stt")).lower().replace("-", "_"),
        tts=_text(body.get("tts")).lower().replace("-", "_"),
    )


def _examples(value: object) -> tuple[dict[str, str], ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        return ()
    found: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        user = _text(item.get("user"))
        soul = _text(item.get("soul"))
        if user or soul:
            found.append({"user": user, "soul": soul})
    return tuple(found)


def _rules(value: object) -> tuple[str, ...]:
    """`rules` is a list of one-line behavior rules; an empty entry is dropped, order kept."""
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        log.warning("soul rules must be a list; ignoring %s", type(value).__name__)
        return ()
    return tuple(_text(item) for item in value if _text(item))


def render_persona(soul: Soul) -> str:
    """Render the persona block in one fixed order, dropping every empty section."""
    lines = [f"# {soul.name}"]
    if soul.description:
        lines.append(soul.description)
    lines.append(f"Active mode: {soul.mode}")
    if len(soul.modes) > 1:
        lines.append(f"Available modes: {', '.join(soul.modes)}")
    if soul.locale:
        lines.append(f"Locale: {soul.locale}")
    if soul.background:
        lines.extend(["## Background", soul.background])
    if soul.persona:
        lines.append(soul.persona)
    if soul.examples:
        lines.append("## Dialogue examples")
        for example in soul.examples:
            if example.get("user"):
                lines.append(f"user: {example['user']}")
            if example.get("soul"):
                lines.append(f"{soul.name}: {example['soul']}")
    return "\n\n".join(lines).strip()


def _mapping(source: Path | str | Mapping[str, object]) -> dict:
    if isinstance(source, Mapping):
        return dict(source)
    path = Path(source).expanduser()
    try:
        body = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        log.warning("soul file not found: %s; using the default soul", path)
        return {}
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"soul file is invalid: {exc}") from exc
    if body is None:
        return {}
    if not isinstance(body, dict):
        raise ValueError(f"soul file must be a mapping: {path}")
    return body


def load_soul(
    source: Path | str | Mapping[str, object],
    *,
    source_path: Path | None = None,
) -> Soul:
    """Load and validate one soul definition; an unknown key is a load error."""
    raw = _mapping(source)
    _unknown_keys(raw, SOUL_SCHEMA, "soul", str(source_path or "soul.yaml"))
    name = _text(raw.get("name")) or "MonoAgens"
    # ADR-0006 §4.1: persona modes are not RBAC roles; `role`/`roles` stay a deprecated alias.
    legacy = "role" in raw or "roles" in raw
    mode = _text(raw.get("mode")) or _text(raw.get("role")) or "assistant"
    modes = tuple(
        _text(item) for item in (raw.get("modes") or raw.get("roles") or ()) if _text(item)
    )
    if legacy and not ("mode" in raw or "modes" in raw):
        log.warning(
            "%s: soul.role/soul.roles are deprecated; rename them to mode/modes",
            source_path or "soul.yaml",
        )
    if not modes:
        modes = (mode,)
    if mode not in modes:
        modes = (mode, *modes)
    for key in ("language", "locale", "timezone"):
        if not _text(raw.get(key)):
            raise ValueError(f"soul {key} is required; `monoagens init` writes it")
    soul = Soul(
        name=name,
        description=_text(raw.get("description")),
        language=normalize_language(raw.get("language"), allow_any=True),
        pronouns=_text(raw.get("pronouns")),
        mode=mode,
        modes=modes,
        locale=_text(raw.get("locale")),
        timezone=_text(raw.get("timezone")),
        background=_text(raw.get("background")),
        persona=_text(raw.get("persona")),
        voice=_voice(raw.get("voice")),
        examples=_examples(raw.get("examples")),
        rules=_rules(raw.get("rules")),
        source=source_path,
    )
    return Soul(
        name=soul.name,
        description=soul.description,
        language=soul.language,
        pronouns=soul.pronouns,
        mode=soul.mode,
        modes=soul.modes,
        locale=soul.locale,
        timezone=soul.timezone,
        background=soul.background,
        persona=render_persona(soul),
        voice=soul.voice,
        examples=soul.examples,
        rules=soul.rules,
        source=soul.source,
    )
