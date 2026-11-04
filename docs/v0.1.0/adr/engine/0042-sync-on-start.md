# ADR-0042: Sync on Start — File Changes Apply Without the CLI

- **Status:** accepted
- **Date:** 2026-11-04
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Context:** follows ADR-0027 (distribution), ADR-0030 (install and init), ADR-0031 (engine structure), ADR-0033 (neutral core), ADR-0039 (setup terminal), ADR-0041 (voice configuration), and ADR-0045 (voice tuning in `soul.yaml`); replaces the manual `config import` step for normal file edits.
- **Supersedes:** —
- **Related:** ADR-0027 (distribution), ADR-0030 (install and init), ADR-0031 (module structure), ADR-0033 (neutral core), ADR-0039 (setup terminal), ADR-0041 (voice configuration), ADR-0045 (voice tuning in `soul.yaml`)
- **Implementation:** draft — the decision is recorded; the hash sync and the startup call are not implemented in this ADR.
- **Location note:** the document store binds the engine runtime, so this ADR sits under `adr/engine/`. The Indonesian copy is `docs/v0.1.0/adr/engine/id/0042-sync-on-start.md`.

---

## 1. Context

The database documents are the source of truth; `config.yaml`, `soul.yaml`, and `roles.yaml` are seeds
plus mirrors. `DocumentStore.seed()` fills missing documents from the files, and an existing document
is left untouched. A manual edit after that requires `eidesoul config import`; the reverse direction
requires `eidesoul config export`.

That rule is safe but expensive in daily operation:

| Surface | Current path | Friction |
| --- | --- | --- |
| `roles.yaml` | edit the file, then `eidesoul config import` | roles are tuned often; every tuning round needs a CLI step |
| `soul.yaml` | edit persona or `voice`, then `eidesoul config import` | one CLI step for every character or voice tune |
| `config.yaml` engine | edit the section, then `eidesoul config import` | one CLI step |

`sync_documents: true` does not solve this: it only seeds missing documents. The operator sees the
file change and expects it to apply; the CLI step is easy to forget, and a forgotten step means the
engine runs on the old document while the file shows the new one.

The owner direction is to make normal edits apply on start, without a CLI step, while keeping the
database authoritative so a DB-side change can never be silently overwritten by a stale file.

## 2. Decision drivers

| Driver | Consequence |
| --- | --- |
| No CLI for normal edits | A changed `roles.yaml` or `soul.yaml` applies on the next start |
| DB stays authoritative | A conflict resolves in favour of the database, with a warning |
| No derived style | `soul.style` and the resolver are gone; the whole soul file is the unit |
| Bounded startup | Hash comparisons only; no resolver and no embedding at boot |
| No watcher | No new dependency and no long-running file watch; startup only |
| One writer | A Postgres advisory lock makes one process apply the file changes |
| Testable | Four sync cases, the first-sync case, and the missing-side case are tests |

## 3. Options considered

| Option | Summary | Value |
| --- | --- | --- |
| **A. Keep the manual `config import`** | the current rule | Rejected: the friction is the problem; a forgotten step runs the old document |
| **B. Watch the files** | a filesystem watcher applies edits live | Rejected: a new dependency, races with the writer, and standby instances |
| **C. Import every file on every start** | file wins, no comparison | Rejected: a stale or partial file overwrites a DB-side change |
| **D. Hash sync on start, DB wins conflicts** | compare file, DB, and last synced hash | **Chosen** |
| **E. File wins conflicts** | the file is the operator's word | Rejected: inverts the document authority and hides DB-side changes |
| **F. Keep `soul.style` and resolve on start** | derive pack selectors from the soul text | Rejected: ADR-0045 removes `soul.style` and the resolver |
| **G. Sync the whole soul file** | one hash, one import, no derived block | **Chosen** |

## 4. Decision

### 4.1 One sync pass on start

`DocumentStore.sync()` runs once per process, after the schema is ready and before the config is
loaded. It reads the host files, compares them with the documents and with the last synced hash
stored in `state`, and applies the missing direction.

The pass is guarded by a Postgres advisory lock so only one instance applies file changes; a standby
that cannot acquire the lock skips the pass and reads the documents.

| File | DB | Last synced hash | Action |
| --- | --- | --- | --- |
| changed | unchanged | file != last, db == last | import file into the DB |
| unchanged | changed | db != last, file == last | export the DB into the file mirror |
| changed | changed | both != last | **DB wins**: export the DB, warn |
| unchanged | unchanged | both == last | no-op |
| present | missing | no last | import file |
| missing | present | no last | export the DB |
| both present, no last | — | first sync | **DB wins**: export the DB, warn |
| missing | missing | — | no-op |

The hash is SHA-256 of the canonical content: the whole `roles.yaml`, the whole `soul.yaml`, and the
`engine` section of `config.yaml`. The host keys of `config.yaml` are host-local and are not
documents; they are read directly from the file.

`sync_documents: false` disables the pass entirely, as it disables `seed`, `import`, and `export`
today.

### 4.2 Soul file

The soul file is one unit: `description`, `background`, `persona`, `voice`, `examples`, `pronouns`, and
`rules`. There is no derived `style:` block and no resolver; ADR-0045 removes `soul.style`.

When the file changed and the DB did not, the sync imports the whole file into the `soul` document. When
the DB changed and the file did not, it exports the document into the mirror. A conflict follows the same
DB-wins rule as every other document. No model call, no embedding, and no keyword resolver runs at boot.

### 4.3 Roles and the engine section

`roles.yaml` has no resolver: a changed file is imported as the `role:<name>` documents. The file mode
stays `0600`; the sync logs hashes, never role bodies.

The `engine` section of `config.yaml` follows the same four-case table. A host-key edit (database,
workspace, sandbox, and the rest) is not a document change and is read from the file on start.

### 4.4 What stays explicit

- `eidesoul config import` and `eidesoul config export` remain for a forced direction and for
  recovery.
- A running process does not watch the files; an edit applies on the next start. A future `/reload`
  can reuse the same pass, but it is not part of this decision.

## 5. Configuration

No new configuration key is introduced. The existing host key controls the pass:

| Key | Default | Effect |
| --- | --- | --- |
| `sync_documents` | `true` | enables `seed`, `sync`, `import`, and `export`; `false` disables all of them |

Sync state lives in the existing `state` table, so there is no schema change:

| State key | Value |
| --- | --- |
| `sync:soul` | hash of the last synced `soul.yaml` |
| `sync:engine` | hash of the last synced `engine` section |
| `sync:role:<name>` | hash of the last synced role body |

The values are hashes only; no file content and no secret is stored in the state rows.

## 6. Metrics and gates

| Metric | Target |
| --- | ---: |
| CLI steps required for a `roles.yaml` edit | 0 |
| CLI steps required for a `soul.yaml` edit | 0 |
| DB-side edits lost to a stale file | 0 |
| Instances that apply a file change in the same pass | 1 |
| Sync cases covered by tests (four-case table, first sync, missing side) | 6 |
| Hash values written that contain file content | 0 |
| `sync_documents: false` file operations | 0 |
| `pytest -q` | pass |
| `ruff check` | pass |
| `python -P scripts/types.py --check` | pass |
| `python -P scripts/adr.py --check` | pass |
| `python -P scripts/adr.py --translate-check` | pass |
| `python scripts/selfcheck.py --check` | pass |

## 7. Acceptance criteria

- [ ] A changed `roles.yaml` applies on the next start without `eidesoul config import`.
- [ ] A changed `soul.yaml` — including the `voice` block — imports on the next start.
- [ ] No resolver, model call, or embedding runs during the sync.
- [ ] A DB-only change exports the document into the file mirror.
- [ ] A file-and-DB change resolves in favour of the DB, exports the mirror, and warns.
- [ ] The first sync with no state resolves in favour of the DB and warns.
- [ ] A missing file imports from the DB; a missing document imports from the file.
- [ ] A standby cannot apply the pass while another instance holds the advisory lock.
- [ ] `sync_documents: false` performs no seed, sync, import, or export.
- [ ] No watcher or new runtime dependency is added.

## 8. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| A stale file overwrites a DB-side change | The DB wins every conflict; the mirror is rewritten and a warning is logged |
| Two instances apply the same edit | A Postgres advisory lock makes one writer; the other skips |
| Startup becomes slow | The sync is hash comparisons; no resolver and no model load |
| State rows drift or are lost | The hash is recomputed from the file and the document; a missing state row follows the first-sync rule |
| `roles.yaml` is world-readable | The file stays `0600`; the sync logs hashes, never bodies |
| A running process misses the edit | Startup only; the docs state that a restart is required until a `/reload` exists |

## 9. Interaction with other ADRs

- **ADR-0027**: `sync_documents` stays a host key with the same default; the pass respects the
  default-off capability rule.
- **ADR-0030**: `init` still writes the seed files; the first `run` imports them without a second
  command.
- **ADR-0031**: the sync stays in `engine/stores/documents.py`; no new module and no budget change.
- **ADR-0033**: the sync carries no language, culture, transport, or vendor vocabulary.
- **ADR-0039**: the setup receipt still writes the files; the operator no longer needs a follow-up
  import for a later edit.
- **ADR-0041**: the voice section is part of `soul.yaml` after ADR-0045; the sync uses the same pass and
  applies the whole soul document.

## 10. Code references

- `eidesoul/engine/stores/documents.py` — `sync()`, `seed()`, `import_from_files()`,
  `export_to_files()`, and the hash comparison.
- `eidesoul/engine/config.py` — the `sync_documents` host key.
- `eidesoul/engine/schema.py` — the `state` table used for the sync hashes.
- `eidesoul/engine/jobs/lease.py` — the advisory lock used to make one writer.
- `eidesoul/__main__.py` — the `run`, `console`, and `database seed` call sites.
- `eidesoul/gateways/whatsapp/cli.py` — the gateway document load.
- `eidesoul/admin/database.py` — the database seed path.
- `eidesoul/engine/soul.py` — the soul document, including the future `voice` block.
- `tests/` — the four-case table, first sync, missing side, and lock coverage.

---

*This ADR lives at `docs/v0.1.0/adr/engine/0042-sync-on-start.md`.*
