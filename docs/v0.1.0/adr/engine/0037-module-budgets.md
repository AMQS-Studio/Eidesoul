# ADR-0037: Module Budgets — Split Before the Cap, One Reason per Module

- **Status:** accepted
- **Date:** 2026-11-04
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Context:** extends ADR-0031 (engine structure, module caps); follows ADR-0001 (one memory port), ADR-0013 (turn lifecycle), ADR-0032 (the dispatch door)
- **Supersedes:** —
- **Related:** ADR-0007 (permissions), ADR-0016 (schema), ADR-0024 (tool input), ADR-0035/0036 (feature decisions, not structure)
- **Implementation:** implemented — `agent.py` is 787 lines over `turn/loop.py`/`intake.py`/`delivery.py`/`commands.py`/`steps.py`, `memory/store.py` is 645 over five helpers, and `jobs/automation.py` is replaced by manifest/store/worker (548/690/744). `LINE_CAPS` is empty and `selfcheck` passes.
- **Location note:** an engine decision (it owns module boundaries and the size gate), so it sits under `adr/engine/`. The Indonesian copy is `docs/v0.1.0/adr/engine/id/0037-module-budgets.md`.

---

## 1. Context

ADR-0031 set the rule: a module has one reason to change, stays at or below 800 lines, and a
stateful module above that needs a line in the ADR that introduces it (`0031 §4.1`). It also named
three concentrated modules with private caps and four permanent exceptions:

| Module | Lines | Cap | Headroom |
| --- | --- | --- | --- |
| `eidesoul/engine/agent.py` | 1,800 | 1,800 | **0** |
| `eidesoul/engine/memory/store.py` | 2,376 | 2,400 | 24 |
| `eidesoul/engine/jobs/automation.py` | 1,971 | 2,000 | 29 |

The four other modules above 800 (`config.py`, `stores/affect.py`, `jobs/cognition.py`,
`schema.py`) are explicit exceptions in ADR-0031 and are not in scope here.

The caps are enforced by `scripts/selfcheck.py`: `LINE_CAPS` holds the three private ceilings and
`OVER_CAP_ALLOWED` holds the four exceptions (`selfcheck.py:36-74`). Any feature edit to `agent.py`
today is a red gate; the next edit to `automation.py` or `memory/store.py` will be the same within
one or two commits. The modules are not over the cap because the cap is wrong; they are over the
cap because several reasons to change still share one file:

- `agent.py` holds the intake, the turn state machine, delivery, command dispatch, notices,
  checkpointing, reload, and the public door (`_run_turn` alone is `:1005-1640`).
- `automation.py` holds manifest validation and authority, the persistence store, and the worker
  pipeline (`:93-599`, `:600-1255`, `:1256-1971`).
- `memory/store.py` holds writes and candidates, reads, retention, privacy/rekey, reflection, and
  disputes behind the single port (`:314-1145`, `:1597-1762`, `:1334-1596`, `:1972-2363`).

ADR-0031 already proved that splitting in place, one green commit at a time, is the safe form. This
ADR applies that rule to the three remaining concentrated modules and lowers their caps instead of
raising them.

## 2. Decision drivers

1. **The cap is a ratchet.** A ceiling that only moves up stops being a signal. Each completed split
   lowers the cap in the same commit; no split may raise one.
2. **One reason to change per module.** Split by behavior, not by technical layer: a "database
   module" that holds every statement is still one file with many reasons to change.
3. **Behavior-preserving moves.** No public signature, turn step, checkpoint, or manifest changes.
   The suite is the proof, not a rewrite.
4. **The single port survives.** `MemoryStore` remains the one write port (ADR-0001); helpers are
   internal functions, never a second backend or a parallel read path.
5. **The turn stays readable.** The state machine may move, but its phases and checkpoint names stay
   identical; a resumed turn must take the same path.
6. **No new top level.** New modules live under `turn/`, `jobs/`, and `memory/`; the top-level cap
   (16) is untouched.

## 3. Options considered

**A. Raise the three caps.** Rejected: it removes the only pressure that keeps the modules from
growing, and the next edit re-opens the question with less headroom.

**B. Split by technical layer (all SQL, all delivery, all validation).** Rejected: a layer split
creates a module with many unrelated callers and no single reason to change; it moves the problem
instead of solving it.

**C. Split by the existing seams and phases (chosen).** Automation already has three sections
separated by class boundaries; the turn already has phases; the memory port already delegates SQL to
`queries.py`. Each extraction is mechanical and independently green.

**D. Rewrite the three modules.** Rejected: a rewrite hides behavior changes inside a move and makes
the checkpoint/resume and single-port invariants hard to prove.

## 4. Decision

### 4.1 The rule

- Split before adding a feature that would breach the cap.
- One commit per extracted module. The public class stays a facade; callers do not change their
  imports beyond the documented façade.
- No behavior change: same step names, same checkpoints, same tool/command signatures, same schema.
- Lower the cap in the same commit as the split. A module's cap may never rise again.
- A split that cannot reach 800 lines is not silently accepted; the ADR is amended with the named
  exception and the reason, exactly as ADR-0031 requires.
- New modules are ordinary modules: one reason to change, at or below 800 lines, no new top-level
  file, no new store/port.

### 4.2 Automation: three modules at the existing class boundaries

`jobs/automation.py` is removed and replaced by:

| New module | Content | Target |
| --- | --- | --- |
| `jobs/automation_manifest.py` | `StepError`/`StepSkipped`, manifest and condition validation, `describe`, `evaluate_condition`, authority helpers | ≤800 |
| `jobs/automation_store.py` | `AutomationStore`: persistence, scheduling, state transitions, runs, budgets | ≤800 |
| `jobs/automation_worker.py` | `AutomationWorker`: loop, pipeline, steps, delivery, apology, notification | ≤800 |

`AutomationStore` and `AutomationWorker` keep their names and public methods. Imports in
`jobs/cognition.py`, `jobs/supervisor.py`, commands, and tests move in the same commit. The
`LINE_CAPS` entry for `jobs/automation.py` is deleted; the three new modules are covered by the
ordinary 800-line gate.

### 4.3 Agent: the turn becomes a facade over phases

`agent.py` keeps the public handle, the dispatch door, identity, body/address lookup, tool and
command contexts, and lifecycle. The turn phases move:

| New module | Content | Target |
| --- | --- | --- |
| `turn/loop.py` | `TurnRunner`: claim/resume, decision phase, draft/review, checkpointing, `_keep_typing` | ≤800 |
| `turn/intake.py` | `_identify`, privacy-pending check, forget intent, `_handle_message` intake | ≤800 |
| `turn/delivery.py` | `deliver_*`, `send_*`, notices, typing; `reply.py` stays text shaping | ≤800 |
| `turn/commands.py` | `_command`, `command_context`, command dispatch preparation | ≤800 |
| `turn/steps.py` | step recorder/lookup | ≤800 |

`TurnRunner` receives a narrow host (the `Agent` satisfies it) and never imports `agent.py` back;
the dependency direction is `agent.py → turn/loop.py` and `turn/loop.py → turn/*`, with no cycle.
`Agent._run_turn` becomes a short delegation; `Agent.handle` and `Agent.dispatch` keep their
signatures. `turn/loop.py` may use `turn/assembly.py`, `turn/prompts.py`, and `turn/reply.py` as
today.

The target is `agent.py` at or below 800 lines. If the remaining door/lifecycle cannot fit, the
fallback is a single named exception in this ADR (no higher than 1,000), not a silent raise. The
preferred outcome is 800.

### 4.4 Memory: the port stays, the reasons to change move

`memory/store.py` keeps `MemoryStore` as the one port and delegates to internal helpers:

| New module | Content | Target |
| --- | --- | --- |
| `memory/writes.py` | add/write preparation and validation, supersede selection | ≤800 |
| `memory/candidates.py` | intake, capture, transition, label/pending, fail/defer | ≤800 |
| `memory/reads.py` | readable/admin/list/get/context/search/backlog/scopes/counts | ≤800 |
| `memory/maintenance.py` | forget, purge, veto, privacy, rekey | ≤800 |
| `memory/disputes.py` | reflection apply/merge, supersede candidates, disputes, soul rules | ≤800 |

Helpers take explicit dependencies (`conn`, `Database`, policy, guard, index) or a small frozen
dependency object; they never construct a second `MemoryStore`, open a second pool, or write
`memories` outside the existing statements in `memory/queries.py`. `MemoryStore` remains the only
object the engine calls; reads for prompts still go through `memories_readable` and
`memory/queries.py`. ADR-0001 is unchanged.

The target is `memory/store.py` at or below 800 lines after all five extractions. The order is
reads, maintenance, candidates, writes, disputes, so each commit is small and the facade shrinks
monotonically.

### 4.5 Caps after the split

| Module | Cap before | Cap after |
| --- | --- | --- |
| `agent.py` | 1,800 | 800 |
| `jobs/automation.py` | 2,000 | removed |
| `memory/store.py` | 2,400 | 800 |
| every new module in §4.2–§4.4 | — | 800 (ordinary gate) |
| `config.py`, `stores/affect.py`, `jobs/cognition.py`, `schema.py` | allowed | unchanged |

`scripts/selfcheck.py` loses the three `LINE_CAPS` entries; the ordinary `MODULE_CAP` is the only
size gate for these files. `TOP_LEVEL_CAP` and `OVER_CAP_ALLOWED` are unchanged.

### 4.6 What does not change

- The turn step names and checkpoint payloads (ADR-0013).
- The dispatch door, address, and receipt types (ADR-0032).
- The single memory port, the write path, and the read path (ADR-0001).
- Automation manifest schema and validation errors; only the module that owns them moves.
- The public `Agent`, `AutomationStore`, `AutomationWorker`, and `MemoryStore` APIs.
- No schema, migration, config key, or tracked document beyond the ADR tree and `AGENTS.md`.

## 5. Configuration

None. This ADR changes no config key, schema, or wire contract.

## 6. Metrics and gates

Counts to drive to zero, measured 2026-11-04:

| Metric | Today | Target |
| --- | --- | --- |
| capped concentrated modules | 3 | 0 |
| modules above 800 lines outside the four ADR-0031 exceptions | 3 | 0 |
| private cap entries in `selfcheck.py` | 3 | 0 |
| new top-level engine files | 0 | 0 |

The gate, per split commit:

1. `pytest -q` (with `EIDESOL_TEST_DATABASE_URL`) passes; no test changes its assertion.
2. `ruff check eidesoul scripts tests plugins migrations` is clean.
3. `python -P scripts/types.py --check` reports no new errors.
4. `python scripts/selfcheck.py --check` passes with the lowered cap.
5. `python scripts/adr.py --check` and `--translate-check` pass.
6. `git diff` for the commit is import moves, class moves, and delegations only; no logic diff in
   the moved bodies except the module prefix and the façade call.
7. The automation and turn tests exercise a resumed turn and a claimed automation run; moving the
   code must not change the step/claim sequence.

## 7. Acceptance criteria

- `agent.py`, `memory/store.py`, and `jobs/automation.py` no longer appear in `LINE_CAPS`; the first
  two are at or below 800 lines, and the last is deleted.
- Each new module in §4.2–§4.4 is at or below 800 lines and has one reason to change.
- `Agent`, `AutomationStore`, `AutomationWorker`, and `MemoryStore` keep their public signatures;
  callers import the facade, not an internal helper.
- `MemoryStore` remains the one port; no second store, pool, or read path exists.
- A resumed turn and a claimed automation run take the same paths as before (tests prove it).
- No new top-level engine file, no schema or config change, and no new tracked document beyond the
  ADR and `AGENTS.md` tree.
- If an extracted module misses 800, this ADR records the named exception and the reason; the cap
  does not silently rise.

## 8. Risks and mitigations

- **A move hides a behavior change.** Mitigation: pure moves, one module per commit, and a diff
  review that ignores the moved body; tests cover resume, claim, and delivery.
- **Circular imports after the turn split.** Mitigation: `turn/loop.py` receives a narrow host
  protocol and never imports `agent.py`; the dependency direction is one-way.
- **The memory façade becomes a second port.** Mitigation: helpers take dependencies, not
  `MemoryStore`; the only public object stays `MemoryStore`, and `memories_readable` stays the read
  SQL.
- **Over-splitting small modules.** Mitigation: 800 is a ceiling, not a target; a module that lands
  far below it may merge with its sibling in the same commit if the reason to change is the same.
- **Import churn breaks external scripts.** Mitigation: keep the public class paths stable; only
  internal helper imports move.
- **The cap ratchet blocks an urgent fix.** Mitigation: a fix that must land first is allowed on
  top of the current cap with a note in the PR/commit; the split follows immediately. The cap is
  never raised.

## 9. Interaction with other ADRs

- ADR-0031: the rule, the top-level tree, and the four exceptions stay; this ADR removes the three
  private caps and adds only in-folder modules.
- ADR-0001: the single `MemoryStore` port and the canonical write path stay; the new memory modules
  are internal helpers.
- ADR-0013: turn checkpoints and resume semantics stay; the state machine moves behind the same
  steps.
- ADR-0032: the dispatch door stays in `Agent`; delivery helpers do not become a second door.
- ADR-0035/0036: app skills and roles add columns/config, not modules; they do not depend on this
  split.

## 10. Code references

- `scripts/selfcheck.py:36-74` — `LINE_CAPS`, `OVER_CAP_ALLOWED`, `MODULE_CAP`, and the size gate.
- `eidesoul/engine/agent.py:753-818,1005-1640,1646-1707,1774-1800` — delivery, `_run_turn`, command
  dispatch, settings/close.
- `eidesoul/engine/jobs/automation.py:75-599,600-1255,1256-1971` — manifest/authority, store, worker.
- `eidesoul/engine/memory/store.py:314-1145,1334-1596,1597-1762,1972-2363` — writes/candidates,
  retention, reads, reflection/disputes.
- `eidesoul/engine/memory/queries.py` — the SQL statements; the helpers call it, they do not replace
  it.
- `eidesoul/engine/turn/` — the folder that receives the new turn modules; `reply.py` stays text
  shaping, `assembly.py`/`prompts.py` keep their roles.
- `tests/test_turn_lifecycle.py`, `tests/test_automation.py`, `tests/test_memory.py` — the suites
  that prove resume, claim, and memory behavior across the moves.

---

*This ADR lives at `docs/v0.1.0/adr/engine/0037-module-budgets.md`.*
