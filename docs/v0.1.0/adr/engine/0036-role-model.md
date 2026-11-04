# ADR-0036: Role Model — Named Bundles, One Protected `administrator`, No Role Types

- **Status:** accepted
- **Date:** 2026-11-04
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Context:** extends ADR-0022 (the `administrator` role), ADR-0007 (capability authorization), ADR-0018 (gateway facts), ADR-0033 (neutral core)
- **Supersedes:** —
- **Related:** ADR-0016 (migrations and documents), ADR-0035 (app namespace, unrelated to grants)
- **Implementation:** implemented — protected/users validation, identity-only selection, `people add`, the neutral seed, and CLI role removal are in `access/roles.py`, `admin/people.py`, and their tests.
- **Location note:** an engine decision (it owns identity, roles, and the boot gate), so it sits under `adr/engine/`. The Indonesian copy is `docs/v0.1.0/adr/engine/id/0036-role-model.md`.

---

## 1. Context

Roles are named bundles of grants. A role document carries `users` (person ids), `tools`, `commands`,
`context`, `priority`, `memory_admin`, `label`, and `protected` (`access/roles.py:27-38`). The
`administrator` name is reserved and protected (`roles.py:19`); the boot gate requires it to exist,
to have at least one user, to carry `tools: ["*"]`, and to allow `approve` (`roles.py:84-99`).
Other names are data: a new role needs no code.

Identity is already separate from transport. A body hands in an address, the core assigns an opaque
`per_...` id through `IdentityStore.resolve` (`agent.py:851-862`, `identity.py:107-145`), and role
lookup uses `inbound.actor` only (`roles.py:60-72`). The checked-in seed has three roles and no
users. Measured on 2026-11-04:

| Gap | Evidence |
| --- | --- |
| a role hint is trusted from every body | `agent.py:654-662` reads `hints["roles"]`; `inbound.py:13-20` documents it as local console only |
| the seed administrator has no user | `roles.yaml:5`, `admin/templates.py:306-320`; `require_administrator()` rejects it |
| init cannot fill the administrator user | `eidesoul init --label` sets `label`, not `users` (`admin/init.py:227-230`) |
| the seed is culture-bound and stale | `description: Committee member`; commands `cari`, `memori`, `kirim`; tool `google` (`roles.yaml:31-46`) |
| `protected` is not restricted to the administrator | `parse_role` accepts `protected: true` on any role (`roles.py:165-174`), while `prompts.py:42` and `status.py:61` treat any protected role as privileged |
| `users` accepts any string | `parse_role` calls `_str_tuple` and stops (`roles.py:151-183`); a raw address silently matches no `per_...` and falls to `default_role` |

The model is directionally sound: permissions come from explicit lists, identity is opaque, and the
administrator invariant is checked at boot. The seed, the role hint, and the `protected` flag are
where it is not yet solid.

## 2. Decision drivers

1. **One source of permission.** Only `tools` and `commands` grant. A role *type* that grants would
   create a second source and make the lists non-authoritative.
2. **One safety invariant, not a category.** `administrator` exists because a protected role must
   always be able to approve and never disappear; it is not a template for a class of roles.
3. **Identity, not transport.** Grants attach to a canonical person, never to a gateway, a thread,
   or a URL. A body may state facts; it may not state authority.
4. **Fail closed.** An unknown role, a missing grant, a missing administrator user, and a role hint
   from an untrusted body all deny or refuse; none silently widen.
5. **Boring and explicit.** Named bundles with flat lists are predictable; type hierarchies,
   inheritance, and per-room scopes add merge rules that must be tested before they can be trusted.
6. **Bootstrap must be possible.** A fresh install must be able to create its first administrator
   without hand-editing a YAML file or weakening the boot gate.

## 3. Options considered

**A. A `type: admin|user|guest` field with implicit grants.** Rejected: it becomes a second
permission source, raises "how many admins", and still needs the protected name for the boot gate.
Two ways to say the same thing is worse than one.

**B. A display-only `kind` field.** Not rejected forever, but deferred: it has no consumer until the
panel needs grouping, and adding dead metadata now invites someone to treat it as authority.
If added later, it must be validated as an enum and must grant nothing.

**C. Per-gateway role configuration (`gateway: whatsapp: users:`).** Rejected: a person could be
administrator on one transport and guest on another, the core would learn transport names, and the
gateway-address mapping already lives in `person_links`. A grant per body is also unnecessary:
`ToolIndex` already exposes only the active body's tools.

**D. Named bundles, one hardcoded protected name, identity-only grants (chosen).**

**E. Keep the role hint with an allowlist of sources.** Rejected for this phase: an allowlist still
makes the wire the place where authority is claimed. The local console can use a normal linked
identity instead, which removes the hint and the extra config key.

## 4. Decision

### 4.1 Roles stay named permission bundles

A role is an entry in `roles.yaml` / the `role:<name>` document with flat `tools` and `commands`
lists. There is no `type`, `kind`, or `level` field with authority. A role named `user` or `admin`
is just a name; it grants only what its lists say.

A display-only `kind` MAY be added later for the panel, with an enum, no default grant, and no use
in policy. It does not exist now.

### 4.2 `administrator` remains the only reserved name

- It must exist, carry `tools: ["*"]`, allow `approve`, and have at least one `per_...` user.
- It cannot be deleted or renamed; only `label` and its user list may change, through `roles.yaml`
  plus `eidesoul config import` (ADR-0022).
- There is no `admin` alias. A second role with `"*"` is allowed and explicit, but it is not
  protected and does not satisfy the boot gate.
- `priority` orders matched roles; it does not make one role override another. Grants are additive.

### 4.3 `protected` belongs only to the administrator

`parse_role` MUST reject `protected: true` on any role whose name is not `administrator`. The flag
keeps both meanings it already has: the role cannot be deleted or renamed, and it is treated as
privileged for diagnostics and redaction. Those meanings are not split into two fields in this ADR;
one flag, one owner, is the smaller contract.

Privilege is one predicate, used everywhere:

```python
def privileged(role) -> bool:
    return role.protected or "*" in role.tools
```

`status` and the prompt redaction path MUST use this predicate, not a second rule. A custom role
with `"*"` is explicitly privileged; a role with `protected` is the administrator.

### 4.4 Role selection is identity-only

- `Inbound.hints["roles"]` is removed from `HINT_KEYS` and from every producer. A body cannot name
  a role.
- `Agent._role_set` deletes the override branch and always calls `roles.for_person(inbound.actor)`.
- `gateways.cli.role` is removed. The console user is a normal address (`user: local`), resolved by
  `IdentityStore` like any other body; the operator links that address to the administrator person
  once, during bootstrap.
- A role hint that arrives anyway is ignored; it never widens a grant.

### 4.5 `users` holds person ids, not addresses

`parse_role` validates every entry against `is_person_id` (`per_` plus ten base32 characters,
`identity.py:30-48`). A transport address, a phone number, a JID, or an arbitrary label is a
`RoleError` at load time. A well-formed `per_...` that has no row yet is allowed (the person may be
created later); matching simply does nothing until the row and link exist.

`people adopt` remains the migration path for legacy raw addresses; the role document is rewritten
to the adopted `per_...` ids, not kept as a second form.

### 4.6 Bootstrap is an explicit, audited command

A new subcommand creates the first administrator without hand-editing YAML:

```text
eidesoul people add <gateway> <address> --role administrator [--name <display name>]
```

It performs one transaction: create the `people` row with a fresh `per_...`, insert the
`person_links` row for `gateway`/`address`, append the id to the role document's `users`, write the
`roles.yaml` mirror, and record the change in `config_changes` and the action ledger. It refuses an
unknown gateway, an unknown role, a role that is not `administrator` for the first user (unless the
role already has users), and an address already linked to another person.

`eidesoul init` prints this command as the first next step. `eidesoul run` keeps the existing boot
gate: with no administrator user it refuses and names the command. It never auto-grants the first
sender.

### 4.7 The seed is neutral and minimal

`roles.yaml` and `admin/templates.ROLES` are updated together:

- English descriptions; no "Committee member".
- No command aliases (`cari`, `memori`, `kirim`) and no Indonesian verb grants.
- No `google` (or any plugin-provided tool) in the default member role; the operator adds it when
  the plugin is enabled.
- The default member keeps only the shipped, always-available capabilities: `read`, `grep`,
  `fetch`, `web_search`, `chat_history`, `memory`, `info`; commands `help`, `ping`, `link`,
  `unlink`, `whoami`, `status`, `tools`. The guest role keeps its narrower list.
- `administrator.users` stays empty in the file; it is filled by `people add`, not by a placeholder.

### 4.8 No deny, inheritance, or per-room grants in this phase

Grants are additive. A role cannot subtract a tool another role granted. There is no `extends`, no
deny list, and no per-thread role. If an operator needs "member in DM, guest in a group", that is a
new policy decision with its own ADR; it is not a YAML nesting trick. The limitation is documented
in the role contract.

## 5. Configuration

- `gateways.cli.role` is removed; no replacement key.
- `engine.default_role` stays (the fallback for an unlinked person), and it must name an existing
  role.
- `engine` gains no role-type key. The only new surface is the `people add` subcommand.
- `roles.yaml` stays seed plus mirror; `role:<name>` documents stay authoritative.

## 6. Metrics and gates

Counts to drive to zero, measured 2026-11-04:

| Gap | Count | Where |
| --- | --- | --- |
| role types with authority | 0 (keep at zero) | — |
| role hints trusted from a body | 1 | `agent.py:654-662`, `inbound.py:13-20` |
| CLI role keys | 1 | `gateways.cli.role` |
| non-`per_` users in the seed | 0 | `roles.yaml`, `admin/templates.py` |
| stale aliases in the seed | 3 | `cari`, `memori`, `kirim` |
| protected roles other than the administrator | 1 possible | `roles.py:165-174` |

The gate:

1. `protected: true` on `member` is a `RoleError`; on `administrator` it is accepted and enforced.
2. A `Percept` carrying `hints["roles"]` cannot change the role set; `HINT_KEYS` no longer contains
   `roles`; the CLI tests no longer expect it.
3. A fresh seed fails `require_administrator`; `people add ... --role administrator` makes it pass;
   the id appears in the DB document and the `roles.yaml` mirror.
4. `users` entries that are not `per_...` are rejected at load; a valid but unknown id is allowed.
5. The seed contains no `Committee member`, no `cari`/`memori`/`kirim`, and no `google`.
6. Two roles listing one person produce the union of their grants; `priority` changes order only.
7. `status` and prompt redaction agree on `privileged(role)` for a role with `"*"` and for the
   administrator.
8. `pytest -q` and the CLI gateway tests are updated; no test asserts the removed hint.

## 7. Acceptance criteria

- The role document validates shape and `users` at load, with an error naming the offending value.
- No code path reads a role from `Percept.hints`; identity resolution is the only selector.
- The console reaches administrator through a linked person, not through a role hint.
- A fresh install can reach a working administrator with one documented command, without editing
  YAML and without weakening the boot gate.
- The seed is English, neutral, minimal, and free of aliases and optional plugin grants.
- The additive-union limitation is documented; no deny/inheritance/per-room behavior is implied.
- No role `type` exists; adding one later would require a new ADR.

## 8. Risks and mitigations

- **Operators lose the console role shortcut.** Mitigation: `people add cli local --role
  administrator` is printed by init; the console user is otherwise a normal identity.
- **A valid `per_...` is written before the person exists.** Mitigation: the id is just a string;
  `people add` creates the row in one transaction, and `people adopt` updates legacy addresses.
- **A custom role with `"*"` is privileged but not protected.** Mitigation: that is explicit and
  intentional; only the boot gate depends on the protected name, not on every powerful role.
- **Seed cleanup removes a grant an operator relied on.** Mitigation: pre-release, no compatibility;
  the operator adds the grant back to `roles.yaml` and imports.
- **The `people add` command becomes a second write path.** Mitigation: it reuses the role import
  path with backup, audit, and the same document store; it is one command, not a new store.
- **Removing the role hint breaks tests and local scripts.** Mitigation: one migration commit updates
  the CLI config, the fixtures, and the architecture test in the same change.

## 9. Interaction with other ADRs

- ADR-0022: the name, protection, and boot gate stay; this ADR removes the shortcut that let a body
  claim the role and fills the user list explicitly.
- ADR-0007: approval classes and the tool matrix are unchanged; identity only selects the role set.
- ADR-0018: the gateway hands in an address, never a role; the body stays untrusted for authority.
- ADR-0033: no gateway or transport name enters the role logic; the CLI is linked like any body.
- ADR-0035: app scope routes skill corpora; it does not change roles or grants.
- ADR-0016: no schema change is required; `people`, `person_links`, and role documents already
  exist.

## 10. Code references

- `eidesoul/engine/access/roles.py:19,27-38,60-72,84-108,151-183` — the reserved name, the Role
  fields, identity lookup, the boot gate, and parsing.
- `eidesoul/engine/access/identity.py:30-48,107-145` — `per_...` shape and resolution.
- `eidesoul/engine/agent.py:654-662,851-862` — the role hint branch and the identity hand-in.
- `eidesoul/engine/inbound.py:13-20,47-53` — the hint list and `HINT_KEYS`.
- `eidesoul/gateways/cli/config.py:8-21` and `gateways/cli/gateway.py:66-67` — the CLI role option
  and the hint it writes.
- `eidesoul/engine/turn/prompts.py:42`, `eidesoul/engine/commands/status.py:61` — the privilege
  predicate sites.
- `eidesoul/engine/access/roles.py` import path and `eidesoul/admin/roles.py` — the audited write
  path `people add` reuses.
- `roles.yaml`, `eidesoul/admin/templates.py:306-320`, `eidesoul/admin/init.py:227-230` — the seed
  and the `--label` behavior.
- `tests/test_roles.py`, `tests/test_cli_gateway.py`, `tests/test_architecture.py` — the suites this
  decision changes.

---

*This ADR lives at `docs/v0.1.0/adr/engine/0036-role-model.md`.*
