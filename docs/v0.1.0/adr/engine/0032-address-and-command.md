# ADR-0032: One Address, One Command — Embodiment-Neutral Output, Refusal over Guessing

- **Status:** accepted
- **Date:** 2026-11-03
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Context:** follows ADR-0000 (the `Percept`/`Action` seam), ADR-0018 (the gateway contract), ADR-0014 (durable routing), ADR-0007 (approval classes), ADR-0031 (this decision lands after the engine split)
- **Supersedes:** —
- **Related:** ADR-0023 (relay consent), ADR-0025 (stable prompt prefix), ADR-0027 (a gateway is a body), ADR-0028 (plugin unit)
- **Implementation:** draft — every call site below still sends through its own body, `send_text` is still mandatory, and no tool index exists per body. The evidence line is replaced when the door lands.
- **Location note:** an engine decision (it defines how the engine addresses any body and what it may ask of one), so it sits under `adr/engine/`. The Indonesian copy is `docs/v0.1.0/adr/engine/id/0032-address-and-command.md`.

---

## 1. Context

The inbound seam is already neutral: a body hands in a `Percept` and the engine emits `Action`s (ADR-0000). The outbound side never finished that thought.

- `GatewayCore` requires `send_text`, so every body must be able to send chat text before it can be a body at all. `render`, chunks, typing, and stickers are chat policy sitting in the same contract.
- The engine builds the chunks and calls the body directly: two direct calls in `agent.py`, and five originators (turn, `automation`, `privacy`, `relay`, `/kirim`). Three thread-to-body resolvers exist (`_gateway_for`, `_thread_gateway`, the inline copy in `ask_memory`), with different fallbacks; `_gateway_for` falls back to the first registered body.
- `Action` already exists and `Agent.handle` already returns it, but only as a record written after the transport call. It is not the path.
- The tool registry is flat: gateway tools are collected into one list at boot, and `Registry` refuses a duplicate name. A second body with `send` or `chat_history` is a boot failure today.
- `scope_proof` carries targets, roots, and class, but not the body. An approval granted for one body could be replayed against another.
- `threads.gateway` makes the route durable (ADR-0014), and `Directory` is body-local. Reads resolve through `ctx.gateway`, so a Discord turn cannot read a WhatsApp room even when the requester's membership would allow it (ADR-0007 §4.5).
- Identity is already synchronized: `person_links` maps a WhatsApp address and a Discord address to one `per_…` id, so "who you are" needs no new registry.

## 2. Decision drivers

1. **Neutral output completes ADR-0000.** The seam is only neutral if the engine never assumes text.
2. **One policy for every caller.** Turn, command, automation, and robot must be judged by the same function from the same inputs: subject, address, origin.
3. **A receipt, never a silent signal.** The send ledger already distinguishes intent, sent, and unknown; a fire-and-forget message would delete that guarantee.
4. **No new state.** Routes, presence, and identity are derived facts that already exist. An index is rebuilt at boot, never stored.
5. **Fail closed.** Unknown body, unknown capability, ambiguous name, unproven membership: one refusal, nothing executed.
6. **The prompt stays stable** (ADR-0025). Body capability must not make the stable prefix change per turn.

## 3. Options considered

**A. Status quo: per-body methods and a flat registry.** Works for one body; a second body collides on names, and the engine keeps chat semantics.

**B. Per-capability methods (`perform`, a `Chat` protocol).** Cleaner types, but the engine still enumerates verbs, and every body must implement a method per chat capability even when it is not a chat.

**C. Command Message plus a dispatcher plus a reply (chosen).** `Action` is the message; the body routes it to its own handler by verb and answers with a receipt. The engine knows no verb.
**D. Event or signal only.** Simplest wire, but no reply: the ledger cannot say sent or unknown, and the turn cannot report a failure. Rejected.
**E. A broker or IPC between engine and bodies.** Adds a server and a second failure domain for a problem that in-process adapters already solve. Rejected while every body shares the process (ADR-0027).

## 4. Decision

### 4.1 `Address` is the only target

`Address` is a pair: the body name and the thread id, both strings, plus the evidence that established membership (`gateway`, `recorded`, or empty). It is built from `threads.gateway`, never parsed from the thread. An unresolved route is a refusal, not a fallback to the first body.

### 4.2 The outbound message is a Command Message

`Action` is the message (ADR-0000 owns the name). A `Command Message` is a regular message that happens to carry a command (EIP); it is not a new type. The body exposes one member:

`dispatch(action) -> str`

- The body keeps a **Message Dispatcher**: a table from `action.capability` to its own handler (`send_text`, `send_file`, `send_sticker`, and, for a robot, `speak` or `move`).
- An unknown verb is refused by the body with one reason; the engine never enumerates verbs.
- `GatewayCore` keeps only generic members: `name`, `tools`, `commands`, `run`, `stop`, `dispatch`, `prompt_hints`, `self_handles`, `chat_name`. `send_text` and `render` move to an optional `Chat` protocol; a chat body opts in, a robot never does.
- Chat-only work (chunking, rendering, typing, media, read receipts) stays in the chat path and in `reply.py`; the door does not know it exists.

### 4.3 The reply is a receipt

Request–Reply (EIP) needs a return address and a correlation identifier. The engine supplies both:

- The `Action` carries an `id` that is the correlation id; the ledger key derives from it.
- The door writes `intent` in `outbound_messages` before dispatch, then records `sent` with the provider reference or `unknown` on timeout.
- The body is an **Idempotent Receiver** keyed by that id; a repeated dispatch of the same id is a no-op.
- Retry is never implicit. It happens only through the durable publisher (`automation_runs`), with the same id.
- A long-running robot action follows the ROS 2 action shape (goal, feedback, result, cancel) inside the same `Action`; it does not need a bigger protocol.

### 4.4 One door for every caller

`dispatch(subject, address, action, *, origin, reason)` is the only engine function that reaches a body's `dispatch`.

- `subject` is the accountable person id: the sender for a turn or a command, `automation.owner` for an automation, the mapped actor for a robot.
- `origin` is the `Address` where the request was born; it is what makes a cross-body act distinguishable from a local one.
- Policy runs before the wire: same thread is `consent`; another thread or another body is `authorization`, with `scope_proof` extended by the target body and the origin, re-checked at execution (ADR-0007). `/approve` is unchanged.
- Attribution for a cross-room send is assembled by the engine from data, never from model text (ADR-0007).
- A caller without a person id may not act cross-body; the refusal says so. Ownership is not permission: an automation owned by a person who is not a member of the target is refused at execution.

### 4.5 Reads use the same address

Reading is a separate gate over the same object, because membership, not delivery, is the question.

- `policy.read(subject, address, origin)` asks the **target** body's `Directory.participants` first; when the body cannot prove membership (a WhatsApp group always answers `None`), it falls back to recorded presence (`rooms_of`); otherwise it refuses.
- The limits are unchanged: `visibility` ceiling per DM or group, `memory.rooms.max_rooms_per_turn`, `max_messages`, and the `ctx.cross_reads` buffer that feeds the verbatim-leak guard.
- `chat_history` stays a body tool (ADR-0000 §4.5) and becomes address-aware: `gateway` selects the body, the target body proves membership, and the engine archive is read under the same limits.
- There is still no capability to read a room the requester is not a member of; `rooms_of` is the basis of the read, not a bypass of it.

### 4.6 The tool index is per body

- Tools are indexed as `core: {name}` plus `body: {gateway: {name}}`, rebuilt at boot from `gateway.tools()`; nothing is persisted.
- Collisions disappear because the key is not global. A cross-body call uses a qualified name (`whatsapp__send`), the convention MCP already uses (`mcp__<server>__<tool>`), or a bare name that resolves to the current body.
- Permission stays on the base capability: a role grants `send`, and the body is part of the scope proof, so `roles.yaml` never needs a prefix.
- The prompt lists the current body's tools plus one line naming the other enabled bodies. Schemas are fetched on demand through `info`; an unlisted call is answered with one refusal that names what the body does have. This keeps the stable prefix byte-identical (ADR-0025).
- A disabled body is absent from the index, so its tools are unavailable rather than denied (ADR-0027).

### 4.7 A thread has exactly one owning body

A route is claimed once, by the body that saw the thread first. A second body claiming the same thread id is refused before any row is written, with one log line naming both bodies. Storing the message and replying through the wrong body is the silent failure this ADR removes.

### 4.8 Failure is one string

Unknown body, unknown capability, unknown thread, no `Directory`, ambiguous name, conflicting route, missing approval, unproven membership: each returns one reason and executes nothing. The caller may surface it; it never retries against another body on its own.

## 5. Configuration

No new key. The class rules come from roles and `approvals`; the route from `threads.gateway`; the read limits from `memory.rooms`; the gateway list from the enabled bodies.

## 6. Metrics and gates

| Metric | Today | Target |
|---|---|---|
| Direct calls to a body's send method (outside tests) | 2, both in `agent.py` | 1 (the door) |
| Engine modules that originate a send | 5 (`agent`, `automation`, `privacy`, `relay`, `/kirim`) | all through the door |
| Thread-to-body resolvers | 3 | 1 |
| Chat members required by `GatewayCore` | 2 (`send_text`, `render`) | 0 |
| Tool name collisions across bodies | boot failure | impossible by construction |
| Cross-body sends that pass an approval | 0 (no path) | every one |
| Bodies reachable by name from another body | none | every enabled body |

Gates: `pytest -q`, `python scripts/adr.py --check`, `python scripts/adr.py --translate-check`, `python scripts/selfcheck.py --check`, a conformance test that runs the same scenarios against every body through the door (local thread, another room of the requester, a room the requester is not in, a body without `Directory`, an ambiguous name), and a grep gate: `grep -rn "gateway\.send_text(\|gateway\.send_file(" eidesoul/engine` matches only the door's module.

## 7. Acceptance criteria

- `GatewayCore` requires no chat member; the CLI, harness, and WhatsApp bodies pass `ensure_core`, and a fake robot body with `dispatch` and a `speak` handler passes it too.
- Exactly one engine module calls a body's `dispatch`; `agent.py`, `automation.py`, `privacy.py`, `relay.py`, and `/kirim` call the door.
- `Address.find("group B", gateway="whatsapp")` returns one address; an ambiguous or missing name returns a refusal and nothing is sent.
- A Discord turn can read or remind a WhatsApp room when the requester is a member there; without proof the answer is `denied: room` and no content leaves.
- The same scenario matrix produces the same verdict for turn, command, automation, and robot with the same subject.
- A second body claiming an existing thread is refused with one log line, and no message or turn row is written.
- The ledger shows `intent` then `sent` (or `unknown`), and a repeated id is a no-op.

## 8. Risks and mitigations

- **Typed capabilities become strings.** One `Action` dataclass, one dispatcher table per body, and a refusal that names the accepted verbs keep it debuggable.
- **Bodies start enforcing policy.** A structure test keeps `guard` out of gateway packages; the door is the only place that decides.
- **A chat path silently assumes chunks.** Chunking stays inside the chat path and `reply.py`; a non-chat body never sees it.
- **Approval fatigue.** Same-thread sends stay `consent` without a prompt; only cross-thread or cross-body asks.
- **Name ambiguity becomes user-visible.** The refusal names the candidates; a clarification question is allowed, a silent pick is not.
- **The tool index drifts from the registries.** It is rebuilt from `gateway.tools()` at boot and on reload, never stored.

## 9. Interaction with other ADRs

ADR-0000 owns `Percept`, `Action`, and the `send`/`chat_history` placement; this ADR changes neither the names nor the placement, only the path. ADR-0007 owns the classes, membership, and attribution, all reused here. ADR-0014 owns the durable route and the automation publisher. ADR-0018 owns the body protocol; §4.2 changes its outbound member from `send_text` to `dispatch`, which is the protocol change this ADR is allowed to make. ADR-0023 keeps relay consent separate: a relay is a reason on a send, not a route. ADR-0025 forbids per-turn capability listings in the stable prefix, which is why §4.6 hides schemas behind `info`. ADR-0027 keeps the engine free of a broker. ADR-0031 sequences this work after the engine split.

## 10. References

- G. Hohpe and B. Woolf, *Command Message*. <https://www.enterpriseintegrationpatterns.com/patterns/messaging/CommandMessage.html> — a command is a regular message carrying a procedure invocation.
- G. Hohpe and B. Woolf, *Request-Reply*. <https://www.enterpriseintegrationpatterns.com/patterns/messaging/RequestReply.html> — a return address and a correlation identifier make the reply possible.
- C. Richardson, *Transactional Outbox*. <https://microservices.io/patterns/data/transactional-outbox> — write the intent with the state change, publish afterwards.
- *Idempotent Receiver*, Enterprise Integration Patterns. <https://www.enterpriseintegrationpatterns.com/patterns/messaging/IdempotentReceiver.html> — a repeated message id must not execute twice.
- ROS 2, *Actions* (Jazzy). <https://docs.ros.org/en/jazzy/Concepts/Basic/About-Actions.html> — a long-running call carries goal, feedback, result, and can be cancelled.
- Akka, *Message Delivery Reliability*. <https://doc.akka.io/libraries/akka-core/current/general/message-delivery-reliability.html> — a mailbox is at-most-once; a stronger guarantee needs an application-level acknowledgement.
- A. Cockburn, *Hexagonal Architecture (Ports and Adapters)*. <https://alistair.cockburn.us/hexagonal-architecture/> — the port stays in the engine, each body is an adapter.

## 11. Code references

- `eidesoul/engine/inbound.py` — `Percept`, `Action`, `GatewayCore`, `Directory`, `refusal`, `directory_refusal`.
- `eidesoul/engine/agent.py` — `send_text`, `_gateway_for`, `ask_memory`, `ask_dispute`; the two direct body calls.
- `eidesoul/engine/automation.py` — `_deliver`, `_gateway`, `_thread_gateway`.
- `eidesoul/engine/reply.py` — the chat text policy (chunking, rendering).
- `eidesoul/engine/privacy.py`, `eidesoul/engine/relay.py` — callers that become door callers.
- `eidesoul/engine/tools/__init__.py` — `rooms_of`, `cross_refusal`, the flat registry door.
- `eidesoul/gateways/whatsapp/tools/chat_history.py`, `.../send_to.py` — body tools that become address-aware.
- `eidesoul/engine/stores/history.py` — `_route`, `gateway_of`, `rooms_of`.

---

*This ADR lives at `docs/v0.1.0/adr/engine/0032-address-and-command.md`.*
