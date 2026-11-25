"""Qualify a story commit from exact, session-local Apply and review history."""

from dataclasses import dataclass, field, replace

from ...artifact_proposals import ArtifactReviewCandidate
from ...document_plan import DocumentPlan, DocumentPlanRef
from ...review_contracts import review_digest
from ...synchronization_assessment import (
    SynchronizationApplication,
    SynchronizationAssessment,
    SynchronizationDocument,
    SynchronizationEventRef,
    SynchronizationJudgment,
)
from ...synchronization_obligations import SynchronizationObligations
from ...synchronization_repair import (
    SynchronizationRepairSelection,
    synchronization_repair_selection_to_dict,
)
from ..event_contracts.artifact_apply import (
    ArtifactApplicationDecisionPayload,
    ArtifactApplicationRequestPayload,
    ArtifactApplicationResultPayload,
)
from ..event_contracts.story_commits import StoryCommitDecisionPayload, StoryCommitResultPayload
from ..event_contracts.synchronization import SynchronizationPayload
from ..event_vocabulary import (
    ArtifactApplicationDecisionKind,
    ArtifactApplicationOutcomeKind,
    CollaborationActorKind,
    StoryCommitOutcomeKind,
)
from ..events import (
    ArtifactObjectivePayload,
    ArtifactObjectivePayloadV2,
    CollaborationEvent,
    CollaborationEventBatch,
    CollaborationEventDraft,
    CollaborationProposal,
    synchronization_event_ref,
)
from .story_commit_review_coverage import (
    StoryCommitReviewCoverage,
    conditional_reviews_are_covered,
    qualify_story_commit_documents,
)
from .synchronization_history import project_synchronization_history
from .synchronization_repair_history import (
    resolved_conditional_findings,
    recorded_synchronization_repair_condition,
    synchronization_repair_selection_for_task,
)


@dataclass(frozen=True, slots=True)
class StoryCommitHistoryApplication:
    """One applied change set with exact review, authorization and outcome evidence."""

    request: ArtifactApplicationRequestPayload = field(repr=False)
    decision: ArtifactApplicationDecisionPayload
    result: ArtifactApplicationResultPayload
    request_ref: SynchronizationEventRef
    decision_ref: SynchronizationEventRef
    result_ref: SynchronizationEventRef
    projected_ref: SynchronizationEventRef
    actual_ref: SynchronizationEventRef
    projected: SynchronizationAssessment = field(repr=False)
    actual: SynchronizationAssessment = field(repr=False)
    consultation_ref: SynchronizationEventRef
    consultation: CollaborationProposal = field(repr=False)
    repair_selection: SynchronizationRepairSelection | None = None

    @property
    def application_id(self) -> str:
        return self.result.application_id

    @property
    def candidate(self) -> ArtifactReviewCandidate:
        return self.request.reviewed_proposal.candidate


@dataclass(frozen=True, slots=True)
class StoryCommitHistoryQualification:
    """A baseline-to-worktree chain and its current semantic qualification."""

    applications: tuple[StoryCommitHistoryApplication, ...]
    basis_digest: str
    blockers: tuple[str, ...]


_SynchronizationAssessments = tuple[tuple[SynchronizationEventRef, SynchronizationAssessment], ...]
_SUCCEEDED = {ArtifactApplicationOutcomeKind.APPLIED, ArtifactApplicationOutcomeKind.UNCHANGED}


def _with_pending_events(
    events: tuple[CollaborationEvent, ...], pending: tuple[CollaborationEventDraft, ...],
) -> tuple[CollaborationEvent, ...]:
    if not pending:
        return events
    first = pending[0]
    if (
        type(first.payload) is not ArtifactApplicationResultPayload
        or any(type(draft.payload) is not SynchronizationPayload for draft in pending[1:])
    ):
        raise ValueError("pending commit evidence must contain only the Apply result and its synchronization")
    position = max(
        (item.sequence for item in events if item.conversation_id == first.conversation_id),
        default=0,
    )
    batch = CollaborationEventBatch(
        batch_id=first.event_id, expected_last_sequence=position, events=pending,
    )
    digest = batch.batch_digest
    # Storage metadata is provisional. Exact evidence refs use caller-owned draft content.
    return events + tuple(
        CollaborationEvent(
            schema_version=draft.schema_version,
            event_id=draft.event_id,
            session_id=draft.session_id,
            conversation_id=draft.conversation_id,
            turn_id=draft.turn_id,
            step_id=draft.step_id,
            sequence=position + ordinal + 1,
            timestamp="1970-01-01T00:00:00+00:00",
            batch_id=batch.batch_id,
            batch_ordinal=ordinal,
            batch_size=len(pending),
            batch_digest=digest,
            kind=draft.kind,
            sender=draft.sender,
            recipient=draft.recipient,
            payload=draft.payload,
        )
        for ordinal, draft in enumerate(pending)
    )


def _applied_evidence(
    event: CollaborationEvent,
    requests: dict[tuple[str, str], CollaborationEvent],
    decisions: dict[tuple[str, str], CollaborationEvent],
    assessments: _SynchronizationAssessments,
    consultations: dict[tuple[str, str, int, str], CollaborationEvent],
    events: dict[tuple[str, str], CollaborationEvent],
) -> StoryCommitHistoryApplication | str:
    payload = event.payload
    assert type(payload) is ArtifactApplicationResultPayload and payload.result is not None
    decision_event = decisions.get((event.conversation_id, payload.decision_event_id))
    decision = decision_event.payload if decision_event else None
    request_event = (
        requests.get((event.conversation_id, decision.confirmation_request_event_id))
        if type(decision) is ArtifactApplicationDecisionPayload else None
    )
    request = request_event.payload if request_event else None
    name = f"Apply {payload.application_id}"
    if (
        type(request) is not ArtifactApplicationRequestPayload
        or type(decision) is not ArtifactApplicationDecisionPayload
        or decision_event.sender.kind is not CollaborationActorKind.USER
        or decision.decision is not ArtifactApplicationDecisionKind.APPLY
        or not request_event.sequence < decision_event.sequence < event.sequence
        or request_event.step_id != decision_event.step_id or decision_event.step_id != event.step_id
        or request.confirmation_request_id != decision.confirmation_request_id
        or request.confirmation_request_id != payload.confirmation_request_id
        or request.application_request.application_id != decision.application_id
        or request.application_request.application_id != payload.application_id
        or request.application_request_digest != decision.application_request_digest
        or request.application_request_digest != payload.application_request_digest
        or request.application_request.provenance != payload.result.provenance
    ):
        return f"{name}: exact Apply request, USER decision, or result is missing or changed."
    reviewed = request.reviewed_proposal
    consultation_event = consultations.get((
        event.conversation_id, reviewed.consultation_proposal_id,
        reviewed.consultation_proposal_revision, reviewed.consultation_proposal_digest,
    ))
    if (
        consultation_event is None or consultation_event.sequence >= request_event.sequence
        or (reviewed.session_id, reviewed.conversation_id, reviewed.step_id)
        != (event.session_id, event.conversation_id, event.step_id)
        or (reviewed.proposal_id, reviewed.proposal_revision, reviewed.proposal_digest)
        != (
            decision.reviewed_proposal_id, decision.reviewed_proposal_revision,
            decision.reviewed_proposal_digest,
        )
        or (reviewed.proposal_id, reviewed.proposal_digest)
        != (payload.reviewed_proposal_id, payload.reviewed_proposal_digest)
        or (decision.candidate_id, decision.candidate_revision, decision.candidate_digest)
        != (reviewed.candidate.candidate_id, reviewed.candidate.candidate_revision, reviewed.candidate.candidate_digest)
    ):
        return f"{name}: Apply result cannot be bound to its exact reviewed candidate."
    result_ref = synchronization_event_ref(event)
    application = SynchronizationApplication(payload.application_id, result_ref)
    actuals = tuple((ref, assessment) for ref, assessment in assessments if assessment.basis.application == application)
    if len(actuals) != 1:
        return f"{name}: exact applied synchronization assessment is missing or ambiguous."
    actual_ref, actual = actuals[0]
    expected_projected = replace(actual, basis=replace(actual.basis, application=None))
    projected = tuple((ref, assessment) for ref, assessment in assessments if assessment == expected_projected)
    if len(projected) != 1:
        return f"{name}: exact projected synchronization evidence is missing or ambiguous."
    projected_ref, projected_assessment = projected[0]
    result_artifacts = {item.path: item.sha256 for item in payload.result.final_artifacts}
    recorded = {item.path: item.digest for item in actual.census.documents if item.path in actual.census.changes}
    if (
        set(payload.result.affected_paths) != set(recorded)
        or {path: digest for path, digest in recorded.items() if digest is not None} != result_artifacts
    ):
        return f"{name}: physical Apply result differs from the exact reviewed document projection."
    review = reviewed.final_review_evidence
    synthesis = actual.basis.review_synthesis
    if (
        synthesis is None
        or (synthesis.conversation_id, synthesis.event_id)
        != (reviewed.conversation_id, reviewed.synthesis_event_id)
        or (actual.basis.task_id, actual.basis.task_revision) != (review.task_id, review.task_revision)
        or not set(reviewed.mandatory_specialist_ids).issubset(review.reviewer_ids)
        or (reviewed.unresolved_input_ids and request.conditional is None)
    ):
        return f"{name}: exact final specialist review, source, or USER conditions are missing or changed."
    selection = synchronization_repair_selection_for_task(
        tuple(events.values()), event.conversation_id,
        actual.basis.task_id, before_sequence=consultation_event.sequence,
    )
    if selection is not None and (
        actual.census.changes != (selection.repair_path,)
        or recorded_synchronization_repair_condition(selection, events) is None
    ):
        return f"{name}: exact synchronization repair authorization is missing or changed."
    return StoryCommitHistoryApplication(
        request, decision, payload, synchronization_event_ref(request_event),
        synchronization_event_ref(decision_event), result_ref, projected_ref, actual_ref,
        projected_assessment, actual, synchronization_event_ref(consultation_event),
        consultation_event.payload,
        repair_selection=selection,
    )


def _linked_work(
    previous: StoryCommitHistoryApplication,
    current: StoryCommitHistoryApplication,
    obligations: SynchronizationObligations,
    plans: dict[DocumentPlanRef, DocumentPlan],
) -> bool:
    previous_basis = previous.actual.basis
    current_basis = current.actual.basis
    if current.repair_selection is not None and (
        current.repair_selection.application == previous.result_ref
        or (
            previous.repair_selection is not None
            and current.repair_selection.application == previous.repair_selection.application
        )
    ):
        return True
    if previous_basis.task_id == current_basis.task_id:
        return previous_basis.task_revision <= current_basis.task_revision
    if previous_basis.request == current_basis.request or _linked_plan(previous, current, plans):
        return True
    previous_conditions = {
        state.condition.condition_id
        for head in obligations.heads
        for state in head.transition.conditions
        if state.condition.detected_by in {previous.projected_ref, previous.actual_ref}
    }
    return any(previous_conditions.intersection(finding.resolves) for finding in current.actual.findings)


def _linked_plan(
    previous: StoryCommitHistoryApplication,
    current: StoryCommitHistoryApplication,
    plans: dict[DocumentPlanRef, DocumentPlan],
) -> bool:
    previous_node = previous.consultation.document_node
    current_node = current.consultation.document_node
    if previous_node is None or current_node is None:
        return False
    previous_plan = plans.get(previous_node.plan_ref)
    current_plan = plans.get(current_node.plan_ref)
    if previous_plan is None or current_plan is None:
        return False
    if not (
        any(node.node_id == previous_node.node_id and node.target_path in previous.actual.census.changes
            for node in previous_plan.nodes)
        and any(node.node_id == current_node.node_id and node.target_path in current.actual.census.changes
                for node in current_plan.nodes)
    ):
        return False
    if previous_node.plan_ref == current_node.plan_ref:
        return True
    return (
        previous_plan.ref.plan_id == current_plan.ref.plan_id
        and previous_plan.ref.plan_revision < current_plan.ref.plan_revision
        and (previous_plan.goal_objective_id, previous_plan.goal_facts_digest, previous_plan.planning_goal)
        == (current_plan.goal_objective_id, current_plan.goal_facts_digest, current_plan.planning_goal)
    )


def _in_flight_blockers(
    events: tuple[CollaborationEvent, ...], active_commit_candidate_id: str | None,
) -> tuple[str, ...]:
    completed_applies = {
        (item.conversation_id, item.payload.decision_event_id)
        for item in events if type(item.payload) is ArtifactApplicationResultPayload
    }
    completed_commits = {
        (item.conversation_id, item.payload.decision_event_id)
        for item in events if type(item.payload) is StoryCommitResultPayload
    }
    approved_commits = [
        item for item in events
        if type(item.payload) is StoryCommitDecisionPayload
        and item.payload.decision.value == "commit"
        and (item.conversation_id, item.event_id) not in completed_commits
    ]
    active = [item for item in approved_commits if item.payload.candidate_id == active_commit_candidate_id]
    exempt = active[0] if len(active) == 1 else None
    decided_requests = {
        (item.conversation_id, item.payload.confirmation_request_event_id)
        for item in events if type(item.payload) is ArtifactApplicationDecisionPayload
    }
    blockers = [
        f"Commit {item.payload.candidate_id}: reconcile the pending Commit before preparing another."
        for item in approved_commits if item is not exempt
    ]
    for item in events:
        payload = item.payload
        if (
            type(payload) is ArtifactApplicationRequestPayload
            and (item.conversation_id, item.event_id) not in decided_requests
        ):
            blockers.append(
                f"Apply {payload.application_request.application_id}: resolve the pending Apply request."
            )
        if (
            type(payload) is ArtifactApplicationDecisionPayload
            and payload.decision is ArtifactApplicationDecisionKind.APPLY
            and (item.conversation_id, item.event_id) not in completed_applies
        ):
            blockers.append(f"Apply {payload.application_id}: approved Apply has no recorded outcome.")
        if type(payload) is ArtifactApplicationResultPayload and payload.outcome in {
            ArtifactApplicationOutcomeKind.OUTCOME_UNKNOWN, ArtifactApplicationOutcomeKind.RECOVERY_REQUIRED,
        }:
            blockers.append(f"Apply {payload.application_id}: reconcile the physical Apply outcome before committing.")
        if type(payload) is StoryCommitResultPayload and payload.outcome in {
            StoryCommitOutcomeKind.OUTCOME_UNKNOWN, StoryCommitOutcomeKind.RECOVERY_REQUIRED,
        }:
            blockers.append(f"Commit {payload.candidate_id}: reconcile the Git outcome before committing.")
    return tuple(sorted(set(blockers)))


def _basis_digest(
    session_id: str,
    baseline_state_digest: str,
    applications: tuple[StoryCommitHistoryApplication, ...],
    obligations: SynchronizationObligations,
    current: tuple[SynchronizationDocument, ...],
    prospective: tuple[SynchronizationDocument, ...],
    trace: tuple[CollaborationEvent, ...],
    supporting_assessments: tuple[SynchronizationEventRef, ...],
    blockers: tuple[str, ...],
) -> str:
    def ref(item: SynchronizationEventRef) -> tuple[str, str, str]:
        return item.conversation_id, item.event_id, item.digest

    def document_values(documents: tuple[SynchronizationDocument, ...]) -> list[tuple[str, str | None, str | None, str | None]]:
        return sorted((item.path, item.digest, item.revision, item.absence) for item in documents)

    def application_row(item: StoryCommitHistoryApplication) -> tuple:
        references = tuple(map(ref, (
            item.request_ref, item.decision_ref, item.result_ref, item.consultation_ref,
            item.projected_ref, item.actual_ref,
        )))
        if item.repair_selection is None:
            return references
        return (*references, synchronization_repair_selection_to_dict(item.repair_selection))

    return review_digest({
        "session": session_id,
        "baseline": baseline_state_digest,
        "applications": [application_row(item) for item in applications],
        "obligations": sorted(ref(item.ref) for item in obligations.heads),
        "supporting_assessments": sorted(map(ref, supporting_assessments)),
        "current": document_values(current),
        "prospective": document_values(prospective),
        "trace": sorted(ref(synchronization_event_ref(item)) for item in trace),
        "blockers": blockers,
    })


def qualify_story_commit_history(
    session_id: str,
    events: tuple[CollaborationEvent, ...],
    *,
    application_id: str,
    baseline_state_digest: str,
    current_documents: tuple[SynchronizationDocument, ...],
    committed_application_ids: frozenset[str] = frozenset(),
    pending: tuple[CollaborationEventDraft, ...] = (),
    prospective_documents: tuple[SynchronizationDocument, ...] | None = None,
    active_commit_candidate_id: str | None = None,
) -> StoryCommitHistoryQualification:
    """Qualify exact Apply lineage without consulting any clock, Git or store."""
    events = _with_pending_events(events, pending)
    identities = {(item.conversation_id, item.event_id) for item in events}
    positions = {(item.conversation_id, item.sequence) for item in events}
    if (
        any(item.session_id != session_id for item in events)
        or len(identities) != len(events) or len(positions) != len(events)
    ):
        raise ValueError("story commit history must be one exact session event set")
    index = {(item.conversation_id, item.event_id): item for item in events}
    histories: dict[str, list[CollaborationEvent]] = {}
    for item in events:
        histories.setdefault(item.conversation_id, []).append(item)
    synchronization = project_synchronization_history(
        tuple(tuple(sorted(items, key=lambda item: item.sequence)) for items in histories.values()),
        session_complete=True,
    )
    requests = {(item.conversation_id, item.event_id): item for item in events if type(item.payload) is ArtifactApplicationRequestPayload}
    decisions = {(item.conversation_id, item.event_id): item for item in events if type(item.payload) is ArtifactApplicationDecisionPayload}
    results = tuple(
        item for item in events
        if type(item.payload) is ArtifactApplicationResultPayload
        and item.payload.application_id not in committed_application_ids
    )
    triggers = tuple(item for item in results if item.payload.application_id == application_id)
    consultations = {
        (item.conversation_id, item.payload.proposal_id, item.payload.proposal_revision, item.payload.proposal_digest): item
        for item in events if type(item.payload) is CollaborationProposal
    }
    plans = {
        item.payload.document_plan.ref: item.payload.document_plan
        for item in events
        if type(item.payload) in {CollaborationProposal, ArtifactObjectivePayload, ArtifactObjectivePayloadV2}
        and item.payload.document_plan is not None
    }
    blockers = list(_in_flight_blockers(events, active_commit_candidate_id))
    chain: list[StoryCommitHistoryApplication] = []
    if not synchronization.complete:
        blockers.append("The session's synchronization evidence is incomplete.")
    if len(triggers) != 1 or triggers[0].payload.outcome not in _SUCCEEDED:
        blockers.append(f"Apply {application_id}: successful session result is missing or ambiguous.")
    else:
        by_digest: dict[str, list[CollaborationEvent]] = {}
        for request_event in requests.values():
            request = request_event.payload.application_request
            by_digest.setdefault(request.change_set.projected_state_digest, []).append(request_event)
        predecessor = triggers[0]
        used: set[str] = set()
        while True:
            evidence = _applied_evidence(predecessor, requests, decisions, synchronization.assessments, consultations, index)
            if isinstance(evidence, str):
                blockers.append(evidence)
                break
            if evidence.application_id in used:
                blockers.append("Apply history repeats an application before reaching the Git baseline.")
                break
            if chain and not _linked_work(evidence, chain[-1], synchronization.obligations, plans):
                blockers.append(f"Apply {evidence.application_id} and Apply {chain[-1].application_id} have no authorized work link.")
                break
            chain.append(evidence)
            used.add(evidence.application_id)
            base = evidence.candidate.change_set.base_state_digest
            if base == baseline_state_digest:
                break
            prior_ids = {
                item.payload.application_request.application_id
                for item in by_digest.get(base, ())
                if item.payload.application_request.application_id not in used
                and item.payload.application_request.change_set.base_state_digest != base
            }
            options = tuple(
                item for item in results
                if item.payload.application_id in prior_ids
                and item.payload.outcome in _SUCCEEDED
                and (item.conversation_id != predecessor.conversation_id or item.sequence < predecessor.sequence)
            )
            if len(options) != 1:
                blockers.append(f"Apply {evidence.application_id}: no unique applied predecessor reaches the Git baseline.")
                break
            predecessor = options[0]
    applications = tuple(reversed(chain))
    prospective = current_documents if prospective_documents is None else prospective_documents
    coverage = qualify_story_commit_documents(
        tuple(
            StoryCommitReviewCoverage(
                item.candidate.change_set, item.request.reviewed_proposal.final_review_evidence,
                item.actual_ref, item.actual,
            )
            for item in applications
        ),
        synchronization, current_documents, prospective,
    )
    blockers.extend(coverage.blockers)
    trace: tuple[CollaborationEvent, ...] = ()
    if applications:
        revisions = {item.actual.basis.task_id: item.actual.basis.task_revision for item in applications}
        for event in consultations.values():
            scope = event.payload.authoring_scope
            if scope is not None and scope.task_id in revisions and scope.task_revision > revisions[scope.task_id]:
                blockers.append(f"Task {scope.task_id}: verify the advanced task revision before committing.")
        assessments = dict(synchronization.assessments)
        for item in applications:
            resolved = ()
            if item.request.conditional is not None:
                resolved = resolved_conditional_findings(
                    index[(item.request_ref.conversation_id, item.request_ref.event_id)],
                    item.projected_ref, item.actual, coverage.obligations, assessments, index,
                )
                if resolved is None:
                    blockers.append(f"Apply {item.application_id}: exact conditional disclosure is unavailable.")
                elif not conditional_reviews_are_covered(item.request.reviewed_proposal, events, resolved):
                    blockers.append(f"Apply {item.application_id}: final specialist concerns lack exact current resolution.")
            discharged = {
                f"{finding.changed_path} → {finding.target_path}: {finding.judgment.value}"
                for finding in resolved or ()
            }
            blockers.extend(
                f"Apply {item.application_id}: {reason}"
                for reason in item.actual.blockers if reason not in discharged
            )
        ids = {item.application_id for item in applications}
        state_digests = {
            digest for item in applications
            for digest in (item.candidate.change_set.base_state_digest, item.candidate.change_set.projected_state_digest)
        }
        related = {
            item.payload.application_request.application_id
            for item in requests.values()
            if item.payload.application_request.change_set.base_state_digest in state_digests
            or item.payload.application_request.change_set.projected_state_digest in state_digests
        }
        trace = tuple(sorted(
            (item for item in results if item.payload.application_id in related - ids),
            key=lambda item: (item.conversation_id, item.sequence),
        ))
        for item in trace:
            if item.payload.outcome in _SUCCEEDED:
                blockers.append(f"Apply {item.payload.application_id}: competing successful result on the bundle's document history.")
    reasons = tuple(dict.fromkeys(blockers))
    return StoryCommitHistoryQualification(
        applications,
        _basis_digest(
            session_id, baseline_state_digest, applications, coverage.obligations,
            current_documents, prospective, trace, coverage.supporting_assessments, reasons,
        ),
        reasons,
    )
