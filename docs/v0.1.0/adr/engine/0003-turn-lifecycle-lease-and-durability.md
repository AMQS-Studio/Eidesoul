# ADR-0003: Turn Lifecycle, Primary Lease, and Crash Safety — Atomic Fencing, Non-Blocking Loop, and Idempotent Resume

- **Status:** accepted
- **Date:** 2026-11-05
- **Deciders:** Muqsith (owner)
- **Context:** Re-baselined for v0.1.0; consolidates crash safety, lease management, turn lifecycle, prompt budgets, and async concurrency (pre-rebaseline 0011, 0012, 0013, 0017, 0037, 0045, 0049, 0053).
- **Supersedes:** pre-rebaseline 0011, 0012, 0013, 0017, 0045, 0049, 0053
- **Related:** ADR-0001 (sovereign invariants), ADR-0002 (memory), ADR-0004 (tools), ADR-0008 (turn pipeline)
- **Implementation:** implemented — checkpointed turn lifecycle, atomic SQL lease fencing across stores, review before the `reviewed` checkpoint, non-blocking loop with progress streams, and idempotent resume with deterministic action ids.

---

## 1. Context

The turn lifecycle coordinates message intake, thread locking, context assembly, model execution,
review gates, and transport delivery. End-to-end verification uncovered critical defects in crash
recovery, concurrency, and prompt assembly:
1. **Phantom Identity on Resume (`BI-01, ST-02`):** When the supervisor/reaper resumed an interrupted
   turn, it built `Percept(actor=row["person_id"], hints={})` containing the internal `per_…` ID.
   `TurnIntake.identify` passed `per_…` to `IdentityStore.resolve`, which looked up `person_links.address = 'per_…'`,
   failed to find a transport link, and minted a brand-new `people` row (`per_…`) on every resume—stripping
   `administrator` roles to `guest`, disconnecting personal memories, and losing `Percept.hints`.
2. **Review Bypass on Resume (`ST-03`):** `turns.checkpoint("drafted")` was written before
   `character.polish`, `privacy.review`, and `guard.cross_chat_leak`. Resuming a turn from `"drafted"`
   skipped all privacy and cross-chat leak checks, sending raw unreviewed drafts directly to chat.
3. **Four Send Idempotency Holes (`ID-1..ID-4, M-07, ST-04`):**
   - Tool-generated `Action` objects used random `uuid4().hex` IDs so `outbound_messages` never caught retries.
   - `TurnStore.claim` and reaper converted `intent -> unknown` using `scope_key == f"turn:{id}"`, whereas
     chunks were stored as `f"turn:{id}:{index}"`.
   - `LLM._tools` wrote `turn_steps` only *after* `execute()` returned, and looked up `spec = by_name.get(name)`
     without resolving tool aliases (`kirim_ke`, `kirim_file`), recording `approval_class="none"` and
     disabling replay protection.
   - `outbox.begin()` and `outbox.mark_sent()` ran in separate transactions around `body.dispatch()`.
4. **Prompt Salience Drops & Window Budget Leaks (`BI-06, ST-12`, Wall 2 & 3):**
   - `PromptPart("safety", ...)` (`CRISIS_SAFETY`) and `PromptPart("research", ...)` (`ResearchCard`) had
     `pinned=False`, and `config.prompt.pinned_blocks` was ignored, causing `salience.select` to drop
     crisis safety instructions under prompt budget pressure.
   - `assembly._trim` only counted `len(entry.text)`, ignoring quote text (120 chars), media lines, and
     inline text attachments (up to 6,000 chars per message) in `thread_chars`.
   - `character.polish` did not verify that numbers and dates in the draft remained unchanged after rewrite.
5. **Event Loop Starvation & Pool Deadlock (`ST-05`):** ~25 synchronous SQLAlchemy transactions and
   MiniLM encodings ran directly inside `async def` without `await io.run()`. When 8 IO workers ran
   `MemoryStore._fence`, each held one connection while `self._guard.require()` checked out a second
   connection ($8 \times 2 = 16 > 15$ max connections), blocking `Supervisor.serve`'s synchronous
   `lease.renew()` on the main event loop for 30s until the primary lost its lease.
6. **Partial Lease Fencing, Reaper Races, and Clock Skew (`BI-15, BI-16, ST-17`):**
   - `LeaseGuard` was only attached to `agent.memory` (via non-atomic check-then-act). `TurnStore`,
     `SkillStore`, `AutomationStore`, `CognitionQueue`, `AffectStore`, `Ambient`, and tool execution
     wrote without generation fencing; `AutomationStore.interrupt` cancelled all claimed rows globally.
   - `AutomationStore.reap` updated expired rows without `AND status IN ('claimed', 'running')`,
     re-queuing jobs that finished between `SELECT` and `UPDATE`.
   - `CognitionQueue.claim` used a 60s lease against a 120s LLM timeout, causing duplicate LLM runs.
   - Claim expiry mixed application `time.time()` with database `clock_timestamp()`.
7. **Runtime State Bugs, Swallowed Interim Progress, & Sampling Mismatches (`BI-07, BI-08, BI-11, BI-12, BI-17, M-04, M-05`):**
   - `LLM.run` checked `deadline is None` before applying `run_budget_seconds` (dead code), and its
     HTTP 4xx image retry overwrote `messages[-1]` even when `messages[-1]` was a `role: "tool"` message.
   - When the model produced both conversational prose (e.g. *"Let me check that first..."*) and `tool_calls`
     in the same round (`llm.py:603`), `LLM.run` overwrote `last_text` and swallowed the prose until the
     final round, and dropped model `thinking` / reasoning blocks instead of emitting progress events to
     the gateway.
   - `LLM.complete()` (`llm.py:668`) unconditionally passed `"temperature": float(temperature)` without
     checking `self.config.llm.sampling_supported` (breaking reasoning models that reject `temperature`),
     while `automation_worker.py` and `guard/sandbox.py` hardcoded `temperature=0.0`/`0.4` instead of using
     `config.llm.utility_temperature`.
   - `AffectStore` serialized all parallel turns on a single `SELECT ... FOR UPDATE` row (`state.key='emotion'`)
     and scanned all `people` rows in `_soothe`.
   - `VibeStore` permanently disabled itself (`self._failed = True`) after a single transient error.
   - `/reload` discarded `soul.yaml` changes whenever `voice.*` keys changed and left cached `soul`/`language`/`tz`
     stale on `MemoryStore`, `AffectStore`, and `StyleStore`.
   - `stores/history.py:179` used deprecated SQLAlchemy JSONB `-` subtraction (342 warnings), and tests/gateways
     (`gateways/cli/gateway.py:117`) used naive `datetime.now()` / `datetime.utcnow()` timestamps.

## 2. Decision drivers

1. **Crash Durability:** Any turn interrupted by SIGKILL, crash, or restart must resume with the exact
   same actor identity, permissions, and hints, delivering each chunk and tool action at most once.
2. **Strict Review Gate:** No unreviewed draft may ever reach a transport; polish, privacy review, and
   cross-chat leak checks must precede the `reviewed` checkpoint.
3. **Atomic Lease Fencing:** Every stateful store and side-effecting operation must verify the primary
   `engine_lease.generation` atomically using the PostgreSQL server clock.
4. **Non-Blocking Async Event Loop:** Zero synchronous DB transactions or CPU model encodings inside
   `async def` without `await io.run()`, backed by a dedicated lease heartbeat connection pool.
5. **Honest Context and Prompt Budgets:** Crisis safety and research cards are pinned, `thread_chars`
   accounts for all inline content, and character polish preserves factual numbers and dates.

## 3. Options considered

**A. In-Memory Coroutine Retry Only.** Rejected: loses turns and duplicates sends across container restarts.

**B. External Workflow Cluster (Temporal/Cadence).** Rejected: heavyweight operational footprint for a
self-hosted single-deployment agent.

**C. PostgreSQL Checkpointed State Machine with Atomic CAS Fencing and Dedicated Heartbeat Pool.** Accepted.

## 4. Decision

### 4.1 Turn Intake, Identity Continuity, and Percept Hints

- `TurnIntake.identify` and `IdentityStore.resolve` short-circuit immediately when given an identifier
  starting with `per_` that exists in `people`, returning it unchanged without creating `people` or
  `person_links` rows.
- The `turns` table persists inbound `hints` (`dm`, `mention`, `reply`, `app`) at intake so
  `Supervisor` reconstructs the complete `Percept` upon turn resume.

### 4.2 Checkpoint Ordering, Prompt Pinning, and Window Budgets

- Turn review (`character.polish -> privacy.review -> guard.cross_chat_leak`) always executes before
  `turns.checkpoint("reviewed")`. Resuming from `"drafted"` re-runs the full review chain; delivery
  only ever dispatches `reviewed` text (`skip_review = step in {"reviewed", "sending"}`).
- `PromptPart("safety", ...)` and `PromptPart("research", ...)` in `turn/prompts.py` are set to
  `pinned=True`, and `config.prompt.pinned_blocks` is honored by `salience.select`.
- `assembly._trim` counts quote text, media description lines, and inline text attachment bodies against
  the `thread_chars` budget.
- `character.polish` validates that all numeric and date tokens from the draft are preserved in the
  polished output, rejecting the rewrite and keeping the draft if any number or date drifts.

### 4.3 Deterministic Action Idempotency and Outbox Reconciliation

- Tool-created outbound `Action` objects use deterministic IDs: `id = f"turn:{turn_id}:tool:{step_seq}"`.
- In `LLM._tools`, tool aliases are resolved before inspecting `spec.approval_class` and `spec.target`
  (`spec = by_name.get(name) or next((s for s in by_name.values() if name in s.aliases), None)`), and
  `turn_steps` records the step *before* executing any non-idempotent tool.
- `TurnStore.claim`, reaper, and startup reconciliation match prefix `scope_key LIKE f"turn:{id}%"`
  (covering both reply chunks `turn:{id}:{i}` and tool actions) to transition stale `intent` rows older
  than the turn deadline to `unknown`.

### 4.4 Non-Blocking Event Loop, Pool Sizing, and Atomic Lease Fencing

- Every synchronous SQLAlchemy block, `embed.encode()` call, and `privacy_evidence.classify()` call
  reachable from an `async def` is offloaded via `await io.run(...)`.
- `Lease.renew`, `lease.frozen`, and `Agent._send_gate` use a dedicated small connection pool (1–2
  connections) isolated from turn/cognition worker pools.
- `MemoryStore._fence` verifies the lease generation within the *same* database transaction connection
  (eliminating double connection checkouts), and `engine.db` sets `max_overflow` explicitly (default `0`
  with `pool_size` sized for `io.workers`), while CLI admin commands share a compact pool.
- Atomic SQL generation fencing (`WHERE EXISTS (SELECT 1 FROM engine_lease WHERE id = 1 AND generation = :gen)`)
  is enforced across `MemoryStore`, `TurnStore.finish/interrupt/checkpoint`, `SkillStore.save`,
  `AutomationStore`, `CognitionQueue._set`, `AffectStore`, `Ambient`, `outbox.mark_sent`, and
  side-effecting tool execution.
- `AutomationStore.reap` includes `AND status IN ('claimed', 'running')`; `AutomationStore.interrupt`
  filters by `claimed_by` and `generation`.
- `CognitionQueue` claims renew their heartbeat during execution, use `lease_seconds > llm.timeout_seconds + 60`,
  and all expiration checks across `turns`, `cognition_jobs`, and `automation_runs` use the database
  server clock (`clock_timestamp()`).

### 4.5 LLM Budget, Thinking & Interim Stream, Sampling Support, Affect Concurrency, and Reload

- **Thinking, Step, Interim, and Live-Stream / Barge-In Contract:**
  - During multi-round execution in `LLM.run`, the core emits structured progress actions to the origin
    gateway: `send_thinking` (carrying the round's extracted model `thinking` block), `send_step`
    (carrying tool name, round index, and start/done status), and `send_interim` (carrying LLM-generated
    transitional prose when `response.text` accompanies `response.tool_calls`, rather than swallowing
    `response.text`).
  - `Action` carries optional `stream_id: str = ""`, `replace_id: str = ""`, and `meta: Mapping[str, object] = {}`
    ( mirrored on `LLMResponse.meta`), allowing gateways that support live message editing (Discord,
    Telegram, Web) or real-time voice/TTS streaming to update in-place without changing `GatewayCore.dispatch`.
  - `TurnCoordinator` exposes a first-class preemption hook (`interrupt_thread(thread_id, reason)`) paired
    with optional `GatewayCore.abort(thread_id)` so real-time voice calls (*barge-in* when the user speaks)
    or physical robot safety stops can cancel an in-flight turn or actuation immediately and cleanly.
  - Gateways decide independently whether and how to render `send_thinking`, `send_step`, and
    `send_interim` based on their own dynamic gateway configuration.
- **Sampling Capability Enforcement & Utility Temperature:**
  - `LLM.complete()` filters `temperature` through `self.config.llm.sampling_supported` (matching
    `LLM.run._request_kwargs`) so reasoning models that forbid `temperature` never fail on `decide_turn`,
    memory extraction, or guard calls.
  - Background and utility calls (`automation_worker.py`, `guard/sandbox.py`, `cognition.py`) use
    `config.llm.utility_temperature` uniformly instead of hardcoded `0.0` or `0.4` literals, while
    user-facing conversational replies in `LLM.run` use `soul.voice.sampling` (`daily_temperature` /
    `professional_temperature`).
- `LLM.run` enforces `min(deadline, start + run_budget_seconds)` rather than checking `deadline is None`.
- On HTTP 4xx image retry in `LLM.run`, image parts are stripped from the user message in place without
  overwriting `messages[-1]` when `messages[-1]` is a `role: "tool"` message.
- `AffectStore` uses lock-free reads and single compare-and-set writes instead of locking
  `state.key='emotion'` with `FOR UPDATE` across the turn, and `_soothe` updates only active/targeted
  records instead of scanning all `people`.
- `VibeStore` retries with exponential backoff after transient errors instead of setting `_failed = True`
  permanently.
- `/reload` (`turn/reload.py`) preserves `soul.yaml` edits when `voice.*` sampling settings change, and
  propagates updated `soul`, `language`, and `tz` attributes to `MemoryStore`, `AffectStore`, and `StyleStore`.
- JSONB key removal in `stores/history.py` uses `.op("-")` instead of deprecated Python `-` subtraction,
  and all naive `datetime.now()` / `datetime.utcnow()` calls across gateways and stores are replaced with
  timezone-aware `clock.now(timezone)` or `datetime.now(datetime.timezone.utc)`.

## 5. Configuration

Configured in `config.yaml`:
```yaml
engine:
  turns:
    timeout_seconds: 300
    lock_timeout_seconds: 15
    min_call_seconds: 5
```

## 6. Metrics and gates

- Chaos tests (`ST-02, ST-03, ST-04, ST-05, ST-12, ST-17`) verify resume identity preservation, review
  enforcement on resumed drafts, exactly-once chunk/tool delivery after crash, and uninterrupted lease
  heartbeat under 8 parallel memory writers.
- Behavioral async heartbeat test verifies a background ticker never stalls by more than 50ms while a
  turn executes.
- Pytest runs with `-W error::sqlalchemy.exc.SADeprecationWarning` and `-W error::DeprecationWarning`.

## 7. Acceptance criteria

- [ ] Resumed turns keep their original `per_…` actor ID, roles, memory access, and `Percept.hints`.
- [ ] Resuming a turn from `"drafted"` executes `polish -> privacy.review -> cross_chat_leak` before sending.
- [ ] `safety` and `research` prompt blocks are `pinned=True` and never dropped by `salience.select`.
- [ ] All tool sends carry deterministic `Action.id`, resolve aliases before step logging, and record `turn_steps` pre-execution.
- [ ] `Lease.renew` runs via `await io.run()` on a dedicated pool; `MemoryStore._fence` uses a single connection.
- [ ] Atomic `generation` predicates guard writes across `turns`, `memories`, `skills`, `automations`, `cognition_jobs`, and `affect`.
- [ ] `LLM.run` emits `send_thinking`, `send_step`, and `send_interim` progress actions without swallowing transitional LLM prose on tool rounds.
- [ ] `LLM.complete` honors `sampling_supported` and uses `config.llm.utility_temperature` consistently.
- [ ] Zero `SADeprecationWarning` or naive `datetime.now()` / `datetime.utcnow()` calls occur across gateways, stores, and tests.

## 8. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Pinned `safety` and `research` blocks reduce volatile prompt space | Salience trims volatile blocks (`memories`, `skills`, `actions`) after reserving pinned budget |
| Pre-execution `turn_steps` leaves step in `started` on hard crash | Reaper marks interrupted non-idempotent steps as `interrupted` so resume does not replay them blindly |

## 9. Interaction with other ADRs

- **ADR-0001:** Binds lease fencing to the single `engine_lease` (`id = 1`) row and namespaced `turns.id`.
- **ADR-0004:** Uses deterministic step IDs and outbox reconciliation for cross-gateway tool deliveries.
- **ADR-0008:** Implements the turn stages via the typed `TurnRuntime` and `TurnState` pipeline.

## 10. Code references

- `monoagens/engine/turn/loop.py`, `intake.py`, `delivery.py`, `prompts.py`, `assembly.py`, `reload.py`
- `monoagens/engine/jobs/supervisor.py`, `lease.py`, `cognition.py`, `automation_store.py`, `ambient.py`
- `monoagens/engine/stores/turns.py`, `history.py`, `skills.py`, `affect.py`, `vibe.py`
- `monoagens/engine/llm.py`, `monoagens/engine/db.py`, `monoagens/engine/character/character.py`

---

*This ADR lives at `docs/v0.1.0/adr/engine/0003-turn-lifecycle-lease-and-durability.md`.*
