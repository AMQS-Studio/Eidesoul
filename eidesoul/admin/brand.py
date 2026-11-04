"""The setup wordmark; the Eidesoul logo stays the existing `assets/logo.png`.

A raster or SVG logo is not embedded in the terminal: GNOME Terminal has no image protocol,
and inventing a second mark would fork the identity. The terminal therefore prints only the
existing wordmark and tagline (ADR-0039 §4.5).
"""

WORDMARK = "EIDESOUL"
TAGLINE = "a soul engine you run on your own machine"


def banner(color: bool = False) -> list[str]:
    """The setup header: wordmark, tagline, then a hairline."""
    lines = [f"  {WORDMARK}", f"  {TAGLINE}", ""]
    if color:
        lines[0] = f"\033[1;38;5;73m{lines[0]}\033[0m"
        lines[1] = f"\033[38;5;244m{lines[1]}\033[0m"
    return lines
