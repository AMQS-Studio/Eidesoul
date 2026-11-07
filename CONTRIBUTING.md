# Contributing to MonoAgens

Thank you for your interest in contributing to **MonoAgens**!

MonoAgens is built around a strict set of architectural invariants: **one continuous Soul, one canonical PostgreSQL + `pgvector` store, one active primary process, and a pure hexagonal boundary between the core engine and its transport bodies.** Before opening an issue or pull request, please read this guide and [`AGENTS.md`](AGENTS.md).

---

## Table of Contents

1. [Core Architectural Principles](#core-architectural-principles)
2. [Development Environment Setup](#development-environment-setup)
3. [Coding Standards & Hygiene Budgets](#coding-standards--hygiene-budgets)
4. [Extending MonoAgens](#extending-monoagens)
5. [Verification Gate](#verification-gate)
6. [Pull Request & Commit Guidelines](#pull-request--commit-guidelines)

---

## Core Architectural Principles

Every contribution must preserve the system boundaries defined in [`AGENTS.md`](AGENTS.md) and [`docs/v0.1.0/README.md`](docs/v0.1.0/README.md):

1. **Sacred Core (`monoagens/engine/`):**
   - Core must remain 100% transport-agnostic and UI-agnostic.
   - `monoagens/engine/**` must **never** import `monoagens.gateways`, transport SDKs (`neonize`, `discord.py`, `slack_bolt`), or construct platform-specific UI widgets/buttons.
2. **Appendage Seam (`monoagens/engine/inbound.py` & `monoagens/gateways/`):**
   - Gateways communicate with the engine exclusively via immutable `Percept`, `Address`, `Action`, and `Receipt` contracts.
   - Gateways own address normalization (`normalize_address`), rate-limited progress editing (`EditDebouncer`), and interactive prompt rendering (`BaseGateway.prompt_options`).
3. **Non-Blocking Async Event Loop:**
   - `async def` functions must **never** block the event loop on synchronous SQLAlchemy queries, CPU-bound embeddings/NER, or blocking disk/subprocess I/O.
   - Always offload synchronous operations through the bounded executor: `await io.run(...)`.
4. **Single Outbound Door & Atomic Lease Fencing:**
   - Every outbound message, file, or transport action must pass through `Agent.dispatch()` with an explicit `origin: Address`.
   - Every mutating PostgreSQL store operation must verify the active `engine_lease` generation fence.
5. **Language & Localization (`i18n`):**
   - All source code, comments, docstrings, log messages, internal error strings, CLI diagnostics, tests, and ADRs (`docs/v0.1.0/adr/`) must be written in **English**.
   - Never hardcode user-facing chat replies in Python code—resolve all chat strings through `t()` in `monoagens/engine/i18n.py` and add corresponding keys to both `monoagens/lang/en.yaml` and `monoagens/lang/id.yaml`.

---

## Development Environment Setup

### Prerequisites
- **Linux or macOS** (POSIX environment)
- **Python 3.14+** and **[`uv`](https://docs.astral.sh/uv/)**
- **Podman or Docker** (for running the real PostgreSQL 17 + `pgvector` test database)

### 1. Clone & Install Dependencies

```bash
git clone https://github.com/AMQStudio/MonoAgens.git
cd MonoAgens

# Create a virtual environment and install core + dev + optional gateway dependencies
uv venv .venv --python 3.14
uv pip install --python .venv/bin/python -e ".[dev,whatsapp,telegram,discord,slack]"
```

### 2. Start the Test Database (`pgvector:pg17`)

MonoAgens tests run against a **real PostgreSQL + `pgvector` database** (never SQLite mocks, because hybrid `vector(384)` HNSW + `tsvector` GIN queries, advisory/row locks, and `engine_lease` fencing are PostgreSQL-native):

```bash
podman run -d --name monoagens-test-pg -p 5433:5432 \
  -e POSTGRES_DB=monoagens_test \
  -e POSTGRES_USER=monoagens \
  -e POSTGRES_PASSWORD=monoagens \
  docker.io/pgvector/pgvector:pg17

export MONOAGENS_TEST_DATABASE_URL="postgresql+psycopg://monoagens:monoagens@127.0.0.1:5433/monoagens_test"
```

---

## Coding Standards & Hygiene Budgets

- **Module Size Cap (`<= 800` lines):** Every Python file under `monoagens/` must stay at or below **800 lines** (enforced by `scripts/selfcheck.py --check` with an empty `OVER_CAP_ALLOWED` set). Split large modules into cohesive subpackages rather than growing monoliths.
- **Strict Static Typing:** All `monoagens`, `scripts`, and `plugins` packages are checked with Pyright (`scripts/types.py --check`) and must produce `0 errors`. For optional plugin dependencies that may not be installed in minimal environments, use `importlib.import_module(...)` at runtime.
- **No Hardcoded Model IDs in Engine Logic:** Model names, temperatures, fallbacks, and budgets come from operator configuration (`config.yaml` / `soul.yaml`), never hardcoded constants inside turn or tool execution paths.
- **Preserve YAML Comments:** Configuration mutations must go through `monoagens/admin/yaml_io.py` and dataclass-derived templates (`monoagens/admin/templates.py`) so operator comments and section banners are preserved.

---

## Extending MonoAgens

For full normative specifications, read the **[Gateway, Plugin, and MCP Contract](docs/v0.1.0/contracts/gateway-plugin.md)** ([Indonesian version](docs/v0.1.0/contracts/gateway-plugin.id.md)).

### Adding or Modifying a Core Tool or Command
- Core tools live in `monoagens/engine/tools/<name>.py`; core commands live in `monoagens/engine/commands/<name>.py`.
- Export `TOOL = Handler(...)` or `COMMAND = Handler(...)` with explicit `family`, `approval_class` (`none`, `consent`, or `authorization`), and `path_arg` / `scan` metadata.
- For command `usage` strings, omit the leading `/<name>` prefix (the `/help` renderer prepends `/{name}` automatically) and add localized descriptions in `monoagens/lang/en.yaml` and `monoagens/lang/id.yaml` under `commands.catalog.<name>`.

### Adding a Gateway
- Create a package under `monoagens/gateways/<name>/`, subclass `BaseGateway` (`monoagens/gateways/base.py`), and decorate with `@register_gateway`.
- Register optional dependencies in `pyproject.toml` under `[project.optional-dependencies]`.
- Keep all transport-specific tools/commands inside `monoagens/gateways/<name>/` and never import the gateway from `monoagens/engine/**`.

### Working with Plugins
- Plugins live under `plugins/<name>/` with a `plugin.yaml` manifest and `plugin.py` entrypoint.
- Whenever you modify files inside a plugin tree, recompute and update its SHA-256 `digest` in `plugin.yaml` using:
  ```bash
  .venv/bin/python -m monoagens plugins digest plugins/<name>
  ```

### Proposing an Architectural Decision Record (ADR)
- Significant architectural changes require an English ADR under `docs/v0.1.0/adr/`:
  ```bash
  .venv/bin/python -P scripts/adr.py --new "Your ADR Title"
  .venv/bin/python -P scripts/adr.py --check
  ```

---

## Verification Gate

Before submitting a pull request, run the full verification suite locally—every check must pass with zero errors:

```bash
# 1. Linter & formatting check
.venv/bin/ruff check monoagens scripts tests plugins migrations

# 2. Architectural invariants, module line caps (<= 800 lines), and i18n catalog parity
.venv/bin/python -P scripts/selfcheck.py --check

# 3. Static type checking across monoagens, scripts, and plugins
.venv/bin/python -P scripts/types.py --check

# 4. ADR structure, numbering, and section reference check
.venv/bin/python -P scripts/adr.py --check

# 5. Full integration & unit test suite against PostgreSQL + pgvector
MONOAGENS_TEST_DATABASE_URL="postgresql+psycopg://monoagens:monoagens@127.0.0.1:5433/monoagens_test" \
  .venv/bin/pytest -q
```

---

## Pull Request & Commit Guidelines

1. **Conventional Commits:** Format commit messages clearly, e.g.:
   - `feat(tools): add sed surgical file-edit tool`
   - `fix(gateways): debounce progress edits on Telegram`
   - `docs(adr): add ADR-0010 for deep native gateways`
   - `chore(cleanup): remove unreferenced dead code`
2. **Third-Party Credits:** If your PR introduces a new third-party library (in core, a gateway extra, or a plugin), add its license and author attribution to the **Credits** table in [`README.md`](README.md#credits) (and `plugin.yaml: credits` for plugins).
3. **License:** By contributing to MonoAgens, you agree that your contributions will be licensed under the [Apache License 2.0](LICENSE).
