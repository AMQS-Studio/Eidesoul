# ADR-0009: ADR Home, Versioned Documents Folder & Guards

- **Status:** accepted
- **Implementation:** implemented — evidence: `docs/v0.1.0/adr/`, `scripts/adr.py --check`, `tests/test_adr.py`; run results are recorded in `docs/v0.1.0/README.md`
- **Date:** 2026-11-02
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Context:** closes the ADR-0001 §14 queue; intersects ADR-0015 §4.9 (capability `docs`)
- **Supersedes:** —
- **Related:** ADR-0001 (§14 queue), ADR-0015 (§4.9 product document)
- **Location note:** the ADR lives at `docs/v0.1.0/adr/` and has no second copy.
- **Amendments:** 2026-11-02 — the §4.4 skeleton contract was tightened (canonical titles, relative order, the regions where extra sections may go, `Amendments` as a header line, `§...` references that must resolve) and an **audit of the 15 ADRs** was added: 7 conforming, 8 missing sections; **the fixes were applied on the same day → 15/15 conforming** (`0002`/`0004` also moved `Amendments` into the header); §4.10 now only moves files. 2026-11-03 — audit: the check rules gained "a heading must not sit inside a fence; the check must be fence-aware"; the earlier audit (2026-11-02) reported 15/15 even though ADR-0015 had a heading inside a fence — that defect class is now recorded, and §4.4 mentions it. 2026-11-03 — **ADR-0016** (schema & migration) joined the set: the count became 16 files, ownership of the `memories` DDL moved from ADR-0001 to ADR-0016 §4.1, and ADR-0001 §4 now holds only a table + role list. 2026-11-03 — the ADR-0015 document allowlist is path-based, so `docs/v0.1.0/README.md` (the ADR index) can be distinguished from the product `README.md`. 2026-11-03 — owner decision: the §4.12 **term register** was added (canonical terms + owner + forbidden synonyms), and `--check` rejects forbidden synonyms inside YAML/SQL fences; the root problem was not one term pair but the absence of a vocabulary owner. 2026-11-03 — the register was updated: `send` is the only send capability (tools + automation steps use the same implementation); `send_to` is recorded as a name that is **merged once the code catches up**, not as a forbidden synonym today. 2026-11-03 — audit: §4.4 adds the `Code references` label rule (a real citation, or the label `proposal`). **2026-11-03** — the set moved into the repo (`docs/v0.1.0/adr/` + the index `docs/v0.1.0/README.md`); the `Implementation` header (`implemented`/`partial`/`draft` + evidence) entered the §4.4 contract; the register's synonym cells gained a scope (no parentheses = every definition line, `(kolom)`/`(column)` = SQL columns, `(nama role)` = `roles:` blocks, a mention of another entity = prose that is not gated) with the `kolom lama`/`legacy` marker exception; `scripts/adr.py` (`--check`/`--new`/`--translate`/`--translate-check`, no LLM) and `tests/test_adr.py` became its enforcers. **2026-11-03** — §4.7/§4.8: two derivative backends (`glossary` = structure only, an honest `konten 0/N`; `opus` = content via a local NMT, dev tooling without a dependency), plus a shape limit locked by tests: fence lines are never sent, markers (`###`/`>`/`1.`/`-`) and table column counts are preserved, `**tebal**`/bold markers are protected while their text is still translated, a paragraph >400 characters is split per sentence, the output cap follows `model.config.max_length`, and memory is bounded (4 threads, batch 8).

---

## 1. Context

**16 ADRs live outside the repository.** All of them sit in `/home/muqsith/Documents/`. As a result,
architecture decisions do not take part in `git log`/`git blame`, cannot be linked from `README.md` or
`AGENTS.md`, do not take part in change review, and are lost along with personal folders.

**The repo rules forbid them entry today.** `AGENTS.md:17-18`: “Tracked docs are `TASK.md`,
`README.md`, `README.id.md`, `LOG.md`, `AGENTS.md`, and `soul.yaml`. Do not create other tracked
`.md` files.” Evidence of the current state: `git ls-files '*.md'` returns only those five files.
The ban makes sense (it prevents stale wild documents), but it is precisely **architecture decisions** —
the most expensive artifact to lose — that are held outside.

**The language has no permission yet either.** `AGENTS.md:7-12` make English the default and limit the
non-English surface to the catalog `eidesoul/lang/`, `soul.yaml`, `eidesoul/library/**`, and
`README.id.md`. ADR prose is Indonesian today; without a change of rules, moving it into the repo means
breaking our own rules in secret.

**There are no rules for shape, status, or numbering yet.** Two classes of incident are already real
across these 15 ADRs: (a) ADR-0004, ADR-0005, ADR-0007, and ADR-0008 were amended after acceptance, so
explicit amendment rules are needed; (b) one ADR (0015) carried a bare code fence inside a table cell
— it broke Markdown rendering and was only caught because it happened to be checked by hand, not
because a gate existed.

**The queue rides along in the memory ADR.** ADR-0001 §14 doubles as the queue for the next ADR and as
the status list of all ADRs. The project decision list rides along in one ADR about the memory source
of truth — two roles in one file.

**Release documents have no home yet.** Architecture, capabilities, and release notes are scattered
today across `/home/muqsith/Documents/` and `references/` (which holds chat logs, not documentation).

**What has been verified so that this decision is safe:**

| Fact | Evidence | Consequences |
|---|---|---|
| `docs/` does not exist yet | `ls` of the repo root | Create a new directory at the root |
| Docs are not part of the distribution | `pyproject.toml:47-48` — `include = ["eidesoul*"]` | Zero impact on the wheel/Docker |
| Docs are not scanned by analysis | `sonar-project.properties:5` — `sonar.sources=eidesoul,scripts` | No new exclusion is needed |
| A guard pattern already exists | `README.md:254-258` and `scripts/{patterns,fakta,affect}.py --check` | The ADR guard script follows proven conventions |
| The package version is still pre-release | `pyproject.toml` `version = 0.0.0.dev0`, `AGENTS.md` §Shape | The version folder name must be honest: a **target** marker, not a release claim |

## 2. Decision drivers

1. **The decision follows the code history** — the same blame, review, and `grep` as code.
2. **One place for one thing** — one live ADR set, one list, with no two copies that could drift.
3. **Documents are versioned, decisions are not** — a release folder may be frozen; ADR numbers are
   global and never reused.
4. **The repo rule must be honest** — if the old rule gets in the way, the rule is changed explicitly,
   not broken quietly.
5. **If a script can check it, do not rely on discipline** — the `scripts/*.py --check` pattern already
   exists.
6. **Zero new dependencies, zero new CI** — this repo has no `.github`; the gate must run through pytest
   and a script an operator can run.
7. **One language source** — one file is the source and the other a derivative; the two must not
   compete over the truth.

## 3. Options considered

| Option | Shape | Advantages | Drawbacks | Status |
|---|---|---|---|---|
| **A. Status quo** | ADRs stay outside the repo | Zero changes | Not in history; lost with personal folders; cannot be linked | Rejected |
| **B. `adr/` at the root** | The ADR directory sits next to `eidesoul/`, `tests/`, `deploy/` | Most visible | The root already holds 21 entries and mixes in code; no place for release documents | Rejected |
| **C. `docs/adr/` versionless + `docs/v0.1.0/` for narrative** | Two document homes | Decisions never need to be moved | Two document places for one release; the owner picks one version folder as the document home | Rejected |
| **D. `docs/<version>/` as the document home + `adr/` inside it** | One folder per release cycle; `docs/v0.1.0/README.md` becomes the index; the one live ADR set moves with `git mv` when a new cycle opens | One home for one release; history intact; zero distribution impact | A strict move rule is needed so that no two sets exist | **Selected** |
| **E. Wiki / GitHub Discussions** | Outside the file tree | Pleasant to read | Needs a network, cannot be `grep`ped offline, not part of change review | Rejected |

## 4. Decision

> **The ADR lives in the versioned documents folder in the repo — today `docs/v0.1.0/adr/`, with the index
> `docs/v0.1.0/README.md` — one live set moved intact with `git mv` when a new release cycle
> opens, guarded by `scripts/adr.py`, which checks the shape, status, number, links, and translation
> freshness. `AGENTS.md` is amended: `docs/**` joins the tracked document list and the valid
> non-English surface.**

### 4.1 Home and naming

```
docs/v0.1.0/ # dokumen siklus rilis v0.1.0 (target pertama)
├── README.md # indeks: daftar dokumen + daftar ADR (nomor · judul · status · tanggal · berkas)
└── adr/
 ├── 0001-initial-invariants.md # root: lintas-potong (fondasi semua permukaan)
 ├── 0009-adr-home.md # root: rumah ADR dan penjaganya
 ├── engine/ # ADR yang mengikat runtime engine
 │ ├── 0001-memory-source-of-truth.md
 │ ├── id/ # salinan Indonesia folder ini
 │ └── ...
 ├── web/ # ADR permukaan web/panel
 │ ├── id/
 │ └── ...
 └── id/ # salinan Indonesia untuk ADR root
 └── 0001-initial-invariants.md
```

- File name `NNNN-slug.md`: four digits, leading zeros, lowercase kebab slug, no date.
- The **folder is the category, the number is the order of the decision**: the ADR home root holds
  cross-cutting decisions (foundations, the ADR home, distribution, plugins), `engine/` holds what binds
  the engine runtime, `web/` holds the web/panel surface.
- A number is **unique across the whole home, chronological, and never reused**; a `superseded` number
  stays used. Moving an ADR between folders does **not** renumber the rest: the gap is left on purpose
  (`engine/` has no `0009` because the ADR home moved to the root, and `web/` starts empty).
- The foundations ADR is `0000`, the single number below `0001`, because it binds every surface; `0001`
  onward is the chronological series.
- A reference is written `ADR-NNNN`. The explicit `ADR-<folder>/NNNN` form is accepted when a reader
  wants to name the folder; it becomes mandatory only if a number ever collides across folders, which
  the gate refuses as an ambiguous bare reference.
- The folder name `v<MAJOR>.<MINOR>.<PATCH>` marks the **release target** of that folder. Until it is
  released, `pyproject.toml` keeps the dev version (`0.0.0.dev0`) per `AGENTS.md` §Shape — a version
  folder is not a release claim, only a cycle marker.
- An ADR file is never rebound to another number; `ADR-XXXX` references always point to the original
  number.

### 4.2 One live set, moved between cycles

There may only be **one** directory `docs/*/adr/`. When the next release cycle opens
(`docs/v0.2.0/`), the whole set moves with `git mv` — file history preserved — so that:

- a release document that has shipped becomes **frozen** (no longer edited, except for link fixes);
- decisions stay one list, with no copy that could drift;
- an old release's narrative document links ADRs by **number + title**, not by a relative path to a
  folder that can move. A relative path to `adr/` is used only inside the live version folder.

A script enforces this rule: more than one `docs/*/adr` = check failure.

### 4.3 The index as the only list

`docs/v0.1.0/README.md` holds: one short line about the project, the list of narrative documents, and
the ADR list formatted `number · title · status · date · file`, where `file` is the path relative to
the ADR home (`engine/0016-schema-migration.md`). ADR-0001 §14 stops being a queue and turns
into a **pointer** to this index — one list, not two.

### 4.4 Mandatory skeleton

Every ADR has the following mandatory header and sections, with **exactly the canonical titles** and a
fixed relative order:

| Header section | Description |
|---|---|
| `Status` | `proposed` \| `accepted` \| `superseded` \| `deprecated` \| `rejected` |
| `Date` | ISO `YYYY-MM-DD` |
| `Deciders` | owner + reviewer |
| `Context` | the ADRs/designs it builds on |
| `Supersedes` / `Superseded-by` | reciprocal when there is a replacement |
| `Related` | other relevant ADRs |
| `Amendments` | optional; one line per dated change |
| `Implementation` | `implemented` \| `partial` \| `draft` + evidence or the concrete remaining work |

Mandatory sections, in order: `Context` → `Decision drivers` → `Options considered` →
`Decision` → `Configuration` → `Metrics and gates` → `Acceptance criteria` → `Risks and mitigations` →
`Interaction with other ADRs` → `Code references`.

Shape rules:

- section numbers rise **monotonically**, with no duplicates; skipping a number is not allowed;
- an **additional** section may exist, but only in two regions: after `Decision drivers` and before
  `Decision`, or after `Decision` and before `Code references`. The last numbered section is always
  `Code references`;
- `Amendments` is **not** a section of its own: it is a header line, one dated clause per change.
  One place for one thing - the change history is read from the header without a second list;
- a `##` heading **must not sit inside a fence**: when rendered it is not a section, and the numbering
  appears to jump. The check must be **fence-aware** (a heading "lost" inside a code block = a defect);
- an identifier used across ADRs must use the term from the **register §4.12**; a forbidden synonym
  inside a YAML/SQL fence → check failure;
- every ADR carries an `Implementation` header line whose value is `implemented`, `partial`, or `draft`.
  `implemented` = the decision already matches today's code **and** evidence is named; `partial` = part
  of it exists and the rest is named; `draft` = not in code yet. Any other value, or an `implemented`
  claim without evidence, counts as a defect: status must be verifiable, not remembered;
- a mandatory section that is genuinely empty **is still written** as one honest line
  ("There is no new configuration key in this ADR."), not deleted — so that the order can be checked;
- a cross-section reference (`§4.5`, `ADR-0008 §4.4B3`) must point to a title that really exists; the
  check verifies that every `§...` reference resolves in its destination file, and that every
  `ADR-XXXX` reference resolves to a real ADR (numbers are unique across the home, so a bare number
  resolves; `ADR-<folder>/NNNN` is the explicit form and would be required only if a collision
  appeared). Two exceptions so that the check does not misreport: a reference that **names a file
  other than itself** (`ADR-0001 §14`, `ADR-0015 (§4.9)`) is checked against that file, not this one;
  and a reference inside a `code span` (rule examples, such as `§11` in this sentence) is not scanned;
- every item in `Code references` must be one of: a citation that really exists in the repo, or the
  label `new module/script`/`proposal` — so that a "current state" claim can be distinguished from a plan.

**Audit 2026-11-02 over the 15 ADRs** (prototype of the rules above: canonical titles, numbering, extra
section regions, `§...` reference resolution):

| Initial state | File | Action |
|---|---|---|
| Conforms to the contract | 0002, 0004, 0005, 0008, 0009, 0010, 0014 (7) | — |
| Missing `Configuration`, `Metrics and gates`, `Interaction with other ADRs` | 0001 | three sections of one or two lines added; tail renumbered (Acceptance 9 →11, Risk 10 →12, Question 11 →14, References 12 →15) |
| Missing `Options considered` | 0003 | one honest line; §4→§5 including sub-sections, everything after shifts |
| `Configuration` nested as a sub-section | 0006 (`### 4.5`), 0007 (`### 4.4`) | promoted to the section `## 5. Configuration`; 0007 sub-sections 4.5–4.7 → 4.4–4.6 |
| Missing `Interaction with other ADRs` | 0011, 0012 | the tracked bullet moved into the existing `Related`/text; References shifts |
| Missing `Options`, `Decision`, `Interaction` | 0013 | `Options` one honest line; `Decision` a summary that points to the detail section; `Interaction`; numbering 3–14 → 5–17 |
| `##` heading without a number | 0015 | an unnumbered heading → `## 5.`; everything after becomes 6–11 |
| `Amendments` as a section rather than a header line | 0002, 0004 | content compressed into the `Amendments` header line; the separate amendment section was removed |

**Result after the fixes (2026-11-02): 15/15 conform to the contract** - monotonic numbering, every
canonical title present and in order, no `##` heading without a number, no dangling `§...` reference.
The fixes were made in the working documents; the §4.10 migration that follows only moves files.

**The 2026-11-03 audit found one defect class that had passed the 2026-11-02 inspection:** a numbered
heading **inside a fence** (`ADR-0015` §5), which made the document show 1-4 and then 6-11 while the
text-counting checker treated it as a valid section. It has been fixed (the line was deleted, sections
6-11 become 5-10); the fence-aware rule in §4.4 above is the lesson from it. The same audit also
verified 129 code claims and found 12 semantic conflicts between ADRs (list: `Eidesoul-Audit-ADRs-2026-11-03.md`).

Three error classes that this prototype caught, and that therefore went into the check list: a `§11`
reference that actually points to another file (it must be written `ADR-0001 §14`), a `##` heading
without a number, and a numbered heading inside a fence (which needs fence-aware scanning).

### 4.5 Status and amendment cycle

- `proposed` → `accepted` once. After `accepted`, **the decision is not changed in place**.
- Changes allowed through a dated amendment: sharpening a sentence, configuration/name detail, added
  test criteria, cross-links. An amendment **must not reverse the decision**.
- Reversing the decision = a new ADR with `Supersedes: ADR-XXXX`, and the old ADR gets
  `Superseded-by` + `Status: superseded`. Both directions must match.
- An ADR whose decision no longer applies and has no replacement → `deprecated`, with a one-line reason.

### 4.6 When **not** to write an ADR

One ADR = one decision that cannot be reversed without a trace. No ADR is needed for: variable naming,
log message shapes, a library choice that can be dropped in one commit, or anything else reversible and
local → a code comment or `TASK.md` is enough.

### 4.7 Languages: English source, Indonesian copy

- **English is the source.** `docs/v*/adr/NNNN-slug.md` is written in English, and the gate (§4.4) is
  built on English section titles (`Context` ... `Code references`) plus the `Implementation` values
  `implemented | partial | draft`.
- The **Indonesian copy** lives in the `id/` folder of the same folder —
  `docs/v*/adr/id/NNNN-slug.md` for a root ADR, `docs/v*/adr/engine/id/NNNN-slug.md` for an engine ADR —
  and opens with
  `> Naskah asli Indonesia — jangan sunting; sumber: <path relatif ke adr/> (sha256: <digest>)`
  followed by a note on how it was produced. A copy is optional per file; a **stale** copy (source hash
  moved) fails `--translate-check`.
- The copy may be the human original text (the state after the 2026-11-03 language flip) or a machine
  derivative; `scripts/adr.py --translate <N|all>` regenerates the machine kind. Only operators run
  it, and nothing calls it inside a turn. Two backends:
  - `--translator glossary` (default): **structure only** — section headings, header labels, footer —
    from the table inside the script. Content lines stay English and are counted honestly as
    `konten 0/N`.
  - `--translator opus`: content as well, through a local NMT model (`Helsinki-NLP/opus-mt-en-id`).
    The environment is **dev tooling, never a package dependency**: `pyproject.toml`/`uv.lock` do not
    change, there is no credits row, and without the stack the tool fails with an install hint instead
    of writing a half-translated file.
- Bounds the model needs: lines inside a code or diagram fence are never sent; `ADR-XXXX`/`§N`
  references, inline code, links, and URLs are protected by placeholders and restored; bold markers
  are masked so their text is still translated; paragraphs are split at 400 characters **before**
  masking; the output cap follows the model's own `max_length`; four torch threads and batches of
  eight pieces keep RSS near 2 GB. Knobs: `ADR_MT_DEVICE`, `ADR_MT_THREADS`, `ADR_MT_BATCH`.
- A piece whose protected spans do not come back intact keeps its source text, and a piece the model
  leaves in the source language keeps that text too: a copy is never dressed up as a translation it is
  not.

### 4.8 `scripts/adr.py`

| Mode | Function |
|---|---|
| `--check` | file names and number format; a number unique across the whole home, chronological, never reused (a move leaves a gap; `0000` is the foundations ADR); exactly one `docs/*/adr`; canonical English sections present and ordered; valid `Status`; ISO date; forbidden register synonyms (§4.12) in YAML/SQL fences; resolvable `ADR-XXXX`/`ADR-<folder>/XXXX`/`§N` references (a bare number must be unique; `ADR-<folder>/XXXX` is the explicit form); balanced fences and no bare fence inside a table cell; footer path; valid `Implementation` |
| `--new "<title>" [--dir root\|engine\|web]` | writes the skeleton of the next ADR, in the chosen folder, from the template, inventing nothing |
| `--translate <N\|all>` | writes or refreshes the copy inside its own folder's `id/`, recording the source hash, the backend, and the counters on the opening lines |
| `--translate-check` | compares each copy's recorded hash with its source; a stale or hash-less copy fails |
| `--translator glossary\|opus` | backend for `--translate`: glossary (structure only, default) or local NMT for content (dev tooling) |

`--check` prints every violation with file and line and exits non-zero. `pytest -q` runs it through
`tests/test_adr.py`, next to the `patterns.py`/`fakta.py`/`affect.py` guards. The
bare-fence-inside-a-table-cell class of defect it catches already happened once in ADR-0015.

### 4.9 Amending `AGENTS.md`

- `AGENTS.md:17-18` becomes: tracked documents = the five old files **+ `docs/v*/**`** (release
  documents and ADRs). The ban on wild documents still stands for everything else.
- `AGENTS.md:7-12` adds `docs/v*/**` as a valid non-English surface, noting that the copies under
  each `adr/**/id/` are derivatives, not manual edits of the source.
- One line is added to the `Verify` section: `python scripts/adr.py --check` runs alongside the other
  guard scripts.

### 4.10 Migration

1. Create `docs/v0.1.0/adr/` and move the 16 ADRs with a rename `ADR-0001-...` → `0001-...`.
2. Drop the `Location note` header line (no longer valid) and the footer
   “*There has been no change in the repository.*” (stale once in the repo).
3. Update the `Related`/`Context` lines that mention "stored outside the repo until ADR-0009".
4. Skeleton normalization (§4.4) **was already done in the working documents on 2026-11-02** (table in
   §4.4): eight files were completed without deleting content, `0015` was given a number, and
   `0002`/`0004` moved the amendment content from a section to the header line. The migration therefore no longer
   touches content — only file names, `Location note`, and the footer.
5. After the numbering changes, **update the `§...` references** that point at section numbers that
   shifted (e.g. the reference to the `ADR-0001 §14` queue) and make sure the reference check passes.
6. Write `docs/v0.1.0/README.md` as an index of the file contents (number, title, status, date).
7. ADR-0001 §14 becomes a pointer to the index.
8. The review report `Eidesoul-Review-Arsitektur-2026-11-01.md` **stays outside the repo** — it is a
   personal work artifact (opinions + scores), not decision notes; the pointer to it points at
   `docs/v0.1.0/`.

### 4.11 What is not an ADR

`LOG.md` chronicles events, `TASK.md` is the work list, `README.md` is the product face, `AGENTS.md`
holds the change rules. An ADR is only a binding and expensive-to-reverse decision.

### 4.12 Register of forbidden terms & synonyms

A term used by more than one ADR is owned by **one** ADR; every other ADR uses the name as it is.
This table is the source, and a canonical term enters it in the same commit in which it is born.

| Canonical term | Short meaning | Owner | Forbidden synonyms |
|---|---|---|---|
| `memory_events` | raw capture journal | 0001 | `event_log`, `raw_events` |
| `memories` | canonical row | 0016 (form), 0004 (state) | `facts`, `memory_rows` |
| `cognition_jobs` | queue of cognitive work, every `kind` | 0003 | `memory_outbox`, `outbox`, `jobs` |
| `state` | row status of `memories` (six values) | 0004 | `status` (for `memories`) |
| `status` | status of non-memory entities (`turns`, `cognition_jobs`, `automation_runs`) | 0013/0003/0014 | `state` (for that entity), `pending` (status turn) |
| `turns` `turn_steps` | the conversation work unit and its steps | 0013 | `turn_jobs`, `sessions`, `actions` |
| `outbound_messages` | send ledger | 0013 | `ledger`, `sent_messages` |
| `scope_key` | ledger key: `turn:<id>` / `automation:<run>` | 0013 | `turn_id` (prosa), wrong ledger PK |
| `automation` `automation_runs` | automation and its run history | 0014 | `rule` (prosa), `job` (prosa), `task` (prosa) |
| `send` | the only send capability: `path?`/`text?` + `chat?` (empty = the active chat); used by turns, recipes, and automation steps | gateway `whatsapp` (`tools/send.py`) + ADR-0014 | official alias: `send_to`, `kirim_file`; forbidden: `forward`, `notify` |
| `gateway` | a body: transport plus its own body actions; installed as a gateway, never as a plugin or a tool | 0027 (contract: engine/0018) | `connector` (prosa), `bridge` (prosa) |
| `tool` | a capability the engine calls; granted only by a role, never by a plugin; a derived procedure is a skill (ADR-0020), not a tool | 0027 | `skill` (prosa), `action` (prosa) |
| `plugin` | an installable capability unit fetched when enabled; not a gateway and not a tool | 0028 | `extension` (prosa), `addon` (prosa) |
| `plugin manifest` | `components/<name>/plugin.yaml`: name, version, engine range, entry, provides, requires, secrets, credits | 0028 | `metadata file` (prosa), `plugin.json` (prosa) |
| `components` | the workspace folder that holds fetched gateway/plugin code and its dependencies | 0028 | `extensions` (prosa), `modules` (prosa) |
| `digest` | the pinned hash an installed component must match before anything is written | 0027 (rule: 0028) | `checksum` (prosa) |
| `panel` | the web operator surface: a separate process at the repo root that reuses the config import path | 0027 | `dashboard` (prosa), `admin ui` (prosa) |
| `clean by default` | gateway, tool, and plugin are each off until enabled; the spine is the only exception | 0027 | `opt-in` (prosa), `default off` (prosa) |
| `operator action` | a human-initiated install/enable/disable/remove, recorded in `config_changes` and the ledger | 0027 | `manual step` (prosa) |
| `rooms_of(person_id)` | the set of rooms in which the asker is a member; the basis of **read** permission (sending has its own class, §4.5) | 0007 | `user_rooms` |
| `cross_send` | cross-room send class (`send.class=cross`); attribution is mandatory; the default in a room is `send.class=room` | 0007 | `blind_send`, `anonymous_send` |
| `cross_read` | **invariant**: there is no cross-room read capability; `rooms_of` is rejected, with no override (enterprise privacy requirement) | 0007 | `allow_cross_read`, `cross_read_enabled` |
| `attribution` | sender identity block + reason on a send; `author` is mandatory (including DM), `reason` is mandatory for `cross_send`, other fields are flexible | 0007 | `embedded`, `signature`, `header` |
| `quoted_refs` | buffer of other rooms' quotes for the verbatim leak guard (not a permission) | 0007 | `cross_reads` (old code name, renamed during implementation) |
| `relay_grant` | the content owner's permission to pass a personal answer on to another party, per task + mode | 0023 (form:0016) | `relay_permission`, `forward_consent` |
| `automation_state_log` | trace of automation state transitions | 0014 (form:0016) | `automation_events` |
| `draft`/`active`/`paused`/`stopped`/`expired` | the five automation states (terminal: `stopped`, `expired`) | 0014 | `disabled` (per automation), `denied` (as state) |
| `occurrence` | one schedule occurrence = one run; `occurrence_key = hash(automation_id, scheduled_at_utc)` | 0014 | `tick`, `slot`, `instance` |
| `participants(chat_id)` | membership fact from the gateway | 0007 (seam) | `members`, `roster` |
| `level` | privacy of memory rows and ceilings (0–4) | 0008/0006 | `privacy` (columns), `sensitivity` (columns) |
| `visibility` | archive level of messages read across rooms | 0007 | `level` (for archive) |
| trust `T0`–`T3` | trust provenance | 0008 | `confidence`, `Tier` (for trust) |
| `Tier` | retention resilience class | 0005 | `retention_tier`, `priority` |
| `Lapis` | prompt payload layer | 0015 | `Tier` (for payload) |
| `stated_by` `about` | provenance attributes; columns `owner_id` / `subject_id` | 0008 | `sender`, `subject` (as a column) |
| `via` | source of a write (enum of seven values) | 0008 | `source` (as new column), `origin` |
| `expires_at` / `max_lifetime_at` / `due_at` | TTL of a row / absolute cap / task deadline | 0005 / 0008 | `ttl_at`, `hard_ttl`, `deadline` |
| `pinned` | boolean pin | 0005 | `pin` (column), `sticky` |
| `quota` `budget` | write volume cap / LLM cost cap | 0008 / 0014 | `limit` (for both) |
| `parameters` | tool argument schema (subset §4.2 ADR-0024), the single source for the manifest and the validator | 0024 | `args_schema`, `input_schema` |
| `additionalProperties` | unknown keys on tool arguments; **absent = `false`** | 0024 | `strict_extra_args`, `allow_unknown_args` |
| `shell.env` | environment whitelist for the `shell` tool; never inherited, never receives `*` | 0024 | `env_inherit`, `pass_env` |
| `prefix stabil` `blok volatil` | two parts of the prompt: `system[0]` byte-identical and cacheable, `system[1]` changes per turn | 0025 | `static_prefix`, `dynamic_prompt` |
| `prompt_prefix_hash` | hash of the stable part + the active tool schema; a cache metric, not a permission | 0025 | `cache_key`, `prefix_id` |
| `max_calls_per_turn` | ceiling on the number of LLM calls in one turn | 0025 | `call_budget`, `turn_calls` |
| `turn_attempts` `worker_attempts` | number of retries per path (turn vs. background worker) | 0017 | `max_retries` (old name), `retries` |
| `retry_after_max_seconds` | cap on waiting for `Retry-After` on the turn path | 0017 | `wait_cap`, `backoff_max` |
| `text_tsv` | derived `tsvector` column for message search | 0026 (form:0016) | `search_vector`, `tsv` |
| `trgm_threshold` | `similarity()` threshold for the trigram net | 0026 | `fuzzy_threshold`, `similarity_min` |
| `search_backend` | the active search backend (`fts+trgm` / `like`), shown in `/status` | 0026 | `search_mode`, `engine_type` |
| `credential_patterns` | secret-read pattern; its class is always `authorization` | 0007 | `secret_patterns`, `sensitive_files` |
| `notice` `apology` | turn status message / missed-task message | 0013 / 0014 | `announcement`, `reminder` |
| `lexicon` | language data surface: per-language words/negations/patterns in `library/lexicon/<lang>.json` | 0000 | `wordlist`, `dictionary` |
| `Percept` | what enters the engine: source/thread/actor/kind/payload/hints | 0000 | `Inbound` (old name), `Event`, `Envelope` |
| `Action` | what leaves the engine: capability/target/payload/approval_class | 0000 | `Outbound`, `Reply`, `Command` (for an action) |
| `body` | a gateway that supplies surface capabilities (chat, robot, harness, editor) | 0000 | `surface`, `embodiment`, `transport` |
| `thread_id` / `threads` / `thread_people` | the neutral replacement for `chat_id`/`chats`/`chat_people` | 0000 (bentuk: 0016) | `chat_id` (prosa), `chat_people` (prosa) |
| `hints` | surface facts declared by the body (`dm`, `mention`, `reply`, `group`, `presence`) | 0000 | `flags`, `is_*` (as a column) |
| `dispute_state` | dispute metadata (not `state`) | 0010 | `disputed` (as state) |
| `superseded` | state of a row that has been replaced | 0008/0016 | `replaced`, `obsolete` |
| `MemoryStore` | single write port | 0001 | `MemoryRepo`, `memory_repository` |
| `GatewayCore` / `Rich` / `Directory` | three gateway protocols: mandatory / optional transport / optional directory | 0018 | `Gateway`, `Adapter` (for contract) |
| `send_sticker` | sends a sticker; **not** a reaction to a message | 0018 | `send_reaction` |
| `mode` | runtime instance state: `primary` / `standby` | 0012 | `state` (for instance), `role` |
| `turn_id` | turn id; the red thread of logs and `llm_calls` | 0013 | `trace_id`, `session_id` |
| `llm_calls` | record per LLM call (tokens, latency) | 0019 (form:0016) | `llm_log`, `usage_rows` |
| `chat_locks` | lock per chat, not per process | 0017 | `turn_lock` (new name), `global_lock` |
| `episode` | a run of turns for one job in one room; the procedure assessment unit | 0020 | `task`, `session`, `thread` |
| `skills` `skill_revisions` | derived procedures and their version history | 0020 (form:0016) | `playbook`, `wiki` (for procedure) |
| `distill` | the `kind` of job that assesses one episode | 0020 | `learn`, `train` |
| `signature` | chain of tools + target type marking two comparable episodes | 0020 | `fingerprint`, `goal_hash` |
| `grep` `fetch` | two token-saving read tools (find file contents / read text URLs) | 0021 | `rg`, `curl`, `wget` |
| `consent` `authorization` | approval class by **target**: the asker's scope (consent) / outside the scope (authorization); sensitivity only decides whether it is needed | 0007 | `self_approve`, `auto_approve` |
| `scope_proof` | deterministic evidence of the action range when approval is requested | 0007 (form:0016) | `scope_hash` (as a column) |
| `administrator` | highest role, protected, free `label` | 0022 | `owner` (name role), `admin`, `root`, `master` |
| `label` | **optional** name for any role holder; used by the prompt to address them | 0022 | `nickname`, `title`, `alias` (for role) |

Enforcement rules:

- `scripts/adr.py --check` gates synonyms on **definition lines** inside a fence (YAML keys,
  SQL columns). The ban's scope is taken from the parentheses in the synonym cell: **no** parentheses =
  it applies to every definition line; `(kolom)`/`(column)` = SQL columns only; `(nama role)`/`(role name)` = only inside
  `roles:` blocks. Parentheses that name another entity (e.g. `(untuk memories)`) mean the rule is
  **prose** — read by a human at review, not gated, and reported as `baris prosa` in the
  check output. A definition line carrying the `kolom lama`/`legacy` marker is excluded (a legacy column
  that is still mapped). Free prose may name old identifiers
  (e.g. "old name: `memory_outbox`") because that is part of the explanation, not a definition; this
  register table itself is excluded.
- A new canonical term is born only by adding a row to this table, in the same commit.
- Human prose is free ("send", "room", "memory"); what is locked is the **identifier**.
- The "owner" column decides who may change the meaning; changing the meaning without the owner's amendment = review failure.
- **Migrating the name `send_to`**: today's code has `send` (files, active chats) and `send_to` (text/files,
  other chats). Merging the two into `send(chat=...)` is implementation work; until it is done,
  `send_to` remains a legal name that is not counted as a synonym.
- The register holds **two classes of owner**: the ADR (the decision) and the gateway/code (a name already live in the repo).
  A code-owned name must not be changed by an ADR without a code change; conversely, an ADR step that collides
  with a tool name must be distinguished (see the `send` row above).

## 5. Configuration

No new engine config keys: documentation is not a runtime part. The only configuration
is the script argument.

```
python scripts/adr.py --check # gerbang bentuk (dipakai pytest)
python scripts/adr.py --new "Judul ADR" --dir engine # kerangka di folder engine/
python scripts/adr.py --translate 9 # turunan English (operator)
```

The existing document context is unchanged: `docs/v0.1.0/README.md` may join the path-based allowlist
`engine.self_knowledge.docs` (ADR-0015 §4.9) if the operator wants the soul to explain its own architecture.

## 6. Metrics and gates

| Metric | Initial target |
|---|---|
| ADRs passing `scripts/adr.py --check` | 100% |
| Duplicate / missing / reused numbers | 0 |
| Non-monotonic / duplicate section number | 0 |
| ADRs not matching the §4.4 skeleton contract | 0 — **16/16 conform as of 2026-11-03** |
| Sections `## ... Amendments` (the history must be a header line) | 0 |
| `§...` references left dangling after renumbering | 0 (tested) |
| Unresolved `ADR-XXXX` references | 0 |
| Forbidden synonyms (§4.12) in YAML/SQL fences | 0 |
| More than one `docs/*/adr` directory | 0 |
| Stale English derivative (hash mismatch) | 0 |
| The "bare fence in a table cell" defect class | 0 (tested with a fixture) |

## 7. Acceptance criteria

- [ ] `docs/v0.1.0/` contains `README.md` and `adr/`; the 16 migrated ADRs are present with the same number and slug.
- [ ] `docs/v0.1.0/README.md` is the only ADR list; ADR-0001 §14 points to it.
- [ ] `AGENTS.md` lists `docs/v*/**` as a tracked document and a valid non-English surface, and mentions `scripts/adr.py --check` in the Verify section.
- [ ] All 16 ADRs match the §4.4 skeleton contract — **applied 2026-11-02/03** (eight files completed without deleting content; ADR-0013 gained a `Decision` summary; `0015` was numbered).
- [ ] `Amendments` lives only as a header line; there is no `## Amendments` section (0002/0004 have been moved).
- [ ] Section numbers rise monotonically without duplicates; additional sections sit only in the allowed regions; the last numbered section is always `Code references`.
- [ ] Every `§...` reference (internal or `ADR-XXXX §...`) points at a title that exists; the renumbering has updated the shifted references.
- [ ] `scripts/adr.py --check` fails on: forbidden synonyms §4.12 in YAML/SQL fences, a wrong file name, duplicate/missing numbers, out-of-order section titles, an invalid `Status`, a non-ISO date, a non-reciprocal `Supersedes`, a dangling `ADR-XXXX` reference, an odd number of code fences, a bare fence in a table cell, two `adr/` directories, a stale translation hash.
- [ ] `scripts/adr.py --new` produces a skeleton with the ten mandatory sections, and the numbering is correct without guessing content.
- [ ] The English derivative is clearly marked as a derivative together with the source hash; a file without one is not counted as a failure.
- [ ] `pyproject.toml` keeps `include = ["eidesoul*"]` so that `docs/` does not enter the wheel; `uv sync --locked` and `pytest -q` stay green.
- [ ] No new `.md` outside `docs/v*/`; `git ls-files '*.md'` stays free of wild documents.

## 8. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Two ADR sets live after a new release cycle | The check rejects more than one `docs/*/adr`; the move must use `git mv` to preserve history |
| A stale ADR left `accepted` while the code changed | The dated amendment is mandatory; a change of decision must be a new ADR with a reciprocal `Supersedes` |
| Renumbering breaks `§...` references | The check verifies that every `§...` reference resolves; migration step §4.10.5 updates shifted references before commit |
| Normalization touches an already `accepted` ADR | Only additions (an honest empty declaration) and one `Decision` summary for ADR-0013; no decision is changed or removed |
| Wild documents grow back | The `AGENTS.md` ban stays; only `docs/v*/**` is exempted |
| The English derivative drifts from the source | The source hash on the opening line + the check; the derivative is never the source |
| Overhead: writing an ADR for trivia | §4.6 sets the boundary; `--new` only builds the skeleton |
| The guard script itself is seen as "heavy" | Zero new dependencies (regex + `pathlib` + `hashlib`); it follows the `patterns.py`/`fakta.py` pattern already in use |
| A version folder mistaken for a release claim | The folder name = the target cycle; `pyproject.toml` keeps the dev version until a real release; recorded in §4.1 |

## 9. Interaction with other ADRs

- **ADR-0001 §14**: the queue stops being a list and becomes a pointer to `docs/v0.1.0/README.md`.
  Applies to all ADRs that are already `accepted`.
- **ADR-0015 §4.9**: `docs/v0.1.0/README.md` may join the `docs` allowlist (path-based), so that the soul can read the
  architecture index itself; ADRs (`adr/**.md`) are not in the default allowlist — decisions carry notes
  that are internal (host paths, thresholds) and need not be loaded in.
- **ADR-0005/0008** (concept): unaffected; only the shared home of documents changes.

## 10. Code references

- `AGENTS.md:7-12` — the language rules (permitted non-English surfaces; the place for the §4.9 amendment).
- `AGENTS.md:17-18` — the tracked document list (the place for the §4.9 amendment).
- `pyproject.toml:42-51` — setuptools build backend, `include = ["eidesoul*"]`, package-data
  (proof that `docs/` is not part of the distribution).
- `pyproject.toml` `version = 0.0.0.dev0` — the pre-release version per `AGENTS.md` §Shape.
- `sonar-project.properties:5` — `sonar.sources=eidesoul,scripts` (proof that no new exclusion is needed).
- `README.md:254-258` — documents `scripts/patterns.py`/`fakta.py --check` (the guard precedent).
- `scripts/affect.py`, `scripts/patterns.py`, `scripts/fakta.py` — the "data + guard script" pattern.
- `tests/` — the test home that calls `scripts/adr.py --check`.
- `LOG.md`, `TASK.md` — surfaces that remain non-ADR (§4.11).

---

*This ADR lives at `docs/v0.1.0/adr/0009-adr-home.md`.*
