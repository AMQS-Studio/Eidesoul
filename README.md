<p align="center">
  <img src="assets/logo.png" alt="MonoAgens" width="240">
</p>

<h1 align="center">MonoAgens</h1>

<p align="center">
  <strong>A sovereign, persistent mono-agent harness.</strong><br>
  <em>One continuous identity, native hybrid memory, and deterministic safety across every surface and embodiment.</em>
</p>

<p align="center">
  <a href="docs/v0.1.0/README.md"><img alt="Docs" src="https://img.shields.io/badge/docs-v0.1.0-111111?style=flat-square&labelColor=555555"></a>
  <a href="LICENSE"><img alt="License" src="https://img.shields.io/badge/license-Apache--2.0-2ea043?style=flat-square&labelColor=555555"></a>
  <a href="pyproject.toml"><img alt="Python" src="https://img.shields.io/badge/python-3.14%2B-3776ab?style=flat-square&labelColor=555555"></a>
  <a href="https://www.postgresql.org/"><img alt="PostgreSQL + pgvector" src="https://img.shields.io/badge/storage-PostgreSQL%20%2B%20pgvector-336791?style=flat-square&labelColor=555555"></a>
  <a href="https://github.com/AMQStudio"><img alt="AMQStudio" src="https://img.shields.io/badge/built%20by-AMQStudio-6f42c1?style=flat-square&labelColor=555555"></a>
</p>

<p align="center">
  <a href="#overview">Overview</a> ·
  <a href="#key-capabilities">Capabilities</a> ·
  <a href="#architecture">Architecture</a> ·
  <a href="#quickstart">Quickstart</a> ·
  <a href="#operator-cli">Operator CLI</a> ·
  <a href="#configuration">Configuration</a> ·
  <a href="#security-and-governance">Security</a> ·
  <a href="#credits">Credits</a>
</p>

---

## Overview

Most agent frameworks treat state as a disposable chat session and personality as a system prompt pasted into a context window. When the window fills, history is compacted away; when you switch from a terminal to a messaging app or a voice interface, you talk to a stranger.

**MonoAgens** is built around a different invariant: **one deployment is one continuous self (`Soul`) backed by one canonical PostgreSQL store.**

- **State lives in the store, not the prompt window.** Every turn dynamically assembles its working context from PostgreSQL (`pgvector` semantic similarity + `tsvector` full-text search + relational transcripts), bounded by a strict token budget. Conversations can span months or years without losing factual recall.
- **One person across every body.** Gateways (WhatsApp, Discord, Telegram, voice calls, or physical robotics) are pure transport and actuator pipes. They normalize inbound events into `Percept` envelopes and dispatch outbound `Action` receipts—never owning memory, persona, or authorization.
- **Deterministic privacy and authority.** Privacy classification (`L0–L4`), secret refusal (`L5`), cross-thread isolation, role-based access control (`roles.yaml`), and command guarding execute deterministically in Python before any language model sees the data or runs a tool.

| Dimension | Session Chatbots & Wrappers | Task / Coding Harnesses | **MonoAgens** |
| :--- | :--- | :--- | :--- |
| **Primary Unit of State** | Ephemeral chat thread | Workspace files & task history | **One persistent Soul** in PostgreSQL + `pgvector` |
| **Memory Architecture** | Context window / naive RAG | File tree & compacted transcript | **Native hybrid retrieval** (HNSW vector + GIN FTS + decay + privacy levels) |
| **Identity Across Surfaces** | Isolated per app or channel | Bound to local terminal/IDE | **Unified `per_…` identity** across chat, voice calls, and physical bodies |
| **Emotional & Vocal State** | Static system prompt | None | **Continuous PAD affect engine**, relationship stages, and unified vocal timbre |
| **Authorization & Safety** | All-or-nothing API keys | Manual Y/N prompt per shell call | **RBAC (`roles.yaml`)**, smart conversational confirmation, and isolated container sandbox |

---

## Key Capabilities

### 1. One Continuous Soul (`soul.yaml`)
- **Declarative Identity:** Define name, pronouns, active roles, background, behavioral rules, and few-shot dialogue anchors in `soul.yaml`.
- **Affect & Relationship Dynamics:** Built-in Pleasure-Arousal-Dominance (PAD) emotional state with personality baselines, natural decay between turns, and per-person relationship progression.
- **Humanized Delivery:** Configurable typing cadence, multi-bubble splitting, transitional thoughts/progress updates, and adaptive communication style per interlocutor.
- **Unified Voice & Embodiment Seam:** Vocal identity (`voice.speech`) belongs to the Soul, while STT, TTS, and real-time full-duplex audio adapters live in core—allowing you to mix cloud APIs (ElevenLabs, OpenAI, Deepgram, Gemini Live) or self-hosted/custom voice models (GPT-SoVITS, F5-TTS, Kokoro, Piper, Faster-Whisper) across voice notes, live phone calls, and robots.

### 2. Native Hybrid Memory & Layered Privacy
- **Single System of Record:** Powered by PostgreSQL + `pgvector` (`paraphrase-multilingual-MiniLM-L12-v2` 384-d embeddings with HNSW index and PostgreSQL `tsvector` GIN index).
- **Hybrid Scoring:** Combines dense semantic cosine similarity, lexical full-text match, importance weighting, and exponential recency decay in a single SQL query.
- **Layered Privacy (`L0–L4`) & Subject Veto:**
  - `L0` Public · `L1` Contextual · `L2` Personal · `L3` Sensitive · `L4` Intimate · `L5` Secret (credentials/keys are rejected before storage).
  - Queries are strictly scoped to `global` + `person:<sender>` + `thread:<active_thread>`. Group chats automatically cap disclosure levels, and the subject of a personal memory holds veto power.

### 3. Autonomous Routines, Goals, & Cross-Gateway Tools
- **Core Capabilities:** `read`, `write`, `shell`, `sandbox`, `web_search` (pluggable across DuckDuckGo, SearXNG, Tavily, Brave, Exa, Serper, and Google CSE with automatic fallbacks), `memory`, `send_message`, `send_file`, `thread_history`, and `mcp`.
- **Cross-Room & Cross-Gateway Dispatch:** Ask the agent from the CLI console or a voice call to research a topic and deliver a document to a WhatsApp group or colleague—routed through a single outbound authorization door (`Agent.dispatch()`).
- **Unified Job Engine:** Schedule recurring routines (`cron`), one-shot reminders (`at`), and autonomous multi-step goals (`goal`) with quiet hours, daily run budgets, and automatic lease recovery.

### 4. On-Demand Extensions (Clean by Default)
- **Zero Bloat:** External gateways, optional tools, and plugins stay completely unimported—and their dependencies uninstalled—until explicitly enabled.
- **Lifecycle Management:** Enable, pair, disable, or completely purge gateways and plugins via the `monoagens` CLI. Plugins are verified by SHA-256 manifest digests before loading.

---

## Architecture

```mermaid
flowchart LR
  CLI["Operator Console<br/>(monoagens cli)"] -->|Percept| CORE
  WA["Messaging Gateways<br/>(WhatsApp, Discord, Telegram)"] -->|Percept| CORE
  VOICE["Voice & Telephony<br/>(Voice Notes, SIP/WebRTC Calls)"] -->|Percept / Audio| CORE
  ROBOT["Physical Embodiment<br/>(Robotics / IoT / Sensors)"] -->|Percept / Telemetry| CORE

  subgraph CORE["MonoAgens Core Engine"]
    COORD["Turn Coordinator<br/>(Single-Primary Lease & Checkpoints)"]
    MEM["Hybrid Memory & Privacy<br/>(L0-L4 + Subject Veto)"]
    AFFECT["Soul & Voice Engine<br/>(PAD Affect, Style, Humanize)"]
    TOOLS["Capability & Job Engine<br/>(Core Tools, Routines, Goals, Guard)"]
    PROV["Provider Wire Adapters<br/>(OpenAI, Gemini, Anthropic, STT/TTS)"]

    COORD --> MEM
    COORD --> AFFECT
    COORD --> TOOLS
    COORD --> PROV
  end

  MEM <--> PG[("PostgreSQL + pgvector<br/>(Single System of Record)")]
  COORD <--> PG

  CORE -->|Action / Stream| CLI
  CORE -->|Action / Receipt| WA
  CORE -->|Audio Stream| VOICE
  CORE -->|Actuator Command| ROBOT
```

### Architectural Invariants
1. **Strict Hexagonal Boundary:** `monoagens/engine/` never imports gateway packages or transport SDKs. Every surface communicates through immutable `Percept`, `Address`, `Action`, and `Receipt` contracts.
2. **Single Primary Process:** A PostgreSQL lease (`engine_lease`) ensures only one active instance mutates state or speaks for the Soul at any moment; secondary instances remain in hot standby.
3. **Crash-Safe Turn Durability:** Inbound messages and turn state are persisted atomically. If a process restarts mid-turn, completed non-idempotent actions are never re-executed, and outbound chunks resume cleanly.
4. **Authoritative Host Configuration:** `config.yaml`, `roles.yaml`, `soul.yaml`, and `.env` on disk are the operator's source of truth; database document rows serve strictly as a derived cache.

---

## Quickstart

### Prerequisites
- **Linux / macOS** (POSIX filesystem supporting `0600` file permissions)
- **Python 3.14+** (installed automatically by `uv` during setup if missing)
- **PostgreSQL 14+ with `pgvector`** (provisioned automatically via Podman/Docker container, or bring your own external PostgreSQL DSN)
- **An LLM Provider** (OpenAI, Google Gemini, Anthropic Claude, OpenRouter, DeepSeek, Groq, Ollama, vLLM, or any OpenAI-compatible endpoint)

### 1. One-Command Interactive Install

Run the interactive installer to configure your stack (`native` or `compose`), database container (`podman` or `docker`), LLM provider, web search backend, and global `monoagens` CLI binary:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/AMQStudio/MonoAgens/main/install.sh)
```

Or clone the repository and run locally:

```bash
git clone https://github.com/AMQStudio/MonoAgens.git
cd MonoAgens
bash install.sh
```

Non-interactive / headless flags are also supported:

```bash
bash install.sh --stack native --engine podman --provider openai --language en
bash install.sh --stack compose --engine docker --gateway whatsapp
bash install.sh --database external --database-url "postgresql+psycopg://user:pass@host:5432/monoagens"
```

### 2. Start & Talk to Your Agent

Once installed, `monoagens` is available globally in your `$PATH`:

```bash
# Start the background service (PostgreSQL + MonoAgens Engine)
monoagens start

# Open the lightweight interactive chat console
monoagens cli

# Follow live logs (add --verbose or --debug for deep inspection)
monoagens logs -f
```

---

## Operator CLI

MonoAgens provides a single unified CLI (`monoagens`) across both native and container deployments:

| Command | Description |
| :--- | :--- |
| `monoagens init` | Interactive wizard to scaffold `config.yaml`, `roles.yaml`, `soul.yaml`, and `.env` |
| `monoagens start \| stop \| restart \| status` | Manage the background daemon or container stack |
| `monoagens run [--verbose \| --debug]` | Run the engine in the foreground with configurable log verbosity |
| `monoagens logs [-f] [--verbose \| --debug]` | Stream service logs in real time |
| `monoagens cli` *(alias: `console`)* | Open the built-in interactive terminal chat with the Soul |
| `monoagens setting` | Interactive menu to configure LLM providers, models, web search, and parameters |
| `monoagens reload` | Validate edited YAML files on disk and hot-reload the running engine |
| `monoagens gateway list` | List available and enabled transport gateways |
| `monoagens gateway enable <name>` | Download dependencies on demand, enable the gateway, and seed role policies |
| `monoagens gateway pair <name>` | Run interactive pairing (e.g. WhatsApp QR / pairing code) |
| `monoagens gateway disable <name>` | Disable a running gateway while keeping its package installed |
| `monoagens gateway delete <name>` | Completely purge gateway code, dependencies, session files, and config blocks |
| `monoagens plugins list \| enable \| disable \| delete` | Manage optional tool plugins verified by SHA-256 digest |
| `monoagens tools list \| enable \| disable` | Toggle core tools (`sandbox`, `shell`, `web_search`, `mcp`, etc.) |
| `monoagens people list \| link \| unlink` | Inspect and link gateway addresses to canonical `per_…` identities |
| `monoagens update [--core]` *(alias: `upgrade`)* | Upgrade core engine, enabled gateways, plugins, and run database migrations |
| `monoagens doctor [--fix]` | Run end-to-end health diagnostics on database, embeddings, configs, and permissions |
| `monoagens backup \| restore <file>` | Create or restore a portable snapshot of the PostgreSQL database and host YAML files |
| `monoagens uninstall [--purge]` | Cleanly remove the service and `$PATH` binary (`--purge` also removes data volumes) |

---

## Configuration

A deployment is governed by three clean YAML files on the host plus `.env` for secrets. All templates are generated directly from schema definitions with inline documentation.

### 1. `soul.yaml` — Identity, Voice, & Affect

```yaml
name: MonoAgens
description: "Persistent sovereign agent"
language: en                    # Installed catalog: en | id
locale: en-US
timezone: Asia/Jakarta          # Required IANA timezone
pronouns: I
role: assistant
roles: [assistant, companion]

background: |
  A thoughtful, observant counterpart with long-term memory and a calm presence.

persona: |
  Direct, grounded, and warm without artificial filler. Prioritizes accuracy,
  remembers personal context naturally, and respects boundaries.

voice:
  sampling:
    daily_temperature: 0.9        # Creative/conversational turns
    professional_temperature: 0.3 # Analytical/tool-heavy turns
  affect:
    enabled: true                 # Continuous PAD emotional state & decay
  style:
    enabled: true                 # Per-person linguistic adaptation
  character:
    enabled: false                # Optional character polish pass
  humanize:
    enabled: true                 # Realistic typing cadence & bubble pacing
    cpms: 25.0
    min_delay: 0.5
    max_delay: 5.0
    between_delay: 0.7
    max_bubbles: 3
  profanity: avoid                # avoid | free
  safety: inherit                 # inherit | off (when supported by provider adapter)

examples:
  - user: "Long day at work."
    soul: "Take a breath first. Want to talk through it, or leave work at the door tonight?"

rules:
  - "Never fabricate citations, URLs, or tool execution results."
```

### 2. `config.yaml` — Engine, Providers, Tools, & Gateways

```yaml
engine:
  llm:
    provider: openai              # openai | gemini | anthropic | openrouter | ollama | custom
    model: gpt-5.4-mini           # Operator's choice; never hardcoded by the engine
    base_url: ""                  # Optional custom endpoint URL
    api_key_env: OPENAI_API_KEY
    utility_temperature: 0.2      # Deterministic temperature for internal extraction & routing

  memory:
    enabled: true
    vector_weight: 0.65
    fts_weight: 0.35

  approval:
    mode: default                 # default (ask on writes) | yolo (auto-run safe writes) | strict (ask all)
    natural_confirm: true         # Allow natural conversational confirmation ("Yes, send it")
    confirm_cross_gateway: true   # Require confirmation before messaging another room/gateway
    confirm_tasks: true           # Require confirmation before scheduling recurring tasks

  tools:
    web_search:
      enabled: true
      provider: ddgs              # ddgs | searxng | tavily | brave | exa | serper | google
      fallbacks: []               # Automatic fallback chain if primary provider fails
    sandbox:
      enabled: false              # Isolated container execution behind egress proxy

gateways: {}                      # Populated on demand via `monoagens gateway enable <name>`
```

### 3. `roles.yaml` — Role-Based Access Control

Permissions are strictly opt-in per role. Identities are stored as opaque `per_…` tokens rather than raw phone numbers or usernames:

```yaml
roles:
  member:
    priority: 10
    users: []
    tools: [read, web_search, memory, thread_history]
    commands: [help, ping, whoami, status, link, unlink]
    memory_admin: false
    gateways:
      whatsapp:
        dm: true
        groups: mention           # none | mention | reply | both | all
        reply_to_bot: true

  administrator:
    priority: 100
    users: ["per_xxxxxxxxxxxx"]
    tools: ["*"]
    commands: ["*"]
    memory_admin: true
    gateways:
      whatsapp:
        dm: true
        groups: both
        reply_to_bot: true
```

---

## Security and Governance

MonoAgens enforces defense-in-depth across three independent layers:

1. **Action-Based Approval & Conversational Checkpoints (`approval.mode`):**
   - **`default`:** Read-only inspections (`read`, `web_search`, `thread_history`, `memory` recall) execute immediately; state mutations, file writes, shell/sandbox execution, task creation, and cross-gateway messages pause for confirmation.
   - **`yolo`:** Normal writes and sandbox commands execute automatically; destructive command patterns, settings changes, and cross-gateway deliveries still require confirmation.
   - **`strict`:** Every tool invocation requires explicit confirmation.
   - With `natural_confirm: true`, the agent asks naturally in conversation and verifies cryptographic `scope_proof` and RBAC permissions in Python before executing the approved action.
2. **Two-Stage Command Guard (`SafetyPolicy`):**
   - **Stage 1 (Deterministic):** Blocks destructive host patterns, privilege escalation, namespace/kernel tampering, raw device writes, and fetch-to-shell pipes before any model call.
   - **Stage 2 (Classifier):** Evaluates ambiguous commands for exfiltration or infrastructure risk, failing closed on timeout or uncertainty.
3. **Disposable Container Sandbox:**
   - Untrusted code and shell tools run inside an ephemeral Podman/Docker container with dropped Linux capabilities, read-only rootfs, PID/CPU/memory limits, and a dedicated internal network behind an SSRF-blocking HTTP egress proxy.

---

## Development & Verification

Tests run against a real PostgreSQL + `pgvector` instance (never SQLite mocks):

```bash
# Start an isolated test database container on port 5433
podman run -d --name monoagens-test-pg -p 5433:5432 \
  -e POSTGRES_DB=monoagens_test -e POSTGRES_USER=monoagens -e POSTGRES_PASSWORD=monoagens \
  docker.io/pgvector/pgvector:pg17

export MONOAGENS_TEST_DATABASE_URL='postgresql+psycopg://monoagens:monoagens@127.0.0.1:5433/monoagens_test'

# Run the verification suite
pytest -q
ruff check monoagens scripts tests plugins migrations
python -P scripts/types.py --check
python -P scripts/adr.py --check
```

---

## Credits

Every third-party component retains its original license and attribution:

| Component | License | Author / Organization | Purpose |
| :--- | :--- | :--- | :--- |
| [Python](https://www.python.org/) | PSF-2.0 | Python Software Foundation | Core runtime |
| [PostgreSQL](https://www.postgresql.org/) & [pgvector](https://github.com/pgvector/pgvector) | PostgreSQL | PostgreSQL Global Development Group & pgvector contributors | Canonical relational & vector store |
| [SQLAlchemy](https://www.sqlalchemy.org/) & [Alembic](https://alembic.sqlalchemy.org/) | MIT | Michael Bayer & contributors | Database toolkit & schema migrations |
| [psycopg](https://www.psycopg.org/) | LGPL-3.0-only | Daniele Varrazzo | Async/sync PostgreSQL driver & pool |
| [httpx](https://www.python-httpx.org/) | BSD-3-Clause | Encode | Async HTTP client for LLM & search providers |
| [openai-python](https://github.com/openai/openai-python) | Apache-2.0 | OpenAI | OpenAI wire client |
| [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) | MIT | Model Context Protocol contributors | Model Context Protocol integration |
| [sentence-transformers](https://github.com/huggingface/sentence-transformers) | Apache-2.0 | UKP Lab / Hugging Face | Local multilingual embeddings (`MiniLM-L12-v2`) |
| [Presidio](https://github.com/microsoft/presidio) & [spaCy](https://spacy.io/) | MIT | Microsoft & Explosion | Local multilingual PII/NER analysis |
| [PyYAML](https://pyyaml.org/) | MIT | Kirill Simonov & contributors | Configuration & Soul parsing |
| [ddgs](https://github.com/deedy5/ddgs) | MIT | deedy5 | Default zero-key web search backend |
| [Neonize](https://github.com/krypton-byte/neonize) & [segno](https://github.com/heuer/segno) | BSD-3-Clause / Upstream | krypton-byte & Lars Heuer | Optional WhatsApp gateway & QR pairing |
| [emotion-engine](https://github.com/pioneerjeff-labs/emotion-engine) | MIT | PioneerJeff Labs | Vendored PAD affect baseline math |
| [Ruff](https://github.com/astral-sh/ruff) & [pytest](https://docs.pytest.org/) | MIT | Astral & pytest contributors | Linting, formatting, and test suite |

---

## License

MonoAgens is released under the [Apache License 2.0](LICENSE). Copyright © 2026 [Abdul Muqsith](https://github.com/AMQStudio) (AMQStudio).
