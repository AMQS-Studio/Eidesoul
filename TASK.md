# TASK

MonoAgens active checklist. Every item is done when the listed surface exists, is covered by real-seam tests, and works end to end.

## Active: v0.1.0 Remediation Roadmap (ADR-0001..0008)

### Phase P0 — Correctness, Security, and Boot Safety (ADR-0001, ADR-0003, ADR-0006, ADR-0007, ADR-0008)

- [ ] **Resume Identity & Percept Hints (ADR-0003 §4.1):**
  - `TurnIntake.identify` and `IdentityStore.resolve` short-circuit when actor starts with `per_` (no phantom `people` rows).
  - Persist `hints` (`dm`, `mention`, `reply`, `app`) on `turns` and restore on supervisor resume.
  - Move `_sync_soul_rules()` from `Agent.__init__` to the primary start path so standby instances write nothing at boot (ADR-0001 §4.1).
- [ ] **Review Ordering & Prompt/Window Budgets (ADR-0003 §4.2):**
  - Execute `character.polish -> privacy.review -> guard.cross_chat_leak` before `checkpoint("reviewed")`; resuming from `"drafted"` re-runs review.
  - Pin `safety` (`CRISIS_SAFETY`) and `research` (`ResearchCard`) prompt parts (`pinned=True`) and honor `config.prompt.pinned_blocks`.
  - Count quote text, media lines, and inline text attachments against `thread_chars` in `assembly._trim`.
  - Reject `character.polish` rewrites that alter numbers or dates from the draft.
- [ ] **Exit Door Authorization & Origin-Tracked Approvals (ADR-0004 §4.2):**
  - Require explicit `origin: Address` in `Delivery._door` (delete `origin or action.address` fallback).
  - Pass `declared_class=action.approval_class` in `Agent.dispatch` so normal replies are `CONSENT` in `strict` mode.
  - Store `origin_gateway`, `origin_thread`, and a unique SQL index on `action_id` in `approvals`; allow `/approve` from the origin room.
  - Run `guard.cross_chat_leak` on outbound tool payloads before cross-room sends.
- [ ] **Send Idempotency & Outbox Reconciliation (ADR-0003 §4.3, ADR-0004 §4.1):**
  - Assign deterministic `Action.id = f"turn:{turn_id}:tool:{step_seq}"` to all tool sends.
  - Match prefix `scope_key LIKE 'turn:{id}%'` in `TurnStore.claim`, reaper, and startup reconciliation.
  - Resolve tool aliases before step logging and record `turn_steps` *before* executing non-idempotent tools.
  - Record separate steps for text and file deliveries so a failed file upload never marks a delivered text as `failed`.
- [ ] **Wiring, Intra-Deployment Keys, & Migration Fixes (ADR-0001 §4.3, ADR-0002 §4.1–4.2, ADR-0005 §4.1):**
  - `cognition.py:1023` & `supervisor.py:277`: Pass keyword `thread_id=thread` to `AutomationStore.propose`.
  - `disputes.py:466`: Match `source_message_id` (or validated UUIDs) in `promote_staged`.
  - Namespace `turns.id` and `outbound_messages.scope_key` by `(gateway, thread, message_id)` and log `RouteConflict` loudly.
  - Append a deterministic hash suffix in `safe_chat(thread_id)` and key `SkillStore.save` on `(app, name)`.
  - Drop `memories_readable` view before dropping columns in migrations `0013` and `0018` `downgrade()`, and add `ix_thread_people_person` to `schema.py`.
- [ ] **Guard, Plugin, & `/link` Hardening + Removal of `harness`/`cli` Gateways (ADR-0005 §4.2–4.3, ADR-0006 §4.3–4.4):**
  - Delete unused `monoagens/gateways/harness/` completely and move `monoagens/gateways/cli/` into the built-in core console (`monoagens console`); enforce `access_refusal` centrally in core `Agent.handle`.
  - `guard/policy.py` & `sandbox.py`: Add `timeout`, `flock`, `setsid`, `script`, `watch`, `doas`, `pkexec`, and `env -S` to `SHELL_WRAPPERS`; reject `${IFS}` and brace expansion; restrict `SAFE_HEADS` to workspace and non-writing flags; ignore plain file arguments in `BLOCKED_RE`.
  - `deploy/sandbox-proxy/squid.conf`: Block IPv6 private/loopback ranges (`::1/128`, `fc00::/7`, `fe80::/10`, `::ffff:0:0/96`, `0.0.0.0/8`).
  - `plugins.py`: Verify `deps/` in `tree_digest` or isolate imports from `sys.path[0]`; verify workspace bounds in `PluginLoader.remove`.
  - `identity.py` & `link.py`: Rate-limit failed `/link` attempts; lock with `SELECT ... FOR UPDATE`; refuse merging users holding roles in `roles.yaml`; match gateway names before 6-char code regex; revoke relay grants and approvals on `/unlink`.
- [ ] **Container, `$PATH` CLI Wrapper, Installer, Dynamic Templates, & CLI Admin Fixes (ADR-0007 §4.1–4.3):**
  - Copy `migrations/` in `Dockerfile` and package in `pyproject.toml`; pass gateway `EXTRAS` in `compose.yaml` and only start `sandbox-proxy` when `sandbox` is enabled.
  - Install global `monoagens` command into `$PATH` (`~/.local/bin/monoagens`) working identically across `native` and `compose` (Docker/Podman) stacks.
  - Add top-level lifecycle & operator commands: `monoagens start|stop|restart|status`, `monoagens logs [-f] [--verbose|--debug]`, `monoagens run [--verbose|--debug]`, `monoagens reload` (explicit reload + auto-trigger after CLI config mutations, without `Supervisor` `mtime` polling), `monoagens setting` (interactive settings + `model`/`provider`/`websearch` shortcuts), `monoagens cli` (simple lightweight terminal chat REPL), `monoagens update` (alias `upgrade`, all-in-one default + `--core`), `monoagens doctor [--fix]`, `monoagens backup|restore`, and `monoagens uninstall [--purge]`.
  - Implement on-demand lifecycle commands `monoagens gateway list|enable|disable|pair|unpair|delete|update`, `monoagens plugins list|enable|disable|delete|update|digest`, and `monoagens tools list|enable|disable` (downloading/installing on `enable`, keeping code off on `disable`, and deleting code/deps/config on `delete`).
  - Derive `HOST`, `ENGINE`, and gateway defaults/role policies in `admin/templates.py` directly from dataclasses and `@register_gateway` (removing hardcoded dict drift), and render `config.yaml`, `roles.yaml`, and `soul.yaml` with full section banners and inline comments matching the post-ADR-0008 blueprint.
  - Update `install.sh` and `monoagens init` (`wizard.py` & `init.py`) to dynamically configure selectable `web_search` providers (`ddgs`, `searxng`, `tavily`, `brave`, `exa`, `serper`, `google`, `off` + API key in `.env`), `approval.mode` (`default` | `yolo` | `strict`), and `voice_parts` (retiring `--voice-layer`), running `db upgrade` and `people add` before starting the daemon container.
  - Preserve YAML comments on config writes and fall back to in-place inode write on `EBUSY`.
  - Fix `monoagens people add` first-user check (`administrator` users) and normalize addresses via `normalize_address`.
  - Fix `monoagens people list` (read `roles.yaml`), `people adopt`, `monoagens config` (no subcommand help), and `monoagens run` (print refusal diagnostic).
- [ ] **Hygiene & CI Gate (ADR-0003 §4.5, ADR-0008 §4.3–4.4):**
  - Cut `turn/loop.py` under 800 lines so `selfcheck.py` is green.
  - Replace SQLAlchemy JSONB `-` with `.op("-")` in `history.py:179` and replace naive `datetime.now()` / `datetime.utcnow()` with timezone-aware timestamps.
  - Filter `temperature` through `sampling_supported` in `LLM.complete()` and use `config.llm.utility_temperature` uniformly.
  - Reject unknown YAML config keys at load (while delegating `gateways.<name>` options to gateways), consolidate `engine.turn` into `engine.turns`, remove `voice.enabled` in `soul.yaml`, remove `approval.sensitive_tools`, and fix the 4 dead keys in `config.yaml` templates.
  - Move hardcoded Indonesian literals in `delivery.py:171`, `llm.py:421`, and `loop.py:283` to `i18n.t()` or English logs.

### Phase P1 — Cross-Gateway, Event Loop, and Unified Memory (ADR-0002, ADR-0003, ADR-0004, ADR-0006)

- [ ] **Core Cross-Gateway Tools, Multi-Attachment Envelope, Conversational Approvals, & Pluggable Web Search (ADR-0004, ADR-0006 §4.5):**
  - Implement `send_message`, `send_file`, `thread_history` in `monoagens/engine/tools/` and remove `monoagens/gateways/whatsapp/tools/{send,send_to,chat_history}.py`.
  - Implement `GatewayCore.normalize_address(raw) -> str` across all gateways, add optional `stream_id`, `replace_id`, and `meta` fields to `Action` (and `LLMResponse`), and expose `TurnCoordinator.interrupt_thread` + `BaseGateway.abort` for voice barge-in and robot safety stops.
  - Upgrade `Message` to `attachments: tuple[Media, ...]` (keeping `.media` property alias) and add `seq` to `message_media` primary key in migration `0019` alongside `AttachmentStore` (`att_…` handles) for multi-file and voice-note messages.
  - Allow sending to unarchived direct targets with `reason` and `AUTHORIZATION` class; add conversational `relay` tool/command; cap multi-turn tool outputs per round in `LLM.run`.
  - Standardize `approval.mode` (`default` | `yolo` | `strict` without `sensitive_tools`) and implement conversational confirmation (`natural_confirm: true`, `confirm_cross_gateway: true`, `confirm_tasks: true`) that supports multiple staged approvals, verifies `scope_proof` deterministically on natural replies (*"Oke, kirim"*, *"Ya"*), and resumes the LLM turn loop for multi-step workflows.
  - Upgrade `web_search` to support selectable providers (`ddgs`, `searxng`, `tavily`, `brave`, `exa`, `serper`, `google`), `fallbacks`, and `ddgs_backend: auto` via `httpx.AsyncClient`.
- [ ] **Non-Blocking Async Event Loop, Thinking/Progress Stream, & Pool Sizing (ADR-0003 §4.4–4.5):**
  - Offload all synchronous SQLAlchemy blocks, `embed.encode()`, and `privacy_evidence.classify()` in `async def` to `await io.run(...)`.
  - Dedicated connection pool for `Lease.renew` and `_send_gate`; single-connection `MemoryStore._fence`; explicit `max_overflow` in `db.py`.
  - Emit `send_thinking`, `send_step`, and `send_interim` progress actions from `LLM.run` (extracting `thinking` in `openai`, `gemini`, and `anthropic` providers and never swallowing transitional prose on tool rounds).
  - Enforce `run_budget_seconds` and fix round-2+ image retry in `LLM.run`; lock-free reads and CAS writes in `AffectStore`; retry backoff in `VibeStore`; preserve `soul.yaml` edits in `/reload`.
- [ ] **Atomic Lease Fencing & Reaper Hardening (ADR-0003 §4.4):**
  - Enforce atomic SQL `engine_lease.generation` predicates across `memories`, `turns`, `skills`, `automations`, `cognition_jobs`, `affect`, `ambient`, and tool execution.
  - Filter `AutomationStore.reap` by `status IN ('claimed', 'running')` and `interrupt` by `claimed_by` and `generation`.
  - Renew cognition claim heartbeats (`lease_seconds > llm.timeout_seconds + 60`) and use `clock_timestamp()` everywhere.
- [ ] **Unified PostgreSQL Hybrid Memory (ADR-0002 §4.1–4.2):**
  - Add `embedding vector(384)` (HNSW) and `search_tsv tsvector` (GIN) to `memories`; reject non-384 embedder configs at load.
  - Replace `MemoryStore.search` with native hybrid search (Cosine + `plainto_tsquery` OR-match fused with RRF).
  - Retire `mem0ai`, `monoagens/engine/memory/mem0.py`, and `mem0_history.db`.
  - Consolidate `supersede:` candidates across all scopes (`person:*`, `thread:*`, `global`), resolve `subject_id` to canonical `per_…`, and convert `record_uses` to a single set-based SQL statement.
- [ ] **High-Precision Detectors (ADR-0002 §4.3–4.4):**
  - Enforce whole-token boundaries (`\b`) across `research.py`, `patterns.py`, and `evidence.py`.
  - Require value-shaped patterns for `SENSITIVE`, check `safety(language).sensitive` in `gate.structural`, and clear `memories.text` on credential `not_worth`.
  - Narrow `research.py` triggers and `EXACT` regex across both language packs; add crisis negation/idiom windows and injection right boundaries.
  - Require context words in `library/privacy/*.json`, wire or disable NER/centroid layers in `POLICY_DETECTORS`, remove `or risky` in `access/privacy.py`, tighten `_regex_answer`, and filter stopwords in `forget` intent.

### Phase P2 — Simplification and Modular Architecture (ADR-0005, ADR-0006, ADR-0008)

- [ ] **Typed Turn Pipeline (ADR-0008 §4.2):** Replace `__getattr__` helpers in `turn/` with `TurnRuntime`, `TurnState`, and `TurnCoordinator`.
- [ ] **Staged Module Consolidation & Honest Budgets (ADR-0008 §4.3):**
  - Replace `monoagens/vendor/emotion_engine.py` (4,855 lines) with ~200-line PAD math in `stores/affect.py`.
  - Consolidate `engine/memory/`, `engine/commands/`, and `admin/` helpers; eliminate `OVER_CAP_ALLOWED` in `selfcheck.py`.
  - Wire `StyleStore.learn` (or remove `StyleStore`), fix `observability.py` event field, and delete dead scripts (`xlsx.py`, `ablate.py`, `neutral.py`).
- [ ] **Persona `mode` vs RBAC Separation (ADR-0006 §4.1–4.2):** Rename `role`/`roles` in `soul.yaml` to `mode`/`modes`, fall back across assigned roles in `RoleRegistry.policy_for`, tighten default `guest` seed, and drop `people.role`.
- [ ] **Extension DX & Unified Job Engine (ADR-0005 §4.1–4.4):** Ship `BaseGateway` + `@register_gateway` (with gateway-owned `default_role_policy`, `check_access`, and dynamic gateway config options), local plugin mode (`plugin.local`, `plugins install <dir>`, `plugins digest`, `@plugin.tool`), unified job engine (`task` kind, `/task`, step-logged recipes), and native Anthropic `/v1/messages` adapter.

## Governance & Repository Invariants

- [x] ADRs re-baselined to clean-slate English-only `ADR-0001..0008` (ADR-0008).
- [x] Pre-release `README.id.md` removed; `AGENTS.md` pruned to timeless invariants.
- [x] `scripts/adr.py --check` passing across all active ADRs.
