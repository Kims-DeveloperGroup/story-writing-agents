"""End-to-end acceptance tests for the lead-front-channel runtime."""

from __future__ import annotations

import asyncio
import inspect
import json
import pickle
import tempfile
import unittest
from collections.abc import Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from typing import Awaitable, Callable

from story_writing_agents import RuntimeSession, StoryRuntime, Workspace
from story_writing_agents import orchestrator as orchestrator_module
from story_writing_agents.agent_context.accounting import (
    ProviderCallAccounting,
    ProviderCallKind,
    ProviderCallState,
    account_provider_payload,
)
from story_writing_agents.agent_context.service import (
    ContextProviderCallRequest,
    ContextProviderCallResult,
    ContextProviderFailureFacts,
    ContextProviderTerminalCode,
    ContextReferencePreludeRequest,
    ContextReferencePreludeResult,
    record_context_provider_failure,
)
from story_writing_agents.agents import (
    CHARACTER_CUSTODIAN,
    EXECUTION_SPECIALIST,
    LEAD_ORCHESTRATOR,
    PLOT_ARCHITECT,
)
from story_writing_agents.conversations import (
    CollaborationConfirmationDecision,
    CollaborationEventKind,
    CollaborationInputKind,
    CollaborationInvocationKind,
    CollaborationLeadOutcomeFailureCode,
    CollaborationLeadOutcomeFailurePayload,
    CollaborationMessagePayload,
    CollaborationNextAction,
    CollaborationProposal,
    CollaborationProviderCallPayload,
    CollaborationProviderCallState,
    CollaborationSideEffect,
    CollaborationStepState,
    LeadOutcomeKind,
    LeadStepDecision,
    project_collaboration_history,
)
from story_writing_agents.errors import BackendProtocolError
from story_writing_agents.writing_artifacts.authorization import (
    ReferenceAuthorizationErrorCode,
)
from story_writing_agents.writing_artifacts.read_actions import (
    ReferenceReadAction,
    ReferenceReadActionKind,
)

from tests.collaboration_contract_fixture import contract_batches


_Response = (
    str
    | BaseException
    | Callable[
        [ContextProviderCallRequest, "_LeadContextProvider"],
        str | BaseException | Awaitable[str | BaseException],
    ]
)


class _Finalizer:
    """Minimal synchronous provider terminal seam."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, object | None]] = []

    def accepted(self) -> None:
        self.calls.append(("accepted", None))

    def rejected(self, code: ContextProviderTerminalCode) -> None:
        self.calls.append(("rejected", code))

    def cancelled(self) -> None:
        self.calls.append(("cancelled", None))

    def post_response_runtime_failure(
        self,
        code: ContextProviderTerminalCode,
    ) -> None:
        self.calls.append(("post_response_runtime_failure", code))


@dataclass(frozen=True, slots=True)
class _SubmittedFailure:
    failure: BaseException
    timed_out: bool = False
    submission_uncertain: bool = False


class _LeadContextProvider:
    """Faithful public context-provider fake with real reference authorization."""

    provider_id = "codex"

    def __init__(
        self,
        backend: "_LeadBackend",
        agent_id: str,
        definition_fingerprint: str,
        responses: tuple[_Response | _SubmittedFailure, ...],
        reference_read: object | None,
    ) -> None:
        self.backend = backend
        self.agent_id = agent_id
        self.definition_fingerprint = definition_fingerprint
        self.responses = list(responses)
        self.reference_read = reference_read
        request = getattr(reference_read, "request", None)
        self.reference_operation_id = (
            None if request is None else request.correlation.operation_id
        )
        self._threads: dict[tuple[object, str], str] = {}
        self._active: tuple[object, str] | None = None
        self._reference_prepared = False
        self._reference_terminal = False
        self.used_handles: tuple[str, ...] = ()

    @asynccontextmanager
    async def invocation(self, scope: object, policy_fingerprint: str):
        self._active = (scope, policy_fingerprint)
        try:
            yield
        finally:
            self._active = None

    def _require_active(self, scope: object, policy_fingerprint: str) -> None:
        if self._active != (scope, policy_fingerprint):
            raise AssertionError("context provider used outside its invocation")

    def inspect_context_thread(
        self,
        scope: object,
        policy_fingerprint: str,
    ) -> str | None:
        self._require_active(scope, policy_fingerprint)
        return self._threads.get((scope, policy_fingerprint))

    @staticmethod
    def _account(
        schema: object,
        payload: object,
        *,
        call_kind: ProviderCallKind,
        component_paths: object = None,
        mandatory_reference_paths: object = (),
    ) -> ProviderCallAccounting:
        encoded, accounting = account_provider_payload(
            schema,  # type: ignore[arg-type]
            payload,  # type: ignore[arg-type]
            call_kind=call_kind,
            component_paths=({} if component_paths is None else component_paths),  # type: ignore[arg-type]
            mandatory_reference_paths=mandatory_reference_paths,  # type: ignore[arg-type]
        )
        return ProviderCallAccounting(
            accounting,
            state=ProviderCallState.SUBMITTED,
            actual_transport_utf8_bytes=len(encoded),
        )

    async def prepare_references(
        self,
        request: ContextReferencePreludeRequest,
    ) -> ContextReferencePreludeResult:
        self._require_active(request.scope, request.policy_fingerprint)
        if self.reference_read is None or self._reference_prepared:
            raise AssertionError("reference prelude did not receive exact authority")
        reference_request = self.reference_read.request
        action = ReferenceReadAction(
            ReferenceReadActionKind.READ_WRITING_ARTIFACTS,
            reference_request.correlation,
            reference_request.references,
        )
        result = self.reference_read.handle_reference_read_action(action)
        with result:
            context = tuple(
                {
                    "ordinal": ordinal,
                    "content": content.decode("utf-8", errors="strict"),
                }
                for ordinal, content in enumerate(result.contents, start=1)
            )
        accounting = self._account(
            {"type": "object"},
            {"reference_count": len(context)},
            call_kind=ProviderCallKind.WRITING_ARTIFACT_REFERENCE_READ,
        )
        thread_id = request.expected_thread_id or "thread-reference"
        self._threads[(request.scope, request.policy_fingerprint)] = thread_id
        self._reference_prepared = True
        self.backend.reference_contexts.append(context)
        self.backend.reference_records.append((self.agent_id, context))
        return ContextReferencePreludeResult(context, thread_id, accounting)

    async def invoke_context(
        self,
        request: ContextProviderCallRequest,
    ) -> ContextProviderCallResult:
        self._require_active(request.scope, request.policy_fingerprint)
        self.backend.calls.append(request)
        self.backend.call_records.append((self.agent_id, request))
        if not self.responses:
            raise AssertionError("unexpected lead provider call")
        scripted = self.responses.pop(0)
        accounting = self._account(
            request.schema,
            request.payload,
            call_kind=request.call_kind,
            component_paths=request.component_paths,
            mandatory_reference_paths=request.mandatory_reference_paths,
        )
        if type(scripted) is _SubmittedFailure:
            record_context_provider_failure(
                scripted.failure,
                ContextProviderFailureFacts(
                    accounting,
                    timed_out=scripted.timed_out,
                    taint_existing_thread=True,
                    submission_uncertain=scripted.submission_uncertain,
                ),
            )
            raise scripted.failure
        response: str | BaseException = scripted
        if callable(response):
            response = response(request, self)
            if inspect.isawaitable(response):
                try:
                    response = await response
                except asyncio.CancelledError as failure:
                    record_context_provider_failure(
                        failure,
                        ContextProviderFailureFacts(
                            accounting,
                            taint_existing_thread=True,
                        ),
                    )
                    raise
        if isinstance(response, BaseException):
            raise response
        thread_id = request.expected_thread_id or "thread-lead"
        self._threads[(request.scope, request.policy_fingerprint)] = thread_id
        finalizer = _Finalizer()
        self.backend.finalizers.append(finalizer)
        return ContextProviderCallResult(
            response,
            thread_id,
            accounting,
            finalizer,
        )

    def complete_references(self, scope: object, policy_fingerprint: str) -> None:
        self._require_active(scope, policy_fingerprint)
        if self.reference_read is None:
            return
        self.reference_read.record_result_received()
        self.reference_read.accept()
        self._reference_terminal = True

    def abort_references(
        self,
        scope: object,
        policy_fingerprint: str,
        *,
        cancelled: bool,
        provider_failure: bool,
    ) -> None:
        self._require_active(scope, policy_fingerprint)
        if self.reference_read is None or self._reference_terminal:
            return
        code = (
            ReferenceAuthorizationErrorCode.CANCELLED
            if cancelled
            else ReferenceAuthorizationErrorCode.PROVIDER_FAILURE
            if provider_failure
            else ReferenceAuthorizationErrorCode.WORKFLOW_FAILURE
        )
        self.reference_read.terminate(code)
        self._reference_terminal = True

    def taint_context_thread(self, scope: object, policy_fingerprint: str) -> None:
        self._threads.pop((scope, policy_fingerprint), None)


class _LeadBoundAgent:
    def __init__(self, backend: "_LeadBackend", definition: object) -> None:
        self.backend = backend
        self.definition = definition

    def context_provider(self, reference_read: object | None) -> _LeadContextProvider:
        agent_id = self.definition.agent_id
        queue = self.backend.scripts_by_agent.get(agent_id)
        if queue:
            script = queue.pop(0)
        elif self.backend.scripts:
            script = self.backend.scripts.pop(0)
        else:
            raise AssertionError(f"no scripted invocation for {agent_id}")
        provider = _LeadContextProvider(
            self.backend,
            agent_id,
            f"sha256:{self.definition.fingerprint}",
            script,
            reference_read,
        )
        self.backend.providers.append(provider)
        return provider


class _LeadBackend:
    """Backend duck type limited to the supported binding/context seams."""

    backend_overrides: dict[str, str] = {}

    def __init__(self, *, bind_failure: BaseException | None = None) -> None:
        self.bind_failure = bind_failure
        self.scripts: list[tuple[_Response | _SubmittedFailure, ...]] = []
        self.scripts_by_agent: dict[
            str, list[tuple[_Response | _SubmittedFailure, ...]]
        ] = {}
        self.calls: list[ContextProviderCallRequest] = []
        self.call_records: list[tuple[str, ContextProviderCallRequest]] = []
        self.reference_contexts: list[tuple[dict[str, object], ...]] = []
        self.reference_records: list[tuple[str, tuple[dict[str, object], ...]]] = []
        self.finalizers: list[_Finalizer] = []
        self.providers: list[_LeadContextProvider] = []
        self.definitions: list[object] = []

    def enqueue(self, *responses: _Response | _SubmittedFailure) -> None:
        self.scripts.append(tuple(responses))

    def enqueue_for(
        self,
        agent_id: str,
        *responses: _Response | _SubmittedFailure,
    ) -> None:
        self.scripts_by_agent.setdefault(agent_id, []).append(tuple(responses))

    def execution_profile_fingerprint(self, agent_id: str) -> str:
        return (
            "sha256:"
            + {
                LEAD_ORCHESTRATOR: "1",
                PLOT_ARCHITECT: "2",
                CHARACTER_CUSTODIAN: "3",
                EXECUTION_SPECIALIST: "4",
            }.get(agent_id, "5")
            * 64
        )

    async def bind(self, definition: object) -> _LeadBoundAgent:
        if self.bind_failure is not None:
            raise self.bind_failure
        self.definitions.append(definition)
        return _LeadBoundAgent(self, definition)


def _provider_envelope(
    call: ContextProviderCallRequest,
    value: object,
    *,
    used_handles: tuple[str, ...] = (),
) -> str:
    return json.dumps(
        {
            "a": "a",
            "c": call.payload["c"],
            "d": [value],
            "u": list(used_handles),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _visible_outcome(kind: str, message: str = "Visible lead response."):
    def respond(
        call: ContextProviderCallRequest,
        _provider: _LeadContextProvider,
    ) -> str:
        return _provider_envelope(
            call,
            {
                "kind": kind,
                "message": message,
                "scope": "workflow_meta",
                "evidence_status": "not_applicable",
                "evidence_ids": [],
                "artifact_intent": None,
                "proposal": None,
            },
        )

    return respond


def _proposal_outcome(
    assignments: list[dict[str, object]],
    *,
    relation: str = "new_step",
    purpose: str = "Check the story evidence before recommending a change.",
):
    def respond(
        call: ContextProviderCallRequest,
        _provider: _LeadContextProvider,
    ) -> str:
        return _provider_envelope(
            call,
            {
                "kind": "proposal",
                "message": "Specialist review would resolve the open question.",
                "scope": "story_specific",
                "evidence_status": "incomplete",
                "evidence_ids": [call.payload["core_statuses"][0]["evidence_id"]],
                "artifact_intent": None,
                "proposal": {
                    "relation": relation,
                    "purpose": purpose,
                    "assignments": assignments,
                },
            },
        )

    return respond


def _workflow_material(value: object) -> dict[str, object]:
    if isinstance(value, Mapping):
        if "answer_contract" in value and "workflow" in value:
            return value
        for item in value.values():
            try:
                return _workflow_material(item)
            except LookupError:
                pass
    elif type(value) is list:
        for item in value:
            try:
                return _workflow_material(item)
            except LookupError:
                pass
    raise LookupError("workflow material is absent")


def _specialist_outcome(
    assessment: str = "supports",
    message: str = "The assigned evidence supports the proposed direction.",
):
    def respond(
        call: ContextProviderCallRequest,
        _provider: _LeadContextProvider,
    ) -> str:
        material = _workflow_material(call.payload)
        contract = material["answer_contract"]
        assert type(contract) is dict
        return _provider_envelope(
            call,
            {
                "assessment": assessment,
                "assignment_id": contract["assignment_id"],
                "input_ids": contract["input_ids"],
                "message": message,
            },
        )

    return respond


def _synthesis_outcome(
    *,
    message: str = "The specialist evidence supports proceeding.",
    unresolved_input_ids: list[str] | None = None,
    next_intent: object = None,
):
    def respond(
        call: ContextProviderCallRequest,
        _provider: _LeadContextProvider,
    ) -> str:
        material = _workflow_material(call.payload)
        contract = material["answer_contract"]
        assert type(contract) is dict
        return _provider_envelope(
            call,
            {
                "evidence_ids": contract["evidence_ids"],
                "evidence_status": contract["evidence_status"],
                "message": message,
                "next_intent": next_intent,
                "reply_event_ids": contract["reply_event_ids"],
                "scope": contract["scope"],
                "unresolved_input_ids": (
                    [] if unresolved_input_ids is None else unresolved_input_ids
                ),
            },
        )

    return respond


def _search_history(
    call: ContextProviderCallRequest,
    _provider: _LeadContextProvider,
) -> str:
    return json.dumps(
        {
            "a": "s",
            "c": call.payload["c"],
            "d": [["visible lead response", ["c"]]],
            "u": [],
        },
        separators=(",", ":"),
    )


def _read_history_hit(
    call: ContextProviderCallRequest,
    provider: _LeadContextProvider,
) -> str:
    observation = call.payload["o"]
    assert isinstance(observation, list) and observation[0] == "s"
    handle = observation[3][0][5]
    provider.used_handles = (handle,)
    return json.dumps(
        {
            "a": "r",
            "c": call.payload["c"],
            "d": [handle],
            "u": [],
        },
        separators=(",", ":"),
    )


def _answer_from_history(
    call: ContextProviderCallRequest,
    provider: _LeadContextProvider,
) -> str:
    observation = call.payload["o"]
    assert isinstance(observation, list) and observation[0] == "r"
    return _provider_envelope(
        call,
        {
            "kind": "answer",
            "message": "The prior lead response supplies enough evidence.",
            "scope": "story_specific",
            "evidence_status": "verified",
            "evidence_ids": [observation[1][0][5]],
            "artifact_intent": None,
            "proposal": None,
        },
        used_handles=provider.used_handles,
    )


def _decision(
    result: object, kind: CollaborationConfirmationDecision, **changes: object
):
    proposal = result.proposal
    values = {
        "confirmation_request_id": result.confirmation_request_id,
        "session_id": result.session_id,
        "conversation_id": result.conversation_id,
        "step_id": result.step_id,
        "proposal_id": proposal.proposal_id,
        "proposal_revision": proposal.proposal_revision,
        "proposal_digest": proposal.proposal_digest,
        "kind": kind,
        "revision_request": (
            "Revise the proposed review."
            if kind is CollaborationConfirmationDecision.REVISE
            else None
        ),
    }
    values.update(changes)
    return LeadStepDecision(**values)


class LeadOrchestrationTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="lead-orchestration-")
        self.addCleanup(temporary.cleanup)
        self.workspace = Workspace(temporary.name).initialize()
        self.workspace.path("core_documents/global_outline.md").write_text(
            "Outline first line.\nOutline second line.\n",
            encoding="utf-8",
        )
        self.workspace.path("core_documents/character_sheets.md").write_text(
            "Mara wants safety.\nMara fears exposure.\n",
            encoding="utf-8",
        )
        part = self.workspace.path("manuscript/part-01")
        part.mkdir(parents=True, exist_ok=True)
        (part / "opening.md").write_text(
            "Opening one.\nOpening two.\nOpening three.\n",
            encoding="utf-8",
        )
        self.backend = _LeadBackend()
        self.runtime = StoryRuntime(self.workspace, self.backend)  # type: ignore[arg-type]
        self.session_id = "story_session"

    def conversation(self, subject: str = "Lead collaboration") -> str:
        return self.runtime.conversation_storage_services.catalog.create(
            self.session_id,
            subject,
        ).conversation_id

    def history(self, conversation_id: str):
        return self.runtime.conversation_storage_services.event_store.read(
            self.session_id,
            conversation_id,
        )

    async def proposal(
        self,
        conversation_id: str,
        *,
        text: str = "Please check the story direction.",
        assignments: list[dict[str, object]] | None = None,
        relation: str = "new_step",
    ):
        self.backend.enqueue(
            _proposal_outcome(
                assignments
                or [
                    {
                        "agent_id": PLOT_ARCHITECT,
                        "instruction": "Check the story direction against the request.",
                        "input_ids": ["user_input"],
                    }
                ],
                relation=relation,
            )
        )
        return await self.runtime.lead_turn(
            self.session_id,
            conversation_id,
            text,
        )


class LeadOutcomeAndProposalTests(LeadOrchestrationTestCase):
    async def test_direct_search_read_answer_uses_bounded_history_without_proposal(
        self,
    ) -> None:
        conversation_id = self.conversation("Searchable lead history")
        self.backend.enqueue(_visible_outcome("answer"))
        await self.runtime.lead_turn(
            self.session_id,
            conversation_id,
            "Create one prior visible answer.",
        )
        first_call_count = len(self.backend.calls)
        self.backend.enqueue(_search_history, _read_history_hit, _answer_from_history)

        result = await self.runtime.lead_turn(
            self.session_id,
            conversation_id,
            "Use bounded conversation history if needed.",
        )

        calls = self.backend.calls[first_call_count:]
        self.assertEqual(len(calls), 3)
        self.assertIs(result.kind, LeadOutcomeKind.ANSWER)
        self.assertIsNone(result.proposal)
        self.assertIsNone(result.confirmation_request_id)
        self.assertEqual(
            tuple(call.payload["q"][1] for call in calls),
            ("sa", "sra", "sa"),
        )
        self.assertNotIn(
            "Visible lead response.",
            json.dumps(dict(calls[0].payload), ensure_ascii=False),
        )
        self.assertIn(
            "visible lead response",
            json.dumps(dict(calls[1].payload), ensure_ascii=False).lower(),
        )
        self.assertEqual(
            project_collaboration_history(
                self.history(conversation_id)
            ).physical_calls_spent,
            3,
        )
        self.assertEqual(
            sum(
                type(event.payload) is CollaborationProviderCallPayload
                and event.payload.state is CollaborationProviderCallState.SUBMITTED
                for event in self.history(conversation_id)
            ),
            4,
        )

    async def test_all_five_outcomes_have_exact_visible_next_actions(self) -> None:
        cases = (
            ("answer", CollaborationNextAction.CONTINUE_WITH_LEAD),
            ("question", CollaborationNextAction.REPLY_TO_LEAD),
            ("critique", CollaborationNextAction.CONTINUE_WITH_LEAD),
            ("recommendation", CollaborationNextAction.CONTINUE_WITH_LEAD),
        )
        for kind, next_action in cases:
            with self.subTest(kind=kind):
                conversation_id = self.conversation(f"Outcome {kind}")
                self.backend.enqueue(_visible_outcome(kind))
                result = await self.runtime.session(self.session_id).lead_turn(
                    conversation_id,
                    f"Return a {kind}.",
                )
                self.assertIs(result.kind, LeadOutcomeKind(kind))
                self.assertIsNone(result.proposal)
                self.assertIsNone(result.state)
                self.assertIs(result.next_action, next_action)
                history = self.history(conversation_id)
                self.assertEqual(
                    tuple(event.kind for event in history),
                    (
                        CollaborationEventKind.MESSAGE,
                        CollaborationEventKind.PROVIDER_CALL_ACCOUNTING,
                        CollaborationEventKind.PROVIDER_CALL_ACCOUNTING,
                        CollaborationEventKind.MESSAGE,
                    ),
                )
                self.assertEqual(
                    project_collaboration_history(history).physical_calls_spent,
                    1,
                )

        proposal_id = self.conversation("Proposal")
        proposed = await self.proposal(proposal_id)
        self.assertIs(proposed.kind, LeadOutcomeKind.PROPOSAL)
        self.assertIs(proposed.state, CollaborationStepState.AWAITING_CONFIRMATION)
        self.assertIs(
            proposed.next_action,
            CollaborationNextAction.SUBMIT_CONFIRMATION,
        )

    async def test_strict_provider_outcomes_reject_unknown_private_mixed_and_duplicate(
        self,
    ) -> None:
        def duplicate(call: ContextProviderCallRequest, _provider: object) -> str:
            correlation = call.payload["c"]
            return (
                '{"a":"a","c":'
                + json.dumps(correlation)
                + ',"d":[{"kind":"answer","kind":"question",'
                '"message":"Visible.","proposal":null}],"u":[]}'
            )

        invalid_values: tuple[object, ...] = (
            {"kind": "unknown", "message": "Visible.", "proposal": None},
            {
                "kind": "answer",
                "message": "Visible.",
                "proposal": None,
                "private_reasoning": "PRIVATE",
            },
            {
                "kind": "answer",
                "message": "Visible.",
                "proposal": {
                    "relation": "new_step",
                    "purpose": "Mixed.",
                    "assignments": [],
                },
            },
            ["answer", "Visible."],
        )
        scripts: tuple[_Response, ...] = tuple(
            (lambda value: (lambda call, _provider: _provider_envelope(call, value)))(
                value
            )
            for value in invalid_values
        ) + (duplicate,)
        for index, script in enumerate(scripts):
            with self.subTest(index=index):
                conversation_id = self.conversation(f"Invalid {index}")
                self.backend.enqueue(script)
                if index < 3:
                    result = await self.runtime.lead_turn(
                        self.session_id,
                        conversation_id,
                        "Reject a noncanonical outcome.",
                    )
                    self.assertIs(
                        result.next_action,
                        CollaborationNextAction.RETRY_LEAD_TURN,
                    )
                    self.assertIs(result.kind, LeadOutcomeKind.RECOMMENDATION)
                else:
                    result = await self.runtime.lead_turn(
                        self.session_id,
                        conversation_id,
                        "Reject a noncanonical outcome.",
                    )
                    self.assertIs(
                        result.state,
                        CollaborationStepState.OUTCOME_UNKNOWN,
                    )
                    self.assertIs(
                        result.next_action,
                        CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME,
                    )
                history = self.history(conversation_id)
                self.assertFalse(
                    any(
                        type(event.payload) is CollaborationProposal
                        for event in history
                    )
                )
                if index < 3:
                    failures = tuple(
                        event.payload
                        for event in history
                        if type(event.payload) is CollaborationLeadOutcomeFailurePayload
                    )
                    self.assertEqual(len(failures), 1)
                    self.assertIs(
                        failures[0].code,
                        CollaborationLeadOutcomeFailureCode.EVIDENCE_VALIDATION_REJECTED,
                    )
                self.assertFalse(
                    any(
                        type(event.payload) is CollaborationMessagePayload
                        and event.sender.agent_id == LEAD_ORCHESTRATOR
                        for event in history
                    )
                )

    async def test_runtime_constructs_complete_ordered_content_free_proposal(
        self,
    ) -> None:
        conversation_id = self.conversation("Cross-domain proposal")
        text = (
            "Check these sources in order: "
            "${core_documents/global_outline.md} "
            "${core_documents/character_sheets.md} "
            "${manuscript/part-01/opening.md}#L2-L3"
        )
        assignments = [
            {
                "agent_id": PLOT_ARCHITECT,
                "instruction": "Check outline causality and the opening range.",
                "input_ids": ["user_input", "reference_1", "reference_3"],
            },
            {
                "agent_id": CHARACTER_CUSTODIAN,
                "instruction": "Check the established character motivation.",
                "input_ids": ["reference_2"],
            },
        ]

        result = await self.proposal(
            conversation_id,
            text=text,
            assignments=assignments,
        )

        proposal = result.proposal
        assert isinstance(proposal, CollaborationProposal)
        self.assertEqual(
            tuple(item.specialist_id for item in proposal.assignments),
            (PLOT_ARCHITECT, CHARACTER_CUSTODIAN),
        )
        self.assertEqual(
            tuple(item.locator for item in proposal.basis.inputs[1:]),
            (
                "${core_documents/global_outline.md}",
                "${core_documents/character_sheets.md}",
                "${manuscript/part-01/opening.md}#L2-L3",
            ),
        )
        self.assertEqual(
            tuple((item.start_line, item.end_line) for item in proposal.basis.inputs),
            ((None, None), (None, None), (None, None), (2, 3)),
        )
        self.assertEqual(
            tuple(item.kind for item in proposal.basis.inputs),
            (
                CollaborationInputKind.USER_INPUT,
                CollaborationInputKind.CORE_DOCUMENT,
                CollaborationInputKind.CORE_DOCUMENT,
                CollaborationInputKind.MANUSCRIPT,
            ),
        )
        self.assertEqual(
            proposal.side_effects, (CollaborationSideEffect.PROVIDER_INVOCATION,)
        )
        self.assertIn("cannot change writing artifacts", proposal.user_impact.lower())
        self.assertEqual(proposal.budget.physical_calls_spent, 2)
        self.assertLessEqual(
            proposal.budget.physical_calls_spent
            + proposal.budget.max_additional_physical_calls,
            proposal.budget.physical_call_limit,
        )
        self.assertEqual(
            tuple(item.agent_id for item in proposal.basis.agent_profiles),
            (LEAD_ORCHESTRATOR, PLOT_ARCHITECT, CHARACTER_CUSTODIAN),
        )
        self.assertEqual(len(self.backend.reference_contexts), 1)
        self.assertEqual(len(self.backend.reference_contexts[0]), 3)
        self.assertIn(
            "Outline first line", self.backend.reference_contexts[0][0]["content"]
        )
        self.assertNotIn("Outline first line", repr(proposal))
        history = self.history(conversation_id)
        call_payloads = tuple(
            event.payload
            for event in history
            if type(event.payload) is CollaborationProviderCallPayload
        )
        self.assertEqual(
            tuple(item.state for item in call_payloads),
            (
                CollaborationProviderCallState.PREPARED,
                CollaborationProviderCallState.SUBMITTED,
            )
            * 2,
        )
        self.assertEqual(
            tuple(item.invocation_kind for item in call_payloads),
            (CollaborationInvocationKind.LEAD,) * 4,
        )

    async def test_runtime_rejects_invalid_specialists_aliases_order_and_reviewers(
        self,
    ) -> None:
        cases = (
            (
                "lead-specialist",
                "Plain request.",
                [
                    {
                        "agent_id": LEAD_ORCHESTRATOR,
                        "instruction": "Invalid self assignment.",
                        "input_ids": ["user_input"],
                    }
                ],
            ),
            (
                "duplicate-specialist",
                "Plain request.",
                [
                    {
                        "agent_id": PLOT_ARCHITECT,
                        "instruction": "First.",
                        "input_ids": ["user_input"],
                    },
                    {
                        "agent_id": PLOT_ARCHITECT,
                        "instruction": "Second.",
                        "input_ids": ["user_input"],
                    },
                ],
            ),
            (
                "noncanonical-order",
                "Plain request.",
                [
                    {
                        "agent_id": CHARACTER_CUSTODIAN,
                        "instruction": "Character first.",
                        "input_ids": ["user_input"],
                    },
                    {
                        "agent_id": PLOT_ARCHITECT,
                        "instruction": "Plot second.",
                        "input_ids": ["user_input"],
                    },
                ],
            ),
            (
                "unknown-alias",
                "Plain request.",
                [
                    {
                        "agent_id": PLOT_ARCHITECT,
                        "instruction": "Use an invalid alias.",
                        "input_ids": ["unknown"],
                    }
                ],
            ),
            (
                "missing-reviewer",
                "Check ${core_documents/global_outline.md}",
                [
                    {
                        "agent_id": CHARACTER_CUSTODIAN,
                        "instruction": "Wrong mandatory reviewer.",
                        "input_ids": ["user_input", "reference_1"],
                    }
                ],
            ),
        )
        for label, text, assignments in cases:
            with self.subTest(label=label):
                conversation_id = self.conversation(label)
                self.backend.enqueue(_proposal_outcome(assignments))
                result = await self.runtime.lead_turn(
                    self.session_id,
                    conversation_id,
                    text,
                )
                self.assertIs(
                    result.next_action,
                    CollaborationNextAction.RETRY_LEAD_TURN,
                )
                self.assertFalse(
                    any(
                        type(event.payload) is CollaborationProposal
                        for event in self.history(conversation_id)
                    )
                )
                self.assertEqual(
                    sum(
                        type(event.payload) is CollaborationLeadOutcomeFailurePayload
                        for event in self.history(conversation_id)
                    ),
                    1,
                )


class LeadDecisionAuthorityTests(LeadOrchestrationTestCase):
    async def test_confirm_is_exact_single_use_and_executes_without_artifact_effect(
        self,
    ) -> None:
        conversation_id = self.conversation("Confirm")
        proposed = await self.proposal(conversation_id)
        calls_before = len(self.backend.calls)
        self.backend.enqueue_for(PLOT_ARCHITECT, _specialist_outcome())
        self.backend.enqueue_for(LEAD_ORCHESTRATOR, _synthesis_outcome())
        artifacts_before = {
            path: path.read_bytes() for path in self.workspace.root.rglob("*.md")
        }

        confirmed = await self.runtime.decide_lead_step(
            _decision(proposed, CollaborationConfirmationDecision.CONFIRM)
        )

        self.assertIs(confirmed.turn.state, CollaborationStepState.COMPLETED)
        self.assertIs(
            confirmed.turn.next_action,
            CollaborationNextAction.CONTINUE_WITH_LEAD,
        )
        self.assertEqual(len(self.backend.calls), calls_before + 2)
        self.assertEqual(
            {path: path.read_bytes() for path in artifacts_before},
            artifacts_before,
        )
        history = self.history(conversation_id)
        self.assertTrue(
            {
                CollaborationEventKind.ASSIGNMENT_DISPATCH,
                CollaborationEventKind.SPECIALIST_REPLY,
                CollaborationEventKind.LEAD_SYNTHESIS,
                CollaborationEventKind.EXECUTION_RESULT,
            }.issubset({event.kind for event in history})
        )
        with self.assertRaises(ValueError):
            await self.runtime.decide_lead_step(
                _decision(proposed, CollaborationConfirmationDecision.CONFIRM)
            )

    async def test_mismatch_reject_revise_and_free_text_are_non_authorizing(
        self,
    ) -> None:
        mismatch_id = self.conversation("Mismatch")
        mismatch = await self.proposal(mismatch_id)
        calls = len(self.backend.calls)
        for changes in (
            {"confirmation_request_id": "wrong_request"},
            {"proposal_id": "wrong_proposal"},
            {"proposal_revision": 2},
            {"proposal_digest": "sha256:" + "0" * 64},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    await self.runtime.decide_lead_step(
                        _decision(
                            mismatch,
                            CollaborationConfirmationDecision.CONFIRM,
                            **changes,
                        )
                    )
                self.assertEqual(len(self.backend.calls), calls)

        reject_id = self.conversation("Reject")
        rejected_proposal = await self.proposal(reject_id)
        calls = len(self.backend.calls)
        rejected = await self.runtime.decide_lead_step(
            _decision(rejected_proposal, CollaborationConfirmationDecision.REJECT)
        )
        self.assertIs(rejected.turn.state, CollaborationStepState.REJECTED)
        self.assertEqual(len(self.backend.calls), calls)

        revise_id = self.conversation("Revise")
        revised_source = await self.proposal(revise_id)
        self.backend.enqueue(
            _proposal_outcome(
                [
                    {
                        "agent_id": PLOT_ARCHITECT,
                        "instruction": "Perform the revised plot check.",
                        "input_ids": ["user_input"],
                    }
                ],
                relation="revise_pending",
            )
        )
        revised = await self.runtime.decide_lead_step(
            _decision(revised_source, CollaborationConfirmationDecision.REVISE)
        )
        replacement = revised.turn.proposal
        assert replacement is not None
        self.assertEqual(replacement.step_id, revised_source.step_id)
        self.assertEqual(
            replacement.proposal_revision,
            revised_source.proposal.proposal_revision + 1,
        )
        self.assertNotEqual(
            replacement.proposal_id, revised_source.proposal.proposal_id
        )
        self.assertNotEqual(
            replacement.proposal_digest,
            revised_source.proposal.proposal_digest,
        )

        free_id = self.conversation("Free text")
        pending = await self.proposal(free_id)
        self.backend.enqueue(_visible_outcome("answer", "Yes is ordinary prose."))
        answered = await self.runtime.lead_turn(self.session_id, free_id, "Yes")
        self.assertIs(answered.kind, LeadOutcomeKind.ANSWER)
        history = self.history(free_id)
        yes_event = next(
            event
            for event in history
            if type(event.payload) is CollaborationMessagePayload
            and event.payload.content == "Yes"
        )
        superseded = next(
            event
            for event in history
            if getattr(event.payload, "state", None)
            is CollaborationStepState.SUPERSEDED
        )
        next_prepared = next(
            event
            for event in history
            if event.sequence > yes_event.sequence
            and type(event.payload) is CollaborationProviderCallPayload
        )
        self.assertEqual(superseded.batch_id, yes_event.batch_id)
        self.assertLess(superseded.sequence, next_prepared.sequence)
        self.assertEqual(pending.step_id, superseded.step_id)

    async def test_reference_drift_blocks_confirmation_without_calls(self) -> None:
        conversation_id = self.conversation("Drift")
        proposed = await self.proposal(
            conversation_id,
            text="Check ${core_documents/global_outline.md}",
            assignments=[
                {
                    "agent_id": PLOT_ARCHITECT,
                    "instruction": "Check the exact outline revision.",
                    "input_ids": ["user_input", "reference_1"],
                }
            ],
        )
        calls = len(self.backend.calls)
        self.workspace.path("core_documents/global_outline.md").write_text(
            "A different outline revision.\n",
            encoding="utf-8",
        )

        with self.assertRaises(BackendProtocolError):
            await self.runtime.decide_lead_step(
                _decision(proposed, CollaborationConfirmationDecision.CONFIRM)
            )

        self.assertEqual(len(self.backend.calls), calls)
        self.assertIs(
            project_collaboration_history(self.history(conversation_id)).current_state,
            CollaborationStepState.AWAITING_CONFIRMATION,
        )

    async def test_revise_can_return_each_direct_superseding_visible_outcome(
        self,
    ) -> None:
        for kind in ("answer", "question", "critique", "recommendation"):
            with self.subTest(kind=kind):
                conversation_id = self.conversation(f"Direct {kind} revision outcome")
                proposed = await self.proposal(conversation_id)
                self.backend.enqueue(
                    _visible_outcome(
                        kind,
                        "The revised direction is safe without another consultation.",
                    )
                )

                revised = await self.runtime.decide_lead_step(
                    _decision(proposed, CollaborationConfirmationDecision.REVISE)
                )

                self.assertIs(revised.turn.kind, LeadOutcomeKind(kind))
                self.assertIs(
                    revised.turn.state,
                    CollaborationStepState.SUPERSEDED,
                )
                self.assertIsNone(revised.turn.proposal)
                self.assertIn("safe without another consultation", revised.turn.message)
                projection = project_collaboration_history(
                    self.history(conversation_id)
                )
                self.assertIs(
                    projection.current_state,
                    CollaborationStepState.SUPERSEDED,
                )


class LeadRecoveryAndAccountingTests(LeadOrchestrationTestCase):
    async def test_restart_after_completed_execution_accepts_a_new_lead_turn(
        self,
    ) -> None:
        conversation_id = self.conversation("Restart completed")
        proposed = await self.proposal(conversation_id)
        self.backend.enqueue_for(PLOT_ARCHITECT, _specialist_outcome())
        self.backend.enqueue_for(LEAD_ORCHESTRATOR, _synthesis_outcome())
        await self.runtime.decide_lead_step(
            _decision(proposed, CollaborationConfirmationDecision.CONFIRM)
        )
        restart_backend = _LeadBackend()
        restart_backend.enqueue(_visible_outcome("answer"))
        restarted = StoryRuntime(self.workspace, restart_backend)  # type: ignore[arg-type]

        result = await restarted.lead_turn(
            self.session_id,
            conversation_id,
            "Continue after the completed consultation.",
        )

        self.assertIs(result.kind, LeadOutcomeKind.ANSWER)
        self.assertIsNone(result.state)
        self.assertEqual(len(restart_backend.calls), 1)

    async def test_unstarted_prepared_and_submitted_requests_never_replay(self) -> None:
        unstarted_id = self.conversation("Unstarted")
        failing_backend = _LeadBackend(bind_failure=RuntimeError("bind failed"))
        failing_runtime = StoryRuntime(self.workspace, failing_backend)  # type: ignore[arg-type]
        with self.assertRaises(RuntimeError):
            await failing_runtime.lead_turn(
                self.session_id,
                unstarted_id,
                "Persist before bind.",
            )
        recovered = await self.runtime.lead_turn(
            self.session_id,
            unstarted_id,
            "Do not replay.",
        )
        self.assertIs(recovered.state, CollaborationStepState.RECOVERY_REQUIRED)

        prepared_id = self.conversation("Prepared")
        self.backend.enqueue(RuntimeError("failed before submission"))
        with self.assertRaises(RuntimeError):
            await self.runtime.lead_turn(
                self.session_id,
                prepared_id,
                "Leave PREPARED durable.",
            )
        prepared_calls = len(self.backend.calls)
        prepared_recovery = await self.runtime.lead_turn(
            self.session_id,
            prepared_id,
            "Do not replay PREPARED.",
        )
        self.assertIs(prepared_recovery.state, CollaborationStepState.RECOVERY_REQUIRED)
        self.assertEqual(len(self.backend.calls), prepared_calls)
        self.assertEqual(
            project_collaboration_history(
                self.history(prepared_id)
            ).physical_calls_spent,
            0,
        )

        submitted_id = self.conversation("Submitted")
        self.backend.enqueue(_SubmittedFailure(RuntimeError("submitted failure")))
        submitted_result = await self.runtime.lead_turn(
            self.session_id,
            submitted_id,
            "Leave SUBMITTED durable.",
        )
        submitted_calls = len(self.backend.calls)
        submitted_events = self.history(submitted_id)
        self.assertIs(submitted_result.state, CollaborationStepState.OUTCOME_UNKNOWN)
        self.assertIs(
            submitted_result.next_action,
            CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME,
        )
        for fragment in (
            "submitted",
            "no reliable result",
            "Do not retry or replay",
            "state is preserved",
            "another conversation",
        ):
            self.assertIn(fragment, submitted_result.message)

        restart_backend = _LeadBackend()
        restarted = StoryRuntime(self.workspace, restart_backend)  # type: ignore[arg-type]
        submitted_recovery = await restarted.lead_turn(
            self.session_id,
            submitted_id,
            "Do not replay SUBMITTED.",
        )
        self.assertEqual(
            (
                submitted_recovery.state,
                submitted_recovery.next_action,
                submitted_recovery.message,
                submitted_recovery.step_id,
                submitted_recovery.turn_id,
            ),
            (
                submitted_result.state,
                submitted_result.next_action,
                submitted_result.message,
                submitted_result.step_id,
                submitted_result.turn_id,
            ),
        )
        self.assertEqual(len(self.backend.calls), submitted_calls)
        self.assertEqual(restart_backend.calls, [])
        self.assertEqual(self.history(submitted_id), submitted_events)
        self.assertEqual(
            sum(
                event.kind is CollaborationEventKind.MESSAGE
                and isinstance(event.payload, CollaborationMessagePayload)
                and event.payload.content == "Leave SUBMITTED durable."
                for event in submitted_events
            ),
            1,
        )
        self.assertEqual(
            project_collaboration_history(
                submitted_events
            ).physical_calls_spent,
            1,
        )

    async def test_unresolved_running_becomes_outcome_unknown_without_provider(
        self,
    ) -> None:
        conversation_id = self.conversation("Running restart")
        batches = contract_batches(conversation_id)
        store = self.runtime.conversation_storage_services.event_store
        for name in ("proposal", "confirmation", "running", "calls"):
            batch = batches[name]
            store.append_batch(
                replace(
                    batch,
                    events=tuple(
                        replace(event, session_id=self.session_id)
                        for event in batch.events
                    ),
                )
            )

        result = await self.runtime.lead_turn(
            self.session_id,
            conversation_id,
            "Never replay running work.",
        )

        self.assertIs(result.state, CollaborationStepState.OUTCOME_UNKNOWN)
        self.assertIs(
            result.next_action,
            CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME,
        )
        self.assertEqual(self.backend.calls, [])
        self.assertIs(
            project_collaboration_history(self.history(conversation_id)).current_state,
            CollaborationStepState.OUTCOME_UNKNOWN,
        )

    def test_capability_is_private_nonserializable_and_single_use(self) -> None:
        capability = orchestrator_module._ConfirmedStepCapability(
            session_id="session",
            conversation_id="conversation",
            step_id="step",
            proposal_id="proposal",
            proposal_revision=1,
            proposal_digest="sha256:" + "1" * 64,
            basis_digest="sha256:" + "2" * 64,
            tail_event_id="tail",
            tail_sequence=1,
        )
        self.assertNotIn("1" * 64, repr(capability))
        with self.assertRaises(TypeError):
            pickle.dumps(capability)
        capability.consume()
        with self.assertRaises(BackendProtocolError):
            capability.consume()
        self.assertFalse(hasattr(RuntimeSession, "execution_capability"))
        self.assertFalse(hasattr(StoryRuntime, "execution_capability"))


if __name__ == "__main__":
    unittest.main()
