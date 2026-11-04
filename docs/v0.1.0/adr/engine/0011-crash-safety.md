# ADR-0011: Crash Safety & Durability: Transactional Queue, Job Claim, Neat Shutdown

- **Status:** accepted
- **Implementation:** implemented — evidence: transactional intake (`HistoryStore.intake` writes messages+turns+memory candidates+events+`cognition_jobs`), claim with `FOR UPDATE SKIP LOCKED`, reaper, backoff + dead-letter, `outbound_messages` ledger, phased shutdown, recovery, `tests/test_cognition.py`, `tests/test_turn_shutdown.py`; gate result: `pytest -q` 798 passed
- **Date:** 2026-11-01
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Context:** follow-up to ADR-0001 (canonical), ADR-0002 (capture), ADR-0003 (worker C/D), ADR-0004 (labeling fallback)
- **Supersedes:** —
- **Related:** ADR-0012 (lease safety), ADR-0013 (turn lifecycle & resume)
- **Location note:** ADR lives in `docs/v0.1.0/adr/engine/`; the Indonesian copy is `docs/v0.1.0/adr/engine/id/0011-crash-safety.md`.
- **Amendments:** 2026-11-02 — normalization of the skeleton (ADR-0009 §4.4): added the Interaction with other ADRs section; Code references shifted to §10. 2026-11-03 — audit: the range `supervisor.py:36-140` was corrected to `36-129`. 2026-11-03 — audit: the job claim time uses `clock_timestamp()` (matching ADR-0012), and the ledger uses `scope_key` so that automation deliveries are covered.

---

## 1. Context

- Messages are stored synchronously in `messages`, but memory capture uses `spawn()` fire-and-forget with no retry, status, or dead-letter (`agent.py:917-921`).
- There is no durable queue: if the process dies, memory work is lost; if the worker dies after the claim, the job hangs.
- Scheduled work does not survive a restart: `answered`, `backlog`, `quiet` live in memory (`agent.py:145-147`).
- When the DB is in trouble, `WorkingMemory.append` catches the error and continues (`context.py:210-225`): the message exists only in memory and is then lost — a fail-open that contradicts the "store" promise.
- The `shell` command uses `create_subprocess_shell` without a process group (`tools/shell.py:13-18`); child processes can be orphaned during shutdown.
- The lease is released on `deactivate()`, but a crash does not give it that chance — recovery depends on the TTL and the safety of takeover (ADR-0012).

## 2. Decision drivers

1. **No job is lost** when the process dies, as long as the DB is healthy.
2. **No double processing** when two runners work.
3. **Automatic recovery** after a restart, with no manual intervention.
4. **Observability**: backlog, dead-letter, and failures are visible.
5. **Simplicity**: one job table, one write door.
6. **Neat shutdown** within the orchestrator's time budget.

## 3. Options considered

| Options | Shapes | Excess | Lack | Status |
|---|---|---|---|---|
| **A. Status quo** | `spawn()` per job | No changes | Lost silently; no retry/status | Rejected |
| **B. Queue without a claim** | Queue table, one worker | Simple | Two runners can process the same item; no lease/reaper | Rejected |
| **C. Transactional queue + claim + ledger + neat shutdown** | Enqueue in one transaction; `SKIP LOCKED`; lease/reaper; outbound ledger; fail-fast when the archive fails | Survives crashes; idempotent; visible | Adds tables, workers, reapers, metrics | **Selected** |

## 4. Decision

### 4.1 Enqueue transactional

One transaction writes:

```
messages (incoming)
turns (status='received') -- ADR-0013
memory_events (kandidat pasca hard exclusion)
memories (state='pending', level NULL) -- ADR-0004
cognition_jobs (kind='capture',...) -- ADR-0003
```

If the transaction fails, the turn is **not** processed. There is no fail-open for the archive: better to fail fast and let the transport retry than to claim "saved" when it is not.

### 4.2 Claim job

```sql
SELECT... FROM cognition_jobs
WHERE status='queued' AND next_attempt_at <= clock_timestamp()
ORDER BY created_at
FOR UPDATE SKIP LOCKED
LIMIT:n
```

- `claimed_by`, `lease_until` are set on claim.
- **Reaper**: `lease_until` passed → `queued` again.
- Retry with backoff; after `dead_letter_after` → status `dead` + alert.
- `idempotency_key` unique per job; canonical writes via `MemoryStore` (upsert).

### 4.3 Delivery ledger

`outbound_messages(scope_key, chunk_index, gateway, thread_id, provider_id, status)` — status `intent|sent|unknown`; `scope_key` = `turn:<id>` or `automation:<run_id>`, so an automation delivery uses the same idempotency path (detail in ADR-0013).

### 4.4 Neat shutdown

```
running → draining → noticed → stopped
```

1. Stop intake; `engine_control.shutting_down = true`.
2. Drain: a turn in flight is given `drain_seconds` to finish.
3. Notice: a chat with an unfinished turn receives one notice (ADR-0013).
4. Workers stop taking new jobs; a job in flight is finished or returned to the queue.
5. Release the lease; the process exits.

Limits: `grace_seconds > drain_seconds + notice_seconds` and `grace_seconds < stop_grace_period` of the orchestrator. This validation is **fail-fast at boot**, not silently truncated.

### 4.5 Recovery on start

- Once the lease is acquired (ADR-0012), run a recovery pass: claim turns/jobs whose `lease_until` has passed.
- A job is returned to the queue by the reaper; a turn is resumed by ADR-0013.

### 4.6 Process hygiene

- `shell`/subprocess: `start_new_session=True` + kill the process group on shutdown.
- Sandbox: containers are labeled (`monoagens.worker`, `monoagens.turn`) for cleanup; `--rm` stays.
- Workers do not block the event loop: DB and MiniLM go through a thread/executor.

### 4.7 What is not durable and must be decided

| Artefacts | Decision |
|---|---|
| `quiet` (`/stop`) | Persist to the DB (`state` per chat) |
| `answered` (dedupe burst) | Persist as part of `turns` (unique `message_id`) |
| `backlog` (away count) | Derive from `messages`/`turns`, not in memory |

## 5. Configuration

```yaml
worker:
 retry: { attempts: 5, backoff_seconds: 2, dead_letter_after: 5 }
 reaper: { every_seconds: 30, batch: 100 }
 queue_cap: 5000
turns:
 shutdown:
 grace_seconds: 25
 drain_seconds: 15
 notice_seconds: 5
 notice_per_chat: true
 notice_retry: { attempts: 2, backoff_seconds: 1 }
```

## 6. Metrics and gates

| Metric | Target |
|---|---|
| A job lost during a crash | 0 |
| Double processing per item | 0 |
| Dead-letters per day | approaching 0 |
| `grace_seconds` vs `stop_grace_period` | boot validation fails if violated |
| Child processes orphaned after shutdown | 0 |

## 7. Acceptance criteria

- [ ] Memory/turn enqueued in one transaction with the message; no fail-open archive.
- [ ] A job is claimed with `SKIP LOCKED` + lease; the reaper returns dead claims.
- [ ] The idempotency key prevents duplicates on replay.
- [ ] The outbound ledger prevents resending chunks.
- [ ] Shutdown follows the phases and obeys the `grace_seconds` limit.
- [ ] Validation `grace_seconds < stop_grace_period` fails at boot.
- [ ] `quiet`/`answered`/`backlog` are persisted or removed from the design.
- [ ] Subprocesses are killed per process group; containers are cleaned up.
- [ ] Backlog status, dead-letters, and stale claims are visible in `/status`.

## 8. Risks and mitigations

| Risks | Mitigation |
|---|---|
| DB down at enqueue | Fail-fast; the turn is not processed; the transport retries |
| Reaper too aggressive | `lease_until` is long enough; re-claiming is idempotent |
| Incomplete ledger | Status `unknown` + `skip`/`resend` policy (ADR-0013) |
| Shutdown truncated by the orchestrator | Config validation at boot; shutdown duration metric |
| Queue piles up | Backpressure + degrade mode + alert (ADR-0003) |

## 9. Interaction with other ADRs

- **ADR-0001** — canonical: canonical writes go through `MemoryStore` (upsert) on job claim (§4.2).
- **ADR-0002** — capture: memory capture that used to be `spawn()` fire-and-forget (§1) is now a transactional queue job.
- **ADR-0003** — the job queue and the same job claim: `cognition_jobs(kind='capture')` is written in the enqueue transaction (§4.1), claimed via `SKIP LOCKED` + lease (§4.2), with backpressure/alert when the queue piles up (§8).
- **ADR-0004** — labeling fallback: memory candidates are written as `memories(state='pending', level NULL)` in the enqueue transaction (§4.1).
- **ADR-0012** — lease safety: recovery depends on the TTL and the safety of takeover; the lease is acquired before the recovery pass (§4.5).
- **ADR-0013** — turn lifecycle & resume: `turns(status='received')` at enqueue (§4.1), delivery ledger (§4.3), shutdown notice (§4.4), turn resume in the recovery pass (§4.5), and the ledger `skip`/`resend` policy (§8).

## 10. Code references

- `monoagens/engine/agent.py:917-921` — spawn capture.
- `monoagens/engine/turn/context.py:210-225` — `WorkingMemory.append` fail-open.
- `monoagens/engine/db.py:95-145` — `connect`, `upsert`.
- `monoagens/engine/jobs/supervisor.py:36-129` — primary/standby lifecycle.
- `monoagens/engine/jobs/lease.py:103-129`
- `monoagens/engine/tools/shell.py:13-18` — subprocess without a process group.

---

*This ADR lives at `docs/v0.1.0/adr/engine/0011-crash-safety.md`.*
