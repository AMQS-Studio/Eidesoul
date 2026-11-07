"""ADR-0010 §4.4: the Discord body — embeds, components, threads, and fence-aware chunks."""

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
from monoagens.gateways.discord import ingress, render
from monoagens.gateways.discord.gateway import DiscordGateway


class FakeTransport:
    """The Discord seam: records what the body asked for, no SDK and no network."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, dict]] = []
        self.edits: list[tuple[str, str, dict]] = []
        self.uploads: list[tuple[str, Path, str]] = []
        self.reacted: list[tuple[str, str, str]] = []
        self.commands: list[dict] | None = None
        self.handlers = None
        self.closed = False
        self.downloaded: list[Path] = []

    async def start(self, handlers):
        self.handlers = handlers

    async def close(self):
        self.closed = True

    async def send(self, channel_id, payload):
        self.sent.append((channel_id, dict(payload)))
        return {"id": f"m{len(self.sent)}"}

    async def edit(self, channel_id, message_id, payload):
        self.edits.append((channel_id, message_id, dict(payload)))

    async def upload(self, channel_id, path, caption):
        self.uploads.append((channel_id, Path(path), caption))
        return {"id": "file-1"}

    async def download(self, url, target):
        self.downloaded.append(Path(target))
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        Path(target).write_bytes(b"file")  # noqa: ASYNC240 - a test double
        return True

    async def react(self, channel_id, message_id, emoji):
        self.reacted.append((channel_id, message_id, emoji))

    async def sync_commands(self, commands):
        self.commands = list(commands)


class RecordingAgent:
    soul = SimpleNamespace(language="en")
    commands = REGISTRY

    def __init__(self) -> None:
        self.seen = []

    async def handle(self, percept, gateway):
        self.seen.append(percept)


def make_discord(tmp_path, transport=None):
    config = build_config(tmp_path)
    db = database()
    history = HistoryStore(db)
    windows = WorkingMemory(store=history)
    gateway = DiscordGateway(
        config,
        db,
        history,
        windows,
        settings=GatewaySettings("discord", True, {}),
        transport=transport or FakeTransport(),
    )
    gateway.self_id = "999"
    gateway.config.workspace.mkdir(parents=True, exist_ok=True)
    return gateway


def message_payload(text: str = "halo", **extra) -> dict:
    return {
        "id": "500",
        "channel_id": "77",
        "guild_id": "12",
        "thread_id": "",
        "author": {"id": "222", "name": "Budi", "bot": False},
        "content": text,
        "timestamp": "2026-11-07T10:00:00+00:00",
        "mentions": [],
        "attachments": [],
        "reference": {},
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


def test_the_role_policy_uses_discord_vocabulary() -> None:
    assert DiscordGateway.default_role_policy("administrator") == {
        "dm": True,
        "channels": "all",
        "threads": "all",
        "reply_to_bot": True,
    }
    assert DiscordGateway.default_role_policy("member")["threads"] == "mention"
    assert DiscordGateway.default_role_policy("guest")["channels"] == "none"
    DiscordGateway.validate_role_policy(
        {"dm": True, "channels": "all", "threads": "all", "reply_to_bot": True}
    )


def test_an_approval_block_renders_an_embed_and_buttons() -> None:
    ui = approval_ui()

    embed = render.embed(ui)
    rows = render.components(ui)

    assert embed is not None
    assert embed["title"] == "Shell command"
    assert embed["description"] == "rm -rf scratch"
    assert rows[0]["type"] == 1
    buttons = rows[0]["components"]
    assert buttons[0]["custom_id"] == "cmd:/approve #42"
    assert buttons[0]["style"] == 3
    assert buttons[1]["style"] == 4
    assert render.embed({}) is None
    assert render.components({}) == []


def test_chunking_keeps_code_fences_closed() -> None:
    body = "intro\n```python\n" + "\n".join(f"x = {index}" for index in range(400)) + "\n```"

    parts = render.chunk(body, 500)

    assert len(parts) > 1
    assert all(len(part) <= 500 for part in parts)
    for part in parts:
        assert part.count("```") % 2 == 0, part


def test_a_guild_message_becomes_a_channel_percept() -> None:
    percept = ingress.message_percept(message_payload("halo"), self_id="999")

    assert percept is not None
    assert percept.thread == "77"
    assert percept.actor == "222"
    assert percept.hints["group"] is True
    assert percept.hints["thread"] is False


def test_a_thread_keeps_its_own_address() -> None:
    percept = ingress.message_percept(
        message_payload("halo", thread_id="88", channel_id="88"), self_id="999"
    )

    assert percept is not None
    assert percept.thread == "88"
    assert percept.hints["thread"] is True


def test_attachments_and_a_mention_are_carried() -> None:
    payload = message_payload(
        "look",
        mentions=[{"id": "999"}],
        attachments=[
            {"id": "1", "filename": "a.png", "content_type": "image/png", "size": 5, "url": "https://x/a.png"},
            {"id": "2", "filename": "b.pdf", "content_type": "application/pdf", "size": 6, "url": "https://x/b.pdf"},
        ],
    )

    percept = ingress.message_percept(payload, self_id="999")

    assert percept is not None
    assert percept.hints["mention"] is True
    kinds = {media.kind for media in percept.payload.attachments}
    assert kinds == {"image", "document"}


def test_an_interaction_becomes_the_command() -> None:
    found = ingress.interaction_percept(
        {
            "id": "i1",
            "channel_id": "77",
            "guild_id": "12",
            "user": {"id": "222", "name": "Budi"},
            "data": {"custom_id": "cmd:/deny #42", "values": []},
            "message": {"id": "500"},
        }
    )

    assert found is not None
    percept, interaction_id, message_id = found
    assert interaction_id == "i1"
    assert message_id == "500"
    assert percept.payload.text == "/deny #42"
    assert percept.hints["interactive"] is True


def test_send_text_sends_an_embed_and_the_component_row(tmp_path) -> None:
    transport = FakeTransport()
    gateway = make_discord(tmp_path, transport)

    asyncio.run(
        gateway.dispatch(
            Action(
                address=Address("discord", "77"),
                capability="send_text",
                payload="Approve this?",
                meta={"ui": approval_ui()},
            )
        )
    )

    channel, payload = transport.sent[0]
    assert channel == "77"
    assert payload["content"] == "Approve this?"
    assert payload["embed"]["title"] == "Shell command"
    assert payload["components"][0]["components"][0]["custom_id"] == "cmd:/approve #42"


def test_progress_sends_once_then_edits_with_a_spoiler(tmp_path) -> None:
    transport = FakeTransport()
    gateway = make_discord(tmp_path, transport)
    gateway.__dict__["_edits"] = EditDebouncer(0.0)
    action = Action(
        address=Address("discord", "77"),
        capability="send_thinking",
        payload="weighing",
        stream_id="turn:1:progress",
        replace_id="turn:1:progress",
    )

    first = asyncio.run(gateway.dispatch(action))
    second = asyncio.run(gateway.dispatch(action))

    assert first == second == "m1"
    assert transport.sent[0][1]["content"] == "> ||💭 weighing||"
    assert transport.edits[0][1] == "m1"
    assert transport.edits[0][2]["content"] == "> ||💭 weighing||"


def test_a_click_is_routed_and_the_buttons_are_stripped(tmp_path) -> None:
    transport = FakeTransport()
    gateway = make_discord(tmp_path, transport)
    agent = RecordingAgent()

    asyncio.run(
        gateway.on_interaction(
            {
                "id": "i1",
                "channel_id": "77",
                "guild_id": "12",
                "user": {"id": "222", "name": "Budi"},
                "data": {"custom_id": "cmd:/approve #42", "values": []},
                "message": {"id": "500"},
            },
            agent,
        )
    )

    assert [percept.payload.text for percept in agent.seen] == ["/approve #42"]
    assert transport.edits == [("77", "500", {"components": []})]


def test_the_body_ignores_its_own_message(tmp_path) -> None:
    gateway = make_discord(tmp_path)
    agent = RecordingAgent()

    own = message_payload("mine", author={"id": "999", "name": "me", "bot": True})
    asyncio.run(gateway.on_message(own, agent))

    assert agent.seen == []


def test_prepare_media_downloads_every_attachment(tmp_path) -> None:
    transport = FakeTransport()
    gateway = make_discord(tmp_path, transport)
    payload = message_payload(
        "",
        attachments=[
            {"id": "1", "filename": "a.png", "content_type": "image/png", "size": 5, "url": "https://x/a.png"},
            {"id": "2", "filename": "b.mp4", "content_type": "video/mp4", "size": 6, "url": "https://x/b.mp4"},
        ],
    )
    percept = ingress.message_percept(payload, self_id="999")
    assert percept is not None

    asyncio.run(gateway.prepare_media(percept.thread, percept.payload))

    assert len(transport.downloaded) == 2
    assert all(media.path is not None for media in percept.payload.attachments)


def test_send_file_refuses_a_path_outside_the_workspace(tmp_path) -> None:
    gateway = make_discord(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")

    with pytest.raises(GatewayError, match="outside the workspace"):
        asyncio.run(gateway.send_file("77", outside))


def test_an_upload_inside_the_workspace_reaches_the_transport(tmp_path) -> None:
    transport = FakeTransport()
    gateway = make_discord(tmp_path, transport)
    path = gateway.config.workspace / "notes.txt"
    path.write_text("hi", encoding="utf-8")

    message_id = asyncio.run(gateway.send_file("77", path, "notes"))

    assert message_id == "file-1"
    assert transport.uploads[0][2] == "notes"


def test_a_reaction_reaches_the_transport(tmp_path) -> None:
    transport = FakeTransport()
    gateway = make_discord(tmp_path, transport)

    asyncio.run(gateway.react("77", "500", "\U0001f44d"))

    assert transport.reacted == [("77", "500", "\U0001f44d")]


def test_ready_syncs_the_registry_as_slash_commands(tmp_path) -> None:
    transport = FakeTransport()
    gateway = make_discord(tmp_path, transport)
    agent = RecordingAgent()
    gateway._agent = agent

    asyncio.run(gateway.on_ready())

    names = {entry["command"] for entry in transport.commands or []}
    assert {"status", "approve", "thinking", "steps"} <= names


def test_run_installs_the_handlers_and_stop_closes_the_transport(tmp_path) -> None:
    transport = FakeTransport()
    gateway = make_discord(tmp_path, transport)

    asyncio.run(gateway.run(RecordingAgent()))
    asyncio.run(gateway.stop())

    assert transport.handlers is not None
    assert transport.closed is True


def test_settings_clean_the_token() -> None:
    from monoagens.gateways.discord.config import DiscordSettings

    settings = DiscordSettings.from_mapping(True, {}, {"DISCORD_BOT_TOKEN": " a b c "})
    assert settings.bot_token == "abc"
