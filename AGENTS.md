# AGENTS

Hard rules, architectural invariants, module topology, and end-to-end call flows for this repository.

## 1. Language and Purity

- **English Only in Code and Docs:** English is mandatory for all Python code, comments, docstrings,
  log messages, internal error strings, CLI help, prompt scaffolding, model-facing payloads, config
  comments, tests, and ADRs (`ADR-0001`).
- **Runtime Data Surfaces:** Non-English is permitted only in `monoagens/lang/<code>.yaml` (localized
  chat catalogs) and `soul.yaml` persona content.
- **No Hardcoded Chat Strings:** All user-facing chat replies and notices must go through `t()` from
  `monoagens/engine/i18n.py` with matching keys in both `monoagens/lang/en.yaml` and
  `monoagens/lang/id.yaml`.
- **Canonical ADR Numbering:** The architectural decision records live in `docs/v0.1.0/adr/` as
  `ADR-0001` through `ADR-0010`. Code comments and docstrings must reference only `ADR-0001`..`ADR-0010`.

## 2. Boundaries and Hexagonal Architecture

- **Engine is an Autonomous Agent Harness (`ADR-0001`):** Pure Python (`>=3.14`) and `asyncio` only
  (zero Node.js or external JS runtimes). The engine maintains one continuous identity (`soul.yaml`),
  persistent hybrid memory, tool orchestration, and role-based access control.
- **Sacred Core & Strict Gateway Separation:** Core lives in `monoagens/engine/` and must **never**
  import `monoagens.gateways`, transport SDKs (`neonize`, `discord`, `slack_bolt`, `aiogram`), or
  construct UI widgets/button styles.
- **Appendage Contract (`monoagens/engine/inbound.py`):** Gateways connect exclusively via
  `Percept`, `Address`, `Action`, `Receipt`, and `GatewayCore` (plus optional protocols `Rich`,
  `Directory`, `PrivateThread`, `Abortable`). Gateways own address normalization, platform rendering,
  and interactive prompt buttons (`BaseGateway.prompt_options`); core holds zero transport vocabulary.
- **Provider Protocol Seam (`monoagens/providers/`):** Provider adapters (`openai`, `gemini`,
  `anthropic`) own wire formatting and streaming deltas. `monoagens/engine/` never branches on
  provider names; capabilities are declared on `ProviderDefaults`.
- **Async Event Loop Integrity (`ADR-0003`):** `async def` paths must never block on synchronous
  SQLAlchemy transactions, CPU-bound embeddings/NLP (MiniLM/spaCy), or file/subprocess I/O. Offload
  all synchronous work on the turn path via `await io.run(...)` (`monoagens/engine/io.py`).
- **Unlimited Defaults & No Hardcoded Models (`ADR-0009`):** `engine.llm.model` is chosen by the
  operator in `config.yaml` (with optional `engine.llm.fallbacks`). Numeric caps (`max_calls_per_turn`,
  `prompt.max_chars`, `prompt.thread_chars`, `memory.max_chars`, `skills.max_chars`) default to `0`
  (unlimited), and internal system rounds (`info` schema lookup, `_merge_system`, `_retry_vision`,
  `_final`) never consume user turn call budgets.

## 3. Storage and Process Invariants

- **Dedicated Sovereign Architecture (`ADR-0001`, `ADR-0002`):**
  `1 Deployment = 1 Soul = 1 PostgreSQL Database = 1 Primary Process`.
- **Single Primary Process & Lease Fencing (`ADR-0002`):** Enforced via `engine_lease` in PostgreSQL
  with `CheckConstraint("id = 1")`. Every mutating store checks the lease generation fence; an
  instance without the lease stays in standby and writes nothing to PostgreSQL.
- **Single System of Record (`ADR-0002`):** PostgreSQL + `pgvector` + `pg_trgm` via SQLAlchemy Core
  (`monoagens/engine/db.py` & `monoagens/engine/schema/`). Schema changes go exclusively through the
  linear Alembic migration chain in `migrations/`.
- **Authoritative Host Files (`ADR-0002`):** Operator YAML files (`config.yaml`, `roles.yaml`,
  `soul.yaml`, `.env`) on disk are the source of truth; the `documents` table in PostgreSQL is a
  derived cache updated after atomic host-file writes. Secrets live only in `.env`.

## 4. Memory, Privacy, and Cross-Thread Isolation

- **Unified Hybrid Memory Store (`ADR-0007`):** The `memories` table in PostgreSQL is the sole
  canonical store and retrieval index (MiniLM `vector(384)` HNSW cosine similarity + `search_tsv`
  GIN full-text search + recency/salience scoring).
- **Single Write & Read Ports:** All canonical memory writes pass through `MemoryStore`
  (`monoagens/engine/memory/store.py` & `writes.py`). Prompt-time reads query only the
  `memories_readable` view (`queries.py`).
- **Layered Privacy (`L0–L4`) & Secret Rejection (`L5`):** Deterministic evidence is collected in
  `monoagens/engine/memory/evidence.py` before privacy levels are assigned. Secret credentials (`L5`)
  are rejected before storage. Memories with `level IS NULL` or `vetoed = true` never reach an LLM.
  Disclosing an `L4` personal memory in a shared room parks a consent approval (`CONSENT`) in the
  subject's private thread (`PrivacyService.review`).
- **Strict Scope & Cross-Room Isolation (`ADR-0004`, `ADR-0007`):**
  - Prompt memory retrieval is strictly scoped to `global` + `person:<sender>` + `thread:<active_thread>`.
  - Reading another thread via `thread_history` (`monoagens/engine/tools/thread_history.py`) requires
    proven membership (`gateway.participants(thread_id)` or recorded presence in `history.rooms_of`,
    failing closed via `cross_refusal` / `inbound.refusal`).
  - Content read from another room is tracked in `ctx.cross_reads` and guarded by
    `guard.cross_chat_leak` (`monoagens/engine/guard/patterns.py`) so verbatim cross-room quotes are
    blocked before delivery.

## 5. Security, Authorization, and Tools

- **Single Outbound Door (`ADR-0004`):** Every outbound message, progress update, file, sticker,
  voice note, or reaction must pass through `Agent.dispatch(action, subject=..., origin=...)`
  (`monoagens/engine/agent_guard.py`).
- **Mandatory Explicit Origin:** `origin: Address` is required on every outbound call and must never
  default to the target address, guaranteeing cross-room and cross-gateway authorization checks.
- **Fail-Closed RBAC (`ADR-0006`):** Permissions come strictly from `roles.yaml`
  (`monoagens/engine/access/`). Unknown senders or missing role entries are denied. `administrator`
  is the protected top role (`tools: ["*"]`, `memory_admin: true`).
- **Sandbox Guard & Approvals (`ADR-0006`, `ADR-0008`):**
  - `shell` and `sandbox` tools execute inside rootless Podman containers (`monoagens/engine/sandbox/`)
    with `--network=none` by default (or filtered through `ProxyServer` blocking RFC1918/loopback/link-local
    IPs when `sandbox.network.enabled: true`).
  - Sensitive tool calls (`shell`, `sandbox`, `settings`, destructive writes, or cross-room sends)
    are evaluated by `SafetyPolicy` (`monoagens/engine/guard/policy.py`) and `SandboxGuard`
    (`monoagens/engine/guard/sandbox.py`), parking pending rows in `ApprovalStore`
    (`monoagens/engine/stores/approvals.py`) resolved via `/approve #<id>` or `/deny #<id>`.
- **Deterministic Tool Action IDs:** Outbound actions emitted inside a tool step use deterministic
  IDs (`bind_tool_action` / `tool_action_id` in `monoagens/engine/inbound.py`) checked against
  `OutboxStore` (`monoagens/engine/stores/outbox.py`) so resumed turns never duplicate side effects.

## 6. Extensions, Gateways, and Module Hygiene

- **Clean by Default (`ADR-0005`, `ADR-0010`):** External gateways (`whatsapp`, `telegram`, `discord`,
  `slack`) and plugins (`plugins/`) are disabled by default until enabled via `monoagens gateway enable`
  or `monoagens plugin enable`.
- **Boundary UI Resolution (`ADR-0010`):** `BaseGateway` (`monoagens/gateways/base.py`) resolves
  interactive options (`prompt_options`) from `Action.meta["options"]` or synthesizes localized
  `Approve` / `Deny` buttons from `/approve #<id>` badges at the gateway boundary, and coalesces
  in-place progress edits (`turn:<turn_id>:progress`) via `EditDebouncer`.
- **Module Size & Count Caps (`scripts/selfcheck.py`):**
  - Every Python file in the repository must stay `<= 800` lines.
  - Top-level `monoagens/engine/*.py` files must stay `<= 16` modules.
  - No dead/unreferenced functions, classes, or stale ADR numbers may accumulate.

## 7. Package and Module Topology

```text
monoagens/
├── cli.py & admin/          # CLI parser, doctor, backup/restore, init wizard, service & gateway admin
├── lang/                    # Localization catalogs (en.yaml, id.yaml) accessed via engine/i18n.py
├── providers/               # Wire adapters: openai.py, gemini.py, anthropic.py, base.py
├── gateways/                # BaseGateway (base.py) + native bodies: whatsapp/, telegram/, discord/, slack/
└── engine/                  # Sacred Core (zero gateway/SDK imports, <= 16 top-level .py files)
    ├── agent.py             # Central Agent harness (inherits DeliveryFacade, CrossRoomGuard)
    ├── agent_delivery.py    # DeliveryFacade forwarding to TurnDelivery
    ├── agent_guard.py       # Single outbound door Agent.dispatch & cross-room guard
    ├── alerts.py            # AlertManager for consecutive failure tracking & operator DM alerts
    ├── clock.py, db.py, i18n.py, inbound.py, io.py, registry.py, runtime.py, self_knowledge.py, soul.py
    ├── access/              # RBAC roles.py, permissions.py, service.py, privacy.py, relay.py
    ├── character/           # Persona voice, style, and draft polish
    ├── commands/            # Slash command handlers (/status, /memory, /approve, /thinking, /steps, /alerts, ...)
    ├── config/              # Config dataclasses, YAML loader, defaults, and settings writer
    ├── guard/               # SafetyPolicy (policy.py), SandboxGuard (sandbox.py), leak/credential patterns.py
    ├── jobs/                # Background workers: lease.py, cognition.py, automation.py, ambient.py
    ├── llm/                 # LLMClient (client.py), model fallbacks.py, retry/circuit-breaker support.py
    ├── memory/              # MemoryStore (store.py, writes.py, queries.py), evidence.py, privacy.py, embedder.py
    ├── observability/       # Structured logging, correlation context, and usage/health reporting
    ├── plugins/             # Plugin manifest validation, loader.py, and installer
    ├── sandbox/             # Podman container manager (manager.py) and SSRF-safe HTTP proxy (proxy.py)
    ├── schema/              # SQLAlchemy Core table definitions (tables.py, memory.py, ops.py)
    ├── speech/              # STT and TTS provider seam
    ├── stores/              # PostgreSQL stores: history, turns, outbox, approvals, identity, skills, usage, ...
    ├── tools/               # ToolIndex & execute door + core tools (read, write, shell, sandbox, web_search, ...)
    └── turn/                # TurnCoordinator, TurnRunner (loop.py), prompts.py, review.py, delivery.py, progress.py
```

## 8. End-to-End Turn Lifecycle and Call-Flow Map

1. **Bootstrap & Lease (`monoagens/cli.py` $\to$ `monoagens/engine/agent.py`):**
   `Agent.__init__` initializes `Database`, `LeaseManager` (`engine_lease` row `id = 1`), `TurnRuntime`,
   `ToolIndex`, `PluginLoader`, and `AlertManager`. `Agent.start()` acquires the lease and launches
   background workers (`cognition`, `automation`, `ambient`).
2. **Inbound Ingress (`monoagens/gateways/<body_name>/ingress.py` $\to$ `Agent.handle`):**
   Platform events normalize into `Percept(source, thread, actor, kind, payload=Message, hints)`.
   The gateway checks `access_refusal` and calls `await agent.handle(percept, gateway)`.
3. **Access Control, Commands, & Turn Queue (`AccessService` $\to$ `TurnCoordinator`):**
   - `AccessService` resolves `person_id` (`IdentityStore`), checks `roles.yaml`, and enforces
     `freeze`, `away`, and rate-limit gates.
   - Slash commands execute directly via `monoagens/engine/commands/__init__.py` (`REGISTRY`).
   - Conversational turns enter `TurnCoordinator` (`monoagens/engine/turn/coordinator.py`), which
     coalesces burst messages, acquires the per-thread lock, and invokes `TurnRunner.run_turn`
     (`monoagens/engine/turn/loop.py`).
4. **Prompt Assembly & Hybrid Memory (`monoagens/engine/turn/prompts.py` & `memory/store.py`):**
   - `system_prompt` assembles `Prompt(stable, volatile)` from `CORE_PARTS` (`soul.yaml`, skills,
     affect, style, workspace, body `prompt_section`).
   - `MemoryStore.context` queries `memories_readable` (pgvector HNSW + FTS GIN) strictly scoped to
     `global` + `person:<sender>` + `thread:<active_thread>`.
5. **LLM Loop & Tool Execution (`TurnRunner` $\to$ `LLMClient` & `tools.execute`):**
   - `LLMClient` calls the active provider (`monoagens/providers/`) with automatic fallback
     (`engine.llm.fallbacks`) and emits progress actions (`send_thinking`, `send_step`, `send_interim`
     with `stream_id = replace_id = "turn:<turn_id>:progress"`).
   - Tool calls enter `tools.execute` (`monoagens/engine/tools/__init__.py`): schema validation $\to$
     RBAC check $\to$ `SafetyPolicy.check` & `SandboxGuard.check` $\to$ `ApprovalStore.create` (if
     `pending`) $\to$ `spec.fn(args, ctx)`.
6. **Review Pipeline & Privacy Guard (`monoagens/engine/turn/review.py`):**
   Draft replies pass through `TurnReview._review`: `character.polish` $\to$ `PrivacyService.review`
   (parking `L4` consent in the subject's private thread if needed) $\to$ `guard.cross_chat_leak`
   (blocking verbatim cross-room quotes).
7. **Single Outbound Door & Gateway Dispatch (`Delivery` $\to$ `Agent.dispatch` $\to$ `BaseGateway`):**
   `Delivery` chunks and checkpoints replies in `OutboxStore`, dispatches each `Action` through
   `Agent.dispatch(action, subject=..., origin=...)`, and the target gateway renders text, media,
   live progress edits (`EditDebouncer`), and interactive prompt buttons (`BaseGateway.prompt_options`).

## 9. Fast Exploration (CodeGraph) and Verification

- **Always Query CodeGraph First:** This repository is indexed in `.codegraph/`. Before running
  manual `grep` or multi-file `read_file` loops to locate symbols, trace call paths, or check blast
  radius, query CodeGraph in one call:
  - MCP tool: `codegraph_explore` (or `codegraph_node` / `codegraph_callers` / `codegraph_impact`).
  - Shell CLI: `codegraph explore "<symbol names or flow>"` (and `codegraph sync` after structural edits).
- **Verification Gate:** Run once after completing code changes:
  - `.venv/bin/ruff check monoagens scripts tests plugins migrations`
  - `.venv/bin/python -P scripts/selfcheck.py --check`
  - `.venv/bin/python -P scripts/types.py --check`
  - `.venv/bin/python -P scripts/adr.py --check`
  - `.venv/bin/pytest -q`
