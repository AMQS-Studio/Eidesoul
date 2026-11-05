# ADR-0040: Naming — MonoAgens and the Persistent Agent Harness

- **Status:** accepted
- **Date:** 2026-11-04
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Context:** follows ADR-0009 (ADR home), ADR-0027 (distribution), ADR-0031 (engine structure), ADR-0033 (neutral core), and ADR-0039 (setup terminal); replaces the product name Eidesoul.
- **Supersedes:** —
- **Related:** ADR-0000 (initial invariants), ADR-0027 (distribution), ADR-0031 (module structure), ADR-0033 (neutral core), ADR-0039 (setup terminal)
- **Implementation:** implemented — package, CLI, environment, state directory, docs, and repository URL use MonoAgens; no alias remains.
- **Location note:** naming is cross-cutting, so this ADR sits at the root of the ADR home. The Indonesian copy is `docs/v0.1.0/adr/id/0040-monoagens-naming.md`.

---

## 1. Context

The product shipped as **Eidesoul**. The name carries *soul*, a metaphysical term: it implies
consciousness and invites the ensoulment debate. The internal concept `soul.yaml` and the `soul`
document are unaffected, but the product name made a claim the engine does not support.

At the same time, **harness** is the correct engineering category — it is the runtime that wraps a
model and turns it into an agent (arXiv:2606.10106; Microsoft Learn, *Agent Harness*) — but it is
jargon for users and should not carry the product name. The `<prefix>Agent` namespace is crowded:
HermesAgent exists, and `mono-agent` is an existing product (PyPI `mono-agent`,
`docs.mono-agent.dev`). A name of that shape would be derivative and would collide.

The literature already names the property and the category: *persistent identity* (Menon,
arXiv:2604.09588; Natangelo, arXiv:2510.24831), *identity continuity* (Ru, arXiv:2609.23356), and
*agent harness* (arXiv:2606.10106). The decision uses those terms instead of inventing a synonym.

The owner asked for a name that is legible (an agent), original (not `<prefix>Agent`), free of
metaphysical claims, and backed by a type name usable in docs. The repository must be renamed in one
cutover, not by compatibility aliases.

## 2. Decision drivers

| Driver | Consequence |
| --- | --- |
| No metaphysical claim | No *soul* and no *self* in the product name; *person* stays product copy, not a category |
| Category must be legible | *agent* is the category; *harness* stays in the runtime type, not in the product name |
| Name must be original | No `<prefix>Agent`; no collision with HermesAgent or `mono-agent` |
| One invariant must be visible | *mono* names one identity, one writer, one store — not one body, not one process |
| Persistence must be visible | *persistent* names the claim: state survives sessions, model swaps, and bodies |
| Clean cutover | One commit; no alias package, no shim, no dual CLI |
| Honest versioning | Pre-release; the rename is a breaking change and is allowed to be one |

## 3. Options considered

| Option | Summary | Value |
| --- | --- | --- |
| **A. Keep Eidesoul** | ship the existing name | Rejected: *soul* implies consciousness; the README would keep explaining what it does not claim |
| **B. MonoHarness** | encode the category in the name | Rejected: *harness* is jargon for users and repeats the runtime type in the product name |
| **C. MonoAgent** | one agent | Rejected: collides with the existing `mono-agent` product and belongs to the derivative `<prefix>Agent` shape |
| **D. MonoAssistant** | one assistant | Rejected: narrows the product to one role; assistant is one of assistant, companion, advisor, and robot |
| **E. Agens** | Latin for *agent* | Considered: category-correct, but it does not carry the one invariant |
| **F. Perstare** | Latin *to persist* | Rejected: semantically exact but not legible to users; the category disappears from the name |
| **G. Synecheia** | Greek *continuity* | Rejected: hard to spell and pronounce |
| **H. Noctum** | night | Rejected: off-concept; names night, not persistence |
| **I. self-continuity** | psychology construct | Rejected: implies self and consciousness; it is a property, not a tool type |
| **J. persistent continuity** | persistence plus continuity | Rejected: redundant; continuity already means persistence through time |
| **K. identity-continuity** | hyphenated compound | Rejected: the hyphenated modifier was rejected for readability |
| **L. MonoAgens** | one agent | **Chosen** |

## 4. Decision

The product and its type are named as follows:

- **Product name:** MonoAgens.
- **Category:** agent.
- **Runtime type:** persistent mono agent harness.
- **Tagline:** *persistent mono agent, always remembering, always learning, the same person wherever
  it lives.*
- **Layer:** MonoAgens is the agent; the persistent mono agent harness is its runtime. MonoAgens is not
  the harness.

The name is one word in product copy and two morphemes in the glossary:

| Morpheme | Meaning | What it rules out |
| --- | --- | --- |
| *mono* | one identity, one writer, one store | not one body, not one process, not one replica |
| *agens* | Latin *one who acts*; the root of *agent* | not an assistant-only role; not a harness |

*Persistent* names the claim in the type, not in the product name: the state survives sessions,
model swaps, and embodiments. *Continuity* stays the short form of the runtime type and the property
that the harness maintains.

The tagline is product copy. *Person* there names the same identity, not personhood, and not
consciousness; the README states this in the naming note.

## 5. Rename scope

The rename is a clean cutover. This ADR records the scope; it does not execute it.

| Surface | From | To |
| --- | --- | --- |
| Python package | `monoagens/` | `monoagens/` |
| Import root | `monoagens.*` | `monoagens.*` |
| Distribution name | `monoagens` | `monoagens` |
| CLI | `monoagens` | `monoagens` |
| Repository | `github.com/inimuqsith/Eidesoul` | `github.com/AMQStudio/MonoAgens` |
| State directory | `.monoagens/` | `.monoagens/` |
| Environment prefix | `MONOAGENS_*` | `MONOAGENS_*` |
| Wordmark | `MONOAGENS` | `MONOAGENS` |
| README and README.id | Eidesoul | MonoAgens |
| AGENTS, TASK, LOG | Eidesoul | MonoAgens |
| ADR code references | `monoagens/...` | `monoagens/...` |
| Docker image and file | `monoagens` | `monoagens` |
| Lockfile | current | regenerated after `pyproject.toml` |

What does **not** change:

- `soul.yaml` and the `soul` document: they name the internal concept, not the product.
- The `engine`, `gateways`, and `roles` configuration sections.
- Gateway names, database schema, table names, and migration identifiers.
- Historical ADR decisions: their prose keeps the old product name; only code references are
  updated so they resolve after the move.

Cutover order:

1. `pyproject.toml`: `name`, `[project.scripts]`, `[tool.setuptools.packages.find]`, project URLs.
2. `git mv monoagens monoagens`; rewrite imports in engine, gateways, admin, providers, tests,
   scripts, and `migrations/env.py`.
3. Runtime paths: `.monoagens/` to `.monoagens/`, environment prefix, gateway defaults.
4. Branding: `admin/brand.py`, `admin/logo_art.py`, `assets/logo.png` alt text, and the lockup
   assertions in `tests/test_init.py`.
5. Docs: `README.md`, `README.id.md`, `AGENTS.md`, `TASK.md`, `LOG.md`, and ADR code references.
6. Repository rename, badges, and links.
7. `uv lock`; then the gates in section 6.

No aliases, no shim package, no second entry point, and no environment-variable fallback. The
pre-release status makes the break acceptable.

## 6. Configuration

No configuration key is added or renamed by this decision. The naming contract is:

| Term | Value |
| --- | --- |
| Product | MonoAgens |
| Category | agent |
| Runtime type | persistent mono agent harness |
| Short runtime type | continuity harness |
| Tagline | *persistent mono agent, always remembering, always learning, the same person wherever it lives.* |
| Package and CLI | `monoagens` |
| State directory | `.monoagens/` |
| Environment prefix | `MONOAGENS_` |

The `soul.yaml` key, the `soul` document, and the `engine` section keep their names.

## 7. Metrics and gates

| Metric | Target |
| --- | ---: |
| `monoagens` occurrences after the cutover | 0 outside historical ADR prose |
| Broken imports in engine, gateways, admin, providers, tests, scripts, migrations | 0 |
| `pytest -q` | pass |
| `ruff check` | pass |
| `python -P scripts/types.py --check` | pass |
| `python scripts/adr.py --check` | pass |
| `python scripts/adr.py --translate-check` | pass |
| `python scripts/selfcheck.py --check` | pass |
| `monoagens selftest` | pass |
| `uv sync --locked` | pass |
| Reserved names (`monoagens` on PyPI, repository on GitHub) | verified before cutover |

## 8. Acceptance criteria

- [x] `monoagens` imports and `pytest -q` passes.
- [x] `monoagens selftest` runs on the real store.
- [x] `README.md` and `README.id.md` use MonoAgens, the tagline, and the naming note.
- [x] `AGENTS.md`, `TASK.md`, and `LOG.md` use MonoAgens.
- [x] ADR code references resolve to `monoagens/...` paths.
- [x] The wordmark and `tests/test_init.py` assertions use MONOAGENS.
- [x] `uv.lock` is regenerated and `uv sync --locked` passes.
- [x] `grep -R monoagens` returns only historical ADR prose.
- [x] The repository, badges, and project URLs point at MonoAgens.
- [x] No alias package, shim, or dual CLI exists.

## 9. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| A partial rename breaks imports | One cutover branch; tests run before the repository is renamed |
| A stale reference survives in docs or scripts | The `grep -R monoagens` gate and the ADR checks |
| The PyPI or GitHub name is taken | Verify both before the cutover; fall back to an owner-scoped repository if needed |
| The ANSI wordmark is regenerated incorrectly | Update `admin/logo_art.py` with the new rows and assert them in `tests/test_init.py` |
| Historical ADRs point at old paths | Update code references only; keep the decision prose unchanged |
| `.monoagens/` state is orphaned | Document a one-time `mv .monoagens .monoagens` in the cutover note; no runtime fallback |
| Installed CLI users break | Pre-release; the break is allowed and is listed in `LOG.md` |

## 10. Interaction with other ADRs

- **ADR-0009**: this ADR lives at the ADR home root; its Indonesian copy lives in `adr/id/`.
- **ADR-0027**: the distribution image and its entry point take the new name; extras and the
  default-off capability rule do not change.
- **ADR-0031**: the package tree is renamed; module boundaries and the 800-line budget do not move.
- **ADR-0033**: the rename introduces no transport, vendor, or culture vocabulary into the engine.
- **ADR-0039**: the setup wordmark and the lockup assertions change; the wizard, location step, and
  secret handling do not.

## 11. Code references

- `pyproject.toml` — distribution name, scripts, package discovery, and project URLs.
- `monoagens/__main__.py` — CLI dispatch and the renamed entry point.
- `monoagens/config.py` — host paths and the state directory.
- `monoagens/admin/brand.py` — wordmark and tagline.
- `monoagens/admin/logo_art.py` — generated half-block rows.
- `monoagens/admin/init.py` — defaults that mention the state directory.
- `migrations/env.py` — package import in the Alembic environment.
- `tests/` — imports and wordmark assertions.
- `scripts/` — imports and path constants.
- `README.md`, `README.id.md`, `AGENTS.md`, `TASK.md`, `LOG.md` — product name and links.
- `Dockerfile`, `uv.lock` — image entry point and the regenerated lockfile.

---

*This ADR lives at `docs/v0.1.0/adr/0040-monoagens-naming.md`.*
