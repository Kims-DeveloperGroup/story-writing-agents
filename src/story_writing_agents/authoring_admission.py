"""Pure, source-bound admission for official authoring, never write authority.

The host proves source identity and freshness. An assessment is a separate
semantic judgment, not proof of a READ or of literary adequacy.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import StrEnum


class AdmissionStatus(StrEnum):
    SUFFICIENT = "sufficient"
    NEEDS_INFORMATION = "needs_information"
    ANALYSIS_ONLY = "analysis_only"


class AdmissionSourceKind(StrEnum):
    USER = "user"
    MATERIAL = "material"
    READ = "read"


class DecisionCoverage(StrEnum):
    SUPPLIED = "supplied"
    DELEGATED = "delegated"
    MISSING = "missing"


def _text(value: object, name: str, maximum: int = 4096) -> None:
    if type(value) is not str or not value.strip() or len(value.encode("utf-8")) > maximum:
        raise ValueError(f"authoring admission {name} must be bounded nonempty text")


def _strings(value: object, name: str, *, nonempty: bool = False) -> None:
    if type(value) is not tuple or len(value) > 32 or (nonempty and not value):
        raise ValueError(f"authoring admission {name} must be a bounded tuple")
    for item in value:
        _text(item, name)
    if len(set(value)) != len(value):
        raise ValueError(f"authoring admission {name} contains duplicates")


def _digest(value: object) -> None:
    if type(value) is not str or re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
        raise ValueError("authoring admission digest is invalid")


def admission_digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class AdmissionSource:
    """Host-created exact input/READ fact; construction alone grants nothing."""

    source_id: str
    kind: AdmissionSourceKind
    source_identity: str
    revision_digest: str
    original_authority: str
    utf8_bytes: int | None = None

    def __post_init__(self) -> None:
        _text(self.source_id, "source ID", 256)
        _text(self.source_identity, "source identity")
        _digest(self.revision_digest)
        if self.utf8_bytes is not None and (type(self.utf8_bytes) is not int or self.utf8_bytes < 0):
            raise ValueError("authoring admission selected-byte length is invalid")
        if type(self.kind) is not AdmissionSourceKind:
            raise TypeError("authoring admission source kind is invalid")
        allowed = {
            AdmissionSourceKind.USER: {"literal_user"},
            AdmissionSourceKind.MATERIAL: {"input_only", "rejected_public_response"},
            AdmissionSourceKind.READ: {"actual_read"},
        }
        if self.original_authority not in allowed[self.kind]:
            raise ValueError("authoring admission source authority is invalid")


@dataclass(frozen=True, slots=True)
class AdmissionEvidence:
    requirement: str
    source_ids: tuple[str, ...]
    justification: str

    def __post_init__(self) -> None:
        _text(self.requirement, "requirement", 256)
        _strings(self.source_ids, "supporting sources", nonempty=True)
        _text(self.justification, "evidence justification")


@dataclass(frozen=True, slots=True)
class AdmissionDecision:
    decision: str
    coverage: DecisionCoverage
    source_ids: tuple[str, ...]
    explanation: str

    def __post_init__(self) -> None:
        _text(self.decision, "decision", 256)
        if type(self.coverage) is not DecisionCoverage:
            raise TypeError("authoring decision coverage is invalid")
        _strings(self.source_ids, "decision sources")
        if self.coverage is not DecisionCoverage.MISSING and not self.source_ids:
            raise ValueError("supplied/delegated decision needs actual source references")
        _text(self.explanation, "decision explanation")


@dataclass(frozen=True, slots=True)
class AuthoringAssessment:
    """Untrusted semantic task input, qualified against host-created facts."""

    status: AdmissionStatus
    foundations: tuple[AdmissionEvidence, ...]
    direction: AdmissionEvidence | None
    creative_scope: AdmissionEvidence | None
    allowed_creative_actions: tuple[str, ...]
    protected_constraints: tuple[str, ...]
    decisions: tuple[AdmissionDecision, ...]
    missing_information: tuple[str, ...]
    rationale: str

    def __post_init__(self) -> None:
        if type(self.status) is not AdmissionStatus:
            raise TypeError("authoring assessment status is invalid")
        for values, cls in ((self.foundations, AdmissionEvidence),
                            (self.decisions, AdmissionDecision)):
            if type(values) is not tuple or len(values) > 32 or any(type(v) is not cls for v in values):
                raise TypeError("authoring assessment evidence/decisions are invalid")
        for item in (self.direction, self.creative_scope):
            if item is not None and type(item) is not AdmissionEvidence:
                raise TypeError("authoring direction/creative scope is invalid")
        for values in (self.allowed_creative_actions, self.protected_constraints,
                       self.missing_information):
            _strings(values, "assessment statements")
        _text(self.rationale, "assessment rationale")
        if len({v.requirement for v in self.foundations}) != len(self.foundations):
            raise ValueError("authoring foundation requirements repeat")
        if len({v.decision for v in self.decisions}) != len(self.decisions):
            raise ValueError("authoring decision requirements repeat")


@dataclass(frozen=True, slots=True)
class AuthoringAdmissionNeeded:
    """Provider-free actionable result; no proposal or confirmation authority."""

    status: AdmissionStatus
    questions: tuple[str, ...]
    offered_sources: tuple[AdmissionSource, ...] = ()

    def __post_init__(self) -> None:
        if type(self.status) is not AdmissionStatus or self.status is AdmissionStatus.SUFFICIENT:
            raise ValueError("admission-needed result cannot be sufficient")
        _strings(self.questions, "admission questions", nonempty=True)
        if type(self.offered_sources) is not tuple or len(self.offered_sources) > 64:
            raise ValueError("admission-needed offered sources are invalid")
        if any(type(v) is not AdmissionSource for v in self.offered_sources):
            raise TypeError("admission-needed offered sources are invalid")

    @property
    def available_source_ids(self) -> tuple[str, ...]:
        return tuple(v.source_id for v in self.offered_sources)

    @property
    def message(self) -> str:
        return "Before official drafting: " + " ".join(self.questions)


@dataclass(frozen=True, slots=True)
class AuthoringAdmissionBasis:
    request_digest: str
    task_id: str
    task_revision: int
    scope_digest: str
    sources: tuple[AdmissionSource, ...]
    assessment: AuthoringAssessment

    def __post_init__(self) -> None:
        _digest(self.request_digest)
        _digest(self.scope_digest)
        _text(self.task_id, "task identity", 256)
        if type(self.task_revision) is not int or self.task_revision < 1:
            raise ValueError("authoring admission task revision is invalid")
        if type(self.sources) is not tuple or not self.sources or len(self.sources) > 64:
            raise ValueError("authoring admission source snapshot is invalid")
        if any(type(v) is not AdmissionSource for v in self.sources):
            raise TypeError("authoring admission source snapshot is invalid")
        if len({v.source_id for v in self.sources}) != len(self.sources):
            raise ValueError("authoring admission source IDs repeat")
        if type(self.assessment) is not AuthoringAssessment:
            raise TypeError("authoring admission assessment is invalid")
        needed = _assessment_gaps(self.assessment, self.sources)
        if needed:
            raise ValueError("authoring admission basis is not sufficient")

    @property
    def digest(self) -> str:
        return admission_digest(admission_basis_to_dict(self))


def _assessment_gaps(assessment: AuthoringAssessment,
                     sources: tuple[AdmissionSource, ...]) -> tuple[str, ...]:
    available = {v.source_id: v for v in sources}
    evidence = (*assessment.foundations,
                *((assessment.direction,) if assessment.direction else ()),
                *((assessment.creative_scope,) if assessment.creative_scope else ()))
    cited = {alias for item in (*evidence, *assessment.decisions) for alias in item.source_ids}
    if not cited <= available.keys():
        raise ValueError("authoring assessment references an unqualified source")
    gaps = list(assessment.missing_information)
    for item in (assessment.direction, assessment.creative_scope):
        if item and not any(available[v].kind is AdmissionSourceKind.USER for v in item.source_ids):
            raise ValueError("authoring direction/creative permission requires literal USER input")
        if item and not any(
            available[v].kind is AdmissionSourceKind.USER
            and available[v].utf8_bytes is not None and available[v].utf8_bytes > 0
            for v in item.source_ids
        ):
            gaps.append(f"The '{item.requirement}' decision needs available nonempty literal USER input.")
    for item in assessment.decisions:
        if item.coverage is DecisionCoverage.DELEGATED and not any(
            available[v].kind is AdmissionSourceKind.USER for v in item.source_ids
        ):
            raise ValueError("creative delegation requires literal USER input")
        if item.coverage is DecisionCoverage.DELEGATED and not any(
            available[v].kind is AdmissionSourceKind.USER
            and available[v].utf8_bytes is not None and available[v].utf8_bytes > 0
            for v in item.source_ids
        ):
            gaps.append(f"The '{item.decision}' delegation needs available nonempty literal USER permission.")
    for item in assessment.foundations:
        if not any(available[v].utf8_bytes is not None and available[v].utf8_bytes > 0
                   for v in item.source_ids):
            gaps.append(f"The foundation '{item.requirement}' has only empty or unavailable content; use actual supplied material or clarify this foundation.")
    if not assessment.foundations:
        gaps.append("Supply the story foundations needed for this requested work, or identify available material to use.")
    if assessment.direction is None:
        gaps.append("State the intended outcome and direction for this work.")
    if assessment.creative_scope is None or not assessment.allowed_creative_actions:
        gaps.append("Identify which gaps may be invented and which elements must be preserved.")
    gaps.extend(v.explanation for v in assessment.decisions if v.coverage is DecisionCoverage.MISSING)
    if not assessment.decisions:
        gaps.append("Identify the decisions required by the requested deliverable and whether the USER supplied or explicitly delegated them.")
    if assessment.status is not AdmissionStatus.SUFFICIENT and not gaps:
        gaps.append(assessment.rationale)
    return tuple(dict.fromkeys(gaps))


def qualify_authoring_assessment(
    assessment: AuthoringAssessment | None, *, sources: tuple[AdmissionSource, ...],
    request_digest: str, task_id: str, task_revision: int, scope_digest: str,
) -> AuthoringAdmissionBasis | AuthoringAdmissionNeeded:
    """Qualify semantic evidence only against this host-provided snapshot."""
    if assessment is None:
        return AuthoringAdmissionNeeded(AdmissionStatus.NEEDS_INFORMATION, (
            "Assess the already supplied inputs for this work; identify only missing foundations, direction, or undecided creative boundaries. Existing source facts need not be supplied again.",
        ), sources)
    if type(assessment) is not AuthoringAssessment:
        raise TypeError("authoring_assessment must be AuthoringAssessment")
    gaps = _assessment_gaps(assessment, sources)
    if gaps:
        status = assessment.status if assessment.status is not AdmissionStatus.SUFFICIENT else AdmissionStatus.NEEDS_INFORMATION
        return AuthoringAdmissionNeeded(status, gaps, sources)
    cited = {alias for item in (*assessment.foundations, assessment.direction,
                               assessment.creative_scope, *assessment.decisions)
             if item is not None for alias in item.source_ids}
    return AuthoringAdmissionBasis(request_digest, task_id, task_revision, scope_digest,
                                  tuple(v for v in sources if v.source_id in cited), assessment)


def revalidate_authoring_basis(basis: AuthoringAdmissionBasis, *, sources: tuple[AdmissionSource, ...],
                              request_digest: str, task_id: str, task_revision: int,
                              scope_digest: str) -> None:
    if type(basis) is not AuthoringAdmissionBasis:
        raise ValueError("official authoring requires a host-qualified admission basis")
    current = qualify_authoring_assessment(basis.assessment, sources=sources,
        request_digest=request_digest, task_id=task_id, task_revision=task_revision,
        scope_digest=scope_digest)
    if current != basis:
        raise ValueError("authoring admission changed; clarify and qualify the current task again")


def assessment_to_dict(value: AuthoringAssessment) -> dict[str, object]:
    def evidence(item: AdmissionEvidence | None) -> object:
        return None if item is None else {"requirement": item.requirement,
            "source_ids": list(item.source_ids), "justification": item.justification}
    return {"status": value.status.value, "foundations": [evidence(v) for v in value.foundations],
        "direction": evidence(value.direction), "creative_scope": evidence(value.creative_scope),
        "allowed_creative_actions": list(value.allowed_creative_actions),
        "protected_constraints": list(value.protected_constraints),
        "decisions": [{"decision": v.decision, "coverage": v.coverage.value,
            "source_ids": list(v.source_ids), "explanation": v.explanation} for v in value.decisions],
        "missing_information": list(value.missing_information), "rationale": value.rationale}


def _object(value: object, keys: set[str]) -> dict:
    if type(value) is not dict or set(value) != keys:
        raise ValueError("authoring admission object has invalid fields")
    return value


def _array(value: object) -> tuple:
    if type(value) is not list or len(value) > 64:
        raise ValueError("authoring admission array is invalid")
    return tuple(value)


def assessment_from_dict(value: object) -> AuthoringAssessment:
    item = _object(value, {"status", "foundations", "direction", "creative_scope",
        "allowed_creative_actions", "protected_constraints", "decisions", "missing_information", "rationale"})
    def evidence(raw: object) -> AdmissionEvidence:
        row = _object(raw, {"requirement", "source_ids", "justification"})
        return AdmissionEvidence(row["requirement"], _array(row["source_ids"]), row["justification"])
    decisions = []
    for raw in _array(item["decisions"]):
        row = _object(raw, {"decision", "coverage", "source_ids", "explanation"})
        decisions.append(AdmissionDecision(row["decision"], DecisionCoverage(row["coverage"]),
                                          _array(row["source_ids"]), row["explanation"]))
    return AuthoringAssessment(AdmissionStatus(item["status"]),
        tuple(evidence(v) for v in _array(item["foundations"])),
        None if item["direction"] is None else evidence(item["direction"]),
        None if item["creative_scope"] is None else evidence(item["creative_scope"]),
        _array(item["allowed_creative_actions"]), _array(item["protected_constraints"]),
        tuple(decisions), _array(item["missing_information"]), item["rationale"])


def admission_basis_to_dict(value: AuthoringAdmissionBasis) -> dict[str, object]:
    return {"request_digest": value.request_digest, "task_id": value.task_id,
        "task_revision": value.task_revision, "scope_digest": value.scope_digest,
        "sources": [{"source_id": v.source_id, "kind": v.kind.value,
            "source_identity": v.source_identity, "revision_digest": v.revision_digest,
            "original_authority": v.original_authority, "utf8_bytes": v.utf8_bytes} for v in value.sources],
        "assessment": assessment_to_dict(value.assessment)}


def admission_basis_from_dict(value: object) -> AuthoringAdmissionBasis:
    item = _object(value, {"request_digest", "task_id", "task_revision", "scope_digest", "sources", "assessment"})
    sources = []
    for raw in _array(item["sources"]):
        row = _object(raw, {"source_id", "kind", "source_identity", "revision_digest", "original_authority", "utf8_bytes"})
        sources.append(AdmissionSource(row["source_id"], AdmissionSourceKind(row["kind"]),
            row["source_identity"], row["revision_digest"], row["original_authority"], row["utf8_bytes"]))
    return AuthoringAdmissionBasis(item["request_digest"], item["task_id"], item["task_revision"],
                                  item["scope_digest"], tuple(sources), assessment_from_dict(item["assessment"]))


def assessment_schema(source_ids: tuple[str, ...] | None = None) -> dict[str, object]:
    """Closed provider semantic shape; no host qualification/digest fields."""
    def obj(properties: dict, description: str) -> dict:
        return {"type": "object", "description": description, "properties": properties,
                "required": list(properties), "additionalProperties": False}
    text = {"type": "string"}
    strings = {"type": "array", "items": text, "minItems": 0, "maxItems": 32}
    refs = {"type": "array", "items": {"type": "string", **({"enum": list(source_ids)} if source_ids else {})}, "minItems": 0, "maxItems": 32}
    evidence = obj({"requirement": text, "source_ids": {**refs, "minItems": 1}, "justification": text},
        'Justify this requirement with exact offered sources.')
    nullable = {"anyOf": [evidence, {"type": "null", "description":
        'This required USER decision is unavailable.'}]}
    return obj({"status": {"type": "string", "enum": [v.value for v in AdmissionStatus], "description": 'Semantic status.'},
        "foundations": {"type": "array", "items": evidence, "minItems": 0, "maxItems": 32},
        "direction": nullable, "creative_scope": nullable, "allowed_creative_actions": strings,
        "protected_constraints": strings, "decisions": {"type": "array", "minItems": 0, "maxItems": 32,
            "items": obj({"decision": {**text, "description": 'Creative choice.'}, "coverage": {"type": "string", "enum": [v.value for v in DecisionCoverage], "description": 'Supplied/delegated/missing.'},
                          "source_ids": refs, "explanation": text},
            'Bind this decision to its actual source or USER delegation.')},
        "missing_information": strings, "rationale": text},
        'Assess supplied foundations and USER-bounded creative work; host qualification remains separate.')
