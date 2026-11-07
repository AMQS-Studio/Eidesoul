"""Host-local configuration for the WhatsApp gateway."""


from dataclasses import dataclass
from pathlib import Path

POLICY_KEYS = frozenset({"dm", "groups", "reply_to_bot"})


@dataclass(frozen=True)
class WhatsAppSettings:
    enabled: bool = True
    session_db: Path = Path(".monoagens/whatsapp.db")
    backlog_seconds: float = 600.0
    show_thinking: bool = False
    show_steps: bool = False
    interim_updates: bool = False

    @classmethod
    def from_mapping(cls, enabled: bool, options: dict, base: Path) -> WhatsAppSettings:
        mislaid = sorted(POLICY_KEYS.intersection(options))
        if mislaid:
            raise ValueError(
                f"gateway 'whatsapp': {mislaid[0]!r} is role authorization; "
                "move it to roles.yaml under roles.<role>.gateways.whatsapp (ADR-0046 §5.1)"
            )
        raw = Path(str(options.get("session_db") or ".monoagens/whatsapp.db")).expanduser()
        session = raw if raw.is_absolute() else Path(base) / raw
        try:
            backlog = float(options.get("backlog_seconds") or 600)
        except (TypeError, ValueError):
            backlog = 600.0
        return cls(
            enabled=bool(enabled),
            session_db=session,
            backlog_seconds=max(0.0, backlog),
            show_thinking=bool(options.get("show_thinking", False)),
            show_steps=bool(options.get("show_steps", False)),
            interim_updates=bool(options.get("interim_updates", False)),
        )
