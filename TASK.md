# TASK

MonoAgens active checklist. Every item is done when the listed surface exists, is covered by tests, and works end to end.

## Active: Audit Remediation (ADR-0001..0008)

Governed by `docs/note.md` (authoritative owner intent) and `docs/audit.md`, `docs/audit2.md`.
Multi-tenancy (`tenant_id`, RLS, composite PKs) is out of scope by decision (ADR-0001).

### Phase P0 — Correctness, Security, and Boot Safety (ADR-0003, ADR-0006, ADR-0007, ADR-0008)

- [ ] **Resume Identity:** `TurnIntake.identify` and `IdentityStore.resolve` short-circuit when actor starts with `per_`; preserve `Percept.hints` on resume (no phantom `people` rows).
- [ ] **Review Before Checkpoint:** `character.polish -> privacy.review -> guard.cross_chat_leak` executed before `checkpoint("reviewed")`; `safety` and `research` prompt blocks pinned.
- [ ] **Exit Door Authorization:** `Delivery._door` requires explicit `origin: Address` without fallback to `action.address`; strict mode passes `declared_class`; approvals store `origin_gateway` and `origin_thread`.
- [ ] **Send Idempotency:** Deterministic `Action.id = f"turn:{turn_id}:tool:{step_seq}"` for all tool sends; turn reaper matches `scope_key LIKE 'turn:{id}%'`; tool aliases resolved before execution.
- [ ] **Wiring Bug Fixes:**
  - `cognition.py:1023`: Call `propose(agent, manifest, thread_id=thread)`.
  - `disputes.py:466`: Fix `promote_staged` to match `source_message_id`.
  - `people.py:103`: Check whether `administrator` role has users, not target role.
  - `__main__.py:1046`: Fix `monoagens config` with no subcommand.
- [ ] **Guard & Plugin Hardening:**
  - `gateways/harness/tools/run.py`: Declare `scan=("command",)`, `target="process"`, `destructive=True`.
  - `guard/policy.py` & `sandbox.py`: Add `timeout`, `flock`, `setsid`, `script`, `env -S` to wrappers; reject `${IFS}` and brace expansion; restrict file read heads to workspace.
  - `plugins.py`: Include `deps/` in manifest digest or isolate imports; validate `workspace` subpath on removal.
  - `identity.py`: Rate limit `/link` attempts; refuse merging users holding roles in `roles.yaml`.
- [ ] **Container & Install Safety:** Copy `migrations/` in `Dockerfile`; run `db upgrade` and `people add` before container start in `install.sh`; handle `EBUSY` on bind-mounted config writes.
- [ ] **Hygiene & CI Gate:** Cut `turn/loop.py` under 800 lines; replace SQLAlchemy JSONB `-` operator in `history.py:179`; replace deprecated `datetime.utcnow()`; reject unknown config keys at load.

### Phase P1 — Cross-Gateway, Event Loop, and Unified Memory (ADR-0002, ADR-0003, ADR-0004)

- [ ] **Core Cross-Gateway Tools (ADR-0004):**
  - Implement `send_message`, `send_file`, `thread_history` in `monoagens/engine/tools/messaging.py`.
  - Implement `GatewayCore.normalize_address(raw) -> str` across all gateways.
  - Introduce `AttachmentStore` with canonical `att_...` handles for cross-gateway forwarding.
  - Remove gateway-bound send tools from `monoagens/gateways/whatsapp/tools/`.
- [ ] **Non-Blocking Async Event Loop:**
  - Offload all synchronous SQLAlchemy queries, `AffectStore` locks, and CPU MiniLM encodes to `await io.run(...)`.
  - Dedicated connection pool for lease renewal and send gate; eliminate double-checkout in `MemoryStore._fence`.
- [ ] **Atomic Lease Fencing:** Convert check-then-act to atomic SQL generation predicates across `turns`, `skills`, `cognition_jobs`, and `automations`; use database server clock (`clock_timestamp()`).
- [ ] **Unified PostgreSQL Memory (ADR-0002):**
  - Add `embedding vector(384)` (HNSW) and `search_tsv tsvector` (GIN) directly to `memories` table.
  - Replace `MemoryStore.search` with native hybrid search (Cosine similarity + tsvector OR-match with RRF).
  - Retire and delete `mem0ai`, `monoagens/engine/memory/mem0.py`, and `mem0_history.db`.
  - Consolidate `supersede:` candidates across all scopes (`person:*`, `thread:*`, `global`).
- [ ] **High-Precision Detectors (ADR-0002 §4.3):**
  - Replace substring `word in body` with whole-token boundary matching (`\b`) in `research.py`, `patterns.py`, and `evidence.py`.
  - Value-shaped secret filtering (distinguish token values from mentions like "token listrik" or "max_tokens").
  - Narrow research triggers and sanitize ISO date / polite phrase triggers in privacy classification.

### Phase P2 — Simplification and Modular Architecture (ADR-0005, ADR-0006, ADR-0008)

- [ ] **Eliminate `__getattr__` Delegation:** Replace `TurnRunner`, `Delivery`, `TurnIntake`, `TurnSteps`, `TurnCommands` with explicit `TurnRuntime` and typed `TurnState` stages.
- [ ] **Vendor Engine Replacement:** Replace 4,855-line `monoagens/vendor/emotion_engine.py` with ~200-line pure PAD arithmetic module in `monoagens/engine/stores/affect.py`.
- [ ] **RBAC & Persona Clean Separation:** Rename `role`/`roles` in `soul.yaml` to `mode`/`modes`; evaluate fallback across all assigned roles in `RoleRegistry.policy_for`; drop dead `people.role` column.
- [ ] **Extension DX (ADR-0005):** Provide `BaseGateway` with safe defaults; enforce `access_refusal` in core; support local unverified plugins for development.
- [ ] **One Job Engine (ADR-0005 §4.1):** Unify tasks, automations, and recipes under one common queue contract.

## Governance & Repository Invariants

- [x] ADRs are English-only; translation ceremony removed (ADR-0008).
- [x] Pre-release `README.id.md` removed; `AGENTS.md` pruned to timeless invariants.
- [x] `scripts/adr.py --check` passing across all active ADRs.
