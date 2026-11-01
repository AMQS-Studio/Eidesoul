"""`eidesoul init`: scaffold config.yaml, roles.yaml, soul.yaml, and .env without overwriting."""

from __future__ import annotations

import contextlib
import os
from pathlib import Path

from ..providers import PROVIDERS
from . import templates

PRIVATE_FILES = (".env", "roles.yaml")


def _write(path: Path, content: str, *, force: bool, mode: int | None = None) -> bool:
    if path.exists() and not force:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if mode is not None:
        with contextlib.suppress(OSError):
            path.chmod(mode)
    return True


def init(
    folder: str | Path,
    *,
    force: bool = False,
    provider: str,
    model: str = "",
) -> tuple[list[Path], list[Path]]:
    """Write the scaffold; return (written, skipped). `model` stays the operator's pick."""
    base = Path(folder).expanduser().resolve()
    base.mkdir(parents=True, exist_ok=True)
    (base / ".eidesoul" / "workspace").mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    skipped: list[Path] = []
    for name, content in templates.files(provider=provider, model=model).items():
        path = base / name
        mode = 0o600 if name in PRIVATE_FILES else None
        if _write(path, content, force=force, mode=mode):
            written.append(path)
        else:
            skipped.append(path)
    return written, skipped


def report(
    base: str | Path,
    written: list[Path],
    skipped: list[Path],
    *,
    provider: str,
    model: str = "",
) -> str:
    lines = [f"Eidesoul scaffold: {Path(base).expanduser().resolve()}"]
    for path in written:
        lines.append(f"  wrote  {path}")
    for path in skipped:
        lines.append(f"  kept   {path} (already exists; --force to overwrite)")
    key_env = PROVIDERS[provider].api_key_env
    steps = [
        f"fill .env ({key_env}, EIDESOUL_DATABASE_URL)"
        if key_env
        else "fill .env (EIDESOUL_DATABASE_URL)"
    ]
    if not model:
        steps.append(
            "choose a model: `eidesoul models` lists the ids the provider serves, then set "
            "engine.llm.model (rerun `eidesoul init --model <id> --force` or edit config.yaml)"
        )
    steps.extend(["eidesoul db upgrade", "eidesoul run"])
    lines.append("Next steps:")
    lines.extend(f"  {index}. {step}" for index, step in enumerate(steps, start=1))
    return "\n".join(lines)


def env_mode(path: str | Path) -> int:
    try:
        return os.stat(path).st_mode & 0o777
    except OSError:
        return 0
