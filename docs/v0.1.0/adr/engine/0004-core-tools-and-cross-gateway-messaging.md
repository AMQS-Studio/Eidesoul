# ADR-0004: Core Tools and Cross-Gateway Messaging — Universal Delivery, Origin Verification, and Tool Contracts

- **Status:** accepted
- **Date:** 2026-11-05
- **Deciders:** Muqsith (owner)
- **Context:** Re-baselined for v0.1.0; consolidates tool manifests, gateway contracts, input/output contracts, turn cost, and cross-gateway messaging (pre-rebaseline 0015, 0018, 0021, 0024, 0025, 0032, 0049, 0050, 0053).
- **Supersedes:** pre-rebaseline 0015, 0018, 0021, 0024, 0025, 0032, 0050
- **Related:** ADR-0001 (sovereign core), ADR-0003 (turn lifecycle), ADR-0006 (access control)
- **Implementation:** implemented — core `send_message`/`send_file`/`thread_history`, explicit origin on the one door, origin-tracked approvals with a unique action id, cross-room leak guard, and the multi-attachment envelope.

---

## 1. Context

A core capability of MonoAgens is multi-channel presence for one owner: interacting with one continuous
soul across CLI, WhatsApp, Discord, Telegram, or Web, and commanding it across channels (e.g., *"From
Discord/CLI, send this file to my colleague's WhatsApp number"*).

End-to-end verification revealed that cross-gateway messaging failed across five architectural layers,
alongside defects in approval routing, relay grants, and multi-turn tool output accumulation:
1. **Layer 1 — Tool Catalog Isolation:** `ToolIndex.specs(gateway)` only included body tools belonging
   to the origin gateway where the turn arrived. A turn arriving on CLI or Discord never received
   `send`, `send_to`, or `chat_history` from `monoagens/gateways/whatsapp/tools/`.
2. **Layer 2 — Broken `<body>__<tool>` Resolution:** `LLM.run` mapped `by_name` using `spec.name` only,
   rejecting `whatsapp__send_to` as an unknown tool.
3. **Layer 3 — Single-Gateway `ToolContext` & Attachment Lockout:** `ToolContext.gateway` held only the
   source gateway instance, so `send_to` checked `directory_refusal(ctx.gateway)` and dispatched back
   to the source gateway. Furthermore, inbound attachments saved under `workspace/attachments/` were
   blocked for `member` roles by `resolve_for` (which only allowed `public/` and `scratch/`), and
   `send_to._cross_refusal` deadlocked on new phone numbers not yet in the `threads` archive (requiring
   `chat_history` first, which itself refused unarchived targets).
4. **Layer 4 — Exit Door Origin Bypass & Strict Mode Approval Deadlock:**
   - `Delivery._door` set `origin = origin or action.address`, making `same = (address == origin)` in
     `Agent.dispatch` always evaluate to `True` for tool sends and bypassing cross-room `AUTHORIZATION`,
     `refusal()`, mandatory `reason`, and `guard.cross_chat_leak` on tool payloads (`ST-09`).
   - In `strict` approval mode (`ST-13`), `Agent.dispatch` omitted `declared_class=action.approval_class`
     on `ActionRequest` (defaulting to `"authorization"`), parking every normal in-room reply as
     `pending approval`.
   - When a cross-room send was parked in `approvals`, `Agent.dispatch` stored the *destination*
     `address.thread` and `address.gateway` instead of the *origin* room, preventing the requester from
     running `/approve <id>` in the origin room, while `approvals.find_action` scanned only the latest
     50 rows in Python.
   - `RelayStore.ask()` could only be invoked from `automation_worker.py`, never from a conversation
     turn, and `send_to._relay` required `len(members) == 1`, failing on both WhatsApp DMs (2 members)
     and groups (`None`).
5. **Layer 5 — Partial Send Replay, Hardcoded Web Search, & Multi-Turn Context Overflow (`ID-3, M-06`):**
   - Sending both `text` and `path` in one `send_to` call returned `"error: ..."` if the file upload
     failed after the text succeeded, marking the entire step `failed` in `turn_steps` and causing the
     text to be sent a second time on retry/resume.
   - `tools/web_search.py` hardcoded `DDGS_BACKENDS = "yahoo"`, used synchronous `urllib.request.urlopen`
     in threads instead of `httpx.AsyncClient`, and only supported `searxng` or `ddgs` rather than
     allowing operators to select modern search providers (`searxng`, `tavily`, `brave`, `exa`, `serper`,
     `google`, `ddgs`) with configurable fallbacks.
   - In `LLM.run`, large tool outputs accumulated across multiple tool rounds without a per-round cap,
     overflowing provider context windows on round 2+.

## 2. Decision drivers

1. **Appendage-Agnostic Core Messaging:** Sending messages, delivering files, and reading permitted
   thread history are core harness capabilities, not gateway-specific plugins.
2. **Strict Origin Accountability:** Every outbound delivery requires an explicit `origin: Address`;
   cross-room and cross-gateway sends always undergo `AUTHORIZATION` and payload leak checks.
3. **Origin-Room Approval UX:** Approvals parked from a cross-gateway command can be approved from the
   origin room and report completion back to the origin room.
4. **Controlled Attachment Forwarding:** First-class attachment handles (`att_…`) allow users to forward
   files they own without opening read access to `workspace/attachments/`.
5. **Bounded Tool Loops:** Step separation, per-tool output truncation, and per-round accumulation caps
   prevent duplicate partial sends and context window overflows.

## 3. Options considered

**A. Expose Gateway-Owned `send_to` Globally Across All Gateways.** Rejected: leaks gateway-specific
schemas into the core prompt and leaves `ToolContext` and `origin` broken.

**B. Separate Cross-Gateway Relay Service.** Rejected: introduces a second delivery path bypassing the
transactional outbox.

**C. Core Messaging Tools with Explicit Origin, `AttachmentStore`, and Origin-Tracked Approvals.** Accepted.

## 4. Decision

### 4.1 Core Messaging Tools and Step Separation

- Three core tools are established under `monoagens/engine/tools/` (replacing `send`, `send_to`, and
  `chat_history` in `monoagens/gateways/whatsapp/tools/`):
  - `send_message(gateway, target, text, reason=None, relay_task=None, relay_from=None)`
  - `send_file(gateway, target, path=None, attachment_id=None, caption=None, reason=None)`
  - `thread_history(gateway, chat, limit=20)`
- Separating `send_message` and `send_file` (or recording two distinct `turn_steps` when sending text
  plus file) ensures a failed file transfer never marks an already-delivered text message as `failed` (`ID-3`).
- `ToolContext` carries the active gateway registry (`agent.body(name)`) and the explicit `origin: Address`
  of the active turn.
- Sending to a direct target that has no prior row in `threads` is permitted when `reason` is supplied
  and evaluated under the `AUTHORIZATION` class; the read-first (`thread_history`) requirement applies
  only to existing archived rooms.

### 4.2 Explicit Origin Enforcement, Leak Screening, and Approval Routing

- `Delivery._door` requires an explicit `origin: Address`; the `origin or action.address` fallback is
  deleted.
- `Agent.dispatch` passes `declared_class=action.approval_class` to `ActionRequest` so normal replies to
  the origin room (`same=True -> CONSENT`) are allowed immediately even in `strict` mode (`ST-13`).
- When `origin != address` (`same=False`), `Agent.dispatch` classifies the action as `AUTHORIZATION`,
  enforces `refusal()` and mandatory `reason`, and runs `guard.cross_chat_leak(payload, ctx.cross_reads)`
  on the outbound tool payload before dispatch (`ST-09`).
- When an action is parked in `approvals`, the row stores `origin_gateway` and `origin_thread` alongside
  the target address and an indexed unique `action_id` (replacing the 50-row Python scan in
  `approvals.find_action`). `/approve <id>` can be executed from the origin room and reports completion
  back to the origin room.

### 4.3 Gateway Address Normalization and Conversational Relay Grants

- `GatewayCore` requires `normalize_address(raw: str) -> str`. All inbound percepts, `monoagens people add`,
  `/link`, `IdentityStore.resolve`, and outbound tool targets normalize addresses through the target
  gateway adapter so core code never parses JIDs or snowflakes.
- A role-gated `relay` tool/command allows creating and managing `RelayStore` grants during a
  conversation turn, and relay target verification delegates participant checks to the target gateway's
  membership rule rather than assuming `len(members) == 1`.

### 4.4 Canonical Multi-Attachment Pipeline (`AttachmentStore` & Multimodal Envelopes)

- **Multi-Attachment per Message (`Message.attachments` & `message_media`):**
  - `Message` in `turn/context.py` supports `attachments: tuple[Media, ...] = ()` (retaining `.media` as a
    backward-compatible property returning `attachments[0] if attachments else None`) and an open
    `meta: Mapping[str, object] = {}` dictionary.
  - `message_media` in `schema.py` includes an ordinal `seq` column in its primary key
    (`(thread_id, message_id, seq)`) so a single message from Discord, Telegram, Email, Web, voice notes,
    or robot sensors can carry multiple images, audio clips, documents, or telemetry blobs without
    `UniqueViolation` errors or future schema breaking changes.
- Inbound media/files from any gateway are stored under `workspace/attachments/<att_id>` and registered
  in `AttachmentStore` with their owner `person_id` and origin `thread_id`.
- Context assembly renders `[attachment id=att_xxx name=file.pdf]` for each attachment in the message entry.
- `send_file(attachment_id="att_xxx")` verifies that the caller is the attachment owner (or holds an
  authorized role) and dispatches the file without granting general read traversal over `workspace/attachments/`.

### 4.5 Pluggable Multi-Provider Web Search and Tool Context Budgets

- **Configurable Multi-Provider `web_search`:**
  - `web_search` in `config.yaml` supports selectable search providers (`provider: "ddgs" | "searxng" | "tavily" | "brave" | "exa" | "serper" | "google"`), an ordered `fallbacks: []` list, `base_url`, `api_key_env`, `engine_id_env` (for Google CSE), and `ddgs_backend: "auto"` (removing the hardcoded `DDGS_BACKENDS = "yahoo"`).
  - HTTP-based search adapters execute via shared non-blocking `httpx.AsyncClient` (replacing synchronous `urllib.request.urlopen`) and normalize results into a uniform `SearchHit(title, url, snippet, published_at)` contract before feeding `ResearchCard` and `fetch` enrichment.
- **Tool Contracts and Multi-Turn Context Budget:**
  - Tool inputs are validated against JSON schemas; unknown or malformed arguments return structured tool
    errors without crashing the turn.
  - Tool outputs are truncated per call (`max_output_chars`, default 3,000) and total tool output appended
    to `messages` in `LLM.run` is capped per round (`max_round_chars`, `M-06`) so multi-turn tool loops
    cannot overflow the provider's context window.

## 5. Configuration

Configured in `config.yaml` and `roles.yaml` (note: default `member` role does not receive cross-gateway
send tools unless granted by the operator):
```yaml
web_search:
  provider: ddgs
  fallbacks: []
  base_url: ""
  api_key_env: ""
  engine_id_env: ""
  ddgs_backend: auto
  region: ""
  timeout_seconds: 10
engine:
  tools:
    max_calls_per_turn: 10
    max_output_chars: 3000
    max_round_chars: 12000
    timeout_seconds: 30
```

## 6. Metrics and gates

- End-to-end cross-gateway test (`ST-01`): A turn on `CLIGateway` calls `send_file(gateway="whatsapp", ...)`
  and delivers the file through a stub `WhatsAppGateway` with `AUTHORIZATION` checks executed.
- Exfiltration test (`ST-09`): A prompt-injected cross-room send attempt in `yolo` mode is caught by
  `AUTHORIZATION` and `guard.cross_chat_leak`.
- Strict-mode test (`ST-13`): Normal DM replies in `approval.mode: strict` succeed without parking as
  `pending`, while cross-room sends park with `origin_thread` and can be approved from the origin room.

## 7. Acceptance criteria

- [ ] `send_message`, `send_file`, and `thread_history` live in `monoagens/engine/tools/` and work across any enabled gateway.
- [ ] Zero `send`, `send_to`, or `chat_history` modules remain under `monoagens/gateways/whatsapp/tools/`.
- [ ] `Delivery._door` has no `origin or action.address` fallback, and `Agent.dispatch` passes `declared_class`.
- [ ] `approvals` has a unique SQL index on `action_id` and records `origin_gateway` and `origin_thread`.
- [ ] `Message.attachments` and `message_media` (`(thread_id, message_id, seq)`) support multiple attachments per message across all modalities (images, voice notes, documents, sensor data).
- [ ] Members can forward their own inbound attachments via `attachment_id="att_…"` without directory read access.
- [ ] `web_search` supports selectable providers (`ddgs`, `searxng`, `tavily`, `brave`, `exa`, `serper`, `google`) and `fallbacks` via `httpx.AsyncClient` without hardcoding `DDGS_BACKENDS = "yahoo"`.
- [ ] Multi-turn tool outputs in `LLM.run` are capped per round to prevent HTTP 400 context window errors.

## 8. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Model targets a disabled or non-existent gateway | Tool returns a clean `error: gateway '<name>' is not enabled` without side effects |
| Cross-room data exfiltration via prompt injection | Mandatory `AUTHORIZATION` class + `guard.cross_chat_leak` on outbound tool payloads |
| Unbounded attachment disk usage | Workspace retention sweep purges expired attachments outside active references |

## 9. Interaction with other ADRs

- **ADR-0001:** Enforces hexagonal separation (`normalize_address` in gateways, orchestration in core).
- **ADR-0003:** Assigns deterministic `Action.id` (`turn:{turn_id}:tool:{step_seq}`) and pre-execution step logging.
- **ADR-0006:** Enforces RBAC tool permissions and approval policies.

## 10. Code references

- `monoagens/engine/tools/__init__.py`, `messaging.py` — `ToolIndex`, `ToolContext`, and core messaging tools.
- `monoagens/engine/turn/delivery.py` — `_door`, `send_text`, `send_file`, `send_reply`.
- `monoagens/engine/agent.py` — `Agent.dispatch`, `ActionRequest` `declared_class`, and approval creation.
- `monoagens/engine/access/approvals.py`, `relay.py`, `monoagens/engine/commands/approve.py`
- `monoagens/engine/inbound.py` — `GatewayCore.normalize_address`.
- `monoagens/engine/llm.py` — multi-turn tool output round cap.

---

*This ADR lives at `docs/v0.1.0/adr/engine/0004-core-tools-and-cross-gateway-messaging.md`.*
