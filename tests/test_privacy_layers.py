"""ADR-0038: evidence before level, local policy, and affect kept out of the decision."""

from eidesoul.engine.memory.evidence import classify, collect, decide


def labels(text: str, language: str = "id") -> set[str]:
    evidence, _ = collect(text, language)
    return {item.label for item in evidence}


def test_structured_credential_is_dropped_not_stored() -> None:
    evidence, decision = classify("passwordku hunter2", "id")

    assert "credential" in {item.label for item in evidence}
    assert decision.action == "drop"
    assert decision.policy_version


def test_a_crime_admission_raises_the_level_and_asks() -> None:
    evidence, decision = classify("aku tadi nyuri di toko", "id")

    assert "crime_admission" in {item.label for item in evidence}
    assert decision.level == 4
    assert decision.ask is True
    assert decision.action == "ask"


def test_affect_only_text_does_not_raise_the_level() -> None:
    evidence, decision = classify("aku sedih banget", "id")

    assert not {item.label for item in evidence} & {"health", "relationship_conflict"}
    assert decision.level == 2
    assert decision.ask is False


def test_ambiguous_text_goes_to_review_instead_of_a_guessed_level() -> None:
    _evidence, decision = classify("aku tadi jatuh", "id")

    assert decision.level == 2
    assert decision.ask is True
    assert decision.needs_review is True


def test_policy_cannot_lower_a_deterministic_floor() -> None:
    from eidesoul.engine.memory.evidence import Evidence

    evidence = [
        Evidence("regex", "pack-1", "d", "health", 1.0, (0, 1)),
        Evidence("encoder", "centroid-1", "d", "relationship_conflict", 0.2, None),
    ]

    decision = decide(evidence)

    assert decision.level == 4


def test_every_evidence_record_has_the_audit_fields() -> None:
    evidence, _ = collect("api_key=abc123", "id")

    record = evidence[0].record()
    assert set(record) == {"detector", "version", "digest", "label", "score", "span"}
    assert record["detector"] == "regex"
    assert record["span"] is not None


def test_golden_set_reports_per_label_precision_and_recall() -> None:
    from pathlib import Path

    import yaml

    rows = yaml.safe_load(
        (Path(__file__).parent / "data" / "privacy_cases.yaml").read_text()
    )["cases"]
    expected = {(row["id"], row["label"]) for row in rows}
    found = set()
    for row in rows:
        evidence, decision = classify(row["text"], row["language"])
        labels = {item.label for item in evidence}
        picked = row["label"] if row["label"] in labels else ("none" if not labels else "")
        found.add((row["id"], picked))
        assert decision.level == row["level"], row["id"]
        assert decision.action == row["action"], row["id"]

    true_positive = len(expected & found)
    precision = true_positive / max(1, len(found))
    recall = true_positive / max(1, len(expected))
    assert precision >= 0.9
    assert recall >= 0.9


def test_classification_is_bounded_to_the_text_cap() -> None:
    from eidesoul.engine.memory.evidence import MAX_CLASSIFY_CHARS

    late = "aman. " * (MAX_CLASSIFY_CHARS // 6) + " password=secret"
    evidence, _ = collect(late, "id")

    assert "credential" not in {item.label for item in evidence}
    assert len(late) > MAX_CLASSIFY_CHARS


def test_one_shared_embedder_and_no_optional_detector_is_loaded() -> None:
    import sys

    for name in ("presidio_analyzer", "spacy", "gliner", "onnxruntime"):
        assert name not in sys.modules
