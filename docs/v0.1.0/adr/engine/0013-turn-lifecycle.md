# ADR-0013: Turn Lifecycle, Resume, Backlog & Notice

- **Status:** accepted
- **Implementation:** partial — present: `turns`/`turn_steps`/`outbound_messages` (revision 3), the checkpointed state machine, per-chunk ledger + resume, notices, `/stop` cancellation, `tests/test_turn_lifecycle.py`; remaining: backlog pacing/fairness (`per_message_then_consolidate`, `max_resume_per_chat`, per-minute limit)
- **Date:** 2026-11-01
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Context:** follow-up to ADR-0011 (crash safety) and ADR-0012 (lease safety); the question: if the bot dies in the middle of a turn, is it continued?
- **Supersedes:** —
- **Related:** ADR-0011 (durability), ADR-0012 (fencing), ADR-0003 (worker), ADR-0004 (labeling)
- **Location note:** ADR lives in `docs/v0.1.0/adr/engine/`; the Indonesian copy is `docs/v0.1.0/adr/engine/id/0013-turn-lifecycle.md`.
- **Amendments:** 2026-11-02 — normalization of the skeleton (ADR-0009 §4.4): added Options considered, Decision (summary), and Interaction with other ADRs; numbering 3-14 shifted to 5-17. 2026-11-03 — audit: the range `supervisor.py` was corrected to `36-129`; the path `eidesoul/engine/gateways` was corrected to `eidesoul/gateways/`; the notice key was standardized to `resume_notice.after_seconds`; the stale line about ADR-0009 was aligned. 2026-11-03 — audit: the turn status `pending` (not in the enum) was replaced by `received`/`running`/`sending`/`interrupted`; the reaper claim uses `clock_timestamp()`; the ledger uses `scope_key`. 2026-11-03 — `send`/`send_to` are no longer called "tools"; both are engine actions (gateway), and `send_to` was once considered a synonym — correction 2026-11-03: `send` and `send_to` are **gateway tools** (`gateways/whatsapp/tools/`), not engine steps; what needs distinguishing is the automation step of ADR-0014. 2026-11-03 — ADR-0016 §4.1: the canonical table name is `turn_steps` (ADR-0009/0016), so the former `turn_actions` block is renamed and gains `step`/`started_at`/`finished_at` for the step history; the action columns stay.

---

## 1. Context

- `handle()` is an in-process coroutine with no checkpoint (`agent.py:413-926`). Process death = the turn is lost.
- Messages are usually already stored in `messages`, but nothing reprocesses them.
- A reply already sent cannot be withdrawn; a partial reply leaves a remainder that was never sent.
- The typing indicator disappears on its own; `answered`/`quiet`/`backlog` live in memory (`agent.py:145-147`).
- The `spawn()` memory capture is lost during a crash (fixed in ADR-0003/0011).

The agreed principle:

> **Every incoming message is a task. It is finished only when its reply has been sent (`delivered`). What is finished is not repeated; what is not finished is continued.**

## 2. Decision drivers

1. **No missing reply** for a message already received.
2. **No double reply** on resume.
3. **Does not repeat side effects** that have already happened.
4. **Honest UX**: the user is told when the bot will stop or has just stopped.
5. **Does not flood** the chat after downtime.
6. **A clear boundary** between pure chat and side-effecting action.

## 3. Options considered

No other alternative was recorded when the decision was made; the option taken, along with its consequences, is discussed directly in Definitions, Schema, and State machine.

## 4. Decision

- Every incoming message is its own task, with a `turns` row carrying `message_id` as the idempotency key; a turn is finished only at `delivered` (see Definitions, Schema).
- Resume starts from the checkpoint `turns.step`; a step that already has a stored output is not repeated (see State machine, Resume).
- An idempotent tool may be run again; a non-idempotent action that has already been `executed` makes the turn `interrupted`, not repeated (see Resume).
- Delivery per chunk: resume sends only chunks not yet `sent`; status `unknown` follows the `partial_delivery` policy (see Resume).
- Shutdown runs `running → draining → noticed → stopped`, with one notice per chat per `shutdown_epoch` (see Shutdown, Notice).
- A notice with `kind='notice:*'` is not an answer and never marks a turn `delivered` (see Notice).
- Backlog is processed with pacing and fairness according to `backlog_policy` (see Backlog, Configuration).

## 5. Definitions

| Term | Meaning |
|---|---|
| **Received** | Message already persisted (`messages` + `turns(status='received')`) |
| **Done** | `turns.status='delivered'` (all chunks sent) |
| **Checkpoint** | The last stored `turns.step`; resume starts from here |
| **Notice** | Bot message `kind='notice:*'`; **not** an answer, does not finish the turn |
| **Epoch** | `shutdown_epoch`/`boot_epoch` for notice idempotence |

## 6. Schema

```sql
turns(
 id text primary key, -- incoming message_id (idempotency key)
 thread_id text not null,
 person_id text not null,
 status text not null, -- received|running|sending|delivered|interrupted|expired
 step text not null, -- received|decided|drafted|reviewed|sending|delivered
 attempt int not null default 0,
 lease_owner text not null default '',
 lease_until double precision not null default 0,
 generation bigint not null default 0,
 decision_json jsonb,
 draft_text text,
 reviewed_text text,
 planned_chunks int not null default 0,
 delivered_chunks int not null default 0,
 shutdown_epoch bigint not null default 0,
 boot_epoch bigint not null default 0,
 last_error text not null default '',
 created_at double precision not null,
 updated_at double precision not null)

turn_steps(
 turn_id text not null,
 step_index int not null,
 step text not null, -- received|decided|drafted|reviewed|sending|delivered
 tool text not null default '',
 args_hash text not null default '',
 result text,
 status text not null, -- executed|failed
 started_at double precision not null default 0,
 finished_at double precision not null default 0,
 primary key (turn_id, step_index))

outbound_messages(
 scope_key text not null, -- turn:<turn_id> | automation:<run_id>
 chunk_index int not null,
 gateway text not null,
 thread_id text not null,
 provider_id text not null default '',
 status text not null, -- intent|sent|unknown
 generation bigint not null default 0,
 primary key (scope_key, chunk_index))
```

`messages` + `turns` + `memory_events` + `memories(pending)` + `cognition_jobs` are written in **one transaction** (ADR-0011).

## 7. State machine

| Step | After | Stored |
|---|---|---|
| `received` | Message received | — |
| `decided` | LLM decision | `decision_json` |
| `drafted` | `llm.run` + tool loop | `draft_text` |
| `reviewed` | `polish`/`regulate`/privacy review | `reviewed_text` |
| `sending` | Start sending bubbles | `delivered_chunks` per chunk |
| `delivered` | All chunks sent | **terminal** |
| `interrupted` | Cannot continue | reason + notice |
| `expired` | Too old to be resumed | reason |

### 7.1 Cancellation: `/stop` (task derivative)

`/stop` cancels **the task currently running in this room** — not killing the instance, not
silencing the room.

- **Scope:** `(thread_id, running task/episode)`; a turn in another room is untouched, and there is no cross-room `/stop`.
- **Effect:** the `handle()` task is cancelled, typing stops, a turn that is not yet `delivered` is marked
`interrupted` with reason `user_stop`, and **one** notice from the catalog. A delivery that has already been sent
is not withdrawn and stays recorded in the ledger.
- It **does not** change `mode`/lease (that is `/freeze`), does not install a quiet period (that mechanism is **deleted**,
see the end of §7.1), and does not delete the backlog.
- An **episode** that is stopped closes as `interrupted`; `distill` may still run because its learning
material already exists.
- **Authority:** the asker in that room may stop their own task; an administrator may
stop any task **in the same room**. There is no cross-room path.
- **Idempotent, without the LLM:** if no task is running, answer briefly "no task is running".
- **Can arrive while a turn is running:** `/stop` is a command (outside `turn_lock`), so it is actually read
while the turn is running — that is its purpose.
- **Fallback:** a memory capture already `spawn`ed is not cancelled (it is durable and invisible to
users); only the work that produces a reply is stopped.

**Old behavior deleted, not moved.** `/stop [minutes]`, which silenced the reply in a room, turned out to be
useless: the message is still written, only the reply is held back, and nobody needs it. It is
deleted along with all of its mechanisms:

- `eidesoul/engine/commands/stop.py` (the command and the alias `diam`);
- `Agent.quiet` and the branch `agent.py:439-440` ("message recorded without a reply");
- `reply.quiet_seconds` (config, `config.yaml`, admin template);
- the i18n key `command.stop.*`;
- the `stop` entry in the registry inventory (`tests/test_registry.py:52`);
- the prompt rule `prompts.py:234` about "quiet period" — replaced by an honest rule: silence is an
action, and the only valid silence is `/stop` cancelling the task.

The name `stop` was freed for task cancellation; there is no alias `diam`.

## 8. Resume

### 8.1 When

Once the lease is acquired (ADR-0012), run the recovery pass once:

```sql
SELECT... FROM turns
WHERE status IN ('received','running','sending') AND lease_until < clock_timestamp()
ORDER BY created_at
FOR UPDATE SKIP LOCKED
LIMIT:n
```

Eligibility for resume:

- `age < max_age_seconds` (default 900),
- `attempt < max_attempts` (default 2),
- no non-idempotent action already `executed` (see `turn_steps`),
- has not passed `stale_after_seconds` for the `consolidate` policy.

### 8.2 From where

- Start from `turns.step`; a step that already has a stored output is not repeated.
- **Idempotent tools** (`read`, `web_search`, `chat_history`, `memory` read): may be run again.
- **Side-effecting tools** (gateway `send`/`send_to`, plus `shell`, `sandbox`, `settings`): not run again; their results are taken from `turn_steps` and put back into the LLM context.
- A non-idempotent action already `executed` and a turn not yet finished → `interrupted`, not repeated.

### 8.3 Delivery

- Before sending: `outbound_messages.status='intent'`.
- After sending: `sent` + `provider_id`.
- Resume sends **only chunks not yet `sent`**.
- `unknown` (a gap between sending and recording) follows `partial_delivery`:
  - `skip` (default): do not resend — avoid a duplicate; the risk is one lost bubble;
  - `resend`: send again — the risk is a duplicate.
- All chunks `sent` but the turn is still `sending` → recovery marks it `delivered` (sending nothing).

## 9. Shutdown

```
running → draining → noticed → stopped
```

1. **Draining** — stop intake; `engine_control.shutting_down=true`; a turn in flight is given `drain_seconds`.
2. **Noticed** — a chat with an unfinished turn receives **one notice per chat per `shutdown_epoch`**.
3. **Stopped** — workers stop, the lease is released, the process exits.

Limits: `grace_seconds > drain_seconds + notice_seconds`; `grace_seconds < stop_grace_period` of the orchestrator; validated at boot (fail-fast).

The race that is guarded against: an event already queued but not yet persisted → **persist first**, then try to finish it; if there is no time, send a notice and then mark the turn `interrupted` (resume material). Never drop.

## 10. Notice

| Type | When | Frequency |
|---|---|---|
| `notice:shutdown` | Before the neat exit | One per chat per `shutdown_epoch` |
| `notice:resumed` | Once up again, if downtime > `resume_notice.after_seconds` | One per chat per `boot_epoch` |
| `notice:interrupted` | The turn cannot continue | At most one per chat, then silence |

Rules:

- A notice is recorded as a bot message with `kind`, entering the catalog `t()`.
- A notice **does not** mark `delivered`; the turn status stays `received`/`running`/`sending`, or `interrupted`.
- If a notice fails: `notice_failed`, retried per `notice_retry`; if it still fails, it is reported in `/status`.
- Tone: "my connection dropped", not "I shut down".

## 11. Backlog

| Policy | Behaviour | When |
|---|---|---|
| `per_message` | Reply one by one in order | Short backlog |
| `per_message_then_consolidate` | One at a time up to the limit, then one combined reply | **Default** |
| `consolidate` | One reply for the whole backlog | Long backlog |
| `last_only` | Reply to the last message, the rest as context | Stale backlog |

- Limits: `max_resume_per_chat` (5) then continue with pacing; `max_resume_per_minute` (10) global.
- Fairness: round-robin between chats.
- Age: `< max_age_seconds` → full resume; `< stale_after_seconds` → `consolidate`; above that → `expired` (optionally one notice).
- Turns of category `action` are not merged; they are processed individually.

## 12. Configuration

```yaml
turns:
 resume: on
 max_attempts: 2
 max_age_seconds: 900
 stale_after_seconds: 21600
 partial_delivery: skip # skip | resend
 max_resume_per_chat: 5
 max_resume_per_minute: 10
 backlog_policy: per_message_then_consolidate
 shutdown:
 grace_seconds: 25 # total shutdown budget (drain 15 + notice 5 + slack 5)
 drain_seconds: 15 # time to finish an in-flight turn
 notice_seconds: 5 # time to send the notice
 notice_per_chat: true # one notice per chat per epoch
 notice_retry: { attempts: 2, backoff_seconds: 1 }
 resume_notice:
 after_seconds: 300 # send the "resumed" notice only if downtime > this
```

Key meaning:

- `grace_seconds` — the total shutdown budget; must be `< stop_grace_period` of the orchestrator.
- `drain_seconds` — the chance for a turn to finish before giving up; the smaller it is → the more turns become `interrupted`.
- `notice_seconds` — the budget for sending a notice; the smaller it is → the more `notice_failed`.
- `notice_per_chat` — avoid spam; one notice per chat per episode.
- `notice_retry` — retries for a notice within the window; exhausted → `notice_failed`.
- `resume_notice.after_seconds` — the downtime threshold before the bot announces "I am back".

## 13. Metrics and gates

| Metric | Target |
|---|---|
| Turn received but not finished | 0 after resume |
| Double reply due to replay | 0 |
| Non-idempotent side effect repeated | 0 |
| Notices sent per chat per epoch | ≤ 1 |
| Turn `expired` | measured, not forced to zero |
| Shutdown duration | < `grace_seconds` |

## 14. Acceptance criteria

- [ ] Every received message has a `turns` row; `delivered` only after all chunks have been sent.
- [ ] A finished turn is never reprocessed (idempotency `message_id`).
- [ ] Resume starts from the checkpoint; LLM output and tool results are not repeated.
- [ ] A non-idempotent action already `executed` → `interrupted`, not repeated.
- [ ] Delivery per chunk: only chunks not yet `sent` are sent on resume.
- [ ] Shutdown: one notice per chat per epoch; the turn is not marked `delivered`.
- [ ] Events already accepted during shutdown are persisted and given a notice.
- [ ] Backlog: pacing + fairness + the `per_message_then_consolidate` policy work.
- [ ] A notice never marks the turn `delivered`.
- [ ] `grace_seconds < stop_grace_period` is validated at boot.
- [ ] `/stop` cancels a running task in that room only; a turn in another room is unaffected.
- [ ] A turn that is cancelled becomes `interrupted(user_stop)` + one notice; a delivery that has already happened stays in the ledger and is not withdrawn.
- [ ] `/stop` does not change `mode`/lease and does not install a quiet period; `/freeze` remains instance-level.
- [ ] No trace of the quiet mechanism remains: `command.stop.*`, `Agent.quiet`, `reply.quiet_seconds`, and the registry entry `stop` are gone (test grep + registry inventory).
- [ ] `/stop` without a running task → a short answer, not an error; a cross-room `/stop` is rejected.

## 15. Risks and mitigations

| Risks | Mitigation |
|---|---|
| Replay triggers a double side effect | Tool classification + `turn_steps` + generation (ADR-0012) |
| A notice is taken as an answer | `kind='notice:*'`; it does not mark `delivered` |
| Notice spam | One per chat per epoch; `resume_notice.after_seconds` |
| Backlog floods | Limit per chat/minute; `consolidate`; humanized pacing |
| An old turn is resumed | `max_age` and `stale_after` |
| Crash during resume | `attempt` + `max_attempts`, then `interrupted` |

## 16. Interaction with other ADRs

- **ADR-0011 (durability)** — `messages` + `turns` + `memory_events` + `memories(pending)` + `cognition_jobs` are written in one transaction; turn durability rests on that rule.
- **ADR-0012 (fencing)** — the lease and generation protect resume from two workers; the columns `lease_owner`, `lease_until`, and `generation` on `turns` follow that fencing schema.
- **ADR-0003 (worker)** — the memory capture fix at crash time makes the turn state survive a restart.
- **ADR-0004 (labeling)** — recorded in the header `Related:`; no technical dependency is detailed in this ADR.
- **ADR-0009 (ADR home)** — already decided: this file is part of the migration into `docs/v0.1.0/adr/engine/0013-turn-lifecycle.md`; there is no change to the turn decisions.

## 17. Code references

- `eidesoul/engine/agent.py:413-926` — `handle` without a checkpoint.
- `eidesoul/engine/agent.py:145-147` — `answered`/`backlog`/`quiet` in memory.
- `eidesoul/engine/agent.py:881-905` — the chunk delivery loop and the `answered` marking.
- `eidesoul/engine/supervisor.py:36-129` — shutdown/primary lifecycle.
- `eidesoul/gateways/` — the intake that must be stopped while draining (outside the core; the core does not import it).
- `eidesoul/engine/tools/__init__.py:182-240` — the single execution door (the idempotence classification point).

---

*This ADR lives at `docs/v0.1.0/adr/engine/0013-turn-lifecycle.md`.*
