# ADR-0027: Distribution and Web-First Configuration — One Core Image, Default-Off Capabilities

- **Status:** accepted
- **Date:** 2026-11-03
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Context:** follows ADR-0001 (surfaces and bodies), ADR-0015 (capability manifest), ADR-0016 (schema), ADR-0018 (gateway contract), ADR-0022 (`administrator`), ADR-0028 (plugins)
- **Supersedes:** —
- **Related:** ADR-0009 (document home), ADR-0012 (lease), ADR-0019 (observability), ADR-0028 (the plugin unit that this ADR distributes)
- **Implementation:** partial — present: the local image build (core-only, `EXTRAS` empty), the plugin/gateway separation, `eidesoul selftest` image contents, the installer's panel-or-CLI finish gate; remaining: `panel/` (operator-owned; `web.enabled: false` until it lands) and publishing the image to GHCR (no registry credentials here)
- **Location note:** this ADR is cross-cutting (it binds every surface and every deployment), so it sits at the root of the ADR home. The Indonesian copy is `docs/v0.1.0/adr/id/0027-distribution-web-config.md`.
- **Amendments:** 2026-11-03 — first version. 2026-11-03 — the voice layer (emotion, humanize, character) is stated as on by default and switchable off per part; clean by default still covers only gateway, tool, and plugin. 2026-11-03 — the fetched-code keys in §5 are `plugin.root`/`plugin.fetch`/`plugin.source`, following the plugin rename (ADR-0028 §5); the default install root is `<workspace>/plugins`.

---

## 1. Context

| Fact | Evidence |
|---|---|
| The image is built but not published | `Dockerfile:1,28-35` builds from `python:3.12-slim` with `ARG EXTRAS=""`; no `.github/workflows` exists |
| Optional capability is compiled into the image today | `pyproject.toml` extras `whatsapp`, `google`; `Dockerfile:28` documents `--extra google` |
| Some capabilities live in the core as if they were first class | `eidesoul/engine/tools/google.py`, `chat_history.py` |
| Every tool and command is already deny-by-default | roles carry `tools`/`commands`; a missing entry denies (repo rule) |
| There is no operator surface for configuration | configuration is `eidesoul init`, `eidesoul config import`, `roles.yaml`/`soul.yaml` mirrors, and `.env` |
| The spine is not optional | Postgres+pgvector, Mem0, the shared embedder, affect prototypes, the guard, and the lease (`AGENTS.md` §Capability spine) |

The owner's decision: distribution happens through `ghcr.io`, the web surface is the primary way to
initialise and configure an install, plugins are added from there, and **nothing is on by default
except the spine** — a plugin's code is fetched only when it is enabled. The web surface itself is
meant to be on by default once it is finished, with a switch to turn it off.

## 2. Decision drivers

| Driver | Consequence |
|---|---|
| Clean by default | Gateway, tool, and plugin are each off until enabled — a fresh install runs the spine and nothing that can act on the world |
| Distinct categories | A gateway is a body, a tool is a capability, a plugin is a bundle; no category absorbs another |
| Code follows enablement | The image stays small and auditable; enabled plugins are fetched, digest-pinned |
| One configuration truth | The web surface writes the same documents as the CLI; no second store |
| Operator-first surface | Initialise, inspect, enable, grant, and revoke without editing YAML by hand |
| Reversible | Every enable has a disable; turning the web surface off never breaks a running engine |
| Honest status | An unpublished image is not presented as a release; unfinished surfaces are not enabled |

## 3. Options considered

| Option | Summary | Value |
|---|---|---|
| **A. One core image, fetch on enable** | Publish a single spine-only image; enabling a plugin downloads it | **Chosen** |
| **B. Fat image per bundle** | `core`, `core-google`, `core-whatsapp`, … | Rejected as default: build/verify matrix grows and "nothing by default" becomes untrue |
| **C. pip extras only** | Keep `--extra google` installs and configure by YAML | Rejected: enablement is a build-time decision, not an operator action |
| **D. No image, source install** | Document `uv sync` and a systemd unit | Rejected: reproducibility and upgrades get worse, not better |
| **E. Engine as an HTTP server** | Fold configuration and API into the engine process | Rejected: the engine stays a library plus gateways; the panel is a separate process (§4.2) |

## 4. Decision

### 4.1 The published artifact

One image per release, published to `ghcr.io/<owner>/<repo>`, built from the existing `Dockerfile`
with `EXTRAS` empty. While nothing is released, the image is built locally only and tags are `git`
based — no release-looking tag is invented (§Versioning in `AGENTS.md`).

- The image contains the **spine and the core tools only**: `read`, `write`, `shell`, `sandbox`,
  `web_search`, `mcp`, `memory`, `recipe`, `settings`, `grep`, `fetch`, `info`, plus the core commands.
- The image contains **no gateway, no provider extra, and no plugin**; those are enabled later
  (§4.3) and their Python dependencies are not part of the locked core set.
- The image declares its contents in a build label and prints them in `eidesoul selftest`, so what a
  running container can do is checkable, not assumed.

### 4.2 The web surface is the primary configuration path

The web panel is the operator surface for init and configuration: first run (database, workspace,
provider and model, first administrator), inspection (`/status` data, ledger, automations, memory
truth), and plugin enablement (§4.3). The CLI keeps parity for headless installs
(`eidesoul init`, `eidesoul config import`, `eidesoul selftest`, `eidesoul models`).

- The panel is a **separate process** at the repository root (`panel/`), never a module inside
  `eidesoul/engine/`, and the engine never imports it. `AGENTS.md` §Shape is amended accordingly: the
  ban on an HTTP gateway stays for the **engine**, while the panel is an operator application that
  reads and writes the same documents.
- Configurations written from the panel take the same path as a manual edit plus
  `eidesoul config import`: documents are the source of truth, YAML files are seed + mirror, secrets
  stay in `.env` (the panel writes env **names**, never secret values into YAML).
- Every change is recorded in `config_changes` with actor and diff, and the panel shows the effective
  value next to its source (`document`, `yaml mirror`, `env`, `default`).
- The panel owns no policy: permissions, approvals, and the guard live in the engine; the panel cannot
  grant itself anything, and a plugin enable is an operator action (§4.3).

### 4.3 Nothing is enabled by default except the spine

**Clean by default is the shared philosophy, not a shared category.** A `gateway`, a `tool`, and a
`plugin` stay three different things: a gateway is a body (transport), a tool is a capability the
engine calls, a plugin is an installable bundle (ADR-0028). None of them is another, and each one is
installed and enabled in its own right — enabling a gateway is never an "install a plugin", and a
plugin never turns into a core tool.

| Layer | Category | Default | How it is turned on |
|---|---|---|---|
| Spine (Postgres+pgvector, Mem0, embedder, affect prototypes, guard, lease) | not a category — required | **on** | not optional |
| Voice layer (emotion, humanize, character) | engine | **on by default** | `init` asks once, with keep-on as the default answer; each part is one switch, and turning one off is an operator action |
| Core tools (`read`, `write`, `shell`, `sandbox`, `web_search`, `mcp`, `memory`, `recipe`, `skill`, `settings`) | tool | code in the image, **no grant** | role `tools` mapping (deny by default) |
| Extra tool pack (capability outside the image) | tool | **off, not installed** | install the tool pack, then grant through roles |
| Gateway (WhatsApp, future robot/harness/editor) | gateway | **off, not installed** | install the gateway, then enable it — its code is fetched |
| Plugin (e.g. `google`) | plugin | **off, not installed** | enable it, then its code is fetched (ADR-0028) |
| Web panel | operator surface | **on once finished** (§4.4) | — |
| Automations | per person | off | authored explicitly (ADR-0014) |

- The **voice layer** is not a capability category: emotion, humanize, and character are on by default,
  and each is one switch. Turning one off changes the voice, never the identity, the memory, or the
  permissions.
- "Enabled" means two separate things and both are required: the code is present **and** a role grants
  the capability. Enabling without a grant leaves the capability inert; a grant without code is
  reported as unavailable, never as denied.
- Enabling a plugin **fetches its code** (wheel or OCI layer) pinned by digest, verifies the digest,
  and only then writes the config document. With no network the enable fails with one explicit message
  and changes nothing — there is no bundled fallback and no partial install.
- Disabling switches the capability off and keeps the files (fast, reversible); removing deletes them
  and their state. Both are operator actions and both land in the ledger.

### 4.4 The web surface is on by default once finished

When the panel is finished it ships enabled: `web.enabled: true`, bound to loopback by default with an
operator-chosen port, no anonymous access (an administrator token created at first run). Turning it off
is one switch (`web.enabled: false`) and never breaks a running engine — the engine keeps serving its
gateways; only the panel stops. While the panel is unfinished the switch exists but defaults to `false`
and the panel is not packaged in the image.

A bind beyond loopback requires an explicit host, an explicit listener statement in the log, and a
secret in `.env`; the panel never enables remote access on its own.

### 4.5 What the panel never does

- No second source of truth: it writes documents through the same import path, never a parallel store.
- No engine logic: no prompt assembly, no memory decision, no guard bypass; it calls the engine's own
  entry points.
- No chat-visible string in code: the panel text comes from the same language catalogs (§Language in
  `AGENTS.md`).
- No silent capability: nothing is enabled, installed, or upgraded without an explicit operator action
  recorded in `config_changes` and the ledger.

### 4.6 Defaults, not examples

A file we ship must be usable as it stands; a file that only demonstrates is deleted. The default
lives in code, `eidesoul init` is the only writer of the host files, and the panel writes through the
same path (§4.2).

- `eidesoul/admin/templates.py` is the single source: `config.yaml`, `roles.yaml`, `soul.yaml`, and
  `.env` are generated from it, with `.env` and `roles.yaml` written `0600` and never overwritten
  unless `--force`. `.env` carries **names with empty values** (`EIDESOL_DATABASE_URL=`,
  `POSTGRES_PASSWORD=`) and no placeholder secret, so a half-filled file cannot be mistaken for a
  working one.
- No `*.example` file is shipped. `config.example.yaml` and `.env.example` duplicated `templates.py` and
  were removed: a key added to the template now reaches every install that re-runs `init`, instead of
  drifting away from a hand-kept copy.
- Optional keys resolve to code defaults when absent (`pool_size=5`, `pool_timeout=30`,
  `timeout_seconds=120`, `pids_limit=256`), so the file carries what the operator decided and nothing
  else.
- Values that are the operator's choice are asked, never defaulted: the provider, the model id, and the
  database URL. `init --provider` is required, `init --model` stays optional, and no model id is pinned
  in code.
- `compose.yaml` is a real default too: it runs as it stands, takes `POSTGRES_PASSWORD` from `.env`,
  builds the core image (`EXTRAS: ""`), and keeps the sandbox proxy behind a `sandbox` profile on a
  self-contained egress network instead of demanding an external one.

## 5. Configuration

| Key (host section) | Default | Meaning |
|---|---|---|
| `web.enabled` | `true` once finished, `false` while unfinished | the panel process |
| `web.host` | `127.0.0.1` | bind address; anything else needs `web.remote_ack: true` and an `.env` secret |
| `web.port` | operator-chosen at first run | panel listener |
| `plugin.root` | `<workspace>/plugins` | where fetched gateway/plugin code lands (§ADR-0028) |
| `plugin.fetch` | `true` | allow fetching code on enable; `false` = offline installs only |
| `image.tag` | unset while unreleased | the running image tag, reported by `eidesoul selftest` |

Secrets live in `.env` only (`WEB_TOKEN`, provider keys); YAML carries names, never values.

## 6. Metrics and gates

| Metric | Initial target |
|---|---|
| Capabilities active on a fresh install (outside the spine and permission grants) | 0 |
| Image contents checkable | `eidesoul selftest` lists gateway/plugin presence; label matches |
| Panel ↔ CLI divergence on the same change | 0 (one import path) |
| Fetched plugin without digest verification | 0 (fails closed) |
| Changes without a `config_changes` row | 0 |
| Secrets written into YAML by the panel | 0 (test) |
| Enable on a machine without network | explicit failure, no state change (test) |
| Shipped files that are examples rather than defaults | 0 |
| Shipped files carrying a placeholder secret (`CHANGE_ME`) | 0 |

## 7. Acceptance criteria

- [ ] One image builds locally with `EXTRAS` empty and runs `eidesoul selftest`; the release tag is published only when a release exists.
- [ ] The image contains no gateway/plugin dependency: `pip list` in the container shows no `neonize`, no `google-api-python-client`.
- [ ] A fresh install starts with the spine only: `/status` reports every gateway and plugin as absent/off, and no capability is granted.
- [ ] Enabling a plugin fetches code by digest; a wrong digest aborts with no config change and no files installed.
- [ ] Enabling a plugin without a role grant leaves it inert (unavailable, not denied) and `/tools` says why.
- [ ] Disabling a plugin keeps its files and stops the capability; removing deletes both code and state, and both actions appear in the ledger.
- [ ] The panel writes a configuration change that `eidesoul config import` would have written, with a `config_changes` row; a manual YAML edit shows up as drift until imported.
- [ ] `web.enabled: false` stops the panel and leaves the engine and its gateways running.
- [ ] No secret value appears in `config.yaml`, `roles.yaml`, or `soul.yaml` after a panel session (test).
- [ ] `AGENTS.md` §Shape states that the engine is not a server while the panel is a separate operator app; `docs/**` joins the tracked document list.
- [ ] No `*.example` file is tracked, and `templates.files()` writes exactly `config.yaml`, `roles.yaml`, `soul.yaml`, and `.env`.
- [ ] `compose.yaml` starts Postgres with only `POSTGRES_PASSWORD` set in `.env`, and no `CHANGE_ME` literal ships anywhere.
- [ ] A key added to `eidesoul/admin/templates.py` reaches the file `init` writes, with no second copy to update.

## 8. Risks and mitigations

| Risk | Mitigation |
|---|---|
| "Nothing by default" drifts into extras creeping back into the image | §6 metric: container dependency list is checked; plugins carry their own dependencies |
| Fetch-on-enable needs network at an awkward time | `plugin.fetch: false` allows an operator to pre-place verified artifacts; the failure is explicit |
| The panel becomes a second, parallel configuration path | One import path (§4.2), `config_changes` rows, and a drift view in the panel |
| A panel exposed beyond loopback becomes an attack surface | Loopback default, explicit `remote_ack`, `.env` secret, no anonymous mode |
| Web-first tempts engine logic into the panel | §4.5 plus the repo rule that `eidesoul/engine/` never imports the panel |
| On-by-default panel surprises headless operators | One switch, documented as the first line of the runbook; the CLI never depends on the panel |
| Unpublished image treated as a release | Dev version rule (§Versioning); no release-looking tag while nothing is released |

## 9. Interaction with other ADRs

- **ADR-0028**: defines the plugin unit, its manifest, and its lifecycle; this ADR owns where it is
  distributed from and how it is turned on.
- **ADR-0015**: the capability manifest stays the model-facing truth; the panel only edits the
  configuration behind it.
- **ADR-0018**: a gateway keeps its contract; enabling it does not change its interface.
- **ADR-0016**: `config_changes` and the documents path stay the schema owner's business.
- **ADR-0022**: the first administrator is created at first run; only `label` is editable afterwards.
- **ADR-0009**: `docs/**` is a tracked document surface, so this ADR and the panel's documentation
  live under version control.

## 10. Code references

- `Dockerfile:1,28-35` — current image build with `ARG EXTRAS=""`.
- `pyproject.toml` `[project.optional-dependencies]` — `whatsapp`, `google`, `dev` extras.
- `plugins/google/` — the Google capability after the move; `eidesoul/gateways/whatsapp/tools/chat_history.py` — the archive tool after the move.
- `eidesoul/__main__.py` — `init`, `models`, `config import`, `selftest` (the CLI parity surface).
- `eidesoul/admin/templates.py:1,151,253` — the single default source for the four host files.
- `eidesoul/admin/init.py:26-46` — the only writer (`0600` for `.env`/`roles.yaml`, `--force` to overwrite).
- `compose.yaml`, `.dockerignore` — the real deployment default, kept out of the image.
- `eidesoul/engine/config.py`, `eidesoul/engine/stores/documents.py` — the configuration path the panel reuses.
- `eidesoul/engine/schema.py` — `config_changes` ledger.
- `panel/` — new tree at the repository root (proposal, not created yet).

---

*This ADR lives at `docs/v0.1.0/adr/0027-distribution-web-config.md`.*