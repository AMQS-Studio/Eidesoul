"""Installer menus: the same terminal layer as `monoagens init`, without importing the engine.

The shell owns detection, sudo, and every filesystem stage. This module only asks one question
and writes the answer to `--out`, so stdout can stay a terminal (curses needs that) while the
shell reads the result from a file.
"""

import argparse
import sys
from pathlib import Path

from . import brand, terminal

TOTAL = 4


def _header(step: int) -> list[str]:
    return [
        *brand.banner(False),
        f"  [{step}/{TOTAL}] installer",
        "  runtime  ·  location  ·  database  ·  container engine",
    ]


def _write(path: str, value: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(value + "\n", encoding="utf-8")


def _ask(
    *,
    title: str,
    choices: list[terminal.Choice],
    default: int,
    step: int,
    searchable: bool = False,
) -> str:
    picked = terminal.choose(
        stdin=sys.stdin,
        stdout=sys.stdout,
        title=title,
        choices=choices,
        default=default,
        header=_header(step),
        searchable=searchable,
        allow_back=False,
        color=True,
    )
    return choices[picked].value


def _text(*, title: str, default: str, step: int, detail: list[str] | None = None) -> str:
    return terminal.text(
        stdin=sys.stdin,
        stdout=sys.stdout,
        title=title,
        default=default,
        header=_header(step),
        detail=detail or [],
        allow_back=False,
        color=True,
    )


def stack(out: str, default: str) -> None:
    choices = [
        terminal.Choice("native", "engine on this host; Postgres in a container", "native"),
        terminal.Choice("compose", "engine and Postgres in containers", "compose"),
    ]
    _write(
        out,
        _ask(
            title="where should the engine run?",
            choices=choices,
            default=1 if default == "compose" else 0,
            step=1,
        ),
    )


def location(out: str, default: str, here: str) -> None:
    choices = [terminal.Choice("default", default, default)]
    if here:
        choices.append(terminal.Choice("here", here, here))
    choices.append(terminal.Choice("custom", "enter a path", "custom"))
    picked = _ask(
        title="where should MonoAgens live?",
        choices=choices,
        default=0,
        step=2,
    )
    if picked == "custom":
        picked = _text(
            title="install directory",
            default=default,
            step=2,
            detail=["config.yaml, roles.yaml, soul.yaml, .env, and runtime state live here"],
        )
    _write(out, picked)


def database(out: str, default: str, external_allowed: bool) -> None:
    choices = [
        terminal.Choice("container", "Postgres in Podman or Docker", "container"),
    ]
    if external_allowed:
        choices.append(
            terminal.Choice("external", "use an existing Postgres server", "external")
        )
    _write(
        out,
        _ask(
            title="where should Postgres run?",
            choices=choices,
            default=1 if default == "external" and external_allowed else 0,
            step=3,
        ),
    )


def engine(out: str, default: str, podman: str, docker: str) -> None:
    choices = [
        terminal.Choice("Podman", podman, "podman"),
        terminal.Choice("Docker", docker, "docker"),
    ]
    _write(
        out,
        _ask(
            title="which container engine should run Postgres?",
            choices=choices,
            default=1 if default == "docker" else 0,
            step=4,
        ),
    )


def confirm(out: str, title: str, detail: list[str], default: str) -> None:
    if detail:
        title = f"{title} — {'; '.join(detail)}"
    choices = [
        terminal.Choice("yes", "", "yes"),
        terminal.Choice("no", "", "no"),
    ]
    _write(
        out,
        _ask(
            title=title,
            choices=choices,
            default=0 if default == "yes" else 1,
            step=4,
        ),
    )


def text(out: str, title: str, default: str, detail: list[str], secret: bool) -> None:
    _write(
        out,
        terminal.text(
            stdin=sys.stdin,
            stdout=sys.stdout,
            title=title,
            default=default,
            header=_header(4),
            detail=detail,
            secret=secret,
            allow_back=False,
            color=True,
        ),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="monoagens.admin.install_ui")
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name: str) -> argparse.ArgumentParser:
        child = sub.add_parser(name)
        child.add_argument("--out", required=True)
        return child

    child = add("stack")
    child.add_argument("--default", choices=("native", "compose"), default="native")

    child = add("location")
    child.add_argument("--default", required=True)
    child.add_argument("--here", default="")

    child = add("database")
    child.add_argument("--default", choices=("container", "external"), default="container")
    child.add_argument("--external-allowed", action="store_true")

    child = add("engine")
    child.add_argument("--default", choices=("podman", "docker"), default="podman")
    child.add_argument("--podman", required=True)
    child.add_argument("--docker", required=True)

    child = add("confirm")
    child.add_argument("--title", required=True)
    child.add_argument("--detail", action="append", default=[])
    child.add_argument("--default", choices=("yes", "no"), default="no")

    child = add("text")
    child.add_argument("--title", required=True)
    child.add_argument("--default", default="")
    child.add_argument("--detail", action="append", default=[])
    child.add_argument("--secret", action="store_true")

    args = parser.parse_args(argv)
    try:
        if args.command == "stack":
            stack(args.out, args.default)
        elif args.command == "location":
            location(args.out, args.default, args.here)
        elif args.command == "database":
            database(args.out, args.default, args.external_allowed)
        elif args.command == "engine":
            engine(args.out, args.default, args.podman, args.docker)
        elif args.command == "confirm":
            confirm(args.out, args.title, args.detail, args.default)
        else:
            text(args.out, args.title, args.default, args.detail, args.secret)
    except terminal.Cancelled as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 130
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
