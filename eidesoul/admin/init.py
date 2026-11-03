"""`eidesoul init`: write the four host files, interactively on a terminal.

The defaults come from `templates.py` only (ADR-0030 §4.3); this module adds the operator's
answers, the review screen, and write-then-rename so an interrupted run leaves nothing behind.
Secrets never reach YAML: `.env` holds the names, and the database URL is the one value written
there, at `0600`.
"""


import contextlib
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import TextIO, cast

import yaml
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from ..engine import clock
from ..engine.config import Config, ConfigError
from ..engine.i18n import host_language, normalize_language
from ..providers import PROVIDERS
from . import templates

PRIVATE_FILES = (".env", "roles.yaml")
HOST_FILES = ("config.yaml", "roles.yaml", "soul.yaml", ".env")
STEPS = ("provider", "model", "database", "administrator", "voice layer")
# The voice layer (ADR-0027 §4.3): the operator's name for a part, then its config section.
VOICE_PARTS = ("emotion", "humanize", "character")
VOICE_KEYS = {"emotion": "affect", "humanize": "humanize", "character": "character"}
LLM_KEYS = ("provider", "base_url", "api_key_env", "model")
DEFAULT_LOG = Path.home() / ".eidesoul" / "logs" / "install.log"
LOGO_SLOT = "[logo]"


class InitCancelled(Exception):
    """The operator stopped the wizard; nothing new was left on disk."""


@dataclass
class Answers:
    """What the operator answered; every field lands in exactly one place."""

    provider: str = ""
    model: str = ""
    database_url: str = ""
    label: str = ""
    voice_off: tuple[str, ...] = ()
    language: str = ""
    locale: str = ""
    timezone: str = ""


# --- values -------------------------------------------------------------------------------


def host_locale() -> str:
    """The operator's locale from the process environment; empty when nothing states one."""
    for name in ("LC_ALL", "LC_MESSAGES", "LANGUAGE", "LANG"):
        raw = str(os.environ.get(name) or "").strip()
        if not raw:
            continue
        text = raw.split(":")[0].split(".")[0].split("@")[0].strip().replace("_", "-")
        if text and text.lower() not in ("c", "posix"):
            return text
    return ""


def host_timezone() -> str:
    """The operator's zone: `TZ` when set, else the `/etc/localtime` target; empty otherwise."""
    stated = str(os.environ.get("TZ") or "").strip()
    if stated:
        return stated
    with contextlib.suppress(OSError):
        target = str(Path("/etc/localtime").resolve())
        marker = "zoneinfo/"
        if marker in target:
            return target.split(marker, 1)[1]
    return ""


def voice_off(value: str | tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """The voice parts turned off: `on`/`off`, or a comma list such as `character,humanize`."""
    if isinstance(value, str):
        raw = value.replace(" ", ",").split(",")
    else:
        raw = [str(part) for part in value]
    names = [part.strip().lower() for part in raw if part.strip()]
    if not names or names == ["on"]:
        return ()
    if names in (["off"], ["none"], ["all"]):
        return VOICE_PARTS
    unknown = sorted(set(names) - set(VOICE_PARTS))
    if unknown:
        choices = ", ".join(VOICE_PARTS)
        raise ValueError(
            f"unknown voice layer part '{unknown[0]}'; choose on, off, or one of: {choices}"
        )
    return tuple(part for part in VOICE_PARTS if part in names)


def validate_provider(name: str) -> str | None:
    """`None` when the name is one of the providers; otherwise the operator message."""
    if name in PROVIDERS:
        return None
    return f"unknown provider '{name}'; choose one of: {', '.join(sorted(PROVIDERS))}"


def validate_database_url(url: str) -> str | None:
    """Blank is allowed (fill `.env` later); otherwise it must be a Postgres URL."""
    if not url.strip():
        return None
    try:
        parsed = make_url(url)
    except (ArgumentError, ValueError) as exc:
        return f"not a database URL: {exc}"
    if parsed.get_backend_name() != "postgresql":
        return "the database URL must be postgresql+psycopg:// (Postgres with pgvector)"
    if not parsed.database:
        return "the database URL needs a database name"
    return None


def _probe(provider: str) -> Config:
    """A read-only stand-in for `Config` so `admin/models.py` stays the only list fetcher."""
    defaults = PROVIDERS[provider]
    llm = SimpleNamespace(
        base_url=defaults.base_url,
        api_key_env=defaults.api_key_env,
        models_url=defaults.models_url,
        api_key_optional=defaults.api_key_optional,
    )

    def api_key() -> str:
        found = os.environ.get(llm.api_key_env)
        if found:
            return found
        if llm.api_key_optional:
            return "local"
        raise KeyError(llm.api_key_env)

    # The stand-in carries only `.llm` and `.api_key()`, which is all `list_models` reads.
    return cast(Config, SimpleNamespace(llm=llm, api_key=api_key))


def model_ids(provider: str) -> list[str]:
    """The provider's served model ids; raises when the list cannot be reached."""
    from . import models as admin_models

    return admin_models.list_models(_probe(provider), timeout=5.0)


def validate_model(provider: str, model: str) -> str | None:
    """A model outside the provider's reachable list; an unreachable list is not an error."""
    if not model.strip() or provider not in PROVIDERS:
        return None
    try:
        served = model_ids(provider)
    except (ConfigError, KeyError, ValueError, OSError):
        return None
    if not served or model in served:
        return None
    sample = ", ".join(served[:5])
    return f"model '{model}' is not in {provider}'s list; served includes: {sample}"


# --- the files ----------------------------------------------------------------------------


def _env_names(text: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        pairs.append((name.strip(), value.strip()))
    return pairs


def _set_env(text: str, name: str, value: str) -> str:
    """Set one name in an env file, keeping its order and every other line."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.partition("=")[0].strip() == name and "=" in line:
            lines[index] = f"{name}={value}"
            return "\n".join(lines) + "\n"
    lines.append(f"{name}={value}")
    return "\n".join(lines) + "\n"


def build(
    *,
    provider: str,
    model: str = "",
    database_url: str = "",
    label: str = "",
    voice: str | tuple[str, ...] = "on",
    language: str = "",
    locale: str = "",
    timezone: str = "",
) -> dict[str, str]:
    """The four files: `templates.files()` plus the operator's answers, nothing else."""
    locale = locale or host_locale()
    language = language or (locale.split("-")[0] if locale else "") or host_language()
    language = normalize_language(language)
    locale = locale or language
    timezone = timezone or host_timezone()
    if not timezone:
        raise ValueError("a timezone is required; pass --timezone <IANA name>")
    clock.zone(timezone)
    contents = templates.files(
        provider=provider,
        model=model,
        language=language,
        locale=locale,
        timezone=timezone,
    )
    off = voice_off(voice)
    if off:
        body = yaml.safe_load(contents["config.yaml"])
        for part in off:
            body["engine"][VOICE_KEYS[part]]["enabled"] = False
        contents["config.yaml"] = templates.dump(body)
    if label.strip():
        roles = yaml.safe_load(contents["roles.yaml"])
        roles["roles"]["administrator"]["label"] = label.strip()
        contents["roles.yaml"] = templates.dump(roles)
    if database_url.strip():
        contents[".env"] = _set_env(contents[".env"], "EIDESOUL_DATABASE_URL", database_url.strip())
    return contents


def mode_of(name: str) -> int:
    """The mode a host file lands with: `.env` and `roles.yaml` stay private."""
    return 0o600 if name in PRIVATE_FILES else 0o644


def _atomic_write(path: Path, text: str, mode: int) -> None:
    """Write a sibling temp file, fsync, then rename: a reader never sees a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp")
    with contextlib.suppress(FileNotFoundError):
        temp.unlink()
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, mode)
        os.replace(temp, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temp.unlink()


def _missing_chain(path: Path) -> list[Path]:
    """Directories that `mkdir -p` will create, outermost first."""
    missing: list[Path] = []
    current = path
    while not current.exists() and current != current.parent:
        missing.append(current)
        current = current.parent
    return list(reversed(missing))


def _merge(name: str, existing: str, plan: str) -> str:
    """`change`: the operator's answers land in the existing file, everything else stays."""
    if name == ".env":
        merged = existing
        for env_name, value in _env_names(plan):
            if value:
                merged = _set_env(merged, env_name, value)
        return merged
    if name == "soul.yaml":
        return plan
    try:
        old = yaml.safe_load(existing) or {}
        new = yaml.safe_load(plan) or {}
    except yaml.YAMLError:
        return plan
    if not isinstance(old, dict) or not isinstance(new, dict):
        return plan
    if name == "config.yaml":
        engine = old.setdefault("engine", {})
        llm = engine.setdefault("llm", {})
        for key in LLM_KEYS:
            if key in new["engine"]["llm"]:
                llm[key] = new["engine"]["llm"][key]
        old.setdefault("database", {})["url_env"] = new["database"]["url_env"]
        for part in VOICE_PARTS:
            section = VOICE_KEYS[part]
            engine.setdefault(section, {})["enabled"] = new["engine"][section]["enabled"]
        return templates.dump(old)
    label = ((new.get("roles") or {}).get("administrator") or {}).get("label")
    if label:
        old.setdefault("roles", {}).setdefault("administrator", {})["label"] = label
    return templates.dump(old)


def write_files(
    folder: str | Path,
    contents: dict[str, str],
    decisions: dict[str, str] | None = None,
) -> tuple[list[Path], list[Path]]:
    """Write the plan through rename; an interruption rolls the new files back."""
    base = Path(folder).expanduser().resolve()
    decided = decisions or {}
    created_dirs = _missing_chain(base)
    created_dirs += _missing_chain(base / ".eidesoul")
    created_dirs += _missing_chain(base / ".eidesoul" / "workspace")
    created_files: list[Path] = []
    written: list[Path] = []
    skipped: list[Path] = []
    try:
        for path in created_dirs:
            path.mkdir(parents=True, exist_ok=True)
        (base / ".eidesoul" / "workspace").mkdir(parents=True, exist_ok=True)
        for name in HOST_FILES:
            path = base / name
            decision = decided.get(name, "new")
            if decision == "keep" or (decision == "new" and path.exists()):
                skipped.append(path)
                continue
            text = contents[name]
            if decision == "change" and path.exists():
                text = _merge(name, path.read_text(encoding="utf-8"), text)
            existed = path.exists()
            _atomic_write(path, text, mode_of(name))
            if not existed:
                created_files.append(path)
            written.append(path)
    except BaseException:
        for path in created_files:
            with contextlib.suppress(OSError):
                path.unlink()
        for path in reversed(created_dirs):
            with contextlib.suppress(OSError):
                path.rmdir()
        raise
    return written, skipped


def init(
    folder: str | Path,
    *,
    force: bool = False,
    provider: str,
    model: str = "",
    database_url: str = "",
    label: str = "",
    voice_layer: str = "on",
    language: str = "",
    locale: str = "",
    timezone: str = "",
) -> tuple[list[Path], list[Path]]:
    """Write the scaffold without prompting; return (written, skipped)."""
    error = validate_provider(provider) or validate_database_url(database_url)
    if error:
        raise ValueError(error)
    contents = build(
        provider=provider,
        model=model,
        database_url=database_url,
        label=label,
        voice=voice_layer,
        language=language,
        locale=locale,
        timezone=timezone,
    )
    decisions: dict[str, str] = dict.fromkeys(HOST_FILES, "replace" if force else "new")
    return write_files(folder, contents, decisions)


# --- reporting ----------------------------------------------------------------------------


def report(
    base: str | Path,
    written: list[Path],
    skipped: list[Path],
    *,
    provider: str,
    model: str = "",
    log_path: str | Path | None = None,
) -> str:
    """The receipt: files with modes, the next commands, and where the log lives."""
    lines = [f"Eidesoul scaffold: {Path(base).expanduser().resolve()}"]
    for path in written:
        lines.append(f"  wrote  {path} ({mode_of(path.name):04o})")
    for path in skipped:
        lines.append(f"  kept   {path} (already exists)")
    key_env = PROVIDERS[provider].api_key_env if provider in PROVIDERS else ""
    steps = [
        f"fill .env ({key_env}, EIDESOUL_DATABASE_URL)"
        if key_env
        else "fill .env (EIDESOUL_DATABASE_URL)"
    ]
    if not model:
        steps.append(
            "choose a model: `eidesoul models` lists the ids the provider serves, then set "
            "engine.llm.model (rerun `eidesoul init --model <id> --force` or edit config.yaml)"
        )
    steps.extend(["eidesoul db upgrade", "eidesoul run"])
    lines.append("Next steps:")
    lines.extend(f"  {index}. {step}" for index, step in enumerate(steps, start=1))
    lines.append(f"Log: {Path(log_path) if log_path else DEFAULT_LOG}")
    return "\n".join(lines)


# --- the wizard ---------------------------------------------------------------------------


def banner(color: bool = False) -> list[str]:
    """The fixed-width header with the logo slot: the mark lands later, the slot does not move."""
    title = "EIDESOUL  a soul engine you run on your own machine"
    slot = f"{LOGO_SLOT}    reserved for the mark; text-only until it lands"
    if color:
        title = f"\033[1m{title}\033[0m"
    return ["-" * 56, f"  {title}", f"  {slot}", "-" * 56]


def _rail(step: int, color: bool = False) -> list[str]:
    """`1/5 provider` with finished steps `+`, the current one `*`, the rest `-`."""
    marks = [
        f"{'*' if index == step else ('+' if index < step else '-')} {name}"
        for index, name in enumerate(STEPS)
    ]
    lines = [f"  [{step + 1}/{len(STEPS)}] {STEPS[step]}"]
    row = "    "
    for item in marks:
        if row.strip() and len(row) + len(item) > 56:
            lines.append(row.rstrip())
            row = "    "
        row += f"{item}  "
    lines.append(row.rstrip())
    if color:
        lines[0] = f"\033[36m{lines[0]}\033[0m"
    return lines


def _say(stdout: TextIO, text: str = "") -> None:
    stdout.write(text + "\n")
    stdout.flush()


def _say_lines(stdout: TextIO, lines: list[str]) -> None:
    for line in lines:
        _say(stdout, line)


def _ask(
    stdin: TextIO,
    stdout: TextIO,
    label: str,
    default: str = "",
    *,
    show_default: bool = True,
) -> str:
    hint = f" [{default}]" if default and show_default else (" [kept]" if default else "")
    stdout.write(f"{label}{hint}: ")
    stdout.flush()
    line = stdin.readline()
    if line == "":
        raise InitCancelled("input closed before the wizard finished; nothing was written")
    value = line.strip()
    return value if value else default


def _ask_provider(state: Answers, stdin: TextIO, stdout: TextIO) -> None:
    names = sorted(PROVIDERS)
    default = "opencode" if "opencode" in PROVIDERS else names[0]
    _say(stdout, "  which provider serves your model?")
    width = max(len(name) for name in names) + 6
    for start in range(0, len(names), 3):
        row = names[start : start + 3]
        offered = "".join(
            f"{index}) {name:<{width}}" for index, name in enumerate(row, start + 1)
        )
        _say(stdout, f"  {offered}")
    while True:
        answer = _ask(stdin, stdout, f"  provider [1-{len(names)} or name]", default)
        picked = answer.lower()
        if answer.isdigit() and 1 <= int(answer) <= len(names):
            picked = names[int(answer) - 1]
        error = validate_provider(picked)
        if error is None:
            state.provider = picked
            return
        _say(stdout, f"  {error}; try again")


def _ask_model(state: Answers, stdin: TextIO, stdout: TextIO) -> None:
    _say(stdout, "  model id (blank leaves it for later; `eidesoul models` lists them)")
    while True:
        answer = _ask(stdin, stdout, "  model", state.model)
        error = validate_model(state.provider, answer)
        if error is None:
            state.model = answer
            return
        _say(stdout, f"  {error}; try again or leave it blank")


def _ask_database(state: Answers, stdin: TextIO, stdout: TextIO) -> None:
    _say(stdout, "  Postgres URL for EIDESOUL_DATABASE_URL (blank fills .env later)")
    while True:
        answer = _ask(
            stdin, stdout, "  database URL", state.database_url, show_default=False
        )
        error = validate_database_url(answer)
        if error is None:
            state.database_url = answer
            return
        _say(stdout, f"  {error}; try again or leave it blank")


def _ask_label(state: Answers, stdin: TextIO, stdout: TextIO) -> None:
    _say(stdout, "  label for the first administrator (roles.yaml; blank keeps it empty)")
    state.label = _ask(stdin, stdout, "  administrator label", state.label)


def _ask_voice(state: Answers, stdin: TextIO, stdout: TextIO) -> None:
    _say(stdout, "  keep emotion, humanize, and character on?")
    _say(stdout, "  (`on`, `off`, or the parts to turn off, e.g. `character`)")
    default = "on" if not state.voice_off else ",".join(state.voice_off)
    while True:
        answer = _ask(stdin, stdout, "  voice layer", default)
        try:
            state.voice_off = voice_off(answer)
            return
        except ValueError as exc:
            _say(stdout, f"  {exc}; try again")


ASKERS = (_ask_provider, _ask_model, _ask_database, _ask_label, _ask_voice)


def review_lines(
    base: Path,
    contents: dict[str, str],
    decisions: dict[str, str],
) -> list[str]:
    """Every file and every answer on one screen; `.env` values are never printed."""
    config = yaml.safe_load(contents["config.yaml"])
    roles = yaml.safe_load(contents["roles.yaml"])
    llm = config["engine"]["llm"]
    lines = ["", "  Review", f"    directory  {base}"]
    for name in HOST_FILES:
        status = decisions.get(name, "new")
        lines.append(f"    {name:<12} {status:<8} {mode_of(name):04o}")
        if name == "config.yaml":
            lines.extend(
                [
                    f"      engine.llm.provider     {llm['provider']}",
                    f"      engine.llm.base_url     {llm['base_url']}",
                    f"      engine.llm.api_key_env  {llm['api_key_env']}",
                    f"      engine.llm.model        {llm['model'] or '(not set)'}",
                    f"      database.url_env        {config['database']['url_env']}",
                ]
            )
            for part in VOICE_PARTS:
                enabled = config["engine"][VOICE_KEYS[part]]["enabled"]
                lines.append(f"      voice.{part:<17} {'on' if enabled else 'off'}")
        elif name == "roles.yaml":
            label = (roles.get("roles") or {}).get("administrator", {}).get("label") or "(empty)"
            lines.append(f"      administrator.label     {label}")
        elif name == "soul.yaml":
            lines.append("      the seed soul from templates.py; no answer of its own")
        else:
            for env_name, value in _env_names(contents[".env"]):
                state = "written, never printed" if value else "left for you"
                lines.append(f"      {env_name:<24} ({state})")
    return lines


def _decide(
    base: Path,
    force: bool,
    stdin: TextIO,
    stdout: TextIO,
) -> dict[str, str]:
    """Existing files are `keep` / `change` / `replace`; nothing is overwritten silently."""
    decisions: dict[str, str] = {}
    for name in HOST_FILES:
        if not (base / name).exists():
            decisions[name] = "new"
            continue
        if force:
            decisions[name] = "replace"
            continue
        _say(stdout, "")
        _say(stdout, f"  {name} already exists: keep it, change the values, or replace it?")
        _say(stdout, "    1) keep   2) change   3) replace")
        while True:
            answer = _ask(stdin, stdout, "  choice", "1")
            picked = {
                "1": "keep",
                "keep": "keep",
                "2": "change",
                "change": "change",
                "3": "replace",
                "replace": "replace",
            }.get(answer)
            if picked:
                decisions[name] = picked
                break
            _say(stdout, "  enter 1, 2, or 3")
    return decisions


def _choose_review(stdin: TextIO, stdout: TextIO) -> str:
    _say(stdout, "")
    _say(stdout, "  apply, change a step, or cancel?")
    _say(stdout, "    1) apply   2) change   3) cancel")
    while True:
        answer = _ask(stdin, stdout, "  choice", "1")
        if answer in ("1", "apply", "a"):
            return "apply"
        if answer in ("2", "change", "c"):
            return "change"
        if answer in ("3", "cancel", "x", "q"):
            return "cancel"
        _say(stdout, "  enter 1, 2, or 3")


def _choose_step(stdin: TextIO, stdout: TextIO) -> int:
    _say(stdout, "  which step?")
    for index, name in enumerate(STEPS, start=1):
        _say(stdout, f"    {index}) {name}")
    while True:
        answer = _ask(stdin, stdout, "  step", "1")
        if answer.isdigit() and 1 <= int(answer) <= len(STEPS):
            return int(answer) - 1
        _say(stdout, f"  enter 1-{len(STEPS)}")


def _interactive(
    folder: str | Path,
    *,
    stdin: TextIO,
    stdout: TextIO,
    color: bool,
    answers: Answers,
    locked: set[int],
    force: bool,
) -> tuple[list[Path], list[Path]]:
    _say_lines(stdout, banner(color))
    base = Path(folder).expanduser().resolve()
    step = 0
    decisions: dict[str, str] | None = None
    while True:
        while step < len(ASKERS):
            if step not in locked:
                _say(stdout, "")
                _say_lines(stdout, _rail(step, color))
                ASKERS[step](answers, stdin, stdout)
            step += 1
        contents = build(
            provider=answers.provider,
            model=answers.model,
            database_url=answers.database_url,
            label=answers.label,
            voice=answers.voice_off,
            language=answers.language,
            locale=answers.locale,
            timezone=answers.timezone,
        )
        if decisions is None:
            decisions = _decide(base, force, stdin, stdout)
        _say_lines(stdout, review_lines(base, contents, decisions))
        action = _choose_review(stdin, stdout)
        if action == "apply":
            break
        if action == "cancel":
            _say(stdout, "cancelled; nothing was written")
            raise InitCancelled("cancelled by the operator; nothing was written")
        step = _choose_step(stdin, stdout)
        locked.discard(step)
    return write_files(base, contents, decisions)


def wizard(
    folder: str | Path,
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    color: bool = False,
    provider: str | None = None,
    model: str | None = None,
    database_url: str | None = None,
    label: str | None = None,
    voice_layer: str | None = None,
    language: str | None = None,
    locale: str | None = None,
    timezone: str | None = None,
    force: bool = False,
) -> tuple[list[Path], list[Path]]:
    """The interactive setup (ADR-0030 §4.2): five steps, a review, then the files.

    A flag pre-answers its step; a step the operator changes is asked again before the next
    review. `Ctrl-C` cancels the run and leaves nothing behind.
    """
    stream_in = stdin or sys.stdin
    stream_out = stdout or sys.stdout
    answers = Answers(
        provider=provider or "",
        model=model or "",
        database_url=database_url or "",
        label=label or "",
        language=language or "",
        locale=locale or "",
        timezone=timezone or "",
    )
    locked: set[int] = set()
    if provider is not None:
        locked.add(0)
    if model is not None:
        locked.add(1)
    if database_url is not None:
        locked.add(2)
    if label is not None:
        locked.add(3)
    if voice_layer is not None:
        answers.voice_off = voice_off(voice_layer)
        locked.add(4)
    try:
        written, skipped = _interactive(
            folder,
            stdin=stream_in,
            stdout=stream_out,
            color=color,
            answers=answers,
            locked=locked,
            force=force,
        )
    except KeyboardInterrupt:
        _say(stream_out, "")
        _say(stream_out, "cancelled; nothing was written")
        raise InitCancelled("cancelled by the operator; nothing was written") from None
    _say(stream_out, "")
    _say(stream_out, "Receipt")
    _say(
        stream_out,
        report(base=folder, written=written, skipped=skipped, provider=answers.provider,
               model=answers.model),
    )
    return written, skipped


__all__ = [
    "HOST_FILES",
    "PRIVATE_FILES",
    "STEPS",
    "Answers",
    "InitCancelled",
    "banner",
    "build",
    "init",
    "mode_of",
    "model_ids",
    "report",
    "review_lines",
    "validate_database_url",
    "validate_model",
    "validate_provider",
    "voice_off",
    "wizard",
    "write_files",
]
