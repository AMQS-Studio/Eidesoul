"""Environment, database URL, and session-file helpers."""

import contextlib
import logging
import os
from pathlib import Path

from .models import DatabaseConfig
from .schema import ConfigError

log = logging.getLogger(__name__)

def resolve_database_url(database: DatabaseConfig) -> str:
    """Effective URL: env `url_env` when set, otherwise `url`."""
    env_name = str(database.url_env or "").strip()
    if env_name and os.environ.get(env_name):
        return str(os.environ[env_name]).strip()
    return str(database.url or "").strip()

def load_env(path: str | Path = ".env", *, root: Path | None = None) -> None:
    """Read simple KEY=VALUE lines from an env file. Missing is fine.

    A bare name or relative path resolves under the working directory. Absolute paths (and
    paths that leave the working directory) are accepted only when the resolved file stays
    inside the working directory, or inside `root` when the caller names the deployment
    directory. Anything else is a refusal, not a silent read of an unrelated file.
    """
    cwd = Path.cwd().resolve()
    raw = Path(str(path)).expanduser()
    if root is None:
        # The bare default: only a file name in the working directory, never a traversal.
        if raw.is_absolute() or raw.name != str(path):
            raise ConfigError(
                f"env file must be a bare file name in the working directory: {path}"
            )
        file = (cwd / raw.name).resolve()
    else:
        boundary = Path(root).expanduser().resolve()
        file = raw.resolve() if raw.is_absolute() else (cwd / raw).resolve()
        if not (file.is_relative_to(cwd) or file.is_relative_to(boundary)):
            raise ConfigError(
                f"env file must stay inside the working directory: {path}"
            )
    if not file.is_file():
        return
    for line_raw in file.read_text(encoding="utf-8").splitlines():
        line = line_raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

def secure_db(path: Path) -> None:
    """The session DB holds credentials; never leave it world-readable."""
    with contextlib.suppress(OSError):
        path.parent.chmod(0o700)
    if path.exists():
        with contextlib.suppress(OSError):
            path.chmod(0o600)

def drop_session(path: Path) -> None:
    """Delete a temporary session DB together with its WAL/SHM files."""
    for suffix in ("", "-wal", "-shm"):
        try:
            Path(f"{path}{suffix}").unlink(missing_ok=True)
        except OSError:
            log.warning("failed to delete session %s", path, exc_info=True)

