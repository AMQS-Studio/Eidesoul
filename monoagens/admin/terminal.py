"""Terminal primitives for `monoagens init`: curses on a TTY, numbered lines elsewhere.

The renderer owns presentation only. `admin/init.py` owns the answers, validation, and file
writes; this module never touches the host (ADR-0039 §4.2).
"""

import contextlib
import os
import textwrap
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TextIO

from . import brand

LOGO_LEFT = 2
LOGO_TOP = 1
LOGO_GAP = 3
LOGO_MIN_WIDTH = 76
LOGO_MIN_HEIGHT = 20
_LOGO_PAIRS: dict[tuple[int, int], int] = {}
_NEXT_PAIR = 10


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


def _init_colors(color: bool) -> None:
    """Initialize the menu pairs and the logo's grayscale pairs once per curses screen."""
    import curses

    global _NEXT_PAIR
    if not color or not curses.has_colors():
        return
    curses.start_color()
    with contextlib.suppress(curses.error):
        curses.use_default_colors()
    for pair, foreground in ((1, curses.COLOR_CYAN), (2, curses.COLOR_YELLOW)):
        with contextlib.suppress(curses.error):
            curses.init_pair(pair, foreground, -1)
    _LOGO_PAIRS.clear()
    _NEXT_PAIR = 10


def _logo_column(width: int, height: int) -> int:
    """Where the right-hand UI starts, or zero when the logo would crowd a small screen."""
    if width < LOGO_MIN_WIDTH or height < LOGO_MIN_HEIGHT:
        return 0
    return LOGO_LEFT + brand.LOGO_WIDTH + LOGO_GAP


def _color_index(rgb: tuple[int, int, int] | None) -> int:
    import curses

    if rgb is None:
        return -1
    if curses.COLORS < 256:
        return curses.COLOR_WHITE if sum(rgb) / 3 >= 110 else curses.COLOR_BLACK
    if max(rgb) - min(rgb) <= 8:
        return 232 + round(sum(rgb) / 3 / 255 * 23)

    def cube(value: int) -> int:
        if value < 48:
            return 0
        if value < 115:
            return 1
        if value < 155:
            return 2
        if value < 195:
            return 3
        if value < 235:
            return 4
        return 5

    return 16 + 36 * cube(rgb[0]) + 6 * cube(rgb[1]) + cube(rgb[2])


def _draw_logo(stdscr, top: int, left: int, color: bool) -> None:
    """Draw the generated half-block cells with curses pairs; never pass ANSI to curses."""
    import curses

    global _NEXT_PAIR
    for row_index, row in enumerate(brand.logo_cells()):
        for column, cell in enumerate(row):
            if cell.char == " ":
                continue
            attr = 0
            if color and curses.has_colors():
                foreground = _color_index(cell.fg)
                background = _color_index(cell.bg)
                key = (foreground, background)
                pair = _LOGO_PAIRS.get(key)
                if pair is None and _NEXT_PAIR < curses.COLOR_PAIRS:
                    pair = _NEXT_PAIR
                    _NEXT_PAIR += 1
                    with contextlib.suppress(curses.error):
                        curses.init_pair(pair, max(0, foreground), background)
                    _LOGO_PAIRS[key] = pair
                attr = curses.color_pair(pair) if pair else 0
            with contextlib.suppress(curses.error):
                stdscr.addnstr(top + row_index, left + column, cell.char, 1, attr)


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


def _curses_choose(  # noqa: C901 — the key loop is the menu state machine
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

    def run(stdscr) -> int:  # noqa: C901, PLR0912
        _init_colors(color)
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
    x = _logo_column(width, height)
    if x:
        _draw_logo(stdscr, LOGO_TOP, LOGO_LEFT, color)
    row = 0

    def put(text: str, *, attr: int = 0, y: int | None = None, x: int = x) -> None:
        line = y if y is not None else row
        if 0 <= line < height:
            with contextlib.suppress(curses.error):
                stdscr.addnstr(line, x, text, max(0, width - x - 1), attr)

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
    put(footer, attr=curses.A_DIM, y=max(0, height - 2), x=0)
    stdscr.refresh()




def multi_choose(
    *,
    stdin: TextIO,
    stdout: TextIO,
    title: str,
    choices: Sequence[Choice],
    selected: Sequence[str] = (),
    header: Sequence[str] = (),
    allow_back: bool = True,
    color: bool = False,
    plain: bool = False,
) -> list[str]:
    """Toggle several choices; Space toggles, Enter continues, Esc/Left cancels/back."""
    if not choices:
        raise ValueError("a multi-select needs at least one choice")
    default = {item.value for item in choices if item.value in set(selected)}
    if not interactive(stdin, stdout, plain=plain):
        return _line_multi(stdin, stdout, title, choices, default)
    return _curses_multi(
        title=title,
        choices=choices,
        selected=default,
        header=header,
        allow_back=allow_back,
        color=color,
    )


def _line_multi(
    stdin: TextIO,
    stdout: TextIO,
    title: str,
    choices: Sequence[Choice],
    selected: set[str],
) -> list[str]:
    stdout.write(f"  {title}\n")
    for index, choice in enumerate(choices, start=1):
        marker = "x" if choice.value in selected else " "
        detail = f"  {choice.detail}" if choice.detail else ""
        stdout.write(f"   [{marker}] {index}) {choice.label}{detail}\n")
    stdout.write("  numbers separated by spaces (blank keeps defaults): ")
    stdout.flush()
    line = stdin.readline()
    if line == "":
        raise Cancelled("input closed before the multi-select answered")
    answer = line.strip()
    if not answer:
        return [choice.value for choice in choices if choice.value in selected]
    chosen: set[str] = set()
    for token in answer.replace(",", " ").split():
        if not token.isdigit() or not 1 <= int(token) <= len(choices):
            stdout.write(f"  choose 1-{len(choices)}\n")
            stdout.flush()
            return _line_multi(stdin, stdout, title, choices, selected)
        chosen.add(choices[int(token) - 1].value)
    return [choice.value for choice in choices if choice.value in chosen]


def _detail_lines(text: str, width: int) -> list[str]:
    value = " ".join(str(text or "").split())
    return textwrap.wrap(value, width=max(20, width), break_long_words=True) or ["(no details)"]


def _curses_multi(  # noqa: C901, PLR0915
    *,
    title: str,
    choices: Sequence[Choice],
    selected: set[str],
    header: Sequence[str],
    allow_back: bool,
    color: bool,
) -> list[str]:
    import curses

    def run(stdscr) -> list[str]:  # noqa: C901, PLR0912, PLR0915
        _init_colors(color)
        cursor = 0
        helping = False
        help_offset = 0
        while True:
            stdscr.erase()
            height, width = stdscr.getmaxyx()
            x = _logo_column(width, height)
            if x:
                _draw_logo(stdscr, LOGO_TOP, LOGO_LEFT, color)
            row = 0
            for line in header:
                _put_line(stdscr, row, line, width, x=x)
                row += 1
            if row and row < height:
                row += 1
            if helping:
                item = choices[cursor]
                _put_line(stdscr, row, f"  {item.label}", width, curses.A_BOLD, x=x)
                row += 1
                lines = _detail_lines(item.detail, width - x - 4)
                visible = max(1, height - row - 3)
                for line in lines[help_offset : help_offset + visible]:
                    _put_line(stdscr, row, f"  {line}", width, x=x)
                    row += 1
                _put_line(
                    stdscr,
                    max(0, height - 2),
                    "  ↑↓ scroll   ?/enter/esc back",
                    width,
                    curses.A_DIM,
                )
                stdscr.refresh()
                key = stdscr.getch()
                if key in (curses.KEY_UP, ord("k")):
                    help_offset = max(0, help_offset - 1)
                elif key in (curses.KEY_DOWN, ord("j")):
                    help_offset = min(max(0, len(lines) - 1), help_offset + 1)
                elif key in (27, ord("?"), ord("d"), curses.KEY_ENTER, 10, 13):
                    helping = False
                    help_offset = 0
                elif key in (3, ord("q")):
                    raise Cancelled("cancelled by the operator")
                continue
            _put_line(stdscr, row, f"  {title}", width, curses.A_BOLD, x=x)
            row += 1
            visible = max(1, height - row - 3)
            start = max(0, cursor - visible + 1)
            for index in range(start, min(len(choices), start + visible)):
                item = choices[index]
                marker = "x" if item.value in selected else " "
                current = "◆" if index == cursor else "○"
                detail = f"   {item.detail}" if item.detail else ""
                attr = curses.A_BOLD if index == cursor else 0
                _put_line(
                    stdscr,
                    row,
                    f"  {current} [{marker}] {item.label}{detail}",
                    width,
                    attr,
                    x=x,
                )
                row += 1
            footer = "  ↑↓ move   space toggle   ? details   enter continue   esc cancel"
            if allow_back:
                footer = footer.replace("esc cancel", "← back   esc cancel")
            _put_line(stdscr, max(0, height - 2), footer, width, curses.A_DIM)
            stdscr.refresh()
            key = stdscr.getch()
            if key in (curses.KEY_UP, ord("k")):
                cursor = (cursor - 1) % len(choices)
            elif key in (curses.KEY_DOWN, ord("j")):
                cursor = (cursor + 1) % len(choices)
            elif key == ord(" "):
                value = choices[cursor].value
                selected.symmetric_difference_update({value})
            elif key in (ord("?"), ord("d")):
                helping = True
                help_offset = 0
            elif key in (curses.KEY_ENTER, 10, 13):
                return [item.value for item in choices if item.value in selected]
            elif key in (curses.KEY_LEFT, 27):
                if allow_back:
                    raise Back()
                raise Cancelled("cancelled at the first field")
            elif key in (ord("q"), 3):
                raise Cancelled("cancelled by the operator")

    return curses.wrapper(run)


def text(
    *,
    stdin: TextIO,
    stdout: TextIO,
    title: str,
    default: str = "",
    header: Sequence[str] = (),
    detail: Sequence[str] = (),
    secret: bool = False,
    allow_back: bool = True,
    color: bool = False,
    plain: bool = False,
) -> str:
    """One framed text field; Enter accepts, Left goes back, Escape cancels."""
    if not interactive(stdin, stdout, plain=plain):
        for line in detail:
            stdout.write(f"  {line}\n")
        hint = f" [{default}]" if default else ""
        stdout.write(f"  {title}{hint}: ")
        stdout.flush()
        line = stdin.readline()
        if line == "":
            raise Cancelled("input closed before the field was answered")
        value = line.rstrip("\n")
        return value if value else default
    return _curses_text(
        title=title,
        default=default,
        header=header,
        detail=detail,
        secret=secret,
        allow_back=allow_back,
        color=color,
    )




def _put_line(stdscr, row: int, text: str, width: int, attr: int = 0, x: int = 0) -> None:
    import curses

    if row < 0:
        return
    with contextlib.suppress(curses.error):
        stdscr.addnstr(row, x, text, max(0, width - x - 1), attr)


def _curses_text(  # noqa: C901, PLR0915
    *,
    title: str,
    default: str,
    header: Sequence[str],
    detail: Sequence[str],
    secret: bool,
    allow_back: bool,
    color: bool,
) -> str:
    import curses

    def run(stdscr) -> str:  # noqa: C901, PLR0912, PLR0915
        _init_colors(color)
        with contextlib.suppress(curses.error):
            curses.curs_set(1)
        stdscr.keypad(True)
        value = list(default)
        while True:
            stdscr.erase()
            height, width = stdscr.getmaxyx()
            x = _logo_column(width, height)
            if x:
                _draw_logo(stdscr, LOGO_TOP, LOGO_LEFT, color)
            row = 0

            for line in header:
                _put_line(stdscr, row, line, width, x=x)
                row += 1
            if row and row < height:
                row += 1
            _put_line(stdscr, row, f"  {title}", width, curses.A_BOLD, x=x)
            row += 1
            for line in detail:
                _put_line(stdscr, row, f"  {line}", width, curses.A_DIM, x=x)
                row += 1
            row += 1
            shown = "•" * len(value) if secret else "".join(value)
            _put_line(stdscr, row, f"  > {shown}", width, x=x)
            with contextlib.suppress(curses.error):
                stdscr.move(min(row, height - 2), min(width - 2, x + 4 + len(shown)))
            stdscr.refresh()
            key = stdscr.getch()
            if key in (curses.KEY_ENTER, 10, 13):
                return "".join(value)
            if key == curses.KEY_LEFT:
                if allow_back:
                    raise Back()
                raise Cancelled("cancelled at the first field")
            if key == 27:
                if allow_back:
                    raise Back()
                raise Cancelled("cancelled at the first field")
            if key in (3,):
                raise Cancelled("cancelled by the operator")
            if key in (curses.KEY_BACKSPACE, 127, 8):
                if value:
                    value.pop()
                continue
            if key == 21:  # Ctrl-U: clear the field
                value.clear()
                continue
            if key == 23:  # Ctrl-W: delete the trailing word
                while value and value[-1] == " ":
                    value.pop()
                while value and value[-1] != " ":
                    value.pop()
                continue
            if 32 <= key < 127 and len(value) < 4096:
                value.append(chr(key))

    return curses.wrapper(run)
