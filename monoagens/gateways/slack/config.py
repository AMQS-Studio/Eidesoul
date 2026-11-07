"""Slack gateway settings: the `gateways.slack` block (ADR-0010 §5).

Socket Mode is the default because it needs no public URL: `app_token_env` names the app-level
token, `bot_token_env` the bot token, and `signing_secret_env` is only required for the HTTP
Events API.
"""


from collections.abc import Mapping
from dataclasses import dataclass

MODES = frozenset({"socket", "events"})


@dataclass(frozen=True)
class SlackSettings:
    enabled: bool = False
    bot_token: str = ""
    app_token: str = ""
    signing_secret: str = ""
    mode: str = "socket"
    show_thinking: bool = False
    show_steps: bool = True
    interim_updates: bool = True
    message_limit: int = 39000

    @classmethod
    def from_mapping(
        cls, enabled: bool, options: Mapping[str, object], environ: Mapping[str, str]
    ) -> SlackSettings:
        mode = str(options.get("mode") or "socket").strip().lower()
        if mode not in MODES:
            raise ValueError(f"gateway 'slack': mode must be one of {sorted(MODES)}")
        return cls(
            enabled=bool(enabled),
            bot_token=_secret(options, environ, "bot_token", "SLACK_BOT_TOKEN"),
            app_token=_secret(options, environ, "app_token", "SLACK_APP_TOKEN"),
            signing_secret=_secret(options, environ, "signing_secret", ""),
            mode=mode,
            show_thinking=bool(options.get("show_thinking", False)),
            show_steps=bool(options.get("show_steps", True)),
            interim_updates=bool(options.get("interim_updates", True)),
            message_limit=max(200, min(39000, _number(options.get("message_limit"), 39000))),
        )


def _secret(
    options: Mapping[str, object], environ: Mapping[str, str], key: str, default_env: str
) -> str:
    """A token from `options.<key>_env` (or the default variable), else `options.<key>`."""
    env_name = str(options.get(f"{key}_env") or default_env).strip()
    direct = str(options.get(key) or "").strip()
    if direct:
        return direct
    return str(environ.get(env_name) or "").strip() if env_name else ""


def _number(value: object, default: int) -> int:
    try:
        return int(str(value if value is not None else default))
    except (TypeError, ValueError):
        return default
