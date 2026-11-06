"""Workspace roots contract per role: public read-only, scratch per chat, private trusted-only."""


import asyncio
from pathlib import Path

from support import make_agent, role

from monoagens.engine.registry import Handler
from monoagens.engine.tools import execute, safe_chat
from monoagens.engine.tools.send_file import TOOL as SEND_FILE
from monoagens.engine.tools.send_message import TOOL as SEND_MESSAGE

SEND = Handler(
    name="send_file",
    family="write",
    description="test",
    fn=SEND_FILE.fn,
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string"}, "chat": {"type": "string"}},
    },
)
SEND_TO = Handler(
    name="send_message",
    family="write",
    description="test",
    fn=SEND_MESSAGE.fn,
    parameters={
        "type": "object",
        "properties": {
            "chat": {"type": "string"},
            "text": {"type": "string"},
            "reason": {"type": "string"},
        },
    },
)


def member_env(tmp_path):
    return make_agent(
        tmp_path,
        roles={
            "member": role(
                "member",
                tools=("read", "write", "send_file", "send_message"),
                commands=("*",),
            )
        },
        default_role="member",
    )


def member_ctx(env, thread_id: str = "111@g.us"):
    return env.agent.tool_context(
        thread_id, "628111", (env.config.roles.get("member"),), env.gateway
    )


def call(env, name: str, args: dict, ctx):
    specs = dict(env.agent.tools.by_name)
    specs["send_file"] = SEND
    specs["send_message"] = SEND_TO
    return asyncio.run(execute(specs, name, args, ctx))


def test_member_reads_public_and_writes_scratch(tmp_path) -> None:
    env = member_env(tmp_path)
    (env.config.workspace / "public" / "x.txt").write_text("isi publik", encoding="utf-8")
    ctx = member_ctx(env)

    read = call(env, "read", {"path": "public/x.txt"}, ctx)
    write = call(env, "write", {"path": "scratch/a.txt", "content": "halo"}, ctx)
    read_again = call(env, "read", {"path": "scratch/a.txt"}, ctx)

    assert "isi publik" in read
    assert write.startswith("ok:")
    assert "halo" in read_again
    assert (env.config.workspace / "scratch" / safe_chat(ctx.thread_id) / "a.txt").is_file()


def test_member_bare_path_is_relative_to_scratch(tmp_path) -> None:
    env = member_env(tmp_path)
    ctx = member_ctx(env)

    write = call(env, "write", {"path": "catatan.txt", "content": "bare"}, ctx)
    read = call(env, "read", {"path": "catatan.txt"}, ctx)

    assert write.startswith("ok:")
    assert "bare" in read
    target = env.config.workspace / "scratch" / safe_chat(ctx.thread_id) / "catatan.txt"
    assert target.is_file()


def test_member_cannot_read_private_paths(tmp_path) -> None:
    env = member_env(tmp_path)
    for name in ("attachments", "whatsapp"):
        target = env.config.workspace / name / "x.txt"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("rahasia", encoding="utf-8")
    (env.config.workspace / "private").mkdir(parents=True, exist_ok=True)
    (env.config.workspace / "private" / "x.txt").write_text("rahasia", encoding="utf-8")
    ctx = member_ctx(env)

    for name in (
        "private/x.txt",
        str(env.config.workspace / "attachments" / "x.txt"),
        str(env.config.workspace / "whatsapp" / "x.txt"),
    ):
        result = call(env, "read", {"path": name}, ctx)
        assert result.startswith("error: path is not allowed for this role"), name


def test_member_cannot_write_public(tmp_path) -> None:
    env = member_env(tmp_path)
    ctx = member_ctx(env)

    result = call(env, "write", {"path": "public/x.txt", "content": "nope"}, ctx)

    assert result == "error: public is read-only for this role"


def test_member_send_reads_public_but_not_private(tmp_path) -> None:
    env = member_env(tmp_path)
    (env.config.workspace / "public" / "ok.txt").write_text("ok", encoding="utf-8")
    private = env.config.workspace / "private" / "s.txt"
    private.parent.mkdir(parents=True, exist_ok=True)
    private.write_text("no", encoding="utf-8")
    ctx = member_ctx(env)

    public = call(env, "send_file", {"path": "public/ok.txt"}, ctx)
    denied = call(env, "send_file", {"path": "private/s.txt"}, ctx)

    assert not public.startswith("error:")
    assert denied.startswith("error: path is not allowed for this role")
    assert env.gateway.files[0][1].name == "ok.txt"


def test_member_round_trips_the_reported_scratch_path(tmp_path) -> None:
    """P0-3: every form `write` may report back must resolve for `send_file` and `read`."""
    env = member_env(tmp_path)
    ctx = member_ctx(env)
    slug = safe_chat(ctx.thread_id)

    write = call(env, "write", {"path": "scratch/jadwal.txt", "content": "isi"}, ctx)

    assert write == f"ok: wrote 3 characters to scratch/{slug}/jadwal.txt"
    for name in ("jadwal.txt", "scratch/jadwal.txt", f"scratch/{slug}/jadwal.txt"):
        assert not call(env, "send_file", {"path": name}, ctx).startswith("error:"), name
        assert "isi" in call(env, "read", {"path": name}, ctx), name
    assert len(env.gateway.files) == 3


def test_member_cannot_escape_scratch_through_the_slug(tmp_path) -> None:
    env = member_env(tmp_path)
    ctx = member_ctx(env)

    result = call(env, "read", {"path": "scratch/../../private/x.txt"}, ctx)

    assert result.startswith("error:")


def test_member_cannot_send_to_another_chat(tmp_path) -> None:
    env = member_env(tmp_path)
    target = ("628999999999@s.whatsapp.net", "Budi")
    env.gateway.resolve_target = lambda query: (target, [])
    ctx = member_ctx(env)

    result = call(env, "send_message", {"chat": "budi", "text": "halo"}, ctx)

    assert result.startswith(("denied:", "error:"))
    assert not env.gateway.sent


def test_trusted_role_keeps_full_workspace(tmp_path) -> None:
    env = make_agent(
        tmp_path,
        roles={"owner": role("owner", tools=("*",), commands=("*",))},
        default_role="owner",
    )
    (env.config.workspace / "attachments").mkdir(parents=True, exist_ok=True)
    (env.config.workspace / "attachments" / "x.txt").write_text("privat", encoding="utf-8")
    ctx = env.agent.tool_context(
        "111@g.us", "628111", (env.config.roles.get("owner"),), env.gateway
    )

    read = call(env, "read", {"path": "attachments/x.txt"}, ctx)
    write = call(env, "write", {"path": "public/owner.txt", "content": "boleh"}, ctx)

    assert "privat" in read
    assert write.startswith("ok:")
    assert (env.config.workspace / "public" / "owner.txt").read_text(encoding="utf-8") == (
        "boleh"
    )


def test_safe_chat_strips_path_separators() -> None:
    value = safe_chat("../../x/y")

    assert "/" not in value
    assert "\\" not in value
    assert Path(value).name == value
    assert value not in {"", ".", ".."}


def test_safe_chat_keeps_thread_ids_that_sanitize_alike_apart() -> None:
    dashed = safe_chat("app:user_1")
    underscored = safe_chat("app_user:1")

    assert dashed != underscored
    assert Path(dashed).name == dashed
    assert Path(underscored).name == underscored
    assert safe_chat("app:user_1") == dashed
    assert safe_chat("").startswith("chat_")
