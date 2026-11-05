# LOG

Engineering log for MonoAgens. Short entries, newest first, decisions and behaviour only.

## 2026-11-06 — v0.1.0 Remediation Complete (Phases P0–P4)
- P0: standby boot writes nothing (soul-rule sync runs on the primary path), a resumed `per_…` actor keeps its identity, percept hints persist on `turns`, review (`polish → privacy.review → cross_chat_leak`) runs before the `reviewed` checkpoint and again after a `drafted` resume, prompt `safety`/`research` blocks are pinned, thread char accounting includes quotes/media/inline text, `character.polish` refuses rewrites that change numbers or dates, the door requires an explicit `origin`, approvals carry `action_id`/origin/batch with a unique index and a 3-tier policy, tool sends use deterministic ids, `scope_key` reconciliation is prefix-based, `turn_steps` opens `running` before execution, guard wrappers/IFS/SAFE_HEADS/squid IPv6 are hardened, plugins verify `deps/` and refuse path escapes, `/link` is rate-limited and row-locked with unlink revocation, the `harness`/`cli` gateways are gone (core `monoagens console`), unknown `engine.*`/`soul.yaml` keys fail at load, and naive datetimes are gone.
- P1: core `send_message`/`send_file`/`thread_history` with gateway-owned compatibility aliases, multi-attachment envelope + `att_…` handles, `/relay` and the `relay` tool, pluggable `web_search` providers with a fallback chain, control-pool lease heartbeats, interim/thinking/step streams, run budget and round-2 vision retry, and atomic SQL lease fencing across every mutating store.
- P2: native pgvector + tsvector hybrid retrieval (RRF, privacy-scoped), mem0 retired, and detector precision (word boundaries, value-shaped credentials, crisis idioms, privacy context words).
- P3: typed `TurnRuntime`/`TurnState`/`TurnCoordinator` with `interrupt_thread`, the vendored 4.8k-line affect engine replaced by native PAD math, every module ≤800 lines with an empty `OVER_CAP_ALLOWED`, persona `mode`/`modes` separated from RBAC (`people.role` dropped), `BaseGateway`/`@register_gateway`, `@plugin.tool`, the unified `task` capability, the native Anthropic `/v1/messages` adapter, and the speech/realtime seam.
- P4: dataclass-derived `templates.py` with banner/comment-preserving `yaml_io`, the full operator CLI (`start|stop|restart|status`, `logs`, `run`, `reload`, `setting`, `console`, `update`, `doctor`, `backup`/`restore`, `uninstall`, gateway/plugin/tool lifecycle), the dynamic installer/wizard, and the ADR promotion to `implemented`.
- Verification: `pytest -q` 1147 passed; `ruff check monoagens scripts tests plugins migrations` clean; `scripts/types.py --check` pyright 0 errors; `scripts/adr.py --check` 8/8 implemented; `scripts/selfcheck.py --check` ok with an empty module-cap allowlist.

## 2026-11-05 — Clean-Slate ADR Rebaseline and Governance Streamlining (ADR-0001..0008)
- Archived pre-rebaseline ADRs to `docs/archive/adr-pre-rebaseline/` and established the unified English-only ADR set (`ADR-0001` through `ADR-0008`) under `docs/v0.1.0/adr/`.
- Ratified dedicated single-deployment invariants (`1 Deployment = 1 Soul = 1 PostgreSQL Database = 1 Primary Process`) optimized for lightweight 350–500 MB RAM execution.
- Defined the three-phase remediation roadmap (`TASK.md`): P0 (resume identity, review ordering, exit door origin, send idempotency, guard/plugin/link hardening, container/CLI fixes), P1 (core cross-gateway messaging, native pgvector+tsvector hybrid memory without Mem0, word-boundary detectors, non-blocking async I/O, atomic lease fencing), and P2 (typed turn pipeline, PAD module replacement, persona mode separation, and extension SDK).

## 2026-11-05 — Native Provider Wire Adapters (ADR-0005)
- Separated wire protocol facts into `monoagens/providers/` (`openai`, `gemini`, `anthropic`). Implemented native Gemini `generateContent` support with `safetySettings` and `topK` controls.

## 2026-11-04 — Terminal Setup Wizard and Location-First Init (ADR-0007)
- Replaced manual config scaffolding with an interactive terminal curses setup wizard (`monoagens init`), supporting masked credentials, provider discovery, and zero-default voice tuning.

## 2026-11-03 — Layered Privacy and Memory Access Controls (ADR-0002)
- Shipped deterministic evidence collection (language pack + Presidio/spaCy NER + MiniLM centroid) and privacy policy (L0–L4) with subject veto and room-level disclosure controls.

## Before 2026-11-03 — Foundation Development (v0.1.0 dev)
- Established the core autonomous agent harness: PostgreSQL transactional intake and turn lifecycle checkpoints, CAS primary engine lease, RBAC role system with protected administrator, declarative automations, and pluggable gateway adapters (`cli`, `whatsapp`, `harness`).
