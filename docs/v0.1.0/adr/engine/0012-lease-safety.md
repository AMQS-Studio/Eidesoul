# ADR-0012: Lease Safety: CAS, Fencing Token, and Database Clock

- **Status:** accepted
- **Implementation:** partial — present: CAS acquire, `generation`, DB clock, takeover audit, generation-keyed renew/release, `LeaseGuard.require()`, revision 2; remaining: generation checks wired into delivery and memory writes (W2/W3 acceptance rows)
- **Date:** 2026-11-01
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Context:** follow-up to ADR-0011 (crash safety & recovery) and architectural review findings: lease without compare-and-swap, using local clocks
- **Supersedes:** —
- **Related:** ADR-0011 (recovery), ADR-0013 (resume turn)
- **Amendments:** 2026-11-02 — normalization of the skeleton (ADR-0009 §4.4): added the Interaction with other ADRs section; Code references shifted to §11. 2026-11-03 — audit: the range `supervisor.py` was corrected to `36-129`; the stale line "waiting for the ADR-0009 decision" was aligned (ADR-0009 is already accepted, the migration to `docs/v0.1.0/adr/`). 2026-11-03 — audit: the fencing column was standardized to `generation` (not `lease_generation`); the migration path points to ADR-0016.
- **Location note:** ADR lives in `docs/v0.1.0/adr/engine/`; the Indonesian copy is `docs/v0.1.0/adr/engine/id/0012-lease-safety.md`.

---

## 1. Context

- `Lease.acquire` reads the row, checks `heartbeat_at < now - ttl`, then **UPDATEs without a condition** (`lease.py:76-101`). There is no CAS, no `RETURNING` that verifies the winner.
- Two standbys that both see an expired lease can **both become primary**; the loser only finds out when `renew` fails, then freezes after 3 failures (~15 seconds).
- Expiry uses each process's own `time.time()` (`lease.py:57,84,104`), so clock skew between hosts can declare a healthy primary dead.
- `frozen()`, `renew()`, and `holder()` do not catch exceptions; a connection error escapes `Supervisor.serve()` and kills the process (`supervisor.py:93-104`). Only `rowcount == 0` counts as a renew failure.
- Recovery in ADR-0011/0013 depends on the assumption that there is only one primary — without this fix, resume can run on two instances at once.

## 2. Decision drivers

1. **Mutual exclusion**: exactly one primary at a time.
2. **Fencing**: the old holder must not write or send after being replaced.
3. **Clock resilience**: decisions based on database time, not the application clock.
4. **Graceful degradation**: a DB error counts as a renew failure, not as process death.
5. **Compatibility**: freeze/takeover stay as they are now.

## 3. Options considered

| Options | Shapes | Excess | Lack | Status |
|---|---|---|---|---|
| **A. Status quo** | Read-check-update, local clock | No changes | Split brain; sensitive to skew; a DB error kills the process | Rejected |
| **B. CAS only** | `UPDATE... WHERE heartbeat_at < now() - ttl RETURNING` | Closes the acquire race | Still vulnerable to clock skew; no fencing for old writers yet | Insufficient |
| **C. CAS + DB clock + fencing token** | CAS with `clock_timestamp()`; `generation` rises on every acquire; writes/deliveries carry the generation | Mutual exclusion + fencing + skew-resistant | Adds a column and generation checks on the write path | **Selected** |

## 4. Decision

### 4.1 Acquire with CAS

```sql
UPDATE engine_lease
SET instance_id =:me,
 host =:host,
 pid =:pid,
 mode =:mode,
 generation = generation + 1,
 started_at = clock_timestamp(),
 heartbeat_at = clock_timestamp()
WHERE id = 1
 AND (instance_id =:me OR heartbeat_at < clock_timestamp() - (:ttl || ' seconds')::interval)
RETURNING instance_id, generation;
```

- A caller becomes primary only if `RETURNING` returns itself.
- The row is created with `INSERT... ON CONFLICT (id) DO NOTHING` if it does not exist yet, then the same CAS.
- An explicit takeover is still respected (`takeover=true` bypasses the expiry condition), but it still goes through the CAS and still raises `generation`.

### 4.2 Renew with generation

```sql
UPDATE engine_lease
SET heartbeat_at = clock_timestamp()
WHERE id = 1 AND instance_id =:me AND generation =:gen
RETURNING generation;
```

`rowcount = 0` or an exception = one renew failure. Three consecutive failures → the primary stops serving and freezes.

### 4.3 Fencing on the write path

- Every write that changes the real world carries `generation`:
  - sending a message (`outbound_messages.generation`),
  - writing canonical memory (the `generation` column on `turns`/job),
  - marking a job complete.
- Before writing, check the current `generation` in the DB. If it has already changed, cancel that operation and mark `superseded` — the old holder must not finish the job after being replaced.
- For delivery: `send_text` cannot be undone, so the generation check must happen **before** the transport call, not after it.

### 4.4 Clock

- All lease time comparisons use `clock_timestamp()` (the real value at that moment), not `now()` (the transaction start time) and not the application clock.
- The column `heartbeat_at` stays a float, but is always filled from the database.

### 4.5 DB error

- `frozen()`, `renew()`, `holder()`, and `acquire()` wrap a connection error as a lease failure, not as an exception that kills the supervisor.
- If the error keeps crossing the threshold, the primary enters `frozen`/`standby` mode and retries with backoff.

### 4.6 Release

```sql
UPDATE engine_lease
SET instance_id = '', heartbeat_at = 0
WHERE id = 1 AND instance_id =:me AND generation =:gen;
```

Release only by the current generation owner.

## 5. Schema

```sql
engine_lease(
 id int primary key check (id = 1),
 instance_id text not null default '',
 host text not null default '',
 pid int not null default 0,
 mode text not null default 'standby',
 generation bigint not null default 0,
 started_at double precision not null default 0,
 heartbeat_at double precision not null default 0)
```

The column `generation` is added; `SCHEMA_VERSION` rises and a migration is required (ADR-0005/0011 for the migration path).

## 6. Configuration

```yaml
lease:
 ttl_seconds: 30
 renew_interval_seconds: 5
 failures_before_freeze: 3
 acquire_retry_seconds: 5
```

## 7. Metrics and gates

| Metric | Target |
|---|---|
| Two primaries at the same time | 0 (tested with concurrency) |
| Renew failed while the DB was healthy | 0 |
| A DB error killing the process | 0 |
| Writing with a stale generation | rejected and recorded |
| Takeover time after a crash | ≤ ttl + renew interval |

## 8. Acceptance criteria

- [ ] Two concurrent `acquire` → exactly one winner (`RETURNING`).
- [ ] Acquire with an expired row twice → only one may write a new generation.
- [ ] Host clock skew does not affect expiry (DB clock).
- [ ] `renew` uses `instance_id` + `generation`.
- [ ] Writes/deliveries check the generation before execution.
- [ ] A DB error does not kill the process; it counts as a renew failure.
- [ ] `release` only by the generation owner.
- [ ] Freeze/takeover still work as before.
- [ ] A concurrency test + a skew test + a DB error test are in CI.

## 9. Risks and mitigations

| Risks | Mitigation |
|---|---|
| Generation not checked on all write paths | A single port (`LeaseGuard`) mandatory before side effects; a test fails if transport is called without the guard |
| Migration of the column `generation` | Alembic + baseline; ADR-0016 (schema & migration) |
| Acquire retry too tight | Backoff + jitter |
| Freeze does not apply to running jobs | Generation check at the step boundary; the job is handed to the new owner |

## 10. Interaction with other ADRs

- **ADR-0011 (crash safety & recovery)** — this ADR is its direct follow-up: its recovery depends on the assumption that there is only one primary, so a lease without CAS/DB clock invalidates that guarantee; the migration path for the column `generation` is in ADR-0016.
- **ADR-0013 (resume turn)** — resume turn also assumes only one primary; without this fix resume can run on two instances at once. Both are listed in the header `Related:` as ADR-0011 (recovery) and ADR-0013 (resume turn).
- **ADR-0016 (schema & migration)** — owner of the canonical DDL and the Alembic baseline: the column `generation`, the `SCHEMA_VERSION` bump, and the constraint/index this ADR needs.
- **ADR-0009 (ADR home)** — already decided: ADRs live in `docs/v0.1.0/adr/`; the migration touches the file location, not the lease decision here.

## 11. Code references

- `eidesoul/engine/lease.py:56-129` — acquire/renew/release.
- `eidesoul/engine/lease.py:145-163` — freeze.
- `eidesoul/engine/supervisor.py:36-129` — primary/standby loop.
- `eidesoul/engine/schema.py:270-290` — `engine_lease`, `engine_control`.
- `eidesoul/engine/db.py:57-86` — create_all/ensure vector.

---

*This ADR lives at `docs/v0.1.0/adr/engine/0012-lease-safety.md`.*
