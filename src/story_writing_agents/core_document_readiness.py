"""Source-bound whole-book readiness; semantic findings never grant write authority."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, fields
from enum import StrEnum

from .authoring_admission import AdmissionSourceKind


class AuthoringTarget(StrEnum):
    GLOBAL_OUTLINE = "core_documents/global_outline.md"
    PART_OUTLINES = "core_documents/part_outlines.md"
    CHARACTER_SHEETS = "core_documents/character_sheets.md"
    NARRATIVE_RULES = "core_documents/narrative_rules.md"
    MANUSCRIPT = "manuscript"


CORE_DOCUMENTS = tuple(target.value for target in AuthoringTarget if target is not AuthoringTarget.MANUSCRIPT)
CRITERIA = {
    AuthoringTarget.GLOBAL_OUTLINE.value: ("plot_progression", "conflict", "ending", "required_setup"),
    AuthoringTarget.PART_OUTLINES.value: ("all_units", "transitions", "late_progression"),
    AuthoringTarget.CHARACTER_SHEETS.value: ("roles", "relationships", "motivation", "character_settings", "character_change"),
    AuthoringTarget.NARRATIVE_RULES.value: ("point_of_view", "tone", "expression", "narration", "exceptions"),
}
DEPENDENCIES = {
    AuthoringTarget.GLOBAL_OUTLINE: (),
    AuthoringTarget.PART_OUTLINES: (AuthoringTarget.GLOBAL_OUTLINE,),
    AuthoringTarget.CHARACTER_SHEETS: (AuthoringTarget.GLOBAL_OUTLINE, AuthoringTarget.PART_OUTLINES),
    AuthoringTarget.NARRATIVE_RULES: (AuthoringTarget.GLOBAL_OUTLINE, AuthoringTarget.PART_OUTLINES),
    AuthoringTarget.MANUSCRIPT: tuple(AuthoringTarget(path) for path in CORE_DOCUMENTS),
}


@dataclass(frozen=True, slots=True)
class BookUnit:
    book_unit_id: str
    book_unit_extent: str
    book_unit_coverage: str
    source_ids: tuple[str, ...]
    explanation: str


@dataclass(frozen=True, slots=True)
class AuthorDecision:
    author_decision_id: str
    author_decision_text: str
    author_decision_coverage: str
    decision_scope_unit_ids: tuple[str, ...]
    source_ids: tuple[str, ...]
    explanation: str


@dataclass(frozen=True, slots=True)
class BookBasis:
    book_scope_status: str
    whole_book_scope: str
    source_ids: tuple[str, ...]
    direction_source_ids: tuple[str, ...]
    whole_book_units: tuple[BookUnit, ...]
    author_decisions: tuple[AuthorDecision, ...]
    allowed_creative_actions: tuple[str, ...]
    protected_constraints: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CriterionFinding:
    criterion_id: str
    criterion_judgment: str
    source_ids: tuple[str, ...]
    depends_on_author_decision_ids: tuple[str, ...]
    explanation: str


@dataclass(frozen=True, slots=True)
class ReaderSecret:
    secret_matter: str
    secret_scope_unit_ids: tuple[str, ...]
    source_ids: tuple[str, ...]
    author_side_fact: str
    author_fact_source_ids: tuple[str, ...]
    author_fact_coverage: str
    explanation: str


@dataclass(frozen=True, slots=True)
class DocumentJudgment:
    document_path: str
    document_read_source_id: str | None
    covered_whole_book_unit_ids: tuple[str, ...]
    criteria_findings: tuple[CriterionFinding, ...]
    dependency_source_ids: tuple[str, ...]
    remaining_author_decision_ids: tuple[str, ...]
    intentional_reader_secrets: tuple[ReaderSecret, ...]
    missing_information: tuple[str, ...]
    rationale: str


@dataclass(frozen=True, slots=True)
class CoreDocumentAssessment:
    source_basis_ref: str
    book_basis: BookBasis
    document_judgments: tuple[DocumentJudgment, ...]
    missing_information: tuple[str, ...]
    rationale: str


@dataclass(frozen=True, slots=True)
class ReadinessSource:
    """Owning adapters derive these facts from actual USER/material/READ records."""
    source_id: str
    source_identity: str
    revision_digest: str
    kind: AdmissionSourceKind
    utf8_bytes: int | None
    source_classification: str = "input_only"
    document_path: str | None = None
    content_digest: str | None = None
    whole_document: bool = False
    acquisition_identity: str | None = None

    def __post_init__(self) -> None:
        for value in (self.source_id, self.source_identity, self.revision_digest):
            _text(value)
        if type(self.kind) is not AdmissionSourceKind:
            raise TypeError("readiness source kind must be typed")
        if self.utf8_bytes is not None and (type(self.utf8_bytes) is not int or self.utf8_bytes < 0):
            raise ValueError("readiness source size must be actual or unknown")
        if self.source_classification not in {"literal_user", "input_only", "rejected_public_response", "actual_read"}:
            raise ValueError("readiness material classification is invalid")
        expected = {AdmissionSourceKind.USER: {"literal_user"},
                    AdmissionSourceKind.MATERIAL: {"input_only", "rejected_public_response"},
                    AdmissionSourceKind.READ: {"actual_read"}}
        if self.source_classification not in expected[self.kind]:
            raise ValueError("readiness source classification must match its actual origin")
        if type(self.whole_document) is not bool:
            raise TypeError("readiness boundary must be typed")
        if self.whole_document and (self.kind is not AdmissionSourceKind.READ or
                                   self.document_path not in CORE_DOCUMENTS or
                                   self.content_digest is None or self.acquisition_identity is None):
            raise ValueError("whole document needs actual READ identity")


@dataclass(frozen=True, slots=True)
class CoreDocumentReadinessBasis:
    assessment: CoreDocumentAssessment
    sources: tuple[ReadinessSource, ...]
    request_digest: str
    prior_assessment_refs: tuple[str, ...] = ()
    history_coverage: tuple[tuple[str, int, str], ...] = ()
    history_metadata: tuple[tuple[str, str, str], ...] = ()

    def __post_init__(self) -> None:
        assessment_from_dict(assessment_to_dict(self.assessment))
        _sources(self.sources)
        _strings(self.prior_assessment_refs)
        if type(self.history_coverage) is not tuple or any(type(v) is not tuple or len(v) != 3
            or type(v[0]) is not str or type(v[1]) is not int or v[1] < 0
            or type(v[2]) is not str for v in self.history_coverage):
            raise ValueError("readiness needs actual immutable history coverage")
        if (type(self.history_metadata) is not tuple or
            any(type(v) is not tuple or len(v) != 3 or any(type(x) is not str for x in v)
                or v[2] not in {"active", "archived"} for v in self.history_metadata)
            or {v[0] for v in self.history_metadata} != {v[0] for v in self.history_coverage}
            or len({v[0] for v in self.history_metadata}) != len(self.history_metadata)):
            raise ValueError("readiness needs the exact original coverage owner metadata")
        if len({v[0] for v in self.history_coverage}) != len(self.history_coverage):
            raise ValueError("readiness history owners must be unique")
        if self.assessment.source_basis_ref != source_basis_ref(self.sources, self.request_digest):
            raise ValueError("readiness assessment does not bind the actual source snapshot")


@dataclass(frozen=True, slots=True)
class CoreDocumentReadinessNeeded:
    reasons: tuple[str, ...]
    required_documents: tuple[str, ...] = ()
    offered_sources: tuple[ReadinessSource, ...] = ()

    @property
    def message(self) -> str:
        return " ".join(self.reasons)


@dataclass(frozen=True, slots=True)
class CoreDocumentReadiness:
    basis: CoreDocumentReadinessBasis | None
    complete_documents: tuple[str, ...]
    remaining: tuple[tuple[str, tuple[str, ...]], ...]


def _text(value: object) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError("readiness text must be nonempty")
    value.encode("utf-8", "strict")
    return value


def _strings(value: object) -> tuple[str, ...]:
    if type(value) not in {tuple, list}:
        raise TypeError("readiness values must be an array")
    result = tuple(_text(item) for item in value)
    if len(result) != len(set(result)):
        raise ValueError("readiness values must be unique")
    return result


def _sources(value: tuple[ReadinessSource, ...]) -> dict[str, ReadinessSource]:
    if type(value) is not tuple or any(type(item) is not ReadinessSource for item in value):
        raise TypeError("readiness sources must be exact facts")
    result = {item.source_id: item for item in value}
    if len(result) != len(value):
        raise ValueError("readiness sources must be unambiguous")
    return result


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8", "strict")


def source_basis_ref(sources: tuple[ReadinessSource, ...], request_digest: str) -> str:
    """Bind original USER/material inputs; subsequent READ acquisitions are separate proof."""
    _sources(sources)
    _text(request_digest)
    offered = [asdict(s) for s in sources if s.kind is not AdmissionSourceKind.READ]
    return "sha256:" + hashlib.sha256(_canonical({"request": request_digest, "sources": offered})).hexdigest()


def assessment_to_dict(value: CoreDocumentAssessment) -> dict:
    if type(value) is not CoreDocumentAssessment:
        raise TypeError("expected typed core document assessment")
    return json.loads(_canonical(asdict(value)))


_OBJECTS = {
    BookUnit: {"book_unit_coverage": ("supplied", "delegated", "missing")},
    AuthorDecision: {"author_decision_coverage": ("supplied", "delegated", "missing")},
    BookBasis: {"book_scope_status": ("whole_book_established", "needs_scope_decision"),
                "whole_book_units": BookUnit, "author_decisions": AuthorDecision},
    CriterionFinding: {"criterion_id": tuple(v for criteria in CRITERIA.values() for v in criteria),
                       "criterion_judgment": ("criteria_satisfied", "criteria_missing", "criteria_unknown")},
    ReaderSecret: {"author_fact_coverage": ("supplied", "delegated", "missing")},
    DocumentJudgment: {"document_path": CORE_DOCUMENTS, "criteria_findings": CriterionFinding,
                       "intentional_reader_secrets": ReaderSecret},
    CoreDocumentAssessment: {"book_basis": BookBasis, "document_judgments": DocumentJudgment},
}
_ARRAYS = {f.name for cls in _OBJECTS for f in fields(cls) if "tuple[" in str(f.type)}


def _decode(cls: type, value: object):
    if type(value) is not dict or set(value) != {f.name for f in fields(cls)}:
        raise ValueError("readiness object fields must match the current closed contract")
    decoded = {}
    for name, item in value.items():
        spec = _OBJECTS[cls].get(name)
        if isinstance(spec, type):
            decoded[name] = tuple(_decode(spec, row) for row in item) if name in _ARRAYS else _decode(spec, item)
        elif name in _ARRAYS:
            decoded[name] = _strings(item)
        elif name == "document_read_source_id" and item is None:
            decoded[name] = None
        else:
            decoded[name] = _text(item)
            if isinstance(spec, tuple) and item not in spec:
                raise ValueError("unknown readiness choice")
    return cls(**decoded)


def assessment_from_dict(value: object) -> CoreDocumentAssessment:
    result = _decode(CoreDocumentAssessment, value)
    docs = tuple(item.document_path for item in result.document_judgments)
    if len(docs) != 4 or set(docs) != set(CORE_DOCUMENTS):
        raise ValueError("readiness requires four distinct document judgments")
    units = tuple(item.book_unit_id for item in result.book_basis.whole_book_units)
    choices = tuple(item.author_decision_id for item in result.book_basis.author_decisions)
    if len(set(units)) != len(units) or len(set(choices)) != len(choices):
        raise ValueError("book units and decisions need unique identities")
    for row in result.document_judgments:
        criteria = tuple(item.criterion_id for item in row.criteria_findings)
        if len(set(criteria)) != len(criteria) or set(criteria) != set(CRITERIA[row.document_path]):
            raise ValueError("readiness requires every document criterion exactly once")
        if not set(row.covered_whole_book_unit_ids) <= set(units) or not set(row.remaining_author_decision_ids) <= set(choices):
            raise ValueError("readiness references unknown book units or decisions")
        choices = {choice.author_decision_id: choice for choice in result.book_basis.author_decisions}
        for finding in row.criteria_findings:
            if not set(finding.depends_on_author_decision_ids) <= set(choices):
                raise ValueError("criterion dependencies must name actual author decisions")
            if finding.criterion_judgment == "criteria_satisfied" and any(
                choices[identity].author_decision_coverage == "missing" for identity in finding.depends_on_author_decision_ids):
                raise ValueError("a satisfied criterion cannot depend on an unresolved author decision")
        for secret in row.intentional_reader_secrets:
            if not set(secret.secret_scope_unit_ids) <= set(units):
                raise ValueError("secret scope must bind actual book units")
    missing = {v.author_decision_id for v in result.book_basis.author_decisions if v.author_decision_coverage == "missing"}
    declared = {v for row in result.document_judgments for v in row.remaining_author_decision_ids}
    if declared != missing:
        raise ValueError("every missing author decision must have its explicit affected document judgments")
    for choice in result.book_basis.author_decisions:
        if not set(choice.decision_scope_unit_ids) <= set(units):
            raise ValueError("decision scope must bind actual book units")
    return result


def basis_to_dict(value: CoreDocumentReadinessBasis) -> dict:
    if type(value) is not CoreDocumentReadinessBasis:
        raise TypeError("expected qualified readiness basis")
    return json.loads(_canonical(asdict(value)))


def basis_from_dict(value: object) -> CoreDocumentReadinessBasis:
    if type(value) is not dict or set(value) != {"assessment", "sources", "request_digest", "prior_assessment_refs", "history_coverage", "history_metadata"}:
        raise ValueError("readiness basis fields must match the current contract")
    if type(value["sources"]) is not list:
        raise ValueError("readiness sources must be an array")
    sources = []
    for row in value["sources"]:
        if type(row) is not dict or set(row) != {f.name for f in fields(ReadinessSource)}:
            raise ValueError("readiness source fields must match the current contract")
        sources.append(ReadinessSource(**{**row, "kind": AdmissionSourceKind(row["kind"])}))
    return qualify_assessment(assessment_from_dict(value["assessment"]), tuple(sources), value["request_digest"],
        prior_assessment_refs=_strings(value["prior_assessment_refs"]),
        history_coverage=tuple(tuple(row) for row in value["history_coverage"]),
        history_metadata=tuple(tuple(row) for row in value["history_metadata"]))


def qualify_assessment(assessment: CoreDocumentAssessment, sources: tuple[ReadinessSource, ...],
                       request_digest: str, *, prior_assessment_refs: tuple[str, ...] = (),
                       history_coverage: tuple[tuple[str, int, str], ...] = (),
                       history_metadata: tuple[tuple[str, str, str], ...] = ()) -> CoreDocumentReadinessBasis:
    facts = _sources(sources)
    value = assessment_from_dict(assessment_to_dict(assessment))
    def refs(ids, *, user=False):
        if any(s not in facts for s in ids):
            raise ValueError("readiness cites unavailable actual sources")
        if user and (not ids or any(facts[s].kind is not AdmissionSourceKind.USER or
                                  not facts[s].utf8_bytes for s in ids)):
            raise ValueError("readiness direction/delegation requires actual positive-byte USER")
    def covered(coverage, ids):
        refs(ids, user=coverage == "delegated")
        if coverage == "supplied" and (not ids or not any(facts[s].utf8_bytes for s in ids)):
            raise ValueError("supplied readiness facts need actual available bytes")
    refs(value.book_basis.source_ids)
    refs(value.book_basis.direction_source_ids, user=value.book_basis.book_scope_status == "whole_book_established")
    for unit in value.book_basis.whole_book_units:
        covered(unit.book_unit_coverage, unit.source_ids)
    for choice in value.book_basis.author_decisions:
        covered(choice.author_decision_coverage, choice.source_ids)
    for row in value.document_judgments:
        refs(row.dependency_source_ids)
        if row.document_read_source_id is not None:
            refs((row.document_read_source_id,))
            fact = facts[row.document_read_source_id]
            if not fact.whole_document or fact.document_path != row.document_path:
                raise ValueError("document finding needs its actual whole-document READ")
        for criterion in row.criteria_findings:
            if not criterion.source_ids:
                raise ValueError("criterion findings require actual cited sources")
            refs(criterion.source_ids)
            if criterion.criterion_judgment == "criteria_satisfied" and (
                row.document_read_source_id is None or row.document_read_source_id not in criterion.source_ids):
                raise ValueError("satisfied document criteria require the exact own whole-document READ")
        for secret in row.intentional_reader_secrets:
            refs(secret.source_ids, user=True)
            covered(secret.author_fact_coverage, secret.author_fact_source_ids)
    return CoreDocumentReadinessBasis(value, sources, request_digest, prior_assessment_refs, history_coverage, history_metadata)


def project_readiness(basis: CoreDocumentReadinessBasis | None,
                      current_sources: tuple[ReadinessSource, ...]) -> CoreDocumentReadiness:
    current = _sources(current_sources)
    complete, remaining = [], []
    if basis is None:
        return CoreDocumentReadiness(None, (), tuple((p, ("No current whole-book assessment.",)) for p in CORE_DOCUMENTS))
    original = _sources(basis.sources)
    book = basis.assessment.book_basis
    units = {u.book_unit_id for u in book.whole_book_units}
    def changed(ids):
        return any(current.get(i) != original.get(i) for i in ids)
    basis_ids = set(book.source_ids + book.direction_source_ids)
    for u in book.whole_book_units:
        basis_ids.update(u.source_ids)
    rows = {row.document_path: row for row in basis.assessment.document_judgments}
    choices = {choice.author_decision_id: choice for choice in book.author_decisions}
    for path in CORE_DOCUMENTS:
        row = rows[path]
        reasons = list(basis.assessment.missing_information + row.missing_information)
        if book.book_scope_status != "whole_book_established" or not units or any(u.book_unit_coverage == "missing" for u in book.whole_book_units):
            reasons.append("Establish the exact whole-book extent and unresolved units.")
        if changed(basis_ids):
            reasons.append("Book basis changed; reassess its affected document judgments.")
        if set(row.covered_whole_book_unit_ids) != units:
            reasons.append("The document does not cover every required whole-book unit.")
        if row.document_read_source_id is None:
            reasons.append("Read the exact current whole document before marking it complete.")
        elif changed((row.document_read_source_id,)):
            reasons.append("Document revision changed; revalidation is needed.")
        elif not original[row.document_read_source_id].utf8_bytes:
            reasons.append("An empty document cannot establish whole-book completion.")
        if changed(row.dependency_source_ids):
            reasons.append("A cited dependency changed; revalidation is needed.")
        for dependency in DEPENDENCIES[AuthoringTarget(row.document_path)]:
            reference = rows[dependency.value].document_read_source_id
            if (dependency.value not in complete or reference is None
                or reference not in row.dependency_source_ids):
                reasons.append(f"Required current completed dependency: {dependency.value}.")
        if row.remaining_author_decision_ids:
            reasons.extend("Resolve required author decision: " + choices[identity].author_decision_text for identity in row.remaining_author_decision_ids)
        for finding in row.criteria_findings:
            if (finding.criterion_judgment != "criteria_satisfied" or changed(finding.source_ids)
                or any(changed(choices[identity].source_ids) for identity in finding.depends_on_author_decision_ids)):
                reasons.append(f"Reassess required criterion: {finding.criterion_id}.")
        for secret in row.intentional_reader_secrets:
            if not secret.secret_scope_unit_ids or secret.author_fact_coverage != "supplied" or changed(secret.source_ids + secret.author_fact_source_ids):
                reasons.append("Reader secrecy requires a resolved supplied author-side fact; delegation alone is insufficient.")
        if not reasons:
            complete.append(row.document_path)
        remaining.append((row.document_path, tuple(dict.fromkeys(reasons))))
    return CoreDocumentReadiness(basis, tuple(complete), tuple(remaining))


def require_readiness(target: AuthoringTarget | None, readiness: CoreDocumentReadiness,
                      sources: tuple[ReadinessSource, ...] = ()) -> CoreDocumentReadinessNeeded | None:
    if target is None:
        return CoreDocumentReadinessNeeded(("Choose the actual core-document or manuscript output target.",), (), sources)
    if type(target) is not AuthoringTarget:
        raise TypeError("authoring target must be explicit and typed")
    missing = tuple(p.value for p in DEPENDENCIES[target] if p.value not in readiness.complete_documents)
    if not missing:
        return None
    reasons = tuple(f"Prepare or revalidate {p}: " + " ".join(dict(readiness.remaining).get(p, ())) for p in missing)
    return CoreDocumentReadinessNeeded(reasons, missing, sources)


def targets_for_paths(paths: tuple[str, ...]) -> tuple[AuthoringTarget, ...]:
    """Classify explicit canonical paths only; never infer a book task from prose."""
    targets = []
    for path in paths:
        target = (AuthoringTarget(path) if path in CORE_DOCUMENTS else
                  AuthoringTarget.MANUSCRIPT if path.startswith("manuscript/") else None)
        if target is not None and target not in targets:
            targets.append(target)
    return tuple(targets)


def validate_authoring_target_basis(target: AuthoringTarget | None,
                                    basis: CoreDocumentReadinessBasis | None) -> None:
    """Current typed constructors cannot substitute caller flags for prerequisites."""
    needed = require_readiness(target, project_readiness(basis, () if basis is None else basis.sources))
    if needed is not None:
        raise ValueError(needed.message)


def assessment_schema(source_ids: tuple[str, ...] | None, read_ids: tuple[str, ...] | None, basis_ref: str) -> dict:
    """Supported closed native schema; no artificial story-size/count limit."""
    text = {"type": "string"}
    def strings(ids=None, minimum=0):
        item = text if ids is None else {"type": "string", "enum": list(ids)}
        return {"type": "array", "minItems": minimum, "items": item} if ids is None or ids else {"type": "array", "minItems": 0, "maxItems": 0, "items": text}
    def obj(cls):
        props = {}
        for f in fields(cls):
            spec = _OBJECTS[cls].get(f.name)
            if isinstance(spec, type):
                prop = {"type": "array", "minItems": 0, "items": obj(spec)} if f.name in _ARRAYS else obj(spec)
            elif f.name in _ARRAYS:
                prop = strings(source_ids if f.name.endswith("source_ids") else None)
            elif isinstance(spec, tuple):
                prop = {"type": "string", "enum": list(spec)}
            elif f.name == "document_read_source_id":
                read = {"type": "string", "description": "Actual whole-document READ identity acquired in this request."}
                if read_ids:
                    read["enum"] = list(read_ids)
                prop = {"anyOf": [read, {"type": "null", "description": "No whole-document READ; cannot complete."}]} if read_ids is None or read_ids else {"type": "null"}
            else:
                prop = dict(text)
            props[f.name] = prop
        if cls is CoreDocumentAssessment:
            props["source_basis_ref"] = {"type": "string", "enum": [basis_ref]}
            props["document_judgments"].update(minItems=4, maxItems=4)
        if cls is DocumentJudgment:
            props["criteria_findings"].update(minItems=1, maxItems=17)
        if cls is CriterionFinding:
            props["source_ids"] = strings(source_ids, minimum=1)
        return {"type": "object", "description": "Current whole-book findings; exact physical sources and required coverage are host qualified. No write authority.",
                "additionalProperties": False, "properties": props, "required": list(props)}
    return obj(CoreDocumentAssessment)
