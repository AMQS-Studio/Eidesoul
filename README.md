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
  <a href="#what-it-solves-in-everyday-life">Everyday Use Cases</a> ·
  <a href="#how-it-compares-to-other-agent-harnesses">Comparison</a> ·
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

<table>
<tr>
  <td><b>One Soul, Any Archetype</b></td>
  <td>Not locked into a generic "assistant" mold. Shape <code>soul.yaml</code> into a <b>Strategic Advisor</b>, <b>Periodic Sysadmin</b>, <b>Senior Developer</b>, <b>Companion</b>, or <b>Embodied Robot</b>—with continuous PAD emotional state, humanized bubble pacing, and one unified voice (Cloud or Local custom TTS/STT) across every surface.</td>
</tr>
<tr>
  <td><b>Hybrid Semantic + Lexical Memory</b></td>
  <td>No session compaction amnesia or frozen flat-file caps. Every turn dynamically queries <b>PostgreSQL + <code>pgvector</code></b> (384-d multilingual HNSW vectors + <code>tsvector</code> GIN full-text search + importance & recency decay) to assemble an exact working set under a strict token budget.</td>
</tr>
<tr>
  <td><b>Multi-User RBAC & Layered Privacy</b></td>
  <td>Built to live safely among many people across DMs and group chats. <code>roles.yaml</code> enforces strict per-role tool, command, and workspace permissions over opaque <code>per_…</code> identities, while deterministic <code>L0–L4</code> privacy caps, <code>L5</code> secret refusal, and <b>subject veto</b> run before any LLM sees the prompt.</td>
</tr>
<tr>
  <td><b>Condition-Aware Autonomy</b></td>
  <td>More than a blind cron job. Scheduled routines (<code>cron</code>, <code>at</code>) and autonomous <code>goal</code> loops evaluate context first (<i>"check server health; only message me if disk &gt; 85% or errors appear"</i>), honoring <code>quiet_hours</code>, daily budgets, and kill switches.</td>
</tr>
<tr>
  <td><b>Crash-Safe & Single-Primary</b></td>
  <td>A PostgreSQL <code>engine_lease</code> guarantees two instances never speak with two mouths. Atomic turn enqueueing and step-level checkpoints ensure a mid-turn restart resumes cleanly without repeating non-idempotent side effects or double-sending replies.</td>
</tr>
<tr>
  <td><b>Unpoisonable Rules & Cited Research</b></td>
  <td>Behavioral rules and permissions live in authoritative host YAML files that untrusted chat or web inputs can never overwrite. Live facts enter only through pluggable multi-provider <code>web_search</code> as cited <code>ResearchCard</code> evidence (<code>[R1]</code>).</td>
</tr>
</table>

---

## What It Solves in Everyday Life

### 1. One Agent for You, Your Team, and Your Groups—Without Giving Everyone Root Access
- **The Problem:** Most autonomous agents assume a single operator on a single system—anyone allowed to message the agent effectively inherits its full toolset, shell access, and shared memory pool. Putting them in a family group or office channel is a security and privacy hazard.
- **In MonoAgens:** First-class **Role-Based Access Control (`roles.yaml`)** and **layered privacy (`L0–L4`)** let one Soul safely interact with dozens of people across WhatsApp, Discord, Telegram, and CLI at the same time:
  - **You (`administrator`):** Full access to `shell`, `sandbox`, `write`, `settings`, and private `L3–L4` memories in DM.
  - **Teammates or Family (`member` / custom roles):** Can chat with the same Soul in group rooms or DMs using safe tools (`web_search`, `read` on shared folders) without ever being able to run your shell tools, read your scratch files, or leak your private `L3–L4` memories into a group chat.

### 2. Switching Surfaces Mid-Workflow (Terminal $\rightarrow$ WhatsApp $\rightarrow$ Voice Call $\rightarrow$ Robot)
- **The Problem:** Conventional harnesses bind state to a local session transcript. If you brainstorm a deployment plan in your terminal CLI, leave your desk, and message your bot on WhatsApp, you are talking to a separate session that lacks context.
- **In MonoAgens:** Your CLI console, WhatsApp account, Discord handle, and phone number map to one canonical identity (`per_…`). Start an investigation in `monoagens cli` on your laptop, walk out the door, and send a WhatsApp voice note: *"Send the summary PDF we just drafted to the engineering group."* The same Soul recalls the turn, asks naturally in chat (*"Ready to send `infra-summary.pdf` to Engineering Group—should I proceed?"*), and dispatches it when you reply *"Yes, send it."*

### 3. True Semantic + Lexical Recall Without Session Compaction Amnesia
- **The Problem:** Session-centric memory engines rely on curated profile notes (capped by character limits and frozen at session start) plus keyword search (`FTS5`) and LLM session summarization. When older turns are compacted away or you phrase a question using different words than the original log, recall fails.
- **In MonoAgens:** The PostgreSQL archive is the permanent record and the prompt window is only this turn's working set. Every turn runs hybrid **384-d semantic vector (`pgvector` HNSW)** + **full-text (`tsvector` GIN)** retrieval. Ask *"Help me plan dinner and flights for mom's visit"* and it semantically retrieves a note from six months ago about her shellfish allergy and morning flight preference—even though those exact keywords weren't in your question.

### 4. Condition-Aware Autonomous Routines (*"Check First, Only Notify Me If Needed"*)
- **The Problem:** Standard scheduled jobs act like dumb crontabs—firing a prompt at a fixed interval and spamming your chat even when nothing happened.
- **In MonoAgens:** Autonomous routines (`cron`, `at`) and multi-step `goal` loops are **condition- and context-aware**. You can instruct it: *"Every 30 minutes, check server disk usage and staging container logs; if everything is healthy stay quiet, and only message me on WhatsApp if disk exceeds 85% or a crash loop appears."* It inspects the state, respects `quiet_hours` and daily budgets, and messages you only when the condition is met.

### 5. Moldable Into Any Archetype (Companion, Advisor, Developer, Periodic Sysadmin, or Robot)
- **The Problem:** Most harnesses lock you into a single hardcoded persona—either a narrow coding utility or a generic, robotic "helpful AI assistant."
- **In MonoAgens:** The engine is completely persona-agnostic. Through `soul.yaml` and `roles.yaml`, you can configure and tune it into **anything you want**—a **Strategic Advisor**, a **Periodic Sysadmin / DevOps Monitor**, a **Senior Developer**, a **Personal Companion**, or an **Embodied Robot**—or equip it with multiple roles (`roles: [assistant, advisor, sysadmin, companion]`) backed by a continuous PAD emotional engine, humanized bubble pacing, and your choice of Cloud or Local custom voice models (ElevenLabs, GPT-SoVITS, F5-TTS, Kokoro, Piper).

---

## How It Compares to Other Agent Harnesses

Modern AI agent harnesses generally fall into three architectural categories:

1. **Developer & Repository Harnesses** — Engineered for single-developer software engineering inside a local git repository and terminal session.
2. **Single-Operator Autonomous Session Agents** — Persistent personal agents built around session transcripts, keyword search (`FTS5`) with LLM session summarization, and curated workspace notes. While effective for one owner on one system, their memory is session-bound rather than native hybrid vector-relational, and they lack multi-user role isolation—meaning anyone allowed to message the agent shares the same tool authority and memory pool.
3. **MonoAgens (Sovereign Persistent Mono-Agent Harness)** — Engineered from the ground up for a **single continuous Soul** that can safely serve **one operator or many people simultaneously across apps, voice calls, and physical bodies**, backed by native semantic + lexical PostgreSQL storage, strict RBAC (`roles.yaml`), and row-level privacy (`L0–L4`).

| Capability / Architecture | Developer & Repo Harnesses | Single-Operator Session Agents | **MonoAgens** |
| :--- | :--- | :--- | :--- |
| **Role & Archetype Flexibility** | Fixed coding & repo utility | General personal assistant / skill runner | **Fully customizable archetype** (*Advisor, Companion, Periodic Sysadmin, Developer, Robot*) via `soul.yaml` |
| **Memory Engine** | Repo context files + compacted session window | Curated profile notes (frozen per session) + `FTS5` keyword search & LLM session summaries | **Native Hybrid Vector + Lexical:** PostgreSQL `pgvector` (384-d HNSW) + `tsvector` (GIN) + decay & importance rebuilt every turn |
| **Session vs. Continuous State** | Ephemeral terminal session with compaction | Session transcripts with `/reset` or `/compress` compaction | **Sessionless working set:** Store is the canonical memory; every turn dynamically assembles context from PostgreSQL |
| **Multi-User & Role System (`RBAC`)** | Single local developer (none) | **Single-owner trust model:** Shared agent means shared tools, files, and credentials across callers | **Strict `roles.yaml` RBAC:** Per-role tools, commands, workspace mounts (`shared`/`scratch`), and gateway policies |
| **Group Chat & Multi-Party Privacy** | N/A (local terminal) | Shared global memory notes; risk of cross-room context bleed | **Row-level `L0–L4` privacy**, `L5` secret refusal, room caps, and **subject veto** enforced in SQL/Python before LLM calls |
| **Rule & Memory Integrity** | Static project instructions | Agent-editable workspace notes susceptible to prompt poisoning | **Host-authoritative rules (`soul.yaml`)**, quarantined memory corroboration, and cited `ResearchCard` web evidence |
| **Cross-Surface Identity** | Terminal / IDE only | Per-channel session or single owner profile | **Unified `per_…` identity** linking CLI, WhatsApp, Discord, Voice Calls, and Robotics |
| **Autonomous Scheduling** | Manual interactive loop | Cron triggers running scheduled prompts | **Condition-aware routines (`cron`/`at`) & `goal` loops** (*"check first, notify only if X"*), `quiet_hours`, & budgets |
| **Affect, Pacing, & Voice** | None | Static text persona prompt | **Continuous PAD affect engine**, per-person style adaptation, bubble pacing, and unified Cloud/Local voice seam |
| **Turn Durability & High Availability** | Process-bound terminal | Local process + session log | **PostgreSQL single-primary lease (`engine_lease`)** + step-level ACID checkpoints (zero duplicate side effects on crash) |

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
