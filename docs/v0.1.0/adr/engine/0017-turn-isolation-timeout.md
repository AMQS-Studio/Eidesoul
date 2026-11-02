# ADR-0017: Turn Isolation, Deadline, and Primary Gate

- **Status:** accepted
- **Implementation:** draft — `turn_lock` is still global; no deadline, no per-chat lock, no primary gate
- **Date:** 2026-11-03
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Context:** review (`Eidesoul-Review-Arsitektur-2026-11-01.md`) §15 P0-4: `turn_lock` is global, synchronous DB/mem0 work blocks the same event loop that renews the heartbeat lease, and `agent.mode` is never read, so a standby instance could still send
- **Supersedes:** —
- **Related:** ADR-0011 (shutdown & ledger), ADR-0012 (lease, `generation`), ADR-0013 (`turns`, `notice`), ADR-0003 (worker & executor), ADR-0019 (`turn_id` in logs)
- **Amendments:** 2026-11-03 — owner's question: "why doesn't the LLM register the timeout/budget?" → the deadline is split into **two layers** (§4.3): a per-request timeout plus explicit retry (turn path), and a turn `deadline` forwarded as `remaining_seconds`; `run_budget_seconds: 240` is declared **unreachable** on the turn path and is rebased onto background work. **2026-11-03** — §4.6 retry per path: `turn_attempts: 1` (HTTP call only; an executed tool is not repeated), `worker_attempts: 3` with backoff + jitter, `Retry-After` respected with a ceiling; the key `llm.max_retries: 0` is replaced by the `llm.retry.*` block.
- **Location note:** ADR lives in `docs/v0.1.0/adr/engine/`; the Indonesian copy is `docs/v0.1.0/adr/engine/id/0017-turn-isolation-timeout.md`.

---

## 1. Context

Four code facts, each reinforcing the others:

| Fact | Evidence |
|---|---|
| One lock for all chats | `agent.py:143` `self.turn_lock = asyncio.Lock()`, used at `:448` for every non-command turn |
| Synchronous DB inside `async def` | `db.py:53` `create_engine(...)`, `db.py:95` `connect()` synchronous; not one `to_thread` anywhere in `eidesoul/engine/` |
| Synchronous embedder | `embed.py:53-62` `SentenceTransformer.encode`, called from `knowledge.py:147`, `patterns.py:235`, and on the mem0 path |
| Standby not checked at the gate | `supervisor.py:62,111` writes `agent.mode`; not a single read of `self.mode` before `send_text`/`send_file` (`agent.py:311,335`) |
| The LLM deadline **restarts per call** | `llm.py:126` `timeout=120` per request; `llm.py:165-169` `run_budget_seconds: 240` is counted from `start` **inside** `run()`; `max_retries` is unset (library default 2) |

The consequences are not theoretical:

1. **One chat holds all chats.** A slow turn in a DM holds the turn of the other room, because the lock is not per chat.
2. **The heartbeat shares the loop with synchronous work.** A 1–2 second block on a query or an encode delays `Lease.renew()`; ADR-0012 treats lateness as failure, so a small block can turn into a takeover and two active instances.
3. **No work limit per turn.** One hanging LLM call holds the lock until `run_budget_seconds` (240 s) runs out, and because one turn calls `run()` more than once (decision → reply → polish) that limit is doubled; with the library default retry (2) one request can eat 3,120 s.
4. **Demotion did not stop sending.** `/freeze` and takeover dropped `mode`, but the send path did not check the fencing token (ADR-0012), so the gate was a formality.

## 2. Decision drivers

- The heartbeat must not depend on the length of any turn (safety invariant, ADR-0012).
- Different chats must not wait for each other.
- Every turn must have a defined end (finished, failed, or `interrupted`), never hang.
- The deadline must **accumulate** across the turn, not restart on each call.
- There must be no second execution path: mem0 stays synchronous, so a full-async rewrite would force two lanes that would then have to be kept in step forever.

## 3. Options considered

| Options | Contents | Cost | Risks | Verdict |
|---|---|---|---|---|
| **A. Full async** | `create_async_engine` | large | mem0 remains synchronous → dual path; the migration touches every handler | Rejected |
| **B. Offload at the seams** | `to_thread` in `Database.connect`, `embed.encode`, a mem0 wrapper, a bounded executor | medium | easy to forget; new code | **Selected** |
| **C. Lock per chat + deadline + `mode` gate** | pure control flow, without touching the I/O | small | does not remove loop blocking | **Selected with B** |
| **D. Let each LLM call register its own budget** | only `timeout_seconds`/`run_budget_seconds` in the LLM layer | zero | budget restarts per `run()` → N×240 s per turn; a hidden retry doubles latency | Rejected as the only layer (kept as layer 1) |
| **E. Do nothing** | — | zero | lease risk and two active instances | Rejected |

## 4. Decision

### 4.1 Offload at the seams, not in each handler

- A bounded executor (`engine.io.workers`, default 8) is shared by `Database.connect` (every query block on one job), `embed.encode`, and the synchronous mem0 call.
- **Hard rule:** `with db.connect()` must not appear inside an `async def` in `eidesoul/engine/`; it is allowed only inside an offloaded helper. Enforced by an AST test (ADR-0019 provides the same check infrastructure as `topology`).
- Async handlers (LLM, sandbox, MCP) are unchanged.

### 4.2 Lock per chat, not per process

- `chat_locks: dict[str, asyncio.Lock]` with `max_chat_locks` (default 256, LRU): a lock is created on use and released when nobody is waiting, so it does not leak across thousands of chats.
- Commands stay outside the lock (current behavior preserved: `nullcontext()`).
- Two turns in the same chat stay ordered (one conversation = one sequence); two turns in different chats run in parallel.

### 4.3 Two-layer deadline: forwarded turn deadline + per-call timeout

**Layer 1 — per call (fail fast, clear error):**
- `engine.llm.timeout_seconds` (existing, 120) applies per HTTP request.
- `engine.llm.max_retries: 0` on the turn path: 5xx/429 are not retried silently, because the library default retry (2) doubles the latency the user sees. Retry exists only for background jobs (digest/reflect) that do not hold the conversation lock.
- `engine.llm.run_budget_seconds` is **no longer a turn limit**: its value (240) is larger than the turn limit, so it is never reached. It is rebased onto the background-job budget; the turn path uses the remainder of the deadline.

**Layer 2 — per turn (accumulating):**
- At the start of `handle()` one `deadline = monotonic() + engine.turn.timeout_seconds` (default 90) is created and **forwarded** to every call as `remaining_seconds`.
- Effective timeout per call = `min(llm.timeout_seconds, remaining_seconds)`.
- If `remaining_seconds < engine.turn.min_call_seconds` (default 5), **no new LLM call is started**: the soul answers from what it already has (or the turn ends as `interrupted`), instead of starting calls that would certainly be truncated.
- `asyncio.wait_for(self.handle(...), timeout)` remains installed as a **watchdog**: many `await`s are not LLM calls (DB, mem0, tools, send), and only a watchdog can cancel one that hangs.

**When the deadline is exhausted:** the task is cancelled, `turns.status='interrupted'` (enum ADR-0013), one `notice:timeout` per chat per turn taken from the catalog (no sentence in code), and the `outbound_messages` ledger accounts for what was already sent. A timeout is **not** `failed`: that turn is not retried automatically, and the next message goes through the usual path.

### 4.4 Four primary gates on the send path

- One helper `_send_gate()` is called before every `send_text`/`send_file`: `mode == 'primary'` **and** `generation` matching `engine_lease`.
- On failure: internal `StandbyError`, **silent** towards the user (no "I am standby" message — that would leak topology), recorded as the counter `standby_refused` (ADR-0019).
- `supervisor` lowers `mode` **before** releasing the lease (order: gate down → release), so the send window after release is zero.

### 4.5 Shutdown obeys drain

- `asyncio.wait_for(asyncio.gather(*tasks), drain_seconds)`; tasks that have not finished are cancelled and marked `interrupted` (ADR-0013 §12; the numbers 25/15/5 belong to ADR-0011 and are not duplicated here).
- The lease is held until the `draining` phase completes, so another reaper instance does not take over in the middle of the drain.

### 4.6 Retry per path

| Path | Retry | Behavior |
|---|---|---|
| Turn (user waiting) | `turn_attempts: 1` | Only the idempotent HTTP model call is retried; 429/5xx; `Retry-After` is honoured only when ≤ `min(retry_after_max_seconds, remaining − min_call_seconds)`, otherwise → visible failure (`agent.api_error`). A tool already executed in that iteration is **not** repeated |
| Background workers (cognition/ambient/`distill`) | `worker_attempts: 3` | Exponential backoff `base_seconds × 2^n` with ceiling `cap_seconds` + `jitter`; `Retry-After` honoured; → job `failed` for a reason visible in `/status` |
| Automation run | joins ADR-0014 §4.7 | Automation retry rules are not duplicated here. |

- There is no retry for 4xx other than 429. The old exception of "drop the image and then retry once" still exists, and it is **not** part of the general retry (`eidesoul/engine/llm.py:195-198`).
- Every attempt writes its own `llm_calls` row (ADR-0019), so a retry is never hidden from the cost count.
- A retry never passes `turn.timeout_seconds`: an attempt that does not fit does not start.

## 5. Configuration

```yaml
engine:
 turn:
 timeout_seconds: 90 # one-turn limit (deadline, cumulative); exhausted -> interrupted + notice:timeout
 min_call_seconds: 5 # remaining below this -> do not start a new LLM call
 llm:
 timeout_seconds: 120 # already exists: one limit per HTTP request
 retry: # §4.6 — turn: at most one retry for an HTTP call; a tool that already ran is not retried
 turn_attempts: 1
 worker_attempts: 3 # background workers: exponential backoff + jitter, `Retry-After` honored
 base_seconds: 2
 cap_seconds: 60
 jitter: 0.2
 retry_after_max_seconds: 10
 io:
 workers: 8 # shared executor for DB, embed, and the synchronous mem0 path
 concurrency:
 max_chat_locks: 256 # LRU; the lock is released when no waiter remains
```

No new keys for shutdown: `grace_seconds`/`drain_seconds`/`notice_seconds` remain in ADR-0011/ADR-0013. `run_budget_seconds` stays for background work; it is no longer counted as a turn limit.

## 6. Metrics and gates

| Metric | Definition | Gate |
|---|---|---|
| `loop_lag_ms` | delay of `asyncio.sleep(0)` against its schedule | p95 < 100 ms during a smoke run with 2 parallel chats |
| `turn_timeout_total` | turns that end `interrupted` because of the deadline | 0 in a normal smoke run, rising during LLM interference |
| `standby_refused_total` | sends rejected by the gate | > 0 on a demotion test; 0 in normal operation |
| `chat_wait_ms` | waiting time for a per-chat lock | must not add cross-chat waiting |
| `heartbeat_delay_ms` | distance between scheduled and actual `renew` | < 25% of the TTL interval (ADR-0012) |
| `llm_calls_per_turn` | LLM call count in one turn | reported; a spike signals that activation failed |
| `remaining_at_call_ms` | remaining deadline when a call starts | never 0 (the call does not start) |

## 7. Acceptance criteria

- [ ] Two `handle()` calls in different chats run in parallel; the slow one does not hold the other (test with a 2 s delay).
- [ ] An injected slow query does not delay `renew()` past the ADR-0012 threshold (loop-lag + heartbeat test).
- [ ] A 90 s timeout → status `interrupted`, exactly one `notice:timeout` per chat, no second delivery of that turn.
- [ ] The deadline **accumulates**: 3 `run()` calls in one turn never exceed `turn.timeout_seconds` (test with a fake slow provider).
- [ ] Remaining deadline < `min_call_seconds` → no new LLM calls; the turn replies from what it has or ends `interrupted`.
- [ ] `max_retries: 0`: a 500/429 response is not retried on the turn path (test that counts requests).
- [ ] An instance with `mode='standby'` rejects `send_text`/`send_file` without sending anything (test), and a stale `generation` is rejected as well.
- [ ] No `with db.connect()` inside an `async def` (AST test; only offloaded helpers are allowed).
- [ ] `embed.encode` and the mem0 path have no synchronous call left outside the executor; loop lag p95 < 100 ms during a capture batch.
- [ ] Shutdown: a task that passes `drain_seconds` is cancelled and marked `interrupted`, not left hanging.
- [ ] 429 with `Retry-After: 30` is not retried on the turn path: the turn fails visibly instead of hanging; `Retry-After: 3` is retried once and each attempt writes its own `llm_calls` row.
- [ ] Retrying the model call does not restart a tool already executed in that iteration (test with a spy).

## 8. Risks and mitigations

| Risks | Mitigation |
|---|---|
| Saturated executor → turns queue on the thread | explicit `workers` + a work timeout that maps to classified errors instead of hanging; metric `io_queue_wait_ms` |
| Cancellation in the middle of a DB write | one offloaded block = one transaction; cancellation happens between queries, never in the middle of a commit |
| Per-chat locks leaking across many chats | LRU `max_chat_locks`; a lock with no waiter is released immediately |
| A send slipping through because of a new code path | one helper `_send_gate()`; a test ensures every `send_*` call goes through the helper |
| `mode` stale compared with the actual lease | the gate compares `generation`, not only `mode`; `mode` is only a fast path |
| Turn truncated when the draft is good | `min_call_seconds` + answering from the parts already available; `interrupted` is not retried automatically |
| Retry turned off and then the provider fails | retry remains for background work; on the turn path, failing fast beats unbounded latency |

## 9. Interaction with other ADRs

- **ADR-0012** `generation` and `mode` are two sides of one decision: the lease determines the primary, and this ADR ensures that only the primary speaks.
- **ADR-0013** status `interrupted` and `notice` are defined there; this ADR only triggers the deadline and shutdown.
- **ADR-0011** drain/grace numbers are not duplicated; the order of the shutdown phases belongs to ADR-0011.
- **ADR-0003** the cognition worker uses the same executor and stays retried (outside the turn).
- **ADR-0019** — `turn_id`, `llm_calls_per_turn`, and the counters `standby_refused`/`turn_timeout_total` are defined there.

## 10. Code references

- `eidesoul/engine/agent.py:143,413,448,1066` — global lock, `handle()`, lock wait point, gather on shutdown.
- `eidesoul/engine/db.py:53,95` — `create_engine` and the synchronous `connect()` (offload seam).
- `eidesoul/engine/embed.py:53-62` — synchronous `encode`.
- `eidesoul/engine/supervisor.py:62,111` — where `mode` primary/standby is written.
- `eidesoul/engine/lease.py:56-101` — `acquire`/`renew` (owner of `generation`).
- `eidesoul/engine/guard/sandbox.py:168` — precedent for `asyncio.wait_for` around bounded work.
- `eidesoul/engine/llm.py:122-128` — `run()` client `AsyncOpenAI(timeout=...)`; `:165-169` `run_budget_seconds` counted per `run()` (basis for the two-layer split).
- `eidesoul/gateways/cli/gateway.py:81`, `eidesoul/gateways/whatsapp/gateway.py:124` — the two callers of `handle()`.

---

*This ADR lives at `docs/v0.1.0/adr/engine/0017-turn-isolation-timeout.md`.*
