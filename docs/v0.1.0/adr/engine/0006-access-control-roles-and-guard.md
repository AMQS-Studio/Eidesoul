# ADR-0006: Access Control, Role-Based Security, and Sandbox Guard — RBAC, Identity Linking, and Command Screening

- **Status:** accepted
- **Date:** 2026-11-05
- **Deciders:** Muqsith (owner)
- **Context:** Re-baselined for v0.1.0; consolidates capability authorization, the administrator role, relay consent, and role models (pre-rebaseline 0007, 0022, 0023, 0036) with the security and identity hardening in pre-rebaseline 0049 and 0053.
- **Supersedes:** pre-rebaseline 0007, 0022, 0023, 0036
- **Related:** ADR-0001 (sovereign core), ADR-0004 (core tools)
- **Implementation:** partial — RBAC and base sandbox guards exist; persona mode separation and `/link` attack hardening are scheduled in Phase P0/P2.

---

## 1. Context

The security harness controls which humans and tools can perform operations. The architectural audits
(`docs/audit.md` §1.3.1, §2.3) identified major security vulnerabilities and conceptual collisions:
1. **Persona vs RBAC Collision:** `soul.yaml` defined `role: assistant` (persona mode), while `roles.yaml`
   defined RBAC authorization roles (`administrator`, `member`). Both were injected into the same model
   prompts, confusing the LLM and leaving dead columns (`people.role`) in the schema.
2. **Single-Role Gateway Rejection:** `RoleRegistry.policy_for` only checked the highest-priority role
   (`roles[0]`). If a user was an `administrator` (lacking WhatsApp policy) and a `member` (having WhatsApp
   policy), the user was rejected completely on WhatsApp.
3. **Identity Absorption Attack via `/link`:** A malicious guest could create a `/link` code and trick an
   admin into entering it; the code absorption logic merged and deleted the admin's `people` record,
   locking the admin out permanently. Furthermore, invalid `/link` attempts did not bump rate limiters.
4. **Sandbox Guard Evasion:** Deterministic screening was bypassed using shell wrappers (`timeout 5 git push --force`,
   `env -S "git push --force"`), `${IFS}` whitespace evasion, or reading sensitive paths (`cat /etc/shadow`).
   Simultaneously, safe commands like `cat docker-compose.yml` were falsely blocked.

## 2. Decision drivers

1. **Role-Based Access Control:** Authoritative permissions reside strictly in `roles.yaml`. A missing
   role or permission entry denies access by default.
2. **Unambiguous Terminology:** Persona modes (`mode: assistant | companion | advisor`) are completely
   separated from access control roles (`administrator | member | guest`).
3. **Multi-Role Fallback:** A user assigned multiple roles inherits permissions via union and evaluates
   gateway policies across all assigned roles in priority order.
4. **Fail-Closed Guardrails:** Sensitive commands and tools require approval; shell execution screens
   against bypass wrappers and filesystem boundaries.
5. **Robust Identity Linking:** Account linking must resist brute-force enumeration and prevent deletion
   of privileged identities.

## 3. Options considered

**A. Hardcoded Permission Tables.** Rejected: inflexible; prevents operators from granting custom
permissions to family or team members.

**B. Pure LLM-Based Security Policy.** Rejected: vulnerable to prompt injection; authorization decisions
must be evaluated deterministically before model invocation.

**C. Layered RBAC with Deterministic AST/Token Guard.** Selected: combines strict role matrices,
multi-role fallback, and robust command screening with fail-closed LLM verification.

## 4. Decision

### 4.1 RBAC and Role Architecture

- Permissions derive strictly from `roles.yaml`:
  - `administrator`: Protected top role (`protected: true`, `tools: "*"`). Must always exist and cannot
    be deleted or renamed.
  - `member`: Standard trusted user with basic conversational and tool rights.
  - `guest`: Untrusted/unknown sender; restricted to `help`, `ping`, and explicit read-only tools.
- `RoleRegistry.policy_for` evaluates assigned roles in priority order, selecting the first role that
  defines policy for the active gateway.
- Dead `people.role` column is dropped; RBAC role assignments are derived dynamically from `roles.yaml`.

### 4.2 Separation of Persona Mode and Access Control

- `soul.yaml` uses `mode` and `modes` (e.g. `mode: assistant`, `modes: [assistant, companion, advisor]`).
- Switching modes via `/mode <name>` alters conversation tone and character tuning, never access
  permissions.

### 4.3 Sandbox Guard and Command Screening

- The deterministic screening layer inspects commands before execution:
  - `SHELL_WRAPPERS`: Recursively unwraps `timeout`, `flock`, `setsid`, `script`, `env -S`, `watch`,
    `doas`, and `pkexec`.
  - Rejects shell evasion syntax including `${IFS}` variable interpolation and brace expansion.
  - File reading commands (`cat`, `sort`, `find`) are strictly bounded to the configured workspace and
    denied write flags (`-o`, `-fprint`).
- The LLM security classifier acts as a secondary layer with a strict 5-second timeout, failing closed
  upon model error or latency.

### 4.4 Hardened Identity Linking (`/link`)

- `/link` rate-limiting: Failed code attempts increment sender and gateway rate counters.
- Atomic transaction: Code consumption uses `SELECT ... FOR UPDATE` in a single transaction.
- Merge protection: `finish_code` refuses to merge or delete any identity that holds assigned roles
  in `roles.yaml`.

## 5. Configuration

Configured in `roles.yaml` and `config.yaml`:
```yaml
engine:
  guard:
    mode: yolo  # or strict
    sensitive_tools: ["shell", "sandbox", "settings"]
```

## 6. Metrics and gates

- Security evasion test: Commands with `timeout`, `env -S`, or `${IFS}` are screened and denied.
- Identity attack test: Entering a link code from a guest account cannot absorb an administrator account.
- Multi-role test: A user with `[administrator, member]` successfully accesses WhatsApp.

## 7. Acceptance criteria

- [ ] `administrator` role cannot be deleted or renamed.
- [ ] Changing `soul.mode` changes only character prompts, not access rights.
- [ ] Destructive shell commands wrapped in execution utilities are blocked or parked for approval.
- [ ] `/link` rejects merging identities registered in `roles.yaml`.

## 8. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Strict mode causes operator approval fatigue | Default `yolo` mode allows autonomous execution within sandbox boundaries |
| Command screen false positives on complex scripts | Operators can explicitly approve flagged commands via `/approve` |

## 9. Interaction with other ADRs

- **ADR-0001:** Enforces `administrator` bootstrap via `monoagens people add`.
- **ADR-0004:** Restricts cross-room messaging tools to authorized roles.

## 10. Code references

- `monoagens/engine/access/roles.py` — `RoleRegistry` and RBAC evaluation.
- `monoagens/engine/access/identity.py` — `IdentityStore` and `/link` security logic.
- `monoagens/engine/guard/` — `SafetyPolicy`, `SandboxGuard`, and command patterns.

---

*This ADR lives at `docs/v0.1.0/adr/engine/0006-access-control-roles-and-guard.md`.*
