# ADR-0020: Derivative Procedure — Skill as Soul Growth, Not a Tool

- **Status:** accepted
- **Implementation:** implemented — evidence: `episodes` + six closure signals, `distill` (one revision per episode), `skill_revisions`, scope-aware retrieval ordered by priority, the failed-step status through `llm.run` → `turn_steps`, the `signature`/`evidence`/`eligible` comparison (`eidesoul/engine/memory/efficiency.py`), the ladder actions with a proposal cooldown and an approval-only automation proposal, `tests/test_episodes.py`, `tests/test_efficiency.py`; gate: `pytest -q` 831 passed, `scripts/adr.py --check`
- **Date:** 2026-11-03
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Context:** the tool `skill` is explicitly unused, yet it charges an index of ±3000 characters on every prompt for every role that holds it; the `skills` schema without scope/provenance/version turns it into a global instruction channel that the model can write — the path ADR-0008 closes for memory
- **Supersedes:** —
- **Related:** ADR-0001 (capture pattern), ADR-0003 (`cognition_jobs`), ADR-0005 (TTL/pin), ADR-0006 (privacy ceiling), ADR-0008 (quarantine/trust), ADR-0010 (reflect & merge), ADR-0013 (`turn_steps`), ADR-0014 (automation & approval), ADR-0015 (payload budget), ADR-0016 (DDL), ADR-0017 (episode closure), ADR-0019 (cost metrics)
- **Amendments:** 2026-11-03 — owner decision: assessment happens **when one task is finished** (the episode is closed), not every turn; the LLM assesses the episode content in the worker, not on the conversation path. 2026-11-03 — added the **sixth episode closure** (artifact sent) and **§4.8 episode-to-episode comparison**. 2026-11-03 — added the **§4.9 efficiency ladder**; promotion/automation decisions **must not be hardcoded**. 2026-11-03 — added **§4.10: learning on by default (out of the box)** — no opt-in, no questions after the task is finished; approval only for changes to the autonomy level (automation).
- **Location note:** ADR lives in `docs/v0.1.0/adr/engine/`; the Indonesian copy is `docs/v0.1.0/adr/engine/id/0020-procedural-memory.md`.

---

## 1. Context

A skill today is an **explicit tool**: `list`/`view`/`read`/`save`/`archive`, with the index inserted into the prompt through `skills.index()` (`agent.py:640`) and bounded by `max_index_chars: 3000` (`config.py:157`).

| Fact | Evidence |
|---|---|
| The index always tags along, even when empty | `agent.py:637-640`; `stores/skills.py:88-102` |
| Usage is only recorded when the model calls `view` | `stores/skills.py:149` |
| Schema without scope, provenance, version, trust | `schema.py` `skills(name, description, body, archived, created_at, updated_at)` |
| `member` holds the tool `skill` | `roles.yaml:8-14` |
| The prompt tells the model to manage it itself | `prompts.py:318` ("action=save; archive useless skills") |
| No usage data in the field | owner's report: the tool is never used |

Compare that with memory, which works without being told: capture runs in the background (`spawn`), assessment in the worker, and retrieval decides whether something is useful. A skill should follow the same pattern instead of asking the model to "remember to save".

The concept used here: **skill = derivative procedure** — "how I usually do X in this room/person", inferred from the work traces that already exist, compared one against another to find a cheaper way, and finally — if it really can — stops being a conversation at all.

## 2. Decision drivers

- A prompt must not pay for capabilities that are not used every turn.
- Learning happens at **work boundaries**, not in every sentence: one finished task = one chance to assess.
- Persistent instructions must not be born without scope, provenance, and a trust gate (ADR-0008).
- The cost of evaluation must be outside the conversation path: it adds no latency and holds no lock (ADR-0017).
- **Efficiency must be measured**, not felt: the question "which way is cheaper" is only valid when there are numbers per episode (ADR-0019) and a comparable comparison.
- Learning may change **procedures**, never **rules, persona, or permission**.
- **No magic numbers.** A fixed threshold that triggers promotion or automation conversion is a dangerous hardcode: it would move work that is not yet stable.
- **Out of the box.** After a task is finished, people no longer care. So learning, comparing, and aging run on their own with the default settings — without opt-in, without questions, without a button to press. Turning it off is an operator decision, not a condition for obtaining the benefit.

## 3. Options considered

| Options | Contents | Cost | Risks | Verdict |
|---|---|---|---|---|
| **A. Explicit tool (status quo)** | the model decides `save` | index 3000 chars/turn | unused; a global instruction channel without trust | Rejected |
| **B. Derivative procedure + retrieval** | the worker assesses episodes, procedures are stored in the `skills` table with a memory life cycle, injected when relevant, compared across episodes, and can be offered as automation | worker + retrieval | noise/duplication | **Selected** |
| **C. `kind='procedure'` in `memories`** | one table | small | long procedure bodies mixed with facts and a broken memory-block render | Rejected |

## 4. Decision

### 4.1 A skill is not a tool

- The tool `skill` is **deleted** from the catalog and from any role; the skill index **no longer** enters the prompt (`skills.index()` is deleted from `agent.py`).
- Owner control stays explicit through one owner-only command: inspection, pin, forget, and import from a file. `member` gets no surface at all.
- A procedure only enters the prompt through **retrieval** (§4.6), with the same character budget as memory blocks.

### 4.2 Episode: the assessment unit

- An **episode** = a sequence of consecutive turns in one room that may do one job; `episode_id` = the id of the first turn.
- An episode is **closed** by one deterministic signal:
 1. an idle pause in that room ≥ `skills.task_idle_minutes` (default 10),
 2. an explicit completion marker from the user ("done", "that works now", "ok, settled") — through the same MiniLM prototype as the conscious signal of ADR-0005,
 3. a topic change (`currents`/`topics` change) after at least one work turn,
 4. a `commitment` that is closed, or an `automation_run` that finishes,
 5. a clean shutdown: episodes that are still open are closed, not discarded,
 6. **artifact sent**: a sequence of turns that ends with a file delivery (`outbound_messages` with an attachment) counts as finished work — assessed after the short pause `skills.idle_after_send_minutes` (default 3), not after waiting 10 minutes.
- An episode does **not** become a new table: its closure is computed from the existing `turns`/`backlog`/`outbound_messages`/`thread_eras`; only the result is recorded.

### 4.3 When it is assessed

- When an episode closes, the engine inserts **one** job `cognition_jobs(kind='distill')` with the `episode_id` and the evidence references (turn id, tool signature, results, correction markers).
- Filter before enqueue (deterministic, no LLM): an episode is admitted only if there was **tool work** (≥1 `execute` call) or a **correction marker** from the user. Ordinary chat is never assessed.
- **The LLM assesses the episode content in the worker** (batch, lease, `SKIP LOCKED`, budget of ADR-0003/0010) — not on the conversation path, not every turn. A finished evaluation writes **at most one** procedure per episode.
- An evaluation failure blocks nothing (the episode is already closed); the job goes to the dead-letter like any other job (ADR-0011).

### 4.4 What may become a procedure

A candidate is accepted only when it meets **one** piece of deterministic evidence:

| Evidence | Example |
|---|---|
| A repeated tool chain | the sequence `read → sandbox → write` with similar arguments succeeds ≥ 2 times |
| Error → repair | `execute` fails with one argument, then succeeds after the argument is changed |
| User correction | "not like that, use X" followed by an accepted result |
| An old procedure that helps | a skill that is injected, used, and the episode succeeds (raising `uses`) |

And **all** of these conditions:

- `via=reflection` with `refs` to the source turn/episode (without a reference → rejected);
- the content passes the `SENSITIVE`/`INJECTION` guard (ADR-0008); a procedure never contains credentials;
- it cannot add a tool or a permission — a procedure only mentions tools already in the catalog;
- scope defaults to `chat` or `person`; `global` only when the source is the owner (T0) or it is promoted through approval;
- contents ≤ `skills.max_body_chars` (default 18 000) and description ≤ 200 characters.

### 4.5 Storage

- The `skills` table stays, with extra columns: `scope`, `via`, `stated_by`, `source_episode`, `uses`, `last_used`, `revision`, `trust`.
- Append-only history in `skill_revisions(skill, revision, body, description, created_at, source_episode)`; `save`/merge does not destroy old versions, so rollback is possible.
- The column shape, constraints, and indexes **belong to ADR-0016** (§4.7); this ADR only sets the contents and their use.
- **Cost is not stored on the skill row**: it is computed at comparison time (§4.8) from `llm_calls`/`turn_steps`, so the numbers can be recomputed and do not become stale metadata.

### 4.6 Retrieval, not an index

- Matching uses the same embedder (`embed.py`, MiniLM 384-d) against `description` + steps, only within the scope the requester may read (ADR-0006 ceiling).
- A procedure is injected as a reference block with a limit of `skills.max_chars` (default 800) for **all** procedures on that turn; one full procedure is better than five fragments.
- A procedure that needs a tool the roles on that turn **do not** have is not injected (not merely hidden): the soul must not offer a step that is certain to fail.
- Injection raises `uses`/`last_used` — the TTL renewal signal (ADR-0005).

### 4.7 Life cycle

- TTL follows usage, not the write date: a procedure that is never used passes `skills.use_renewal_days` (default 30) and its TTL ends → `archived`, not deleted, so the owner can still restore it.
- `pin` is only for procedures written/imported by the owner; a derivative procedure cannot pin itself.
- Duplicates are merged in the consolidation pass (ADR-0010): similar description + similar steps → one survivor, the revision is recorded.

### 4.8 Episode-to-episode comparison: "how can it be more efficient?"

This answers a different question from "what did I learn just now": the first remembers the **content**, the second compares the **method**.

- **Signature** = the ordered tool names + target type (room/file/document) + scope. Not message content, not free arguments — so that "the same job" stays the same even when the sentences differ.
- **Comparable first, then compared.** Two episodes may only be compared when their signatures are the same. Comparison across signatures is forbidden — that compares apples and oranges.
- **Numbers come from sources that already exist**: `llm_calls` (tokens/`latency_ms`, ADR-0019) joined to the episode through `turn_id`, plus `turn_steps` (ADR-0013) for the number of steps and failures, and the `uses` of the injected procedure.
- **Only when the evidence is sufficient**: the comparison runs at `reflect` (ADR-0010) when there are ≥ `skills.efficiency.min_evidence_episodes` (default 2) episodes with the same signature inside the `skills.efficiency.window_days` window (default 30). These numbers are an **evidence floor**, not a decision trigger.
- **Allowed actions** (on procedures only):
 1. **promote** a procedure with a lower median tokens/steps **when its success rate is equivalent** → a new revision + a higher retrieval ranking (not immunity),
 2. **merge** two similar procedures (consolidation, ADR-0010),
 3. **archive** a wasteful procedure that always loses, with its revisions kept,
 4. **propose** a step correction to an old procedure (a new revision, not a silent overwrite).
- **Hard limit**: a comparison result **never** changes rules, persona, style, permission, or security defaults. Only the procedure's steps may change.
- **Deterministic outside the decision**: episode pair selection, median calculation, and the completeness of the numbers happen without an LLM; the LLM is used only when two procedures must be summarized into one procedure text.
- **Visible, not silent**: every promotion/archive writes a row in `skill_revisions` + one operator `notice` (not a message to the room), so the owner can reverse it.

### 4.9 Efficiency ladder: procedure → automation, decided from evidence (not fixed numbers)

The question "can the turn count be lower?" is only valid when answered from real episodes, not from a fixed rule.

- **Evidence packages** are assembled by the worker per signature: episode list, argument variations, median turns/tokens/steps, failures, step differences between episodes, and the artifacts produced.
- **The deterministic part only guards evidence quality**, it does not trigger the decision: at least `min_evidence_episodes` episodes, `failed_episodes = 0` in the window, and equivalent deliverables. There is no "N successes → automation" rule.
- **The LLM assesses the evidence package** and returns exactly one output with a reason + `refs`: `keep`, `merge`, `promote`, or `propose_automation`.
- `promote` — used when the steps can be shortened without losing the result; the effect is a procedure revision + retrieval ranking.
- `propose_automation` — a proposal `event` → `condition` → `steps` that must capture **all** cases in the evidence package (argument variations, past failures). The proposal **executes nothing**: it enters `approvals` and waits for the owner (ADR-0014 §4.5, §4.9). Rejected or expired → it stays a procedure, with no silent retry.
- **Explicit prohibition (hardcode):** no `signature → automation` mapping, no list of tool chains that "may become automation", no target turn count as a gate. Adding a new tool must not require a code change on this path; a new signature is still assessed by the same rules.
- **Conversation floor**: 2-3 turns (retrieval + action + confirmation). Dropping to 0 turns only happens through **approved** automation, not by cutting verification.
- **Reversible**: a promoted procedure that later fails falls back to the previous revision; a failed automation is disabled to `paused` and the work is returned to the conversation procedure, with one operator notice.

### 4.10 On by default: learning must not wait to be asked

- `skills.enabled`, `skills.efficiency.enabled`, and `skills.ladder.enabled` **are on by default**. A fresh install learns without changing configuration; turning it off is an operator opt-out, not a condition for use.
- **There are no learning questions to the user.** After a task is finished there is no "shall I save this way?", no procedure confirmation, no approval prompt for distillation. Questions are born only from rights (privacy L3/L4, veto) and from autonomy (automation proposals), not from learning.
- **There are no buttons to press.** Procedures are formed, promoted, and archived without interaction; the only trace the user sees is shorter, more precise answers.
- **Operators still see**: every promotion/archive/automation proposal appears in `/status` and as an operator notice, so the silence of learning does not mean it is unwatched.
- **Degradation without asking**: when the provider does not return `usage`, the efficiency comparison is skipped (instead of asking the user for numbers); when the embedder is unavailable, retrieval falls back to keywords. No path ends with "please confirm to continue learning".

## 5. Configuration

```yaml
skills:
 enabled: true # on by default; operator opt-out, not opt-in
 task_idle_minutes: 10 # episode close on idle
 idle_after_send_minutes: 3 # episode close after the artifact is delivered
 per_episode: 1 # maximum new procedures per episode
 max_chars: 800 # injection limit for all procedures per turn
 max_body_chars: 18000 # body limit for one procedure
 use_renewal_days: 30 # TTL extended when used
 index_in_prompt: false # stays false: a skill never becomes an index
 efficiency:
 enabled: true
 min_evidence_episodes: 2 # evidence limit, not a decision threshold
 window_days: 30
 ladder:
 enabled: true
 proposal_cooldown_days: 14 # proposal anti-spam, not a decision trigger
```

The old `max_index_chars` key is removed along with the index.

## 6. Metrics and gates

| Metric | Definition | Gate |
|---|---|---|
| `episodes_closed_total` | episodes closed per signal §4.2 | reported per closure type (including `artifact_sent`) |
| `distill_jobs_total` | `distill` jobs that are enqueued | only episodes with tool work/corrections |
| `skills_created_total` / `skills_merged_total` | new / merged procedures | ≤ 1 per episode |
| `skill_hits_total` | injections that were used in a successful episode | rises over time |
| `skill_chars_injected` | procedure characters per turn | ≤ `skills.max_chars` |
| `skill_decay_archived_total` | procedures archived for being unused | visible, not silently zero |
| `efficiency_compares_total` | same-signature episode pairs compared | > 0 once the evidence is sufficient |
| `skill_promoted_total` / `skill_archived_expensive_total` | promotion for being cheaper / archive for being wasteful | every action has a revision row |
| `ladder_evaluations_total` | evidence packages assessed for the efficiency ladder | only when the evidence is sufficient + cooldown |
| `automation_proposals_total` / `automation_proposals_approved_total` | automation proposals / those approved by the owner | a proposal without approval is never executed |
| `episode_cost_tokens_p50/p95` | tokens per same-signature episode | reported; a downward trend = learning works |
| `learning_prompts_total` | learning/skill-confirmation questions sent to the user | **0** (out of the box without a door) |
| `prompt_skill_chars` | skill characters in the prompt outside retrieval | **0** (index deleted) |

## 7. Acceptance criteria

- [ ] `skills.index()` is no longer called on the prompt path; `prompt_skill_chars = 0` on every turn.
- [ ] The tool `skill` is not in the engine catalog and is not given to any role; an owner-only command is available for inspection/pin/forget/import.
- [ ] Episodes are closed by the six signals of §4.2, including `artifact_sent` with `idle_after_send_minutes`; an episode with no tool work and no correction produces no `distill` job.
- [ ] Exactly one `distill` job per closed episode; evaluation runs in the worker, not on the conversation path (it adds no turn latency).
- [ ] New procedures always have `via=reflection`, `refs` episodes, and scope `thread`/`person`; `global` only from the owner or approval.
- [ ] Procedures containing `SENSITIVE`/`INJECTION` are rejected; a procedure never adds a tool/permission (test against the catalog).
- [ ] Retrieval injection ≤ `skills.max_chars`, only tools the role holds, and it raises `uses`/`last_used`.
- [ ] Unused procedures pass `use_renewal_days` → `archived`, can be restored, and do not appear in retrieval.
- [ ] `save`/merge writes a `skill_revisions` row so the previous version can be restored.
- [ ] Efficiency comparison only happens between episodes with the **same signature**, inside the window, and only when the evidence is sufficient; there is no cross-signature comparison (test with two different signatures).
- [ ] Promotion/archive never changes rules/persona/permission; tests make sure no path runs from §4.8 to `soul.yaml`/roles.
- [ ] Every promotion/archive is recorded in `skill_revisions` and produces one operator `notice` that can be reversed.
- [ ] The efficiency ladder **uses no fixed numbers**: there is no "N successes → automation" rule; the `ladder` output always comes from an assessment of the evidence package and carries `refs` (test: a new signature is assessed without a code change).
- [ ] `propose_automation` executes nothing; without owner approval it stays a procedure, and a rejected proposal is not retried silently.
- [ ] A fresh install with the default configuration runs `distill` without settings changes; `learning_prompts_total = 0` (test scans outbound messages: no learning/skill-confirmation questions).
- [ ] Leak test: a procedure scoped `person`/`chat` never appears in another room.

## 8. Risks and mitigations

| Risks | Mitigation |
|---|---|
| Distillation produces junk procedures | §4.4 evidence requirements + at most one per episode + usage-based decay |
| A wrong procedure persists for a long time | append-only revisions + `archived` + the owner can forget/pin; consolidation merges duplicates |
| The evaluation cost swells | only episodes with tool work/corrections; batch worker; `per_episode: 1` |
| A procedure leaks the contents of a private room | default scope `thread`/`person`; ADR-0006 ceiling at retrieval; leak tests |
| A procedure becomes a new instruction path | content stays data until injected; ADR-0008 quarantine; it cannot add a tool |
| Evaluation assesses an episode that is not really finished | deterministic closure §4.2; episodes forced open by shutdown are still closed, not assessed twice (idempotent per `episode_id`) |
| The efficiency comparison compares things that are not comparable | signature + scope + window requirement; without a signature match there is no comparison at all |
| "More efficient" is used to cut caution | hard limit §4.8: the procedure may change, rules/permission may not; tested, not just written |
| Token numbers are partly missing (provider without usage) | episodes without a complete `llm_calls` are excluded from the comparison (not treated as zero) |
| The ladder assessment is too aggressive in proposing automation | mandatory evidence packages, owner approval, `proposal_cooldown_days`, and reversibility |
| Covert hardcode (signature/tool lists that "may become automation") | explicitly forbidden; tests make sure a new signature is assessed without code changes |
| Learning becomes invisible by staying silent | every action appears in `/status` and operator notices; metric §6 is exposed |
| On by default is read as "doing something without permission" | what is on is **learning**, not acting: sends/automation still need approval (ADR-0014) |

## 9. Interaction with other ADRs

- **ADR-0001** — the capture pattern (enqueue at a boundary, value in the background) is used as is; there is no second write path.
- **ADR-0003** — `distill` is a new `kind` in `cognition_jobs`, joining lease/`SKIP LOCKED`/retry/dead-letter.
- **ADR-0005** — TTL and renewal for procedures use the same rules (used = extended), pin only for the owner.
- **ADR-0006** — procedure retrieval obeys the same ceiling as memory; `global` does not open another room.
- **ADR-0008** — a procedure is data; quarantine, `SENSITIVE`, and the ban on raising permissions apply in full.
- **ADR-0010** — consolidation merges duplicate procedures; the `reflect` pass is where §4.8/§4.9 run (one schedule, one budget), but the rules belong to this ADR.
- **ADR-0013** — `turn_steps` is the source of the step/failure count for §4.8.
- **ADR-0014** — a proposal from §4.9 can only become automation through the ADR-0014 path: a valid manifest, the owner's authority checked twice, approval, and reversibility.
- **ADR-0015** — payload budget: a procedure enters as a reference block, not Layer 1; there is no skill index.
- **ADR-0016** — owner of the `skills`/`skill_revisions` DDL (§4.7).
- **ADR-0017** — episodes are closed using the existing pause/backlog; it adds no new lock or task.
- **ADR-0019** — `llm_calls` (tokens, `latency_ms`, `turn_id`) is the raw material for the §4.8 comparison; without it the efficiency question cannot be answered.

## 10. Code references

- `eidesoul/engine/stores/skills.py:88` — `index()` (the index to be removed from the prompt); `:144-149` — `view()` and the only `_record_use`.
- `eidesoul/engine/commands/skill.py` — the owner command; the model-facing `eidesoul/engine/tools/skill.py` is deleted.
- `eidesoul/engine/agent.py:637-640` — where the skill index is assembled (the cutover point).
- `eidesoul/engine/config.py:156-157` — `SkillsConfig.max_index_chars` (replaced, §5).
- `eidesoul/engine/turn/prompts.py:300,318` — the prompt rule that tells the model to manage its own skills (removed).
- `eidesoul/engine/schema.py` — tables `skills`, `skill_files`, `skill_usage` (new form in ADR-0016 §4.7); `turns`/`turn_steps` as the source of step counts.
- `eidesoul/engine/llm.py:206-255` — the tool loop: the source of each episode's tool-chain signature (material for §4.8).
- `eidesoul/engine/embed.py:53-62` — the embedder used by procedure retrieval.
- `eidesoul/engine/approvals.py` — the approval path used by §4.9 automation proposals.
- `roles.yaml:8-14` — the `member` role holding the tool `skill` (revoked).

---

*This ADR lives at `docs/v0.1.0/adr/engine/0020-procedural-memory.md`.*
