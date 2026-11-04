# ADR-0030: On-Machine Install and Interactive Init — Our Own Wizard, One Installer, Two Runtimes

- **Status:** accepted
- **Date:** 2026-11-03
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Context:** follows ADR-0027 (distribution and web-first configuration), ADR-0007 (approval classes), ADR-0016 (migration), ADR-0022 (the protected `administrator` role), ADR-0029 (the test store)
- **Supersedes:** —
- **Related:** ADR-0009 (the document home), ADR-0015 (capability manifest), ADR-0019 (observability), ADR-0028 (plugins)
- **Implementation:** partial — present: `install.sh` (opt-in runtime install, pinned uv digest, `--dry-run`, log, signals, both runtimes), curses menus shared with `monoagens init`, `--label`/`--stack`/`--engine`/`--database`/`--voice-layer`, generated database credentials, automatic `cli local` administrator, `tests/test_init.py`, `tests/test_install.py`; remaining: the panel branch of the finish screen waits for `panel/` (ADR-0027)
- **Location note:** this ADR binds every surface (it decides how a host is born), so it sits at the root of the ADR home. The Indonesian copy is `docs/v0.1.0/adr/id/0030-install-and-init.md`.
- **Amendments:** 2026-11-05 — the installer uses the same curses layer as `init`, asks native/compose, container/external, and Podman/Docker with live detection, asks for sudo through the terminal, prepares the database before `init`, and registers the local administrator. 2026-11-05 — the installer asks native versus compose, both stacks use a Postgres container by default, and installing a missing runtime is opt-in with `sudo`. 2026-11-03 — first version. 2026-11-03 — the wizard asks the voice layer once (keep-on is the default answer) and carries `--voice-layer on|off` for headless parity (ADR-0027 §4.3). 2026-11-03 — `--label` (administrator address) and `--stack native|compose` join the flag table: §6's "steps without a flag equivalent = 0" requires them.

---

## 1. Context

| Fact | Evidence |
|---|---|
| There is no on-machine install path | `README.md` quickstart starts with `podman compose up -d postgres`; the only non-container path is "clone and `uv sync`" in prose |
| `init` is non-interactive and demands flags | `monoagens init --provider <name>` is required (`monoagens/__main__.py:709-714`), written from code templates (`monoagens/admin/init.py:26-46`) |
| The defaults already live in one place | `monoagens/admin/templates.py:1` — "one source of truth, no separate example files" (ADR-0027 §4.6) |
| The panel does not exist yet | no `panel/` tree; ADR-0027 §4.4 plans it on by default once finished |
| A reference, not a template | the Hermes bootstrap (`hermes-agent.nousresearch.com/install.sh`, 1302 lines) was studied: stage protocol, pinned `uv` with per-arch SHA-256, quiet output discipline, install log, signal handling, idempotent repair |
| The user's direction | the interactive setup must not be a copy: our own style, more capable, with a logo slot that is filled later |

## 2. Decision drivers

| Driver | Consequence |
|---|---|
| Own identity | The wizard is designed for this product: a step rail, a review step, and a receipt, not another project's frame |
| Operator-first | One command installs, one command initialises, and the wizard says exactly what it wrote |
| Defaults stay in code | The installer and the wizard orchestrate `templates.py`; neither carries a second default |
| Two stacks, one installer | Native (this machine) and compose (server) share the same script and the same `init` |
| Never surprise the host | No silent `sudo`, no package installs behind the operator's back, no PATH edits without a guard |
| Headless parity | Every interactive question has a flag; piped input never hangs and never guesses |
| Honest staging | The panel is not built yet, so the script ends with the CLI path and says so |
| Reversible | Running it again repairs; it never overwrites a file that exists without being asked |

## 3. Options considered

| Option | Summary | Value |
|---|---|---|
| **A. Imitate the reference installer** | copy the banner, the stage protocol, and the wording | Rejected: it is another product's identity, and the stage protocol exists for their GUI driver, which we do not have |
| **B. Our own installer and wizard** | `install.sh` at the repo root, an interactive `init` with our own visual language, one logo slot | **Chosen** |
| **C. Prompts in bash only** | the installer asks, Python never does | Rejected: headless parity and testability suffer, and the defaults would drift back into shell |
| **D. Distro packages (RPM/COPR)** | `dnf install monoagens` | Deferred: needs releases that do not exist yet (§Versioning) and a maintainer story |

## 4. Decision

### 4.1 `install.sh` at the repository root

One Bash script, invoked either from a clone (`bash install.sh`) or from the network
(`bash <(curl -fsSL https://raw.githubusercontent.com/<owner>/<repo>/main/install.sh)`). It is the whole
install path for both runtimes. A plain `curl ... | bash` leaves no TTY for the wizard, so the network
form downloads the script first or uses process substitution; `curl | bash` stays fail-closed.

| Aspect | Rule |
|---|---|
| Runtime choice | `--stack native` runs the engine on the host with Postgres in a container; `--stack compose` runs both in containers. The installer asks when `--stack` is omitted. |
| Container choice | `--engine podman\|docker` selects the Postgres runtime; the menu shows **installed and running**, **installed**, or **not installed** for both, and offers the missing one for install. |
| Database choice | `--database container\|external` (native only). An external URL is asked at the step and forwarded to `init`; compose keeps its own Postgres. |
| License to touch the host | **never** runs `sudo` without explicit consent. A missing runtime is offered for install; `sudo -v` asks for the password in the terminal and does not store it. `--install-runtime` is the headless equivalent. No other system package is installed. |
| `uv` | if absent, download the pinned release for the running architecture and verify its SHA-256 before use; the pin lives in one commented block with a "how to bump" line |
| Python | requires 3.14+ and reports the interpreter it found; the container image keeps the same 3.14 floor — the difference is stated, not hidden |
| Dependencies | `uv sync --locked` inside the install directory (`--dir`, default `~/.monoagens/app`); no dependency is installed outside it |
| Setup | uses the same curses menus as `init` for runtime, location, database, and engine, then hands over to `monoagens init` with the location locked (§4.3): interactive on a terminal, flag-driven otherwise |
| Database | both stacks use a Podman/Docker Postgres by default. The installer generates `POSTGRES_PASSWORD` and `MONOAGENS_DATABASE_URL` **before** `init` so the review shows them; an explicit external `--database-url` skips the container in native mode |
| Continue | start Postgres (or the full compose stack), `monoagens db upgrade`, register `cli local` as administrator, then `monoagens selftest` |
| Finish | offers a choice (§4.5): open the panel, or stay on the command line |
| Observation | one status line while a child runs; non-interactive child output is appended to `~/.monoagens/logs/install.log`, while the interactive wizard stays on the terminal (no secret in the log); a failure prints the last lines plus the log path |
| Signals | `INT`/`TERM`/`HUP` mark the run `interrupted` and are re-raised, so the exit status stays the signal's |
| Re-run | a repair: an existing checkout is refreshed from `main` while `config.yaml`, `roles.yaml`, `soul.yaml`, `.env`, `.monoagens`, `.venv`, `plugins`, and `panel` stay; a git checkout with local changes is kept; a partial checkout with no `pyproject.toml` is offered for removal before the retry |
| Platform | Linux x86_64 and aarch64 in this version; anything else is refused with one line |
| Preview | `--dry-run` prints every action and writes nothing |

### 4.2 The wizard's own visual language

The interactive setup is designed here, not borrowed. What it borrows from the reference is only the
*discipline* (quiet output, a log, honest failures).

| Element | Our design |
|---|---|
| Header | the generated logo and wordmark shared with `init`, a step counter, and a one-line role statement; a narrow terminal or `NO_COLOR` falls back to plain lines |
| Steps | a rail of named steps with a counter (`1/5 provider`, `2/5 model`, …) where the current step is marked, finished steps stay marked, and a step can be retried without restarting the run |
| Prompting | numbered choices by default so a numpad works; typing a value is always allowed where a value is expected; `Ctrl-C` cancels the whole run and leaves nothing behind |
| Review | before writing, one screen shows every file that will be created and every value it will hold (secrets shown as their **env names only**) with `apply`/`change`/`cancel` |
| Receipt | after writing: the files with their modes, the two next commands, and where the log lives — the operator never has to guess what happened |
| Colour | terminal-only, `NO_COLOR` respected, no animation, and no box drawing that breaks on a 60-column terminal |
| Voice | English operator text (internal diagnostics, per the repo rule); the soul's language governs chat, not the installer |

### 4.3 `monoagens init` becomes interactive on a terminal

- On a TTY, `init` asks the same questions the flags carry: location (unless the installer already
  asked and passes `--no-location`), provider, model (optional), database URL, the first
  administrator's label, and the **voice layer** — off by default (ADR-0041), with `on` available.
  Existing files are offered as `keep` / `change` / `replace`; replacing `.env` still keeps generated
  database credentials.
- Off a terminal (piped, CI, `--non-interactive`), it **fails closed**: it names the flags it needs
  instead of waiting for input that cannot arrive.
- Secrets never reach YAML: the wizard writes env **names** and puts values in `.env` (`0600`), exactly
  as the non-interactive path already does (`monoagens/admin/init.py:14-20`).
- Every value it writes comes from `monoagens/admin/templates.py` plus the operator's answers — the
  wizard owns no default of its own.
- Validation happens before writing: an unknown provider name, a model that is not in the provider's
  list when that list is reachable, or an unusable database URL is reported at the step that asked for
  it, not after the files are on disk.
- Files are written only after the whole wizard succeeds (write-then-rename), so an interrupted run
  cannot leave a half-configured host.

### 4.4 What the wizard writes, and what it never touches

| Writes | Never |
|---|---|
`config.yaml`, `roles.yaml`, `soul.yaml`, `.env` in the chosen directory, from the templates | a second config file, an `.example` copy, or a default that is not in `templates.py` |
`.env` with names and empty values, mode `0600` | a secret value in YAML, in the log, or in the shell history |
a `.monoagens/workspace` directory | anything outside `--dir`; no `sudo` without consent, no system service, no PATH line without a duplicate guard |

### 4.5 The end of an install is a choice, not a surprise

The installer finishes by offering: **open the panel** (once the panel exists and `web.enabled` is true —
ADR-0027 §4.4) or **stay on the command line** (print the headless commands). It never decides for the
operator, and until the panel exists the second option is the only one and is labelled as such.

The compose path keeps the same shape: the installer prepares the database credentials, `init` writes
the host files, then it brings the stack up and runs `db upgrade` plus `people add` inside the
container (`docker compose exec monoagens monoagens ...`). No second installer is written for containers.

### 4.6 The logo lands later, the slot does not move

The banner reserves the logo area now so the mark can be dropped in without touching the wizard's
layout, and it is never a config key: the installer's appearance is code, not configuration.

## 5. Configuration

| Flag | Meaning |
|---|---|
| `--dir PATH` | install directory (default `~/.monoagens/app`) |
| `--stack native\|compose` | runtime choice; both use a Postgres container unless `--database-url` is external |
| `--engine podman\|docker` | container runtime for Postgres; the menu shows detection status |
| `--database container\|external` | native database choice; external needs `--database-url` |
| `--install-runtime` | allow installing Podman/Docker and its compose provider with `sudo` when missing |
| `--dry-run` | print every action, write nothing |
| `--non-interactive` | never prompt; fail if a required value is missing |
| `--provider NAME` / `--model ID` | forwarded to `init` |
| `--database-url URL` | forwarded to `init` and written to `.env` as a value, never into YAML |
| `--skip-selftest` | skip the closing self-test (for automation that runs it separately) |
| `--voice-layer on\|off` | keep emotion, humanize, and character on (default `off`, ADR-0041) or turn all three off |
| `--no-color` | plain output (also honoured via `NO_COLOR`) |

No key is added to `config.yaml` by this ADR: the installer's behaviour is flags, and the host
configuration stays what `init` writes.

## 6. Metrics and gates

| Metric | Initial target |
|---|---|
| Wizard configuration defaults outside `monoagens/admin/templates.py` | 0 |
| `sudo` invocations without explicit consent | 0 |
| Database credentials generated for a container | 1 password, written only to `.env` at `0600` |
| Prompts when stdin is not a terminal | 0 (fail with the flag list instead) |
| Secrets written to YAML, logs, or history by the wizard | 0 |
| Files left behind by `Ctrl-C` mid-run | 0 |
| Behaviour difference between `--dry-run` and a real run | 0 actions, 0 writes |
| Steps without a flag equivalent | 0 |
| Logo-dependent layout | 0 (slot sized now, filled later) |

## 7. Acceptance criteria

- [ ] `bash install.sh --dry-run` prints the plan and leaves the filesystem unchanged (checked with a before/after listing).
- [ ] On a machine without `uv`, the pinned release is fetched, its SHA-256 verified, and a mismatch aborts before anything else happens.
- [ ] On a machine without Postgres or a container runtime, the installer prints the exact install command and does not run it without consent.
- [ ] `install.sh` performs no `sudo` call in a normal run; `--install-runtime` or an explicit `y` is the only path that installs a runtime.
- [ ] `monoagens init` on a TTY walks provider → model → database URL → administrator label, shows a review screen, then writes the four files with `.env` and `roles.yaml` at `0600`.
- [ ] `monoagens init` with piped stdin and no flags exits non-zero naming the missing flags; it never blocks.
- [ ] `Ctrl-C` at any wizard step leaves no new file and no partial file (checked with a before/after listing).
- [ ] An unknown provider name or an unreachable model list fails at the asking step, before any file is written.
- [ ] Running the wizard twice offers `keep` / `change` / `replace` and never overwrites silently.
- [ ] The closing choice offers panel and command line; with no panel built, the panel option is absent and the command-line steps are printed.
- [ ] The same `init` invocation works inside the compose container with no different flags.
- [ ] `NO_COLOR=1` and a 60-column terminal produce readable plain output, and the logo slot degrades to text.
- [ ] The install log contains no secret value (test with a marker password).
- [ ] `pytest -q` and `python scripts/adr.py --check` still pass; the wizard's own behaviour is covered by CLI tests.

## 8. Risks and mitigations

| Risk | Mitigation |
|---|---|
| `curl \| bash` executes something the operator never read | `--dry-run`, a short header that names the repository and revision, and the documented `bash install.sh` or `bash <(curl ...)` alternative. The pipe form has no TTY, so the wizard fails closed instead of hanging. |
| The wizard drifts into a second source of defaults | §4.3: values come from `templates.py` plus answers; §6 metric and the review screen make a second default visible |
| A wizard that hangs in automation | Piped stdin is never prompted; the missing flags are named instead |
| Half-written host after an interruption | Write-then-rename, and nothing is written before the review step is accepted |
| The installer slowly grows into a package manager | It installs only Podman/Docker and its compose provider, only after explicit consent; every other package is printed, not installed |
| Pinned `uv` rots | The pin block carries its own bump instruction and is one edit; the version is printed on every run |
| Python 3.14+ promise versus a 3.14 image | Both numbers are stated where they apply; the installer reports the interpreter it used |
| The logo lands late and breaks the layout | The slot is sized and degrades to text; no configuration depends on it |
| A future panel needs machine-readable output | `--non-interactive` already exists; a JSON result can be added without changing the interactive design (a decision for the panel ADR) |

## 9. Interaction with other ADRs

- **ADR-0027**: the panel stays the primary configuration surface; this ADR only decides how a host is
  created and how the operator reaches that panel.
- **ADR-0022**: the wizard asks for the first administrator's label; the role stays protected and can
  only be renamed through `roles.yaml` plus `config import`.
- **ADR-0016**: `db upgrade` after `init` is the only schema step the installer runs.
- **ADR-0029**: wizard behaviour is covered by CLI tests; a test that touches the store uses the real
  store, not a simulated one.
- **ADR-0019**: the install log is an operator artifact with a closed set of failure classes; no content,
  no path, and no secret is recorded beyond what a failure needs.
- **ADR-0028**: "enable a plugin" stays an operator action after install; the installer never enables,
  fetches, or grants anything.

## 10. Code references

- `monoagens/admin/init.py:14-46` — the writer, its `0600` modes, and its never-overwrite rule.
- `monoagens/admin/templates.py:1,151,253` — the single default source the wizard reuses.
- `monoagens/__main__.py:587,709-714` — `_init`, the `init` subcommand, and the required `--provider`.
- `monoagens/providers/__init__.py` — provider names and their key variables, used for validation.
- `README.md:88-105`, `README.id.md:96-112` — the quickstart this ADR's native path sits beside.
- `compose.yaml` — the container path that keeps the same `init`.
- `monoagens/admin/install_ui.py` — installer menus on the shared terminal layer.
- `install.sh` — runtime, database, and engine choices; opt-in runtime install; generated database credentials; the curl bootstrap.
- `pyproject.toml` — unchanged: the installer adds no dependency, and the pinned `uv` is fetched, not vendored.

---

*This ADR lives at `docs/v0.1.0/adr/0030-install-and-init.md`.*