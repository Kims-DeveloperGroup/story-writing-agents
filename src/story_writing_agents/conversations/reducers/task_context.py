"""Apply task provenance and task views to a history projection."""

from __future__ import annotations

from dataclasses import replace

from ..artifact_revision_sources import artifact_revision_source
from ..event_contracts.artifact_apply import ArtifactApplicationResultPayload
from ..event_contracts.artifact_reviews import (
    ArtifactReviewCandidatePayload,
    ArtifactReviewedProposal,
)
from ..event_contracts.authoring_records import (
    CollaborationLeadDiscussionPayload,
    CollaborationLeadMaterialCapturePayload,
    CollaborationLeadPublicResponsePayload,
    CollaborationLeadTaskDecisionPayload,
    CollaborationLeadTaskTransitionPayload,
)
from ..event_contracts.control_records import (
    CollaborationConfirmationPayload,
    CollaborationProviderCallPayload,
    CollaborationStepStatePayload,
)
from ..event_contracts.execution import (
    CollaborationExecutionResult,
    CollaborationMessagePayload,
    CollaborationSpecialistReplyPayload,
)
from ..event_contracts.lead_outcomes import (
    CollaborationLeadJudgmentPayload,
    CollaborationLeadOutcomeFailurePayload,
    authoring_scope_is_view,
)
from ..event_contracts.synchronization import SynchronizationPayload
from ..event_vocabulary import (
    ArtifactApplicationOutcomeKind,
    CollaborationActorKind,
    CollaborationConfirmationDecision,
    CollaborationDirection,
    CollaborationEventKind as CollaborationEventKindV2,
    CollaborationExecutionOutcomeKind,
    CollaborationInputKind,
    CollaborationInputState,
    CollaborationInvocationKind,
    CollaborationProviderCallState,
    CollaborationSpecialistAssessment,
    CollaborationStepState,
    LeadOutcomeKind,
)
from ..events import (
    CollaborationLeadSynthesisPayload,
    CollaborationProposal,
    LeadRecommendationActionKind,
    LeadRecommendationDecisionPayload,
    LeadRecommendationRequestPayload,
    LeadRecommendationResultPayload,
    LeadRecommendationStartedPayload,
    LeadRecommendationState,
    authoring_capture_identity,
    confirmation_task_bindings,
    confirmed_authoring_proposals,
    synchronization_event_ref,
)
from .history_projection import CollaborationHistoryProjection
from .readiness_history import validate_readiness_owner_prefix
from .recommendation_history import recommendation_key as _recommendation_key
from .task_source_history import (
    public_response_omission_call as _public_response_omission_call,
    task_scope_digest as _task_scope_digest,
    task_user_origins as _task_user_origins,
    validate_authoring_admission_provenance as _validate_authoring_admission_provenance,
)
from ..views.collaboration_history_values import (
    CollaborationNextAction,
    LeadMaterialChoice,
    LeadPublicResponseView,
    LeadTaskView,
)
from ...lead_task_transitions import (
    LeadMaterialKind,
    LeadMaterialRef,
    LeadTaskAction,
    LeadTaskRef,
    LeadTaskTransitionReceipt,
    task_material_sha256,
)
from ...specialist_routing import (
    AuthoringActivity,
    AuthoringIntent,
    AuthoringScope,
)
from ...writing_artifacts import ArtifactRenameOperation


def project_task_context(
    events: tuple[CollaborationEvent, ...], projection: CollaborationHistoryProjection,
    *, _source_history: tuple[CollaborationEvent, ...] = (),
) -> CollaborationHistoryProjection:
    """Validate USER controls and project literal material without execution proof."""
    def _authoring_source_prior(subject: CollaborationEvent) -> dict[str, CollaborationEvent]:
        source_prior: dict[str, CollaborationEvent] = {}
        for original in _source_history or events:
            if (original.session_id, original.conversation_id) != (
                subject.session_id, subject.conversation_id,
            ):
                raise ValueError("authoring admission source history changed owner")
            if original.event_id == subject.event_id:
                if original != subject:
                    raise ValueError("authoring admission source subject changed")
                return source_prior
            if original.sequence >= subject.sequence or original.event_id in source_prior:
                raise ValueError("authoring admission source prefix is not exact")
            source_prior[original.event_id] = original
        raise ValueError("authoring admission source subject is unavailable")


    def _ordinary_supersedes_artifact_revision(receipt, subject, history) -> bool:
        """A new literal USER request ends only its exact unconfirmed preparation plan."""
        proposal = subject.payload
        scope = proposal.authoring_scope
        if (scope.schema_version != 3 or scope.intent is not AuthoringIntent.AUTHORING
                or scope.activity is not AuthoringActivity.REVIEW
                or proposal.proposal_revision != 1 or subject.step_id == receipt.planning_step_id
                or confirmed_authoring_proposals(scope, history)):
            return False
        by_id = {item.event_id: item for item in history}
        origin = by_id.get(receipt.origin_event_id)
        if origin is None or type(origin.payload) is not CollaborationConfirmationPayload:
            return False
        originals = tuple(item.payload for item in history
            if type(item.payload) is CollaborationProposal and
            (item.payload.proposal_id, item.payload.proposal_revision, item.payload.proposal_digest)
            == (origin.payload.proposal_id, origin.payload.proposal_revision, origin.payload.proposal_digest))
        if (len(originals) != 1 or proposal.proposal_id == originals[0].proposal_id
                or not artifact_revision_source(receipt, originals[0], history)):
            return False
        original = originals[0]
        publication = (subject.session_id, subject.conversation_id, subject.turn_id,
            subject.step_id, subject.batch_id)
        candidates = tuple(item for item in events
            if (item.session_id, item.conversation_id, item.turn_id, item.step_id, item.batch_id) == publication
            and type(item.payload) is ArtifactReviewCandidatePayload
            and (item.payload.consultation_proposal_id, item.payload.consultation_proposal_revision,
                 item.payload.consultation_proposal_digest)
            == (proposal.proposal_id, proposal.proposal_revision, proposal.proposal_digest))
        if len(candidates) != 1:
            return False
        for item in history:
            request = item.payload
            if (type(request) is not LeadRecommendationRequestPayload
                    or request.kind is not LeadRecommendationActionKind.PREPARE_ARTIFACT
                    or request.source_execution_attempt_id is not None
                    or request.authoring_scope is None
                    or (request.authoring_scope.task_id, request.authoring_scope.task_revision)
                    != (receipt.target_task_id, receipt.target_task_revision)
                    or (request.source_proposal_id, request.source_proposal_revision, request.source_proposal_digest)
                    != (original.proposal_id, original.proposal_revision, original.proposal_digest)):
                continue
            source = by_id.get(request.source_recommendation_event_id)
            if (source is None or type(source.payload) is not CollaborationLeadJudgmentPayload
                    or source.payload.kind is not LeadOutcomeKind.RECOMMENDATION
                    or source.step_id != receipt.planning_step_id or source.payload.authoring_scope is None
                    or (source.payload.authoring_scope.task_id, source.payload.authoring_scope.task_revision)
                    != (receipt.target_task_id, receipt.target_task_revision)):
                continue
            key = _recommendation_key(request)
            if any(type(row.payload) in {LeadRecommendationDecisionPayload, LeadRecommendationStartedPayload}
                   and _recommendation_key(row.payload) == key for row in history):
                continue
            closures = tuple(row for row in history
                if type(row.payload) is LeadRecommendationResultPayload
                and _recommendation_key(row.payload) == key)
            if len(closures) != 1:
                continue
            closure = closures[0]
            result = closure.payload
            user = by_id.get(result.superseding_user_event_id)
            if (result.state is LeadRecommendationState.SUPERSEDED
                    and result.decision_event_id is None and result.start_event_id is None
                    and result.lead_result_event_id is None
                    and user is not None and type(user.payload) is CollaborationMessagePayload
                    and user.sender.kind is CollaborationActorKind.USER
                    and user.direction is CollaborationDirection.USER_TO_LEAD
                    and (user.session_id, user.conversation_id, user.turn_id, user.step_id)
                    == publication[:4]
                    and (closure.session_id, closure.conversation_id, closure.turn_id, closure.step_id)
                    == publication[:4]
                    and user.batch_id == closure.batch_id
                    and user.sequence + 1 == closure.sequence < subject.sequence):
                return True
        return False

    prior: dict[str, CollaborationEvent] = {}
    tasks: dict[str, tuple[CollaborationEvent, AuthoringScope]] = {}
    confirmations = confirmation_task_bindings(events)
    pinned = set()
    goals: dict[str, str] = {}
    statuses: dict[str, str] = {}
    transitions: dict[str, CollaborationEvent] = {}
    captures: dict[str, CollaborationEvent] = {}
    choices: list[LeadMaterialChoice] = []
    public: list[LeadPublicResponseView] = []
    submitted_calls: dict[tuple[str, str], CollaborationEvent] = {}
    discussion_prepared: dict[tuple[str, str], CollaborationEvent] = {}
    discussion_submitted: set[tuple[str, str]] = set()
    discussion_ordinals: dict[str, int] = {}
    resumptions: dict[str, LeadTaskTransitionReceipt] = {}
    discussion: LeadTaskTransitionReceipt | None = None

    def correlated(left: CollaborationEvent, right: CollaborationEvent) -> bool:
        return (left.session_id, left.conversation_id, left.turn_id, left.step_id,
                left.batch_id) == (right.session_id, right.conversation_id,
                                  right.turn_id, right.step_id, right.batch_id)

    def source_scope(ref: LeadTaskRef) -> AuthoringScope:
        source = prior.get(ref.scope_event_id)
        scope = getattr(source.payload, "authoring_scope", None) if source is not None else None
        if source is None or scope is None or (
            source.session_id, source.conversation_id, scope.task_id,
            scope.task_revision, _task_scope_digest(scope),
        ) != (ref.session_id, ref.conversation_id, ref.task_id,
              ref.task_revision, ref.scope_digest):
            raise ValueError("task control source scope is not exact")
        latest = tasks.get(ref.task_id)
        if latest is None or latest[1].task_revision != ref.task_revision:
            raise ValueError("task control requires the current task revision")
        return scope

    for event in events:
        if event.event_id in confirmations:
            pinned.add(confirmations[event.event_id])
        payload = event.payload
        if (type(payload) is CollaborationMessagePayload
                and event.sender.kind is CollaborationActorKind.RUNTIME
                and payload.content.startswith("Public response unavailable for ")
                and _public_response_omission_call(event, tuple(prior.values())) is None):
            raise ValueError("public omission notice requires exact submitted-call display facts")
        scope = getattr(payload, "authoring_scope", None)
        if type(payload) is CollaborationLeadJudgmentPayload and payload.core_document_readiness is not None:
            validate_readiness_owner_prefix(event, _authoring_source_prior(event))
        if type(payload) in {CollaborationProposal, LeadRecommendationRequestPayload}:
            _validate_authoring_admission_provenance(payload, _authoring_source_prior(event), statuses)
            if payload.basis.core_document_readiness is not None:
                validate_readiness_owner_prefix(event, _authoring_source_prior(event))
        if type(payload) is CollaborationProposal and scope is not None:
            planning = next((control.payload.receipt for control in reversed(tuple(transitions.values()))
                             if (control.payload.receipt.target_task_id,
                                 control.payload.receipt.target_task_revision)
                             == (scope.task_id, scope.task_revision)), None)
            if (planning is not None and _ordinary_supersedes_artifact_revision(
                    planning, event, tuple(prior.values()))):
                planning = None
            if planning is not None:
                source_review_revision = False
                source_artifact_revision = False
                prepared_final_review = False
                if scope.activity is AuthoringActivity.REVIEW:
                    past = tuple(prior.values())
                    origin = prior.get(planning.origin_event_id)
                    original = next((
                        item.payload for item in past
                        if type(item.payload) is CollaborationProposal
                        and origin is not None
                        and type(origin.payload) is CollaborationConfirmationPayload
                        and (
                            item.payload.proposal_id,
                            item.payload.proposal_revision,
                            item.payload.proposal_digest,
                        ) == (
                            origin.payload.proposal_id,
                            origin.payload.proposal_revision,
                            origin.payload.proposal_digest,
                        )
                    ), None)
                    source_review_revision = bool(
                        planning.origin_kind == "revise_decision"
                        and origin is not None
                        and type(origin.payload) is CollaborationConfirmationPayload
                        and origin.payload.decision is CollaborationConfirmationDecision.REVISE
                        and original is not None
                        and original.authoring_scope is not None
                        and original.authoring_scope.activity is AuthoringActivity.REVIEW
                        and planning.source_task_ref is not None
                        and (
                            original.authoring_scope.task_id,
                            original.authoring_scope.task_revision,
                        ) == (
                            planning.source_task_ref.task_id,
                            planning.source_task_ref.task_revision,
                        )
                        and scope.task_id == original.authoring_scope.task_id
                        and scope.task_revision == original.authoring_scope.task_revision + 1
                        and event.step_id == planning.planning_step_id == origin.step_id == original.step_id
                        and payload.proposal_revision == original.proposal_revision + 1
                        and not planning.material_capture_event_ids
                        and _task_scope_digest(original.authoring_scope) == planning.source_task_ref.scope_digest
                        and not any(
                            item.batch_id == event.batch_id
                            and type(item.payload) is ArtifactReviewCandidatePayload
                            and (
                                item.payload.consultation_proposal_id,
                                item.payload.consultation_proposal_revision,
                                item.payload.consultation_proposal_digest,
                            ) == (payload.proposal_id, payload.proposal_revision, payload.proposal_digest)
                            for item in events
                        )
                        and not any(
                            type(item.payload) is ArtifactReviewCandidatePayload
                            and (
                                item.payload.consultation_proposal_id,
                                item.payload.consultation_proposal_revision,
                                item.payload.consultation_proposal_digest,
                            ) == (
                                original.proposal_id, original.proposal_revision, original.proposal_digest,
                            )
                            for item in past
                        )
                    )
                    request = next((
                        item.payload for item in reversed(past)
                        if item.step_id == event.step_id
                        and type(item.payload) is LeadRecommendationRequestPayload
                        and item.payload.kind is LeadRecommendationActionKind.PREPARE_ARTIFACT
                    ), None)
                    source = None if request is None else next((
                        item.payload for item in past
                        if type(item.payload) is CollaborationProposal
                        and (
                            item.payload.proposal_id,
                            item.payload.proposal_revision,
                            item.payload.proposal_digest,
                        ) == (
                            request.source_proposal_id,
                            request.source_proposal_revision,
                            request.source_proposal_digest,
                        )
                    ), None)
                    prepared_final_review = bool(
                        request is not None
                        and request.source_execution_attempt_id is not None
                        and request.authoring_scope is not None
                        and request.authoring_scope.activity in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE}
                        and (request.authoring_scope.task_id, request.authoring_scope.task_revision)
                        == (scope.task_id, scope.task_revision)
                        and source is not None
                        and source.authoring_scope is not None
                        and source.authoring_scope.activity in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE}
                        and source in confirmed_authoring_proposals(scope, past)
                        and authoring_capture_identity(scope, payload.basis)
                        == authoring_capture_identity(source.authoring_scope, source.basis)
                        and any(
                            item.batch_id == event.batch_id
                            and item.turn_id == event.turn_id
                            and item.step_id == event.step_id
                            and type(item.payload) is ArtifactReviewCandidatePayload
                            and (
                                item.payload.consultation_proposal_id,
                                item.payload.consultation_proposal_revision,
                                item.payload.consultation_proposal_digest,
                            ) == (payload.proposal_id, payload.proposal_revision, payload.proposal_digest)
                            for item in events
                        )
                    )
                    source_artifact_revision = artifact_revision_source(planning, original, past)
                    if source_artifact_revision:
                        direct_replacement = bool(
                            event.step_id == planning.planning_step_id
                            and payload.proposal_id != original.proposal_id
                            and payload.proposal_revision == original.proposal_revision + 1
                        )
                        request_event = next((
                            item for item in reversed(past)
                            if item.step_id == event.step_id
                            and type(item.payload) is LeadRecommendationRequestPayload
                            and item.payload.kind is LeadRecommendationActionKind.PREPARE_ARTIFACT
                        ), None)
                        preparation_start = None if request_event is None else next((
                            item for item in reversed(past)
                            if item.step_id == event.step_id
                            and type(item.payload) is LeadRecommendationStartedPayload
                            and item.payload.request_event_id == request_event.event_id
                        ), None)
                        preparation_decision = None if preparation_start is None else prior.get(
                            preparation_start.payload.decision_event_id
                        )
                        preparation_source = None if request_event is None else prior.get(
                            request_event.payload.source_recommendation_event_id
                        )
                        prepared_replacement = bool(
                            request_event is not None
                            and request_event.payload.authoring_scope == scope
                            and (request_event.payload.source_proposal_id,
                                 request_event.payload.source_proposal_revision,
                                 request_event.payload.source_proposal_digest)
                            == (original.proposal_id, original.proposal_revision, original.proposal_digest)
                            and request_event.payload.source_execution_attempt_id is None
                            and preparation_source is not None
                            and preparation_source.step_id == planning.planning_step_id
                            and type(preparation_source.payload) is CollaborationLeadJudgmentPayload
                            and preparation_source.payload.kind is LeadOutcomeKind.RECOMMENDATION
                            and preparation_source.payload.authoring_scope == scope
                            and preparation_start is not None
                            and preparation_decision is not None
                            and type(preparation_decision.payload) is LeadRecommendationDecisionPayload
                            and preparation_decision.payload.decision.kind is CollaborationConfirmationDecision.CONFIRM
                            and any(
                                item.batch_id == event.batch_id
                                and item.step_id == event.step_id
                                and type(item.payload) is LeadRecommendationResultPayload
                                and item.payload.state is LeadRecommendationState.COMPLETED
                                and item.payload.request_event_id == request_event.event_id
                                and item.payload.start_event_id == preparation_start.event_id
                                and item.payload.decision_event_id == preparation_decision.event_id
                                for item in events
                            )
                        )
                        new_candidates = tuple(
                            item for item in events
                            if item.batch_id == event.batch_id
                            and item.step_id == event.step_id
                            and type(item.payload) is ArtifactReviewCandidatePayload
                            and (item.payload.consultation_proposal_id,
                                 item.payload.consultation_proposal_revision,
                                 item.payload.consultation_proposal_digest)
                            == (payload.proposal_id, payload.proposal_revision, payload.proposal_digest)
                        )
                        source_artifact_revision = bool(
                            (direct_replacement or prepared_replacement)
                            and (scope.task_id, scope.task_revision)
                            == (planning.target_task_id, planning.target_task_revision)
                            and len(new_candidates) == 1
                        )
                control = prior.get(planning.origin_event_id)
                decision = (
                    control.payload if control is not None
                    and type(control.payload) is CollaborationLeadTaskDecisionPayload else None
                )
                selection = decision.document_selection if decision is not None else None
                repair = decision.synchronization_repair if decision is not None else None
                if repair is not None:
                    targets = set(scope.target_paths)
                    read_only = scope.intent is AuthoringIntent.REVIEW_ONLY
                    if (read_only and (len(targets) != 2 or repair.repair_path not in targets)) or (
                        not read_only and scope.target_paths != (repair.repair_path,)
                    ):
                        raise ValueError("task proposal exceeds the exact synchronization repair scope")
                    if repair.head.conversation_id == event.conversation_id:
                        head = (_authoring_source_prior(event) if _source_history else prior).get(repair.head.event_id)
                        condition = next((
                            state.condition for state in head.payload.transition.conditions
                            if state.condition.condition_id == repair.condition_id
                        ), None) if (
                            head is not None and synchronization_event_ref(head) == repair.head
                            and type(head.payload) is SynchronizationPayload and head.payload.transition is not None
                        ) else None
                        if (
                            condition is None or condition.domain not in scope.domains
                            or repair.repair_path not in {condition.changed_path, condition.target_path}
                            or (read_only and targets != {condition.changed_path, condition.target_path})
                        ):
                            raise ValueError("task proposal changed the selected synchronization condition")
                selected_node = payload.document_node
                selected_document = bool(selection is not None and selected_node is not None
                    and (selection.plan_ref == selected_node.plan_ref or (payload.document_plan is not None
                        and selection.plan_ref.plan_id == selected_node.plan_ref.plan_id))
                    and ((selection.node_id is not None and selection.node_id == selected_node.node_id)
                        or (selection.target_path is not None and scope.target_paths == (selection.target_path,))))
                selected_repair = repair is not None and planning.action is LeadTaskAction.NEW_TASK
                selected_review = bool(scope.schema_version == 3 and scope.activity is AuthoringActivity.REVIEW
                    and planning.origin_kind == "task_decision" and planning.action in {LeadTaskAction.NEW_TASK, LeadTaskAction.RESUME}
                    and (scope.task_id, scope.task_revision) == (planning.target_task_id, planning.target_task_revision)
                    and (selected_repair or (selected_document and len(scope.target_paths) == 1)))
                if selected_review and scope.intent is AuthoringIntent.AUTHORING:
                    message = prior.get(planning.origin_message_event_id)
                    # Existing admission validation qualifies actual USER bytes. A real
                    # co-batched exact candidate distinguishes this USER-candidate path.
                    selected_review = bool(message is not None
                        and type(message.payload) is CollaborationMessagePayload
                        and message.sender.kind is CollaborationActorKind.USER
                        and (message.turn_id, message.step_id) == (control.turn_id, control.step_id)
                        and payload.basis.authoring_admission is not None
                        and any(type(item.payload) is ArtifactReviewCandidatePayload
                            and item.batch_id == event.batch_id
                            and (item.payload.consultation_proposal_id, item.payload.consultation_proposal_revision,
                                 item.payload.consultation_proposal_digest) ==
                                (payload.proposal_id, payload.proposal_revision, payload.proposal_digest)
                            and {path for op in item.payload.candidate.change_set.operations for path in
                                ((op.source_path, op.destination_path) if type(op) is ArtifactRenameOperation else (op.path,))} == set(scope.target_paths)
                            for item in events))
                elif selected_review:
                    selected_review = scope.intent is AuthoringIntent.REVIEW_ONLY and (
                        not selected_repair or not any(
                            type(item.payload) is ArtifactReviewCandidatePayload
                            and item.batch_id == event.batch_id and item.step_id == event.step_id
                            for item in events
                        )
                    )
                if (not selected_review and (scope.schema_version != 3 or scope.intent is not AuthoringIntent.AUTHORING
                        or (scope.activity not in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE}
                            and not (source_review_revision or source_artifact_revision) and not prepared_final_review))):
                    raise ValueError("task transition requires fresh authoring scope and owner drafts")
                required = tuple(f"selected_material_{index}" for index in
                                 range(1, len(planning.material_capture_event_ids) + 1))
                for alias, identity in zip(required, planning.material_capture_event_ids, strict=True):
                    captured = captures[identity].payload.capture
                    matching = tuple(item for item in payload.basis.inputs if item.input_id == alias)
                    if (len(matching) != 1 or matching[0].kind is not CollaborationInputKind.CONVERSATION_EVENT
                            or matching[0].state is not CollaborationInputState.PRESENT
                            or matching[0].locator != f"conversation/{identity}"
                            or matching[0].revision_digest != f"sha256:{captured.material_digest}"
                            or (matching[0].start_line is not None or matching[0].end_line is not None)
                            or any(alias not in assignment.input_ids for assignment in payload.assignments)):
                        raise ValueError("task proposal must retain each exact whole selected input for every owner")
        if (scope is not None and scope.activity is not AuthoringActivity.WORKFLOW
                and not authoring_scope_is_view(payload, pinned)):
            previous = tasks.get(scope.task_id)
            if previous is None or scope.task_revision > previous[1].task_revision or (
                scope.task_revision == previous[1].task_revision
                and scope.activity in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE}
            ):
                tasks[scope.task_id] = event, scope
                statuses.setdefault(scope.task_id, "active")
            if scope.task_id not in goals:
                user = next((item for item in prior.values()
                             if (item.turn_id, item.step_id) == (event.turn_id, event.step_id)
                             and type(item.payload) is CollaborationMessagePayload
                             and item.sender.kind is CollaborationActorKind.USER), None)
                goals[scope.task_id] = scope.interpretation if user is None else user.payload.content
            pending = resumptions.get(scope.task_id)
            if (
                type(payload) is CollaborationProposal
                and pending is not None
                and (scope.task_id, scope.task_revision)
                == (pending.target_task_id, pending.target_task_revision)
            ):
                statuses[scope.task_id] = "active"
        if (type(payload) is CollaborationProviderCallPayload
                and payload.invocation_kind is CollaborationInvocationKind.LEAD_DISCUSSION):
            control = next((item for item in transitions.values()
                            if item.payload.receipt.action is LeadTaskAction.DISCUSS
                            and (item.turn_id, item.step_id) == (event.turn_id, event.step_id)), None)
            if control is None:
                raise ValueError("discussion call requires prior exact USER discussion receipt")
            key = payload.invocation_id, payload.call_id
            if payload.state is CollaborationProviderCallState.PREPARED:
                if key in discussion_prepared or payload.ordinal != discussion_ordinals.get(payload.invocation_id, 0) + 1:
                    raise ValueError("discussion preparation requires unique ordered physical call")
                discussion_prepared[key] = event
                discussion_ordinals[payload.invocation_id] = payload.ordinal
            else:
                prepared = discussion_prepared.get(key)
                if (prepared is None or key in discussion_submitted
                        or (prepared.session_id, prepared.conversation_id, prepared.turn_id, prepared.step_id,
                            prepared.payload.ordinal, prepared.payload.call_kind)
                        != (event.session_id, event.conversation_id, event.turn_id, event.step_id,
                            payload.ordinal, payload.call_kind)):
                    raise ValueError("discussion submission requires exact prepared call identity")
                discussion_submitted.add(key)
                if sum(identity[0] == payload.invocation_id for identity in discussion_submitted) > 6:
                    raise ValueError("discussion exceeds existing five-call front plus one correction")
        if type(payload) is CollaborationProviderCallPayload and payload.state is CollaborationProviderCallState.SUBMITTED:
            submitted_calls[(payload.invocation_id, payload.call_id)] = event
        if type(payload) is CollaborationLeadTaskDecisionPayload:
            decision = payload.decision
            if event.sender.kind is not CollaborationActorKind.USER or (
                event.event_id, event.session_id, event.conversation_id,
            ) != (decision.request_id, decision.session_id, decision.conversation_id):
                raise ValueError("task decision must be exact USER intake")
            if decision.task_ref is not None:
                source_scope(decision.task_ref)
                if decision.action in {LeadTaskAction.RESUME, LeadTaskAction.HOLD,
                                       LeadTaskAction.DISCARD} and statuses.get(
                    decision.task_ref.task_id,
                ) in {"discarded", "completed"}:
                    raise ValueError("retired task cannot regain workflow authority")
        elif type(payload) is CollaborationLeadMaterialCapturePayload:
            capture = payload.capture
            origin = prior.get(capture.origin_event_id)
            source = prior.get(capture.source.event_id)
            if origin is None or type(origin.payload) is not CollaborationLeadTaskDecisionPayload:
                raise ValueError("material capture requires exact USER choice")
            if not correlated(origin, event) or capture.source not in origin.payload.decision.material_refs:
                raise ValueError("material capture is detached from USER selection")
            if capture.source.conversation_id == event.conversation_id:
                if source is None or (source.session_id, source.conversation_id) != (
                    capture.source.session_id, capture.source.conversation_id,
                ):
                    raise ValueError("selected material source is unavailable")
                selected = next((item for item in choices if item.ref == capture.source), None)
                if selected is None or selected.text != capture.selected_text:
                    raise ValueError("selected material event/type/bytes changed")
            elif capture.source.session_id != event.session_id:
                raise ValueError("foreign material must belong to the bound session")
            # Foreign source existence/type/bytes are revalidated against the exact
            # SessionHistorySnapshot at intake, context delivery, and Confirm.

            captures[event.event_id] = event
        elif type(payload) is CollaborationLeadTaskTransitionPayload:
            receipt = payload.receipt
            origin = prior.get(receipt.origin_event_id)
            recommendation_origin = (origin is not None
                                     and type(origin.payload) is LeadRecommendationDecisionPayload
                                     and receipt.origin_kind == "revise_decision")
            same_origin_batch = (origin is not None
                                 and (origin.session_id, origin.conversation_id, origin.turn_id, origin.batch_id)
                                 == (event.session_id, event.conversation_id, event.turn_id, event.batch_id))
            if (origin is None or (not correlated(origin, event)
                    and not (recommendation_origin and same_origin_batch
                             and receipt.planning_step_id == event.step_id))):
                raise ValueError("task transition requires exact co-batched USER origin")
            if receipt.origin_kind == "task_decision":
                if type(origin.payload) is not CollaborationLeadTaskDecisionPayload:
                    raise ValueError("task transition origin type is invalid")
                decision = origin.payload.decision
                if (decision.action, decision.task_ref) != (receipt.action, receipt.source_task_ref):
                    raise ValueError("task transition changed the USER control")
            else:
                if recommendation_origin:
                    decision_value = origin.payload.decision
                    requested = prior.get(decision_value.request_event_id)
                    if (decision_value.kind is not CollaborationConfirmationDecision.REVISE
                            or requested is None or type(requested.payload) is not LeadRecommendationRequestPayload
                            or origin.step_id != requested.step_id
                            or (receipt.action is LeadTaskAction.RESUME and (
                                receipt.source_task_ref is None or requested.payload.authoring_scope is None
                                or (receipt.source_task_ref.task_id, receipt.source_task_ref.task_revision)
                                != (requested.payload.authoring_scope.task_id,
                                    requested.payload.authoring_scope.task_revision)))
                            or (receipt.action is LeadTaskAction.DISCUSS
                                and (receipt.source_task_ref is not None or requested.payload.authoring_scope is not None))):
                        raise ValueError("recommendation revision origin and current source are not exact")
                elif (type(origin.payload) is not CollaborationConfirmationPayload
                        or origin.payload.decision is not CollaborationConfirmationDecision.REVISE
                        or receipt.action is not LeadTaskAction.RESUME):
                    raise ValueError("discussion revision requires exact structured USER Revise")
                decision = None
            if receipt.source_task_ref is not None:
                source_scope(receipt.source_task_ref)
            if receipt.origin_message_event_id is not None:
                message = prior.get(receipt.origin_message_event_id)
                if message is None or not correlated(message, event) or (
                    type(message.payload) is not CollaborationMessagePayload
                    or message.sender.kind is not CollaborationActorKind.USER
                ):
                    raise ValueError("task control requires its exact USER message")
                expected = decision.text if decision is not None else message.payload.content
                expected_text = (decision.text if decision is not None else
                                 origin.payload.decision.revision_request if recommendation_origin else message.payload.content)
                if message.payload.content != expected_text:
                    raise ValueError("task transition must preserve exact USER intent text")
            if receipt.action is LeadTaskAction.RESUME:
                ref = receipt.source_task_ref
                if ref is None or (receipt.target_task_id, receipt.target_task_revision) != (
                    ref.task_id, ref.task_revision + 1,
                ):
                    raise ValueError("resume requires an exact fresh task successor")
                resumptions[ref.task_id] = receipt
            elif receipt.action is LeadTaskAction.NEW_TASK:
                if receipt.target_task_revision != 1 or receipt.target_task_id in tasks:
                    raise ValueError("new task requires a new identity at revision one")
            selected_captures = tuple(captures.get(identity) for identity in receipt.material_capture_event_ids)
            if any(item is None or not correlated(item, event) for item in selected_captures):
                raise ValueError("task transition requires exact co-batched material captures")
            if decision is not None and tuple(item.payload.capture.source for item in selected_captures) != decision.material_refs:
                raise ValueError("task transition omitted or reordered USER materials")
            for item in selected_captures:
                if (item.payload.capture.target_task_id, item.payload.capture.target_task_revision) != (
                    receipt.target_task_id, receipt.target_task_revision,
                ):
                    raise ValueError("task capture target is detached from planning")
            ref = receipt.source_task_ref
            if ref is not None:
                if receipt.action is LeadTaskAction.HOLD:
                    statuses[ref.task_id] = "held"
                elif receipt.action is LeadTaskAction.DISCARD:
                    statuses[ref.task_id] = "discarded"
                elif receipt.action is LeadTaskAction.NEW_TASK and statuses.get(ref.task_id) == "active":
                    statuses[ref.task_id] = "held"
            discussion = receipt if receipt.action in {
                LeadTaskAction.DISCUSS, LeadTaskAction.RESUME, LeadTaskAction.NEW_TASK,
            } else None
            transitions[event.event_id] = event
        elif type(payload) is CollaborationLeadPublicResponsePayload:
            response = payload.response
            submitted = submitted_calls.get((response.invocation_id, response.call_id))
            if submitted is None or (submitted.session_id, submitted.conversation_id,
                                     submitted.turn_id, submitted.step_id,
                                     submitted.payload.ordinal, submitted.payload.call_kind.value) != (
                event.session_id, event.conversation_id, event.turn_id, event.step_id,
                response.ordinal, response.call_kind,
            ):
                raise ValueError("public response requires exact actual submitted-call facts")
            ref = LeadMaterialRef(session_id=event.session_id, conversation_id=event.conversation_id,
                                  event_id=event.event_id, kind=LeadMaterialKind.PUBLIC_RESPONSE,
                                  material_digest=response.material_digest)
            public.append(LeadPublicResponseView(ref, response))
            choices.append(LeadMaterialChoice(ref, "Public response (" + response.acceptance.value + ")",
                                              response.response_text[:240], response.response_text,
                                              response.acceptance.value, response.call_id))
        elif type(payload) is CollaborationLeadDiscussionPayload:
            report = payload.report
            origin = transitions.get(report.origin_control_event_id)
            if origin is None or origin.session_id != event.session_id or origin.conversation_id != event.conversation_id:
                raise ValueError("discussion requires exact durable USER control")
            receipt = origin.payload.receipt
            if (receipt.action is LeadTaskAction.DISCUSS
                    and (origin.turn_id, origin.step_id) != (event.turn_id, event.step_id)):
                raise ValueError("pure discussion result must close its exact current USER control")
            if (receipt.action in {LeadTaskAction.RESUME, LeadTaskAction.NEW_TASK}
                    and receipt.planning_step_id != event.step_id):
                raise ValueError("clarification must preserve its exact planning lineage")
            if report.associated_task_ref != origin.payload.receipt.source_task_ref:
                raise ValueError("discussion changed its task association")
            calls = [item for item in prior.values() if type(item.payload) is CollaborationProviderCallPayload
                     and item.payload.state is CollaborationProviderCallState.SUBMITTED
                     and (item.turn_id, item.step_id) == (event.turn_id, event.step_id)]
            if not calls:
                raise ValueError("discussion requires actual submitted provider work")
            discussion = origin.payload.receipt
            ref = LeadMaterialRef(session_id=event.session_id, conversation_id=event.conversation_id,
                                  event_id=event.event_id, kind=LeadMaterialKind.DISCUSSION,
                                  material_digest=task_material_sha256(report.response_text))
            choices.append(LeadMaterialChoice(ref, "Discussion (provisional)", report.response_text[:240], report.response_text))
        elif type(payload) is CollaborationSpecialistReplyPayload:
            if payload.assessment is CollaborationSpecialistAssessment.DRAFTED:
                text = payload.message
                ref = LeadMaterialRef(session_id=event.session_id, conversation_id=event.conversation_id,
                                      event_id=event.event_id, kind=LeadMaterialKind.DRAFT,
                                      material_digest=task_material_sha256(text))
                choices.append(LeadMaterialChoice(ref, "Prior specialist draft (input only)", text[:240], text))
        elif (type(payload) is CollaborationExecutionResult
              and payload.overall_outcome is CollaborationExecutionOutcomeKind.SUCCEEDED
              and payload.synthesis_result_event_id is not None):
            source = prior.get(payload.synthesis_result_event_id)
            source_value = None if source is None else source.payload
            if (type(source_value) is CollaborationLeadSynthesisPayload
                    and source_value.premise_report is None and source_value.authoring_scope is not None
                    and source_value.authoring_scope.intent is AuthoringIntent.AUTHORING
                    and source_value.authoring_scope.activity in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE}
                    and (payload.proposal_id, payload.proposal_revision, payload.proposal_digest,
                         payload.execution_attempt_id)
                    == (source_value.proposal_id, source_value.proposal_revision,
                        source_value.proposal_digest, source_value.execution_attempt_id)):
                text = source_value.message
                ref = LeadMaterialRef(session_id=source.session_id, conversation_id=source.conversation_id,
                                      event_id=source.event_id, kind=LeadMaterialKind.DRAFT,
                                      material_digest=task_material_sha256(text))
                choices.append(LeadMaterialChoice(ref, "Prior integrated draft (input only)", text[:240], text))
        if type(payload) is ArtifactApplicationResultPayload and payload.outcome in {
            ArtifactApplicationOutcomeKind.APPLIED, ArtifactApplicationOutcomeKind.UNCHANGED,
        }:
            review = next((item.payload for item in prior.values()
                           if type(item.payload) is ArtifactReviewedProposal
                           and (item.payload.proposal_id, item.payload.proposal_digest) == (
                               payload.reviewed_proposal_id, payload.reviewed_proposal_digest,
                           )), None)
            source = None if review is None else next((item.payload for item in prior.values()
                       if type(item.payload) is CollaborationProposal
                       and item.payload.proposal_id == review.consultation_proposal_id), None)
            if source is not None and source.authoring_scope is not None:
                statuses[source.authoring_scope.task_id] = "completed"
        prior[event.event_id] = event
    if (discussion is not None and discussion.target_task_id is not None
            and discussion.target_task_id in tasks
            and tasks[discussion.target_task_id][1].task_revision == discussion.target_task_revision):
        discussion = None
    protected = projection.current_state in {
        CollaborationStepState.RUNNING, CollaborationStepState.OUTCOME_UNKNOWN,
        CollaborationStepState.RECOVERY_REQUIRED,
    }
    views = []
    for task_id, (event, scope) in tasks.items():
        status = statuses.get(task_id, "active")
        if protected and projection.current_proposal is not None and projection.current_proposal.authoring_scope is not None and (
            projection.current_proposal.authoring_scope.task_id == task_id
        ):
            status = "protected"
        ref = LeadTaskRef(session_id=event.session_id, conversation_id=event.conversation_id,
                          task_id=task_id, task_revision=scope.task_revision,
                          scope_event_id=event.event_id, scope_digest=_task_scope_digest(scope))
        task_steps = {item.step_id for item in events
                      if getattr(item.payload, "authoring_scope", None) is not None
                      and item.payload.authoring_scope.task_id == task_id}
        task_steps.update(item.payload.receipt.planning_step_id for item in events
                          if type(item.payload) is CollaborationLeadTaskTransitionPayload
                          and item.payload.receipt.target_task_id == task_id)
        submitted = tuple(dict.fromkeys(item.payload.call_id for item in events
                          if type(item.payload) is CollaborationProviderCallPayload
                          and item.payload.state is CollaborationProviderCallState.SUBMITTED
                          and item.payload.invocation_kind is not CollaborationInvocationKind.LEAD_DISCUSSION
                          and item.step_id in task_steps))
        user_sources = _task_user_origins(events, scope)
        goal = goals[task_id]
        pending_ids = ()
        if (projection.current_proposal is not None
                and projection.current_proposal.authoring_scope is not None
                and projection.current_proposal.authoring_scope.task_id == task_id
                and projection.current_state in {CollaborationStepState.AWAITING_CONFIRMATION,
                                                 CollaborationStepState.RECONFIRMATION_REQUIRED}
                and projection.confirmation_request_id is not None):
            pending_ids = (projection.confirmation_request_id,)
        views.append(LeadTaskView(ref, goal[:160], status, scope, goal,
                                  tuple(item.event_id for item in user_sources), pending_ids, submitted))
    discussion_steps = {(event.turn_id, event.step_id) for event in transitions.values()
                        if event.payload.receipt.action is LeadTaskAction.DISCUSS}
    protected_kinds = {
        CollaborationEventKindV2.ASSIGNMENT_DISPATCH, CollaborationEventKindV2.EXECUTION_RESULT,
        CollaborationEventKindV2.ARTIFACT_REVIEW_CANDIDATE,
        CollaborationEventKindV2.ARTIFACT_APPLICATION_REQUEST,
        CollaborationEventKindV2.ARTIFACT_APPLICATION_RESULT,
        CollaborationEventKindV2.STORY_COMMIT_REQUEST, CollaborationEventKindV2.STORY_COMMIT_RESULT,
    }
    for event in events:
        if (event.turn_id, event.step_id) not in discussion_steps:
            continue
        if event.kind in protected_kinds or type(event.payload) in {
            CollaborationProposal, CollaborationLeadJudgmentPayload,
        }:
            raise ValueError("pure discussion cannot publish execution or write authority")
        if type(event.payload) is CollaborationStepStatePayload and event.payload.state not in {
            CollaborationStepState.FAILED, CollaborationStepState.INTERRUPTED,
            CollaborationStepState.OUTCOME_UNKNOWN, CollaborationStepState.RECOVERY_REQUIRED,
        }:
            raise ValueError("discussion state may record only failure or uncertainty bookkeeping")
    uncertain = False
    for event in events:
        call = event.payload
        if (type(call) is not CollaborationProviderCallPayload
                or call.invocation_kind is not CollaborationInvocationKind.LEAD_DISCUSSION):
            continue
        following = tuple(later for later in events
                          if (later.turn_id, later.step_id) == (event.turn_id, event.step_id)
                          and later.sequence > event.sequence)
        submitted = tuple(later.payload for later in following
                          if type(later.payload) is CollaborationProviderCallPayload
                          and (later.payload.invocation_id, later.payload.call_id)
                          == (call.invocation_id, call.call_id)
                          and later.payload.state is CollaborationProviderCallState.SUBMITTED)
        if submitted:
            call = submitted[-1]
        reports = tuple(later for later in following
                        if type(later.payload) is CollaborationLeadDiscussionPayload)
        states = tuple(later.payload.state for later in following
                       if type(later.payload) is CollaborationStepStatePayload)
        failures = tuple(later.payload for later in following
                         if type(later.payload) is CollaborationLeadOutcomeFailurePayload
                         and (later.payload.invocation_id, later.payload.call_id)
                         == (call.invocation_id, call.call_id))
        known_failure = bool(failures and call.state is CollaborationProviderCallState.SUBMITTED
                             and (not states or states[-1] in {
                                 CollaborationStepState.FAILED, CollaborationStepState.INTERRUPTED,
                             }))
        if not reports and not known_failure:
            uncertain = True
            break
    return replace(projection, available_tasks=tuple(views), selectable_materials=tuple(choices),
                   public_responses=tuple(public), discussion_context=discussion,
                   next_safe_action=(CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME
                                     if uncertain else projection.next_safe_action),
                   last_sequence=events[-1].sequence if events else projection.last_sequence)
