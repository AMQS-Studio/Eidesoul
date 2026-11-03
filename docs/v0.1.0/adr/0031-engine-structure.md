# ADR-0031: Engine Structure — Layout, Growth Budget, and Toolchain Baseline

- **Status:** accepted
- **Date:** 2026-11-03
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Context:** follows ADR-0009 (the ADR home and its gate), ADR-0016 (schema and migration form), ADR-0027 (distribution and default-off capability), ADR-0028 (plugin unit)
- **Supersedes:** —
- **Related:** ADR-0000 (surface shape), ADR-0001 (one memory port), ADR-0032 (the delivery door lands after this split)
- **Implementation:** implemented — top level 16 files (the clock joined in ADR-0033); 25 modules in `turn/`, `character/`, `access/`, `jobs/`; `ablate.py` moved out; `agent.py` 1,800 lines, `automation.py` 1,953, `memory/store.py` 2,399; Python floor 3.14 everywhere; 0 `from __future__ import annotations`; `[dependency-groups] dev`; ruff `RUF/ASYNC/PT/FA`; the pyright baseline is committed (`scripts/types-baseline.json`).
- **Location note:** cross-cutting (it governs every engine surface), so it sits at the root of the ADR home. The Indonesian copy is `docs/v0.1.0/adr/id/0031-engine-structure.md`.

---

## 1. Context

The engine holds 100 modules (excluding `__init__.py`) and 32,835 lines. Measured on 2026-11-03:

- 41 files sit flat at the top of `eidesoul/engine/`, ordered alphabetically, so unrelated domains interleave: `ablate.py` above `agent.py`, `approvals.py` between `ambient.py` and `assembly.py`.
- Three modules carry 22% of the tree: `memory/store.py` (2,785), `agent.py` (2,215), `automation.py` (2,106). Four more are above 800 lines (`config.py` 1,776, `stores/affect.py` 1,285, `cognition.py` 1,164, `schema.py` 954).
- The code is not bloated: 84% of the lines are code, 11% blank, 5% docstring or comment; an 8-line clone scan finds 21 repeated windows (about 170 lines); the only module not on a runtime path is `ablate.py`, a research tool driven by `scripts/ablate.py`.
- 60 of the 100 modules live in five folders with a fixed contract: 12 tools and 22 commands are one file per unit (folder discovery, never a registry edit), 9 stores hold persistence, 3 guard modules and 14 memory modules each have one reason to change.
- The toolchain is a step behind the language: `requires-python` says 3.11 while the machine runs 3.15, 138 of 148 modules carry `from __future__ import annotations`, no type checker is configured, and development tooling sits in an optional extra instead of a dependency group.

So the problem is not the number of files. It is that navigation mixes domains, and that three files concentrate every change. A second pressure is new: agentic coding tools read the repository tree as context, and this repository already ships `AGENTS.md`; a flat alphabetical tree makes that context worse, not better.

## 2. Decision drivers

1. **One reason to change per module** (information hiding, Parnas 1972). A module earns its own file only when a different force can change it.
2. **Evidence on module size is two-sided.** Defect density has a minimum at an intermediate size; very small modules pay interface cost, very large modules pay comprehension cost (Malaiya and Denton 2000; Koru et al. 2009). The response is to cap the top, not to shatter the bottom.
3. **Delivery friction tracks coupling, not file size.** Two modules that always change together are one module in disguise (CodeScene change coupling).
4. **No new abstraction layers.** The engine is not a server and not a framework; a layer nobody asked for is a liability.
5. **Every rule must be checkable without a model.** The gate is `pytest -q`, `scripts/adr.py --check`, `scripts/adr.py --translate-check`, and `scripts/selfcheck.py --check`.
6. **The tree is agent context.** `AGENTS.md` is the interoperable context file across tools (Galster et al. 2026); the layout it describes must stay legible and stable.

## 3. Options considered

**A. Status quo: fix nothing, split only when a file hurts.** Zero churn today, but the 41-line flat list stays, and every new feature adds another top-level file.

**B. Merge aggressively into fewer, larger files.** Minimizes file count, but raises every stateful module past the size where comprehension cost dominates, and contradicts driver 2.

**C. Split the three concentrated modules, group the rest into domain folders, and cap growth (chosen).** Mechanical moves, no behavior change, each step independently green.

**D. Rewrite into layers (services, repositories, use cases).** Adds a layer the engine does not need; the seams that matter already exist (`Handler`, `Percept`/`Action`, the gateway protocols).

**E. Rename with domain prefixes instead of folders (`turn_agent.py`).** Changes every name and keeps the flat list; churn without structure.

## 4. Decision

### 4.1 The module rule

A module has one reason to change. A stateful module stays at or below **800 lines**; a module above that needs a line in the ADR that introduces it. The discovery folders keep their contract: one file per tool in `tools/`, one file per command in `commands/`, persistence in `stores/`, guards in `guard/`, memory in `memory/`. Those rules are not relaxed by this ADR; they are the reason the core stays small.

### 4.2 Split in place first (bucket 1)

No new folders yet, because a split and a move in the same commit hide both diffs.

- Delivery (send, notice, typing, media) moves from `agent.py` into `reply.py`, which already owns text shaping. Target: `agent.py` about 1,750 lines.
- Capability counters move from `agent.py` into `runtime.py`, which already holds the runtime facts. Target: `runtime.py` about 180 lines.
- Pure SQL for canonical memory moves from `memory/store.py` into a new `memory/queries.py` (functions that take a connection). `MemoryStore` stays the single port (ADR-0001); only the statements move. Target: `store.py` below 2,400 lines.
- `agent.py` and `automation.py` keep one delivery path instead of two (the second copy is the same chunk, outbox, and attribution logic). See ADR-0032 for the door itself.
- Three routing copies become one resolver (also ADR-0032).

`memory/queries.py` is the only new engine module in this bucket. It is allowed because it removes statements from the port without moving the port.

### 4.3 Then group by domain (bucket 2)

25 modules move; `ablate.py` leaves the engine for `scripts/`. The top level drops from 41 files to 15, and `clock.py` joins as the sixteenth when ADR-0033 §4.13 requires one `now(tz)` helper.

```text
eidesoul/engine/
  __init__.py
  agent.py                      # the handle: one turn, one orchestration
  inbound.py  plugins.py  registry.py  llm.py
  config.py  db.py  schema.py
  embed.py  i18n.py  io.py  runtime.py  soul.py  self_knowledge.py
  clock.py                      # one clock: now(tz), day buckets (ADR-0033 §4.13)

  turn/                         # one message becomes one reply
    context.py  decision.py  assembly.py  salience.py
    prompts.py  reply.py  observability.py
  character/                    # persona data resolution
    character.py  knowledge.py  lexicon.py  patterns.py
  access/                       # who may do what
    roles.py  permissions.py  identity.py
    privacy.py  approvals.py  relay.py  target.py
  jobs/                         # background work and lifecycle
    cognition.py  automation.py  supervisor.py  lease.py
    ambient.py  currents.py  mcp.py

  memory/  stores/  tools/  commands/  guard/     # unchanged layout
```

`__init__.py` becomes a written map of the tree; it stops importing subpackages eagerly, because callers already import the module they need.

### 4.4 Change mechanics

Each folder is one commit, and each commit is a pure move: `git mv`, then import rewrite, then ADR path references, then the gate. No reformatting, no behavior change, no renamed symbols. The 831-test suite is the safety net; a red gate reverts the commit, never the test.

Order: split (4.2), then folders (4.3), then the core work in ADR-0032. The split changes file contents; the folders change paths. Doing them in this order rewrites the ADR path references once, after the new modules exist.

### 4.5 Growth budget

The engine module count is a budget, not an accident. A new engine module is allowed when it replaces one, splits a module that is over 800 lines, or the ADR that introduces it names the reason. A feature extends the module that owns its domain. This is the rule that keeps the next eight ADRs from adding another eight top-level files.

### 4.6 Mirrors are generated, not copied

`admin/templates.py` writes the default configuration; today it repeats the dataclass defaults by hand. It derives them from the dataclass tree instead, the same tree `self_knowledge.py` already walks. A mirror that cannot drift is better than a mirror that is checked.

`scripts/adr.py` gains `--stamp`: after an English ADR is edited, the tool writes the `sha256` line of its Indonesian copy. The copy stays hand-written (ADR-0009); only the arithmetic is automated.

### 4.7 Toolchain baseline

The language floor and the checkers are part of the structure, because they decide what can be written where.

- **Python floor 3.14.** `requires-python = ">=3.14"`, ruff `target-version = "py314"`, and the image and `install.sh` follow. On 3.14 annotations are evaluated lazily (PEP 649/749), so `from __future__ import annotations` is removed from every module and a structure test forbids it again; ruff's `FA` family only *adds* the import, so it cannot enforce this by itself.
- **Dependency groups (PEP 735).** Development tooling moves to `[dependency-groups] dev`; `[project.optional-dependencies]` keeps only runtime options (`whatsapp`). The lockfile is regenerated in the same commit.
- **A type checker starts as a ratchet, not a wall.** `pyright` runs with `basic`, and a committed baseline records the error count per directory; the gate fails when a directory grows. `strict` is not enabled in one step, because `agent.py` and `automation.py` would be the first wall.
- **Ruff gains `RUF`, `ASYNC`, `PT`, and `FA`.** `C90` and `PLR0912` stay: they are the mechanical guard for the size rule in §4.1, not a style preference.
- **`TaskGroup` only where sibling cancellation is intended.** The `asyncio.gather(..., return_exceptions=True)` sites keep their semantics; structured cancellation changes failure behavior and is not a style upgrade.
- **`slots=True` on the hot dataclasses** (`Percept`, `Action`, `Handler`), and nowhere else by default.

None of this changes a behavior; it changes what the gate can see.

## 5. Configuration

No runtime configuration key. The toolchain lives in `pyproject.toml` only: `requires-python`, `[tool.ruff]` (`target-version`, rule selection), `[dependency-groups] dev`, `[tool.pyright]`, and its baseline file. Structure is code, not a knob; the reported values are the measurements in §6, printed by `scripts/selfcheck.py`.

## 6. Metrics and gates

| Metric | Today | Target after this ADR |
|---|---|---|
| Engine modules (excluding `__init__`) | 100 | 101 (`memory/queries.py`) |
| Top-level engine files | 41 | 15 |
| Modules above 2,000 lines | 3 | 1 (`memory/store.py`, the port) |
| Modules above 800 lines | 7 | 7: the three above 2,000 shrink and none is added |
| Import lines rewritten once | — | about 226 (turn 85, character 34, access 69, jobs 37) |
| ADR path references updated once | — | about 126 lines |
| Repeated 8-line windows | 21 | not a gate; reported only |
| Python floor | `>=3.11`; image and install script pin 3.12 | `>=3.14` everywhere |
| `from __future__ import annotations` | 138 of 148 modules | 0, enforced by a structure test |
| Development dependencies | `dev` in `[project.optional-dependencies]` | `[dependency-groups] dev` (PEP 735) |
| Type-check errors | not measured | a committed per-directory baseline that never increases |

Gates, after every commit of the sequence: `pytest -q` on the throwaway Postgres+pgvector store, `ruff check`, `python scripts/types.py --check`, `python scripts/adr.py --check`, `python scripts/adr.py --translate-check`, `python scripts/selfcheck.py --check`.

## 7. Acceptance criteria

- `agent.py`, `automation.py`, and `memory/store.py` are below their targets, and no module exceeds 800 lines except the ones listed in §6.
- The top level of `eidesoul/engine/` holds only the modules shown in §4.3.
- `grep -rn "components\.\|ComponentSettings"` stays empty; no symbol is renamed by the move.
- Each of the five commits is green on all four gates, and the 831 tests pass before and after.
- `scripts/selfcheck.py --check` prints the engine module count and any module above 800 lines.
- `AGENTS.md` carries the target tree so the rule and the code agree.
- The suite runs on 3.14 or newer, no module imports `__future__.annotations`, and a structure test fails when one does.
- `uv sync --locked` installs the dev group from `[dependency-groups]`, and no `dev` extra exists.
- `scripts/types.py --check` fails only when a directory exceeds its recorded baseline; lowering a baseline is its own commit.

## 8. Risks and mitigations

- **Import churn breaks scripts and tests.** Pure moves, one folder per commit, and the suite as the gate.
- **ADR paths go stale.** The path references are rewritten in the same commit, and `--translate-check` fails the moment a copy drifts.
- **`git blame` becomes less useful.** `git mv` without reformatting keeps rename detection.
- **Over-splitting after the cap.** The evidence cuts both ways; the 800-line cap is a ceiling, not a target, and the bottom is left alone.
- **A moving tree confuses agents.** `AGENTS.md` and `eidesoul/engine/__init__.py` carry the map, and the folders are stable after one landing.
- **The floor bump strands an old image or venv.** The Dockerfile, `config.yaml`, `install.sh`, and both READMEs change in the same commit; a 3.13 interpreter now fails fast with one message.
- **A ratchet baseline hides new debt inside an old count.** The script records per directory, not one global number, and lowering is a commit of its own.
- **A new rule set floods the gate.** Rules land with `--fix` first, remaining findings are triaged once, and a rule that cannot be satisfied is ignored explicitly with a reason.

## 9. Interaction with other ADRs

ADR-0009 governs where these decisions live and gates the copies. ADR-0016 is untouched: no schema change, no migration. ADR-0027 and ADR-0028 keep their boundaries; the plugin and gateway folders are not moved. ADR-0001 keeps the single write port, which is why `memory/store.py` stays one object and only its statements move to `memory/queries.py`. ADR-0032 depends on this split: the delivery door replaces code that first has to live in `reply.py` alone.

## 10. References

- D. L. Parnas, *On the Criteria To Be Used in Decomposing Systems into Modules*, CACM 15(12), 1972. doi:10.1145/361598.361623 — one reason to change per module.
- Y. K. Malaiya and J. Denton, *Module Size Distribution and Defect Density*, ISSRE 2000. <https://www.cs.colostate.edu/~cs530dl/s25/8b3modsize.pdf> — defect density has a minimum at an intermediate module size.
- A. G. Koru et al., *An Investigation into the Functional Form of the Size-Defect Relationship for Software Modules*, IEEE TSE 35(2), 2009 — smaller modules are proportionally more defect prone; do not shatter.
- CodeScene, *How to assess coupling health* (change coupling). <https://helpcenter.codescene.com/en/help/articles/1844075-how-to-assess-coupling-health-in-codescene> — delivery friction comes from how code changes together.
- B. Ahmeti, M. Linder, R. Groner, R. Wohlrab, *Architecture Decision Records in Practice: An Action Research Study*, ECSA 2024. <https://rebekkaa.github.io/files/2024_ECSA.pdf> — decisions kept in the repository beside the code are the ones that stay useful.
- M. Galster et al., *Harness Engineering for Agentic AI Coding Tools: An Exploratory Study*, arXiv:2602.14690, 2026. <https://arxiv.org/abs/2602.14690> — `AGENTS.md` is the interoperable context file; repository layout is part of that context.
- M. Borg et al., *Echoes of AI: Investigating the downstream effects of AI assistants on software maintainability*, EMSE 31:161, 2026. doi:10.1007/s10664-026-10889-1 — no systematic maintainability penalty was measured, while code bloat from generation remains the named risk; the budget in §4.5 is the counterweight.
- Python 3.14, *What's New* (PEP 649/749, deferred evaluation of annotations). <https://docs.python.org/3/whatsnew/3.14.html>
- PEP 735, *Dependency Groups in pyproject.toml*. <https://peps.python.org/pep-0735/>
- Ruff, *Rules* — the `FA` family adds the future import, it does not ban it. <https://docs.astral.sh/ruff/rules/>
- Pyright, *Configuration* (`basic` and `strict`, per-directory `include`). <https://microsoft.github.io/pyright/#/configuration>

## 11. Code references

- `eidesoul/engine/agent.py` — the handle; delivery and counters leave it in §4.2.
- `eidesoul/engine/turn/reply.py` — receives the delivery helpers.
- `eidesoul/engine/runtime.py` — receives the capability counters.
- `eidesoul/engine/memory/store.py` — the single port; statements move to `memory/queries.py`.
- `eidesoul/engine/jobs/automation.py` — second delivery copy removed in §4.2.
- `eidesoul/admin/templates.py` — defaults derived from the dataclass tree in §4.6.
- `scripts/adr.py` — `--stamp` in §4.6.
- `scripts/selfcheck.py` — reports the module count and over-size modules in §6.
- `pyproject.toml` — floor, ruff selection, dependency group, and pyright config in §4.7.
- `scripts/types.py` — new file (proposal, not created yet): the per-directory type-check ratchet in §4.7.
- `tests/test_architecture.py` — bans `__future__.annotations` in §4.7.

---

*This ADR lives at `docs/v0.1.0/adr/0031-engine-structure.md`.*
