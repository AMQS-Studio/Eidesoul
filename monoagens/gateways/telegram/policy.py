"""Telegram role policy: the `gateways.telegram` block of one role (ADR-0046 §4.2).

The vocabulary is Telegram's own: a DM, a group, a forum topic, and whether the bot has to be
addressed. A malformed or missing block denies, exactly like every other body.
"""


from collections.abc import Mapping
from dataclasses import dataclass

MODES = frozenset({"none", "mention", "reply", "both", "all"})


@dataclass(frozen=True, slots=True)
class Access:
    dm: bool = False
    groups: str = "none"
    topics: str = "none"
    reply_to_bot: bool = False

    @classmethod
    def parse(cls, block: object) -> Access | None:
        """One valid policy block, or None when the block is absent or malformed."""
        if not isinstance(block, Mapping):
            return None
        dm = block.get("dm")
        groups = block.get("groups")
        topics = block.get("topics", groups)
        reply = block.get("reply_to_bot")
        if not isinstance(dm, bool) or not isinstance(reply, bool):
            return None
        if not isinstance(groups, str) or groups not in MODES:
            return None
        if not isinstance(topics, str) or topics not in MODES:
            return None
        return cls(dm=dm, groups=groups, topics=topics, reply_to_bot=reply)

    def _mode(self, hints: Mapping[str, object]) -> str:
        return self.topics if hints.get("topic") else self.groups

    def refusal(self, hints: Mapping[str, object]) -> str:
        """Empty when the message may enter a turn, otherwise a diagnostic reason."""
        if not bool(hints.get("group")):
            return "" if self.dm else "dm_denied"
        mode = self._mode(hints)
        if mode == "all":
            return ""
        mentioned = bool(hints.get("mention"))
        replied = self.reply_to_bot and bool(hints.get("reply"))
        if mode in ("mention", "both") and (mentioned or replied):
            return ""
        if mode == "reply" and replied:
            return ""
        scope = "topic" if hints.get("topic") else "group"
        return f"{scope}_{mode}"
