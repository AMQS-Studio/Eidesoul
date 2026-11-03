# ADR-0014: Automation → Event → Action, Owner Authority, and Sandbox as Steps

- **Status:** accepted
- **Implementation:** implemented — evidence: `automations`/`automation_runs`/`automation_state_log` (revisions 10 and 13), declarative manifest + authority check, occurrence idempotency, quiet hours/daily budget/kill switch, the step runner, retries + dead-letter, the `message` event source (`AutomationStore.enqueue_message` inside the intake transaction), the gateway capability counters on `/status`, and durable `threads.gateway` routing; gate: `pytest -q` 831 passed, `scripts/adr.py --check`
- **Date:** 2026-11-02
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Context:** follows ADR-0003 (worker/outbox), ADR-0006 (group privacy), ADR-0007 (capability vs authority), ADR-0008 (memory integrity), ADR-0011 (crash safety), ADR-0012 (lease), ADR-0013 (turn lifecycle)
- **Supersedes:** —
- **Related:** ADR-0005 (retention), ADR-0010 (reflection), ADR-0019 (`llm_calls`), ADR-0020 (proposals from the efficiency ladder)
- **Amendments:** 2026-11-03 — audit: the fencing column is `generation`, not `lease_generation`; ledgers follow ADR-0013 §6 with `scope_key = automation:<run_id>`; the state/run columns follow ADR-0016. 2026-11-03 — steps merged: `send_to` is removed (a duplicate of `send` + `to:`), and `cross_send` is specified as a rule of the step/tool; `send` is executed by the engine through `gateway.send_text`, not through `execute`. 2026-11-03 — owner's decision: the `sandbox` step does not carry `network`/`timeout` by itself; it uses the `sandbox` host with effective timeout `min(sandbox.timeout_seconds, turns.shutdown.drain_seconds)`. 2026-11-03 — `send` calls the same **tool gateway** `send` through `execute`; no second implementation or fence; the target is still validated against the manifest. 2026-11-03 — `catchup_minutes` is stored only in `automations.resilience.*` (the twin key at the root is removed). 2026-11-03 — §4.12: a request that demands autonomy must be answered with an automation proposal or an explicit refusal; no promise to monitor without an automation, no silent creation, and the autonomy question is in fact mandatory. 2026-11-03 — §4.13: the state life cycle (`draft/active/paused/stopped/expired`), the canonical run status, retry in two classes (transient/permanent), and dynamic conditions composed from a closed evaluator; `denied`/`disabled` are no longer used as automation state.
- **Location note:** ADR lives in `docs/v0.1.0/adr/engine/`; the Indonesian copy is `docs/v0.1.0/adr/engine/id/0014-automation.md`.

---

## 1. Context

A user's need does not stop at a time reminder. Its shape is always the same: **event, condition, action**:

| User speech | Event | Condition | Action |
|---|---|---|---|
| "You'll be back by 11 o'clock." | schedule | — | collect → compose → send |
| "If A says the project's over, let me know." | new message | sender A + the meaning "project is done" | send it to my DM |
| "If this group discusses HB, summarize it and write to docs" | new message | the meaning of “HB” | summarize → write to the workspace |

A reminder at 11 is only a **special case** of the same shape: the event is a schedule.

**Why not "full Python integration in the background".** Five things are lost at once: the group privacy ceiling and the presence of the requester (ADR-0006/0007) can be bypassed by a script that reads storage on its own; our guard works per turn, with a human nearby, while the background has no approval window; an internal API is a versionless integration surface; the lease/effectively-once/audit guarantees are missing; and the costs can run out of control. The industry answer for arbitrary code is **isolate + host functions** (WASM/Extism, Cloudflare Workers, Shopify Functions), not running user code in the process.

**Materials that already exist and are reused:**

| Materials | Evidence |
|---|---|
| Turn + tool + permission | `agent.py` (turn path), `tools/__init__.py:execute` (single permission door) |
| Worker, queue, retry, dead-letter | ADR-0003 |
| Job claim `SKIP LOCKED`, outbound ledger | ADR-0011 (claim/reaper), ADR-0013 §6 (ledger) |
| Lease + fencing (only the primary acts) | ADR-0012 |
| Approval | `approvals.py`, `/approve` (ADR-0007) |
| Isolated sandbox | `tools/sandbox.py:33-80` — `--read-only`, `--cap-drop ALL`, `no-new-privileges`, `--pids-limit`, limited memory/CPU, `--network cfg.network`, filtered env (`CONTAINER_ENV = TZ, LANG`), only the workspace mounted, output capped at 64 KB |
| Named recipe | `stores/recipes.py` (`run_recipe`) |
| Ambient never greets first | `ambient.py:1-4` |

## 2. Decision drivers

1. **Expressive for real wishes** — without asking the user to write a program.
2. **Logic may be free, the capability stays closed.**
3. **Privacy and permission cannot be bypassed** by automation.
4. **Owner authority, checked twice** (when created and when due).
5. **Effectively once, auditable and replayable**, plus a kill switch.
6. **Costs under control** — budget, quiet hours, per-automation limits.
7. **No second execution path** — one worker, one turn path for anything that needs a sentence.

## 3. Options considered

| Options | Shapes | Excess | Lack | Status |
|---|---|---|---|---|
| **A. Declarative automation + named steps** | `event` + `condition` + `steps` (manifest), closed actions, sandbox only with a named program | Expressive for real needs; all invariants remain valid; cheap review | Requires a bounded state language + a new table | ** Selected** |
| **B. Isolated Python hook** | Separate process, JSON in/out; the script only *declares*, the engine executes the validated manifest action | The freest logic among the accountable options | Larger surface, requires a contract + a sandbox process | Following (4.9) |
| **C. Full Python, direct integration, background** | Script in process, accesses storage + sends by itself | Unlimited | Bypasses privacy/permission; no approval; cannot audit; no crash/lease guarantee | Rejected |

## 4. Decision

> **Automation is a declarative row owned by a person: `event` → `condition` → `steps`, with a closed list of steps, a named sandbox program, owner authority checked twice, and effectively-once execution.**

### 4.1 Model

```yaml
automation:
 id: a-7f3a
 owner: person:xxx # who asked; the source of authority
 event: { kind: schedule, at: "11:00", tz: Asia/Jakarta }
 condition: { all: [] }
 steps:
 - { kind: collect, source: chat, chat: <opaque-id>, window: last_24h, limit: 200 }
 - { kind: sandbox, program: scripts/rekap.py, args: { format: markdown }, timeout: 60, network: none }
 - { kind: compose }
 - { kind: send, to: owner_dm }
 policy: { budget_per_day: 4, quiet_hours: "22:00-07:00", on_error: notify_owner }
 state: active # active | paused | denied | disabled
```

The internal name **`automation`** was chosen because `rule` is already used for `kind=rule` in memory (ADR-0008); `recipe` is already used for named procedures; and `hook` implies user code. What is said to the user is free — the soul paraphrases it through the LLM.

### 4.2 Event (closed, v1)

| Kind | Contents | Note |
|---|---|---|
| `schedule` | `at` (HH:MM daily), `every` (interval), `tz` | Tested with the DB clock + catch-up window |
| `message` | this room or a room list; the sender filter | From the existing gateway, not a new poll. |

Following (not v1): `memory_write`, `file_change`, and external adapters (e.g. `google`). Adding one requires core code; a user cannot inject new event sources.

**Repeat: one occurrence = one run.** `schedule` takes a time list, not just one:

| Field | Contents | Sample |
|---|---|---|
| `at` | list of `HH:MM` local (24-hour) | `["10:00"]`, `["01:00","07:00","12:00"]` |
| `days` | `daily` / day list / `every:N` | `daily`, `mon,wed`, `every:2` |
| `start` `until` | date bounds | `until: 2026-11-06` |
| `count` | occurrence count | `count: 3` |
| `tz` | timezone | `Asia/Jakarta` |

- "10 o'clock in 3 days" = `at: ["10:00"], count: 3` (or `until` three days).
- "1, 7, 12 o'clock" = `at: ["01:00","07:00","12:00"]` → three runs per day.
- "every day at 10" = `at: ["10:00"], days: daily`, without `until`/`count`.
- Every occurrence is scheduled as **its own run row**; there is no loop within one run.
- `occurrence_key = hash(automation_id, scheduled_at_utc)` → attention; a restart/takeover does not double it, and two colliding occurrences remain two different runs.
- Catch-up is **per occurrence** (§4.10): each has its own window; outside the window → `missed` for that occurrence, not for the whole automation.
- `quiet_hours` is respected per occurrence: `policy.quiet = skip` (default, recorded `skipped`) or `defer` (executed once at the end of the quiet hours). Nothing is ever sent in the middle of the quiet hours.
- Authoring limits: minimum interval 1 minute, maximum 24 occurrences/day per automation, and `occurrences_per_day ≤ budget_per_day`. A violation is **rejected while authoring**, not silently truncated.
- `until`/`count` → exits `expired` (terminal, §4.13).

### 4.3 Condition

- **Bounded and schema-typed**, not a programming language: typed filters (sender, room, keyword, time range), the operators `all`/`any`/`not`, counters (`count >= n`), and state markers (`never_notified`, `last_fired_at`). No loops, no user functions.
- **Two stages like the guard pattern:** a cheap deterministic layer first (including **MiniLM cosine** for meanings such as "HB"), then small LLMs only for the ambiguous cases.
- **Natural-language criteria opt-in per automation** for meaning that is hard to structure (e.g. a negative), with a daily token budget.

### 4.4 Steps (closed)

| Steps | Who runs it | What it may do |
|---|---|---|
| `collect` | **core** | Reads rooms/memory **within the privacy ceiling (ADR-0006) and the presence of the requester (ADR-0007)**; the result is written to the input file for the next step |
| `sandbox` | isolated container | Processing: a **named program** + runtime arguments. No DB, memory, or `send`. Network & timeout follow the `sandbox` host; effective timeout = `min(sandbox.timeout_seconds, turns.shutdown.drain_seconds)`, so a clean shutdown never cuts a step |
| `web_search` | tool | Web search within the owner's permission limit |
| `compose` | soul (LLM) | Builds the sentence in the soul's voice, guarded |
| `send` | **tool gateway** `send` | One implementation for every path: `send(path=..., text=..., chat=...)` — an empty `chat` means the active chat. This step calls it through `execute`; the target is still validated against the manifest, and the membership fence of the requester (ADR-0007 §4.5) applies exactly as it does from a turn |
| `write` | tool | Writes in the workspace under an allowed root |
| `remember` | tool | Writes memory with `via=automation` (trust T2, see 4.6) |
| `queue_reflection` | worker | Inserts into the reflection queue (ADR-0010) |
| `recipe` | `run_recipe` | Executes a pre-reviewed procedure |

**Sandbox as a step requirement:** it executes only a **named program** (e.g. `scripts/rekap.py`) with runtime arguments — never a command created when due. Input is prepared by `collect` (core), so containers only process. The script itself is authored and approved **in turn** (see 4.8), so the review happens once, in front of a human.

### 4.5 Owner authority (checked twice)

| Steps | Capabilities the owner must have |
|---|---|
| `collect` | the read tool/`chat_history` **+ the presence of the requester in that room** (ADR-0007 §4.5) |
| `sandbox` | the `sandbox` tool |
| `web_search` | the `web_search` tool |
| `write` | the `write` tool + a root allowed for its role |
| `send` | the `send` tool + the target present in the manifest; if `to:` is outside the **requester's room** → reject (ADR-0007 §4.5) |
| `remember` | the `memory` tool; a `global` scope additionally requires `memory_admin` |
| `recipe` | the item listed in the recipe |

- **At creation:** if a step exceeds those rights → **rejected**, not stored half-way.
- **Source of a proposal:** an automation may be born from the efficiency-ladder proposal in ADR-0020 §4.9 — the LLM judges repeated episode evidence and then **proposes** it; there is no automatic conversion and no hardcoded signature list. A proposal still requires a valid manifest and its own approval on this path.
- **When due:** the owner's role is recomputed; if not entitled → the run is marked `denied`, and no step was executed.
- **On the first `denied`:** the automation is `paused` (`capability revoked`) and the owner is notified **once**; it does not fail silently every day.
- `owner_roles_at_creation` is stored for diagnosis only, **not** as a permission source (except through the `/approve` path, ADR-0007 §4.2).
- **Owner removed** → their automations are `paused`.
- `owner=system` (engine internal work) must be marked explicitly and needs `memory_admin`; it is not the default path.

### 4.6 Memory writing by automation

- Always `via=automation` → trust **T2** (`soul_inference`), never T0.
- It cannot write `kind=rule` (ADR-0008 §4.3).
- It remains subject to the owner's **quota** (ADR-0008 §4.4 A/E); automation gets no free path.
- A `collect` result does not automatically become a memory; it must be an explicit `remember` step.

### 4.7 Execution

- It runs on the **existing worker** (ADR-0003) under a **lease** (ADR-0012): it claims due rows with `SKIP LOCKED`, the idempotency key is `(automation_id, fire_at)`, and the **outbound ledger** (ADR-0013 §6, `scope_key = automation:<run_id>`) makes delivery *effectively once*.
- **No second send path:** the `send` step calls the same tool gateway `send` (through `execute`), so no parallel implementation/fence could deviate. A deployment without a gateway → this step returns a clear `error:`, not silence.
- The execution path is **light**: not a full turn (4–6 calls), but the steps above, using the same compose/guard/polish utilities so the voice stays one.
- It does **not insert** into the running turn in the same room (turn lock).
- **Time:** `due_at` is stored in UTC + the source timezone; the catch-up window (`catchup_minutes`, e.g. 15) → outside it the run is marked `missed` instead of being sent hours late; `quiet_hours`; `budget_per_day`.
- **Failure:** a failing step stops the pipeline (no half-baked delivery), the run is recorded `failed`, and `on_error` determines the notice to the owner.
- **Execution limits:** `run_timeout_seconds` (default 120; hard limit 300) wraps the whole run, and `max_steps` (default 10) bounds the **`steps` entries in the manifest** — not conversation turns (ADR-0013), not LLM rounds within one step, and not the number of `send` targets. A violation is denied while authoring, not truncated while running. `condition` does not count as a step.
- **Kill switch:** global (`automations.kill_switch`) and per automation (`state=stopped` for permanent, `paused` for temporary) — `disabled` is not an automation state (§4.13).
- All runs are recorded in **`automation_runs`** (event, state decision, steps, result, time) in order, so they can be replayed and audited.

### 4.8 Authoring through turn + approval

1. The user states a wish in natural language (or through the prototype intent "remind/watch").
2. The soul compiles `event`/`condition`/`steps`; if needed, it writes the script into the workspace.
3. The content is displayed and approval is requested.
4. Once approved, the automation is active; the scripts become artifacts named in the manifest.

### 4.9 The explicit rejection path

If a request cannot be expressed honestly (the event source or action does not exist, or the meaning cannot be structured), the soul **refuses explicitly** and records it as a capability gap — it does **not** map a good intention onto weak keywords that will misfire later. Closing the gap means adding a new adapter/action as reviewed core code.

### 4.10 Resilience against sudden death & apology for a missed job

An automation is a **promise**, and the promise must not be lost because the process died. Its schedule state lives in Postgres, and every loss leaves a trace.

**Run state (durable, in `automation_runs`):** `scheduled → claimed → running → sent | failed | missed | interrupted`, with `fire_at`, `claimed_at`, `attempts`, and `apology_sent_at`.

| Events | Behavior |
|---|---|
| Bot alive and normal | Claims the due row with `SKIP LOCKED`, runs it, marks it `sent` |
| Bot died, alive again **within** `catchup_minutes` | The missed schedule runs **late**, with a short note that it is late |
| Bot down longer than the window | The run is marked **`missed`** — **never** fired stale (a reminder about something 3 days old is worse than none) |
| The process dies in the middle of a run | The `stale` claim is released by the reaper (pattern ADR-0011) → restarted; the send is still protected by the idempotency key + outbound ledger, so it is not delivered twice |
| Clean shutdown | An in-flight run is marked `interrupted` together with the shutdown/resume notice (ADR-0013), so the next boot knows whether to resume or apologize |
| Standby instance | Never fires (lease, ADR-0012). On takeover, the new primary runs a catch-up scan, so a replica replacing a dead bot stays on schedule |

**Apology for a missed job:**

- Every `missed` run produces **one** apology message to **its owner** (the target from the manifest), on the same path: `compose` then `send`, so the wording stays in the soul's voice.
- **One message per dead span, not one message per task.** If the bot was down 3 days and 4 reminders were missed, one brief message is sent: the duration, the list of missed tasks, then the offer to act.
- **Once only:** `apology_sent_at` on each run; never repeated.
- **Effectively once** like any other delivery (idempotency key + outbound ledger).
- **Quiet hours respected:** the apology waits for the waking window; it is not sent at 3 a.m.
- **Content stays bounded:** only the owner's own tasks and when they were missed are mentioned. The contents of other rooms are not involved (ADR-0006).
- **Continue after apologizing:** (a) start now, (b) reschedule, (c) disable the automation. The answer is processed as a regular turn through the same approval path.
- **`max_stale_days` limit:** beyond it the automation is `paused` with reason `stale` and the owner is told once; a schedule that has already become irrelevant is not allowed to fire.

### 4.11 Option B follows

If the condition logic is truly exotic, the path is an isolated hook: a separate process, JSON in/out, and an **engine** that executes validated manifest actions. The contract does not exist yet; until then the limit is the bounded condition language in 4.3.

### 4.12 Requests demanding autonomy: propose, do not promise

A sentence like "Please monitor this and let me know when the job is done" is not a request with one answer; it asks for **repeated action outside the turn**. The turn must not pretend to have it.

**Three legitimate responses, no fourth:**

| State | The soul's answer |
|---|---|
| Expressable as automation | "Can. Here's the plan: <event/condition/step/target/budget>. Agreed?" + an approval id |
| Possible, but the requester is not authorised | explain the requirement (owner/admin) + offer the draft; **do not** create it |
| Inexpressible | explicit refusal (4.9): which part does not exist, and what **can** be done right now |

- **No promises**: "I'll check it later", "I'll keep watch", "I'll let you know" are not allowed without an automation that is stored and approved.
- **Silent creation is forbidden**: no automation without approval, even when the requester is an administrator.
- **This question is valid and mandatory.** It is the exception to the "do not ask" rule: a question about **autonomy** (whether something can be automated) is mandatory when the request demands repeated behavior. Questions about preferences/study remain banned (ADR-0020 §4.10).
- **Deterministic classification first**: the repeat markers "when", "every hour", "check", "report when done", "monitor" trigger this path; the LLM only judges the remaining ambiguous cases.
- **A condition must have a source**: the evaluated state must be readable deterministically (`collect`/table/tool) or NL-bound (4.3). If "job done" means an external service that is not reachable (`fetch` rejects internal SSRF/host), the answer is a refusal + an alternative, not blind polling.
- **One turn, one decision:** the manifest proposal is shown in human language (event, condition, steps, targets, budgets, quiet hours), approval happens in the same turn (4.8), and after approval the soul says **once** that the automation is active. The offer is not repeated every turn.
- **Send target**: the room where the request came from (or the requester's DM). Sending to another room follows ADR-0007 §4.5 (`cross_send` + mandatory attribution).

**"Precisely" is not a schedule.** Words like "calculate", "sort", "sometimes" have no operational meaning. The soul **asks one option question**, instead of guessing:

1. **Interval** — 30 minutes / 30 minutes per hour / 3× a day;
2. **Active window**;
3. **End limit** — `count`/`until`, or "until you say stop";
4. **What counts as one occurrence** — e.g. "a new attachment in group A since the last run".

If the requester only chooses the option ("up"), the soul **mentions** the default it chose ("Every 30 minutes, 8:00-22:00, until you say stop") and still asks for confirmation, so a default is never applied secretly. A manifest without `at`/`every` is explicitly rejected while authoring.

**The clarifying question is written by the soul, not by the catalog.** The engine only prepares a **slot spec** in data form: the list of supported intervals, the active-window bounds, how it ends (`count`/`until`/stop), the occurrence definition, and the relevant facts (bot present/absent in the source room, requester membership, budget). The LLM composes the question itself, in the chosen order, explains what each option means, names the consequences ("Every 15 minutes is more responsive, but also more wasteful"), and confirms when the user answers while still on the run.

- only options present in the slot spec may be offered (the LLM does not invent intervals);
- user answers are mapped back to the slot spec and validated before the manifest is created;
- at most **two clarification rounds**; after that the soul offers an explicit default or refuses — there is no third round going round and round;
- no question string in the catalog or in code: the text comes from the LLM, per ADR-0007 §4.6;
- the explanation uses facts from the engine, not essays (e.g. "I am not in that group" is stated plainly).

**A room as the source.** "Check group A" means reading the room, so the condition is checked twice: the requester must be a member of group A (`rooms_of`) both **while authoring** and **when due**, and the bot must be in the group (gateway). If the requester's membership is missing → `paused(authority)` + one notice, instead of reading quietly. This is not a `cross_read`: the read is a task in the member's name, and the result goes back to them; other requesters stay closed.

### 4.13 Life cycle: state, transition, and retry

**A single state list (owned by this ADR):**

| State | Meaning | Schedulable | Moved by |
|---|---|---|---|
| `draft` | created in a turn, unapproved | No | engine on authoring |
| `active` | waiting to run / scheduled | yes | approval / owner |
| `paused` | temporary pause; `reason`: `user`, `authority`, `stale`, `error_threshold`, `consent_revoked` | No | owner / engine |
| `stopped` | permanently stopped by the owner | No | owner |
| `expired` | past `ends_at`, or one-shot complete | No | engine |

- `denied` is **not** an automation state; it is a run result (`automation_runs.status`). `disabled` is only the global kill switch (configured on the host), not a per-automation state.
- Legal transitions: `draft→active` (approval), `active→paused`, `paused→active` (resume; authority is checked **again**, not from a snapshot), `active|paused→stopped`, `active|paused→expired`.
- `stopped` and `expired` are **terminal**: restarting means creating a new automation with a new manifest + a new approval, not reversing the state.
- Every transition writes one `automation_state_log` row (`from`, `to`, `reason`, `actor`, time); no state changes without a trace.

**Run status (canonical):** `queued | running | succeeded | failed | skipped | missed | denied | interrupted | cancelled`.

- `failed` = permanent failure after retries are exhausted; `cancelled` = stopped by the owner or consent revoked; `skipped` = the condition was not met, or the occurrence fell in quiet hours with `policy.quiet=skip`; `missed` = the catch-up window was missed (not run stale); `denied` = invalid authority when due.

**Retry, two classes:**

| Class | Sample | Treatment |
|---|---|---|
| `transient` | timeout, 5xx, serialization failure, missing claim release | retry: `max_attempts` (default 3), exponential backoff + jitter, respect `Retry-After`; exhausted `failed` + one notice to the owner |
| `permanent` | 4xx, guard deny, permission denied, invalid manifest, consent revoked | **Never retried**; `denied`/`failed` according to the class |

- `missed` is not retried as a new run (the data is old); only the catch-up window in §4.10 applies.
- A retry must not double-send: `scope_key=automation:<run_id>` + `step_index` in the ledger (ADR-0013 §6).
- `budget_per_day` counts **runs**, not attempts; `max_attempts` counts executions. This keeps a retry storm from eating the send budget.

**Dynamic conditions, still closed.** Requests such as "if it is not done by 5 p.m.", "if three people have not answered", "if X and not Y" are expressed as an **evaluator composition** (§4.3: `all`/`any`/`not`, time windows, counters, state markers, comparisons). Natural language is classified into that form; if it cannot be mapped → `condition.unexpressible`, answered with an explicit refusal (§4.9) + an honest approximation offer ("every hour at 17", not "when finished"). No `eval`, free SQL, or user regex ever runs; changing the behavior means a new manifest (revision), not a quiet edit.

## 5. Configuration

```yaml
automations:
 enabled: true
 kill_switch: false
 default_budget_per_day: 4
 run_timeout_seconds: 120 # default per run; hard limit 300
 max_steps: 10 # step limit per manifest
 min_interval_seconds: 60 # hard limit; not a per-automation setting
 max_occurrences_per_day: 24 # hard limit per automation
 default_quiet_hours: "22:00-07:00"
 default_quiet_policy: skip # skip | defer (once at the end of quiet hours)
 max_active_per_person: 10
 sandbox:
 programs_root: workspace # the program must live inside the workspace
 # network & timeout follow the `sandbox` host; effective timeout =
 # min(sandbox.timeout_seconds, turns.shutdown.drain_seconds) so shutdown does not cut a run short
 condition:
 nl_criteria: true # natural-language criteria, opt-in per automation
 nl_budget_tokens_per_day: 20000
 retry:
 max_attempts: 3 # transient class only
 backoff_seconds: 30
 max_backoff_seconds: 900
 jitter: true
 respect_retry_after: true
 resilience:
 catchup_minutes: 15 # past this -> missed, not sent stale
 max_stale_days: 7 # past this -> paused (stale reason) + one notice
 stale_claim_seconds: 300 # the reaper releases a dead claim
 missed_apology: true # apologize to the owner for a missed job
 apology_digest: true # one message per downtime window, not per job
 apology_respect_quiet_hours: true
```

## 6. Metrics and gates

| Metric | Start target |
|---|---|
| A run `denied` because authority was revoked | Visible, and the automation is `paused` immediately |
| Double delivery per `(automation, fire_at)` | 0 (tested) |
| A run `missed` (outside catch-up) | Visible; the window is material to the metric |
| Cost per automation per day | Under budget; the pause triggers |
| A sandbox step with a program outside the workspace | 0 (tested) |
| An automation without an owner (`owner=system`) | 0 unless marked + `memory_admin` (tested) |
| Automation-result memory trusted as T0 | 0 (tested) |
| A run `missed` that is never apologized for | 0 (tested) |
| Repeated apologies for the same run | 0 (`apology_sent_at`) |
| A `stale` claim left hanging | 0 after a reaper run |

## 7. Acceptance criteria

- [ ] Automation is stored as a row with `event`, `condition`, `steps`, `administrator`, `policy`, `state`.
- [ ] Every step is checked against the owner's authority **at creation**; any step that exceeds it is rejected.
- [ ] Authority is checked **again** when due; `denied` → `paused` + one notice to the owner.
- [ ] `owner_roles_at_creation` is never used as a permission source.
- [ ] `sandbox` only runs a named program inside the workspace, with runtime arguments; no free shell.
- [ ] `collect` is the only room/memory reader, and it enforces the ceiling (ADR-0006) + presence (ADR-0007).
- [ ] `send` only goes to a target in the manifest; the engine executes it.
- [ ] Automation memory is always `via=automation` and trusted T2; never `kind=rule`.
- [ ] Execution uses a worker + lease + idempotency key + outbound ledger → effectively once.
- [ ] A run is recorded in `automation_runs` and can be replayed; the global and per-automation kill switches work.
- [ ] `quiet_hours`, `budget_per_day`, and the catch-up window are enforced; past the window → `missed`.
- [ ] Unexpressible requests are explicitly refused and recorded as capability gaps.
- [ ] The schedule and the automation definition survive process death; the first tick after boot runs a catch-up scan.
- [ ] Inside the window → the run happens late with a note; outside it → `missed`, not fired stale.
- [ ] A `stale` claim from a dead process is released by the reaper and restarted without double sending.
- [ ] Every `missed` run produces **one** apology to its owner, through `compose` + `send`, and `apology_sent_at` prevents repeats.
- [ ] Several runs missed in one dead span → one digest message, not a flood.
- [ ] The apology respects quiet hours and does not contain the contents of other rooms.
- [ ] The apology offers continuation: start now / reschedule / disable the automation.
- [ ] Past `max_stale_days` → `paused` (reason `stale`) + one notice.
- [ ] A clean shutdown marks an in-flight run `interrupted`; the next boot resumes or apologizes.
- [ ] A request to "watch/notify for a period" → the answer contains an automation proposal or an explicit refusal; no monitoring promise without a stored automation.
- [ ] No automation row is stored without approval (tested), including when the requester is an administrator.
- [ ] A condition without a source (deterministic or NL-bounded) → an explicit refusal + alternatives that can be done now.
- [ ] A requester without authority → a draft proposal + the authority requirement; no automation created.
- [ ] The proposal is shown in human language (event, condition, steps, target, budget, quiet hours) and not repeated once it was decided.
- [ ] `stopped`/`expired` are terminal; resuming `paused` re-checks authority; `denied` is only a run status, not an automation state.
- [ ] Every state transition writes `automation_state_log` (`from`/`to`/`reason`/`actor`); no change without a trace.
- [ ] Retry exists only for the `transient` class; `permanent` is never retried; an exhausted attempt is `failed` + one notice to the owner.
- [ ] Retry does not double-send (`scope_key=automation:<run_id>` + `step_index`).
- [ ] Dynamic conditions are composed from the closed evaluator; what cannot be mapped → explicit refusal + honest approximation.
- [ ] `budget_per_day` counts runs and `max_attempts` counts executions; a retry storm does not eat the send budget.
- [ ] `at` takes a time list and produces a separate occurrence per time (3 times × 3 days = 9 runs), not one loop run.
- [ ] `occurrence_key` prevents doubling across a restart/takeover; two different occurrences remain two runs.
- [ ] `budget_per_day` < number of occurrences/day → rejected while authoring, not silently truncated.
- [ ] Catch-up is counted per occurrence; one `missed` occurrence does not cancel another occurrence on the same day.
- [ ] `policy.quiet=skip` records `skipped`; `defer` executes once at the end of the quiet hours.
- [ ] `count`/`until` → `expired` (terminal), not running.
- [ ] "calculate/routine" without `at`/`every` → the soul asks with concrete options (interval, active windows, limit, occurrence definition); a manifest without them is rejected while authoring.
- [ ] Answer "whatever" → the soul names the default it chose and asks for confirmation; defaults never apply secretly.
- [ ] The scheduling question is written by the LLM (context changes, no fixed strings in catalog/code); the engine only supplies the slot spec.
- [ ] The LLM cannot offer intervals/windows outside the slot spec; foreign options are rejected before the manifest is created.
- [ ] Ambiguous answers → at most two clarification rounds, then an explicit default or a refusal.
- [ ] Invalid answer/outside the spec → no manifest is created; the soul explains why, using engine facts.
- [ ] A room as the source: the requester must be a member at authoring **and** when due; membership lost → `paused(authority)` + one notice, not a silent read.
- [ ] The bot is not in the source room → explicit refusal + offer, not empty polls that always return "no files".

## 8. Risks and mitigations

| Risks | Mitigation |
|---|---|
| Automation used to bypass privacy | There is no storage access; `collect` is the only reader and it enforces the ceiling + presence |
| A script becoming an arbitrary code path | Only named programs in the workspace; runtime arguments only; authored & approved in turn |
| Double delivery on retry/takeover | Idempotency key + outbound ledger (ADR-0013 §6) + lease (ADR-0012) |
| Cost loop (every group message triggers an LLM) | Deterministic layer + MiniLM first; the LLM only handles ambiguity; budget per automation per day |
| Authority revoked but the automation still running | Recheck when due + automatic `paused` |
| Automation writing its own memory without restraint | `via=automation`, trust T2, subject to the owner's quota, `remember` must be explicit |
| Mis-expressed rules | Explicit refusal path; no stealth mapping onto weak conditions |
| A promise not kept (a reminder that never came) | Catch-up window + `missed` status + apology to the owner + `automation_runs` |
| The bot dead for long, tasks piling up | One apology digest per dead span + the offer to reschedule/disable; past `max_stale_days` → `paused` |
| A run stuck in `claimed` after death | The reaper releases the `stale` claim (`stale_claim_seconds`) |
| The apology itself becoming spam | Digest + `apology_sent_at` |

## 9. Interaction with other ADRs

- **ADR-0002/0003** — automation uses the same queue and worker; a two-stage condition follows the cheap-gate-then-expensive-model pattern.
- **ADR-0005** — automation does not change retention; the memory it writes follows the normal rules.
- **ADR-0006** — `collect` enforces the privacy ceiling; a room summary → DM only because the owner is in that room (ADR-0007 §4.5).
- **ADR-0007** — owner authority = the same permission classes; automation never raises permissions, and `send` is only allowed to the requester's room (membership, not a flag).
- **ADR-0008** — `via=automation` → T2; must not write `kind=rule`; the volume quota remains valid.
- **ADR-0010** — `queue_reflection` is how automation hands off batch work; `disputed` stays there — **accepted** (`ADR-0010-reflection-disputed.md`); automation only queues, it does not write the result and does not resolve a dispute.
- **ADR-0011/0012/0013** — claim `SKIP LOCKED`, idempotency, outbound ledger (`scope_key = automation:<run_id>`), lease/fencing; the reaper releases dead claims.
- **ADR-0013** — a clean shutdown marks a run `interrupted`; the catch-up scan follows the same resume/backlog pattern; automation execution does not insert an active turn in the same room.
- **ADR-0015** — the automation status (active/inactive) appears in the capability index, so the soul does not promise it wrongly; the rarely used steps `sandbox`/`recipe` are fetched with `info` when needed.

## 10. Code references

- `eidesoul/engine/tools/sandbox.py:33-80` — container isolation flags, filtered env, truncated output.
- `eidesoul/engine/tools/__init__.py:180-235` — the single permission door `execute` + permission check.
- `eidesoul/engine/approvals.py` + `eidesoul/engine/commands/approve.py` — approval and authority recheck.
- `eidesoul/engine/stores/recipes.py` — `run_recipe` as the `recipe` step.
- `eidesoul/engine/ambient.py:1-4,103-160` — the autonomy limit (never greets first).
- `eidesoul/engine/memory/mem0.py:308-392` — memory-write metadata (`source`, `owner_id`, `subject_id`).
- `eidesoul/engine/agent.py:440-460,600-640,900-918` — turn lane, memory block, capture gate.
- `eidesoul/engine/guard/policy.py:198-215` — deterministic policy patterns before the small models.

---

*This ADR lives at `docs/v0.1.0/adr/engine/0014-automation.md`.*
