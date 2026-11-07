"""Telegram gateway settings: the `gateways.telegram` block (ADR-0010 §5).

The token never lives in the YAML: `bot_token_env` names the environment variable, exactly like
every other secret in this project. A missing token is a boot refusal from `from_mapping`, not a
half-configured body.
"""


from collections.abc import Mapping
from dataclasses import dataclass

from ..base import clean_token

MODES = frozenset({"polling", "webhook"})


@dataclass(frozen=True)
class TelegramSettings:
    enabled: bool = False
    bot_token: str = ""
    mode: str = "polling"
    webhook_url: str = ""
    webhook_secret: str = ""
    webhook_host: str = "127.0.0.1"
    webhook_port: int = 8443
    sync_commands: bool = True
    show_thinking: bool = False
    show_steps: bool = True
    interim_updates: bool = True
    poll_timeout: float = 25.0

    @classmethod
    def from_mapping(
        cls, enabled: bool, options: Mapping[str, object], environ: Mapping[str, str]
    ) -> TelegramSettings:
        """Build the settings; the token comes from the environment variable it names."""
        env_name = str(options.get("bot_token_env") or "TELEGRAM_BOT_TOKEN").strip()
        bot_token = clean_token(
            str(options.get("bot_token") or "") or str(environ.get(env_name) or "")
        )
        mode = str(options.get("mode") or "polling").strip().lower()
        if mode not in MODES:
            raise ValueError(f"gateway 'telegram': mode must be one of {sorted(MODES)}")
        secret_env = str(options.get("webhook_secret_env") or "").strip()
        secret = str(environ.get(secret_env) or "").strip() if secret_env else ""
        try:
            port = int(str(options.get("webhook_port") or 8443))
        except (TypeError, ValueError):
            port = 8443
        try:
            timeout = float(str(options.get("poll_timeout") or 25))
        except (TypeError, ValueError):
            timeout = 25.0
        return cls(
            enabled=bool(enabled),
            bot_token=bot_token,
            mode=mode,
            webhook_url=str(options.get("webhook_url") or "").strip(),
            webhook_secret=secret,
            webhook_host=str(options.get("webhook_host") or "127.0.0.1").strip(),
            webhook_port=max(1, min(65535, port)),
            sync_commands=bool(options.get("sync_commands", True)),
            show_thinking=bool(options.get("show_thinking", False)),
            show_steps=bool(options.get("show_steps", True)),
            interim_updates=bool(options.get("interim_updates", True)),
            poll_timeout=max(0.0, timeout),
        )
