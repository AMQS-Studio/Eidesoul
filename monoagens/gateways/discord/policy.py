"""Discord role policy: the `gateways.discord` block of one role (ADR-0002 §4.2).

Discord separates a guild text channel from a thread; the block names both, plus the DM switch
and whether the bot has to be addressed. A malformed block denies.
"""


from collections.abc import Mapping
from dataclasses import dataclass

MODES = frozenset({"none", "mention", "reply", "both", "all"})


@dataclass(frozen=True, slots=True)
class Access:
    dm: bool = False
    channels: str = "none"
    threads: str = "none"
    reply_to_bot: bool = False

    @classmethod
    def parse(cls, block: object) -> Access | None:
        if not isinstance(block, Mapping):
            return None
        dm = block.get("dm")
        channels = block.get("channels")
        threads = block.get("threads", channels)
        reply = block.get("reply_to_bot")
        if not isinstance(dm, bool) or not isinstance(reply, bool):
            return None
        if not isinstance(channels, str) or channels not in MODES:
            return None
        if not isinstance(threads, str) or threads not in MODES:
            return None
        return cls(dm=dm, channels=channels, threads=threads, reply_to_bot=reply)

    def refusal(self, hints: Mapping[str, object]) -> str:
        if not bool(hints.get("group")):
            return "" if self.dm else "dm_denied"
        mode = self.threads if hints.get("thread") else self.channels
        if mode == "all":
            return ""
        mentioned = bool(hints.get("mention"))
        replied = self.reply_to_bot and bool(hints.get("reply"))
        if mode in ("mention", "both") and (mentioned or replied):
            return ""
        if mode == "reply" and replied:
            return ""
        scope = "thread" if hints.get("thread") else "channel"
        return f"{scope}_{mode}"
