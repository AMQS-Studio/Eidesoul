# ADR-0049: Audit Remediation — Correctness, Crash Safety, and Simplification in Three Phases

- **Status:** accepted
- **Date:** 2026-11-05
- **Deciders:** Muqsith (owner)
- **Context:** turns verified codebase findings into an ordered implementation plan, scoped by ADR-0048.
- **Supersedes:** —
- **Related:** ADR-0011 (crash safety), ADR-0012 (lease safety), ADR-0013 (turn lifecycle), ADR-0014 (automation), ADR-0031 (engine structure), ADR-0048 (single-tenant), ADR-0050 (cross-gateway), ADR-0051 (native memory retrieval), ADR-0052 (detector precision)
- **Implementation:** draft — nothing below is applied yet; `TASK.md` carries the checklist.
- **Location note:** this plan binds the engine, so it sits under `adr/engine/`.

---

## 1. Context

Two audits read the repository end to end. The second re-ran the first one's probes and confirmed about
95% of its claims. The full test suite is green (922 passed) because the tests check syntax and
mock the very seams that are broken: an AST test accepts a sync DB block called from `async def`, a
lambda replaces `AutomationStore.propose`, and every `send_to` test goes WhatsApp to WhatsApp. Green
tests are not evidence here.

Verified defects that need a fix, grouped by cause:

| Group | Defect | Where |
| --- | --- | --- |
| Identity | a resumed turn passes `per_…` as an address; `IdentityStore.resolve` mints a phantom person | `turn/intake.py`, `jobs/supervisor.py`, `access/identity.py` |
| Wiring | `AutomationStore.propose` takes `thread_id` keyword-only; `cognition.py` passes it positionally | `jobs/cognition.py`, `jobs/automation_store.py` |
| Memory | `promote_staged` compares UUID `memories.id` with `message:<id>` refs, so staged rows never promote | `memory/disputes.py` |
| Resume | `drafted` checkpoint is written before `privacy.review`; a resume skips review | `turn/loop.py` |
| Resume | `intent -> unknown` looks for `turn:{id}` but chunks use `turn:{id}:{n}`; tool sends use a random `Action.id` | `stores/turns.py`, `turn/delivery.py`, `inbound.py` |
| Prompt | `safety` and `research` blocks are not pinned and can be dropped by salience | `turn/prompts.py` |
| Approval | strict mode parks plain replies because `declared_class` is not passed | `agent.py` |
| Guard | wrappers (`timeout`, `env -S`, `${IFS}`) and credential paths pass; harness `run` skips the scan | `guard/`, `gateways/harness/tools/run.py` |
| `/link` | wrong codes are not counted; an admin can be merged away | `access/identity.py` |
| Plugins | `deps/` is imported with `sys.path.insert(0, …)` but excluded from the digest | `plugins.py` |
| Gate | `turn/loop.py` is 809 lines; `scripts/selfcheck.py --check` fails today | `turn/loop.py` |
| Event loop | sync DB and MiniLM calls run inside `async def`, which can starve the lease heartbeat | `turn/loop.py`, `jobs/supervisor.py`, `turn/research.py` |
| Install | image lacks `migrations/`; `install.sh` starts the container before the admin exists; `people add --role member` is refused; `monoagens config` without a subcommand crashes | `Dockerfile`, `install.sh`, `admin/people.py`, `__main__.py` |
| Hygiene | SQLAlchemy JSONB `-` operator warns 342 times and will raise; `datetime.utcnow()`; config keys that no loader reads; Indonesian literals in engine strings | `stores/history.py`, `config.yaml` |

Cross-gateway sending, memory retrieval, and detector precision are large enough to carry their own
ADRs (0050, 0051, 0052). The remaining audit items sit in ADR-0053 (stores, jobs, identity, access,
install), ADR-0054 (extension DX), and ADR-0055 (repository slimming).

## 2. Decision drivers

1. Fix what loses data, leaks privacy, double-sends, or crashes before anything cosmetic.
2. A fix must come with a test that exercises the real seam, not a mock of it.
3. Keep every change inside the existing contracts (ADR-0011, 0012, 0013); no new server or queue.
4. Restore a green `selfcheck` before adding code.
5. Simplification is allowed only when behaviour and the vision stay intact.

## 3. Options considered

**A. One large refactor.** Rejected: it hides regressions and blocks the hot fixes behind the rewrite.

**B. Fix only the headline bugs.** Rejected: the test suite cannot catch the next one; the seam tests
are part of the fix.

**C. Three ordered phases, each shippable.** Accepted.

## 4. Decision

### 4.1 Phase P0 — boot, data correctness, security

1. **Identity on resume.** `TurnIntake.identify` and `IdentityStore.resolve` return a known `per_…` id
   unchanged and never create a `people` or `person_links` row for it. The turn row stores `hints`
   (`dm`, `mention`, `reply`, `app`) so the supervisor restores the full `Percept`.
2. **Review before checkpoint.** `polish -> privacy.review -> cross_chat_leak` always run before
   `checkpoint("reviewed")`; delivery sends only reviewed text, and a resume from `drafted` re-runs
   review. `safety` and `research` prompt parts become `pinned=True`.
3. **Exit authorization.** `origin` is a required argument of the delivery door; the fallback
   `origin or action.address` is deleted. `Agent.dispatch` passes `declared_class` to `ActionRequest`
   so a normal reply is `consent` in strict mode. Approvals store `origin_gateway` and `origin_thread`.
4. **Idempotent sends.** Every tool-created `Action` gets `id = f"turn:{turn_id}:tool:{step_seq}"`.
   The reaper matches `scope_key LIKE 'turn:{id}%'`. Tool aliases resolve before `execute`, and the
   step is recorded before a non-idempotent tool runs.
5. **Wiring fixes.** `propose(manifest, thread_id=thread)`; `promote_staged` matches the real
   `source_message_id`; `monoagens people add` checks the `administrator` role for a first user;
   `monoagens config` with no subcommand prints help.
6. **Guard and plugin fixes.** Harness `run` declares `scan=("command",)`. `SHELL_WRAPPERS` adds
   `timeout`, `flock`, `setsid`, `script`, `watch`, `doas`, `pkexec`; `env -S` keeps its value;
   `${IFS}` and brace expansion are refused; `cat`, `find`, and `sort` are limited to the workspace and
   to non-writing flags. `deps/` joins the digest or loads without `sys.path.insert(0, …)`; `plugins
   remove` checks the target sits under the workspace. `/link` counts wrong codes and refuses to merge a
   person who holds a role in `roles.yaml`.
7. **Install path.** The image copies `migrations/`; `install.sh` runs `db upgrade` and `people add`
   before starting the service; config writes survive a single-file bind-mount (`EBUSY`).
8. **Gate and hygiene.** Cut `turn/loop.py` under 800 lines so `selfcheck` is green. Replace the JSONB
   `-` operator in `stores/history.py`. Replace `datetime.utcnow()`. Move the Indonesian literals in
   `delivery.py`, `llm.py`, and `loop.py` into `t()` catalogs or English diagnostics. Reject unknown
   config keys at load.

### 4.2 Phase P1 — cross-gateway, async I/O, memory machine

1. **Cross-gateway** is ADR-0050. **Memory retrieval** is ADR-0051. **Detector precision** is ADR-0052.
2. **Event loop.** Every sync DB call and every `embed.encode` or privacy classify call that sits in
   an `async def` goes through `await io.run(...)`. `Lease.renew` and the send gate get a small
   dedicated pool; `MemoryStore._fence` uses one transaction instead of a double checkout.
3. **Fencing.** The check-then-act in `MemoryStore._fence` becomes one SQL predicate on
   `engine_lease.generation`; `TurnStore.finish/interrupt`, `AutomationStore`, `CognitionQueue._set`, and
   `SkillStore.save` get the same predicate. Expiry uses the database clock.
4. **Consolidation.** `supersede:` candidates consolidate for every scope, not only `global` with
   `skills.enabled`. The cognition claim lease exceeds `llm.timeout_seconds` plus a margin.
5. **Reload.** `/reload` keeps `soul.yaml` edits when only `voice.*` changed.

### 4.3 Phase P2 — simplification

1. Replace the five `__getattr__` helpers in `turn/` (`TurnRunner`, `Delivery`, `TurnIntake`,
   `TurnSteps`, `TurnCommands`) with explicit `TurnRuntime` dependencies and a typed `TurnState`; move the
   nine in-flight maps of `Agent` into one `TurnCoordinator`.
2. Replace `monoagens/vendor/emotion_engine.py` (4,855 lines) with a PAD module of about 200 lines.
   `StyleStore.learn` is wired to the end of the turn or the store is deleted.
3. Rename `role`/`roles` in `soul.yaml` to `mode`/`modes` so persona modes stop colliding with RBAC;
   `RoleRegistry.policy_for` reads the first role that defines the gateway, not only `roles[0]`.
4. Remove the dead `people.role` column and fix `monoagens people list`.
5. Remove the `panel/` promise from `AGENTS.md` and ADR-0027 until it exists, and declare
   `jobs/schedule.py` in the engine tree.

### 4.4 Rules for every change

- A fix lands with a failing test first, run against the real seam (real `AutomationStore.propose`,
  real `IdentityStore`, real second gateway).
- AST tests stay as a floor; add behavioural tests for each defect in §1.
- Natural-language probes in tests use real questions and everyday sentences.

## 5. Configuration

No new setting. Unknown keys in `config.yaml` become a load error, and the dead keys
(`engine.memory.capture_min_chars`, `engine.skills.max_index_chars`, `engine.ambient.context_enabled`,
`gateways.cli.role`) are corrected or removed in the template.

## 6. Metrics and gates

- `pytest -q`, `ruff`, `python -P scripts/types.py --check`, `python -P scripts/selfcheck.py --check`
  (currently failing), `python -P scripts/adr.py --check`.
- A chaos test per ST scenario (resume, crash after send, parallel writers).
- `-W error::sqlalchemy.exc.SADeprecationWarning` on the suite.

## 7. Acceptance criteria

- [ ] P0 items 1–8 each have a test that failed before the fix.
- [ ] `selfcheck --check` is green.
- [ ] A turn killed mid-run resumes with the same `per_…` and sends each chunk once.
- [ ] No `async def` in the engine reaches a sync DB or encode call without `io.run`.
- [ ] No `__getattr__` delegation remains under `monoagens/engine/turn/`.

## 8. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Pinning `safety` and `research` squeezes the prompt budget | Count pinned blocks first; the budget test in ADR-0037 gates it |
| Stricter guard raises false positives | Pair every new deny with an allow case in the tests |
| The `mode`/`modes` rename breaks existing souls | One loader error that names the new key; no alias (AGENTS.md) |
| Fencing predicates add load | One indexed single-row read per write |

## 9. Interaction with other ADRs

ADR-0011/0013 keep their contracts; this ADR fixes how the code meets them. ADR-0012 gains the atomic
predicate in §4.2.3. ADR-0031 and ADR-0037 keep the 800-line cap; the `__getattr__` split is replaced,
not relaxed.

## 10. Code references

- `monoagens/engine/turn/loop.py`, `intake.py`, `delivery.py`, `prompts.py`
- `monoagens/engine/jobs/supervisor.py`, `cognition.py`, `automation_store.py`
- `monoagens/engine/access/identity.py`, `monoagens/engine/stores/turns.py`
- `monoagens/engine/guard/policy.py`, `sandbox.py`, `monoagens/engine/plugins.py`

---

*This ADR lives at `docs/v0.1.0/adr/engine/0049-audit-remediation.md`.*
