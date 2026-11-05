# LOG

Engineering log for MonoAgens. Short entries, newest first, decisions and behaviour only.

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
