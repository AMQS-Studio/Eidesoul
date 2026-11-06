"""Unicode word splitting shared by salience keywords and confirmation cues.

An ASCII class silently drops every non-Latin script, which made keyword salience and short
confirmations behave differently per language. `re`'s Unicode word class keeps them.
"""


import re

_WORD = re.compile(r"[^\W_]+")


def words(text: object, *, minimum: int = 1) -> list[str]:
    """Case-folded words; `minimum` filters out tokens shorter than that many characters."""
    return [
        match.group(0).casefold()
        for match in _WORD.finditer(str(text or ""))
        if len(match.group(0)) >= minimum
    ]
