"""Invocation-local provider accounting and correlation observations."""
from __future__ import annotations
import json
import time
from typing import AsyncContextManager, Callable, cast
from ..errors import BackendProtocolError, ProviderAuthenticationRequiredError
from ..agent_context.invocation.provider import (
    BoundContextProviderCallRequest,
    ContextProviderCallRequest,
    ContextProviderCallResult,
    ContextReferencePreludeRequest,
    ContextReferencePreludeResult,
    context_provider_failure_facts,
)
from ..conversations import (
    COLLABORATION_VISIBLE_TEXT_MAX_UTF8_BYTES,
    CollaborationAppendReceipt,
    CollaborationEventBatch,
    CollaborationEventDraft,
    CollaborationEventKind,
    CollaborationEvent as CollaborationEventV2,
    CollaborationHistoryProjection,
    CollaborationInvocationKind,
    CollaborationLeadJudgmentPayload,
    CollaborationLeadOutcomeFailureCode,
    CollaborationLeadOutcomeFailurePayload,
    CollaborationLeadPublicResponsePayload,
    CollaborationMessagePayload,
    CollaborationProviderCallKind,
    CollaborationProviderCallPayload,
    CollaborationProviderCallState,
    CollaborationStepStatePayload,
    LeadRecommendationRequestPayload,
)
from ..conversations.events import CollaborationLeadGroupTracePayload
from ..agent_context.manifest import ContextContinuationIdentity, ContextScope
from ..agent_context.contracts.events import ContextExecutionEvent, ContextNativeDisposition
from ..document_plan import DocumentNodeBinding
from ..agents import LEAD_ORCHESTRATOR
from ..agent_context.group_observation import LeadGroupObservation, LeadGroupObservationKind, LeadGroupTrace
from ..lead_task_transitions import LeadPublicResponse, LeadPublicResponseAcceptance, task_material_sha256
from ..lead_premise_decisions import PremiseFreshWorkAdmission
from ..agent_context.accounting import ProviderCallAccounting, ProviderCallKind, ProviderCallState
from ..provider_execution_workspace import ProviderExecutionWorkspace
from ..structured_output_validation import bounded_chunks, diagnose_json_text
from . import collaboration_event_journal as event_journal
from ..provider_response_mapping import (
    recover_lead_public_response,
    recover_lead_public_response_kind,
)
from dataclasses import replace


class LeadCallBudgetExceeded(BackendProtocolError):
    """Reject a proposal whose minimum calls exceed its lineage budget."""

class AccountingContextProvider:
    """Persist content-free call lifecycle facts around one backend context seam."""

    def __init__(self,
        delegate: object,
        *,
        session_id: str,
        conversation_id: str,
        turn_id: str,
        step_id: str,
        invocation_id: str,
        role: str,
        invocation_kind: CollaborationInvocationKind,
        admission: PremiseFreshWorkAdmission | None=None,
        effective_language: str | None=None,
        task_revisions: tuple[tuple[str,
                int
            ],
            ...
        ]=(),
        document_node: DocumentNodeBinding | None=None,
        document_goal=None,
        document_budget_task_id=None,
        read_events: Callable[[str,
                str
            ],
            tuple[CollaborationEventV2,
                ...
            ]
        ],
        read_context: Callable[[str,
                str
            ],
            CollaborationHistoryProjection
        ],
        append_batch: Callable[[CollaborationEventBatch],
            CollaborationAppendReceipt
        ]
    ) -> None:
        self.effective_language = effective_language
        self.task_revisions = task_revisions
        self._read_events = read_events
        self._read_context = read_context
        self._append_batch = append_batch
        self._delegate = delegate
        self._session_id = session_id
        self._conversation_id = conversation_id
        self._turn_id = turn_id
        self._step_id = step_id
        self._invocation_id = invocation_id
        self._role = role
        self._invocation_kind = invocation_kind
        self._admission = admission
        if admission is not None and (admission.provider_invocation_id != invocation_id or admission.provider_step_id != step_id or role != LEAD_ORCHESTRATOR):
            raise BackendProtocolError("provider wrapper does not match its fresh-work admission")
        self._ordinals: dict[str, int] = {}
        self._submitted_call_ids: set[str] = set()
        self._validation_details: dict[str, tuple[str, ...]] = {}
        self._validation_messages_published = False
        self.provider_id = cast(str, getattr(delegate, "provider_id"))
        self.definition_fingerprint = cast(
            str,
            getattr(delegate, "definition_fingerprint"),
        )
        self.reference_operation_id = cast(
            str | None,
            getattr(delegate, "reference_operation_id"),
        )
        self._public_response_contract_id: str | None = None
        self._public_observations: dict[str, tuple[str, int, CollaborationProviderCallKind, str | None]] = {}
        self._public_accepted_call_id: str | None = None
        self._diagnostic_finalizers: dict[str, object] = {}
        self._public_responses_published = False
        self._group_observations: list[LeadGroupObservation] = []
        self._group_started_ns: int | None = None
        self._task_front_budget: int | None = None
        self._document_node = document_node
        self._document_goal = document_goal
        self._document_budget_task_id = document_budget_task_id


    def renew_delegate(self, delegate: object) -> None:
        """Rebind one fresh compatible seam without resetting call accounting."""

        provider_id = getattr(delegate, "provider_id", None)
        definition_fingerprint = getattr(delegate, "definition_fingerprint", None)
        reference_operation_id = getattr(delegate, "reference_operation_id", None)
        if (
            delegate is self._delegate
            or type(provider_id) is not str
            or provider_id != self.provider_id
            or type(definition_fingerprint) is not str
            or definition_fingerprint != self.definition_fingerprint
            or (
                reference_operation_id is not None
                and type(reference_operation_id) is not str
            )
            or reference_operation_id != self.reference_operation_id
        ):
            raise BackendProtocolError(
                "corrective context provider does not match the original binding"
            )
        self._delegate = delegate

    def invocation(
        self,
        scope: ContextScope,
        policy_fingerprint: str,
        *,
        continuation_identity: ContextContinuationIdentity | None = None,
        execution_workspace: ProviderExecutionWorkspace | None = None,
    ) -> AsyncContextManager[None]:
        if continuation_identity is not None:
            return getattr(self._delegate, "invocation")(
                scope, policy_fingerprint,
                continuation_identity=continuation_identity,
                execution_workspace=execution_workspace,
            )
        return getattr(self._delegate, "invocation")(
            scope,
            policy_fingerprint,
            execution_workspace=execution_workspace,
        )

    def inspect_context_thread(
        self,
        scope: ContextScope,
        policy_fingerprint: str,
        *,
        execution_workspace: ProviderExecutionWorkspace | None = None,
    ) -> str | None:
        return getattr(self._delegate, "inspect_context_thread")(
            scope,
            policy_fingerprint,
            execution_workspace=execution_workspace,
        )

    def require_current_execution_workspace(
        self,
        scope: ContextScope,
        policy_fingerprint: str,
    ) -> None:
        callback = getattr(
            self._delegate,
            "require_current_execution_workspace",
            None,
        )
        if callback is not None:
            callback(scope, policy_fingerprint)

    async def prepare_references(
        self,
        request: ContextReferencePreludeRequest,
    ) -> ContextReferencePreludeResult:
        """Load authorized references locally without provider-call accounting."""

        return await getattr(self._delegate, "prepare_references")(request)

    async def invoke_context(
        self,
        request: ContextProviderCallRequest | BoundContextProviderCallRequest,
    ) -> ContextProviderCallResult:
        kind = (
            CollaborationProviderCallKind.INITIAL
            if request.call_kind
            in {
                ProviderCallKind.CONVERSATION,
                ProviderCallKind.CONVERSATION_RETRIEVAL_INITIAL,
                ProviderCallKind.COLLABORATION_INITIAL,
                ProviderCallKind.DEBATE,
            }
            else CollaborationProviderCallKind.CONTINUATION
        )
        ordinal = self._prepare(request.call_id, kind)
        try:
            result = await getattr(self._delegate, "invoke_context")(request)
        except BaseException as failure:
            if (
                isinstance(failure, ProviderAuthenticationRequiredError)
                and failure.replay_aborted
            ):
                raise
            self._record_failure_submission(
                failure,
                request.call_id,
                ordinal,
                kind,
            )
            raise
        details = (
            bounded_chunks(
                request.contract.structural_findings(result.response_text)
                if type(request) is BoundContextProviderCallRequest
                else diagnose_json_text(result.response_text, request.schema)
            )
            if type(result) is ContextProviderCallResult else ()
        )
        self._record_result_submission(result, request.call_id, ordinal, kind, details)
        try:
            if type(result) is ContextProviderCallResult:
                self._diagnostic_finalizers[request.call_id] = result.finalizer
        except BaseException:
            pass
        if type(result) is ContextProviderCallResult and self._public_response_contract_id is not None:
            public_text = recover_lead_public_response(
                result.response_text, self._public_response_contract_id,
                maximum_utf8_bytes=COLLABORATION_VISIBLE_TEXT_MAX_UTF8_BYTES,
            )
            if public_text is not None and request.call_id in self._submitted_call_ids:
                self._public_observations[request.call_id] = (public_text, ordinal, kind,
                    recover_lead_public_response_kind(result.response_text,
                        self._public_response_contract_id,
                        maximum_utf8_bytes=COLLABORATION_VISIBLE_TEXT_MAX_UTF8_BYTES))
        return result

    def record_validated_task(self, call_id: str, task_id: str, task_revision: int) -> None:
        """Attach only a validated host task to its exact physical response."""
        try:
            finalizer = self._diagnostic_finalizers.get(call_id)
            callback = getattr(finalizer, "set_host_task_diagnostic", None)
            if callable(callback):
                callback(task_id, task_revision)
        except BaseException:
            pass

    def record_validation_details(
        self, call_id: str, details: tuple[str, ...],
    ) -> None:
        """Retain safe rejection details until the invocation closes."""

        if call_id in self._submitted_call_ids and details:
            labelled = self._label_validation_details(call_id, details)
            self._validation_details[call_id] = labelled

    def record_group_observation(self, observation: LeadGroupObservation) -> None:
        """Keep observations invocation-local until existing terminal publication."""
        if type(observation) is not LeadGroupObservation:
            raise TypeError("group observer requires a typed observation")
        if self._group_started_ns is None and observation.kind is LeadGroupObservationKind.BOUND:
            self._group_started_ns = time.monotonic_ns()
        self._group_observations.append(observation)

    def group_trace_drafts(self, terminal_code: str, terminal_drafts=()) -> tuple[CollaborationEventDraft, ...]:
        if not self._group_observations:
            return ()
        events = self._read_events(self._session_id, self._conversation_id)
        if any(type(event.payload) is CollaborationLeadGroupTracePayload
               and event.payload.trace.invocation_id == self._invocation_id for event in events):
            return ()
        terminal = next((item for item in terminal_drafts
            if type(item.payload) in {CollaborationLeadJudgmentPayload, CollaborationLeadOutcomeFailurePayload}), None)
        if (terminal is not None and type(terminal.payload) is CollaborationLeadOutcomeFailurePayload
                and terminal.payload.code is CollaborationLeadOutcomeFailureCode.REQUEST_NOT_SUBMITTED
                and all(item.kind is LeadGroupObservationKind.TERMINAL for item in self._group_observations)):
            return ()
        terminal_snapshot = None
        if terminal is not None:
            payload = terminal.payload
            scope = getattr(payload, "authoring_scope", None)
            terminal_snapshot = json.dumps({
                "source_event_id": terminal.event_id, "source_kind": terminal.kind.value,
                "outcome_kind": payload.kind.value if type(payload) is CollaborationLeadJudgmentPayload else None,
                "failure_code": payload.code.value if type(payload) is CollaborationLeadOutcomeFailurePayload else None,
                "task_ref": None if scope is None else {"task_id": scope.task_id, "revision": scope.task_revision},
                "task_completion": None, "objective_completion": None,
            }, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        return (event_journal.draft(
            session_id=self._session_id, conversation_id=self._conversation_id,
            turn_id=self._turn_id, step_id=self._step_id,
            kind=CollaborationEventKind.LEAD_GROUP_TRACE,
            sender=event_journal.RUNTIME_ACTOR, recipient=event_journal.LEAD_ACTOR,
            payload=CollaborationLeadGroupTracePayload(LeadGroupTrace(
                self._invocation_id, tuple(self._group_observations), terminal_code,
                elapsed_ns=None if self._group_started_ns is None else time.monotonic_ns() - self._group_started_ns,
                terminal_snapshot=terminal_snapshot)),
        ),)

    def with_group_trace(self, drafts, terminal_code: str):
        traces = self.group_trace_drafts(terminal_code, terminal_drafts=drafts)
        if not traces:
            return tuple(drafts)
        source_ids = {draft.payload.source_recommendation_event_id for draft in drafts
                      if type(draft.payload) is LeadRecommendationRequestPayload}
        tail_index = next((index for index, draft in enumerate(drafts)
            if type(draft.payload) in {CollaborationStepStatePayload, LeadRecommendationRequestPayload}
            or (type(draft.payload) is CollaborationLeadJudgmentPayload and draft.event_id in source_ids)), len(drafts))
        drafts = tuple(replace(draft, payload=replace(
            draft.payload, tail_sequence=draft.payload.tail_sequence + len(traces),
        )) if type(draft.payload) is LeadRecommendationRequestPayload else draft for draft in drafts)
        return (*drafts[:tail_index], *traces, *drafts[tail_index:])

    def publish_public_responses(self) -> None:
        if self._public_responses_published or self._public_response_contract_id is None:
            return
        if not self._validation_messages_published:
            self.publish_validation_messages()
        events = self._read_events(
            self._session_id, self._conversation_id,
        )
        submitted = {
            event.payload.call_id: event.payload for event in events
            if type(event.payload) is CollaborationProviderCallPayload
            and event.payload.state is CollaborationProviderCallState.SUBMITTED
            and event.payload.invocation_id == self._invocation_id
            and event.payload.invocation_kind is self._invocation_kind
            and (event.session_id, event.conversation_id, event.turn_id, event.step_id) == (
                self._session_id, self._conversation_id, self._turn_id, self._step_id,
            )
        }
        drafts = []
        previous_rejected = None
        for call_id in self._ordinals:
            if call_id not in self._submitted_call_ids:
                continue
            call = submitted.get(call_id)
            if call is None or call.ordinal != self._ordinals[call_id]:
                raise BackendProtocolError("public response lacks exact submitted-call facts")
            details = self._validation_details.get(call_id, ())
            accepted = call_id == self._public_accepted_call_id
            observation = self._public_observations.get(call_id)
            if observation is not None:
                text, ordinal, kind, response_kind = observation
                if ordinal != call.ordinal or kind is not call.call_kind:
                    raise BackendProtocolError("public response changed its actual call identity")
                acceptance = (
                    LeadPublicResponseAcceptance.ACCEPTED if accepted
                    else LeadPublicResponseAcceptance.REJECTED if details
                    else LeadPublicResponseAcceptance.UNESTABLISHED
                )
                response = LeadPublicResponse(
                    invocation_id=self._invocation_id, call_id=call_id, ordinal=ordinal,
                    call_kind=kind.value, acceptance=acceptance, response_text=text,
                    material_digest=task_material_sha256(text),
                    repair_of_call_id=previous_rejected, validation_details=details,
                    response_kind=response_kind,
                )
                drafts.append(event_journal.draft(
                    session_id=self._session_id, conversation_id=self._conversation_id,
                    turn_id=self._turn_id, step_id=self._step_id,
                    kind=CollaborationEventKind.LEAD_PUBLIC_RESPONSE,
                    sender=event_journal.RUNTIME_ACTOR, recipient=event_journal.USER_ACTOR,
                    payload=CollaborationLeadPublicResponsePayload(response),
                ))
            elif details or accepted:
                disposition = "accepted" if accepted else "rejected"
                message = (
                    f"Public response unavailable for {disposition} call {call_id}: "
                    "no bounded literal final response_text was recoverable."
                )
                drafts.append(event_journal.draft(
                    session_id=self._session_id, conversation_id=self._conversation_id,
                    turn_id=self._turn_id, step_id=self._step_id,
                    kind=CollaborationEventKind.MESSAGE,
                    sender=event_journal.RUNTIME_ACTOR, recipient=event_journal.USER_ACTOR,
                    payload=CollaborationMessagePayload(message),
                ))
            if details and not accepted:
                previous_rejected = call_id
        if not drafts:
            return
        event_journal.append_drafts(
            events[-1].sequence if events else 0, tuple(drafts),
            append_batch=self._append_batch,
        )
        self._public_responses_published = True

    def publish_validation_messages(self) -> None:
        """Publish diagnostics only after contextual provider work has closed."""

        if self._validation_messages_published:
            return
        details_by_call = self.validation_details_by_call
        if details_by_call:
            event_journal.append_validation_messages(
                session_id=self._session_id,
                conversation_id=self._conversation_id,
                turn_id=self._turn_id,
                step_id=self._step_id,
                invocation_id=self._invocation_id,
                details_by_call=details_by_call,
                read_events=self._read_events,
                append_batch=self._append_batch,
            )
        self._validation_messages_published = True

    def publish_or_defer_validation_messages(
        self,
    ) -> tuple[str, tuple[tuple[str, tuple[str, ...]], ...]] | None:
        """Retain an exact retry batch when diagnostic persistence fails."""

        try:
            self.publish_validation_messages()
        except Exception:
            details_by_call = self.validation_details_by_call
            if details_by_call:
                return self._invocation_id, details_by_call
        return None

    @property
    def validation_details_by_call(self) -> tuple[tuple[str, tuple[str, ...]], ...]:
        """Return ordered safe chunks with their submitted call identities."""

        return tuple(
            (call_id, self._validation_details[call_id])
            for call_id in self._ordinals
            if call_id in self._validation_details
        )

    def _label_validation_details(
        self, call_id: str, details: tuple[str, ...],
    ) -> tuple[str, ...]:
        first_diagnostic_call_id = next(iter(self._validation_details), call_id)
        label = (
            "Original response"
            if call_id == first_diagnostic_call_id else "Repair response"
        )
        chunks = (
            details
            if all(item.startswith("Structured reply validation") for item in details)
            else bounded_chunks(details)
        )
        return tuple(
            item.replace("Structured reply validation", label, 1)
            if item.startswith("Structured reply validation")
            else f"{label}: {item}"
            for item in chunks
        )

    @property
    def validation_details(self) -> tuple[str, ...]:
        """Return ordered provider diagnostics without display metadata."""
        chunks: list[str] = []
        for call_id in self._ordinals:
            chunks.extend(self._validation_details.get(call_id, ()))
        return tuple(chunks)

    def complete_references(
        self,
        scope: ContextScope,
        policy_fingerprint: str,
    ) -> None:
        getattr(self._delegate, "complete_references")(scope, policy_fingerprint)

    def abort_references(
        self,
        scope: ContextScope,
        policy_fingerprint: str,
        *,
        cancelled: bool,
        provider_failure: bool,
    ) -> None:
        getattr(self._delegate, "abort_references")(
            scope,
            policy_fingerprint,
            cancelled=cancelled,
            provider_failure=provider_failure,
        )

    def taint_context_thread(
        self,
        scope: ContextScope,
        policy_fingerprint: str,
        *,
        execution_workspace: ProviderExecutionWorkspace | None = None,
    ) -> None:
        getattr(self._delegate, "taint_context_thread")(
            scope,
            policy_fingerprint,
            execution_workspace=execution_workspace,
        )

    def _prepare(self, call_id: str, kind: CollaborationProviderCallKind) -> int:
        if self._task_front_budget is not None:
            history = self._read_events(self._session_id, self._conversation_id)
            if self._invocation_kind is CollaborationInvocationKind.LEAD_DISCUSSION:
                spent = sum(type(event.payload) is CollaborationProviderCallPayload
                            and event.payload.invocation_id == self._invocation_id
                            and event.payload.state is CollaborationProviderCallState.SUBMITTED
                            for event in history)
            else:
                spent = sum(type(event.payload) is CollaborationProviderCallPayload
                            and event.step_id == self._step_id
                            and event.payload.invocation_kind is not CollaborationInvocationKind.LEAD_DISCUSSION
                            and event.payload.state is CollaborationProviderCallState.SUBMITTED
                            for event in history)
            if spent >= self._task_front_budget:
                raise LeadCallBudgetExceeded("the exact USER task/discussion lineage has no physical call remaining")
        if self._admission is not None:
            history = self._read_events(self._session_id, self._conversation_id)
            spent = sum(type(event.payload) is CollaborationProviderCallPayload
                and event.payload.invocation_id == self._invocation_id
                and event.payload.state is CollaborationProviderCallState.SUBMITTED for event in history)
            if max(spent, self.submitted_physical_calls) >= self._admission.front_physical_call_limit:
                raise LeadCallBudgetExceeded("fresh-work front admission cannot fund another physical call")
        if call_id in self._ordinals:
            raise BackendProtocolError("provider call ID was prepared more than once")
        ordinal = len(self._ordinals) + 1
        self._ordinals[call_id] = ordinal
        event_journal.append_provider_call_fact(
            document_node=self._document_node, document_goal=self._document_goal, document_budget_task_id=self._document_budget_task_id,
            session_id=self._session_id,
            conversation_id=self._conversation_id,
            turn_id=self._turn_id,
            step_id=self._step_id,
            invocation_id=self._invocation_id,
            call_id=call_id,
            ordinal=ordinal,
            call_kind=kind,
            state=CollaborationProviderCallState.PREPARED,
            role=self._role,
            invocation_kind=self._invocation_kind,
            read_context=self._read_context,
            append_batch=self._append_batch,
        )
        return ordinal

    def _record_failure_submission(
        self,
        failure: BaseException,
        call_id: str,
        ordinal: int,
        kind: CollaborationProviderCallKind,
    ) -> None:
        facts = context_provider_failure_facts(failure)
        if (
            facts.accounting is not None
            and facts.accounting.state is ProviderCallState.SUBMITTED
        ):
            self._submitted(call_id, ordinal, kind, facts.accounting)

    def _record_result_submission(
        self,
        result: object,
        call_id: str,
        ordinal: int,
        kind: CollaborationProviderCallKind,
        details: tuple[str, ...] = (),
    ) -> None:
        accounting = getattr(result, "accounting", None)
        if (
            type(accounting) is not ProviderCallAccounting
            or accounting.state is not ProviderCallState.SUBMITTED
        ):
            return
        self._submitted(call_id, ordinal, kind, accounting, details)

    def _submitted(
        self,
        call_id: str,
        ordinal: int,
        kind: CollaborationProviderCallKind,
        accounting: ProviderCallAccounting,
        details: tuple[str, ...] = (),
    ) -> None:
        if call_id in self._submitted_call_ids:
            raise BackendProtocolError("provider call was submitted more than once")
        if accounting.payload.call_kind is ProviderCallKind.UNCLASSIFIED:
            raise BackendProtocolError("submitted provider call is unclassified")
        self._submitted_call_ids.add(call_id)
        if details:
            details = self._label_validation_details(call_id, details)
            self._validation_details[call_id] = details
        event_journal.append_provider_call_fact(
            document_node=self._document_node, document_goal=self._document_goal, document_budget_task_id=self._document_budget_task_id,
            session_id=self._session_id,
            conversation_id=self._conversation_id,
            turn_id=self._turn_id,
            step_id=self._step_id,
            invocation_id=self._invocation_id,
            call_id=call_id,
            ordinal=ordinal,
            call_kind=kind,
            state=CollaborationProviderCallState.SUBMITTED,
            role=self._role,
            invocation_kind=self._invocation_kind,
            validation_details=details,
            usage=accounting.usage,
            accounting=accounting,
            read_context=self._read_context,
            append_batch=self._append_batch,
        )

    @property
    def submitted_physical_calls(self) -> int:
        """Return exact submitted calls observed by this no-await wrapper."""

        return len(self._submitted_call_ids)

    @property
    def invocation_id(self) -> str:
        """Return the durable invocation identity used by call facts."""

        return self._invocation_id

    @property
    def last_submitted_call_id(self) -> str | None:
        """Return the exact most recently prepared call that was submitted."""

        return next(
            (
                call_id
                for call_id in reversed(tuple(self._ordinals))
                if call_id in self._submitted_call_ids
            ),
            None,
        )


    def limit_task_front_calls(self, limit: int | None) -> None:
        self._task_front_budget = limit

    def configure_public_response_contract(self, contract_id: str | None) -> None:
        self._public_response_contract_id = contract_id

    def accept_last_public_response(self) -> None:
        self._public_accepted_call_id = self.last_submitted_call_id

    def correlate_native_context_event(self, event: ContextExecutionEvent) -> ContextExecutionEvent:
        return replace(event, invocation_id=self._invocation_id)

    def record_native_context_recovery(self,
        event: ContextExecutionEvent,
        disposition: ContextNativeDisposition
    ) -> None:
        if self._group_observations:
            prior = self._group_observations[-1]
            snapshot = json.dumps({"check": event.native_context_check.as_object(),
                "disposition": disposition.value, "attempt_id": event.attempt_id,
                "call_phase": None if event.group_phase is None else event.group_phase.value,
                "native_process_completed": None if event.native_context_check.normal_exit_code is None else event.native_context_check.normal_exit_code == 0},
                ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
            self.record_group_observation(LeadGroupObservation(
                LeadGroupObservationKind.NATIVE_CONTEXT, prior.request_basis, prior.phase,
                event.native_context_check.call_id, native_context_snapshot=snapshot))
