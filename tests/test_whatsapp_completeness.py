"""ADR-0010 §4.2: WhatsApp completeness — media, reactions, edits, and structured stanzas."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from neonize.proto.Neonize_pb2 import Message as ProtoMessage
from neonize.proto.waE2E.WAWebProtobufsE2E_pb2 import ProtocolMessage
from test_whatsapp_gateway import BOT, make_gateway

from monoagens.engine.inbound import Action, Address
from monoagens.engine.turn.context import Media, Message
from monoagens.gateways.base import EditDebouncer
from monoagens.gateways.whatsapp import ingress
from monoagens.gateways.whatsapp.media import message_text, send_kind


class RichClient:
    """Every send path the body uses, recording the call and its arguments."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.downloads: list[str] = []

    async def send_message(self, jid, payload):
        self.calls.append(("message", payload))
        return SimpleNamespace(ID="sent-1")

    async def send_sticker(self, jid, path):
        self.calls.append(("sticker", Path(path).name))
        return SimpleNamespace(ID="sticker-1")

    async def send_image(self, jid, path, caption=None):
        self.calls.append(("image", Path(path).name, caption))
        return SimpleNamespace(ID="image-1")

    async def send_document(self, jid, path, caption=None, filename=None):
        self.calls.append(("document", Path(path).name, caption))
        return SimpleNamespace(ID="doc-1")

    async def send_audio(self, jid, path, ptt=False):
        self.calls.append(("audio", Path(path).name, ptt))
        return SimpleNamespace(ID="audio-1")

    async def send_video(self, jid, path, caption=None):
        self.calls.append(("video", Path(path).name, caption))
        return SimpleNamespace(ID="video-1")

    async def get_me(self):
        return SimpleNamespace(JID=SimpleNamespace(User=BOT))

    async def build_reaction(self, chat, sender, message_id, reaction):
        self.calls.append(("build_reaction", message_id, reaction))
        return SimpleNamespace(reactionMessage=SimpleNamespace(text=reaction))

    async def edit_message(self, chat, message_id, new_message):
        self.calls.append(("edit", message_id, new_message.conversation))
        return SimpleNamespace(ID=message_id)

    async def download_any(self, proto, target):
        self.downloads.append(target)
        path = Path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"data")  # noqa: ASYNC240 - a test double
        return target


def gateway_with_client(tmp_path, *, speech=None):
    gateway = make_gateway(tmp_path)
    gateway.config.workspace.mkdir(parents=True, exist_ok=True)
    gateway.client = RichClient()
    if speech is not None:
        gateway.config = replace(gateway.config, speech=speech)
    return gateway


def action(capability: str, payload=None, **kwargs) -> Action:
    return Action(
        address=Address("whatsapp", f"{BOT}@s.whatsapp.net"),
        capability=capability,
        payload=payload,
        **kwargs,
    )


def write(tmp_path, name: str, content: bytes = b"x") -> Path:
    path = tmp_path / name
    path.write_bytes(content)
    return path


def message_with(*attachments: Media, text: str = "", message_id: str = "wamid-1") -> Message:
    return Message(
        id=message_id,
        sender_name="Budi",
        sender_id="628222222222",
        when=datetime.now(UTC),
        text=text,
        attachments=attachments,
    )


def inbound_event(*, chat: str = "628222222222@s.whatsapp.net", event_id: str = "wamid-1"):
    ev = ProtoMessage()
    ev.Info.ID = event_id
    ev.Info.Timestamp = 1_700_000_000
    ev.Info.MessageSource.Chat.User, _, ev.Info.MessageSource.Chat.Server = chat.partition("@")
    ev.Info.MessageSource.Sender.User = "628222222222"
    ev.Info.MessageSource.Sender.Server = "s.whatsapp.net"
    return ev


def test_send_kind_classifies_voice_audio_video_and_files(tmp_path) -> None:
    assert send_kind(write(tmp_path, "note.ogg")) == "voice"
    assert send_kind(write(tmp_path, "note.opus")) == "voice"
    assert send_kind(write(tmp_path, "song.mp3")) == "audio"
    assert send_kind(write(tmp_path, "clip.m4a")) == "audio"
    assert send_kind(write(tmp_path, "movie.mp4")) == "video"
    assert send_kind(write(tmp_path, "movie.mov")) == "video"
    assert send_kind(write(tmp_path, "notes.txt")) == "file"


def test_send_kind_still_reads_image_bytes(tmp_path) -> None:
    jpeg = write(tmp_path, "photo.bin", b"\xff\xd8\xff" + b"\x00" * 40)
    assert send_kind(jpeg) == "photo"


def test_dispatch_send_voice_uses_a_playable_voice_note(tmp_path) -> None:
    gateway = gateway_with_client(tmp_path)
    note = gateway.config.workspace / "note.ogg"
    note.write_bytes(b"OggS")

    message_id = asyncio.run(gateway.dispatch(action("send_voice", str(note))))

    assert message_id == "audio-1"
    assert gateway.client.calls == [("audio", "note.ogg", True)]


def test_dispatch_send_file_routes_audio_video_and_documents(tmp_path) -> None:
    gateway = gateway_with_client(tmp_path)
    workspace = gateway.config.workspace
    for name, content in (
        ("song.mp3", b"ID3"),
        ("movie.mp4", b"\x00\x00\x00\x18ftyp"),
        ("notes.txt", b"hi"),
    ):
        (workspace / name).write_bytes(content)

    for name in ("song.mp3", "movie.mp4", "notes.txt"):
        asyncio.run(
            gateway.dispatch(action("send_file", {"path": str(workspace / name)}))
        )

    assert gateway.client.calls == [
        ("audio", "song.mp3", False),
        ("video", "movie.mp4", None),
        ("document", "notes.txt", None),
    ]


def test_dispatch_react_sends_a_native_reaction(tmp_path) -> None:
    gateway = gateway_with_client(tmp_path)

    asyncio.run(
        gateway.dispatch(
            action("react", {"message_id": "wamid-1", "emoji": "\U0001f44d"})
        )
    )

    assert gateway.client.calls[0] == ("build_reaction", "wamid-1", "\U0001f44d")
    assert gateway.client.calls[1][0] == "message"


def test_progress_streams_send_once_then_edit_the_same_bubble(tmp_path) -> None:
    gateway = gateway_with_client(tmp_path)
    gateway.__dict__["_edits"] = EditDebouncer(0.0)
    progress = Action(
        address=Address("whatsapp", f"{BOT}@s.whatsapp.net"),
        capability="send_thinking",
        payload="weighing options",
        stream_id="turn:1:progress",
        replace_id="turn:1:progress",
    )

    first = asyncio.run(gateway.dispatch(progress))
    second = asyncio.run(gateway.dispatch(progress))

    assert first == second == "sent-1"
    assert gateway.client.calls[0] == ("message", "💭 weighing options")
    assert gateway.client.calls[1] == ("edit", "sent-1", "💭 weighing options")


def test_a_step_bubble_renders_the_tool_and_status(tmp_path) -> None:
    gateway = gateway_with_client(tmp_path)

    asyncio.run(
        gateway.dispatch(
            action(
                "send_step",
                {"tool": "read", "status": "executed"},
                replace_id="turn:2:progress",
            )
        )
    )

    assert gateway.client.calls[0][1] == "🔧 read -> executed"


def test_an_approval_prompt_gets_the_reaction_hint_and_a_mapping(tmp_path) -> None:
    gateway = gateway_with_client(tmp_path)
    # The engine appends its readable badge; the body turns that into the decision pair.
    message_id = asyncio.run(
        gateway.dispatch(
            action(
                "send_text",
                'Approve?\n\n🕓 Waiting: /approve 42 (or just reply "ok, send").',
            )
        )
    )

    assert gateway.approval_command("\U0001f44d", message_id) == "/approve #42"
    assert gateway.approval_command("\u274c", message_id) == "/deny #42"
    assert gateway.approval_command("\U0001f44d", "unknown") == ""
    sent = gateway.client.calls[0][1]
    assert "Approve?" in sent
    assert "👍" in sent


def test_an_explicit_ui_block_also_maps_the_prompt(tmp_path) -> None:
    gateway = gateway_with_client(tmp_path)
    message_id = asyncio.run(
        gateway.dispatch(
            action(
                "send_text",
                "Approve?",
                meta={
                    "ui": {
                        "kind": "approval",
                        "approval_id": 7,
                        "title": "Shell",
                        "summary": "rm -rf scratch",
                        "buttons": [{"label": "Approve", "command": "/approve #7"}],
                    }
                },
            )
        )
    )

    assert gateway.approval_command("\U0001f44d", message_id) == "/approve #7"


def test_a_reaction_on_the_prompt_becomes_the_command(tmp_path) -> None:
    gateway = gateway_with_client(tmp_path)
    gateway.approvals_by_message["sent-1"] = 42
    ev = inbound_event(chat=f"{BOT}@s.whatsapp.net", event_id="reaction-1")
    ev.Message.reactionMessage.text = "\U0001f44d"
    ev.Message.reactionMessage.key.ID = "sent-1"

    percept = ingress.reaction_target(ev, gateway)

    assert percept is not None
    assert percept.payload.text == "/approve #42"
    assert percept.hints["interactive"] is True
    assert percept.actor == "628222222222"


def test_a_reaction_on_an_unknown_message_is_not_a_turn(tmp_path) -> None:
    gateway = gateway_with_client(tmp_path)
    ev = inbound_event(event_id="reaction-2")
    ev.Message.reactionMessage.text = "\U0001f44d"
    ev.Message.reactionMessage.key.ID = "someone-else"

    assert ingress.reaction_target(ev, gateway) is None


def test_an_inbound_edit_yields_the_new_text(tmp_path) -> None:
    gateway = gateway_with_client(tmp_path)
    ev = inbound_event(event_id="wamid-9")
    ev.Message.protocolMessage.type = ProtocolMessage.MESSAGE_EDIT
    ev.Message.protocolMessage.key.ID = "wamid-1"
    ev.Message.protocolMessage.editedMessage.conversation = "typo fixed"

    found = ingress.edit_target(ev)

    assert found == ("wamid-1", "typo fixed")
    assert found is not None
    # An edit that is not addressed refreshes the window; it is not a new turn.
    assert ingress.edit_to_inbound(ev, gateway, *found) is None


def test_an_edited_group_message_is_routed_only_when_addressed(tmp_path) -> None:
    gateway = gateway_with_client(tmp_path)
    ev = inbound_event(chat="111@g.us", event_id="wamid-9")
    ev.Message.protocolMessage.type = ProtocolMessage.MESSAGE_EDIT
    ev.Message.protocolMessage.key.ID = "wamid-1"
    edited = ev.Message.protocolMessage.editedMessage
    edited.extendedTextMessage.text = "typo fixed"
    found = ingress.edit_target(ev)
    assert found is not None

    assert ingress.edit_to_inbound(ev, gateway, *found) is None

    edited.extendedTextMessage.contextInfo.mentionedJID.extend([f"{BOT}@s.whatsapp.net"])
    found = ingress.edit_target(ev)
    assert found is not None
    percept = ingress.edit_to_inbound(ev, gateway, *found)

    assert percept is not None
    assert percept.payload.text == "typo fixed"
    assert percept.thread == "111@g.us"


def test_a_location_becomes_one_readable_line() -> None:
    message = ProtoMessage()
    message.Message.locationMessage.degreesLatitude = -6.2
    message.Message.locationMessage.degreesLongitude = 106.8
    message.Message.locationMessage.name = "Office"

    assert message_text(message.Message) == "[location: -6.200000, 106.800000 — Office]"


def test_a_contact_card_becomes_one_readable_line() -> None:
    message = ProtoMessage()
    message.Message.contactMessage.displayName = "Budi Santoso"

    assert message_text(message.Message) == "[contact: Budi Santoso]"


def test_a_poll_becomes_one_readable_line() -> None:
    message = ProtoMessage()
    message.Message.pollCreationMessage.name = "Lunch"
    message.Message.pollCreationMessage.options.add().optionName = "Rice"
    message.Message.pollCreationMessage.options.add().optionName = "Noodles"

    assert message_text(message.Message) == "[poll: Lunch — Rice / Noodles]"


def test_prepare_media_downloads_every_attachment(tmp_path) -> None:
    gateway = gateway_with_client(tmp_path)
    first = Media(
        kind="image", mimetype="image/jpeg", filename="a.jpg", size=1, proto=ProtoMessage()
    )
    second = Media(
        kind="video", mimetype="video/mp4", filename="b.mp4", size=1, proto=ProtoMessage()
    )

    asyncio.run(gateway.prepare_media("chat", message_with(first, second)))

    assert len(gateway.client.downloads) == 2
    assert first.path is not None
    assert second.path is not None


def test_a_voice_note_is_transcribed_when_stt_is_configured(tmp_path) -> None:
    calls: list[Path] = []

    def transcribe(path):
        calls.append(Path(path))
        return "halo dari suara"

    speech = SimpleNamespace(
        stt=SimpleNamespace(provider="local"),
        stt_provider=lambda: SimpleNamespace(transcribe=transcribe),
    )
    gateway = gateway_with_client(tmp_path, speech=speech)
    note = gateway.config.workspace / "voice.ogg"
    note.write_bytes(b"OggS")
    media = Media(
        kind="voice",
        mimetype="audio/ogg",
        filename="voice.ogg",
        size=4,
        proto=ProtoMessage(),
        path=note,
    )
    message = message_with(media, text="dengarkan ini", message_id="wamid-2")

    asyncio.run(gateway.prepare_media("chat", message))

    assert calls == [note]
    assert "dengarkan ini" in message.text
    assert "halo dari suara" in message.text


def test_no_stt_provider_keeps_the_note_untouched(tmp_path) -> None:
    gateway = gateway_with_client(tmp_path)
    note = gateway.config.workspace / "voice.ogg"
    note.write_bytes(b"OggS")
    media = Media(
        kind="voice",
        mimetype="audio/ogg",
        filename="voice.ogg",
        size=4,
        proto=ProtoMessage(),
        path=note,
    )
    message = message_with(media, text="dengarkan ini", message_id="wamid-3")

    asyncio.run(gateway.prepare_media("chat", message))

    assert message.text == "dengarkan ini"


def test_the_window_rewrites_one_edited_row(tmp_path) -> None:
    gateway = gateway_with_client(tmp_path)
    gateway.windows.append("chat", message_with(text="before"))

    assert gateway.windows.replace_text("chat", "wamid-1", "after") is True
    found = gateway.windows.find("chat", "wamid-1")
    assert found is not None
    assert found.text == "after"
    assert gateway.windows.replace_text("chat", "missing", "x") is False
