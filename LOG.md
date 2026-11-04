# LOG

## 2026-11-04 — ADR-0039 init terminal interface and the Eidos mark

`eidesoul init` now starts at **Location**, shows the resolved directory before any write, and
navigates with `↑`/`↓`, `j`/`k`, `/` filter, Enter, `←` back, and Escape/Ctrl-C cancel. The
provider picker uses operator labels while IDs and key variables stay in `providers`; the model
picker reads the provider's served ids when reachable; the credential is masked and written only
to `.env` at `0600`; `--api-key-file` is the headless equivalent. Review keeps secrets redacted.
The Eidos lockup replaces the `[logo]` placeholder: an open ring with a center dot, the `◈`
terminal glyph, and SVG assets under `docs/brand/`. The writer and interaction are split:
`admin/init.py` 443 lines, `admin/wizard.py` 472, `admin/terminal.py` 281. Tests cover the
location step, credential masking, `--api-key-file`, lockup, filter, cancel, and line fallback.

Gates: `pytest -q` 879 passed; `ruff` clean; `pyright` 0 errors; `selfcheck` ok; ADR check 40/40 and
translation clean.


## 2026-11-04 — ADR-0034..0038 implemented

The module caps are real: `agent.py` is 787 lines behind `turn/loop.py`, `intake.py`, `delivery.py`,
`commands.py`, and `steps.py`; `memory/store.py` is 645 lines behind writes/candidates/reads/
maintenance/disputes/records; `jobs/automation.py` is replaced by manifest/store/worker. `selfcheck`
has no private cap entries. Prompt blocks are one `PromptPart` registry with a frozen `PromptContext`
and metadata salience; plugins declare static prompt parts and the loader checks them before import.
Skills gain a nullable `app` namespace above `scope`, threaded from the body hint through turns,
episodes, retrieval, and writes. Roles are identity-only bundles: `protected` is administrator-only,
`users` are `per_...`, the `roles` hint and CLI role are gone, and `eidesoul people add` is the
bootstrap. Privacy now classifies locally: `memory/evidence.py` returns detector/version/digest/
label/score/span, a versioned policy writes the level, credentials drop, `ask` is policy-owned, and
`memories.privacy_evidence`/`privacy_policy` persist the record. The auditor and pending-answer
parser call no external model. The golden set is `tests/data/privacy_cases.yaml`. The entity layer is
now Presidio 2.2.366 with spaCy 3.8.18 and `xx_ent_wiki_sm` 3.8.0: its tree digest is checked at
boot, the model is loaded without the 300 MB vocabulary, torch/transformers are blocked during that
import, and every result is `entity` evidence with a span. Measured entity cost is 111 MiB
incremental and 40.4 MB on disk; `tests/test_privacy_budget.py` enforces the 200 MiB/80 MB row.

Gates: `pytest -q` 869 passed; `ruff` clean; `pyright` 0 errors; `selfcheck` ok; ADR check 39/39 and
translation clean. ADR-0038 remains partial: a fresh process that loads the shared PyTorch MiniLM
plus the entity layer measured about 929 MiB, above the §5.1 700 MiB cold cap. Moving the shared
embedder to int8/ONNX (or removing a detector) is the next budget fix; the semantic linear head and
production calibration are still open.

## 2026-11-04 — ADR-0038 decides layered privacy

The level stays, but the label gets an evidence trail and the policy gets sole authority. A
deterministic floor (regex/checksum/dictionary) can only be raised; entity and semantic detectors
return evidence with spans and scores; a versioned policy writes the final level and action; L4
goes to a human. A semantic taxonomy covers sensitivity that PII detection cannot see
(`crime_admission`, `health`, `relationship_conflict`, `third_party_personal`, ...), with a worked
mapping for "aku tadi nyuri" and third-party opinions. Local is the default; L4 and credentials
never leave raw; no external residual model is part of the decision. The
decision record carries detector/model versions, policy hash, approver, and expiry, and maps to EU
AI Act 12/19 and ISO 42001. `memories.privacy_evidence` becomes the persisted array; `MemoryStore`
stays the single port. Evaluation per label/language and a 30-day detection mode gate enforcement. Amended: §5.1 caps the default privacy add-on at 300 MB incremental RSS, reuses the existing MiniLM, and removes the optional GLiNER2/Privacy Filter/SLM paths; adding a model now requires a new ADR.
It also fixes the deployment boundary: the engine is a program, the deployer is the controller, and
notice/consent/DSAR/DPO/DPIA/breach workflows belong to the deployment or the separate hosted app,
not to the engine. §4.10 adds the affect boundary: sentiment may shape tone, salience, or a crisis
route but never the privacy level; ambiguous affective text is staged or reviewed, not guessed.
§6.1 makes every gate and acceptance criterion an automated test; a rule without a passing test is
not implemented, and bug fixes ship with a regression test that fails before and passes after.
## 2026-11-04 — ADR-0037 plans the module split before the cap

`agent.py` is at its 1,800-line cap, `memory/store.py` has 24 lines of headroom, and
`jobs/automation.py` has 29; the next feature edit is a red gate. ADR-0037 keeps the ADR-0031 rule
and lowers the ceilings instead of raising them: automation splits at its existing class boundaries
(manifest, store, worker), the turn loop moves into `turn/loop.py` with intake/delivery/commands/steps,
and the memory port stays one facade over writes, candidates, reads, maintenance, and disputes.
Each split is one behavior-preserving commit that lowers the cap in the same change; the three
private `LINE_CAPS` entries go away.

The golden prompt contract was already decided in ADR-0033 §6.11 and exists in
`tests/test_prompt_contract.py` + `tests/data/prompt_cases.yaml`; the missing crisis/approval cases
are implementation, not a new decision. Panel and release stay out of scope per the current focus.

## 2026-11-04 — ADR-0036 decides the role model

Roles stay named, flat permission bundles: no `type`/`kind` with authority. `administrator` remains
the only reserved, protected name (no `admin` alias); `protected: true` is rejected on any other
role, and one `privileged(role)` predicate replaces the split `status`/prompt rules. Role selection
becomes identity-only: the `roles` hint, `HINT_KEYS` entry, and `gateways.cli.role` go, so a body
cannot claim authority. `users` must be `per_...` ids, the seed drops `Committee member`,
`cari`/`memori`/`kirim`, and `google`, and a new `eidesoul people add <gateway> <address> --role
administrator` bootstraps the first administrator in one audited transaction. Additive union is
documented; deny, inheritance, and per-room grants are out of scope.

Next: implement `people add`, remove the hint and CLI role key, update the seed and tests.
## 2026-11-04 — ADR-0035 decides app-scoped skills

Skills carry an audience (`global|thread|person`) but no corpus, so "procedure for app A" can only
be approximated by a fixed thread, a plugin pack with its own tool, or the incidental
tool-mention filter. One body may host several apps and one app may span bodies, so body is the
wrong axis.

ADR-0035 (draft) adds a nullable `app` namespace on `turns`, `episodes`, and `skills`, fed by
`Inbound.hints["app"]`. Visibility filters app first, then the unchanged scope rule; a missing hint
hides app-scoped skills and keeps shared ones (fail closed). The episode signature includes the
app, reflection copies it, promote/merge preserve it, and `ToolContext` gains `app` so a plugin
tool filters with an explicit fact. Shared skills stay `app=''`; no app literal lives in code.

Next: migration 0017, the visibility matrix test, and the intake/signature changes.

## 2026-11-04 — ADR-0034 decides a prompt-part registry

The prompt is assembled correctly but described three times: sixteen block names in
`stable_blocks`/`volatile_blocks`, the same names in `PINNED`/`MANDATORY`/`COMPETING`/`ORDER`,
and twelve keyword arguments at the agent's call site. Adding a block edits four files plus a
test; a plugin has no prompt surface at all.

ADR-0034 (draft) chooses one `PromptPart` entry per block (`name`, `stable`, `order`, `bonus`,
`pinned`, `produce`/`text`), one frozen `PromptContext` built per turn, metadata-driven salience,
and plugin parts as static text declared in `provides.prompt`. The body keeps `prompt_section()`
as the single `body` part; a gateway gains no second surface. No new module: the registry lives
in `turn/prompts.py`. It rejects the `Handler` route (permissions/catalog/async are the wrong
contract) and the store-push route (dependency inversion and a global registry).

Next: `tests/test_prompt_parts.py` and a rebuilt `test_salience.py` before any producer moves.

## 2026-11-04 — Neutral core lands: culture, transport, and vendor are data

ADR-0033 executed in four waves on top of the required-values commit.

**Culture is packs.** Crisis and sensitive phrases moved to `library/safety/<lang>.json` (the
English net stays compiled as the last line; a language without a pack is refused at boot unless
`engine.safety.allow_degraded`). Profanity severity, the rhythm seed, particles, emojis, laughter,
affirmations, level words, and serious registers are per-language data; `stores/style.py` no longer
seeds an English chat with an Indonesian rhythm, and a language without a style pack gets no style
block.

**Currents and the old names are gone.** `currents` is deleted in full (module pair, command, state,
config keys, and the country-shaped prompt prose). Commands are English only: `cari`, `kirim`, and
`memori` became `search`, `send`, and `memory`; `konteks` and `gender` disappeared with their keys,
aliases, and the verb DSL.

**A person is not classified.** `access/target.py`, `display_target`, the gender config keys, the
`people.gender*` columns (migration 0014), `affect.gender`/`set_gender`, and the WhatsApp avatar
signal are removed. The register comes from the thread's configuration; the hard floor is unchanged.

**Extensions declare, core discovers.** Handlers carry `target`, `approval_class`, `destructive`,
`path_arg`, and `scan`; `FAMILIES`, `AUTHORIZATION_TOOLS`, `DESTRUCTIVE_ACTIONS`, `PROVIDES_KEYS`,
and the per-name policy branches are gone. A plugin's tool passes validation by declaration, and an
episode reads the target recorded with the step (migration 0016). Google's tables live in the plugin
tree (migration 0015 drops the core copies), the legacy role rename and the address-adopt path are
deleted, and `__main__.py` no longer imports a transport.

**Gates.** `scripts/neutral.py --check` scans engine, admin, `__main__.py`, templates, and the
Dockerfile for culture words, non-ASCII letters, language/locale/zone defaults, closed name tables,
vendor names outside the allowlist, and clock reads outside `clock.py`. `tests/data/prompt_cases.yaml`
plus `tests/test_prompt_contract.py` pin prompt properties per body (empty section, CLI, shared
thread) and per behavior (crisis, register, missing catalog), never wording.

**The last transport seam moved.** `HistoryStore.dm_thread` and its three callers became
`PrivateThread.private_thread(person_id)`: the CLI, harness, and WhatsApp body answer it, and the
engine reads no thread kind or address. The `Chat` protocol is gone (the door is `dispatch`), the
digest prompt names a thread instead of a chat group, style sampling skips media rows at the query
instead of matching body placeholders, and the relay question names a private thread. ADR-0033 is
`implemented`.

## 2026-11-04 — ADR-0033 decides a neutral core

One decision record for the rule the tree was missing: **core is mechanism, never culture,
geography, transport, or vendor.** The evidence is measured in §1, not asserted — thirteen
`language="id"` defaults (nine signatures plus `soul.language`, `normalize_language`, the `auto`
alias, `parse_digest`), four word tables in code (`privacy` ×2, `character.SEVERITY`,
`target.py`), a crisis regex that only knows Indonesian and a handful of English phrases,
`DEFAULT_TZ = "Asia/Jakarta"`, `currents` carrying one country's news topics and answerability to
one politician, four plugin references (template default, role grant, a three-name `ENV_TAIL`
against a manifest that declares one, `TARGET_TYPES`), five transport references (`relay`'s DM,
`dm_thread` ×3, `Chat`, `digest`), twelve non-English surface names, and four legacy markers.
The shipped library makes the same point from the data side: the only pattern and fact packs are
`language: id`, so `packs_for("en")` is empty.

**Decided** — culture becomes packs (`library/safety/<lang>.json`, `library/severity/<lang>.json`,
`affirmations`/`level_words` in `lexicon`); `language` and a manifest's `tz` are required values
with no code default; `currents` is deleted in full (module pair, `/konteks`, prompt block, state
key); `dm_thread`, `Chat`, and `digest` move behind the inbound seam; templates write `plugins: {}`
and derive `.env` from installed manifests' `secrets`; tool target types come from the tool's own
declaration; `cari`/`kirim`/`memori` become `search`/`send`/`memory` with no localized aliases;
`LEGACY`, `LEGACY_GATEWAY`, and the compatibility hooks are deleted rather than deprecated.
A language without a safety pack is refused unless `engine.safety.allow_degraded: true`, in which
case `/status` and `selftest` report `degraded`.

**Implementation: draft.** Nothing above is code yet; §6 names the gate (`scripts/neutral.py
--check`) and the counts to drive to zero, and §7 is the acceptance list. The Indonesian copy is
`docs/v0.1.0/adr/id/0033-neutral-core.md`; the register row is in `docs/v0.1.0/README.md`.

**Amended after a full sweep.** A read-only audit of `engine`, `admin`, `__main__.py`, the
templates, the image, and `scripts/` found the first inventory understated and six decisions
incomplete, so §1 was rewritten with measured counts (25 language defaults plus 8 fallbacks, not
13; 8 word tables, not 4; 72 transport lines, not 5; 13 vendor references plus two plugin tables
in `schema.py`; 9 locale/country defaults, not 2; 6 clock sites) and three clauses were added:
§4.11 a handler declares its own `family`/`target`/`approval_class`/`destructive`/`path_arg`/`scan`
so a plugin tool is classifiable at all, §4.12 the spine is the only vendor allowlist, §4.13 one
clock and one locale. §4.1 now states that the shipped CLI body is not an "optional gateway",
§4.4 requires the `currents` topic list to leave `turn/prompts.py` as well, §4.8 covers the second
gender gate in `character/patterns.py`, and §6's first check now reads "non-ASCII letter" rather
than "non-ASCII character", because the old wording could never pass against `§`, `—`, `→`, and the
emoji in command descriptions.

**Prompt neutrality is delegated, and then tested.** Transport facts leave the core prompt and become
a body-declared `prompt_section()` on `CoreDefaults`: empty by default, static per boot, English,
capped at 1200 characters, no user data. The CLI and WhatsApp describe their own mentions, quotes,
stickers, and reachable targets; a robot with one thread per sensor returns nothing and gets a
neutral prompt. Because the section joins the system prompt, `prompt_prefix_hash` (ADR-0025)
already covers it, so the cache stays correct. A prompt contract suite
(`tests/data/prompt_cases.yaml`, run by `pytest -q`) pins properties — required fragments,
forbidden tokens, expected prefix hash — per body and per behavior, never word-for-word text, so a
rewording is free and a leaked transport or culture token fails.

## 2026-11-04 — Pyright is clean: zero errors, and the ratchet has nothing left to permit

`pyright` over `eidesoul`, `admin`, `gateways`, and `scripts` reports **0 errors**, so
`scripts/types-baseline.json` is now an empty object: the gate no longer allows a single diagnostic
to appear. The 191 diagnostics were fixed as type narrowing only (guards, hoisted locals, precise
annotations) across 40 files; no test was rewritten, and `ruff check` and the 851-test suite are
unchanged.

**Two real defects the checker had been holding** — `MemoryStore.label(memory_id, *, level, ...)`
was unreachable: `__init__` sets `self.label = "postgres"` and `bind(label=...)` resets it, so the
instance attribute shadowed the method for every instance, and no caller ever invoked it; the method
is deleted and the string attribute (`memory_status`, `bind`) is the surviving contract.
`CursorResult.inserted_primary_key` is `Optional[Any]` in SQLAlchemy 2.1, and both insert sites
(`access/approvals.py`, `memory/ledger.py`) indexed it blind; each now raises its own
`RuntimeError` when the insert reports no key.

**One contract gap** — `MemoryBackend.context` omitted the `direct` keyword that
`MemoryStore.context` requires and enforces (ADR-0006 §4.1); the protocol now declares it, and the
store keeps raising when a caller does not state its audience.

**The recurring shapes** — a ternary such as `x = raw.get(k) if isinstance(raw.get(k), dict) else {}`
leaves `x` as `dict | None` because pyright narrows the checked expression, not the value
expression; `int()`/`float()` on a JSON scalar relied on `except (TypeError, ValueError)` for the
`None` case, which is now an explicit branch in front of the same `try`; `getattr`-reached gateway
methods needed `Callable`/`Awaitable` annotations; and the neonize/genai stubs accept no cast-free
form, so those boundaries carry a `cast` with a one-line reason.

**Verification** — `pyright` 0 errors; `ruff check eidesoul scripts tests plugins migrations` clean;
`pytest -q` 851 passed; `scripts/selfcheck.py --check` ok (`engine_modules=111`); `scripts/adr.py
--check` 33/33 and `--translate-check` 33 files, 0 problems.

## 2026-11-03 — ADR-0031 and ADR-0032 implemented

Both drafts are now code, on the same day they were accepted. Measured after the landing: the engine
top level is **15 files** (from 41), **25 modules** sit in `turn/`, `character/`, `access/`, `jobs/`,
`ablate.py` left for `scripts/ablate_engine.py`, and the three concentrated modules are
`memory/store.py` 2,399 (from 2,785), `agent.py` 1,800 (from 2,215), `jobs/automation.py` 1,953
(from 2,106). `scripts/selfcheck.py --check` now fails when any of those caps or the 800-line module
ceiling is exceeded.

**Toolchain** — `requires-python = ">=3.14"` (image, `config.yaml`, installer, both READMEs),
`from __future__ import annotations` removed from 249 files and banned by a structure test, PEP 735
`[dependency-groups] dev` replaces the `dev` extra, ruff gained `RUF`/`ASYNC`/`PT`/`FA` (per-file
ignores record the pre-existing complexity debt instead of hiding it globally), and
`scripts/types.py --check` gates pyright against a committed per-package baseline (163/11/15/7 errors
today; the ratchet lowers, never raises).

**One door** — `Agent.dispatch(subject, address, action, *, origin, reason)` is the only engine
function that reaches a body: policy, approval row, ledger (`intent` → `sent`/`unknown`, keyed by
`action.id`), then `body.dispatch(action)`. The turn, automation, privacy, relay, `/approve`, and the
WhatsApp `send_to` tool all go through it; the three thread→body resolvers are one
(`Agent.address_of`); `GatewayCore` no longer requires `send_text`/`render` (they moved to the
optional `Chat` protocol), and the CLI, harness, WhatsApp, and a dispatch-only robot all pass
`ensure_core`. Tools are indexed per body (`ToolIndex`), so two bodies may share a name, a qualified
`<body>__<tool>` reaches another one, and the prompt lists the current body's tools plus one line
naming the others.

**Behaviour notes** — engine-chunked replies no longer quote the triggering message: quoting is
chat-only work the ADR keeps out of the door, and a body may add it on its own. A send whose target
has no route is refused (`no body owns thread '<id>'`) instead of falling back to the first body; an
automation's `owner_dm` resolves the owner's DM thread and refuses when there is none. A second body
claiming an existing thread raises `RouteConflict` before any row is written, with one log line.

**Register and home** — `dispatch` and `receipt` join the §4.12 register; ADR-0000 §4.3 amends
`Action` to `address/capability/payload/approval_class/id`; both headers carry `implemented` with the
numbers above. ADR home: 33 files (`root=7`, `engine=26`), `implemented=31 partial=2 draft=0`, both
gates clean (`--check`, `--translate-check`), and `adr.py --stamp` now rewrites the Indonesian copy's
hash after an English edit.

## 2026-11-03 — Two draft ADRs: engine structure and one delivery door

ADR-0031 and ADR-0032 are accepted as decisions and recorded as `draft`: no engine file has moved and
no delivery call site has changed yet. They exist so the next wave has a written contract instead of
a plan in a chat.

**ADR-0031** — the measured inventory behind it: 100 engine modules, 32,835 lines, 41 flat top-level
files, three modules above 2,000 lines, 84% code / 11% blank / 5% docstring, 21 repeated 8-line
windows. The decision is a split in place (`agent.py` delivery to `reply.py`, counters to
`runtime.py`, memory SQL to `memory/queries.py`), then 25 modules into four domain folders with the
top level at 15, then a growth budget: an engine module is added when it replaces one, splits one,
or its ADR names the reason. §4.7 adds the toolchain half: Python floor 3.14 (so
`from __future__ import annotations` leaves every module and a structure test bans it again),
PEP 735 dependency groups for dev tooling, `pyright` `basic` as a per-directory ratchet, ruff gains
`RUF`/`ASYNC`/`PT`/`FA` while `C90`/`PLR0912` stay, `TaskGroup` only where sibling cancellation is
wanted, and `slots=True` on the hot dataclasses. None of it has landed yet.

**ADR-0032** — one `Address(gateway, thread)` built from the durable route, one `dispatch` that is the
only caller of a body's outbound handler, and the approval class checked before the wire instead of
recorded after it. The outbound message is a Command Message with a receipt (Request–Reply over the
`outbound_messages` outbox, idempotent by `id`), `GatewayCore` stops requiring chat members, reads use
the same address with membership proven by the target body first, and tools are indexed per body so
two bodies can share a name. A second body claiming an existing thread is refused; an ambiguous name
returns candidates, never a pick.

**Register** — `Address` enters the §4.12 term register (owner ADR-0032); ADR-0009 and its Indonesian
copy are re-stamped. ADR home is now 33 files (`root=7`, `engine=26`), `implemented=29 partial=2
draft=2`; both gates clean.

## 2026-11-03 — Eight partial ADRs land, one schema revision

ADR-0012, 0010, 0008, 0005, 0013, 0014, 0020, and 0001 were `partial` for code reasons; each now has
the code, the tests, and the gate result to say `implemented`. ADR-0027 and ADR-0030 stay `partial`,
because the panel and the registry publish are still outside this box.

**Schema (revision 13)** — one append-only revision carries every column the eight needed:
`cognition_jobs.generation`, `memories.dispute_asked_at` + `ix_memories_subject_asked`,
`threads.gateway`, `turns.category`, `skills.priority`, `automation_runs.payload`, and the partial
`ix_automations_message` index. `memories_readable` is recreated so the appended column stays
visible on an upgraded database, and it is the reason `create_all` and the chain agree.

**ADR-0012** — `MemoryStore._fence` runs inside the writer's transaction on every canonical
mutation (write, transition, label, intake journal, disputes, veto, privacy, rekey, forget, purge),
and `CognitionQueue` claim/complete/fail carry the claimed generation. The supervisor binds
`LeaseGuard` to the store once the primary is acquired; admin, CLI, and tests stay unbound.

**ADR-0010** — `questions_per_day` bounds dispute questions per subject per UTC day, counted over
distinct pairs because one question marks both rows. `Agent.ask_dispute` targets the subject's
single-member DM (`HistoryStore.dm_thread`, `threads.kind='dm'`), and the reflection pass hands it
the pairs it opened. Fixing this exposed two live defects: the reflection job handed an
un-awaited coroutine to `io.run`, so no pass had ever run, and `mark_disputed` paired rows by
position over an unordered `IN (…)`.

**ADR-0008** — `label_candidate` passes the stored kind through the same quarantine as the
canonical write, `soul.rules` is the only source of `kind=rule`, and `sync_soul_rules` +
`promote_soul_rules` require two UTC days of corroboration before a rule exists.

**ADR-0005** — MiniLM remember/forget prototypes live in `eidesoul/library/intent/` with
`scripts/intent.py --check` as their gate; the remember hint raises durability in capture, the
deterministic "forget that …" path vetoes and forgets exactly one readable row or nothing at all,
and the daily audit job purges with `memory.retention.purge.{grace_days,batch}`.

**ADR-0013** — the backlog pass is paced: a rolling sixty-second budget, `max_resume_per_chat`
per room, round-robin across rooms, `TurnStore.release` for what was claimed but not selected, and
`per_message_then_consolidate` expiring the tail unless `turns.category='action'`.

**ADR-0014** — `message` automations become real: the intake transaction writes one run per
matching message, the condition reads that message's text and sender, gateway capability counters
appear on `/status`, and `threads.gateway` makes delivery routing durable across restarts.

**ADR-0020** — failed tool steps are recorded as `failed`, the episode signature names scope, tools,
and target types, `eidesoul/engine/memory/efficiency.py` measures the evidence, and the ladder
applies keep/merge/promote/archive/propose as one deterministic action; a proposal is an approval
and nothing else.

**ADR-0001** — `tests/test_memory_eval.py` plus `tests/data/memory_eval.json` measure the canonical
lexical path (12 cases, recall 1.0, precision 1.0, duplicate rate 0.0) and prove
`backfill_index` idempotence; the ADR's stale `remaining` line (capture retry) is corrected.

**Verification** — `pytest -q` 831 passed on Postgres+pgvector; `scripts/adr.py --check` 31/31,
implemented=29 partial=2 draft=0; `--translate-check` 31 files, 0 issues; `scripts/intent.py --check`
OK; `uv lock --check` clean.

## 2026-11-03 — One term for the unit: `plugin`

The installable unit was called `component` in code, config, and the CLI while ADR-0028 and the
config map already said `plugin`. Collapsed to one term, with no alias and no shim.

**Code** — `eidesoul/engine/components.py` is `eidesoul/engine/plugins.py`; `ComponentError`,
`Component`, `Loaded.components`, and `ComponentSettings` are `PluginError`, `Plugin`,
`Loaded.plugins`, and `PluginLoader` (the per-plugin config row keeps its name `PluginSettings`).
The synthetic import package is `eidesoul_plugin_<name>`. Config: `ComponentsConfig` →
`PluginConfig`, `HostConfig.components`/`Config.components` → `.plugin`, the YAML section is
`plugin: {root, fetch, source}`, and the default install root is `<workspace>/plugins`. The CLI verb
is `eidesoul plugins list|install|enable|disable|remove`, and the loader's own strings/audit
literals follow (`invalid plugin name`, `plugin.fetch is false`, `plugin.source is not set`,
`plugins cannot register gateways`, `log_action("plugin", ...)`, `config_changes.file = "plugin"`).

**Unchanged on purpose** — the manifest file name `plugin.yaml`, the `plugins.<name>.enabled|digest`
map, the `plugins/google/` source tree, the credits table's generic "component" wording, and
`Dockerfile --strip-components`.

**Migration** — `config.yaml` never carried a `components:` block, so nothing is rewritten. An
install that already unpacked code under `<workspace>/components` is moved by hand:
`mv <workspace>/components <workspace>/plugins`, as ADR-0028 §8 says.

**Docs** — ADR-0028 §4.1/§4.2/§4.3/§4.4/§5/§8/§10, the ADR-0027 §5 key table and §7 acceptance
criteria, and the ADR-0009 register rows (plugin manifest path, install root, digest) now read
`plugin`; the `id/` copies match and their `sha256:` stamps are restamped. `AGENTS.md`, `README.md`,
and `README.id.md` follow. Editing the Google plugin's docstrings changes its code tree, so
`plugins/google/plugin.yaml` carries the recomputed `digest:` again.

## 2026-11-03 — ADR waves W0-W7: the recorded decisions land

Implementation of the approved ADR set, one wave at a time, each gated before the next. **W0**:
Alembic baseline + primary-only `db upgrade`, lease CAS with `generation` and the DB clock, and the
test suite moved to the real Postgres+pgvector store (`tests/conftest.py`, no SQLite). **W1**: language
packs under `library/lexicon/`, the `Percept`/`Message`/`thread` cutover, `GatewayCore`/`Rich`/
`Directory`, the harness body, and `tests/test_architecture.py`. **W2**: `turns`, `turn_steps`,
`outbound_messages`, checkpointed resume, the delivery ledger, phased shutdown, per-thread locks, the
turn deadline, and the primary send gate. **W3**: `MemoryStore` as the single write port,
`memories_readable`, retention, provenance/trust, the deterministic capture gate, the cognition worker,
reflection/disputes, group privacy, episodes/skills. **W4**: approval classes with `scope_proof`,
the capability index + `info`, per-tool caps with `grep`/`fetch`, input validation, cost rows with
`max_calls_per_turn`, FTS/trigram search. **W5**: the protected `administrator` role and `relay_grants`.
**W6**: `turn_id` + JSON logs + the `/status` card and declarative automations. **W7**: the plugin model
with digest fetch (Google moved to `plugins/google/`), the installer and interactive `init`. The web
panel remains operator-owned and planned, so ADR-0027 and ADR-0030 stay `partial` on that box.
**Verification** — `pytest -q` 792 passed on Postgres+pgvector; `scripts/adr.py --check` 31/31,
implemented=18 partial=13 draft=0; `--translate-check` 31 files, 0 issues; `scripts/selfcheck.py
--check` ok; `uv lock --check` clean; `bash install.sh --dry-run` writes nothing.

## 2026-11-03 — ADR truth audit before implementation

All 31 ADRs were re-read against the code before any of them was implemented. The recorded split
holds: 1 `implemented` (ADR-0009), 9 `partial`, 21 `draft`; no ADR claimed a mechanism the code does
not have, so none was overstated. Three were **understated**: ADR-0000 still said "no engine change
yet" although `send` already left the core for
`eidesoul/gateways/whatsapp/tools/send.py`; ADR-0020 already had `skills`/`skill_files`/`skill_usage`
and the `skill` tool; ADR-0018 already had `Gateway`/`GatewayError` in `inbound.py`. Stale §10 paths
were corrected: ADR-0000 and ADR-0023 now name the gateway `send.py`, ADR-0027 names
`eidesoul/engine/stores/documents.py`, and ADR-0027 §5's row key is `components.root`. The
Indonesian copies were edited on the same lines and re-stamped.
**Verification** — `scripts/adr.py --check` 31/31 (implemented=1 partial=9 draft=21);
`--translate-check` 31 files, 0 issues; `pytest -q` 541 passed before the audit (documentation only).

## 2026-11-03 — The voice layer is on, and switchable (ADR-0027 amendment)

ADR-0027 §4.3 listed what is off by default but was silent about the parts that shape the voice, so
emotion, humanize, and character had no default anywhere. Amendment: a **voice layer** row — emotion,
humanize, and character are **on by default**, `init` asks once with **keep on** as the answer that
needs no input, each part is one switch, and turning one off is an operator action; clean by default
still covers only gateway, tool, and plugin, and the spine stays required. ADR-0030 gains the question
in the wizard and `--voice-layer on|off` for headless parity, so the interactive and piped paths stay
equal. The ADR draws the boundary in one line: switching a part off changes the voice, never the
identity, the memory, or the permissions. ADR-0009's register gains `voice layer` with its synonyms. The
README (EN/ID) states the same rule instead of implying that every subsystem is optional. Documentation
only; gate 31/31, copies 31/31.

## 2026-11-03 — On-machine install and interactive init (ADR-0030)

Onboarding had exactly one path and it started with `podman compose up -d postgres`, while
`eidesoul init` demanded flags (`__main__.py:709-714`). New **ADR-0030** decides the path: one
`install.sh` at the repository root serving both stacks (native and compose), which never runs `sudo`
(a missing package is printed, not installed), pins `uv` by SHA-256 when it is absent, runs
`uv sync --locked` inside its own directory, then hands over to `init`; `eidesoul init` becomes
**interactive on a TTY** and **fails closed** off one, naming the flags it needs; the wizard carries
**our own visual language** — a step rail, a review screen that shows every file and value (secrets as
env names only), a receipt, and a reserved **logo slot** filled later. The studied Hermes bootstrap is a
reference for discipline only (quiet output, install log, honest failures, idempotent repair), never a
template: no stage protocol, no vendored package manager, no telemetry. The installer ends by
**offering** the panel or the command line instead of choosing, and the container path keeps the same
`init` inside the container. Implementation is `draft`: no installer, no interactive `init`, no panel
yet. Gate 31/31, copies 31/31, ADR tests 16 passed; documentation only.

## 2026-11-03 — Test store is the real store (ADR-0029)

The suite runs on SQLite today, so pgvector/Mem0, the dialect-specific upsert branch, real `db upgrade`
DDL, `JSONB` operators, and row-lock behaviour were **simulated rather than tested** (evidence table in
ADR-0029 §1). New **ADR-0029** records the decision: the suite runs on Postgres+pgvector, one throwaway
test database, `CREATE EXTENSION vector` plus the production migration path once per session,
`TRUNCATE ... RESTART IDENTITY CASCADE` per test, a deterministic 384-dimension stub embedder, and
**SQLite removed** together with its dialect branches — a missing test database fails loudly with the
compose command instead of falling back. `Implementation: draft`: the harness switch, compose
parameterisation (`POSTGRES_DB`/`POSTGRES_PORT`), and the `AGENTS.md` storage/verify rule change follow in
a separate change. Gate 30/30, copies 30/30, ADR tests 16 passed. Documentation only: no engine or test
code changed.

## 2026-11-03 — Defaults, not examples

`config.example.yaml` and `.env.example` were **deleted**: `eidesoul/admin/templates.py` already is the
single default source (`init` writes `config.yaml`, `roles.yaml`, `soul.yaml`, and `.env`, the last two
`0600`), and the two example files only duplicated it — a key added to the template would drift away
from a hand-kept copy. `compose.example.yaml` became a real **`compose.yaml`**: it runs as it stands,
takes `POSTGRES_PASSWORD` from `.env`, builds the core image (`EXTRAS: ""`), and keeps the sandbox proxy
behind a `sandbox` profile on a self-contained egress network instead of demanding `dokploy-network`.

The `.env` seed no longer ships a placeholder password: `EIDESOL_DATABASE_URL=` and `POSTGRES_PASSWORD=`
are written as empty names, so a half-filled file cannot look like a working one. ADR-0027 §4.6 records
the rule ("a file we ship must be usable as it stands: the default lives in code, `init` is the only
writer, the panel writes through the same path"), with its own metrics and acceptance criteria; README
and README.id.md were reordered so `init` comes first and the database comes up from `.env`. New guard:
`tests/test_defaults.py` (4 tests) — it caught the placeholder password in the template, which was then
fixed in the same change. Full suite: 541 passed.

## 2026-11-03 — ADR home gains folders; distribution and plugins decided

**Folders** — the ADR home is now categorised: cross-cutting decisions at `docs/v0.1.0/adr/` (the
foundations ADR **ADR-0000**, the ADR home itself `0009`, and the two new distribution ADRs `0027`/`0028`),
engine decisions under `adr/engine/` (25 files, numbers unchanged), and `adr/web/` for the panel surface,
created empty. Numbers stay **chronological and unique across the whole home** (a move leaves a gap —
`engine/` has no `0009` — and a number is never reused), so the root looks sparse on purpose. Every
folder keeps its Indonesian copies in its own `id/` subfolder (`adr/id/`, `adr/engine/id/`), so a copy
always sits beside its source folder.

**Gate** — `scripts/adr.py` is folder-aware: it scans the root plus `engine/` and `web/`, refuses a
number used twice anywhere in the home, refuses a reference that does not resolve (the explicit
`ADR-<folder>/NNNN` form is accepted but no longer needed), checks each footer against the file's own
relative path, mirrors the `id/` copy per folder, and scaffolds with
`--new "<title>" [--dir root|engine|web]`. The register gained the distribution vocabulary (`gateway`,
`tool`, `plugin`, `plugin manifest`, `components`, `digest`, `panel`, `clean by default`,
`operator action`) and the register row for `send` was restored after a bad edit.

**ADR-0027 (distribution, web-first config)** — one image published to `ghcr.io` with `EXTRAS` empty
(spine + core tools only), the web panel as the primary init/configuration surface (a separate process
at the repo root, same config-import path, no second source of truth), **clean by default** for gateway,
tool, and plugin with the code fetched by digest at enable time, and the panel enabled by default once
finished with `web.enabled: false` as the switch. It amends `AGENTS.md` §Shape: the engine is not a
server, the panel is a separate operator app.

**ADR-0028 (plugin model)** — a plugin is an installable unit (`components/<name>/plugin.yaml` +
code, dependencies inside the plugin folder), fetched when enabled, loaded by one engine module through
the existing registries; install/enable/disable/remove are operator actions in the `authorization`
class. Google becomes the first plugin (out of `tools/` and out of `pyproject.toml`'s extras). The three
categories stay distinct: a gateway is a body, a tool is a capability, a plugin is a bundle — none of
them is another, and all three are off by default.

**State** — 29 ADRs (`root=4`, `engine=25`, `web=0`), gate `29/29`, copies `29/29` fresh, `pytest
tests/test_adr.py` 15 passed, `ruff` clean. Still documentation only: no engine file changed.

## 2026-11-03 — ADR home moves into the repo, with an implementation status and a gate

**Home** — the decision set (26 ADRs, `0001`–`0026`) moved out of the working directory into
`docs/v0.1.0/adr/`, indexed by `docs/v0.1.0/README.md`. `AGENTS.md` now lists that home as a tracked
doc and allows the Indonesian copies (each folder keeps them in its own `id/`). ADR-0001 §14 stopped
being a second queue and points at the index; every ADR's footer names its own path.

**Status** — each ADR carries `- **Implementation:** implemented|partial|draft` plus concrete evidence
or the remaining work, so "already done" is verifiable instead of remembered
(`implemented=1 partial=9 draft=16` at the time; `draft=19` after ADR-0027/0028 and the foundations ADR
joined the set).

**Language pass** — every ADR was then read file by file and its English repaired (7 parallel reviewers,
one slice each): literal mistranslations (`Provence` → `provenance`, `refraction` → `reflection`,
`pen` → `the requester`, "model bathing" → `model batching`), word order, articles/tenses, mangled
bold and brackets, broken table rows, and lost checklist markers. 131 comment/example lines inside
code fences were translated too. Invariants were checked mechanically: fenced **code** (comments
stripped) and every backticked identifier/YAML key are byte-identical to the pre-pass snapshot, the
`id/` copies were untouched apart from their hash line, and heading numbers, footers, `ADR-XXXX`/`§N`
references and `Implementation` values are unchanged. What deliberately stays Indonesian: data literals
(prototype sentences, injection examples, Indonesian predicate pairs) and the register's own literal
tokens (`(untuk memories)`, `baris prosa`) that the gate matches.

**Foundations** — **ADR-0000** (initial invariants, at the ADR home root) gathers the three rules that
must precede any new body: surface shape (new trees live outside `eidesoul/`; the package keeps its six
subpackages), language data (per-language lexicon pack under `library/lexicon/`, pronoun default from the
catalog, fixtures out of the package), and the embodiment-neutral seam (`Percept`/`Action` with
body-declared `hints`, the mechanical `chat_id`→`thread_id` rename, and `send`/`chat_history` moving into
a gateway). The gate enforces the folder layout, global chronological numbering, and resolvable
references; the register owns the new terms (`Percept`, `Action`, `body`, `hints`, `lexicon`,
`thread_id`). Status is `draft`: nothing in the engine changed.

**Gate and translator** — `scripts/adr.py` enforces the ADR-0009 §4.4 contract (canonical English
sections, monotonic numbering, fence-aware headings, resolvable `§` references, register synonyms
scoped to definition lines, footer path, `Implementation` enum), scaffolds the next number, and writes
the Indonesian copy inside the source folder's own `id/`. Two backends: `glossary` (default) translates structure only and
counts the content it leaves alone (`konten 0/N`); `opus` translates content with a local NMT model.
The NMT stack is dev tooling, never a package dependency — `pyproject.toml` and `uv.lock` are
untouched, and a missing stack fails with an install hint instead of emitting a half-translated file.
Code fences and inline spans are never sent to the translator; markers (`###`, `>`, `1.`, `-`), table
column counts, and header labels survive, header values are content, and the output cap follows the
model's `max_length` so long paragraphs are not cut off. Measured on the laptop CPU build: one ADR
takes ~70 s with a 1.5 GB peak RSS (four threads, batches of eight pieces) — before those bounds the
same run peaked at 7.5 GB, which is what forced the change. On the CUDA build (`torch 2.15.0+cu132`,
RTX 4060) all 26 ADRs take 3 m 27 s (~8 s each) at a 1.9 GB RSS and 2.9 GB VRAM. An earlier CUDA run
aborted with `indexSelectSmallIndex` because the output cap exceeded the model's 512 positions; the cap
now follows `model.config.max_length` and long paragraphs are split per sentence.

**Language flip** — English became the ADR source (`adr/NNNN-slug.md`) and the human Indonesian text
moved to `adr/id/NNNN-slug.md`, each copy recording its source hash. The English came from
`opus-mt-id-en` with per-piece verification: 5 705 pieces translated, 31 kept their Indonesian source
because the model left them broken. Residue: 416 of 6 652 lines (~6%) still carry Indonesian words
(mostly `Amendments` entries), placeholder/bracket junk at zero, and `--translate-check` keeps every
copy honest. `tests/test_adr.py` runs the gate and proves it rejects a banned synonym, an extra
section after `Code references`, a stale copy, and any attempt to send fenced lines to the backend.

## 2026-11-01 — identity is runtime state, context is assembled state (`/reset` deleted)

**Identity** — `people` keeps the person and the new `person_links` table maps `(gateway, address)`
to an opaque `per_…` id (ten base32 characters, `eidesoul/engine/access/identity.py`). `Inbound` carries
`person_address` and the core fills `person_id` in `Agent.handle`; the WhatsApp ingress and the CLI
gateway stopped computing an identity and `directory.resolve_person` is gone. `roles.*.users` holds
only `per_…` ids (`RoleRegistry.for_person`), so no phone number lives in config any more.

**Linking** — `/link` shows the identity card, `/link <gateway>` issues a six-character code (ten
minutes, five attempts) and `/link <code>` consumes it from the other gateway; `/unlink <gateway>
confirm` detaches one address. The code names the surviving identity: the new gateway's provisional
`per_…` is folded in through `IdentityStore.merge` (chat_people summed, links re-pointed, google rows
re-keyed, `people` fields carried over) and `StyleStore.merge_people` moves the style bucket;
`memory.rekey` moves Mem0 entries. `/whoami` shows `🪪 Identitas: <name> (<per_…>)`.

**Adopt** — `eidesoul people list|link|unlink|adopt` is the operator surface. `adopt` collects every
legacy id (`people`, `chat_people`, the google tables, style buckets, role `users`), resolves the new
identity, folds the old one, rewrites the role documents plus the `roles.yaml` mirror, re-keys memory
when Mem0 is available, and is idempotent.

**Context** — the payload is `assembly.thread`: rows from the archive, the newest tail (floor six,
cap twenty, twelve hundred characters) plus up to three quote anchors, deduped and trimmed to
`engine.prompt.thread_chars` (4000) without ever dropping the floor. `WorkingMemory` is a live merge
buffer of forty rows (`LIVE_BUFFER`) and no longer decides the payload; `through` is gone and the
header reads `Thread (N messages, oldest → newest; local time)`.

**Eras** — the ambient digest archives a dated `chat_eras` row when the new capsule summary shares
under 20% of the old summary's keywords, and `topic_block` then renders `Sebelumnya: …` above the
current topic instead of a wipe.

**Removed** — `/reset` (and its `r`/`clear`/`new` aliases), `HistoryStore.reset`, the `chats.reset_ts`
read filter (the column and its write stay for deployed schemas), `WorkingMemory.reset`, and the
`command.reset.*` catalog keys.

**Verification** — `pytest -q` 519 passed; `ruff check` on every touched file matches its baseline;
a scratch deployment seeded with a legacy owner ran `people adopt` (`6285111242960 -> per_…`),
preserved name/trust/notes/chat_people/style, rewrote `roles.yaml`, and the second run migrated 0.

## 2026-11-01 — neutral core: no gateway or provider vocabulary in `eidesoul/engine`

**Message facts** — `ChatMessage.sender_jid` is `sender_address`, and a new `sender_kind` (`"person"`
when the transport can mention that address, `""` otherwise) carries mentionability. The gateway
computes both (`whatsapp/ingress.py`, `whatsapp/history_sync.py`) and the archive persists the kind in
the message `data` blob next to media, quote, and the visibility flags; the richer-content upsert now
strips `FACT_KEYS` before deciding a row is empty, so a fact alone never overwrites media or a quote.
`_human_rows` labels the address column `sender_address`, which the style and affect seeds read.

**Canonical identities** — `permissions.normalize` is `strip().casefold()` and nothing else: the core
no longer parses phone digits or JID domains. WhatsApp's digit rule moved verbatim to the new
`eidesoul/gateways/whatsapp/identity.py` (`normalize_jid`), used by ingress, directory, history sync,
and media. Roles now match `Inbound.person_id` (falling back to `sender_address`), so `roles.yaml`
keeps its bare-digit owner entries; a gateway that leaves `person_id` empty must list full addresses.
Deletion compares canonical addresses (`mark_deleted(by_address)`, `WorkingMemory`/`HistoryStore`)
and `ingress.revoke_target(ev, gateway)` returns the same canonical address the live path uses, LID
mapped to PN. Mention rendering is `sender_kind == "person"`.

**Chat shape** — `CommandContext.is_dm` (from `Inbound.is_dm`, `True` when there is no envelope)
replaces the `@g.us` suffix test in `/whoami` and `/status`; `ablate.decision_shift` takes `is_dm`
and `scripts/ablate.py` computes it at the call site. Core no longer writes `chats.kind`
(`reset`, `set_chat_name`): only the WhatsApp directory labels a chat, and the column keeps its
`'dm'` default. The long-text cache moved from `workspace/whatsapp/` to `workspace/media/` and the
duplicate WhatsApp `text_path` is gone; old cache files are abandoned and rewritten on demand.
`chat_history` and the prompt scaffolding say "chat name or id" instead of "group/contact name or
JID".

**Provider is the operator's** — `LLMConfig` defaults for `provider`, `base_url`, and `api_key_env`
are blank, `_llm_config` raises `ConfigError` when `engine.llm.provider` is missing or when a
provider without a default key has no `api_key_env`, and the scaffold follows: `eidesoul init
--provider <name>` is required, `templates.ENGINE["llm"]` ships blanks, and `.env` names only the
chosen provider's key variable (`env_file`). Both README quickstarts show the flag.

**Verification** — `pytest -q` 489 passed; `selftest` passes every check except `llm`, which the
provider refused with `429 GoUsageLimitError: Go usage limit exceeded` (external quota, the request
reached `opencode.ai`); `console` shows `/whoami` as `dm "Terminal" (cli:local)` on surface `cli` and
`/status` with the same model and archive counts; `init --provider groq` writes `GROQ_API_KEY=` first
in `.env` and `init` without the flag exits with the argparse "required" error.

## 2026-11-01 — SonarCloud: dead keyword argument and three backtracking regexes

**Bug (S930)** — `scripts/replay.py`, `scripts/grow.py`, and `scripts/research.py` passed
`reasoning_effort="minimal"` to `LLM.complete`, which has no such parameter, so those judge and
summarise calls raised `TypeError` inside best-effort handlers and silently returned nothing. The
argument is gone: effort is operator config (`engine.llm.reasoning_effort`) and no internal call
forces a value.

**Super-linear regex (S8786)** — `character.QUOTED` became `unwrap_quotes` (read the leading quote
run, then the closing run that ends at the last non-space character) and `reply.INLINE_BUBBLE`
became `inline_breaks` (replace one `space-run ---run space-run` at a time, left to right). Both are
linear on an unclosed quote or a long space run; a 600k-case fuzz against the old patterns
reproduces every result exactly. `scripts/xlsx.ATTR` uses a possessive word run
(`(\w\w*+)="([^"]*)"`) so the same scan no longer backtracks.

**Scanner** — `sonar-project.properties` pointed at the phantom `sanka-agent` key; it now carries
`sonar.organization=inimuqsith` and `sonar.projectKey=inimuqsith_SankaAgent`.

**Verification** — `pytest -q` 491 passed, including the new
`test_wrapped_quotes_are_unwrapped_without_backtracking` and
`test_inline_break_scan_keeps_the_marker_rules`.

## 2026-11-01 — v2: providers, guard package, capability spine, affect prototypes, soul resolver

**Providers** — new `eidesoul/providers/` package holding only client/auth/default-model facts for
the OpenAI-compatible endpoints (`opencode`, `openai`, `google`, `openrouter`, `groq`, `deepseek`,
`ollama`, `custom`). `LLMConfig` gained `provider` as its first field, `load_config` resolves the
name into `base_url`/`api_key_env`/`model` before building the config (explicit values still win,
`ConfigError` on an unknown name, `custom` needs a `base_url`), and `api_key()` accepts an empty
`OLLAMA_API_KEY` for a local endpoint. `config.example.yaml` and the `init` template gained the
`provider` key; Gemini is reached through its OpenAI-compatible endpoint.

**Guard package** — `eidesoul/engine/guard.py`, `safety.py`, and `sandbox_guard.py` moved verbatim
into `eidesoul/engine/guard/` as `patterns.py`, `policy.py`, and `sandbox.py`, and the package
`__init__` re-exports the same public symbols, so `from . import guard` call sites in the agent,
ambient, llm, privacy, and affect modules did not change. No shim: the three old modules are gone and
`agent.py`, `tools/__init__.py`, and the two guard test modules import from the package.

**One embedder** — `eidesoul/engine/embed.py` owns the MiniLM model, the 384 dimensions, and the
cosine helper; patterns, facts, affect prototypes, the admin resolver, and Mem0 all share it, so the
two private `_default_encoder` copies and three private cosine implementations are gone. Encoding
runs without progress bars.

**Affect prototypes** — the EmoSense classifier is replaced by multilingual prototype sentences in
`eidesoul/library/affect/{id,en}.json` (`anger, anticipation, disgust, fear, joy, sadness, surprise,
trust`, six casual sentences each). `stores/vibe.py` now loads the bank, scores a message by the max
cosine per label, and turns neutral input into `None` through a calibrated floor and margin
(`0.73`/`0.063`, measured on the fixture sentences: neutral `0.54`, accepted `>= 0.92`).
`VibeConfig` lost `model`, `max_length`, and `neutral_floor`; `scripts/affect.py --check/--stats`
gates the data. The `vibe` extra and the torch/transformers dependency are gone.

**Capability spine** — `mem0ai` and `sentence-transformers` are core dependencies (plus a direct
`torch` so the `pytorch-cpu` index keeps applying; without it the lock pulled the CUDA wheels).
`MemoryConfig.backend`, `MemoryConfig.embedder`, and `EmbedderConfig` are deleted: memory is Mem0 or
nothing, the embedder is the shared MiniLM, and `build_memory` only skips SQLite. The Dockerfile's
`EXTRAS` defaults to empty and `compose.example.yaml` asks for `whatsapp`+`google`. `uv.lock`
regenerated (125 packages, no CUDA).

**Soul and resolver** — `DEFAULT_SOUL_YAML` is identity only (`roles`, `style`, and the `examples`
comment are gone); `load_soul` defaults `roles` to `(role,)` and the persona only lists other roles
when there is more than one. New `eidesoul/admin/library.py` derives the style top-down:
`pack_profiles`, `packs_for(language)`, `suggest(text, packs, use_embedding=...)` (cosine tier with a
floor and margin, then a keyword tier, never a forced pick), `seed_style` for `init`, and
`resolve_soul` which rewrites `style:` while preserving every other key. `eidesoul library resolve
[--explain] [--force]` writes `soul.yaml` and prints `next: eidesoul config import`.

**License and credits** — Apache-2.0 `LICENSE` with `Copyright 2026 Abdul Muqsith`, PEP 639
`license`/`license-files` metadata (setuptools floor raised to 77), an author, and a repository URL.
The README credits list is now one table per language (component, license, author, source, use) and
covers every declared dependency; values come from installed distribution metadata, and packages
without a declared license say so.

**Verification** — `pytest -q` 464 passed; `uv lock --check` clean; `scripts/affect.py --check`
prints `labels=8 ok`; the prototype fixtures classify to `sadness`/`anger`/`joy`/`fear`/`trust` with
`besok rapat jam sembilan` staying neutral; `eidesoul init` writes `style` with `jawatimur` and
`indonesia` and no `roles`/`examples`; `eidesoul library resolve --explain --force` reports
`indonesia facts ... tokens: surabaya` and keeps `jawatimur`; a second call prints `style already
set`; `selftest` OK and `STATUS` present.

## 2026-11-01 — v1: rebrand, soul.yaml, character library, sandbox guard

**Brand** — the project is Eidesoul everywhere: package `eidesoul`, CLI `eidesoul`, default
workspace `.eidesoul/workspace`, lock file `.eidesoul.lock`, LLM session names `eidesoul-*`, the
GitHub repository renamed to `Eidesoul`, and the remote points at
`https://github.com/inimuqsith/Eidesoul.git`. The external SonarCloud project key stays
`sanka-agent` so the existing quality-gate history is preserved.

**Clean cutover** — deleted the compatibility layer instead of carrying it: the config migrator,
the memory_legacy migrator and table, the SQLite importer, the missing-column schema upgrade
(`SCHEMA_VERSION` is now 1), the legacy key/path rejection, and `infer_language`. Existing
databases are not migrated; a v1 deployment starts from a fresh database or an explicit manual
export.

**soul.yaml** — replaced `SOUL.md` with one host file per database. `Soul` now carries `name`,
`description`, `language`, `pronouns`, `gender`, `role`, `roles`, `locale`, `background`,
`persona`, `style`, and `examples`; the loader validates genders, prepends the active role,
validates pack ids, clamps the per-turn counts, and renders the persona in a fixed order. The
`soul` document is authoritative and `soul.yaml` is seed plus mirror; the `persona` document is
gone. `config.yaml` gained the `soul_file` host key and lost its `soul` section.

**Character library** — `eidesoul/data/` became `eidesoul/library/`: the pattern pack is
`library/patterns/jawatimur.json` (+ `_vectors.json`) with `version`, `model`, `language`,
`lexicon`, and `patterns`, and the fact pack is `library/facts/indonesia.json` (+ `_vectors.json`).
Nothing is loaded unless `soul.style` selects a pack, so a fresh install carries no character data.
The register lexicon moved out of the engine into the pack, `select_patterns` filters by an optional
register allow-list, and every `say` line carrying brand text was replaced by a generic line.
`scripts/patterns.py`, `scripts/fakta.py`, and `scripts/grow.py` take `--pack` and keep every write
inside the library.

**Sandbox guard** — `eidesoul/engine/sandbox_guard.py` runs before any sandbox command reaches the
container. Layer 1 is deterministic: host-escape and destructive patterns deny, over-long or
sensitive commands ask for approval, plain read-only commands allow, everything else falls through.
Layer 2 is one small fast classifier call with a timeout; an unusable answer, a timeout, and a
missing model all fail closed. A non-approver role is denied instead of being parked in a pending
approval, `approved=True` (`/approve`) skips both layers, and `engine.guard.enabled: false` allows
everything (documented risk). Only the tool name, chat id, verdict, and reason are logged, never the
command body.

**Optional extras** — `google-api-python-client`, `google-auth`, and `httpx` left the core
dependencies; Google is now the `google` extra with lazy imports, so the tool stays discoverable and
returns `error: Google extra is not installed; run 'uv sync --extra google'.` without it. WhatsApp
stays an optional `whatsapp` extra, is absent from the default and example config, and the default
member role no longer receives the WhatsApp-only `send` tool. The Dockerfile takes `EXTRAS` as a
build argument.

**Prompts and voice** — the engine keeps generic behavior: `VOICE_STYLE` and `CONTINUITY_STYLE` were
removed, `DAILY_STYLE` now asks for the room's register without forcing slang, `PRO_STYLE` closes
with the soul's name, the decision prompt is built from the soul's name/gender/role(s), the
character polish and downgrade prompts take the soul's name, the avatar prompt depends on
`soul.locale`, and the "still <name>" honesty line uses `config.soul.name`. Model-facing payload
markers (deleted, view-once, undecryptable, attachments) are English, and the affect character id is
`soul`.

**Docs** — `README.md` is the English technology-first document, `README.id.md` its Indonesian
translation with explicit anchors, `AGENTS.md` states the English default and the removability
rules, `TASK.md` is the v1 checklist, and this entry replaces the earlier history.

**Verification** — `pytest -q` 447 passed; `python -m eidesoul selftest` OK; `eidesoul init` writes
`soul.yaml` and no `SOUL.md`; `db upgrade` + `config import` + `config export` round-trip the soul
document into `soul.yaml`; `/language en` writes the document and its mirror; `scripts/patterns.py
--check` and `scripts/fakta.py --check` pass on the shipped packs; `uv lock --check` passes.
