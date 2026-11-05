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
from . import brand, provider_catalog, templates, terminal
from .init import (
    HOST_FILES,
    Answers,
    InitCancelled,
    _env_names,
    banner,
    build,
    mode_of,
    model_ids,
    report,
    validate_credential,
    validate_database_url,
    validate_model,
    voice_selection,
    write_files,
)

STEPS = (
    "location",
    "provider",
    "credential",
    "model",
    "database",
    "identity",
    "web search",
    "approval",
    "voice",
    "capabilities",
)
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
        if row.strip() and len(row) + len(item) > 50:
            lines.append(row.rstrip())
            row = "    "
        row += f"{item}  "
    lines.append(row.rstrip())
    if color:
        lines[0] = f"\033[36m{lines[0]}\033[0m"
    return lines


def _menu_header(step: int, color: bool) -> list[str]:
    # Curses draws text, not ANSI: a colorized header would render as literal `^[[...m`.
    return [*banner(False), *_rail(step, False)]


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
    while True:
        base = Path(current).expanduser()
        detail = [
            f"{name:<12} {'existing' if (base / name).exists() else 'new'}"
            for name in HOST_FILES
        ]
        detail.append(".monoagens/   workspace will be created")
        try:
            answer = terminal.text(
                stdin=stdin,
                stdout=stdout,
                title="where should this soul live?",
                default=current,
                header=_menu_header(STEP_INDEX["location"], color),
                detail=detail,
                allow_back=False,
                color=color,
                plain=not color,
            )
        except terminal.Cancelled:
            raise
        path = Path(answer).expanduser()
        if path.exists() and not path.is_dir():
            _say(stdout, "  that path is a file, not a directory; try again")
            current = answer
            continue
        probe = path
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        if not os.access(probe, os.W_OK):
            _say(stdout, f"  {probe} is not writable; choose another directory")
            current = answer
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
    state.provider = names[picked]


def _ask_credential(state: Answers, stdin: TextIO, stdout: TextIO, color: bool = False) -> None:
    defaults = PROVIDERS[state.provider]
    key_env = defaults.api_key_env
    if not key_env:
        state.api_key = ""
        return
    optional = " (optional for this provider)" if defaults.api_key_optional else ""
    while True:
        key = terminal.text(
            stdin=stdin,
            stdout=stdout,
            title=f"{key_env}{optional}",
            default=state.api_key,
            header=_menu_header(STEP_INDEX["credential"], color),
            detail=["blank leaves it in .env to fill later"],
            secret=True,
            allow_back=True,
            color=color,
            plain=not color,
        )
        error = validate_credential(state.provider, key)
        if error is None:
            state.api_key = key
            return
        _say(stdout, f"  {error}")
        action = terminal.choose(
            stdin=stdin,
            stdout=stdout,
            title="the provider rejected that key; what now?",
            choices=[
                terminal.Choice("Try again", "re-enter the key", "retry"),
                terminal.Choice("Keep it anyway", "write it to .env and continue", "keep"),
                terminal.Choice("Leave it blank", "fill .env later", "blank"),
            ],
            default=0,
            header=_menu_header(STEP_INDEX["credential"], color),
            searchable=False,
            allow_back=True,
            color=color,
            plain=not color,
        )
        if action == 1:
            state.api_key = key
            return
        if action == 2:
            state.api_key = ""
            return


def _ask_model(state: Answers, stdin: TextIO, stdout: TextIO, color: bool = False) -> None:
    try:
        served = model_ids(state.provider, state.api_key)
    except (ConfigError, KeyError, ValueError, OSError):
        served = []
    if served:
        choices = [
            terminal.Choice(model_id, f"served by {state.provider}", model_id)
            for model_id in served
        ]
        choices.append(terminal.Choice("Enter another model id", "manual entry", ""))
        default = served.index(state.model) if state.model in served else 0
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
        if picked < len(served):
            state.model = served[picked]
            return
    while True:
        answer = terminal.text(
            stdin=stdin,
            stdout=stdout,
            title="model id (blank leaves it for later; `monoagens models` lists them)",
            default=state.model,
            header=_menu_header(STEP_INDEX["model"], color),
            detail=(
                ["the provider did not return a model list; enter an id or leave it blank"]
                if state.api_key
                else ["enter an id or leave it blank"]
            ),
            allow_back=True,
            color=color,
            plain=not color,
        )
        error = validate_model(state.provider, answer, state.api_key)
        if error is None:
            state.model = answer
            return
        _say(stdout, f"  {error}; try again or leave it blank")


def _ask_database(state: Answers, stdin: TextIO, stdout: TextIO, color: bool = False) -> None:
    while True:
        answer = terminal.text(
            stdin=stdin,
            stdout=stdout,
            title="Postgres URL for MONOAGENS_DATABASE_URL (blank fills .env later)",
            default=state.database_url,
            header=_menu_header(STEP_INDEX["database"], color),
            allow_back=True,
            color=color,
            plain=not color,
        )
        error = validate_database_url(answer)
        if error is None:
            state.database_url = answer
            return
        _say(stdout, f"  {error}; try again or leave it blank")


def _ask_label(state: Answers, stdin: TextIO, stdout: TextIO, color: bool = False) -> None:
    state.label = terminal.text(
        stdin=stdin,
        stdout=stdout,
        title="label for the first administrator (blank keeps it empty)",
        default=state.label,
        header=_menu_header(STEP_INDEX["identity"], color),
        allow_back=True,
        color=color,
        plain=not color,
    )


def _ask_websearch(state: Answers, stdin: TextIO, stdout: TextIO, color: bool = False) -> None:
    """The search provider, then only the credentials that provider actually reads."""
    from . import catalog

    items = catalog.websearch_choices()
    choices = [terminal.Choice(item.name, item.detail, item.name) for item in items]
    default = next(
        (index for index, item in enumerate(items) if item.name == state.websearch), 0
    )
    picked = terminal.choose(
        stdin=stdin,
        stdout=stdout,
        title="which web search provider?",
        choices=choices,
        default=default,
        header=_menu_header(STEP_INDEX["web search"], color),
        searchable=True,
        allow_back=True,
        color=color,
        plain=not color,
    )
    state.websearch = items[picked].name
    if state.websearch == "searxng":
        while not state.websearch_url.strip():
            state.websearch_url = terminal.text(
                stdin=stdin,
                stdout=stdout,
                title="SearXNG base URL",
                default=state.websearch_url,
                header=_menu_header(STEP_INDEX["web search"], color),
                detail=["JSON endpoint, for example http://127.0.0.1:8888"],
                allow_back=True,
                color=color,
                plain=not color,
            )
    else:
        state.websearch_url = ""
    key_env = templates.WEB_SEARCH_API_KEYS.get(state.websearch, "")
    engine_env = templates.WEB_SEARCH_ENGINE_KEYS.get(state.websearch, "")
    if not key_env:
        state.websearch_key = ""
    if not engine_env:
        state.websearch_engine_id = ""
    if key_env:
        state.websearch_key = terminal.text(
            stdin=stdin,
            stdout=stdout,
            title=f"{key_env} (optional)",
            default=state.websearch_key,
            header=_menu_header(STEP_INDEX["web search"], color),
            detail=["stored in .env at 0600; never written to config.yaml"],
            secret=True,
            allow_back=True,
            color=color,
            plain=not color,
        )
    if engine_env:
        state.websearch_engine_id = terminal.text(
            stdin=stdin,
            stdout=stdout,
            title=f"{engine_env} (optional)",
            default=state.websearch_engine_id,
            header=_menu_header(STEP_INDEX["web search"], color),
            detail=["the Programmable Search engine id; stored in .env"],
            allow_back=True,
            color=color,
            plain=not color,
        )


def _ask_approval(state: Answers, stdin: TextIO, stdout: TextIO, color: bool = False) -> None:
    modes = [
        terminal.Choice("default", "pause state changes; ordinary tools run", "default"),
        terminal.Choice("yolo", "trust ordinary tools; sensitive commands still pause", "yolo"),
        terminal.Choice("strict", "pause every state-changing tool", "strict"),
    ]
    current = state.approval_mode or "default"
    picked = terminal.choose(
        stdin=stdin,
        stdout=stdout,
        title="which approval policy?",
        choices=modes,
        default=next(
            (index for index, item in enumerate(modes) if item.value == current), 0
        ),
        header=_menu_header(STEP_INDEX["approval"], color),
        searchable=False,
        allow_back=True,
        color=color,
        plain=not color,
    )
    state.approval_mode = modes[picked].value
    natural = terminal.choose(
        stdin=stdin,
        stdout=stdout,
        title="accept a plain chat reply as the confirmation?",
        choices=[
            terminal.Choice("yes", "a natural yes/no stands in for /approve", "yes"),
            terminal.Choice("no", "only /approve and /deny decide", "no"),
        ],
        default=0 if state.natural_confirm else 1,
        header=_menu_header(STEP_INDEX["approval"], color),
        searchable=False,
        allow_back=True,
        color=color,
        plain=not color,
    )
    state.natural_confirm = natural == 0


def _ask_voice(state: Answers, stdin: TextIO, stdout: TextIO, color: bool = False) -> None:
    """Every part governs itself; the retired master switch never appears here."""
    from . import catalog

    items = catalog.voice_parts()
    if not items:
        state.voice_parts = ()
        return
    selected = (
        state.voice_parts
        if state.voice_parts is not None
        else catalog.default_voice_parts()
    )
    picked = terminal.multi_choose(
        stdin=stdin,
        stdout=stdout,
        title="which voice parts should be active?",
        choices=[
            terminal.Choice(item.name, item.detail, item.name) for item in items
        ],
        selected=selected,
        header=_menu_header(STEP_INDEX["voice"], color),
        color=color,
        plain=not color,
    )
    state.voice_parts = tuple(picked)


def _ask_capabilities(state: Answers, stdin: TextIO, stdout: TextIO, color: bool = False) -> None:
    from . import catalog

    header = [
        *banner(False),
        "  capabilities",
        "  gateways  ·  tools",
    ]
    if state.gateways is None:
        choices = [
            terminal.Choice(item.name, item.detail, item.name) for item in catalog.gateways()
        ]
        if choices:
            picked = terminal.multi_choose(
                stdin=stdin,
                stdout=stdout,
                title="which gateways should be enabled?",
                choices=choices,
                selected=catalog.default_gateways(),
                header=header,
                color=color,
                plain=not color,
            )
            state.gateways = tuple(picked)
        else:
            state.gateways = ()
    if state.tools is None:
        choices = [
            terminal.Choice(item.name, item.detail, item.name) for item in catalog.tools()
        ]
        if choices:
            picked = terminal.multi_choose(
                stdin=stdin,
                stdout=stdout,
                title="which tools should the member role receive?",
                choices=choices,
                selected=catalog.default_tools(),
                header=header,
                color=color,
                plain=not color,
            )
            state.tools = tuple(picked)


ASKERS = (
    _ask_location,
    _ask_provider,
    _ask_credential,
    _ask_model,
    _ask_database,
    _ask_label,
    _ask_websearch,
    _ask_approval,
    _ask_voice,
    _ask_capabilities,
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
                    f"      web_search.provider     {config['web_search']['provider']}",
                    f"      web_search.api_key_env  "
                    f"{config['web_search']['api_key_env'] or '(no key)'}",
                    f"      engine.approval.mode    {config['engine']['approval']['mode']}",
                    "      engine.approval.natural_confirm  "
                    f"{config['engine']['approval']['natural_confirm']}",
                    f"      engine.research.mode    {config['engine']['research']['mode']}",
                    f"      gateways                {', '.join(config['gateways']) or '(none)'}",
                    f"      engine.default_role     {config['engine']['default_role']}",
                    "      defaults                guard on, research auto, memory privacy on",
                ]
            )
        elif name == "roles.yaml":
            role_map = roles.get("roles") or {}
            label = (role_map.get("administrator") or {}).get("label") or "(empty)"
            lines.append(f"      roles                   {', '.join(sorted(role_map))}")
            lines.append(f"      administrator.label     {label}")
            lines.append("      administrator.tools     *")
        elif name == "soul.yaml":
            soul = yaml.safe_load(contents["soul.yaml"]) or {}
            name = soul.get("name")
            language = soul.get("language")
            lines.append(f"      name / language         {name} / {language}")
            parts = [
                name
                for name, value in (soul.get("voice") or {}).items()
                if isinstance(value, dict) and value.get("enabled") is True
            ]
            lines.append(f"      soul.voice.parts        {', '.join(parts) or '(none)'}")
            persona = "set" if soul.get("persona") else "empty"
            lines.append(f"      persona                 {persona}")
        else:
            existing_env = base / ".env"
            existing_values = (
                dict(_env_names(existing_env.read_text(encoding="utf-8")))
                if existing_env.exists()
                else {}
            )
            shown: set[str] = set()
            for env_name, value in _env_names(contents[".env"]):
                shown.add(env_name)
                if value:
                    state = "written, never printed"
                elif existing_values.get(env_name):
                    state = "kept, never printed"
                else:
                    state = "left for you"
                lines.append(f"      {env_name:<24} ({state})")
            for env_name in existing_values:
                if env_name not in shown:
                    lines.append(f"      {env_name:<24} (kept, never printed)")
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


def _interactive(  # noqa: C901
    folder: str | Path,
    *,
    stdin: TextIO,
    stdout: TextIO,
    color: bool,
    answers: Answers,
    locked: set[int],
    force: bool,
) -> tuple[list[Path], list[Path]]:
    # A line fallback must never print ANSI: if curses cannot run, drop colour too.
    use_curses = terminal.interactive(stdin, stdout, plain=not color)
    color = color and use_curses
    if not use_curses:
        _say_lines(stdout, brand.lockup(color))
    if not answers.location:
        answers.location = str(Path(folder).expanduser().resolve())
    step = 0
    decisions: dict[str, str] | None = None
    try:
        while True:
            while step < len(ASKERS):
                if step not in locked:
                    if not use_curses:
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
                voice_parts=answers.voice_parts,
                language=answers.language,
                locale=answers.locale,
                timezone=answers.timezone,
                websearch=answers.websearch,
                websearch_url=answers.websearch_url,
                websearch_key=answers.websearch_key,
                websearch_engine_id=answers.websearch_engine_id,
                approval_mode=answers.approval_mode,
                natural_confirm=answers.natural_confirm,
                tools=answers.tools,
                gateways=answers.gateways,
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
    websearch: str | None = None,
    websearch_url: str | None = None,
    websearch_key: str | None = None,
    websearch_engine_id: str | None = None,
    approval_mode: str | None = None,
    natural_confirm: bool | None = None,
    tools: tuple[str, ...] | None = None,
    gateways: tuple[str, ...] | None = None,
    voice_parts: tuple[str, ...] | None = None,
    ask_capabilities: bool = False,
    force: bool = False,
    ask_location: bool = True,
) -> tuple[list[Path], list[Path]]:
    """The interactive setup (ADR-0039 §4.2): location through review.

    A flag pre-answers its step; a step the operator changes is asked again before the next
    review. `Ctrl-C` or Escape cancels the run and leaves nothing behind. `ask_capabilities`
    is accepted for the installer's old call site; gateways and tools are always asked.
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
        websearch=websearch or "",
        websearch_url=websearch_url or "",
        websearch_key=websearch_key or "",
        websearch_engine_id=websearch_engine_id or "",
        approval_mode=approval_mode or "",
        natural_confirm=True if natural_confirm is None else natural_confirm,
        tools=tools,
        gateways=gateways,
        voice_parts=voice_parts,
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
    if websearch is not None:
        locked.add(STEP_INDEX["web search"])
    if approval_mode is not None or natural_confirm is not None:
        locked.add(STEP_INDEX["approval"])
    if voice_layer is not None or voice_parts is not None:
        answers.voice_parts = voice_selection(voice_layer, voice_parts)
        locked.add(STEP_INDEX["voice"])
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
