"""Terminal primitives for `eidesoul init`: curses on a TTY, numbered lines elsewhere.

The renderer owns presentation only. `admin/init.py` owns the answers, validation, and file
writes; this module never touches the host (ADR-0039 §4.2).
"""

import contextlib
import os
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TextIO


class Cancelled(Exception):
    """The operator pressed Escape, q, or Ctrl-C in a menu."""


class Back(Exception):
    """The operator pressed Left: the wizard replays the previous step."""


@dataclass(frozen=True)
class Choice:
    label: str
    detail: str = ""
    value: str = ""


def interactive(stdin: TextIO, stdout: TextIO, *, plain: bool = False) -> bool:
    """True only for a real POSIX terminal; tests, pipes, and `--no-color` keep lines."""
    if plain or os.name == "nt":
        return False
    if os.environ.get("TERM", "").strip().lower() in ("", "dumb"):
        return False
    return bool(_is_tty(stdin) and _is_tty(stdout))


def _is_tty(stream: TextIO) -> bool:
    try:
        return bool(stream.isatty())
    except (AttributeError, OSError):
        return False


def choose(
    *,
    stdin: TextIO,
    stdout: TextIO,
    title: str,
    choices: Sequence[Choice],
    default: int = 0,
    header: Sequence[str] = (),
    searchable: bool = True,
    allow_back: bool = True,
    color: bool = False,
    plain: bool = False,
) -> int:
    """Pick one choice; raises `Cancelled` or `Back` so the caller can leave cleanly."""
    if not choices:
        raise ValueError("a menu needs at least one choice")
    default = max(0, min(default, len(choices) - 1))
    if not interactive(stdin, stdout, plain=plain):
        return _line_choose(stdin, stdout, title, choices, default)
    return _curses_choose(
        title=title,
        choices=choices,
        default=default,
        header=header,
        searchable=searchable,
        allow_back=allow_back,
        color=color,
    )


def _line_choose(
    stdin: TextIO,
    stdout: TextIO,
    title: str,
    choices: Sequence[Choice],
    default: int,
) -> int:
    stdout.write(f"  {title}\n")
    stdout.flush()
    for index, choice in enumerate(choices, start=1):
        marker = "*" if index - 1 == default else " "
        detail = f"  {choice.detail}" if choice.detail else ""
        stdout.write(f"   {marker} {index}) {choice.label}{detail}\n")
    while True:
        stdout.write(f"  choice [{default + 1}]: ")
        stdout.flush()
        line = stdin.readline()
        if line == "":
            raise Cancelled("input closed before the menu answered")
        answer = line.strip()
        if not answer:
            return default
        if answer in ("q", "quit"):
            raise Cancelled("cancelled by the operator")
        if answer.isdigit() and 1 <= int(answer) <= len(choices):
            return int(answer) - 1
        lowered = answer.casefold()
        for index, choice in enumerate(choices):
            if lowered in (choice.value.casefold(), choice.label.casefold()):
                return index
        stdout.write(f"  choose 1-{len(choices)} or a name\n")
        stdout.flush()


def _curses_choose(  # noqa: C901, PLR0915 — the key loop is the menu state machine
    *,
    title: str,
    choices: Sequence[Choice],
    default: int,
    header: Sequence[str],
    searchable: bool,
    allow_back: bool,
    color: bool,
) -> int:
    import curses

    def run(stdscr) -> int:  # noqa: C901, PLR0912, PLR0915
        if color and curses.has_colors():
            curses.start_color()
            with contextlib.suppress(curses.error):
                curses.use_default_colors()
            curses.init_pair(1, curses.COLOR_CYAN, -1)
            curses.init_pair(2, curses.COLOR_YELLOW, -1)
        with contextlib.suppress(curses.error):
            curses.curs_set(0)
        stdscr.keypad(True)
        cursor = default
        query = ""
        searching = False
        filtered = list(range(len(choices)))
        while True:
            filtered = _filtered(choices, query)
            if not filtered:
                filtered = list(range(len(choices)))
            if cursor not in filtered:
                cursor = filtered[0]
            _draw(
                stdscr,
                title=title,
                choices=choices,
                header=header,
                filtered=filtered,
                cursor=cursor,
                query=query,
                searching=searching,
                searchable=searchable,
                allow_back=allow_back,
                color=color,
            )
            key = stdscr.getch()
            if key in (curses.KEY_UP, ord("k")):
                cursor = filtered[(filtered.index(cursor) - 1) % len(filtered)]
                continue
            if key in (curses.KEY_DOWN, ord("j")):
                cursor = filtered[(filtered.index(cursor) + 1) % len(filtered)]
                continue
            if key in (curses.KEY_ENTER, 10, 13):
                return cursor
            if key == curses.KEY_LEFT:
                if allow_back:
                    raise Back()
                raise Cancelled("cancelled at the first step")
            if key == 27:
                if searching:
                    searching = False
                    query = ""
                    continue
                if allow_back:
                    raise Back()
                raise Cancelled("cancelled at the first step")
            if searching:
                if key in (curses.KEY_BACKSPACE, 127, 8):
                    query = query[:-1]
                elif key == 21:
                    query = ""
                elif 32 <= key < 127:
                    query += chr(key)
                continue
            if searchable and key == ord("/"):
                searching = True
                query = ""
            elif key in (ord("q"), 3):
                raise Cancelled("cancelled by the operator")

    return curses.wrapper(run)


def _filtered(choices: Sequence[Choice], query: str) -> list[int]:
    needle = query.strip().casefold()
    if not needle:
        return list(range(len(choices)))
    return [
        index
        for index, choice in enumerate(choices)
        if needle in f"{choice.label} {choice.detail} {choice.value}".casefold()
    ]


def _draw(
    stdscr,
    *,
    title: str,
    choices: Sequence[Choice],
    header: Sequence[str],
    filtered: Sequence[int],
    cursor: int,
    query: str,
    searching: bool,
    searchable: bool,
    allow_back: bool,
    color: bool,
) -> None:
    import curses

    stdscr.erase()
    height, width = stdscr.getmaxyx()
    row = 0

    def put(text: str, *, attr: int = 0, y: int | None = None) -> None:
        line = y if y is not None else row
        if 0 <= line < height:
            with contextlib.suppress(curses.error):
                stdscr.addnstr(line, 0, text, max(0, width - 1), attr)

    for line in header:
        put(line)
        row += 1
    if row and row < height:
        row += 1
    put(f"  {title}", attr=curses.A_BOLD)
    row += 1
    if searching:
        put(f"  search: {query}  (enter to keep, esc to clear)", attr=curses.A_DIM)
        row += 1
    position = filtered.index(cursor) if cursor in filtered else 0
    visible = max(1, height - row - 3)
    start = max(0, position - visible + 1)
    for index in filtered[start : start + visible]:
        selected = index == cursor
        marker = "◆" if selected else "○"
        item = choices[index]
        detail = f"   {item.detail}" if item.detail else ""
        attr = 0
        if selected and color and curses.has_colors():
            try:
                attr = curses.color_pair(1) | curses.A_BOLD
            except curses.error:
                attr = curses.A_BOLD
        elif selected:
            attr = curses.A_BOLD
        put(f"  {marker} {item.label}{detail}", attr=attr)
        row += 1
    footer = (
        "  ↑↓ move   enter select   / search   ← back   esc cancel"
        if searchable
        else "  ↑↓ move   enter select   esc cancel"
    )
    if not allow_back:
        footer = footer.replace("← back   ", "")
    put(footer, attr=curses.A_DIM, y=max(0, height - 2))
    stdscr.refresh()


def secret(*, stdin: TextIO, stdout: TextIO, prompt: str, plain: bool = False) -> str:
    """Read a secret without echoing it on a real terminal; fallback never prints the value."""
    if interactive(stdin, stdout, plain=plain) and hasattr(stdin, "fileno"):
        import getpass

        try:
            return getpass.getpass(prompt, stream=stdout)
        except (EOFError, KeyboardInterrupt) as exc:
            raise Cancelled("secret prompt cancelled") from exc
    stdout.write(prompt)
    stdout.flush()
    line = stdin.readline()
    if line == "":
        raise Cancelled("input closed before the secret was entered")
    return line.strip()
