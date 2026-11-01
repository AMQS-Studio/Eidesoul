# LOG

## 2026-11-01 — neutral core: no gateway or provider vocabulary in `eidesoul/engine`

**Message facts** — `ChatMessage.sender_jid` is `sender_address`, and a new `sender_kind` (`"person"`
when the transport can mention that address, `""` otherwise) carries mentionability. The gateway
computes both (`whatsapp/ingress.py`, `whatsapp/history_sync.py`) and the archive persists the kind in
the message `data` blob next to media, quote, and the visibility flags; the richer-content upsert now
strips `FACT_KEYS` before deciding a row is empty, so a fact alone never overwrites media or a quote.
`_human_rows` labels the address column `sender_address`, which the style and affect seeds read.

**Canonical identities** — `permissions.normalize` is `strip().casefold()` and nothing else: the core
no longer parses phone digits or JID domains. WhatsApp's digit rule moved verbatim to the new
`eidesoul/gateways/whatsapp/identity.py` (`normalize_jid`), used by ingress, directory, history sync,
and media. Roles now match `Inbound.person_id` (falling back to `sender_address`), so `roles.yaml`
keeps its bare-digit owner entries; a gateway that leaves `person_id` empty must list full addresses.
Deletion compares canonical addresses (`mark_deleted(by_address)`, `WorkingMemory`/`HistoryStore`)
and `ingress.revoke_target(ev, gateway)` returns the same canonical address the live path uses, LID
mapped to PN. Mention rendering is `sender_kind == "person"`.

**Chat shape** — `CommandContext.is_dm` (from `Inbound.is_dm`, `True` when there is no envelope)
replaces the `@g.us` suffix test in `/whoami` and `/status`; `ablate.decision_shift` takes `is_dm`
and `scripts/ablate.py` computes it at the call site. Core no longer writes `chats.kind`
(`reset`, `set_chat_name`): only the WhatsApp directory labels a chat, and the column keeps its
`'dm'` default. The long-text cache moved from `workspace/whatsapp/` to `workspace/media/` and the
duplicate WhatsApp `text_path` is gone; old cache files are abandoned and rewritten on demand.
`chat_history` and the prompt scaffolding say "chat name or id" instead of "group/contact name or
JID".

**Provider is the operator's** — `LLMConfig` defaults for `provider`, `base_url`, and `api_key_env`
are blank, `_llm_config` raises `ConfigError` when `engine.llm.provider` is missing or when a
provider without a default key has no `api_key_env`, and the scaffold follows: `eidesoul init
--provider <name>` is required, `templates.ENGINE["llm"]` ships blanks, and `.env` names only the
chosen provider's key variable (`env_file`). Both README quickstarts show the flag.

**Verification** — `pytest -q` 489 passed; `selftest` passes every check except `llm`, which the
provider refused with `429 GoUsageLimitError: Go usage limit exceeded` (external quota, the request
reached `opencode.ai`); `console` shows `/whoami` as `dm "Terminal" (cli:local)` on surface `cli` and
`/status` with the same model and archive counts; `init --provider groq` writes `GROQ_API_KEY=` first
in `.env` and `init` without the flag exits with the argparse "required" error.

## 2026-11-01 — SonarCloud: dead keyword argument and three backtracking regexes

**Bug (S930)** — `scripts/replay.py`, `scripts/grow.py`, and `scripts/research.py` passed
`reasoning_effort="minimal"` to `LLM.complete`, which has no such parameter, so those judge and
summarise calls raised `TypeError` inside best-effort handlers and silently returned nothing. The
argument is gone: effort is operator config (`engine.llm.reasoning_effort`) and no internal call
forces a value.

**Super-linear regex (S8786)** — `character.QUOTED` became `unwrap_quotes` (read the leading quote
run, then the closing run that ends at the last non-space character) and `reply.INLINE_BUBBLE`
became `inline_breaks` (replace one `space-run ---run space-run` at a time, left to right). Both are
linear on an unclosed quote or a long space run; a 600k-case fuzz against the old patterns
reproduces every result exactly. `scripts/xlsx.ATTR` uses a possessive word run
(`(\w\w*+)="([^"]*)"`) so the same scan no longer backtracks.

**Scanner** — `sonar-project.properties` pointed at the phantom `sanka-agent` key; it now carries
`sonar.organization=inimuqsith` and `sonar.projectKey=inimuqsith_SankaAgent`.

**Verification** — `pytest -q` 491 passed, including the new
`test_wrapped_quotes_are_unwrapped_without_backtracking` and
`test_inline_break_scan_keeps_the_marker_rules`.

## 2026-11-01 — v2: providers, guard package, capability spine, affect prototypes, soul resolver

**Providers** — new `eidesoul/providers/` package holding only client/auth/default-model facts for
the OpenAI-compatible endpoints (`opencode`, `openai`, `google`, `openrouter`, `groq`, `deepseek`,
`ollama`, `custom`). `LLMConfig` gained `provider` as its first field, `load_config` resolves the
name into `base_url`/`api_key_env`/`model` before building the config (explicit values still win,
`ConfigError` on an unknown name, `custom` needs a `base_url`), and `api_key()` accepts an empty
`OLLAMA_API_KEY` for a local endpoint. `config.example.yaml` and the `init` template gained the
`provider` key; Gemini is reached through its OpenAI-compatible endpoint.

**Guard package** — `eidesoul/engine/guard.py`, `safety.py`, and `sandbox_guard.py` moved verbatim
into `eidesoul/engine/guard/` as `patterns.py`, `policy.py`, and `sandbox.py`, and the package
`__init__` re-exports the same public symbols, so `from . import guard` call sites in the agent,
ambient, llm, privacy, and affect modules did not change. No shim: the three old modules are gone and
`agent.py`, `tools/__init__.py`, and the two guard test modules import from the package.

**One embedder** — `eidesoul/engine/embed.py` owns the MiniLM model, the 384 dimensions, and the
cosine helper; patterns, facts, affect prototypes, the admin resolver, and Mem0 all share it, so the
two private `_default_encoder` copies and three private cosine implementations are gone. Encoding
runs without progress bars.

**Affect prototypes** — the EmoSense classifier is replaced by multilingual prototype sentences in
`eidesoul/library/affect/{id,en}.json` (`anger, anticipation, disgust, fear, joy, sadness, surprise,
trust`, six casual sentences each). `stores/vibe.py` now loads the bank, scores a message by the max
cosine per label, and turns neutral input into `None` through a calibrated floor and margin
(`0.73`/`0.063`, measured on the fixture sentences: neutral `0.54`, accepted `>= 0.92`).
`VibeConfig` lost `model`, `max_length`, and `neutral_floor`; `scripts/affect.py --check/--stats`
gates the data. The `vibe` extra and the torch/transformers dependency are gone.

**Capability spine** — `mem0ai` and `sentence-transformers` are core dependencies (plus a direct
`torch` so the `pytorch-cpu` index keeps applying; without it the lock pulled the CUDA wheels).
`MemoryConfig.backend`, `MemoryConfig.embedder`, and `EmbedderConfig` are deleted: memory is Mem0 or
nothing, the embedder is the shared MiniLM, and `build_memory` only skips SQLite. The Dockerfile's
`EXTRAS` defaults to empty and `compose.example.yaml` asks for `whatsapp`+`google`. `uv.lock`
regenerated (125 packages, no CUDA).

**Soul and resolver** — `DEFAULT_SOUL_YAML` is identity only (`roles`, `style`, and the `examples`
comment are gone); `load_soul` defaults `roles` to `(role,)` and the persona only lists other roles
when there is more than one. New `eidesoul/admin/library.py` derives the style top-down:
`pack_profiles`, `packs_for(language)`, `suggest(text, packs, use_embedding=...)` (cosine tier with a
floor and margin, then a keyword tier, never a forced pick), `seed_style` for `init`, and
`resolve_soul` which rewrites `style:` while preserving every other key. `eidesoul library resolve
[--explain] [--force]` writes `soul.yaml` and prints `next: eidesoul config import`.

**License and credits** — Apache-2.0 `LICENSE` with `Copyright 2026 Abdul Muqsith`, PEP 639
`license`/`license-files` metadata (setuptools floor raised to 77), an author, and a repository URL.
The README credits list is now one table per language (component, license, author, source, use) and
covers every declared dependency; values come from installed distribution metadata, and packages
without a declared license say so.

**Verification** — `pytest -q` 464 passed; `uv lock --check` clean; `scripts/affect.py --check`
prints `labels=8 ok`; the prototype fixtures classify to `sadness`/`anger`/`joy`/`fear`/`trust` with
`besok rapat jam sembilan` staying neutral; `eidesoul init` writes `style` with `jawatimur` and
`indonesia` and no `roles`/`examples`; `eidesoul library resolve --explain --force` reports
`indonesia facts ... tokens: surabaya` and keeps `jawatimur`; a second call prints `style already
set`; `selftest` OK and `STATUS` present.

## 2026-11-01 — v1: rebrand, soul.yaml, character library, sandbox guard

**Brand** — the project is Eidesoul everywhere: package `eidesoul`, CLI `eidesoul`, default
workspace `.eidesoul/workspace`, lock file `.eidesoul.lock`, LLM session names `eidesoul-*`, the
GitHub repository renamed to `Eidesoul`, and the remote points at
`https://github.com/inimuqsith/Eidesoul.git`. The external SonarCloud project key stays
`sanka-agent` so the existing quality-gate history is preserved.

**Clean cutover** — deleted the compatibility layer instead of carrying it: the config migrator,
the memory_legacy migrator and table, the SQLite importer, the missing-column schema upgrade
(`SCHEMA_VERSION` is now 1), the legacy key/path rejection, and `infer_language`. Existing
databases are not migrated; a v1 deployment starts from a fresh database or an explicit manual
export.

**soul.yaml** — replaced `SOUL.md` with one host file per database. `Soul` now carries `name`,
`description`, `language`, `pronouns`, `gender`, `role`, `roles`, `locale`, `background`,
`persona`, `style`, and `examples`; the loader validates genders, prepends the active role,
validates pack ids, clamps the per-turn counts, and renders the persona in a fixed order. The
`soul` document is authoritative and `soul.yaml` is seed plus mirror; the `persona` document is
gone. `config.yaml` gained the `soul_file` host key and lost its `soul` section.

**Character library** — `eidesoul/data/` became `eidesoul/library/`: the pattern pack is
`library/patterns/jawatimur.json` (+ `_vectors.json`) with `version`, `model`, `language`,
`lexicon`, and `patterns`, and the fact pack is `library/facts/indonesia.json` (+ `_vectors.json`).
Nothing is loaded unless `soul.style` selects a pack, so a fresh install carries no character data.
The register lexicon moved out of the engine into the pack, `select_patterns` filters by an optional
register allow-list, and every `say` line carrying brand text was replaced by a generic line.
`scripts/patterns.py`, `scripts/fakta.py`, and `scripts/grow.py` take `--pack` and keep every write
inside the library.

**Sandbox guard** — `eidesoul/engine/sandbox_guard.py` runs before any sandbox command reaches the
container. Layer 1 is deterministic: host-escape and destructive patterns deny, over-long or
sensitive commands ask for approval, plain read-only commands allow, everything else falls through.
Layer 2 is one small fast classifier call with a timeout; an unusable answer, a timeout, and a
missing model all fail closed. A non-approver role is denied instead of being parked in a pending
approval, `approved=True` (`/approve`) skips both layers, and `engine.guard.enabled: false` allows
everything (documented risk). Only the tool name, chat id, verdict, and reason are logged, never the
command body.

**Optional extras** — `google-api-python-client`, `google-auth`, and `httpx` left the core
dependencies; Google is now the `google` extra with lazy imports, so the tool stays discoverable and
returns `error: Google extra is not installed; run 'uv sync --extra google'.` without it. WhatsApp
stays an optional `whatsapp` extra, is absent from the default and example config, and the default
member role no longer receives the WhatsApp-only `send` tool. The Dockerfile takes `EXTRAS` as a
build argument.

**Prompts and voice** — the engine keeps generic behavior: `VOICE_STYLE` and `CONTINUITY_STYLE` were
removed, `DAILY_STYLE` now asks for the room's register without forcing slang, `PRO_STYLE` closes
with the soul's name, the decision prompt is built from the soul's name/gender/role(s), the
character polish and downgrade prompts take the soul's name, the avatar prompt depends on
`soul.locale`, and the "still <name>" honesty line uses `config.soul.name`. Model-facing payload
markers (deleted, view-once, undecryptable, attachments) are English, and the affect character id is
`soul`.

**Docs** — `README.md` is the English technology-first document, `README.id.md` its Indonesian
translation with explicit anchors, `AGENTS.md` states the English default and the removability
rules, `TASK.md` is the v1 checklist, and this entry replaces the earlier history.

**Verification** — `pytest -q` 447 passed; `python -m eidesoul selftest` OK; `eidesoul init` writes
`soul.yaml` and no `SOUL.md`; `db upgrade` + `config import` + `config export` round-trip the soul
document into `soul.yaml`; `/language en` writes the document and its mirror; `scripts/patterns.py
--check` and `scripts/fakta.py --check` pass on the shipped packs; `uv lock --check` passes.
