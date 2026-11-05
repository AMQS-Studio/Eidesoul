# ADR-0051: Native Memory Retrieval — pgvector and Full-Text in `memories`, Mem0 Retired

- **Status:** accepted
- **Date:** 2026-11-05
- **Deciders:** Muqsith (owner)
- **Context:** the Mem0 vector collection is written but never read, and recall is an `AND` of `ILIKE` terms; the owner targets a 350–500 MB idle footprint on a small server.
- **Supersedes:** —
- **Related:** ADR-0001 (memory source of truth), ADR-0004, ADR-0010, ADR-0026 (search), ADR-0048 (single-tenant), ADR-0049 (audit remediation)
- **Implementation:** draft — the schema column, the query, and the Mem0 removal are not done.
- **Location note:** the change binds the memory package, so this ADR sits under `adr/engine/`. It amends the Mem0 role in ADR-0001 §4; the Postgres source of truth and `MemoryStore` as the single write port are unchanged.

---

## 1. Context

`MemoryStore.search` calls `queries.match_rows`, which requires every token of the user message to match
`ILIKE '%token%'`. "Apa warna favoritku sekarang?" finds nothing against "Warna favoritku adalah biru
tua", because "apa" and "sekarang" are not in the memory. The Mem0 pgvector collection and its
`mem0_history.db` SQLite file receive every write but no read path calls `index.search`. Cognition
already does its own assessment (`assess_batch`), so Mem0 is neither the extractor nor the retriever.
It costs a second model load (about 300 MB), a second store, and the sync code in `writes.py` and
`mem0.py`.

## 2. Decision drivers

1. Natural questions must recall natural facts.
2. One store, one truth: no drift between `memories` and a shadow index.
3. Lower idle memory and fewer dependencies.
4. The privacy path is untouched: reads go through `memories_readable`, and a `NULL` level is never used.
5. Cross-thread rules stay: `global` + `person:<sender>` + `thread:<this thread>` only.

## 3. Options considered

**A. Wire Mem0 search into `MemoryStore.search`.** Rejected: keeps the second store, the second model, and
the history database for no gain.

**B. Fix `ILIKE` with `OR` and stopwords.** Rejected: still no semantic match, and it needs per-language
word lists.

**C. Native hybrid search in `memories`.** Accepted.

## 4. Decision

### 4.1 Schema

A migration adds `embedding vector(384)` with an HNSW index and `search_tsv tsvector` with a GIN index to
`memories`. The embedding comes from the shared `monoagens/engine/embed.py` (MiniLM, 384 dim) when a
candidate becomes `ok`. `SCHEMA_VERSION` mirrors the new Alembic head.

### 4.2 Query

`MemoryStore.search` runs one SQL statement over `memories_readable`: cosine rank on `embedding` and an
OR-ranked `tsvector` match, fused by Reciprocal Rank Fusion, with the existing scope and privacy filters.
A question word cannot veto a result.

### 4.3 Removal

`mem0ai`, `monoagens/engine/memory/mem0.py`, the Mem0 write branch in `writes.py`, and
`mem0_history.db` are deleted. `pyproject.toml` and `uv.lock` change in the same commit, and the README
credits table (both languages) drops the Mem0 row. A vector dimension other than 384 is refused at
load, since the column is fixed.

### 4.4 Related memory fixes

`supersede:` consolidation runs for every scope; `promote_staged` matches the real source message id;
`subject_id` is resolved to the canonical `per_…` when the subject is a known speaker.

## 5. Configuration

No new key. `engine.embed` keeps the 384-dim MiniLM; a different embedder needs its own ADR and a column
migration.

## 6. Metrics and gates

- A recall test with natural questions ("Apa warna favoritku?") over stored statements, in both
  languages.
- Idle RSS measured before and after; target 350–500 MB without privacy NER, recorded next to the
  existing 1536 MiB cap in `tests/test_privacy_budget.py`.
- The existing recall/precision eval of ADR-0001 stays green.

## 7. Acceptance criteria

- [ ] `memories` has `embedding` and `search_tsv` with indexes, behind one migration.
- [ ] `grep -r mem0` finds no code, dependency, or credit row.
- [ ] A natural question retrieves its fact in the top result.
- [ ] No read path bypasses `memories_readable`.

## 8. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Backfill of existing rows | A `monoagens backfill` step embeds `ok` rows once |
| HNSW build cost on large tables | Build the index after the backfill, in the migration |
| Losing Mem0's extraction | It was never on the production path; cognition owns extraction |

## 9. Interaction with other ADRs

ADR-0001 keeps Postgres as canonical and the write port; its Mem0 intelligence role is retired here.
ADR-0026 search stays for the operator search tool. ADR-0038 privacy layers are unchanged and share the
embedder.

## 10. Code references

- `monoagens/engine/memory/queries.py` — `match_rows`.
- `monoagens/engine/memory/store.py` — `search`.
- `monoagens/engine/memory/writes.py`, `mem0.py` — the write branch to remove.
- `monoagens/engine/schema.py` — `memories`.
- `monoagens/engine/embed.py` — the shared embedder.

---

*This ADR lives at `docs/v0.1.0/adr/engine/0051-native-memory-retrieval.md`.*
