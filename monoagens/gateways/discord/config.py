"""Discord gateway settings: the `gateways.discord` block (ADR-0010 §5).

The token is read from the environment variable `bot_token_env` names; a missing token refuses
at boot with a clear message instead of a half-started client.
"""


from collections.abc import Mapping
from dataclasses import dataclass

from ..base import clean_token


@dataclass(frozen=True)
class DiscordSettings:
    enabled: bool = False
    bot_token: str = ""
    sync_commands: bool = True
    show_thinking: bool = False
    show_steps: bool = True
    interim_updates: bool = True
    message_limit: int = 2000

    @classmethod
    def from_mapping(
        cls, enabled: bool, options: Mapping[str, object], environ: Mapping[str, str]
    ) -> DiscordSettings:
        env_name = str(options.get("bot_token_env") or "DISCORD_BOT_TOKEN").strip()
        bot_token = clean_token(
            str(options.get("bot_token") or "") or str(environ.get(env_name) or "")
        )
        try:
            limit = int(str(options.get("message_limit") or 2000))
        except (TypeError, ValueError):
            limit = 2000
        return cls(
            enabled=bool(enabled),
            bot_token=bot_token,
            sync_commands=bool(options.get("sync_commands", True)),
            show_thinking=bool(options.get("show_thinking", False)),
            show_steps=bool(options.get("show_steps", True)),
            interim_updates=bool(options.get("interim_updates", True)),
            message_limit=max(200, min(2000, limit)),
        )
