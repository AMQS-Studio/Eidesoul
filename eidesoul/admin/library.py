"""Library resolver: pick the character packs that fit a soul's own text.

`style` used to be authored by hand. It is now derived top-down: the soul text (description,
background, persona, examples) decides which pattern and fact packs belong to it, first by
tokens and, when the embedder is available, by cosine against each pack's own vocabulary.
A pack is only accepted with a clear winner; nothing is ever forced, and a kind without a
match keeps whatever the soul already selected.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import yaml

from ..engine import embed, knowledge, patterns
from ..engine.soul import Soul, SoulStyle, load_soul

FLOOR = 0.35  # cosine below this never selects a pack
MARGIN = 0.03  # the winner must beat the runner-up by this much
DEFAULT_PATTERNS = 2
DEFAULT_FACTS = 3
KINDS = ("patterns", "facts")
TOKEN = re.compile(r"[^\W_]+", re.UNICODE)


@dataclass(frozen=True)
class Candidate:
    """One accepted (or kept) pack, with the evidence that chose it."""

    pack: str
    kind: str
    score: float
    reason: str


@dataclass(frozen=True)
class _Pack:
    name: str
    kind: str
    language: str
    profile: str


def _tokens(text: str) -> set[str]:
    return set(TOKEN.findall(str(text or "").casefold()))


def _pattern_packs() -> list[_Pack]:
    found: list[_Pack] = []
    for target in sorted(patterns.LIBRARY.glob("*.json")):
        if target.stem.endswith("_vectors"):
            continue
        body = patterns.load_pack(target.stem)
        if not body:
            continue
        lexicon = body.get("lexicon") if isinstance(body.get("lexicon"), dict) else {}
        words = [str(item) for values in lexicon.values() for item in values or ()]
        for row in body.get("patterns") or ():
            if isinstance(row, dict):
                words.extend(str(key) for key in row.get("keys") or ())
        found.append(
            _Pack(target.stem, "patterns", str(body.get("language") or ""), " ".join(words))
        )
    return found


def _fact_packs() -> list[_Pack]:
    found: list[_Pack] = []
    for target in sorted(knowledge.LIBRARY.glob("*.json")):
        if target.stem.endswith("_vectors"):
            continue
        body = knowledge.load_pack(target.stem)
        if not body:
            continue
        words: list[str] = []
        for row in body.get("facts") or ():
            if isinstance(row, dict):
                words.extend(str(key) for key in row.get("keys") or ())
                words.append(str(row.get("topic") or ""))
        found.append(
            _Pack(target.stem, "facts", str(body.get("language") or ""), " ".join(words))
        )
    return found


@cache
def _packs() -> tuple[_Pack, ...]:
    return tuple(_pattern_packs() + _fact_packs())


@cache
def _kinds() -> dict[str, str]:
    return {pack.name: pack.kind for pack in _packs()}


def pack_profiles() -> dict[str, str]:
    """Pack id -> the vocabulary that describes it (lexicon, pattern keys, fact keys, topics)."""
    return {pack.name: pack.profile for pack in _packs() if pack.profile}


def packs_for(language: str) -> dict[str, str]:
    """Profiles a soul in `language` may use; other languages are gated out."""
    wanted = str(language or "").strip().lower()
    return {
        pack.name: pack.profile
        for pack in _packs()
        if pack.language == wanted and pack.profile
    }


def _keyword_pick(kind: str, group: dict[str, str], text_tokens: set[str]) -> Candidate | None:
    scored = sorted(
        ((name, len(_tokens(profile) & text_tokens)) for name, profile in group.items()),
        key=lambda pair: (-pair[1], pair[0]),
    )
    top, score = scored[0]
    second = scored[1][1] if len(scored) > 1 else 0
    if score <= 0 or score - second < 1:
        return None
    matched = sorted(_tokens(group[top]) & text_tokens)
    return Candidate(top, kind, float(score), f"tokens: {', '.join(matched)}")


def _embedding_pick(kind: str, group: dict[str, str], text: str) -> Candidate | None:
    names = [name for name in sorted(group) if _tokens(group[name])]
    if not names:
        return None
    words: list[str] = []
    sizes: list[int] = []
    for name in names:
        found = sorted(_tokens(group[name]))
        words.extend(found)
        sizes.append(len(found))
    query = embed.encode([text])
    vectors = embed.encode(words)
    if not query or len(vectors) != len(words):
        return None
    scored: list[tuple[str, float]] = []
    index = 0
    for name, size in zip(names, sizes, strict=True):
        group_vectors = vectors[index : index + size]
        index += size
        centroid = [
            sum(value) / size for value in zip(*group_vectors, strict=True)
        ]
        scored.append((name, embed.cosine(query[0], centroid)))
    scored.sort(key=lambda pair: (-pair[1], pair[0]))
    top, best = scored[0]
    runner_up = scored[1][1] if len(scored) > 1 else 0.0
    if best < FLOOR or best - runner_up < MARGIN:
        return None
    return Candidate(top, kind, best, f"cosine {best:.3f}")


def suggest(text: str, packs: dict[str, str], *, use_embedding: bool) -> list[Candidate]:
    """At most one pack per kind, accepted only on clear evidence; `[]` means no pick."""
    body = " ".join(str(text or "").split())
    if not body or not packs:
        return []
    kinds = _kinds()
    groups: dict[str, dict[str, str]] = {}
    for name, profile in packs.items():
        kind = kinds.get(name)
        if kind is not None:
            groups.setdefault(kind, {})[name] = profile
    text_tokens = _tokens(body)
    found: list[Candidate] = []
    for kind in KINDS:
        group = groups.get(kind) or {}
        if not group:
            continue
        pick = None
        if use_embedding:
            pick = _embedding_pick(kind, group, body)
        if pick is None:
            pick = _keyword_pick(kind, group, text_tokens)
        if pick is not None:
            found.append(pick)
    return found


def soul_text(soul: Soul) -> str:
    """The text the resolver reads: what the author wrote, never the pack ids."""
    examples = " ".join(
        " ".join(str(value) for value in example.values()) for example in soul.examples
    )
    return " ".join(
        part for part in (soul.description, soul.background, soul.persona, examples) if part
    )


def seed_style(text: str, language: str) -> SoulStyle:
    """Style for a fresh soul: the text decides, otherwise the first pack of each kind."""
    packs = packs_for(language)
    accepted = suggest(text, packs, use_embedding=False)
    kinds = _kinds()
    chosen: dict[str, tuple[str, ...]] = {}
    for kind in KINDS:
        found = tuple(candidate.pack for candidate in accepted if candidate.kind == kind)
        if not found:
            found = tuple(sorted(name for name in packs if kinds.get(name) == kind))[:1]
        chosen[kind] = found
    return SoulStyle(
        patterns=chosen["patterns"],
        facts=chosen["facts"],
        max_patterns=DEFAULT_PATTERNS if chosen["patterns"] else 0,
        max_facts=DEFAULT_FACTS if chosen["facts"] else 0,
    )


def _style_dict(style: SoulStyle) -> dict[str, object]:
    return {
        "patterns": list(style.patterns),
        "facts": list(style.facts),
        "registers": list(style.registers),
        "max_patterns": style.max_patterns,
        "max_facts": style.max_facts,
    }


def resolve_soul(soul_path: Path, *, use_embedding: bool) -> tuple[str, list[Candidate]]:
    """New `soul.yaml` text plus the evidence; other keys are preserved, `style:` is replaced."""
    path = Path(soul_path)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"soul file not found: {path}") from exc
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"soul file is invalid: {exc}") from exc
    raw = raw if isinstance(raw, dict) else {}
    soul = load_soul(raw, source_path=path)
    accepted = suggest(soul_text(soul), packs_for(soul.language), use_embedding=use_embedding)
    existing = {"patterns": soul.style.patterns, "facts": soul.style.facts}
    chosen: dict[str, tuple[str, ...]] = {}
    report: list[Candidate] = []
    for kind in KINDS:
        matched = [candidate for candidate in accepted if candidate.kind == kind]
        chosen[kind] = tuple(candidate.pack for candidate in matched) or existing[kind]
        if matched:
            report.extend(matched)
        else:
            report.extend(
                Candidate(pack, kind, 0.0, "kept: no pack matched")
                for pack in chosen[kind]
            )
    style = SoulStyle(
        patterns=chosen["patterns"],
        facts=chosen["facts"],
        registers=soul.style.registers,
        max_patterns=soul.style.max_patterns
        or (DEFAULT_PATTERNS if chosen["patterns"] else 0),
        max_facts=soul.style.max_facts or (DEFAULT_FACTS if chosen["facts"] else 0),
    )
    raw["style"] = _style_dict(style)
    return yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), report
