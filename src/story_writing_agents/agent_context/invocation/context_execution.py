"""Context execution."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import secrets
from collections.abc import Callable, Mapping
from contextlib import AsyncExitStack
from dataclasses import replace
from ..accounting import (
    ContextComponentKind,
    ProviderCallAccounting,
    ProviderCallKind,
    ProviderCallState,
    account_provider_payload,
    canonical_provider_json,
    normalized_provider_usage,
)
from ..actions import (
    CONTEXT_ACTION_POLICY,
    ContextActionError,
    ContextActionPolicy,
    ContextAnswerAction,
    ContextAnswerAuthorization,
    ContextReadAction,
    ContextSearchAction,
)
from ..continuation import ContextContinuationTransaction
from ..contracts.action_schema import context_action_schema
from ..contracts.bound_call import BoundCallFactory, BoundCallLineage
from ..contracts.checkpoints import ContextCheckpointView
from ..contracts.context_values import (
    CONTEXT_WIRE_VERSION,
    canonical_bytes,
    digest,
    manifest_fingerprint,
    safe_digest,
    safe_identity,
)
from ..contracts.errors import ContextExecutionError, ContextExecutionErrorCode, execution_error
from ..contracts.events import (
    ContextExecutionEvent,
    ContextExecutionEventKind,
    ContextExecutionEventSink,
    ContextNativeDisposition,
)
from ..contracts.planning_inputs import (
    ContextCheckpointState,
    ContextDeliveryMode,
    ContextMaterial,
    ContextPlanningConditions,
    ContextPlanningPolicy,
    ContextRecordCursor,
    ContextRecordMaterial,
    DEFAULT_CONTEXT_PLANNING_POLICY,
)
from ..contracts.session import ContextInvocationBinding, ContextReadReceipt, action_correlation
from ..group_observation import (
    LeadGroupObservation,
    LeadGroupObservationKind,
    LeadGroupObservationSink,
)
from .delivery import (
    ContextDeliverySnapshot,
    ContextGroupInvocationState,
    ContextSourceClassification,
    verified_group_deliveries,
)
from .group_payload import render_group_context_payload
from .invocation_session import ContextInvocationSession
from .provider import (
    BoundContextProviderCallRequest,
    ContextProvider,
    ContextProviderCallFinalizer,
    ContextProviderCallRequest,
    ContextProviderCallResult,
    ContextProviderFailureFacts,
    ContextProviderTerminalCode,
    ContextReferencePreludeRequest,
    ContextReferencePreludeResult,
    context_provider_decode_terminal_code,
    context_provider_failure_facts,
    record_context_provider_failure,
)
from .requests import ContextInvocationRequest, ContextInvocationResult
from ..ledger.checkpoint_ledger import ContextLedger
from ..ledger.checkpoint_wire import context_checkpoint_proposal_digest
from ..ledger.record_validation import MAX_CONTEXT_LEDGER_SOURCES
from ..ledger.record_values import (
    ContextLedgerAttemptOutcome,
    ContextLedgerAttemptPlan,
    ContextLedgerCommitAuthorization,
    ContextLedgerKey,
    ContextLedgerProviderCall,
    ContextLedgerRecordState,
)
from ..manifest import (
    ContextContinuationIdentity,
    ContextManifest,
    ContextManifestEntry,
    ContextPriority,
    ContextScope,
    ContextSemanticChannel,
    ContextSubjectKind,
)
from ..payload.actions import context_validation_details, decode_context_action
from ..payload.context import (
    continuation_payload,
    initial_payload,
    invocation_phase_contract,
    invocation_source_aliases,
    render_context_payload,
)
from ..payload.history import conversation_read_record_count, history_current_fields
from ..planning import plan_context_projection
from ..progress import (
    ContextActionProgressEvent,
    ContextActionProgressKind,
    ContextActionProgressQuery,
    ContextActionProgressSink,
)
from ..sources.active_debate import ActiveDebateSourceAdapter
from ..sources.capture_values import (
    CONTEXT_SOURCE_POLICY,
    ContextSourceDescriptor,
    ContextSourceError,
    ContextSourceErrorCode,
    ContextSourcePolicy,
)
from ..sources.focused_conversation import FocusedConversationSourceAdapter
from ..sources.own_agent_state import OwnAgentStateSourceAdapter
from ..sources.registry import ContextSourceRegistry
from ..sources.writing_artifact import WritingArtifactSourceAdapter
from ...agents import LEAD_ORCHESTRATOR
from ...conversations.session_context import (
    MAX_PROVIDER_PAYLOAD_UTF8_BYTES,
    SessionContextCapacityError,
    SessionContextHistoryChangedError,
    SessionHistorySnapshot,
)
from ...errors import ProviderAuthenticationRequiredError
from ...lead_schema_groups import LeadGroupPhase
from ...provider_adapter import NativeContextCheck
from ...provider_auth_recovery import authentication_replay, request_authentication
from ...provider_wire import (
    BUILTIN_RESULT_CONTRACT_IDS,
    diagnostic_to_wire,
    project_lead_scope_diagnostic,
)
from ...structured_output_validation import (
    ContractRejection,
    DomainValidationRule,
    bounded_chunks,
    carry_validation,
    diagnose_json_text,
    domain_rule_chunks,
    failure_issues,
)
from ...workspace import Workspace
from typing import cast


_ACTION_POLICY_FINGERPRINT_FIELDS = (
    "max_nonterminal_actions",
    "max_provider_calls",
    "max_search_actions",
    "max_read_actions",
    "max_queries_per_search",
    "max_cumulative_queries",
    "max_handles_per_read",
    "max_cumulative_handles",
    "max_hits_per_query",
    "max_hits_per_search",
    "max_cumulative_hits",
    "max_hits_per_source_query",
    "max_query_characters",
    "max_snippet_characters",
    "max_snippet_utf8_bytes",
    "max_excerpt_utf8_bytes",
    "max_source_read_utf8_bytes",
    "max_read_utf8_bytes",
    "max_observation_utf8_bytes",
)

_SOURCE_POLICY_FINGERPRINT_FIELDS = (
    "max_inventory_sources",
    "max_traversal_entries",
    "max_records_per_source",
    "max_source_utf8_bytes",
    "max_inventory_body_utf8_bytes",
    "max_source_scan_utf8_bytes",
    "max_cumulative_scan_utf8_bytes",
)

def _require_context_capacity(
    schema: Mapping[str, object],
    payload: Mapping[str, object],
    *,
    stage: str,
) -> None:
    """Check actual provider payload JSON without changing delivery or accounting."""
    payload_json, _ = account_provider_payload(schema, payload)
    actual_utf8_bytes = len(payload_json)
    if actual_utf8_bytes > MAX_PROVIDER_PAYLOAD_UTF8_BYTES:
        raise SessionContextCapacityError(
            stage=stage, actual_utf8_bytes=actual_utf8_bytes,
        )

def _execution_policy_fingerprint(
    planning_policy: ContextPlanningPolicy,
    action_policy: ContextActionPolicy,
    source_policy: ContextSourcePolicy,
) -> str:
    """Bind durable execution state to wire and every effective policy value."""

    body = canonical_bytes(
        [
            CONTEXT_WIRE_VERSION,
            planning_policy.fingerprint,
            [
                [name, getattr(action_policy, name)]
                for name in _ACTION_POLICY_FINGERPRINT_FIELDS
            ],
            [
                [name, getattr(source_policy, name)]
                for name in _SOURCE_POLICY_FINGERPRINT_FIELDS
            ],
        ]
    )
    return digest(body)

def _validate_sync_callback(value: object, *, optional: bool = True) -> None:
    if value is None and optional:
        return
    if not callable(value):
        raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
    call = getattr(value, "__call__", None)
    if (
        inspect.iscoroutinefunction(value)
        or inspect.isasyncgenfunction(value)
        or inspect.iscoroutinefunction(call)
        or inspect.isasyncgenfunction(call)
    ):
        raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)

def _require_sync_none(value: object) -> None:
    if inspect.isawaitable(value):
        if inspect.iscoroutine(value):
            value.close()
        raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
    if inspect.isasyncgen(value) or value is not None:
        raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)

def _emit_context_event(
    sink: ContextExecutionEventSink | None,
    event: ContextExecutionEvent | None,
) -> None:
    if sink is None:
        return
    assert event is not None
    _require_sync_none(sink(event))

def _emit_action_progress(
    sink: ContextActionProgressSink | None,
    event: ContextActionProgressEvent,
) -> None:
    if sink is not None:
        _require_sync_none(sink(event))

def _emit_context_event_without_replacing(
    sink: ContextExecutionEventSink | None,
    event: ContextExecutionEvent | None,
) -> None:
    """Best-effort emit after an authoritative outcome already exists."""

    try:
        _emit_context_event(sink, event)
    except BaseException:
        pass

def _accept_provider_call_without_replacing(finalizer: ContextProviderCallFinalizer) -> bool:
    """기존 best-effort 정책을 유지하며 관측 가능한 수락 여부만 반환한다."""
    try:
        callback = cast(Callable[[], object], finalizer.accepted)
        _require_sync_none(callback())
        return True
    except BaseException:
        return False

def _reject_provider_call_without_replacing(
    finalizer: ContextProviderCallFinalizer,
    code: ContextProviderTerminalCode,
) -> None:
    """Best-effort reject one response with a closed semantic code."""

    try:
        callback = cast(
            Callable[[ContextProviderTerminalCode], object],
            finalizer.rejected,
        )
        _require_sync_none(callback(code))
    except BaseException:
        pass

def _cancel_provider_call_without_replacing(
    finalizer: ContextProviderCallFinalizer,
) -> None:
    """Best-effort record cancellation after one response."""

    try:
        callback = cast(Callable[[], object], finalizer.cancelled)
        _require_sync_none(callback())
    except BaseException:
        pass

def _fail_provider_call_without_replacing(
    finalizer: ContextProviderCallFinalizer,
    code: ContextProviderTerminalCode,
) -> None:
    """Best-effort record one post-response runtime failure."""

    try:
        callback = cast(
            Callable[[ContextProviderTerminalCode], object],
            finalizer.post_response_runtime_failure,
        )
        _require_sync_none(callback(code))
    except BaseException:
        pass

def _provider_input_token_total(
    accountings: list[ProviderCallAccounting],
) -> int | None:
    """Sum exact submitted input tokens only when every call supplies them."""

    if not accountings:
        return None
    total = 0
    previous = {}
    for accounting in accountings:
        usage = accounting.usage
        observation = accounting.usage_observation
        if observation is not None:
            key = observation.session_ref, observation.previous_prefix_ref
            usage = normalized_provider_usage(observation, previous.get(key))
            if observation.session_ref is not None and observation.prefix_ref is not None:
                previous[(observation.session_ref, observation.prefix_ref)] = observation
        if usage is None or usage.input_tokens is None:
            return None
        total += usage.input_tokens
    return total

def _workspace_context_id(workspace: Workspace) -> str:
    """Derive a content-free identity from the public canonical root."""

    if type(workspace) is not Workspace:
        raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
    try:
        root = workspace.root.as_posix().encode("utf-8", errors="strict")
    except (AttributeError, UnicodeEncodeError):
        raise execution_error(ContextExecutionErrorCode.INVALID_VALUE) from None
    body = b"story-writing-agents-context-workspace-v1\x00" + root
    return f"sha256:{hashlib.sha256(body).hexdigest()}"

def _descriptor_entry(
    descriptor: object,
    classification: ContextSourceClassification,
) -> ContextManifestEntry:
    """Project one authoritative descriptor into its planned manifest entry."""

    return ContextManifestEntry(
        source_id=descriptor.source_id,
        source_kind=descriptor.source_kind,
        semantic_channel=classification.semantic_channel,
        priority=classification.priority,
        ordinal=classification.ordinal,
        revision=descriptor.revision,
        content_digest=descriptor.content_digest,
        provenance=descriptor.provenance,
        utf8_bytes=descriptor.utf8_bytes,
        searchable=True,
    )

def _attempt_identifier() -> str:
    value: str | None = None
    try:
        value = secrets.token_hex(16)
    except (OSError, RuntimeError, ValueError):
        pass
    if type(value) is not str or len(value) != 32:
        raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
    return f"context_attempt_{value}"

class AgentContextService:
    """Own workspace-scoped contextual composition, execution, and commit."""

    def __init__(
        self,
        workspace: Workspace,
        *,
        planning_policy: ContextPlanningPolicy = DEFAULT_CONTEXT_PLANNING_POLICY,
        action_policy: ContextActionPolicy = CONTEXT_ACTION_POLICY,
        source_policy: ContextSourcePolicy = CONTEXT_SOURCE_POLICY,
    ) -> None:
        if (
            type(workspace) is not Workspace
            or type(planning_policy) is not ContextPlanningPolicy
            or type(action_policy) is not ContextActionPolicy
            or type(source_policy) is not ContextSourcePolicy
        ):
            raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        self._workspace = workspace
        self._workspace_id = _workspace_context_id(workspace)
        self._planning_policy = planning_policy
        self._action_policy = action_policy
        self._source_policy = source_policy
        self._execution_policy_fingerprint = _execution_policy_fingerprint(
            planning_policy,
            action_policy,
            source_policy,
        )
        self._ledger = ContextLedger(workspace)
        self._locks: dict[tuple[str, ...], asyncio.Lock] = {}

    @property
    def workspace_id(self) -> str:
        """Return the authoritative content-free identity of this workspace."""

        return self._workspace_id

    @property
    def policy_fingerprint(self) -> str:
        """Return the exact provider-neutral execution-policy fingerprint."""

        return self._execution_policy_fingerprint

    def scope(
        self,
        session_id: str,
        subject_kind: ContextSubjectKind,
        subject_id: str,
        agent_id: str,
    ) -> ContextScope:
        """Construct one scope without accepting caller-selected workspace identity."""

        return ContextScope(
            self._workspace_id,
            session_id,
            subject_kind,
            subject_id,
            agent_id,
        )

    def session_continuation(
        self, session_id: str, agent_id: str,
    ) -> ContextContinuationTransaction:
        """Serialize a lead operation until its authoritative result is durable."""

        return ContextContinuationTransaction(ContextContinuationIdentity(
            self._workspace_id, session_id, agent_id,
        ))

    def _scope_for(self, request: ContextInvocationRequest) -> ContextScope:
        return self.scope(
            request.session_id,
            request.subject_kind,
            request.subject_id,
            request.agent_id,
        )

    def _lock_for(
        self,
        scope: ContextScope,
        provider_id: str,
        definition_fingerprint: str,
    ) -> asyncio.Lock:
        key = (
            scope.workspace_id,
            scope.session_id,
            scope.subject_kind.value,
            scope.subject_id,
            scope.agent_id,
            provider_id,
            definition_fingerprint,
            self._execution_policy_fingerprint,
        )
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    def _compose_context(
        self,
        request: ContextInvocationRequest,
        scope: ContextScope,
        writing_artifacts: WritingArtifactSourceAdapter,
        focused_conversation: FocusedConversationSourceAdapter,
        active_debate: ActiveDebateSourceAdapter,
        own_agent_state: OwnAgentStateSourceAdapter,
    ) -> tuple[ContextSourceRegistry, ContextManifest, tuple[ContextMaterial, ...]]:
        registry = ContextSourceRegistry(
            writing_artifacts,
            focused_conversation,
            active_debate,
            own_agent_state,
        )
        if registry.scope != scope:
            raise ContextSourceError(ContextSourceErrorCode.SCOPE_MISMATCH)
        descriptors = registry.inventory(self._source_policy)
        manifest = self._manifest_from_descriptors(request, scope, descriptors)
        source_entries = manifest.entries[len(request.materials) :]
        source_materials: list[ContextMaterial] = []
        for entry in source_entries:
            snapshot = registry.capture(manifest, entry, self._source_policy)
            records = tuple(
                ContextRecordMaterial(
                    ContextRecordCursor(
                        record_id=record.record_id,
                        boundary_kind=record.boundary.kind.value,
                        boundary_start=record.boundary.start,
                        boundary_end=record.boundary.end,
                        boundary_first_id=record.boundary.first_id,
                        boundary_last_id=record.boundary.last_id,
                        content_digest=record.content_digest,
                        utf8_bytes=record.utf8_bytes,
                    ),
                    record.content.encode("utf-8", errors="strict"),
                )
                for record in snapshot.records
            )
            source_materials.append(ContextMaterial(entry, snapshot.content, records))
        materials = (*request.materials, *source_materials)
        if len({item.entry.source_id for item in materials}) != len(materials):
            raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        return registry, manifest, tuple(materials)

    def _manifest_from_descriptors(
        self,
        request: ContextInvocationRequest,
        scope: ContextScope,
        descriptors: tuple[ContextSourceDescriptor, ...],
    ) -> ContextManifest:
        """Classify one authoritative content-free source inventory."""

        classifications = {
            item.source_id: item for item in request.source_classifications
        }
        descriptor_ids = {item.source_id for item in descriptors}
        unmatched_classifications = set(classifications).difference(descriptor_ids)
        if any(
            not classifications[source_id].allow_absent
            for source_id in unmatched_classifications
        ):
            raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        source_entries: list[ContextManifestEntry] = []
        for index, descriptor in enumerate(descriptors):
            classification = classifications.get(descriptor.source_id)
            if classification is None:
                classification = ContextSourceClassification(
                    descriptor.source_id,
                    ContextSemanticChannel.OPTIONAL_BACKGROUND,
                    ContextPriority.P3_OPTIONAL,
                    index,
                )
            source_entries.append(_descriptor_entry(descriptor, classification))
        base_entries = tuple(item.entry for item in request.materials)
        entries = (*base_entries, *source_entries)
        if len(entries) > MAX_CONTEXT_LEDGER_SOURCES:
            raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        return ContextManifest(scope, entries)

    @staticmethod
    def _provider_identity(provider: ContextProvider) -> tuple[str, str]:
        provider_id = getattr(provider, "provider_id", None)
        definition_fingerprint = getattr(
            provider,
            "definition_fingerprint",
            None,
        )
        try:
            safe_identity(provider_id)
            safe_digest(definition_fingerprint)
        except ContextActionError:
            raise execution_error(
                ContextExecutionErrorCode.PROVIDER_CONTRACT
            ) from None
        for name in (
            "invocation",
            "inspect_context_thread",
            "prepare_references",
            "invoke_context",
            "complete_references",
            "abort_references",
            "taint_context_thread",
        ):
            if not callable(getattr(provider, name, None)):
                raise execution_error(ContextExecutionErrorCode.PROVIDER_CONTRACT)
        assert isinstance(provider_id, str)
        assert isinstance(definition_fingerprint, str)
        return provider_id, definition_fingerprint

    @staticmethod
    def _append_call(
        ledger: ContextLedger,
        key: ContextLedgerKey,
        generation: int,
        attempt_id: str,
        calls: list[ContextLedgerProviderCall],
        call_id: str,
        accounting: ProviderCallAccounting,
    ) -> int:
        provider_call = ContextLedgerProviderCall(
            len(calls) + 1,
            call_id,
            accounting,
        )
        inspection = ledger.append_provider_call(
            key,
            expected_generation=generation,
            attempt_id=attempt_id,
            provider_call=provider_call,
        )
        calls.append(provider_call)
        return inspection.generation

    @classmethod
    def _append_failure_call_without_replacing(
        cls,
        ledger: ContextLedger,
        key: ContextLedgerKey,
        generation: int,
        attempt_id: str,
        calls: list[ContextLedgerProviderCall],
        call_id: str,
        accounting: ProviderCallAccounting,
    ) -> int:
        """Best-effort retain facts while preserving the primary failure."""

        try:
            return cls._append_call(
                ledger,
                key,
                generation,
                attempt_id,
                calls,
                call_id,
                accounting,
            )
        except BaseException:
            return generation

    @staticmethod
    def _failure_outcome(
        failure: BaseException,
        *,
        stage: str,
        history_advanced: bool,
        terminal_provider_succeeded: bool,
    ) -> ContextLedgerAttemptOutcome:
        facts = context_provider_failure_facts(failure)
        submitted = (
            facts.accounting is not None
            and facts.accounting.state is ProviderCallState.SUBMITTED
        )
        if isinstance(failure, asyncio.CancelledError):
            return ContextLedgerAttemptOutcome.CANCELLED
        if facts.timed_out:
            return ContextLedgerAttemptOutcome.TIMED_OUT
        if (
            isinstance(failure, ProviderAuthenticationRequiredError)
            and failure.replay_safe
        ):
            return ContextLedgerAttemptOutcome.FAILED
        if isinstance(failure, ContextSourceError):
            return ContextLedgerAttemptOutcome.SOURCE_INVALID
        if stage == "decode":
            return ContextLedgerAttemptOutcome.MALFORMED
        if stage == "action" and isinstance(failure, ContextActionError):
            return ContextLedgerAttemptOutcome.MALFORMED
        if stage in {"source", "source_revalidation"}:
            return ContextLedgerAttemptOutcome.SOURCE_INVALID
        if stage in {"answer", "answer_validation"}:
            return ContextLedgerAttemptOutcome.OUTPUT_INVALID
        if stage in {"reference", "reference_complete"}:
            return ContextLedgerAttemptOutcome.REFERENCE_INVALID
        if stage == "exhausted":
            return ContextLedgerAttemptOutcome.INCOMPLETE
        if submitted or history_advanced or terminal_provider_succeeded:
            return ContextLedgerAttemptOutcome.AMBIGUOUS
        if stage == "provider":
            return ContextLedgerAttemptOutcome.FAILED
        return ContextLedgerAttemptOutcome.INCOMPLETE

    @staticmethod
    def _should_taint_context(
        failure: BaseException,
        outcome: ContextLedgerAttemptOutcome,
        *,
        history_advanced: bool,
        terminal_provider_succeeded: bool,
    ) -> bool:
        facts = context_provider_failure_facts(failure)
        submitted = (
            facts.accounting is not None
            and facts.accounting.state is ProviderCallState.SUBMITTED
        )
        return bool(
            outcome is ContextLedgerAttemptOutcome.AMBIGUOUS
            or facts.taint_existing_thread
            or outcome is ContextLedgerAttemptOutcome.MALFORMED
            or (
                outcome
                in {
                    ContextLedgerAttemptOutcome.CANCELLED,
                    ContextLedgerAttemptOutcome.TIMED_OUT,
                }
                and submitted
            )
            or history_advanced
            or terminal_provider_succeeded
        )

    async def invoke(
        self,
        request: ContextInvocationRequest,
        provider: ContextProvider,
        *,
        writing_artifacts: WritingArtifactSourceAdapter,
        focused_conversation: FocusedConversationSourceAdapter,
        active_debate: ActiveDebateSourceAdapter,
        own_agent_state: OwnAgentStateSourceAdapter,
        answer_validator: Callable[[str, ContextAnswerAuthorization], object]
        | None = None,
        answer_diagnostic: Callable[[str], tuple[DomainValidationRule, ...]] | None = None,
        contract_lineage: BoundCallLineage | None = None,
        validation_sink: Callable[[str, tuple[str, ...]], object] | None = None,
        validated_answer_sink: Callable[[str], object] | None = None,
        event_sink: ContextExecutionEventSink | None = None,
        group_observation_sink: LeadGroupObservationSink | None = None,
        progress_sink: ContextActionProgressSink | None = None,
        phase: str | None = None,
        round_number: int | None = None,
        continuation: ContextContinuationTransaction | None = None,
        session_history: SessionHistorySnapshot | None = None,
        publication_validator: Callable[[], bool] | None = None,
        abandoned_operation_ids: tuple[str, ...] = (),
    ) -> ContextInvocationResult:
        """Execute under the exact scope lock and active continuation lease.

        Abandoned operations identify durable read-only failures. Only an exact
        matching unfinished session attempt may retire its native continuation;
        a successful checkpoint for another operation is preserved.
        """

        if type(abandoned_operation_ids) is not tuple:
            raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        for operation_id in abandoned_operation_ids:
            safe_identity(operation_id)
        if abandoned_operation_ids and continuation is None:
            raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if type(request) is not ContextInvocationRequest:
            raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if request.execution_workspace is not None:
            request.execution_workspace.require_current()
        physical_call_limit = (
            self._action_policy.max_provider_calls
            if request.physical_call_limit is None
            else request.physical_call_limit
        )
        if (
            physical_call_limit > self._action_policy.max_provider_calls
            or physical_call_limit <= 0
        ):
            raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        _validate_sync_callback(answer_validator)
        _validate_sync_callback(answer_diagnostic)
        if contract_lineage is not None and type(contract_lineage) is not BoundCallLineage:
            raise TypeError("corrective request requires a bound lineage capability")
        if request.agent_id == LEAD_ORCHESTRATOR and not isinstance(request.result_contract, BoundCallFactory):
            raise TypeError("Lead invocation requires its typed logical-owner capability")
        if isinstance(request.result_contract, BoundCallFactory):
            if request.agent_id != LEAD_ORCHESTRATOR:
                raise TypeError("Lead logical-owner capability requires the actual Lead route")
            if answer_validator is not None or answer_diagnostic is not None:
                raise TypeError("Lead relationship hooks belong to the typed contract owner")
        elif contract_lineage is not None:
            raise TypeError("nonbound contracts cannot accept Lead lineage")
        _validate_sync_callback(validation_sink)
        _validate_sync_callback(validated_answer_sink)
        _validate_sync_callback(event_sink)
        _validate_sync_callback(progress_sink)
        if request.subject_kind is ContextSubjectKind.DEBATE:
            if event_sink is not None and (phase is None or round_number is None):
                raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
            if (phase is None) is not (round_number is None):
                raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
            if phase is not None:
                safe_identity(phase)
                if type(round_number) is not int or round_number < 0:
                    raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        elif phase is not None or round_number is not None:
            raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        provider_id, definition_fingerprint = self._provider_identity(provider)
        reference_operation_id = getattr(provider, "reference_operation_id", None)
        if reference_operation_id != request.reference_operation_id:
            raise execution_error(ContextExecutionErrorCode.PROVIDER_CONTRACT)
        scope = self._scope_for(request)
        if continuation is not None:
            continuation.require_active()
            if (
                not continuation.identity.accepts(scope)
                or type(session_history) is not SessionHistorySnapshot
                or session_history.session_id != scope.session_id
                or not callable(publication_validator)
            ):
                raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        elif (
            session_history is not None or publication_validator is not None
            or request.session_state_json is not None
        ):
            raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        lock = self._lock_for(scope, provider_id, definition_fingerprint)
        policy_fingerprint = self._execution_policy_fingerprint
        if request.execution_workspace is None:
            context_manager = (
                provider.invocation(scope, policy_fingerprint)
                if continuation is None
                else provider.invocation(
                    scope,
                    policy_fingerprint,
                    continuation_identity=continuation.identity,
                )
            )
        else:
            context_manager = (
                provider.invocation(
                    scope,
                    policy_fingerprint,
                    execution_workspace=request.execution_workspace,
                )
                if continuation is None
                else provider.invocation(
                    scope,
                    policy_fingerprint,
                    continuation_identity=continuation.identity,
                    execution_workspace=request.execution_workspace,
                )
            )
        if not hasattr(context_manager, "__aenter__") or not hasattr(
            context_manager,
            "__aexit__",
        ):
            raise execution_error(ContextExecutionErrorCode.PROVIDER_CONTRACT)
        checkpoint_scope = (
            scope if continuation is None else continuation.identity.checkpoint_scope()
        )
        key = ContextLedgerKey(checkpoint_scope, provider_id)
        result: ContextInvocationResult | None = None
        # The terminal response succeeds only after the provider context closes.
        successful_finalizers: list[ContextProviderCallFinalizer] = []
        try:
            async with AsyncExitStack() as provider_stack:
                await provider_stack.enter_async_context(context_manager)
                async with lock:
                    if abandoned_operation_ids:
                        assert continuation is not None
                        inspection = self._ledger.inspect(key)
                        record = inspection.record
                        if (record is not None and record.attempt is not None
                            and record.attempt.plan.operation_id in abandoned_operation_ids
                            and record.attempt.outcome is not ContextLedgerAttemptOutcome.COMMITTED):
                            # Invalidate first: a crash during native retirement must
                            # still force the next owner to bootstrap, never resume.
                            self._ledger.invalidate(key, expected_generation=inspection.generation)
                            if request.execution_workspace is None:
                                provider.taint_context_thread(scope, policy_fingerprint)
                                remaining_thread = provider.inspect_context_thread(
                                    scope, policy_fingerprint
                                )
                            else:
                                provider.taint_context_thread(
                                    scope,
                                    policy_fingerprint,
                                    execution_workspace=request.execution_workspace,
                                )
                                remaining_thread = provider.inspect_context_thread(
                                    scope,
                                    policy_fingerprint,
                                    execution_workspace=request.execution_workspace,
                                )
                            if remaining_thread is not None:
                                raise execution_error(ContextExecutionErrorCode.PROVIDER_CONTRACT)
                    result = await self._invoke_locked(
                        request,
                        provider,
                        provider_id=provider_id,
                        definition_fingerprint=definition_fingerprint,
                        scope=scope,
                        writing_artifacts=writing_artifacts,
                        focused_conversation=focused_conversation,
                        active_debate=active_debate,
                        own_agent_state=own_agent_state,
                        physical_call_limit=physical_call_limit,
                        answer_validator=answer_validator,
                        answer_diagnostic=answer_diagnostic,
                        contract_lineage=contract_lineage,
                        validation_sink=validation_sink,
                        validated_answer_sink=validated_answer_sink,
                        event_sink=event_sink,
                            group_observation_sink=group_observation_sink,
                        progress_sink=progress_sink,
                        phase=phase,
                        round_number=round_number,
                        continuation=continuation,
                        session_history=session_history,
                        publication_validator=publication_validator,
                        successful_finalizers=successful_finalizers,
                    )
                    if (
                        continuation is not None
                        and provider_id in {"codex", "antigravity"}
                        and result.checkpoint_pending
                    ):
                        continuation.retain_async_context_manager(
                            provider_stack.pop_all()
                        )
            if successful_finalizers and request.execution_workspace is not None:
                request.execution_workspace.require_current()
            for finalizer in successful_finalizers:
                _accept_provider_call_without_replacing(finalizer)
        except BaseException as failure:
            for finalizer in successful_finalizers:
                _fail_provider_call_without_replacing(
                    finalizer, ContextProviderTerminalCode.CONTEXT_RUNTIME_FAILED
                )
            if result is not None and not result.checkpoint_pending:
                try:
                    self._ledger.invalidate(
                        key,
                        expected_generation=result.ledger_generation,
                    )
                except BaseException as invalidation_failure:
                    try:
                        failure.add_note(
                            "context checkpoint invalidation failed: "
                            f"{type(invalidation_failure).__name__}"
                        )
                    except BaseException:
                        pass
            raise
        assert result is not None
        return result

    async def _invoke_locked(
        self,
        request: ContextInvocationRequest,
        provider: ContextProvider,
        *,
        provider_id: str,
        definition_fingerprint: str,
        scope: ContextScope,
        writing_artifacts: WritingArtifactSourceAdapter,
        focused_conversation: FocusedConversationSourceAdapter,
        active_debate: ActiveDebateSourceAdapter,
        own_agent_state: OwnAgentStateSourceAdapter,
        physical_call_limit: int,
        answer_validator: Callable[[str, ContextAnswerAuthorization], object] | None,
        answer_diagnostic: Callable[[str], tuple[DomainValidationRule, ...]] | None,
        contract_lineage: BoundCallLineage | None,
        validation_sink: Callable[[str, tuple[str, ...]], object] | None,
        validated_answer_sink: Callable[[str], object] | None,
        event_sink: ContextExecutionEventSink | None,
        group_observation_sink: LeadGroupObservationSink | None,
        progress_sink: ContextActionProgressSink | None,
        phase: str | None,
        round_number: int | None,
        continuation: ContextContinuationTransaction | None,
        session_history: SessionHistorySnapshot | None,
        publication_validator: Callable[[], bool] | None,
        successful_finalizers: list[ContextProviderCallFinalizer],
    ) -> ContextInvocationResult:
        call_contract = None
        call_decoded = None
        group_state = request.group_state
        pending_group_input: bytes | None = None
        pending_group_payload: bytes | None = None
        current_lineage = contract_lineage
        policy_fingerprint = self._execution_policy_fingerprint
        operation_id = request.operation_id

        last_native_check: NativeContextCheck | None = None

        def observe_group(kind, *, delivery=None, action_type=None, terminal_code=None,
                          native_check=None, native_disposition=None):
            if group_observation_sink is None or group_state is None or group_state.workflow is None:
                return
            try:
                workflow = group_state.workflow
                binding = None
                if call_contract is not None:
                    bound_workflow = call_contract.group_workflow
                    binding = {
                        "call_id": call_contract.offers.call_id,
                        "logical_origin_call_id": call_contract.logical_origin_call_id,
                        "offer_digest": call_contract.offers.offer_digest,
                        "eligibility_offer_digest": call_contract.eligibility_offers.offer_digest,
                        "outer_inner_schema_ids": call_contract.outer_inner_schema_ids,
                        "binding_phase": call_contract.phase,
                        "catalog_id": None if bound_workflow is None else bound_workflow.catalog.catalog_id,
                        "group_id": None if bound_workflow is None or bound_workflow.active_selection is None else bound_workflow.active_selection.group_id,
                        "completed_read_facts": [
                            {"evidence_id": item.evidence_id, "source_id": item.source_id,
                             "revision": item.revision, "source_digest": item.source_digest,
                             "boundary_digest": item.boundary_digest}
                            for item in call_contract.offers.completed_read_facts
                        ],
                    }
                if delivery is not None:
                    delivery = dict(delivery)
                    delivery["observation_stage"] = "bound_payload_before_native_submission"
                    delivery["decision_reason"] = (
                        "verified_native_delta" if delivery.get("mode") == "delta" else
                        "explicit_fresh" if request.fresh else
                        "no_prior_delivery" if group_state.transport_delivery is None else
                        "no_live_thread" if current_thread_id is None else
                        "no_accepted_delivery" if group_state.accepted_delivery is None else
                        "preservation_unproven"
                    )
                _require_sync_none(group_observation_sink(LeadGroupObservation(
                    kind, group_state.request_basis,
                    workflow.phase if call_contract is None else call_contract.schema_group_phase,
                    None if call_contract is None else call_contract.offers.call_id,
                    workflow.catalog.historical_snapshot(),
                    tuple(canonical_bytes(item.as_object()) for item in workflow.history),
                    None if binding is None else canonical_bytes(binding),
                    None if delivery is None else canonical_bytes(delivery), action_type,
                    None if call_contract is None or call_contract.lineage is None else call_contract.lineage.kind, terminal_code,
                native_context_snapshot=None if native_check is None else canonical_bytes({
                        "check": native_check.as_object(), "disposition": native_disposition.value,
                        "attempt_id": attempt_id,
                        "call_phase": None if call_contract is None or call_contract.schema_group_phase is None else call_contract.schema_group_phase.value,
                        "native_process_completed": None if native_check.normal_exit_code is None else native_check.normal_exit_code == 0}),
                )))
            except BaseException:
                # Observation cannot change execution, authority, or the primary failure.
                pass

        def inspect_provider_thread() -> str | None:
            if request.execution_workspace is None:
                return provider.inspect_context_thread(scope, policy_fingerprint)
            return provider.inspect_context_thread(
                scope,
                policy_fingerprint,
                execution_workspace=request.execution_workspace,
            )

        def taint_provider_thread() -> None:
            if request.execution_workspace is None:
                provider.taint_context_thread(scope, policy_fingerprint)
                return
            provider.taint_context_thread(
                scope,
                policy_fingerprint,
                execution_workspace=request.execution_workspace,
            )

        def require_execution_workspace_current() -> None:
            if request.execution_workspace is not None:
                request.execution_workspace.require_current()
                return
            callback = getattr(
                provider,
                "require_current_execution_workspace",
                None,
            )
            if callback is not None:
                _require_sync_none(callback(scope, policy_fingerprint))

        checkpoint_scope = (
            scope if continuation is None else continuation.identity.checkpoint_scope()
        )
        key = ContextLedgerKey(checkpoint_scope, provider_id)
        attempt_id: str | None = None
        generation: int | None = None
        calls: list[ContextLedgerProviderCall] = []
        submitted_accountings: list[ProviderCallAccounting] = []
        action_session: ContextInvocationSession | None = None
        reference_started = False
        history_advanced = False
        terminal_provider_succeeded = False
        stage = "preflight"
        provider_failure = False
        retired_thread_id: str | None = None
        checkpoint_committed = False
        call_finalizer: ContextProviderCallFinalizer | None = None
        call_validation_details: tuple[str, ...] = ()
        response_rejection_code: ContextProviderTerminalCode | None = None

        def execution_event(
            kind: ContextExecutionEventKind,
            **facts: object,
        ) -> ContextExecutionEvent | None:
            """Build one event with authoritative call-local identity facts."""

            if event_sink is None:
                return None
            return ContextExecutionEvent(
                kind,
                scope,
                operation_id,
                phase=phase,
                round_number=round_number,
                **facts,
            )

        def progress_event(
            kind: ContextActionProgressKind,
            action_index: int,
            **facts: object,
        ) -> ContextActionProgressEvent:
            return ContextActionProgressEvent(
                kind=kind,
                agent_id=scope.agent_id,
                operation_id=operation_id,
                action_index=action_index,
                **facts,
            )

        def observe_native_context(check, disposition):
            if check is None:
                return
            try:
                observe_group(LeadGroupObservationKind.NATIVE_CONTEXT,
                    native_check=check, native_disposition=disposition)
                _emit_context_event_without_replacing(event_sink, execution_event(
                    ContextExecutionEventKind.NATIVE_CONTEXT, native_context_check=check,
                    native_disposition=disposition, attempt_id=attempt_id,
                    group_phase=None if call_contract is None else call_contract.schema_group_phase))
            except BaseException:
                pass

        def reject_response(
            finalizer: ContextProviderCallFinalizer,
            code: ContextProviderTerminalCode,
        ) -> None:
            """Finalize and retain one deterministic raw-response rejection."""

            nonlocal response_rejection_code
            details = call_validation_details
            if not details and code is ContextProviderTerminalCode.RESPONSE_CORRELATION_MISMATCH:
                details = bounded_chunks(("$.action: response correlation must match the current request",))
            try:
                retain_details = getattr(finalizer, "set_validation_details", None)
                if callable(retain_details):
                    retain_details(details)
            except BaseException:
                pass
            _reject_provider_call_without_replacing(finalizer, code)
            response_rejection_code = code
            observe_group(LeadGroupObservationKind.REJECTED, terminal_code=code.value)

        def completed_group_state() -> ContextGroupInvocationState | None:
            if group_state is None:
                return None
            if call_contract is None or call_decoded is None or group_state.workflow is None:
                raise execution_error(ContextExecutionErrorCode.PROVIDER_CONTRACT)
            observe_native_context(last_native_check, ContextNativeDisposition.RESULT_ACCEPTED)
            observe_group(LeadGroupObservationKind.TERMINAL, terminal_code="accepted")
            return replace(group_state, workflow=group_state.workflow.complete(call_contract.generation_binding(), call_decoded.selected_result))

        try:
            if (group_state is not None and group_state.workflow is not None
                    and group_state.workflow.phase in {LeadGroupPhase.STOP, LeadGroupPhase.DONE}):
                stage = "exhausted"
                raise execution_error(ContextExecutionErrorCode.ACTION_EXHAUSTED)
            stage = "source"
            registry, manifest, materials = self._compose_context(
                request,
                scope,
                writing_artifacts,
                focused_conversation,
                active_debate,
                own_agent_state,
            )
            manifest_digest = manifest_fingerprint(manifest)
            core_statuses = (
                registry.canonical_core_statuses(
                    manifest_digest,
                    self._source_policy,
                )
                if request.include_core_statuses
                else ()
            )
            stage = "preflight"
            active_thread_id = inspect_provider_thread()
            if active_thread_id is not None:
                safe_identity(active_thread_id)
            execution_conditions = ContextPlanningConditions(
                checkpoint_scope,
                provider_id,
                definition_fingerprint,
                policy_fingerprint,
            )
            planning_conditions = replace(
                execution_conditions,
                policy_fingerprint=self._planning_policy.fingerprint,
            )
            inspection = self._ledger.inspect(key)
            incoming_deliveries = ()
            if not request.fresh and contract_lineage is not None:
                incoming_deliveries = verified_group_deliveries(
                    group_state, planning_conditions, inspection.record, active_thread_id)
                if incoming_deliveries:
                    origin = contract_lineage.origin
                    transport = incoming_deliveries[-1]
                    if (origin.request_basis != group_state.request_basis
                            or origin.group_workflow is not group_state.workflow
                            or origin.prepared_compilation is not group_state.prepared_compilation
                            or transport.call_id != origin.offers.call_id
                            or transport.schema_sha256 != hashlib.sha256(
                                canonical_provider_json(origin.schema_value())).hexdigest()):
                        incoming_deliveries = ()
            planning_checkpoint = inspection.checkpoint_view
            if planning_checkpoint.state is ContextCheckpointState.READY:
                checkpoint_conditions = planning_checkpoint.conditions
                assert checkpoint_conditions is not None
                if checkpoint_conditions.policy_fingerprint == policy_fingerprint:
                    planning_checkpoint = replace(
                        planning_checkpoint,
                        conditions=replace(
                            checkpoint_conditions,
                            policy_fingerprint=self._planning_policy.fingerprint,
                        ),
                    )
                else:
                    # Leave the incompatible persisted record untouched while
                    # ensuring neither its checkpoint nor its native thread can
                    # be resumed by the current contextual attempt.
                    planning_checkpoint = ContextCheckpointView(
                        ContextCheckpointState.STALE
                    )
            projection = plan_context_projection(
                replace(manifest, scope=checkpoint_scope),
                materials,
                policy=self._planning_policy,
                conditions=planning_conditions,
                checkpoint=planning_checkpoint,
                active_provider_thread_id=active_thread_id,
                mandatory_references=request.mandatory_references,
                fresh=request.fresh or group_state is not None,
            )
            history_delivery = None
            history_records = None
            if session_history is not None:
                history_records = {
                    f"conversation/{item.metadata.conversation_id}": {
                        record.event_id: {
                            "event_id": record.event_id,
                            "batch_digest": record.batch_digest,
                            "turn_id": record.turn_id,
                            "kind": record.kind.value,
                            "sender": {
                                "kind": record.sender.kind.value,
                                "agent_id": record.sender.agent_id,
                            },
                            "recipient": {
                                "kind": record.recipient.kind.value,
                                "agent_id": record.recipient.agent_id,
                            },
                            "fields": [
                                {"key": value.key.value, "text": value.text,
                                 "assignment_ordinal": value.assignment_ordinal,
                                 "input_ordinal": value.input_ordinal}
                                for value in record.fields
                            ],
                        }
                        for record in item.records
                    }
                    for item in session_history.conversations
                }
                native_operation_id = None
                record = inspection.record
                if (
                    projection.mode is ContextDeliveryMode.DELTA
                    and record is not None
                    and record.attempt is not None
                    and record.attempt.outcome is ContextLedgerAttemptOutcome.COMMITTED
                ):
                    native_operation_id = record.attempt.plan.operation_id
                try:
                    history_delivery = session_history.delivery(
                        planning_checkpoint.session_history_coverage
                        if projection.mode is ContextDeliveryMode.DELTA else (),
                        known_native_operation_id=native_operation_id,
                        native_agent_id=(
                            request.agent_id
                            if projection.mode is ContextDeliveryMode.DELTA else None
                        ),
                        included_fields=(
                            history_current_fields(session_history, request)
                            if projection.mode is ContextDeliveryMode.DELTA else None
                        ),
                    )
                except SessionContextHistoryChangedError:
                    incoming_deliveries = ()
                    inspection = self._ledger.invalidate(
                        key, expected_generation=inspection.generation,
                    )
                    planning_checkpoint = inspection.checkpoint_view
                    projection = plan_context_projection(
                        replace(manifest, scope=checkpoint_scope), materials,
                        policy=self._planning_policy,
                        conditions=planning_conditions,
                        checkpoint=planning_checkpoint,
                        active_provider_thread_id=active_thread_id,
                        mandatory_references=request.mandatory_references,
                        fresh=request.fresh or group_state is not None,
                    )
                    history_delivery = session_history.delivery()
                if request.session_state_json is not None:
                    history_delivery["current_state"] = json.loads(request.session_state_json)
                history_delivery["delivery_mode"] = projection.mode.value
            projection = replace(
                projection,
                checkpoint_proposal=replace(
                    projection.checkpoint_proposal,
                    conditions=execution_conditions,
                    session_history_coverage=(
                        () if session_history is None else session_history.coverage
                    ),
                ),
            )
            binding = ContextInvocationBinding(
                scope,
                request.operation_id,
                request.result_correlation_id,
                manifest,
                request.reference_operation_id,
                request.mandatory_references,
            )
            action_session = ContextInvocationSession(
                binding,
                registry,
                action_policy=self._action_policy,
                source_policy=self._source_policy,
                source_aliases=invocation_source_aliases(
                    manifest,
                    projection,
                ),
                allowed_search_source_kinds=(request.allowed_search_source_kinds),
                physical_call_limit=physical_call_limit,
                core_statuses=core_statuses,
            )

            # All statically available mandatory material is checked before the
            # local reference load. Its generated observation is checked again
            # in the exact substantive payload before physical submission.
            if history_delivery is not None:
                known_payload, known_component_paths, known_reference_paths = initial_payload(
                    binding, projection, planning_checkpoint, action_session,
                    materials, phase_contract=invocation_phase_contract(request),
                    action_index=1,
                    allowed_actions=action_session.allowed_actions(
                        after=None, search_had_hits=False,
                    ),
                    reference_context=(),
                    session_history_records=history_records,
                )
                known_payload["h"] = history_delivery
                known_component_paths[("h",)] = ContextComponentKind.CONVERSATION_CONTEXT
                known_payload, _, _ = render_context_payload(
                    known_payload, known_component_paths, known_reference_paths,
                    schema={}, projection=projection,
                )
                _require_context_capacity({}, known_payload, stage="known_envelope")

            if projection.mode is ContextDeliveryMode.BOOTSTRAP and not incoming_deliveries:
                retired_thread_id = active_thread_id
                if retired_thread_id is not None:
                    stage = "thread_reset"
                    taint_provider_thread()
                    remaining_thread = inspect_provider_thread()
                    if remaining_thread is not None:
                        raise execution_error(
                            ContextExecutionErrorCode.PROVIDER_CONTRACT
                        )
                active_thread_id = None
            _emit_context_event(
                event_sink,
                execution_event(
                    (
                        ContextExecutionEventKind.BOOTSTRAP
                        if projection.mode is ContextDeliveryMode.BOOTSTRAP
                        else ContextExecutionEventKind.DELTA
                    ),
                    selected_source_count=(
                        projection.accounting.selected_logical_source_count
                    ),
                    selected_source_utf8_bytes=(
                        projection.accounting.selected_logical_utf8_bytes
                    ),
                    omitted_searchable_source_count=(
                        projection.accounting.searchable.unprojected_source_count
                    ),
                    omitted_searchable_source_utf8_bytes=(
                        projection.accounting.searchable.unprojected_source_utf8_bytes
                    ),
                    mandatory_reference_count=(
                        projection.accounting.mandatory_reference_count
                    ),
                    mandatory_reference_utf8_bytes=(
                        projection.accounting.mandatory_reference_utf8_bytes
                    ),
                ),
            )
            omitted_count = projection.accounting.searchable.unprojected_source_count
            if omitted_count:
                _emit_context_event(
                    event_sink,
                    execution_event(
                        ContextExecutionEventKind.OMISSION,
                        omitted_searchable_source_count=omitted_count,
                        omitted_searchable_source_utf8_bytes=(
                            projection.accounting.searchable.unprojected_source_utf8_bytes
                        ),
                    ),
                )
            attempt_id = _attempt_identifier()
            proposal_digest = context_checkpoint_proposal_digest(
                projection.checkpoint_proposal
            )
            plan = ContextLedgerAttemptPlan(
                attempt_id=attempt_id,
                operation_id=request.operation_id,
                result_correlation_id=request.result_correlation_id,
                proposal_digest=proposal_digest,
                delivery_mode=projection.mode,
                bootstrap_reason=projection.bootstrap_reason,
                proposed_delta_depth=(projection.checkpoint_proposal.delta_depth),
                mandatory_reference_count=len(request.mandatory_references),
                planned_provider_thread_id=active_thread_id,
                reference_operation_id=request.reference_operation_id,
                projection_accounting=projection.accounting,
            )
            reserved = self._ledger.reserve_attempt(
                key,
                expected_generation=inspection.generation,
                plan=plan,
            )
            generation = reserved.generation

            def replay_sources_unchanged() -> bool:
                """Recheck captured source identity before replaying held payload bytes."""

                try:
                    if request.include_core_statuses and (
                        registry.canonical_core_statuses(manifest_digest, self._source_policy)
                        != core_statuses
                    ):
                        return False
                    return self._manifest_from_descriptors(
                        request, scope, registry.inventory(self._source_policy),
                    ) == manifest
                except Exception:
                    return False

            def group_delivery_ledger_verified() -> bool:
                current = self._ledger.inspect(key)
                record = current.record
                if (current.generation != generation or record is None or record.attempt is None
                        or record.attempt.plan.attempt_id != attempt_id
                        or record.key != key or record.ready_checkpoint is not None
                        or record.state is not ContextLedgerRecordState.INCOMPLETE):
                    return False
                return bool(verified_group_deliveries(
                    group_state, planning_conditions, record, current_thread_id,
                    incoming=incoming_deliveries))

            current_thread_id = active_thread_id
            reference_context: tuple[Mapping[str, object], ...] = ()
            prelude = None
            prelude_request = None
            if request.mandatory_references:
                stage = "reference"
                reference_started = True
                prelude_request = ContextReferencePreludeRequest(
                    scope, policy_fingerprint,
                    f"{attempt_id}_call_{len(calls) + 1}",
                    request.reference_operation_id, current_thread_id,
                    None if continuation is None else continuation.identity,
                    request.execution_workspace,
                )
                prelude = await provider.prepare_references(prelude_request)
                stage = "reference_result"
                if type(prelude) is not ContextReferencePreludeResult:
                    raise execution_error(ContextExecutionErrorCode.PROVIDER_CONTRACT)
                reference_context = prelude.context

            observation: str | None = None
            pending_read: ContextReadReceipt | None = None
            terminal_call_id: str | None = None
            previous_action: str | None = None
            previous_search_had_hits = False
            phase_contract = invocation_phase_contract(request)
            substantive_call_limit = physical_call_limit - len(calls)
            call_contract = None
            call_decoded = None
            for _round in range(substantive_call_limit):
                if len(calls) >= physical_call_limit:
                    break
                action_index = action_session.next_action_index
                if group_state is not None:
                    call_decoded = None
                allowed_actions = action_session.allowed_actions(
                    after=previous_action,
                    search_had_hits=previous_search_had_hits,
                )
                call_id = f"{attempt_id}_call_{len(calls) + 1}"
                correlation = action_correlation(binding, action_index)
                physical_reference_context = reference_context
                async def build_call_request(call_id: str, lineage: BoundCallLineage | None):
                    nonlocal call_contract, group_state, allowed_actions, stage, pending_group_input, pending_group_payload
                    if (group_state is not None and group_state.workflow is not None
                            and group_state.workflow.phase in {LeadGroupPhase.STOP, LeadGroupPhase.DONE}):
                        stage = "exhausted"
                        raise execution_error(ContextExecutionErrorCode.ACTION_EXHAUSTED)
                    if group_state is not None:
                        action_session.set_remaining_provider_calls(
                            physical_call_limit - len(calls), minimum_completion_calls=(
                                2 if group_state.workflow is None
                                else group_state.workflow.minimum_remaining_calls))
                        if lineage is None:
                            allowed_actions = action_session.allowed_actions(
                                after=previous_action, search_had_hits=previous_search_had_hits)
                    if isinstance(request.result_contract, BoundCallFactory):
                        offers = action_session.contract_offers(
                            call_id, allowed_actions=allowed_actions, delivery_read=pending_read,
                            reference_prelude=prelude, reference_request=prelude_request,
                        )
                        group_arguments = {} if group_state is None else {
                            "request_basis": group_state.request_basis,
                            "group_workflow": group_state.workflow,
                            "prepared_compilation": group_state.prepared_compilation,
                        }
                        if group_state is not None:
                            group_arguments["prepared_compilation"] = await request.result_contract.prepare_schema_compilation(
                                offers, lineage, **group_arguments)
                        call_contract = request.result_contract.for_offer(offers, lineage, **group_arguments)
                        if group_state is not None:
                            group_state = ContextGroupInvocationState(
                                call_contract.request_basis, call_contract.group_workflow,
                                call_contract.prepared_compilation,
                                transport_delivery=group_state.transport_delivery, accepted_delivery=group_state.accepted_delivery)
                            remaining = physical_call_limit - len(calls)
                            minimum = group_state.workflow.minimum_remaining_calls
                            action_session.set_remaining_provider_calls(
                                remaining, minimum_completion_calls=minimum)
                            if lineage is not None and any(
                                    value in {"search", "read"}
                                    for value in call_contract.eligibility_offers.allowed_actions):
                                minimum += 1
                            if remaining < minimum:
                                group_state = replace(group_state, workflow=group_state.workflow.stop(
                                    "frozen_contract_budget_exhausted" if lineage is not None
                                    else "provider_call_budget_exhausted"))
                                stage = "exhausted"
                                raise execution_error(ContextExecutionErrorCode.ACTION_EXHAUSTED)
                            if lineage is None:
                                current_actions = action_session.allowed_actions(
                                    after=previous_action, search_had_hits=previous_search_had_hits)
                                current_offers = action_session.contract_offers(
                                    call_id, allowed_actions=current_actions, delivery_read=pending_read,
                                    reference_prelude=prelude, reference_request=prelude_request)
                                if current_offers != offers:
                                    allowed_actions = current_actions
                                    prepared = await request.result_contract.prepare_schema_compilation(
                                        current_offers, request_basis=group_state.request_basis,
                                        group_workflow=group_state.workflow,
                                        prepared_compilation=group_state.prepared_compilation)
                                    call_contract = request.result_contract.for_offer(
                                        current_offers, request_basis=group_state.request_basis,
                                        group_workflow=group_state.workflow,
                                        prepared_compilation=prepared)
                                    group_state = ContextGroupInvocationState(
                                        call_contract.request_basis, call_contract.group_workflow,
                                        call_contract.prepared_compilation,
                                transport_delivery=group_state.transport_delivery, accepted_delivery=group_state.accepted_delivery)
                        schema = call_contract.schema_value()
                    else:
                        call_contract = None
                        schema = context_action_schema(allowed_actions=allowed_actions, result_contract=phase_contract[1],
                            allowed_search_source_kinds=request.allowed_search_source_kinds, action_limits=action_session.action_limits())
                    if observation is None or group_state is not None:
                        payload, component_paths, reference_paths = initial_payload(
                            binding,
                            projection,
                            planning_checkpoint if group_state is None else ContextCheckpointView(ContextCheckpointState.MISSING),
                            action_session,
                            materials,
                            phase_contract=phase_contract,
                            action_index=action_index,
                            allowed_actions=allowed_actions,
                            reference_context=physical_reference_context,
                            session_history_records=history_records,
                        )
                        if history_delivery is not None:
                            payload["h"] = history_delivery
                            component_paths[("h",)] = ContextComponentKind.CONVERSATION_CONTEXT
                        call_kind = (
                            ProviderCallKind.DEBATE
                            if scope.subject_kind is ContextSubjectKind.DEBATE
                            else (
                                ProviderCallKind.COLLABORATION_INITIAL
                                if request.invocation_kind is not None
                                else ProviderCallKind.CONVERSATION_RETRIEVAL_INITIAL
                            )
                        )
                        reference_context = ()
                    else:
                        payload, component_paths = continuation_payload(
                            binding,
                            action_session,
                            phase_contract=phase_contract,
                            action_index=action_index,
                            allowed_actions=allowed_actions,
                            observation=observation,
                        )
                        reference_paths = ()
                        call_kind = (
                            ProviderCallKind.DEBATE
                            if scope.subject_kind is ContextSubjectKind.DEBATE
                            else (
                                ProviderCallKind.COLLABORATION_CONTINUATION
                                if request.invocation_kind is not None
                                else ProviderCallKind.CONVERSATION_RETRIEVAL_CONTINUATION
                            )
                        )
                    if group_state is not None:
                        payload, component_paths, reference_paths, pending_group_input = render_group_context_payload(
                            payload, component_paths, reference_paths, schema=schema, contract=call_contract,
                            state=group_state, remaining=physical_call_limit - len(calls), observation=observation, conditions=planning_conditions, fresh=request.fresh,
                            ledger_verified=group_delivery_ledger_verified(), provider_thread_id=current_thread_id)
                    else:
                        payload, component_paths, reference_paths = render_context_payload(
                            payload, component_paths, reference_paths, schema=schema, projection=projection)
                    delta_delivery = (payload.get("response_contract", {}).get("schema_selection", {})
                        .get("context_delivery", {}).get("mode") == "delta")
                    call_request = (BoundContextProviderCallRequest(contract=call_contract, policy_fingerprint=policy_fingerprint, call_kind=call_kind, payload=payload, component_paths=component_paths, mandatory_reference_paths=reference_paths, expected_thread_id=current_thread_id, continuation_identity=None if continuation is None else continuation.identity, execution_workspace=request.execution_workspace, context_witness=((None if group_state is None or group_state.transport_delivery is None or payload["response_contract"]["schema_selection"]["context_delivery"]["mode"] != "delta" else group_state.transport_delivery.witness) if delta_delivery else None), context_anchor=((group_state.accepted_delivery.witness if group_state is not None and group_state.accepted_delivery is not None and payload["response_contract"]["schema_selection"]["context_delivery"]["mode"] == "delta" else None) if delta_delivery else None))
                                    if call_contract is not None else ContextProviderCallRequest(scope=scope, policy_fingerprint=policy_fingerprint, call_id=call_id, call_kind=call_kind, schema=schema, payload=payload, component_paths=component_paths, mandatory_reference_paths=reference_paths, expected_thread_id=current_thread_id, continuation_identity=None if continuation is None else continuation.identity, execution_workspace=request.execution_workspace))
                    if group_state is not None:
                        pending_group_payload = canonical_provider_json(call_request.payload)
                    if call_contract is not None or history_delivery is not None or continuation is not None:
                        _require_context_capacity(
                            schema=call_request.schema,
                            payload=call_request.payload,
                            stage="provider_payload",
                        )
                    observe_group(LeadGroupObservationKind.BOUND, delivery=(call_request.payload.get("response_contract", {}).get("schema_selection", {}).get("context_delivery")))
                    return call_request
                call_request = await build_call_request(call_id, current_lineage)
                if group_state is None:
                    reference_context = set()
                schema, call_kind = call_request.schema, call_request.call_kind
                call_decoded = None
                stage = "provider"
                provider_failure = True
                for auth_attempt in range(2):
                    call_id = call_request.call_id
                    try:
                        if auth_attempt:
                            with authentication_replay(auth_failure, replay_sources_unchanged):
                                if call_contract is not None:
                                    call_contract.require_current_offer(
                                        action_session.contract_offers(call_request.call_id, allowed_actions=allowed_actions,
                                            delivery_read=pending_read,
                                            reference_prelude=prelude,
                                            reference_request=prelude_request))
                                result = await provider.invoke_context(call_request)
                        else:
                            if call_contract is not None:
                                call_contract.require_current_offer(
                                    action_session.contract_offers(call_request.call_id, allowed_actions=allowed_actions,
                                        delivery_read=pending_read,
                                        reference_prelude=prelude,
                                        reference_request=prelude_request))
                            result = await provider.invoke_context(call_request)
                        break
                    except BaseException as failure:
                        if (
                            isinstance(failure, ProviderAuthenticationRequiredError)
                            and failure.replay_aborted
                        ):
                            raise
                        facts = context_provider_failure_facts(failure)
                        recorded_before = len(calls)
                        if facts.accounting is not None:
                            if facts.accounting.state is ProviderCallState.SUBMITTED:
                                submitted_accountings.append(facts.accounting)
                            generation = self._append_failure_call_without_replacing(
                                self._ledger, key, generation, attempt_id,
                                calls, call_id, facts.accounting,
                            )
                        if (
                            not isinstance(failure, ProviderAuthenticationRequiredError)
                            or auth_attempt
                        ):
                            raise
                        if group_state is not None and group_state.workflow is not None:
                            replay_minimum = group_state.workflow.minimum_remaining_calls
                            if call_contract is not None and any(
                                    value in {"search", "read"}
                                    for value in call_contract.eligibility_offers.allowed_actions):
                                replay_minimum += 1
                            if physical_call_limit - len(calls) < replay_minimum:
                                group_state = replace(group_state, workflow=group_state.workflow.stop(
                                    "authentication_replay_budget_exhausted"))
                                raise
                        try:
                            authenticated = await request_authentication(failure)
                        except BaseException:
                            failure.clear_replay_validator()
                            raise
                        if not (
                            authenticated and failure.replay_safe
                            and not facts.timed_out and not facts.submission_uncertain
                            and not facts.taint_existing_thread
                            and len(calls) == recorded_before + 1
                            and len(calls) < physical_call_limit
                        ):
                            raise
                        if not failure.validate_replay() or not replay_sources_unchanged():
                            raise
                        auth_failure = failure
                        call_request = await build_call_request(f'{attempt_id}_call_{len(calls) + 1}',
                            None if call_contract is None else call_contract.replay_lineage())
                        schema, call_kind = call_request.schema, call_request.call_kind
                terminal_provider_succeeded = True
                if type(result) is not ContextProviderCallResult:
                    failure = execution_error(
                        ContextExecutionErrorCode.PROVIDER_CONTRACT
                    )
                    accounting = getattr(result, "accounting", None)
                    if (
                        type(accounting) is ProviderCallAccounting
                        and accounting.state is ProviderCallState.SUBMITTED
                    ):
                        submitted_accountings.append(accounting)
                        record_context_provider_failure(
                            failure,
                            ContextProviderFailureFacts(
                                accounting,
                                taint_existing_thread=True,
                            ),
                        )
                        generation = self._append_failure_call_without_replacing(
                            self._ledger,
                            key,
                            generation,
                            attempt_id,
                            calls,
                            call_id,
                            accounting,
                        )
                    raise failure
                call_finalizer = result.finalizer
                call_validation_details = ()
                submitted_accountings.append(result.accounting)
                generation = self._append_call(
                    self._ledger,
                    key,
                    generation,
                    attempt_id,
                    calls,
                    call_id,
                    result.accounting,
                )
                require_execution_workspace_current()
                if result.accounting.payload.call_kind is not call_kind:
                    finalizer = call_finalizer
                    call_finalizer = None
                    if finalizer is not None:
                        reject_response(
                            finalizer,
                            ContextProviderTerminalCode.RESPONSE_CORRELATION_MISMATCH,
                        )
                    failure = execution_error(
                        ContextExecutionErrorCode.PROVIDER_CONTRACT
                    )
                    record_context_provider_failure(
                        failure,
                        ContextProviderFailureFacts(
                            result.accounting,
                            taint_existing_thread=True,
                        ),
                    )
                    raise failure
                if (
                    current_thread_id is not None
                    and result.thread_id != current_thread_id
                ):
                    finalizer = call_finalizer
                    call_finalizer = None
                    if finalizer is not None:
                        reject_response(
                            finalizer,
                            ContextProviderTerminalCode.RESPONSE_CORRELATION_MISMATCH,
                        )
                    failure = execution_error(
                        ContextExecutionErrorCode.CONTINUATION_MISMATCH
                    )
                    record_context_provider_failure(
                        failure,
                        ContextProviderFailureFacts(
                            result.accounting,
                            taint_existing_thread=True,
                        ),
                    )
                    raise failure
                if (
                    retired_thread_id is not None
                    and result.thread_id == retired_thread_id
                ):
                    finalizer = call_finalizer
                    call_finalizer = None
                    if finalizer is not None:
                        reject_response(
                            finalizer,
                            ContextProviderTerminalCode.RESPONSE_CORRELATION_MISMATCH,
                        )
                    failure = execution_error(
                        ContextExecutionErrorCode.CONTINUATION_MISMATCH
                    )
                    record_context_provider_failure(
                        failure,
                        ContextProviderFailureFacts(
                            result.accounting,
                            taint_existing_thread=True,
                        ),
                    )
                    raise failure
                provider_failure = False
                current_thread_id = result.thread_id
                last_native_check = result.native_context_check
                observe_native_context(last_native_check, ContextNativeDisposition.OBSERVED)
                if group_state is not None and pending_group_input is not None:
                    group_state = replace(group_state, transport_delivery=ContextDeliverySnapshot(
                        group_state.request_basis, planning_conditions, attempt_id, generation, call_id,
                        pending_group_input, pending_group_payload, hashlib.sha256(pending_group_payload).hexdigest(),
                        hashlib.sha256(canonical_provider_json(call_request.schema)).hexdigest(),
                        self._ledger.inspect(key).record, result.context_witness))
                if pending_read is not None:
                    stage = "observation_delivery"
                    action_session.complete_read(pending_read)
                    pending_read = None
                stage = "decode"
                schema_issues: tuple[str, ...] = ()
                try:
                    if call_contract is not None:
                        try:
                            call_decoded = call_contract.decode_response(result.response_text)
                            action = None if call_decoded.group_control is not None else decode_context_action(result.response_text, correlation=correlation, allowed_actions=allowed_actions, result_contract=phase_contract[1], allowed_search_source_kinds=request.allowed_search_source_kinds, resolve_handle=action_session.resolve_wire_handle, completed_handles=action_session.completed_handles, decoded_action=call_decoded)
                        except ContractRejection as bound_failure:
                            rejected = carry_validation(execution_error(ContextExecutionErrorCode.MALFORMED_ACTION), bound_failure)
                            rejected.bound_contract_failure = bound_failure
                            raise rejected from None
                        if (type(action) not in {ContextSearchAction, ContextReadAction, ContextAnswerAction}
                                and not (action is None and call_decoded.group_control is not None)):
                            raise execution_error(ContextExecutionErrorCode.PROVIDER_CONTRACT)
                    else:
                        schema_issues = diagnose_json_text(result.response_text, schema)
                        if schema_issues:
                            raise execution_error(ContextExecutionErrorCode.MALFORMED_ACTION)
                        action = decode_context_action(
                            result.response_text,
                            correlation=correlation,
                            allowed_actions=allowed_actions,
                            result_contract=phase_contract[1],
                            allowed_search_source_kinds=(
                                request.allowed_search_source_kinds
                            ),
                            resolve_handle=action_session.resolve_wire_handle,
                            completed_handles=action_session.completed_handles,
                            read_limit=min(*action_session.action_limits()[5:7], CONTEXT_ACTION_POLICY.max_handles_per_read),
                        )
                except ContextExecutionError as decode_failure:
                    call_validation_details = context_validation_details(
                        result.response_text, schema_issues, decode_failure,
                    )
                    if call_contract is not None:
                        bound_failure = getattr(decode_failure, "bound_contract_failure", decode_failure)
                        bound_details = failure_issues(
                            call_contract.bind_failure(bound_failure, call_decoded))
                        if bound_details:
                            call_validation_details = bound_details
                    if validation_sink is not None:
                        _require_sync_none(validation_sink(call_id, call_validation_details))
                    if (
                        decode_failure.code
                        is ContextExecutionErrorCode.MALFORMED_ACTION
                        and action_index == substantive_call_limit
                    ):
                        try:
                            if call_contract is not None:
                                call_decoded = call_contract.decode_response(result.response_text)
                                exhausted_action = decode_context_action(result.response_text, correlation=correlation, allowed_actions=('search', 'read', 'answer'), result_contract=phase_contract[1], allowed_search_source_kinds=request.allowed_search_source_kinds, resolve_handle=action_session.resolve_wire_handle, completed_handles=action_session.completed_handles, decoded_action=call_decoded)
                            else:
                                exhausted_action = decode_context_action(
                                result.response_text,
                                correlation=correlation,
                                allowed_actions=("search", "read", "answer"),
                                result_contract=phase_contract[1],
                                allowed_search_source_kinds=(
                                    request.allowed_search_source_kinds
                                ),
                                resolve_handle=(action_session.resolve_wire_handle),
                                completed_handles=action_session.completed_handles,
                        read_limit=min(*action_session.action_limits()[5:7], CONTEXT_ACTION_POLICY.max_handles_per_read),
                            )
                        except (ContextExecutionError, ContractRejection):
                            finalizer = call_finalizer
                            call_finalizer = None
                            if finalizer is not None:
                                reject_response(
                                    finalizer,
                                    context_provider_decode_terminal_code(
                                        decode_failure
                                    ),
                                )
                            raise decode_failure
                        if type(exhausted_action) in {
                            ContextSearchAction,
                            ContextReadAction,
                        }:
                            stage = "exhausted"
                            finalizer = call_finalizer
                            call_finalizer = None
                            if finalizer is not None:
                                reject_response(
                                    finalizer,
                                    ContextProviderTerminalCode.ACTION_EXHAUSTED,
                                )
                            raise execution_error(
                                ContextExecutionErrorCode.ACTION_EXHAUSTED
                            )
                    finalizer = call_finalizer
                    call_finalizer = None
                    if finalizer is not None:
                        reject_response(
                            finalizer,
                            context_provider_decode_terminal_code(decode_failure),
                        )
                    raise
                terminal_provider_succeeded = False
                if call_decoded is not None and call_decoded.group_control is not None:
                    if group_state is None or call_contract.group_workflow is not group_state.workflow:
                        raise execution_error(ContextExecutionErrorCode.PROVIDER_CONTRACT)
                    record = call_contract.record_group_control(call_decoded)
                    group_state = replace(group_state, workflow=group_state.workflow.accept(
                        record, remaining_calls=physical_call_limit - len(calls)))
                    observe_group(LeadGroupObservationKind.CONTROL_ACCEPTED)
                    action_session.advance_group_control(correlation)
                    history_advanced = True
                    require_execution_workspace_current()
                    observe_native_context(last_native_check, ContextNativeDisposition.CONTROL_ACCEPTED)
                    finalizer = call_finalizer
                    call_finalizer = None
                    if finalizer is not None:
                        finalized = _accept_provider_call_without_replacing(finalizer)
                        if finalized and group_state is not None:
                            group_state = replace(group_state, accepted_delivery=group_state.transport_delivery)
                    current_lineage = None
                    if group_state.workflow.phase is LeadGroupPhase.STOP:
                        stage = "exhausted"
                        raise execution_error(ContextExecutionErrorCode.ACTION_EXHAUSTED)
                    continue
                if type(action) is ContextSearchAction:
                    observe_group(LeadGroupObservationKind.CONTEXT_ACTION, action_type="search_context")
                    history_advanced = True
                    stage = "action_observer"
                    _emit_context_event(
                        event_sink,
                        execution_event(
                            ContextExecutionEventKind.SEARCH_STARTED,
                            action_index=action_index,
                        ),
                    )
                    _emit_action_progress(
                        progress_sink,
                        progress_event(
                            ContextActionProgressKind.SEARCH_STARTED,
                            action_index,
                            queries=tuple(
                                ContextActionProgressQuery(
                                    query.text,
                                    query.source_kinds,
                                    query.artifact_path_prefix,
                                )
                                for query in action.queries
                            ),
                        ),
                    )
                    stage = "action"
                    search_observation = action_session.search(action)
                    observation = action_session.serialize_observation(
                        search_observation
                    )
                    previous_action = "search"
                    if group_state is not None:
                        current_lineage = None
                    previous_search_had_hits = bool(search_observation.hits)
                    stage = "action_observer"
                    _emit_context_event(
                        event_sink,
                        execution_event(
                            ContextExecutionEventKind.SEARCH_COMPLETED,
                            action_index=action_index,
                            search_hit_count=len(search_observation.hits),
                            observation_utf8_bytes=len(
                                observation.encode("utf-8", errors="strict")
                            ),
                            truncated=search_observation.truncated,
                        ),
                    )
                    _emit_action_progress(
                        progress_sink,
                        progress_event(
                            ContextActionProgressKind.SEARCH_COMPLETED,
                            action_index,
                            sources=action_session.progress_sources(
                                tuple(hit.handle for hit in search_observation.hits),
                                utf8_bytes={
                                    hit.handle.value: hit.boundary_utf8_bytes
                                    for hit in search_observation.hits
                                },
                            ),
                            truncated=search_observation.truncated,
                        ),
                    )
                    require_execution_workspace_current()
                    finalizer = call_finalizer
                    call_finalizer = None
                    if finalizer is not None:
                        finalized = _accept_provider_call_without_replacing(finalizer)
                        if finalized and group_state is not None:
                            group_state = replace(group_state, accepted_delivery=group_state.transport_delivery)
                    continue
                if type(action) is ContextReadAction:
                    observe_group(LeadGroupObservationKind.CONTEXT_ACTION, action_type="read_context")
                    history_advanced = True
                    stage = "action_observer"
                    read_progress_sources = action_session.progress_sources(
                        action.handles
                    )
                    _emit_context_event(
                        event_sink,
                        execution_event(
                            ContextExecutionEventKind.READ_STARTED,
                            action_index=action_index,
                        ),
                    )
                    _emit_action_progress(
                        progress_sink,
                        progress_event(
                            ContextActionProgressKind.READ_STARTED,
                            action_index,
                            sources=read_progress_sources,
                        ),
                    )
                    stage = "action"
                    read_observation, pending_read = action_session.read(action)
                    observation = action_session.serialize_observation(read_observation)
                    previous_action = "read"
                    if group_state is not None:
                        current_lineage = None
                    previous_search_had_hits = False
                    record_count = conversation_read_record_count(read_observation)
                    stage = "action_observer"
                    _emit_context_event(
                        event_sink,
                        execution_event(
                            ContextExecutionEventKind.READ_COMPLETED,
                            action_index=action_index,
                            retrieved_source_count=len(read_observation.evidence),
                            retrieved_source_utf8_bytes=sum(
                                evidence.receipt.boundary_utf8_bytes
                                for evidence in read_observation.evidence
                            ),
                            history_record_count=record_count,
                            observation_utf8_bytes=len(
                                observation.encode("utf-8", errors="strict")
                            ),
                        ),
                    )
                    _emit_action_progress(
                        progress_sink,
                        progress_event(
                            ContextActionProgressKind.READ_COMPLETED,
                            action_index,
                            sources=tuple(
                                replace(
                                    source,
                                    utf8_bytes=evidence.receipt.boundary_utf8_bytes,
                                )
                                for source, evidence in zip(
                                    read_progress_sources,
                                    read_observation.evidence,
                                    strict=True,
                                )
                            ),
                        ),
                    )
                    require_execution_workspace_current()
                    finalizer = call_finalizer
                    call_finalizer = None
                    if finalizer is not None:
                        finalized = _accept_provider_call_without_replacing(finalizer)
                        if finalized and group_state is not None:
                            group_state = replace(group_state, accepted_delivery=group_state.transport_delivery)
                    continue

                terminal_provider_succeeded = True
                terminal_call_id = call_id
                stage = "answer"
                proposed_group_state = completed_group_state()
                authorization = action_session.authorize_answer(action)
                stage = "source_revalidation"
                if request.include_core_statuses and (
                    registry.canonical_core_statuses(
                        manifest_digest,
                        self._source_policy,
                    )
                    != core_statuses
                ):
                    raise ContextSourceError(ContextSourceErrorCode.SOURCE_DRIFT)
                revalidated_manifest = self._manifest_from_descriptors(
                    request,
                    scope,
                    registry.inventory(self._source_policy),
                )
                if revalidated_manifest != manifest:
                    raise ContextSourceError(ContextSourceErrorCode.SOURCE_DRIFT)
                stage = "answer_validation"
                if answer_validator is not None or call_contract is not None:
                    try:
                        if call_contract is not None:
                            call_contract.require_authorized_answer(call_decoded, authorization)
                        else:
                            _require_sync_none(answer_validator(action.answer, authorization))
                    except Exception as answer_failure:
                        issues = failure_issues(answer_failure, path="$.agent_action.task_result")
                        if phase_contract[1].contract_id in BUILTIN_RESULT_CONTRACT_IDS:
                            issues = tuple(diagnostic_to_wire(item) for item in issues)
                            if call_contract is not None:
                                issues = tuple(project_lead_scope_diagnostic(item) for item in issues)
                        if issues:
                            details = bounded_chunks(issues)
                        else:
                            try:
                                rules = None if answer_diagnostic is None else answer_diagnostic(action.answer)
                            except Exception:
                                rules = None
                            details = domain_rule_chunks(rules)
                            if phase_contract[1].contract_id in BUILTIN_RESULT_CONTRACT_IDS:
                                details = tuple(diagnostic_to_wire(item) for item in details)
                                if call_contract is not None:
                                    details = tuple(project_lead_scope_diagnostic(item) for item in details)
                        call_validation_details = details
                        if call_finalizer is not None:
                            try:
                                retain_details = getattr(
                                    call_finalizer, "set_validation_details", None,
                                )
                                if callable(retain_details):
                                    retain_details(details)
                            except BaseException:
                                pass
                        if validation_sink is not None:
                            _require_sync_none(validation_sink(
                                call_id, details,
                            ))
                        raise
                stage = "answer_observer"
                if validated_answer_sink is not None:
                    # Diagnostics are observational: never replace execution or
                    # authorization with a capture/observer failure.
                    try:
                        _require_sync_none(validated_answer_sink(call_id))
                    except BaseException:
                        pass
                _emit_context_event(
                    event_sink,
                    execution_event(
                        ContextExecutionEventKind.ANSWER_AUTHORIZED,
                        action_index=action_index,
                    ),
                )
                if request.mandatory_references:
                    stage = "reference_complete"
                    require_execution_workspace_current()
                    provider.complete_references(scope, policy_fingerprint)
                stage = "commit"
                require_execution_workspace_current()
                assert current_thread_id is not None
                assert terminal_call_id is not None
                commit_authorization = ContextLedgerCommitAuthorization(
                        attempt_id=attempt_id,
                        operation_id=request.operation_id,
                        result_correlation_id=request.result_correlation_id,
                        proposal=projection.checkpoint_proposal,
                        revalidated_proposal_digest=proposal_digest,
                        terminal_call_id=terminal_call_id,
                        final_provider_thread_id=current_thread_id,
                        completed_reference_operation_id=(
                            request.reference_operation_id
                        ),
                        output_action_authorization_id=(authorization.authorization_id),
                )
                if continuation is not None:
                    assert publication_validator is not None
                    deferred_finalizer = call_finalizer
                    call_finalizer = None
                    deferred_committed = False
                    deferred_generation: int | None = None
                    deferred_aborted = False

                    def commit_published() -> None:
                        nonlocal deferred_committed, deferred_generation
                        require_execution_workspace_current()
                        commit = self._ledger.commit_attempt(
                            key,
                            expected_generation=generation,
                            authorization=commit_authorization,
                        )
                        try:
                            require_execution_workspace_current()
                        except BaseException as failure:
                            try:
                                self._ledger.invalidate(
                                    key,
                                    expected_generation=commit.generation,
                                )
                            except BaseException as invalidation_failure:
                                try:
                                    failure.add_note(
                                        "context checkpoint invalidation failed: "
                                        f"{type(invalidation_failure).__name__}"
                                    )
                                except BaseException:
                                    pass
                            raise
                        deferred_generation = commit.generation
                        deferred_committed = True

                    def complete_published() -> None:
                        _emit_context_event(event_sink, execution_event(
                            ContextExecutionEventKind.CHECKPOINT_ADVANCED,
                            provider_input_tokens=_provider_input_token_total(submitted_accountings),
                        ))
                        if deferred_finalizer is not None:
                            _accept_provider_call_without_replacing(deferred_finalizer)

                    def abort_unpublished() -> None:
                        nonlocal deferred_aborted
                        if deferred_aborted:
                            return
                        deferred_aborted = True
                        try:
                            if deferred_committed:
                                assert deferred_generation is not None
                                self._ledger.invalidate(
                                    key,
                                    expected_generation=deferred_generation,
                                )
                            else:
                                self._ledger.taint_attempt(
                                    key, expected_generation=generation,
                                    attempt_id=attempt_id,
                                    outcome=ContextLedgerAttemptOutcome.FAILED,
                                )
                        except BaseException:
                            pass
                        if deferred_finalizer is not None:
                            _fail_provider_call_without_replacing(
                                deferred_finalizer,
                                ContextProviderTerminalCode.CONTEXT_RUNTIME_FAILED,
                            )
                        _emit_context_event_without_replacing(event_sink, execution_event(
                            ContextExecutionEventKind.CHECKPOINT_NOT_ADVANCED,
                        ))

                    continuation.stage(
                        commit_published,
                        abort_unpublished,
                        publication_validator,
                        complete_published,
                    )
                    require_execution_workspace_current()
                    return ContextInvocationResult(
                        action.answer,
                        authorization,
                        projection.accounting,
                        current_thread_id,
                        tuple(calls),
                        generation,
                        checkpoint_pending=True,
                        group_state=proposed_group_state,
                    )
                commit = self._ledger.commit_attempt(
                    key, expected_generation=generation,
                    authorization=commit_authorization,
                )
                try:
                    require_execution_workspace_current()
                except BaseException as failure:
                    try:
                        self._ledger.invalidate(
                            key,
                            expected_generation=commit.generation,
                        )
                    except BaseException as invalidation_failure:
                        try:
                            failure.add_note(
                                "context checkpoint invalidation failed: "
                                f"{type(invalidation_failure).__name__}"
                            )
                        except BaseException:
                            pass
                    raise
                terminal_provider_succeeded = False
                checkpoint_committed = True
                finalizer = call_finalizer
                call_finalizer = None
                if finalizer is not None:
                    successful_finalizers.append(finalizer)
                _emit_context_event(
                    event_sink,
                    execution_event(
                        ContextExecutionEventKind.CHECKPOINT_ADVANCED,
                        provider_input_tokens=_provider_input_token_total(
                            submitted_accountings
                        ),
                    ),
                )
                return ContextInvocationResult(
                    action.answer,
                    authorization,
                    projection.accounting,
                    current_thread_id,
                    tuple(calls),
                    commit.generation,
                    group_state=proposed_group_state,
                )
            stage = "exhausted"
            raise execution_error(ContextExecutionErrorCode.ACTION_EXHAUSTED)
        except BaseException as failure:
            native_facts = context_provider_failure_facts(failure)
            check = native_facts.native_context_check
            if check is None and last_native_check is not None and last_native_check.call_id == call_id:
                check = last_native_check
                record_context_provider_failure(failure, replace(native_facts, native_context_check=check))
            observe_native_context(check, ContextNativeDisposition.RESPONSE_REJECTED)
            observe_group(LeadGroupObservationKind.TERMINAL, terminal_code=(response_rejection_code.value if response_rejection_code is not None else type(failure).__name__))
            if group_state is not None:
                if (stage == "exhausted" and group_state.workflow is not None
                        and group_state.workflow.phase in {LeadGroupPhase.SELECT, LeadGroupPhase.GENERATE}):
                    group_state = replace(group_state, workflow=group_state.workflow.stop(
                        "provider_call_budget_exhausted"))
                record_context_provider_failure(failure, replace(
                    context_provider_failure_facts(failure), group_state=group_state))
            if call_contract is not None:
                call_contract.bind_failure(failure, call_decoded)
            if checkpoint_committed:
                raise
            if call_finalizer is not None:
                if stage in {"action", "answer"} and isinstance(failure, ContextActionError):
                    call_validation_details = bounded_chunks(failure_issues(failure, path="$.action"))
                    if validation_sink is not None and call_validation_details:
                        try:
                            _require_sync_none(validation_sink(call_id, call_validation_details))
                        except BaseException:
                            pass
                finalizer = call_finalizer
                call_finalizer = None
                if isinstance(failure, asyncio.CancelledError):
                    _cancel_provider_call_without_replacing(finalizer)
                elif stage in {"decode", "exhausted"} and isinstance(
                    failure,
                    ContextExecutionError,
                ):
                    code = (
                        ContextProviderTerminalCode.ACTION_EXHAUSTED
                        if stage == "exhausted"
                        else context_provider_decode_terminal_code(failure)
                    )
                    reject_response(finalizer, code)
                elif stage in {"action", "answer"} and isinstance(
                    failure,
                    ContextActionError,
                ):
                    reject_response(
                        finalizer,
                        ContextProviderTerminalCode.RESPONSE_SEMANTIC_REJECTED,
                    )
                elif stage == "answer_validation":
                    reject_response(
                        finalizer,
                        ContextProviderTerminalCode.ANSWER_VALIDATION_FAILED,
                    )
                elif stage == "reference_complete":
                    _fail_provider_call_without_replacing(
                        finalizer,
                        ContextProviderTerminalCode.REFERENCE_RUNTIME_FAILED,
                    )
                else:
                    _fail_provider_call_without_replacing(
                        finalizer,
                        ContextProviderTerminalCode.CONTEXT_RUNTIME_FAILED,
                    )
            if isinstance(failure, ContextSourceError) or stage in {
                "source",
                "source_revalidation",
            }:
                _emit_context_event_without_replacing(
                    event_sink,
                    execution_event(
                        ContextExecutionEventKind.INVALIDATED,
                    ),
                )
            failure_code = getattr(getattr(failure, "code", None), "value", "")
            if (
                isinstance(failure_code, str)
                and ("budget_exceeded" in failure_code or "limit_exceeded" in failure_code)
            ) or isinstance(failure, SessionContextCapacityError):
                _emit_context_event_without_replacing(
                    event_sink,
                    execution_event(
                        ContextExecutionEventKind.BUDGET_EXHAUSTED,
                    ),
                )
            if stage == "exhausted":
                _emit_context_event_without_replacing(
                    event_sink,
                    execution_event(
                        ContextExecutionEventKind.ACTION_EXHAUSTED,
                    ),
                )
            if action_session is not None:
                try:
                    action_session.fail()
                except BaseException:
                    pass
            outcome = self._failure_outcome(
                failure,
                stage=stage,
                history_advanced=history_advanced,
                terminal_provider_succeeded=terminal_provider_succeeded,
            )
            if reference_started:
                try:
                    provider.abort_references(
                        scope,
                        policy_fingerprint,
                        cancelled=isinstance(failure, asyncio.CancelledError),
                        provider_failure=provider_failure,
                    )
                except BaseException:
                    pass
            should_taint = self._should_taint_context(
                failure,
                outcome,
                history_advanced=history_advanced,
                terminal_provider_succeeded=terminal_provider_succeeded,
            )
            # 구조 거절은 완료된 입력 전달을 취소하지 않는다. Host 검사 실패에는 이 예외를 적용하지 않는다.
            if (should_taint and not request.fresh and call_contract is not None
                    and call_contract.schema_group_phase is not None
                    and stage == "decode" and type(failure) is ContextExecutionError
                    and outcome is ContextLedgerAttemptOutcome.MALFORMED
                    and response_rejection_code in {
                        ContextProviderTerminalCode.MALFORMED_STRUCTURED_RESPONSE,
                        ContextProviderTerminalCode.RESPONSE_CORRELATION_MISMATCH,
                    }):
                try:
                    facts = context_provider_failure_facts(failure)
                    require_execution_workspace_current()
                    transport = group_state.transport_delivery
                    if (not facts.timed_out and not facts.submission_uncertain
                            and not facts.taint_existing_thread and facts.continuation_rejection is None
                            and transport is not None and transport.call_id == call_request.call_id
                            and transport.payload_json == pending_group_payload == canonical_provider_json(call_request.payload)
                            and transport.schema_sha256 == hashlib.sha256(canonical_provider_json(call_request.schema)).hexdigest()
                            and inspect_provider_thread() == current_thread_id
                            and call_contract.structural_findings(result.response_text)
                            and replay_sources_unchanged() and group_delivery_ledger_verified()):
                        should_taint = False
                except BaseException:
                    pass
            if should_taint:
                try:
                    taint_provider_thread()
                except BaseException:
                    pass
                _emit_context_event_without_replacing(
                    event_sink,
                    execution_event(
                        ContextExecutionEventKind.TAINTED,
                    ),
                )
            if attempt_id is not None and generation is not None:
                try:
                    self._ledger.taint_attempt(
                        key,
                        expected_generation=generation,
                        attempt_id=attempt_id,
                        outcome=outcome,
                    )
                except BaseException:
                    pass
            _emit_context_event_without_replacing(
                event_sink,
                execution_event(
                    ContextExecutionEventKind.CHECKPOINT_NOT_ADVANCED,
                    provider_input_tokens=_provider_input_token_total(
                        submitted_accountings
                    ),
                ),
            )
            if isinstance(failure, ProviderAuthenticationRequiredError):
                failure.clear_replay_validator()
            existing_facts = context_provider_failure_facts(failure)
            cumulative = list(submitted_accountings)
            if (
                existing_facts.accounting is not None
                and existing_facts.accounting.state is ProviderCallState.SUBMITTED
                and existing_facts.accounting not in cumulative
            ):
                cumulative.append(existing_facts.accounting)
            record_context_provider_failure(
                failure,
                ContextProviderFailureFacts(
                    accounting=existing_facts.accounting,
                    timed_out=existing_facts.timed_out,
                    taint_existing_thread=existing_facts.taint_existing_thread,
                    submitted_accountings=tuple(cumulative),
                    submission_uncertain=(
                        existing_facts.submission_uncertain
                        or (
                            stage == "provider"
                            and provider_failure
                            and existing_facts.accounting is None
                        )
                    ),
                    response_rejection_code=response_rejection_code,
                    continuation_rejection=existing_facts.continuation_rejection,
                    native_context_check=existing_facts.native_context_check,
                    group_state=group_state,
                ),
            )
            raise


__all__ = ["AgentContextService"]
