# ADR-0004: Core Tools and Cross-Gateway Messaging — Universal Delivery, Origin Verification, and Tool Contracts

- **Status:** accepted
- **Date:** 2026-11-05
- **Deciders:** Muqsith (owner)
- **Context:** Re-baselined for v0.1.0; consolidates tool manifests, gateway contracts, input/output contracts, and turn cost (pre-rebaseline 0015, 0018, 0021, 0024, 0025, 0032) with the cross-gateway architecture in pre-rebaseline 0050.
- **Supersedes:** pre-rebaseline 0015, 0018, 0021, 0024, 0025, 0032, 0050
- **Related:** ADR-0001 (sovereign core), ADR-0003 (turn lifecycle), ADR-0006 (access control)
- **Implementation:** partial — base tool index exists; core messaging tools and explicit origin enforcement are scheduled in Phase P0/P1.

---

## 1. Context

One of MonoAgens' defining value propositions is multi-channel presence: an operator interacts with
one continuous soul across Discord, WhatsApp, Telegram, Web, and CLI. However, the architectural audit
(`docs/audit.md` §2.2) discovered that cross-gateway messaging ("From Discord/CLI, send a file to
WhatsApp") failed catastrophically across five architectural layers:
1. **Tool Catalog Isolation:** `ToolIndex.specs` only loaded tools belonging to the origin gateway,
   hiding WhatsApp send tools from CLI/Discord turns.
2. **Name Resolution Failure:** Syntaxes like `whatsapp__send_to` failed tool resolution.
3. **Gateway Context Lock:** `ToolContext.gateway` was locked to the origin gateway; `send_to` resolved
   targets against the origin gateway's directory and dispatched files back to the origin gateway.
4. **Origin Bypass at Delivery Door:** `Delivery._door` defaulted `origin = origin or action.address`.
   Because origin matched destination address, `Agent.dispatch` considered `same = True`, bypassing all
   cross-room `authorization` checks and `/approve` flows.
5. **Attachment Inaccessibility:** Inbound attachments were saved in gateway-specific directories that
   non-admin roles could not access, preventing members from forwarding files they received.

## 2. Decision drivers

1. **appendage-Agnostic Core Messaging:** Messaging and file transfer are core engine orchestration
   capabilities, not gateway-owned plugins.
2. **Strict Origin Accountability:** Cross-room and cross-gateway deliveries must require explicit
   `origin: Address` to enforce authorization policies.
3. **Address Normalization at the Edge:** Gateways own transport address parsing and canonicalization.
4. **Controlled Attachment Sharing:** Secure attachment handles (`att_...`) allow users to reference
   and forward files without broad filesystem permissions.
5. **Robust Tool Contracts:** Input schemas, output size truncation, and call ceilings prevent token
   window overflow.

## 3. Options considered

**A. Gateway-Owned Tools with Global Visibility.** Rejected: leaks gateway-specific parameters into
the general model prompt and leaves origin tracking broken.

**B. Separate Dedicated Relay Microservice.** Rejected: adds unnecessary deployment boundaries
contrary to the sovereign single-process architecture.

**C. Core Cross-Gateway Tools with Explicit Origin.** Selected: implements `send_message`, `send_file`,
and `thread_history` as core tools, enforces explicit `origin: Address`, and establishes a canonical
`AttachmentStore`.

## 4. Decision

### 4.1 Core Messaging Tools

The following tools are promoted to core tools under `monoagens/engine/tools/messaging.py`:
- `send_message(gateway, target, text)`: Sends text to any configured gateway and thread.
- `send_file(gateway, target, attachment_id=None, path=None, caption=None)`: Sends files using workspace
  paths or canonical attachment handles.
- `thread_history(gateway, chat, limit=20)`: Reads conversation context from permitted archives.
- Gateway-specific send tools in `monoagens/gateways/whatsapp/tools/` are removed. Gateways only provide
  body-specific physical actions (e.g. reaction emojis, presence).

### 4.2 Explicit Origin Enforcement

- `Delivery._door` requires an explicit `origin: Address` parameter; fallback to `action.address` is deleted.
- When `origin.gateway != address.gateway` or `origin.thread != address.thread`, `Agent.dispatch` classifies
  the action as `AUTHORIZATION`, enforcing permissions, `/approve` gates, and leak verification.

### 4.3 Gateway Address Normalization Contract

`GatewayCore` protocol requires `normalize_address(raw: str) -> str`:
- Gateways canonicalize raw strings (e.g., `+62812...` or `62812...@s.whatsapp.net` -> `62812...`).
- Used universally by CLI `people add`, `/link`, identity resolution, and outbound routing. Core engine
  never parses transport-specific JIDs or snowflakes.

### 4.4 Canonical Attachment Pipeline

- Inbound files are saved to `workspace/attachments/<att_id>` and registered in `AttachmentStore`.
- Messages present `[attachment id=att_xxx name=file.pdf]` in context.
- Members can forward their own attachments via `send_file(attachment_id="att_xxx")` without needing
  direct directory traversal access to `workspace/attachments/`.

### 4.5 Tool Execution and Safety Limits

- Tool inputs are validated against JSON schemas generated from function type signatures.
- Output text is truncated per tool (`max_chars`, default 3,000) to protect context windows.
- Turn call ceiling: max 10 tool calls per turn prevents infinite model loops.

## 5. Configuration

Configured in `config.yaml`:
```yaml
engine:
  tools:
    max_calls_per_turn: 10
    max_output_chars: 3000
    timeout_seconds: 30
```

## 6. Metrics and gates

- Cross-gateway test: CLI command sends a file through WhatsApp mock adapter end to end.
- Security test: Cross-gateway send from non-approver role is parked in `pending approval` status.
- Attachment test: Member role successfully forwards an inbound file via `attachment_id`.

## 7. Acceptance criteria

- [ ] `send_message` and `send_file` can target any enabled gateway from any active turn.
- [ ] `Delivery._door` rejects any call lacking an explicit `origin: Address`.
- [ ] No `send_to` module remains in `monoagens/gateways/whatsapp/tools/`.
- [ ] Tool output exceeding configured character caps is cleanly truncated with a notice.

## 8. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Cross-room data exfiltration via prompt injection | `guard.cross_chat_leak` verifies outbound tool payload before cross-send |
| Attachment storage unbounded growth | Scheduled workspace retention sweeps purge attachments older than 30 days |

## 9. Interaction with other ADRs

- **ADR-0003:** Assigns deterministic action IDs to all tool delivery invocations.
- **ADR-0006:** Evaluates role permissions for core messaging tools before execution.

## 10. Code references

- `monoagens/engine/tools/` — Core tool implementations and `ToolIndex`.
- `monoagens/engine/turn/delivery.py` — Delivery door and explicit origin validation.
- `monoagens/engine/agent.py` — `dispatch()` authorization and outbox routing.
- `monoagens/engine/inbound.py` — `GatewayCore` and `normalize_address` protocol.

---

*This ADR lives at `docs/v0.1.0/adr/engine/0004-core-tools-and-cross-gateway-messaging.md`.*
