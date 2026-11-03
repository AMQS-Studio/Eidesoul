# ADR-0010: Reflection, Contradiction & Status `disputed`

- **Status:** accepted
- **Implementation:** implemented — evidence: scheduled reflection (`cognition.py`), `dispute_state` + `mark_disputed`/`resolve_dispute`/`stale_disputes`, one-level derivation at T2, dispute hides rows, the `questions_per_day` cap (`disputes_asked_today`/`mark_dispute_asked`) with one DM question per pass (`Agent.ask_dispute`, `HistoryStore.dm_thread`), `tests/test_reflection.py`, `tests/test_agent.py`; gate: `pytest -q` 831 passed, `scripts/adr.py --check`
- **Date:** 2026-11-02
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Context:** paying off the ADR-0008 §4.9C debt (`disputed` was deferred to here) and closing the last slot in the ADR-0001 §14 queue
- **Supersedes:** —
- **Related:** ADR-0003 (worker & queue), ADR-0004 (state `staged`), ADR-0005 (TTL/renewal), ADR-0006 (group privacy), ADR-0007 (permission classes), ADR-0008 (integrity), ADR-0013 (turn lifecycle), ADR-0014 (automation), ADR-0015 (payload), ADR-0019 (`llm_calls`), ADR-0020 (derivative procedure)
- **Amendments:** 2026-11-03 — audit: the reference path for `capture_turn` was corrected to `eidesoul/engine/privacy.py:222-303` (`memory/privacy.py` is 71 lines, only `show`/`disclose`). 2026-11-03 — the `staged` promotion threshold is stated as a row count (≥2 `ok` rows), distinguished from the day threshold `corroboration_days` (ADR-0008); the `state` list points to ADR-0004 §5.1 / the ADR-0016 enum. 2026-11-03 — audit: `refs`/`derive_depth`/`dispute_state` in §10 Code references are labeled as a proposal (not yet in the repo).
- **Location note:** ADR lives in `docs/v0.1.0/adr/engine/`; the Indonesian copy is `docs/v0.1.0/adr/engine/id/0010-reflection-disputed.md`.

---

## 1. Context

**Reflection does not exist.** Across `eidesoul/engine` there is not a single reflection module:
`grep -iE 'reflect|rumina'` only finds `ambient.py:1`, which mentions "summarise topics and
research while idle". What really runs today is `Ambient` — a background loop that, when
a chat goes idle, builds a topic digest and researches `web_search`, then writes a row
`scope=chat, kind=note, source=ambient:<thread_id>` (`ambient.py:203-315`), and **never sends
a message to any chat** (`ambient.py:1-4`). ADR-0003 has already set its place: a job
`kind=reflect` on the table `cognition_jobs`, cold path, default **hourly**, run by Worker C that
holds the lease. The slot is empty; its shape has not been decided — until here.

**Contradictions today have no owner.** ADR-0008 decided anti-poisoning A (quota), B/B2
(proven supersede), and D (strict promotion), then **deferred** the marking of contradictions: "Marking
contradictions requires detection that is batch in nature" (ADR-0008:180) and handed it to this ADR
(ADR-0008:281, :297). The effect today: two conflicting rows can **both be
`state='ok'`**, both enter the prompt, and the soul has to choose for itself — nondeterministic, and
wrong exactly in the most expensive cases.

**Grounding is already decided but not yet enforced.** ADR-0008:187 states "Reflection may only
conclude from canonical rows that are `state='ok'`". Without a mechanism, that rule is just a sentence:
no mandatory `refs`, no derivative depth limit, no write budget.

**`staged` awaits review.** ADR-0004 and ADR-0008 created `staged` (labeled, unreadable, TTL
following `kind`, promotion via recall-miss). There is only one promotion path; nobody reviews
`staged` in batches, and nobody answers: if two `staged` rows conflict,
which one rises?

**What must be avoided from the start:** reflection that makes things up, reflection that quotes
itself until it amplifies hallucinations, and reflection that turns into a chat — asking
users about things that do not matter.

## 2. Decision drivers

1. **Deterministic first, LLM only for the unclassified remainder** — the pattern already used by
   the guards (ADR-0008), and used here for contradiction detection.
2. **Every derivative write can be traced** to its source row; without a source, it is not written.
3. **Zero amplification** — a derivative must not become the input for the next derivative without limit.
4. **One writer** — all writes still go through `MemoryStore`; there is no second write path.
5. **Does not interfere with the turn** — background reflection never blocks the reply (ADR-0013).
6. **Questions to users are expensive** — at most one per person per day, only in DM.
7. **Cost is visible and can be turned off** — explicit budgets, kill switch, status in `/status`.
8. **Privacy never drops by itself** — the level of a derivative must not be lower than that of its source; the
   level is never lowered by an automatic process.

## 3. Options considered

| Options | Shapes | Excess | Lack | Status |
|---|---|---|---|---|
| **A. No reflection** | Let contradictions live side by side | Zero cost, zero code | The soul chooses for itself in the prompt; `staged` is never reviewed; `disputed` is a forever debt | Rejected |
| **B. Per-capture inline detection** | Every capture calls the LLM to check contradictions | Fastest detection | One extra LLM call per message; cost linear with traffic; violates the "labels may be batched" principle (ADR-0003 §5.2) | Rejected |
| **C. Batch reflection + deterministic detection first + tiered `disputed`** | Cold path per scope, contradiction candidates from deterministic rules, the LLM only rules on candidates, output must carry `refs`, disputes resolved through the subject in DM | Controllable cost; traceable; does not interfere with the turn; `staged` gets reviewed too | Requires new attributes (`refs`, `derive_depth`, `dispute_state`) and a question path in DM | **Selected** |
| **D. Leave it to Mem0/the inference provider** | Let the third-party memory layer resolve conflicts | Zero code | No testable contract; privacy decisions must not be handed to a third party | Rejected |

## 4. Decision

> **Reflection is a cold-path batch job per scope that reads only rows `state='ok'`,
> writes only with `refs` to the source row, is bounded to a derivative depth of one level and a write
> quota, detects contradictions deterministically then asks the LLM to rule on the candidates, marks
> pairs that truly contradict each other as `disputed`, and resolves them through the subject — in
> DM, at most once a day.**

```mermaid
flowchart LR
 OK["a state='ok' or staged row"] --> DET[Deterministic detector<br/>without LLM
 DET -->|candidate| LLM[LLM batch<br/>per scope, per item
 LLM --> W[MemoryStore.apply_reflection
 W --> OK2[derived row: via=reflection + refs
 W --> DIS[dispute_state=open
 DIS --> ASK[Question to the subject<br/>DM, max 1/day
 ASK --> RES[resolve: superseded / different context / tangent
```

### 4.1 What reflection does — and what it does not

**Does (v1):**

| Work | Output |
|---|---|
| Semantic consolidation | Merge existing cross-turn/chat duplicates; `refs` points at all sources |
| Contradiction | Pairs of mutually conflicting rows → `dispute_state='open'` |
| Review of `staged` | Promotion to `ok` when strengthened by ≥2 `ok` rows; demotion when involved in a dispute; the rest follow TTL |
| Era/topic summaries | Summaries that **carry `refs`**, not new stories |
| Forget proposal | A proposal to the retention path (ADR-0005), not direct deletion |

**Not a reflection job:**

- changing persona, `soul`, or `kind=rule` — hard limit ADR-0008 §4.4 (`self_stated` + soul document
  only);
- sending a message to a chat (same as `Ambient`, `ambient.py:1-4`);
- lowering the privacy level, opening `secret`, or touching level 5/credentials;
- deleting rows — deletion still belongs to retention/`/memori` with the subject's rights;
- judging the truth of the outside world (that is the job of `web_search`, and its result enters as a sourced fact).

### 4.2 Trigger

| Trigger | Condition | Note |
|---|---|---|
| Scheduled | `reflect_every_minutes: 60` (default hourly, ADR-0003 §5.3) | Only while the lease is held by the primary; standby is frozen (ADR-0012) |
| Backlog | ≥ `reflect_min_new_rows: 25` new `ok` rows in that scope since the last pass | Prevents an empty pass |
| Idle | No incoming message in the scope for `reflect_idle_minutes: 30` | Different from `Ambient`: `Ambient` summarizes chat topics, reflection reasons over canonical memory |
| Explicit | Step `queue_reflection` of an automation (ADR-0014 §4.5) or an owner request | Automation only **enqueues**; it does not write results |

One pass processes **one scope** (`max_scopes_per_pass: 1`) and does not mix scopes in one
request — the same rule as capture batching (ADR-0003 §5.5).

### 4.3 Grounding: output must point at a source

Every row written by reflection:

- `via: reflection` — explicit provenance (ADR-0008 §4.1);
- `refs: [<source row id>, ...]` — **mandatory, must not be empty**. Without sources, the item is dropped, not
  written with an empty source;
- `stated_by`/`about` **are inherited** from the strongest source, not derived again;
- `level` = **maximum** source level; reflection never lowers the level;
- `kind` follows the source category (a duplicate merge keeps its original `kind`), and `commitment` still
  carries `due_at` + status (ADR-0008 §4.4B3);
- `derive_depth: 1` (see 4.4);
- `trust: T2` — a hint, not a rule (ADR-0008 §4.5): a derivative never binds behavior, cannot
  climb to Tier 1, and cannot promote itself; `state` and trust are two
  separate axes, so `state='ok'` + `trust=T2` is legal;
- `asserted_at` = pass time, not source time.

All direct derivative rows **`state='ok'`** (the information is real and sourced), except those
under dispute → they stay `ok` + `dispute_state='open'`, and **only** may be used as context,
not for behavior (the hard T0 limit in ADR-0008 still applies and has already closed that path).

### 4.4 Depth limit & anti-amplification

| Rules | Value | Reason |
|---|---|---|
| Derivative depth | `derive_depth: 1` — reflection **does not** read rows `via=reflection` to produce new derivatives | Prevents self-reinforcement: a summary of a summary quickly drifts from the source |
| Derivative write quota | Part of the per-person quota (ADR-0008 §4.4A), `reflect_quota_share: 0.25` | Derivative writes must not eat into the user-statement quota |
| Shape prohibition | `kind=rule` from reflection: **0** — a rule only `via=soul_doc` (ADR-0008 §4.3) | Rules come only from the soul document; reflection over memory has no path |
| Limit per pass | `max_writes_per_pass: 20` | Caps cost/volume explosions |
| Idempotence | One window per scope; a re-pass over the same window writes no new row | See 4.9 |

Reflection may **update** an existing derivative row (adding `refs`, updating a summary)
via a proven supersede (ADR-0008 §4.4B), but it does not create derivatives of derivatives.

### 4.5 Contradiction detection: deterministic first, LLM later

**Layer 1 — deterministic (always runs, without the LLM).** Candidates are selected from rows `state='ok'`
with the same `scope`, `subject_id`, and `kind`, when one of these holds:

- opposite polarity on the same predicate (`suka` vs `tidak suka`, `aktif` vs `berhenti`) using
  the polarity lexicon already in the guard path;
- mutually exclusive values: a different number, date, or `due_at` for the same claim;
- mutually exclusive states: `done` vs `pending` for the same `commitment`;
- a location/time that cannot both be true for the same routine claim.

This layer is cheap, deterministic, and **marks nothing** — it only collects candidates.

**Layer 2 — LLM (candidates only).** A batch per scope rules: `contradiction`, `contextual`
(both true in different contexts), or `unrelated`. The batch prompt is stable (no `uptime`/`lease`)
so that prompt caching works (ADR-0003 §5.5). An item that fails does not fail the batch; it is retried.

**Threshold bound.** Candidates are only processed when `reflect_min_candidates: 1` in that scope; there is no
pass that contains only one or two rows unless triggered explicitly.

### 4.6 `disputed`: data shape and read effects

`disputed` is **not** a new `state` value. `state` remains the six values of ADR-0004 §5.1 (`pending`, `ok`,
`staged`, `failed`, `not_worth`, `superseded`; the official enum in ADR-0016 §4.3), so the invariant
"only `state='ok'` is readable" does not change.
What is added is metadata on the row:

| Attributes | Value |
|---|---|
| `dispute_state` | `none` \| `open` \| `resolved` \| `stale` |
| `disputed_with` | opposite row id |
| `dispute_reason` | `deterministic:<rule>` \| `llm:<label>` |
| `dispute_opened_at`, `dispute_asked_at`, `dispute_resolved_at` | timestamp |

Read effects:

- `dispute_state='open'` **remains readable** (the information is not yet false, merely undecided) and is
rendered with a marker, using the existing `?` convention for unconfirmed rows
(`commands/memori.py`: `mark = "" if hit.confirmed else "?"`) → a dispute uses an additional marker
`!` on the same display path;
- an `open` pair **must not** be used for behavior: it does not enter Tier 1, it does not become a procedure,
it does not become a preference that binds — exactly the hard limit of ADR-0008;
- a dispute **does not extend the TTL** (ADR-0005 still applies as is); marking is not using.

### 4.7 Resolution through the subject

| Steps | Rules |
|---|---|
| Who is asked | `subject_id` of the dispute; if unknown, `owner_id`. Not a third party, not the group |
| Where | **DM only.** In a group there are no privacy questions (ADR-0006) and the dispute is not mentioned |
| How often | `max_dispute_questions_per_day: 1` per person; a second question waits for the next day |
| Shape | One short question with two short quotes + 2-3 options ("which one holds?"), not an interview |
| Answer "A" | Row B → `superseded`, dated with reason `dispute_resolved`; A survives and may be confirmed |
| Answer "both, different contexts" | Both survive, `dispute_state='resolved'`, scope/context narrowed if possible |
| Answer "both are wrong" | Both → `superseded` by the new statement; the new statement is captured through the normal path |
| No answer | After `dispute_ttl_days: 30` → `dispute_state='stale'`; rows with weaker provenance drop to `staged` (still alive, can be promoted); if equal, both survive with markers |
| Veto | A subject veto (consent path ADR-0007/ADR-0005) always wins: the row disappears from retrieval, the dispute is closed |
| Automatic | Automation **must not** resolve a dispute; it only enqueues passes (ADR-0014) |

The subject's answer is captured as an ordinary statement (`via=turn`), so its trust is full — it is not
reflection that raised it.

### 4.8 Review of `staged`

The same pass reviews `staged` in that scope:

1. **Promotion** to `ok` when strengthened by ≥2 distinct `ok` rows (`refs` filled) or when a recall-miss
raises it; promotion still obeys ADR-0004/ADR-0008. This threshold is counted as a **row
count**, not days — the day threshold (`corroboration_days: 2`, ADR-0008) applies only to trust
promotion, not to leaving `staged`.
2. **Demotion**: an `ok` row involved in a dispute and losing → `staged` (not deleted), with the TTL
following `kind`;
3. **Cleanup**: a `staged` row that passes the TTL is discarded per ADR-0004/ADR-0005 — reflection does not
prolong any life except through legitimate re-evidence.

### 4.9 One writer & idempotence

- All writes go through **one door**: `MemoryStore.apply_reflection(...)` in `memory/mem0.py`, using the
existing metadata + supersede path (`mem0.py:340-392`, `:323-338`). There is no direct write
to the Mem0 table from a worker.
- Idempotency key per pass: `reflect:<scope>:<window_start>`; a re-pass over the same window
produces zero new rows even if the process dies in the middle.
- Validation **per item** before writing: `refs` is not empty, the source is still `state='ok'` and still readable
by the writer (it does not contain a scope that is not its right), the derivative level ≥ the maximum source
level. An item that fails is dropped and reported in `/status`, not written halfway.
- Worker C and Worker D use the same function so that the Batch API path (ADR-0003 §5.6) cannot
smuggle in writes that skip validation.

### 4.10 Budgets, kill switch, and visibility

- Budget per pass: `max_items_per_pass: 40`, `max_writes_per_pass: 20`,
`max_seconds_per_pass: 60`. Crossing the limit → the pass stops neatly, the rest waits for the next pass.
- Budget per day per scope: `max_passes_per_day: 24` (hourly) and the cost multiplier are visible in
`/status`.
- Kill switch: `reflection.enabled: false` stops new passes; old result rows remain valid,
an `open` dispute is left standing (not force-closed) and the question is not sent.
- Visibility: `/status` shows the count of passes, writes, disputes opened/resolved/`stale`,
and questions sent — numbers, not narratives.
- Without an LLM (`llm is None`) reflection does not run at all; there is no fallback path that
guesses. The deterministic layer is `eidesoul/engine/memory/reflect.py`; a manual CLI wrapper is not built yet.
to inspect candidates without writing anything.

## 5. Configuration

```yaml
engine:
 reflection:
 enabled: true
 every_minutes: 60 # scheduled pass (default hourly, ADR-0003 §5.3)
 idle_minutes: 30 # idle trigger, separate from ambient.idle_minutes
 min_new_rows: 25 # minimum backlog for the pass to run
 min_candidates: 1 # minimum candidates for the LLM to be called
 scopes_per_pass: 1
 items_per_pass: 40
 writes_per_pass: 20
 seconds_per_pass: 60
 passes_per_day: 24
 derive_depth: 1 # derivation from a derivation: forbidden
 quota_share: 0.25 # share of the write quota per person (ADR-0008 A)
 dispute_ttl_days: 30
 questions_per_day: 1 # per subject, DM only
 batch_api: off # optional slow path (ADR-0003 §5.6)
```

This key is part of the `engine` document (seed `config.yaml`, mirrored in the DB); changing it manually →
`eidesoul config import`.

## 6. Metrics and gates

| Metric | Initial target |
|---|---|
| Derivative writes without `refs` | 0 (tested) |
| Rows `via=reflection` with `derive_depth > 1` | 0 (tested) |
| `kind=rule` written by reflection | 0 (tested) |
| Derivative level lower than the source | 0 (tested) |
| Dispute questions in a group | 0 (tested) |
| Dispute questions exceeding `questions_per_day` | 0 |
| Disputes open without resolution after the TTL | 0 (become `stale`) |
| A pass crossing `writes_per_pass`/`seconds_per_pass` without stopping neatly | 0 |
| Deterministic candidates → LLM verdict (`contradiction`/`contextual`/`unrelated`) | ≥ 90% decided, the rest `unrelated` |

## 7. Acceptance criteria

- [ ] `reflection.enabled: true` runs a pass per scope without blocking the turn; `false` stops new passes without changing old rows.
- [ ] Every derivative row has `via=reflection` and a non-empty `refs`; without `refs`, items are dropped and reported.
- [ ] `stated_by`/`about` are inherited from the source; the derivative `level` = the maximum source level; no automatic level drop.
- [ ] Reflection does not read rows `via=reflection` to produce new derivatives (`derive_depth: 1` enforced by validation).
- [ ] The deterministic detector collects candidates without the LLM; the LLM only receives candidates, batched per scope, with a stable prompt.
- [ ] `disputed` adds no `state` value; the invariant "only `state='ok'` is readable" stays intact; `open` dispute rows remain readable with the `!` marker and cannot be used for behavior.
- [ ] Dispute questions are sent only in DM, at most `questions_per_day` per subject, with two quotes and an answer option.
- [ ] The subject's answer enters through the ordinary capture path (full trust), not raised by reflection.
- [ ] A dispute that passes the TTL becomes `stale`; rows with weaker provenance drop to `staged`.
- [ ] `staged` is reviewed: promotion when strengthened by ≥2 `ok` rows, demotion when it loses a dispute, the rest follow TTL.
- [ ] All writes go through `MemoryStore.apply_reflection` with the idempotency key `reflect:<scope>:<window_start>`; a re-pass produces no new rows.
- [ ] Budgets per pass and `passes_per_day` are enforced; crossing a limit stops neatly, not silently truncated.
- [ ] `/status` shows the count of passes/writes/disputes/questions; no narrative.
- [ ] A manual `reflect --dry-run` CLI shows candidates and the write plan **without writing anything** (read-only, pattern `scripts/ablate.py`).

## 8. Risks and mitigations

| Risks | Mitigation |
|---|---|
| Reflection invents facts | `refs` mandatory + dropped without a source; the source must be `state='ok'`; `stated_by`/`about` inherited |
| Self-amplification | `derive_depth: 1`; reflection does not read its own output |
| LLM cost explodes | Deterministic candidates first; batch per scope; `items_per_pass`/`writes_per_pass`/`seconds_per_pass`; `passes_per_day` |
| Reflection blames people | The derivative `about` is inherited, the level does not drop, and `disputed` is only about data contradictions — not a judgment of a person; the question goes only to the subject |
| Questions turn into interviews | `questions_per_day: 1`, one question containing two quotes + options, only in DM |
| A dispute hangs forever | `dispute_ttl_days: 30` → `stale` + lower the weak ones to `staged` |
| TTL breaks because of a dispute | A dispute does not extend the TTL (ADR-0005 stands) |
| Two writers compete | One door `apply_reflection`, one queue `cognition_jobs`, claim `SKIP LOCKED` + lease (ADR-0003, ADR-0012) |
| `staged` piles up without review | The same pass reviews `staged`: promotion, demotion, or discarded per TTL |
| Reflection revives vetoed data | A veto always wins and the dispute closes; the pass reads only-`ok` rows that already passed the privacy funnel |
| A feature is turned off and then the soul guesses | When `enabled: false`, `/status` says so; there is no guessing on the other path |

## 9. Interaction with other ADRs

- **ADR-0003** — reflection is a job `kind=reflect` in `cognition_jobs` (cold path Worker C,
default hourly); the Batch API is only for the slow path and must pass the same validation.
- **ADR-0004** — `state` gains no value; `staged` is reviewed here (promotion/demotion/TTL).
- **ADR-0005** — a dispute does not extend the TTL; renewal stays hit-based; the forget proposal from
reflection is only a proposal.
- **ADR-0006** — there are no privacy questions in a group; a dispute is discussed only in the subject's DM.
- **ADR-0007** — the dispute question falls in the consent class (the subject's own memory); a veto always wins;
automation must not resolve a dispute.
- **ADR-0008** — `disputed` settles §4.9C; the hard trust limit T0 stands; `refs` strengthens the B/B2 evidence path;
reflection derivatives are always `trust=T2` (§4.5 there) so they never bind behavior; reflection
obeys quota A.
- **ADR-0019/ADR-0020** — this pass is where the inter-episode efficiency comparison
(ADR-0020 §4.8) runs: its input numbers are `llm_calls` (ADR-0019) and `turn_steps` (ADR-0013).
The comparison rules belong to ADR-0020; reflection only provides the schedule and the budget.
- **ADR-0011/ADR-0012** — the pass uses claim `SKIP LOCKED` + lease; standby does not run reflection.
- **ADR-0013** — reflection never blocks a turn; a running turn does not wait for the pass.
- **ADR-0014** — the step `queue_reflection` only enqueues a pass; there is no direct write from automation.
- **ADR-0015** — reflection status appears via `/status` and the topic `batas` (a rough number), not the regular
payload; `refs`/row ids still do not enter the user prompt.

## 10. Code references

- `eidesoul/engine/ambient.py:1-4`, `:103-200`, `:203-315` — the idle loop that exists; the limit "never
sends a message" and the write shape `scope=chat, kind=note, source=ambient:<thread_id>`.
- `eidesoul/engine/config.py:236-252` — `AmbientConfig` (precedent for budget keys: `tick_seconds`,
`idle_minutes`, `research_per_day`).
- `eidesoul/engine/memory/mem0.py:340-392` — metadata path; `refs`, `derive_depth`, `dispute_state`
**proposal** (not yet in the repo; the column in ADR-0016 §4.2). `:323-338` — proven supersede (exists).
- `eidesoul/engine/privacy.py:222-303` — `capture_turn` (the entry path for the subject's answer);
`eidesoul/engine/memory/privacy.py:52-71` — `show`/`disclose`.
- `eidesoul/engine/memory/context.py:91-133` — `render`/`build` (where the dispute marker is rendered too).
- `eidesoul/engine/commands/memori.py:15-23` — the `?` marker for unconfirmed rows (a display
convention extended by `!`).
- `eidesoul/engine/tools/memory.py` — conscious-action tool (still the main path for "remember/forget").
- `eidesoul/engine/commands/status.py` — where the pass/write/dispute/question numbers are displayed.
- `eidesoul/engine/guard/patterns.py:11-26` — the lexicon/patterns used by the deterministic layer.
- `scripts/ablate.py:1-8` — read-only harness precedent; `scripts/replay.py:1-12` — rubric
scenario harness to assess the quality of a reflection pass.
- ADR-0003 §5.3-5.6 — the `cognition_jobs` queue, batching per scope, the Batch API path.

---

*This ADR lives at `docs/v0.1.0/adr/engine/0010-reflection-disputed.md`.*
