"""The existing Eidesoul identity for the setup surface.

The product logo is `assets/logo.png`; `logo_art.py` is a generated half-block rendering of that
same asset, not a second mark. Line mode prints its truecolor ANSI rows; curses parses the same
cells into color pairs because `curses.addstr` does not interpret escape sequences (ADR-0039 §4.5).
"""

import re
from dataclasses import dataclass
from functools import cache

from . import logo_art

WORDMARK = "EIDESOUL"
TAGLINE = "a soul engine you run on your own machine"
LOGO_WIDTH = logo_art.LOGO_WIDTH
LOGO_HEIGHT = logo_art.LOGO_HEIGHT

_SGR = re.compile(r"(\x1b\[[0-9;]*m)")


@dataclass(frozen=True)
class LogoCell:
    """One terminal cell: the glyph plus the colors the generated ANSI line carries."""

    char: str
    fg: tuple[int, int, int] | None = None
    bg: tuple[int, int, int] | None = None


def banner(color: bool = False) -> list[str]:
    """The wordmark and tagline; no replacement mark and no image dependency."""
    lines = [f"  {WORDMARK}", f"  {TAGLINE}", ""]
    if color:
        lines[0] = f"\033[1;38;5;73m{lines[0]}\033[0m"
        lines[1] = f"\033[38;5;244m{lines[1]}\033[0m"
    return lines


def logo_lines(color: bool = False) -> list[str]:
    """The generated logo rows: truecolor ANSI when asked, plain shade blocks otherwise."""
    return list(logo_art.ANSI_LOGO if color else logo_art.PLAIN_LOGO)


def lockup(color: bool = False) -> list[str]:
    """Logo on the left, wordmark/tagline on the right; used by numbered line mode."""
    logo = logo_lines(color)
    head = banner(color)
    rows: list[str] = []
    for index in range(max(len(logo), len(head))):
        left = logo[index] if index < len(logo) else " " * LOGO_WIDTH
        right = head[index] if index < len(head) else ""
        rows.append(f"{left}   {right}".rstrip())
    return rows


@cache
def logo_cells() -> tuple[tuple[LogoCell, ...], ...]:
    """Parse the generated ANSI rows once; curses draws the cells with color pairs."""
    rows: list[tuple[LogoCell, ...]] = []
    for line in logo_art.ANSI_LOGO:
        cells: list[LogoCell] = []
        fg: tuple[int, int, int] | None = None
        bg: tuple[int, int, int] | None = None
        for token in _SGR.split(line):
            if not token:
                continue
            if token.startswith("\x1b["):
                codes = [int(code) for code in token[2:-1].split(";") if code]
                if not codes or codes[0] == 0:
                    fg, bg = None, None
                elif codes[0] == 38 and codes[1:2] == [2]:
                    fg = tuple(codes[2:5])  # type: ignore[assignment]
                elif codes[0] == 48 and codes[1:2] == [2]:
                    bg = tuple(codes[2:5])  # type: ignore[assignment]
                elif codes[0] == 39:
                    fg = None
                elif codes[0] == 49:
                    bg = None
                continue
            for char in token:
                cells.append(LogoCell(char, fg, bg))
        rows.append(tuple(cells))
    return tuple(rows)
