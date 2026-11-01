# AGENTS

Hard rules for changes in this repo.

## Language

- English is the default for all code, comments, docstrings, log messages, internal `error:`
  strings, CLI help, prompt scaffolding, model-facing payloads, config comments, tests, and docs.
- Non-English is allowed only in these data surfaces: `eidesoul/lang/<code>.yaml` (chat catalogs),
  `soul.yaml` persona content, `eidesoul/library/**` character/fact packs, and `README.id.md`.
- No hardcoded chat-visible string in code: call `t()` from `eidesoul/engine/i18n.py` and let the
  catalog supply the text. English code strings are internal diagnostics, never chat replies.

## Shape

- Python only. No HTTP gateway, no Node/Baileys, no bridge, no extra server.
- Tracked docs are `TASK.md`, `README.md`, `README.id.md`, `LOG.md`, `AGENTS.md`, and `soul.yaml`.
  Do not create other tracked `.md` files.
- Core lives in `eidesoul/engine/` and must not import `eidesoul.gateways` or `neonize`.
  Root `eidesoul/` only holds `__main__.py`, `admin/`, `gateways/`, `library/`, `lang/`, `vendor/`.
- Eidesoul is a soul engine, not an agent product and not an assistant template. Assistant is one
  role among companion, advisor, and robot embodiments.
- Tools are removable capabilities. The six core families (`shell`, `read`, `write`, `web_search`,
  `sandbox`, `mcp`) are examples; a robot or custom gateway exposes its body-specific actions in
  `eidesoul/gateways/<name>/tools`, never in core. The engine never hardcodes a body or an agent loop.
- A gateway is an appendage: transport only through the contract in `eidesoul/engine/inbound.py`
  (`Inbound`, `Gateway`, `GatewayError`). Gateway-owned tools/commands live in
  `eidesoul/gateways/<name>/tools|commands`, never in core.
- Every optional feature must be removable: extras in `pyproject.toml`, lazy imports, no core import
  of gateway or vendor libraries, no compatibility shims. Prefer deleting an optional path over
  carrying a fallback.

## Extending

- A new tool is a module in `eidesoul/engine/tools/` with `TOOL = Handler(...)` and a family from
  `FAMILIES`; the filename must match `name`; never edit the registry.
- A new command is a module in `eidesoul/engine/commands/` with `COMMAND = Handler(...)`; never edit
  the dispatcher.
- Permissions come only from roles (`role:<name>` documents / the `roles.yaml` mirror). No handler
  has default permissions. A missing `tools`/`commands` entry denies everything. Tool permission is
  checked only at `eidesoul/engine/tools/__init__.py:execute`.
- Semantic memory only through Mem0 (`eidesoul/engine/memory/mem0.py`, `mem0ai`). Never revive
  `MemoryStore`/custom reflection. The action ledger stays `ActionLedger`; transcripts stay per chat.

## Config and soul

- `soul.yaml` is the only persona source. The `soul` document in the database is authoritative and
  `soul.yaml` is seed plus mirror; there is no legacy persona file and no `persona` document.
- Documents `engine`, `soul`, and `role:<name>` are the source of truth in the database;
  `config.yaml` (engine section), `soul.yaml`, and `roles.yaml` are seeds plus mirrors.
  Manual edit → `eidesoul config import`. The loader must never create directories.
- Config layout: one host-local `config.yaml` holds host keys (`database`, `workspace`, `roles_file`,
  `soul_file`, `sandbox`, `web_search`, `mcp`, `sync_documents`) plus the `engine` and `gateways`
  sections. One `roles.yaml` holds every role. One `soul.yaml` holds the soul. There are no separate
  `engine.yaml`/`gateway.yaml`/`soul.yaml`-per-section files and no `roles/` directory.
- Secrets only in `.env` (env names referenced from YAML); `config.yaml`, `roles.yaml`, `.env`, and
  any pre-v1 runtime data directory are never committed.
- Storage: SQLAlchemy Core + `psycopg` to Postgres; one `Database` (`eidesoul/engine/db.py`) and one
  `MetaData` (`eidesoul/engine/schema.py`). No `sqlite3`, `PRAGMA`, FTS5, or state files in stores;
  SQLite is only a test URL. `SCHEMA_VERSION` starts at 1 for a fresh install.
- Any `pyproject.toml` change ships with a regenerated `uv.lock` in the same commit: the image runs
  `uv sync --locked`, which fails on a stale lock even when only the `dev` extra moved.

## Character data

- Character data lives only in `eidesoul/library/` packs selected by `soul.style`
  (`patterns` and `facts` pack ids, `registers`, `max_patterns`, `max_facts`). No pack selected means
  no character data and no facts. No persona text and no raw transcripts in the core.
- Pack ids match `^[a-z0-9][a-z0-9-]{0,63}$`. Pattern packs carry `version`, `model`, `language`,
  `lexicon`, and `patterns`; fact packs carry `version`, `model`, `language`, and `facts`.
- Never put names, phone numbers, URLs, or raw transcripts into a pattern pack. Every fact carries at
  least one source URL. A fact is not a move: never move one between the two kinds.
- Edit packs with `scripts/patterns.py` and `scripts/fakta.py` (`--pack`, `--check`, then `--embed`);
  grow new rows from the corpus with `scripts/grow.py`, never by hand.

## Safety

- The sandbox guard is mandatory: the deterministic layer always runs and the small fast LLM
  classifier only sees what the first layer cannot classify. Non-approver roles are denied, never
  parked in a pending approval. `approved=True` (`/approve`) bypasses both layers.
- Sensitive actions (`shell`, `sandbox`) and internal changes (`settings`) must pass
  `SafetyPolicy`/`approvals` and `/approve`. Default mode `yolo`; `off` disables approval (document
  the risk). `/approve` never calls the LLM and re-checks role permissions.
- One primary: `engine_lease`. An instance without the lease stays standby and frozen; `/freeze` and
  takeover happen only through the lease/DB, never two hearts.

## Memory and privacy

- Cross-chat identity: query only `global` + `person:<sender>` + `chat:<this chat>`; another person's
  private memory is never queried and must not leak into another chat.
- Memory carries a numeric privacy level 0-4 (public, team, personal, sensitive/aib, secret).
  Level 5/credentials are never stored. `owner_id` is the speaker who stated the fact; `subject_id`
  is who it is about and only that subject may veto it. Level maths, veto precedence, and disclosure
  verdicts live in `eidesoul/engine/memory/privacy.py` (deterministic, no LLM). The LLM only labels a
  provisional level, phrases the confirmation question, and identifies which entries a draft
  mentions; `eidesoul/engine/privacy.py` is the only agent-facing hook.
- Disclosure policy: L0-L2 stay fluid everywhere; L3 in a group and L4 ask for confirmation through
  `approvals`; vetoed entries are never shown and never disclosed. The auditor pass runs only when a
  risky entry exists.

## Language

- Chat-visible strings live only in `eidesoul/lang/<code>.yaml`; `soul.language` selects the catalog,
  so adding a language means adding one YAML file with the same `messages:` keys. `/language <code>`
  (owner-only) writes the soul and its `soul.yaml` mirror.

## Optional integrations

- Google is an optional extra (`--extra google`) with lazy imports; without it the `google` tool
  returns a clear `error:` and stays inert.
- WhatsApp is an optional gateway extra (`--extra whatsapp`). Default config never enables it, core
  never imports it eagerly, and the default member role does not receive WhatsApp-only `send`.

## Verify

- `pytest -q` and `python -m eidesoul selftest`. For gateway/lease changes also smoke
  `eidesoul console --takeover`.
