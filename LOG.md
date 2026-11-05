# LOG

Engineering log for MonoAgens. Short entries, newest first, decisions and behaviour only.

## 2026-11-05 — ADR Consolidation and Governance Streamlining (ADR-0055)
- Owner approved the architectural simplification plan: English-only ADRs, removal of the translation gate, deletion of `README.id.md` pre-release, streamlining `AGENTS.md` into timeless invariants, and folding the task runner draft into ADR-0054.
- ADR index and checklist (`TASK.md`) aligned with the post-audit target state.

## 2026-11-05 — Sovereign Single-Tenant Architecture Decision (ADR-0048)
- Officially ratified the deliberate design choice: 1 Deployment = 1 Soul = 1 PostgreSQL Database = 1 Primary Process.
- Dismissed multi-tenant SaaS proposals (no `tenant_id` columns, no composite `engine_lease` PK, no shared RLS) to protect data sovereignty and commercial moat. Focused refactoring efforts on VPS efficiency ($5–$10/mo), native Postgres memory, and cross-gateway reliability.

## 2026-11-05 — Comprehensive System and Cross-Gateway Audit (ADR-0049..0054)
- Completed two independent architectural audits (`docs/audit.md`, `docs/audit2.md`) identifying core cross-gateway failures, word-boundary detector precision traps (FD-01..FD-12), write-only Mem0 vector search, and resume identity bugs.
- Defined phased remediation roadmap: P0 (correctness & security), P1 (cross-gateway tools, native pgvector+tsvector hybrid memory, non-blocking I/O), and P2 (structural pipeline simplification).

## 2026-11-05 — Native Provider Wire Adapters (ADR-0044)
- Separated wire protocol facts into `monoagens/providers/` (`openai`, `gemini`, `anthropic`). Implemented native Gemini `generateContent` support with safetySettings and topK controls.

## 2026-11-04 — Terminal Setup Wizard and Location-First Init (ADR-0030, ADR-0039)
- Replaced manual config scaffolding with an interactive terminal curses setup wizard (`monoagens init`), supporting masked credentials, provider discovery, and zero-default voice tuning.

## 2026-11-03 — Layered Privacy and Memory Access Controls (ADR-0038)
- Shipped deterministic evidence collection (language pack + Presidio/spaCy NER + MiniLM centroid) and privacy policy (L0–L4) with subject veto and room-level disclosure controls.

## Before 2026-11-03 — Foundation Development (v0.1.0 dev)
- Established the core autonomous agent harness: PostgreSQL transactional intake and turn lifecycle checkpoints, CAS primary engine lease, RBAC role system with protected administrator, declarative automations, and pluggable gateway adapters (`cli`, `whatsapp`, `harness`).
