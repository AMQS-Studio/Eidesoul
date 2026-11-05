# ADR-0007: Distribution, Installation, and Initialization — Terminal Wizard, Thin Bootstrap, and Container Packaging

- **Status:** accepted
- **Date:** 2026-11-05
- **Deciders:** Muqsith (owner)
- **Context:** Re-baselined for v0.1.0; consolidates distribution, installation, and terminal wizard interfaces (pre-rebaseline 0027, 0030, 0039) with the container and installation fixes in pre-rebaseline 0049 and 0053.
- **Supersedes:** pre-rebaseline 0027, 0030, 0039
- **Related:** ADR-0001 (sovereign deployment), ADR-0006 (access control)
- **Implementation:** partial — terminal init wizard and base installer exist; container migration copy and bootstrap ordering fixes are scheduled in Phase P0.

---

## 1. Context

Setting up a sovereign agent must be fast and foolproof for self-hosters. The audit (`docs/audit.md`
§1.4.1, §4.2) revealed several distribution and installation breakdowns:
1. **Broken Compose Installation:** `install.sh --stack compose` failed immediately on fresh machines
   because `Dockerfile` did not copy `migrations/`, `install.sh` started the container before the admin
   user was created (triggering a fatal crash loop), and single-file bind mounts in `compose.yaml` caused
   `os.replace()` to fail with Linux `EBUSY` errors.
2. **Ghost Web Panel:** Previous documentation promised an operator web panel in a `panel/` directory,
   leading operators to expect a browser-based setup interface that did not exist in the repository.
3. **Monolithic Script Duplication:** `install.sh` was an unwieldy 1,753-line script that duplicated
   configuration scaffolding logic already implemented cleanly in Python by `monoagens init`.

## 2. Decision drivers

1. **One Authoritative Wizard:** `monoagens init` in Python owns all interactive setup logic, question
   flows, credential masking, and configuration scaffolding.
2. **Thin Bootstrap Shell:** `install.sh` serves only as a lightweight curl-to-bash bootstrap to verify
   runtimes (Python/Docker), install `uv`, and invoke `monoagens init`.
3. **Robust Container Lifecycle:** The Docker image must include all migration files, mount configuration
   directories rather than individual files, and run database upgrades and admin bootstrap before starting services.
4. **Clean by Default:** Gateways and plugins remain uninstalled and disabled until explicitly chosen
   by the operator during setup.
5. **No Phantom Surfaces:** The web panel is deferred to an independent operator project; core MonoAgens
   remains terminal and CLI-first.

## 3. Options considered

**A. Browser-First Web Setup Wizard.** Rejected: requires hosting an HTTP server in the engine or
bundling a separate web runtime, violating the lightweight self-hosted harness principle.

**B. Pure Shell-Script Installer (`install.sh`).** Rejected: maintaining complex configuration
generation in Bash is brittle and duplicates Python logic.

**C. Python Terminal Wizard (`curses`) with Thin Bootstrap.** Selected: provides a polished, interactive
terminal experience (`monoagens init`) with reliable fallback for headless environments.

## 4. Decision

### 4.1 Interactive Terminal Setup Wizard (`monoagens init`)

- Driven by `monoagens/admin/terminal.py` (curses UI with numbered fallback for dumb terminals).
- Step sequence:
  1. Language and location selection (detects host timezone and locale).
  2. Database configuration (PostgreSQL connection string validation).
  3. LLM provider selection and API key configuration (with credential masking).
  4. Model discovery via provider APIs.
  5. Voice and persona customization (defaults off).
  6. First administrator identity creation.
- Configuration writer outputs fully commented `config.yaml`, `roles.yaml`, `soul.yaml`, and `.env`.

### 4.2 Lightweight Bootstrap Script (`install.sh`)

- `install.sh` is simplified to a thin bootstrap script (<200 lines):
  - Checks system dependencies (curl, git, Python 3.14+ or Docker/Podman).
  - Installs `uv` package manager if absent.
  - Clones or updates the repository.
  - Launches `monoagens init` to handle configuration.

### 4.3 Container Packaging and Lifecycle Fixes

- `Dockerfile` copies `migrations/` into the application image.
- `compose.yaml` mounts the entire configuration directory (or parent directory) rather than individual
  file mounts, preventing `EBUSY` when `os.replace()` writes updated configs.
- Container initialization runs `db upgrade` and `people add` to bootstrap the administrator before
  launching the main `monoagens` primary process.
- The web panel promise is removed from documentation; core remains CLI/terminal-driven.

## 5. Configuration

Output of `monoagens init`:
- `config.yaml`: Core engine and enabled gateway settings.
- `roles.yaml`: Initial RBAC configuration with bootstrapped administrator.
- `soul.yaml`: Persona identity and voice defaults.
- `.env`: Secret keys (e.g. `OPENAI_API_KEY`, `POSTGRES_PASSWORD`).

## 6. Metrics and gates

- Automated container test: `docker compose up -d` boots cleanly on a fresh environment.
- Config writer test: Modifying settings preserves all comments and structural indentation.
- Terminal fallback test: Running in a non-TTY environment falls back to standard line-input prompts.

## 7. Acceptance criteria

- [ ] `Dockerfile` packages `migrations/` and succeeds on container `db upgrade`.
- [ ] `install.sh` completes installation and administrator bootstrapping without manual file edits.
- [ ] No references to a root `panel/` directory remain in active documentation.
- [ ] Configuration writes survive Linux bind-mount updates without `EBUSY` errors.

## 8. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Operators want graphical web management | Independent web panel can interact via standard database and CLI interfaces |
| Non-standard terminal sizes break curses | Automatic fallback to line-based numbered question prompts |

## 9. Interaction with other ADRs

- **ADR-0001:** Enforces single-tenant deployment invariants during initialization.
- **ADR-0006:** Bootstraps the initial protected `administrator` account.

## 10. Code references

- `monoagens/admin/terminal.py` — Interactive curses terminal interface.
- `monoagens/admin/wizard.py` — Step-by-step setup state machine.
- `monoagens/admin/templates.py` — Fully commented YAML configuration templates.
- `Dockerfile`, `compose.yaml`, `install.sh` — Packaging and deployment manifests.

---

*This ADR lives at `docs/v0.1.0/adr/0007-distribution-install-and-initialization.md`.*
