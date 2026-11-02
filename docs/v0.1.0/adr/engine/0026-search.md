# ADR-0026: Full-Text Search of Messages, Trigrams, and Scope

- **Status:** accepted
- **Implementation:** draft — search still uses `ILIKE`; there is no `text_tsv`/trigram
- **Date:** 2026-11-03
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Context:** message archive search is only `ILIKE` per term with no text index, so its cost grows linearly with the number of messages and recall depends exactly on substrings; the memory path has its own engine (Mem0), and the read scope stays restricted to room membership.
- **Supersedes:** —
- **Related:** ADR-0001 (Mem0 as the only memory path), ADR-0006/0007 (privacy & read scope, §4.5), ADR-0016 (DDL & index owner), ADR-0019 (metric), ADR-0021 (tool output contract)
- **Location note:** ADR lives in `docs/v0.1.0/adr/engine/`; the Indonesian copy is `docs/v0.1.0/adr/engine/id/0026-search.md`.

---

## 1. Context

| Fact | Evidence |
|---|---|
| Message search uses `ILIKE` per term over `text`/`sender_name`, `limit` ≤ 50 | `eidesoul/engine/stores/history.py:318-347` |
| Command `/cari` calls that store for the active room | `eidesoul/engine/commands/cari.py:25-40` |
| Tool `chat_history` can search other rooms' archives, with the `cross_reads` buffer | `eidesoul/engine/tools/chat_history.py:60-118` |
| Message index: `(chat_id, ts, id)` and `(sender_address)` — no text index | `eidesoul/engine/schema.py:66-67` |
| Memory has its own path (Mem0 + `memories_readable`), not `messages` | `eidesoul/engine/commands/memori.py:69,99`, ADR-0001 |
| Read scope = room membership + the `visibility` gate | ADR-0007 §4.5 |
| `pgvector` is already a precedent extension in the baseline | ADR-0001/0016 |
| Tool output has caps and a `next` marker | ADR-0021 |

Two things that **do not** change with this ADR: the read scope (still room membership, no override) and memory ownership (still Mem0). This ADR only changes how text is searched and how the cost is measured.

## 2. Decision drivers

| Driver | Consequences |
|---|---|
| Search cost is not linear | Full-text index, not `ILIKE` over the whole table |
| Recall for Indonesian | FTS without a dictionary + trigram as a second net |
| Scope must not expand | The `chat_id` filter + membership stay in the same query |
| Zero LLM decisions | Deterministic ranking; no model rerank |
| One DDL owner | Derived column and index go through ADR-0016 |

## 3. Options considered

| Options | Summary | Value |
|---|---|---|
| **A. Keep `ILIKE`** | No index | Rejected: linear cost; recall only by substring |
| **B. FTS only (`simple`)** | `tsvector` + GIN | Rejected as the only path: Indonesian morphology is unhandled |
| **C. FTS + `pg_trgm`** | FTS for exact tokens, trigrams for affixes/typos | **Selected** |
| **D. Semantic search over `messages`** | Embedding every message | Rejected: embed cost per message and a second retrieval path; memory stays owned by Mem0 (ADR-0001) |

## 4. Decision

### 4.1 Full-text search for messages

Derived column `messages.text_tsv = to_tsvector('simple', coalesce(text,'') || ' ' || coalesce(sender_name,''))` with a GIN index. The query uses `websearch_to_tsquery('simple', :q)`, ranking `ts_rank_cd` with tiebreak `ts DESC, id DESC`.

### 4.2 `simple`, not language stemming

The soul catalog can be multilingual and the engine does not choose a language for the operator. Consequently Indonesian morphology is not handled by FTS; that is the job of §4.3, not a reason to add per-language dictionaries to the engine.

### 4.3 `pg_trgm` as the second net

`similarity()` + the `gin_trgm_ops` index for: messages (fallback when FTS is empty), `people.display_name`, and short facts. Threshold `engine.search.trgm_threshold: 0.3`. The order is fixed: FTS first; trigram is only invoked when FTS returns no result, so the cost and quality of both can be compared.

### 4.4 Scope unchanged

Message search is always restricted by `chat_id` **and** the asker's membership (`rooms_of`, ADR-0007 §4.5) plus the `visibility` gate. There is no cross-room search beyond that, including for `administrator`. Memory still goes through Mem0 + the privacy filter (ADR-0001/0008): this ADR **does not** add a second memory path and does not change `commands/memori.py`.

### 4.5 Output follows ADR-0021

Concise mode by default (`ts`, sender, ≈`snippet_chars` characters), `next: before=<ts>` when the result is truncated, `limit` default 20 and maximum 50 as today, without sending the whole content of 50 messages to the prompt.

### 4.6 DDL through ADR-0016

`CREATE EXTENSION IF NOT EXISTS pg_trgm` enters the baseline; the derived column + two indexes enter the migration order. If the extension is unavailable or cannot be created, boot **fails** with a clear `error:` — not a silent degradation to `LIKE`.

### 4.7 No LLM rerank

Search results are deterministic. Reranking with a model means a second call the asker did not request and cannot see; if it is truly needed later, that is a new ADR.

## 5. Configuration

```yaml
engine:
 search:
 fts: true
 lang: simple
 trgm: true
 trgm_threshold: 0.3
 max_results: 50 # hard cap; default per panggilan 20
 snippet_chars: 240
```

## 6. Metrics and gates

| Metric | Start target |
|---|---|
| `search_latency_ms` p95 | < 150 ms on a fixture of 100 000 messages |
| `fts_hits_total` / `trgm_fallback_ratio` | a visible ratio; drops once the index is filled |
| `search_no_result_total` | present; used to tune `trgm_threshold` |
| `search_backend` | `fts+trgm` (default) or `like` (only when config disables it) |
| `search_scope_denied_total` | > 0 only when there is an attempt outside the scope — that is a signal, not normal |

## 7. Acceptance criteria

- [ ] An exact token query returns the right rows via FTS (not `ILIKE`).
- [ ] A query with affixes/a typo returns results via trigram (fixture).
- [ ] A search in room A never returns room B rows; attempts from non-members are rejected, including for `administrator`.
- [ ] Truncated results write `next: before=<ts>` and follow-up calls do not duplicate rows.
- [ ] Without a `pg_trgm` that can be created, boot fails with a clear message; `/status` reports `search_backend`.
- [ ] The memory path does not change: `commands/memori.py` keeps using Mem0 + privacy, with no new query to `messages`.

## 8. Risks and mitigations

| Risks | Mitigation |
|---|---|
| The derived column adds message write cost (hot path) | Measured before/after; index filling goes through the ADR-0016 migration |
| `simple` gives low recall for affixed languages | The trigram net closes it; `trgm_fallback_ratio` stays a metric |
| Trigram matches across lines within the same room | Scope stays `chat_id`; there is no change in reach |
| The extension is unavailable on managed Postgres | Explicit boot failure; `search.fts: false` may only be chosen deliberately by the operator |
| The index is stale after a migration | `db check` reports the schema version + the presence of the index/extension |

## 9. Interaction with other ADRs

- **ADR-0001**: one memory path (Mem0, `memories_readable`) stays intact; this ADR does not touch it.
- **ADR-0007 §4.5**: `rooms_of` + `visibility` is the scope gate used as is.
- **ADR-0016**: DDL owner; the extension, derived column, and index enter the migration order there.
- **ADR-0021**: output cap and the `next` marker for search results.
- **ADR-0019**: `search_latency_ms` and the backend ratio enter the existing metric surface.

## 10. Code references

- `eidesoul/engine/stores/history.py:318-347` — `search()` today (`ILIKE`, `limit` ≤ 50); the query replacement point.
- `eidesoul/engine/commands/cari.py:25-40` — `/cari` (the only command caller of `history.search`).
- `eidesoul/engine/tools/chat_history.py:60-118` — `_target`/`_remember_cross`/`_search` (other rooms' archives + the `cross_reads` buffer).
- `eidesoul/engine/schema.py:44-67` — the `messages` table and the indexes `ix_messages_chat_ts`/`ix_messages_sender`.
- `eidesoul/engine/commands/memori.py:69,99` — the unchanged memory path (Mem0).
- `eidesoul/engine/config.py` — new config block `engine.search` following the `SandboxConfig`/`WebSearchConfig` pattern.

---

*This ADR lives at `docs/v0.1.0/adr/engine/0026-search.md`.*
