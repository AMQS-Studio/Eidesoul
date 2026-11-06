"""Command /language: switch MonoAgens's reply language to any installed catalog code.

The language lives in soul.yaml, the operator file; the `soul` cache row follows the file
(ADR-0046 §4.1). The catalog in `monoagens/lang/<code>.yaml` supplies every chat-visible string.
"""


import contextlib
import os

import yaml

from ..i18n import available, normalize_language, t
from ..registry import Handler
from . import CommandContext


def _soul_body(text: str) -> dict:
    if not text.strip():
        return {}
    body = yaml.safe_load(text)
    return body if isinstance(body, dict) else {}


def _write_soul(path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    with contextlib.suppress(OSError):
        tmp.chmod(path.stat().st_mode & 0o777)
    os.replace(tmp, path)


async def language_command(ctx: CommandContext) -> str:
    agent = ctx.agent
    lang = ctx.lang
    raw = ctx.args.strip()
    codes = ", ".join(available())
    if not raw:
        return t(
            "command.language.current", lang, language=agent.soul.language, available=codes
        )
    try:
        language = normalize_language(raw, allow_any=True)
    except ValueError:
        return t("command.language.invalid", lang, value=raw, available=codes)
    if agent.documents is None:
        return t("command.language.no_documents", lang)
    path = agent.config.paths.soul_yaml
    body = _soul_body(path.read_text(encoding="utf-8") if path.is_file() else "")
    body["language"] = language
    text = yaml.safe_dump(body, sort_keys=False, allow_unicode=True)
    _write_soul(path, text)
    agent.documents.put("soul", text, actor=ctx.sender)
    await agent.reload()
    return t("command.language.set", language, language=language)


COMMAND = Handler(
    name="language",
    description="Switch MonoAgens's reply language (installed catalog code).",
    fn=language_command,
    aliases=(),
    usage="[code]",
)
