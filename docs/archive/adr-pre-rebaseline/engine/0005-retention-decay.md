# ADR-0005: Retention & Decay — hybrid TTL, Renewal On Use, and Auto Pin

- **Status:** accepted
- **Implementation:** implemented — evidence: `pinned`/`durability`/`category`/`use_*`/`expires_at`/`max_lifetime_at`, renewal per delivered turn, auto-pin, `memory purge` with `--dry-run` and library-copy removal, the MiniLM remember/forget prototypes (`monoagens/library/intent/`, `monoagens/engine/memory/intent.py`, `scripts/intent.py --check`), the remember hint in capture, the deterministic "forget that …" path, and the scheduled purge with `memory.retention.purge.{grace_days,batch}` on the daily audit job; gate: `pytest -q` 831 passed, `scripts/adr.py --check`
- **Date:** 2026-11-01
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Context:** follows from ADR-0001 (canonical), ADR-0003 (worker & consolidation), ADR-0004 (TTL for non-`ok` states); default-on memory needs an age policy
- **Supersedes:** —
- **Related:** ADR-0006 (group privacy), ADR-0008 (integrity), ADR-0010, ADR-0011 (purge job), ADR-0016 (column retention)
- **Amendments:** 2026-11-03 — owner decision: purge/`forget` also removes the **library copy** (`mem0_history.db`: the `history`/`messages` rows), because that file stores `old_memory`/`new_memory` including already-deleted text. 2026-11-03 — audit: `commitment` removed from `core_durability_pin_categories` (an unconditional pin contradicted the rule "pin only while the task is open"); added `commitment_pin_when_open: true`. The retention columns are now final in ADR-0016 §4.2. 2026-11-03 — the `skills`/`skill_revisions` artefacts (ADR-0020) entered §4.7: archived when unused, append-only revisions. 2026-11-03 — ADR-0000 §4.4 rename: `thread_eras` → `thread_eras`.
- **Location note:** ADR lives in `docs/v0.1.0/adr/engine/`; the Indonesian copy is `docs/v0.1.0/adr/engine/id/0005-retention-decay.md`.

---

## 1. Context

- No retention at all: no `delete(messages)`; memory, approvals, `thread_eras`, `config_changes`, and the media workspace grow forever.
- `veto` hides and `forget` removes through the adapter — but there is no age policy.
- ADR-0004 sets a 7-day TTL for `pending`/`failed`/`not_worth`; ADR-0005 completes the policy for lines with `state='ok'`.
- Consolidation (ADR-0003 cool/cold, ADR-0010 reflection) will only mature further, so the base TTL may be long (365 days) without making memory dirty.
- No decay: retrieval gets noisy, the index swells, costs rise, and the promise of deliberate forgetting has no mechanism.

## 2. Decision drivers

1. **Data minimization** and the right to be forgotten.
2. **Memory quality** — a stale fact lowers the answer; important ones must stay alive.
3. **Cost & operations** — DB, index, backup, media.
4. **Auditability** — a trace without content.
5. **Reliability** — what matters must not disappear just because it happened to go unused.
6. **Determinism** — a policy that can be recomputed and tested.

## 3. Options considered

| Options | Shapes | Excess | Lack | Status |
|---|---|---|---|---|
| **A. Forever** | No purge | Simple | Grows without bound; retrieval gets worse | Rejected |
| **B. TTL per level** | L0 long → L4 short | Easy | Ignores the memory type | Rejected |
| **C. TTL per type** | Long preference, short occurrence | Closer to the mark | Ignores sensitivity | Rejected |
| **D. Decay by usage only** | Frequently used → stays alive | Like human memory | Not deterministic; important facts can be lost | Rejected |
| **E. hybrid + renewal + automatic pin** | Base TTL (type × level) + renew on use + pin/silent extension for explicit sources | Lean but reliable | Slightly more complicated | **Selected** |

## 4. Decision

### 4.1 Tier Reliability

"Tier" here means the **retention durability class** (how easily a row is purged) — not a payload layer
(ADR-0015 "Lapis 1/2") and not provenance trust (ADR-0008 `T0–T3`).

"Always remember" here means **always remember what matters**, not store everything.

| Tier | Contents | Policy |
|---|---|---|
| **1 — Core** | Identity (name, pronoun, language), `pinned`, an explicit "remember this" | No TTL; removed only manually or by veto |
| **2 — Precious** | `preference`, `fact`, `rule`, `commitment` | Long base TTL + renew on use + silent extensions for explicit sources |
| **3 — Seasonal** | `event`, `topic`, `note` | Short TTL; may decay |

Consolidation (ADR-0010) may **promote**: a fact that appears consistently across several events can be lifted from Tier 3 to Tier 2, with the source evidence. An identity that proves stable enters Tier 1.

### 4.2 Base TTL

| Type | L0–L2 | L3 | L4 |
|---|---:|---:|---:|
| `identity` | Tier 1 (no TTL) | — | — |
| `preference` | 365 | 180 | 90 |
| `fact` | 365 | 120 | 60 |
| `rule` | 365 | 180 | 90 |
| `commitment` | 365 | 180 | 90 |
| `event` `topic` | 90 | 60 | 30 |
| `note` | 30 | 14 | 7 |
| `superseded` | 30 after being replaced | — | — |
| `pending` / `failed` / `not_worth` | 7 (ADR-0004) | — | — |

Principle: the more sensitive, the shorter; the more "event-like", the shorter.

### 4.3 Renewal on use

"Touch" = the memory actually entered the memory block in the prompt.

```sql
memory_uses(
 memory_id text not null,
 day date not null, -- hari menurut jam DB (UTC)
 turn_id text not null,
 primary key (memory_id, day, turn_id))
```

Rules:

1. One touch per memory per turn; **counted only on a `delivered` turn** (a turn that crashed does not prolong life).
2. Worker consolidation aggregates: `last_used_at`, `use_count`, `distinct_use_days`.
3. Renewal: `expires_at = min(now + base_ttl × multiplier, max_lifetime_at)`.
4. `renew.min_interval_hours` (e.g. 12) prevents renewals many times in a day.
5. **`distinct_use_days`** is used, not the raw number of occurrences — so one long conversation does not make a memory immortal.
6. Renewal does not apply to `superseded`, `vetoed`, `failed`, `not_worth`.
7. L4 is only renewable if `pinned` or confirmed; otherwise the TTL runs out.

Tiered strength (≥3 distinct days → ×1.5; ≥10 days → ×2) is prepared as a v2 option, **off by default** until data exists.

### 4.4 Reliability Without Asking

Decision: **no expiry review questions.** The importance of a memory is judged from automatic signals:

| Signal | Source | Effects |
|---|---|---|
| **Semantic durability** | Assess LLM: `durability = core \| durable \| episodic` | `core` → `max_lifetime`; `durable` → Tier 2; `episodic` → Tier 3 |
| **Deterministic category** | Assess: `category` | `identity`/`health`/`safety` → Tier 1 (pin); `commitment` → Tier 1 **only while the task is open** (finished/cancelled/overdue: the pin is released, it follows the `kind` TTL, see ADR-0008 §4.4 B2/B3) |
| **Corroboration & re-evidence** | The fact resurfaces on different days; consolidation merge | Promote Tier 3 → Tier 2; reset the survivor's TTL |
| **Hit** | Enters the prompt block on a `delivered` turn | Renewal TTL (section 4.3) |
| **Soft-hit** | High similarity but not in the top-k | Partial credit (e.g. 25% of the base TTL), restricted |

**Remember-intent via MiniLM, not keywords.** Because the user does not know what memory holds and rarely states an explicit command, this signal is a bonus rather than the main path:

- The multilingual prototypes live at `library/intent/remember.<lang>.json` — e.g. “ingat ini”, “remember this”, “catat ya”, “jangan lupa”, “simpan ini” — plus **negative** prototypes: questions (“kamu ingat ini?”), quotes (“dia bilang ingat ini”), and negations (“jangan ingat ini”).
- Detection = cosine to the prototype, with floor + margin (the same pattern as the affect prototype, `scripts/affect.py --check`).
- If it is detected as an imperative addressed to the soul: a `durability=core` hint → `expires_at = max_lifetime_at`. A **full pin** only when the assess result is also `core`/a core category, or corroboration ≥ 2 days.
- Hard exclusion comes first: intent cannot force storing credentials, crisis content, or dangerous instructions.
- `forget`-intent also goes through the multilingual prototype, entering the deterministic `forget`/`veto` path with an owner/subject check.
- The prototype data is gated by language and validated by script (`--check`, `--embed`), not by strings in code.

Risks **accepted**: important facts that are never mentioned again within their TTL can be lost. Mitigation: base TTL 365 days, the durability/category/corroboration signals, `soft_hit`, manual pins, and user feedback as an adjustment signal.

### 4.5 Purge

- A job scheduled nightly; also manual: `monoagens memory purge [--dry-run]`.
- Selects `expires_at <= now - grace_days` (grace 1 day) and `pinned=false`.
- Order per batch: remove index → remove canonical → **remove the library copy** (the `history`/`messages` rows
in `mem0_history.db` for that id/session) → write a tombstone; one transaction per batch.
- Idempotent; a failure goes to the dead-letter queue (ADR-0011).
- Uses the DB clock (`clock_timestamp()`), never holding the event loop.
- Claim the row before the purge so it does not race with use.

### 4.6 Tombstone

```sql
memory_tombstones(
 id uuid primary key,
 kind text not null,
 level smallint,
 scope text not null,
 content_hash text not null,
 reason text not null, -- forgot | expired | vetoed | superseded
 deleted_at double precision not null)
```

Without content. For audits, and to answer “did we ever have this?”.

### 4.7 Other artefacts

| Artefacts | Default | Note |
|---|---|---|
| Transcript (`messages`) | Forever | Soul history archive; manual purge per chat is possible |
| `llm_calls` | 90 days (`engine.metrics.retention_days`) | Cost metric; its contents are ids + numbers, with no content |
| Media workspace | 30 days | Large, rarely used after a turn |
| `outbound_messages` | 30 days | For dedupe; unnecessary afterwards |
| `turn_steps` | 30 days | Audit of side effects |
| Approvals | 30 days after completion | The execution TTL remains 900 seconds |
| `config_changes` | 365 days | Operator audit |
| `thread_eras` | Follows its content level | It holds content, not neutral metadata |
| `skills` `skill_revisions` | Archived when unused (`skills.use_renewal_days`) | Derived procedure; append-only revisions, `archived` not deleted (ADR-0020) |

## 5. Configuration

```yaml
memory:
 retention:
 enabled: true
 renew_on_use: true
 renew:
 min_interval_hours: 12
 count_only_delivered: true
 strength: { enabled: false }
 max_lifetime_days: { "0-2": 3650, "3": 1095, "4": 365 }
 pin: true
 silent:
 core_durability_pin_categories: [identity, health, safety
 commitment_pin_when_open: true
 corroboration_promote: { min_days: 2 }
 renew_on_consolidation_merge: true
 intent:
 data: library/intent
 remember: { enabled: true, floor: 0.62, margin: 0.06 }
 forget: { enabled: true, floor: 0.64, margin: 0.06 }
 pin_when: core_or_corroborated
 soft_hit: { enabled: true, credit: 0.25, max_days: 90 }
 purge: { schedule: nightly, grace_days: 1, batch: 500, dry_run: false }
 ttl_days:
 preference: { "0-2": 365, "3": 180, "4": 90 }
 fact: { "0-2": 365, "3": 120, "4": 60 }
 rule: { "0-2": 365, "3": 180, "4": 90 }
 commitment: { "0-2": 365, "3": 180, "4": 90 }
 event: { "0-2": 90, "3": 60, "4": 30 }
 note: { "0-2": 30, "3": 14, "4": 7 }
 transcripts: { ttl_days: 0, media_ttl_days: 30 }
 approvals: { keep_days: 30 }
 audit: { keep_days: 365 }
```

## 6. Metrics and gates

| Metric | Function |
|---|---|
| Renewal rate (alive because used vs. base TTL) | Judging the policy |
| Distribution of `distinct_use_days` | Judging strength |
| Ratio `expired` vs `renewed` | Balance |
| Unintended forget (feedback) | Adjusting the TTL |
| Average age per level | Load |
| Purge failure / dead-letter | Health |
| Auto-pin / auto-extend per month | Quiet reliability working |
| Renewal from re-evidence & merge | Consolidation effectiveness |

## 7. Acceptance criteria

- [ ] Every line with `state='ok'` has an `expires_at` deterministic from `(kind, level)`.
- [ ] A touch is recorded only on a `delivered` turn; one per memory per turn.
- [ ] Renewal uses `distinct_use_days` and `min_interval_hours`, capped by `max_lifetime_at`.
- [ ] L4 is not renewable without a pin/confirmation.
- [ ] `durability=core` gives `max_lifetime`; full pins only for core categories or corroboration ≥ 2 days.
- [ ] `commitment` is pinned only while the task is open; finished/cancelled/`overdue` releases the pin and follows the `kind` TTL (ADR-0008 §4.4 B2/B3).
- [ ] `durable` enters Tier 2; `episodic` enters Tier 3.
- [ ] Remember/forget intent is detected through the multilingual MiniLM prototypes (not keywords), with negative prototypes.
- [ ] Intent cannot bypass hard exclusion.
- [ ] Soft-hit grants limited partial credit, not a full renewal.
- [ ] Re-evidence and a consolidation merge reset the survivor's TTL.
- [ ] No expiry review question is sent to the user.
- [ ] Purge is idempotent, `--dry-run` changes nothing, and grace is 1 day.
- [ ] `forget` removes canonical + index + Mem0 + the **library copy** (`history`/`messages`), then writes a content-free tombstone.
- [ ] The media workspace is purged according to `media_ttl_days`.
- [ ] No content in tombstone/audit **and** no text left in the derived file (`mem0_history.db`) after `forget`.
- [ ] Test: expire → not visible in `memories_readable`; purge → gone from canonical, index, and library copy; pin → not purged.

## 8. Risks and mitigations

| Risks | Mitigation |
|---|---|
| Deleting facts that still matter | Tier 1 pin, silent auto-pin/auto-extend, `renew_on_use`, feedback metric |
| The user is shocked that the soul forgot | Documentation + manual `pin` + a visible tombstone |
| Purge racing with use | Claim/lease + grace_days |
| Index and canonical out of sync | Fixed purge order + a reconciliation job |
| Renewal makes memories immortal | `max_lifetime_at` + `min_interval_hours` + distinct days |
| Auto-pin overload | Core categories only, or corroboration ≥ 2 days; `core` alone gets `max_lifetime` only |
| Intent misfire (quote, negation, question) | Negative prototypes + floor/margin + intent is only a bonus + hard exclusion |

## 9. Interaction with other ADRs

- **ADR-0001** — the canonical row stores `expires_at`, `last_used_at`, `use_count`, `distinct_use_days`, `pinned`, `durability`, `category`.
- **ADR-0002** — the assess returns `durability`/`category`; remember/forget intent through the MiniLM prototypes only gives a hint, not the final decision.
- **ADR-0003** — worker C runs touch aggregation and the light purge.
- **ADR-0004** — a 7-day TTL for non-`ok` states.
- **ADR-0006** — a high level does not enter the group prompt; retention still applies.
- **ADR-0008** — provenance/trust affects whether automatic renewal is allowed.
- **ADR-0010** — reflection runs the Tier 3 → Tier 2 promotion and the merge that renews the survivor; it sends no expiry question — **accepted** (`ADR-0010-reflection-disputed.md`); a dispute does not extend the TTL and sends no age question.
- **ADR-0011** — the purge uses a job claim, idempotency, and a dead-letter queue.

## 10. Code references

- `monoagens/engine/memory/mem0.py:496-531` — `set_privacy`, `veto`.
- `monoagens/engine/memory/context.py:48-108` — retrieval and render (the “used” touch point).
- `monoagens/engine/memory/rekey.py` — an example metadata migration that must honor retention.
- `monoagens/engine/access/privacy.py:222-278` — capture and level.
- `monoagens/engine/config.py:118-138` — `PrivacyConfig`, `MemoryConfig`.
- `monoagens/engine/db.py:112-145` — `upsert` for the batch phase.

---

*This ADR lives at `docs/v0.1.0/adr/engine/0005-retention-decay.md`.*
