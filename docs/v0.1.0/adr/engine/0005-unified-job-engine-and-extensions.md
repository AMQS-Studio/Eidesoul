# ADR-0005: Unified Job Engine and Extension Architecture — Workflows, Tasks, Plugins, and Provider Adapters

- **Status:** accepted
- **Date:** 2026-11-05
- **Deciders:** Muqsith (owner)
- **Context:** Re-baselined for v0.1.0; unifies automations, task execution, plugins, and provider adapters (pre-rebaseline 0014, 0028, 0044, 0047, 0054) into one job execution and extension standard.
- **Supersedes:** pre-rebaseline 0014, 0028, 0044, 0047, 0054
- **Related:** ADR-0001 (sovereign harness), ADR-0003 (lease fencing), ADR-0004 (core tools)
- **Implementation:** partial — automation worker and provider wire adapters are implemented; unified task execution and local plugin modes are scheduled in Phase P1/P2.

---

## 1. Context

Long-running or background execution previously threatened architectural sprawl:
1. **Queue Duplication:** The repository was accumulating four separate queue mechanisms (`turns`,
   `recipes`, `automations`, and a proposed standalone task runner), each with redundant claim,
   lease, retry, and reaper logic.
2. **DX Friction for Extensions:** Adding a gateway required 8–9 files across multiple directories.
   Developing a plugin required manual SHA-256 recomputation on every single line edit because
   `PluginLoader` only supported tarball archives with strict hash locks.
3. **Provider Wire Gaps:** `monoagens/providers/` handled OpenAI and Gemini natively, but Anthropic was
   routed through an OpenAI-compatibility layer that broke image inputs and prompt caching.

## 2. Decision drivers

1. **One Unified Job Engine:** Background workflows (automations, durable tasks, recipes) must share
   one claim, lease, reaper, and step-recording infrastructure under `monoagens/engine/jobs/`.
2. **Durable Task Support:** Ad-hoc goals exceeding a turn's timeout must run outside the per-thread
   lock with checkpointing, budgeting, and single-delivery completion reporting.
3. **Frictionless Developer Experience:** Adding a gateway should require only one file and clean base class
   inheritance. Local plugins under development must load without hash recalculation.
4. **Provider Protocol Independence:** Provider wire facts stay isolated in `monoagens/providers/` without
   leaking vendor names into core engine logic.

## 3. Options considered

**A. Separate Distributed Task Queue (Celery/BullMQ).** Rejected: adds external queue infrastructure
contrary to the self-hosted single-database invariant.

**B. Unbounded Turn Timeouts.** Rejected: one hung tool or model provider blocks the chat thread lock
indefinitely.

**C. Unified Job Engine with Specialized Kinds.** Selected: tasks, automations, and recipes share
common execution plumbing in PostgreSQL while exposing appropriate operator interfaces.

## 4. Decision

### 4.1 Unified Job Engine

All asynchronous and background work runs through the unified engine in `monoagens/engine/jobs/`:
- **Automations:** Declarative `event -> condition -> steps` workflows triggered by schedules or chat events.
- **Durable Tasks:** Goal-oriented agent loops executing ad-hoc instructions outside turn locks with
  explicit budgets (`run_budget_seconds`, max tool steps). Created via `/task new` or model tool.
- **Recipes:** Parameterized tool macros recorded durably in `turn_steps` before execution.
- Shared infrastructure: Database clock timestamps, atomic CAS generation fencing, and unified reaper
  reconciliation.

### 4.2 Gateway Extension Model (`BaseGateway`)

Gateways inherit from `BaseGateway` in `monoagens/engine/inbound.py`:
- Safe default implementations for `Rich`, `Directory`, and `PrivateThread` protocols.
- Mandatory `normalize_address(raw) -> str` implementation.
- Gateway permission screening (`access_refusal`) is enforced centrally in `Agent.handle()`, ensuring
  no gateway implementation can omit security checks.
- Simple gateways can be implemented in a single module (e.g. `monoagens/gateways/discord.py`).

### 4.3 Dual-Mode Plugin System

`PluginLoader` supports two distinct operational modes:
- **Local Plugins:** Directories residing in `plugins/<name>/` loaded without SHA-256 verification
  for active development. Manifest fields default sensibly from function signatures via `@plugin.tool`.
- **Remote Fetched Plugins:** Bundles downloaded from verified URLs with strict SHA-256 digest validation
  before extraction. Dependencies load isolated without injecting unverified paths to `sys.path[0]`.

### 4.4 Provider Wire Adapters

Provider adapters in `monoagens/providers/` standardize LLM communication:
- `openai.py`: Standard chat completions wire with tool calling.
- `gemini.py`: Native Google `generateContent` with `safetySettings` and `topK`.
- `anthropic.py`: Native `/v1/messages` protocol with prompt caching and native tool schemas.

## 5. Configuration

Configured in `config.yaml`:
```yaml
engine:
  jobs:
    worker_interval_seconds: 5
    task_timeout_seconds: 1200
  plugins:
    local_development: true
```

## 6. Metrics and gates

- Concurrency test: Multiple background tasks execute without holding chat thread locks.
- Plugin security test: Tampered remote plugin archive is rejected before import.
- Provider integration test: Gemini and Anthropic fixtures verify wire-level compliance.

## 7. Acceptance criteria

- [ ] Background tasks checkpoint progress after every tool step.
- [ ] New gateway can be added in a single file inheriting `BaseGateway`.
- [ ] Local plugins load and reload without manual SHA-256 hash calculation.
- [ ] Automated reaper recovers hung jobs without re-executing finished steps.

## 8. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Runaway task costs excess tokens | Hard task budgets on wall time, model calls, and maximum tool steps |
| Local plugins introduce vulnerable code | Local mode requires explicit `local_development: true` configuration |

## 9. Interaction with other ADRs

- **ADR-0003:** Job claims and executions are fenced with atomic lease generation tokens.
- **ADR-0004:** Tasks and automations execute actions through the core messaging tools.

## 10. Code references

- `monoagens/engine/jobs/` — Unified job store, worker, and automation scheduler.
- `monoagens/engine/plugins.py` — Dual-mode `PluginLoader` and digest verification.
- `monoagens/providers/` — Protocol wire adapters (`openai.py`, `gemini.py`, `anthropic.py`).

---

*This ADR lives at `docs/v0.1.0/adr/engine/0005-unified-job-engine-and-extensions.md`.*
