# ADR-0005: Unified Job Engine and Extension Architecture — Workflows, Tasks, Plugins, and Provider Adapters

- **Status:** accepted
- **Date:** 2026-11-05
- **Deciders:** Muqsith (owner)
- **Context:** Re-baselined for v0.1.0; unifies automations, durable tasks, recipes, gateway base classes, plugins, and provider wire adapters (pre-rebaseline 0014, 0028, 0044, 0047, 0049, 0053, 0054).
- **Supersedes:** pre-rebaseline 0014, 0028, 0044, 0047, 0054
- **Related:** ADR-0001 (sovereign harness), ADR-0003 (lease fencing), ADR-0004 (core tools), ADR-0006 (access control)
- **Implementation:** partial — automation worker and provider wire adapters are implemented; `propose` wiring fix, unified task execution, `BaseGateway`, and local plugin modes are scheduled in Phase P0/P1/P2.

---

## 1. Context

Background execution and third-party extensibility suffered from four structural issues:
1. **Queue Sprawl & Production Wiring Bugs (`BI-02, ST-16`):**
   - The codebase was accumulating four separate queue engines (`turns`, `recipes`, `automations`, and
     a proposed `tasks` table), each duplicating claim, lease, reaper, retry, and step-recording logic.
   - `recipes.py` executed tool steps directly without recording to `turn_steps`, replaying side effects
     on crash.
   - In production (`supervisor.py:277` and `cognition.py:1023`), the efficiency ladder called
     `self._propose(self._agent, manifest, thread)` positionally, whereas `AutomationStore.propose`
     required `thread_id` as a keyword-only argument (`*`), causing a guaranteed `TypeError` at runtime
     (masked in unit tests by a 3-argument mock lambda).
2. **Gateway Boilerplate, Misplaced Console/Harness Gateways, & Eager Bundling:**
   - The local operator console (`cli`) was modeled as a transport gateway (`monoagens/gateways/cli/`),
     forcing `config.yaml` and `roles.yaml` to carry artificial `gateways.cli` blocks even though the
     console is a core harness interface (`monoagens console`), while an unused experimental
     `monoagens/gateways/harness/` package lingered in the repository.
   - Adding a gateway (such as WhatsApp, Discord, or Telegram) required touching 8–9 files and hardcoded
     a WhatsApp-centric `{"dm": ..., "groups": ..., "reply_to_bot": ...}` policy dictionary in
     `admin/templates.py`.
   - Gateways and plugins lacked a unified on-demand lifecycle (`enable` to fetch/install on demand,
     `disable` to turn off while keeping files, `pair`/`unpair` for transport sessions, and `delete` to
     purge code and dependencies from disk).
   - `access_refusal` was left up to each gateway to call manually.
   - Gateways could not declare dynamic transport options (such as how to render `thinking` blocks, tool
     steps, or interim progress updates) without modifying core config loaders.
3. **Plugin Development Friction & Path Security (`BI-18`):**
   - `plugin.yaml` required 10 mandatory fields, and `PluginLoader` enforced SHA-256 `tree_digest`
     verification on every load, forcing developers to recompute hashes and edit both `plugin.yaml` and
     `config.yaml` after every single line edit.
   - `monoagens plugins install` required a `.tar.gz` archive in `plugin.source` and rejected local
     directories, while providing no CLI command to write or refresh digests.
   - `tree_digest` excluded `deps/` (`IGNORED_NAMES`), yet `_entry_module` executed
     `sys.path.insert(0, str(deps))`, allowing unverified code in `deps/` to hijack core Python imports.
   - `PluginLoader.remove` called `shutil.rmtree(self.root / name)` without verifying that the target
     resolved inside the workspace directory.
4. **Provider Wire Gaps:** `monoagens/providers/` provided native adapters for OpenAI and Gemini
   (`generateContent`), but `anthropic.py` was an 18-line wrapper around the OpenAI-compatibility
   endpoint that lacked support for file/audio parts, prompt caching, and strict tool schemas.

## 2. Decision drivers

1. **One Unified Job Engine:** Automations, durable tasks, and recipes share one claim, lease, reaper,
   retry, and step-recording implementation under `monoagens/engine/jobs/`.
2. **Safe, Budgeted Durable Tasks:** Work exceeding a single turn timeout runs outside the per-thread
   lock with strict owner authority, memory scoping, and budgets.
3. **One-File Gateway DX with Core-Enforced Security:** A new gateway can be written in a single file
   inheriting `BaseGateway`, while `Agent.handle` enforces `access_refusal` centrally in core.
4. **Frictionless Local Plugins & Strict Remote Verification:** Local plugins under `plugins/<name>/`
   develop without manual SHA-256 ceremony, whereas remote fetched plugins enforce full digest and
   import isolation.
5. **Clean Provider Wire Seam:** Vendor wire specifics stay isolated in `monoagens/providers/`.

## 3. Options considered

**A. Separate `tasks` and `task_steps` Queue Engine alongside `automations` and `recipes`.** Rejected:
creates four competing queue implementations in PostgreSQL.

**B. External Distributed Task Broker (Celery/Redis).** Rejected: adds external infrastructure
dependencies unnecessary for a single-deployment harness.

**C. Unified Job Engine with Specialized Job Kinds, `BaseGateway`, and Dual-Mode `PluginLoader`.** Accepted.

## 4. Decision

### 4.1 Unified Job Engine (Automations, Durable Tasks, and Recipes)

- **Immediate Wiring Fix (`BI-02, ST-16`):** `cognition.py` and `supervisor.py` pass `thread_id=thread`
  as a keyword argument to `AutomationStore.propose(agent, manifest, *, thread_id, gateway=None)`.
- **Shared Execution Plumbing:** Automations, durable tasks, and recipes share one claim, database-clock
  lease, atomic generation fence, reaper, and step ledger in `monoagens/engine/jobs/`:
  - **Automations (`kind="automation"`):** Declarative `event -> condition -> steps` workflows.
  - **Durable Tasks (`kind="task"`):** Goal-driven agent runs owned by a `person_id` with explicit
    budgets (wall time `run_budget_seconds`, model calls, max steps, queue depth, and per-person quota).
    - Exposed via a role-gated `task` tool and `/task new|list|show|cancel` commands (not granted to
      the default `member` role).
    - Authority & Privacy: A task never exceeds its owner's role permissions at execution time;
      sensitive tools pass through `execute` and `/approve`; memory reads are strictly scoped to the
      owner (`global` + `person:<owner>` + `thread:<origin>`); and completion delivers to the origin
      thread exactly once.
  - **Recipes:** Parameterized tool sequences record each step in `turn_steps` *before* tool execution
    so crash recovery never repeats completed steps.

### 4.2 Core Console Separation, On-Demand Gateway Lifecycle, and `BaseGateway`

- **Built-In Core Console (`monoagens console`) & Removal of `harness`:**
  - The local terminal REPL is a built-in core/admin capability (`monoagens console`), not a transport
    gateway. `monoagens/gateways/cli/` is moved into core/admin console handling, and `config.yaml` /
    `roles.yaml` no longer carry `gateways.cli` blocks (`gateways: {}` is valid when no external
    transport gateway is enabled).
  - The unused `monoagens/gateways/harness/` package is deleted from the repository.
- **On-Demand Gateway & Extension Lifecycle (`enable`, `disable`, `pair`, `unpair`, `delete`):**
  - Unselected gateways, plugins, and optional tool dependencies are not installed or loaded by default.
  - `monoagens gateway enable <name>`: If the gateway code or its Python extra is not yet present on
    disk, automatically downloads/installs the gateway package and its dependencies on demand, seeds
    `config.yaml` (`gateways.<name>.enabled: true`) and `roles.yaml` (using the gateway's own
    `default_role_policy`), and activates it. If already downloaded, flips `enabled: true`.
  - `monoagens gateway disable <name>`: Sets `enabled: false` in `config.yaml` while keeping the
    downloaded code on disk for instant re-enabling.
  - `monoagens gateway pair <name>` & `monoagens gateway unpair <name>`: Runs interactive terminal
    pairing/onboarding (`pair`), or logs out and securely wipes the gateway's transport session files
    (`unpair`) without deleting the gateway code.
  - `monoagens gateway delete <name>`: Stops the gateway, deletes its code directory and session files
    from disk, removes its optional dependencies, and cleans its entries from `config.yaml` and `roles.yaml`.
- **Single-File Gateway SDK (`BaseGateway` & `@register_gateway`):**
  - `monoagens/engine/inbound.py` provides `BaseGateway` with safe default implementations for `Rich`,
    `Directory`, and `PrivateThread` that refuse unsupported capabilities cleanly.
  - A gateway subclass implements three core methods: `normalize_address(self, raw: str) -> str`,
    `run(self, agent) -> None`, and `dispatch(self, action: Action) -> Receipt`.
  - Each gateway registers its own role-policy vocabulary and per-role seed via
    `BaseGateway.default_role_policy(role_name)` and validates it via `BaseGateway.check_access(block, percept)`
    (e.g. `whatsapp` seeds `{"dm", "groups", "reply_to_bot"}`; `discord` seeds `{"dm", "channels", "threads", "reply_to_bot"}`).
  - Core emits `send_thinking`, `send_step`, and `send_interim` actions during turn execution (ADR-0003 §4.5);
    each gateway decides whether and how to render them and validates its own dynamic config keys under
    `gateways.<name>`.
  - Role policy screening (`access_refusal`) executes centrally inside `Agent.handle` / `TurnIntake.receive`
    so no gateway implementation can omit it.

### 4.3 Dual-Mode Plugin Loader and Security Hardening

- **On-Demand Plugin Lifecycle (`enable`, `disable`, `delete`, `digest`):**
  - `monoagens plugins enable <name>` downloads/installs the plugin on demand if not yet present on disk,
    verifies its digest (when remote), and enables it in `config.yaml`.
  - `monoagens plugins disable <name>` turns the plugin off while keeping its code on disk.
  - `monoagens plugins delete <name>` (alias `remove`) verifies that `target.resolve()` is strictly a
    child of the configured plugins directory (`BI-18`), deletes the plugin code from disk, and removes
    its entry from `config.yaml`.
- **Local Development Mode (`plugin.local: true`, default `false`):**
  - Loads plugins directly from `plugins/<name>/` directories without failing on digest edits, and
    marks them `local` in `monoagens plugins list`.
  - `monoagens plugins install <dir>` accepts a local directory and writes its digest into `config.yaml`;
    `monoagens plugins digest <name>` refreshes the digest after local edits.
- **Fetched Archive Verification (`BI-18`):**
  - Bundles fetched from remote sources require a pinned `sha256:` digest verified before extraction and
    import, and `deps/` is included in `tree_digest` verification (never injected unverified into `sys.path[0]`).
- **Ergonomic Manifests & Decorators:**
  - `plugin.yaml` requires only `name`, `version`, `entry`, and `engine`; `provides`, `requires`,
    `secrets`, and `credits` are optional (validated when present).
  - `@plugin.tool` generates JSON parameter schemas automatically from Python type annotations and
    docstrings while keeping explicit schema dictionaries supported.

### 4.4 Provider Wire and Multimodal Speech/Realtime Seam

- **LLM Wire Adapters (`monoagens/providers/`):**
  - Isolates wire protocol specifics behind `ProviderDefaults` and extracts model reasoning/thinking
    content into `LLMResponse.thinking` and open `LLMResponse.meta`:
    - `openai.py`: Chat Completions wire protocol (extracting `reasoning_content` when present).
    - `gemini.py`: Native `generateContent` wire with `safetySettings`, `topK`, and `thought` part extraction.
    - `anthropic.py`: Native `/v1/messages` adapter (~120 lines) supporting multimodal parts, prompt
      caching, `thinking` content blocks, and strict tool schemas alongside `gemini.py`.
- **Unified Soul Voice, TTS/STT, and Real-Time Call/Robot Seam (Cloud, Local, & Custom Models):**
  - Vocal identity (`voice.speech`) belongs to the single `Soul` in `soul.yaml`, while speech synthesis
    (`tts`), transcription (`stt`), and low-latency full-duplex audio streaming (`realtime`) are
    **provider-agnostic core seams** under `monoagens/providers/` rather than gateway-specific code or
    hardcoded vendor SDKs.
  - Operators can freely mix and match **cloud APIs, self-hosted local models, or custom fine-tuned/cloned
    voices** via `provider`, `model`, and `base_url` without vendor lock-in:
    - **TTS (`tts`):** ElevenLabs, Cartesia, OpenAI Audio, or local/custom engines (`piper`, `kokoro`,
      `gpt-sovits`, `f5-tts`, `xtts`, or any custom HTTP/WebSocket/OpenAI-compatible `base_url`).
    - **STT (`stt`):** Whisper API, Deepgram, Groq Audio, or local engines (`faster-whisper`, `whisper.cpp`,
      `sensevoice`, or custom local `base_url`).
    - **Real-Time Voice (`realtime`):** Native speech-to-speech (`Gemini Live`, `OpenAI Realtime`) **or** a
      composable local/hybrid pipeline (`Local VAD/STT` $\rightarrow$ `Core LLM` $\rightarrow$ `Streaming
      ElevenLabs / Local Custom TTS`) so custom cloned voices and local LLMs work seamlessly on live phone
      calls and robots.
  - Gateways (such as WhatsApp Voice Notes / WhatsApp Calls, Discord Voice, SIP/WebRTC telephony, or a
    physical Robot's microphone and speaker) act purely as audio/actuator transport pipes (`Percept`
    audio frames/files in $\rightarrow$ `Action(capability="send_voice" | "call_stream" | "actuate", stream_id=...)`
    out, with `interrupt_thread` / `abort` for barge-in).
  - Because all voice, call, and robot sessions run through the same core `Agent`, `MemoryStore`,
    `AffectStore`, `IdentityStore` (`per_…`), and `ToolIndex`, the user interacts with **the exact same
    continuous soul, vocal timbre, emotional state, memories, and cross-gateway tools** across text chat,
    WhatsApp calls, and physical robot embodiments.

## 5. Configuration

Configured in `config.yaml`:
```yaml
engine:
  jobs:
    worker_interval_seconds: 5
    task_timeout_seconds: 1200
  plugin:
    local: false
```

## 6. Metrics and gates

- Wiring integration test (`ST-16`): Efficiency ladder invokes the real `AutomationStore.propose` method
  without mocking and creates a proposed automation row.
- Gateway contract test: A single-file gateway subclassing `BaseGateway` passes contract verification
  and central `access_refusal` screening.
- Plugin security test: Tampered `deps/` in a fetched plugin is rejected, and `PluginLoader.remove`
  refuses paths outside the workspace.

## 7. Acceptance criteria

- [ ] `cognition.py` calls `AutomationStore.propose` with keyword `thread_id=thread` and passes unmocked tests.
- [ ] `monoagens/gateways/cli/` is moved to the built-in core console (`monoagens console`) and `monoagens/gateways/harness/` is deleted.
- [ ] `Agent.handle` enforces `access_refusal` centrally for every external gateway.
- [ ] `monoagens gateway enable|disable|pair|unpair|delete` and `monoagens plugins enable|disable|delete|digest` manage extension code and dependencies on demand.
- [ ] `deps/` cannot bypass `tree_digest` into `sys.path[0]`, and plugin/gateway deletion guards workspace bounds.
- [ ] Recipes and tasks record steps prior to execution under the unified job engine in `monoagens/engine/jobs/`.

## 8. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Runaway background task consumes excessive tokens | Hard per-task budgets on wall time, model calls, steps, and per-person quotas |
| Local plugin mode left enabled in production | Defaults to `plugin.local: false` and visibly tagged `local` in `monoagens plugins list` |

## 9. Interaction with other ADRs

- **ADR-0003:** Applies database-clock expiry and atomic lease generation fencing to all job claims and updates.
- **ADR-0004:** Tasks and automations deliver outbound messages exclusively via core messaging tools and `Agent.dispatch`.
- **ADR-0006:** Enforces owner role permissions on tasks, recipes, gateways, and plugins.

## 10. Code references

- `monoagens/engine/jobs/supervisor.py`, `cognition.py`, `automation_store.py`, `automation_worker.py`
- `monoagens/engine/stores/recipes.py` — recipe step recording.
- `monoagens/engine/inbound.py` — `BaseGateway` and `@register_gateway`.
- `monoagens/engine/plugins.py` — `PluginLoader`, `tree_digest`, and `@plugin.tool`.
- `monoagens/providers/` — `base.py`, `openai.py`, `gemini.py`, `anthropic.py`.

---

*This ADR lives at `docs/v0.1.0/adr/engine/0005-unified-job-engine-and-extensions.md`.*
