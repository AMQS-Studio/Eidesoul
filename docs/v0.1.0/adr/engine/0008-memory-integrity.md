# ADR-0008: Memory Integrity — Provenance, Trust, Instruction Quarantine, Anti-Poisoning

- **Status:** accepted
- **Implementation:** implemented — evidence: `via`/`refs`/`asserted_at`/`trust` T0-T3, deterministic `integrity.py`, quarantine on the canonical write and on `label_candidate`, tombstones, supersede proof, tiered quota into `staged`, recall-miss promotion, prompt trust markers, and `kind=rule` born only from `MemoryStore.sync_soul_rules`/`promote_soul_rules` (`soul.rules`, T0, two UTC days of corroboration); gate: `pytest -q` 831 passed, `scripts/adr.py --check`
- **Date:** 2026-11-02
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Context:** follows from ADR-0001 (canonical), ADR-0002 (gate capture), ADR-0004 (labeling), ADR-0005 (retention), ADR-0006 (group privacy), ADR-0007 (capability vs authorization)
- **Supersedes:** —
- **Related:** ADR-0003 (worker), ADR-0010 (reflection), ADR-0011 (crash safety)
- **Location note:** ADR lives in `docs/v0.1.0/adr/engine/`; the Indonesian copy is `docs/v0.1.0/adr/engine/id/0008-memory-integrity.md`.
- **Amendments:** 2026-11-02 — §4.4 A: overflow quota enters state `staged` (labeled, TTL follows `kind`, promotion on recall-miss), not `pending`. 2026-11-03 — audit: the `kind=rule` acceptance criterion was aligned with §4.3 (`via=soul_doc`). 2026-11-03 — ADR-0000 §4.4 rename: `thread_id` metadata/column → `thread_id`, scope `thread` → `thread`.

---

## 1. Context

This soul engine has one memory that lives in many rooms. The consequence: **anyone who
can chat with the soul can write to its memory.** The cheapest attack is not breaking into a server —
just talking: stuffing claims, overwriting stable facts, or smuggling in instructions to be
kept as a rule.

**Foundations that already exist and are kept:**

| Control | Evidence |
|---|---|
| Deterministic injection pattern | `guard/patterns.py:16-26` — `INJECTION` (ignore previous, system prompt, jailbreak, “abaikan instruksi”, “mulai sekarang kamu”, developer mode) |
| Capture rejects injection | `agent.py:914` |
| Hard exclusion of credentials & crisis | `SENSITIVE` (`patterns.py:11-14`), `safe_text` (`:29-35`), the crisis prompt rule (`prompts.py:54-55`) |
| Prompt: other people's words = data | `prompts.py:266` |
| Provenance is partially stored | metadata `scope, kind, person_id, thread_id, audience, source, level, owner_id, subject_id, confirmed` (`mem0.py:340-392`) |
| `source` values already in use | `turn` (`privacy.py:260`), `tool` (`tools/memory.py:94`), `command` (`commands/memori.py:131`), `ambient:<thread_id>` (`ambient.py:255,314`) |
| Veto is only by subject | `mem0.py:527-530` |
| Manual supersede + scope/owner validation | `mem0.py:323-338` |

**Four holes:**

1. **`kind=rule` can be born from user speech.** The prompt allows saving "a short proven
procedure... as `kind=rule`" (`prompts.py:314-315`), and the tool accepts any `kind` from the model
(`tools/memory.py:75`). A rule influences behavior, so this is an escalation path.
2. **A rule cannot be told apart from a fact when read.** `render()` only shows
`[scope/date L{level}{?}]` + text (`memory/context.py:105-110`); `kind` does not appear.
3. **There is no trust per memory row.** The only "trust" that exists is an affect relation (`schema.py:130`);
self-statements, claims about others, and ambient synthesis (`ambient.py:255,314`) enter the
same prompt block with no distinguishing mark.
4. **A `supersedes=<id>` reference without a visible id.** The prompt tells the model to use an id from the memory block
(`prompts.py:313-314`), but the block does not display ids — the model can only guess or search
first via `memory search`.

**Four attack scenarios:**

| # | Scenario | What is attacked |
|---|---|---|
| S1 | One person sends hundreds of claims within an hour until retrieval is saturated and the old facts sink | memory as a whole |
| S2 | Someone sees a memory id and then persuades the soul to use `supersedes=<id>` to overwrite stable facts | a specific row |
| S3 | Two conflicting claims about the same person; the system quietly picks the newest | the truth of a fact |
| S4 | A weak claim (ambient/reflection synthesis) slowly rises to "core" because it keeps appearing | Tier 1 (ADR-0005) |

## 2. Decision drivers

1. **Trust must be computable deterministically** from stored facts, not judged by a model.
2. **Rules and persona must not be born from other people's speech** — only from the soul and its documents.
3. **No silent loss**: what is held back must be visible and able to live again.
4. **The write path stays cheap**: no model call per message (the spirit of ADR-0002/0003).
5. **Contradictions are not settled by "the newest wins".**
6. **Auditable**: every row's origin can be traced.
7. **It changes nothing** about hard exclusion, the privacy ceiling (ADR-0006), or the permission classes (ADR-0007).

## 3. Options considered

| Options | Shapes | Excess | Lack | Status |
|---|---|---|---|---|
| **A. All rows are equal (status quo)** | All rows treated the same | Simplest | An instruction can disguise itself as a rule; a weak claim ranks equal to a self fact | Rejected |
| **B. Trust as a ranking weight only** | Trust only lowers retrieval scores | Does not touch behavior | A weak fact can still change behavior if its score is high | Rejected (decision: hard limit) |
| **C. Hard trust + quarantine + anti-poisoning A/B/D, `disputed` later** | Provenance → trust → behavior limit; instructions quarantined; volume limited; overwriting needs evidence; strict promotion | Every attack has an owner; the write path stays cheap | Needs new columns/attributes + the `superseded` state | **Selected** |
| **D. Every write needs approval** | A human approves every store | Safest | Impractical; the soul would have no automatic memory | Rejected |

## 4. Decision

> **Every memory row carries provenance. Trust is computed from provenance and bounds the influence on behavior. Imperative text never becomes a rule. Write volume is limited and overwriting needs evidence. Promotion is only for strong provenance.**

### 4.1 Provenance

Every row stores, on top of the existing fields:

| Attributes | Contents | The field exists |
|---|---|---|
| `via` | `turn` / `tool` / `command` / `reflection` / `ambient` / `automation` / `soul_doc` | derived from `source` (old values mapped, not a second column) |
| `stated_by` | the speaker who states it | `owner_id` |
| `about` | the person being talked about | `subject_id` |
| `thread_id` | the room of origin | `thread_id` |
| `asserted_at` | the time of the statement | row time |
| `refs` | a message/event reference, if any | metadata |

Provenance is the **only** source of trust — there is no model judgment on this path.

### 4.2 Trust tiers and the hard limit

| Tier | Name | Source | May be used for |
|---|---|---|---|
| **T0** | `self_stated` | a person states something about themself (`about == stated_by`) | behavior: preference, style, procedure, tool decisions |
| **T1** | `stated_about_other` | a person states something about someone else | context and knowledge; does **not** change rules |
| **T2** | `soul_inference` | ambient/reflection (synthesis by the soul) | hints/topics; does not change rules; cannot become Tier 1 |
| **T3** | `unlabeled` | not labeled (`level NULL`, ADR-0004) | not read at all |

- **Hard limit:** rules, persona, style, and tool decisions may only come from **T0** +
the soul document (`soul.yaml`/the `soul` document, `engine`). T1 and T2 may be remembered and quoted, never
governing.
- **Enforcement points:** (a) the retrieval funnel (ADR-0006) drops T3; (b) the prompt-block builder
marks the tier; (c) the write of `kind=rule` is restricted (§4.3); (d) test: a T1/T2 row that influences
a rule fails.
- **Trust ≠ privacy level.** They are two different axes: trust answers "who said it", level answers
"how sensitive it is". They do not replace each other.

### 4.3 Instruction quarantine

- **The deterministic pattern always runs:** an extension of `INJECTION` + imperative patterns ("mulai sekarang",
"jangan pernah", "aturan baru", "always/never ... you").
- **Only what is unclassified** is seen by a small LLM classifier — the same pattern as the sandbox
guard (`guard/sandbox.py:139-192`). There is no model call for text that is already clear.
- **Outcome:** imperative text from a user is at most stored as a **sourced fact** ("X
said ..."), never as a rule, and never touches the soul document. The document write
path stays only `settings`/`language` with permission and approval (ADR-0007).
- **`kind=rule` only from the soul document:** one path, `via=soul_doc` (soul-document extraction), provenance T0, and corroboration ≥ 2 days. Reflection over memory **never** writes a rule (ADR-0010 §4.5); user speech has no path there.
- **The memory block displays `kind` for rules**, so that a genuine rule can be distinguished from an ordinary fact.

### 4.4 Anti-poisoning

**A. Text volume limit (rate limit).** The quota is **per category**, not one number. An excess
**does not** enter `pending` — that is the bucket for data *not yet labeled* (ADR-0004) with a 7-day TTL,
too short for already-labeled rows. The excess enters state **`staged`**: already
labeled (category, level, trust, provenance), unreadable, and its TTL follows `kind`
(365/180/90 days, ADR-0005).

| Safeguard | Contents |
|---|---|
| Tiered quota | `health`/`safety`/`identity`/`commitment` **do not count against the quota**; `event`/`note`/the first conversation are held back |
| `staged` ≠ `pending` | `pending` = not yet labeled, TTL 7 days. `staged` = already labeled, TTL follows `kind`. Not discarded, not read |
| Promotion on recall-miss | If normal retrieval (`state='ok'`) finds nothing, a `staged` row matching the query is promoted to `ok` **first**, then used — so a fact first needed three weeks later stays alive |
| The time wall still applies | `staged` follows its `kind` TTL; never used or promoted → exhausted with a tombstone like any other row |
| A conscious signal breaks through | `ingat ini` (MiniLM prototype, ADR-0005), `pin`, and writes through `tool`/`command` do not count against the quota |
| Burst triggers a batch | Volume ≥ a multiple of the quota → the worker reviews `staged` (promotion/purge) earlier, instead of waiting for the idle timer |

The invariant stays intact: **only `state='ok'` can be read** (ADR-0004 §5.3). Promotion happens
before use, and during promotion the privacy ceiling (ADR-0006) and trust (§4.2) remain
enforced. The miss path is limited: at most once per turn and at most a few rows, so
that it does not become a back door for opening all of `staged`.

**B. Supersede is proven.** `supersedes` is legal only when: the same `stated_by`, the same `about`, the same scope and
`kind`, and the new claim genuinely conflicts. The old row is not deleted: it becomes
`superseded` with a date and a pointer to its replacement (TTL 30 days, ADR-0005), so it can
be reversed and audited. The old row's `level` and `refs` are **kept** (CHECK in ADR-0016 §4.3).
A Tier 1/`pinned` row cannot be overwritten by another party. The memory block shows a
**short id** (e.g. `#a1b2`) so that `supersedes` does not have to guess — completing the finding of hole #4.

**B2. A status change is not a contradiction.** A task or commitment changes state with no conflicting
claim: "the project is finished", "the plan is cancelled", "it slipped to next week". The
`supersedes` path therefore accepts **two reasons** with the same proof requirements (the same `stated_by`,
the same `about`/scope/`kind`):

1. **Contradiction** — the new claim negates the old one.
2. **Status change** — the new statement explicitly declares a new state (`done`,
`cancelled`, `postponed`), not a different statement about something else.

The old row still becomes a dated `superseded`; only the new row's `status` changes. If
the new statement only adds information without changing state, it is stored as an ordinary row —
it overwrites nothing.

**B3. `commitment` has a life cycle.** A task that is **finished or cancelled is still remembered**, but
it is **no longer pinned**: it loses Tier 1 status and follows the TTL of its `kind` like any other row
(ADR-0005). So that a dangling task does not become immortal:

- `commitment` accepts an optional `due_at` (a deadline, when mentioned).
- Passing the deadline without a completion statement: the status is marked `overdue`, the pin is released, and the normal TTL
applies — not pinned forever, not quietly deleted.
- A pin (Tier 1) applies only while the task is **open**.

**D. Promotion needs strong provenance.** Tier 3 → 2 only for T0/T1 + corroboration ≥ 2 days
(ADR-0005); Tier 2 → 1 only for T0 + corroboration, and T2 never reaches Tier 1; a T1 row about
another person needs the subject's confirmation to rise.

**C. `disputed` is run in ADR-0010.** Marking contradictions needs batch-style detection;
putting it on the write path would mean adding model calls to every capture. ADR-0010 already
closes it: the `dispute_state`/`disputed_with` metadata (not a new `state` value), a detector that is
deterministic first and then LLM only for candidates, and resolution through the subject in a DM. Until the
first pass runs, contradictions remain handled by B (proven supersede) and D (strict promotion):
a weak claim cannot shift a strong one, and an overwrite without evidence is rejected.

### 4.5 Grounding reflection

Reflection (ADR-0010) may only conclude from canonical rows that are `state='ok'` and
T0/T1 in provenance, recording `refs` containing the source row ids (the name used by ADR-0010), and it must not
create new facts. A reflection row is always T2 — a hint, not a rule — and cannot
promote itself. `state` and trust are two separate axes: a derived row may be
`state='ok'` (read as context) while staying T2 (never binding behavior), just like
`via=automation`.

### 4.6 Accompanying small improvements

- The memory block displays a short id (so that `supersedes` can be used without an extra search).
- The memory block displays `kind` for `rule` rows (so that a genuine rule does not disguise itself).
- If those two markers are not wanted, the alternative is to fix the prompt wording so that it does not
assume ids are visible and does not mention rules that cannot be told apart.

## 5. Configuration

```yaml
memory:
 integrity:
 trust_behavior_gate: true # rules/persona only from T0 + soul documents
 rules_from_users: false # kind=rule never from user speech
 quarantine: { enabled: true, classifier: true }
 write_limit:
 enabled: true
 per_person: { "1h": 20, "1d": 60 }
 per_chat: { "1h": 50 }
 exempt_categories: [health, safety, identity, commitment
 exempt_via: [tool, command
 conscious_intent_bypass: true
 burst_trigger: 3 # quota multiple -> earlier batch
 overflow_state: staged # not pending; TTL follows the kind
 staged_ttl: kind # 365/180/90 days follow the kind (ADR-0005)
 promote_on_miss: { enabled: true, max_per_turn: 1, max_rows: 3 }
 supersede:
 require_proof: true
 keep_old_as: superseded
 ttl_days: 30
 allow_status_change: true # B2: done/cancelled/postponed is not a contradiction
 commitment:
 due_at: true # B3: deadline optional
 overdue_unpin: true # past the deadline -> pin released, follows the kind TTL
 promotion:
 require_trust: [T0
 corroboration_days: 2
 render:
 show_short_id: true
 show_kind_for_rules: true
```

## 6. Metrics and gates

| Metric | Start target |
|---|---|
| T1/T2 rows that influence rules | 0 (tested) |
| `kind=rule` that comes from user speech | 0 (tested) |
| Overflow to `staged` per hour + the number of unused `staged` rows | Visible in `/status` |
| Promotion of `staged` through recall-miss | Measured; 0 means the miss path is not working |
| `staged` rows that run out their TTL without ever being used | Visible, not zero — input for tuning the quota threshold |
| Supersede that passes without evidence | 0 (tested) |
| A Tier 1 row with T2 provenance | 0 (tested) |
| Silent loss (a row missing without a tombstone) | 0 (tested) |
| A quarantine false negative (an instruction passing as a fact) | Measured through an eval probe |

## 7. Acceptance criteria

- [ ] Every new row has `via`, `stated_by`, `about`, `thread_id`, `asserted_at`.
- [ ] Trust is computed deterministically from provenance; there is no model call on this path.
- [ ] Rules/persona/style/tool decisions never come from T1/T2/T3.
- [ ] Imperative text from a user is never stored as `kind=rule`.
- [ ] `kind=rule` is born only from `via=soul_doc` + T0 + corroboration ≥ 2 days.
- [ ] The prompt block shows trust/kind markers so that the reader can distinguish a genuine rule from a sourced fact.
- [ ] The write quota is tiered by category; `health`/`safety`/`identity`/`commitment` are not held by the quota.
- [ ] Overflow becomes `state='staged'`, labeled, with the TTL following `kind`; **not** `pending`, and not discarded.
- [ ] A `staged` row never appears in normal retrieval/prompt/disclosure.
- [ ] Recall-miss promotes matching `staged` rows to `ok` before use, through the same privacy ceiling and trust.
- [ ] The miss path is limited to once per turn and at most `max_rows` rows.
- [ ] A burst (≥ a multiple of the quota) triggers the `staged` batch earlier, instead of waiting for the idle timer.
- [ ] `supersedes` is rejected without evidence; the old row becomes a dated `superseded`, not missing.
- [ ] A status change (finished/cancelled/postponed) is valid as `supersedes` without having to be a contradiction.
- [ ] A statement that only adds information overwrites no row.
- [ ] A finished/cancelled/`overdue` `commitment` is no longer pinned and follows the `kind` TTL.
- [ ] Promotion Tier 3 → 2 and 2 → 1 follows the provenance + corroboration requirements.
- [ ] Reflection records `derived_from` and its result is always T2.
- [ ] Hard exclusion, the privacy ceiling, and the permission classes (ADR-0006/0007) do not change.

## 8. Risks and mitigations

| Risks | Mitigation |
|---|---|
| Forgetting during an intense conversation | A category priority path + `staged` labeled with the TTL following `kind` + promotion on recall-miss + a burst triggering a batch + a conscious signal breaking through the quota; the threshold is in the config |
| A fact was needed weeks later | `staged` is not punished with the 7-day TTL like `pending`; it lives until its `kind` TTL and is promoted when the topic comes up |
| `staged` piles up without ever being used | `kind` TTL + a purge batch + the "ran out its TTL unused" metric as input for tuning the threshold |
| The recall-miss path becomes a back door | Limited to once per turn + `max_rows`; promotion still passes the privacy ceiling and trust; recorded in the metrics |
| The quota is too tight for legitimate chat | The quota numbers and exempt categories can be tuned; overflow is visible, not lost |
| An instruction escapes quarantine (false negative) | The deterministic pattern + a classifier for the rest + the prompt rule + an eval probe |
| An ordinary fact is wrongly quarantined (false positive) | Quarantine only downgrades it to a sourced fact; the content is not discarded |
| Disputes/contradictions are not yet handled systemically | Handled through B/B2 and D until ADR-0010 adds `disputed` |
| Automation writes its own memory without let-up | See ADR-0014 §4.6: `via=automation`, trust T2, it counts against the owner's volume quota |
| A dangling task becomes immortal | B3: `due_at` + `overdue` status releases the pin and returns it to the `kind` TTL |
| `supersede` is used to silence facts one dislikes | The evidence requirement (same speaker/subject/scope) + the old row stays stored as a dated `superseded` that can be reversed |
| Adding provenance attributes | A schema change without a compatibility shim (ADR-0001: `SCHEMA_VERSION` goes up, a fresh install) |
| The operator needs rules from users | The `rules_from_users: true` lever is available with a documented risk and approval |

## 9. Interaction with other ADRs

- **ADR-0001** — canonical remains the source of truth; provenance is its column, the index is only a shadow.
- **ADR-0002** — the volume quota enters the structural gate (cheap, no model); the assess stays batch.
- **ADR-0003** — the worker runs the `staged`/`pending` batches and bursts without checking trust itself.
- **ADR-0004** — the `staged` state is added there: labeled, unreadable, promoted through recall-miss or a batch; `pending` stays for data not yet labeled, with a 7-day TTL.
- **ADR-0005** — the `staged` TTL follows `kind`; promotion uses the durability/corroboration signals; `superseded` already exists there.
- **ADR-0006** — the privacy ceiling stays in front, including during a `staged` promotion; trust does not raise the ceiling.
- **ADR-0007** — the permission class does not change; `kind=rule` is not a new permission path.
- **ADR-0010** — `disputed`, batch contradiction detection, and grounding reflection live there (**accepted** — `ADR-0010-reflection-disputed.md`); it also reviews `staged`. Its shape: the `dispute_state`/`disputed_with` metadata (not a new `state` value), a dispute does not extend the TTL, and resolution goes only through the subject in a DM.
- **ADR-0014** — memory produced by automation submits to this ADR: `via=automation`, trust T2, it counts against the owner's volume quota, and it may not be `kind=rule`.
- **ADR-0011** — the writes of `pending`, `staged`, and `superseded` must survive a restart.

## 10. Code references

- `eidesoul/engine/guard/patterns.py:11-35,16-26` — `SENSITIVE`, `safe_text`, `INJECTION`.
- `eidesoul/engine/guard/sandbox.py:139-192` — deterministic patterns + a small classifier.
- `eidesoul/engine/agent.py:906-918` — the capture gate (injection, length, `can_memory`).
- `eidesoul/engine/privacy.py:225-277,295-310` — the capture turn and `_assess`.
- `eidesoul/engine/memory/mem0.py:308-392,323-338,527-530` — metadata, supersede, veto.
- `eidesoul/engine/memory/context.py:91-133` — `render` (where id/kind are added) and `build`.
- `eidesoul/engine/tools/memory.py:69-105,175-183,230-234` — `add`, `kind`, `supersedes`, `veto`.
- `eidesoul/engine/commands/memori.py:83-134` — the command door (source `command`).
- `eidesoul/engine/ambient.py:251-256,310-315` — soul synthesis (source `ambient:<chat>`).
- `eidesoul/engine/turn/prompts.py:53-56,266,300-316` — the memory and crisis prompt rules.
- `eidesoul/engine/schema.py:129-142` — the `people` table (relationship trust, not memory trust).

---

*This ADR lives at `docs/v0.1.0/adr/engine/0008-memory-integrity.md`.*
