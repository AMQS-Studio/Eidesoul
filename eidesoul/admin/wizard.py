"""The interactive init shell: location, provider, credential, model, database, identity, voice.

The writer, defaults, and validation live in `admin/init.py`; this module owns keys, frames, and
the review only (ADR-0039 §4.2). Tests drive the numbered fallback; a real POSIX TTY gets the
curses menu.
"""

import os
import sys
from pathlib import Path
from typing import TextIO

import yaml

from ..engine.config import ConfigError
from ..providers import PROVIDERS
from . import brand, provider_catalog, terminal
from .init import (
    HOST_FILES,
    VOICE_KEYS,
    VOICE_PARTS,
    Answers,
    InitCancelled,
    _env_names,
    banner,
    build,
    mode_of,
    model_ids,
    report,
    validate_database_url,
    validate_model,
    voice_off,
    write_files,
)

STEPS = ("location", "provider", "credential", "model", "database", "identity", "voice layer")
STEP_INDEX = {name: index for index, name in enumerate(STEPS)}

def _rail(step: int, color: bool = False) -> list[str]:
    """`1/7 location` with finished steps `+`, the current one `*`, the rest `-`."""
    marks = [
        f"{'*' if index == step else ('+' if index < step else '-')} {name}"
        for index, name in enumerate(STEPS)
    ]
    lines = [f"  [{step + 1}/{len(STEPS)}] {STEPS[step]}"]
    row = "    "
    for item in marks:
        if row.strip() and len(row) + len(item) > 64:
            lines.append(row.rstrip())
            row = "    "
        row += f"{item}  "
    lines.append(row.rstrip())
    if color:
        lines[0] = f"\033[36m{lines[0]}\033[0m"
    return lines


def _menu_header(step: int, color: bool) -> list[str]:
    return [*banner(color), *_rail(step, color)]


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


def _ask_location(state: Answers, stdin: TextIO, stdout: TextIO, color: bool = False) -> None:
    """The first decision: where the host files land, shown before anything is written."""
    current = state.location or str(Path.cwd())
    _say(stdout, "  where should this soul live?")
    base = Path(current).expanduser()
    for name in HOST_FILES:
        path = base / name
        status = "existing" if path.exists() else "new"
        _say(stdout, f"    {name:<12} {status}")
    _say(stdout, "    .eidesoul/   workspace will be created")
    while True:
        answer = _ask(stdin, stdout, "  directory", current, show_default=False)
        path = Path(answer).expanduser()
        if path.exists() and not path.is_dir():
            _say(stdout, "  that path is a file, not a directory; try again")
            continue
        probe = path
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        if not os.access(probe, os.W_OK):
            _say(stdout, f"  {probe} is not writable; choose another directory")
            continue
        state.location = str(path.resolve())
        return


def _ask_provider(state: Answers, stdin: TextIO, stdout: TextIO, color: bool = False) -> None:
    names = sorted(PROVIDERS)
    choices = [
        terminal.Choice(
            provider_catalog.label(name),
            provider_catalog.detail(name),
            name,
        )
        for name in names
    ]
    default = names.index("opencode") if "opencode" in names else 0
    try:
        picked = terminal.choose(
            stdin=stdin,
            stdout=stdout,
            title="which provider serves your model?",
            choices=choices,
            default=default,
            header=_menu_header(STEP_INDEX["provider"], color),
            searchable=True,
            allow_back=True,
            color=color,
            plain=not color,
        )
    except terminal.Back:
        raise
    state.provider = names[picked]


def _ask_credential(state: Answers, stdin: TextIO, stdout: TextIO, color: bool = False) -> None:
    defaults = PROVIDERS[state.provider]
    key_env = defaults.api_key_env
    if not key_env:
        state.api_key = ""
        return
    optional = " (optional for this provider)" if defaults.api_key_optional else ""
    _say(stdout, f"  {key_env}{optional} (blank leaves it in .env to fill later)")
    try:
        state.api_key = terminal.secret(
            stdin=stdin,
            stdout=stdout,
            prompt=f"  {key_env}: ",
            plain=not color,
        )
    except terminal.Cancelled:
        raise


def _ask_model(state: Answers, stdin: TextIO, stdout: TextIO, color: bool = False) -> None:
    _say(stdout, "  model id (blank leaves it for later; `eidesoul models` lists them)")
    try:
        served = model_ids(state.provider)
    except (ConfigError, KeyError, ValueError, OSError):
        served = []
    if served:
        choices = [
            terminal.Choice(model_id, f"served by {state.provider}", model_id)
            for model_id in served
        ]
        choices.append(terminal.Choice("Enter another model id", "manual entry", ""))
        default = served.index(state.model) if state.model in served else 0
        try:
            picked = terminal.choose(
                stdin=stdin,
                stdout=stdout,
                title="which model should this soul use?",
                choices=choices,
                default=default,
                header=_menu_header(STEP_INDEX["model"], color),
                searchable=True,
                allow_back=True,
                color=color,
                plain=not color,
            )
        except terminal.Back:
            raise
        if picked < len(served):
            state.model = served[picked]
            return
    while True:
        answer = _ask(stdin, stdout, "  model", state.model)
        error = validate_model(state.provider, answer)
        if error is None:
            state.model = answer
            return
        _say(stdout, f"  {error}; try again or leave it blank")


def _ask_database(state: Answers, stdin: TextIO, stdout: TextIO, color: bool = False) -> None:
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


def _ask_label(state: Answers, stdin: TextIO, stdout: TextIO, color: bool = False) -> None:
    _say(stdout, "  label for the first administrator (roles.yaml; blank keeps it empty)")
    state.label = _ask(stdin, stdout, "  administrator label", state.label)


def _ask_voice(state: Answers, stdin: TextIO, stdout: TextIO, color: bool = False) -> None:
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


ASKERS = (
    _ask_location,
    _ask_provider,
    _ask_credential,
    _ask_model,
    _ask_database,
    _ask_label,
    _ask_voice,
)


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
    _say_lines(stdout, brand.banner(color))
    if not answers.location:
        answers.location = str(Path(folder).expanduser().resolve())
    step = 0
    decisions: dict[str, str] | None = None
    try:
        while True:
            while step < len(ASKERS):
                if step not in locked:
                    _say(stdout, "")
                    _say_lines(stdout, _rail(step, color))
                    try:
                        ASKERS[step](answers, stdin, stdout, color)
                    except terminal.Back:
                        step = max(0, step - 1)
                        while step in locked and step > 0:
                            step -= 1
                        continue
                step += 1
            base = Path(answers.location).expanduser().resolve()
            contents = build(
                provider=answers.provider,
                api_key=answers.api_key,
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
    except terminal.Cancelled as exc:
        raise InitCancelled(str(exc)) from None
    return write_files(base, contents, decisions)


def wizard(
    folder: str | Path,
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    color: bool = False,
    provider: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    database_url: str | None = None,
    label: str | None = None,
    voice_layer: str | None = None,
    language: str | None = None,
    locale: str | None = None,
    timezone: str | None = None,
    force: bool = False,
    ask_location: bool = True,
) -> tuple[list[Path], list[Path]]:
    """The interactive setup (ADR-0039 §4.2): location through review.

    A flag pre-answers its step; a step the operator changes is asked again before the next
    review. `Ctrl-C` or Escape cancels the run and leaves nothing behind.
    """
    stream_in = stdin or sys.stdin
    stream_out = stdout or sys.stdout
    answers = Answers(
        location=str(Path(folder).expanduser().resolve()),
        provider=provider or "",
        api_key=api_key or "",
        model=model or "",
        database_url=database_url or "",
        label=label or "",
        language=language or "",
        locale=locale or "",
        timezone=timezone or "",
    )
    locked: set[int] = set()
    if not ask_location:
        locked.add(STEP_INDEX["location"])
    if provider is not None:
        locked.add(STEP_INDEX["provider"])
    if api_key is not None:
        locked.add(STEP_INDEX["credential"])
    if model is not None:
        locked.add(STEP_INDEX["model"])
    if database_url is not None:
        locked.add(STEP_INDEX["database"])
    if label is not None:
        locked.add(STEP_INDEX["identity"])
    if voice_layer is not None:
        answers.voice_off = voice_off(voice_layer)
        locked.add(STEP_INDEX["voice layer"])
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
    except (KeyboardInterrupt, terminal.Cancelled):
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
