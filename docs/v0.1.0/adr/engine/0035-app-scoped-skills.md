# ADR-0035: App-Scoped Skills — A Namespace Above `scope`, Not a Fourth Audience

- **Status:** accepted
- **Date:** 2026-11-04
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Context:** extends ADR-0020 (procedural memory), ADR-0018 (body facts), ADR-0006 (audience ceiling); follows ADR-0033 (neutral core) and ADR-0034 (prompt parts, draft)
- **Supersedes:** —
- **Related:** ADR-0031 (schema and module budgets), ADR-0016 (migrations), ADR-0027/0028 (plugin unit), ADR-0032 (address and body)
- **Implementation:** draft — the columns, the hint, and the visibility rules are decided here; skills still carry only `global|thread|person`.
- **Location note:** an engine decision (it extends procedural memory and the inbound contract), so it sits under `adr/engine/`. The Indonesian copy is `docs/v0.1.0/adr/engine/id/0035-app-scoped-skills.md`.

---

## 1. Context

A skill is procedural memory: derived from an episode or imported by an operator, visible under one of three audiences, and injected into the prompt by relevance inside a budget (ADR-0020). The audience is explicit:

- `SkillStore._visible` returns global for everything, thread when `stated_by` is the current thread, and person when `stated_by` is the current person (`stores/skills.py:164-170`).
- `skills.scope` is constrained to `('thread','person','global')` (`schema.py:485`).
- `retrieve()` receives the query, thread, person, and the tool catalog; it has no notion of an application (`stores/skills.py:205-240`), and the agent passes no such value (`agent.py:1309-1316`).
- The learned path writes the scope from the episode: a thread episode yields `scope=thread`, a global one yields `scope=global` (`memory/episodes.py:176`, `jobs/cognition.py:948-980`, `:1069-1078`). There is no application provenance on `turns` or `episodes` (`schema.py:827-845`, `:854-878`).
- A body can already tell the engine facts through `Inbound.hints` (`dm`, `mention`, `reply`, `group`, `presence`, `self`, `roles`; `inbound.py:13-20`). There is no `app` key.
- Workarounds exist and are real: a plugin can keep its own pack and expose a tool that filters by `ctx.gateway.name`; a thread-scoped import auto-injects in one fixed thread; a skill whose body names a tool the current body does not hold is skipped (`stores/skills.py:224-229`). None of them expresses "this corpus belongs to app A" while still auto-injecting across every thread of app A.
- One body can host several applications (an HTTP gateway with tenants) and one application can span bodies (WhatsApp plus web). "Body" is therefore the wrong axis for a corpus.

## 2. Decision drivers

1. **Corpus and audience are different questions.** "Which app owns this procedure" is orthogonal to "who may see it".
2. **Body is transport.** A body may serve many apps and an app may span bodies; the app identity must come from the body as data, not from the core knowing a gateway name.
3. **Fail closed.** A turn without an app sees shared skills only; an app-scoped skill never leaks into another app because the hint was missing.
4. **Learning keeps provenance.** A procedure learned inside app A stays in app A, and two runs with the same tools in different apps must not merge into one skill.
5. **One memory path.** Skills remain ADR-0020 procedural memory; the namespace is a column, not a second store.
6. **No app literal in core.** App names are data written by operators and bodies; the engine validates their shape and never defaults to one.

## 3. Options considered

**A. Add `scope='app'`.** Rejected: one enum cannot express "app A, and only this person" or "app A, and only this thread"; `stated_by` would mean a thread, a person, or an app depending on the value. Cheap, but it caps the model at app-wide visibility.

**B. Make `body` the namespace.** Rejected: body is a transport and a repository of tenant routing. One app across WhatsApp and the web would split its corpus; one HTTP body serving two tenants would merge them.

**C. Plugin-owned packs only.** Accepted as a complementary path for large or externally indexed corpora, not as the model: it cannot auto-inject, and it makes every app responsible for re-implementing retrieval, priority, and decay.

**D. A nullable `app` namespace above `scope`, fed by an `app` hint (chosen).** One column per record, two filters at read time, and the existing scope logic untouched.

**E. Thread-per-app convention.** Accepted as a migration bridge only: it works today with no schema change, but it forces one thread per app and cannot cover an app with many conversations.

## 4. Decision

### 4.1 The data

Three nullable columns, one index, one migration (`0017`):

```text
turns.app     String(40) NOT NULL DEFAULT ''   -- provenance of one turn (body hint)
episodes.app  String(40) NOT NULL DEFAULT ''   -- copied from the first turn of the episode
skills.app    String(40) NOT NULL DEFAULT ''   -- the corpus the procedure belongs to
Index("ix_skills_app_scope", skills.c.app, skills.c.scope)
```

`''` means shared (no app). App names are data: `^[a-z0-9][a-z0-9-]{0,63}$`, validated where a skill is written. No engine default exists and no app name appears in code.

`turns.app` is written once at intake from `Inbound.hints["app"]` (`stores/history.py:273-283` gains `app=""`; `agent.py` passes the hint). A body that omits the hint writes `''`; existing rows backfill to `''` in the migration.

`episodes.app` is copied from the episode's first turn (`memory/episodes.py:90-122`). `_signature()` includes `app=<value>` (`:158-177`), so two runs of the same tools and targets in different apps do not share a procedure. A shared run and an app run also differ.

`skills.app` is written by `save`, `revise`, and the import command. The reflection path copies `episode.app`; promote and merge preserve the existing value.

### 4.2 Visibility

Read order is app first, audience second:

```python
if skill.app and skill.app != current_app:
    return False
```

The existing rule (`global`, `thread` by `stated_by`, `person` by `stated_by`) then applies unchanged.

`current_app` is the turn's hint, not the body name. A missing or unknown app hides every app-scoped skill and keeps shared ones visible; that is the fail-closed direction.

`retrieve()` gains `app: str = ""` and `_visible()` gains the same argument; the agent passes `inbound.hints.get("app", "")`. The budget, priority, usage decay, and injection format do not change.

### 4.3 The write path

- `SkillStore.save(..., app="")` and `revise(..., app=None)`; `revise` keeps the current value when the argument is `None`.
- `/skill import <path>` accepts `app=<name>`; without it the skill is shared. `/skill save` accepts the same token.
- A plugin imports through `ctx.skills` and passes `ctx.app`.
- `ToolContext` gains `app` (from the same hint), so a plugin tool filters by an explicit fact instead of reading `ctx.gateway.name`.

### 4.4 The body contract

`Inbound.hints["app"]` is the body's answer to "which application is this message for". The key joins the documented hint list in `inbound.py`; it is optional. A body may derive it from its own configuration (a route, a group mapping, a tenant id) and never from a core table.

A gateway or plugin may keep its own pack and index. When it wants the engine's auto-injection, it writes into the core skill table with the same `app` it reports in the hint.

### 4.5 What does not change

- `scope` keeps three values; no fourth audience is introduced.
- Skills remain procedural memory with revisions, trust, priority, usage, and decay.
- Plugin prompt parts stay static (ADR-0034); this ADR adds no dynamic prompt surface.
- The plugin path for large packs remains valid; it is complementary, not replaced.

## 5. Configuration

No engine key. The only new data surfaces are the body hint `app` and the import/save token `app=<name>`. A gateway may expose its own setting (for example `gateways.<name>.app`) and translate it into the hint; the engine never reads a gateway's configuration for this.

## 6. Metrics and gates

Counts to drive to zero, measured 2026-11-04:

| Gap | Count | Where |
| --- | --- | --- |
| audience values that cannot carry app | 1 enum, 3 values | `schema.py:485` |
| records without app provenance | 3 tables | `turns`, `episodes`, `skills` |
| retrieval call sites without app | 1 | `agent.py:1309` |
| documented hint keys without app | 1 list | `inbound.py:13-20` |

The gate:

1. A skill written with `app='a'` is absent from the prompt of a turn whose hint is `b` or missing, and present for every thread of `a` when its scope allows it.
2. A shared skill (`app=''`) is present in every app.
3. `app='a'` combined with `scope='person'` is visible only to that person inside `a`; combined with `scope='thread'` only in that thread of `a`.
4. Two episodes with the same tools and targets but different `turns.app` produce different signatures and do not merge.
5. A reflection candidate from an episode of `a` is stored with `app='a'`; a shared episode stores `''`.
6. A turn without the hint never fails and never sees an app-scoped skill.
7. The neutral gate finds no app name literal in `eidesoul/engine/**`; only the pattern constant exists.
8. `tests/test_migrations.py` covers `0017`; `pytest -q` and the prompt contract suite stay green.

## 7. Acceptance criteria

- `skills.app`, `episodes.app`, and `turns.app` exist with the stated defaults and index; the migration backfills `''`.
- The visibility matrix in §6 passes as a table-driven test in `tests/test_skills.py` and `tests/test_prompt_contract.py` (a case per app).
- `retrieve(..., app=...)` is the only read path; no caller omits it inside the engine.
- `/skill import app=a` and `/skill save app=a` work; without the token the skill is shared.
- A plugin tool receives `ctx.app` and can filter its own pack by it.
- Deleting an app name from data needs no code change; adding one needs no migration.
- The prompt budget, priority, and decay behave exactly as before for shared skills.

## 8. Risks and mitigations

- **A body forgets the hint and app skills disappear.** Mitigation: fail closed is deliberate; `/skill list` and the import result name the app, and a missing hint is visible in the turn row (`turns.app=''`).
- **App names sprawl or collide by case.** Mitigation: one pattern, lowercased before write, no aliases; an app name is not a role and grants nothing.
- **Cross-app leakage through learned procedures.** Mitigation: app is part of the episode signature and copied on every write; promote and merge preserve it.
- **A migration on three tables under load.** Mitigation: nullable columns with a server default, one index built in the same revision, no rewrite; existing rows read `''`.
- **Two models of the same corpus (core skills and a plugin pack) drift.** Mitigation: the rule is explicit — either the plugin writes into core with `app`, or it serves its own pack and does not expect auto-injection. It never does both for one corpus.
- **The hint becomes a privilege.** Mitigation: it is not; roles still decide tools and permissions. The app hint only selects a corpus.

## 9. Interaction with other ADRs

- ADR-0020: skills keep their meaning; app is provenance, scope stays audience. The signature change is the one behavioural adjustment.
- ADR-0018: `app` joins the open hint map, supplied by the body, never guessed by the engine.
- ADR-0006: app is routing, not a privacy level; the audience ceiling still decides which entries may be disclosed.
- ADR-0034: prompt parts stay static; this ADR does not add a dynamic prompt surface.
- ADR-0027/0028: a plugin may keep its own pack; when it wants engine injection it writes through `ctx.skills` with the app it reports.
- ADR-0016: one migration, one index, no compatibility shim.
- ADR-0031: three nullable columns and one index; no new module.

## 10. Code references

- `eidesoul/engine/schema.py:467-489` — `skills`, the scope CHECK, and the scope index; `:827-845` episodes; `:854-878` turns.
- `eidesoul/engine/stores/skills.py:164-170,188-260` — `_visible`, `_mentions`, `_vector`, `retrieve`, `_inject`.
- `eidesoul/engine/stores/skills.py:303-330` — `view`/`read`; the operator path that must name the app too.
- `eidesoul/engine/agent.py:1306-1316` — the single retrieval call that will pass `hints["app"]`.
- `eidesoul/engine/stores/history.py:273-283` — the intake transaction that records `turns.app`.
- `eidesoul/engine/memory/episodes.py:90-122,158-177` — episode start and signature.
- `eidesoul/engine/jobs/cognition.py:948-980,1069-1078` — promote/merge and the distill write that must copy the app.
- `eidesoul/engine/inbound.py:13-20` — the documented hint keys.
- `eidesoul/engine/tools/__init__.py:28-100` — `ToolContext`, which gains `app`.
- `tests/test_skills.py`, `tests/test_prompt_contract.py`, `tests/test_migrations.py` — the three suites this decision extends.

---

*This ADR lives at `docs/v0.1.0/adr/engine/0035-app-scoped-skills.md`.*
