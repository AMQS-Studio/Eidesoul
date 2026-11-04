# ADR-0039: Init as a First-Class Terminal Interface, and the Eidos Mark

- **Status:** accepted
- **Date:** 2026-11-04
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Context:** fills the logo slot and the interaction debt left by ADR-0030; follows ADR-0009 (ADR home), ADR-0019 (observability), ADR-0022 (protected administrator), ADR-0027 (distribution), ADR-0033 (neutral core)
- **Supersedes:** —
- **Related:** ADR-0030 (install and init), ADR-0031 (module structure)
- **Implementation:** implemented — `admin/terminal.py` (curses plus numbered fallback), `admin/wizard.py` (step machine, location, credential, provider/model pickers, review), `admin/brand.py` (Eidos lockup), `admin/provider_catalog.py` (operator labels), the `--api-key-file` flag, `tests/test_init.py`, and the SVG mark under `docs/brand/`.
- **Location note:** setup is a host-wide surface, so this ADR sits at the root of the ADR home. The Indonesian copy is `docs/v0.1.0/adr/id/0039-setup-terminal-and-mark.md`.

---

## 1. Context

ADR-0030 made `eidesoul init` interactive and left a logo slot (`[logo]`) for a mark that did not
exist. The wizard it shipped is a linear sequence of line prompts: no arrow keys, no search, no
back navigation, and no visible location step. `eidesoul init` from an arbitrary working directory
therefore asks for provider and model before the operator has confirmed where the files will land.

The operator direction was explicit: study the Hermes setup, then build our own interaction and
identity rather than copy its frame or its mark. The defects are concrete:

| Fact | Evidence |
| --- | --- |
| The terminal is line-only | `admin/init.py` used `_ask()` and numbered choices for every step |
| Location is implicit | `--dir` defaulted to `.` and no step showed the resolved target |
| The logo is a placeholder | `LOGO_SLOT = "[logo]"` in `admin/init.py` |
| Provider IDs have no operator labels | the picker printed raw IDs such as `opencode` |
| Secrets cannot be entered by the wizard | `.env` was written empty for the operator to fill later |
| The reference has a richer loop | Hermes `hermes_cli/setup.py` and `curses_ui.py` use radiolists, checklists, fuzzy `/` search, Escape/Left navigation, and a step runner |

Hermes is MIT and remains a reference for interaction discipline only. No Hermes code, glyph,
wordmark, or asset is copied; the Eidos mark is original.

## 2. Decision drivers

| Driver | Consequence |
| --- | --- |
| Location before credentials | The target directory is the first decision and is shown in Review and Receipt |
| Keyboard first, pipes safe | A POSIX TTY gets curses; a pipe, `TERM=dumb`, or a test gets numbered lines |
| Reversible decisions | Left returns to the previous step with earlier answers preserved; Escape cancels without writing |
| Secrets stay secret | A key is masked, written only to `.env` at `0600`, and never printed in Review or the log |
| One data source | `admin/templates.py` stays the only default source; interaction never invents a value |
| Own identity | The Eidos mark is code and SVG, not a borrowed logo |
| Testable UI | Frame/key logic is separable from the file writer; tests never need a real curses terminal |

## 3. Options considered

| Option | Summary | Value |
| --- | --- | --- |
| **A. Keep line prompts and add color** | wrap ADR-0030 output in ANSI | Rejected: does not add navigation, search, or a real location decision |
| **B. Copy the Hermes curses UI** | transplant its menus and steps | Rejected: another product's vocabulary and layout; larger dependency surface than needed |
| **C. Curses on a TTY, numbered fallback elsewhere** | stdlib-only menus; the existing line path stays testable | **Chosen** |
| **D. Add a full TUI framework** | Textual or prompt_toolkit | Rejected: a setup-only surface does not justify a new runtime dependency |
| **E. Make the location a hidden default** | keep `--dir` only | Rejected: the operator cannot audit where secrets and config land |

## 4. Decision

### 4.1 Location is the first step

The rail is `location · provider · credential · model · database · identity · voice layer`, followed
by Review. The location step shows the resolved directory and the status of `config.yaml`,
`roles.yaml`, `soul.yaml`, `.env`, and `.eidesoul/`. It offers `new`, `existing`, or `keep` /
`change` / `replace` through the existing decision screen. A path that is a file, or whose nearest
existing ancestor is not writable, is rejected at the step. No directory is created before Apply.

`--dir PATH` seeds the answer; `--non-interactive` uses it without prompting. The resolved path is
always shown in Review and Receipt.

### 4.2 Terminal interaction

| Key | Action |
| --- | --- |
| `↑` / `↓`, `j` / `k` | move the cursor |
| `Enter` | select |
| `/` | start a case-insensitive filter over label, detail, and value |
| `Backspace` | edit the filter |
| `←` | return to the previous step; answers are preserved |
| `Esc` | clear an active filter, otherwise cancel the wizard |
| `q` / `Ctrl-C` | cancel without writing |

The curses layer uses the standard library only and runs only when stdin and stdout are both TTYs,
`TERM` is usable, and the platform is POSIX. Everywhere else the numbered line fallback accepts a
number, an exact label, or an exact value, and a blank answer keeps the default.

### 4.3 Steps and operators

1. **Provider** — operator label from `admin/provider_catalog.py`; the ID and key variable stay in
   `eidesoul/providers`.
2. **Credential** — masked input for the provider's `api_key_env`; blank leaves `.env` for later.
   `--api-key-file PATH` is the headless equivalent, so a key never has to appear in shell history.
3. **Model** — the provider's served ids when reachable, searchable; manual entry when the list is
   empty or unavailable.
4. **Database** — the existing Postgres URL validation; the password is not rendered in Review.
5. **Identity** — administrator label plus language, locale, and timezone from the existing defaults.
6. **Voice layer** — keep emotion, humanize, and character on, or name the parts to turn off.

### 4.4 Review, secrets, and writes

Review shows the location, every file and mode, provider, base URL, key variable, model, database
env name, administrator label, and voice status. It never shows the key value or the database
password. Apply is still the only path to `write_files()`; Cancel, Escape, Ctrl-C, and an interrupted
write leave no new file. Existing files keep the `keep` / `change` / `replace` decisions from
ADR-0030. `.env` remains `0600`.

### 4.5 The Eidos mark

The mark is an open ring with a centered dot: form around a soul, with the opening as agency. The
wordmark is `EIDESOUL`; the tagline is `a soul engine you run on your own machine`. The terminal
prints `◈` plus the lockup; `--no-color`, `TERM=dumb`, and non-TTY output use the plain form. The SVG
assets are `docs/brand/eidesoul-mark.svg` and `docs/brand/eidesoul-lockup.svg`; the mark is never a
config key and never changes layout width.

### 4.6 Module boundaries

`admin/init.py` keeps defaults, validation, `build()`, and the atomic writer. `admin/wizard.py`
keeps the step machine, prompts, review, and navigation. `admin/terminal.py` owns curses and the
line fallback. `admin/brand.py` owns the lockup. `admin/provider_catalog.py` owns operator labels.
No engine module imports a terminal library.

## 5. Configuration

| Flag | Meaning |
| --- | --- |
| `--dir PATH` | seed the Location step; default `.` |
| `--provider NAME` | pre-answer Provider |
| `--api-key-file PATH` | read the key from a file; never a command-line value |
| `--model ID` | pre-answer Model |
| `--database-url URL` | pre-answer Database and write it to `.env` |
| `--label TEXT` | pre-answer Identity |
| `--voice-layer on\|off` | pre-answer Voice |
| `--language`, `--locale`, `--timezone` | pre-answer the corresponding defaults |
| `--no-color` | plain output; also honoured through `NO_COLOR` |
| `--non-interactive` | never prompt; fail closed when a required flag is missing |

No key is added to `config.yaml`. The API key value lives only in `.env`.

## 6. Metrics and gates

| Metric | Target |
| --- | ---: |
| Prompts when stdin is not a terminal | 0 (fail with the flag list) |
| Secrets printed in frames, Review, Receipt, or logs | 0 |
| Files left by Escape/Ctrl-C before Apply | 0 |
| Steps without a headless equivalent | 0 |
| Provider IDs rendered without an operator label in the TTY picker | 0 |
| Logo placeholders in the shipped wizard | 0 |
| New runtime dependencies for the terminal UI | 0 |

## 7. Acceptance criteria

- [ ] `eidesoul init` on a POSIX TTY shows the Eidos lockup and the location-first rail.
- [ ] Provider and model pickers move with arrows, filter with `/`, select with Enter, and return with Left.
- [ ] Escape and Ctrl-C leave no new file and no partial file.
- [ ] A custom location writes all four host files and `.eidesoul/workspace` there.
- [ ] A path that is a file, or whose nearest existing ancestor is unwritable, fails at Location.
- [ ] Review and Receipt show the location; Review never shows the API key or database password.
- [ ] A masked credential lands only in `.env` at `0600`; `--api-key-file` does the same headlessly.
- [ ] Non-TTY, `TERM=dumb`, and `--no-color` produce a readable numbered fallback.
- [ ] Existing files still offer `keep` / `change` / `replace`.
- [ ] `pytest -q`, `ruff`, `pyright`, and the ADR gates pass.

## 8. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Curses breaks a narrow or dumb terminal | TTY/TERM guard and numbered fallback; no dependency on width for the line path |
| A hidden location writes into the wrong directory | Location is step one, shows the resolved path, and is repeated in Review/Receipt |
| A secret leaks through a frame or log | Masked input, no value in Review, `.env` `0600`; tests assert a marker is absent from stdout |
| The split duplicates validation | `admin/init.py` remains the only validator and writer; `wizard.py` calls it |
| The mark becomes a configuration surface | It is code and SVG only; no key and no layout dependence |
| A future Web panel needs the same flow | `--non-interactive` and the module boundary let a panel drive `init` without the curses layer |

## 9. Interaction with other ADRs

- **ADR-0030**: this ADR fills its logo slot and replaces the linear interaction; the writer,
  never-overwrite rule, `.env` modes, and headless rule stay.
- **ADR-0022**: Identity still asks only for the administrator label; protected-role rules do not move.
- **ADR-0019**: the terminal layer logs no secret and no frame content.
- **ADR-0031**: the split keeps both files under the 800-line cap (`init.py` 442, `wizard.py` 475).
- **ADR-0033**: provider labels live in `admin/`, not in the neutral engine; the engine still holds no
  transport or vendor vocabulary.

## 10. Code references

- `eidesoul/admin/init.py` — `Answers`, validation, `build()`, atomic `write_files()`, `banner()`.
- `eidesoul/admin/wizard.py` — location, provider, credential, model, database, identity, voice,
  review, navigation, and the numbered fallback.
- `eidesoul/admin/terminal.py` — curses radiolist, `/` filter, Back/Cancel, masked secret.
- `eidesoul/admin/brand.py` — the Eidos lockup and ASCII fallback.
- `eidesoul/admin/provider_catalog.py` — operator labels and key-variable detail.
- `eidesoul/__main__.py` — `--api-key-file` and the `init` dispatch.
- `docs/brand/eidesoul-mark.svg`, `docs/brand/eidesoul-lockup.svg` — the mark and lockup.
- `tests/test_init.py` — location, credential, lockup, filter, review, cancel, and fallback coverage.

---

*This ADR lives at `docs/v0.1.0/adr/0039-setup-terminal-and-mark.md`.*
