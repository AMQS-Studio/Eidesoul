# ADR — v0.1.0

The decision record for this repository. Each ADR states one decision, the options that were
rejected, the configuration it introduces, how it is measured, and which code it refers to.

**English is the source language** (`adr/NNNN-slug.md`). The Indonesian copy lives in `adr/id/` and is
marked as such at the top of every file; the copy may be the original human text or a machine
derivative, and both carry the hash of the English source they belong to.

## How to work with it

```
python scripts/adr.py --check                 # structure gate (also run by tests/test_adr.py)
python scripts/adr.py --new "Judul ADR"       # scaffold the next number
python scripts/adr.py --translate 9           # Indonesian copy, structure only (default)
python scripts/adr.py --translate all --translator opus   # Indonesian copy with content translated
python scripts/adr.py --translate-check       # fail when a copy is stale (source hash moved)
```

`--check` is the contract of ADR-0009 §4.4: canonical sections, monotonic numbering, fence-aware
headings, resolvable `§` references, register synonyms scoped to definition lines, a footer naming the
file's own path, and an `Implementation` line whose value is `implemented`, `partial`, or `draft`. The
checks are regex and path work — no model, no network.

Two translation backends, both described in ADR-0009 §4.7:

| Backend | What it translates |
|---|---|
| `glossary` (default) | structure only: section headings, header labels, footer. Content lines stay English and are counted as `konten 0/N` |
| `opus` | content too (paragraphs, bullets, table cells) via a local NMT model. Code fences and inline spans are never sent; protected spans are restored afterwards |

`opus` is **dev tooling, never a package dependency** — `pyproject.toml` and `uv.lock` do not change,
there is no credits row, and when the stack is missing the tool fails with an install hint instead of
writing a half-translated file. A working local setup on this host:

```
uv venv --python 3.12 .venv-mt
uv pip install --python .venv-mt/bin/python "transformers<5" sentencepiece
uv pip install --python .venv-mt/bin/python torch --torch-backend auto   # CUDA build when a GPU exists
.venv-mt/bin/python scripts/adr.py --translate all --translator opus
```

(`transformers>=5` dropped the Marian tokenizer these models need, hence the pin; `.venv-mt/` is
git-ignored and the model cache lives in the user's Hugging Face cache.) The device is auto-detected
and reported in the copy's header (`@cuda` / `@cpu`). Memory is bounded by design: four torch threads,
batches of eight pieces, paragraphs split at 400 characters **before** inline spans are masked, and an
output cap clamped to the model's own `max_length`. Measured on this laptop:

| Build | 26 ADRs | Peak RSS | Peak VRAM |
|---|---|---|---|
| CPU (before the bounds) | — | 7.5 GB, swap churn | — |
| CPU (after the bounds) | ~70 s per ADR | 1.5 GB | — |
| CUDA (this repo's `.venv-mt`) | 3 m 27 s total, ~8 s per ADR | 1.9 GB | 2.9 GB |

An earlier CUDA run died with `indexSelectSmallIndex` because the output cap exceeded the model's
512 positions; the cap now follows `model.config.max_length` and long paragraphs are split, so that
failure cannot come back silently.

## Index

| ADR | Judul | Folder | Implementation |
|---|---|---|---|
| [0000](adr/0000-initial-invariants.md) | Initial Invariants — Surface Shape, Language Data, Embodiment-Neutral Seam | root | draft |
| [0009](adr/0009-adr-home.md) | ADR Home, Versioned Documents Folder & Guards | root | implemented |
| [0027](adr/0027-distribution-web-config.md) | Distribution and Web-First Configuration — One Core Image, Default-Off Capabilities | root | draft |
| [0028](adr/0028-plugin-model.md) | Plugin Model — One Installable Unit for Fetched Capability | root | draft |
| [0029](adr/0029-test-store.md) | Test Store Is the Real Store — Postgres+pgvector, One Test Database, Truncate per Test | root | draft |
| [0030](adr/0030-install-and-init.md) | On-Machine Install and Interactive Init — Our Own Wizard, One Installer, Two Stacks | root | draft |
| [0001](adr/engine/0001-memory-source-of-truth.md) | Memory Source of Truth — Postgres Canonical, Mem0 as Memory Intelligence | engine | partial |
| [0002](adr/engine/0002-capture-trigger.md) | Capture Trigger — Deterministic Gate, Novelty Check, Batch Assess | engine | partial |
| [0003](adr/engine/0003-cognition-worker.md) | Cognition Worker & Model Batching — Gate vs Label Boundary | engine | draft |
| [0004](adr/engine/0004-labeling-fallback.md) | Fallback Labeling — Provisional Canonical with `level NULL` as a Sentinel | engine | partial |
| [0005](adr/engine/0005-retention-decay.md) | Retention & Decay — hybrid TTL, Renewal On Use, and Auto Pin | engine | draft |
| [0006](adr/engine/0006-group-privacy.md) | Group Privacy & Pre-Filter — Ceiling Before Prompt, Self-Capture | engine | partial |
| [0007](adr/engine/0007-capability-authorization.md) | Capability vs Authorization — Three Permission Classes and Approval as a Continuation of Tool Permissions | engine | partial |
| [0008](adr/engine/0008-memory-integrity.md) | Memory Integrity — Provenance, Trust, Instruction Quarantine, Anti-Poisoning | engine | draft |
| [0010](adr/engine/0010-reflection-disputed.md) | Reflection, Contradiction & Status `disputed` | engine | draft |
| [0011](adr/engine/0011-crash-safety.md) | Crash Safety & Durability: Transactional Queue, Job Claim, Neat Shutdown | engine | draft |
| [0012](adr/engine/0012-lease-safety.md) | Lease Safety: CAS, Fencing Token, and Database Clock | engine | partial |
| [0013](adr/engine/0013-turn-lifecycle.md) | Turn Lifecycle, Resume, Backlog & Notice | engine | draft |
| [0014](adr/engine/0014-automation.md) | Automation → Event → Action, Owner Authority, and Sandbox as Steps | engine | draft |
| [0015](adr/engine/0015-capability-manifest.md) | Layered Payload & Capability Manifest | engine | draft |
| [0016](adr/engine/0016-schema-migration.md) | Canonical Schema & Migration Scheme, One DDL, One Alembic Baseline | engine | draft |
| [0017](adr/engine/0017-turn-isolation-timeout.md) | Turn Isolation, Deadline, and Primary Gate | engine | draft |
| [0018](adr/engine/0018-gateway-contract.md) | Gateway Contract — Core, Rich, Directory | engine | partial |
| [0019](adr/engine/0019-observability.md) | Observability — Structured Log, Token Accounting, and `/status` | engine | partial |
| [0020](adr/engine/0020-procedural-memory.md) | Derivative Procedure — Skill as Soul Growth, Not a Tool | engine | partial |
| [0021](adr/engine/0021-tool-output-contract.md) | Tool Output Contract, `grep`, and `fetch` | engine | draft |
| [0022](adr/engine/0022-administrator-role.md) | Role `administrator` | engine | draft |
| [0023](adr/engine/0023-relay-consent.md) | Relay Consent — Forwarding Personal Answers to Others | engine | draft |
| [0024](adr/engine/0024-tool-input-contract.md) | Tool Input Contract — Argument Validation, Async Handlers, and Shell `env` | engine | draft |
| [0025](adr/engine/0025-turn-cost.md) | Turn Cost — Stable Prefix, Gating `polish`, and Call Budgets | engine | draft |
| [0026](adr/engine/0026-search.md) | Full-Text Search of Messages, Trigrams, and Scope | engine | draft |

ADRs are grouped by folder: cross-cutting decisions at the `adr/` root, engine decisions under
`adr/engine/`, web/panel decisions under `adr/web/`. Numbers are chronological and unique across the
whole home, so a folder may look sparse (`engine/` has no `0009`, the root holds `0000`, `0009`,
`0027`-`0030`): a move leaves a gap on purpose and a number is never reused. Each folder keeps its
Indonesian copies in its own `id/` subfolder.

## Status

```
python scripts/adr.py --check
OK: 26/26 ADR lolos check; implemented=1 partial=9 draft=17; sinonim digerbang=100 baris prosa=20

python scripts/adr.py --translate-check
TURUNAN 26 berkas, 0 masalah
```

Re-run both after any edit. The Indonesian copies carry the source hash, so `--translate-check` fails
the moment one drifts.

**Source language flip (2026-11-03).** English is now the source and the Indonesian copy moved to
`adr/id/`. The English text started as a local NMT pass (`opus-mt-id-en`) and was then read file by
file and repaired by hand: literal mistranslations, word order, grammar, mangled markdown, and 131
comment lines inside fences. What remains Indonesian is deliberate data: prototype sentences,
injection examples, Indonesian predicate pairs, and the register's own literal tokens. Fenced code and
every backticked identifier were verified byte-identical to the pre-pass snapshot, so the decisions,
numbers, and references did not move.

Two limits are deliberate, not bugs: everything inside a code or diagram fence stays verbatim
(ADR-0009 §4.7 — YAML keys, SQL columns, and mermaid node labels must not be rewritten by a model),
and inline spans (`` `kode` ``, **bold**, URLs, `ADR-XXXX`/`§N` references) are restored unchanged
after translation.
