"""Language catalogs: chat-visible strings live in `monoagens/lang/<code>.yaml`."""


import os
from functools import cache
from pathlib import Path

import yaml

CATALOG_DIR = Path(__file__).resolve().parents[1] / "lang"
FALLBACK = "en"
ALIASES = {
    "en": "en",
    "english": "en",
    "id": "id",
    "in": "id",
    "indonesian": "id",
}


def available() -> tuple[str, ...]:
    """Language codes with a catalog file, sorted."""
    if not CATALOG_DIR.is_dir():
        return ()
    return tuple(sorted(path.stem for path in CATALOG_DIR.glob("*.yaml") if path.is_file()))


@cache
def catalog(code: str) -> dict[str, str]:
    """Load and validate one catalog; raise ValueError when unusable."""
    path = CATALOG_DIR / f"{code}.yaml"
    if not path.is_file():
        listing = ", ".join(available()) or "none"
        raise ValueError(f"language '{code}' has no catalog; available: {listing}")
    try:
        body = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"{path.name} is not valid YAML: {exc}") from exc
    messages = body.get("messages") if isinstance(body, dict) else None
    if not isinstance(messages, dict):
        raise ValueError(f"{path.name} must contain a 'messages' mapping")
    return {str(key): str(value) for key, value in messages.items()}


def reload_catalogs() -> None:
    """Drop the parsed catalogs so edited YAML files are re-read."""
    catalog.cache_clear()


def host_language() -> str:
    """The operator's language from the process locale; empty means nothing was stated."""
    for name in ("LC_ALL", "LC_MESSAGES", "LANGUAGE", "LANG"):
        raw = str(os.environ.get(name) or "").strip()
        if not raw:
            continue
        primary = raw.split(":")[0].split(".")[0].split("@")[0].strip().lower()
        if primary and primary not in ("c", "posix"):
            return primary.split("_")[0]
    return ""


def normalize_language(value: object, *, allow_any: bool = False) -> str:
    """Map aliases to an installed catalog code; `allow_any` keeps a bare language code."""
    text = str(value or "").strip().lower()
    if not text:
        raise ValueError("a language is required; pass a catalog code such as `en`")
    if text == "auto":
        text = host_language()
        if not text:
            raise ValueError("`auto` needs a process locale; pass a catalog code instead")
    code = ALIASES.get(text, text)
    if code not in available():
        primary = code.split("-")[0]
        if allow_any and len(primary) == 2 and primary.isalpha():
            return primary
        raise ValueError(
            f"language '{text}' has no catalog; available: {', '.join(available()) or 'none'}"
        )
    catalog(code)
    return code


def t(key: str, lang: str, default: str | None = None, **kwargs: object) -> str:
    """Format one catalog message, falling back to `en`, the default, or the key."""
    want = str(lang or "").strip().lower()
    for code in (ALIASES.get(want, want), FALLBACK):
        try:
            messages = catalog(code)
        except ValueError:
            continue
        template = messages.get(key)
        if template is not None:
            try:
                return template.format(**kwargs)
            except (KeyError, IndexError, ValueError):
                return template
    return key if default is None else default
