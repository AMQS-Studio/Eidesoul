"""grep contract: same roots as read, deterministic order, compact modes, caps (ADR-0021)."""


import asyncio
from pathlib import Path

from support import build_config, role

from monoagens.engine.tools import REGISTRY, ToolContext, execute


def env(tmp_path, *, trusted: bool = True):
    config = build_config(tmp_path)
    config.workspace.mkdir(parents=True, exist_ok=True)
    (config.workspace / "public").mkdir(parents=True, exist_ok=True)
    (config.workspace / "scratch" / "chat").mkdir(parents=True, exist_ok=True)
    tools = ("*",) if trusted else ("grep", "read")
    ctx = ToolContext(
        config, (role("member", tools=tools),), ("grep",), thread_id="chat", sender="p"
    )
    return config, ctx


def run(ctx, args):
    return asyncio.run(execute(REGISTRY.by_name, "grep", args, ctx))


def write(config, relative: str, text: str) -> Path:
    path = config.workspace / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_member_sees_only_public_and_this_scratch(tmp_path) -> None:
    config, ctx = env(tmp_path, trusted=False)
    write(config, "public/a.py", "needle here")
    write(config, "scratch/chat/b.py", "needle there")
    write(config, "scratch/other/c.py", "needle hidden")

    result = run(ctx, {"pattern": "needle"})

    assert "public/a.py" in result
    assert "scratch/chat/b.py" in result
    assert "scratch/other" not in result


def test_count_and_files_modes_have_no_line_content(tmp_path) -> None:
    config, ctx = env(tmp_path)
    write(config, "public/a.py", "needle\nneedle\n")
    write(config, "public/b.py", "other")

    counted = run(ctx, {"pattern": "needle", "mode": "count"})
    listed = run(ctx, {"pattern": "needle", "mode": "files"})

    assert counted.startswith("2 matches in 1 files")
    assert "public/a.py: 2" in counted
    assert "needle" not in counted
    assert listed.startswith("1 files")
    assert "public/a.py" in listed


def test_secret_file_is_skipped(tmp_path) -> None:
    config, ctx = env(tmp_path)
    write(config, "public/.env", "TOKEN=needle")

    assert run(ctx, {"pattern": "needle"}) == "No matches."


def test_outside_root_is_a_permission_error(tmp_path) -> None:
    _config, ctx = env(tmp_path, trusted=False)

    result = run(ctx, {"pattern": "x", "path": "../outside"})

    assert result.startswith("error:")
    assert "not allowed" in result


def test_limit_and_next_hint(tmp_path) -> None:
    config, ctx = env(tmp_path)
    write(config, "public/a.py", "\n".join("needle" for _ in range(10)))

    result = run(ctx, {"pattern": "needle", "limit": 3})

    assert result.count("needle") == 3
    assert "next:" in result


def test_symlink_escaping_the_root_is_skipped(tmp_path) -> None:
    config, ctx = env(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("needle", encoding="utf-8")
    link = config.workspace / "public" / "link.txt"
    link.symlink_to(outside)

    assert run(ctx, {"pattern": "needle"}) == "No matches."
