# ADR-0003: Turn Lifecycle, Primary Lease, and Crash Safety — Atomic Fencing and Idempotent Resume

- **Status:** accepted
- **Date:** 2026-11-05
- **Deciders:** Muqsith (owner)
- **Context:** Re-baselined for v0.1.0; consolidates crash safety, lease management, turn lifecycle, and turn isolation (pre-rebaseline 0011, 0012, 0013, 0017) with the audit remediation specifications of pre-rebaseline 0049 and 0053.
- **Supersedes:** pre-rebaseline 0011, 0012, 0013, 0017, 0049
- **Related:** ADR-0001 (sovereign invariants), ADR-0002 (memory), ADR-0004 (tools)
- **Implementation:** partial — turn checkpoints and base lease exist; atomic CAS fencing across all stores and phantom identity fix are scheduled in Phase P0/P1.

---

## 1. Context

The turn lifecycle coordinates message intake, thread locking, decision making, tool loops,
and delivery. The architectural audits (`docs/audit.md` §1.3.2, §3.3) identified critical reliability
and concurrency defects:
1. **Phantom Identity on Resume:** When a crashed turn was resumed by the reaper, `TurnIntake.identify`
   passed internal `per_...` IDs to `IdentityStore.resolve`, creating a new `people` row for every resume,
   stripping admin permissions, and disconnecting user memory.
2. **Review Bypass on Resume:** `checkpoint("drafted")` was recorded before `privacy.review`, causing
   resumed turns to send unreviewed draft text directly to chat.
3. **Send Non-Idempotency:** Tool-generated actions used random `uuid4()`, and turn chunk status
   `intent -> unknown` queried `scope_key == "turn:{id}"` while chunks were keyed as `"turn:{id}:{index}"`,
   causing duplicate deliveries upon restart.
4. **Partial Lease Fencing:** `LeaseGuard` was only wired to `agent.memory`. Stateful updates to `turns`,
   `skills`, and `automations` ran without generation fencing, creating split-brain race conditions.
5. **Event Loop Starvation:** Synchronous database transactions and MiniLM embeddings running directly
   inside `async def` starved the lease renewal heartbeat, causing healthy primaries to lose their lease.

## 2. Decision drivers

1. **Crash Durability:** A turn interrupted by SIGKILL, power loss, or network drop must resume cleanly
   without duplicate side effects or identity corruption.
2. **Atomic Fencing:** Every stateful write must verify the primary lease generation token in the same
   atomic SQL transaction.
3. **Strict Review Gate:** Unreviewed drafts must never be delivered; review stages must execute before
   the final delivery checkpoint.
4. **Deterministic Side Effects:** All outbound actions must carry deterministic idempotency keys.
5. **Non-Blocking Loop:** Async paths must offload all blocking DB operations and CPU models to `io.run`.

## 3. Options considered

**A. Coroutine Resume in Memory.** Rejected: loses all in-flight work upon process restart.

**B. External Workflow Engine (Temporal/Cadence).** Rejected: introduces heavyweight external
infrastructure dependencies for a single-tenant agent harness.

**C. PostgreSQL Transactional Ledger & Checkpointed State Machine.** Selected: leverages existing
PostgreSQL infrastructure with atomic checkpoints and durable outbox tables.

## 4. Decision

### 4.1 Turn Intake and Identity Continuity

- `TurnIntake.identify` and `IdentityStore.resolve` short-circuit when receiving an identifier starting
  with `per_`, returning the canonical ID immediately without creating new rows.
- The `turns` table persists inbound `hints` (`dm`, `mention`, `reply`, `app`) to allow full `Percept`
  reconstruction upon reaper resume.

### 4.2 Checkpoint Pipeline and Review Ordering

The turn execution pipeline strictly adheres to the following stages:
1. `intake`: Atomic transaction persisting inbound thread, message, and initial turn row.
2. `decide`: Model decision on whether to reply, ignore, or perform ambient updates.
3. `act`: Iterative tool execution loop with deterministic step recording.
4. `review`: `character.polish -> privacy.review -> guard.cross_chat_leak` executed sequentially.
5. `checkpoint("reviewed")`: Persisted ONLY after all review and leak checks pass.
6. `deliver`: Chunks dispatched to transport; resumed turns re-execute review if resuming prior to
   `reviewed` status.

### 4.3 Deterministic Action Idempotency and Outbox Reconciliation

- Outbound tool actions receive deterministic IDs: `id = f"turn:{turn_id}:tool:{step_seq}"`.
- Outbox transitions for interrupted turns query prefix matching: `scope_key LIKE f"turn:{turn_id}%"`.
- A turn reaper reconciles actions left in `intent` state longer than the turn deadline to `unknown`.

### 4.4 Atomic Lease Generation Fencing and Server Clock

- Atomic CAS generation fencing is applied across all stateful stores (`turns`, `skills`, `memories`,
  `automations`) using SQL clauses: `WHERE generation = :current_generation`.
- Expiration calculations across `turns`, `cognition_jobs`, and `automation_runs` use the database
  clock (`clock_timestamp()`) to prevent container clock skew.
- Primary heartbeat renewal runs on an isolated dedicated database connection pool.

## 5. Configuration

Configured in `config.yaml`:
```yaml
engine:
  turn:
    timeout_seconds: 300
    lock_timeout_seconds: 15
    thread_chars: 9000
```

## 6. Metrics and gates

- Crash stress tests (SIGKILL during LLM generation and post-dispatch) verify exactly-once message delivery.
- Resume tests verify the `per_` actor ID remains identical and no phantom `people` rows are created.
- Fencing tests verify an expired primary process receives SQL rejection on subsequent write attempts.

## 7. Acceptance criteria

- [ ] Resumed turns retain original actor identity and permissions.
- [ ] No unreviewed draft is delivered to chat following a turn resume.
- [ ] Tool deliveries emit deterministic IDs preventing transport replay.
- [ ] Database transactions and MiniLM encodings on turn paths run via `await io.run()`.

## 8. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Strict deadline cuts off long reasoning | Configurable turn timeout (300 s); long work offloaded to durable jobs (ADR-005) |
| Connection pool exhaustion blocks lease | Dedicated reserve pool for lease heartbeat and primary send gates |

## 9. Interaction with other ADRs

- **ADR-0001:** Binds primary process election to the single `engine_lease` row.
- **ADR-0004:** Ensures tool execution steps are durably recorded before network transport.

## 10. Code references

- `monoagens/engine/turn/loop.py` — Turn execution runner and checkpoint loop.
- `monoagens/engine/turn/intake.py` — Inbound percept intake and identity resolution.
- `monoagens/engine/turn/delivery.py` — Delivery door and chunking ledger.
- `monoagens/engine/jobs/lease.py` — Atomic CAS lease fencing.

---

*This ADR lives at `docs/v0.1.0/adr/engine/0003-turn-lifecycle-lease-and-durability.md`.*
