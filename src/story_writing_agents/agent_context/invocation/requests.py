"""Application invocation requests and accepted results."""

from __future__ import annotations

import json
from ...agents import LEAD_ORCHESTRATOR
from ...lead_schema_groups import LeadGroupPhase
from ...provider_execution_workspace import ProviderExecutionWorkspace
from ..actions import (
    CONTEXT_ACTION_POLICY,
    ContextAnswerAuthorization,
)
from ..contracts.bound_call import BoundCallFactory
from ..contracts.call_offers import (
    ContextInvocationKind,
    ContextJsonObjectResultContract,
)
from ..contracts.context_values import (
    SEARCH_SOURCE_KIND_ORDER,
    canonical_bytes,
    safe_identity,
)
from ..contracts.errors import (
    ContextExecutionErrorCode,
    execution_error,
)
from ..contracts.planning_inputs import (
    ContextMaterial,
    MandatoryReferenceReservation,
)
from ..contracts.projection_accounting import ContextProjectionAccounting
from ..ledger import ContextLedgerProviderCall
from ..manifest import (
    ContextScope,
    ContextSourceKind,
    ContextSubjectKind,
)
from .delivery import (
    ContextGroupInvocationState,
    ContextSourceClassification,
)
from dataclasses import (
    dataclass,
    field,
)


@dataclass(frozen=True, slots=True)
class ContextInvocationRequest:
    """Domain facts and transient caller-owned material for one invocation."""

    session_id: str
    subject_kind: ContextSubjectKind
    subject_id: str
    agent_id: str
    operation_id: str
    result_correlation_id: str
    materials: tuple[ContextMaterial, ...]
    result_contract: ContextJsonObjectResultContract | BoundCallFactory
    source_classifications: tuple[ContextSourceClassification, ...] = ()
    reference_operation_id: str | None = None
    mandatory_references: tuple[MandatoryReferenceReservation, ...] = ()
    fresh: bool = False
    allowed_search_source_kinds: tuple[ContextSourceKind, ...] = (
        SEARCH_SOURCE_KIND_ORDER
    )
    invocation_kind: ContextInvocationKind | None = None
    physical_call_limit: int | None = None
    include_core_statuses: bool = False
    session_state_json: str | None = field(default=None, repr=False)
    execution_workspace: ProviderExecutionWorkspace | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    group_state: ContextGroupInvocationState | None = field(default=None, kw_only=True, repr=False)

    def __post_init__(self) -> None:
        safe_identity(self.session_id)
        if self.group_state is not None and (
            type(self.group_state) is not ContextGroupInvocationState
            or not isinstance(self.result_contract, BoundCallFactory)
            or self.agent_id != LEAD_ORCHESTRATOR
            or self.group_state.request_basis.operation_id != self.operation_id
            or self.group_state.request_basis.result_correlation_id != self.result_correlation_id
        ):
            raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if type(self.subject_kind) is not ContextSubjectKind:
            raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if self.subject_kind is ContextSubjectKind.SESSION:
            raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        # Reuse the canonical subject grammar without accepting workspace input.
        ContextScope(
            "workspace",
            self.session_id,
            self.subject_kind,
            self.subject_id,
            self.agent_id,
        )
        safe_identity(self.operation_id)
        safe_identity(self.result_correlation_id)
        if type(self.materials) is not tuple or any(
            type(item) is not ContextMaterial for item in self.materials
        ):
            raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if any(
            item.entry.source_kind
            not in {
                ContextSourceKind.RUNTIME_INPUT,
                ContextSourceKind.AGENT_DEFINITION,
            }
            for item in self.materials
        ):
            raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if type(self.source_classifications) is not tuple or any(
            type(item) is not ContextSourceClassification
            for item in self.source_classifications
        ):
            raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        source_ids = tuple(item.source_id for item in self.source_classifications)
        if len(set(source_ids)) != len(source_ids):
            raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if self.reference_operation_id is not None:
            safe_identity(self.reference_operation_id)
        if type(self.mandatory_references) is not tuple or any(
            type(item) is not MandatoryReferenceReservation
            for item in self.mandatory_references
        ):
            raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if bool(self.reference_operation_id) is not bool(self.mandatory_references):
            raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if type(self.fresh) is not bool:
            raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if (
            type(self.allowed_search_source_kinds) is not tuple
            or not self.allowed_search_source_kinds
            or any(
                type(kind) is not ContextSourceKind
                or kind not in SEARCH_SOURCE_KIND_ORDER
                for kind in self.allowed_search_source_kinds
            )
            or self.allowed_search_source_kinds
            != tuple(
                kind
                for kind in SEARCH_SOURCE_KIND_ORDER
                if kind in self.allowed_search_source_kinds
            )
        ):
            raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if self.invocation_kind is not None and type(
            self.invocation_kind
        ) is not ContextInvocationKind:
            raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if type(self.result_contract) is not ContextJsonObjectResultContract and not isinstance(self.result_contract, BoundCallFactory):
            raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if self.physical_call_limit is not None and (
            type(self.physical_call_limit) is not int
            or self.physical_call_limit < 1
            or self.physical_call_limit > CONTEXT_ACTION_POLICY.max_provider_calls
        ):
            raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if type(self.include_core_statuses) is not bool:
            raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if self.session_state_json is not None:
            try:
                state = json.loads(self.session_state_json)
                if type(state) is not dict:
                    raise ValueError
                canonical_bytes(state)
            except (TypeError, ValueError):
                raise execution_error(ContextExecutionErrorCode.INVALID_VALUE) from None
        if self.execution_workspace is not None and (
            type(self.execution_workspace) is not ProviderExecutionWorkspace
            or self.execution_workspace.session_id != self.session_id
        ):
            raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)


@dataclass(frozen=True, slots=True)
class ContextInvocationResult:
    """Accepted answer with content-free plan, authorization, and call facts."""

    answer: str = field(repr=False)
    authorization: ContextAnswerAuthorization
    projection_accounting: ContextProjectionAccounting
    provider_thread_id: str
    provider_calls: tuple[ContextLedgerProviderCall, ...]
    ledger_generation: int
    checkpoint_pending: bool = False
    group_state: ContextGroupInvocationState | None = field(default=None, kw_only=True, repr=False)

    def __post_init__(self) -> None:
        if self.group_state is not None and (
            type(self.group_state) is not ContextGroupInvocationState
            or self.group_state.workflow is None
            or self.group_state.workflow.phase is not LeadGroupPhase.DONE
        ):
            raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if type(self.answer) is not str or not self.answer:
            raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if type(self.authorization) is not ContextAnswerAuthorization:
            raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if type(self.projection_accounting) is not ContextProjectionAccounting:
            raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        safe_identity(self.provider_thread_id)
        if type(self.provider_calls) is not tuple or any(
            type(item) is not ContextLedgerProviderCall for item in self.provider_calls
        ):
            raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if type(self.ledger_generation) is not int or self.ledger_generation < 1:
            raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)


__all__ = [
    'ContextInvocationRequest',
    'ContextInvocationResult',
]
