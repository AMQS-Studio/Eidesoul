# ADR-0050: Cross-Gateway Messaging — Send and File Delivery as Core Tools

- **Status:** accepted
- **Date:** 2026-11-05
- **Deciders:** Muqsith (owner)
- **Context:** the owner holds several channels (WhatsApp, Discord, Telegram, web, CLI) in one deployment; "from Discord, send this file to my colleague on WhatsApp" must work safely across all five routing layers.
- **Supersedes:** —
- **Related:** ADR-0007 (authorization), ADR-0013 (turn lifecycle), ADR-0018 (gateway contract), ADR-0032 (address and command), ADR-0046 (gateway access), ADR-0048 (single-tenant), ADR-0049 (audit remediation)
- **Implementation:** draft — no layer is changed yet.
- **Location note:** the seam binds the engine tool index and the delivery door, so this ADR sits under `adr/engine/`.

---

## 1. Context

`send`, `send_to`, and `chat_history` live in `monoagens/gateways/whatsapp/tools/`. The failure chain,
confirmed against the code:

1. **Catalog.** `ToolIndex.specs` adds gateway tools only for the gateway the turn came from, so a CLI
   or Discord turn never sees `send_to`.
2. **Name syntax.** `LLM.run` resolves only `spec.name`; `whatsapp__send_to` is an unknown tool.
3. **Context.** `ToolContext.gateway` is the source gateway; `send_to` checks the directory of that
   gateway and sends through it.
4. **Authorization.** `Delivery._door` sets `origin=origin or action.address`, so `same` in
   `Agent.dispatch` is always true and the cross-room `authorization` check never runs.
5. **Idempotency.** Tool-built `Action` objects get a fresh `uuid4()`; a retry is never recognised.

Sending to an `Address(gateway, thread)` is a harness capability. A gateway only executes
`dispatch(Action)`.

## 2. Decision drivers

1. One owner, many channels is a core use case.
2. The core holds no gateway vocabulary (ADR-0033); address formats stay in the gateway.
3. The target decides the approval class (`consent` or `authorization`), not a name table.
4. A resumed turn never sends twice.
5. A member forwards only attachments they own, without free read access to `attachments/`.

## 3. Options considered

**A. Keep gateway-owned tools, expose them to every gateway.** Rejected: gateway-specific arguments leak
into the model contract, and the origin problem remains.

**B. Core tools with an explicit gateway argument.** Accepted.

**C. A dedicated cross-gateway service.** Rejected: a second delivery path next to the outbox.

## 4. Decision

### 4.1 Core tools

New modules in `monoagens/engine/tools/`: `send_message`, `send_file`, and `thread_history`, each with
`TOOL = Handler(...)`. Arguments: `gateway` (optional, defaults to the active gateway), `target`, and
the payload. `send_file` takes `path` (workspace) or `attachment_id`. The three gateway-owned
modules are removed from `monoagens/gateways/whatsapp/tools/`; WhatsApp keeps only body-specific actions.

### 4.2 Address ownership

`GatewayCore` gains `normalize_address(raw) -> str`. `monoagens people add`, `/link`,
`IdentityStore.resolve`, and every outbound target go through it, so core code never parses a JID or a
snowflake.

### 4.3 Execution context

`ToolContext` carries the active gateway map (`agent.body(name)`) and the origin `Address`. The delivery
door requires `origin`; there is no fallback. The cross-room check in `Agent.dispatch` therefore runs for
every tool send, and `guard.cross_chat_leak` also runs on tool payloads before a cross-room send.

### 4.4 Attachments

Intake stores an inbound file under `workspace/attachments/<att_id>` and prints
`[attachment id=att_xxx name=…]` in the context. `send_file(attachment_id=…)` works for the attachment's
owner without opening the directory.

### 4.5 Idempotency

`Action.id = f"turn:{turn_id}:tool:{step_seq}"` (ADR-0049 §4.1.4). The intent row is written before
`body.dispatch`; a repeat with the same id is a no-op.

## 5. Configuration

No new key. Role `tools` entries grant `send_message`, `send_file`, and `thread_history`; the default
member role does not receive them (AGENTS.md).

## 6. Metrics and gates

- A test with two real gateways (CLI to a stub WhatsApp) sends text and a file end to end.
- A test crashes after `dispatch` and resumes; the transport sees one send.
- A cross-room send in `yolo` mode still hits the `authorization` class.

## 7. Acceptance criteria

- [ ] From a CLI turn, `send_file(gateway="whatsapp", …)` delivers the file.
- [ ] `Delivery._door` has no `origin or action.address` fallback.
- [ ] Core code contains no gateway-specific address parsing.
- [ ] `grep` finds no `send_to` module under `monoagens/gateways/whatsapp/tools/`.

## 8. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| A tool names a gateway that is disabled | The tool refuses with a short `error:` and sends nothing |
| Exfiltration through `send_message` after prompt injection | `cross_chat_leak` on payloads plus the `authorization` class |
| Attachment store grows | Retention follows the existing workspace sweep; the ID carries the owner |

## 9. Interaction with other ADRs

ADR-0007 keeps the classes; the origin fix makes them effective. ADR-0018 keeps the gateway contract and
adds `normalize_address`. ADR-0046 stays the source of gateway authorization.

## 10. Code references

- `monoagens/engine/tools/__init__.py` — `ToolIndex.specs`, `execute`.
- `monoagens/engine/turn/delivery.py` — `_door`, `file`, `text`.
- `monoagens/engine/agent.py` — `dispatch`.
- `monoagens/engine/inbound.py` — `Action`, `GatewayCore`.
- `monoagens/gateways/whatsapp/tools/` — the modules to remove.

---

*This ADR lives at `docs/v0.1.0/adr/engine/0050-cross-gateway-messaging.md`.*
