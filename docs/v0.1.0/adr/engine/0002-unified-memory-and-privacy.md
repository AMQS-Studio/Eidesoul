# ADR-0002: Unified Memory, Layered Privacy, and Detector Precision — Native PostgreSQL Hybrid Search and Word Boundaries

- **Status:** accepted
- **Date:** 2026-11-05
- **Deciders:** Muqsith (owner)
- **Context:** Re-baselined for v0.1.0; unifies the memory pipeline, retention, integrity, privacy, reflection, native hybrid retrieval, and detector precision (pre-rebaseline 0001–0006, 0008, 0010, 0020, 0026, 0033, 0038, 0043, 0051, 0052, 0053).
- **Supersedes:** pre-rebaseline 0001, 0002, 0003, 0004, 0005, 0006, 0008, 0010, 0020, 0026, 0033, 0038, 0043, 0051, 0052
- **Related:** ADR-0001 (sovereign core), ADR-0003 (turn lifecycle), ADR-0006 (sandbox guard)
- **Implementation:** implemented — native pgvector + tsvector hybrid retrieval with RRF, mem0 retired, word-boundary detectors, and value-shaped credential patterns with redaction.

---

## 1. Context

Earlier iterations of the memory and detection subsystems suffered from four structural defects:
1. **Write-Only Vector Index and Recall Failure:** `mem0ai` and its shadow `mem0_history.db` SQLite file
   received every memory write, but no read path ever called `index.search`. Instead, `MemoryStore.search`
   called `queries.match_rows`, which joined `ILIKE '%token%'` with `AND` across every word in the user's
   message. Natural questions like `"Apa warna favoritku sekarang?"` returned 0 results against
   `"Warna favoritku adalah biru tua"` because `"apa"` and `"sekarang"` were absent from the stored fact.
   Loading a second HuggingFace embedder inside Mem0 also wasted ~300 MB of idle RAM.
2. **Memory State Machine Deadlocks and N+1 Queries:**
   - `supersede:` candidates (`novelty="known"`, cosine >= 0.88) only consolidated when `scope="global"`
     and `skills.enabled=True`, freezing personal (`person:…`) and thread (`thread:…`) updates in
     `pending` forever.
   - `promote_staged` in `memory/disputes.py` compared UUID column `memories.id` with `"message:<id>"`
     strings in `refs`, so `staged` rows never promoted to `ok`.
   - `assess.py` extracted free-text names (e.g. `"Budi"`) into `memories.subject_id` while
     `MemoryStore.veto()` compared `subject_id` against canonical `per_…` IDs, breaking subject veto.
   - `MemoryStore.record_uses` executed up to 48 synchronous N+1 SQL queries at the end of every turn.
   - Alembic migrations `0013` and `0018` failed on `downgrade()` by recreating `memories_readable`
     with `SELECT *` before dropping columns on `memories`, and `ix_thread_people_person` was missing
     from `schema.py`.
3. **Epidemic of Substring and Context-Free False Detections:**
   - **Research Trigger (`FD-01..03`):** `word in body` matched substrings without word boundaries
     (`"obat"` in `"Halo sobat"`, `"kurs"` in `"kursi"`, `"hasil"` in `"berhasil"`, `"link"` in
     `"unlink"`, `"now"` in `"I know"`), forcing irrelevant web searches that failed citation verification
     and forced the bot to abstain. `EXACT` matched clock times (`10:00`), phone numbers (`0812-3456`),
     and chapter ranges (`1-3`). Furthermore, `trigger_reason` only read the `soul.language` pack,
     missing English queries sent to an Indonesian soul.
   - **Crisis Detector (`FD-04`):** Substring matching without negation or idiom checks triggered suicide
     crisis mode on `"lampu kamar mau mati"`, `"aku mau mati ketawa"`, `"HP-ku pengen mati"`, and
     `"I don't want to die"`.
   - **Secret Filter (`FD-05, FD-06`):** `guard.SENSITIVE` matched bare words (`"token listrik"`,
     `"otp"` inside `"hotpot"`, `"secret"` inside `"secretary"`, `"max_tokens"`), erasing messages to
     `""` and blocking settings reads. Conversely, `gate.structural` ignored `safety(language).sensitive`,
     letting `"kata sandi saya hunter2"` write directly into `memories.text` without clearing it when
     later marked `not_worth`.
   - **Prompt Injection (`FD-07`):** Left-only boundary regex blocked benign technical questions like
     `"how do I write a good system prompt?"` or `"cara aktifkan developer mode di Android"`.
   - **Privacy Evidence & Answer Parser (`FD-08, FD-09, FD-12`):** `library/privacy/*.json` labeled
     `"pukul 3 sore"` as violence (L4), `"maaf, aku yang salah"` as crime admission (L4), `"sakit hati"`
     as health (L4), `"koneksi putus"` as relationship conflict (L3), and ISO dates `2026-11-05` as
     phone numbers (L3). Presidio NER (`entity`) and MiniLM `centroid` were computed at high CPU/RAM cost
     but excluded from `POLICY_DETECTORS = ("regex",)`. Meanwhile, `_regex_answer` hijacked normal
     replies containing numbers `0..4` or `"biasa"` (e.g. `"oke nanti jam 3"`) as privacy level answers,
     and `access/privacy.py` blocked safe replies via an `or risky` fallback whenever an unconfirmed L4
     memory sat in the working set.
   - **Forget Intent (`FD-10`):** Token overlap in `maintenance.py` kept 4-letter stopwords (`"yang"`,
     `"soal"`), hard-deleting memories (including `global` scope) on `"hapus pesan yang tadi"`.

## 2. Decision drivers

1. **Single Store of Record and Search:** PostgreSQL + pgvector is the sole storage and retrieval engine.
2. **Natural Hybrid Retrieval:** Semantic similarity (MiniLM `vector(384)` with HNSW) combined with
   full-text search (`tsvector` with GIN) fused via Reciprocal Rank Fusion (RRF).
3. **Layered Privacy (L0–L4) & Subject Veto:** Deterministic evidence collection before privacy levels
   are evaluated; `level NULL` is never disclosed; canonical `subject_id` enables reliable subject veto.
4. **Footprint Efficiency:** Eliminating Mem0 saves ~300 MB RAM, keeping idle footprint at 350–500 MB.
5. **Detector Precision:** Detectors match whole tokens (`\b`), value-shaped secrets, and context-aware
   phrases across both language packs without hijacking everyday conversation.

## 3. Options considered

**A. Keep Mem0 and Wire `index.search` into `MemoryStore.search`.** Rejected: retains dual stores, dual
embedder loads, and `mem0_history.db` synchronization drift.

**B. Fix SQL `ILIKE` with `OR` and Stopword Lists.** Rejected: provides no semantic recall.

**C. Unified PostgreSQL Native Hybrid Store with Token-Boundary Detectors.** Accepted.

## 4. Decision

### 4.1 Schema, Native Hybrid Retrieval, and Mem0 Retirement

- A migration adds `embedding vector(384)` (HNSW index `vector_cosine_ops`) and `search_tsv tsvector`
  (GIN index) to `memories`. Embeddings are generated by the shared `monoagens/engine/embed.py` (384-dim
  MiniLM) when a candidate becomes `ok`.
- Configuring an embedder whose vector dimension is not `384` is rejected at startup load.
- `MemoryStore.search` runs a single SQL query over `memories_readable` combining cosine rank on
  `embedding` and OR-ranked `plainto_tsquery` on `search_tsv` fused via Reciprocal Rank Fusion (RRF),
  preserving strict scope (`global`, `person:<sender>`, `thread:<active>`) and privacy filters.
- `mem0ai`, `monoagens/engine/memory/mem0.py`, the Mem0 write branch in `writes.py`, and
  `mem0_history.db` are deleted, and removed from `pyproject.toml`, `uv.lock`, and `README.md` credits.
- Migrations `0013` and `0018` drop the dependent `memories_readable` view before dropping columns in
  `downgrade()`, and `ix_thread_people_person` is added to `monoagens/engine/schema.py`.

### 4.2 Single Write Port, State Machine Integrity, and Set-Based Usage Tracking

- All canonical writes pass through `MemoryStore` across the six states:
  `pending -> ok | staged | failed | not_worth | superseded`.
- `supersede:` candidate consolidation runs across every scope (`person:*`, `thread:*`, `global`)
  regardless of whether `skills.enabled` is true.
- `promote_staged` matches message references against `source_message_id` (or validated UUIDs) rather
  than passing `"message:<id>"` strings into `memories.c.id.in_(...)`.
- When `cognition.py` extracts a memory, `subject_id` is resolved to the canonical `per_…` ID whenever
  the subject matches the speaker or a known thread participant, restoring `MemoryStore.veto()` and
  contradiction checks in `reflect.py`.
- `MemoryStore.record_uses` is rewritten as a single set-based SQL statement per turn instead of up to
  48 sequential N+1 queries.

### 4.3 Layered Privacy (L0–L4) and Active Evidence Layers

- Privacy levels L0–L4 remain a core invariant; L5 (credentials) is never stored, and `level NULL`
  acts as a quarantine sentinel never disclosed to LLMs.
- Every active detector layer in `evidence.py` (`regex`, Presidio `entity`, MiniLM `centroid`) either
  contributes to `privacy.decide()` via `POLICY_DETECTORS` or is not computed, eliminating wasted CPU/RAM.
- In `access/privacy.py`:
  - The `or risky` fallback in mention auditing is removed so a reply that does not mention an
    unconfirmed L4 memory is never blocked merely because that memory was in context (`FD-12`).
  - `_regex_answer` accepts only an explicit answer: a standalone `"0"`–`"4"`, a level word as the
    entire message, or `/privacy`, never hijacking normal sentences like `"oke nanti jam 3"` (`FD-09`).

### 4.4 High-Precision Detectors and Word Boundaries

- **Whole-Token Matching (`FD-01`):** All word-list detectors in `turn/research.py`, `guard/patterns.py`,
  and `memory/evidence.py` match whole tokens (`\b` or token sets); substring checks (`word in body`)
  are removed.
- **Value-Shaped Secrets & Language Pack Gate (`FD-05, FD-06`):** `guard.SENSITIVE` requires a credential
  key followed by `:` or `=` and a value of >= 6 characters, or known token prefixes (`sk-`, `ghp_`,
  `AIza`, `xox[baprs]-`). Bare mentions like `"token listrik"`, `"hotpot"`, `"secretary"`, or
  `"max_tokens"` are never erased or blocked in `settings.py`. `gate.structural` also checks
  `safety(language).sensitive` (`"kata sandi"`, `"kredensial"`), and any row later marked `not_worth`
  for a credential has its `memories.text` and `memory_events.content` cleared.
- **Narrowed Research Trigger & Dual-Pack Lookup (`FD-01, FD-02, FD-03`):** Polysemous words
  (`"sekarang"`, `"hari ini"`, `"hasil"`, `"jadwal"`, `"kontak"`, `"alamat"`, `"obat"`, `"harga"`,
  `"kurs"`) are removed from forced triggers. `EXACT` keeps currencies, percentages, and full dates
  while dropping clock times (`10:00`), phone numbers, and numeric ranges (`1-3`). Both `id` and `en`
  language packs are consulted regardless of `soul.language`.
- **Negation-Aware Crisis & Context-Aware Injection (`FD-04, FD-07`):** Crisis detection checks a
  negation window and per-language idiom list (`"lampu mau mati"`, `"mati ketawa"`, `"I don't want to die"`).
  Prompt injection regex enforces a right word boundary and allows technical terms (`"system prompt"`,
  `"developer mode"`) unless paired with an override/ignore verb.
- **Context-Bounded Privacy Patterns & Assistant Tells (`FD-08, FD-12`):** Patterns in
  `library/privacy/{id,en}.json` for `pukul`, `aku yang salah`, `sakit`, `putus`, `jatuh`, `kill`,
  `hit`, `sick`, and ISO dates require context words. Assistant-tell regex in `character.py` adds a
  right word boundary so `"Tentu dong!"` / `"Tentunya"` is not flagged.
- **Stopword-Filtered Forget Intent (`FD-10`):** `forget` token matching in `maintenance.py` filters
  stopwords, requires at least two content tokens, and protects `global` memories from accidental
  deletion.

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

- Natural-language recall test (`"Apa warna favoritku sekarang?"`) retrieves `"Warna favoritku adalah biru tua"`
  in the top results in both Indonesian and English.
- False-detection fixture suite (`ST-06` and `FD-01..FD-12` reproducers) asserts zero false positives,
  paired with a true-positive fixture suite for real credentials, crisis phrases, and injections.
- Alembic downgrade round-trip test (`head -> 0010 -> head`) succeeds on a populated database.
- Idle RSS memory stays within 350–500 MB (without optional NER) and under the `tests/test_privacy_budget.py` ceiling.

## 7. Acceptance criteria

- [ ] `memories` has `embedding vector(384)` (HNSW) and `search_tsv tsvector` (GIN) behind an Alembic migration.
- [ ] Zero `mem0` imports, dependencies, or credit rows remain in the repository.
- [ ] All everyday sentences in `FD-01..FD-12` pass through as normal chat without false triggers.
- [ ] `"kata sandi saya hunter2"` is blocked at intake and never persists in `memories.text`.
- [ ] `supersede:` candidates consolidate for `person:*`, `thread:*`, and `global` scopes.
- [ ] `promote_staged` promotes staged memories using `source_message_id`, and `veto()` works for known speakers.
- [ ] `record_uses` executes a single set-based SQL update per turn.

## 8. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Existing `ok` rows lack embeddings after upgrade | `monoagens backfill` embeds existing `ok` rows once |
| Negation window misses a real crisis phrase | Narrow negation list gated by true-positive crisis test fixtures |
| Context words slightly lower regex privacy recall | Presidio NER and structured LLM assessment (`assess_batch`) cover semantic sensitivity |

## 9. Interaction with other ADRs

- **ADR-0001:** Enforces PostgreSQL as the single system of record and `MemoryStore` as the single write port.
- **ADR-0003:** Offloads all `embed.encode` and `MemoryStore` calls to `await io.run()` with atomic lease fencing.
- **ADR-0006:** Shares deterministic command and pattern guard primitives.

## 10. Code references

- `monoagens/engine/schema.py`, `migrations/versions/` — `memories` table, indexes, and view migrations.
- `monoagens/engine/memory/store.py`, `queries.py`, `writes.py`, `disputes.py`, `maintenance.py`
- `monoagens/engine/memory/evidence.py`, `privacy.py`, `gate.py`, `intent.py`
- `monoagens/engine/access/privacy.py` — disclosure auditor and `_regex_answer`.
- `monoagens/engine/turn/research.py`, `monoagens/engine/guard/patterns.py`, `monoagens/engine/character/character.py`
- `monoagens/library/privacy/`, `monoagens/library/lexicon/`, `monoagens/library/safety/`

---

*This ADR lives at `docs/v0.1.0/adr/engine/0002-unified-memory-and-privacy.md`.*
