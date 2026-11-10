"""Provider call, reference, failure, and finalization contracts."""

from __future__ import annotations

import inspect
from ...provider_adapter import (
    NativeContextCheck,
    ProviderContextWitness,
)
from ...provider_execution_workspace import ProviderExecutionWorkspace
from ...writing_artifacts.read_actions import (
    AuthorizedArtifactReference,
    ReferenceReadRequest,
    ReferenceReadResult,
)
from ..accounting import (
    ContextComponentKind,
    JsonPath,
    ProviderCallAccounting,
    ProviderCallKind,
    ProviderCallState,
)
from ..actions import ContextCallReadFact
from ..contracts.bound_call import BoundCallContract
from ..contracts.context_values import (
    safe_digest,
    safe_identity,
)
from ..contracts.errors import (
    ContextExecutionError,
    ContextExecutionErrorCode,
    ContextExecutionErrorDetail,
    execution_error,
    execution_error_has_detail,
)
from ..manifest import (
    ContextContinuationIdentity,
    ContextScope,
    ContextSourceKind,
)
from ..sources import writing_artifact_source_id
from .delivery import ContextGroupInvocationState
from collections.abc import Mapping
from dataclasses import (
    dataclass,
    field,
    replace,
)
from enum import StrEnum
from types import MappingProxyType
from typing import (
    AsyncContextManager,
    Protocol,
    runtime_checkable,
)


class ContextContinuationRejection(StrEnum):
    """Transport-proven rejection before execution, never inferred from text."""

    UNAVAILABLE = "continuation_unavailable"
    CAPACITY = "context_capacity"


@dataclass(frozen=True, slots=True)
class ContextProviderFailureFacts:
    """Validated physical-call facts attached without replacing a failure."""

    accounting: ProviderCallAccounting | None = None
    timed_out: bool = False
    taint_existing_thread: bool = False
    submitted_accountings: tuple[ProviderCallAccounting, ...] = ()
    submission_uncertain: bool = False
    response_rejection_code: ContextProviderTerminalCode | None = None
    continuation_rejection: ContextContinuationRejection | None = None
    group_state: ContextGroupInvocationState | None = field(default=None, kw_only=True, repr=False)
    native_context_check: NativeContextCheck | None = field(default=None, kw_only=True, repr=False)

    def __post_init__(self) -> None:
        if self.native_context_check is not None and type(self.native_context_check) is not NativeContextCheck:
            raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if self.group_state is not None and type(self.group_state) is not ContextGroupInvocationState:
            raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if self.continuation_rejection is not None and (
            type(self.continuation_rejection) is not ContextContinuationRejection
            or self.timed_out or self.submission_uncertain
        ):
            raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if self.accounting is not None and type(self.accounting) is not (
            ProviderCallAccounting
        ):
            raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if (
            type(self.timed_out) is not bool
            or type(self.taint_existing_thread) is not bool
            or type(self.submission_uncertain) is not bool
        ):
            raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if type(self.submitted_accountings) is not tuple or any(
            type(item) is not ProviderCallAccounting
            or item.state is not ProviderCallState.SUBMITTED
            for item in self.submitted_accountings
        ):
            raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if self.response_rejection_code is not None and (
            type(self.response_rejection_code) is not ContextProviderTerminalCode
            or self.response_rejection_code
            not in _CONTEXT_PROVIDER_RESPONSE_REJECTION_CODES
            or not self.submitted_accountings
        ):
            raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)


_EMPTY_PROVIDER_FAILURE_FACTS = ContextProviderFailureFacts()


_PROVIDER_FAILURE_FACTS_ATTRIBUTE = "_agent_context_provider_failure_facts"


def record_context_provider_failure(
    failure: BaseException,
    facts: ContextProviderFailureFacts,
) -> None:
    """Attach only validated content-free facts to the original failure."""

    if not isinstance(failure, BaseException) or type(facts) is not (
        ContextProviderFailureFacts
    ):
        raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
    previous = getattr(failure, _PROVIDER_FAILURE_FACTS_ATTRIBUTE, None)
    if (facts.group_state is None and type(previous) is ContextProviderFailureFacts
            and previous.group_state is not None):
        facts = replace(facts, group_state=previous.group_state)
    try:
        setattr(failure, _PROVIDER_FAILURE_FACTS_ATTRIBUTE, facts)
    except (AttributeError, TypeError):
        pass


def context_provider_failure_facts(
    failure: BaseException,
) -> ContextProviderFailureFacts:
    """Return validated private call facts or a prepared-free default."""

    facts = getattr(failure, _PROVIDER_FAILURE_FACTS_ATTRIBUTE, None)
    if type(facts) is not ContextProviderFailureFacts:
        return _EMPTY_PROVIDER_FAILURE_FACTS
    return facts


class ContextProviderTerminalCode(StrEnum):
    """Closed provider-neutral classifications for a completed response."""

    MALFORMED_STRUCTURED_RESPONSE = "malformed_structured_response"
    RESPONSE_CORRELATION_MISMATCH = "response_correlation_mismatch"
    RESPONSE_SEMANTIC_REJECTED = "response_semantic_rejected"
    ACTION_EXHAUSTED = "action_exhausted"
    ANSWER_VALIDATION_FAILED = "answer_validation_failed"
    REFERENCE_RUNTIME_FAILED = "reference_runtime_failed"
    CONTEXT_RUNTIME_FAILED = "context_runtime_failed"


_CONTEXT_PROVIDER_RESPONSE_REJECTION_CODES = frozenset(
    {
        ContextProviderTerminalCode.MALFORMED_STRUCTURED_RESPONSE,
        ContextProviderTerminalCode.RESPONSE_CORRELATION_MISMATCH,
        ContextProviderTerminalCode.RESPONSE_SEMANTIC_REJECTED,
        ContextProviderTerminalCode.ACTION_EXHAUSTED,
        ContextProviderTerminalCode.ANSWER_VALIDATION_FAILED,
    }
)


def context_provider_decode_terminal_code(
    failure: ContextExecutionError,
) -> ContextProviderTerminalCode:
    """Classify one rejected decoded response without response content."""

    if execution_error_has_detail(
        failure,
        ContextExecutionErrorDetail.RESPONSE_CORRELATION_MISMATCH,
    ):
        return ContextProviderTerminalCode.RESPONSE_CORRELATION_MISMATCH
    return ContextProviderTerminalCode.MALFORMED_STRUCTURED_RESPONSE


@runtime_checkable
class ContextProviderCallFinalizer(Protocol):
    """Provider-neutral exactly-once terminal callback for one raw response."""

    def accepted(self) -> None:
        """Record that the response completed its contextual operation."""

    def rejected(self, code: ContextProviderTerminalCode) -> None:
        """Record a closed semantic rejection of the response."""

    def cancelled(self) -> None:
        """Record cancellation after the response was received."""

    def post_response_runtime_failure(
        self,
        code: ContextProviderTerminalCode,
    ) -> None:
        """Record a closed runtime failure after the response was received."""


def _validate_context_provider_finalizer(value: object) -> None:
    """Require one synchronous provider-neutral finalizer implementation."""

    for name in (
        "accepted",
        "rejected",
        "cancelled",
        "post_response_runtime_failure",
    ):
        method = getattr(value, name, None)
        call = getattr(method, "__call__", None)
        if (
            not callable(method)
            or inspect.iscoroutinefunction(method)
            or inspect.isasyncgenfunction(method)
            or inspect.iscoroutinefunction(call)
            or inspect.isasyncgenfunction(call)
        ):
            raise execution_error(ContextExecutionErrorCode.PROVIDER_CONTRACT)


@dataclass(frozen=True, slots=True)
class ContextProviderCallRequest:
    """One exact service-built substantive physical provider call."""

    scope: ContextScope
    policy_fingerprint: str
    call_id: str
    call_kind: ProviderCallKind
    schema: Mapping[str, object] = field(repr=False)
    payload: Mapping[str, object] = field(repr=False)
    component_paths: Mapping[JsonPath, ContextComponentKind] = field(
        default_factory=dict,
        repr=False,
    )
    mandatory_reference_paths: tuple[JsonPath, ...] = ()
    expected_thread_id: str | None = None
    continuation_identity: ContextContinuationIdentity | None = None
    execution_workspace: ProviderExecutionWorkspace | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    context_witness: ProviderContextWitness | None = field(default=None, kw_only=True, repr=False)
    context_anchor: ProviderContextWitness | None = field(default=None, kw_only=True, repr=False)

    def __post_init__(self) -> None:
        if (self.context_anchor is not None and type(self.context_anchor) is not ProviderContextWitness
                or (self.context_anchor is None) != (self.context_witness is None)):
            raise TypeError("delta needs exact accepted and transport witnesses")
        if self.context_witness is not None and type(self.context_witness) is not ProviderContextWitness:
            raise TypeError("context provider witness has an invalid type")
        if type(self.scope) is not ContextScope:
            raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if self.continuation_identity is not None and (
            type(self.continuation_identity) is not ContextContinuationIdentity
            or not self.continuation_identity.accepts(self.scope)
        ):
            raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        safe_digest(self.policy_fingerprint)
        safe_identity(self.call_id)
        if type(self.call_kind) is not ProviderCallKind or self.call_kind in {
            ProviderCallKind.UNCLASSIFIED,
            ProviderCallKind.WRITING_ARTIFACT_REFERENCE_READ,
        }:
            raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if not isinstance(self.schema, Mapping) or not isinstance(
            self.payload,
            Mapping,
        ):
            raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if not isinstance(self.component_paths, Mapping):
            raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if type(self.mandatory_reference_paths) is not tuple:
            raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if self.expected_thread_id is not None:
            safe_identity(self.expected_thread_id)
        if self.execution_workspace is not None and (
            type(self.execution_workspace) is not ProviderExecutionWorkspace
            or self.execution_workspace.session_id != self.scope.session_id
        ):
            raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        object.__setattr__(self, "schema", MappingProxyType(dict(self.schema)))
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))
        object.__setattr__(
            self,
            "component_paths",
            MappingProxyType(dict(self.component_paths)),
        )


@dataclass(frozen=True, slots=True)
class BoundContextProviderCallRequest:
    """A physical Lead request carries one concrete contract, never a schema bundle."""
    contract: BoundCallContract = field(repr=False, compare=False)
    policy_fingerprint: str
    call_kind: ProviderCallKind
    payload: Mapping[str, object] = field(repr=False, compare=False)
    component_paths: Mapping[JsonPath, ContextComponentKind] = field(default_factory=dict, repr=False, compare=False)
    mandatory_reference_paths: tuple[JsonPath, ...] = ()
    expected_thread_id: str | None = None
    continuation_identity: ContextContinuationIdentity | None = None
    execution_workspace: ProviderExecutionWorkspace | None = field(default=None, repr=False, compare=False)

    context_witness: ProviderContextWitness | None = field(default=None, kw_only=True, repr=False)
    context_anchor: ProviderContextWitness | None = field(default=None, kw_only=True, repr=False)

    def __post_init__(self) -> None:
        if (self.context_anchor is not None and type(self.context_anchor) is not ProviderContextWitness
                or (self.context_anchor is None) != (self.context_witness is None)):
            raise TypeError("delta needs exact accepted and transport witnesses")
        if self.context_witness is not None and type(self.context_witness) is not ProviderContextWitness:
            raise TypeError("context provider witness has an invalid type")
        if type(self.contract) is not BoundCallContract:
            raise TypeError("physical Lead request requires its concrete bound contract")
        self.contract.require_current_offer(self.contract.offers)
        # Reuse existing metadata/payload/resource checks inside their owner; schema
        # is derived here and is never a constructor input on this typed request.
        metadata = ContextProviderCallRequest(scope=self.scope, policy_fingerprint=self.policy_fingerprint,
            call_id=self.call_id, call_kind=self.call_kind, schema=self.contract.schema_value(), payload=self.payload,
            component_paths=self.component_paths, mandatory_reference_paths=self.mandatory_reference_paths,
            expected_thread_id=self.expected_thread_id, continuation_identity=self.continuation_identity,
            execution_workspace=self.execution_workspace, context_witness=self.context_witness, context_anchor=self.context_anchor)
        object.__setattr__(self, "payload", metadata.payload)
        object.__setattr__(self, "component_paths", metadata.component_paths)
        object.__setattr__(self, "mandatory_reference_paths", metadata.mandatory_reference_paths)

    @property
    def scope(self) -> ContextScope:
        return self.contract.offers.scope

    @property
    def call_id(self) -> str:
        return self.contract.offers.call_id

    @property
    def schema(self) -> Mapping[str, object]:
        return self.contract.schema_value()


@dataclass(frozen=True, slots=True)
class ContextProviderCallResult:
    """Raw provider response plus exact submitted physical-call accounting."""

    response_text: str = field(repr=False)
    thread_id: str
    accounting: ProviderCallAccounting
    finalizer: ContextProviderCallFinalizer = field(repr=False, compare=False)

    context_witness: ProviderContextWitness | None = field(default=None, kw_only=True, repr=False)
    native_context_check: NativeContextCheck | None = field(default=None, kw_only=True, repr=False)

    def __post_init__(self) -> None:
        if self.native_context_check is not None and type(self.native_context_check) is not NativeContextCheck:
            raise execution_error(ContextExecutionErrorCode.PROVIDER_CONTRACT)
        if self.context_witness is not None and (type(self.context_witness) is not ProviderContextWitness or self.context_witness.thread_id != self.thread_id):
            raise TypeError("context result witness belongs to another thread")
        if self.context_witness is not None and type(self.context_witness) is not ProviderContextWitness:
            raise TypeError("context provider witness has an invalid type")
        if type(self.response_text) is not str:
            raise execution_error(ContextExecutionErrorCode.PROVIDER_CONTRACT)
        safe_identity(self.thread_id)
        if (
            type(self.accounting) is not ProviderCallAccounting
            or self.accounting.state is not ProviderCallState.SUBMITTED
        ):
            raise execution_error(ContextExecutionErrorCode.PROVIDER_CONTRACT)
        _validate_context_provider_finalizer(self.finalizer)


@dataclass(frozen=True, slots=True)
class ContextReferencePreludeRequest:
    """One exact mandatory-reference request resolved locally by the backend."""

    scope: ContextScope
    policy_fingerprint: str
    call_id: str
    reference_operation_id: str
    expected_thread_id: str | None
    continuation_identity: ContextContinuationIdentity | None = None
    execution_workspace: ProviderExecutionWorkspace | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        if type(self.scope) is not ContextScope:
            raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if self.continuation_identity is not None and (
            type(self.continuation_identity) is not ContextContinuationIdentity
            or not self.continuation_identity.accepts(self.scope)
        ):
            raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        safe_digest(self.policy_fingerprint)
        safe_identity(self.call_id)
        safe_identity(self.reference_operation_id)
        if self.expected_thread_id is not None:
            safe_identity(self.expected_thread_id)
        if self.execution_workspace is not None and (
            type(self.execution_workspace) is not ProviderExecutionWorkspace
            or self.execution_workspace.session_id != self.scope.session_id
        ):
            raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)


@dataclass(frozen=True, slots=True)
class ContextReferencePreludeResult:
    """Transient reference context and immutable proofs of its actual delivery."""

    context: tuple[Mapping[str, object], ...] = field(repr=False)
    read_facts: tuple[ContextCallReadFact, ...] = field(default=(), init=False, repr=False)
    _prelude_request: ContextReferencePreludeRequest | None = field(
        default=None, init=False, repr=False, compare=False,
    )
    _read_request: ReferenceReadRequest | None = field(default=None, init=False, repr=False)
    _authorized_references: tuple[AuthorizedArtifactReference, ...] = field(
        default=(), init=False, repr=False,
    )

    def __post_init__(self) -> None:
        if type(self.context) is not tuple or any(
            not isinstance(item, Mapping) for item in self.context
        ):
            raise execution_error(ContextExecutionErrorCode.PROVIDER_CONTRACT)

    @classmethod
    def from_read_result(
        cls,
        result: ReferenceReadResult,
        *,
        request: ContextReferencePreludeRequest | None = None,
    ) -> ContextReferencePreludeResult:
        """Retain actual proofs; only a bound prelude may publish READ facts."""

        if type(result) is not ReferenceReadResult or (
            request is not None and type(request) is not ContextReferencePreludeRequest
        ):
            raise execution_error(ContextExecutionErrorCode.PROVIDER_CONTRACT)
        correlation = result.request.correlation
        if request is not None and (
            correlation.operation_id != request.reference_operation_id
            or correlation.agent_id != request.scope.agent_id
        ):
            raise execution_error(ContextExecutionErrorCode.PROVIDER_CONTRACT)
        contents = result.contents
        context = tuple({
            "reference": {
                "path": reference.path,
                "line_range": (None if reference.line_range is None else {
                    "start": reference.line_range.start,
                    "end": reference.line_range.end,
                }),
            },
            "content": content.decode("utf-8", errors="strict"),
        } for reference, content in zip(result.request.references, contents, strict=True))
        prelude = cls(context)
        object.__setattr__(prelude, "_prelude_request", request)
        object.__setattr__(prelude, "_read_request", result.request)
        object.__setattr__(prelude, "_authorized_references", result.authorized_references)
        if request is not None:
            facts = tuple(ContextCallReadFact(
                evidence_id=f"reference_{proof.ordinal}",
                source_id=writing_artifact_source_id(proof.reference.path),
                source_kind=ContextSourceKind.WRITING_ARTIFACT,
                revision=f"sha256:{proof.source.source_sha256}",
                source_digest=f"sha256:{proof.source.source_sha256}",
                boundary_digest=f"sha256:{proof.selected_sha256}",
                whole_source=proof.reference.line_range is None,
                artifact_locator=proof.reference.path,
                boundary_kind="whole" if proof.reference.line_range is None else "artifact_lines",
                boundary_start=None if proof.reference.line_range is None else proof.reference.line_range.start,
                boundary_end=None if proof.reference.line_range is None else proof.reference.line_range.end,
            ) for proof in result.authorized_references)
            object.__setattr__(prelude, "read_facts", facts)
        return prelude


@runtime_checkable
class ContextProvider(Protocol):
    """Service-owned inversion consumed by the backend physical-call seam."""

    provider_id: str
    definition_fingerprint: str
    reference_operation_id: str | None

    def invocation(
        self,
        scope: ContextScope,
        policy_fingerprint: str,
        *,
        continuation_identity: ContextContinuationIdentity | None = None,
        execution_workspace: ProviderExecutionWorkspace | None = None,
    ) -> AsyncContextManager[None]:
        """Retain the provider binding for one complete contextual lifecycle."""

    def inspect_context_thread(
        self,
        scope: ContextScope,
        policy_fingerprint: str,
        *,
        execution_workspace: ProviderExecutionWorkspace | None = None,
    ) -> str | None:
        """Return one usable exact scoped continuation, if present."""

    def require_current_execution_workspace(
        self,
        scope: ContextScope,
        policy_fingerprint: str,
    ) -> None:
        """Revalidate any execution workspace retained by the invocation."""

    async def prepare_references(
        self,
        request: ContextReferencePreludeRequest,
    ) -> ContextReferencePreludeResult:
        """Load authorized mandatory references without a provider call."""

    async def invoke_context(
        self,
        request: ContextProviderCallRequest | BoundContextProviderCallRequest,
    ) -> ContextProviderCallResult:
        """Execute one raw structured call without decoding context actions."""

    def complete_references(
        self,
        scope: ContextScope,
        policy_fingerprint: str,
    ) -> None:
        """Accept the pending exact reference operation after terminal success."""

    def abort_references(
        self,
        scope: ContextScope,
        policy_fingerprint: str,
        *,
        cancelled: bool,
        provider_failure: bool,
    ) -> None:
        """Best-effort terminate a pending reference operation."""

    def taint_context_thread(
        self,
        scope: ContextScope,
        policy_fingerprint: str,
        *,
        execution_workspace: ProviderExecutionWorkspace | None = None,
    ) -> None:
        """Best-effort mark the exact scoped native continuation unusable."""


__all__ = [
    'ContextContinuationRejection',
    'ContextProviderFailureFacts',
    'record_context_provider_failure',
    'context_provider_failure_facts',
    'ContextProviderTerminalCode',
    'context_provider_decode_terminal_code',
    'ContextProviderCallFinalizer',
    'ContextProviderCallRequest',
    'BoundContextProviderCallRequest',
    'ContextProviderCallResult',
    'ContextReferencePreludeRequest',
    'ContextReferencePreludeResult',
    'ContextProvider',
]
