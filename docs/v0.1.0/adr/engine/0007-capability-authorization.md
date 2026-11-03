# ADR-0007: Capability vs Authorization — Three Permission Classes and Approval as a Continuation of Tool Permissions

- **Status:** accepted
- **Implementation:** implemented — evidence: `guard/policy.py` (`consent`/`authorization`, `scope_proof`, `recheck_scope`, `credential_read`), `approvals` `approval_class`/`scope_proof` (revision 7), execution re-check in `commands/approve.py`, `memory_admin`, membership refusal in `chat_history`/`send_to`, `tests/test_safety.py`; gate result: `pytest -q` 753 passed
- **Date:** 2026-11-02
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Context:** follows from ADR-0001 (canonical), ADR-0002 (gate capture), ADR-0004 (labeling), ADR-0005 (retention), ADR-0006 (group privacy)
- **Supersedes:** —
- **Related:** ADR-0003 (worker), ADR-0008 (integrity), ADR-0011 (crash safety), ADR-0012 (lease)
- **Location note:** ADR lives in `docs/v0.1.0/adr/engine/`; the Indonesian copy is `docs/v0.1.0/adr/engine/id/0007-capability-authorization.md`.
- **Amendments:** 2026-11-02 — §4.5 the recall ladder: memory as the umbrella, the archive as evidence, the requester's presence required for cross-room access (gateway-first, fail closed), the `visibility` ceiling only for cross-room reads; §4.6 rejection: the decision is deterministic, the sentence comes entirely from the LLM (varied, not rigid), with no catalog key; the no-oracle invariant is kept on the data side; skeleton normalization (ADR-0009 §4.4): `### 4.4 Konfigurasi` promoted to `## 5. Konfigurasi`, subsections 4.5–4.7 → 4.4–4.6, top-level 5–9 → 6–10. 2026-11-03 — audit: the claim "permission only in `execute`" was corrected to "one door per type of capability" (tools in `execute`, commands in `command_allowed`), because the command gate already exists at `agent.py:961`. 2026-11-03 — owner decision: `cross_send` is defined in §4.5 as the counterpart of `cross_read` — any role that holds send permission may send across rooms **only when the requester is proven to be present in the destination room** (gateway `participants`, fallback `rooms_of`, fail closed) and the target is in the manifest; there is no global permission. 2026-11-03 — `cross_send` was given the same shape as `cross_read`: a role flag (default `false`) **plus** the requester's presence in the destination room; the permission attaches to the requester's role, not to the `send` step. 2026-11-03 — one guard for every send path (the gateway `send` tool from a turn/recipe as well as the automation step): `cross_send` + presence; `trusted`/owner is no longer a send requirement. 2026-11-03 — **the model changed**: `cross_read`/`cross_send` were removed as permissions. Read/send permission is determined by **the requester's membership** (`rooms_of`), not by a role flag; `memory.cross_read.*` → `memory.rooms.*`, `cross_send.max_per_chat_hour` → `send.max_per_chat_hour`; and the content of a send is stated to be subject to the destination room's ceiling (not only its frequency). Reason: "cross" is relative to the conversation room, whereas permission should be relative to the requester's membership. 2026-11-03 — ADR-0020: `skill` was revoked from the `member` role (it becomes an owner-only command). 2026-11-03 — owner decision: approval was split into the `consent` class (the requester themself, once `scope_proof` is proven) and the `authorization` class (the approver role); a non-trusted `sandbox` may take `consent`, while `shell`/`settings`/a trusted sandbox/cross-room send never do. **2026-11-03** — §4.8 the credential class: reading a secret (`env`/`printenv`, `.env`, `/proc/*/environ`, SSH/AWS keys, `docker inspect`) is always `authorization`, never `consent`, including when the requester is `administrator`; a class can only go up, never down.

---

## 1. Context

**Recall is always active; capture is the one that is authorization-gated.** `agent.py:619-628` builds the
memory block without checking the role, so a role without the `memory` tool still receives memory (restricted
only by the ADR-0006 ceiling). Capture, by contrast, requires the `memory` tool on the role:
`can_memory = any(spec.name == "memory" for spec in specs)` (`agent.py:614`).

**Consent is already deterministic and spread across several places:**

- `forget`: a person's own memory only (`mem0.py:487-489`), the chat must be the same chat (`:489`),
global needs `allow_global` (`:491`), which comes from `memory_admin` (`tools/memory.py:129`,
`commands/memori.py:92`).
- `veto`: only the subject (`mem0.py:527-530`, `tools/memory.py:175-183`).
- `add` with scope `global`: `memory_admin` (`tools/memory.py:69-70`, `commands/memori.py:122`).
- `level >= ask_level`: requires `confirmed=true` (`tools/memory.py:71-73`).
- `supersedes`: must be the same scope and not someone else's (`mem0.py:330-338`).

**Approval:** sensitive tools, destructive actions, and a suspicious shell become `pending`
(`guard/policy.py:198-215`); `/approve` is bound to chat+sender+TTL and does not call the LLM
(`commands/approve.py`), and `approved=True` does bypass both safety layers.

**Two gaps:**

1. **`/approve` does not verify current role membership.** `commands/approve.py:41-49`
takes the role name from the payload and then `agent.config.roles.get(name)` — that ensures the role
**exists**, not that the sender **still** holds it. A role revoked after the approval was created
(within the 900-second TTL, `config.py:152`) still runs the tool with full permission.
2. **Capture dies too** when the role is not given the `memory` tool, even though capture is a
capability of the soul, not a conscious action.

## 2. Decision drivers

1. **The soul keeps its memory, and that memory belongs to the person** — capture and recall must not
depend on the list of tools that happened to be granted.
2. **A conscious action can still be restricted** — `add`/`forget`/`veto` called by the model may follow
the tool grant.
3. **Consent cannot be delegated to a role** — only the owner/subject may decide the
life or death of their own memory.
4. **There is no escalation path** — approval must not grant new permissions.
5. **One door per type of capability** — tools in `tools/__init__.py:execute`, commands in `permissions.command_allowed`; no third gate.
6. **Room access follows the requester's membership**, not a permission flag: if they are a member of that room, it is their room.
7. **Auditable** — every permission decision leaves a trace in `ActionLedger`/logs.

## 3. Options considered

| Options | Shapes | Excess | Lack | Status |
|---|---|---|---|---|
| **A. All role-gated** | Capture, recall, forget, veto, export all from the role's tool list | Simple mental model | The soul loses memory if a role is set wrong; consent becomes a role right, not a person's right | Rejected |
| **B. All capability** | Everything open, security only from guard/level | The most "alive" | `forget`/`veto`/`global` become uncontrolled; leaks across rooms | Rejected |
| **C. Three classes without approval enforcement** | Capability/consent/role separated, approval left open | Enough for normal operation | The gap of a revoked role stays open | Rejected |
| **D. Three classes + approval as a continuation of the tool permission** | Capability always active; consent owned by the subject; role/flag for global and export; room access from the requester's membership; approval inherits its tool permission and is rechecked on approve | A clear, deterministic model; each class can be tested | Needs `rooms_of` + a recheck in `/approve` | **Selected** |

## 4. Decision

> **There are three permission classes — capability, consent, authorization. Approval is not an access-granting command: it is only a continuation of the permission of the tool already invoked, and that permission is recomputed against the sender's current role.**

### 4.1 Class map

| Behavior | Class | Exercising |
|---|---|---|
| Recall of own memory (prompt block) | **Capability** | Always active; restricted by the ADR-0006 privacy ceiling |
| Automatic capture | **Capability** | Always active; ADR-0002 structural gate + hard exclusion + `memory.auto_capture` |
| Crisis & hard exclusion | **Capability** | Always runs, cannot be switched off by a role |
| `add`/`search`/`list` person+chat through the `memory` tool | **Authorization** | Grant of the `memory` tool |
| `forget` own memory (person/chat) | **Consent** | Owner/subject; no role |
| `veto` own memory | **Consent** | Only `subject_id` |
| `add`/`search`/`list`/`forget` with scope `global` | **Authorization** | `memory_admin` |
| `forget`/`veto` someone else's memory | **Absolutely forbidden** | Not consent, not a role |
| `add` facts about others through the tool (conscious action, e.g. `subject=<name>`) | **Consent** | May be stored only as a conscious action; `subject_id` required; only that subject may veto |
| `level >= ask_level` | **Consent** | `confirmed=true` + a question (in a group → `pending`, ADR-0006) |
| Reading the archive of **the requester's own rooms** (`chat_history chat=<other>`) | **Authorization** | The requester's membership of that room (`participants` → `rooms_of`, fail closed); no role flag |
| Sensitive tools (`shell`, `sandbox`) | **Authorization** | Tool grant + approval |
| Internal changes (`settings`) | **Authorization** | Tool grant + approval + audit. **Reading** (`schema`/`get`/`reload`) is not a sensitive action: no approval, but it must be redacted (ADR-0015 §4.7) |
| Memory export | **Authorization** | `memory_admin` + audit + L4/credentials excluded |
| `/approve` | — | **Not a permission granter**; it only continues an already-allowed tool |

### 4.2 Approval = a continuation of the tool permission

- When creating an approval: store `tool`, `args`, `kind`, chat, sender, TTL, `roles` (the role name
snapshot as an **upper bound**), and the fingerprint (already running).
- On `/approve`:
 1. Compute the sender's **current** role for that chat (the same path as a regular turn).
 2. The tool must be **still allowed** for the current role; otherwise → `denied` + a ledger note.
 3. The stored role is only used to make sure the approval never exceeds the context it was
created in; it is **not** a source of permission.
 4. `kind=settings` follows the same rule: the sender must still be allowed to call the
`settings` tool (including when `approval.settings` is true).
- `/approve` still does not call the LLM.
- `approved=True` still bypasses `safety.check` (that is the definition of approval), but **not** the
tool permission check.
- An expired approval, or one belonging to another chat or sender, is still rejected as today.

### 4.3 One door per type of capability

- Tool permission is still checked only in `tools/__init__.py:execute` (`allows(ctx.allowed, spec.name)`).
- Command permission is checked in `permissions.command_allowed` from the command path (`agent.py:961`) — this already exists, and no path is added for tools.
- Addition: `chat_history` rejects `chat=` that is **not one of the requester's rooms** (see §4.5).
- No default allow: a role that does not list `tools`/`commands` is still rejected for everything
(as today).
- Global memory remains `memory_admin` at both doors (tool and command).

### 4.4 Memory export

- Requires `memory_admin`, always through approval, and every export writes an audit entry.
- L4 and anything containing credentials is excluded; a content-free tombstone is not exported.
- Export has no implementation today (what exists is the new `documents.export_to_files` for mirroring the
config document, `stores/documents.py:137`). This ADR sets the rules; the implementation follows.

### 4.5 Recall ladder: umbrella memory, the archive as evidence

**Principles:** the soul does not "cross" rooms. It works inside **the requester's rooms** — the set of rooms where
the person asking is genuinely a member, plus the room where the current conversation is happening.
A DM or a group does not change permissions; it changes only the privacy ceiling (ADR-0006). Example: A is a member of the
PDD group and then asks over the DM about PDD → PDD is one of their rooms, not an "other room".

| Steps | Source | When? |
|---|---|---|
| 1 | Memory block (canonical + index) | Always, every turn |
| 2 | Archive of **this room** (`chat_history`, default) | Natural and automatic when the details of the current conversation are needed |
| 3 | Archive of **the requester's other rooms** (`chat_history chat=<other>`) | When the soul decides the memory it holds is only a fragment; membership is enough, no flag |

**The "fragment" signal.** There is no new classifier. The memory block lists `id`, `scope`, date,
and level, and the prompt already tells the soul not to invent: if what it remembers is only a scrap —
e.g. only the theme, with no time/actor/detail — the soul re-reads the room it came from via `chat_history`.
That decision belongs to the soul (tool call), just like a human who remembers a fragment and then opens
the group again. Only the limits are deterministic:

- at most 2 rooms per turn and 20 messages per query;
- only rooms where **the requester** genuinely exists (`rooms_of(requester)`; gateway-first, fail closed);
- a room outside that set → the tool refuses; there is no override;
- every read outside this room is recorded in `ctx.cross_reads` and the ledger.

**The requester's membership, two layers, neutral in the core.** The person asking must genuinely be in that room;
otherwise there is no reading and no sending. It is never guessed from a name or an address:

| Layer | Source | Qualities |
|---|---|---|
| 1 | The gateway capability `participants(thread_id) -> set[person_id] \| None` (`None` = does not know) | **Authoritative**: a transport fact owned by the gateway. If it answers and the requester is not there → **refuse**, whatever layer 2 says |
| 2 | Recorded presence (`HistoryStore.rooms_of(person_id)`): `messages.sender_id` in that chat ∪ `memories.owner_id/subject_id` in that chat | A neutral fallback: used only when the gateway does not know (e.g. console/CLI). Weaker — someone who has left the group still looks present |

- **Fail closed**: presence not proven by any layer → refuse.
- **No override**: `memory_admin` cannot read a room they do not enter themself.
- Core seam: `HistoryStore.recent_chats()` (`stores/history.py:343-352`) gives the `thread_id` list,
and the person→room derivation pattern already exists in `_chats(db, person_id)` (`memory/rekey.py:91`); the
new part is only `rooms_of(person_id)` as an explicit intersection for the requester.
- `thread_id` stays opaque; **names** of rooms only come from `gateway.chat_name()`
(`gateways/whatsapp/tools/chat_history.py`). The gateway stays an appendage: it supplies labels and membership facts,
not permission rules. The rules stay in the core.

Note: scope `person` needs no membership — that memory follows its person. Membership is only
relevant to the archive and to sending outside this room. There is no new field in `Role`: the set is computed
from gateway/archive facts, not stored as a role flag.

**A ceiling for the archive outside this room.** `HistoryStore.search` today does not filter
`visibility` (`stores/history.py:318-341`) — its filters are only chat, time, and keywords. That is a hole
of the same class as the one ADR-0006 closed, so it is closed here:

- archive outside this room: `visibility <= (dm_max_level for a DM, group_max_level for a group)` — L3/L4
never leave the room they came from;
- own room: not filtered — the soul really is there, and its reply goes back to that room;
- the `cross_chat_leak` guard (`agent.py:857-861`, `guard/patterns.py:45`) remains as the last
layer against verbatim quotes.

**The direction of leakage that stays forbidden:** content born in one room does not become ambient context
of another room (ADR-0006). This rule only opens *conscious reads* by the requester who is indeed a member of
that room; it is not an automatic sweep on every turn.

**Sends: default in-room, `cross_send` across rooms, and a `cross_read` that does not exist.** The gateway
`send` tool accepts `chat`; an empty `chat` means this room. The class is determined by **the requester's membership**, not
by the model's choice. Only the cross case needs a name, because only it needs special treatment:

| Case | Condition | Attribution (embedded) |
|---|---|---|
| ordinary send (`send.class=room`) | target ∈ `rooms_of(requester)` or this conversation room | **optional** — the room context is already clear |
| `cross_send` (`send.class=cross`) | target **not** a room of the requester (including another person's DM) | **required**, rendered by the engine |
| `cross_read` | — | **no such capability**: a cross-room read is rejected by `rooms_of`, with no override |

**A cross-room read is not "not yet permitted", it does not exist.** `cross_read` is a privacy invariant,
not a flag that can be switched on: it is what makes the system pass an enterprise privacy audit, because there is no
code path that can read a room without membership. Conversely, **`cross_send` does not require
`cross_read`**: sending does not require reading, provided the message makes clear who sent it and why —
and that is guaranteed by the attribution block below.

**The attribution block is mandatory and must not be removed by the model.** For `cross_send`, the engine assembles the block
from data, not from LLM text:

- `author` — **required, out of the box**, including for a DM target: the canonical person id
(`inbound.person_id`) + display name. It cannot be set from model text, and the name cannot be replaced
by someone else's — impersonation attempts are rejected.
- `reason` — **required** for `cross_send`: the reason the requester stated. No reason → the send
is rejected, not invented.
- `sent_at` and the destination room name — from the engine.
- **a flexible extra field**: the requester may ask for another field ("say this about project X"),
rendered as data inside the same block, sanitized and length-limited. There is no hardcoded list
of fields in the code; the engine only **requires** `author` (and `reason` for
`cross_send`).

The LLM writes the **body** of the message; the engine attaches the block and validates its presence. If the model forgets,
the block is still added; if the model tries to fake `author` or remove the markers, the send
**fails** — it is not sent without attribution.

**Limits and consequences.** `max_per_chat_hour` remains; every send is recorded in `ActionLedger`
(actor, destination, class, reason). The requester **receives no reply** from a room that is not theirs, and
the soul says so plainly when asked. The payload is subject to the destination room's ceiling and the
`cross_chat_leak` guard: a fact about others above the ceiling must not travel; a fact about the
requester themself may. The automation step `send` (ADR-0014) uses the same rules, with `administrator`
as `author`.

**The payload is subject to the destination room's ceiling.** The `cross_chat_leak` guard is extended to the output of `send`,
not only to a turn reply. Without this, `send` becomes an exfiltration channel: read in room 1, publish in
room 2. A fact about the requester themself is not restricted by the ceiling — they may tell about themself.

**One guard, all paths.** The class and attribution checks are used by **every** send capability:
the gateway `send` tool (from a turn or from a recipe) and the automation step `send` (ADR-0014). There is no
attribution exception — not even `administrator` can `cross_send` without `author` + `reason`.

The only message without this block is the **DM protocol** to the data subject about **their own data**
(L3/L4 confirmation questions and the veto options, ADR-0006): it is an engine message, not a `cross_send` from
the requester, its contents are limited, and it is one message.

### 4.6 Rejection: the sentence from the LLM, the decision from the core

The permission decision is deterministic; **the delivery of the sentence is handed to the LLM** so that it is not uniform and
not rigid, without compromising the invariant.

| | Deterministic (core) | Delegated (LLM) |
|---|---|---|
| Allowed to read or not | The requester's membership of that room + the `visibility` ceiling, fail closed | — |
| Information that reaches the model | Yes: a denial signal that is **free of information** — no number of messages, no presence or absence of content, no topic hints | — |
| The sentence sent to the user | — | Free paraphrase: persona, language, context. Variation in shape is allowed; **the information must not increase** |

- **The tool returns a signal, not a sentence.** The contract is e.g. `denied: cross-room` with no content
at all. Since the model never receives a fact about that room, it **cannot** leak
what it has never seen. An internal `error:` is forbidden — that leaks to the user as text.
- **One prompt rule.** If a room is out of reach: say briefly that you cannot
help because the requester has no access to it. Do not speculate, do not confirm or
deny anything's existence there, do not offer another path to the same content.
- **No oracle still holds.** Replies do not distinguish a room with contents from an empty room; asking
multiple times with different keywords produces no new information. Variation of sentences is not
a leak as long as the fact given to the model is identical.
- **No special catalog key.** The rejection is always born from the LLM, because the LLM is the one calling the
tool and composing the reply. Degradation does not change that: an invalid provider key, a dead provider,
or exhausted quota still ends as an error message on the LLM path (`agent.api_error`), not as a
tool rejection that needs deterministic text. Deterministic commands that live without the LLM
(`/help`, `/memori`, `/status`, `/cari`) read nothing across rooms, so there is no chat
string that needs to be written in the code or in a catalog.
- **Consistent with the other path:** the same request through `memory` (scope `thread` of another room) is also
rejected, because scope `thread` is only read in its own room (ADR-0006 §4.4). One behavior, two doors.

### 4.7 Approval: sensitivity determines *whether* there is an approval, the target determines *its class*

Approval is not a derivative of the tool permission, and it is not "sandbox always asks for permission". Two questions are separated:

1. **Is the action sensitive?** → determines whether approval is needed. Sensitive = destructive (delete/overwrite/truncate), credential access, configuration change, network egress, cross-room send, or a command pattern on the sensitive list. The detection is deterministic (`guard/policy.py`: `sensitive_tools`, `sensitive_commands`, `DESTRUCTIVE_ACTIONS`).
2. **Where is the target?** → determines who approves.

| Action target | Class | Decider |
|---|---|---|
| Inside the requester's scope (`scratch/<chat>`, their own memory, their own DM) | `consent` | the requester themself (`member` is allowed too) |
| Outside the requester's scope (`public/`, another room, another person's row, host configuration, credentials) | `authorization` | the approver role (administrator) |

Examples that are referenced:

- "please delete my workspace" → **destructive**, so approval exists; every target is in `scratch/<chat>` → **`consent` to the requester**, not to the administrator.
- "add this sticker to `public`" → the target is a shared area → **`authorization` to the administrator**, with a proposal-shaped message: *"Si wants to add this sticker to the public. Agreed?"* + artifact/diff.
- A non-trusted `sandbox` cannot touch `public` (mounted `ro`), so its destructive action always ends as `consent`.
- `shell` (host, unjailed), `settings`, credentials, and cross-room sends are always `authorization`, because the target is never proven to be inside the requester's scope.

The `consent` condition is **evidence**, not good intentions:

- `scope_proof` is computed deterministically at request time — a list of action targets (path, room, column) + a snapshot of the requester's role roots — and is **re-matched at execution**; if the role changed such that the reach widened, the approval rises to `authorization` or is denied, and it is never executed quietly.
- The approval message contains a summary of the resolved targets ("12 files in `scratch/<chat>`; `public` read-only") and is sent **in the requester's DM**, not in the group; for `authorization`, the message names the requester and their artifacts ("Si wants to add...").
- `/approve` for `consent` requires `sender == requester` and the same `thread_id`; for `authorization` it needs the approver role. There is no "approve someone else's" path.
- `mode` remains valid: `yolo` (default) does not use approval; `off` disables it; `strict` uses the class above, not "everything goes to the administrator".
- Tool permissions are still rechecked at execution (the approval class adds no permission whatsoever).

### 4.8 Credentials: reading a secret is always `authorization`

If the host's secret is the target rather than the requester's scope, the class is **`authorization`** (the approver role) —
never `consent`, including when the requester is `administrator` and the room is their own DM. What is at stake
is not the requester's reach, but the instance's credentials.

| Pattern | Sample |
|---|---|
| Dump environment | `env`, `printenv`, `set` with no argument, `export -p` |
| Secret file | `.env`, `*.env`, `*.pem`, `*.key`, `~/.aws/credentials`, `~/.ssh/id_*`, `~/.netrc` |
| Another process | `/proc/*/environ`, `/proc/*/cmdline` |
| Runtime metadata | `docker inspect`, `docker compose config` (loads the container env) |
| Host config | `settings get` on a sensitive key (DSN, API key, credentials path) |

- ADR-0024 §4.6 already closed the leak through `env` inheritance on `shell`; this class closes the
reading of **files**.
- `scope_proof` records the matching patterns + the target path, then rechecks at execution; a widened
scope reopens the approval.
- `engine.guard.credential_patterns` only **adds** patterns; revoking a default pattern needs a new ADR.
- The class can only go up: this pattern never lowers the class of an action that is already `authorization`.

## 5. Configuration

```yaml
approval:
 mode: yolo
 consent_max_per_hour: 20 # self-confirmation anti-spam; the class is derived from the target, not from the tool list

roles:
 member:
 tools: [read, grep, fetch, web_search, sandbox, chat_history, memory, google, send
 memory_admin: false
 administrator:
 tools: ["*"
 memory_admin: true

memory:
 rooms:
 max_rooms_per_turn: 2 # deterministic limit; the decision to read still belongs to the soul
 max_messages: 20
 require_membership: true # participants → rooms_of → fail closed
 source: gateway_first # gateway_first | recorded (recorded fallback)

send:
 max_per_chat_hour: 10 # anti-spam for menfess/repeated sends

guard:
 credential_patterns: [] # additions (§4.8); the class is always authorization, built-in patterns cannot be revoked
```

There is no new field in `Role`: membership is computed when used (`rooms_of`), not stored on the
role. The implementation follows in `chat_history`/`send` with one shared helper.

## 6. Metrics and gates

| Metric | Start target |
|---|---|
| An approval rejected because the role was revoked | Visible in the ledger, not zero when there is a revocation |
| An approval run with a role that no longer holds the tool | 0 (tested) |
| Capture from a role without the `memory` tool | Not zero (proof the capability works) |
| Reading of a room outside the requester's rooms | 0 (tested) |
| Export without `memory_admin` | 0 (tested) |
| Verbatim leak between rooms | 0 (guard `cross_chat_leak` + test) |
| Rooms read per turn | ≤ 2 (deterministic bound) |
| L3/L4 rows of another room read from a group | 0 (tested) |
| A reply that distinguishes a room with contents from an empty one | 0 (tested) |
| A cross-room rejection that mentions the existence of content | 0 (probe) |
| LLM speculation about the contents of another room | 0 (eval probe) |
| `chat_history`/`send` to a chat outside the requester's rooms | 0 (tested) |
| A leak through `send` (a third-party fact above the ceiling to another room) | 0 (tested) |

## 7. Acceptance criteria

- [ ] Capture runs for any role as long as the structural gate passes; the `memory` tool grant no longer decides.
- [ ] Recall of own memory is still always active; no role can turn it off.
- [ ] `/approve` refuses when the tool is no longer allowed for the current sender role.
- [ ] `/approve` refuses when sender/chat do not match or the approval has expired (already the case, kept).
- [ ] `forget`/`veto` of another person's memory has no path at all, including through approval.
- [ ] A fact about others can only enter through the conscious `memory` tool action with `subject_id`, and only that subject may veto.
- [ ] `chat_history chat=<other>` is refused when that room is outside **the requester's rooms** (no flag).
- [ ] Text sent over `send` to another room does not contain a third-party fact above the destination ceiling (the same guard as for a turn reply).
- [ ] `global` still needs `memory_admin` in the tool and the command.
- [ ] `level >= ask_level` still needs `confirmed=true`; in a group it becomes `pending` (ADR-0006).
- [ ] Memory export requires `memory_admin` + approval + audit, and rejects L4/credentials.
- [ ] `HistoryStore.search` applies the `visibility` ceiling when `thread_id != ctx.thread_id`; the own room is not filtered.
- [ ] Test: an L4 row of another room does not appear when read from a group, but does appear when read from a DM.
- [ ] `HistoryStore.rooms_of(person_id)` returns only the rooms where that person is recorded as present (the intersection of `messages.sender_id` and `memories.owner_id/subject_id`).
- [ ] No automatic room sweep per turn; the archive outside this room only goes through a soul tool call and is bounded by `max_rooms_per_turn`.
- [ ] A cross-room send is refused when the requester is not in the destination room (the gateway says no → refuse even if the recorded presence agrees).
- [ ] Unproven presence (`None` from the gateway and no core record) → refuse (fail closed).
- [ ] `memory_admin` is also refused when reading a room they do not enter.
- [ ] `rooms_of(person_id)` computes presence from `messages.sender_id` ∪ `memories.owner_id/subject_id` in that chat.
- [ ] Room names come only from `gateway.chat_name()`; the core does not call the gateway membership API.
- [ ] The tool returns a `denied: room` signal without content; no internal `error:` reaches the user as text.
- [ ] The rejection sentence is composed by the LLM (persona + language), and the facts the model receives are no more than "access denied".
- [ ] There is no chat string for a room rejection in the code or in a catalog; the sentence always comes from the LLM.
- [ ] No-oracle: replies do not distinguish a room with contents from an empty one, do not confirm the existence of content, and probes with different keywords add no information.
- [ ] No handler has a default permission.
- [ ] `cross_send` to a room where the requester is not a member (including "B's date") **is sent with** the attribution block `author` + `reason`; `cross_read` to that room remains rejected.
- [ ] The model removes the block or fakes `author` → the send fails; no message is sent without attribution.
- [ ] `cross_send` without `reason` → rejected; an ordinary send (`send.class=room`) without attribution → still allowed.
- [ ] Extra fields requested by the requester are rendered inside the block (data, sanitized, length-limited); there is no hardcoded field list.
- [ ] Relaying content from room X to room Y is still bounded by the ceiling + `cross_chat_leak`; a third-party fact above the ceiling does not travel.
- [ ] The DM protocol (ADR-0006) remains one confirmation message about the subject's data, with no other room's content, and is not a `cross_send` from the requester.
- [ ] `cross_read` has no handler or flag: no code path reads a non-member room; access tests refuse without override, including for administrators.
- [ ] `send.class` is recorded in the ledger (`room`/`cross`), so cross-room sends can be counted without a third term in human language.
- [ ] `env`/`printenv`/`cat .env`/`cat /proc/self/environ` → class `authorization`, not `consent`, and still so when the requester is `administrator`; an added pattern never lowers the class.

## 8. Risks and mitigations

| Risks | Mitigation |
|---|---|
| Capture always active adds cost | ADR-0002 structural gate + rate limit + cost metric |
| A role is revoked but the approval is still alive | Permission recheck on `/approve` + 900-second TTL |
| Room access is taken for granted | Membership computed when used (`rooms_of`), fail closed; verbatim-quote guard; `cross_reads` audit |
| Consent is assumed to be "helped" by a role | The class table is the reference; a test rejects the role path for `forget`/`veto` of others |
| Export becomes a leak path | `memory_admin` + approval + audit + the L4/credentials exception |
| Two doors to global (tool & command) | Both read the same `memory_admin`; a test covers both |
| The soul feels "fragmentary" and invents | The memory block carries `id`/scope/date/level; the prompt tells it to re-read the room it came from, not to guess |
| Room access is used to peek into other rooms | The requester's membership (gateway-first, fail closed) + ledger; no admin override, no flag to redeem |
| The archive is used as a second memory | Recall ladder explicit: memory is the umbrella; the archive is only evidence; the archive outside this room is bounded to 2 rooms/turn |
| A sensitive row of another room gets read | The `visibility` ceiling outside this room (equivalent to ADR-0006) + the `cross_chat_leak` guard |
| The existence of content in another room leaks through the shape of the reply | Information-free rejection + a "with contents vs empty" test |
| The LLM speculates or invents about another room | The model never receives a fact about that room; the prompt forbids speculation; eval probe |
| Sentence variation is seen as inconsistent | Variation in shape is intentional; what is measured is the information invariant, not word uniformity |

## 9. Interaction with other ADRs

- **ADR-0001** — canonical remains the source of truth; the permission class only determines who may touch it.
- **ADR-0002** — capture always active reinforces the structural gate as the only cost filter.
- **ADR-0003** — the worker does not check the role; it works the queue.
- **ADR-0004** — `pending`/`level NULL` cannot be read by anyone, including `memory_admin`.
- **ADR-0005** — retention runs regardless of the permission class.
- **ADR-0006** — the group ceiling and self-capture apply before any permission class; §4.5 applies the same ceiling to the archive outside this room.
- **ADR-0008** — provenance/trust does not raise the permission class.
- **ADR-0011/0012** — approval and lease are both CAS/claim; a failure must not grant permission.
- **ADR-0015** — the capability index shows permissions per requester without concealment; the permission gate stays `execute`, and tool activation is not a permission granter.
- **ADR-0023** — forwarding a private answer to another party uses `relay_grant` (the content owner's permission), not `cross_read` and not an ordinary `cross_send`.

## 10. Code references

- `eidesoul/engine/agent.py:614-628,857-861` — capture gate, memory block, cross-chat guard.
- `eidesoul/engine/tools/__init__.py:180-235` — the single door `execute` + `allows`.
- `eidesoul/engine/tools/memory.py:64-73,104-119,127-183` — `global`, `confirmed`, `forget`, `veto`.
- `eidesoul/engine/commands/memori.py:41-43,83-92,121-123` — the command door for scope/`memory_admin`.
- `eidesoul/engine/commands/approve.py:20-80` — chat/sender/TTL binding and the re-run through `execute`.
- `eidesoul/engine/guard/policy.py:105-215` — `sensitive_tools`, destructive actions, shell scans.
- `eidesoul/engine/memory/mem0.py:476-530` — `forget`, `set_privacy`, `veto`.
- `eidesoul/engine/access/roles.py:18-28,100-116` — `Role` (no room flag; membership is computed when used).
- `eidesoul/gateways/whatsapp/tools/chat_history.py` — reading another room + the `cross_reads` buffer for the leak guard.
- `eidesoul/engine/tools/settings.py:294-413` — approval for internal changes.
- `eidesoul/engine/stores/documents.py:137` — `export_to_files` (config document mirror).
- `eidesoul/engine/stores/history.py:318-352` — `search` (where the `visibility` ceiling is installed) and `recent_chats`.
- `eidesoul/gateways/whatsapp/tools/chat_history.py` — `chat_history` and the separation of this room from other rooms.
- `eidesoul/engine/memory/rekey.py:83-97` — the `_chats(db, person_id)` pattern for `rooms_of(person_id)`.
- `eidesoul/engine/inbound.py:29-70` — the `Gateway` contract (where the `participants(thread_id)` capability is added, optional).
- `eidesoul/engine/turn/context.py:72-81,288-300` — `clamp_visibility`, `entry_tag`, `set_visibility`.
- `eidesoul/engine/config.py:148-152` — `ApprovalConfig` (mode, ttl, settings).
- `eidesoul/engine/turn/prompts.py:195-355` — `system_prompt` (where the "out of reach → short refusal, do not speculate" rule lives).
- `eidesoul/engine/guard/patterns.py:12,14` — `SENSITIVE`/`INJECTION`, the existing pattern shape; the §4.8 credential class **does not exist in code** (a proposal, part of ADR-0024 at implementation).
- `eidesoul/engine/config.py:227-232` — `GuardConfig`, where `credential_patterns` is added.

---

*This ADR lives at `docs/v0.1.0/adr/engine/0007-capability-authorization.md`.*
