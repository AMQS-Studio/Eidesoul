"""ADR-0010 §4.5: the Slack body — Block Kit, `block_actions`, threads, and multi-file ingress."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from support import build_config, database

from monoagens.engine.commands import REGISTRY
from monoagens.engine.config import GatewaySettings
from monoagens.engine.inbound import Action, Address, GatewayError
from monoagens.engine.stores.history import HistoryStore
from monoagens.engine.turn.context import WorkingMemory
from monoagens.gateways.base import EditDebouncer
from monoagens.gateways.slack import ingress, render
from monoagens.gateways.slack.config import SlackSettings
from monoagens.gateways.slack.gateway import SlackGateway


class FakeTransport:
    """The Slack seam: records what the body asked for, no SDK and no network."""

    def __init__(self) -> None:
        self.posted: list[tuple] = []
        self.updated: list[tuple] = []
        self.uploaded: list[tuple] = []
        self.reacted: list[tuple] = []
        self.status: list[tuple] = []
        self.handlers = None
        self.closed = False
        self.downloaded: list[Path] = []
        self.bot_user_id = "U999"

    async def start(self, handlers):
        self.handlers = handlers

    async def close(self):
        self.closed = True

    async def post(self, channel, text, blocks=None, thread_ts=""):
        self.posted.append((channel, text, list(blocks or ()), thread_ts))
        return {"ts": f"ts{len(self.posted)}"}

    async def update(self, channel, ts, text, blocks=None):
        self.updated.append((channel, ts, text, list(blocks or ())))

    async def upload(self, channel, path, caption, thread_ts=""):
        self.uploaded.append((channel, Path(path), caption, thread_ts))
        return {"ts": "file-1"}

    async def download(self, url, target):
        self.downloaded.append(Path(target))
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        Path(target).write_bytes(b"file")  # noqa: ASYNC240 - a test double
        return True

    async def react(self, channel, ts, emoji):
        self.reacted.append((channel, ts, emoji))

    async def set_status(self, channel, thread_ts, status):
        self.status.append((channel, thread_ts, status))


class RecordingAgent:
    soul = SimpleNamespace(language="en")
    commands = REGISTRY

    def __init__(self) -> None:
        self.seen = []

    async def handle(self, percept, gateway):
        self.seen.append(percept)


def make_slack(tmp_path, transport=None):
    config = build_config(tmp_path)
    db = database()
    history = HistoryStore(db)
    windows = WorkingMemory(store=history)
    gateway = SlackGateway(
        config,
        db,
        history,
        windows,
        settings=GatewaySettings("slack", True, {}),
        transport=transport or FakeTransport(),
    )
    gateway.self_id = "U999"
    gateway.config.workspace.mkdir(parents=True, exist_ok=True)
    return gateway


def message_event(text: str = "halo", **extra) -> dict:
    return {
        "type": "message",
        "channel": "C1",
        "channel_type": "channel",
        "user": "U222",
        "text": text,
        "ts": "1700000000.000100",
        **extra,
    }


def approval_ui() -> dict:
    return {
        "kind": "approval",
        "approval_id": 42,
        "approval_class": "authorization",
        "title": "Shell command",
        "summary": "rm -rf scratch",
        "buttons": [
            {"id": "approve:42", "label": "Approve", "command": "/approve #42", "style": "primary"},
            {"id": "deny:42", "label": "Deny", "command": "/deny #42", "style": "danger"},
        ],
    }


def test_settings_read_both_tokens_from_the_environment() -> None:
    settings = SlackSettings.from_mapping(
        True,
        {"bot_token_env": "SB", "app_token_env": "SA"},
        {"SB": "xoxb-1", "SA": "xapp-1"},
    )

    assert settings.bot_token == "xoxb-1"
    assert settings.app_token == "xapp-1"
    assert settings.mode == "socket"


def test_an_unknown_mode_is_refused() -> None:
    with pytest.raises(ValueError, match="mode must be one of"):
        SlackSettings.from_mapping(True, {"mode": "smoke-signal"}, {})


def test_the_role_policy_uses_slack_vocabulary() -> None:
    assert SlackGateway.default_role_policy("administrator") == {
        "dm": True,
        "channels": "all",
        "threads": "all",
        "reply_to_bot": True,
    }
    assert SlackGateway.default_role_policy("member")["threads"] == "mention"
    assert SlackGateway.default_role_policy("guest")["dm"] is False


def test_an_approval_block_renders_block_kit() -> None:
    blocks = render.approval_blocks(approval_ui())

    assert blocks[0]["type"] == "header"
    assert blocks[1]["type"] == "section"
    assert blocks[2]["type"] == "actions"
    buttons = blocks[2]["elements"]
    assert buttons[0]["action_id"] == "cmd:/approve #42"
    assert buttons[0]["style"] == "primary"
    assert buttons[1]["style"] == "danger"
    assert render.approval_blocks({}) == []


def test_a_step_and_a_thinking_line_render_a_context_block() -> None:
    assert render.progress_blocks("send_step", "🔧 read -> executed") == [
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": "🔧 read -> executed"}],
        }
    ]
    thinking = render.progress_blocks("send_thinking", "weighing")
    assert thinking[0]["elements"][0]["text"].startswith("🧠")
    assert render.progress_blocks("send_thinking", "") == []


def test_a_message_in_a_thread_keeps_the_thread_address() -> None:
    percept = ingress.message_percept(
        message_event("halo", thread_ts="1700000000.000001"), self_id="U999"
    )

    assert percept is not None
    assert percept.thread == "C1:1700000000.000001"
    assert percept.hints["thread"] is True


def test_a_direct_message_is_private() -> None:
    percept = ingress.message_percept(
        message_event("halo", channel_type="im"), self_id="U999"
    )

    assert percept is not None
    assert percept.hints["dm"] is True
    assert percept.thread == "C1"


def test_a_mention_and_files_are_carried() -> None:
    percept = ingress.message_percept(
        message_event(
            "hey <@U999>",
            files=[
                {"name": "a.png", "mimetype": "image/png", "size": 5, "url_private": "https://x/a"},
                {"name": "b.pdf", "mimetype": "application/pdf", "size": 6, "url_private": "https://x/b"},
            ],
        ),
        self_id="U999",
    )

    assert percept is not None
    assert percept.hints["mention"] is True
    assert {media.kind for media in percept.payload.attachments} == {"image", "document"}


def test_an_edited_message_is_read_from_the_inner_message() -> None:
    percept = ingress.message_percept(
        {
            "type": "message",
            "subtype": "message_changed",
            "channel": "C1",
            "message": {"user": "U222", "text": "fixed", "ts": "1700000000.000100"},
        },
        self_id="U999",
    )

    assert percept is not None
    assert percept.payload.text == "fixed"


def test_a_block_action_becomes_the_command() -> None:
    found = ingress.action_percept(
        {
            "actions": [{"action_id": "cmd:/approve #42", "value": "/approve #42"}],
            "user": {"id": "U222", "name": "Budi"},
            "channel": {"id": "C1", "name": "ops"},
            "container": {"message_ts": "1700000000.000100", "thread_ts": "1700000000.000001"},
            "response_url": "https://hooks.slack/1",
        }
    )

    assert found is not None
    percept, response_url, message_ts = found
    assert response_url == "https://hooks.slack/1"
    assert message_ts == "1700000000.000100"
    assert percept.payload.text == "/approve #42"
    assert percept.thread == "C1:1700000000.000001"
    assert percept.hints["interactive"] is True


def test_send_text_posts_the_blocks_and_keeps_the_thread(tmp_path) -> None:
    transport = FakeTransport()
    gateway = make_slack(tmp_path, transport)

    asyncio.run(
        gateway.dispatch(
            Action(
                address=Address("slack", "C1:1700000000.000001"),
                capability="send_text",
                payload="Approve this?",
                meta={"ui": approval_ui()},
            )
        )
    )

    channel, text, blocks, thread_ts = transport.posted[0]
    assert channel == "C1"
    assert text == "Approve this?"
    assert thread_ts == "1700000000.000001"
    assert blocks[2]["type"] == "actions"


def test_progress_sends_once_then_updates_the_message(tmp_path) -> None:
    transport = FakeTransport()
    gateway = make_slack(tmp_path, transport)
    gateway.__dict__["_edits"] = EditDebouncer(0.0)
    action = Action(
        address=Address("slack", "C1:1700000000.000001"),
        capability="send_step",
        payload={"tool": "read", "status": "executed"},
        stream_id="turn:1:progress",
        replace_id="turn:1:progress",
    )

    first = asyncio.run(gateway.dispatch(action))
    second = asyncio.run(gateway.dispatch(action))

    assert first == second == "ts1"
    assert transport.posted[0][1] == "🔧 read -> executed"
    assert transport.status[0][1] == "1700000000.000001"
    assert transport.updated[0][1] == "ts1"
    assert transport.updated[0][3][0]["type"] == "context"


def test_a_click_is_routed_and_the_message_is_marked(tmp_path) -> None:
    transport = FakeTransport()
    gateway = make_slack(tmp_path, transport)
    agent = RecordingAgent()

    asyncio.run(
        gateway.on_action(
            {
                "actions": [{"action_id": "cmd:/deny #42", "value": "/deny #42"}],
                "user": {"id": "U222", "name": "Budi"},
                "channel": {"id": "C1", "name": "ops"},
                "container": {"message_ts": "1700000000.000100"},
            },
            agent,
        )
    )

    assert [percept.payload.text for percept in agent.seen] == ["/deny #42"]
    assert transport.updated[0][1] == "1700000000.000100"
    assert "/deny #42" in transport.updated[0][2]


def test_a_bot_message_is_never_a_turn(tmp_path) -> None:
    gateway = make_slack(tmp_path)
    agent = RecordingAgent()

    asyncio.run(gateway.on_message(message_event("mine", bot_id="B1"), agent))

    assert agent.seen == []


def test_prepare_media_downloads_every_file(tmp_path) -> None:
    transport = FakeTransport()
    gateway = make_slack(tmp_path, transport)
    percept = ingress.message_percept(
        message_event(
            "",
            files=[
                {"name": "a.png", "mimetype": "image/png", "size": 5, "url_private": "https://x/a"},
                {"name": "b.m4a", "mimetype": "audio/mp4", "size": 6, "url_private": "https://x/b"},
            ],
        ),
        self_id="U999",
    )
    assert percept is not None

    asyncio.run(gateway.prepare_media(percept.thread, percept.payload))

    assert len(transport.downloaded) == 2
    assert all(media.path is not None for media in percept.payload.attachments)


def test_send_file_refuses_a_path_outside_the_workspace(tmp_path) -> None:
    gateway = make_slack(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")

    with pytest.raises(GatewayError, match="outside the workspace"):
        asyncio.run(gateway.send_file("C1", outside))


def test_an_upload_inside_the_workspace_reaches_the_transport(tmp_path) -> None:
    transport = FakeTransport()
    gateway = make_slack(tmp_path, transport)
    path = gateway.config.workspace / "notes.txt"
    path.write_text("hi", encoding="utf-8")

    message_id = asyncio.run(gateway.send_file("C1:t1", path, "notes"))

    assert message_id == "file-1"
    assert transport.uploaded[0][0] == "C1"
    assert transport.uploaded[0][3] == "t1"


def test_a_reaction_reaches_the_transport(tmp_path) -> None:
    transport = FakeTransport()
    gateway = make_slack(tmp_path, transport)

    asyncio.run(gateway.react("C1", "1700000000.000100", "\U0001f44d"))

    assert transport.reacted == [("C1", "1700000000.000100", "\U0001f44d")]


def test_ready_learns_the_bot_user_id(tmp_path) -> None:
    transport = FakeTransport()
    gateway = make_slack(tmp_path, transport)

    asyncio.run(gateway.on_ready())

    assert gateway.self_id == "U999"


def test_run_installs_the_handlers_and_stop_closes_the_transport(tmp_path) -> None:
    transport = FakeTransport()
    gateway = make_slack(tmp_path, transport)

    asyncio.run(gateway.run(RecordingAgent()))
    asyncio.run(gateway.stop())

    assert transport.handlers is not None
    assert transport.closed is True
