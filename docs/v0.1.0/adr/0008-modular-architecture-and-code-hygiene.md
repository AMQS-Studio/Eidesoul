# ADR-0008: Modular Architecture, Code Hygiene, and Honest Budgets — ADR Home, Typed Pipeline, and Vendor Trimming

- **Status:** accepted
- **Date:** 2026-11-05
- **Deciders:** Muqsith (owner)
- **Context:** Re-baselined for v0.1.0; consolidates the ADR home, engine structure, module budgets, and repository slimming (pre-rebaseline 0009, 0031, 0037, 0055) into a unified codebase hygiene standard.
- **Supersedes:** pre-rebaseline 0009, 0031, 0037, 0055
- **Related:** ADR-0001 (sovereign core), ADR-0003 (turn lifecycle)
- **Implementation:** partial — ADR home structure gate is implemented; typed pipeline migration and vendor emotion engine trimming are scheduled in Phase P0/P2.

---

## 1. Context

As the repository expanded to over 400 files and 100,000 lines of code, architectural complexity and
governance bureaucracy began actively degrading developer velocity:
1. **Fake Modularity via `__getattr__`:** To nominally adhere to the 800-line module budget cap,
   methods were split across helper classes (`TurnRunner`, `Delivery`, `TurnIntake`, `TurnSteps`,
   `TurnCommands`) that each implemented `def __getattr__(self, name): return getattr(self.agent, name)`,
   mutating private `Agent` dictionaries across files with zero type safety.
2. **AI Slop Governance & Allowlist Evasion:** While `scripts/selfcheck.py` enforced an 800-line module
   cap, massive modules (`config.py` at 1,724 lines, `affect.py` at 1,160 lines, `cognition.py` at 1,186 lines)
   were exempted via `OVER_CAP_ALLOWED`. Simultaneously, `scripts/selfcheck.py` was failing in clean
   checkouts due to `loop.py` reaching 809 lines.
3. **Bloated Vendored Dependencies:** The codebase included `monoagens/vendor/emotion_engine.py` (4,855
   lines / 179 KB) containing Mandarin parser logic and standalone CLI tools, when MonoAgens only utilized
   basic Pleasure-Arousal-Dominance (PAD) vector decay and appraisal math (~200 lines).
4. **ADR Translation Overhead:** Maintaining 49 duplicate Indonesian ADR translations with line-by-line
   SHA-256 hash checks created excessive ceremony that impeded refactoring.

## 2. Decision drivers

1. **Honest Modularity:** No module caps bypasses via allowlists or dynamic `__getattr__` proxying.
2. **Pipes and Filters Pipeline:** Explicit `TurnRuntime` (immutable dependencies) and `TurnState` (turn
   data) replace God-object mutations.
3. **Radical Trimming:** Vendor code and dead scripts must be replaced with lightweight, readable
   native implementations.
4. **English-Only ADR Documentation:** Architecture records are maintained exclusively in English to
   eliminate translation drift.
5. **Standardized Invariant Testing:** Boundary checks live in pytest (`tests/test_invariants.py`), not
   in fragile policing scripts.

## 3. Options considered

**A. Status Quo (Allowlist Caps and `__getattr__` Splitting).** Rejected: destroys static type
checking, obscures data ownership, and creates maintenance gridlock.

**B. Pure Monolithic Modules.** Rejected: files exceeding 2,000 lines are unreadable and hinder code reviews.

**C. Typed Pipeline Architecture with Honest 800-Line Budget.** Selected: separates state from
runtime, enforces strict 800-line module limits without allowlists, and trims vendored bloat.

## 4. Decision

### 4.1 Architecture Decision Records (ADR Home)

- Documentation home lives in `docs/v0.1.0/adr/` with index at `docs/v0.1.0/README.md`.
- English is the sole source language for all ADRs. Translation copies and gates are retired.
- Monotonic numbering (`0001`..`NNNN`), canonical 10 sections, and structure verification enforced by
  `python -P scripts/adr.py --check`.

### 4.2 Explicit Turn Pipeline Architecture

- `TurnRunner` and helper classes remove all `__getattr__` dynamic delegation.
- Execution is structured as a typed pipeline:
  - `TurnRuntime`: Read-only dependencies passed to stages (`db`, `llm`, `memory`, `outbox`, `gateways`).
  - `TurnState`: Dataclass carrying turn context (`turn_id`, `actor_id`, `inbound`, `decision`, `reviewed_text`).
- Stateful in-flight mappings are consolidated into a dedicated `TurnCoordinator`.

### 4.3 Honest Module Budgets and Cap Enforcement

- Strict 800-line ceiling per Python module. `OVER_CAP_ALLOWED` allowlists are deleted.
- Modules exceeding the limit (`loop.py`, `config.py`, `affect.py`, `cognition.py`) are refactored
  into cohesive single-responsibility submodules.
- Structural boundary invariants are tested directly in `tests/test_invariants.py`.

### 4.4 Vendor Trimming and Script Pruning

- `monoagens/vendor/emotion_engine.py` (4,855 lines) is replaced by a pure ~200-line PAD arithmetic
  module in `monoagens/engine/stores/affect.py`.
- Dead scripts (`scripts/xlsx.py`, `scripts/ablate.py`, `scripts/neutral.py`) are deleted.
- Unused duplicate configurations (`.sonarcloud.properties`) are consolidated into `sonar-project.properties`.

### 4.5 Register istilah kanonik

The following canonical vocabulary binds all configurations, code definitions, and schemas:

| Istilah kanonik | Pemilik | Sinonim terlarang | Catatan |
|---|---|---|---|
| `Percept` | ADR-0001 | `Inbound`, `IncomingMessage` | Kontrak pesan masuk dari gateway |
| `Address` | ADR-0001 | `Target`, `Destination` | Alamat kanonis `(gateway, thread)` |
| `Action` | ADR-0001 | `Outbound`, `Delivery` | Kontrak aksi/pengiriman keluar |
| `administrator` | ADR-0006 | `admin`, `owner` (nama role) | Peran terlindungi tertinggi |
| `memories` | ADR-0002 | `facts`, `memory_store` (kolom) | Tabel kanonis memori |
| `mode` | ADR-0006 | `role` (dalam soul.yaml) | Mode persona kepribadian agen |

## 5. Configuration

Configured in `pyproject.toml` and `.ruff.toml`:
```toml
[tool.ruff]
line-length = 100
target-version = "py314"
```

## 6. Metrics and gates

- `python -P scripts/adr.py --check` passes across all active ADRs.
- `scripts/selfcheck.py` (or `test_invariants.py`) verifies zero modules exceed 800 lines.
- Static analysis with `ruff check` and `pyright` passes with zero errors.

## 7. Acceptance criteria

- [ ] All ADRs reside in English under `docs/v0.1.0/adr/` and pass validation.
- [ ] No `__getattr__` proxying remains in `monoagens/engine/turn/`.
- [ ] `monoagens/vendor/emotion_engine.py` is removed; emotion tests pass on the lightweight PAD module.
- [ ] No module allowlist exists in `selfcheck.py`.

## 8. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Refactoring large modules breaks existing behavior | Comprehensive regression tests verify parity before and after file splits |
| PAD simplification alters emotional drift | Parity tests on emotion vectors run against identical appraisal test cases |

## 9. Interaction with other ADRs

- **ADR-0001:** Enforces clean separation between engine core and gateway adapters.
- **ADR-0003:** Integrates the typed pipeline into the checkpointed turn lifecycle.

## 10. Code references

- `scripts/adr.py` — ADR structure and synonym validation gate.
- `monoagens/engine/turn/` — Staged turn execution pipeline.
- `monoagens/engine/stores/affect.py` — Native PAD emotion state transitions.

---

*This ADR lives at `docs/v0.1.0/adr/0008-modular-architecture-and-code-hygiene.md`.*
