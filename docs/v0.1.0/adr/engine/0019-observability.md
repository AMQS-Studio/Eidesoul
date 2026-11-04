# ADR-0019: Observability — Structured Log, Token Accounting, and `/status`

- **Status:** accepted
- **Implementation:** implemented — evidence: `engine/observability.py` (contextvars, JSON/text formatter, redaction), `turn_id` bound through `agent.handle` into every `llm_calls` row, `/status` §4.4 card from real rows, `llm_calls` retention, `tests/test_observability.py`; gate result: `pytest -q` 785 passed
- **Date:** 2026-11-03
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Context:** review (`Eidesoul-Review-Arsitektur-2026-11-01.md`) §15 P2-11 — the only logging is free-text `logging.basicConfig` with no correlation; `llm.run` never reads `response.usage`, so tokens/cost are never measured; there is no counter, queue, or latency an operator can see without guessing from the log
- **Supersedes:** —
- **Related:** ADR-0013 (`turns`, `turn_id`), ADR-0003 (`cognition_jobs`, queue depth), ADR-0014 (`automation_runs`), ADR-0011 (shutdown/ledger), ADR-0015 (redaction), ADR-0016 (DDL `llm_calls`), ADR-0017 (timeout/`standby_refused`)
- **Location note:** ADR lives in `docs/v0.1.0/adr/engine/`; the Indonesian copy is `docs/v0.1.0/adr/engine/id/0019-observability.md`.

---

## 1. Context

| Fact | Evidence |
|---|---|
| The log is free text only, with no correlation id | `__main__.py:22` `logging.basicConfig(level=INFO, format="%(asctime)s %(levelname)s %(message)s")` |
| Tokens are never read | `llm.py:150-200` returns only `content`; there is no `response.usage` |
| No metrics | no counter/latency in `monoagens/`; `ablate.replay_metrics` is offline analysis only |
| Diagnosis today | read the log and guess; cannot answer "how many tokens this turn" or "how deep the cognition queue" |

This is not tidiness: without measured tokens, the cost decisions in ADR-0014/0015 (budget, `run_budget_seconds`, compaction) run without feedback; without `turn_id`, a single user complaint cannot be traced to a single turn.

## 2. Decision drivers

- Diagnosis must be possible **without reading message content** (privacy ADR-0006/0008, redaction ADR-0015 §4.7).
- LLM cost must be measured per call and per turn, not felt.
- No new dependency and no HTTP: AGENTS forbids an HTTP gateway; operators use commands and logs.
- The correlation must survive cross-process for background work (cognition jobs, automation runs).

## 3. Options considered

| Options | Contents | Cost | Risks | Verdict |
|---|---|---|---|---|
| **A. Structured log + in-process counter + `/status` + token accounting** | stdlib only | small-medium | no Prometheus export (deliberate) | **Selected** |
| **B. OTel now (extra)** | SDK + exporter | medium | a dependency and infrastructure assumptions the v0.1.0 user does not have | Deferred |
| **C. Tidy up the log only** | a better format | small | tokens, queues, and latency remain unmeasured | Rejected |

## 4. Decision

### 4.1 Selectable log format, uniform mandatory fields

- `engine.logging.format: text | json` (default `text`). `json` uses the stdlib formatter (no dependency), one object per line.
- Mandatory fields on every line: `ts`, `level`, `event` (stable keys, not free sentences), `logger`; optional: `turn_id`, `thread_id`, `job_id`, `run_id`, `gateway`, `duration_ms`.
- **No message content, memory text, archive quotes, or people's names** in the log — only ids and numbers. Enforced by tests (see §7).

### 4.2 `turn_id` as the red thread

- Created at the start of `handle()` (also used as `turns.id`), passed on through `contextvars` so that every log inside the turn carries it without extra parameters.
- Background work carries `job_id`/`run_id` (ADR-0003/0014) and **not** another turn's `turn_id`; there is no context mixing between tasks.

### 4.3 Token accounting per call

- `llm.run`, `llm.complete`, and background calls read `response.usage` (prompt/completion) and write **one row** in `llm_calls` per call: `turn_id` (null for background), `job_id`/`run_id`, `purpose` (`decision`/`reply`/`polish`/`digest`/`extract`/`reflect`/`research`), `model`, `tokens_in`, `tokens_out`, `latency_ms`, `ok`.
- The column shape, indexes, and retention **belong to ADR-0016** (§4.6); this ADR only sets the contents and their use.
- There is no price table in the engine (models are never pinned; AGENTS). The operator computes cost from `model` + tokens.

### 4.4 In-process counters, not an exporter

Mandatory counters (raised in memory, read by `/status`):

| Counter | Source |
|---|---|
| `turns_total`, `turn_errors_total`, `turn_timeout_total` | ADR-0013/0017 |
| `standby_refused_total` | ADR-0017 §4.4 |
| `llm_calls_total`, `llm_tokens_in_total`, `llm_tokens_out_total` | ADR-0019 §4.3 |
| `queue_depth` (read from `cognition_jobs`) | ADR-0003 |
| `rich_missing_total`, `directory_missing_total`, `participants_unknown_total` | ADR-0018 |
| `memory_hits_total`, `memory_staged_total` | ADR-0005/0008 |
| `automation_runs_total`, `automation_missed_total` | ADR-0014 |
| `turn_latency_ms` p50/p95 (ring `window_seconds`) | ADR-0013 |

### 4.5 `/status` (owner) as the only surface

- The existing `status` command is extended: mode/primary, `generation`, uptime, schema version, the counters in §4.4, and rough buckets (e.g. "queue: medium") — raw numbers are for the owner role only.
- No HTTP server, no `/metrics` endpoint. External monitoring integration reads the JSON log (`turn_id`, `event`) and the `llm_calls` table.
- Redaction: `status` never displays the DSN, a key, or memory content (ADR-0015 §4.7 remains valid).

## 5. Configuration

```yaml
engine:
 logging:
 format: text # text | json
 level: INFO
 metrics:
 enabled: true
 window_seconds: 300 # ring window for p50/p95 turn latency
 retention_days: 90 # `llm_calls` rows older than this are deleted by the ADR-0005 purge job
```

## 6. Metrics and gates

| Metric | Definition | Gate |
|---|---|---|
| `log_json_valid` | every line in json mode can be parsed | 100% on smoke |
| `log_content_leak` | a log line containing message/memory text | **0** (the test inspects records) |
| `llm_usage_missing` | a completed LLM call with no `llm_calls` row | 0 |
| `turn_latency_p95_ms` | p95 turn latency in the window | reported, not a hard gate |
| `status_administrator_only` | `/status` from a non-administrator role | rough buckets only |

## 7. Acceptance criteria

- [ ] `engine.logging.format=json`: every line is valid JSON with the mandatory fields of §4.1.
- [ ] Every log inside a turn carries the same `turn_id`; background work carries `job_id`/`run_id` and does not carry another turn's `turn_id`.
- [ ] Test: one turn containing a secret message → not a single log record contains that text.
- [ ] Every completed LLM call produces exactly one `llm_calls` row with tokens > 0 (unless the provider does not return usage → `ok=false`, `tokens_*` null, and still recorded).
- [ ] `/status` shows mode, `generation`, counters, and queue buckets; a non-administrator role receives only rough buckets.
- [ ] `turn_latency_ms` p50/p95 can be read after at least 5 turns.
- [ ] No new dependency in `pyproject.toml` for this ADR.

## 8. Risks and mitigations

| Risks | Mitigation |
|---|---|
| The log leaks the contents of the conversation | rule §4.1 + test `log_content_leak=0`; text may only enter tables/archives |
| Cardinality explodes (`thread_id` as a label) | `thread_id` is a log field only, never a metric label; counters are aggregate-based |
| `contextvars` leak between tasks | `turn_id` is reset at the start of every task; background work clears the context |
| Token not available from the provider | the row is still written with `ok=false`; no false estimate |
| JSON log overhead | format `text` remains the default; `json` for the operator who needs it |

## 9. Interaction with other ADRs

- **ADR-0016** — owner of the `llm_calls` DDL (§4.6); this ADR writes no DDL.
- **ADR-0013** — `turn_id` is the turn id; the turn status remains the single source of truth.
- **ADR-0003** — `queue_depth` is read from `cognition_jobs`, not a copied counter.
- **ADR-0014** — `automation_runs` already records runs; counters add aggregates, not duplicated data.
- **ADR-0015** — the redaction of `settings`/`status` follows the same rules.
- **ADR-0017** — `turn_timeout_total` and `standby_refused_total` are born there, read here.

## 10. Code references

- `monoagens/__main__.py:22` — the only logging configuration at this time.
- `monoagens/engine/llm.py:150-200` — `run()` returns `content`; the read point for `response.usage`.
- `monoagens/engine/agent.py:413` — the start of `handle()` (where `turn_id` is created).
- `monoagens/engine/schema.py` — tables `turns`/`cognition_jobs` (source of `turn_id` and `queue_depth`).
- `monoagens/engine/commands/status.py` — the extended `status` command.
- `monoagens/engine/db.py:24` — `redact_dsn` (used for the `/status` redaction).

---

*This ADR lives at `docs/v0.1.0/adr/engine/0019-observability.md`.*
