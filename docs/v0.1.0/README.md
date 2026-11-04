# ADR — v0.1.0

The decision record for this repository. Each ADR states one decision, the options that were
rejected, the configuration it introduces, how it is measured, and which code it refers to.

**English is the source language** (`adr/NNNN-slug.md`). The Indonesian copy lives in `adr/id/` and is
marked as such at the top of every file; it is written by hand and carries the hash of the English
source it belongs to.

## How to work with it

```
python scripts/adr.py --check            # structure gate (also run by tests/test_adr.py)
python scripts/adr.py --new "Judul ADR"  # scaffold the next number
python scripts/adr.py --translate-check  # fail when a derivative is stale (source hash moved)
```

`--check` is the contract of ADR-0009 §4.4: canonical sections, monotonic numbering, fence-aware
headings, resolvable `§` references, register synonyms scoped to definition lines, a footer naming the
file's own path, and an `Implementation` line whose value is `implemented`, `partial`, or `draft`. The
checks are regex and path work — no model, no network.

The Indonesian copy under `adr/id/` is written by hand and carries the hash of its English source in
the first line. `--translate-check` is the gate: edit the source, edit the copy on the same lines, then
re-stamp the hash. No machine translator ships with the repo.

## Index

| ADR | Judul | Folder | Implementation |
|---|---|---|---|
| [0000](adr/0000-initial-invariants.md) | Initial Invariants — Surface Shape, Language Data, Embodiment-Neutral Seam | root | partial |
| [0009](adr/0009-adr-home.md) | ADR Home, Versioned Documents Folder & Guards | root | implemented |
| [0027](adr/0027-distribution-web-config.md) | Distribution and Web-First Configuration — One Core Image, Default-Off Capabilities | root | partial |
| [0028](adr/0028-plugin-model.md) | Plugin Model — One Installable Unit for Fetched Capability | root | implemented |
| [0029](adr/0029-test-store.md) | Test Store Is the Real Store — Postgres+pgvector, One Test Database, Truncate per Test | root | implemented |
| [0030](adr/0030-install-and-init.md) | On-Machine Install and Interactive Init — Our Own Wizard, One Installer, Two Stacks | root | partial |
| [0031](adr/0031-engine-structure.md) | Engine Structure — Layout, Growth Budget, and Toolchain Baseline | root | implemented |
| [0001](adr/engine/0001-memory-source-of-truth.md) | Memory Source of Truth — Postgres Canonical, Mem0 as Memory Intelligence | engine | implemented |
| [0002](adr/engine/0002-capture-trigger.md) | Capture Trigger — Deterministic Gate, Novelty Check, Batch Assess | engine | implemented |
| [0003](adr/engine/0003-cognition-worker.md) | Cognition Worker & Model Batching — Gate vs Label Boundary | engine | implemented |
| [0004](adr/engine/0004-labeling-fallback.md) | Fallback Labeling — Provisional Canonical with `level NULL` as a Sentinel | engine | partial |
| [0005](adr/engine/0005-retention-decay.md) | Retention & Decay — hybrid TTL, Renewal On Use, and Auto Pin | engine | implemented |
| [0006](adr/engine/0006-group-privacy.md) | Group Privacy & Pre-Filter — Ceiling Before Prompt, Self-Capture | engine | implemented |
| [0007](adr/engine/0007-capability-authorization.md) | Capability vs Authorization — Three Permission Classes and Approval as a Continuation of Tool Permissions | engine | implemented |
| [0008](adr/engine/0008-memory-integrity.md) | Memory Integrity — Provenance, Trust, Instruction Quarantine, Anti-Poisoning | engine | implemented |
| [0010](adr/engine/0010-reflection-disputed.md) | Reflection, Contradiction & Status `disputed` | engine | implemented |
| [0011](adr/engine/0011-crash-safety.md) | Crash Safety & Durability: Transactional Queue, Job Claim, Neat Shutdown | engine | implemented |
| [0012](adr/engine/0012-lease-safety.md) | Lease Safety: CAS, Fencing Token, and Database Clock | engine | implemented |
| [0013](adr/engine/0013-turn-lifecycle.md) | Turn Lifecycle, Resume, Backlog & Notice | engine | implemented |
| [0014](adr/engine/0014-automation.md) | Automation → Event → Action, Owner Authority, and Sandbox as Steps | engine | implemented |
| [0015](adr/engine/0015-capability-manifest.md) | Layered Payload & Capability Manifest | engine | implemented |
| [0016](adr/engine/0016-schema-migration.md) | Canonical Schema & Migration Scheme, One DDL, One Alembic Baseline | engine | partial |
| [0017](adr/engine/0017-turn-isolation-timeout.md) | Turn Isolation, Deadline, and Primary Gate | engine | implemented |
| [0018](adr/engine/0018-gateway-contract.md) | Gateway Contract — Core, Rich, Directory | engine | implemented |
| [0019](adr/engine/0019-observability.md) | Observability — Structured Log, Token Accounting, and `/status` | engine | implemented |
| [0020](adr/engine/0020-procedural-memory.md) | Derivative Procedure — Skill as Soul Growth, Not a Tool | engine | implemented |
| [0021](adr/engine/0021-tool-output-contract.md) | Tool Output Contract, `grep`, and `fetch` | engine | implemented |
| [0022](adr/engine/0022-administrator-role.md) | Role `administrator` | engine | implemented |
| [0023](adr/engine/0023-relay-consent.md) | Relay Consent — Forwarding Personal Answers to Others | engine | implemented |
| [0024](adr/engine/0024-tool-input-contract.md) | Tool Input Contract — Argument Validation, Async Handlers, and Shell `env` | engine | implemented |
| [0025](adr/engine/0025-turn-cost.md) | Turn Cost — Stable Prefix, Gating `polish`, and Call Budgets | engine | implemented |
| [0026](adr/engine/0026-search.md) | Full-Text Search of Messages, Trigrams, and Scope | engine | implemented |
| [0032](adr/engine/0032-address-and-command.md) | One Address, One Command — Embodiment-Neutral Output, Refusal over Guessing | engine | implemented |
| [0034](adr/engine/0034-prompt-parts.md) | Prompt Parts — One Registry for Block Metadata, Not a Second Tool Contract | engine | partial |
| [0035](adr/engine/0035-app-scoped-skills.md) | App-Scoped Skills — A Namespace Above `scope`, Not a Fourth Audience | engine | implemented |
| [0036](adr/engine/0036-role-model.md) | Role Model — Named Bundles, One Protected `administrator`, No Role Types | engine | implemented |
| [0037](adr/engine/0037-module-budgets.md) | Module Budgets — Split Before the Cap, One Reason per Module | engine | implemented |
| [0038](adr/engine/0038-privacy-layers.md) | Layered Privacy — Evidence Before Level, Deterministic Policy, Advisory Models | engine | partial |
| [0039](adr/0039-setup-terminal.md) | Init as a First-Class Terminal Interface | root | implemented |
| [0040](adr/0040-monoagens-naming.md) | Naming — MonoAgens and the Persistent Agent Harness | root | draft |
| [0041](adr/engine/0041-voice-layer-off-by-default.md) | Voice Layer Configuration — One Section, Off by Default, and a Fully Commented Config | engine | draft |
| [0042](adr/engine/0042-sync-on-start.md) | Sync on Start — File Changes Apply Without the CLI | engine | draft |
| [0043](adr/engine/0043-research-contract.md) | Research Contract — Model Prior, Websearch, and the Ephemeral ResearchCard | engine | draft |
| [0044](adr/engine/0044-provider-wire-adapters.md) | Provider Wire Adapters — Native Gemini First, One Seam | engine | draft |
| [0045](adr/engine/0045-character-without-local-packs.md) | Character Without Local Packs — Soul, Exemplars, No Gender Gate, No Level Table | engine | draft |
| [0033](adr/0033-neutral-core.md) | Neutral Core, Specialized Edges — No Culture, Transport, or Vendor in Mechanism | root | implemented |

ADRs are grouped by folder: cross-cutting decisions at the `adr/` root, engine decisions under
`adr/engine/`, web/panel decisions under `adr/web/`. Numbers are chronological and unique across the
whole home, so a folder may look sparse (`engine/` has no `0009`, the root holds `0000`, `0009`,
`0027`-`0031`): a move leaves a gap on purpose and a number is never reused. Each folder keeps its
Indonesian copies in its own `id/` subfolder.

## Status

```
python scripts/adr.py --check
OK: 46/46 ADR lolos check; folder root=10, engine=36, web=0; implemented=36 partial=4 draft=6; sinonim digerbang=119 baris prosa=44

python scripts/adr.py --translate-check
TURUNAN 46 berkas, 0 masalah
```

Re-run both after any edit. The Indonesian copies carry the source hash, so `--translate-check` fails
the moment one drifts.
