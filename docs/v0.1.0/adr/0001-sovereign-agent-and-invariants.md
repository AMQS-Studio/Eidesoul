# ADR-0001: Sovereign Agent and Core Invariants — One Soul, One Database, One Primary, and Hexagonal Seam

- **Status:** accepted
- **Date:** 2026-11-05
- **Deciders:** Muqsith (owner)
- **Context:** Re-baselined for v0.1.0; consolidates foundational architectural invariants, intra-deployment namespace isolation, and configuration authority (pre-rebaseline 0000, 0035, 0040, 0048, 0053).
- **Supersedes:** pre-rebaseline 0000, 0035, 0040, 0048
- **Related:** ADR-0002 (memory), ADR-0003 (turn lifecycle), ADR-0004 (tools)
- **Implementation:** implemented — single-lease constraint and gateway isolation are enforced by tests; standby boot writes zero rows, a resumed `per_…` actor keeps its identity, and turn keys are namespaced per gateway/thread.

---

## 1. Context

MonoAgens (Eidesoul) is a persistent autonomous agent harness maintaining one continuous identity
(soul), long-term memory, and role-based access control across multiple physical appendages (gateways).

To keep the harness lightweight, predictable, and structurally private on modest self-hosted hardware,
the architecture is built around a dedicated deployment model:

```
1 Deployment = 1 Soul = 1 PostgreSQL Database = 1 Primary Process
```

Within a single deployment, however, multiple gateways (such as CLI, WhatsApp, Discord) and multiple
app scopes operate concurrently against the same PostgreSQL database. Earlier iterations had several
intra-deployment namespace collisions and boot ordering defects:
1. **Transport Message ID Collision (`turns.id`):** `turns.id` used the raw transport `message.id` as a
   global primary key. If two gateways emitted the same sequential ID (e.g. `"msg-1"` or `"1"`),
   `HistoryStore.intake` silently dropped the second message on conflict.
2. **Thread Route Collisions (`RouteConflict`):** `threads.thread_id` was global with a single `gateway`
   column, and `RouteConflict` dropped colliding messages silently without operator visibility.
3. **Scratch Directory Collisions (`safe_chat`):** `safe_chat(thread_id)` replaced non-alphanumeric
   characters with `_` without a hash suffix, mapping `"app:user_1"` and `"app_user:1"` to the same
   `workspace/scratch/app_user_1` directory.
4. **Cross-App Skill Overwrites (`SkillStore.save`):** The `skills` table conflicted on `name` alone
   rather than `(app, name)`, allowing one app scope to overwrite another app's procedural skill, while
   accepting unverified `hints["app"]` from any gateway.
5. **Standby Boot Writes (`_sync_soul_rules`):** `Agent.__init__` invoked `_sync_soul_rules()` before
   acquiring the primary lease, causing standby instances to write to PostgreSQL during boot.
6. **Configuration Authority Drift:** Active `config.yaml`, `roles.yaml`, and `soul.yaml` files were
   tracked in Git, and legacy comments claimed the `documents` database table superseded host files.

## 2. Decision drivers

1. **Lightweight Dedicated Footprint:** Keep the schema fast, compact, and scoped to a single deployment
   so the agent runs comfortably in 350–500 MB RAM on a small server.
2. **Hexagonal Architecture:** Core engine (`monoagens/engine/`) must remain appendage-agnostic and
   never import gateway protocol SDKs.
3. **Intra-Deployment Namespace Isolation:** Multiple gateways and apps inside one deployment must never
   collide on turn keys, scratch directories, or skill names.
4. **Single Outbound Door:** Every outgoing delivery and tool side effect must pass through
   `Agent.dispatch()` for centralized authorization, auditing, and idempotency.
5. **Authoritative Host Configuration:** Host YAML files (`config.yaml`, `roles.yaml`, `soul.yaml`, `.env`)
   are the operator's single source of truth; database `documents` rows are derived caches only.

## 3. Options considered

**A. Shared Multi-Database or Multi-Schema Routing Inside the Engine.** Rejected: adds unnecessary query
and operational overhead when process-level deployment isolation is simpler and lighter.

**B. Unscoped Global Transport Identifiers.** Rejected: causes silent message drops when two active
gateways emit identical message IDs.

**C. Dedicated Sovereign Harness with Namespaced Intra-Deployment Keys.** Accepted.

## 4. Decision

### 4.1 Sovereign Process and Storage Invariants

- One deployment holds one soul, one PostgreSQL database, and one primary process enforced via
  `engine_lease` with `CheckConstraint("id = 1")`.
- Schema tables remain lean and dedicated to the single soul without external orchestration columns,
  composite lease keys, or external inference sidecar daemons.
- `_sync_soul_rules()` executes only on the primary start path after lease acquisition; a standby
  instance writes nothing to the database at boot.

### 4.2 Appendage and Gateway Isolation (Multimodal, Voice Call, & Robotics Seam)

- Gateways connect exclusively via contracts in `monoagens/engine/inbound.py`:
  - `Percept`: Inbound event carrying a normalized `Address`, `kind` (`message`, `task`, or extensible
    modalities such as `voice_call`, `audio_frame`, `sensor`, `event`), `payload`, and open
    `hints: Mapping[str, object]`.
  - `Address`: Canonical `(gateway, thread)` tuple.
  - `Action`: Outbound command carrying `id`, `address`, `capability` (`send_text`, `send_file`,
    `send_interim`, `send_thinking`, `send_step`, or embodiment-specific capabilities such as
    `send_voice`, `call_stream`, `actuate`), `payload`, `approval_class`, plus optional `stream_id: str = ""`,
    `replace_id: str = ""` (for live message editing and audio/token streaming), and open
    `meta: Mapping[str, object] = {}` (for voice/TTS options, interactive buttons, or robot actuator
    parameters).
  - `Receipt`: Outbound execution confirmation.
  - `GatewayCore`: Protocol implemented by every transport adapter (`normalize_address`, `run`, `dispatch`,
    and optional `abort(thread)` for real-time voice barge-in or physical robot emergency stop).
- Core holds no transport-specific vocabulary (no JIDs, phone prefixes, or protocol tokens).
- Transport normalization (`GatewayCore.normalize_address`) and dispatch are owned by gateway adapters.

### 4.3 Intra-Deployment Identifier and Corpus Isolation

- **Turn and Outbox Keys:** `turns.id` is namespaced as `f"{gateway}:{thread}:{message_id}"` (or an
  internal ID backed by a unique index on `(gateway, thread, message_id)`), and
  `outbound_messages.scope_key` follows the same namespace so two gateways emitting identical message
  IDs never collide or drop messages.
- **Thread Routing:** `RouteConflict` in `HistoryStore._route` rejects collisions loudly with an explicit
  structured log entry and operator diagnostic rather than silently dropping the inbound message.
- **Workspace Scratch Isolation:** `safe_chat(thread_id)` appends a short deterministic hash suffix of
  the original `thread_id` (e.g. `app_user_1_<hash8>`) so distinct thread IDs never share a scratch
  directory.
- **App Corpus Namespace:** `SkillStore.save` keys uniqueness on `(app, name)` rather than `name` alone.
  `app` remains a corpus namespace (not a security boundary), and `inbound.hints["app"]` is accepted only
  from gateways that explicitly declare the app in their configuration.

### 4.4 Outbound Door and Authoritative Configuration

- All side effects and messages pass through `Agent.dispatch()` with an explicit `origin: Address`.
- Host files (`config.yaml`, `roles.yaml`, `soul.yaml`, `.env`) are authoritative; `documents` rows in
  PostgreSQL are derived caches and never override host files on conflict.
- Active host configuration files (`config.yaml`, `roles.yaml`, `soul.yaml`, `.env`) are untracked in Git;
  the repository ships clean templates materialized by `monoagens init`.
- **Admin & Web Control Panel Seam:** Future operator control panels (Web UI / Desktop) run as separate
  surfaces without Core ever importing a web framework: they inspect and manage state through the same
  PostgreSQL stores (`MemoryStore`, `IdentityStore`, `JobStore`, `ApprovalStore`), mutate and validate host
  YAML files through the shared dataclass schema layer + `reload` IPC trigger (`ADR-0007`), and stream live
  chat, thinking/step progress, approvals, or voice via the standard `Percept` $\leftrightarrow$ `Action`
  (`stream_id`, `replace_id`, `meta`) contract.

## 5. Configuration

Core host configuration resides in `config.yaml`:
```yaml
engine:
  lease:
    ttl_seconds: 30
    renew_seconds: 10
```

## 6. Metrics and gates

- Schema and architecture tests verify `engine_lease` enforces `id = 1` and `monoagens/engine/` contains
  zero gateway imports.
- Two-gateway intake test verifies that identical transport `message.id` values from two gateways both
  record turns and produce replies.
- Scratch path test verifies `"app:user_1"` and `"app_user:1"` resolve to distinct directories.

## 7. Acceptance criteria

- [x] `engine_lease` CheckConstraint locks `id = 1`.
- [x] `monoagens/engine/` imports no gateway or transport SDK modules.
- [ ] Two gateways emitting identical transport `message.id` values both succeed without collision.
- [ ] Standby process performs zero database writes before acquiring `engine_lease`.
- [ ] `safe_chat` appends a hash suffix preventing scratch directory collisions.
- [ ] `SkillStore.save` scopes uniqueness to `(app, name)` and validates gateway `app` hints.

## 8. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Turn key format change affects in-flight rows during upgrade | Migration rewrites existing `turns.id` and `outbound_messages.scope_key` entries |
| Untracking host YAML files impacts CI test fixtures | Tests construct configurations from `monoagens/admin/templates.py` in `tmp_path` |

## 9. Interaction with other ADRs

- **ADR-0002:** Scopes all memories to `global` + `person:<sender>` + `thread:<active>`.
- **ADR-0003:** Uses the single lease row to fence turn execution and job claims atomically.
- **ADR-0004:** Enforces explicit `origin: Address` across all outbound tool dispatches.

## 10. Code references

- `monoagens/engine/schema.py` — `engine_lease`, `turns`, `threads`, and `skills` table definitions.
- `monoagens/engine/inbound.py` — `Percept`, `Address`, `Action`, `Receipt`, `GatewayCore`.
- `monoagens/engine/agent.py` — `Agent.__init__`, `_sync_soul_rules`, and `Agent.dispatch()`.
- `monoagens/engine/stores/history.py` — `HistoryStore.intake` and `_route`.
- `monoagens/engine/stores/skills.py` — `SkillStore.save` `(app, name)` key.
- `monoagens/engine/tools/__init__.py` — `safe_chat` scratch directory resolution.

---

*This ADR lives at `docs/v0.1.0/adr/0001-sovereign-agent-and-invariants.md`.*
