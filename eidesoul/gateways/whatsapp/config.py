"""Host-local configuration for the WhatsApp gateway."""


from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WhatsAppSettings:
    enabled: bool = True
    session_db: Path = Path(".eidesoul/whatsapp.db")
    backlog_seconds: float = 600.0

    @classmethod
    def from_mapping(cls, enabled: bool, options: dict, base: Path) -> "WhatsAppSettings":
        raw = Path(str(options.get("session_db") or ".eidesoul/whatsapp.db")).expanduser()
        session = raw if raw.is_absolute() else Path(base) / raw
        try:
            backlog = float(options.get("backlog_seconds") or 600)
        except (TypeError, ValueError):
            backlog = 600.0
        return cls(enabled=bool(enabled), session_db=session, backlog_seconds=max(0.0, backlog))
