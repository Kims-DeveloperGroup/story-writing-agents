"""Qualify fully reviewed, exact Apply consent when known repairs are outstanding."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from ..conversations.event_contracts.artifact_apply import (
    ConditionalArtifactApplication,
    ConditionalArtifactApplicationIssue,
)
from ..conversations.event_contracts.artifact_reviews import ArtifactReviewedProposal
from ..conversations.event_contracts.execution import CollaborationSpecialistReplyPayload
from ..conversations.event_vocabulary import CollaborationSpecialistAssessment
from ..conversations.events import CollaborationEvent
from ..conversations.reducers.synchronization_history import SynchronizationHistory
from ..conversations.reducers.synchronization_repair_history import (
    has_blocking_applied_synchronization,
    has_competing_artifact_offer,
    has_unsettled_synchronization_write,
    project_synchronization_repair_work,
    require_synchronization_repair_scope,
    synchronization_repair_selection_for_task,
)
from ..synchronization_assessment import (
    SynchronizationAssessment,
    SynchronizationEventRef,
    SynchronizationFinding,
    SynchronizationJudgment,
)
from ..synchronization_obligations import SynchronizationCondition, SynchronizationStatus

_KNOWN_STATUS = frozenset({SynchronizationStatus.OPEN, SynchronizationStatus.IN_PROGRESS})
_SETTLED_STATUS = frozenset({SynchronizationStatus.RESOLVED, SynchronizationStatus.NOT_APPLICABLE})
_UNVERIFIED_FINDINGS = frozenset({
    SynchronizationJudgment.UNREAD,
    SynchronizationJudgment.MISSING,
    SynchronizationJudgment.UNDECIDED,
})
_NONBLOCKING_REVIEW = frozenset({
    CollaborationSpecialistAssessment.SUPPORTS,
    CollaborationSpecialistAssessment.CONSTRAINTS_ASSESSED,
    CollaborationSpecialistAssessment.ALTERNATIVES_ASSESSED,
})


class SynchronizationApplyBlocked(ValueError):
    """A safe diagnostic for the exact Apply request that failed qualification."""


def require_previous_write_outcomes(
    events: tuple[CollaborationEvent, ...], *, active_apply: SynchronizationEventRef | None = None,
) -> None:
    """Block a new write when the same session still has an uncertain one."""
    if has_unsettled_synchronization_write(events, active_application_decision=active_apply):
        raise SynchronizationApplyBlocked("Inspect or recover the previous write outcome before applying changes.")


def _require_exact_conditional_reviews(
    reviewed: ArtifactReviewedProposal,
    events: tuple[CollaborationEvent, ...],
    repair_findings: tuple[SynchronizationFinding, ...],
) -> None:
    cited = {
        finding.review.event_id for finding in repair_findings
        if finding.review is not None and finding.review.conversation_id == reviewed.conversation_id
    }
    replies = {
        event.event_id: event.payload for event in events
        if event.conversation_id == reviewed.conversation_id
        and type(event.payload) is CollaborationSpecialistReplyPayload
    }
    covered = set()
    for reply in reviewed.reviews:
        if reply.assessment in _NONBLOCKING_REVIEW:
            continue
        source = replies.get(reply.reply_event_id)
        if (
            source is None or reply.reply_event_id not in cited
            or source.assessment is not reply.assessment or source.assignment_id != reply.assignment_id
            or reply.assessment not in {
                CollaborationSpecialistAssessment.CONCERN, CollaborationSpecialistAssessment.CONFLICT,
            }
        ):
            raise SynchronizationApplyBlocked("The final specialist review has unresolved inputs outside the disclosed synchronization issues.")
        covered.update(source.input_ids)
    if covered != set(reviewed.unresolved_input_ids):
        raise SynchronizationApplyBlocked("The final specialist review has unresolved inputs outside the disclosed synchronization issues.")


def _known_condition(
    condition: SynchronizationCondition,
    origin: SynchronizationAssessment,
    assessment: SynchronizationAssessment,
    findings: tuple[SynchronizationFinding, ...],
) -> bool:
    """Check the original evidence still applies or the current review confirms it."""
    if any(
        set(finding.pair) == {condition.changed_path, condition.target_path}
        and finding.domain is condition.domain
        and finding.reason == condition.discrepancy
        and finding.action == condition.action and finding.resolution == condition.resolution
        for finding in findings
    ):
        return True
    origin_documents = {item.path: item for item in origin.census.documents}
    projected = {item.path: item for item in assessment.census.documents}
    return (
        origin.basis.rules_digest == assessment.basis.rules_digest
        and all(
            path in projected and projected[path] == origin_documents.get(path)
            for path in (condition.changed_path, condition.target_path)
        )
    )


def _require_related_repair(
    history: SynchronizationHistory,
    events: tuple[CollaborationEvent, ...],
    assessment_ref: SynchronizationEventRef,
    assessment: SynchronizationAssessment,
    active_apply: SynchronizationEventRef | None,
) -> None:
    source = next((
        event for event in events
        if (event.conversation_id, event.event_id) == (assessment_ref.conversation_id, assessment_ref.event_id)
    ), None)
    selection = None if source is None else synchronization_repair_selection_for_task(
        events, assessment_ref.conversation_id, assessment.basis.task_id,
        before_sequence=source.sequence,
    )
    if selection is None or set(assessment.census.changes) != {selection.repair_path}:
        raise SynchronizationApplyBlocked("Select an exact outstanding synchronization repair before applying related changes.")
    try:
        require_synchronization_repair_scope(
            selection,
            project_synchronization_repair_work(events, history, active_application_decision=active_apply),
        )
    except ValueError as exc:
        raise SynchronizationApplyBlocked("The selected synchronization repair changed. Review its current obligations.") from exc


def qualify_synchronization_apply(
    history: SynchronizationHistory,
    reviewed: ArtifactReviewedProposal,
    events: tuple[CollaborationEvent, ...],
    current_digests: Mapping[str, str],
    *,
    active_apply: SynchronizationEventRef | None = None,
) -> tuple[SynchronizationAssessment, ConditionalArtifactApplication | None]:
    """Return an exact candidate assessment and consent disclosure or fail closed."""
    if not history.complete:
        raise SynchronizationApplyBlocked("Synchronization history requires complete session evidence.")
    entry = history.assessment_entry_for_review(reviewed)
    if entry is None:
        raise SynchronizationApplyBlocked("No current synchronization assessment covers the exact candidate.")
    assessment_ref, assessment = entry
    coverage_blockers = assessment.coverage_blockers
    if coverage_blockers:
        raise SynchronizationApplyBlocked(coverage_blockers[0])
    unverified = next((item for item in assessment.findings if item.judgment in _UNVERIFIED_FINDINGS), None)
    if unverified is not None:
        raise SynchronizationApplyBlocked(
            f"{unverified.changed_path} → {unverified.target_path}: {unverified.judgment.value}"
        )
    obligations = history.prospective_obligations(assessment)
    invalid_resolutions = history.resolution_blockers(assessment, obligations)
    if invalid_resolutions:
        raise SynchronizationApplyBlocked(invalid_resolutions[0])
    if obligations.forked_conditions:
        raise SynchronizationApplyBlocked("Concurrent synchronization states require reconciliation before Apply.")

    repairs = tuple(
        finding for finding in assessment.findings
        if finding.judgment is SynchronizationJudgment.NEEDS_CHANGE
    )
    resolved = {condition_id for finding in assessment.findings for condition_id in finding.resolves}
    assessments = dict(history.assessments)
    applied_assessments = {
        replace(actual, basis=replace(actual.basis, application=None))
        for actual in assessments.values() if actual.basis.application is not None
    }
    already_pending = {
        state.condition.condition_id
        for head in history.obligations.heads for state in head.conditions
        if state.status not in _SETTLED_STATUS
    }
    disclosed: list[ConditionalArtifactApplicationIssue] = []
    needs_related_repair = False
    detected_pairs: set[tuple[str, str]] = set()
    for head in obligations.heads:
        for state in head.conditions:
            if state.status in _SETTLED_STATUS:
                continue
            condition = state.condition
            origin = assessments.get(condition.detected_by)
            if origin is None:
                raise SynchronizationApplyBlocked("An outstanding synchronization issue lacks its reviewed source.")
            pair = (condition.changed_path, condition.target_path)
            applied_origin = origin in applied_assessments
            needs_related_repair |= applied_origin and condition.condition_id in already_pending
            if condition.condition_id in resolved:
                continue
            if condition.detected_by != assessment_ref and not applied_origin:
                raise SynchronizationApplyBlocked("An outstanding synchronization issue belongs to another unapplied candidate.")
            if state.status not in _KNOWN_STATUS or not _known_condition(condition, origin, assessment, repairs):
                raise SynchronizationApplyBlocked("The outstanding synchronization conditions lack currently reviewed repair evidence.")
            if condition.detected_by == assessment_ref:
                detected_pairs.add(pair)
            disclosed.append(ConditionalArtifactApplicationIssue(head.ref, condition, state.status))
    current_history = history.with_current_artifact_digests(current_digests)
    needs_related_repair |= has_blocking_applied_synchronization(events, current_history)
    if (disclosed or needs_related_repair) and has_competing_artifact_offer(events):
        raise SynchronizationApplyBlocked("Resolve the outstanding Apply or Commit request before synchronization work.")
    if needs_related_repair:
        _require_related_repair(current_history, events, assessment_ref, assessment, active_apply)
    if {finding.pair for finding in repairs} != detected_pairs:
        raise SynchronizationApplyBlocked("A reviewed synchronization discrepancy lacks its exact outstanding obligation.")
    if not disclosed:
        return assessment, None
    _require_exact_conditional_reviews(reviewed, events, repairs)
    return assessment, ConditionalArtifactApplication(
        assessment_ref, tuple(sorted(disclosed, key=lambda issue: issue.condition.condition_id)),
    )
