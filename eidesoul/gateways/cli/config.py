"""Konfigurasi gateway CLI."""


from dataclasses import dataclass


@dataclass(frozen=True)
class CliSettings:
    enabled: bool = False
    role: str = ""
    user: str = "local"
    thread_id: str = "cli:local"
    chat_name: str = "Terminal"

    @classmethod
    def from_mapping(cls, enabled: bool, options: dict) -> CliSettings:
        return cls(
            enabled=bool(enabled),
            role=str(options.get("role") or ""),
            user=str(options.get("user") or "local"),
            thread_id=str(options.get("thread_id") or "cli:local"),
            chat_name=str(options.get("chat_name") or "Terminal"),
        )
