# ADR-0002: Capture Trigger — Deterministic Gate, Novelty Check, Batch Assess

- **Status:** accepted
- **Implementation:** implemented — evidence: `memory/gate.py` (structural gate + dedupe), `memory/assess.py` (batch assess), `memory/store.py` novelty on the shared 384-d embedder, intake seam wired to `cognition_jobs`, `tests/test_cognition.py`; gate result: `pytest -q` 648 passed
- **Date:** 2026-11-01
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Context:** follow-up to ADR-0001 (Postgres canonical + Mem0 as `MemoryExtractor`/`MemoryIndex`) and the default-on memory plan
- **Supersedes:** —
- **Related:** ADR-0001 (source of truth), ADR-0003 (cognition worker & batching), ADR-0004 (fallback labeling), ADR-0008 (integrity), ADR-0010 (reflection)
- **Amendments:** 2026-11-01 — hard exclusion moved to **before** `memory_events` is written (credentials/crisis/dangerous instructions are never persisted in the memory journal); the structural gate is evaluated when the job runs; the canonical row is created `pending` with `level NULL` (ADR-0004); diagram and acceptance criteria are aligned. 2026-11-02 — in group chats, capture is limited to the speaker's statements about themselves; third-party facts and the soul's own replies are not auto-captured (ADR-0006). 2026-11-03 — audit: the reference range `embed.py:17-80` was fixed to `16-66` (the file is 66 lines). 2026-11-03 — queue name in the diagram: `memory_outbox` → `cognition_jobs`. 2026-11-03 — audit: `rate_limit_per_chat_hour` removed from `capture`; the write-volume quota is a single number, owned by ADR-0008 §4.4A.
- **Location note:** ADR lives in `docs/v0.1.0/adr/engine/`; the Indonesian copy is `docs/v0.1.0/adr/engine/id/0002-capture-trigger.md`.

---

## 1. Context

- Capture today is conditional and inconsistent: only when the role has the `memory` tool, the message length is ≥ `capture_min_chars` (default 8), it is not a crisis, not injection, not `SENSITIVE`, privacy assess returns JSON, then `spawn()` fire-and-forget (`agent.py:614,906-921`, `privacy.py:222-303`).
- The default-on memory plan would make capture run on every turn. Without a gate, every turn triggers an assess LLM call + Mem0 extraction — two extra LLM calls on top of the 4–6 calls that already exist.
- An Indonesian keyword-based prefilter would violate the "any body/language" principle and the `AGENTS.md` rule (no non-English strings in code).
- Default-on capture also enlarges the memory-poisoning surface: text from an untrusted source can enter memory and influence the next turn.

## 2. Decision drivers

1. **Cost** — capture must not double the cost of a turn.
2. **Recall** — important facts must not be missed.
3. **Precision** — memory must not fill up with pleasantries, duplicates, and transient status.
4. **Language neutrality** — gate rules must not depend on one language's vocabulary.
5. **Determinism** — the gate must be testable; decisions the LLM can avoid should not be handed to the LLM.
6. **Privacy & security** — hard exclusion stays in force; unlabeled data must not be disclosed.
7. **Operational simplicity** — one light real-time path + one shared batch path for all cognitive work.

## 3. Options considered

| Options | Shape | Strengths | Weaknesses | Status |
|---|---|---|---|---|
| **A. Always assess** | Every turn → assess → extract | Maximum recall; simple | Cost ~2×; noise; long queue; large poisoning surface | Rejected |
| **B. Prefilter + assess** | Deterministic gates → assess for candidates | Controlled cost | Can miss implicit facts; no recovery mechanism | Rejected as the final shape |
| **C. Model decides** | The soul calls the `memory` tool when it feels it is needed | No automatic assess needed | Inconsistent; the model can forget; the soul is burdened with the duty of remembering | Rejected as the main path |
| **D. Tiered hybrid** | Structural gate → novelty check (MiniLM) → LLM assess+extraction for novel candidates → store; recovery via reflection batch | Cheap; language-neutral; deterministic in the early stages; recall restored periodically | Most complex; depends on threshold quality | **Selected** |

## 4. Decision

**Capture is a four-stage pipeline with two paths: light real-time and periodic batch.**

```mermaid
flowchart LR
 T[Turn selesai --> H{Hard exclusion}
 H -->|kredensial / krisis / injeksi| X[buang
 H -->|lolos| E[(memory_events<br/>jurnal mentah)
 E --> P[(memories pending<br/>level NULL)
 E --> S[1. Gate struktural<br/>deterministik, netral bahasa
 S -->|non-kandidat| SK[skip / not_worth
 S --> N[2. Cek kebaruan<br/>MiniLM + cosine
 N -->|duplikat| K[skip / tandai kandidat supersede
 N --> A[3. Assess + ekstraksi<br/>satu panggilan LLM
 A --> C[(memories state=ok)
 A --> O[(cognition_jobs)
 R[4. Reflection batch<br/>durasi/idle --> A
```

### Stage 1 — Structural gate (no model)

Runs right on the turn, cheap and deterministic:

- not a command, not a system message, not a tool result;
- minimum length (`min_chars`);
- dedupe hash per `(thread_id, message_id, kind)`;
- rate limit per chat per hour;
- hard exclusion: credentials/secret, crisis, dangerous instructions, text detected as injection;
- provenance is recorded (`source`, `trust`) for ADR-0008.

This gate **does not** use language vocabulary. If it later needs a lexical signal, that signal must live as per-language data in `lang/`, not as a constant in the engine.

### Stage 2 — Novelty check (MiniLM, no LLM)

- Use the MiniLM vectors that already exist; where possible, **reuse the turn's existing embedding** so the extra cost is zero.
- Compare against the relevant `person`/`chat`/`global` memories in canonical + index.
- Similarity ≥ `novelty_threshold` → an already known candidate:
  - identical text → skip;
  - different text but the same topic → mark it as a `supersedes` candidate, resolved by reflection (ADR-0010), not on the real-time path.
- Novel ≠ worth it: this stage only filters duplicates, it does not decide value.

### Stage 3 — Assess + extraction (one LLM call)

- Only for novel candidates. One call returns validated JSON:

```json
{
 "worth": true,
 "level": 2,
 "subject": "nama atau id",
 "facts": [
 {"kind": "preference", "text": "...", "supersedes": "uuid atau null"}
 
}
```

- Combining assess and extraction was chosen to save one call per candidate. If the result proves worse, splitting them again is a small change recorded in a separate ADR.
- The rubric taught to the model:
  - **Worth it:** preferences, identity, relationships, commitments, recurring schedules, rules, durable work context.
  - **Not worth it:** pleasantries, transient status, questions without facts, tool results, secrets/credentials, instructions aimed at the system.
- Prompt instructions are in English; the input may be any language; `subject` is returned as-is.
- Validation per item, retry per item, and failures follow ADR-0004 (default: store it raw as `unconfirmed`, conservatively, and do not disclose it until it is labeled).

### Stage 4 — Recovery via reflection batch

- Backfill is **not** run on the real-time path and is not part of the first release.
- Facts missed by the gate are recovered by the periodic reflection over `memory_events` (ADR-0010), over the same batch path as capture.
- This preserves the principle: one batch mechanism for all cognitive work.

### Tool `memory`

- It stays, but only for conscious actions: "remember this", corrections, `forget`, `veto`, `export`.
- It is not the main capture path; the soul does not have to choose to remember.

## 5. Configuration

```yaml
memory:
 capture:
 mode: balanced # off | conservative | balanced | eager
 min_chars: 8
 # kuota volume tulis (per chat / per orang): satu angka, di ADR-0008 §4.4A
 novelty_threshold: 0.88
 combine_assess_and_extract: true
```

- `off` → operator kill switch.
- `conservative` → only candidates with strong signals.
- `balanced` → default.
- `eager` → close to "always assess", for operators who accept the cost.

## 6. Consequences

**Positive**

- Most turns add no LLM call; only a novel candidate triggers one call.
- The gates are language-neutral; there is no list of Indonesian words in the engine.
- Stages 1–2 are deterministic and testable without an LLM.
- Recall still has a recovery path (reflection), without burdening real time.

**Negative**

- A fact that passes the gate only enters memory after reflection; there is a delay window.
- The similarity threshold needs calibration and can fail in both directions.
- Combining assess + extraction adds coupling; it needs to be watched with eval.
- Reflection becomes a recall dependency; if it is disabled, facts that passed are still lost.

## 7. Metrics and gates

| Metric | Initial target |
|---|---|
| Extra LLM calls per turn | < 0.5 on average |
| Duplicate rate in canonical | < 5% |
| Recall on the golden set | ≥ 0,7 |
| Precision on the golden set | ≥ 0,9 |
| Rate-limit hits per chat | measured, not zero |
| `capture` backlog p95 | < 60 seconds (label, not access) |

These numbers are an initial proposal; they are for tuning, not dogma.

## 8. Acceptance criteria

- [ ] Capture runs without a role grant, but the structural gate and hard exclusion still apply.
- [ ] Non-candidates do not call the LLM at all.
- [ ] Duplicates are detected before the LLM is called.
- [ ] Only a novel candidate triggers assess + extraction, and at most one call per candidate.
- [ ] LLM output is validated per item; a failed item does not fail the whole batch.
- [ ] Unlabeled data is never disclosed before it is labeled.
- [ ] `memory_events` is written in one transaction with the message for candidates that pass **hard exclusion**; the structural gate is evaluated when the job runs.
- [ ] The `memory` tool is only used for conscious actions, not for normal capture.

## 9. Risks and mitigations

| Risks | Mitigation |
|---|---|
| Implicit fact missed by the gate | Reflection batch (ADR-0010) |
| Wrong novelty threshold | Golden-set eval; threshold as config; log the gate decision |
| Poisoning via default-on capture | Provenance/trust + instruction quarantine (ADR-0008) |
| Assess cost ballooning | Rate limit, dedupe, rate metric, kill switch |
| Leak of unlabeled data | Conservative default (ADR-0004); not disclosed before labeling |
| Two paths behaving differently | The same single `CognitionWorker` (ADR-0003) for capture and reflection |

## 10. Interaction with other ADRs

- **ADR-0001** — the canonical store becomes the write target; `memory_events` is written first.
- **ADR-0003** — gates and assess become `capture` queue jobs; validation per item, retry, dead-letter.
- **ADR-0004** — determines what happens when assess fails.
- **ADR-0006** — determines the capture policy in groups.
- **ADR-0008** — provenance/trust and anti-poisoning.
- **ADR-0010** — the reflection batch that recovers missed facts and resolves `supersedes` — **accepted** (`ADR-0010-reflection-disputed.md`).

## 11. Code references

- `eidesoul/engine/agent.py:614,906-921` — gate `can_memory` and spawn capture.
- `eidesoul/engine/privacy.py:222-303` — `capture_turn` and `_assess`.
- `eidesoul/engine/memory/mem0.py:360-380` — `capture` guard.
- `eidesoul/engine/memory/context.py:48-76` — `gather` and the retrieval scope.
- `eidesoul/engine/embed.py:16-66` — the reused MiniLM 384-d.
- `eidesoul/engine/guard/patterns.py:12-19` — the existing hard exclusion.
- `eidesoul/engine/config.py:130-138` — `MemoryConfig`.

---

*This ADR lives at `docs/v0.1.0/adr/engine/0002-capture-trigger.md`.*
