"""Validate final specialist and synchronization coverage for a story commit."""

from dataclasses import dataclass

from ...review_contracts import FinalReviewEvidence, ReviewTargetKind, review_digest
from ...synchronization_assessment import (
    SynchronizationAssessment,
    SynchronizationDocument,
    SynchronizationEventRef,
    SynchronizationJudgment,
)
from ...synchronization_obligations import SynchronizationObligations
from ...writing_artifacts.change_sets import ArtifactChangeSet, ArtifactRenameOperation
from .synchronization_history import SynchronizationHistory


@dataclass(frozen=True, slots=True)
class StoryCommitReviewCoverage:
    """One qualified Apply's final review and exact resulting assessment."""

    change_set: ArtifactChangeSet
    final_review: FinalReviewEvidence
    assessment_ref: SynchronizationEventRef
    assessment: SynchronizationAssessment


@dataclass(frozen=True, slots=True)
class StoryCommitDocumentQualification:
    obligations: SynchronizationObligations
    supporting_assessments: tuple[SynchronizationEventRef, ...]
    blockers: tuple[str, ...]


_SynchronizationAssessments = tuple[tuple[SynchronizationEventRef, SynchronizationAssessment], ...]
_REVIEWED = {
    SynchronizationJudgment.INITIAL,
    SynchronizationJudgment.NO_CHANGE,
    SynchronizationJudgment.NOT_APPLICABLE,
}


def _document_matches(
    known: SynchronizationDocument | None, current: SynchronizationDocument | None,
) -> bool:
    if known is None or current is None:
        return known is current
    return known.digest == current.digest and (
        known.revision is None or known.revision == current.revision
    )


def _rules_digest(documents: tuple[SynchronizationDocument, ...]) -> str:
    rules = next((item for item in documents if item.path == "core_documents/narrative_rules.md"), None)
    return "sha256:" + review_digest(rules.digest if rules else None)


def _matches_final_assessment(
    final: SynchronizationAssessment, documents: tuple[SynchronizationDocument, ...],
) -> bool:
    def present(items: tuple[SynchronizationDocument, ...]) -> dict[str, SynchronizationDocument]:
        return {
            item.path: item for item in items
            if item.digest is not None
        }

    reviewed, current = present(final.census.documents), present(documents)
    return reviewed.keys() == current.keys() and all(
        _document_matches(document, current[path]) for path, document in reviewed.items()
    )


def _inventory_blockers(
    reviews: tuple[StoryCommitReviewCoverage, ...],
    assessments: _SynchronizationAssessments,
    documents: tuple[SynchronizationDocument, ...],
    label: str,
    omitted: frozenset[str] = frozenset(),
) -> tuple[str, ...]:
    by_path = {item.path: item for item in documents}
    touched = {path for review in reviews for path in review.assessment.census.changes}
    blockers: list[str] = []
    remaining = touched - omitted
    if not remaining:
        return (f"{label}: no reviewed writing-artifact change is included.",)
    final_basis = reviews[-1].assessment.basis
    rules_digest = _rules_digest(documents)
    verified: set[tuple[str, str]] = set()
    for _, assessment in assessments:
        basis = assessment.basis
        if not assessment.census.complete or basis.rules_digest != rules_digest:
            continue
        if (
            basis.task_id == final_basis.task_id
            and basis.task_revision >= final_basis.task_revision
            and basis.conditions_digest != final_basis.conditions_digest
        ):
            continue
        census = {item.path: item for item in assessment.census.documents}
        for finding in assessment.findings:
            if finding.judgment not in _REVIEWED:
                continue
            changed, target = finding.pair
            if (
                changed in census and target in census
                and _document_matches(census[changed], by_path.get(changed))
                and _document_matches(census[target], by_path.get(target))
            ):
                verified.update(((changed, target), (target, changed)))
    for path in sorted(remaining):
        for target in sorted(by_path.keys() - {path}):
            if (path, target) not in verified:
                blockers.append(f"{label}: {path} → {target} needs a specialist impact check for these revisions.")
    for review in reviews:
        for operation in review.change_set.operations:
            if type(operation) is ArtifactRenameOperation and (
                (operation.source_path in omitted) != (operation.destination_path in omitted)
            ):
                blockers.append(f"{label}: both paths of {operation.source_path} → {operation.destination_path} must be recorded together.")
        assessment = review.assessment
        if not remaining.intersection(assessment.census.changes):
            continue
        for source in review.final_review.sources:
            if source.kind in {ReviewTargetKind.WRITING_ARTIFACT, ReviewTargetKind.READ_SOURCE}:
                path = source.target_id.removeprefix("${").removesuffix("}")
                if path not in assessment.census.changes and path in omitted:
                    document = by_path.get(path)
                    if document is None or source.source_digest != f"sha256:{document.digest}":
                        blockers.append(
                            f"{label}: specialist review source {path} differs from the recorded revision."
                        )
    return tuple(dict.fromkeys(blockers))


def _obligation_blockers(obligations: SynchronizationObligations) -> tuple[str, ...]:
    blockers = list(obligations.blockers)
    for head in obligations.heads:
        for state in head.conditions:
            condition = state.condition
            if any(item.startswith(f"{condition.condition_id}:") for item in blockers):
                blockers.append(
                    f"{condition.changed_path} → {condition.target_path}: {condition.action}"
                )
    return tuple(dict.fromkeys(blockers))


def qualify_story_commit_documents(
    reviews: tuple[StoryCommitReviewCoverage, ...],
    synchronization: SynchronizationHistory,
    current_documents: tuple[SynchronizationDocument, ...],
    prospective_documents: tuple[SynchronizationDocument, ...],
) -> StoryCommitDocumentQualification:
    """Check current and recorded document revisions against final review and obligations."""
    if not reviews:
        return StoryCommitDocumentQualification(synchronization.obligations, (), ())
    final = reviews[-1].assessment
    current_map = {item.path: item for item in current_documents}
    prospective_map = {item.path: item for item in prospective_documents}
    omitted = frozenset(
        path for path in current_map.keys() | prospective_map.keys()
        if not _document_matches(current_map.get(path), prospective_map.get(path))
    )
    applied_refs = {review.assessment_ref for review in reviews}
    current_assessments = tuple(
        (ref, assessment) for ref, assessment in synchronization.assessments
        if assessment.basis.application is None and not assessment.basis.projected
        and assessment.basis.result_state_digest == final.basis.result_state_digest
    )
    assessments = tuple(
        (ref, assessment) for ref, assessment in synchronization.assessments if ref in applied_refs
    ) + current_assessments
    blockers: list[str] = []
    if not _matches_final_assessment(final, current_documents):
        blockers.append("The complete worktree inventory differs from the last verified Apply.")
    blockers.extend(_inventory_blockers(reviews, assessments, current_documents, "Worktree"))
    current = synchronization.with_current_documents(
        current_documents, rules_digest=_rules_digest(current_documents),
    )
    blockers.extend(_obligation_blockers(current.obligations))
    if omitted:
        blockers.extend(
            _inventory_blockers(reviews, assessments, prospective_documents, "Commit tree", omitted)
        )
        recorded = synchronization.with_current_documents(
            prospective_documents, rules_digest=_rules_digest(prospective_documents),
        )
        blockers.extend(_obligation_blockers(recorded.obligations))
    return StoryCommitDocumentQualification(
        current.obligations, tuple(ref for ref, _ in current_assessments), tuple(blockers),
    )
