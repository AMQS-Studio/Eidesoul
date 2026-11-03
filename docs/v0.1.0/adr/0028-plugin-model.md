# ADR-0028: Plugin Model — One Installable Unit for Fetched Capability

- **Status:** accepted
- **Date:** 2026-11-03
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Context:** follows ADR-0027 (distribution and default-off capability), ADR-0001 (surfaces), ADR-0015 (capability manifest), ADR-0007 (permission classes), ADR-0022 (`administrator`)
- **Supersedes:** —
- **Related:** ADR-0018 (gateway contract), ADR-0019 (ledger), ADR-0021 (tool contracts), ADR-0026 (a core tool that stays core)
- **Implementation:** implemented — evidence: `eidesoul/engine/components.py` (manifest, tree digest, fetch-by-digest, one entry import, lifecycle/state), `plugins/google/` (manifest + credits + tool), `google` extra removed, `eidesoul components` CLI, `tests/test_plugins.py`, `tests/test_google_plugin.py`; gate result: `pytest -q` 798 passed, `uv lock --check` clean
- **Location note:** this ADR is cross-cutting (it defines a distribution unit used by every surface), so it sits at the root of the ADR home. The Indonesian copy is `docs/v0.1.0/adr/id/0028-plugin-model.md`.
- **Amendments:** 2026-11-03 — first version.

---

## 1. Context

| Fact | Evidence |
|---|---|
| `google` is a core tool today | `eidesoul/engine/tools/google.py` sits beside `read.py`, `shell.py`, … |
| `google` is also a build-time extra | `pyproject.toml` `google = ["google-api-python-client…", "google-auth…", "httpx…"]` |
| A tool is a module implementing one contract | repo rule: a module in `eidesoul/engine/tools/` with `TOOL = Handler(...)`, filename matching `name` |
| Permissions never come from a handler | repo rule: only roles grant `tools`/`commands`; a missing entry denies |
| Gateways are already separable packages | `eidesoul/gateways/<name>/` with `tools|commands` of their own, transport-only (ADR-0018) |
| Data surfaces exist for optional content | `eidesoul/library/**` packs, selected by `soul.style` |

The owner's decision: capability that is not the spine is a **plugin**, and Google is the first one —
not a core tool, and not a build-time extra. A plugin's code is fetched when the plugin is enabled, so
it is a packaging unit the engine must know how to load without importing its code at import time.

## 2. Decision drivers

| Driver | Consequence |
|---|---|
| One packaging unit | An operator enables "google", not "an extra plus a config key plus a tool" |
| Nothing enabled by default | A plugin is off, unfetched, and unpermissioned until an operator acts |
| Core stays clean | The engine holds a loader and a manifest contract, never plugin code or plugin vocabulary |
| Permissions stay role-owned | A plugin cannot grant itself a capability; install/enable is an operator action |
| Reversible | disable keeps files, remove deletes; both are logged |
| Honest dependencies | Plugin dependencies live with the plugin, so the image's locked set does not grow |

## 3. Options considered

| Option | Summary | Value |
|---|---|---|
| **A. Plugin as an installable unit, fetched on enable** | Manifest + code in `components/<name>`; engine loads through the existing registries | **Chosen** |
| **B. Keep packaging Python extras, add only config switches** | `--extra google` stays, plugin means "enabled" | Rejected: enablement remains a build decision and the image carries every optional dependency |
| **C. Plugin as a separate service** | Every plugin runs out of process behind an API | Rejected for now: latency, failure modes, and a new transport for something that is a library call |
| **D. Fold plugins into gateways** | Treat `google` as a gateway | Rejected: a gateway is a body (transport), Google is capability code; the two vocabularies must stay separate (ADR-0001 §4.3) |
| **E. Fold plugins into tools** | Treat `google` as one more core tool module | Rejected: a plugin is fetched and installed by lifecycle; a core tool is shipped in the image and only needs a grant |

## 4. Decision

### 4.1 What a plugin is (and what it is not)

**A gateway, a tool, and a plugin are three different things.** The engine keeps three separate
vocabularies (ADR-0001 §4.3/§4.5), and this ADR adds nothing to the first two: a gateway stays a body
(transport plus its own body actions), a tool stays a capability the engine calls, and a plugin is a
**packaging and lifecycle unit**. None of them is another: the WhatsApp gateway is installed *as a
gateway*, `read`/`shell` are tools, and `google` is a plugin. What they share is one philosophy —
**clean by default**: nothing is connected, granted, or installed until an operator says so.

| Term | Meaning | Example |
|---|---|---|
| **Spine** | Always-on core of the engine | Postgres+pgvector, Mem0, embedder, guard, lease |
| **Core tool** | Capability shipped in the image, granted by roles | `read`, `shell`, `memory`, `settings` |
| **Gateway** | A body: transport plus its own body actions (ADR-0018) | WhatsApp; a future robot |
| **Plugin** | **An installable unit fetched at enable time** whose content is capability code | `google` (mail, calendar) |
| **Extra** | A build-time Python dependency of the image (used only for what the spine needs) | `dev` |

A plugin is never a new concept in prompt assembly, permission checks, or memory: what it provides
enters the engine through the **same** registries as a core module, and a gateway it ships is
registered as a gateway — never as "a plugin gateway".

### 4.2 Manifest

`components/<name>/plugin.yaml`:

```
name: google                  # ^[a-z0-9][a-z0-9-]{0,63}$ (same rule as library packs)
version: 1.0.0                # semver of the plugin
engine: ">=0.1,<0.2"          # compatible engine range; a mismatch refuses to load
entry: plugin.py:register     # the one function the loader calls
provides:
  tools: [mail, calendar]
  commands: [mail_digest]
  gateways: []
requires:                     # fetched into the plugin's own folder, never into the image
  - google-api-python-client>=2.150,<3
secrets:                      # env names only, values stay in .env
  - GOOGLE_CLIENT_ID
  - GOOGLE_CLIENT_SECRET
credits:                      # the README credits row this plugin owes (license, author, source, use)
  license: Apache-2.0
  author: Google LLC
  source: https://github.com/googleapis/google-api-python-client
  use: Gmail and Calendar access
```

- The manifest is data, not code: it is read before anything is imported, so an incompatible or
  incomplete plugin is refused without executing it.
- `provides` names are validated against the loader's own rules (`name` matches the module's `name`,
  no collision with a core tool or a gateway) — a plugin cannot silently shadow a core capability.

### 4.3 Lifecycle

`available → installing → installed → enabled ⇄ disabled → removed`

| Step | Actor | Effect |
|---|---|---|
| `installing` | operator | fetch by digest, verify, unpack under `components/<name>`, fetch `requires` into `components/<name>/deps` |
| `installed` | operator | code present, nothing granted, nothing imported |
| `enabled` | operator | the loader imports `entry`, registers what `provides` names, writes the config document; the role must still grant each capability |
| `disabled` | operator | capability off; files and state stay; fast to reverse |
| `removed` | operator | code and plugin state deleted; the ledger keeps the history |

- Every step is an operator action, lands in `config_changes` and in the action ledger, and is refused
  for a non-operator (`authorization` class, ADR-0007).
- Install is the only step that needs the network, and it fails closed: no digest match, no install —
  no partial unpack, no fallback copy, no "installed but untrusted" state.
- No auto-update and no background fetch: an update is an operator action that installs a new digest.

### 4.4 Loading, isolation, dependencies

- A single engine module (`eidesoul/engine/components.py`, new) owns manifests, digest verification,
  the fetch step, and the one import of `entry`. Nothing else in the engine imports plugin code.
- The loader hands `provides` to the existing registries: tools as `TOOL = Handler(...)` modules,
  commands as `COMMAND = Handler(...)` modules, gateways through the gateway registry (ADR-0018). The
  loader never special-cases a plugin name and never grows plugin vocabulary.
- Plugin dependencies are installed **inside the plugin folder** and imported from there; the image's
  locked dependency set is unchanged, so `uv sync --locked` keeps working and the credits table stays
  honest.
- A plugin that raises while registering is disabled with one warning and does not block the engine.
- Core never imports a plugin at module import time; a disabled or removed plugin is invisible at boot.

### 4.5 Google is the first plugin

- `eidesoul/engine/tools/google.py` moves out of the core and becomes the `google` plugin providing
  mail and calendar tools (and any digest command it needs).
- The `google` **extra** is removed from `pyproject.toml` (the same commit regenerates `uv.lock`); its
  README credits rows move into the plugin manifest's `credits`.
- `AGENTS.md` §Optional integrations is rewritten: Google is not an extra and not a core tool; it is a
  plugin, fetched when enabled, inert when absent (the current "returns a clear `error:`" behavior
  becomes the loader's uniform "unavailable" answer).
- A soul that asks for mail without the plugin enabled gets an honest refusal from the model, phrased
  from the manifest's availability fact — never a fabricated result.

### 4.6 Trust and permissions

- A plugin cannot grant itself anything: permissions come only from roles (`tools`/`commands`), and a
  plugin's own manifest cannot write a role.
- Install/enable/disable/remove are `authorization`-class actions: only a role with the capability may
  perform them, and the web panel is one place to do it (ADR-0027 §4.2).
- Code is trusted by digest and by origin (the operator's configured source); an unsigned plugin is
  allowed only from a source the operator named explicitly, and the manifest never claims a license it
  does not carry.
- Plugin-provided tools run through the same guard, approval, and output contracts as core tools
  (ADR-0007, ADR-0021) — no exemption path exists.

## 5. Configuration

| Key | Default | Meaning |
|---|---|---|
| `components.root` | `<workspace>/components` | where fetched plugin code and their dependencies live |
| `components.fetch` | `true` | fetch code during install; `false` = operator pre-places artifacts |
| `components.source` | unset | the operator-named source (registry/URL) installs are fetched from |
| `plugins.<name>.enabled` | `false` | whether the loader registers the plugin at boot |
| `plugins.<name>.digest` | unset | the pinned digest the installed copy must match |

Secrets are `.env` names only; a plugin that needs a value it cannot find is `unavailable`, not broken.

## 6. Metrics and gates

| Metric | Initial target |
|---|---|
| Plugin code imported by `eidesoul/engine` at module import time | 0 |
| Plugin dependencies inside the image's locked set | 0 |
| Installs without a verified digest | 0 (fails closed) |
| Caps not granted by a role but reachable through a plugin | 0 |
| Plugin-provided tools bypassing guard/approval/output contracts | 0 |
| Plugins shipped with a version pinned in code instead of the manifest | 0 |
| Credits rows missing for a fetched plugin's dependency | 0 |

## 7. Acceptance criteria

- [ ] Installing a plugin writes nothing until its digest verifies; a wrong digest leaves no files and no config change.
- [ ] A plugin's tools are invisible in the capability manifest until the plugin is enabled **and** a role grants them.
- [ ] Enabling a plugin with an unsatisfied `secrets` entry reports `unavailable` with the missing env name, without crashing the engine or logging the value.
- [ ] A plugin that shadows a core tool name is refused at load with the collision named.
- [ ] A plugin raising during registration is disabled with one warning and the engine keeps running.
- [ ] `google` is no longer importable from `eidesoul/engine/tools/`; the `google` extra is gone from `pyproject.toml` and `uv.lock` matches.
- [ ] With the `google` plugin absent, a mail request produces an honest model-authored refusal, never fabricated data and never a plugin traceback.
- [ ] Disable keeps files and stops the capability; remove deletes files and state; both leave ledger rows.
- [ ] A non-operator cannot install, enable, disable, or remove a plugin (guard test).
- [ ] `eidesoul selftest` lists each plugin with `absent | installed | enabled`.

## 8. Risks and mitigations

| Risk | Mitigation |
|---|---|
| A plugin becomes a way to smuggle in an unguarded tool | §4.6: same guard, approval, and output contracts; collision check at load |
| Dependency drift between plugin and engine | Manifest `engine` range checked before import; dependencies live with the plugin |
| Fetch-on-enable breaks air-gapped installs | `components.fetch: false` plus operator-placed artifacts; explicit failure otherwise |
| Plugin vocabulary leaks into the core | §4.4: one loader module, registries unchanged, no plugin name in engine logic (AST-checkable) |
| `google` removal breaks an existing install | Pre-release, so the cutover is clean: no shim and no alias (repo rule); the runbook says to install the plugin |
| A plugin claims a license it does not carry | Manifest `credits` are validated against the installed distribution at load; missing license is reported, never guessed |

## 9. Interaction with other ADRs

- **ADR-0027**: owns the image, the fetch-on-enable policy, and the panel action that enables a plugin.
- **ADR-0015**: the capability manifest reports plugin tools like any other capability, including
  `unavailable` when the plugin is absent.
- **ADR-0007**: install/enable are `authorization`-class; plugin tools inherit the same approval rules.
- **ADR-0018**: a plugin-provided gateway obeys the same gateway contract as a first-party one.
- **ADR-0021** / **ADR-0024**: plugin tools follow the same input/output contracts.
- **ADR-0016**: no new table is required; plugin state lives in documents plus the existing ledger.

## 10. Code references

- `eidesoul/engine/tools/google.py` — the tool that becomes a plugin.
- `pyproject.toml` `[project.optional-dependencies] google` — the extra to remove (with `uv.lock`).
- `eidesoul/engine/tools/__init__.py` — the registry the loader feeds; `execute` stays the only permission check.
- `eidesoul/engine/commands/__init__.py` — the command registry.
- `eidesoul/gateways/` — the gateway packages the loader may expose.
- `eidesoul/library/**` — the pack-id rule (`^[a-z0-9][a-z0-9-]{0,63}$`) the plugin name reuses.
- `eidesoul/engine/components.py` — new module: manifest, digest, fetch, one import (proposal).

---

*This ADR lives at `docs/v0.1.0/adr/0028-plugin-model.md`.*