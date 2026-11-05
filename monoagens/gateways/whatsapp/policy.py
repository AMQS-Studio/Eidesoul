"""WhatsApp gateway policy: validate one role's `gateways.whatsapp` block (ADR-0046 §4.2).

The block is role data, opaque to the core. This module owns the vocabulary and fails closed:
a missing or malformed block denies the message before history, memory, and the model.
"""


from collections.abc import Mapping
from dataclasses import dataclass

GROUPS = frozenset({"none", "mention", "reply", "both", "all"})


@dataclass(frozen=True, slots=True)
class Access:
    dm: bool = False
    groups: str = "none"
    reply_to_bot: bool = False

    @classmethod
    def parse(cls, block: object) -> Access | None:
        """One valid policy block, or None when the block is absent or malformed."""
        if not isinstance(block, Mapping):
            return None
        dm = block.get("dm")
        groups = block.get("groups")
        reply = block.get("reply_to_bot")
        if not isinstance(dm, bool) or not isinstance(reply, bool):
            return None
        if not isinstance(groups, str) or groups not in GROUPS:
            return None
        return cls(dm=dm, groups=groups, reply_to_bot=reply)

    def refusal(self, hints: Mapping[str, object]) -> str:
        """Empty when the message may enter a turn, otherwise a diagnostic reason."""
        if not bool(hints.get("group")):
            return "" if self.dm else "dm_denied"
        if self.groups == "all":
            return ""
        if self.groups in {"mention", "both"} and bool(hints.get("mention")):
            return ""
        if (
            self.groups in {"reply", "both"}
            and self.reply_to_bot
            and bool(hints.get("reply"))
        ):
            return ""
        return f"group_{self.groups}"
