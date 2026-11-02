# ADR-0004: Fallback Labeling — Provisional Canonical with `level NULL` as a Sentinel

- **Status:** accepted
- **Implementation:** partial — present: `_assess` + the drop path on failure; remaining: the `staged` state, the `via` enum
- **Date:** 2026-11-01
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Context:** follow-up to ADR-0001 (canonical + Mem0 adapter), ADR-0002 (capture gate + assess batch), ADR-0003 (cognition worker)
- **Supersedes:** —
- **Related:** ADR-0005 (retention), ADR-0006 (group privacy), ADR-0008 (integrity), ADR-0011 (crash safety)
- **Amendments:** 2026-11-02 — `staged` state added (ADR-0008 §4.4 A): labeled rows held back by the write-volume quota, TTL follows `kind`, promotion via recall-miss or batch; the §5.2 constraint was adjusted (`state IN ('ok','staged')` ⇔ `level IS NOT NULL`), the §5.3 view is unchanged so that `staged` is never read on the normal path, and `pending` rows are not treated as trusted facts. 2026-11-03 — audit: the `_assess` range was aligned to `privacy.py:282-309`.
- **Location note:** ADR lives in `docs/v0.1.0/adr/engine/`; the Indonesian copy is `docs/v0.1.0/adr/engine/id/0004-labeling-fallback.md`.

---

## 1. Context

- `_assess` returns `None` after two failed attempts, then `capture_turn` stops without storing anything (`privacy.py:282-309`). This means every LLM hiccup silently deletes memory.
- Dangerous privacy defaults for fallbacks: `default_level: 2`, `group_max_level: 2` (`config.py:118-126`). Dropping unlabeled data to `default_level` means content that is actually sensitive **leaks to the group**.
- LLM output can be partial: `worth=true` but `level`/`subject` invalid. Storing half-baked rows would revive the bug class currently being eradicated (incomplete security metadata).
- Raw candidates (`memory_events`) are already durable; what is undecided is how to represent unlocalized candidates in canonical.

## 2. Decision drivers

1. **No memory loss** when the LLM is in trouble.
2. **No leak**: unlabeled data must not be recalled or disclosed.
3. **Testable invariants**, not human discipline.
4. **One row lifecycle**: avoid insert-vs-update races.
5. **Automatic recovery**: retry, visible, and reprocessable.
6. **Data minimization**: raw material that is never localized has a TTL.
7. **Controlled cost**: retries are bounded.

## 3. Pipeline corrections

Three stages are firmly distinguished:

| Stage | Nature | When |
|---|---|---|
| **Hard exclusion** — credentials, crisis, dangerous instructions | Must not be stored at all | **Before** `memory_events` is written |
| **Structural gate** — length, command, dedupe, rate limit | May be stored, just not worth processing now | When the job runs |
| **Labeling** — worth, level, subject | Determines usefulness and safety | After `memory_events` exists |

ADR-0002 amendment: "`memory_events` is written whatever the gate decides" is replaced by "it is written for candidates that pass **hard exclusion**; the structural gate is evaluated when the job runs."

## 4. Options considered

| Options | Shape | Strengths | Weaknesses | Status |
|---|---|---|---|---|
| **A. Drop** | Labeling fails → not stored | Safest | Silent memory loss; the "remembering" claim is a lie | Rejected |
| **B. Deferred canonical** | Raw in `memory_events`; the `memories` row only after labeling succeeds | Simple invariant | Admin must join; dedupe does not see pending | Rejected in favor of C |
| **C. Provisional canonical (`level NULL`)** | `memories` row created `pending` with `level NULL`; only `state='ok'` is readable | One lifecycle; dedupe sees pending; admin sees the labeling queue | Needs view/port enforcement so pending is never read | **Selected** |
| **D. Provisional + owner-only injection** | Like C, but it may enter the owner's DM | Least "forgetful" | Highest risk; third parties can be exposed; hard to test | Rejected |

## 5. Decision

> **`level NULL` is the sentinel for "must not be used". It is never a temporarily usable state. The only transition to "usable" is `state = ok` with a valid `level`.**

### 5.1 State and columns

| Column | Rule |
|---|---|
| `state` | six values: `pending` / `ok` / `staged` / `failed` / `not_worth` / `superseded` (enum & CHECK in ADR-0016 §4.3) |
| `level` | NULL except when `state = ok` |
| `confirmed` | Only meaningful when `state = ok` |
| `label_attempts` | Number of labeling attempts |
| `last_error` | Last failure reason (truncated) |
| `label_after` | Next attempt time (backoff) |
| `expires_at` | TTL for non-`ok` states |

`vetoed` is orthogonal: it can happen on `state='ok'` and immediately removes the row from the read view.

### 5.2 Constraint

```sql
CHECK ( state IN ('pending','ok','staged','failed','not_worth','superseded'))
CHECK ( (state IN ('ok','staged','superseded')) = (level IS NOT NULL))
CHECK ( level IS NULL OR level BETWEEN 0 AND 4)
CHECK ( state <> 'ok' OR confirmed IS NOT NULL)
```

Final DDL and index shapes are in ADR-0016 §4.2-§4.3; `staged` rows (ADR-0008 §4.4 A) carry a level
because they are already labeled, and `superseded` **keeps** the level of the row it replaced (ADR-0008 B).
All of them stay unreadable while the state is not `ok`.

### 5.3 Single read path

```sql
CREATE VIEW memories_readable AS
SELECT * FROM memories
WHERE state = 'ok' AND vetoed = false;
```

- All retrieval, prompting, and disclosure **go through this view only**.
- Worker and admin use the base table.
- Ports: `MemoryStore.read()` → `memories_readable`; `MemoryStore.admin()` → `memories`.
- Optional (extra layer): RLS on the base table so that the application role only sees rows with `state='ok' AND vetoed=false`.

### 5.4 State machine

| State | Level | Recall? | Disclosed? | Transition |
|---|---|---|---|---|
| `pending` | NULL | No | No | → `ok` / `failed` after retry |
| `staged` | 0–4 | No (except the recall-miss path) | No | → `ok` on promotion (recall-miss or batch) |
| `ok` | 0–4 | Yes, according to scope | According to level policy | → `superseded` (replaced) / `vetoed` (column) / purge |
| `failed` | NULL | No | No | TTL purge + alert |
| `not_worth` | NULL | No | No | TTL purge (backfill/eval) |
| `superseded` | 0–4 | No | No | TTL 30 days, then purge + tombstone |

`vetoed` is not a state: it is an orthogonal column (0–4 remains its level) that removes the row from the read view
without changing `state`. Purge deletes the row and leaves an audit tombstone; there is no `expired`
state.

`staged` (ADR-0008 §4.4 A): rows that are **already labeled** but held back by the write-volume quota. They are
not read on the normal path — the view invariant stays "only `ok`". The difference from `pending`: `pending`
is for data that is not yet localized (TTL 7 days) and `staged` is for labeled data (TTL follows `kind`,
ADR-0005).

### 5.5 Lifecycle

1. The turn passes hard exclusion → `memory_events` + a `memories(state='pending', level=NULL)` row in the same transaction as the message.
2. The worker attempts labeling → on success: `state='ok'`, `level` filled in, `confirmed` per the result.
3. Partial output (`worth=true` without a valid level, or a level outside 0–4) is treated as **pending**, not stored half-baked.
4. Failure → retry with exponential backoff; at most **5 attempts**; once exhausted → `state='failed'` + alert at `/status`.
5. `pending`/`failed`/`not_worth` past the **7-day** TTL → purge + audit tombstone without content.
6. `ok` → retention follows the level (ADR-0005).
7. `veto` at any time removes the row from the view; the row remains for audit.
8. `staged` (volume quota, ADR-0008 §4.4 A) is promoted to `ok` by: (a) **recall-miss** — normal retrieval
   finds nothing, then a matching `staged` row is lifted before use, limited to
   once per turn and `max_rows`; or (b) a worker batch during a burst. If it is never used
   or promoted, it expires by its `kind` TTL (tombstone without content).

### 5.6 When the LLM is unavailable

- Raw material is still written (except hard exclusion); the row stays `pending` with `level NULL`.
- The soul will not remember anything new while the LLM is down, and it will not invent old memories.
- The backlog is visible at `/status`; the worker reprocesses when the LLM is up.
- This is honest degradation, not loss: no row can be read before it is localized.

## 6. Configuration

```yaml
memory:
 labeling:
 max_attempts: 5
 backoff_seconds: 5
 pending_ttl_days: 7
 staged_ttl: kind # staged row (ADR-0008) follows the kind TTL
 alert_backlog: 100
```

## 7. Metrics and gates

| Metric | Initial target |
|---|---|
| Labeling success rate | ≥ 99% |
| `pending` backlog p95 age (LLM healthy) | < 10 minutes |
| Raw pending purged by TTL | ~0 when the LLM is healthy |
| Retries per item | ≤ 2 calls |
| Leak from a non-`ok` state | 0 (tested) |

## 8. Acceptance criteria

- [ ] There is no way to read `memories` for prompts except through `memories_readable`.
- [ ] Rows with `state='pending'` never appear in retrieval/prompt/disclosure, even if the caller forgets to filter.
- [ ] The CHECK constraints reject `state='ok'` rows without a level, and non-`ok` rows that have a level.
- [ ] Partial output never becomes an `ok` row.
- [ ] Hard exclusion happens before `memory_events` is written.
- [ ] Retries are limited to 5 attempts with backoff; failures are visible at `/status`.
- [ ] A 7-day TTL for unlabeled states (`pending`/`failed`/`not_worth`); `staged` follows the `kind` TTL (ADR-0008), with an audit tombstone.
- [ ] Test: vetoing an `ok` row immediately removes it from the view.
- [ ] `staged` rows never appear in normal retrieval/prompt/disclosure; promotion is only via recall-miss (bounded) or batch.
- [ ] The constraint rejects `state='staged'` without a level.

## 9. Risks and mitigations

| Risks | Mitigation |
|---|---|
| A read path that bypasses the view | `read()` as the only access port; a test that fails if the base table is accessed for prompts |
| Pending pile-up when the LLM is down for a long time | Threshold alert; raw TTL; documented honest degradation |
| `level NULL` mistaken for a low level | CHECK constraint + view; no default of 2 for fallbacks |
| Retry cost | Attempt limit + backoff + metric |
| Third-party data stored raw | Hard exclusion + TTL + ADR-0006 |

## 10. Interaction with other ADRs

- **ADR-0002** — amended: hard exclusion before `memory_events`.
- **ADR-0003** — retry and alert run in worker C; failed items go to the dead-letter.
- **ADR-0005** — retention for `ok` follows the level; non-`ok` uses a 7-day TTL.
- **ADR-0006** — the L3/L4 pre-filter stays in force after labeling.
- **ADR-0008** — provenance/trust; pending is not treated as a trusted fact.
- **ADR-0011** — crash safety: `pending` rows must survive and be recovered after a restart.

## 11. Code references

- `eidesoul/engine/privacy.py:222-303` — `capture_turn`, `_assess`.
- `eidesoul/engine/privacy.py:133-140` — `_risky` (level and confirmed).
- `eidesoul/engine/memory/privacy.py:52-71` — `show`, `disclose`.
- `eidesoul/engine/memory/mem0.py:360-380` — `capture` guard.
- `eidesoul/engine/config.py:118-126` — `PrivacyConfig` (default 2, group max 2).
- `eidesoul/engine/agent.py:906-921` — the current capture gate.

---

*This ADR lives at `docs/v0.1.0/adr/engine/0004-labeling-fallback.md`.*
