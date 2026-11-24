"""Closed Lead wire values for unqualified semantic impact judgments."""

from dataclasses import dataclass, fields
from enum import StrEnum

from ..conversations.event_value_validation import validate_identifier, validate_visible_text
from ..lead_provider_contracts import nonempty_text_schema
from ..specialist_routing import ArtifactReviewDomain
from ..structured_output_validation import validation_call, validation_failure
from ..writing_artifacts.catalog import canonical_artifact_kind
from .reply_contract_values import closed_result_object, string_array_result_schema


class SynchronizationReviewJudgment(StrEnum):
    NO_CHANGE = "no_change"
    NOT_APPLICABLE = "not_applicable"
    NEEDS_CHANGE = "needs_change"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class SynchronizationReviewFinding:
    """One model judgment with unverified references to reads and reviews."""

    changed_path: str
    target_path: str
    judgment: SynchronizationReviewJudgment
    reason: str
    evidence_ids: tuple[str, ...]
    review_event_ids: tuple[str, ...]
    domain: ArtifactReviewDomain | None
    action: str | None
    resolution: str | None
    abstraction: str | None
    resolves: tuple[str, ...]


_FINDING_FIELDS = frozenset(item.name for item in fields(SynchronizationReviewFinding))


def synchronization_findings_schema(reply_event_ids: tuple[str, ...]) -> dict[str, object]:
    """Declare the wire field without embedding the full artifact inventory."""

    def optional_text(description: str) -> dict[str, object]:
        return {"anyOf": [
            {**nonempty_text_schema(), "description": description},
            {"type": "null", "description": "This value does not apply to the judgment."},
        ]}

    return {
        "type": "array",
        "minItems": 0,
        "items": closed_result_object({
            "changed_path": {**nonempty_text_schema(), "description": "Exact changed path from the given impact targets."},
            "target_path": {**nonempty_text_schema(), "description": "Exact affected path from the same impact pair."},
            "judgment": {"type": "string", "enum": [item.value for item in SynchronizationReviewJudgment]},
            "reason": {**nonempty_text_schema(), "description": "State the concrete discrepancy, lack of impact, or evidence gap."},
            "evidence_ids": {
                **string_array_result_schema(),
                "description": "Cite actual delivered READ or disclosed source receipt IDs for the judgment. Use unknown when the material was not read.",
            },
            "review_event_ids": {
                **string_array_result_schema(allowed_values=reply_event_ids, max_items=len(reply_event_ids)),
                "description": "Cite exact specialist replies that support this specific judgment. An unrelated or unresolved review does not establish impact.",
            },
            "domain": {"anyOf": [
                {"type": "string", "enum": [item.value for item in ArtifactReviewDomain],
                 "description": "The specialist domain responsible for the impact."},
                {"type": "null", "description": "No responsible specialist domain was identified."},
            ], "description": "Set the responsible specialist domain for needs_change."},
            "action": optional_text("For needs_change, specify what the responsible specialist should correct or assess."),
            "resolution": optional_text("For needs_change, give the observable condition and review needed to verify resolution."),
            "abstraction": optional_text("For not_applicable, explain the target's abstraction level and why the detail belongs elsewhere."),
            "resolves": {
                **string_array_result_schema(),
                "description": "For no_change or not_applicable, cite only disclosed condition IDs addressed by this pair in the exact state under review.",
            },
        }),
        "description": (
            "Assess each supplied changed/affected path pair once. A current source and technical structure check do not establish semantic consistency. "
            "Use no_change when the materials support consistency without an edit. Use not_applicable for a supported lack of impact at the target's abstraction level. "
            "Use needs_change for a grounded inconsistency, or unknown for gaps. Cover effects across outlines, character, style, and manuscript material without copying irrelevant prose details. "
            "Leave unassessed pairs unknown. Do not infer missing files are ready. Runtime validates each judgment's evidence separately."
        ),
    }


def decode_synchronization_findings(
    value: object, *, reply_event_ids: tuple[str, ...],
) -> tuple[SynchronizationReviewFinding, ...]:
    if type(value) is not list:
        raise validation_failure(TypeError(), "$", "expected an array of synchronization findings")
    offered_replies = frozenset(reply_event_ids)
    findings = tuple(
        validation_call(f"$[{index}]", _decode_finding, row, offered_replies=offered_replies)
        for index, row in enumerate(value)
    )
    if len({(row.changed_path, row.target_path) for row in findings}) != len(findings):
        raise validation_failure(ValueError(), "$", "report each affected path pair no more than once")
    return findings


def _decode_finding(row: object, *, offered_replies: frozenset[str]) -> SynchronizationReviewFinding:
    if type(row) is not dict or row.keys() != _FINDING_FIELDS:
        raise validation_failure(ValueError(), "$", "expected the closed synchronization finding fields")
    changed_path = _path(row["changed_path"], "changed_path")
    target_path = _path(row["target_path"], "target_path")
    if changed_path == target_path:
        raise validation_failure(ValueError(), "$.target_path", "affected path must differ from the changed path")
    judgment = validation_call("$.judgment", SynchronizationReviewJudgment, row["judgment"])
    reason = _text(row["reason"], "reason")
    evidence_ids = _identifiers(row["evidence_ids"], "evidence_ids")
    review_ids = _identifiers(row["review_event_ids"], "review_event_ids")
    resolves = _identifiers(row["resolves"], "resolves")
    domain = (
        None if row["domain"] is None
        else validation_call("$.domain", ArtifactReviewDomain, row["domain"])
    )
    action = _optional_text(row["action"], "action")
    resolution = _optional_text(row["resolution"], "resolution")
    abstraction = _optional_text(row["abstraction"], "abstraction")
    if not offered_replies.issuperset(review_ids):
        raise validation_failure(ValueError(), "$.review_event_ids", "use only the disclosed specialist reply IDs")
    if judgment is not SynchronizationReviewJudgment.UNKNOWN and not evidence_ids:
        raise validation_failure(ValueError(), "$.evidence_ids", "a semantic judgment needs current evidence")
    if judgment is SynchronizationReviewJudgment.NEEDS_CHANGE and None in (domain, action, resolution):
        raise validation_failure(ValueError(), "$", "needs_change requires a domain, action and resolution condition")
    if judgment is SynchronizationReviewJudgment.NOT_APPLICABLE and abstraction is None:
        raise validation_failure(ValueError(), "$.abstraction", "not_applicable requires a concrete abstraction explanation")
    if resolves and judgment not in {SynchronizationReviewJudgment.NO_CHANGE, SynchronizationReviewJudgment.NOT_APPLICABLE}:
        raise validation_failure(ValueError(), "$.resolves", "use condition IDs only for no_change or not_applicable")
    return SynchronizationReviewFinding(
        changed_path, target_path, judgment, reason, evidence_ids, review_ids,
        domain, action, resolution, abstraction, resolves,
    )


def _path(value: object, label: str) -> str:
    try:
        if type(value) is str and canonical_artifact_kind(value) is not None:
            return value
    except (TypeError, ValueError) as failure:
        raise validation_failure(failure, f"$.{label}", "expected one exact canonical writing path")
    raise validation_failure(ValueError(), f"$.{label}", "expected one exact canonical writing path")


def _text(value: object, label: str) -> str:
    if type(value) is not str or not value.strip() or "\x00" in value:
        raise validation_failure(ValueError(), f"$.{label}", "expected nonblank text")
    return validation_call(f"$.{label}", validate_visible_text, value, label=label)


def _optional_text(value: object, label: str) -> str | None:
    return None if value is None else _text(value, label)


def _identifiers(value: object, label: str) -> tuple[str, ...]:
    if type(value) is not list:
        raise validation_failure(TypeError(), f"$.{label}", "expected an array of exact IDs")
    ids = tuple(
        validation_call(f"$.{label}[{index}]", validate_identifier, item, label=label)
        for index, item in enumerate(value)
    )
    if len(set(ids)) != len(ids):
        raise validation_failure(ValueError(), f"$.{label}", "each cited ID must be unique")
    return ids
