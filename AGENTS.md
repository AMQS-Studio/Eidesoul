# AGENTS

Hard rules for changes in this repo.

## Language

- English is the default for all code, comments, docstrings, log messages, internal `error:`
  strings, CLI help, prompt scaffolding, model-facing payloads, config comments, tests, and docs.
- Non-English is allowed only in these data surfaces: `monoagens/lang/<code>.yaml` (chat catalogs),
  `soul.yaml` persona content, detector data under `monoagens/library/**`, `README.id.md`, and the
  Indonesian ADR copies in `docs/v*/adr/id/**` (the ADRs themselves are English in `docs/v*/adr/`).
- No hardcoded chat-visible string in code: call `t()` from `monoagens/engine/i18n.py` and let the
  catalog supply the text. English code strings are internal diagnostics, never chat replies.

## Shape

- Python only. The engine is not a server: no HTTP gateway, no Node/Baileys, no bridge. The web panel
  is a separate operator app at the repo root (`panel/`), a different process that `monoagens/engine/`
  never imports (ADR-0027).
- Tracked docs are `TASK.md`, `README.md`, `README.id.md`, `LOG.md`, `AGENTS.md`, `soul.yaml`, and
  the ADR home `docs/v*/README.md` + `docs/v*/adr/**` (ADR-0009). An ADR sits at the ADR home root
  (cross-cutting), under `engine/`, or under `web/`; each folder keeps its Indonesian copies in its own
  `id/` subfolder. Do not create other tracked `.md` files. The ADR gate is
  `python scripts/adr.py --check`.
- Allowed root trees are `docs/`, `scripts/`, `tests/`, `migrations/`, `plugins/`, `panel/`
  (the operator app), and `apps/` (any future application that imports the engine). Schema changes live only in `migrations/`; the engine may hold
  `plugins.py` (plugin loader) and `self_knowledge.py` (capability index) beside its other modules.
- The engine tree is the one in ADR-0031 §4.3. The top level is 16 files — `agent.py` (the handle),
  `inbound.py` (the body seam), `plugins.py`, `registry.py`, `llm.py`, `config.py`, `db.py`,
  `schema.py`, `embed.py`, `i18n.py`, `io.py`, `runtime.py`, `soul.py`, `self_knowledge.py`, `clock.py`, and
  `__init__.py` (a written map, no imports). Everything else lives in the folder that owns its reason
  to change: `turn/` (context, decision, assembly, salience, prompts, reply, research,
  observability, loop, intake, delivery, commands, steps, reload, plugin_registration), `character/`
  (character, lexicon), `access/` (roles, permissions, identity, privacy, approvals, relay, target),
  `jobs/` (cognition, automation_manifest, automation_store, automation_worker, supervisor, lease,
  ambient, mcp), plus the fixed contracts `memory/`, `stores/`,
  `tools/`, `commands/`, `guard/`.
  `ablate.py` is a research tool, not engine code: it lives at `scripts/ablate_engine.py`.
- A module has one reason to change and stays at or below 800 lines; a stateful module above that
  needs a line in the ADR that introduces it (ADR-0031 §4.1). A new engine module is allowed when it
  replaces one, splits a module over the cap, or the ADR names the reason (§4.5).
- Core lives in `monoagens/engine/` and must not import `monoagens.gateways` or `neonize`.
  Root `monoagens/` only holds `__main__.py`, `admin/`, `gateways/`, `library/`, `lang/`, `providers/`,
  `vendor/`.
- Core may import `monoagens.providers`: that package holds endpoint, auth, wire, and adapter facts
  for the selectable AI providers, and it never imports an engine module. Select a provider by name
  in `engine.llm.provider`; explicit `base_url`/`api_key_env` stay overrides. Provider facts live one
  module per wire (`openai.py`, `gemini.py`, `anthropic.py`). Quirks and capabilities are fields on
  `ProviderDefaults` (`session_header`, `api_key_optional`, `models_url`, `wire`,
  `sampling_supported`, `safety_supported`), never provider-name comparisons in the engine. A
  different wire protocol needs an adapter in `monoagens/providers/` plus the seam in `llm.py`, never
  a data entry.
- Never pin a model id in code: `engine.llm.model` is the operator's choice, written by
  `monoagens init --model` (list ids with `monoagens models`) and required by the loader. Reasoning
  effort is the same: `engine.llm.reasoning_effort` empty means the provider's own default, and no
  internal call may force a value.
- Versioning stays honest about churn: while nothing is released, `pyproject.toml` carries a PEP 440
  dev version (`0.0.0.devN`), never a release-looking one; `uv.lock` mirrors it. The first release
  starts at `0.1.0`, then semver `MAJOR.MINOR.PATCH` with `aN`/`bN`/`rcN` pre-release suffixes.
  HTTP user agents carry no version.
- Guard modules live only under `monoagens/engine/guard/` (`patterns.py`, `policy.py`, `sandbox.py`);
  callers import the package (`from .guard import ...`), never a module path inside it.
- MonoAgens is an agent; its runtime is a persistent mono agent harness (ADR-0040). Assistant is one
  role among companion, advisor, and robot embodiments; the `soul` document names the internal
  identity, never the product.
- Tools are removable capabilities. The six core families (`shell`, `read`, `write`, `web_search`,
  `sandbox`, `mcp`) are examples; a robot or custom gateway exposes its body-specific actions in
  `monoagens/gateways/<name>/tools`, never in core. The engine never hardcodes a body or an agent loop.
- A gateway is an appendage: transport only through the contract in `monoagens/engine/inbound.py`
  (`Inbound`, `Gateway`, `GatewayError`). Gateway-owned tools/commands live in
  `monoagens/gateways/<name>/tools|commands`, never in core.
- The core holds no gateway or provider vocabulary: no transport address suffix test, no JID
  parsing, no provider name. Roles match `Inbound.person_id`, deletion and role comparison use the
  canonical form from `permissions.normalize`, the DM/group label is `Inbound.is_dm`,
  mentionability is `ChatMessage.sender_kind == "person"`, and gateways normalize their own
  identities (`monoagens/gateways/whatsapp/identity.py`). `engine.llm.provider` is required and has
  no code default; `monoagens init --provider` writes it.
- Every optional feature must be removable: extras in `pyproject.toml`, lazy imports, no core import
  of gateway or vendor libraries, no compatibility shims. Prefer deleting an optional path over
  carrying a fallback.

## Capability spine

- The spine is required, never optional: Postgres+pgvector storage, Mem0 semantic memory, one shared
  embedder (`monoagens/engine/embed.py`, MiniLM, 384 dim), the affect prototypes under
  `monoagens/library/affect/`, the guard, and the lease. Transport and integrations stay extras
  (`whatsapp`, `google`).
- A dependency the spine needs goes into `dependencies`, not into an extra; a feature that only a
  gateway or a body needs stays an extra. The Dockerfile installs `EXTRAS` (empty by default) on top
  of the locked core set.

## Extending

- A new tool is a module in `monoagens/engine/tools/` with `TOOL = Handler(...)` and a family from
  `FAMILIES`; the filename must match `name`; never edit the registry.
- A new command is a module in `monoagens/engine/commands/` with `COMMAND = Handler(...)`; never edit
  the dispatcher.
- Permissions come only from roles (`role:<name>` documents / the `roles.yaml` mirror). No handler
  has default permissions. A missing `tools`/`commands` entry denies everything. Tool permission is
  checked only at `monoagens/engine/tools/__init__.py:execute`.
- Semantic memory only through Mem0 (`monoagens/engine/memory/mem0.py`, `mem0ai`). The single write
  port is `MemoryStore` (ADR-0001): canonical writes go through it, reads go through
  `memories_readable`. It is not the pre-v1 store and must not grow a second semantic path —
  derivation stays in Mem0/reflection (ADR-0010). The action ledger stays `ActionLedger`;
  transcripts stay per chat.

## Config and soul

- `soul.yaml` is the only persona source; `soul.yaml` is authoritative and the `soul` document is a
  derived cache (ADR-0046 §4.1). There is no legacy persona file and no `persona` document.
- `config.yaml` (engine section), `soul.yaml`, and `roles.yaml` are the operator's source of truth
  (ADR-0046 §4.1); the `engine`, `soul`, and `role:<name>` documents are caches keyed by content
  hash. A command writes the file first, then refreshes the cache; a file wins every disagreement,
  and a cache miss is rebuilt from the files. Manual edit → restart or `/reload`; `monoagens config
  import` only refreshes the cache. The loader must never create directories.
- Gateway authorization is role data (ADR-0046 §4.2): `roles.<role>.gateways.<gateway>` lives only
  in `roles.yaml`, the highest-priority role is the only policy source, and the gateway owns its
  vocabulary and validation. A missing or malformed block denies before history, memory, and the
  model; `monoagens gateway doctor --fix` writes the seed, never a running gateway.
  `config.yaml` `gateways` stays transport-only (`enabled`, session, backlog).
- Config layout: one host-local `config.yaml` holds host keys (`database`, `workspace`, `roles_file`,
  `soul_file`, `sandbox`, `web_search`, `mcp`, `sync_documents`) plus the `engine` and `gateways`
  sections. One `roles.yaml` holds every role. One `soul.yaml` holds the soul. There are no separate
  `engine.yaml`/`gateway.yaml`/`soul.yaml`-per-section files and no `roles/` directory.
- Secrets only in `.env` (env names referenced from YAML); `config.yaml`, `roles.yaml`, `.env`, and
  any pre-v1 runtime data directory are never committed.
- Storage: SQLAlchemy Core + `psycopg` to Postgres; one `Database` (`monoagens/engine/db.py`) and one
  `MetaData` (`monoagens/engine/schema.py`). No `sqlite3`, `PRAGMA`, FTS5, or state files in stores.
  Schema changes go through the Alembic chain in `migrations/` (`db upgrade`, primary-only);
  `create_all` is for an empty database and tests only. A library cache under the workspace (currently
  Mem0's `mem0_history.db`, ADR-0001) is derived: never read by the engine for truth, safe to lose.
  A gateway session file (`gateways/whatsapp` `session_db`, default `.monoagens/whatsapp.db`) is a
  host-local protocol artifact: one per instance, never shared, never read by the engine (contact
  import is read-only), and losing it costs a re-pair, not data. `SCHEMA_VERSION` mirrors the Alembic
  head. Message history is never there: it lives in Postgres (`messages`, `threads`, `thread_people`).
- Any `pyproject.toml` change ships with a regenerated `uv.lock` in the same commit: the image runs
  `uv sync --locked`, which fails on a stale lock even when only the `dev` extra moved.

## Character data

- Character comes only from `soul.yaml`: `description`, `background`, `persona`, `examples`, and
  `rules`. There is no runtime pattern pack, fact pack, resolver, or character level table
  (ADR-0045). Do not reintroduce `library/patterns`, `library/facts`, or `library/severity`.
- `soul.voice` is the one tuning block: master `enabled`, `sampling`, `affect`, `style`,
  `character`, `humanize`, `profanity`, and `safety`. The master defaults off; `sampling`,
  `profanity`, and `safety` are request-level. `engine.affect`, `engine.style`,
  `engine.character`, `engine.humanize`, and `engine.llm.temperature` do not exist.
- `soul.voice.profanity` is a prompt instruction (`free` or `avoid`), not a numeric toxicity table.
  `soul.voice.safety` is `inherit` or `off`; `off` is accepted only when the selected wire reports a
  request-level safety capability, and core harms stay blocked upstream.
- The guard, sandbox, and approval policy stay in `engine.guard`; a persona never weakens them.
- `library/lexicon` holds detector lists only: negations, stopwords, assistant tells, injection,
  affirmations, laughter, particles, serious registers, research triggers, and privacy level words.
  No prompt examples and no character-level map. Other detector data lives in `library/privacy/`,
  `library/safety/`, `library/style/`, `library/affect/`, and `library/intent/`.
- Stable common knowledge may come from the model prior. Current, specific, local, numeric, disputed,
  or citation-required claims go through `web_search`; short snippets are enriched from the fetched
  page, and evidence enters the prompt only as an ephemeral `ResearchCard`. A local MiniLM support
  check, the 40-case eval, and abstention on missing evidence are part of the contract (ADR-0043).
  Personal facts stay in Mem0; never add a public-fact catalogue or a persistent research table.
- Affect prototypes live in `monoagens/library/affect/<language>.json`: exactly the eight
  `EMOTION_VAD` labels, at least six short casual sentences each, never a name, URL, or phone number.
  `scripts/affect.py --check` gates them.

## Licensing

- The project is Apache-2.0 (`LICENSE`, `pyproject.toml` `license` + `license-files`). Every new
  dependency ships its row in the README credits table (component, license, author, source, use) in
  the same commit; both `README.md` and `README.id.md` carry the same rows.
- Record the license the installed distribution declares. A package with no declared upstream
  license is written as `not declared upstream` with its repository link, never guessed.

## Safety

- The sandbox guard is mandatory: the deterministic layer always runs and the small fast LLM
  classifier only sees what the first layer cannot classify. Sensitivity decides whether an action
  needs approval; the target decides the class: own scope (`scratch/<chat>`, own memory, own DM)
  → `consent` (requester approves), shared or outside (`public/`, other rooms, host config,
  credentials) → `authorization` (approver role). `scope_proof` is computed at request and re-checked
  at execution, so a widened role reopens the approval. `approved=True` (`/approve`) bypasses both layers.
- `administrator` is the protected top role (ADR-0022/0036): it must exist with `tools: "*"`, `memory_admin`, and `approve`; it cannot be deleted or renamed, only its `label` (address) may change, and only through `roles.yaml` + `monoagens config import`. `protected: true` is refused on every other role. `users` holds `per_...` ids, never an address; a body never names a role. Bootstrap with `monoagens people add <gateway> <address> --role administrator`, which links the identity and updates the role document plus its mirror.
- Sensitive actions (`shell`, `sandbox`) and internal changes (`settings`) must pass
  `SafetyPolicy`/`approvals` and `/approve`. Default mode `yolo`; `off` disables approval (document
  the risk). `/approve` never calls the LLM and re-checks role permissions.
- One primary: `engine_lease`. An instance without the lease stays standby and frozen; `/freeze` and
  takeover happen only through the lease/DB, never two hearts.

## Memory and privacy

- Postgres is the memory system of record (`memories`, `memory_events`, `memory_uses`,
  `memory_tombstones`, `memory_index`); Mem0 is the extractor and vector index behind the port, never
  the store of truth. `MemoryStore` (`monoagens/engine/memory/store.py`) is the single write port, and
  reads for prompts go through `memories_readable` only. The state machine is
  `pending|ok|staged|failed|not_worth|superseded`, with `level NULL` meaning "must not be used".
- Cross-thread identity: query only `global` + `person:<sender>` + `thread:<this thread>`; another
  person's private memory is never queried and must not leak into another thread.
- Memory carries a numeric privacy level 0-4 (public, team, personal, sensitive/aib, secret).
  Level 5/credentials are never stored. `owner_id` is the speaker who stated the fact; `subject_id`
  is who it is about and only that subject may veto it. Level maths, veto precedence, and disclosure
  verdicts live in `monoagens/engine/memory/privacy.py` (deterministic, no LLM). Evidence comes from
  `monoagens/engine/memory/evidence.py` (deterministic language pack, Presidio + `xx_ent_wiki_sm`
  NER, and the shared MiniLM centroid layer) and only its policy writes the level;
  `memories.privacy_evidence` and `privacy_policy` persist the record. The NER path verifies the
  model-tree digest, loads without the model vocabulary, and blocks torch/transformers during its
  import; `entity` evidence carries a span and never a level. `monoagens/engine/access/privacy.py` is
  the only agent-facing hook and detects mentions locally; no memory text is sent to an external
  model. ADR-0038's cold privacy stack fits the §5.1 1536 MiB cap with the shared fp32 MiniLM;
  the remaining partial work is the linear semantic head and production calibration.
- Disclosure policy: L0-L2 stay fluid everywhere; L3 in a group and L4 ask for confirmation through
  `approvals`; vetoed entries are never shown and never disclosed. The auditor pass runs only when a
  risky entry exists.

## Language

- Chat-visible strings live only in `monoagens/lang/<code>.yaml`; `soul.language` selects the catalog,
  so adding a language means adding one YAML file with the same `messages:` keys. `/language <code>`
  (administrator-only) writes the soul and its `soul.yaml` mirror.
- Culture is data, not mechanism (ADR-0033). No language literal, country default, transport, or
  vendor name in `monoagens/engine/**`: word lists live in `monoagens/library/<kind>/<lang>.json`,
  `language`, `locale`, and `timezone` are required values with no code default, and a plugin or
  gateway is named only in its own tree and manifest (a template writes `plugins: {}`). A handler
  declares its own `family`/`target`/`approval_class`; no name table decides a class. One clock
  helper `now(tz)`; no `date.today()` or `time.localtime()`. `scripts/neutral.py --check` gates
  this once the ADR's phases land; until then the rule binds every new change.

## Optional integrations and distribution

- **Clean by default.** A `gateway`, a `tool`, and a `plugin` are three different things and none of
  them is another: a gateway is a body (transport plus its own body actions), a tool is a capability
  the engine calls, a plugin is an installable bundle of capability code (ADR-0027, ADR-0028). All
  three are off until an operator installs and enables them; only the spine is always on.
- A plugin lives in `plugins/<name>/plugin.yaml` (`plugin.root`, default
  `<workspace>/plugins`). The manifest is data: name, version, engine range, `entry`, the
  `sha256:` digest of the code tree, `provides` (tools/commands), `requires`, `secrets` (env names
  only), and `credits`. The loader verifies the digest before the one import of `entry`; a mismatch
  leaves no files and no config change. Plugin dependencies install inside the plugin's own `deps/`,
  never in the image's locked set; the manifest carries the credits rows they owe.
- Enabling fetches code by digest (`plugin.fetch: true`, source in `plugin.source`) and
  writes `plugins.<name>.enabled: true` only after verification. With `plugin.fetch: false` the
  operator pre-places verified code; otherwise the enable fails with one message and changes
  nothing. Disable keeps the files; remove deletes code and the config entry.
- Google is a **plugin** (`google`, source tree `plugins/google/`), not a core tool and not an
  extra: there is no `--extra google`, and `monoagens/engine/tools/` holds no Google module. While
  the plugin is absent or disabled the capability is `unavailable` (`monoagens plugins list`,
  `monoagens selftest`) — never fabricated and never a plugin traceback; a role grant without code
  is unavailable, not denied (ADR-0027 §4.3).
- The image is the spine plus the core tools only (ADR-0027): it carries no gateway and no plugin
  dependency, so plugin `requires` never enters the image's locked set.
- WhatsApp is an optional gateway (its own install, not a plugin). Default config never enables it,
  core never imports it eagerly, and the default member role does not receive WhatsApp-only `send`.

## Verify

- `pytest -q` (with `MONOAGENS_TEST_DATABASE_URL` set to the throwaway Postgres+pgvector store),
  `ruff check monoagens scripts tests plugins migrations`, `python -P scripts/types.py --check`
  (the pyright ratchet; `-P` keeps `scripts/` off `sys.path`), `python -m monoagens selftest`,
  `python scripts/adr.py --check`, `python scripts/adr.py --translate-check` (then `--stamp` after
  editing an English ADR), and `python scripts/selfcheck.py --check` (which also enforces the
  ADR-0031 size caps). For gateway/lease changes also smoke `monoagens console --takeover`.
