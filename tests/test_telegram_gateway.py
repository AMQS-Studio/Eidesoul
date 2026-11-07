"""ADR-0010 §4.3: the Telegram body — rendering, ingress, dispatch, albums, and webhooks."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from support import build_config, database

from monoagens.engine.commands import REGISTRY
from monoagens.engine.config import GatewaySettings
from monoagens.engine.inbound import Action, Address
from monoagens.engine.stores.history import HistoryStore
from monoagens.engine.turn.context import Message, WorkingMemory
from monoagens.gateways.base import EditDebouncer
from monoagens.gateways.telegram import ingress, render, webhook
from monoagens.gateways.telegram.config import TelegramSettings
from monoagens.gateways.telegram.gateway import TelegramGateway


class FakeBot:
    """A Bot API double: records every method and answers what the body reads."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.downloaded: list[Path] = []
        self.closed = False

    async def call(self, method, params=None):
        self.calls.append((method, dict(params or {})))
        if method == "getMe":
            return {"id": "999", "username": "mono_bot"}
        if method == "getFile":
            return {"file_path": "voice/file_1.oga"}
        if method.startswith(("send", "edit")):
            return {"message_id": len(self.calls)}
        return True

    async def download(self, file_path, target):
        self.downloaded.append(Path(target))
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        Path(target).write_bytes(b"voice")  # noqa: ASYNC240 - a test double
        return True

    async def close(self):
        self.closed = True


class RecordingAgent:
    """The engine side of `handle_update`: it records percepts and exposes the catalog."""

    soul = SimpleNamespace(language="en")
    commands = REGISTRY

    def __init__(self) -> None:
        self.seen = []

    async def handle(self, percept, gateway):
        self.seen.append(percept)


def make_telegram(tmp_path, bot=None, *, options=None):
    config = build_config(tmp_path)
    db = database()
    history = HistoryStore(db)
    windows = WorkingMemory(store=history)
    gateway = TelegramGateway(
        config,
        db,
        history,
        windows,
        settings=GatewaySettings("telegram", True, dict(options or {})),
        transport=bot or FakeBot(),
    )
    gateway.self_id = "999"
    gateway.username = "mono_bot"
    gateway.config.workspace.mkdir(parents=True, exist_ok=True)
    return gateway


def update(text: str, *, chat: dict | None = None, **extra) -> dict:
    message = {
        "message_id": 11,
        "date": 1_700_000_000,
        "chat": chat or {"id": 111, "type": "private"},
        "from": {"id": 222, "first_name": "Budi", "is_bot": False},
        "text": text,
        **extra,
    }
    return {"update_id": 1, "message": message}


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


def test_settings_read_the_token_from_the_environment_variable() -> None:
    settings = TelegramSettings.from_mapping(
        True, {"bot_token_env": "TG_TOKEN"}, {"TG_TOKEN": "secret"}
    )
    assert settings.bot_token == "secret"
    assert settings.mode == "polling"
    assert settings.show_steps is True
    assert settings.interim_updates is True


def test_a_token_with_inner_whitespace_is_cleaned() -> None:
    settings = TelegramSettings.from_mapping(
        True, {}, {"TELEGRAM_BOT_TOKEN": "123:AA\n bb\tcc "}
    )
    assert settings.bot_token == "123:AAbbcc"


def test_an_unknown_mode_is_refused() -> None:
    with pytest.raises(ValueError, match="mode must be one of"):
        TelegramSettings.from_mapping(True, {"mode": "carrier-pigeon"}, {})


def test_the_role_policy_uses_telegram_vocabulary() -> None:
    assert TelegramGateway.default_role_policy("administrator") == {
        "dm": True,
        "groups": "all",
        "topics": "all",
        "reply_to_bot": True,
    }
    assert TelegramGateway.default_role_policy("member")["topics"] == "mention"
    assert TelegramGateway.default_role_policy("guest")["groups"] == "none"
    TelegramGateway.validate_role_policy(
        {"dm": True, "groups": "all", "topics": "all", "reply_to_bot": True}
    )


def test_the_inline_keyboard_carries_the_exact_commands() -> None:
    markup = render.inline_keyboard(approval_ui())

    assert markup == {
        "inline_keyboard": [
            [{"text": "Approve", "callback_data": "cmd:/approve #42"}],
            [{"text": "Deny", "callback_data": "cmd:/deny #42"}],
        ]
    }
    assert render.inline_keyboard({}) is None


def test_a_command_that_does_not_fit_callback_data_is_skipped() -> None:
    ui = {"buttons": [{"label": "Long", "command": "/x " + "a" * 80}]}
    assert render.inline_keyboard(ui) is None


def test_thinking_renders_as_an_expandable_blockquote() -> None:
    assert render.body("send_thinking", "<b>hmm</b>") == (
        "<blockquote expandable>&lt;b&gt;hmm&lt;/b&gt;</blockquote>"
    )
    assert render.body("send_interim", "<ok>") == "&lt;ok&gt;"


def test_chunking_respects_the_telegram_ceiling() -> None:
    body = "\n".join(f"line {index}" for index in range(2000))
    parts = render.chunk(body, 500)

    assert len(parts) > 1
    assert all(len(part) <= 500 for part in parts)
    assert "\n".join(parts) == body


def test_a_direct_message_becomes_a_private_percept() -> None:
    percept = ingress.message_percept(update("halo")["message"], self_id="999", username="mono_bot")

    assert percept is not None
    assert percept.thread == "111"
    assert percept.actor == "222"
    assert percept.payload.text == "halo"
    assert percept.hints["dm"] is True
    assert percept.hints["mention"] is False


def test_a_forum_topic_keeps_its_own_thread() -> None:
    chat = {"id": -1001, "type": "supergroup"}
    percept = ingress.message_percept(
        update("halo", chat=chat, message_thread_id=77)["message"],
        self_id="999",
        username="mono_bot",
    )

    assert percept is not None
    assert percept.thread == "-1001:77"
    assert percept.hints["topic"] is True
    assert percept.hints["group"] is True


def test_a_mention_and_a_reply_are_provable_facts() -> None:
    mentioned = update("hey", entities=[{"type": "mention", "offset": 0, "length": 4}])
    replied = update(
        "iya", reply_to_message={"message_id": 3, "from": {"id": 999, "is_bot": True}}
    )

    mention = ingress.message_percept(mentioned["message"], self_id="999")
    reply = ingress.message_percept(replied["message"], self_id="999")
    assert mention is not None
    assert mention.hints["mention"] is True
    assert reply is not None
    assert reply.hints["reply"] is True


def test_voice_and_documents_become_attachments() -> None:
    message = update(
        "dengarkan",
        voice={"file_id": "voice-1", "duration": 3, "mime_type": "audio/ogg"},
        document={"file_id": "doc-1", "file_name": "notes.pdf", "file_size": 10},
    )["message"]

    percept = ingress.message_percept(message, self_id="999")

    assert percept is not None
    kinds = {media.kind: media for media in percept.payload.attachments}
    assert set(kinds) == {"voice", "document"}
    assert kinds["document"].filename == "notes.pdf"
    assert kinds["voice"].ref == b"voice-1"


def test_a_callback_query_becomes_the_command() -> None:
    found = ingress.callback_percept(
        {
            "callback_query": {
                "id": "cb-1",
                "from": {"id": 222, "first_name": "Budi"},
                "data": "cmd:/approve #42",
                "message": {
                    "message_id": 11,
                    "chat": {"id": -1001, "type": "supergroup"},
                    "message_thread_id": 77,
                },
            }
        }
    )

    assert found is not None
    percept, callback_id, _message = found
    assert callback_id == "cb-1"
    assert percept.payload.text == "/approve #42"
    assert percept.thread == "-1001:77"
    assert percept.hints["interactive"] is True


def test_send_text_renders_the_card_and_the_keyboard(tmp_path) -> None:
    bot = FakeBot()
    gateway = make_telegram(tmp_path, bot)

    message_id = asyncio.run(
        gateway.dispatch(
            Action(
                address=Address("telegram", "111"),
                capability="send_text",
                payload="Approve this?",
                meta={"ui": approval_ui()},
            )
        )
    )

    method, params = bot.calls[0]
    assert method == "sendMessage"
    assert params["chat_id"] == "111"
    assert "Approve this?" in params["text"]
    assert "Shell command" in params["text"]
    assert params["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "cmd:/approve #42"
    assert message_id == "1"


def test_progress_sends_once_then_edits_the_bubble(tmp_path) -> None:
    bot = FakeBot()
    gateway = make_telegram(tmp_path, bot)
    gateway.__dict__["_edits"] = EditDebouncer(0.0)

    action = Action(
        address=Address("telegram", "-1001:77"),
        capability="send_thinking",
        payload="weighing",
        stream_id="turn:1:progress",
        replace_id="turn:1:progress",
    )

    first = asyncio.run(gateway.dispatch(action))
    second = asyncio.run(gateway.dispatch(action))

    assert first == second == "1"
    assert bot.calls[0][0] == "sendMessage"
    assert bot.calls[0][1]["message_thread_id"] == 77
    assert bot.calls[1][0] == "editMessageText"
    assert bot.calls[1][1]["message_id"] == "1"


def test_a_callback_update_answers_and_routes_the_command(tmp_path) -> None:
    bot = FakeBot()
    gateway = make_telegram(tmp_path, bot)
    agent = RecordingAgent()

    asyncio.run(
        gateway.handle_update(
            {
                "callback_query": {
                    "id": "cb-1",
                    "from": {"id": 222, "first_name": "Budi"},
                    "data": "cmd:/approve #42",
                    "message": {"message_id": 11, "chat": {"id": 111, "type": "private"}},
                }
            },
            agent,
        )
    )

    assert [percept.payload.text for percept in agent.seen] == ["/approve #42"]
    methods = [call[0] for call in bot.calls]
    assert methods == ["answerCallbackQuery", "editMessageReplyMarkup"]


def test_an_album_is_coalesced_into_one_turn(tmp_path) -> None:
    gateway = make_telegram(tmp_path)
    agent = RecordingAgent()
    first = ingress.message_percept(
        update("", photo=[{"file_id": "p1"}], media_group_id="album-1")["message"],
        self_id="999",
    )
    second = ingress.message_percept(
        update("", photo=[{"file_id": "p2"}], media_group_id="album-1")["message"],
        self_id="999",
    )
    assert first is not None
    assert second is not None

    asyncio.run(gateway.collect_album("album-1", first, agent))
    asyncio.run(gateway.collect_album("album-1", second, agent))
    asyncio.run(gateway.flush_album("album-1", agent))

    assert len(agent.seen) == 1
    assert len(agent.seen[0].payload.attachments) == 2


def test_an_edited_message_updates_the_window(tmp_path) -> None:
    gateway = make_telegram(tmp_path)
    gateway.windows.append(
        "111",
        Message(
            id="11",
            sender_name="Budi",
            sender_id="222",
            when=datetime.now(UTC),
            text="before",
        ),
    )
    agent = RecordingAgent()

    asyncio.run(
        gateway.handle_update(
            {"edited_message": {**update("after")["message"], "message_id": 11}}, agent
        )
    )

    assert agent.seen == []
    found = gateway.windows.find("111", "11")
    assert found is not None
    assert found.text == "after"


def test_prepare_media_downloads_a_voice_note(tmp_path) -> None:
    bot = FakeBot()
    gateway = make_telegram(tmp_path, bot)
    percept = ingress.message_percept(
        update("", voice={"file_id": "voice-1", "mime_type": "audio/ogg"})["message"],
        self_id="999",
    )
    assert percept is not None

    asyncio.run(gateway.prepare_media(percept.thread, percept.payload))

    assert bot.calls[0][0] == "getFile"
    assert bot.downloaded
    assert bot.downloaded[0].is_file()
    assert percept.payload.attachments[0].path is not None


def test_command_sync_publishes_the_registry(tmp_path) -> None:
    bot = FakeBot()
    gateway = make_telegram(tmp_path, bot)

    asyncio.run(gateway.sync_commands(RecordingAgent()))

    method, params = bot.calls[0]
    assert method == "setMyCommands"
    names = {item["command"] for item in params["commands"]}
    assert {"status", "approve", "thinking", "steps"} <= names


def test_a_send_file_routes_the_native_method(tmp_path) -> None:
    bot = FakeBot()
    gateway = make_telegram(tmp_path, bot)
    for name, method in (
        ("note.ogg", "sendVoice"),
        ("song.mp3", "sendAudio"),
        ("clip.mp4", "sendVideo"),
        ("photo.png", "sendPhoto"),
        ("notes.txt", "sendDocument"),
    ):
        path = gateway.config.workspace / name
        path.write_bytes(b"x")
        asyncio.run(gateway.send_file("111", path, "caption"))
        assert bot.calls[-1][0] == method


def test_typing_and_reactions_are_best_effort(tmp_path) -> None:
    bot = FakeBot()
    gateway = make_telegram(tmp_path, bot)

    asyncio.run(gateway.typing("111", True))
    asyncio.run(gateway.react("111", "11", "\U0001f44d"))
    asyncio.run(gateway.typing("111", False))

    assert [call[0] for call in bot.calls] == ["sendChatAction", "setMessageReaction"]


def test_stop_closes_the_transport(tmp_path) -> None:
    bot = FakeBot()
    gateway = make_telegram(tmp_path, bot)

    asyncio.run(gateway.stop())

    assert bot.closed is True


def test_the_webhook_request_is_parsed_and_the_secret_is_enforced() -> None:
    head = (
        b"POST /hook HTTP/1.1\r\nContent-Length: 2\r\n"
        b"X-Telegram-Bot-Api-Secret-Token: s3\r\n\r\n"
    )
    body = b"{}"

    assert webhook.parse_request(head, body, "s3") == {}
    assert webhook.parse_request(head, body, "other") is None
    assert webhook.parse_request(b"GET / HTTP/1.1\r\n\r\n", b"", "") is None
    assert webhook.parse_request(head, b"not json", "s3") is None


def test_the_opening_start_command_answers_like_help() -> None:
    message = {
        "message_id": 1,
        "date": 1767225600,
        "chat": {"id": 42, "type": "private"},
        "from": {"id": 42, "first_name": "Budi"},
        "text": "/start",
    }
    percept = ingress.message_percept(message, self_id="9", username="bot")
    assert percept is not None
    assert percept.payload.text == "/help"


def test_start_with_a_deep_link_payload_or_mention_still_maps_to_help() -> None:
    assert ingress.normalize_start("/start abc123") == "/help"
    assert ingress.normalize_start("/start@MonoAgens_bot") == "/help"
    assert ingress.normalize_start("/startx") == "/startx"
    assert ingress.normalize_start("halo /start") == "halo /start"
