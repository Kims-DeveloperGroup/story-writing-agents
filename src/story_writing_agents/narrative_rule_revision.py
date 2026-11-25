"""Exact user selection for a work-wide narrative-rule revision."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .synchronization_assessment import (
    SynchronizationEventRef,
    require_synchronization_digest,
    require_synchronization_text,
    require_synchronization_tuple,
)


class NarrativeRuleRevisionFeature(StrEnum):
    DIALECT = "dialect"
    TONE = "tone"
    SENTENCE_LENGTH = "sentence_length"
    EXPRESSION = "expression"
    POINT_OF_VIEW = "point_of_view"
    NARRATION = "narration"
    EXCEPTIONS = "exceptions"


@dataclass(frozen=True, slots=True)
class NarrativeRuleRevisionSelection:
    """User-approved direction against the current canonical rules bytes."""

    rules_digest: str
    direction: str
    features: tuple[NarrativeRuleRevisionFeature, ...]
    preserve: tuple[str, ...] = ()
    exceptions: tuple[str, ...] = ()
    impact: SynchronizationEventRef | None = None

    def __post_init__(self) -> None:
        require_synchronization_digest(self.rules_digest)
        require_synchronization_text(self.direction)
        require_synchronization_tuple(self.features, NarrativeRuleRevisionFeature)
        if not self.features:
            raise ValueError("a narrative-rule direction requires at least one affected feature")
        for conditions in (self.preserve, self.exceptions):
            require_synchronization_tuple(conditions, str)
            for condition in conditions:
                require_synchronization_text(condition)
        if self.impact is not None and type(self.impact) is not SynchronizationEventRef:
            raise TypeError("narrative-rule revision requires an exact impact event reference")


def narrative_rule_revision_selection_to_dict(
    value: NarrativeRuleRevisionSelection,
) -> dict[str, object]:
    if type(value) is not NarrativeRuleRevisionSelection:
        raise TypeError("narrative-rule revision selection must be exact")
    impact = value.impact
    return {
        "rules_digest": value.rules_digest,
        "direction": value.direction,
        "features": [feature.value for feature in value.features],
        "preserve": list(value.preserve),
        "exceptions": list(value.exceptions),
        "impact": None if impact is None else {
            "conversation_id": impact.conversation_id,
            "event_id": impact.event_id,
            "digest": impact.digest,
        },
    }


def narrative_rule_revision_selection_from_dict(value: object) -> NarrativeRuleRevisionSelection:
    if type(value) is not dict or set(value) != {
        "rules_digest", "direction", "features", "preserve", "exceptions", "impact",
    }:
        raise ValueError("invalid narrative-rule revision selection fields")
    if any(type(value[name]) is not list for name in ("features", "preserve", "exceptions")):
        raise ValueError("narrative-rule revision features and conditions must be arrays")
    impact = value["impact"]
    if impact is not None:
        if type(impact) is not dict or set(impact) != {"conversation_id", "event_id", "digest"}:
            raise ValueError("invalid narrative-rule impact reference fields")
        impact = SynchronizationEventRef(impact["conversation_id"], impact["event_id"], impact["digest"])
    if any(type(feature) is not str for feature in value["features"]):
        raise ValueError("narrative-rule revision feature names must be strings")
    return NarrativeRuleRevisionSelection(
        value["rules_digest"], value["direction"],
        tuple(NarrativeRuleRevisionFeature(feature) for feature in value["features"]),
        tuple(value["preserve"]), tuple(value["exceptions"]), impact,
    )
