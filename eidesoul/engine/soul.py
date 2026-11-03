"""The soul: identity, roles, locale, and persona loaded from `soul.yaml`."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .i18n import catalog, normalize_language, t

log = logging.getLogger(__name__)

IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
MAX_PER_TURN = 6
GENDERS = ("male", "female", "nonbinary", "unspecified")
PRONOUN_KEY = "soul.pronoun.default"

# Seed for `eidesoul init` and the tracked root `soul.yaml`.
DEFAULT_SOUL_YAML = """\
# Eidesoul soul definition. One database = one soul.
name: Eidesoul
description: A persistent soul engine that can inhabit any body or role.
language: id
pronouns: ""
gender: unspecified
role: assistant
locale: id-ID

# background: |
#   Born and raised in ...
background: ""

# persona: |
#   ## Personality
#   ...
persona: ""
"""


@dataclass(frozen=True)
class SoulStyle:
    patterns: tuple[str, ...] = ()
    facts: tuple[str, ...] = ()
    registers: tuple[str, ...] = ()
    max_patterns: int = 0
    max_facts: int = 0


@dataclass(frozen=True)
class Soul:
    name: str = "Eidesoul"
    description: str = ""
    language: str = "id"
    pronouns: str = ""
    gender: str = "unspecified"
    role: str = "assistant"
    roles: tuple[str, ...] = ("assistant",)
    locale: str = "id-ID"
    background: str = ""
    persona: str = ""
    style: SoulStyle = field(default_factory=SoulStyle)
    examples: tuple[dict[str, str], ...] = ()
    source: Path | None = None

    def __post_init__(self) -> None:
        """Resolve an empty pronoun from the catalog; a missing key fails the boot."""
        if not self.pronouns:
            object.__setattr__(self, "pronouns", default_pronoun(self.language))


def default_pronoun(language: str) -> str:
    """`soul.pronoun.default` for one catalog; the key is required, so a boot without it fails."""
    code = normalize_language(language, "id")
    if PRONOUN_KEY not in catalog(code):
        raise ValueError(f"language catalog '{code}' is missing the required key '{PRONOUN_KEY}'")
    return t(PRONOUN_KEY, code)


def _flow(values: tuple[str, ...]) -> str:
    return "[" + ", ".join(values) + "]" if values else "[]"


def style_block(style: SoulStyle) -> str:
    """The `style:` YAML fragment written by `library resolve` and `init`."""
    return "\n".join(
        [
            "style:",
            f"  patterns: {_flow(style.patterns)}",
            f"  facts: {_flow(style.facts)}",
            f"  registers: {_flow(style.registers)}",
            f"  max_patterns: {style.max_patterns}",
            f"  max_facts: {style.max_facts}",
        ]
    )


def language_line(soul: Soul) -> str:
    """Model-facing instruction that pins the reply language to the soul catalog."""
    language = normalize_language(soul.language, "id")
    if language == "en":
        return "Reply in English."
    return "Reply in Indonesian (Bahasa Indonesia), matching the sender's casual register."


def _identifiers(value: object, key: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        items: list[object] = [value]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        raise ValueError(f"soul {key} must be a list of pack ids")
    found: list[str] = []
    for item in items:
        text = str(item).strip()
        if not text:
            continue
        if not IDENTIFIER.match(text):
            raise ValueError(f"soul {key} has an invalid id: {text!r}")
        found.append(text)
    return tuple(found)


def _clamp(value: object, key: str) -> int:
    if value is None:
        return 0
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"soul style.{key} must be an integer") from exc
    return max(0, min(MAX_PER_TURN, number))


def _text(value: object) -> str:
    return str(value or "").strip()


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


def _style(raw: object) -> SoulStyle:
    body = raw if isinstance(raw, Mapping) else {}
    return SoulStyle(
        patterns=_identifiers(body.get("patterns"), "style.patterns"),
        facts=_identifiers(body.get("facts"), "style.facts"),
        registers=_identifiers(body.get("registers"), "style.registers"),
        max_patterns=_clamp(body.get("max_patterns"), "max_patterns"),
        max_facts=_clamp(body.get("max_facts"), "max_facts"),
    )


def render_persona(soul: Soul) -> str:
    """Render the persona block in one fixed order, dropping every empty section."""
    lines = [f"# {soul.name}"]
    if soul.description:
        lines.append(soul.description)
    lines.append(f"Active role: {soul.role}")
    if len(soul.roles) > 1:
        lines.append(f"Available roles: {', '.join(soul.roles)}")
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
    """Load and validate one soul definition; unknown keys are ignored."""
    raw = _mapping(source)
    name = _text(raw.get("name")) or "Eidesoul"
    gender = _text(raw.get("gender")).lower() or "unspecified"
    if gender not in GENDERS:
        log.warning("soul gender %r is not one of %s; using unspecified", gender, GENDERS)
        gender = "unspecified"
    role = _text(raw.get("role")) or "assistant"
    roles = tuple(_text(item) for item in raw.get("roles") or () if _text(item))
    if not roles:
        roles = (role,)
    if role not in roles:
        roles = (role, *roles)
    style = _style(raw.get("style"))
    soul = Soul(
        name=name,
        description=_text(raw.get("description")),
        language=normalize_language(raw.get("language"), "id"),
        pronouns=_text(raw.get("pronouns")),
        gender=gender,
        role=role,
        roles=roles,
        locale=_text(raw.get("locale")) or "id-ID",
        background=_text(raw.get("background")),
        persona=_text(raw.get("persona")),
        style=style,
        examples=_examples(raw.get("examples")),
        source=source_path,
    )
    return Soul(
        name=soul.name,
        description=soul.description,
        language=soul.language,
        pronouns=soul.pronouns,
        gender=soul.gender,
        role=soul.role,
        roles=soul.roles,
        locale=soul.locale,
        background=soul.background,
        persona=render_persona(soul),
        style=soul.style,
        examples=soul.examples,
        source=soul.source,
    )
