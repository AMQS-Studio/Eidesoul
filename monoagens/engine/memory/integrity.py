"""Deterministic provenance, trust, quarantine, and write-volume rules (ADR-0008 §4).

Nothing here calls a model. `MemoryStore` computes these before a canonical insert so the
security metadata cannot drift from the text.
"""


from collections.abc import Mapping
from dataclasses import dataclass

TRUST_SELF = 0  # T0 self_stated
TRUST_OTHER = 1  # T1 stated_about_other
TRUST_INFERENCE = 2  # T2 soul_inference
TRUST_UNLABELED = 3  # T3 unlabeled (level NULL)

TRUST_NAMES = ("T0", "T1", "T2", "T3")

VIA_VALUES = (
    "turn",
    "tool",
    "command",
    "reflection",
    "ambient",
    "automation",
    "soul_doc",
)

# Legacy `source` values map onto `via`; a source never becomes a second column.
SOURCE_VIA = {
    "turn": "turn",
    "tool": "tool",
    "command": "command",
    "reflection": "reflection",
    "ambient": "ambient",
    "automation": "automation",
    "soul_doc": "soul_doc",
}

SELF_SOURCES = ("turn", "tool", "command")
INFERENCE_SOURCES = ("reflection", "ambient", "automation")

RULE_MIN_CORROBORATION_DAYS = 2


def via_of(source: str = "", fallback: str = "turn") -> str:
    """Map a legacy `source` (`ambient:<thread>`) onto the `via` enum; never a second field."""
    raw = str(source or "").strip().lower()
    if raw.startswith("ambient"):
        return "ambient"
    if raw in SOURCE_VIA:
        return SOURCE_VIA[raw]
    return fallback if fallback in VIA_VALUES else "turn"


def trust_for(via: str, owner_id: str, subject_id: str) -> int:
    """ADR-0008 §4.2, deterministic: provenance in, tier out."""
    via = via if via in VIA_VALUES else "turn"
    if via == "soul_doc":
        return TRUST_SELF
    if via in INFERENCE_SOURCES:
        return TRUST_INFERENCE
    if not str(owner_id or "").strip():
        return TRUST_INFERENCE
    subject = str(subject_id or "").strip() or str(owner_id)
    return TRUST_SELF if subject == str(owner_id) else TRUST_OTHER


def quarantine_kind(
    kind: str, *, via: str, trust: int, corroboration_days: int = 0
) -> str:
    """`kind=rule` is born only from `via=soul_doc` + T0 + corroboration ≥ 2 days.

    Any other rule request is downgraded to a sourced fact (ADR-0008 §4.3), never dropped:
    the content survives, its influence on behavior does not.
    """
    wanted = str(kind or "fact").strip().lower()
    if wanted != "rule":
        return wanted or "fact"
    if (
        str(via) == "soul_doc"
        and int(trust) == TRUST_SELF
        and int(corroboration_days) >= RULE_MIN_CORROBORATION_DAYS
    ):
        return "rule"
    return "fact"


def can_govern_behavior(*, kind: str, trust: int, via: str) -> bool:
    """Hard limit: rules/persona/style/tool decisions only from T0 + the soul document."""
    if str(kind) == "rule":
        return int(trust) == TRUST_SELF and str(via) == "soul_doc"
    return int(trust) == TRUST_SELF


@dataclass(frozen=True)
class WriteQuota:
    """ADR-0008 §4.4 A: tiered volume quota per person and per thread."""

    enabled: bool = True
    per_person_hour: int = 20
    per_person_day: int = 60
    per_thread_hour: int = 50
    exempt_categories: tuple[str, ...] = ("health", "safety", "identity", "commitment")
    exempt_via: tuple[str, ...] = ("tool", "command")
    conscious_bypass: bool = True
    burst_trigger: int = 3

    def exempt(
        self,
        *,
        category: str,
        via: str,
        pinned: bool,
        kind: str = "",
        conscious: bool = False,
    ) -> bool:
        if (
            str(category or "") in self.exempt_categories
            or str(kind or "") in self.exempt_categories
            or str(via or "") in self.exempt_via
            or pinned
        ):
            return True
        return bool(conscious and self.conscious_bypass)

    def overflow(
        self,
        *,
        person_hour: int,
        person_day: int,
        thread_hour: int,
    ) -> bool:
        if not self.enabled:
            return False
        return (
            int(person_hour) >= self.per_person_hour
            or int(person_day) >= self.per_person_day
            or int(thread_hour) >= self.per_thread_hour
        )

    def burst(self, *, person_hour: int) -> bool:
        return self.enabled and int(person_hour) >= self.per_person_hour * self.burst_trigger


@dataclass(frozen=True)
class SupersedeVerdict:
    allowed: bool
    reason: str = ""


def supersede_verdict(
    old: Mapping,
    *,
    owner_id: str,
    subject_id: str,
    scope: str,
    kind: str,
    thread_id: str,
    change: str = "contradiction",
    task_status: str | None = None,
) -> SupersedeVerdict:
    """ADR-0008 §4.4 B/B2: prove the replacement before the old row is touched."""
    if str(change or "contradiction") not in {"contradiction", "status_change"}:
        return SupersedeVerdict(False, "unknown supersede reason")
    if str(old.get("state") or "") != "ok":
        return SupersedeVerdict(False, "only an ok memory can be superseded")
    if str(old.get("scope") or "") != str(scope):
        return SupersedeVerdict(False, "supersede must share the scope")
    if str(old.get("kind") or "") != str(kind):
        return SupersedeVerdict(False, "supersede must share the kind")
    if str(old.get("owner_id") or "") != str(owner_id or ""):
        return SupersedeVerdict(False, "supersede must share the same stated_by")
    old_subject = str(old.get("subject_id") or "")
    if old_subject != str(subject_id or ""):
        return SupersedeVerdict(False, "supersede must share the same about")
    if str(scope) == "thread" and str(old.get("thread_id") or "") != str(thread_id or ""):
        return SupersedeVerdict(False, "another thread's memory cannot be superseded")
    if bool(old.get("pinned")) and old_subject != str(owner_id or ""):
        return SupersedeVerdict(False, "a Tier 1 row cannot be overwritten by another party")
    if str(change) == "status_change" and (
        str(kind) != "commitment" or not str(task_status or "").strip()
    ):
        return SupersedeVerdict(False, "a status change needs a commitment and its new status")
    return SupersedeVerdict(True)
