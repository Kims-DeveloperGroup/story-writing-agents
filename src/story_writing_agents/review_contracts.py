"""Immutable review facts. Qualification and all authority stay with runtime owners."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import hashlib
import json


class ReviewTargetKind(StrEnum):
    USER_PROVIDED = "user_provided"
    WRITING_ARTIFACT = "writing_artifact"
    CANDIDATE = "candidate"
    READ_SOURCE = "read_source"



class ReviewTargetAvailability(StrEnum):
    SUPPLIED = "supplied"
    COMPLETED_READ = "completed_read"
    UNREAD = "unread"


class PreliminaryNextAction(StrEnum):
    FINISH_DIRECT = "finish_direct"
    REQUEST_INFORMATION = "request_information"
    PROPOSE_SPECIALIST_WORK = "propose_specialist_work"
    PROPOSE_CHANGE_TASK = "propose_change_task"


def _text(value: object, name: str, *, maximum: int = 262144) -> str:
    if type(value) is not str or not value.strip() or len(value.encode("utf-8")) > maximum:
        raise ValueError(f"{name} requires bounded nonempty text")
    return value


def _texts(values: object, name: str, *, unique: bool = True) -> tuple[str, ...]:
    if type(values) is not tuple or len(values) > 256:
        raise ValueError(f"{name} requires a bounded exact tuple")
    for value in values:
        _text(value, name)
    if unique and len(set(values)) != len(values):
        raise ValueError(f"{name} must be unique")
    return values


def review_digest(value: object) -> str:
    """Digest one source-authored, content-complete review value."""
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        allow_nan=False, separators=(",", ":")).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ReviewTarget:
    input_id: str
    kind: ReviewTargetKind
    target_id: str
    revision: str | None
    content_digest: str | None
    source_digest: str | None
    boundary_digest: str | None
    whole_source: bool
    boundary_kind: str = "whole"
    boundary_start: int | None = None
    boundary_end: int | None = None
    availability: ReviewTargetAvailability = field(default=ReviewTargetAvailability.SUPPLIED, kw_only=True)
    source_status: str | None = field(default=None, kw_only=True)
    source_manifest_digest: str | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        for name in ("input_id", "target_id", "boundary_kind"):
            _text(getattr(self, name), name, maximum=4096)
        if type(self.kind) is not ReviewTargetKind or type(self.whole_source) is not bool:
            raise ValueError("review target requires exact kind and coverage")
        if type(self.availability) is not ReviewTargetAvailability:
            raise TypeError("review availability must be host-issued")
        for name in ("revision", "content_digest", "source_digest", "boundary_digest"):
            value = getattr(self, name)
            if value is not None or self.availability is not ReviewTargetAvailability.UNREAD:
                _text(value, name, maximum=4096)
        if self.availability is ReviewTargetAvailability.UNREAD:
            if (self.whole_source or self.content_digest is not None or self.boundary_digest is not None
                    or self.boundary_kind != "unread" or self.boundary_start is not None or self.boundary_end is not None
                    or self.source_status not in {"present", "empty", "absent"}):
                raise ValueError("unread status is metadata, never inspection evidence")
            _text(self.source_manifest_digest, "status manifest digest", maximum=4096)
        elif self.source_status is not None or self.source_manifest_digest is not None:
            raise ValueError("status provenance belongs only to unread metadata")
        if (self.boundary_start is None) != (self.boundary_end is None):
            raise ValueError("review target boundary needs both endpoints")
        if self.boundary_start is not None and (
                type(self.boundary_start) is not int or type(self.boundary_end) is not int
                or self.boundary_start < 0 or self.boundary_end < self.boundary_start):
            raise ValueError("invalid review target boundary")
        if self.whole_source and self.source_digest != self.boundary_digest:
            raise ValueError("whole review target must bind the whole source digest")


def review_target_to_dict(value: ReviewTarget) -> dict[str, object]:
    if type(value) is not ReviewTarget:
        raise TypeError("expected exact review target")
    return {name: getattr(value, name) for name in
            ("input_id", "target_id", "revision", "content_digest", "source_digest", "boundary_digest",
             "whole_source", "boundary_kind", "boundary_start", "boundary_end", "source_status",
             "source_manifest_digest")} | {"kind": value.kind.value, "availability": value.availability.value}


def review_target_from_dict(value: object) -> ReviewTarget:
    names = {"input_id", "kind", "target_id", "revision", "content_digest", "source_digest",
             "boundary_digest", "whole_source", "boundary_kind", "boundary_start", "boundary_end",
             "availability", "source_status", "source_manifest_digest"}
    if type(value) is not dict or set(value) != names:
        raise ValueError("review target requires its complete declared record")
    return ReviewTarget(**{**value, "kind": ReviewTargetKind(value["kind"]),
                           "availability": ReviewTargetAvailability(value["availability"])})


@dataclass(frozen=True, slots=True)
class ReviewFinding:
    message: str
    evidence_input_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _text(self.message, "review finding")
        _texts(self.evidence_input_ids, "finding evidence")
        if not self.evidence_input_ids:
            raise ValueError("a finding requires actual inspected evidence")


@dataclass(frozen=True, slots=True)
class LeadPreliminaryReview:
    task_id: str
    task_revision: int
    targets: tuple[ReviewTarget, ...]
    inspected_input_ids: tuple[str, ...]
    excluded_input_ids: tuple[str, ...]
    findings: tuple[ReviewFinding, ...]
    conflicts: tuple[ReviewFinding, ...]
    missing_information: tuple[str, ...]
    further_review_required: bool
    next_action: PreliminaryNextAction
    stage: str = "lead_preliminary_review"

    def __post_init__(self) -> None:
        _text(self.task_id, "preliminary task", maximum=4096)
        if type(self.task_revision) is not int or self.task_revision < 1:
            raise ValueError("preliminary review requires exact current task revision")
        if self.stage != "lead_preliminary_review":
            raise ValueError("preliminary review cannot acquire another stage")
        if type(self.targets) is not tuple or not self.targets or len(self.targets) > 256:
            raise ValueError("preliminary review requires bounded exact targets")
        if any(type(item) is not ReviewTarget for item in self.targets):
            raise TypeError("preliminary targets must be exact qualified values")
        target_ids = tuple(item.input_id for item in self.targets)
        if len(set(target_ids)) != len(target_ids):
            raise ValueError("preliminary target IDs must be unique")
        inspected = _texts(self.inspected_input_ids, "inspected inputs")
        excluded = _texts(self.excluded_input_ids, "excluded inputs")
        if any(item.input_id in inspected and item.availability is ReviewTargetAvailability.UNREAD for item in self.targets):
            raise ValueError("unread metadata cannot be claimed as inspected")
        if set(inspected) & set(excluded) or set(inspected) | set(excluded) != set(target_ids):
            raise ValueError("inspected and excluded scope must partition exact targets")
        for rows in (self.findings, self.conflicts):
            if type(rows) is not tuple or len(rows) > 256 or any(type(x) is not ReviewFinding for x in rows):
                raise ValueError("preliminary findings require bounded exact records")
            if any(not set(x.evidence_input_ids).issubset(inspected) for x in rows):
                raise ValueError("findings may cite only genuinely inspected input IDs")
        if any(len(row.evidence_input_ids) < 2 for row in self.conflicts):
            raise ValueError("an explicit conflict requires both inspected sources")
        _texts(self.missing_information, "missing information", unique=False)
        if type(self.further_review_required) is not bool or type(self.next_action) is not PreliminaryNextAction:
            raise ValueError("preliminary review requires exact follow-up declarations")
        if excluded and not (self.missing_information or self.further_review_required):
            raise ValueError("uninspected scope requires an explicit limitation")

    @property
    def whole_target_coverage(self) -> bool:
        """Report only complete covered targets, never an unread-source inference."""
        return (not self.excluded_input_ids and not self.missing_information
                and all(item.whole_source and item.boundary_kind == "whole"
                        and item.availability is not ReviewTargetAvailability.UNREAD
                        for item in self.targets))


def preliminary_review_to_dict(value: LeadPreliminaryReview) -> dict[str, object]:
    if type(value) is not LeadPreliminaryReview:
        raise TypeError("expected exact preliminary review")
    finding = lambda x: {"message": x.message, "evidence_input_ids": list(x.evidence_input_ids)}
    return {"stage": value.stage, "task_id": value.task_id, "task_revision": value.task_revision,
        "targets": [review_target_to_dict(x) for x in value.targets],
        "inspected_input_ids": list(value.inspected_input_ids),
        "excluded_input_ids": list(value.excluded_input_ids),
        "findings": [finding(x) for x in value.findings],
        "conflicts": [finding(x) for x in value.conflicts],
        "missing_information": list(value.missing_information),
        "further_review_required": value.further_review_required, "next_action": value.next_action.value}


def preliminary_review_from_dict(value: object) -> LeadPreliminaryReview:
    names = {"stage", "task_id", "task_revision", "targets", "inspected_input_ids",
        "excluded_input_ids", "findings", "conflicts", "missing_information",
        "further_review_required", "next_action"}
    if type(value) is not dict or set(value) != names:
        raise ValueError("preliminary review requires its complete declared record")
    for name in ("targets", "inspected_input_ids", "excluded_input_ids", "findings", "conflicts", "missing_information"):
        if type(value[name]) is not list:
            raise ValueError("preliminary collection must be an exact list")
    def finding(x: object) -> ReviewFinding:
        if type(x) is not dict or set(x) != {"message", "evidence_input_ids"} or type(x["evidence_input_ids"]) is not list:
            raise ValueError("invalid preliminary finding")
        return ReviewFinding(x["message"], tuple(x["evidence_input_ids"]))
    return LeadPreliminaryReview(value["task_id"], value["task_revision"],
        tuple(review_target_from_dict(x) for x in value["targets"]),
        tuple(value["inspected_input_ids"]), tuple(value["excluded_input_ids"]),
        tuple(finding(x) for x in value["findings"]), tuple(finding(x) for x in value["conflicts"]),
        tuple(value["missing_information"]), value["further_review_required"],
        PreliminaryNextAction(value["next_action"]), value["stage"])


@dataclass(frozen=True, slots=True)
class FinalReviewEvidence:
    candidate_id: str
    candidate_revision: int
    candidate_digest: str
    task_id: str
    task_revision: int
    sources: tuple[ReviewTarget, ...]
    user_conditions_digest: str
    scope_digest: str
    criteria_digest: str
    reviewer_ids: tuple[str, ...]
    reply_event_ids: tuple[str, ...]
    stage: str = "final_candidate_review"

    def __post_init__(self) -> None:
        for name in ("candidate_id", "candidate_digest", "task_id", "user_conditions_digest", "scope_digest", "criteria_digest"):
            _text(getattr(self, name), name, maximum=4096)
        if any(type(x) is not int or x < 1 for x in (self.candidate_revision, self.task_revision)):
            raise ValueError("final evidence requires exact candidate/task revisions")
        if self.stage != "final_candidate_review" or type(self.sources) is not tuple or any(type(x) is not ReviewTarget for x in self.sources):
            raise ValueError("final evidence requires exact source-qualified final stage")
        if len(self.sources) > 256 or len({x.input_id for x in self.sources}) != len(self.sources):
            raise ValueError("final evidence source vector must be bounded and unique")
        _texts(self.reviewer_ids, "final reviewers"); _texts(self.reply_event_ids, "final replies")
        if not self.reviewer_ids or len(self.reviewer_ids) != len(self.reply_event_ids):
            raise ValueError("final reviewers must have distinct correlated replies")
        if any(item.availability is ReviewTargetAvailability.UNREAD for item in self.sources):
            raise ValueError("preliminary unread metadata cannot qualify final review")

    @property
    def evidence_digest(self) -> str:
        return review_digest(final_review_evidence_to_dict(self))


def final_review_evidence_to_dict(value: FinalReviewEvidence) -> dict[str, object]:
    if type(value) is not FinalReviewEvidence:
        raise TypeError("expected host-qualified final evidence value")
    return {"stage": value.stage, "candidate_id": value.candidate_id,
        "candidate_revision": value.candidate_revision, "candidate_digest": value.candidate_digest,
        "task_id": value.task_id, "task_revision": value.task_revision,
        "sources": [review_target_to_dict(x) for x in value.sources],
        "user_conditions_digest": value.user_conditions_digest, "scope_digest": value.scope_digest,
        "criteria_digest": value.criteria_digest, "reviewer_ids": list(value.reviewer_ids),
        "reply_event_ids": list(value.reply_event_ids)}


def final_review_evidence_from_dict(value: object) -> FinalReviewEvidence:
    keys = {"stage", "candidate_id", "candidate_revision", "candidate_digest", "task_id",
        "task_revision", "sources", "user_conditions_digest", "scope_digest", "criteria_digest",
        "reviewer_ids", "reply_event_ids"}
    if type(value) is not dict or set(value) != keys or any(type(value[x]) is not list for x in ("sources", "reviewer_ids", "reply_event_ids")):
        raise ValueError("final review requires its complete declared record")
    return FinalReviewEvidence(value["candidate_id"], value["candidate_revision"], value["candidate_digest"],
        value["task_id"], value["task_revision"], tuple(review_target_from_dict(x) for x in value["sources"]),
        value["user_conditions_digest"], value["scope_digest"], value["criteria_digest"],
        tuple(value["reviewer_ids"]), tuple(value["reply_event_ids"]), value["stage"])


@dataclass(frozen=True, slots=True)
class FinalReviewQualification:
    eligible: bool
    reasons: tuple[str, ...]
    evidence_digest: str


def qualify_final_review(evidence: FinalReviewEvidence, current: FinalReviewEvidence,
                         *, current_read_applicable: bool) -> FinalReviewQualification:
    """Compare a physical review basis; a new READ alias is not source drift."""
    if type(evidence) is not FinalReviewEvidence or type(current) is not FinalReviewEvidence:
        raise TypeError("final qualification requires two exact final review records")
    if type(current_read_applicable) is not bool:
        raise TypeError("current READ applicability must be a host-qualified boolean")
    reasons = []
    groups = (("candidate_changed", ("candidate_id", "candidate_revision", "candidate_digest")),
        ("task_changed", ("task_id", "task_revision")),
        ("user_conditions_changed", ("user_conditions_digest",)),
        ("scope_changed", ("scope_digest",)), ("criteria_changed", ("criteria_digest",)),
        ("reviewer_changed", ("reviewer_ids", "reply_event_ids")))
    for reason, fields in groups:
        if any(getattr(evidence, name) != getattr(current, name) for name in fields):
            reasons.append(reason)
    def physical_sources(record):
        values = []
        for target in record.sources:
            value = review_target_to_dict(target)
            del value["input_id"]
            values.append(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")))
        return tuple(sorted(values))
    if physical_sources(evidence) != physical_sources(current):
        reasons.append("source_changed")
    if not current_read_applicable:
        reasons.append("current_read_inapplicable")
    return FinalReviewQualification(not reasons, tuple(reasons), evidence.evidence_digest)


def preliminary_scope_eligible(activity: str, target_paths: tuple[str, ...],
                               targets: tuple[ReviewTarget, ...]) -> bool:
    """One scope law for the native arm and the typed preliminary judgment."""
    if activity != "review" or not targets:
        return False
    paths = {item.target_id for item in targets
             if item.kind is ReviewTargetKind.WRITING_ARTIFACT}
    return set(target_paths).issubset(paths)


def preliminary_intent_schema(targets: tuple[ReviewTarget, ...]) -> dict[str, object]:
    """Owner declaration; identity/digests are captured by the host, never minted by output."""
    identities = tuple(item.input_id for item in targets if item.availability is not ReviewTargetAvailability.UNREAD)
    def obj(properties):
        return {"type": "object", "properties": properties, "required": list(properties),
                "additionalProperties": False}
    def text():
        return {"type": "string", "minLength": 1, "maxLength": 262144}
    def refs():
        return {"type": "array", "items": {"type": "string", "enum": list(identities)},
                "minItems": 1, "maxItems": len(identities), "uniqueItems": True}
    finding = obj({"message": text(), "evidence_input_ids": refs()})
    return obj({"stage": {"type": "string", "enum": ["lead_preliminary_review"]},
        "inspection": {**obj({target.input_id: {"type": "string", "description": f"Host target {target.target_id}; availability={target.availability.value}; boundary={target.boundary_kind}. Unread targets in the declared scope are excluded; inventory does not create scope.", "enum": (["excluded"] if target.availability is ReviewTargetAvailability.UNREAD else ["inspected", "excluded"])} for target in targets}), "x-swa-property-policy": "closed-map"},
        "findings": {"type": "array", "items": finding, "maxItems": 256},
        "conflicts": {"type": "array", "items": finding, "maxItems": 256},
        "missing_information": {"type": "array", "items": text(), "maxItems": 256},
        "further_review_required": {"type": "boolean"},
        "next_action": {"type": "string", "enum": [item.value for item in PreliminaryNextAction]}})


def scoped_preliminary_targets(targets: tuple[ReviewTarget, ...], target_paths: tuple[str, ...]) -> tuple[ReviewTarget, ...]:
    """Coverage metadata describes declared targets, never creates a review scope."""
    return tuple(item for item in targets if item.availability is not ReviewTargetAvailability.UNREAD or item.target_id in target_paths)


def bind_preliminary_intent(value: object, targets: tuple[ReviewTarget, ...],
                            task_id: str, task_revision: int, *, target_paths: tuple[str, ...]) -> LeadPreliminaryReview:
    if type(value) is not dict or set(value) != {"stage", "inspection", "findings",
            "conflicts", "missing_information", "further_review_required", "next_action"}:
        raise ValueError("preliminary review requires its complete current declared record")
    inspection = value["inspection"]
    if (type(inspection) is not dict or set(inspection) != {t.input_id for t in targets}
            or any(type(v) is not str or v not in {"inspected", "excluded"}
                   for v in inspection.values())):
        raise ValueError("preliminary inspection must cover the exact offered targets")
    def findings(name):
        rows = value[name]
        if type(rows) is not list:
            raise ValueError("preliminary findings require an array")
        result = []
        for row in rows:
            if (type(row) is not dict or set(row) != {"message", "evidence_input_ids"}
                    or type(row["evidence_input_ids"]) is not list):
                raise ValueError("finding requires exact inspected evidence")
            result.append(ReviewFinding(row["message"], tuple(row["evidence_input_ids"])))
        return tuple(result)
    if type(value["missing_information"]) is not list:
        raise ValueError("missing information requires an array")
    targets = scoped_preliminary_targets(targets, target_paths)
    return LeadPreliminaryReview(task_id, task_revision, targets,
        tuple(t.input_id for t in targets if inspection[t.input_id] == "inspected"),
        tuple(t.input_id for t in targets if inspection[t.input_id] == "excluded"),
        findings("findings"), findings("conflicts"), tuple(value["missing_information"]),
        value["further_review_required"], PreliminaryNextAction(value["next_action"]), value["stage"])
