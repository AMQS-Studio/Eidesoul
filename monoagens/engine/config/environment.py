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

def load_env(path: str | Path = ".env") -> None:
    """Read simple KEY=VALUE lines from a file in the working directory. Missing is fine."""
    file = Path(str(path)).expanduser()
    if file.is_absolute() or file.name != str(path):
        raise ConfigError(f"env file must be a bare file name in the working directory: {path}")
    file = (Path.cwd() / file.name).resolve()
    if not file.is_file():
        return
    for raw in file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
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

