"""Management, configuration, and inspection for story runtimes."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .agents import (
    CHARACTER_CUSTODIAN,
    EXECUTION_SPECIALIST,
    LEAD_ORCHESTRATOR,
    PLOT_ARCHITECT,
    ROSTER,
    SPECIALIST_IDS,
)
from .agent_context.accounting import (
    ProviderCallAccounting,
    ProviderCallKind,
    ProviderCallState,
)
from .agent_context.manifest import (
    ContextAuthorityKind,
    ContextManifestEntry,
    ContextPriority,
    ContextProvenance,
    ContextSemanticChannel,
    ContextSourceKind,
    ContextSubjectKind,
)
from .agent_context.planning import ContextMaterial, MandatoryReferenceReservation
from .agent_context.service import (
    AgentContextService,
    ContextExecutionEvent,
    ContextInvocationKind,
    ContextInvocationRequest,
    ContextJsonObjectResultContract,
    ContextProviderCallRequest,
    ContextProviderCallResult,
    ContextReferencePreludeRequest,
    ContextReferencePreludeResult,
    context_provider_failure_facts,
)
from .agent_context.sources import (
    ActiveDebateSourceAdapter,
    FocusedConversationSourceAdapter,
    OwnAgentStateSourceAdapter,
    WritingArtifactSourceAdapter,
    writing_artifact_source_id,
)
from .backend import (
    AgentBackend,
    BackendRuntimeConfigurationCandidate,
    BoundAgent,
    backend_configuration_snapshot,
    release_bound_agent,
)
from .config import ConfigurationSnapshot, ConfigManager
from .conversation_rag_runtime import ConversationRagRuntimeService
from .conversation_storage.composition import compose_filesystem_conversation_storage
from .conversation_storage.contracts import ConversationStorageServices
from .conversations import (
    COLLABORATION_EVENT_SCHEMA_VERSION,
    COLLABORATION_PHYSICAL_CALL_LIMIT,
    COLLABORATION_ROUND_LIMIT,
    CollaborationActor,
    CollaborationActorKind,
    CollaborationAgentProfile,
    CollaborationAssignment,
    CollaborationBudget,
    CollaborationConfirmationDecision,
    CollaborationConfirmationPayload,
    CollaborationEvent,
    CollaborationEventBatch,
    CollaborationEventDraft,
    CollaborationEventKind,
    CollaborationHistoryProjection,
    CollaborationInputKind,
    CollaborationInputRevision,
    CollaborationInputState,
    CollaborationInterruptionPayload,
    CollaborationInvocationKind,
    CollaborationMessagePayload,
    CollaborationNextAction,
    CollaborationPayload,
    CollaborationProposal,
    CollaborationProposalBasis,
    CollaborationProviderCallKind,
    CollaborationProviderCallPayload,
    CollaborationProviderCallState,
    CollaborationSideEffect,
    CollaborationStepKind,
    CollaborationStepState,
    CollaborationStepStatePayload,
    ConversationStatus,
    LeadAssignmentIntent,
    LeadDecisionResult,
    LeadOutcomeKind,
    LeadProposalIntent,
    LeadProposalRelation,
    LeadProviderOutcome,
    LeadStepDecision,
    LeadTurnResult,
    project_collaboration_history,
)
from .debug_log import (
    DebugComponent,
    DebugOperation,
    DebugOutcome,
    DebugPhase,
    WorkspaceDebugLog,
)
from .directives import apply_directive as persist_directive
from .errors import BackendProtocolError, ConversationNotFoundError
from .prompts import PromptAssembler
from .workspace import Workspace
from .writing_artifacts import (
    MAX_REFERENCE_TOTAL_BYTES,
    MAX_REFERENCE_UTF8_BYTES,
    ReferenceOperationHistory,
    parse_artifact_references,
)
from .writing_artifacts.repository import WritingArtifactRepository
from .writing_artifacts.runtime_references import (
    RuntimeReferenceOperation,
    prepare_runtime_reference_operation,
    reference_operation_belongs_to_session,
)


_USER_ACTOR = CollaborationActor(CollaborationActorKind.USER)
_LEAD_ACTOR = CollaborationActor(
    CollaborationActorKind.LEAD,
    LEAD_ORCHESTRATOR,
)
_RUNTIME_ACTOR = CollaborationActor(CollaborationActorKind.RUNTIME)
_CONTEXT_RESULT_CONTRACT = ContextJsonObjectResultContract("outcome-v1")
_BUDGET_POLICY_FINGERPRINT = (
    "sha256:"
    + hashlib.sha256(
        b"lead-confirmed-collaboration-budget-v1;rounds=2;calls=12;"
        b"context=5;references=prelude;allocation=specialist-round-robin"
    ).hexdigest()
)
_CONTEXT_INVOCATION_CALL_LIMIT = 5
_LEAD_PROPOSAL_USER_IMPACT = (
    "Specialist advice and durable collaboration records only. "
    "This step cannot change writing artifacts, the Git index, commits, "
    "remotes, or merges."
)
_CORE_REVIEWERS = {
    "core_documents/global_outline.md": PLOT_ARCHITECT,
    "core_documents/part_outlines.md": PLOT_ARCHITECT,
    "core_documents/character_sheets.md": CHARACTER_CUSTODIAN,
    "core_documents/narrative_rules.md": EXECUTION_SPECIALIST,
}


class _DuplicateJsonKey(ValueError):
    pass


class _SupersededBindingSelection(Exception):
    """Retry a selection-only request whose bind completed out of order."""


class _ConfirmedStepCapability:
    """Process-local, nonserializable, single-use exact confirmed-step authority."""

    __slots__ = (
        "_consumed",
        "_secret",
        "basis_digest",
        "proposal_digest",
        "proposal_id",
        "proposal_revision",
        "session_id",
        "step_id",
        "tail_event_id",
        "tail_sequence",
        "conversation_id",
    )

    def __init__(
        self,
        *,
        session_id: str,
        conversation_id: str,
        step_id: str,
        proposal_id: str,
        proposal_revision: int,
        proposal_digest: str,
        basis_digest: str,
        tail_event_id: str,
        tail_sequence: int,
    ) -> None:
        self.session_id = session_id
        self.conversation_id = conversation_id
        self.step_id = step_id
        self.proposal_id = proposal_id
        self.proposal_revision = proposal_revision
        self.proposal_digest = proposal_digest
        self.basis_digest = basis_digest
        self.tail_event_id = tail_event_id
        self.tail_sequence = tail_sequence
        self._secret = bytearray(uuid.uuid4().bytes + uuid.uuid4().bytes)
        self._consumed = False

    def __repr__(self) -> str:
        return "_ConfirmedStepCapability(<content hidden>)"

    def __reduce__(self) -> object:
        raise TypeError("confirmed-step capability cannot be serialized")

    def consume(self) -> None:
        if self._consumed:
            raise BackendProtocolError("confirmed-step capability is already consumed")
        self._consumed = True
        for index in range(len(self._secret)):
            self._secret[index] = 0


class _AccountingContextProvider:
    """Persist content-free call lifecycle facts around one backend context seam."""

    def __init__(
        self,
        runtime: StoryRuntime,
        delegate: object,
        *,
        session_id: str,
        conversation_id: str,
        turn_id: str,
        step_id: str,
        invocation_id: str,
    ) -> None:
        self._runtime = runtime
        self._delegate = delegate
        self._session_id = session_id
        self._conversation_id = conversation_id
        self._turn_id = turn_id
        self._step_id = step_id
        self._invocation_id = invocation_id
        self._ordinals: dict[str, int] = {}

    @property
    def provider_id(self) -> str:
        return getattr(self._delegate, "provider_id")

    @property
    def definition_fingerprint(self) -> str:
        return getattr(self._delegate, "definition_fingerprint")

    @property
    def reference_operation_id(self) -> str | None:
        return getattr(self._delegate, "reference_operation_id")

    def invocation(self, scope: object, policy_fingerprint: str) -> object:
        return getattr(self._delegate, "invocation")(scope, policy_fingerprint)

    def inspect_context_thread(
        self,
        scope: object,
        policy_fingerprint: str,
    ) -> str | None:
        return getattr(self._delegate, "inspect_context_thread")(
            scope,
            policy_fingerprint,
        )

    async def prepare_references(
        self,
        request: ContextReferencePreludeRequest,
    ) -> ContextReferencePreludeResult:
        ordinal = self._prepare(
            request.call_id,
            CollaborationProviderCallKind.REFERENCE_PRELUDE,
        )
        try:
            result = await getattr(self._delegate, "prepare_references")(request)
        except BaseException as failure:
            self._record_failure_submission(
                failure,
                request.call_id,
                ordinal,
                CollaborationProviderCallKind.REFERENCE_PRELUDE,
            )
            raise
        self._record_result_submission(
            result,
            request.call_id,
            ordinal,
            CollaborationProviderCallKind.REFERENCE_PRELUDE,
        )
        return result

    async def invoke_context(
        self,
        request: ContextProviderCallRequest,
    ) -> ContextProviderCallResult:
        kind = (
            CollaborationProviderCallKind.INITIAL
            if request.call_kind
            in {
                ProviderCallKind.CONVERSATION,
                ProviderCallKind.CONVERSATION_RETRIEVAL_INITIAL,
                ProviderCallKind.DEBATE,
            }
            else CollaborationProviderCallKind.CONTINUATION
        )
        ordinal = self._prepare(request.call_id, kind)
        try:
            result = await getattr(self._delegate, "invoke_context")(request)
        except BaseException as failure:
            self._record_failure_submission(
                failure,
                request.call_id,
                ordinal,
                kind,
            )
            raise
        self._record_result_submission(result, request.call_id, ordinal, kind)
        return result

    def complete_references(self, scope: object, policy_fingerprint: str) -> None:
        getattr(self._delegate, "complete_references")(scope, policy_fingerprint)

    def abort_references(
        self,
        scope: object,
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

    def taint_context_thread(self, scope: object, policy_fingerprint: str) -> None:
        getattr(self._delegate, "taint_context_thread")(scope, policy_fingerprint)

    def _prepare(self, call_id: str, kind: CollaborationProviderCallKind) -> int:
        if call_id in self._ordinals:
            raise BackendProtocolError("provider call ID was prepared more than once")
        ordinal = len(self._ordinals) + 1
        self._ordinals[call_id] = ordinal
        self._runtime._append_provider_call_fact(
            session_id=self._session_id,
            conversation_id=self._conversation_id,
            turn_id=self._turn_id,
            step_id=self._step_id,
            invocation_id=self._invocation_id,
            call_id=call_id,
            ordinal=ordinal,
            call_kind=kind,
            state=CollaborationProviderCallState.PREPARED,
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
    ) -> None:
        accounting = getattr(result, "accounting", None)
        if (
            type(accounting) is not ProviderCallAccounting
            or accounting.state is not ProviderCallState.SUBMITTED
        ):
            return
        self._submitted(call_id, ordinal, kind, accounting)

    def _submitted(
        self,
        call_id: str,
        ordinal: int,
        kind: CollaborationProviderCallKind,
        accounting: ProviderCallAccounting,
    ) -> None:
        if accounting.payload.call_kind is ProviderCallKind.UNCLASSIFIED:
            raise BackendProtocolError("submitted provider call is unclassified")
        self._runtime._append_provider_call_fact(
            session_id=self._session_id,
            conversation_id=self._conversation_id,
            turn_id=self._turn_id,
            step_id=self._step_id,
            invocation_id=self._invocation_id,
            call_id=call_id,
            ordinal=ordinal,
            call_kind=kind,
            state=CollaborationProviderCallState.SUBMITTED,
        )


@dataclass(frozen=True, slots=True)
class RuntimeConfigurationCandidate:
    """One quiescent runtime prompt/config candidate awaiting activation."""

    owner: StoryRuntime
    configuration: ConfigurationSnapshot
    prompts: PromptAssembler


class StoryRuntime:
    """Public management and lead front-channel/confirmation APIs."""

    def __init__(
        self,
        workspace: Workspace | str | Path,
        backend: AgentBackend | None = None,
        *,
        conversation_storage_services: ConversationStorageServices | None = None,
        conversation_rag_service: ConversationRagRuntimeService | None = None,
    ) -> None:
        workspace_literal = (
            str(workspace.root) if isinstance(workspace, Workspace) else str(workspace)
        )
        self.workspace = (
            workspace if isinstance(workspace, Workspace) else Workspace(workspace)
        )
        self._debug_log = WorkspaceDebugLog(self.workspace)
        self.backend = backend
        self.artifacts = WritingArtifactRepository(self.workspace.root)
        if (
            conversation_storage_services is not None
            and type(conversation_storage_services) is not ConversationStorageServices
        ):
            raise TypeError(
                "conversation_storage_services must be ConversationStorageServices"
            )
        if conversation_rag_service is not None and (
            type(conversation_rag_service) is not ConversationRagRuntimeService
        ):
            raise TypeError(
                "conversation_rag_service must be ConversationRagRuntimeService"
            )
        if conversation_storage_services is None:
            conversation_storage_services = compose_filesystem_conversation_storage(
                self.workspace
            )
        self.conversation_storage_services = conversation_storage_services
        self.conversation_rag_service = conversation_rag_service
        self._conversation_workspace_roots = tuple(
            dict.fromkeys((workspace_literal, str(self.workspace.root)))
        )
        backend_overrides = (
            getattr(backend, "backend_overrides", {}) if backend is not None else {}
        )
        self._configuration_snapshot = (
            backend_configuration_snapshot(backend)
            if isinstance(backend, AgentBackend)
            else None
        )
        route_generation_reader = (
            None
            if backend is None
            else getattr(backend, "provider_route_generations", None)
        )
        self.prompts = PromptAssembler(
            self.workspace,
            artifact_repository=self.artifacts,
            backend_overrides=backend_overrides,
            configuration_snapshot=self._configuration_snapshot,
            provider_route_generations=(
                {}
                if not callable(route_generation_reader)
                else route_generation_reader()
            ),
        )
        self.config = ConfigManager(self.workspace)
        self._bound_agents: dict[tuple[str, str], BoundAgent] = {}
        self._desired_binding_fingerprints: dict[str, str] = {}
        self._retiring_bound_agents: dict[tuple[str, str], BoundAgent] = {}
        self._bound_agent_uses: dict[int, tuple[BoundAgent, int]] = {}
        self._binding_tasks: dict[tuple[str, str], asyncio.Task[BoundAgent]] = {}
        self._binding_waiters: dict[tuple[str, str], int] = {}
        self._binding_lock = asyncio.Lock()
        self._conversation_locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._agent_context_service = AgentContextService(self.workspace)
        self._confirmed_capabilities: dict[
            tuple[str, str, str], _ConfirmedStepCapability
        ] = {}
        self._live_execution_markers: set[tuple[str, str, str, str]] = set()

    def _trace_debug(
        self,
        operation: DebugOperation,
        phase: DebugPhase,
        *,
        session_id: str | None = None,
        conversation_id: str | None = None,
        debate_id: str | None = None,
        agent_id: str | None = None,
        outcome: DebugOutcome | None = None,
    ) -> None:
        """Record a trace without allowing diagnostics to alter behavior."""

        try:
            self._debug_log.trace(
                component=DebugComponent.RUNTIME,
                operation=operation,
                phase=phase,
                session_id=session_id,
                conversation_id=conversation_id,
                debate_id=debate_id,
                agent_id=agent_id,
                outcome=outcome,
            )
        except BaseException:
            pass

    def _record_debug_error(
        self,
        operation: DebugOperation,
        failure: BaseException,
        *,
        session_id: str | None = None,
        conversation_id: str | None = None,
        debate_id: str | None = None,
        agent_id: str | None = None,
        phase: DebugPhase = DebugPhase.FAILED,
        outcome: DebugOutcome = DebugOutcome.FAILED,
    ) -> None:
        """Record a runtime failure without allowing diagnostics to replace it."""

        if (
            phase is DebugPhase.FAILED
            and outcome is DebugOutcome.FAILED
            and isinstance(
                failure,
                (asyncio.CancelledError, KeyboardInterrupt, SystemExit),
            )
        ):
            phase = DebugPhase.CANCELLED
            outcome = DebugOutcome.INTERRUPTED
        try:
            self._debug_log.error(
                DebugComponent.RUNTIME,
                operation,
                failure,
                phase=phase,
                session_id=session_id,
                conversation_id=conversation_id,
                debate_id=debate_id,
                agent_id=agent_id,
                outcome=outcome,
            )
        except BaseException:
            pass

    def session(self, session_id: str) -> RuntimeSession:
        """Return a validated management view bound to one session."""

        self.workspace.validate_identifier(session_id)
        return RuntimeSession(self, session_id)

    def apply_directive(self, session_id: str, text: str) -> dict[str, object]:
        """Persist one supported runtime directive for a session."""

        try:
            result = persist_directive(self.workspace, session_id, text)
        except (KeyboardInterrupt, SystemExit) as exc:
            self._record_debug_error(
                DebugOperation.DIRECTIVE,
                exc,
                session_id=session_id,
            )
            raise
        except Exception as exc:
            self._record_debug_error(
                DebugOperation.DIRECTIVE,
                exc,
                session_id=session_id,
            )
            raise
        self._trace_debug(
            DebugOperation.DIRECTIVE,
            DebugPhase.SUCCEEDED,
            session_id=session_id,
            outcome=DebugOutcome.COMPLETED,
        )
        return result

    def set_runtime_overrides(
        self,
        session_id: str,
        overrides: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Replace one session's overrides only after complete validation."""

        try:
            validated = self.config.validate_runtime_overrides(overrides)
            self.workspace.save_session_overrides(session_id, validated)
        except (KeyboardInterrupt, SystemExit) as exc:
            self._record_debug_error(
                DebugOperation.CONFIGURATION,
                exc,
                session_id=session_id,
            )
            raise
        except Exception as exc:
            self._record_debug_error(
                DebugOperation.CONFIGURATION,
                exc,
                session_id=session_id,
            )
            raise
        self._trace_debug(
            DebugOperation.CONFIGURATION,
            DebugPhase.SUCCEEDED,
            session_id=session_id,
            outcome=DebugOutcome.COMPLETED,
        )
        return validated

    async def prepare_runtime_configuration(
        self,
        configuration: ConfigurationSnapshot,
        provider_route_generations: Mapping[str, int],
    ) -> RuntimeConfigurationCandidate:
        """Prepare an exact prompt snapshot after proving runtime quiescence."""

        if self.backend is None:
            raise BackendProtocolError(
                "runtime configuration activation requires an AgentBackend"
            )
        if type(configuration) is not ConfigurationSnapshot:
            raise TypeError("configuration must be a ConfigurationSnapshot")
        prompts = PromptAssembler(
            self.workspace,
            artifact_repository=self.artifacts,
            backend_overrides=self.backend.backend_overrides,
            configuration_snapshot=configuration,
            provider_route_generations=provider_route_generations,
        )
        releases: list[BoundAgent] = []
        async with self._binding_lock:
            if self._bound_agent_uses or self._binding_tasks or self._binding_waiters:
                raise BackendProtocolError(
                    "runtime configuration requires a quiescent provider boundary"
                )
            releases.extend(self._bound_agents.values())
            releases.extend(self._retiring_bound_agents.values())
            self._bound_agents.clear()
            self._retiring_bound_agents.clear()
            self._desired_binding_fingerprints.clear()
        self._release_provider_bindings(releases)
        return RuntimeConfigurationCandidate(self, configuration, prompts)

    async def commit_runtime_configuration(
        self,
        candidate: RuntimeConfigurationCandidate,
        backend_candidate: BackendRuntimeConfigurationCandidate,
    ) -> None:
        """Activate backend and prompt snapshots as one quiescent shell state."""

        if (
            type(candidate) is not RuntimeConfigurationCandidate
            or candidate.owner is not self
        ):
            raise TypeError("runtime candidate does not belong to this runtime")
        async with self._binding_lock:
            if (
                self._bound_agents
                or self._retiring_bound_agents
                or self._bound_agent_uses
                or self._binding_tasks
                or self._binding_waiters
            ):
                raise BackendProtocolError(
                    "runtime configuration activation lost quiescence"
                )
            assert self.backend is not None
            self.backend.commit_runtime_configuration(backend_candidate)
            self._configuration_snapshot = candidate.configuration
            self.prompts = candidate.prompts

    @staticmethod
    def discard_runtime_configuration(
        candidate: RuntimeConfigurationCandidate,
    ) -> None:
        """Discard an uncommitted prompt candidate; it owns no resources."""

        if type(candidate) is not RuntimeConfigurationCandidate:
            raise TypeError("candidate must be a RuntimeConfigurationCandidate")

    def inspect_session(self, session_id: str) -> dict[str, Any]:
        """Return persisted session state without mutation."""

        try:
            result = self.workspace.inspect_session(session_id)
        except (KeyboardInterrupt, SystemExit) as exc:
            self._record_debug_error(
                DebugOperation.INSPECTION,
                exc,
                session_id=session_id,
            )
            raise
        except Exception as exc:
            self._record_debug_error(
                DebugOperation.INSPECTION,
                exc,
                session_id=session_id,
            )
            raise
        self._trace_debug(
            DebugOperation.INSPECTION,
            DebugPhase.OBSERVED,
            session_id=session_id,
            outcome=DebugOutcome.COMPLETED,
        )
        return result

    def inspect_debate(self, session_id: str, debate_id: str) -> dict[str, Any]:
        """Return persisted debate state without mutation."""

        try:
            result = self.workspace.inspect_debate(session_id, debate_id)
        except (KeyboardInterrupt, SystemExit) as exc:
            self._record_debug_error(
                DebugOperation.INSPECTION,
                exc,
                session_id=session_id,
                debate_id=debate_id,
            )
            raise
        except Exception as exc:
            self._record_debug_error(
                DebugOperation.INSPECTION,
                exc,
                session_id=session_id,
                debate_id=debate_id,
            )
            raise
        self._trace_debug(
            DebugOperation.INSPECTION,
            DebugPhase.OBSERVED,
            session_id=session_id,
            debate_id=debate_id,
            outcome=DebugOutcome.COMPLETED,
        )
        return result

    def inspect_debate_context(
        self,
        session_id: str,
        debate_id: str,
    ) -> dict[str, Any] | None:
        """Return a strict context sidecar or ``None`` without mutating state."""

        try:
            result = self.workspace.inspect_debate_context(session_id, debate_id)
        except (KeyboardInterrupt, SystemExit) as exc:
            self._record_debug_error(
                DebugOperation.INSPECTION,
                exc,
                session_id=session_id,
                debate_id=debate_id,
            )
            raise
        except Exception as exc:
            self._record_debug_error(
                DebugOperation.INSPECTION,
                exc,
                session_id=session_id,
                debate_id=debate_id,
            )
            raise
        self._trace_debug(
            DebugOperation.INSPECTION,
            DebugPhase.OBSERVED,
            session_id=session_id,
            debate_id=debate_id,
            outcome=DebugOutcome.COMPLETED,
        )
        return result

    def inspect_reference_operation(
        self,
        operation_id: str,
    ) -> ReferenceOperationHistory | None:
        """Return validated content-free read history for one operation."""

        return self.artifacts.inspect_reference_operation(operation_id)

    async def lead_turn(
        self,
        session_id: str,
        conversation_id: str,
        text: str,
        context_event_sink: Callable[[ContextExecutionEvent], None] | None = None,
    ) -> LeadTurnResult:
        """Run one ordinary user turn through the canonical lead front channel."""

        self._validate_lead_identity(session_id, conversation_id)
        text = self._validate_visible_text(text, label="lead input")
        self._validate_context_event_sink(context_event_sink)
        lock = self._conversation_lock(session_id, conversation_id)
        async with lock:
            events, projection, failure = self._read_lead_history(
                session_id,
                conversation_id,
            )
            if failure is not None:
                return self._blocking_lead_result(
                    session_id,
                    conversation_id,
                    CollaborationStepState.RECOVERY_REQUIRED,
                    "Authoritative collaboration history is invalid; repair it "
                    "before continuing.",
                )
            assert events is not None and projection is not None
            normalized = self._normalize_lead_history(
                session_id,
                conversation_id,
                events,
                projection,
            )
            if normalized is not None:
                return normalized

            turn_id = self._new_id("t")
            provider_step_id = self._new_id("s")
            pending_proposal = (
                projection.current_proposal
                if projection.current_state
                in {
                    CollaborationStepState.AWAITING_CONFIRMATION,
                    CollaborationStepState.RECONFIRMATION_REQUIRED,
                }
                else None
            )
            message_step_id = (
                provider_step_id
                if pending_proposal is None
                else pending_proposal.step_id
            )
            user_event = self._draft(
                session_id=session_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                step_id=message_step_id,
                kind=CollaborationEventKind.MESSAGE,
                sender=_USER_ACTOR,
                recipient=_LEAD_ACTOR,
                payload=CollaborationMessagePayload(text),
            )
            if pending_proposal is not None:
                superseded = self._draft(
                    session_id=session_id,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    step_id=pending_proposal.step_id,
                    kind=CollaborationEventKind.STEP_STATE,
                    sender=_RUNTIME_ACTOR,
                    recipient=_USER_ACTOR,
                    payload=CollaborationStepStatePayload(
                        state=CollaborationStepState.SUPERSEDED,
                        proposal_id=pending_proposal.proposal_id,
                        proposal_revision=pending_proposal.proposal_revision,
                        proposal_digest=pending_proposal.proposal_digest,
                    ),
                )
                self._append_drafts(
                    projection.last_sequence,
                    (user_event, superseded),
                )
            else:
                self._append_drafts(projection.last_sequence, (user_event,))

            return await self._run_lead_provider_locked(
                session_id=session_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                step_id=provider_step_id,
                text=text,
                expected_relation=LeadProposalRelation.NEW_STEP,
                previous_proposal=None,
                context_event_sink=context_event_sink,
            )

    async def decide_lead_step(
        self,
        decision: LeadStepDecision,
    ) -> LeadDecisionResult:
        """Apply one exact structured decision to the authoritative request tail."""

        if type(decision) is not LeadStepDecision:
            raise TypeError("decision must be a LeadStepDecision")
        self._validate_lead_identity(decision.session_id, decision.conversation_id)
        lock = self._conversation_lock(
            decision.session_id,
            decision.conversation_id,
        )
        async with lock:
            events = self.conversation_storage_services.event_store.read(
                decision.session_id,
                decision.conversation_id,
            )
            projection = project_collaboration_history(events)
            proposal = projection.current_proposal
            if (
                proposal is None
                or projection.current_state
                not in {
                    CollaborationStepState.AWAITING_CONFIRMATION,
                    CollaborationStepState.RECONFIRMATION_REQUIRED,
                }
                or not events
            ):
                raise ValueError("lead decision has no authoritative pending request")
            request_event = events[-1]
            request_payload = request_event.payload
            if type(request_payload) is not CollaborationStepStatePayload or (
                request_payload.state
                not in {
                    CollaborationStepState.AWAITING_CONFIRMATION,
                    CollaborationStepState.RECONFIRMATION_REQUIRED,
                }
            ):
                raise ValueError("lead decision request is not the exact history tail")
            exact = (
                decision.confirmation_request_id
                == request_payload.confirmation_request_id
                and decision.session_id == request_event.session_id
                and decision.conversation_id == request_event.conversation_id
                and decision.step_id == request_event.step_id == proposal.step_id
                and decision.proposal_id == proposal.proposal_id
                and decision.proposal_revision == proposal.proposal_revision
                and decision.proposal_digest == proposal.proposal_digest
            )
            if not exact:
                raise ValueError("lead decision does not match the authoritative tail")
            if decision.kind is CollaborationConfirmationDecision.CONFIRM:
                self._validate_current_proposal_authority(
                    session_id=decision.session_id,
                    events=events,
                    projection=projection,
                    proposal=proposal,
                )

            turn_id = self._new_id("t")
            confirmation = self._draft(
                session_id=decision.session_id,
                conversation_id=decision.conversation_id,
                turn_id=turn_id,
                step_id=decision.step_id,
                kind=CollaborationEventKind.CONFIRMATION,
                sender=_USER_ACTOR,
                recipient=_RUNTIME_ACTOR,
                payload=CollaborationConfirmationPayload(
                    confirmation_request_id=decision.confirmation_request_id,
                    confirmation_request_event_id=request_event.event_id,
                    proposal_id=proposal.proposal_id,
                    proposal_revision=proposal.proposal_revision,
                    proposal_digest=proposal.proposal_digest,
                    decision=decision.kind,
                ),
            )
            resulting_state = {
                CollaborationConfirmationDecision.CONFIRM: (
                    CollaborationStepState.CONFIRMED
                ),
                CollaborationConfirmationDecision.REJECT: (
                    CollaborationStepState.REJECTED
                ),
                CollaborationConfirmationDecision.REVISE: (
                    CollaborationStepState.SUPERSEDED
                ),
            }[decision.kind]
            state_event = self._draft(
                session_id=decision.session_id,
                conversation_id=decision.conversation_id,
                turn_id=turn_id,
                step_id=decision.step_id,
                kind=CollaborationEventKind.STEP_STATE,
                sender=_RUNTIME_ACTOR,
                recipient=_USER_ACTOR,
                payload=CollaborationStepStatePayload(
                    state=resulting_state,
                    proposal_id=proposal.proposal_id,
                    proposal_revision=proposal.proposal_revision,
                    proposal_digest=proposal.proposal_digest,
                ),
            )
            drafts: tuple[CollaborationEventDraft, ...]
            if decision.kind is CollaborationConfirmationDecision.REVISE:
                assert decision.revision_request is not None
                drafts = (
                    confirmation,
                    self._draft(
                        session_id=decision.session_id,
                        conversation_id=decision.conversation_id,
                        turn_id=turn_id,
                        step_id=decision.step_id,
                        kind=CollaborationEventKind.MESSAGE,
                        sender=_USER_ACTOR,
                        recipient=_LEAD_ACTOR,
                        payload=CollaborationMessagePayload(decision.revision_request),
                    ),
                    state_event,
                )
            else:
                drafts = (confirmation, state_event)
            self._append_drafts(projection.last_sequence, drafts)

            if decision.kind is CollaborationConfirmationDecision.CONFIRM:
                persisted = self.conversation_storage_services.event_store.read(
                    decision.session_id,
                    decision.conversation_id,
                )
                tail = persisted[-1]
                capability = _ConfirmedStepCapability(
                    session_id=decision.session_id,
                    conversation_id=decision.conversation_id,
                    step_id=proposal.step_id,
                    proposal_id=proposal.proposal_id,
                    proposal_revision=proposal.proposal_revision,
                    proposal_digest=proposal.proposal_digest,
                    basis_digest=self._proposal_basis_digest(proposal),
                    tail_event_id=tail.event_id,
                    tail_sequence=tail.sequence,
                )
                self._confirmed_capabilities[
                    (
                        decision.session_id,
                        decision.conversation_id,
                        decision.step_id,
                    )
                ] = capability
                return LeadDecisionResult(
                    decision.session_id,
                    decision.conversation_id,
                    decision.step_id,
                    decision.kind,
                    proposal,
                    resulting_state,
                    CollaborationNextAction.START_OR_RECONFIRM,
                )
            if decision.kind is CollaborationConfirmationDecision.REJECT:
                self._confirmed_capabilities.pop(
                    (
                        decision.session_id,
                        decision.conversation_id,
                        decision.step_id,
                    ),
                    None,
                )
                return LeadDecisionResult(
                    decision.session_id,
                    decision.conversation_id,
                    decision.step_id,
                    decision.kind,
                    proposal,
                    resulting_state,
                    CollaborationNextAction.CONTINUE_WITH_LEAD,
                )

            assert decision.revision_request is not None
            retained_references = " ".join(
                item.locator
                for item in proposal.basis.inputs
                if item.kind
                in {
                    CollaborationInputKind.CORE_DOCUMENT,
                    CollaborationInputKind.MANUSCRIPT,
                    CollaborationInputKind.WRITING_ARTIFACT,
                }
            )
            provider_text = decision.revision_request
            if retained_references:
                provider_text = f"{provider_text}\n{retained_references}"
            revised_turn = await self._run_lead_provider_locked(
                session_id=decision.session_id,
                conversation_id=decision.conversation_id,
                turn_id=turn_id,
                step_id=decision.step_id,
                text=provider_text,
                expected_relation=LeadProposalRelation.REVISE_PENDING,
                previous_proposal=proposal,
                context_event_sink=None,
                user_source_text=decision.revision_request,
            )
            return LeadDecisionResult(
                decision.session_id,
                decision.conversation_id,
                decision.step_id,
                decision.kind,
                proposal,
                resulting_state,
                CollaborationNextAction.CONTINUE_WITH_LEAD,
                revised_turn,
            )

    def _read_lead_history(
        self,
        session_id: str,
        conversation_id: str,
    ) -> tuple[
        tuple[CollaborationEvent, ...] | None,
        CollaborationHistoryProjection | None,
        BaseException | None,
    ]:
        try:
            events = self.conversation_storage_services.event_store.read(
                session_id,
                conversation_id,
            )
            if type(events) is not tuple:
                raise TypeError("collaboration store returned a non-tuple history")
            return events, project_collaboration_history(events), None
        except Exception as failure:
            return None, None, failure

    def _normalize_lead_history(
        self,
        session_id: str,
        conversation_id: str,
        events: tuple[CollaborationEvent, ...],
        projection: CollaborationHistoryProjection,
    ) -> LeadTurnResult | None:
        if self._has_unstarted_lead_request(events):
            return self._blocking_lead_result(
                session_id,
                conversation_id,
                CollaborationStepState.RECOVERY_REQUIRED,
                "A durable user request has no provider-preparation or lead-outcome "
                "marker. Inspect and repair the request before continuing; it will "
                "not be replayed automatically.",
                step_id=projection.current_step_id,
                turn_id=projection.current_turn_id,
            )
        if self._has_dangling_lead_call(events):
            return self._blocking_lead_result(
                session_id,
                conversation_id,
                CollaborationStepState.RECOVERY_REQUIRED,
                "A provider call may have crossed the boundary without a durable "
                "lead outcome; inspect and repair authoritative history before "
                "continuing.",
                step_id=projection.current_step_id,
                turn_id=projection.current_turn_id,
            )
        state = projection.current_state
        proposal = projection.current_proposal
        if state is CollaborationStepState.CONFIRMED:
            if proposal is None or not events:
                return self._blocking_lead_result(
                    session_id,
                    conversation_id,
                    CollaborationStepState.RECOVERY_REQUIRED,
                    "Confirmed history is incomplete and requires repair.",
                    step_id=projection.current_step_id,
                    turn_id=projection.current_turn_id,
                )
            key = (session_id, conversation_id, proposal.step_id)
            capability = self._confirmed_capabilities.get(key)
            tail = events[-1]
            if capability is not None and self._capability_matches(
                capability,
                proposal,
                tail,
            ):
                return LeadTurnResult(
                    session_id,
                    conversation_id,
                    tail.turn_id,
                    proposal.step_id,
                    LeadOutcomeKind.RECOMMENDATION,
                    "The confirmed collaboration step is ready for the runtime's "
                    "separately owned execution phase.",
                    proposal,
                    CollaborationStepState.CONFIRMED,
                    None,
                    CollaborationNextAction.START_OR_RECONFIRM,
                )
            self._confirmed_capabilities.pop(key, None)
            request_id = self._new_id("c")
            turn_id = self._new_id("t")
            reconfirmation = self._draft(
                session_id=session_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                step_id=proposal.step_id,
                kind=CollaborationEventKind.STEP_STATE,
                sender=_RUNTIME_ACTOR,
                recipient=_USER_ACTOR,
                payload=CollaborationStepStatePayload(
                    state=CollaborationStepState.RECONFIRMATION_REQUIRED,
                    proposal_id=proposal.proposal_id,
                    proposal_revision=proposal.proposal_revision,
                    proposal_digest=proposal.proposal_digest,
                    confirmation_request_id=request_id,
                ),
            )
            self._append_drafts(projection.last_sequence, (reconfirmation,))
            return LeadTurnResult(
                session_id,
                conversation_id,
                turn_id,
                proposal.step_id,
                LeadOutcomeKind.RECOMMENDATION,
                "The prior confirmation lost its process-local start authority. "
                "Reconfirm, reject, or revise the unchanged proposal.",
                proposal,
                CollaborationStepState.RECONFIRMATION_REQUIRED,
                request_id,
                CollaborationNextAction.SUBMIT_CONFIRMATION,
            )
        if state is CollaborationStepState.RUNNING:
            if proposal is None or projection.execution_attempt_id is None:
                return self._blocking_lead_result(
                    session_id,
                    conversation_id,
                    CollaborationStepState.RECOVERY_REQUIRED,
                    "Running history is incomplete and requires repair.",
                    step_id=projection.current_step_id,
                    turn_id=projection.current_turn_id,
                )
            marker = (
                session_id,
                conversation_id,
                proposal.step_id,
                projection.execution_attempt_id,
            )
            if projection.execution_result is not None:
                return LeadTurnResult(
                    session_id,
                    conversation_id,
                    projection.current_turn_id or self._new_id("t"),
                    proposal.step_id,
                    LeadOutcomeKind.RECOMMENDATION,
                    "A known execution result is durable and awaits deterministic "
                    "local finalization.",
                    None,
                    CollaborationStepState.RUNNING,
                    None,
                    CollaborationNextAction.FINALIZE_RESULT,
                )
            if marker in self._live_execution_markers:
                return LeadTurnResult(
                    session_id,
                    conversation_id,
                    projection.current_turn_id or self._new_id("t"),
                    proposal.step_id,
                    LeadOutcomeKind.RECOMMENDATION,
                    "The collaboration step is still owned by a live runtime "
                    "execution; wait for its durable result.",
                    None,
                    CollaborationStepState.RUNNING,
                    None,
                    CollaborationNextAction.NONE,
                )
            turn_id = self._new_id("t")
            interruption = self._draft(
                session_id=session_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                step_id=proposal.step_id,
                kind=CollaborationEventKind.INTERRUPTION,
                sender=_RUNTIME_ACTOR,
                recipient=_USER_ACTOR,
                payload=CollaborationInterruptionPayload(
                    projection.execution_attempt_id,
                    "Runtime authority was lost after work may have started; the "
                    "attempt will not be replayed.",
                ),
            )
            unknown = self._draft(
                session_id=session_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                step_id=proposal.step_id,
                kind=CollaborationEventKind.STEP_STATE,
                sender=_RUNTIME_ACTOR,
                recipient=_USER_ACTOR,
                payload=CollaborationStepStatePayload(
                    state=CollaborationStepState.OUTCOME_UNKNOWN,
                    proposal_id=proposal.proposal_id,
                    proposal_revision=proposal.proposal_revision,
                    proposal_digest=proposal.proposal_digest,
                    execution_attempt_id=projection.execution_attempt_id,
                ),
            )
            self._append_drafts(projection.last_sequence, (interruption, unknown))
            return LeadTurnResult(
                session_id,
                conversation_id,
                turn_id,
                proposal.step_id,
                LeadOutcomeKind.RECOMMENDATION,
                "External work may have happened. Inspect and resolve the outcome; "
                "the runtime will not replay it.",
                None,
                CollaborationStepState.OUTCOME_UNKNOWN,
                None,
                CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME,
            )
        if state is CollaborationStepState.OUTCOME_UNKNOWN:
            return self._blocking_lead_result(
                session_id,
                conversation_id,
                state,
                "External work may have happened. Inspect and resolve the outcome "
                "before proposing new work.",
                step_id=projection.current_step_id,
                turn_id=projection.current_turn_id,
            )
        if state is CollaborationStepState.RECOVERY_REQUIRED:
            return self._blocking_lead_result(
                session_id,
                conversation_id,
                state,
                "Authoritative collaboration history requires repair before normal "
                "collaboration can continue.",
                step_id=projection.current_step_id,
                turn_id=projection.current_turn_id,
            )
        return None

    def _blocking_lead_result(
        self,
        session_id: str,
        conversation_id: str,
        state: CollaborationStepState,
        message: str,
        *,
        step_id: str | None = None,
        turn_id: str | None = None,
    ) -> LeadTurnResult:
        next_action = {
            CollaborationStepState.OUTCOME_UNKNOWN: (
                CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME
            ),
            CollaborationStepState.RECOVERY_REQUIRED: (
                CollaborationNextAction.REPAIR_AUTHORITATIVE_HISTORY
            ),
        }.get(state, CollaborationNextAction.REPAIR_AUTHORITATIVE_HISTORY)
        result_state = (
            state
            if state
            in {
                CollaborationStepState.OUTCOME_UNKNOWN,
                CollaborationStepState.RECOVERY_REQUIRED,
            }
            else CollaborationStepState.RECOVERY_REQUIRED
        )
        return LeadTurnResult(
            session_id,
            conversation_id,
            turn_id or self._new_id("t"),
            step_id or self._new_id("s"),
            LeadOutcomeKind.RECOMMENDATION,
            message,
            None,
            result_state,
            None,
            next_action,
        )

    async def _run_lead_provider_locked(
        self,
        *,
        session_id: str,
        conversation_id: str,
        turn_id: str,
        step_id: str,
        text: str,
        expected_relation: LeadProposalRelation,
        previous_proposal: CollaborationProposal | None,
        context_event_sink: Callable[[ContextExecutionEvent], None] | None,
        user_source_text: str | None = None,
    ) -> LeadTurnResult:
        if self.backend is None:
            raise BackendProtocolError("lead collaboration requires an AgentBackend")
        reference_operation = prepare_runtime_reference_operation(
            self.artifacts,
            session_id=session_id,
            text=text,
        )
        inputs = self._lead_inputs(
            turn_id,
            (
                reference_operation.source_text
                if user_source_text is None
                else user_source_text
            ),
            reference_operation,
        )
        input_aliases = [
            {
                "input_id": item.input_id,
                "kind": item.kind.value,
                "locator": item.locator,
                "revision_digest": item.revision_digest,
                "start_line": item.start_line,
                "end_line": item.end_line,
            }
            for item in inputs
        ]
        definition = self.prompts.build_contextual_definition(
            session_id=session_id,
            agent_id=LEAD_ORCHESTRATOR,
        )
        bound_agent = await self._acquire_bound_agent(definition)
        reference_read: object | None = None
        try:
            reference_read = reference_operation.begin_invocation(
                agent_id=LEAD_ORCHESTRATOR,
                phase=ContextInvocationKind.LEAD.value,
                round_number=None,
            )
            provider_factory = self._validated_context_provider_factory(bound_agent)
            delegate = provider_factory(reference_read)
            if inspect.isawaitable(delegate):
                raise BackendProtocolError(
                    "bound context_provider() must be synchronous"
                )
            invocation_id = self._new_id("inv")
            provider = _AccountingContextProvider(
                self,
                delegate,
                session_id=session_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                step_id=step_id,
                invocation_id=invocation_id,
            )
            scope = self._agent_context_service.scope(
                session_id,
                ContextSubjectKind.CONVERSATION,
                conversation_id,
                LEAD_ORCHESTRATOR,
            )
            services = self.conversation_storage_services
            authorities = (
                WritingArtifactSourceAdapter(self.artifacts, scope),
                FocusedConversationSourceAdapter(
                    services.catalog,
                    services.event_store,
                    scope,
                    conversation_id,
                    include_same_session_active=True,
                    include_searchable_history=True,
                ),
                ActiveDebateSourceAdapter(self.workspace, scope, None),
                OwnAgentStateSourceAdapter(self.workspace, scope),
            )
            reference_operation_id, reservations = self._reference_reservations(
                reference_operation
            )
            current = self._context_material(
                source_id="runtime_input:current",
                semantic_channel=ContextSemanticChannel.CURRENT_INPUT,
                ordinal=0,
                locator="request/current",
                value={
                    "answer_contract": {
                        "assignments": {
                            "agent_id": "canonical specialist ID",
                            "input_ids": "nonempty runtime-supplied aliases",
                            "instruction": "complete visible assignment",
                        },
                        "kind": [item.value for item in LeadOutcomeKind],
                        "message": "nonempty user-visible text",
                        "proposal": {
                            "assignments": "required only for proposal",
                            "purpose": "visible purpose",
                            "relation": expected_relation.value,
                        },
                    },
                    "command": reference_operation.provider_text,
                    "identity": {
                        "agent_id": LEAD_ORCHESTRATOR,
                        "conversation_id": conversation_id,
                        "session_id": session_id,
                        "step_id": step_id,
                        "turn_id": turn_id,
                    },
                    "input_aliases": input_aliases,
                    "workflow": "lead_front_channel",
                },
            )
            projection = services.event_store.read_context(
                session_id,
                conversation_id,
            )
            remaining = COLLABORATION_PHYSICAL_CALL_LIMIT - (
                projection.physical_calls_spent
            )
            minimum_calls = 1 + int(bool(reservations))
            if remaining < minimum_calls:
                raise BackendProtocolError(
                    "lead invocation cannot fit its mandatory references and answer "
                    "inside the physical-call lineage"
                )
            physical_call_limit = min(_CONTEXT_INVOCATION_CALL_LIMIT, remaining)
            contextual_request = ContextInvocationRequest(
                session_id=session_id,
                subject_kind=ContextSubjectKind.CONVERSATION,
                subject_id=conversation_id,
                agent_id=LEAD_ORCHESTRATOR,
                operation_id=f"lead_{turn_id}",
                result_correlation_id=f"outcome_{turn_id}",
                materials=(current,),
                source_classifications=(),
                reference_operation_id=reference_operation_id,
                mandatory_references=reservations,
                fresh=True,
                invocation_kind=ContextInvocationKind.LEAD,
                result_contract=_CONTEXT_RESULT_CONTRACT,
                physical_call_limit=physical_call_limit,
            )
            decoded: LeadProviderOutcome | None = None

            def validate(answer: str, _authorization: object) -> None:
                nonlocal decoded
                decoded = self._decode_lead_outcome(answer)

            result = await self._agent_context_service.invoke(
                contextual_request,
                provider,
                writing_artifacts=authorities[0],
                focused_conversation=authorities[1],
                active_debate=authorities[2],
                own_agent_state=authorities[3],
                answer_validator=validate,
                event_sink=context_event_sink,
            )
            outcome = decoded or self._decode_lead_outcome(result.answer)
            reference_operation.finish()
        except BaseException as failure:
            reference_failure = reference_operation.failure(failure)
            if reference_failure is not None:
                raise reference_failure from failure
            raise
        finally:
            await self._release_bound_agent_use(bound_agent)

        if outcome.kind is LeadOutcomeKind.PROPOSAL:
            intent = outcome.proposal
            assert intent is not None
            if intent.relation is not expected_relation:
                raise BackendProtocolError(
                    "lead proposal relation does not match the runtime request"
                )
            refreshed = self.conversation_storage_services.event_store.read_context(
                session_id,
                conversation_id,
            )
            proposal = self._build_lead_proposal(
                session_id=session_id,
                step_id=step_id,
                intent=intent,
                inputs=inputs,
                previous_proposal=previous_proposal,
                projection=refreshed,
                lead_definition=definition,
            )
            request_id = self._new_id("c")
            message_event = self._draft(
                session_id=session_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                step_id=step_id,
                kind=CollaborationEventKind.MESSAGE,
                sender=_LEAD_ACTOR,
                recipient=_USER_ACTOR,
                payload=CollaborationMessagePayload(outcome.message),
            )
            proposal_event = self._draft(
                session_id=session_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                step_id=step_id,
                kind=CollaborationEventKind.MESSAGE,
                sender=_LEAD_ACTOR,
                recipient=_USER_ACTOR,
                payload=proposal,
            )
            state_event = self._draft(
                session_id=session_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                step_id=step_id,
                kind=CollaborationEventKind.STEP_STATE,
                sender=_RUNTIME_ACTOR,
                recipient=_USER_ACTOR,
                payload=CollaborationStepStatePayload(
                    state=CollaborationStepState.AWAITING_CONFIRMATION,
                    proposal_id=proposal.proposal_id,
                    proposal_revision=proposal.proposal_revision,
                    proposal_digest=proposal.proposal_digest,
                    confirmation_request_id=request_id,
                ),
            )
            self._append_drafts(
                refreshed.last_sequence,
                (message_event, proposal_event, state_event),
            )
            return LeadTurnResult(
                session_id,
                conversation_id,
                turn_id,
                step_id,
                outcome.kind,
                outcome.message,
                proposal,
                CollaborationStepState.AWAITING_CONFIRMATION,
                request_id,
                CollaborationNextAction.SUBMIT_CONFIRMATION,
            )

        refreshed = self.conversation_storage_services.event_store.read_context(
            session_id,
            conversation_id,
        )
        self._append_drafts(
            refreshed.last_sequence,
            (
                self._draft(
                    session_id=session_id,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    step_id=step_id,
                    kind=CollaborationEventKind.MESSAGE,
                    sender=_LEAD_ACTOR,
                    recipient=_USER_ACTOR,
                    payload=CollaborationMessagePayload(outcome.message),
                ),
            ),
        )
        return LeadTurnResult(
            session_id,
            conversation_id,
            turn_id,
            step_id,
            outcome.kind,
            outcome.message,
            None,
            (
                CollaborationStepState.SUPERSEDED
                if previous_proposal is not None
                else None
            ),
            None,
            (
                CollaborationNextAction.REPLY_TO_LEAD
                if outcome.kind is LeadOutcomeKind.QUESTION
                else CollaborationNextAction.CONTINUE_WITH_LEAD
            ),
        )

    @staticmethod
    def _decode_lead_outcome(answer: str) -> LeadProviderOutcome:
        """Strictly decode the neutral JSON-object result into closed lead intent."""

        if type(answer) is not str:
            raise BackendProtocolError("lead answer must be strict JSON text")

        def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, value in pairs:
                if key in result:
                    raise _DuplicateJsonKey
                result[key] = value
            return result

        try:
            value = json.loads(
                answer,
                object_pairs_hook=unique,
                parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
            )
            if type(value) is not dict or set(value) != {
                "kind",
                "message",
                "proposal",
            }:
                raise ValueError
            kind = LeadOutcomeKind(value["kind"])
            message = value["message"]
            proposal_value = value["proposal"]
            if kind is not LeadOutcomeKind.PROPOSAL:
                if proposal_value is not None:
                    raise ValueError
                return LeadProviderOutcome(kind, message, None)
            if type(proposal_value) is not dict or set(proposal_value) != {
                "assignments",
                "purpose",
                "relation",
            }:
                raise ValueError
            raw_assignments = proposal_value["assignments"]
            if type(raw_assignments) is not list or not raw_assignments:
                raise ValueError
            assignments: list[LeadAssignmentIntent] = []
            for item in raw_assignments:
                if type(item) is not dict or set(item) != {
                    "agent_id",
                    "input_ids",
                    "instruction",
                }:
                    raise ValueError
                if type(item["input_ids"]) is not list:
                    raise ValueError
                assignments.append(
                    LeadAssignmentIntent(
                        agent_id=item["agent_id"],
                        instruction=item["instruction"],
                        input_ids=tuple(item["input_ids"]),
                    )
                )
            proposal = LeadProposalIntent(
                relation=LeadProposalRelation(proposal_value["relation"]),
                purpose=proposal_value["purpose"],
                assignments=tuple(assignments),
            )
            return LeadProviderOutcome(kind, message, proposal)
        except (
            _DuplicateJsonKey,
            json.JSONDecodeError,
            KeyError,
            TypeError,
            UnicodeError,
            ValueError,
        ) as exc:
            raise BackendProtocolError("lead returned a noncanonical outcome") from exc

    def _build_lead_proposal(
        self,
        *,
        session_id: str,
        step_id: str,
        intent: LeadProposalIntent,
        inputs: tuple[CollaborationInputRevision, ...],
        previous_proposal: CollaborationProposal | None,
        projection: CollaborationHistoryProjection,
        lead_definition: object,
    ) -> CollaborationProposal:
        assignments = intent.assignments
        specialist_ids = tuple(item.agent_id for item in assignments)
        canonical = tuple(
            agent_id for agent_id in SPECIALIST_IDS if agent_id in specialist_ids
        )
        if specialist_ids != canonical:
            raise BackendProtocolError(
                "lead assignments must use canonical specialist order"
            )
        known_input_ids = {item.input_id for item in inputs}
        assigned_input_ids = {
            input_id for item in assignments for input_id in item.input_ids
        }
        if assigned_input_ids != known_input_ids:
            raise BackendProtocolError(
                "lead proposal inputs must close exactly over runtime aliases"
            )
        required_reviewers = {
            reviewer
            for item in inputs
            if item.kind is CollaborationInputKind.CORE_DOCUMENT
            for reviewer in (self._core_reviewer(item.locator),)
            if reviewer is not None
        }
        if not required_reviewers.issubset(specialist_ids):
            raise BackendProtocolError(
                "lead proposal omits a mandatory closed-fact reviewer"
            )
        assignment_inputs = {
            item.agent_id: frozenset(item.input_ids) for item in assignments
        }
        if any(
            reviewer not in assignment_inputs
            or item.input_id not in assignment_inputs[reviewer]
            for item in inputs
            if item.kind is CollaborationInputKind.CORE_DOCUMENT
            for reviewer in (self._core_reviewer(item.locator),)
            if reviewer is not None
        ):
            raise BackendProtocolError(
                "mandatory reviewer assignment omits its closed-fact input"
            )
        if projection.rounds_spent >= COLLABORATION_ROUND_LIMIT:
            raise BackendProtocolError("specialist round three is unreachable")
        allocated_limits, synthesis_calls, required_calls = (
            self._proposal_call_reservations(
                inputs=inputs,
                assignment_input_ids=tuple(item.input_ids for item in assignments),
                physical_calls_spent=projection.physical_calls_spent,
            )
        )
        proposal_revision = (
            1 if previous_proposal is None else previous_proposal.proposal_revision + 1
        )
        proposal_id = self._new_id("p")
        runtime_assignments = tuple(
            CollaborationAssignment(
                assignment_id=self._new_id("a"),
                specialist_id=item.agent_id,
                round=projection.rounds_spent + 1,
                instruction=item.instruction,
                input_ids=item.input_ids,
                max_physical_calls=call_limit,
            )
            for item, call_limit in zip(
                assignments,
                allocated_limits,
                strict=True,
            )
        )
        definitions = {LEAD_ORCHESTRATOR: lead_definition}
        definitions.update(
            {
                agent_id: self.prompts.build_contextual_definition(
                    session_id=session_id,
                    agent_id=agent_id,
                )
                for agent_id in specialist_ids
            }
        )
        participant_ids = {
            LEAD_ORCHESTRATOR,
            *specialist_ids,
        }
        profiles = tuple(
            CollaborationAgentProfile(
                agent_id,
                f"sha256:{getattr(definitions[agent_id], 'fingerprint')}",
            )
            for agent_id in ROSTER
            if agent_id in participant_ids
        )
        route_value = {
            "route": "lead_specialist_consultation",
            "participants": list(specialist_ids),
            "profiles": [
                [item.agent_id, item.profile_fingerprint] for item in profiles
            ],
        }
        basis = CollaborationProposalBasis(
            route="lead_specialist_consultation",
            route_fingerprint=self._canonical_digest(route_value),
            agent_profiles=profiles,
            context_policy_fingerprint=(self._agent_context_service.policy_fingerprint),
            budget_policy_fingerprint=_BUDGET_POLICY_FINGERPRINT,
            inputs=inputs,
        )
        budget = CollaborationBudget(
            round_limit=COLLABORATION_ROUND_LIMIT,
            physical_call_limit=COLLABORATION_PHYSICAL_CALL_LIMIT,
            rounds_spent=projection.rounds_spent,
            rounds_added=1,
            physical_calls_spent=projection.physical_calls_spent,
            max_additional_physical_calls=required_calls,
            synthesis_max_physical_calls=synthesis_calls,
        )
        return CollaborationProposal.create(
            step_id=step_id,
            proposal_id=proposal_id,
            proposal_revision=proposal_revision,
            step_kind=CollaborationStepKind.LEAD_SPECIALIST_CONSULTATION,
            purpose=intent.purpose,
            assignments=runtime_assignments,
            user_impact=_LEAD_PROPOSAL_USER_IMPACT,
            side_effects=(CollaborationSideEffect.PROVIDER_INVOCATION,),
            budget=budget,
            basis=basis,
        )

    def _validate_current_proposal_authority(
        self,
        *,
        session_id: str,
        events: tuple[CollaborationEvent, ...],
        projection: CollaborationHistoryProjection,
        proposal: CollaborationProposal,
    ) -> None:
        """Re-derive every mutable runtime fact before minting start authority."""

        try:
            inputs = self._current_lead_inputs(events, proposal)
            specialist_ids = tuple(
                assignment.specialist_id for assignment in proposal.assignments
            )
            canonical = tuple(
                agent_id for agent_id in SPECIALIST_IDS if agent_id in specialist_ids
            )
            if specialist_ids != canonical:
                raise ValueError("proposal specialist order is no longer canonical")
            self._validate_required_reviewers(inputs, proposal.assignments)
            if projection.rounds_spent >= COLLABORATION_ROUND_LIMIT:
                raise ValueError("proposal no longer fits the round budget")
            call_limits, synthesis_calls, required_calls = (
                self._proposal_call_reservations(
                    inputs=inputs,
                    assignment_input_ids=tuple(
                        assignment.input_ids for assignment in proposal.assignments
                    ),
                    physical_calls_spent=projection.physical_calls_spent,
                )
            )
            runtime_assignments = tuple(
                CollaborationAssignment(
                    assignment_id=assignment.assignment_id,
                    specialist_id=assignment.specialist_id,
                    round=projection.rounds_spent + 1,
                    instruction=assignment.instruction,
                    input_ids=assignment.input_ids,
                    max_physical_calls=call_limit,
                )
                for assignment, call_limit in zip(
                    proposal.assignments,
                    call_limits,
                    strict=True,
                )
            )
            definitions = {
                agent_id: self.prompts.build_contextual_definition(
                    session_id=session_id,
                    agent_id=agent_id,
                )
                for agent_id in (LEAD_ORCHESTRATOR, *specialist_ids)
            }
            participant_ids = {LEAD_ORCHESTRATOR, *specialist_ids}
            profiles = tuple(
                CollaborationAgentProfile(
                    agent_id,
                    f"sha256:{definitions[agent_id].fingerprint}",
                )
                for agent_id in ROSTER
                if agent_id in participant_ids
            )
            route_value = {
                "route": "lead_specialist_consultation",
                "participants": list(specialist_ids),
                "profiles": [
                    [item.agent_id, item.profile_fingerprint] for item in profiles
                ],
            }
            basis = CollaborationProposalBasis(
                route="lead_specialist_consultation",
                route_fingerprint=self._canonical_digest(route_value),
                agent_profiles=profiles,
                context_policy_fingerprint=(
                    self._agent_context_service.policy_fingerprint
                ),
                budget_policy_fingerprint=_BUDGET_POLICY_FINGERPRINT,
                inputs=inputs,
            )
            budget = CollaborationBudget(
                round_limit=COLLABORATION_ROUND_LIMIT,
                physical_call_limit=COLLABORATION_PHYSICAL_CALL_LIMIT,
                rounds_spent=projection.rounds_spent,
                rounds_added=1,
                physical_calls_spent=projection.physical_calls_spent,
                max_additional_physical_calls=required_calls,
                synthesis_max_physical_calls=synthesis_calls,
            )
            current = CollaborationProposal.create(
                step_id=proposal.step_id,
                proposal_id=proposal.proposal_id,
                proposal_revision=proposal.proposal_revision,
                step_kind=CollaborationStepKind.LEAD_SPECIALIST_CONSULTATION,
                purpose=proposal.purpose,
                assignments=runtime_assignments,
                user_impact=_LEAD_PROPOSAL_USER_IMPACT,
                side_effects=(CollaborationSideEffect.PROVIDER_INVOCATION,),
                budget=budget,
                basis=basis,
            )
        except Exception as exc:
            raise BackendProtocolError(
                "lead proposal authority drift requires regeneration and confirmation"
            ) from exc
        if current != proposal:
            raise BackendProtocolError(
                "lead proposal authority drift requires regeneration and confirmation"
            )

    def _current_lead_inputs(
        self,
        events: tuple[CollaborationEvent, ...],
        proposal: CollaborationProposal,
    ) -> tuple[CollaborationInputRevision, ...]:
        proposal_events = tuple(
            event
            for event in events
            if type(event.payload) is CollaborationProposal
            and event.payload.proposal_id == proposal.proposal_id
            and event.payload.proposal_revision == proposal.proposal_revision
            and event.payload.proposal_digest == proposal.proposal_digest
        )
        if len(proposal_events) != 1:
            raise ValueError("proposal event identity is not unique")
        proposal_event = proposal_events[0]
        user_events = tuple(
            event
            for event in events
            if event.sequence < proposal_event.sequence
            and event.turn_id == proposal_event.turn_id
            and type(event.payload) is CollaborationMessagePayload
            and event.sender == _USER_ACTOR
            and event.recipient == _LEAD_ACTOR
        )
        if len(user_events) != 1:
            raise ValueError("proposal user input identity is not unique")
        user_content = user_events[0].payload.content
        current: list[CollaborationInputRevision] = [
            CollaborationInputRevision(
                input_id="user_input",
                kind=CollaborationInputKind.USER_INPUT,
                locator=f"conversation/{proposal_event.turn_id}/user_input",
                state=CollaborationInputState.PRESENT,
                revision_digest=(
                    "sha256:"
                    + hashlib.sha256(
                        user_content.encode("utf-8", errors="strict")
                    ).hexdigest()
                ),
            )
        ]
        references = []
        for expected in proposal.basis.inputs[1:]:
            batch = parse_artifact_references(expected.locator)
            if (
                batch.source_text != expected.locator
                or batch.provider_text != expected.locator
                or len(batch.references) != 1
                or batch.references[0].token != expected.locator
            ):
                raise ValueError("proposal reference locator is not canonical")
            references.append(batch.references[0])
        paths = tuple(dict.fromkeys(reference.path for reference in references))
        snapshots = self.artifacts.read_current(paths)
        if tuple(snapshot.path for snapshot in snapshots) != paths:
            raise ValueError("proposal reference source is no longer readable")
        by_path = {snapshot.path: snapshot for snapshot in snapshots}
        selected_total = 0
        for ordinal, reference in enumerate(references, start=1):
            selected = self._select_current_reference_bytes(
                by_path[reference.path].content,
                None if reference.line_range is None else reference.line_range.start,
                None if reference.line_range is None else reference.line_range.end,
            )
            selected_total += len(selected)
            if selected_total > MAX_REFERENCE_TOTAL_BYTES:
                raise ValueError("proposal references exceed the aggregate read limit")
            kind = (
                CollaborationInputKind.CORE_DOCUMENT
                if reference.path.startswith("core_documents/")
                else (
                    CollaborationInputKind.MANUSCRIPT
                    if reference.path.startswith("manuscript/")
                    else CollaborationInputKind.WRITING_ARTIFACT
                )
            )
            current.append(
                CollaborationInputRevision(
                    input_id=f"reference_{ordinal}",
                    kind=kind,
                    locator=reference.token,
                    state=CollaborationInputState.PRESENT,
                    revision_digest=("sha256:" + hashlib.sha256(selected).hexdigest()),
                    start_line=(
                        None
                        if reference.line_range is None
                        else reference.line_range.start
                    ),
                    end_line=(
                        None
                        if reference.line_range is None
                        else reference.line_range.end
                    ),
                )
            )
        return tuple(current)

    @staticmethod
    def _select_current_reference_bytes(
        content: bytes,
        start_line: int | None,
        end_line: int | None,
    ) -> bytes:
        content.decode("utf-8", errors="strict")
        if b"\x00" in content:
            raise ValueError("proposal reference source contains NUL")
        spans: list[tuple[int, int]] = []
        start = 0
        index = 0
        while index < len(content):
            byte = content[index]
            if byte == 13:
                index += (
                    2 if index + 1 < len(content) and content[index + 1] == 10 else 1
                )
                spans.append((start, index))
                start = index
                continue
            if byte == 10:
                index += 1
                spans.append((start, index))
                start = index
                continue
            index += 1
        if start < len(content):
            spans.append((start, len(content)))
        if start_line is None or end_line is None:
            selected = content
        else:
            if start_line < 1 or end_line < start_line or end_line > len(spans):
                raise ValueError("proposal reference range is no longer readable")
            selected = content[spans[start_line - 1][0] : spans[end_line - 1][1]]
        if len(selected) > MAX_REFERENCE_UTF8_BYTES:
            raise ValueError("proposal reference exceeds the per-reference read limit")
        return selected

    def _validate_required_reviewers(
        self,
        inputs: tuple[CollaborationInputRevision, ...],
        assignments: tuple[CollaborationAssignment, ...],
    ) -> None:
        specialist_ids = tuple(item.specialist_id for item in assignments)
        required_reviewers = {
            reviewer
            for item in inputs
            if item.kind is CollaborationInputKind.CORE_DOCUMENT
            for reviewer in (self._core_reviewer(item.locator),)
            if reviewer is not None
        }
        if not required_reviewers.issubset(specialist_ids):
            raise ValueError("proposal omits a mandatory closed-fact reviewer")
        assignment_inputs = {
            item.specialist_id: frozenset(item.input_ids) for item in assignments
        }
        if any(
            reviewer not in assignment_inputs
            or item.input_id not in assignment_inputs[reviewer]
            for item in inputs
            if item.kind is CollaborationInputKind.CORE_DOCUMENT
            for reviewer in (self._core_reviewer(item.locator),)
            if reviewer is not None
        ):
            raise ValueError("mandatory reviewer omits its closed-fact input")

    @staticmethod
    def _proposal_call_reservations(
        *,
        inputs: tuple[CollaborationInputRevision, ...],
        assignment_input_ids: tuple[tuple[str, ...], ...],
        physical_calls_spent: int,
    ) -> tuple[tuple[int, ...], int, int]:
        reference_input_ids = {
            item.input_id
            for item in inputs
            if item.kind is not CollaborationInputKind.USER_INPUT
        }
        assignment_call_limits = tuple(
            1 + int(any(input_id in reference_input_ids for input_id in input_ids))
            for input_ids in assignment_input_ids
        )
        remaining = COLLABORATION_PHYSICAL_CALL_LIMIT - physical_calls_spent
        minimum_required_calls = sum(assignment_call_limits) + 1
        if minimum_required_calls > remaining:
            raise BackendProtocolError(
                "lead proposal cannot fit the remaining physical-call budget"
            )
        allocated_limits = list(assignment_call_limits)
        unallocated = remaining - minimum_required_calls
        while unallocated and any(
            limit < _CONTEXT_INVOCATION_CALL_LIMIT for limit in allocated_limits
        ):
            for index, limit in enumerate(allocated_limits):
                if not unallocated:
                    break
                if limit < _CONTEXT_INVOCATION_CALL_LIMIT:
                    allocated_limits[index] += 1
                    unallocated -= 1
        synthesis_calls = 1 + min(
            _CONTEXT_INVOCATION_CALL_LIMIT - 1,
            unallocated,
        )
        required_calls = sum(allocated_limits) + synthesis_calls
        return tuple(allocated_limits), synthesis_calls, required_calls

    @staticmethod
    def _core_reviewer(locator: str) -> str | None:
        if not locator.startswith("${") or "}" not in locator:
            return None
        return _CORE_REVIEWERS.get(locator[2 : locator.index("}")])

    @staticmethod
    def _lead_inputs(
        turn_id: str,
        source_text: str,
        operation: RuntimeReferenceOperation,
    ) -> tuple[CollaborationInputRevision, ...]:
        user_digest = (
            "sha256:"
            + hashlib.sha256(source_text.encode("utf-8", errors="strict")).hexdigest()
        )
        inputs: list[CollaborationInputRevision] = [
            CollaborationInputRevision(
                input_id="user_input",
                kind=CollaborationInputKind.USER_INPUT,
                locator=f"conversation/{turn_id}/user_input",
                state=CollaborationInputState.PRESENT,
                revision_digest=user_digest,
            )
        ]
        for proof in operation.reference_proofs:
            path = proof.reference.path
            kind = (
                CollaborationInputKind.CORE_DOCUMENT
                if path.startswith("core_documents/")
                else (
                    CollaborationInputKind.MANUSCRIPT
                    if path.startswith("manuscript/")
                    else CollaborationInputKind.WRITING_ARTIFACT
                )
            )
            line_range = proof.reference.line_range
            inputs.append(
                CollaborationInputRevision(
                    input_id=f"reference_{proof.ordinal}",
                    kind=kind,
                    locator=proof.reference.token,
                    state=CollaborationInputState.PRESENT,
                    revision_digest=f"sha256:{proof.selected_sha256}",
                    start_line=(None if line_range is None else line_range.start),
                    end_line=(None if line_range is None else line_range.end),
                )
            )
        return tuple(inputs)

    @staticmethod
    def _reference_reservations(
        operation: RuntimeReferenceOperation,
    ) -> tuple[str | None, tuple[MandatoryReferenceReservation, ...]]:
        if not operation.reference_proofs:
            return None, ()
        if operation.operation_id is None:
            raise BackendProtocolError("reference operation identity is missing")
        return (
            operation.operation_id,
            tuple(
                MandatoryReferenceReservation(
                    ordinal=proof.ordinal,
                    reference_id=f"reference_{proof.ordinal}",
                    source_id=writing_artifact_source_id(proof.reference.path),
                    revision=f"sha256:{proof.source.source_sha256}",
                    provenance=ContextProvenance(
                        ContextAuthorityKind.REFERENCE_AUTHORIZATION,
                        proof.reference.token,
                    ),
                    content_digest=f"sha256:{proof.selected_sha256}",
                    utf8_bytes=proof.selected_utf8_bytes,
                )
                for proof in operation.reference_proofs
            ),
        )

    @staticmethod
    def _context_material(
        *,
        source_id: str,
        semantic_channel: ContextSemanticChannel,
        ordinal: int,
        locator: str,
        value: object,
    ) -> ContextMaterial:
        body = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8", errors="strict")
        digest = "sha256:" + hashlib.sha256(body).hexdigest()
        return ContextMaterial(
            ContextManifestEntry(
                source_id=source_id,
                source_kind=ContextSourceKind.RUNTIME_INPUT,
                semantic_channel=semantic_channel,
                priority=ContextPriority.P0_REQUIRED,
                ordinal=ordinal,
                revision=digest,
                content_digest=digest,
                provenance=ContextProvenance(ContextAuthorityKind.REQUEST, locator),
                utf8_bytes=len(body),
                searchable=False,
            ),
            body,
        )

    def _append_provider_call_fact(
        self,
        *,
        session_id: str,
        conversation_id: str,
        turn_id: str,
        step_id: str,
        invocation_id: str,
        call_id: str,
        ordinal: int,
        call_kind: CollaborationProviderCallKind,
        state: CollaborationProviderCallState,
    ) -> None:
        projection = self.conversation_storage_services.event_store.read_context(
            session_id,
            conversation_id,
        )
        self._append_drafts(
            projection.last_sequence,
            (
                self._draft(
                    session_id=session_id,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    step_id=step_id,
                    kind=CollaborationEventKind.PROVIDER_CALL_ACCOUNTING,
                    sender=_RUNTIME_ACTOR,
                    recipient=_LEAD_ACTOR,
                    payload=CollaborationProviderCallPayload(
                        invocation_id=invocation_id,
                        call_id=call_id,
                        ordinal=ordinal,
                        role=LEAD_ORCHESTRATOR,
                        invocation_kind=CollaborationInvocationKind.LEAD,
                        call_kind=call_kind,
                        state=state,
                    ),
                ),
            ),
        )

    def _append_drafts(
        self,
        expected_last_sequence: int,
        drafts: tuple[CollaborationEventDraft, ...],
    ) -> object:
        batch = CollaborationEventBatch(
            batch_id=self._new_id("b"),
            expected_last_sequence=expected_last_sequence,
            events=drafts,
        )
        return self.conversation_storage_services.event_store.append_batch(batch)

    @classmethod
    def _draft(
        cls,
        *,
        session_id: str,
        conversation_id: str,
        turn_id: str,
        step_id: str,
        kind: CollaborationEventKind,
        sender: CollaborationActor,
        recipient: CollaborationActor,
        payload: CollaborationPayload,
    ) -> CollaborationEventDraft:
        return CollaborationEventDraft(
            schema_version=COLLABORATION_EVENT_SCHEMA_VERSION,
            event_id=cls._new_id("e"),
            session_id=session_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            step_id=step_id,
            kind=kind,
            sender=sender,
            recipient=recipient,
            payload=payload,
        )

    @staticmethod
    def _new_id(prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex}"

    @staticmethod
    def _canonical_digest(value: object) -> str:
        body = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8", errors="strict")
        return "sha256:" + hashlib.sha256(body).hexdigest()

    @classmethod
    def _proposal_basis_digest(cls, proposal: CollaborationProposal) -> str:
        return cls._canonical_digest(
            {
                "budget": {
                    "calls": proposal.budget.physical_calls_spent,
                    "rounds": proposal.budget.rounds_spent,
                },
                "context": proposal.basis.context_policy_fingerprint,
                "inputs": [
                    [
                        item.input_id,
                        item.kind.value,
                        item.locator,
                        item.state.value,
                        item.revision_digest,
                        item.start_line,
                        item.end_line,
                    ]
                    for item in proposal.basis.inputs
                ],
                "profiles": [
                    [item.agent_id, item.profile_fingerprint]
                    for item in proposal.basis.agent_profiles
                ],
                "route": proposal.basis.route_fingerprint,
            }
        )

    @classmethod
    def _capability_matches(
        cls,
        capability: _ConfirmedStepCapability,
        proposal: CollaborationProposal,
        tail: CollaborationEvent,
    ) -> bool:
        return bool(
            capability.session_id == tail.session_id
            and capability.conversation_id == tail.conversation_id
            and capability.step_id == proposal.step_id == tail.step_id
            and capability.proposal_id == proposal.proposal_id
            and capability.proposal_revision == proposal.proposal_revision
            and capability.proposal_digest == proposal.proposal_digest
            and capability.basis_digest == cls._proposal_basis_digest(proposal)
            and capability.tail_event_id == tail.event_id
            and capability.tail_sequence == tail.sequence
        )

    @staticmethod
    def _has_unstarted_lead_request(
        events: tuple[CollaborationEvent, ...],
    ) -> bool:
        """Detect any durable request lacking a later same-turn invocation marker."""

        marked_turns: set[str] = set()
        for event in reversed(events):
            if (
                type(event.payload) is CollaborationProviderCallPayload
                and event.payload.invocation_kind is CollaborationInvocationKind.LEAD
            ) or (
                event.kind is CollaborationEventKind.MESSAGE
                and event.sender == _LEAD_ACTOR
                and event.recipient == _USER_ACTOR
            ):
                marked_turns.add(event.turn_id)
                continue
            if (
                type(event.payload) is CollaborationMessagePayload
                and event.sender == _USER_ACTOR
                and event.recipient == _LEAD_ACTOR
                and event.turn_id not in marked_turns
            ):
                return True
        return False

    @staticmethod
    def _has_dangling_lead_call(events: tuple[CollaborationEvent, ...]) -> bool:
        completed_invocations: set[tuple[str, str]] = set()
        for event in reversed(events):
            correlation = (event.step_id, event.turn_id)
            if (
                event.kind is CollaborationEventKind.MESSAGE
                and event.sender == _LEAD_ACTOR
                and event.recipient == _USER_ACTOR
            ):
                completed_invocations.add(correlation)
                continue
            if (
                type(event.payload) is CollaborationProviderCallPayload
                and event.payload.invocation_kind is CollaborationInvocationKind.LEAD
                and correlation not in completed_invocations
            ):
                return True
        return False

    def _validate_lead_identity(
        self,
        session_id: str,
        conversation_id: str,
    ) -> None:
        self.workspace.validate_identifier(session_id)
        self.workspace.validate_identifier(conversation_id)
        metadata = self.conversation_storage_services.catalog.get(
            session_id,
            conversation_id,
        )
        if metadata.status is not ConversationStatus.ACTIVE:
            raise ConversationNotFoundError(conversation_id)

    @staticmethod
    def _validate_visible_text(value: object, *, label: str) -> str:
        if type(value) is not str or not value:
            raise ValueError(f"{label} must be nonempty text")
        try:
            encoded = value.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            raise ValueError(f"{label} must be strict UTF-8") from exc
        if len(encoded) > 32_768:
            raise ValueError(f"{label} exceeds the visible text limit")
        return value

    @staticmethod
    def _validate_context_event_sink(
        sink: Callable[[ContextExecutionEvent], None] | None,
    ) -> None:
        if sink is None:
            return
        call = getattr(sink, "__call__", None)
        if (
            not callable(sink)
            or inspect.iscoroutinefunction(sink)
            or inspect.isasyncgenfunction(sink)
            or inspect.iscoroutinefunction(call)
            or inspect.isasyncgenfunction(call)
        ):
            raise TypeError("context_event_sink must be a synchronous callable")

    def _conversation_lock(
        self,
        session_id: str,
        conversation_id: str,
    ) -> asyncio.Lock:
        key = (session_id, conversation_id)
        lock = self._conversation_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._conversation_locks[key] = lock
        return lock

    @staticmethod
    def _validated_context_provider_factory(
        bound_agent: BoundAgent,
    ) -> Callable[[object], object]:
        factory = getattr(bound_agent, "context_provider", None)
        factory_call = getattr(factory, "__call__", None)
        if not callable(factory):
            raise BackendProtocolError(
                f"{bound_agent.definition.agent_id} binding does not provide "
                "context_provider()"
            )
        if (
            inspect.iscoroutinefunction(factory)
            or inspect.isasyncgenfunction(factory)
            or inspect.iscoroutinefunction(factory_call)
            or inspect.isasyncgenfunction(factory_call)
        ):
            raise BackendProtocolError("bound context_provider() must be synchronous")
        return factory

    async def _acquire_bound_agent(
        self,
        definition: object,
    ) -> BoundAgent:
        """Select one binding and retain it for the exact lead operation."""

        return await self._select_bound_agent(definition, retain_use=True)

    async def _select_bound_agent(
        self,
        definition: object,
        *,
        retain_use: bool,
    ) -> BoundAgent:
        agent_id = getattr(definition, "agent_id", None)
        fingerprint = getattr(definition, "fingerprint", None)
        if type(agent_id) is not str or type(fingerprint) is not str:
            raise BackendProtocolError("agent definition identity is invalid")
        key = (agent_id, fingerprint)
        releases: list[BoundAgent] = []
        selected: BoundAgent | None = None
        async with self._binding_lock:
            self._desired_binding_fingerprints[key[0]] = key[1]
            selected = self._bound_agents.get(key)
            if selected is None:
                selected = self._retiring_bound_agents.pop(key, None)
                if selected is not None:
                    releases.extend(self._install_current_binding_locked(key, selected))
            if selected is not None:
                if retain_use:
                    self._retain_bound_agent_use_locked(selected)
                task = None
            else:
                task = self._binding_tasks.get(key)
                if task is None:
                    task = asyncio.create_task(
                        self._bind_definition(definition),
                        name=f"bind:{agent_id}:{fingerprint[:12]}",
                    )
                    self._binding_tasks[key] = task
                self._binding_waiters[key] = self._binding_waiters.get(key, 0) + 1

        self._release_provider_bindings(releases)
        if selected is not None:
            return selected
        assert task is not None
        task_result_handled = False
        superseded_selection = False
        try:
            bound_agent = await asyncio.shield(task)
            releases = []
            async with self._binding_lock:
                if (
                    self._binding_tasks.get(key) is task
                    and self._binding_waiters.get(key, 0) > 0
                ):
                    is_desired = (
                        self._desired_binding_fingerprints.get(key[0]) == key[1]
                    )
                    if retain_use:
                        self._retain_bound_agent_use_locked(bound_agent)
                    if is_desired:
                        releases.extend(
                            self._install_current_binding_locked(key, bound_agent)
                        )
                    elif not retain_use:
                        releases.append(bound_agent)
                        superseded_selection = True
                    else:
                        releases.extend(
                            self._mark_binding_retiring_locked(key, bound_agent)
                        )
                    if not superseded_selection:
                        selected = bound_agent
                    task_result_handled = True
            self._release_provider_bindings(releases)
            if superseded_selection:
                raise _SupersededBindingSelection
            if selected is None:
                raise BackendProtocolError(
                    "completed binding lost its runtime ownership"
                )
            return selected
        finally:
            task_to_join: asyncio.Task[BoundAgent] | None = None
            async with self._binding_lock:
                remaining = self._binding_waiters.get(key, 0) - 1
                if remaining > 0:
                    self._binding_waiters[key] = remaining
                else:
                    self._binding_waiters.pop(key, None)
                    if self._binding_tasks.get(key) is task:
                        self._binding_tasks.pop(key, None)
                        if not task.done():
                            task.cancel()
                        task_to_join = task
            if task_to_join is not None:
                outcome = (await asyncio.gather(task_to_join, return_exceptions=True))[
                    0
                ]
                if not task_result_handled and not isinstance(
                    outcome,
                    BaseException,
                ):
                    async with self._binding_lock:
                        tracked = self._binding_is_tracked_locked(outcome)
                    if not tracked:
                        release_bound_agent(outcome)

    def _install_current_binding_locked(
        self,
        key: tuple[str, str],
        bound_agent: BoundAgent,
    ) -> list[BoundAgent]:
        releases: list[BoundAgent] = []
        for current_key, current in tuple(self._bound_agents.items()):
            if current_key[0] != key[0] or current_key == key:
                continue
            self._bound_agents.pop(current_key)
            if current is not bound_agent:
                releases.extend(
                    self._mark_binding_retiring_locked(current_key, current)
                )
        self._retiring_bound_agents.pop(key, None)
        self._bound_agents[key] = bound_agent
        return releases

    def _mark_binding_retiring_locked(
        self,
        key: tuple[str, str],
        bound_agent: BoundAgent,
    ) -> list[BoundAgent]:
        existing = self._retiring_bound_agents.get(key)
        if existing is bound_agent:
            return []
        use = self._bound_agent_uses.get(id(bound_agent))
        if use is not None and use[0] is bound_agent and use[1] > 0:
            self._retiring_bound_agents[key] = bound_agent
            return []
        return [bound_agent]

    def _retain_bound_agent_use_locked(self, bound_agent: BoundAgent) -> None:
        identity = id(bound_agent)
        current = self._bound_agent_uses.get(identity)
        if current is None:
            self._bound_agent_uses[identity] = (bound_agent, 1)
            return
        if current[0] is not bound_agent:
            raise BackendProtocolError("bound agent identity was reused")
        self._bound_agent_uses[identity] = (bound_agent, current[1] + 1)

    async def _release_bound_agent_use(self, bound_agent: BoundAgent) -> None:
        release: BoundAgent | None = None
        async with self._binding_lock:
            identity = id(bound_agent)
            current = self._bound_agent_uses.get(identity)
            if current is None or current[0] is not bound_agent:
                raise BackendProtocolError("bound agent operation use is absent")
            remaining = current[1] - 1
            if remaining > 0:
                self._bound_agent_uses[identity] = (bound_agent, remaining)
            else:
                self._bound_agent_uses.pop(identity)
                for key, retiring in tuple(self._retiring_bound_agents.items()):
                    if retiring is bound_agent:
                        self._retiring_bound_agents.pop(key)
                        release = bound_agent
                        break
        if release is not None:
            release_bound_agent(release)

    def _binding_is_tracked_locked(self, bound_agent: object) -> bool:
        return (
            any(current is bound_agent for current in self._bound_agents.values())
            or any(
                retiring is bound_agent
                for retiring in self._retiring_bound_agents.values()
            )
            or (
                id(bound_agent) in self._bound_agent_uses
                and self._bound_agent_uses[id(bound_agent)][0] is bound_agent
            )
        )

    async def _bind_definition(self, definition: object) -> BoundAgent:
        if self.backend is None:
            raise BackendProtocolError("lead collaboration requires a backend")
        bind = getattr(self.backend, "bind", None)
        if not callable(bind) or not inspect.iscoroutinefunction(bind):
            raise BackendProtocolError("backend bind() must be async")
        self._verify_definition_files(definition)
        bound_agent = await bind(definition)
        try:
            if not callable(getattr(bound_agent, "context_provider", None)):
                raise BackendProtocolError(
                    "lead binding does not provide context_provider()"
                )
            self._verify_definition_files(definition)
        except BaseException:
            release_bound_agent(bound_agent)
            raise
        return bound_agent

    @staticmethod
    def _verify_definition_files(definition: object) -> None:
        file_references = getattr(definition, "file_references", None)
        if not callable(file_references):
            raise BackendProtocolError("agent definition has no file references")
        for reference in file_references():
            reference.read_text()

    @staticmethod
    def _release_provider_bindings(bindings: list[BoundAgent]) -> None:
        """Release each identity once outside the runtime binding lock."""

        released: set[int] = set()
        for bound_agent in bindings:
            identity = id(bound_agent)
            if identity in released:
                continue
            released.add(identity)
            release_bound_agent(bound_agent)


class RuntimeSession:
    """Session-bound management and lead front-channel/confirmation APIs."""

    def __init__(self, runtime: StoryRuntime, session_id: str) -> None:
        self.runtime = runtime
        self.session_id = session_id

    def apply_directive(self, text: str) -> dict[str, object]:
        """Apply a supported directive to this session."""

        return self.runtime.apply_directive(self.session_id, text)

    def set_overrides(self, overrides: Mapping[str, Any]) -> dict[str, Any]:
        """Replace this session's validated runtime overrides."""

        return self.runtime.set_runtime_overrides(self.session_id, overrides)

    def inspect(self) -> dict[str, Any]:
        """Return persisted state for this session."""

        return self.runtime.inspect_session(self.session_id)

    def inspect_debate(self, debate_id: str) -> dict[str, Any]:
        """Return one persisted debate owned by this session."""

        return self.runtime.inspect_debate(self.session_id, debate_id)

    def inspect_debate_context(self, debate_id: str) -> dict[str, Any] | None:
        """Return this session's strict context sidecar without mutation."""

        return self.runtime.inspect_debate_context(self.session_id, debate_id)

    def inspect_reference_operation(
        self,
        operation_id: str,
    ) -> ReferenceOperationHistory | None:
        """Return read history only when the operation belongs to this session."""

        if not reference_operation_belongs_to_session(
            operation_id,
            self.session_id,
        ):
            raise ValueError("reference operation does not belong to this session")
        return self.runtime.inspect_reference_operation(operation_id)

    async def lead_turn(
        self,
        conversation_id: str,
        text: str,
        context_event_sink: Callable[[ContextExecutionEvent], None] | None = None,
    ) -> LeadTurnResult:
        """Run one ordinary turn through this session's lead front channel."""

        return await self.runtime.lead_turn(
            self.session_id,
            conversation_id,
            text,
            context_event_sink,
        )

    async def decide_lead_step(
        self,
        decision: LeadStepDecision,
    ) -> LeadDecisionResult:
        """Apply an exact pending-step decision owned by this session."""

        if type(decision) is not LeadStepDecision:
            raise TypeError("decision must be a LeadStepDecision")
        if decision.session_id != self.session_id:
            raise ValueError("lead decision does not belong to this session")
        return await self.runtime.decide_lead_step(decision)
