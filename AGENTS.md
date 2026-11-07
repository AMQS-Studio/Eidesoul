# AGENTS

Timeless architectural principles, system boundaries, and high-level invariants for this repository.

## 1. Language and Localization

- **Code and Technical Specs in English:** English is the standard for source code, comments,
  docstrings, log messages, internal error strings, CLI diagnostics, model-facing scaffolding,
  tests, and ADRs.
- **Multi-Language Surfaces:** User-facing documentation (`README`, guides, tutorials), runtime
  localization catalogs (`monoagens/lang/<code>.yaml`), and `soul.yaml` persona content may be
  authored in multiple languages.
- **No Hardcoded Chat Strings:** All user-facing chat replies and notices must resolve through the
  localization catalog (`t()` in `monoagens/engine/i18n.py`) rather than hardcoding chat text in
  Python code.

## 2. Purpose and Hexagonal Boundaries

- **Autonomous Agent Harness:** The engine is a pure Python (`asyncio`) harness maintaining one
  continuous identity (soul), persistent hybrid memory, tool orchestration, and role-based access
  control.
- **Sacred Core (`monoagens/engine/`):** Core must remain transport-agnostic and UI-agnostic. It
  must never import gateway packages, transport SDKs, or construct platform-specific UI widgets.
- **Appendage Seam (`monoagens/engine/inbound.py`):** Gateways (`monoagens/gateways/`) connect to
  the core exclusively via transport-neutral contracts (`Percept`, `Address`, `Action`, `Receipt`,
  `GatewayCore`, and optional capability protocols). Gateways own address normalization, platform
  rendering, and interactive prompt presentation.
- **Provider Seam (`monoagens/providers/`):** LLM and speech provider adapters handle wire protocols
  and streaming deltas. Core logic never branches on provider names; capabilities are declared via
  provider defaults and configuration.
- **Async Event Loop Integrity:** Async execution paths (`async def`) must never block on
  synchronous database queries, CPU-bound embeddings/NLP, or blocking I/O. Offload synchronous work
  through the bounded executor (`await io.run(...)`).
- **Operator-Controlled Models and Limits:** Model selection, fallbacks, and numeric budgets come
  from operator configuration (`config.yaml`), never hardcoded model IDs.

## 3. Sovereign Storage and Process Invariants

- **Dedicated Sovereign Topology:**
  `1 Deployment = 1 Soul = 1 PostgreSQL Database = 1 Primary Process`.
- **Single Primary Process (Lease Fencing):** A single primary process holds the database lease
  (`engine_lease`). Mutating stores verify the lease fence; instances without the lease remain in
  standby and perform zero database writes.
- **Single System of Record:** PostgreSQL (with `pgvector` and `pg_trgm`) is the sole canonical
  data store. All schema changes go through the linear Alembic migration chain in `migrations/`.
- **Authoritative Host Configuration:** Operator files on disk (`config.yaml`, `roles.yaml`,
  `soul.yaml`, `.env`) are the source of truth; database document rows act only as derived caches.
  Secrets live exclusively in `.env`.

## 4. Memory, Privacy, and Isolation

- **Unified Hybrid Memory:** The `memories` table in PostgreSQL serves as both the canonical store
  and the hybrid retrieval index (semantic vector similarity + full-text search + salience).
- **Single Write and Read Ports:** All canonical memory writes pass through `MemoryStore`; prompt
  retrieval reads exclusively through the `memories_readable` view.
- **Layered Privacy (`L0–L4`) and Secret Rejection (`L5`):** Privacy evidence is evaluated
  deterministically before storage. Secret credentials (`L5`) are rejected. The subject of a
  personal memory retains veto power, and disclosing sensitive (`L4`) memories in shared contexts
  requires subject consent.
- **Strict Scope and Cross-Room Isolation:**
  - Automatic prompt memory retrieval is scoped strictly to `global` + `person:<sender>` +
    `thread:<active_thread>`.
  - Cross-thread history reads require proven room membership (fail-closed) and are guarded against
    verbatim cross-room leakage.

## 5. Security, Authorization, and Execution

- **Single Outbound Door:** Every outbound message, file, or side-effecting transport action must
  pass through `Agent.dispatch()` with an explicit `origin: Address` (never defaulting `origin` to
  the target address).
- **Fail-Closed RBAC:** Permissions are governed strictly by `roles.yaml`. Unknown actors or
  undeclared permissions are denied by default. `administrator` is the protected top role.
- **Safety Policy, Sandbox, and Approvals:** Sensitive tool calls (shell/container execution,
  settings mutations, destructive writes, and cross-room actions) are gated by `SafetyPolicy`,
  rootless container isolation, and explicit user approval (`/approve`).
- **Idempotent Outbound Actions:** Tool-emitted actions carry deterministic IDs recorded in the
  outbox ledger so resumed turns never duplicate external side effects.

## 6. High-Level Architecture Overview

1. **Ingress:** Gateways normalize platform events into `Percept` objects and pass them to
   `Agent.handle()`.
2. **Access & Coordination:** The access layer resolves caller identity and role permissions;
   commands execute directly while conversational messages enter the per-thread turn coordinator.
3. **Context & Memory Assembly:** The turn runner builds the stable/volatile system prompt (`soul`,
   skills, style, workspace) and retrieves scoped hybrid memories.
4. **Model & Tool Loop:** The LLM client streams responses and tool calls through the single tool
   execution door (schema validation $\to$ RBAC $\to$ safety/sandbox check $\to$ approval gate $\to$
   execution).
5. **Review & Outbound Delivery:** Draft replies pass through character polish, privacy consent
   review, and cross-room leak checks before chunked, outbox-checkpointed dispatch through
   `Agent.dispatch()` back to the target gateway.

## 7. Code Exploration and Verification

- **CodeGraph First:** Because implementation details evolve, do not rely on hardcoded file lists.
  When `.codegraph/` is present, use `codegraph_explore` (MCP) or `codegraph explore "<query>"`
  (CLI) first to inspect current symbol definitions, call paths, and blast radius in one call.
- **Verification Gate:** Keep the test suite and static checks clean after changes:
  - `.venv/bin/ruff check monoagens scripts tests plugins migrations`
  - `.venv/bin/python -P scripts/selfcheck.py --check`
  - `.venv/bin/python -P scripts/types.py --check`
  - `.venv/bin/python -P scripts/adr.py --check`
  - `.venv/bin/pytest -q`
