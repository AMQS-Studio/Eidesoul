"""sed contract: exact-snippet and anchor-verified range edits under the `read` roots."""


import asyncio
import hashlib
from pathlib import Path

from support import build_config, role

from monoagens.engine.tools import REGISTRY, ToolContext, execute, safe_chat


def env(tmp_path, *, trusted: bool = True, thread: str = "chat"):
    config = build_config(tmp_path)
    config.workspace.mkdir(parents=True, exist_ok=True)
    (config.workspace / "public").mkdir(parents=True, exist_ok=True)
    (config.workspace / "scratch" / safe_chat(thread)).mkdir(parents=True, exist_ok=True)
    tools = ("*",) if trusted else ("read", "sed")
    ctx = ToolContext(config, (role("member", tools=tools),), ("sed",), thread_id=thread)
    return config, ctx


def run(ctx, args, name: str = "sed"):
    return asyncio.run(execute(REGISTRY.by_name, name, args, ctx))


def write(config, relative: str, text: str) -> Path:
    path = config.workspace / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# ------------------------------------------------------------------ mode 1: snippet


def test_snippet_replaces_one_unique_match(tmp_path) -> None:
    config, ctx = env(tmp_path)
    path = write(config, "public/a.txt", "alpha\nbeta\ngamma\n")

    result = run(ctx, {"path": "public/a.txt", "old": "beta", "new": "BETA"})

    assert result == "ok: updated public/a.txt (replaced 1 of 1 matches)"
    assert path.read_text(encoding="utf-8") == "alpha\nBETA\ngamma\n"


def test_ambiguous_snippet_is_refused_with_line_numbers(tmp_path) -> None:
    config, ctx = env(tmp_path)
    path = write(config, "public/a.txt", "same\nother\nsame\n")

    result = run(ctx, {"path": "public/a.txt", "old": "same", "new": "new"})

    assert result.startswith("error:")
    assert "matches 2 times" in result
    assert "lines 1, 3" in result
    assert path.read_text(encoding="utf-8") == "same\nother\nsame\n"


def test_count_zero_replaces_every_match(tmp_path) -> None:
    config, ctx = env(tmp_path)
    path = write(config, "public/a.txt", "same same same\n")

    result = run(ctx, {"path": "public/a.txt", "old": "same", "new": "new", "count": 0})

    assert result.endswith("(replaced all 3 matches)")
    assert path.read_text(encoding="utf-8") == "new new new\n"


def test_count_two_replaces_the_first_two_matches(tmp_path) -> None:
    config, ctx = env(tmp_path)
    path = write(config, "public/a.txt", "x\nx\nx\n")

    result = run(ctx, {"path": "public/a.txt", "old": "x", "new": "y", "count": 2})

    assert result.endswith("(replaced 2 of 3 matches)")
    assert path.read_text(encoding="utf-8") == "y\ny\nx\n"


def test_missing_snippet_is_refused(tmp_path) -> None:
    config, ctx = env(tmp_path)
    path = write(config, "public/a.txt", "alpha\n")

    result = run(ctx, {"path": "public/a.txt", "old": "omega", "new": "x"})

    assert result.startswith("error:")
    assert "not found" in result
    assert path.read_text(encoding="utf-8") == "alpha\n"


def test_an_alias_reaches_the_same_handler(tmp_path) -> None:
    config, ctx = env(tmp_path)
    path = write(config, "public/a.txt", "alpha\n")

    assert run(ctx, {"path": "public/a.txt", "old": "alpha", "new": "beta"}, "edit").startswith(
        "ok: updated"
    )
    assert path.read_text(encoding="utf-8") == "beta\n"


# ------------------------------------------------------ mode 2: line range + anchor


def test_line_range_with_a_matching_anchor(tmp_path) -> None:
    config, ctx = env(tmp_path)
    path = write(config, "public/a.txt", "one\ntwo\nthree\nfour\n")

    result = run(
        ctx,
        {
            "path": "public/a.txt",
            "start_line": 2,
            "end_line": 3,
            "anchor": "two",
            "new": "TWO\nTHREE",
        },
    )

    assert result.endswith("(replaced lines 2-3 with 2 lines)")
    assert path.read_text(encoding="utf-8") == "one\nTWO\nTHREE\nfour\n"


def test_a_digest_prefix_anchor_is_accepted(tmp_path) -> None:
    config, ctx = env(tmp_path)
    path = write(config, "public/a.txt", "one\ntwo\nthree\n")
    anchor = hashlib.sha256(b"two").hexdigest()[:12]

    result = run(
        ctx,
        {"path": "public/a.txt", "start_line": 2, "anchor": anchor, "new": "TWO"},
    )

    assert result.startswith("ok: updated")
    assert path.read_text(encoding="utf-8") == "one\nTWO\nthree\n"


def test_a_mismatched_anchor_shows_the_actual_lines(tmp_path) -> None:
    config, ctx = env(tmp_path)
    path = write(config, "public/a.txt", "one\ntwo\nthree\n")

    result = run(
        ctx,
        {"path": "public/a.txt", "start_line": 2, "anchor": "nope", "new": "TWO"},
    )

    assert result.startswith("error:")
    assert "anchor does not match lines 2-2" in result
    assert "2: two" in result
    assert path.read_text(encoding="utf-8") == "one\ntwo\nthree\n"


def test_an_out_of_bounds_line_range_is_refused(tmp_path) -> None:
    config, ctx = env(tmp_path)
    path = write(config, "public/a.txt", "one\ntwo\nthree\n")

    result = run(
        ctx, {"path": "public/a.txt", "start_line": 9, "anchor": "x", "new": "y"}
    )

    assert result.startswith("error:")
    assert "past the end of the file (3 lines)" in result
    assert path.read_text(encoding="utf-8") == "one\ntwo\nthree\n"


def test_a_missing_anchor_is_refused(tmp_path) -> None:
    config, ctx = env(tmp_path)
    write(config, "public/a.txt", "one\n")

    assert run(ctx, {"path": "public/a.txt", "start_line": 1, "new": "x"}).startswith(
        "error: anchor is required"
    )


def test_an_empty_new_deletes_the_line_range(tmp_path) -> None:
    config, ctx = env(tmp_path)
    path = write(config, "public/a.txt", "one\ntwo\nthree\n")

    result = run(ctx, {"path": "public/a.txt", "start_line": 2, "anchor": "two", "new": ""})

    assert result.startswith("ok: updated")
    assert path.read_text(encoding="utf-8") == "one\nthree\n"


def test_a_call_with_neither_mode_is_refused(tmp_path) -> None:
    config, ctx = env(tmp_path)
    write(config, "public/a.txt", "one\n")

    result = run(ctx, {"path": "public/a.txt", "new": "x"})

    assert result.startswith("error:")
    assert "`old`" in result


# ---------------------------------------------------------------------- security


def test_a_secret_file_is_closed(tmp_path) -> None:
    config, ctx = env(tmp_path)
    path = write(config, ".env", "TOKEN=a\n")

    result = run(ctx, {"path": ".env", "old": "TOKEN=a", "new": "TOKEN=b"})

    assert result.startswith("error:")
    assert "closed to sed" in result
    assert path.read_text(encoding="utf-8") == "TOKEN=a\n"


def test_host_configuration_is_closed(tmp_path) -> None:
    config, ctx = env(tmp_path)
    path = write(config, "config.yaml", "engine: {}\n")

    result = run(ctx, {"path": "config.yaml", "old": "engine", "new": "harness"})

    assert result.startswith("error:")
    assert "settings tool" in result
    assert path.read_text(encoding="utf-8") == "engine: {}\n"


def test_a_path_outside_the_roots_is_a_permission_error(tmp_path) -> None:
    _config, ctx = env(tmp_path, trusted=False)

    result = run(ctx, {"path": "../outside.txt", "old": "a", "new": "b"})

    assert result.startswith("error:")
    assert "not allowed" in result


def test_public_is_read_only_for_an_untrusted_role(tmp_path) -> None:
    config, ctx = env(tmp_path, trusted=False)
    path = write(config, "public/a.txt", "alpha\n")

    result = run(ctx, {"path": "public/a.txt", "old": "alpha", "new": "beta"})

    assert result.startswith("error:")
    assert "public is read-only" in result
    assert path.read_text(encoding="utf-8") == "alpha\n"


def test_an_untrusted_role_edits_its_own_scratch(tmp_path) -> None:
    config, ctx = env(tmp_path, trusted=False)
    own = f"scratch/{safe_chat('chat')}/a.txt"
    path = write(config, own, "alpha\n")

    result = run(ctx, {"path": own, "old": "alpha", "new": "beta"})

    assert result.startswith("ok: updated")
    assert path.read_text(encoding="utf-8") == "beta\n"


def test_a_missing_file_is_refused(tmp_path) -> None:
    _config, ctx = env(tmp_path)

    assert run(ctx, {"path": "public/none.txt", "old": "a", "new": "b"}).startswith(
        "error: file not found"
    )
