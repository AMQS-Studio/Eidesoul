"""The Eidos mark and the setup lockup; no asset, font, or image dependency.

The terminal gets a block-art mark plus a wordmark. The full mark lives in
`docs/brand/eidesoul-mark.svg`; the ASCII fallback keeps `--no-color`, `TERM=dumb`, and a
non-Unicode terminal readable (ADR-0039 §4.5).
"""

MARK = "◈"
ASCII_MARK = "(*)"
WORDMARK = "EIDESOUL"
TAGLINE = "a soul engine you run on your own machine"


def banner(color: bool = False, *, ascii_only: bool = False) -> list[str]:
    """The setup header: an open-ring mark, wordmark, tagline, then a hairline."""
    if ascii_only:
        art = ["  +-----+", "  | (*) |", "  +-----+"]
    else:
        art = ["   ▄▄▄▄▄", "  █  ◈  █", "  █     ▀", "   ▀▀▀▀"]
    lines = [
        f"{art[0]}   {WORDMARK}",
        f"{art[1]}   {TAGLINE}",
        *art[2:],
        "",
    ]
    if color:
        lines[0] = f"\033[1;38;5;73m{lines[0]}\033[0m"
        lines[1] = f"\033[38;5;244m{lines[1]}\033[0m"
    return lines
