# ADR-0003: Cognition Worker & Model Batching — Gate vs Label Boundary

- **Status:** accepted
- **Implementation:** implemented — evidence: `cognition_jobs` (revision 5), `eidesoul/engine/jobs/cognition.py` queue + worker, `FOR UPDATE SKIP LOCKED` claim, reaper, backoff + dead-letter, intake enqueue in the same transaction, supervisor lifecycle, `tests/test_cognition.py`
- **Date:** 2026-11-01
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Context:** follow-up to ADR-0001 (canonical + Mem0 adapter) and ADR-0002 (capture gate + assess batch); the default-on memory plan
- **Supersedes:** —
- **Related:** ADR-0001, ADR-0002, ADR-0004 (fallback labeling), ADR-0008 (integrity), ADR-0010 (semantic reflection)
- **Location note:** ADR lives in `docs/v0.1.0/adr/engine/`; the Indonesian copy is `docs/v0.1.0/adr/engine/id/0003-cognition-worker.md`.
- **Amendments:** 2026-11-02 — framework normalization (ADR-0009 §4.4): added Options considered; §4→§5 and the numbering after it shifted. 2026-11-03 — audit: the range `supervisor.py:36-140` was fixed to `36-129` (the file is 129 lines). 2026-11-03 — audit: routine reflection = hourly worker C, the Batch API does not schedule it; the number of items per pass belongs to ADR-0010 §6 (`engine.reflection`), and a duplicate-key bystander was removed. 2026-11-03 — `kind='distill'` added (ADR-0020): it assesses a single episode when it is closed, in the worker, not on the turn path.

---

## 1. Context

- The real-time path now calls the LLM 4–6 times per DM: decision, reply + tool loop, `polish`, `regulate`, privacy audit, plus capture in the background.
- Default-on memory (ADR-0002) adds load to every turn. If everything is done inline, the cost and the tail latency get worse.
- There is no queue: capture uses fire-and-forget `spawn()` with no retry, status, or dead-letter (`agent.py:917-921`).
- Part of the LLM's work does not need to finish before the reply is sent — those are the batch candidates.
- There is also work that **must** finish before any action or utterance: guard, approval, crisis. That work cannot be batched.

## 2. Decision drivers

1. **Security** — anything that acts as a gate must not be delayed.
2. **Cost** — labeling work is done in groups, not per item.
3. **Latency** — the real-time path contains only the reply itself.
4. **Resilience** — work must not be lost; there is retry, status, and dead-letter.
5. **Operational simplicity** — one job table, two runners.
6. **Privacy isolation** — a batch must not mix different scopes/people.
7. **Observability** — backlog, queue age, and failures are visible at `/status`.

## 3. Terminology (locking ambiguity)

| Term | Meaning |
|---|---|
| **Runtime** | Eidesoul process (`eidesoul run`/`console`): supervisor, turn loop, worker |
| **Worker C** | In-process task in the runtime, only while primary; fast track |
| **Worker D** | Scheduled runner for the slow track; starts in-process, can be separate |
| **MiniLM** | Local embedder (384-d) inside the process; computes vectors |
| **LLM** | Provider language model (remote, HTTP) |
| **Mem0** | Extraction/index adapter behind a port (ADR-0001) |
| **Postgres** | Canonical memory + the `cognition_jobs` queue |
| **Provider Batch API** | Cheap execution mode for the slow lane |

"Owner" means whoever runs and is responsible for the lifecycle of a job — not whoever counts.

## 4. Options considered

No separate alternatives were recorded when the decision was made; the chosen path and its consequences are discussed directly in the Decision section.

## 5. Decision

### 5.1 Two paths

```mermaid
flowchart TB
 subgraph RT["Runtime Eidesoul — proses primary"
 T[Turn loop --> J[(cognition_jobs)
 C[Worker C --> J
 D[Worker D, in-process default --> J
 end
 C --> ML[MiniLM lokal
 C --> LLM["LLM provider (request biasa)"
 C --> MS[MemoryStore
 D --> BATCH["Provider Batch API (opsional)"
 D --> MS
 MS --> PG[(Postgres canonical)
 MS -.derived.-> M0[Mem0 extractor/index
```

- **Worker C is owned by the runtime**, it runs only while the lease is held (primary) and stops at freeze/standby.
- **Worker D is owned by the scheduler**; by default it runs in-process as a scheduled task, with an exit door to `eidesoul cognition --slow` (separate process/container) when needed.
- Both use **one job table** and **one write door** (`MemoryStore`).

### 5.2 Gate vs label boundary

| Work | Nature | Decision |
|---|---|---|
| `shell`/`sandbox` guard, approval | Gate before action | Deterministic, stays inline |
| Crisis handling | Gate before utterance | Deterministic, stays inline |
| L3/L4 disclosure | Gate before utterance | Replaced by **information pre-filter**: L3/L4 does not enter a non-DM prompt |
| Privacy auditor LLM | Label after draft | Demoted to a batch quality check |
| Memory assess + extraction | Label after turn | Worker C |
| Quick consolidation | Label/tidy-up | Worker C, every minute |
| Deep reflection | After the fact | Worker C, **hourly (default)** |
| Backfill, mass audit | After the fact | Worker D + provider Batch API |
| Topic/era digest, currents | After the fact | Worker C |

Principle: **if the work must hold back an action, do not batch it; if it only labels, batch it.**

### 5.3 Model of memory temperature

```mermaid
flowchart LR
 H[Hot: memory_events mentah --> W[Warm: fakta dilabeli capture
 W --> C[Cool: konsolidasi cepat tiap menit
 C --> D[Cold: deep reflection hourly
 D --> L[Lapse: retention, decay, hard delete
```

| Stage | Contents | Runner |
|---|---|---|
| Hot | `memory_events` raw, one transaction with the message | No LLM |
| Warm | assess + extraction (facts, level, subject) | C |
| Cool | merge duplicates, resolve simple `supersedes`, keep the index | C, every minute |
| Cold | contradictions, era/topics, decay, forgetting proposals | C, hourly (default) |
| Lapse | retention, hard delete, tombstone audit | Scheduled job, no LLM |

### 5.4 Single queue

Table `cognition_jobs`:

```sql
cognition_jobs(
 id uuid primary key,
 kind text not null, -- capture | consolidate | reflect | backfill | audit | distill
 scope text not null, -- global | person:<id> | chat:<id>
 payload_ref text not null, -- referensi ke memory_events
 status text not null, -- queued | claimed | done | failed | dead
 claimed_by text not null default '',
 lease_until double precision not null default 0,
 attempts int not null default 0,
 next_attempt_at double precision not null default 0,
 result_ref text not null default '',
 idempotency_key text not null,
 created_at double precision not null,
 updated_at double precision not null,
 unique (idempotency_key))
```

- Job claim: `SELECT ... WHERE status='queued' AND next_attempt_at <= now() ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT n`.
- **Reaper**: `lease_until` passed → back to `queued`.
- Retry with backoff; after `dead_letter_after` → status `dead`, visible at `/status`.
- One idempotency key per job; canonical writes are upserted through `MemoryStore`.

### 5.5 Batch mechanics

- One request contains an array of items with `id`; the output must be the same array with `id`.
- Validation **per item**; a failed item does not fail the batch; retry per item.
- Batch size is limited by the context window and output tokens; chunk when needed.
- The batch system prompt is **stable** (no `uptime`/`lease` in the prefix) so that prompt caching works.
- **Batch per scope**: do not mix items from different people/chats in one request; an L3/L4 item does not join a batch containing other scopes.
- The worker must not block the event loop: DB and MiniLM go through a thread/executor.

### 5.6 Provider Batch API

- Used **only** for the slow lane: backfill and mass audit. Routine reflection **does not** go through it —
  it is scheduled by hourly worker C (ADR-0010 §6).
- Rule: **one batch outstanding**: submit only when no batch is running and the backlog passes the threshold.
- Completion-based, not a fixed schedule; results are polled, validated per item, then written canonical.
- Hour-scale latency, no streaming, format per provider → separate adapters in `providers/`, not branches on the provider name.
- Default: **`batch_api: off`** until the main path is stable.

## 6. Configuration

```yaml
memory:
 worker:
 c:
 owner: runtime
 capture: { max_items: 20, max_wait_seconds: 30 }
 consolidation: { every_seconds: 60, max_items: 100 }
 reflection: { every_seconds: 3600 } # jumlah item/pass: ADR-0010 §6 (engine.reflection)
 concurrency: 2
 retry: { attempts: 5, backoff_seconds: 2, dead_letter_after: 5 }
 queue_cap: 5000
 d:
 owner: scheduler
 runner: in_process # in_process | command
 schedule: nightly # nightly | weekly
 one_batch_at_a_time: true
 batch_api: off # off | slow_lane
```

The agreed default: **quick consolidation every 60 seconds, deep reflection every 3600 seconds (hourly) via worker C.**

## 7. Consequences

**Positive**

- The real-time path contains only the reply; cost and latency are down.
- Work survives restarts: status, retry, dead-letter.
- One table and one write door: C and D cannot overwrite each other.
- Quick consolidation keeps "hot" memory from piling up duplicates.
- The provider Batch API is an option, not a dependency.

**Negative**

- It adds tables, workers, reapers, and metrics.
- Memory labels are not instant; there is a window between capture and consolidation.
- A batch per scope is smaller, so it is less economical than a mixed batch.
- `polish`/`regulate` is undecided; if it stays inline, the real-time cost has not dropped as far as it could.

**Neutral**

- Guard/approval/crisis stay as they are; only the auditor and the label jobs change.
- Mem0 is still used as an adapter; its status is derived.

## 8. Metrics and gates

| Metric | Initial target |
|---|---|
| `queued` queue p95 age (capture) | < 60 seconds |
| Average batch size | > 5 |
| LLM calls per item (capture) | ≤ 1 |
| Dead-letters per day | approaching 0 |
| Cost per turn | down ≥ 50% compared with inline |
| Late correction | measured, trending down |
| Quick consolidation delayed | < 5 minutes |

## 9. Acceptance criteria

- [ ] No non-gate LLM work on the real-time path other than the reply.
- [ ] Guard/approval/crisis stays deterministic and inline.
- [ ] The auditor is no longer a gate; leaks are prevented by the L3/L4 pre-filter.
- [ ] The worker survives restarts; no item is lost; it is idempotent; there is a dead-letter.
- [ ] A failed item does not fail the batch.
- [ ] A batch does not mix scopes/people.
- [ ] The worker stops at standby/freeze and does not block the event loop.
- [ ] Quick consolidation runs every minute and deep reflection every hour (default).
- [ ] The provider Batch API is only for the slow lane, one batch outstanding.
- [ ] Queue status is visible at `/status`.

## 10. Risks and mitigations

| Risks | Mitigation |
|---|---|
| Late correction | Deterministic gate; correction via edit/delete/message; not for physical actions |
| Batch poisoning | Validation per item; provenance; instruction quarantine (ADR-0008) |
| Cross-item contamination | Batch per scope; forbid mixing L3/L4 |
| Queue pile-up | Backpressure: degrade to metadata-only + alert |
| Runaway reflection cost | Budget per round; skip when there are no changes |
| Batch API pile-up/expiry | One batch outstanding; completion-based |
| Two runners overwriting each other | `SKIP LOCKED` + lease + reaper + idempotency |

## 11. Interaction with other ADRs

- **ADR-0001** — the canonical store and outbox become the write target.
- **ADR-0002** — gates and assess become `kind=capture` jobs.
- **ADR-0004** — behavior when assess fails (drop/provisional/stub).
- **ADR-0006** — L3/L4 pre-filter for groups.
- **ADR-0008** — provenance/trust and anti-poisoning.
- **ADR-0010** — semantic deep reflection (grounding, audit, budget) — **accepted** (`ADR-0010-reflection-disputed.md`): job `kind=reflect`, one scope per pass, `refs` mandatory, `derive_depth: 1`, budget per pass/day.

## 12. Code references

- `eidesoul/engine/agent.py:917-921` — `spawn()` capture without retry.
- `eidesoul/engine/access/privacy.py:141-214` — `review` (auditor) as the current gate.
- `eidesoul/engine/guard/policy.py:200-213` — deterministic policy that stays inline.
- `eidesoul/engine/tools/__init__.py:182-240` — the single tool execution door.
- `eidesoul/engine/jobs/supervisor.py:36-129` — primary/standby lifecycle, the parent of worker C.
- `eidesoul/engine/stores/affect.py` — a worker/state-document pattern that can be copied.
- `eidesoul/engine/config.py:130-138` — `MemoryConfig` for the worker knobs.

---

*This ADR lives at `docs/v0.1.0/adr/engine/0003-cognition-worker.md`.*
