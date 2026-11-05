# ADR-0001: Memory Source of Truth — Postgres Canonical, Mem0 as Memory Intelligence

- **Status:** accepted
- **Implementation:** implemented — evidence: canonical `memories`/`memory_events`/`memory_uses`/`memory_index`/`memory_tombstones`, `MemoryStore` as the single write port with view-only reads, Mem0 demoted to extractor/index, rekey, veto, forget/purge, the intake seam, capture retry/backoff/dead-letter in `cognition.py`, and the recall/precision eval (`tests/test_memory_eval.py`: recall 1.0, precision 1.0, duplicate rate 0.0 over 12 cases); gate: `pytest -q` 831 passed, `scripts/adr.py --check`
- **Date:** 2026-11-01
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Context:** Eidesoul architecture review session at commit `5dc004c`; follow-up discussion on default-on memory and Mem0's roles
- **Supersedes:** —
- **Related:** ADR-0002 ... ADR-0008 (decision queue), report `Eidesoul-Review-Arsitektur-2026-11-01.md`
- **Amendments:** 2026-11-02 — framework normalization (ADR-0009 §4.4): added Configuration, Metrics and gates, and Interaction with other ADRs; renumbered the sections after them. 2026-11-03 — audit: the §14 heading was aligned with its content (queue + status, not "open questions") and the list of job `kind`s in the queue was matched to ADR-0003 §5.4 (`capture`/`consolidate`/`reflect`/`backfill`/`audit`). 2026-11-03 — owner decision: the `memories` DDL moved to **ADR-0016** (one canonical DDL, supersede-aware constraints), the §14 queue gains ADR-0016, and **`AGENTS.md` was amended**: `MemoryStore` becomes the single write port (the "never revive" prohibition is replaced by a prohibition on a second semantic path). 2026-11-03 — queue name made uniform: `memory_outbox` → `cognition_jobs` (diagram, tables, implementation stages). 2026-11-03 — the §14 queue also adopts the term **Layer 1/2** for the ADR-0015 payload. 2026-11-03 — owner decision: **Mem0 is mandatory** (spine), so option B2 "drop Mem0" is declared **rejected** (§3/§4/§7 adjusted); `mem0_history.db` is designated a derived library cache (`memory/mem0.py:157-182`) and `AGENTS.md` gains an explicit clause for library caches. 2026-11-03 — that file is declared to hold text
(including deleted text), so purge/`forget` must clean its copy too (ADR-0005 §4.5). 2026-11-03 — audit: references to the `mem0ai` package schema (`history`/`messages`) were added to the claim about the file's contents.
- **Location note:** ADR lives in `docs/v0.1.0/adr/engine/`.
- **Amendments (2026-11-05, ADR-0051):** Mem0 is retired; PostgreSQL `memories` with pgvector(384) + tsvector serves as the unified system of record and native hybrid retrieval index. MemoryStore remains the single canonical write port.

---

## 1. Context

- Memory is practically default-off today: auto-capture only runs when the role has the `memory` tool (`agent.py:614,906-921`), and capture is fire-and-forget with neither retry nor status.
- Mem0 owns both the data and the security metadata (level, owner, subject, veto, audience) in the JSONB payload, not the schema we control.
- Consequences verified in review:
  - identity rekey revives an already vetoed entry and changes the audience (`memory/rekey.py:15-62`);
  - `set_privacy` has no authorization (`memory/mem0.py:496-525`);
  - privacy level does not filter retrieval, only output/audit (`memory/context.py:127-131`, `memory/privacy.py:52-71`);
  - Mem0 fails to init → `NullMemory`, the bot still replies but does not remember (`memory/__init__.py:131-188`);
  - no schema migration, RLS, retention, or dependable audit.
- Product goal: memory is the soul's spine — it must be durable, auditable, properly forgettable, and consistent across body/gateway.

## 2. Decision drivers

1. **Durability.** Memory must not be lost because a library failed.
2. **Consistency.** Capture should be one transaction with message/event storage.
3. **Security.** Level, owner, subject, veto, audience must become columns that can be queried, tested, and used in `WHERE`.
4. **Operational.** Backup, migration, retention, audit, and index rebuild.
5. **Time-to-value.** Do not rebuild the extraction/consolidation engine without evidence.
6. **Freedom to leave.** Mem0 must be replaceable without changing the caller.
7. **Constraint.** `AGENTS.md` states "Semantic memory only through Mem0"; this decision changes its meaning, not removing Mem0.

## 3. Options considered

| Options | Shape | Consequences | Status |
|---|---|---|---|
| **A. Mem0 canonical + outbox** | Postgres is just a queue; Mem0 remains the data owner | Fast; data loss is held in the outbox; but security metadata stays in the library, so rekey/veto/level stays fragile and migration/RLS stays impossible | Rejected as target |
| **B1. Path 1 — Postgres canonical + Mem0 as intelligence/index** | `memories` is ours; Mem0 runs extraction/consolidation and semantic search behind ports; the index can be rebuilt | Durable, transactional, queryable/RLS/migration-ready; metadata bugs disappear; still uses Mem0's value; adds outbox, reconciliation, and ports | **Selected** |
| **B2. Path 2 — drop Mem0** | Postgres canonical including `vector(384)` + HNSW; our own extraction/dedupe/ranking | Full control and a single store; but then we own the quality of extraction, dedupe, ranking, and eval | **Rejected**: Mem0 is mandatory in the spine (AGENTS) |
| **C. Mem0 as a silent index** | Canonical Postgres, but Mem0 only stores vectors | The dependency remains, but Mem0's intelligence value is unused | Rejected |
| **D. Status quo** | Mem0 as full canonical | The smallest change, but unfit for data claimed to be the soul's core | Rejected |

Why B2 is rejected: Mem0 is a **mandatory part of the spine**, not an implementation option — `AGENTS.md` says it can be revoked only by changing the spine, not through an implementation path. The hardest part is indeed not the vector column but the extraction prompt, dedupe/conflict resolution, ranking/hybrid search, embedding versioning, and evaluation; because Mem0 is mandatory, that work still belongs to Mem0 and we only keep the contract.

## 4. Decision

> **Own the record, rent the intelligence.**

- **Postgres is the system of record.** Table `memories` is the source of truth for facts, security level, ownership, veto, and retention.
- **Mem0 is `MemoryExtractor` + `MemoryIndex` behind ports.** It still does the most valuable work: turning conversations into structured facts, dedupe/consolidation, and semantic search.
- **The index and the extraction results can be rebuilt from canonical.** Derived stores may be lost; canonical may not.
- **Security decisions stay in canonical**, filtered in SQL before ranking.
- **Mem0 is not used as a silent index.** If its intelligence is no longer used, the adapter is what gets fixed (or the spine is changed via a new ADR that changes `AGENTS.md`), instead of keeping it as a storage vector.

## 5. Target architecture

```mermaid
flowchart LR
 T[Turn / incoming event -->|the same transaction| J[(memory_events)
 J --> O[(cognition_jobs)
 O --> X[MemoryExtractor<br/>Mem0 or the LLM
 X --> C[(memories<br/>canonical)
 C --> IDX[(memory_index<br/>Mem0 or pgvector)
 C --> R[Retrieval: security filter in SQL
 IDX --> R
 R --> P[Prompt turn
```

Table plans:

| Table | Role | Properties |
|---|---|---|
| `memory_events` | Raw capture journal (messages/observation) | Immutable, written in one transaction with the message |
| `cognition_jobs` | Extraction/index job queue (old name: `memory_outbox`) | Idempotent, retried, visible status |
| `memories` | Canonical facts + security metadata | Source of truth |
| `memory_index` | Vectors for semantic search | Derived, rebuildable |

The final shape of the `memories` columns, constraints, and indexes is in **ADR-0016 §4.2-§4.3** (one
canonical DDL). This ADR holds the table list and their roles; columns that are born in derivative ADRs
(`state`, `level`, `confirmed` in ADR-0004; retention in ADR-0005; provenance/trust in ADR-0008;
dispute in ADR-0010) are not rewritten here, so that no two versions can diverge.

Invariants to be tested:

- `extra` can never overwrite a security column (`level`, `owner_id`, `subject_id`, `vetoed`, `audience`).
- Retrieval filters always include `vetoed = false` and the allowed scopes; level is filtered before the prompt for non-DM rooms.
- `forget` deletes the canonical row **and** the index entry; the audit keeps a content-free tombstone.
- Identity rekey is only `UPDATE memories SET person_id = …`; veto/audience/level/confirmed are carried along.

## 6. Consequences

**Positive**

- Memory no longer disappears silently when Mem0/Postgres is in trouble.
- Veto, level, audience, owner/subject become testable columns; rekey becomes a SQL operation.
- Backup, migration, and audit are back to normal (one Postgres, Alembic).
- Mem0 is still used for what is hard and valuable.
- The port keeps Mem0 behind an interface: changing the version or the adapter does not touch the caller, and the spine stays mandatory.

**Negative**

- It adds work: outbox, worker, idempotency, reconciliation, status.
- There are two writes (canonical + derived); the index can fall behind.
- Mem0's output shape remains a dependency on the adapter layer; contract tests and version pins are needed.
- An eval set is needed to assess extraction/retrieval quality.
- `AGENTS.md` must be changed: Mem0 stays the semantic engine, but not the data owner.

**Neutral**

- `mem0_history.db` (SQLite) stays as long as Mem0 is used: an **internal library cache**, derived, its path set by the adapter (`memory/mem0.py:157-182`), never read by the engine as truth, and safe to lose. The file holds `old_memory`/`new_memory` (the `mem0ai` package schema: the `history` and `messages` tables)
(including deleted text) and `messages.content`, so it **is subject to the purge rules of ADR-0005**:
"safe to lose" means it may be deleted at any time, not that it may keep a trace. `AGENTS.md` gives an
explicit clause for library caches like this.
- Extraction still calls the LLM; cost and latency must be measured, not assumed.

## 7. Staged implementation

| Stage | Contents | Acceptance gate |
|---|---|---|
| **0** | Fix the privacy P0: pre-filter L3/L4, subject veto, rekey preserves metadata | Privacy tests green; no L4 in the group prompt |
| **1** | `memory_events` + `cognition_jobs`; capture in one transaction; retry + status | Mem0 down → capture enters the queue; `/status` shows the backlog; nothing is lost |
| **2** | Mirror extracted facts into `memories`; retrieval filters on canonical columns | Rekey/veto/level pass tests; retrieval uses SQL filters |
| **3** | Ports `MemoryStore`/`MemoryExtractor`/`MemoryIndex`; `memory reindex` command; eval set | Recall/precision measured; rebuild is idempotent |
| **4** | Assess Mem0 quality as an extractor (prompt/adapter tuned; Mem0 stays because the spine is mandatory) | Data-driven decision on cost/latency/quality |

Ordering note: **default-on and any "remembering" claim may only be switched on after stages 0 and 1 are complete.**

## 8. Document changes and rules

- **`AGENTS.md`**: state Postgres as the memory system of record; Mem0 as `MemoryExtractor`/`MemoryIndex`; clarify the exception to "Semantic memory only through Mem0" and the "tools are removable" rule.
- **`TASK.md`**: add canonical store, outbox, reindex, and eval items.
- **`LOG.md`**: record this ADR as an architecture change, not just a feature.
- **`README.md`**: explain default storage, selective recall, controlled disclosure; do not claim to "always remember".
- **ADR home**: move the ADR to `docs/adr/` and allow it in `AGENTS.md` via a separate ADR.

## 9. Configuration

This umbrella ADR has no configuration key of its own; the concrete keys (capture trigger, batching, retention, group privacy, and so on) live in their respective derivative ADRs.

## 10. Metrics and gates

Thresholds and metrics (recall/precision, backlog, extraction cost/latency) are defined in the derivative ADRs, not here. The acceptance gate per implementation stage is in §7.

## 11. Acceptance criteria

- [ ] Capture is not lost when Mem0/Postgres is in trouble; retry and status exist.
- [ ] `memories` is the only source of truth for facts and security metadata.
- [ ] The index can be rebuilt from canonical without losing facts.
- [ ] Identity rekey preserves `vetoed`, `audience`, `level`, `confirmed`.
- [ ] A subject can veto facts about themselves, from any scope.
- [ ] L3/L4 does not enter a non-DM prompt without approval.
- [ ] `forget` removes canonical and index entries; audit tombstone without content.
- [ ] Recall/precision eval is available and run in CI.
- [ ] No monkey-patching of internal libraries without a version check.

## 12. Risks and mitigations

| Risks | Mitigation |
|---|---|
| Index drift (derived left behind) | Outbox + idempotency key + job reconciliation + `memory reindex` |
| Lock-in on Mem0 extraction | `MemoryExtractor` port; our own extraction prompts prepared as a fallback |
| Privacy: conversation content sent to the extraction LLM | Hard exclusion (credential/crisis), retention policy, operator documentation |
| Extraction cost/latency per turn | Deterministic prefilter before assess; batching; cost metric |
| Damage during migration | Shadow index + compare results before the swap; canonical is not changed |
| Mem0 changes the payload shape | Pin the version, contract-test the adapter, defensive parsing |

## 13. Interaction with other ADRs

This ADR is an umbrella ADR that links the derivative ADRs; the decision queue and the status of each ADR are in §14, while document interactions and rules (e.g. `AGENTS.md`, `README.md`) are in §8.

## 14. Decision queue and ADR status

The numbers and statuses of every ADR live in one place: **`docs/v0.1.0/README.md`** (the ADR home
index, ADR-0009). The long list is no longer maintained in this section.

## 15. Code references

- `monoagens/engine/memory/__init__.py:131-188` — `NullMemory`, `build_memory`.
- `monoagens/engine/memory/mem0.py:139-203` — `available`, config, monkey-patched client.
- `monoagens/engine/memory/mem0.py:246-290` — `_metadata` and merge order.
- `monoagens/engine/memory/mem0.py:496-531` — `set_privacy`, `veto`.
- `monoagens/engine/memory/rekey.py:15-62` — `RESERVED`, `_move`.
- `monoagens/engine/memory/context.py:127-131` — retrieval filter.
- `monoagens/engine/access/privacy.py:222-303` — capture and assess.
- `monoagens/engine/agent.py:614,906-921` — gate `can_memory` and spawn capture.
- `monoagens/engine/stores/affect.py` — a state-document pattern that can be copied for `memory_events`.

---

*This ADR lives at `docs/v0.1.0/adr/engine/0001-memory-source-of-truth.md`.*
