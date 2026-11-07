# ADR-0010: Deep Native Gateway Embodiment and Runtime Layer Architecture — Discord, Telegram, Slack, WhatsApp Completeness, and Layered Extensions

- **Status:** accepted
- **Date:** 2026-11-07
- **Deciders:** Muqsith (owner)
- **Context:** Defines the full native capability contract for Discord, Telegram, Slack, and WhatsApp gateways—including interactive UI blocks (`Action.meta`), native `/` command menus, progressive streaming edits, threads, reactions, and media—and records the forward-compatible Runtime Layer Extension architecture (`layer` hooks) for future deep plugins without modifying core code.
- **Supersedes:** —
- **Related:** ADR-0001 (sovereign harness and appendage seam), ADR-0003 (turn progress and streaming), ADR-0004 (cross-gateway delivery), ADR-0005 (extensions and `BaseGateway`), ADR-0006 (RBAC and approvals)
- **Implementation:** implemented

---

## 1. Context

While the core engine (`monoagens/engine/`) and the foundational `BaseGateway` contract (ADR-0005 §4.2) are complete, two architectural gaps remain for rich multi-platform deployment and future extensibility:

1. **Underutilized Native Platform Features Across Gateways:**
   - Currently, only `monoagens/gateways/whatsapp/` is implemented in the tree, while **Discord**, **Telegram**, and **Slack** have no first-class packages under `monoagens/gateways/`.
   - Modern messaging platforms provide rich native primitives—interactive buttons (`Approve` / `Deny` without typing `/approve #id`), native `/` slash-command menus with autocomplete, message editing for live streaming (`stream_id` / `replace_id`), collapsible quote/spoiler blocks for `send_thinking` and `send_step`, native threads/topics, emoji reactions, polls, and voice notes.
   - Without a standardized **Interactive Block & Native Capability Contract** in `Action.meta` and `Percept.hints`, gateways degrade into plain-text pipes, forcing users to type raw CLI-like commands (`/approve #12`, `/skills`, `/status`) instead of clicking native buttons or selecting native `/` commands.
2. **Remaining Gaps in the Existing WhatsApp Gateway (`monoagens/gateways/whatsapp/`):**
   - `WhatsAppGateway.dispatch` (`gateway.py:366-388`) handles only `send_text`, `send_file`, `send_sticker`, and `typing`. It does not yet handle:
     - **Message Editing (`replace_id` / `stream_id`):** Progressive updates for `send_interim`, `send_thinking`, `send_step`, or editing a sent message via protobuf `protocolMessage` (`MESSAGE_EDIT`).
     - **Message Edit Ingress:** When a user edits an existing WhatsApp message, `ingress.py` does not yet normalize the edited stanza to update the window/history row.
     - **Emoji Reactions (Inbound & Outbound):** Sending or receiving native emoji reactions (`ReactionMessage` 👍/❤️/✅) on specific message IDs (currently only random sticker reactions exist).
     - **Voice Note (PTT) & Video/Audio Send:** `send_file` (`gateway.py:441-454`) only branches on `sticker`, `photo`, and `document`, sending `.ogg`/`.mp3` audio and `.mp4` video as generic documents rather than native playable voice notes (`send_audio` with `ptt=True`) or inline videos (`send_video`).
     - **Multi-Attachment `prepare_media`:** `gateway.py:507-525` still checks `message.media` (`attachments[0]`) instead of iterating `message.attachments`.
     - **Polls, Locations, & Contact Cards (vCard):** Extracting structured text from incoming locations, contacts, and poll votes/creations.
3. **Future Deep Plugin Compatibility Without Core Pollution:**
   - Standard plugins (`PluginLoader`, ADR-0005 §4.3) register additive `tools`, `commands`, and `prompts`.
   - Future enterprise or specialized deployments may need a plugin to sit in a **higher runtime layer above Core** (e.g., wrapping `HistoryStore` / `MemoryStore` for custom field-level encryption, overriding a storage or search adapter, or intercepting turn lifecycle hooks) strictly at runtime in memory while keeping `monoagens/engine/**` 100% untouched in Git. Because this is not needed yet and premature runtime patching is hard to test, its contract must be specified cleanly in this ADR for future compatibility without activating it by default.

## 2. Decision drivers

1. **100% Native Platform Ergonomics (Pure Python `asyncio`, Zero Node.js Runtime):** Each gateway (Discord via `discord.py`, Telegram via `aiogram` v3, Slack via async `slack-bolt`/`slack-sdk`, and WhatsApp via `neonize`) runs strictly in-process on Python `asyncio` without any Node.js, Chromium, or external JS runtime overhead, exploiting its platform's full native UX (buttons, menus, modals, threads, reactions, live message edits, native media types) while keeping `monoagens/engine/` zero-knowledge of transport SDKs (`AGENTS.md §2`).
2. **One Universal Interactive Schema (`Action.meta` & `Percept`) & Progress Commands (`/thinking`, `/steps`):** The engine emits transport-neutral structured blocks (approval cards, step cards, thinking blocks, choices) inside `Action.meta`; each gateway renders them natively (Buttons/Components on Discord, InlineKeyboards on Telegram, Block Kit on Slack, Reply/Reaction/List on WhatsApp), translates button clicks back into standard `Percept` events, and allows toggling `show_thinking` / `show_steps` / `interim_updates` via chat commands.
3. **Complete Command Auto-Registration:** All RBAC-allowed engine and plugin commands (`REGISTRY`) automatically sync to each platform's native `/` command menu (`BotCommandScope` in Telegram, Application Guild/Global Commands in Discord, Slash Commands in Slack).
4. **Pure Core & Layered Runtime Extensibility:** Core stays sacred; deep integration extensions operate as ordered, reversible runtime layers (`Layer 1..N`) over `Layer 0` (Core) only when explicitly enabled.

## 3. Options considered

**A. Plain-Text-Only Gateways (Lowest Common Denominator).** Rejected: wastes the native capabilities of Discord, Telegram, Slack, and WhatsApp, forcing users to type `/approve #id` manually.

**B. Put Platform-Specific UI Payload Builders Inside `monoagens/engine/`.** Rejected: violates `AGENTS.md §2` ("Strict Gateway Separation: core holds no transport vocabulary").

**C. Transport-Neutral `Action.meta` UI Block Contract + Full Native Gateways (`discord`, `telegram`, `slack`, `whatsapp`) + Reserved Runtime Layer Contract.** Accepted.

## 4. Decision

### 4.1 Pure Core & Gateway-Side Universal Interactive Options (`BaseGateway` $\leftrightarrow$ `Percept`)

`monoagens/engine/**` remains 100% pure and free of UI widget builders (`inbound.py`, `TurnState`, and `Delivery` never construct button styles or approval UI cards). Instead, interactive prompt rendering lives in `BaseGateway` (`monoagens/gateways/base.py`):

1. **Universal Interactive Options (`options` in `Action.meta` & Automatic `/approve #<id>` Detection in `BaseGateway`):**
   - Any tool, plugin, or command can attach generic prompt choices via `Action.meta["options"]` (e.g. `[{"id": "...", "label": "...", "command": "..."}]`).
   - For pending approvals and L4 memory consent, the engine emits its standard human-readable `/approve #<id>` text without any special UI plumbing in `monoagens/engine/**`; `BaseGateway` automatically detects `/approve #<id>` in the outbound `Action` and synthesizes the localized `Approve` (`/approve #<id>`) and `Deny` (`/deny #<id>`) interactive options at the gateway boundary.
   - **Gateway Rendering:** Discord renders `discord.ui.View` (`ui.Button` / `ui.Select`), Telegram renders `InlineKeyboardMarkup`, Slack renders Block Kit `actions` buttons, and WhatsApp maps reaction quick-confirm (👍 / 👎) or reply options.
   - **Inbound Click Normalization:** When a user clicks an option in the client app, the gateway verifies the clicker's identity, updates/disables the button state on the message, and hands `agent.handle` a standard `Percept(kind="message", payload=Message(text=command), hints={"interactive": True, ...})`. The engine processes it through the exact same RBAC and `ApprovalStore` path with zero special cases.
2. **Progressive Streaming, Thinking, & Tool Step Blocks (`send_thinking`, `send_step`, `send_interim`):**
   - `Action.stream_id` and `Action.replace_id` identify an in-flight turn bubble (`turn:<turn_id>:progress`).
   - When `show_thinking: true` or `show_steps: true` is enabled on a gateway:
     - Gateways that support fast message editing (Discord, Telegram, Slack, WhatsApp edit) maintain a single live status message per turn (`replace_id`), rendering `thinking` inside a native collapsible spoiler/blockquote (`> ||...||` on Discord, `<blockquote expandable>` on Telegram, context block on Slack) and updating tool steps (`🔧 read -> ✅ executed`) in place before replacing or following with the final answer.

### 4.2 WhatsApp Gateway Completeness (`monoagens/gateways/whatsapp/`)

To bring `monoagens/gateways/whatsapp/` to 100% native feature parity:
1. **Multi-Attachment Preparation:** Update `WhatsAppGateway.prepare_media` (`gateway.py:507-525`) to iterate over `for item in message.attachments:` (and `message.quote.attachments`) rather than only `message.media`.
2. **Native Audio/Voice-Note (`ptt`), Video, and Reaction Dispatch:**
   - Extend `WhatsAppGateway.dispatch` (`gateway.py:366-388`) and `send_file` (`gateway.py:420-459`) to support:
     - `send_voice` / audio files: call `client.send_audio(jid, target, ptt=True)` when sending voice replies (`TTS`) or `.ogg`/`.opus` voice notes, and `ptt=False` for standard audio files.
     - `send_video`: call `client.send_video(jid, target, caption=caption)` for `.mp4`/`.mov`/`.webm` files instead of sending them as generic documents.
     - `react`: call `client.send_reaction(jid, sender_jid, target_message_id, emoji)` for lightweight acknowledgments (e.g. 👀 when starting a long task, ✅ on completion) and allow the user to approve/deny pending confirmations by reacting 👍 / 👎 to the bot's prompt message!
3. **Message Edit (Inbound & Outbound):**
   - Outbound: When `action.replace_id` is set, edit the existing bot message via `client.edit_message` rather than sending a duplicate bubble.
   - Inbound: In `ingress.py`, detect `protocolMessage.type == ProtocolMessage.MESSAGE_EDIT` (`editedMessage`), update the message in `windows` and `history`, and route the edited turn if addressed.
4. **Rich Inbound Media & Stanza Extraction (`media.py`):**
   - Extract readable text summaries from `locationMessage` / `liveLocationMessage` (`[location: lat, lon — name]`), `contactMessage` / `contactsArrayMessage` (`[contact: name, vcard]`), and `pollCreationMessage` / `pollUpdateMessage`.
   - When `config.speech.stt.provider` is configured, automatically transcribe incoming WhatsApp voice notes (`audioMessage` with `ptt=True`) via `config.speech.stt_provider().transcribe(path)` and inject the transcript into `Message.text`.

### 4.3 Telegram Gateway (`monoagens/gateways/telegram/`) — Deep Native Integration

Registered via `@register_gateway("telegram", extra="telegram")` under `monoagens/gateways/telegram/`:
1. **Transport & Mode:** Supports both long-polling (`polling`, zero public IP required) and webhook mode (`webhook_url`, `webhook_secret_token`), powered by `aiogram` v3 (or async Bot API over `httpx`).
2. **Native `/` Command Menu Sync (`setMyCommands`):**
   - On startup and `/reload`, automatically registers the core/plugin command catalog (`commands.REGISTRY`) with Telegram's native menu (`BotCommandScopeDefault` for `member` commands, `BotCommandScopeChat` for `administrator` chats) so typing `/` in Telegram shows instant native autocomplete with localized descriptions from `monoagens/lang/<code>.yaml`.
3. **Forum Topics / Threads (`message_thread_id`):**
   - Maps Telegram Supergroup Forum Topics (`chat_id:topic_id`) to distinct `thread` addresses so each topic maintains isolated thread context and scratch workspace while sharing group membership policies.
4. **Inline Keyboards & Callback Queries (`callback_query`):**
   - Renders `Action.meta["ui"]` buttons as `InlineKeyboardMarkup`.
   - Handles `CallbackQuery` events: answers the callback toast (`answerCallbackQuery`), updates the inline keyboard to show `[ ✅ Approved by @user ]`, and dispatches the bound command (`Percept`) into `agent.handle`.
5. **Live Streaming Edits, Expandable Quotes, & Media Albums:**
   - Uses `editMessageText` (`replace_id`) with rate-limit debouncing for `send_interim` / `send_step`, renders `thinking` inside `<blockquote expandable>...</blockquote>`, groups multi-attachment inbound `media_group_id` albums into a single `Message.attachments` tuple, and supports native voice notes (`sendVoice` `.ogg` Opus), stickers, photos, documents, and reactions (`setMessageReaction`).

### 4.4 Discord Gateway (`monoagens/gateways/discord/`) — Deep Native Integration

Registered via `@register_gateway("discord", extra="discord")` under `monoagens/gateways/discord/`:
1. **Role Policy Vocabulary:**
   - Seeds `{"dm": bool, "channels": "all|mention|none", "threads": "all|mention|none", "reply_to_bot": bool}` in `roles.yaml`.
2. **Native Slash Commands (`app_commands`) & Autocomplete:**
   - Synchronizes MonoAgens commands (`/status`, `/memory`, `/skills`, `/task`, `/approve`, `/deny`, `/language`, `/away`, `/freeze`, `/alerts`) as native Discord Slash Commands with typed option autocomplete.
3. **Components V2: Buttons, Select Menus, Modals, & Embeds:**
   - Renders `Action.meta["ui"]` approval cards and tool step summaries using native Discord `Embed` + `ui.View` (`ui.Button`, `ui.Select`).
   - Button interactions (`InteractionType.component`) verify the clicking user's Discord snowflakes via `IdentityStore`, defer/update the interaction message cleanly, and route the `Percept` to `agent.handle`.
4. **Native Threads, Forum Channels, Multi-Attachment & Voice:**
   - Supports Discord Guild Text Channels, Public/Private Threads, and Forum Posts (mapping thread snowflakes to `Address("discord", str(channel_or_thread_id))`).
   - Collects all `message.attachments` into `Message.attachments` in one turn, splits messages $> 2000$ chars cleanly along code-fence boundaries, and supports Discord Voice Channel audio seams (`ADR-0005 §4.4`).

### 4.5 Slack Gateway (`monoagens/gateways/slack/`) — Deep Native Integration

Registered via `@register_gateway("slack", extra="slack")` under `monoagens/gateways/slack/`:
1. **Socket Mode & Events API:**
   - Defaults to **Slack Socket Mode** (`app_token_env: SLACK_APP_TOKEN`, `bot_token_env: SLACK_BOT_TOKEN`) so self-hosted deployments work behind NAT/firewalls without exposing a public webhook URL, while also supporting HTTP Events API with `signing_secret_env`.
2. **Block Kit Interactive UI & Modals:**
   - Renders `Action.meta["ui"]` as native Slack **Block Kit** (`header`, `section`, `context` for `send_step`/`send_thinking`, and `actions` block for `Approve` / `Deny` buttons).
   - Handles `block_actions` payloads over Socket Mode, updates the Block Kit message via `chat.update` to reflect the decision, and routes the `Percept` to `agent.handle`.
3. **Native Threading (`thread_ts`), App Home, & Slash Commands:**
   - Preserves Slack thread hierarchy (`channel_id:thread_ts`), supports Slack Assistants API status indicators (`assistant.threads.setStatus` for `send_thinking` / `send_step`), and maps Slack files (`files[]`) into `Message.attachments`.

### 4.6 Future-Compatible Runtime Layer Extension Architecture (Reserved Contract)

To support future deep-integration plugins (such as SaaS field encryption, custom telemetry/billing interceptors, or alternative storage/search backends) **without ever modifying `monoagens/engine/**` source files** and without introducing untestable complexity today:

1. **Core Remains Layer 0 (Sacred & Immutable on Disk):**
   - `monoagens/engine/**` is always Layer 0. No plugin may modify files inside `monoagens/engine/`.
2. **Reserved `layers` Contract in `PluginLoader` (Opt-In, Disabled by Default):**
   - A future system plugin (`tier: system` in `plugin.yaml`, permitted only when `engine.plugin.allow_layers: true`, default `false`) may return `"layers": [RuntimeLayer(name=..., order=100, target=..., wrap=...)]` from `register()`.
   - Layers mount in strict ascending `order: int` (`Layer 0 Core` $\rightarrow$ `Layer 100` $\rightarrow$ `Layer 200`) using explicit Python protocol wrappers around well-defined engine ports (`history`, `memory`, `llm`, `delivery`, `turn`) so multiple layers compose deterministically without monkey-patch collisions.
   - Calling `/reload` or disabling a plugin unmounts all runtime layers and rebuilds the stack cleanly from Layer 0.
   - *Status:* Reserved in architecture (`draft`); not activated until a concrete system plugin requires it.

## 5. Configuration

Configured in `config.yaml` and `roles.yaml` (all external gateways stay disabled/uninstalled by default until enabled via `monoagens gateway enable <name>`):
```yaml
gateways:
  whatsapp:
    enabled: true
    session_db: .monoagens/whatsapp.db
    backlog_seconds: 180
    show_thinking: false
    show_steps: false
    interim_updates: false
  telegram:
    enabled: false
    bot_token_env: TELEGRAM_BOT_TOKEN
    mode: polling
    webhook_url: ''
    webhook_secret_env: ''
    sync_commands: true
    show_thinking: false
    show_steps: true
    interim_updates: true
  discord:
    enabled: false
    bot_token_env: DISCORD_BOT_TOKEN
    sync_commands: true
    show_thinking: false
    show_steps: true
    interim_updates: true
  slack:
    enabled: false
    bot_token_env: SLACK_BOT_TOKEN
    app_token_env: SLACK_APP_TOKEN
    signing_secret_env: ''
    mode: socket
    show_thinking: false
    show_steps: true
    interim_updates: true
```

## 6. Metrics and gates

- `python -P scripts/adr.py --check` and `python -P scripts/selfcheck.py --check` pass with zero errors.
- Gateway contract suite (`tests/test_gateway_contract.py`) verifies `whatsapp`, `telegram`, `discord`, and `slack` against `BaseGateway`, central `access_refusal`, multi-attachment `Message.attachments`, `BaseGateway.ui_of` prompt rendering (explicit `meta["ui"]` plus the synthesized `/approve #<id>` pair), and interactive button callback $\rightarrow$ `Percept` routing.
- `monoagens/engine/**` imports zero symbols from `monoagens.gateways`, `neonize`, `aiogram`, `discord`, or `slack_bolt` (`AGENTS.md §2`).

## 7. Acceptance criteria

- [x] `BaseGateway.ui_of` resolves universal interactive options from `Action.meta["options"]`/`meta["ui"]` and synthesizes the localized `Approve`/`Deny` pair from the engine's own `/approve #<id>` badge, so `monoagens/engine/` carries no UI widget builder and no transport vocabulary.
- [x] `monoagens/gateways/whatsapp/` supports multi-attachment `prepare_media`, native voice notes (`ptt`), video send, emoji reactions, message edits (`replace_id` & inbound `MESSAGE_EDIT`), and location/contact/poll extraction.
- [x] `monoagens/gateways/telegram/` implements polling/webhook ingress, `setMyCommands` native `/` sync, Forum Topic routing, `InlineKeyboardMarkup` approval buttons, album grouping, and live edit streaming.
- [x] `monoagens/gateways/discord/` implements Gateway WebSocket events, Slash Command sync, `ui.View` interactive buttons/selects, thread/forum routing, and multi-attachment ingress.
- [x] `monoagens/gateways/slack/` implements Socket Mode, Block Kit interactive approval/step blocks, `thread_ts` routing, and multi-file ingress.
- [x] Runtime Layer Extension contract (`§4.6`) is documented as the sole sanctioned path for future deep plugins so `monoagens/engine/**` stays untouched.

## 8. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Unverified user clicks an `Approve` button in a group chat | Gateway callback handler sets `Percept.actor` to the clicking user's ID, and `ApprovalStore` / RBAC verifies the clicker's role and identity before executing |
| Rapid streaming updates (`send_step` / `send_thinking`) hit Telegram/Discord/Slack edit rate limits (HTTP 429) | Per-thread edit debouncer in `BaseGateway` coalesces intermediate updates to at most 1 edit per `1.5s` and respects `Retry-After` |
| Heavy third-party SDKs (`discord.py`, `aiogram`, `slack-bolt`) bloat base install | Installed strictly on demand via `monoagens gateway enable <name>` extras (`ADR-0005 §4.2`) |

## 9. Interaction with other ADRs

- **ADR-0001:** Preserves the hexagonal `Percept` / `Address` / `Action` / `Receipt` seam; core never imports gateway packages.
- **ADR-0003:** Connects `send_thinking`, `send_step`, `send_interim`, and `abort` (`stream_id` / `replace_id`) to native message editing and status indicators.
- **ADR-0004:** Enables seamless cross-gateway delivery (e.g., instructing the agent from Discord or Slack to send a file or voice note to a WhatsApp or Telegram contact).
- **ADR-0005:** Builds on `BaseGateway`, `@register_gateway`, `monoagens gateway enable|disable|pair|unpair|delete`, and the core `STT`/`TTS` speech seam.
- **ADR-0006:** Routes interactive button clicks through central RBAC and `ApprovalStore` verification.

## 10. Code references

- `monoagens/engine/inbound.py` — `Percept`, `Address`, `Action`, `Receipt`, `action_json`.
- `monoagens/gateways/base.py` — `BaseGateway` and `@register_gateway`.
- `monoagens/gateways/whatsapp/` — `gateway.py`, `ingress.py`, `media.py`, `directory.py`, `policy.py`.
- `monoagens/gateways/telegram/` — `config.py`, `policy.py`, `render.py`, `ingress.py`, `transport.py`, `webhook.py`, `gateway.py`.
- `monoagens/gateways/discord/` — `config.py`, `policy.py`, `render.py`, `ingress.py`, `transport.py`, `gateway.py`.
- `monoagens/gateways/slack/` — `config.py`, `policy.py`, `render.py`, `ingress.py`, `transport.py`, `gateway.py`.
- `monoagens/engine/plugins/loader.py` — `PluginLoader` and reserved runtime layer seam (§4.6).

---

*This ADR lives at `docs/v0.1.0/adr/0010-deep-native-gateway-embodiment-and-runtime-layers.md`.*
