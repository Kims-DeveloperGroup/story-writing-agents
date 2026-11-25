"""Exact document census and evidence for one semantic synchronization assessment."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256

from .specialist_routing import ArtifactReviewDomain, artifact_domain
from .writing_artifacts.catalog import CORE_ARTIFACT_PATHS, canonical_artifact_kind


NARRATIVE_RULES_PATH = "core_documents/narrative_rules.md"

def require_synchronization_identifier(value: object, *, maximum: int = 64) -> None:
    if (
        type(value) is not str
        or len(value) > maximum
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", value) is None
    ):
        raise ValueError("synchronization identifier is invalid")


def require_synchronization_digest(value: object, *, tagged: bool = True) -> None:
    pattern = r"sha256:[0-9a-f]{64}" if tagged else r"[0-9a-f]{64}"
    if type(value) is not str or re.fullmatch(pattern, value) is None:
        raise ValueError("synchronization digest is invalid")


def require_synchronization_text(value: object) -> None:
    if type(value) is not str or not value.strip() or len(value.encode("utf-8")) > 4_096:
        raise ValueError("synchronization explanation must be bounded nonblank UTF-8")


def require_synchronization_tuple(value: object, item_type: type) -> None:
    if type(value) is not tuple or any(type(item) is not item_type for item in value):
        raise TypeError("synchronization collection must be a tuple of exact values")
    if len(set(value)) != len(value):
        raise ValueError("synchronization collection contains duplicate values")


def _positive(value: object) -> None:
    if type(value) is not int or value < 1:
        raise ValueError("synchronization revision or range must be a positive integer")


def _artifact_path(value: str) -> None:
    if canonical_artifact_kind(value) is None:
        raise ValueError("synchronization path is not a canonical writing artifact")


@dataclass(frozen=True, slots=True)
class SynchronizationEventRef:
    """Same-session event identity with an exact canonical content digest."""

    conversation_id: str
    event_id: str
    digest: str

    def __post_init__(self) -> None:
        require_synchronization_identifier(self.conversation_id)
        require_synchronization_identifier(self.event_id)
        require_synchronization_digest(self.digest)


@dataclass(frozen=True, slots=True)
class SynchronizationCandidate:
    candidate_id: str
    candidate_revision: int
    candidate_digest: str
    change_set_digest: str

    def __post_init__(self) -> None:
        require_synchronization_identifier(self.candidate_id)
        _positive(self.candidate_revision)
        require_synchronization_digest(self.candidate_digest)
        require_synchronization_digest(self.change_set_digest, tagged=False)


@dataclass(frozen=True, slots=True)
class SynchronizationApplication:
    application_id: str
    result: SynchronizationEventRef

    def __post_init__(self) -> None:
        require_synchronization_identifier(self.application_id, maximum=128)
        if type(self.result) is not SynchronizationEventRef:
            raise TypeError("synchronization application needs an exact result reference")


@dataclass(frozen=True, slots=True)
class SynchronizationBasis:
    task_id: str
    task_revision: int
    request: SynchronizationEventRef
    conditions_digest: str
    rules_digest: str
    base_state_digest: str
    result_state_digest: str
    candidate: SynchronizationCandidate | None = None
    application: SynchronizationApplication | None = None
    review_synthesis: SynchronizationEventRef | None = None
    narrative_rule_revision: SynchronizationEventRef | None = None
    narrative_rule_line_counts: tuple[tuple[str, int], ...] = ()
    narrative_rule_retained: tuple[tuple[str, SynchronizationEventRef], ...] = ()

    def __post_init__(self) -> None:
        require_synchronization_identifier(self.task_id)
        _positive(self.task_revision)
        if type(self.request) is not SynchronizationEventRef:
            raise TypeError("synchronization basis needs an exact USER event reference")
        for digest in (self.conditions_digest, self.rules_digest):
            require_synchronization_digest(digest)
        for digest in (self.base_state_digest, self.result_state_digest):
            require_synchronization_digest(digest, tagged=False)
        if self.candidate is not None and type(self.candidate) is not SynchronizationCandidate:
            raise TypeError("synchronization basis candidate must be exact")
        if self.application is not None and (
            type(self.application) is not SynchronizationApplication or self.candidate is None
        ):
            raise ValueError("synchronization application needs an exact candidate")
        if self.review_synthesis is not None:
            if type(self.review_synthesis) is not SynchronizationEventRef:
                raise ValueError("synchronization synthesis must be an exact review reference")
            if self.candidate is None and self.base_state_digest != self.result_state_digest:
                raise ValueError("current-state synchronization review cannot change the document state")

        if type(self.narrative_rule_line_counts) is not tuple or type(self.narrative_rule_retained) is not tuple:
            raise TypeError("narrative-rule impact collections must be exact tuples")
        if self.narrative_rule_revision is None:
            if self.narrative_rule_line_counts or self.narrative_rule_retained:
                raise ValueError("line census and retained findings require a narrative-rule impact basis")
        elif (
            self.narrative_rule_revision != self.request or self.candidate is not None
            or self.review_synthesis is None
        ):
            raise ValueError("narrative-rule impact requires the exact current USER request and synthesis")
        else:
            paths = set()
            for entry in self.narrative_rule_line_counts:
                if type(entry) is not tuple or len(entry) != 2:
                    raise ValueError("narrative-rule line census needs exact path/count pairs")
                path, count = entry
                _artifact_path(path)
                if type(count) is not int or count < 0 or path in paths:
                    raise ValueError("narrative-rule line census must contain distinct nonnegative counts")
                paths.add(path)

            retained_paths = set()
            for entry in self.narrative_rule_retained:
                if type(entry) is not tuple or len(entry) != 2:
                    raise ValueError("retained narrative-rule findings need exact path/event pairs")
                path, origin = entry
                _artifact_path(path)
                if type(origin) is not SynchronizationEventRef or path in retained_paths:
                    raise ValueError("retained narrative-rule findings need distinct exact origins")
                retained_paths.add(path)

    @property
    def projected(self) -> bool:
        """True while an exact candidate has no qualified successful Apply."""
        return self.candidate is not None and self.application is None


class SynchronizationAbsence(StrEnum):
    """Qualified absence of authored content or of the physical source."""

    INITIAL = "initial"
    ABSENT = "absent"


@dataclass(frozen=True, slots=True)
class SynchronizationDocument:
    path: str
    digest: str | None = None
    revision: str | None = None
    absence: SynchronizationAbsence | None = None

    def __post_init__(self) -> None:
        _artifact_path(self.path)
        if self.digest is not None:
            require_synchronization_digest(self.digest, tagged=False)
        if self.revision is not None:
            require_synchronization_digest(self.revision)
        if self.absence is not None and (
            type(self.absence) is not SynchronizationAbsence
            or (self.digest is not None and (
                self.absence is not SynchronizationAbsence.INITIAL
                or self.digest != "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
            ))
        ):
            raise ValueError("synchronization document absence conflicts with its content")


@dataclass(frozen=True, slots=True)
class SynchronizationCensus:
    """Current or projected state; inventory completeness is qualified by its caller."""

    changes: tuple[str, ...]
    documents: tuple[SynchronizationDocument, ...]
    complete: bool
    prior_documents: tuple[SynchronizationDocument, ...] = ()

    def __post_init__(self) -> None:
        require_synchronization_tuple(self.changes, str)
        if not self.changes:
            raise ValueError("synchronization census needs at least one changed document")
        for path in self.changes:
            _artifact_path(path)
        for collection in (self.documents, self.prior_documents):
            require_synchronization_tuple(collection, SynchronizationDocument)
            if len({document.path for document in collection}) != len(collection):
                raise ValueError("synchronization census repeats a document path")
        if not {document.path for document in self.prior_documents}.issubset(self.changes):
            raise ValueError("prior synchronization evidence must belong to a changed path")
        if type(self.complete) is not bool:
            raise TypeError("synchronization inventory completeness must be a boolean")

    @property
    def coverage_paths(self) -> frozenset[str]:
        """Every core document, manuscript in the census, and changed identity."""
        return frozenset((*CORE_ARTIFACT_PATHS, *self.changes, *(item.path for item in self.documents)))

    @property
    def required_pairs(self) -> tuple[tuple[str, str], ...]:
        """Each change requires screening of every other eligible document."""
        paths = sorted(self.coverage_paths)
        return tuple(
            (changed_path, target_path)
            for changed_path in sorted(self.changes)
            for target_path in paths
            if changed_path != target_path
        )


@dataclass(frozen=True, slots=True)
class SynchronizationAbsenceOrigin:
    """Exact unexecuted candidate provenance for one absent condition path."""

    path: str
    condition_id: str
    detected_by: SynchronizationEventRef
    candidate: SynchronizationCandidate
    candidate_tail: SynchronizationEventRef

    def __post_init__(self) -> None:
        _artifact_path(self.path)
        require_synchronization_identifier(self.condition_id)
        if (
            type(self.detected_by) is not SynchronizationEventRef
            or type(self.candidate_tail) is not SynchronizationEventRef
            or type(self.candidate) is not SynchronizationCandidate
            or self.detected_by.conversation_id != self.candidate_tail.conversation_id
        ):
            raise ValueError("absence provenance requires exact detection and candidate-owner tail")

    @property
    def receipt_id(self) -> str:
        digest = sha256(f"{self.condition_id}\0{self.path}".encode()).hexdigest()
        return f"absence_{digest[:48]}"


@dataclass(frozen=True, slots=True)
class SynchronizationAbsenceProof:
    origin: SynchronizationAbsenceOrigin
    source: SynchronizationEventRef

    def __post_init__(self) -> None:
        if type(self.origin) is not SynchronizationAbsenceOrigin or type(self.source) is not SynchronizationEventRef:
            raise TypeError("absence proof requires a typed origin and evidence reference")


def _validate_range(start_line: int | None, end_line: int | None) -> None:
    if start_line is None and end_line is None:
        return
    _positive(start_line)
    _positive(end_line)
    assert start_line is not None and end_line is not None
    if start_line > end_line:
        raise ValueError("synchronization READ range is reversed")


@dataclass(frozen=True, slots=True)
class SynchronizationRead:
    """A physical or exact candidate read whose identity is qualified by the host."""

    document: SynchronizationDocument
    source: SynchronizationEventRef
    receipt_id: str
    start_line: int | None = None
    end_line: int | None = None
    selection_digest: str | None = None
    source_revision: str | None = None

    def __post_init__(self) -> None:
        if type(self.document) is not SynchronizationDocument or self.document.digest is None:
            raise ValueError("synchronization READ evidence needs exact document bytes")
        if type(self.source) is not SynchronizationEventRef:
            raise TypeError("synchronization READ evidence needs an exact source event")
        require_synchronization_identifier(self.receipt_id)
        _validate_range(self.start_line, self.end_line)
        if (self.selection_digest is None) != (self.source_revision is None):
            raise ValueError("synchronization READ must retain both selection and revision digests")
        if self.selection_digest is not None:
            require_synchronization_digest(self.selection_digest)
            require_synchronization_digest(self.source_revision)
            if self.start_line is None and self.selection_digest != f"sha256:{self.document.digest}":
                raise ValueError("synchronization whole source selection must match document bytes")


class SynchronizationJudgment(StrEnum):
    UNREAD = "unread"
    MISSING = "missing"
    UNDECIDED = "undecided"
    INITIAL = "initial"
    NEEDS_CHANGE = "needs_change"
    NO_CHANGE = "no_change"
    NOT_APPLICABLE = "not_applicable"


_FINISHED = frozenset({
    SynchronizationJudgment.INITIAL,
    SynchronizationJudgment.NO_CHANGE,
    SynchronizationJudgment.NOT_APPLICABLE,
})
_REVIEWED = frozenset({
    SynchronizationJudgment.NEEDS_CHANGE,
    SynchronizationJudgment.NO_CHANGE,
    SynchronizationJudgment.NOT_APPLICABLE,
})


@dataclass(frozen=True, slots=True)
class SynchronizationFinding:
    changed_path: str
    target_path: str
    judgment: SynchronizationJudgment
    reason: str
    reads: tuple[SynchronizationRead, ...] = ()
    review: SynchronizationEventRef | None = None
    domain: ArtifactReviewDomain | None = None
    action: str | None = None
    resolution: str | None = None
    abstraction: str | None = None
    resolves: tuple[str, ...] = ()
    absences: tuple[SynchronizationAbsenceProof, ...] = ()

    def __post_init__(self) -> None:
        _artifact_path(self.changed_path)
        _artifact_path(self.target_path)
        if self.changed_path == self.target_path:
            raise ValueError("synchronization cannot compare a document with itself")
        if type(self.judgment) is not SynchronizationJudgment:
            raise TypeError("synchronization finding needs an exact judgment")
        require_synchronization_text(self.reason)
        require_synchronization_tuple(self.reads, SynchronizationRead)
        require_synchronization_tuple(self.resolves, str)
        require_synchronization_tuple(self.absences, SynchronizationAbsenceProof)
        for condition_id in self.resolves:
            require_synchronization_identifier(condition_id)
        if self.review is not None and type(self.review) is not SynchronizationEventRef:
            raise TypeError("synchronization review must be an exact event reference")
        if self.domain is not None and type(self.domain) is not ArtifactReviewDomain:
            raise TypeError("synchronization specialist domain must be exact")
        read_paths = {read.document.path for read in self.reads}
        absent_paths = {proof.origin.path for proof in self.absences}
        if self.judgment in _REVIEWED and (
            self.review is None or read_paths.intersection(absent_paths)
            or read_paths | absent_paths != set(self.pair)
        ):
            raise ValueError("semantic judgment requires review and exact material evidence for both paths")
        if self.absences and (
            self.judgment is not SynchronizationJudgment.NOT_APPLICABLE
            or not self.resolves
            or len(self.absences) != len(self.resolves) * len(absent_paths)
            or {(proof.origin.condition_id, proof.origin.path) for proof in self.absences}
            != {(condition, path) for condition in self.resolves for path in absent_paths}
        ):
            raise ValueError("absence evidence may only resolve an exact not-applicable condition")
        if self.judgment is SynchronizationJudgment.NEEDS_CHANGE:
            if self.domain is None:
                raise ValueError("a synchronization repair must have a specialist domain")
            require_synchronization_text(self.action)
            require_synchronization_text(self.resolution)
        elif self.action is not None or self.resolution is not None:
            raise ValueError("only a synchronization repair can prescribe a change")
        if self.judgment is SynchronizationJudgment.NOT_APPLICABLE:
            require_synchronization_text(self.abstraction)
        elif self.abstraction is not None:
            raise ValueError("abstraction belongs to a not-applicable finding")
        if self.resolves and self.domain is None:
            raise ValueError("an obligation resolution needs its specialist domain")
        if self.resolves and self.judgment not in {
            SynchronizationJudgment.NO_CHANGE, SynchronizationJudgment.NOT_APPLICABLE,
        }:
            raise ValueError("only a verified semantic judgment can satisfy an obligation")

    @property
    def pair(self) -> tuple[str, str]:
        return self.changed_path, self.target_path


@dataclass(frozen=True, slots=True)
class SynchronizationAssessment:
    """Coverage over the complete change set, separate from Apply authority."""

    basis: SynchronizationBasis
    census: SynchronizationCensus
    findings: tuple[SynchronizationFinding, ...]

    def __post_init__(self) -> None:
        if type(self.basis) is not SynchronizationBasis or type(self.census) is not SynchronizationCensus:
            raise TypeError("synchronization assessment needs exact basis and census values")
        require_synchronization_tuple(self.findings, SynchronizationFinding)
        pairs = {finding.pair for finding in self.findings}
        changes = frozenset(self.census.changes)
        paths = self.census.coverage_paths
        if len(pairs) != len(self.findings) or any(
            changed not in changes or target not in paths for changed, target in pairs
        ):
            raise ValueError("synchronization findings contain a duplicate or out-of-scope pair")
        documents = {item.path: item for item in self.census.documents}
        prior = {item.path: item for item in self.census.prior_documents}
        conditions: set[str] = set()
        for finding in self.findings:
            target = documents.get(finding.target_path)
            self._validate_finding(finding, target, documents, prior)
            if finding.absences and (
                self.basis.candidate is not None or not self.census.complete
                or any(
                    proof.origin.path not in documents
                    or documents[proof.origin.path].absence is not SynchronizationAbsence.ABSENT
                    for proof in finding.absences
                )
            ):
                raise ValueError("absence proof requires a complete current inventory with absent paths")
            if conditions.intersection(finding.resolves):
                raise ValueError("multiple synchronization findings claim the same resolution")
            conditions.update(finding.resolves)

    @staticmethod
    def _validate_finding(
        finding: SynchronizationFinding,
        target: SynchronizationDocument | None,
        documents: dict[str, SynchronizationDocument],
        prior: dict[str, SynchronizationDocument],
    ) -> None:
        if finding.judgment is SynchronizationJudgment.INITIAL and (
            target is None or target.absence is not SynchronizationAbsence.INITIAL
        ):
            raise ValueError("initial construction needs qualified absence")
        if finding.judgment is SynchronizationJudgment.MISSING and target is not None and (
            target.digest is not None or target.absence is SynchronizationAbsence.INITIAL
        ):
            raise ValueError("missing synchronization target conflicts with its known state")
        for read in finding.reads:
            path = read.document.path
            current = documents.get(path)
            # A deletion must be judged against the departed bytes and the exact
            # absent result recorded in the census.
            if current is not None and current.absence is SynchronizationAbsence.ABSENT:
                current = prior.get(path)
            if path not in finding.pair or read.document != current:
                raise ValueError("synchronization READ does not describe the exact current documents")

    @property
    def coverage_blockers(self) -> tuple[str, ...]:
        blockers = [] if self.census.complete else ["Document inventory is incomplete."]
        documents = {item.path: item for item in self.census.documents}
        deleted = {item.path for item in self.census.prior_documents if item.digest is not None}
        for path in self.census.changes:
            document = documents.get(path)
            if document is None or (
                document.digest is None
                and (document.absence is not SynchronizationAbsence.ABSENT or path not in deleted)
            ):
                blockers.append(f"{path}: changed revision is unconfirmed")
        expected = len(self.census.changes) * (len(self.census.coverage_paths) - 1)
        if expected > len(self.findings):
            blockers.append(f"Required impact checks without an assessment: {expected - len(self.findings)}.")
        return tuple(blockers)

    @property
    def blockers(self) -> tuple[str, ...]:
        return self.coverage_blockers + tuple(
            f"{finding.changed_path} → {finding.target_path}: {finding.judgment.value}"
            for finding in self.findings if finding.judgment not in _FINISHED
        )

    @property
    def ready(self) -> bool:
        return not self.blockers


def narrative_rule_finding_has_full_reads(
    finding: SynchronizationFinding,
    line_counts: tuple[tuple[str, int], ...],
) -> bool:
    if (
        finding.judgment not in _REVIEWED or finding.review is None or finding.domain is None
        or finding.domain is not artifact_domain(finding.target_path) or finding.resolves or finding.absences
    ):
        return False
    counts = dict(line_counts)
    for path in finding.pair:
        count = counts.get(path)
        reads = tuple(
            item for item in finding.reads if item.document.path == path
            and item.selection_digest is not None and item.source_revision is not None
        )
        if count is None or len({item.source_revision for item in reads}) != 1:
            return False
        if any(item.start_line is None for item in reads):
            continue
        covered = 0
        for start, end in sorted((item.start_line, item.end_line) for item in reads):
            if start > covered + 1 or end > count:
                return False
            covered = max(covered, end)
        if count == 0 or covered != count:
            return False
    return True


def narrative_rule_impact_complete(assessment: SynchronizationAssessment) -> bool:
    """Recognize a full current work census with qualified judgment for every target."""
    basis = assessment.basis
    census = assessment.census
    return (
        basis.narrative_rule_revision is not None and census.complete
        and census.changes == (NARRATIVE_RULES_PATH,) and not census.prior_documents
        and {path for path, _ in basis.narrative_rule_line_counts}
        == {item.path for item in census.documents if item.digest is not None}
        and {item.pair for item in assessment.findings} == set(census.required_pairs)
        and all(
            narrative_rule_finding_has_full_reads(item, basis.narrative_rule_line_counts)
            for item in assessment.findings
        )
    )


def same_narrative_impact_material(
    prior: SynchronizationAssessment,
    basis: SynchronizationBasis,
    census: SynchronizationCensus,
) -> bool:
    """Compare the physical and editorial basis while allowing a fresh approved task identity."""
    return (
        prior.basis.narrative_rule_revision is not None
        and prior.basis.result_state_digest == basis.result_state_digest
        and prior.basis.rules_digest == basis.rules_digest
        and prior.basis.conditions_digest == basis.conditions_digest
        and prior.basis.narrative_rule_line_counts == basis.narrative_rule_line_counts
        and prior.census == census
    )
