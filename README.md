# Eidesoul

a persistent soul engine: one identity, one memory, any body

[English](README.md) | [Bahasa Indonesia](README.id.md)

[Architecture](#architecture) · [Quickstart](#quickstart) · [soul.yaml](#soulyaml) ·
[Character library](#character-library) · [Supported gateways](#supported-gateways) ·
[Roles and permissions](#roles-and-permissions) · [Safety](#safety) · [Credits](#credits)

## What it is

Eidesoul is a soul engine. It is not an agent, not an assistant template, and not an
autonomous-agent framework.

A soul engine keeps one identity and one memory and lets them inhabit any embodiment: a terminal, a
chat app, a device, a robot. The engine owns the turn — it reads what arrived, recalls what it
knows, decides, speaks — and nothing else. Everything that carries messages in and out is an
appendage around that turn.

Assistant is one role among companion, advisor, and robot embodiments. The soul declares its active
role and the roles it can take in `soul.yaml`; the same identity, memory, and voice carry across all
of them. Changing role changes what the soul is allowed to do, never who it is.

## Architecture

Three layers, one direction of dependency.

- **Core engine** — the turn loop plus the services it needs: prompt assembly, language catalog,
  semantic memory and privacy, affect and style, the LLM client, and tool execution. Core never
  imports a gateway, a transport library, or a vendor SDK eagerly.
- **Gateway contract** — every transport implements the same small interface: normalize an incoming
  message into one envelope, hand it to the engine, and deliver the reply. Gateways are plugins.
  They are discovered by name, and a deployment enables only the ones it wants.
- **State** — one database holds one soul: transcripts per conversation, semantic memory with a
  privacy level, affect, approvals, and the single-primary lease. The host files
  (`config.yaml`, `roles.yaml`, `soul.yaml`) are seeds and mirrors of what the database holds.

```mermaid
flowchart LR
  GA[Gateway A<br/>chat transport] -->|inbound envelope| CORE
  GB[Gateway B<br/>terminal or local REPL] -->|inbound envelope| CORE
  GC[Gateway C<br/>robot or custom body] -->|inbound envelope| CORE

  subgraph CORE[Core engine]
    TURN[Turn loop<br/>context - recall - decide - reply]
    TURN --> PROMPT[Prompt + language catalog]
    TURN --> MEM[Semantic memory + privacy]
    TURN --> AFFECT[Affect, style, character]
    TURN --> TOOLS[Tools<br/>removable capabilities]
    TURN --> LLM[LLM client]
  end

  MEM <--> STORE[(Postgres + pgvector)]
  PROMPT <--> STORE
  LEASE[Single-primary lease] --- TURN

  CORE -->|reply| GA
  CORE -->|reply| GB
  CORE -->|reply| GC

  OWNED[Gateway-owned tools] -.granted by role.-> TOOLS
```

The lease is how one process stays primary: an instance without it stays frozen and standby, so two
processes never speak with the same mouth. Everything the engine knows is stored, so a restart, a
moved deployment, or a different body resumes the same soul rather than a fresh one.

## Tools are capabilities

Tools are capabilities the soul can be granted, not a fixed body. A body decides which capabilities
exist; a role decides which of them the soul may use in that body.

The six core families are examples, not a taxonomy to fill: `shell`, `read`, `write`, `web_search`,
`sandbox`, and `mcp`. They cover the common cases — run a command, read a file, search the web, call
an MCP server — and nothing more.

A robot, a device, or a custom gateway exposes its own body-specific actions as gateway-owned tool
modules, next to the gateway that gives them meaning. A camera, a motor, a printer, or a home
automation bus belongs to the embodiment, not to the engine. Roles grant those tools explicitly, so
a companion role on a phone never inherits the controls of a robot.

The engine never hardcodes a body and never hardcodes an agent loop. There is no planner to
configure, no task graph to launch, no background swarm: one soul, one turn, and the capabilities
this instant has been handed.

## Quickstart

Requirements: Python 3.12, Postgres 14+ with `pgvector`, and a reachable OpenAI-compatible LLM
endpoint. The container image already pins Python 3.12; running from source on 3.11+ works too.

```bash
# 1. One database for one soul
cp compose.example.yaml compose.yaml          # set a real password
podman compose -f compose.yaml up -d postgres

# 2. Scaffold config.yaml, roles.yaml, soul.yaml, and .env
eidesoul init

# 3. Make it this soul, not the default one
$EDITOR soul.yaml

# 4. Point it at the database and the model
#    .env:
#      EIDESOUL_DATABASE_URL=postgresql+psycopg://eidesoul:CHANGE_ME@127.0.0.1:5432/eidesoul
#      OPENCODE_GO_API_KEY=...

# 5. Create the schema and seed the host files into it
eidesoul db upgrade

# 6. Run the gateways enabled in config.yaml
eidesoul run

# Local REPL against the same soul and database; takes the lease
eidesoul console --takeover
```

`eidesoul run` loads the documents, acquires the primary lease, and starts the enabled gateways. A
second instance stays standby and frozen until it takes over through the lease, so
`eidesoul console --takeover` is the supported way to talk to a running deployment by hand.

`eidesoul db check` prints the schema and content summary; `eidesoul selftest` exercises stores,
tools, and the LLM once without starting a gateway.

`uv sync` installs the capability spine: Postgres+pgvector storage, Mem0 memory, the multilingual
embedder, the affect prototypes, and the guard. Only transport and integrations are extras:

| Extra | Brings | Needed for |
|---|---|---|
| `whatsapp` | Neonize + segno | the WhatsApp gateway |
| `google` | Google API client, google-auth, httpx | the Google Workspace tool |

```bash
uv sync --extra whatsapp --extra google
```

Every extra is removable: nothing in core imports it eagerly, and without it the affected tool or
gateway reports why it is inert.

## soul.yaml

One database is one soul, and `soul.yaml` is where that soul is written down. Every key except
`name` is optional; unknown keys are ignored. After the first `db upgrade` the stored soul is
authoritative and `soul.yaml` is its seed and mirror — edit the file, then run
`eidesoul config import` to fold the change in, or `eidesoul config export` to write the mirror back
out.

```yaml
name: Eidesoul                  # required; falls back to "Eidesoul"
description: ""                 # one line, shown in the persona block
language: id                    # installed catalog: id | en
pronouns: aku
gender: unspecified             # male | female | nonbinary | unspecified
role: assistant                 # the active role
roles: [assistant]              # optional; defaults to [role]
locale: id-ID                   # context locale used by prompts and currents
background: ""                  # free text: where this soul comes from
persona: ""                     # free-form markdown: personality, voice, values
style:                          # written by `eidesoul library resolve`, not by hand
  patterns: []                  # pattern pack ids from eidesoul/library/patterns/
  facts: []                     # fact pack ids from eidesoul/library/facts/
  registers: []                 # optional allow-list of registers; empty = pack default
  max_patterns: 0               # pattern moves per turn, 0..6
  max_facts: 0                  # facts per turn, 0..6
examples: []                    # optional [{user: "...", soul: "..."}]
```

Rules the loader enforces:

- `name` must be non-empty, otherwise the default is used.
- `gender` must be one of `male`, `female`, `nonbinary`, or `unspecified`; anything else falls back
  to `unspecified` with a warning.
- `roles` defaults to `[role]` when absent; if `role` is not listed in `roles`, it is prepended,
  and the rendered persona only lists the other roles when there is more than one.
- Pack ids and registers must match `^[a-z0-9][a-z0-9-]{0,63}$`; an invalid id is an error, not a
  silent skip.
- `max_patterns` and `max_facts` clamp to `0..6`.
- `persona` is rendered in a fixed order: name, description, active role, available roles, locale,
  background, free-form persona, then dialogue examples. Empty sections are omitted.
- `style` is derived: `eidesoul init` seeds it and `eidesoul library resolve --force` rewrites it
  from `description` + `background` + `persona` + `examples`, keeping only packs whose `language`
  matches the soul. A weak match, a tie, or no match selects nothing, and a kind that resolves
  nothing keeps the packs the soul already had.

The active role and the style packs are the two knobs that shape a turn. The active role decides
permissions and working posture; the style packs decide how the soul sounds, and how much of that
character data it may use per turn. A soul with no packs selected has no borrowed colloquialisms and
no facts — just its own `persona` and `background`.

A short example (omit `style` and let the resolver write it):

```yaml
name: Eidesoul
language: id
role: companion
roles: [assistant, companion]
locale: id-ID
background: |
  Grew up around a noisy port and still thinks in harbour stories.
persona: |
  ## Personality
  Direct, dry, warm once trust is earned.
style:
  patterns: [jawatimur]
  facts: [indonesia]
  max_patterns: 3
  max_facts: 2
examples:
  - user: "long day"
    soul: "come here, sit down first"
```

## Character library

Character data is a library the soul opts into, not something the engine ships in its voice.
Selection is top-down: `soul.yaml` names the packs, `eidesoul init` seeds that list for the soul's
language, and `eidesoul library resolve` re-derives it from what the author actually wrote. An empty
`style` means no patterns and no facts.

Packs live under `eidesoul/library/`:

- `patterns/<pack>.json` — conditional moves ("if this, then that") tagged with a register, a
  gender target, and toxicity and adult gates. A pack carries `version`, `model`, `language`, a
  `lexicon` of conversational tokens, and the `patterns` rows.
- `facts/<pack>.json` — short grounded facts, each with at least one source URL. A pack carries
  `version`, `model`, `language`, and the `facts` rows.

Two packs ship as worked examples: `jawatimur` (East Javanese conversational moves) and `indonesia`
(Indonesian facts). Select them, ignore them, or write your own:

```yaml
style:
  patterns: [jawatimur]
  facts: [indonesia]
  registers: [serious, laughter]   # optional: keep only these registers
  max_patterns: 3
  max_facts: 2
```

Selection is deterministic per turn: the engine reads the recent messages, derives the room
register, and picks at most `max_patterns` moves and `max_facts` facts. Keyword hits come first, then
cosine similarity on the shared embedder (`eidesoul/engine/embed.py`). A pack that is missing, unreadable, or named outside
the id pattern contributes nothing and logs a warning instead of failing the turn.

No raw transcripts are shipped. A `say` line in a pattern pack is an approved exemplar, never a
copied private message, and fact rows carry sources rather than conversation.

Edit packs with the scripts, never by hand:

```bash
python scripts/patterns.py --pack jawatimur --check     # schema, gates, duplicates, reachability
python scripts/patterns.py --pack jawatimur --embed     # rebuild the pack vectors
python scripts/fakta.py --pack indonesia --check        # schema and vectors
python scripts/fakta.py --pack indonesia --embed
python scripts/grow.py --pack jawatimur --patterns      # propose new rows from a corpus
```

`--embed` must run after any content change so vectors match the rows. A new pack is a new file pair
in the same directories with a name that matches the id pattern; no code change is required to
select it.

## Supported gateways

This is the whole list; nowhere else in the docs enumerates gateways.

- **CLI** — built in. The default config enables it, and it is how `eidesoul run` and
  `eidesoul console` talk to a soul without any network transport.
- **WhatsApp** — an optional extra. Install it with `uv sync --extra whatsapp` and enable it in the
  `gateways` section; omit both and it does not exist for this deployment. The core never imports
  it, and the default member role never receives its `send` tool.
- **Your own embodiment** — a robot, a voice loop, a device, or a private HTTP endpoint is just
  another gateway. Implement the inbound envelope and the gateway interface, ship the class as an
  entry point under the `eidesoul.gateways` group or enable it by name, and it joins the same soul.

A gateway is a transport, not a brain. It normalizes input, delivers replies, and owns whatever
body-specific tool modules it wants to expose. Gateway-owned tools are granted to roles explicitly,
exactly like core tools.

## Roles and permissions

Permissions come only from roles. Every role lists its own `tools` and `commands`; no handler
carries a default, so a role that omits a section is denied everything in it. One `roles.yaml` holds
all roles, and the stored role is authoritative with the file as its mirror.

```yaml
roles:
  member:
    priority: 10
    users: []
    tools: [read, web_search, sandbox, memory, google]
    commands: [help, ping, reset, whoami, status, tools]
    context: ""
    memory_admin: false
  owner:
    priority: 100
    users: ["<your address>"]
    tools: ["*"]
    commands: ["*"]
    memory_admin: true
```

- `tools` and `commands` accept exact names and `*`. A missing list denies everything.
- Tool permission is checked at one place, when a tool is executed, so a tool that is visible but
  not granted cannot run.
- `approve` is a command permission like any other: only a role that lists it can clear a pending
  action.
- `memory_admin: true` is required to write the soul's global memory.
- Addresses in several roles are unioned; `context` blocks are ordered by priority, then name.
- A workspace scope follows the same grant: a role without `*` sees the shared read-only area and
  its own scratch area, and the sandbox mirrors that with read-only shared mounts and one writable
  scratch mount.

Hand-editing `roles.yaml` is supported: run `eidesoul config import` afterwards so the database sees
it.

## Safety

Two independent mechanisms, both fail-closed.

**Approvals.** Sensitive actions — `shell`, `sandbox`, and internal `settings` changes — pass the
safety policy before they run. `approval.mode` is `yolo` by default (auto-approve, meant for a
trusted single-operator deployment), `strict` parks every non-read-only action, and `off` disables
the check entirely. `off` is a documented risk, not a convenience: nothing stands between the model
and the host. `/approve` resolves a pending action, never calls the LLM, and re-checks the role's
permissions first.

**The sandbox guard.** Every command that reaches the `sandbox` tool passes two layers, in order:

1. *Deterministic.* A fixed rule set rejects host escape and destructive patterns — filesystem
   wipes, device writes, kernel and namespace tools, container and firewall control, fetch-to-shell
   pipes, privilege escalation, service management, and the classic fork bomb. Simple read-only
   commands are allowed on the spot with no model call. Anything else is inconclusive.
2. *Classifier.* Only the inconclusive commands reach a small, fast LLM classifier with a short
   timeout. It answers one question: is this command trying to escape, attack infrastructure,
   exfiltrate secrets, persist, or destroy host data? Unknown counts as suspicious.

The verdict decides who proceeds. A command the deterministic layer blocks is denied for everyone,
including an approver. A pending or suspicious command becomes a pending approval for a role that
holds `approve`, and is denied outright for every other role — a member is never parked in a queue
it cannot clear. `approved=True` from `/approve` bypasses both layers, after the permission
re-check. If the classifier is unavailable, times out, or returns something unparsable, the result
is closed: pending for an approver, denied for everyone else. Commands longer than
`guard.max_command_chars` are treated the same way.

`guard.enabled: false` disables both layers; the sandbox then runs whatever it is handed. Use it
only while debugging a rule, never in a deployment that accepts untrusted input. Logs record the
verdict, the reason, and the conversation — never the command body.

The sandbox itself runs in a disposable container with only the shared and scratch mounts, on a
dedicated internal network behind an egress proxy, with a timeout, a PID limit, memory, and CPU
caps. Private destinations are blocked at the proxy, so a command cannot reach the host or the
local network even when it is allowed to run.

## Optional integrations

```bash
uv sync --extra whatsapp --extra google                 # optional gateway and integration
```

```bash
docker build --build-arg EXTRAS="--extra whatsapp --extra google" .
```

Memory, the embedder, and the affect prototypes are part of the core install; there is no extra to
add for them. On SQLite, Mem0 stays off and `status` reports why.

- **Affect vibes** read the shared embedder against `eidesoul/library/affect/*.json`; with no
  embedder the room mood stays neutral instead of guessing.
- **WhatsApp** (`whatsapp`): the optional gateway. Never enabled by default.
- **Google Workspace** (`google`): an in-process tool for files shared with a service account, with
  per-sender identity linking. Without the extra the tool stays discoverable but inert and returns a
  clear error instead of a traceback.

Secrets belong in `.env` only. `config.yaml`, `roles.yaml`, `soul.yaml`, and `.env` are never
committed.

## Credits

Every component below keeps its own license and attribution. Versions are the ones the lock file
installs; the last column says what Eidesoul uses it for.

| Component | License | Author / org | Source | Used for |
|---|---|---|---|---|
| Python | PSF-2.0 | Python Software Foundation | https://www.python.org/ | runtime |
| SQLAlchemy | MIT | Michael Bayer / SQLAlchemy authors | https://www.sqlalchemy.org/ | storage |
| psycopg | LGPL-3.0-only | Daniele Varrazzo | https://psycopg.org/ | Postgres driver |
| psycopg-pool | LGPL-3.0-only | Daniele Varrazzo | https://www.psycopg.org/ | connection pool |
| pgvector | PostgreSQL | pgvector contributors | https://github.com/pgvector/pgvector | vector column |
| openai-python | Apache-2.0 | OpenAI | https://github.com/openai/openai-python | LLM client |
| MCP Python SDK | MIT | Model Context Protocol authors | https://github.com/modelcontextprotocol/python-sdk | MCP tools |
| PyYAML | MIT | Kirill Simonov / PyYAML authors | https://pyyaml.org/ | config + soul files |
| ddgs | MIT | ddgs authors | https://github.com/deedy5/ddgs | web search |
| Mem0 (`mem0ai`) | Apache-2.0 | Mem0 | https://github.com/mem0ai/mem0 | semantic memory |
| sentence-transformers | Apache-2.0 | UKP Lab / Hugging Face | https://github.com/huggingface/sentence-transformers | embeddings |
| paraphrase-multilingual-MiniLM-L12-v2 | Apache-2.0 | sentence-transformers | https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 | embedder weights |
| PyTorch | Apache-2.0 AND Apache-2.0 WITH LLVM-exception AND BSD-2-Clause AND BSD-3-Clause AND BSL-1.0 AND ISC AND MIT AND Zlib | Meta / PyTorch contributors | https://github.com/pytorch/pytorch | tensor runtime |
| Transformers | Apache-2.0 | Hugging Face | https://github.com/huggingface/transformers | model loading |
| Neonize | not declared upstream | krypton-byte | https://github.com/krypton-byte/neonize | WhatsApp transport |
| segno | BSD-3-Clause | Lars Heuer | https://github.com/heuer/segno | QR pairing |
| google-api-python-client | Apache-2.0 | Google | https://github.com/googleapis/google-api-python-client | Workspace tool |
| google-auth | Apache-2.0 | Google | https://github.com/googleapis/google-auth-library-python | Google auth |
| httpx | BSD-3-Clause | Encode | https://github.com/encode/httpx | HTTP client |
| emotion-engine | MIT | PioneerJeff Labs | https://github.com/pioneerjeff-labs/emotion-engine | vendored affect engine |
| EmoSense-ID classifier | see model card | Aardiiiiy | https://huggingface.co/Aardiiiiy/EmoSense-ID-Indonesian-Emotion-Classifier | calibration reference only |
| pytest | MIT | pytest authors | https://github.com/pytest-dev/pytest | dev |
| pytest-cov | MIT | pytest-cov authors | https://github.com/pytest-dev/pytest-cov | dev |
| ruff | MIT | Astral | https://github.com/astral-sh/ruff | dev |

## License

Eidesoul is released under the [Apache License 2.0](LICENSE). Copyright 2026 Abdul Muqsith.

Third-party components keep their own terms; the table above lists each license, author, and source.
The vendored emotion engine carries its license next to its source as
`eidesoul/vendor/LICENSE.emotion-engine`.
