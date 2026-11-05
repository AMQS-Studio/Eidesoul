# ADR-0008: Modular Architecture, Code Hygiene, and Honest Budgets — ADR Home, Typed Pipeline, and Testing Discipline

- **Status:** accepted
- **Date:** 2026-11-05
- **Deciders:** Muqsith (owner)
- **Context:** Re-baselined for v0.1.0; consolidates the ADR home, engine structure, module budgets, repository slimming, config/language hygiene, and real-seam testing discipline (pre-rebaseline 0009, 0031, 0037, 0049, 0053, 0055).
- **Supersedes:** pre-rebaseline 0009, 0031, 0037, 0055
- **Related:** ADR-0001 (sovereign core), ADR-0003 (turn lifecycle), ADR-0005 (extensions)
- **Implementation:** implemented — English-only ADR home and structure gate, typed turn pipeline, module cap enforced with an empty allowlist, unknown config/soul key rejection, and the vendored affect engine replaced by native PAD math.

---

## 1. Context

As the repository grew to 413 files and ~101,000 lines, artificial file fragmentation, bypassed quality
gates, and mock-heavy tests created significant maintenance friction:
1. **Fake Modularity via `__getattr__` & Broken Module Cap (`M-01`):**
   - To keep `agent.py` under 800 lines, turn logic was split across five helper classes (`TurnRunner`,
     `Delivery`, `TurnIntake`, `TurnSteps`, `TurnCommands`) that each defined
     `def __getattr__(self, name): return getattr(self.agent, name)`, mutating 9 private `Agent`
     in-flight dictionaries across files without static type safety.
   - `scripts/selfcheck.py` failed on clean checkouts because `monoagens/engine/turn/loop.py` reached
     809 lines (cap 800), while `config.py` (1,724 lines), `affect.py` (1,160 lines), `cognition.py`
     (1,135 lines), and `__main__.py` (1,084 lines) were exempted via `OVER_CAP_ALLOWED`.
2. **Bloated Vendored Code & Disconnected Stores (`BI-05, BI-19, M-03`):**
   - `monoagens/vendor/emotion_engine.py` carried 4,855 lines (179 KB) of Mandarin keyword parsers and
     CLI code when `AffectStore` only used ~200 lines of PAD vector decay, appraisal, and relation math.
   - `StyleStore.learn` (`stores/style.py:216-283`) was never called on the live turn path, leaving
     adaptive style profiles static.
   - `monoagens/engine/turn/observability.py` expected `extra={"event": ...}` which zero callers
     supplied, and `monoagens/engine/jobs/schedule.py` existed without declaration in architecture docs.
   - `scripts/xlsx.py` (unused) and `scripts/ablate.py` (broken `max_index_chars` attribute) lingered
     alongside duplicate `.sonarcloud.properties` and `sonar-project.properties` files, while
     `scripts/neutral.py` banned Indonesian stopwords in code and inadvertently caused `maintenance.py`
     to omit stopword filtering.
3. **Dead/Redundant Config Keys & Hardcoded Indonesian Strings in Engine:**
   - `load_config` silently ignored unknown keys, hiding four dead keys in `config.yaml`:
     `engine.memory.capture_min_chars`, `engine.skills.max_index_chars`,
     `engine.ambient.context_enabled`, and `gateways.cli.role`.
   - `config.yaml` split `TurnConfig` across two redundant sections (`engine.turn` and `engine.turns`),
     and `soul.yaml` carried a redundant master switch `voice.enabled: false` that silently disabled
     `affect`, `style`, `character`, and `humanize` even when their own `enabled: true` flags were set.
   - Three hardcoded Indonesian strings leaked into `monoagens/engine/`: `turn/delivery.py:171`
     (`"(file terkirim: ...)"`), `llm.py:421` (`"- {tool} ke {target}: ..."`), and `turn/loop.py:283`
     (`log.info("diam chat=%s ...")`).
4. **"The Green Test Illusion":**
   - 922 tests passed 100% while critical production paths were broken because tests checked AST syntax
     rather than runtime behavior (`test_no_blocking_db_block_inside_an_async_def` passed when `with db.connect():`
     was wrapped in a synchronous helper called directly from `async def`), mocked broken seams
     (replacing `AutomationStore.propose` with a 3-arg lambda and testing `send_to` only from WhatsApp
     to WhatsApp), and queried memory only with single exact keywords rather than natural sentences.

## 2. Decision drivers

1. **Honest Modularity:** Enforce the 800-line module ceiling without `OVER_CAP_ALLOWED` allowlists or
   `__getattr__` proxy delegation.
2. **Explicit Typed Turn Pipeline:** Replace God-object attribute mutation with `TurnRuntime`
   (immutable dependencies), `TurnState` (typed per-turn data), and `TurnCoordinator` (in-flight maps).
3. **Staged, Safe Consolidation:** Trim vendored bloat (`emotion_engine.py`) and merge over-fragmented
   packages one area per commit while keeping tests green.
4. **Strict Config & Language Hygiene:** Reject unknown YAML keys at load and keep `monoagens/engine/`
   100% English (all user-facing strings via `i18n.t()`).
5. **Real-Seam Behavioral Testing:** Every bug fix lands with a behavioral test exercising the real
   production seam, natural-language queries, and chaos scenarios (`ST-01..ST-18`).

## 3. Options considered

**A. Keep `__getattr__` Helpers and `OVER_CAP_ALLOWED` Exemptions.** Rejected: hides type errors and
defeats the purpose of module budgets.

**B. Rewrite the Entire 194-File Package into 32 Files in a Single Commit.** Rejected: a single giant
rewrite hides regressions; staged per-area consolidation is safer and verifiable.

**C. Typed Pipeline, Honest 800-Line Budget, Staged Consolidation, and Real-Seam Tests.** Accepted.

## 4. Decision

### 4.1 Architecture Decision Records (ADR Home) and Governance Files

- ADRs live in `docs/v0.1.0/adr/` (root, `engine/`, `web/`) with index at `docs/v0.1.0/README.md`.
- English is the sole language for all ADRs. Indonesian ADR copies (`adr/id/`), `--translate-check`,
  `--stamp`, and `README.id.md` are retired.
- `AGENTS.md` contains only timeless architectural invariants (no rotting file lists or line numbers).
- `TASK.md` tracks only active open work referencing `ADR-0001..0008`.
- `LOG.md` records concise, high-level decisions (1–4 lines per entry, newest first).

### 4.2 Explicit Turn Pipeline (`TurnRuntime`, `TurnState`, `TurnCoordinator`)

- All `def __getattr__(self, name): return getattr(self.agent, name)` methods are removed from
  `monoagens/engine/turn/` (`TurnRunner`, `Delivery`, `TurnIntake`, `TurnSteps`, `TurnCommands`).
- Turn execution is structured as explicit stages (`intake -> decide -> context -> act -> review -> deliver`)
  operating on:
  - `TurnRuntime`: Read-only collaborator references (`db`, `llm`, `memory`, `outbox`, `gateways`, `approvals`).
  - `TurnState`: Typed dataclass carrying per-turn state (`turn_id`, `generation`, `inbound`,
    `origin_address`, `actor_id`, `roles`, `decision`, `draft_text`, `reviewed_text`, `chunks`).
  - `TurnCoordinator`: Owns the in-flight mutable dictionaries (`_thread_locks`, `_turn_tasks`,
    `_turn_gateways`, `_cancel_reasons`).

### 4.3 Honest 800-Line Module Cap and Staged Consolidation

- Every Python module in `monoagens/` must stay `<= 800` lines; `OVER_CAP_ALLOWED` is removed by
  splitting `loop.py`, `config.py`, `affect.py`, `cognition.py`, and `__main__.py` along cohesive
  responsibilities.
- Structural consolidation proceeds one area per commit with full test parity:
  1. Replace `monoagens/vendor/emotion_engine.py` (4,855 lines) with a ~200-line pure PAD math module
     inside `monoagens/engine/stores/affect.py`.
  2. Consolidate `monoagens/engine/memory/` once `mem0.py` is removed (ADR-0002).
  3. Consolidate one-line command wrappers in `monoagens/engine/commands/`.
  4. Consolidate `monoagens/admin/` helpers (`logo_art.py`, `brand.py`).
  5. Migrate `monoagens/engine/turn/` to the staged pipeline (§4.2).
- `StyleStore.learn` is wired to the end of the turn (offloaded via `io.run`) or `StyleStore` is
  removed; `observability.py` callers supply `extra={"event": ...}` (or the unused field is dropped);
  and `monoagens/engine/jobs/schedule.py` is formally part of `jobs/`.
- Dead scripts (`scripts/xlsx.py`, `scripts/ablate.py`, `scripts/neutral.py`) and duplicate
  `.sonarcloud.properties` are removed; boundary checks live in `tests/test_invariants.py`.

### 4.4 Configuration Validation and Language Hygiene

- `load_config` rejects unknown YAML keys in core sections with a clear error message at startup (while
  delegating `gateways.<name>` options to each gateway's own validator), and the four dead keys are fixed
  (`engine.memory.capture.min_chars`, `engine.skills.max_chars`, and removal of
  `engine.ambient.context_enabled` and `gateways.cli.role`).
- Duplicate `engine.turn` and `engine.turns` sections in `config.yaml` are consolidated into a single
  `engine.turns` section (`timeout_seconds` and `min_call_seconds` live directly inside `engine.turns`),
  `engine.approval.sensitive_tools` is removed (ADR-0006 §4.5), and the redundant `voice.enabled` master
  switch in `soul.yaml` is removed so `voice.affect.enabled`, `voice.style.enabled`,
  `voice.character.enabled`, and `voice.humanize.enabled` govern themselves directly.
- Hardcoded Indonesian strings in `turn/delivery.py:171`, `llm.py:421`, and `turn/loop.py:283` are
  replaced with `i18n.t()` catalog keys or English internal diagnostics.

### 4.5 Testing Discipline (Closing "The Green Test Illusion")

- **Behavioral over AST-Only Checks:** Supplement AST checks with a live async event-loop heartbeat
  test verifying that turn execution never blocks the loop.
- **No Blind Mocking of Seams:** Integration tests must bind the real `AutomationStore.propose`, real
  `IdentityStore.resolve` on resumed `per_…` actors, and real multi-gateway routing (`CLIGateway` to
  `WhatsAppGateway` stub).
- **Natural-Language & Chaos Probes:** Memory and detector tests use full natural sentences
  (`"Apa warna favoritku sekarang?"`, `"Halo sobat"`, `"lampu kamar mau mati"`), and chaos tests cover
  scenarios `ST-01` through `ST-18`.

### 4.6 Canonical term register

The following canonical vocabulary binds all configurations, code definitions, and schemas:

| Canonical term | Owner | Forbidden synonyms | Notes |
|---|---|---|---|
| `Percept` | ADR-0001 | `Inbound`, `IncomingMessage` | Inbound message contract from gateway |
| `Address` | ADR-0001 | `Target`, `Destination` | Canonical address `(gateway, thread)` |
| `Action` | ADR-0001 | `Outbound`, `Delivery` | Outbound action/delivery contract |
| `administrator` | ADR-0006 | `admin`, `owner` (role name) | Protected top RBAC role |
| `memories` | ADR-0002 | `facts`, `memory_store` (column) | Canonical memory table |
| `mode` | ADR-0006 | `role` (in soul.yaml) | Persona mode of the soul |

## 5. Configuration

Configured in `pyproject.toml` and `.ruff.toml`:
```toml
[tool.ruff]
line-length = 100
target-version = "py314"
```

## 6. Metrics and gates

- `python -P scripts/adr.py --check` passes across all ADRs.
- `tests/test_invariants.py` (and `scripts/selfcheck.py`) verifies zero modules exceed 800 lines with
  `OVER_CAP_ALLOWED` empty.
- `ruff check monoagens scripts tests plugins migrations` and `python -P scripts/types.py --check` pass
  with zero errors.
- `pytest -q -W error::sqlalchemy.exc.SADeprecationWarning` passes across the full test suite.

## 7. Acceptance criteria

- [ ] `monoagens/engine/turn/loop.py` and all other modules are `<= 800` lines without `OVER_CAP_ALLOWED`.
- [ ] Zero `__getattr__` proxy methods remain in `monoagens/engine/turn/`.
- [ ] `monoagens/vendor/emotion_engine.py` is replaced by ~200 lines of PAD math with parity test coverage.
- [ ] `load_config` rejects unknown YAML keys, and zero dead keys remain in `config.yaml` templates.
- [ ] Zero hardcoded Indonesian strings remain in `monoagens/engine/`.
- [ ] Real-seam integration tests cover `ST-01..ST-18` without mocking the tested seam.

## 8. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Rejecting unknown config keys breaks stale local `config.yaml` files | Error message names the exact unknown key and its replacement path |
| Splitting large modules breaks internal imports | Re-export public symbols until callers are updated in the same commit |

## 9. Interaction with other ADRs

- **ADR-0001:** Enforces strict separation between `monoagens/engine/` and `monoagens/gateways/`.
- **ADR-0002:** Retires `mem0.py` and `scripts/neutral.py` stopword side effects.
- **ADR-0003:** Integrates `TurnRuntime` and `TurnState` with turn checkpoints and async offloading.

## 10. Code references

- `scripts/adr.py`, `scripts/selfcheck.py`, `tests/test_invariants.py`, `tests/test_adr.py`
- `monoagens/engine/turn/loop.py`, `delivery.py`, `intake.py`, `steps.py`, `commands.py`, `observability.py`
- `monoagens/engine/config.py`, `monoagens/engine/stores/affect.py`, `monoagens/engine/stores/style.py`
- `monoagens/vendor/emotion_engine.py` — vendored module to replace.

---

*This ADR lives at `docs/v0.1.0/adr/0008-modular-architecture-and-code-hygiene.md`.*
