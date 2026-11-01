# LOG

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
