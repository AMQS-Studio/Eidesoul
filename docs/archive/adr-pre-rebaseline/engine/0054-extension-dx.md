# ADR-0054: Extension DX — One-File Gateways, Local Plugins, and One Job Engine

- **Status:** proposed
- **Date:** 2026-11-05
- **Deciders:** Muqsith (owner)
- **Context:** adding a gateway touches 8–9 files, a plugin needs a hand-computed digest for every edit, and four queue engines are in the making; the owner wants a one-file gateway path.
- **Supersedes:** — (absorbs the former durable-task-runner draft, §4.4)
- **Related:** ADR-0014 (automation), ADR-0018 (gateway contract), ADR-0028 (plugin model), ADR-0044 (provider wire adapters), ADR-0046 (gateway access), ADR-0050 (cross-gateway), ADR-0053 (addendum)
- **Implementation:** draft — nothing is built; the owner accepts or trims the proposals before work starts.
- **Location note:** the proposals bind the gateway contract and the plugin loader, so this ADR sits under `adr/engine/`.

---

## 1. Context

**A gateway today.** A new body needs `descriptor.py`, `config.py`, `policy.py`, `identity.py`,
`gateway.py` (five protocols from `inbound.py`: `CoreDefaults`, `GatewayCore`, `Rich`, `Directory`,
`PrivateThread`), its own `tools/`, a seed policy in `admin/templates.py` (`GATEWAY_POLICY`, `COMMENTS`),
and edits to every role in `roles.yaml` plus `config.yaml`. A forgotten `access_refusal` (as in the
harness) removes role enforcement.

**A plugin today.** `plugin.yaml` carries ten required fields. `tree_digest` changes on any edit, so a
developer recomputes a SHA-256 and edits both `plugin.yaml` and `config.yaml` after every debug
iteration. `plugins install` wants a `.tar.gz` under `plugin.source` and refuses a local directory.

**Queues.** `turns`, `recipes`, `automations`, and the former durable-task-runner draft (`tasks`) each have claim, lease,
reaper, retry, and step recording. `recipes.py` runs tools without writing `turn_steps`, so a crash
repeats effects.

**Providers.** `providers/anthropic.py` is 18 lines that point at the OpenAI-compatibility endpoint; its
own docstring lists the gaps (file parts, caching, strict tool schemas).

## 2. Decision drivers

1. A third party writes a gateway or plugin in one sitting.
2. Security checks live in the core, not in each gateway's goodwill.
3. A plugin under development is not a plugin fetched from the network; only the second needs a digest.
4. One queue engine, one claim/lease/reaper/step contract.
5. Everything stays removable and off by default (AGENTS.md).

## 3. Options considered

**A. Keep the five protocols and document them better.** Rejected: the documentation fixes nothing the
core could enforce.

**B. A `BaseGateway` with safe defaults plus a registration decorator.** Accepted as the direction.

**C. Skip the DX work until after release.** Rejected for the gateway contract, because ADR-0050 adds
`normalize_address` to it anyway.

## 4. Decision

### 4.1 Gateway as one class

`engine/inbound.py` gains `BaseGateway` with defaults for `Rich`, `Directory`, and `PrivateThread` that
refuse cleanly. A gateway implements `normalize_address`, `run`, and `dispatch`. A `register_gateway`
decorator declares `name`, `extra`, and `default_policy`; `GATEWAY_POLICY` and the template comments
are generated from it, and `gateway doctor --fix` reads the same source. The role check
(`access_refusal`) moves into `Agent.handle`, so no gateway can omit it. `monoagens/gateways/<name>.py`
is allowed as a single-file layout when the gateway needs no subpackage.

### 4.2 Gateway tools stay body-specific

Send, file, and history tools are core (ADR-0050). A gateway ships `tools/` only for actions of its own
body (a robot arm, a reaction emoji).

### 4.3 Plugins

A plugin has two modes. A **local** plugin sits under `plugins/<name>/`, loads without a digest check,
and is marked `local` in `monoagens plugins list`. `monoagens plugins enable <name>` writes the current
digest. `monoagens plugins digest <name>` refreshes it. A **fetched** plugin keeps today's contract: an
archive, a pinned `sha256:`, and verification before the import (ADR-0028). `plugins install` accepts a
local directory. A `@plugin.tool` decorator builds the JSON schema from type hints and the docstring;
the hand-written schema dict stays supported. `plugin.yaml` requires only `name`, `version`, `entry`,
and `engine`; `provides`, `requires`, `secrets`, and `credits` are optional but validated when present.

### 4.4 One job engine, with durable tasks as a kind of job

The owner folded the durable-task-runner draft (no code) into this ADR and deleted it. What
survives of it:

- **The need.** Work longer than one turn ("refactor this module", "research this question") needs a
  durable, owned, budgeted unit that runs outside the turn lock, checkpoints each step, and reports
  back once. The turn deadline (`engine.turn.timeout_seconds`) and the closed automation language
  cannot carry it.
- **The shape.** A task is a row owned by one person with a goal and a budget (wall time, model calls,
  steps, queue depth, per-person quota). The creating turn writes the row, answers with the id, and
  ends; a worker in the primary process runs the existing `llm.run` loop outside `TurnRunner` and the
  per-thread lock. Surfaces: a `task` tool and `/task new|list|show|cancel`, each behind a role grant
  (the default member role does not receive it).
- **The rules.** A task never has more authority than its owner at execution time; sensitive tools go
  through `execute` and approvals; it reads only the owner's memory scopes and delivers its result to
  the origin thread once; crash recovery replays nothing that already ran (step recorded before the
  effect, deterministic action ids per ADR-0049 §4.1.4).
- **The change from that draft.** No separate `tasks`/`task_steps` engine. Automations, recipes, and tasks
  share one claim, lease, reaper, retry, and step-recording implementation in `jobs/`; a task is a job
  `kind` with a free-form goal and a checkpoint, where an automation is a job `kind` with a declarative
  manifest. Recipes record `turn_steps` before each tool call. No ADR adds another queue engine.
- **Deferred.** A separate worker process or external queue is a deployment decision for a later ADR.

### 4.5 Anthropic native wire

A native `/v1/messages` adapter (about 120 lines) sits next to `gemini.py`, behind the ADR-0044 seam, in
a later phase. Until then Anthropic stays on the compatibility layer with its documented limits.

## 5. Configuration

`plugin.local: true` (default `false`) enables the local mode; no other key changes. Each new key gets
its comment from `monoagens init`.

## 6. Metrics and gates

- A sample Discord-style gateway in one file passes the gateway contract test and role enforcement.
- A local plugin edit loads without a manual digest step; a fetched plugin with a wrong digest still
  fails.
- A crash test for a recipe shows each tool step executes once.

## 7. Acceptance criteria

- [ ] A new gateway needs one file plus one `pyproject.toml` extra.
- [ ] `access_refusal` is enforced by the core for every gateway.
- [ ] `plugins install ./plugins/foo` works and records its digest.
- [ ] No second queue engine appears in `migrations/`; a task is a job `kind`.

## 8. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| A local plugin bypasses the digest | It is off unless `plugin.local: true`, and `plugins list` marks it |
| The base class hides a protocol change | The contract test runs against the base class and the sample gateway |
| Merging the job engines destabilises automations | ADR-0014 keeps its manifest; only claim and step code is shared |

## 9. Interaction with other ADRs

ADR-0018 keeps the contract and adds a base class. ADR-0028 keeps verified fetch and adds the local
mode. ADR-0046 stays the policy source; the decorator only supplies its seed. The task-runner draft is folded into §4.4.

## 10. Code references

- `monoagens/engine/inbound.py` — the gateway protocols.
- `monoagens/engine/plugins.py` — `PluginLoader`, `tree_digest`.
- `monoagens/admin/templates.py` — `GATEWAY_POLICY`, `COMMENTS`.
- `monoagens/engine/stores/recipes.py`, `monoagens/engine/jobs/automation_*.py`
- `monoagens/providers/anthropic.py`, `monoagens/providers/gemini.py`

---

*This ADR lives at `docs/v0.1.0/adr/engine/0054-extension-dx.md`.*
