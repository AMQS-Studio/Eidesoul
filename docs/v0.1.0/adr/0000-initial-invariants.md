# ADR-0000: Initial Invariants — Surface Shape, Language Data, Embodiment-Neutral Seam

- **Status:** accepted
- **Date:** 2026-11-03
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Context:** three foundations were decided after the first ADR set was written: where new surfaces may live, where language data lives, and whether the core may assume chat. They belong together because each one constrains every later feature — including the robot, harness, and editing bodies the owner plans.
- **Supersedes:** —
- **Related:** ADR-0001 (memory), ADR-0007 (capability text and approval), ADR-0009 (ADR home and its gate), ADR-0015 (payload), ADR-0016 (schema owner), ADR-0018 (gateway contract), ADR-0020 (episode), ADR-0021 (tool output), ADR-0022 (`administrator` and `label`)
- **Order:** foundational — implement before any new body gateway, before the dashboard, and before any string or lexicon move that depends on it.
- **Implementation:** implemented — evidence: §4.2 lexicon packs + pronoun + catalog-only help, §4.3 `Percept`/`Action` + hint keys, §4.4 rename cutover, §4.5 `send`/`chat_history` in gateways, harness body, `tests/test_architecture.py`, §7 box 8 in `AGENTS.md` (`docs/`, `scripts/`, `tests/`, `panel/`, `apps/` outside the package)
- **Location note:** this is the cross-cutting foundations document, so it sits at the root of the ADR home
  (`docs/v0.1.0/adr/`); the Indonesian copy is `docs/v0.1.0/adr/id/0001-initial-invariants.md`. Engine ADRs
  live under `docs/v0.1.0/adr/engine/`, web/panel ADRs under `docs/v0.1.0/adr/web/`.

---

## 1. Context

| Fact | Evidence |
|---|---|
| The core speaks chat | `chat_id` 635 times in `eidesoul/engine`, `send` 301, `reply` 135, `dm` 125, `ChatMessage` 71, `group` 62 |
| The seam is chat-shaped | `Inbound` carries `chat_id`, `is_dm`, `is_mention`, `is_reply` (`inbound.py:18-29`) |
| Storage is chat-shaped | tables `chats`, `chat_eras`, `chat_people` (`schema.py:69,164,187`) |
| Language data sits in modules | `privacy.py:27`, `context.py:317-320`, `character.py:18`, `guard/patterns.py:16`, `prompts.py:97,239,242,277`; default pronoun `"aku"` in `soul.py:57,221` |
| One reply path is hardcoded | `commands/konteks.py:21` returns a literal string |
| Catalogs are complete | 201 keys in each of `lang/en.yaml` and `lang/id.yaml`, parity 0/0 |
| The spine is already body-neutral | memory, affect, guard, lease, worker, and episode do not depend on a transport |

The chat bias is not a naming annoyance: it decides what a "message", a "reply", and a "room" mean
inside the engine, so a robot or an editor body can only be added by pretending to be chat. The
foundations below fix the vocabulary before more features are built on it.

## 2. Decision drivers

| Driver | Consequence |
|---|---|
| Bodies are peers | Chat is one body among robot, harness, editor; none is the base class |
| One owner per concept | Each vocabulary item has a single home and a single ADR owner |
| No aliases, no shims | A rename lands as one cutover (repo rule), so the old name cannot linger |
| Honest degradation | Missing language data weakens a feature visibly, never silently in the wrong language |
| Flat package | New surfaces live outside `eidesoul/`; the package keeps its six subpackages |
| Foundations first | The invariants are implemented before the features that would otherwise bake in chat |

## 3. Options considered

| Option | Summary | Value |
|---|---|---|
| **A. Rename only** | `chat_id`→`thread_id`, `chats`→`threads`, `is_dm`→hint | Cheap, removes the vocabulary, but the seam stays chat-shaped |
| **B. Percept/Action seam** | The engine consumes `Percept` and emits `Action`; a body supplies facts and capabilities | **Chosen, with A as its first step** |
| **C. Rewrite the engine** | New package, new storage | Rejected: the spine is already correct; cost far above benefit |
| **D. Adapter only** | Keep chat types and wrap robots in a chat-shaped adapter | Rejected: every new body would inherit chat semantics it does not have |

## 4. Decision

### 4.1 Surface shape (what may live where)

`eidesoul/` holds the spine and its appendages only: `engine/`, `gateways/`, `providers/`,
`library/`, `lang/`, `vendor/`, `admin/`, `__main__.py`. Everything else lives at the repo root:
`docs/`, `scripts/`, `tests/`, and any future `panel/` or `apps/`. A new tree is never added inside
`eidesoul/engine/`; a body is a package under `eidesoul/gateways/<name>/` (ADR-0018), and an
application that imports the engine (dashboard, editor UI) is a separate process outside the package.

### 4.2 Language data lives in data surfaces

Language-specific lists move to `eidesoul/library/lexicon/<lang>.json` with the schema
`{version, language, negations, stopwords, assistant_tells, injection, prompt_examples}`; the engine
loads the pack for `soul.language` lazily and caches it.

- A missing pack degrades explicitly: language-independent guard patterns still run, the digest falls
  back to length-based selection, prompt examples are omitted — each with one warning visible in
  `/status`. It never borrows another language's list.
- `soul.pronouns` stays optional; when empty it resolves from `t("soul.pronoun.default", language)`.
  No code default `"aku"` survives; a catalog without that key fails at boot.
- Chat-visible text only through `t()`; `/help` requires `command.<name>.description` in the catalog
  and no longer falls back to the handler literal, while `Handler.description` stays the
  **model-facing** tool description.
- CLI demo conversations move to `tests/fixtures/`.
- Enforcement: `tests/test_language.py` scans string literals in `eidesoul/engine/**` against the word
  list taken **from** `library/lexicon/id.json`, checks catalog parity, and checks that no default
  pronoun literal returns.

### 4.3 The seam is `Percept` and `Action`

The engine consumes a percept and emits actions; nothing in `engine/` tests for a transport.

```
Percept  = source (body name), thread (opaque id), actor (person_id),
           kind, payload, hints (body-declared facts), raw (transport event)
Action   = capability (tool name), target (thread or address), payload,
           approval_class (consent | authorization)
```

- `hints` carry body semantics (`dm`, `mention`, `reply`, `group`, `presence`) as **facts from the
  body**. The engine reads them where a decision needs them (privacy, attribution, cross-scope send);
  it never branches on a transport name and never requires a hint it cannot get.
- A body declares its capabilities and hooks: `GatewayCore` for delivery, `Rich`/`Directory` for media
  and people (ADR-0018), plus a body-specific tool package for body actions.
- Memory, affect, guard, lease, worker, and episode stay exactly as they are: they already operate on
  actors and work items, not on chat.

### 4.4 Mechanical rename (step A of the seam)

One cutover, no aliases:

| Today | Becomes |
|---|---|
| `Inbound.chat_id` | `Percept.thread` |
| `ChatMessage` | `Message` (transport-neutral envelope) |
| `Inbound.is_dm` / `is_mention` / `is_reply` | `Percept.hints["dm" \| "mention" \| "reply"]` |
| tables `chats`, `chat_eras`, `chat_people` | `threads`, `thread_eras`, `thread_people` |
| `messages.chat_id` | `messages.thread_id` |
| `rooms_of(person_id)` | `threads_of(person_id)` |

DDL and the migration order belong to ADR-0016; the names belong here. Repo rules forbid shims, so the
old names disappear in the same change and every caller is migrated.

### 4.5 Chat-only capabilities leave the core

`send` (chat egress) and `chat_history` (archive browsing) move to `eidesoul/gateways/whatsapp/tools`
and `.../tools` of whichever body owns an archive. The engine keeps only body-agnostic tools:
`read`, `write`, `shell`, `sandbox`, `web_search`, `mcp`, `memory`, `recipe`, `skill`, `settings`.
A robot body exposes motion/sensor tools, a harness body exposes run/inspect tools, an editor body
exposes document-edit tools — all in their own gateway package (repo rule: body actions never live in
the core).

### 4.6 Order of work

1. Language data: lexicon files, pronoun default, `t()` for `/konteks`, fixtures out, `test_language`.
2. Rename cutover (§4.4) with the ADR-0016 migration.
3. Percept/Action seam (§4.3) and the move of `send`/`chat_history` out of the core (§4.5).
4. Then the first non-chat body (robot, harness, or editor) — and only then the dashboard.

### 4.7 Enforcement

- `tests/test_language.py` (§4.2).
- `tests/test_architecture.py`: an AST scan asserting that `eidesoul/engine/**` contains no transport
  vocabulary outside an explicit allowlist (`hints` keys, gateway-neutral names), and that no module
  under `eidesoul/` outside `gateways/` imports `neonize` or a gateway package (repo rule made
  mechanical).
- `scripts/adr.py --check` stays the gate for this document set; it now enforces the folder layout
  (root, `engine/`, `web/`), globally unique chronological numbers, and resolvable references
  (ADR-0009 §4.1).

## 5. Configuration

No new engine keys for the seam. Language data derives its path from `soul.language`
(`eidesoul/library/lexicon/<code>.json`), with a test-only `engine.language.lexicon_dir` override.
A body declares its name and capabilities in its own config section under `gateways.<name>`.

## 6. Metrics and gates

| Metric | Initial target |
|---|---|
| Transport vocabulary in `eidesoul/engine/**` (`chat_id`, `is_dm`, `ChatMessage`, `chat_eras`) | 0 after the rename |
| Indonesian literals in `eidesoul/engine/**` (outside the lexicon) | 0 (scanner) |
| Catalog parity `en` ⇄ `id` | 0 missing keys per side |
| Commands without `command.<name>.description` | 0 (boot fails otherwise) |
| Bodies that run end-to-end (smoke) | ≥ 2 — chat plus one non-chat body |
| Body-specific tools inside `eidesoul/engine/tools` | 0 |
| New trees inside `eidesoul/engine/` | 0 |

## 7. Acceptance criteria

- [ ] `library/lexicon/{id,en}.json` exist, pass their check, and the engine reads them; grep finds no Indonesian word literal in `eidesoul/engine/**`.
- [ ] A soul with `language: en` and empty `pronouns` resolves an English pronoun; a catalog missing `soul.pronoun.default` fails at boot.
- [ ] `/konteks` and every other handler answer only through `t()`; `/help` refuses to boot when a command description key is missing; `__main__.py` keeps no demo conversation.
- [ ] `Percept`/`Action` replace `Inbound`/`ChatMessage` in the engine; `hints` carry `dm`/`mention`/`reply`; no engine branch on a transport name (AST test).
- [ ] The rename lands as one cutover: no `chat_id`/`is_dm`/`ChatMessage`/`chat_eras` remain in the engine, and the DDL migration is in the ADR-0016 sequence.
- [ ] `send` and `chat_history` live in a gateway package; `eidesoul/engine/tools` holds no body-specific tool.
- [ ] One non-chat body completes a smoke run (percept in, action out) without any engine special case.
- [ ] `docs/`, `scripts/`, `tests/`, `panel/`, `apps/` stay outside `eidesoul/`, and `AGENTS.md` says so.

## 8. Risks and mitigations

| Risk | Mitigation |
|---|---|
| A 635-point rename hides behaviour changes | Cutover is mechanical (search, AST check), tests pin behaviour per path, and the AST guard fails if an old name returns |
| Storage rename needs a migration | ADR-0016 owns the sequence; no dual-write, no compatibility view |
| Prompts regress when chat framing becomes body facts | Payload tests assert the surface facts block; a body that omits them degrades to the neutral wording |
| New bodies tempt special cases in the engine | §4.7 AST allowlist; body actions only in gateway packages |
| Language pack drifts from the catalog | One scanner reads both and compares parity |
| Foundations slip and features bake in chat again | `Order:` line (§4.6) plus this ADR's acceptance list; the dashboard is explicitly last |

## 9. Interaction with other ADRs

- **ADR-0007**: `rooms_of`/`cross_*` become membership facts supplied by the body; the approval classes are unchanged.
- **ADR-0009**: the ADR home gains categories (root for cross-cutting, `engine/`, `web/`) and the rule
  that numbers are unique across the home and never reused (`0000` for this foundations document); this
  ADR sits at the root because it binds every surface.
- **ADR-0016**: owns the `threads`/`thread_people`/`thread_eras` DDL and migration order.
- **ADR-0018**: `GatewayCore`/`Rich`/`Directory` stay; a body adds its own tool package and hints.
- **ADR-0020**: episodes and skills are already body-neutral; a robot episode uses the same closure signals.
- **ADR-0021**: tool output caps stay per tool; body tools inherit the same contract.
- **ADR-0022**: `administrator` and `label` are person/role data, never body vocabulary.

## 10. Code references

- `eidesoul/engine/inbound.py:18-29` — `Inbound` and `Gateway` (the seam to replace).
- `eidesoul/engine/schema.py:44-67,69,164,187` — `messages`, `chats`, `chat_eras`, `chat_people`.
- `eidesoul/engine/agent.py` — send calls and turn handling (rename + seam).
- `eidesoul/engine/turn/prompts.py:141-147` — role rendering; body facts join the payload here.
- `eidesoul/gateways/whatsapp/tools/send.py` and `eidesoul/gateways/whatsapp/tools/chat_history.py` — the two chat-only tools, both out of the core.
- `eidesoul/engine/soul.py:57,221` — `pronouns` default (to remove).
- `eidesoul/engine/privacy.py:27`, `context.py:317-320`, `character.py:18`, `guard/patterns.py:16`, `prompts.py:97,239,242,277` — language data (to move).
- `eidesoul/engine/commands/konteks.py:21` — hardcoded reply (to route through `t()`).
- `eidesoul/__main__.py:429,456` — Indonesian fixtures (to move to `tests/fixtures/`).
- `eidesoul/lang/en.yaml`, `eidesoul/lang/id.yaml` — 201 keys each, parity 0/0.
- `eidesoul/library/patterns|facts|affect/**` — precedent for a checked data surface.

---

*This ADR lives at `docs/v0.1.0/adr/0000-initial-invariants.md`.*