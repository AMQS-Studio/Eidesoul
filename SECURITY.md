# Security Policy

MonoAgens is designed to run as a persistent sovereign agent across personal terminals, multi-user messaging rooms, voice notes, and extensible embodiments. Because a single deployment can serve both an administrator and untrusted or semi-trusted participants across DMs and group chats, security and privacy boundaries are treated as core architectural invariants.

---

## Supported Versions

| Version | Supported | Notes |
| :--- | :--- | :--- |
| `0.1.x` (`main`) | Yes | Active development and security patches |
| `< 0.1.0` (pre-rebaseline) | No | Historical archive only |

---

## Reporting a Vulnerability

**Please do not report security vulnerabilities through public GitHub issues, discussions, or pull requests.**

Instead, report vulnerabilities privately through **[GitHub Private Vulnerability Reporting](https://github.com/AMQStudio/MonoAgens/security/advisories/new)**.

### What to Include

To help us triage and reproduce the issue quickly, please include:

1. **Affected Component:** e.g., `engine/access`, `engine/guard`, `engine/memory`, `engine/tools/sed.py`, `gateways/whatsapp`, `deploy/sandbox-proxy`, etc.
2. **Threat Class:** e.g., RBAC bypass, cross-room memory leak, workspace symlink escape, command guard bypass, approval spoofing, etc.
3. **Reproduction Steps:** Minimal configuration (`config.yaml`, `roles.yaml`) and exact sequence of inputs (`Percept`, tool call, or CLI command) that trigger the issue.
4. **Impact Assessment:** What an unprivileged caller (`guest` or `member`) or untrusted external content (web page, MCP output, group message) can achieve.

### Response Timeline

- **Acknowledgment:** Within **48 hours** of submission.
- **Triage & Severity Assessment:** Within **5 business days**.
- **Remediation & Coordinated Disclosure:** Critical and high-severity fixes are prioritized on `main` and released with a security advisory crediting the reporter (unless anonymity is requested).

---

## Security Architecture & In-Scope Vulnerability Classes

A bug is considered a **security vulnerability** when it violates any of the following deterministic boundaries:

### 1. Role-Based Access Control (`roles.yaml`) & Identity Linking
- An unprivileged or unknown caller invoking a tool or command not granted to their resolved role in `roles.yaml`.
- Bypassing `monoagens/engine/tools/__init__.py:execute` to run a tool without permission verification.
- Hijacking another person's canonical `per_…` identity via `/link`, bypassing `/link` rate limits, or retaining active relay/approval grants after `/unlink`.

### 2. Outbound Door (`Agent.dispatch`) & Approval Gate
- Emitting an outbound message, file, or transport action without passing through `Agent.dispatch()` with an explicit `origin: Address`.
- Forging or bypassing `scope_proof` on staged approvals (`/approve` or natural conversational confirmation) so that a third party in a group chat approves an administrator's pending action.
- Executing a `CONSENT`- or `AUTHORIZATION`-class tool without user confirmation when required by `approval.mode` (`default` or `strict`).

### 3. Layered Privacy (`L0–L4`), Secret Refusal (`L5`), & Cross-Room Isolation
- Storing raw credentials, API keys, or private tokens (`L5`) into the `memories` table instead of refusing/redacting them at intake.
- Disclosing `L3–L4` personal/sensitive memories in a shared group thread above the room's privacy cap or against the subject's veto.
- Reading cross-thread history via `thread_history` without proven room membership (fail-closed) or leaking verbatim private room content across rooms (`guard.cross_chat_leak`).

### 4. Workspace & Host Configuration Protection
- Path traversal or symlink escape outside the caller role's allowed workspace roots (`shared` / `scratch`) in `read`, `write`, `sed`, `grep`, or `send_file`.
- Model-initiated reads or mutations of protected secret files (`.env*`, `*.pem`, `*.key`) or authoritative host configuration files (`config.yaml`, `roles.yaml`, `soul.yaml`) through file tools (`write`, `sed`).

### 5. Command Guard (`SafetyPolicy`) & Container Sandbox
- Bypassing Stage 1 deterministic command screening (`SHELL_WRAPPERS`, `${IFS}` expansion, fetch-to-shell pipes, raw device writes, or privilege escalation).
- Escaping the rootless Podman/Docker disposable sandbox container or bypassing the `deploy/sandbox-proxy/squid.conf` SSRF egress filter to reach loopback/private IPv4 or IPv6 networks (`127.0.0.0/8`, `10.0.0.0/8`, `169.254.169.254`, `::1/128`, `fc00::/7`, `fe80::/10`).

### 6. Plugin Digest Verification & Single-Primary Lease Fencing
- Importing or executing plugin code when `plugin.yaml` or `deps/` fails SHA-256 `tree_digest` verification against the pinned digest.
- A standby instance (without the active PostgreSQL `engine_lease` generation) performing database writes or dispatching outbound gateway actions.

---

## Out of Scope

The following are generally considered operator configuration choices rather than engine vulnerabilities:

- An operator explicitly granting `shell` or `write` tools to untrusted public users in `roles.yaml`.
- An operator running with `approval.mode: yolo` and `sandbox.enabled: false` on a bare host while processing untrusted prompts.
- Hallucinations or stylistic drift by the upstream LLM provider that do not cross RBAC, privacy (`L0–L5`), file-system, or approval boundaries.

---

## Operator Hardening Checklist

When deploying MonoAgens in production or shared multi-user environments:

1. **Protect `.env` and Session Files:** Keep `.env` at `0600` permissions (`monoagens doctor --fix` verifies this automatically). Never commit `.env` or `.monoagens/` session directories to version control.
2. **Keep `administrator` Minimal:** Assign the `administrator` role in `roles.yaml` only to your own verified `per_…` identity. Use `member` or custom restricted roles for teammates, family, and group rooms.
3. **Enable the Container Sandbox:** Set `engine.tools.sandbox.enabled: true` in `config.yaml` whenever `shell` or code execution is used so commands run inside an ephemeral, network-filtered container.
4. **Use `default` or `strict` Approval Mode:** Keep `engine.approval.mode: default` (or `strict`) with `confirm_cross_gateway: true` and `confirm_tasks: true`.
5. **Enable Operator Alerts:** Keep `engine.alerts.enabled: true` (`/alerts on`) so repeated provider, job, or gateway failures are reported directly to administrator DMs.
