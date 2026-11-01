# TASK

Eidesoul v1 checklist. Every item is done when the listed surface exists and works end to end.

## Core

- [x] One Python process: `eidesoul run|console|selftest|backfill`, no HTTP gateway, no bridge.
- [x] `eidesoul/engine/` is gateway-free: no `eidesoul.gateways` or `neonize` import in core.
- [x] One `Database` + one `MetaData`; `SCHEMA_VERSION = 1`; no legacy tables, no SQLite import,
      no split-config migration path.
- [x] One primary enforced by `engine_lease`; `/freeze` and takeover go through the lease only.

## soul.yaml

- [x] `soul.yaml` is the only persona/host soul file; `soul_file` is a host key in `config.yaml`.
- [x] Schema: `name`, `description`, `language`, `pronouns`, `gender`, `role`, `roles`, `locale`,
      `background`, `persona`, `style.{patterns,facts,registers,max_patterns,max_facts}`, `examples`.
- [x] `load_soul` validates genders, prepends the active role, validates pack ids, clamps per-turn
      counts to 0..6, and renders the persona in one fixed order.
- [x] The `soul` DB document is authoritative; `soul.yaml` is seed plus mirror. No legacy persona
      file, no `persona` document.
- [x] `eidesoul init` scaffolds `config.yaml`, `roles.yaml`, `soul.yaml`, `.env`.

## Character library

- [x] Packs under `eidesoul/library/patterns` and `eidesoul/library/facts`, with
      `version`, `model`, `language` (+ `lexicon` for patterns).
- [x] `soul.style` selects the packs; no pack selected means no character data and no facts.
- [x] `scripts/patterns.py --pack`, `scripts/fakta.py --pack`, `scripts/grow.py --pack` maintain
      packs; `--check` and `--embed` pass on the shipped packs.
- [x] No brand text and no raw transcripts inside a pack.

## Sandbox guard

- [x] Deterministic layer: host-escape/destructive patterns deny, long or sensitive commands ask
      for approval, plainly read-only commands allow, unclassified commands fall through.
- [x] LLM layer: small fast classifier with a timeout; unusable answers, timeouts, and a missing
      model all fail closed.
- [x] Non-approver roles are denied, never parked in a pending approval; `approved=True` skips both
      layers; `engine.guard.enabled: false` allows everything (documented risk).

## Optional features

- [x] Extras: `memory`, `vibe`, `whatsapp`, `google`, `dev`; `uv.lock` regenerated.
- [x] Google is a lazy optional extra: the tool stays discoverable and returns a clear `error:`
      without the extra.
- [x] WhatsApp is an optional gateway extra: default config never enables it, core never imports it,
      and the default member role has no WhatsApp-only `send`.
- [x] Docker image takes `EXTRAS` as a build argument instead of hardcoded extras.

## Docs

- [x] `README.md` is the English technology-first document; `README.id.md` is the Indonesian
      translation with explicit section anchors.
- [x] `AGENTS.md` states the English default, the allowed non-English data surfaces, and the
      removability rules.
- [x] `pytest -q` and `python -m eidesoul selftest` pass.
