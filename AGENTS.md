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
  Root `eidesoul/` only holds `__main__.py`, `admin/`, `gateways/`, `library/`, `lang/`, `providers/`,
  `vendor/`.
- Core may import `eidesoul.providers`: that package holds endpoint and auth facts for the
  selectable AI providers and nothing else, and it never imports an engine module. Select a
  provider by name in `engine.llm.provider`; explicit `base_url`/`api_key_env` stay overrides.
  Provider facts live one module per endpoint format (`openai.py`, `gemini.py`, `anthropic.py`).
  Quirks are fields on `ProviderDefaults` (`session_header`, `api_key_optional`, `models_url`),
  never provider-name comparisons in the engine. A different wire protocol needs an adapter in
  `eidesoul/providers/` plus a seam in `llm.py`, never a data entry.
- Never pin a model id in code: `engine.llm.model` is the operator's choice, written by
  `eidesoul init --model` (list ids with `eidesoul models`) and required by the loader. Reasoning
  effort is the same: `engine.llm.reasoning_effort` empty means the provider's own default, and no
  internal call may force a value.
- Versioning stays honest about churn: while nothing is released, `pyproject.toml` carries a PEP 440
  dev version (`0.0.0.devN`), never a release-looking one; `uv.lock` mirrors it. The first release
  starts at `0.1.0`, then semver `MAJOR.MINOR.PATCH` with `aN`/`bN`/`rcN` pre-release suffixes.
  HTTP user agents carry no version.
- Guard modules live only under `eidesoul/engine/guard/` (`patterns.py`, `policy.py`, `sandbox.py`);
  callers import the package (`from .guard import ...`), never a module path inside it.
- Eidesoul is a soul engine, not an agent product and not an assistant template. Assistant is one
  role among companion, advisor, and robot embodiments.
- Tools are removable capabilities. The six core families (`shell`, `read`, `write`, `web_search`,
  `sandbox`, `mcp`) are examples; a robot or custom gateway exposes its body-specific actions in
  `eidesoul/gateways/<name>/tools`, never in core. The engine never hardcodes a body or an agent loop.
- A gateway is an appendage: transport only through the contract in `eidesoul/engine/inbound.py`
  (`Inbound`, `Gateway`, `GatewayError`). Gateway-owned tools/commands live in
  `eidesoul/gateways/<name>/tools|commands`, never in core.
- The core holds no gateway or provider vocabulary: no transport address suffix test, no JID
  parsing, no provider name. Roles match `Inbound.person_id`, deletion and role comparison use the
  canonical form from `permissions.normalize`, the DM/group label is `Inbound.is_dm`,
  mentionability is `ChatMessage.sender_kind == "person"`, and gateways normalize their own
  identities (`eidesoul/gateways/whatsapp/identity.py`). `engine.llm.provider` is required and has
  no code default; `eidesoul init --provider` writes it.
- Every optional feature must be removable: extras in `pyproject.toml`, lazy imports, no core import
  of gateway or vendor libraries, no compatibility shims. Prefer deleting an optional path over
  carrying a fallback.

## Capability spine

- The spine is required, never optional: Postgres+pgvector storage, Mem0 semantic memory, one shared
  embedder (`eidesoul/engine/embed.py`, MiniLM, 384 dim), the affect prototypes under
  `eidesoul/library/affect/`, the guard, and the lease. Transport and integrations stay extras
  (`whatsapp`, `google`).
- A dependency the spine needs goes into `dependencies`, not into an extra; a feature that only a
  gateway or a body needs stays an extra. The Dockerfile installs `EXTRAS` (empty by default) on top
  of the locked core set.

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
- Affect prototypes live in `eidesoul/library/affect/<language>.json`: exactly the eight
  `EMOTION_VAD` labels, at least six short casual sentences each, never a name, URL, or phone number.
  `scripts/affect.py --check` gates them.
- Edit packs with `scripts/patterns.py` and `scripts/fakta.py` (`--pack`, `--check`, then `--embed`);
  grow new rows from the corpus with `scripts/grow.py`, never by hand.
- `soul.style` is derived, not hand-tuned: `eidesoul library resolve` (or `eidesoul init`) writes it
  from the soul text, gated by language, and never forces a pack. A kind without a match keeps the
  packs the soul already selected.

## Licensing

- The project is Apache-2.0 (`LICENSE`, `pyproject.toml` `license` + `license-files`). Every new
  dependency ships its row in the README credits table (component, license, author, source, use) in
  the same commit; both `README.md` and `README.id.md` carry the same rows.
- Record the license the installed distribution declares. A package with no declared upstream
  license is written as `not declared upstream` with its repository link, never guessed.

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
