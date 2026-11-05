# ADR-0055: Repository Slimming — Governance Files, Scripts, and Module Consolidation

- **Status:** accepted
- **Date:** 2026-11-05
- **Deciders:** Muqsith (owner)
- **Context:** repository review measured 413 files and about 101,000 lines, with governance scripts that the code evades. Several proposals touch current hard rules in `AGENTS.md`, so the owner decided each one (§4.2).
- **Supersedes:** —
- **Related:** ADR-0009 (ADR home), ADR-0031 (engine structure), ADR-0037 (module budgets), ADR-0049 (audit remediation), ADR-0051 (native memory retrieval), ADR-0054 (extension DX)
- **Implementation:** draft — the owner decided §4.2 on 2026-11-05; no file has been deleted or rewritten yet.
- **Location note:** the ADR binds repository layout across the engine and the docs tree, so it sits at the ADR home root.

---

## 1. Context

| Area | Files | Lines | Finding |
| --- | ---: | ---: | --- |
| `monoagens/` | 194 | 50,345 | many 6–50 line modules; `memory/` 23 files, `turn/` 16, `commands/` 21 |
| `monoagens/vendor/emotion_engine.py` | 1 | 4,855 | only decay, appraisal, and relation updates are used |
| `docs/v0.1.0/` | 99 | 25,973 | English ADRs plus hash-stamped Indonesian copies (the copies are already deleted in the working tree) |
| `tests/` | 88 | 20,704 | many meta-tests on constants, file counts, and banned words |
| `scripts/` | 11 | 1,954 | `adr.py` 668, `neutral.py` 168, `selfcheck.py` 135 |
| governance docs | 4 | 2,393 | `AGENTS.md` 259, `TASK.md` 166, `LOG.md` 961, `README.id.md` |
| `install.sh` | 1 | 1,753 | duplicates `monoagens init` |

Evidence that the guards are gamed rather than obeyed: the AST test passes while sync DB calls block the
loop; the 800-line cap passes through `__getattr__` helpers and an `OVER_CAP_ALLOWED` list that holds
`config.py` (1,724), `cognition.py` (1,135), `affect.py` (1,160), and `__main__.py` (1,084);
`neutral.py` banned Indonesian stopwords, so the forget intent kept words like "yang" as match tokens;
`AGENTS.md` lists file names that rot (`access/target.py` does not exist) and symbols the tests forbid
(`Inbound`, `ChatMessage`).

## 2. Decision drivers

1. A rule that the code can satisfy without improving anything is removed or made behavioural.
2. Documentation records decisions that last, not file lists or session diaries.
3. Cohesion beats file count, but a split is judged by one reason to change (ADR-0031), not by a
   number.
4. Nothing here changes behaviour; each step keeps the suite green.
5. The owner keeps the final word on rules written in `AGENTS.md`.

## 3. Options considered

**A. Radical consolidation to about 32 Python files in one pass.** Rejected as a single step: a rewrite
of that size hides regressions and overrides ADR-0031 and ADR-0037 without a vote.

**B. Do nothing.** Rejected: the cap script already fails on a clean checkout (ADR-0049).

**C. Staged slimming, safe items first, structural merges one area at a time.** Accepted.

## 4. Decision

### 4.1 Safe now (no behaviour, no rule change)

- Delete `scripts/xlsx.py` (unused), fix or delete `scripts/ablate.py`, and keep one of
  `.sonarcloud.properties` and `sonar-project.properties`.
- Make `scripts/selfcheck.py` honest: remove `OVER_CAP_ALLOWED` by splitting `config.py`, `cognition.py`,
  `affect.py`, and `__main__.py` along their reasons to change, or record each overage in the ADR that
  introduces it (ADR-0031 §4.1).
- Replace the AST-only blocking test with a behavioural one: a heartbeat task must keep ticking while a
  turn runs (ADR-0049 §4.2).
- Correct `AGENTS.md` and `TASK.md` facts that are wrong today: the `Inbound`/`ChatMessage` names, the
  missing `access/target.py`, the source-of-truth line, and the `panel/` promise.

### 4.2 Owner decisions (2026-11-05)

1. **Indonesian ADR copies and the translation gate: removed.** ADRs are English only. The `id/` copies
   stay deleted; `--translate-check`, `--stamp`, and the hash header are removed from `scripts/adr.py`,
   `tests/test_adr.py`, ADR-0009 §4.4, the `docs/v0.1.0/README.md` text, and the `AGENTS.md` language
   section. Indonesian remains only in the data surfaces that `AGENTS.md` already lists (`lang/id.yaml`,
   `soul.yaml`, `library/**`).
2. **`README.id.md`: removed** until a stable release. The README credits rule drops its mirror clause.
3. **`LOG.md`: kept but simplified and stable.** It is rewritten to short entries: newest first, one to
   four lines each, behaviour and decisions only, no file lists, no line numbers, no test names. Entries
   older than the current ADR set are folded into one "before 0.1.0" paragraph. New entries follow the
   same shape; a change that already has an ADR links the ADR number and nothing more.
4. **`TASK.md`: kept as the active checklist only.** Finished sections are pruned; open items point at
   their ADR.
5. **`AGENTS.md`: cut to timeless invariants.** Language, boundaries (engine never imports gateways or
   vendor SDKs; async paths never block), security and privacy (one outbound door, privacy before the
   model), configuration (files are authoritative), and verification. No file lists, no ADR numbers, no
   line caps by name. The facts listed in §4.1 are corrected as part of the cut.
6. **`scripts/` guards:** the checks that matter (no gateway or vendor import in `engine/`, no culture
   literal, the module cap) move to `tests/test_invariants.py`; `scripts/neutral.py` and the other policing
   scripts are deleted. `scripts/adr.py --check` stays as the ADR structure gate.
7. **`install.sh`: a thin bootstrap** that installs the runtime and calls `monoagens init`, which owns the
   logic.
8. **Module merges: staged**, one area per commit, starting with `vendor/emotion_engine.py` and then
   `memory/` after Mem0 is gone (ADR-0051).
9. **ADR set: fewer files, no redundancy** (§4.4). The durable-task-runner draft is folded into ADR-0054 and its file deleted.

### 4.3 Structural merges, one area at a time

Each merge is its own commit, keeps public imports stable until the last step, and needs the owner's
acceptance before it starts.

1. `vendor/emotion_engine.py` becomes a PAD module of about 200 lines (ADR-0049 §4.3.2).
2. `engine/memory/` (23 files) drops to the files a single table needs once Mem0 is gone (ADR-0051):
   queries, writes, and reads merge into a store module; evidence and privacy stay together.
3. `engine/commands/` merges its one-line wrappers into one declarative module.
4. `admin/` keeps the CLI and the templates; logo art and brand helpers fold into one module.
5. The `turn/` helpers become explicit stages (ADR-0049 §4.3.1).
6. The proposed target tree is a direction, not a contract: ADR-0031 §4.3 and
   ADR-0037 are rewritten only after merges 1–5 prove the shape.

### 4.4 Fewer ADRs

Rules: an ADR that was never built and is superseded by a live one is merged and deleted; an
implemented ADR whose content is now one section of a broader decision is merged into the survivor;
an ADR that is still the only record of a live contract stays. Merging keeps the `Decision` text that
code still depends on and drops history and rejected-option prose that no longer informs a change.

| Step | Action | Result |
| --- | --- | --- |
| 1 | Fold the durable-task-runner draft (428 lines, no code) into ADR-0054 §4.4 | done: that draft deleted |
| 2 | Rewrite ADR-0001 around the single store without Mem0 (ADR-0051) and absorb the Mem0-specific text of ADR-0002/0003 | one memory-pipeline ADR |
| 3 | Merge the memory-state ADRs (0004, 0005, 0006, 0008, 0010) into one "memory lifecycle and privacy" ADR beside ADR-0038 | two files instead of six |
| 4 | Merge turn-safety ADRs (0011, 0012, 0013, 0017) into one "turn lifecycle and lease" ADR | one file instead of four |
| 5 | Merge tool-contract ADRs (0015, 0021, 0024, 0025) into one "tool contract" ADR | one file instead of four |
| 6 | Merge install/distribution ADRs (0027, 0030, 0039) into one, with the `panel/` promise removed | one file instead of three |
| 7 | Mark ADR-0009's translation text as removed; fold ADR-0031 and ADR-0037 into one structure ADR after the module merges | one file instead of two |

Each merge is its own commit, runs `scripts/adr.py --check`, and renumbers nothing: the survivor keeps
its number and lists the merged numbers in `Supersedes`. Steps 2–7 need the owner to read the merged
draft before the old files are removed.

## 5. Configuration

None.

## 6. Metrics and gates

- `ruff`, `pyright` ratchet, and `pytest -q` stay green after each commit.
- File count and line count of `monoagens/` are recorded before and after each merge in the commit
  message.
- No new `OVER_CAP_ALLOWED` entry may be added.

## 7. Acceptance criteria

- [ ] `selfcheck --check` passes without an allowlist.
- [ ] `AGENTS.md` and `TASK.md` contain no stale file or symbol name.
- [x] The owner has answered each decision in §4.2.
- [ ] `vendor/emotion_engine.py` is gone with identical PAD outputs on the fixture set.

## 8. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| A merge breaks imports | Re-export from the old path until the last commit of that merge |
| Loss of ADR translations | Indonesian text stays only in data surfaces (AGENTS.md language rule) |
| Dropping guards weakens boundaries | The boundary tests move, they are not deleted |

## 9. Interaction with other ADRs

This ADR would amend ADR-0009 (translation gate), ADR-0031 and ADR-0037 (tree and caps) if the owner
accepts §4.2 and §4.3. Until then those ADRs stay in force and `selfcheck` must pass under them.

## 10. Code references

- `scripts/selfcheck.py`, `scripts/adr.py`, `scripts/neutral.py`, `scripts/xlsx.py`, `scripts/ablate.py`
- `AGENTS.md`, `TASK.md`, `LOG.md`, `README.id.md`, `install.sh`
- `monoagens/vendor/emotion_engine.py`, `monoagens/engine/memory/`, `monoagens/engine/commands/`

---

*This ADR lives at `docs/v0.1.0/adr/0055-repository-slimming.md`.*
