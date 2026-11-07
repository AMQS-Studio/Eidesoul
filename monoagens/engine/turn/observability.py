"""Structured log, correlation ids, and the counters `/status` reads (ADR-0003).

One log line is one event. The mandatory fields are `ts`, `level`, `event`, and `logger`; the
optional correlation fields (`turn_id`, `thread_id`, `job_id`, `run_id`, `gateway`,
`duration_ms`) come from `contextvars`, so a call site never has to thread an id through every
function. The turn sets its ids once (ADR-0003 §4.2); background work sets `job_id`/`run_id`
and clears them again, so one task never carries another task's turn.

`event` is a stable key, not a sentence: the call site may pass `extra={"event": "turn.diam"}`.
A record without an explicit key reports its call site (`logger.function`) instead of one shared
placeholder, so every line carries a distinct, parseable event name.
No prompt, response, memory, or archive text belongs in a log line; the formatter additionally
redacts credentials and DSN passwords as a second net. `text` stays the default format; `json`
is one object per line and is what an external monitor is expected to parse.
"""


import contextlib
import json
import logging
import re
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

TURN_ID: ContextVar[str] = ContextVar("monoagens_turn_id", default="")
THREAD_ID: ContextVar[str] = ContextVar("monoagens_thread_id", default="")
JOB_ID: ContextVar[int | None] = ContextVar("monoagens_job_id", default=None)
RUN_ID: ContextVar[str] = ContextVar("monoagens_run_id", default="")
GATEWAY: ContextVar[str] = ContextVar("monoagens_gateway", default="")

FIELDS = ("turn_id", "thread_id", "job_id", "run_id", "gateway", "duration_ms")
FORMATS = ("text", "json")
LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

_DSN_PASSWORD = re.compile(r"(://[^:/@\s]+:)[^@\s]+(@)")
_KEY_LINE = re.compile(
    r"\b(?:sk|pk|api|token|secret|key)[-_][A-Za-z0-9_-]{8,}\b", re.IGNORECASE
)
_BEARER = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE)
_HANDLER_MARK = "monoagens_structured"
NOISY_LOGGERS = (
    "httpx",
    "httpcore",
    "huggingface_hub",
    "sentence_transformers",
    "urllib3",
    "filelock",
)


def redact(text: object) -> str:
    """Mask credentials in a log message; never a content filter, only a leak net."""
    value = str(text if text is not None else "")
    value = _DSN_PASSWORD.sub(r"\1***\2", value)
    value = _BEARER.sub("Bearer ***", value)
    return _KEY_LINE.sub("***", value)


@contextlib.contextmanager
def bind(**fields: Any):
    """Bind correlation ids for one turn/job; every log inside the block carries them."""
    tokens = []
    for name, var in (
        ("turn_id", TURN_ID),
        ("thread_id", THREAD_ID),
        ("job_id", JOB_ID),
        ("run_id", RUN_ID),
        ("gateway", GATEWAY),
    ):
        if name in fields:
            tokens.append((var, var.set(fields[name])))
    try:
        yield
    finally:
        for var, token in reversed(tokens):
            var.reset(token)


def enter(**fields: Any) -> list[tuple[ContextVar, Any]]:
    """Set correlation ids and return the reset tokens; call `leave` in a `finally`."""
    tokens: list[tuple[ContextVar, Any]] = []
    for name, var in (
        ("turn_id", TURN_ID),
        ("thread_id", THREAD_ID),
        ("job_id", JOB_ID),
        ("run_id", RUN_ID),
        ("gateway", GATEWAY),
    ):
        if name in fields:
            tokens.append((var, var.set(fields[name])))
    return tokens


def leave(tokens: list[tuple[ContextVar, Any]]) -> None:
    for var, token in reversed(tokens):
        var.reset(token)


def clear() -> None:
    """Drop every correlation id: background work never inherits a finished turn."""
    TURN_ID.set("")
    THREAD_ID.set("")
    JOB_ID.set(None)
    RUN_ID.set("")
    GATEWAY.set("")


def event_name(record: logging.LogRecord) -> str:
    """The one stable `event` key: the caller's name, else the call site."""
    explicit = str(getattr(record, "event", "") or "").strip()
    return explicit or f"{record.name}.{record.funcName or 'log'}"


class ContextFilter(logging.Filter):
    """Fill the mandatory `event` and the optional ids from the current context."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.event = event_name(record)
        for name, value in (
            ("turn_id", TURN_ID.get()),
            ("thread_id", THREAD_ID.get()),
            ("job_id", JOB_ID.get()),
            ("run_id", RUN_ID.get()),
            ("gateway", GATEWAY.get()),
        ):
            if name not in record.__dict__ and value:
                record.__dict__[name] = value
        return True


def _stamp(record: logging.LogRecord) -> str:
    created = datetime.fromtimestamp(record.created, UTC)
    return created.strftime("%Y-%m-%dT%H:%M:%S") + f".{int(record.msecs):03d}"


def exception_name(record: logging.LogRecord) -> str:
    """The exception class on this record, or "" when there is none.

    `record.exc_info` is not always `None` or a tuple: `logging` keeps whatever the caller
    passed, so `exc_info=False` (asyncio's own error handler) and `exc_info=True` both reach a
    formatter. A truthiness check on a bool would raise inside `emit` and lose the line.
    """
    info = record.exc_info
    if not info or not isinstance(info, tuple):
        return ""
    kind = info[0]
    return getattr(kind, "__name__", "") if kind is not None else ""


class JsonFormatter(logging.Formatter):
    """One JSON object per line: mandatory fields plus correlation ids, never a traceback."""

    def format(self, record: logging.LogRecord) -> str:
        body: dict[str, Any] = {
            "ts": _stamp(record),
            "level": record.levelname,
            "event": event_name(record),
            "logger": record.name,
            "message": redact(record.getMessage()),
        }
        for name in FIELDS:
            value = getattr(record, name, None)
            if value not in (None, ""):
                body[name] = value
        exception = exception_name(record)
        if exception:
            body["exception"] = exception
        return json.dumps(body, ensure_ascii=False, separators=(",", ":"))


class TextFormatter(logging.Formatter):
    """Human-readable line: time, level, logger, ids, event, then the message."""

    def format(self, record: logging.LogRecord) -> str:
        parts = [_stamp(record), record.levelname, record.name]
        turn_id = getattr(record, "turn_id", "")
        if turn_id:
            parts.append(f"turn={turn_id}")
        thread_id = getattr(record, "thread_id", "")
        if thread_id:
            parts.append(f"thread={thread_id}")
        job_id = getattr(record, "job_id", None)
        if job_id:
            parts.append(f"job={job_id}")
        run_id = getattr(record, "run_id", "")
        if run_id:
            parts.append(f"run={run_id}")
        gateway = getattr(record, "gateway", "")
        if gateway:
            parts.append(f"gateway={gateway}")
        duration_ms = getattr(record, "duration_ms", None)
        if duration_ms is not None:
            parts.append(f"duration_ms={duration_ms}")
        event = event_name(record)
        body = f"{' '.join(parts)} {event}: {redact(record.getMessage())}"
        exception = exception_name(record)
        if exception:
            body += f" [{exception}]"
        return body


def formatter(fmt: str) -> logging.Formatter:
    return JsonFormatter() if fmt == "json" else TextFormatter()


def level_number(name: str) -> int:
    value = logging.getLevelName(str(name or "INFO").strip().upper())
    return value if isinstance(value, int) else logging.INFO


def install(fmt: str = "text", level: str = "INFO") -> logging.Handler:
    """Replace our own handler on the root logger; never duplicates a line on reload."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, _HANDLER_MARK, False):
            root.removeHandler(handler)
            handler.close()
    handler = logging.StreamHandler()
    handler.setFormatter(formatter(fmt))
    handler.addFilter(ContextFilter())
    setattr(handler, _HANDLER_MARK, True)
    handler.setLevel(level_number(level))
    root.addHandler(handler)
    root.setLevel(level_number(level))
    return handler


def silence_third_party(*, debug: bool = False) -> None:
    """Keep transport and model-loading chatter off the console unless the operator asks."""
    level = logging.DEBUG if debug else logging.ERROR
    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(level)


def configure(log_settings) -> logging.Handler:
    """`engine.logging.format|level` from the loaded config (ADR-0003 §4.1)."""
    fmt = str(getattr(log_settings, "format", "text") or "text").strip().lower()
    level = str(getattr(log_settings, "level", "INFO") or "INFO").strip().upper()
    return install(fmt if fmt in FORMATS else "text", level if level in LEVELS else "INFO")
