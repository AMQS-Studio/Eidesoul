# ADR-0002: Unified Memory and Layered Privacy — Native PostgreSQL, Hybrid Retrieval, and Mem0 Retired

- **Status:** accepted
- **Date:** 2026-11-05
- **Deciders:** Muqsith (owner)
- **Context:** Re-baselined for v0.1.0; unifies the memory pipeline, retention, integrity, privacy, and reflection (pre-rebaseline 0001–0006, 0008, 0010, 0020, 0038) with the audit findings in `docs/audit.md` and native retrieval in pre-rebaseline 0051 and 0052.
- **Supersedes:** pre-rebaseline 0001, 0002, 0003, 0004, 0005, 0006, 0008, 0010, 0020, 0038, 0051, 0052
- **Related:** ADR-0001 (sovereign core), ADR-0003 (turn lifecycle)
- **Implementation:** partial — canonical PostgreSQL schema and privacy policies are implemented; native hybrid retrieval and Mem0 retirement are scheduled in Phase P1.

---

## 1. Context

In earlier iterations, memory intelligence relied on the `mem0ai` package and an SQLite shadow database
(`mem0_history.db`). The architectural audit (`docs/audit.md` §1.3.1, §3.1) revealed that:
1. Mem0 vector embeddings were written to a separate table but never queried during retrieval.
   Instead, `MemoryStore.search` fell back to SQL `ILIKE '%token%'` joined by `AND`, failing natural
   language questions (e.g., "Apa warna favoritku?" failed to recall "Warna favoritku biru").
2. Loading dual embedder instances (MiniLM in core and in Mem0) consumed ~300 MB extra RAM.
3. Substring matching without word boundaries (`\b`) produced severe false detections (e.g. "obat" in
   "sobat", "otp" in "hotpot", "pukul 3 sore" marked as violence L4).
4. Memory update candidates (`supersede:`) froze permanently for personal scopes because consolidate
   jobs only scheduled for `scope="global"` when `skills.enabled=True`.

## 2. Decision drivers

1. **Single Store of Record and Search:** PostgreSQL + pgvector is the single storage and retrieval engine.
2. **Natural Hybrid Retrieval:** Semantic similarity (MiniLM `vector(384)` with HNSW) combined with
   full-text search (`tsvector` with GIN) fused via Reciprocal Rank Fusion (RRF).
3. **Layered Privacy (L0–L4):** Deterministic evidence collection before privacy levels are evaluated;
   memories with `level NULL` are strictly hidden from LLMs; the subject holds veto power.
4. **Footprint Efficiency:** Idle process memory must stay within 350–500 MB on a low-cost VPS.
5. **Detector Precision:** All keyword gates must use whole-token boundaries (`\b`) and value-shaped
   secret patterns (no false positives on common words like "token listrik").

## 3. Options considered

**A. Status Quo (Postgres Canonical + Mem0 Intelligence).** Rejected: write-only vector index, dual
model memory overhead, SQLite sync drift, and natural recall failure.

**B. External Vector Database (Qdrant/Milvus/Pinecone).** Rejected: violates the single system of
record invariant (Postgres + pgvector).

**C. Unified PostgreSQL Native Hybrid Store.** Selected: eliminates Mem0 and SQLite, embeds directly
via the shared MiniLM embedder, and queries `memories_readable` with cosine + FTS ranking.

## 4. Decision

### 4.1 Schema and Native Hybrid Retrieval

The canonical `memories` table holds facts, security metadata, and search representations:
- `embedding vector(384)` indexed with HNSW (`vector_cosine_ops`).
- `search_tsv tsvector` indexed with GIN.
- `mem0ai`, `monoagens/engine/memory/mem0.py`, and `mem0_history.db` are permanently retired.
- `MemoryStore.search` executes a single SQL query over `memories_readable` combining cosine distance
  and `plainto_tsquery` ranking via Reciprocal Rank Fusion.

### 4.2 Single Write Port and Six-State Lifecycle

All canonical writes pass through `MemoryStore`:
- States: `pending -> ok | staged | failed | not_worth | superseded`.
- Memories with `level NULL` act as sentinels and are never disclosed in prompts.
- `supersede:` candidate consolidation runs across all scopes (`person:*`, `thread:*`, `global`).
- `promote_staged` compares UUID references correctly with `source_message_id`.

### 4.3 Layered Privacy and Subject Veto

- Privacy levels range from L0 (public) to L4 (sensitive/secret). Level 5 (credentials) is never stored.
- Deterministic evidence (`Evidence`) is collected locally (word lists, Presidio NER, MiniLM centroid)
  and persisted in `memories.privacy_evidence`.
- The subject of a personal memory (`subject_id` matching canonical `per_...`) holds absolute veto power
  via `MemoryStore.veto()`.

### 4.4 High-Precision Detectors

- Keyword matching in `research.py`, `patterns.py`, and `evidence.py` enforces word boundaries (`\b`).
- Secret filters require value-shaped patterns (`key: value` or token prefixes like `sk-`, `ghp_`).
  Mentions of concepts like "token listrik" or "max_tokens" are never erased.

## 5. Configuration

Configured in `config.yaml`:
```yaml
engine:
  memory:
    capture:
      enabled: true
      min_chars: 8
    retrieval:
      max_results: 12
      hybrid_rrf_k: 60
```

## 6. Metrics and gates

- Precision/recall evaluation: natural language queries over stored facts achieve recall >= 0.95.
- Idle RSS memory budget measured <= 500 MB (verified in `tests/test_privacy_budget.py`).
- Word-boundary test suite asserts no false triggers on everyday Indonesian/English phrases.

## 7. Acceptance criteria

- [ ] `memories` table contains `embedding` (HNSW) and `search_tsv` (GIN) columns.
- [ ] Natural language questions ("Apa warna favoritku?") retrieve correct facts in the top 3 hits.
- [ ] No `mem0` imports, dependencies, or database files remain.
- [ ] Everyday conversational inputs ("Halo sobat", "lampu kamar mau mati") trigger zero false alarms.

## 8. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Migration backfill for existing rows | Run `monoagens backfill` to embed existing `ok` rows once |
| Embedding generation load during turns | Offload `embed.encode` to worker threads via `await io.run()` |

## 9. Interaction with other ADRs

- **ADR-0001:** Enforces single PostgreSQL system of record without tenant columns.
- **ADR-0003:** Memory writes are fenced with atomic lease generation tokens.

## 10. Code references

- `monoagens/engine/schema.py` — `memories` table definition and pgvector columns.
- `monoagens/engine/memory/store.py` — `MemoryStore` single write port and search.
- `monoagens/engine/memory/privacy.py` — deterministic L0–L4 privacy evaluations.
- `monoagens/engine/embed.py` — shared 384-dimensional MiniLM embedder.

---

*This ADR lives at `docs/v0.1.0/adr/engine/0002-unified-memory-and-privacy.md`.*
