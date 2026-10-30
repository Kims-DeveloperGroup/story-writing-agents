"""Call-local state machine for bounded agent-directed context operations."""

from __future__ import annotations

from ..provider_adapter import ProviderContextWitness
from ..provider_wire import SchemaSelectionInput, LEAD_GROUP_PHASE_GUIDES
from .accounting import canonical_provider_json

import asyncio
import base64
import hashlib
import hmac
import inspect
from ..agents import LEAD_ORCHESTRATOR
from abc import ABC, abstractmethod
from copy import deepcopy
from typing import TypeVar
import json
import math
import secrets
import threading
from ..structured_output_validation import (
    ContractOwner,
    ContractRejection, BoundContract, NamedHostCheck, TypedDecoder, HostCheckContext,
    SelectedHostContext, SelectedContract, ValidatedValue, closed_rule, strict_json_member_text, json_value_utf8_size, failure_findings,
    SchemaRules,
    finite_subset_rule, finite_selection_rule, decode_finite_selection,
    canonical_schema_rules, diagnose_value, schema_accepts_only_objects, strict_json_object,
    validation_failure,
    validation_call,
    carry_validation,
    failure_issues,
    remap_validation,
    encoding_failure,
)
from contextlib import AsyncExitStack
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from types import MappingProxyType
from typing import AsyncContextManager, Protocol, cast, runtime_checkable

from ..errors import ProviderAuthenticationRequiredError
from ..structured_output_validation import (
    DomainValidationRule, bounded_chunks, diagnose_json_text, domain_rule_chunks,
)
from ..provider_auth_recovery import authentication_replay, request_authentication
from ..provider_execution_workspace import ProviderExecutionWorkspace
from ..workspace import Workspace
from ..writing_artifacts.read_actions import (
    AuthorizedArtifactReference, ReferenceReadRequest, ReferenceReadResult,
)
from ..conversations.session_context import (
    MAX_PROVIDER_SCHEMA_UTF8_BYTES,
    MAX_SESSION_CONTEXT_UTF8_BYTES,
    SessionContextCapacityError,
    SessionContextHistoryChangedError,
    SessionHistorySnapshot,
)
from .continuation import ContextContinuationTransaction
from .accounting import (
    ContextComponentKind,
    JsonPath,
    ProviderCallAccounting,
    ProviderCallKind,
    ProviderCallState,
    account_provider_payload,
)

from .actions import (
    ContextCallReadFact, search_text_rule, artifact_directory_prefix_rule,
    CONTEXT_ACTION_POLICY,
    ContextActionCorrelation,
    ContextActionError,
    ContextActionErrorCode,
    ContextActionPolicy,
    ContextAnswerAction,
    ContextAnswerAuthorization,
    ContextEvidenceReceipt,
    ContextReadAction,
    ContextReadObservation,
    ContextSearchAction,
    ContextSearchHit,
    ContextSearchObservation,
    ContextSearchQuery,
    ContextSourceBoundary,
    ContextSourceBoundaryKind,
    ContextSourceEvidence,
    ContextSourceHandle,
    ContextSourceStatusReceipt,
    ContextZeroHitSearchReceipt,
    serialize_context_action_observation,
)
from .manifest import (
    ContextContinuationIdentity,
    ContextManifest,
    ContextManifestEntry,
    ContextManifestError,
    ContextPriority,
    ContextScope,
    ContextSemanticChannel,
    ContextSourceKind,
    ContextSubjectKind,
    validate_context_digest,
    validate_context_identity,
)
from .planning import (
    DEFAULT_CONTEXT_PLANNING_POLICY,
    MAX_MANDATORY_REFERENCE_RESERVATION_UTF8_BYTES,
    ContextCheckpointState,
    ContextCatalogCursor,
    ContextCheckpointRecord,
    ContextCheckpointSource,
    ContextCheckpointView,
    ContextDeliveryMode,
    ContextMaterial,
    ContextRecordCursor,
    ContextRecordMaterial,
    MandatoryReferenceReservation,
    ContextPlanningConditions,
    ContextPlanningPolicy,
    ContextProjection,
    ContextProjectionAccounting,
    context_source_uses_catalog_alias,
    plan_context_projection,
)
from .progress import (
    ContextActionProgressEvent,
    ContextActionProgressKind,
    ContextActionProgressQuery,
    ContextActionProgressSink,
    ContextActionProgressSource,
)
from .ledger import (
    MAX_CONTEXT_LEDGER_SOURCES,
    ContextLedger,
    ContextLedgerRecord,
    ContextLedgerRecordState,
    ContextLedgerAttemptOutcome,
    ContextLedgerAttemptPlan,
    ContextLedgerCommitAuthorization,
    ContextLedgerKey,
    ContextLedgerProviderCall,
    context_checkpoint_proposal_digest,
)
from .request_compaction import compact_context_payload
from ..lead_provider_contracts import (
    LeadStagePolicy, LeadContractIndex, LeadResultContractOwner, LeadGroupCompilation,
    LeadDecodedResult, LeadContractRejection, LeadGroupedStage, factor_rule_schema,
)
from ..lead_schema_groups import (
    LeadGroupGenerationBinding, LeadGroupPhase, LeadGroupRequestBasis,
    LeadGroupSelectionRecord, LeadGroupWorkflow, SchemaGroupControl,
    SchemaGroupControlOwner, SchemaGroupReselection, SchemaGroupSelection,
)
from .actions import normalized_search_text, SEARCH_NORMALIZATION_REVISION
from ..provider_wire import (
    require_schema_projection,
    BUILTIN_RESULT_CONTRACT_IDS, WIRE_VERSION, WireShapeError, add_contract_guidance, diagnostic_to_wire, project_lead_scope_diagnostic, normalize_context_action,
    normalize_result, project_action_schema, project_context_payload,
    hoist_schema_definitions, rebase_schema_definitions,
)
from .sources import (
    CONTEXT_SOURCE_POLICY,
    ActiveDebateSourceAdapter,
    ContextSourceError,
    ContextSourceErrorCode,
    ContextSourceDescriptor,
    ContextSourcePolicy,
    ContextSourceRegistry,
    ContextSourceSnapshot,
    FocusedConversationSourceAdapter,
    OwnAgentStateSourceAdapter,
    WritingArtifactSourceAdapter,
    writing_artifact_source_id,
)

__all__ = [
    "AgentContextService",
    "ContextExecutionError",
    "ContextExecutionErrorCode",
    "ContextExecutionEvent",
    "ContextExecutionEventKind",
    "ContextExecutionEventSink",
    "ContextInvocationBinding",
    "ContextInvocationKind",
    "ContextInvocationRequest",
    "ContextInvocationResult",
    "ContextInvocationSession",
    "ContextInvocationState",
    "ContextJsonObjectResultContract",
    "ContextProvider",
    "ContextProviderCallFinalizer",
    "ContextProviderCallRequest",
    "ContextProviderCallResult",
    "ContextProviderFailureFacts",
    "ContextContinuationRejection",
    "ContextProviderTerminalCode",
    "ContextReferencePreludeRequest",
    "ContextReferencePreludeResult",
    "ContextReadReceipt",
    "ContextScope",
    "ContextSourceClassification",
    "context_provider_failure_facts",
    "record_context_provider_failure",
]

_CONTEXT_WIRE_VERSION = WIRE_VERSION
_ACTION_WIRE_CODES = {
    "search": "s",
    "read": "r",
    "answer": "a",
}
_SOURCE_KIND_WIRE_CODES = {
    ContextSourceKind.WRITING_ARTIFACT: "w",
    ContextSourceKind.CONVERSATION: "c",
    ContextSourceKind.ACTIVE_DEBATE: "d",
    ContextSourceKind.OWN_AGENT_STATE: "o",
}
_SEARCH_SOURCE_KIND_ORDER = tuple(_SOURCE_KIND_WIRE_CODES)
_SEMANTIC_CHANNEL_WIRE_CODES = {
    ContextSemanticChannel.CURRENT_INPUT: "i",
    ContextSemanticChannel.CANDIDATE: "k",
    ContextSemanticChannel.STABLE_BEHAVIOR: "s",
    ContextSemanticChannel.HARD_CONSTRAINT: "h",
    ContextSemanticChannel.FOCUSED_EVIDENCE: "f",
    ContextSemanticChannel.RETRIEVED_EVIDENCE: "r",
    ContextSemanticChannel.HISTORY: "y",
    ContextSemanticChannel.OPTIONAL_BACKGROUND: "b",
}
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
_STRUCTURED_WIRE_SOURCE_KINDS = {
    ContextSourceKind.RUNTIME_INPUT,
    ContextSourceKind.CONVERSATION,
    ContextSourceKind.ACTIVE_DEBATE,
    ContextSourceKind.OWN_AGENT_STATE,
}


_CONTEXT_ACTION_ROOT_DESCRIPTION = (
    'Gather needed evidence, then return the task result. Choose one currently '
    'permitted agent_action; search finds sources, read obtains contents, and '
    'submit_result completes the task. The runtime owns correlation and action '
    'bookkeeping.'
)

class ContextInvocationState(StrEnum):
    """Observable lifecycle of one call-local context action session."""

    OPEN = "open"
    SEARCHING = "searching"
    READING = "reading"
    ANSWER_AUTHORIZED = "answer_authorized"
    FAILED = "failed"


class ContextInvocationKind(StrEnum):
    """Optional provider-neutral semantic phase for a contextual invocation."""

    LEAD = "lead"
    SPECIALIST_ASSIGNMENT = "specialist_assignment"
    LEAD_SYNTHESIS = "lead_synthesis"
    SPECIALIST_ADVISORY = "specialist_advisory"


@dataclass(frozen=True, slots=True)
class ContextJsonObjectResultContract:
    """Carry one immutable owner-supplied JSON-object result schema."""

    contract_id: str
    schema: Mapping[str, object] = field(repr=False, compare=False)
    wire_format: bool = field(default=False, kw_only=True)
    _schema_bytes: bytes = field(init=False, repr=False)
    rule_graph: SchemaRules = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if type(self.wire_format) is not bool:
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        _safe_identity(self.contract_id)
        encoded = _validated_result_schema_bytes(self.schema)
        decoded = json.loads(encoded)
        assert type(decoded) is dict
        object.__setattr__(self, "schema", MappingProxyType(decoded))
        object.__setattr__(self, "_schema_bytes", encoded)
        object.__setattr__(self, "rule_graph", SchemaRules(encoded))

    def schema_value(self) -> dict[str, object]:
        """Return a detached exact schema for one physical provider call."""

        return self.rule_graph.provider_schema_value()


@dataclass(frozen=True, slots=True)
class ContextCallOffers:
    """One owner-locked physical-call snapshot, without source content."""
    call_id: str
    scope: ContextScope
    action_index: int
    allowed_actions: tuple[str, ...]
    action_limits: tuple[tuple[str, int], ...]
    search_source_kinds: tuple[ContextSourceKind, ...]
    issued_handle_aliases: tuple[str, ...]
    completed_read_facts: tuple[ContextCallReadFact, ...]
    delivery_read_facts: tuple[ContextCallReadFact, ...]
    core_status_facts: tuple[ContextSourceStatusReceipt, ...]
    reference_read_digest: str | None = field(default=None, init=False)
    offer_digest: str = field(init=False)

    remaining_provider_calls: int | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        if self.remaining_provider_calls is not None and (type(self.remaining_provider_calls) is not int or self.remaining_provider_calls < 0):
            raise ValueError("current physical remaining is invalid")
        _safe_identity(self.call_id)
        if (type(self.scope) is not ContextScope or type(self.action_index) is not int or self.action_index < 1
                or type(self.allowed_actions) is not tuple or not self.allowed_actions
                or len(set(self.allowed_actions)) != len(self.allowed_actions)
                or any(item not in {"search", "read", "answer"} for item in self.allowed_actions)
                or type(self.action_limits) is not tuple or any(
                    type(item) is not tuple or len(item) != 2 or type(item[0]) is not str
                    or type(item[1]) is not int or item[1] < 0 for item in self.action_limits)
                or len({item[0] for item in self.action_limits}) != len(self.action_limits)
                or type(self.search_source_kinds) is not tuple
                or any(type(item) is not ContextSourceKind for item in self.search_source_kinds)
                or type(self.issued_handle_aliases) is not tuple
                or any(type(item) is not str for item in self.issued_handle_aliases)
                or len(set(self.issued_handle_aliases)) != len(self.issued_handle_aliases)
                or any(type(values) is not tuple or any(type(item) is not ContextCallReadFact for item in values)
                       for values in (self.completed_read_facts, self.delivery_read_facts))):
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if (type(self.core_status_facts) is not tuple
                or any(type(item) is not ContextSourceStatusReceipt
                       for item in self.core_status_facts)
                or len({item.evidence_id for item in self.core_status_facts})
                != len(self.core_status_facts)):
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if self.reference_read_digest is not None:
            _safe_digest(self.reference_read_digest)
        encoded = json.dumps(self.snapshot_value(), sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False).encode("utf-8", "strict")
        object.__setattr__(self, "offer_digest", hashlib.sha256(encoded).hexdigest())

    def snapshot_value(self) -> dict[str, object]:
        """Return the full detached source/eligibility facts of this physical offer."""
        value = {"call_id": self.call_id, "scope": repr(self.scope), "action_index": self.action_index,
            "actions": self.allowed_actions, "limits": self.action_limits,
            "search_kinds": [kind.value for kind in self.search_source_kinds],
            "handles": self.issued_handle_aliases,
            "completed": [item.snapshot_value() for item in self.completed_read_facts],
            "delivery": [item.snapshot_value() for item in self.delivery_read_facts],
            "core_statuses": [_status_value(item) for item in self.core_status_facts],
        }
        if self.reference_read_digest is not None:
            value["reference_read_digest"] = self.reference_read_digest
        if self.remaining_provider_calls is not None:
            value["remaining_provider_calls"] = self.remaining_provider_calls
        return value


_CallValue = TypeVar("_CallValue")
_CALL_PROOF_SEAL = object()


@dataclass(frozen=True, slots=True)
class AuthorizedAnswerHostContext(HostCheckContext):
    """The actual service authorization, supplied only at its existing owner point."""
    authorization: ContextAnswerAuthorization

    def __post_init__(self) -> None:
        if type(self.authorization) is not ContextAnswerAuthorization:
            raise TypeError("authorized checks require actual service authorization")


@dataclass(frozen=True, slots=True)
class ContextActionValue:
    """Explicit pure action-codec output; resources remain service-owned."""
    _value: dict[str, object] = field(repr=False, compare=False)

    def as_object(self) -> dict[str, object]:
        return deepcopy(self._value)


@dataclass(frozen=True, slots=True)
class ContextActionContractOwner(ContractOwner[ContextActionValue]):
    owner_id: str
    rules: SchemaRules
    eligibility_offers: ContextCallOffers
    semantic_member: BoundContract[LeadDecodedResult | SchemaGroupControl] | None = field(repr=False, compare=False)
    owner_revision: str = field(init=False, default="848-v3:typed-action-owner")
    _checks: tuple[NamedHostCheck[ContextActionValue], ...] = field(init=False, repr=False, compare=False)
    _declared_decode: Callable[[ValidatedValue], ContextActionValue] = field(init=False, repr=False, compare=False)
    _declared_inputs: tuple[tuple[object, ...], tuple[object, ...]] = field(init=False, repr=False, compare=False)

    def __init_subclass__(cls, **kwargs):
        raise TypeError("context action owner cannot be overridden")

    def __post_init__(self) -> None:
        if type(self.rules) is not SchemaRules or type(self.eligibility_offers) is not ContextCallOffers:
            raise TypeError("action owner requires exact current canonical declarations")
        action_type = self.rules.literal_at(("action_type",))
        if action_type == "submit_result":
            if (type(self.semantic_member) is not BoundContract
                    or self.rules.literal_at(("task_result", "schema_id")) != self.semantic_member.schema_id
                    or self.rules.node_fingerprint(("task_result",)) != self.semantic_member.rule_graph.node_fingerprint()):
                raise ValueError("ANSWER requires its exact authoritative semantic member")
        elif action_type in {"search_context", "read_context"}:
            if self.semantic_member is not None:
                raise ValueError("SEARCH/READ cannot bind a semantic result member")
        else:
            raise ValueError("unknown current action family")
        checks = ()
        if action_type == "search_context":
            limit = dict(self.eligibility_offers.action_limits)["query_characters"]
            facts = json.dumps({"requirements": [{"path": "$.queries[*].search_text",
                "rule": "NORMALIZED_SEARCH_TEXT_LENGTH", "expected": {
                    "normalization": SEARCH_NORMALIZATION_REVISION, "minimum": 1, "maximum": limit}}]},
                sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
            checks = (NamedHostCheck("owner.normalized_search_length", SEARCH_NORMALIZATION_REVISION,
                "selected", SelectedHostContext, facts, self._require_normalized_queries,
                dependencies=(normalized_search_text,)),)
        object.__setattr__(self, "_checks", checks)
        issued_handle_aliases = self.eligibility_offers.issued_handle_aliases
        read_maximum = (
            min(dict(self.eligibility_offers.action_limits)["handles_per_read"],
                dict(self.eligibility_offers.action_limits)["cumulative_handles"],
                CONTEXT_ACTION_POLICY.max_handles_per_read, len(issued_handle_aliases))
            if self.rules.literal_at(("action_type",)) == "read_context" else 0
        )
        is_group_control = (self.semantic_member is not None
                            and type(self.semantic_member.owner) is SchemaGroupControlOwner)

        def decode_typed(value: ValidatedValue) -> ContextActionValue:
            if type(value) is not ValidatedValue:
                raise TypeError("action codec requires complete selected graph validation")
            if is_group_control:
                return ContextActionValue({"action": {"kind": "schema_group_control"}})
            domain = normalize_context_action({"agent_action": value.as_object()}, finite_source_kinds=True)
            action = domain["action"]
            if action["kind"] == "read":
                action["handles"] = list(decode_finite_selection(action["handles"],
                                                                issued_handle_aliases,
                                                                minimum=1, maximum=read_maximum))
            return ContextActionValue(deepcopy(domain))
        object.__setattr__(self, "_declared_decode", decode_typed)
        object.__setattr__(self, "_declared_inputs", self._capture_declaration(action_type))

    def _capture_declaration(self, action_type: object) -> tuple[tuple[object, ...], tuple[object, ...]]:
        member = self.semantic_member
        references = (self.rules, self.eligibility_offers,
                      self.eligibility_offers.issued_handle_aliases,
                      self.eligibility_offers.action_limits, member,
                      None if member is None else member.rule_graph,
                      self._checks, self._declared_decode,
                      type(self)._require_normalized_queries,
                      normalized_search_text if action_type == "search_context" else None,
                      *(item for check in self._checks
                        for item in (check, check.check, check.dependencies)))
        # The canonical rules pin action_type without reparsing the graph on each access.
        values = (action_type, self.owner_id, self.owner_revision,
                  self.rules.canonical_bytes, self.rules.maximum_bytes,
                  CONTEXT_ACTION_POLICY.max_handles_per_read
                  if action_type == "read_context" else None,
                  SEARCH_NORMALIZATION_REVISION if action_type == "search_context" else None,
                  None if member is None else (member.schema_id, member.rule_graph.canonical_bytes),
                  tuple((check.name, check.revision, check.phase, check.context_type,
                         check.corrective_facts) for check in self._checks))
        return references, values

    def _require_declaration(self) -> None:
        expected_references, expected_values = self._declared_inputs
        try:
            references, values = self._capture_declaration(expected_values[0])
        except (AttributeError, TypeError) as exc:
            raise ValueError("frozen context action owner declaration changed") from exc
        if (len(references) != len(expected_references)
                or any(current is not expected
                       for current, expected in zip(references, expected_references))
                or values != expected_values):
            raise ValueError("frozen context action owner declaration changed")

    def declare_rules(self) -> SchemaRules:
        self._require_declaration()
        return self.rules

    def named_host_checks(self) -> tuple[NamedHostCheck[ContextActionValue], ...]:
        self._require_declaration()
        return self._checks

    def typed_decoder(self) -> TypedDecoder[ContextActionValue]:
        self._require_declaration()
        return TypedDecoder("context.current_action_codec", "848-v3:typed-action-owner", self._declared_decode,
                            (normalize_context_action, decode_finite_selection))

    def _require_normalized_queries(self, value: ContextActionValue, context: HostCheckContext) -> None:
        maximum = dict(self.eligibility_offers.action_limits)["query_characters"]
        for query in value.as_object()["action"]["queries"]:
            text = normalized_search_text(query["text"])
            if not 1 <= len(text) <= maximum:
                raise validation_failure(ValueError(), "$.queries[*].search_text",
                                          "normalized SEARCH text length within owner limit")


class BoundCallFactory(ABC):
    """A logical owner declares typed families; concrete call relationships are sealed."""
    schema_group_protocol = False
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if any(name in cls.__dict__ for name in ("for_offer", "repair_lineage", "corrective_input", "diagnostic")):
            raise TypeError("a call factory may only declare its typed result owners")

    @property
    @abstractmethod
    def contract_id(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def declare_result_owners(self, offers: ContextCallOffers) -> LeadStagePolicy:
        raise NotImplementedError

    @abstractmethod
    def require_declaration(self) -> None:
        """Reject changed owner invocation targets without freezing live context."""
        raise NotImplementedError

    def declare_schema_groups(self, offers: ContextCallOffers,
                              request_basis: LeadGroupRequestBasis, *,
                              prepared_compilation: LeadGroupCompilation | None = None) -> LeadGroupedStage | None:
        return None

    def for_offer(self, offers: ContextCallOffers, lineage: BoundCallLineage | None = None,
                  *, request_basis: LeadGroupRequestBasis | None = None,
                  group_workflow: LeadGroupWorkflow | None = None,
                  prepared_compilation: LeadGroupCompilation | None = None) -> BoundCallContract:
        return BoundCallContract.bind(self, offers, lineage, request_basis=request_basis,
                                      group_workflow=group_workflow,
                                      prepared_compilation=prepared_compilation)

    def repair_lineage(self, failure: BaseException) -> BoundCallLineage:
        contract, _ = _bound_call_failure(failure)
        if contract.factory is not self:
            raise ValueError("repair belongs to another frozen logical owner")
        return contract.repair_lineage()

    def corrective_input(self, failure: BaseException) -> str:
        contract, rejection = _bound_call_failure(failure)
        if contract.factory is not self:
            raise ValueError("repair belongs to another frozen logical owner")
        return contract.corrective_input(rejection)

    def diagnostic(self, failure: BaseException) -> tuple[str, ...]:
        contract, rejection = _bound_call_failure(failure)
        if contract.factory is not self:
            raise ValueError("diagnostic belongs to another frozen logical owner")
        return rejection.requirements


@dataclass(frozen=True, slots=True, init=False)
class BoundCallLineage:
    origin: BoundCallContract = field(repr=False, compare=False)
    kind: str

    def __init__(self, origin: BoundCallContract, kind: str, *, seal=None):
        if seal is not _CALL_PROOF_SEAL or type(origin) is not BoundCallContract or kind not in {"repair", "replay"}:
            raise TypeError("lineage is issued only by the frozen current call")
        object.__setattr__(self, "origin", origin)
        object.__setattr__(self, "kind", kind)


@dataclass(frozen=True, slots=True, init=False)
class BoundCallDecodedAction:
    selected_action: SelectedContract[ContextActionValue]
    selected_result: SelectedContract[LeadDecodedResult | SchemaGroupControl] | None
    _contract: BoundCallContract = field(repr=False, compare=False)

    def __init__(self):
        raise TypeError("call results are issued by the selected bound call")

    def belongs_to(self, contract: BoundCallContract) -> bool:
        return self._contract is contract and self.selected_action.belongs_to(contract.action_index.select(self.selected_action.schema_id))

    def domain_value(self) -> dict[str, object]:
        return self.selected_action.value.as_object()

    @property
    def answer_json(self) -> str | None:
        if self.selected_result is None:
            return None
        if type(self.selected_result.value) is not LeadDecodedResult:
            raise TypeError("그룹 제어 결과를 업무 답변으로 읽을 수 없습니다")
        return self.selected_result.value.domain_json

    @property
    def group_control(self) -> SchemaGroupControl | None:
        if self.selected_result is not None and type(self.selected_result.value) in {
                SchemaGroupSelection, SchemaGroupReselection}:
            return self.selected_result.value
        return None


@dataclass(frozen=True, slots=True)
class BoundCallContract:
    """Actual physical offer plus sealed complete outer/inner and repair relationships."""
    factory: BoundCallFactory = field(repr=False, compare=False)
    offers: ContextCallOffers
    lineage: BoundCallLineage | None = field(default=None, repr=False, compare=False)
    request_basis: LeadGroupRequestBasis | None = field(default=None, kw_only=True)
    group_workflow: LeadGroupWorkflow | None = field(default=None, kw_only=True)
    prepared_compilation: LeadGroupCompilation | None = field(default=None, kw_only=True, repr=False, compare=False)
    group_stage: LeadGroupedStage | None = field(init=False, repr=False, compare=False)
    _group_anchor: tuple = field(init=False, repr=False, compare=False)
    eligibility_offers: ContextCallOffers = field(init=False)
    semantic_index: LeadContractIndex[LeadDecodedResult | SchemaGroupControl] = field(init=False, repr=False, compare=False)
    action_index: LeadContractIndex[ContextActionValue] = field(init=False, repr=False, compare=False)
    rule_graph: SchemaRules = field(init=False, repr=False)
    outer_inner_schema_ids: tuple[tuple[str, str | None], ...] = field(init=False)
    logical_origin_call_id: str = field(init=False)
    phase: str = field(init=False)
    _semantic_anchor: LeadContractIndex[LeadDecodedResult | SchemaGroupControl] = field(init=False, repr=False, compare=False)
    _action_anchor: LeadContractIndex[ContextActionValue] = field(init=False, repr=False, compare=False)

    def __init_subclass__(cls, **kwargs):
        raise TypeError("bound call relationships cannot be overridden")

    def __post_init__(self) -> None:
        if not isinstance(self.factory, BoundCallFactory) or type(self.offers) is not ContextCallOffers:
            raise TypeError("bound call requires typed logical factory and actual physical offers")
        if self.offers.scope.agent_id != LEAD_ORCHESTRATOR:
            raise TypeError("typed Lead call requires actual Lead offers")
        guard = type(self.factory).require_declaration
        TypedDecoder.require_hook_shape(self.factory.require_declaration, 0)
        if guard(self.factory) is not None:
            raise TypeError("frozen owner declaration guard must return None")
        if type(self.factory.schema_group_protocol) is not bool:
            raise TypeError("그룹 프로토콜 활성화는 신뢰된 bool 프로필이어야 합니다")
        group_stage = None
        if self.lineage is None:
            if self.factory.schema_group_protocol:
                if type(self.request_basis) is not LeadGroupRequestBasis:
                    raise TypeError("두 국면 계약에는 서비스의 현재 논리 요청 참조가 필요합니다")
                group_stage = self.factory.declare_schema_groups(
                    self.offers, self.request_basis, prepared_compilation=self.prepared_compilation)
                if type(group_stage) is not LeadGroupedStage:
                    raise TypeError("두 국면 factory는 하나의 현재 typed 의미 계획을 선언해야 합니다")
                object.__setattr__(self, "prepared_compilation", group_stage.compilation)
                workflow = self.group_workflow
                if workflow is not None and type(workflow) is not LeadGroupWorkflow:
                    raise TypeError("현재 typed 그룹 국면 상태가 필요합니다")
                workflow = (LeadGroupWorkflow(group_stage.catalog) if workflow is None else
                            workflow.refresh(group_stage.catalog))
                object.__setattr__(self, "group_workflow", workflow)
                policy = (group_stage.selection_policy if workflow.phase is LeadGroupPhase.SELECT else
                          group_stage.result_policy(workflow.active_selection.group_id))
            else:
                if (self.request_basis is not None or self.group_workflow is not None
                        or self.prepared_compilation is not None):
                    raise ValueError("단일 국면 호출에 그룹 선택 상태를 넣을 수 없습니다")
                policy = self.factory.declare_result_owners(self.offers)
            owners = () if type(policy) is not LeadStagePolicy else policy.owner_declarations
            if (type(policy) is not LeadStagePolicy or policy.physical_call_id != self.offers.call_id
                    or policy.offer_digest != self.offers.offer_digest
                    or policy.eligibility_offer_digest != self.offers.offer_digest
                    or policy.logical_origin_call_id != self.offers.call_id
                    or policy.maximum_schema_bytes != 65_536
                    or policy.wire_root_path != "$.agent_action.task_result"
                    or type(owners) is not tuple
                    or any(type(owner) not in {LeadResultContractOwner, SchemaGroupControlOwner} for owner in owners)
                    or any(type(owner) is SchemaGroupControlOwner and group_stage is None for owner in owners)
                    or any(owner.checks[0].context_type is not AuthorizedAnswerHostContext
                           for owner in owners if type(owner) is LeadResultContractOwner)):
                raise ValueError("logical owner declaration differs from actual physical offer")
            if group_stage is not None:
                controls = tuple(owner for owner in owners if type(owner) is SchemaGroupControlOwner)
                selected_group = (None if self.group_workflow.active_selection is None else
                                  self.group_workflow.active_selection.group_id)
                if (any(owner.catalog is not group_stage.catalog
                        or owner.current_group_id != selected_group for owner in controls)
                        or self.group_workflow.phase is LeadGroupPhase.SELECT
                        and (len(owners) != 1 or len(controls) != 1)
                        or self.group_workflow.phase is LeadGroupPhase.GENERATE
                        and (len(controls) != int(len(group_stage.catalog.groups) > 1)
                             or not any(type(owner) is LeadResultContractOwner for owner in owners)
                             or any(owner.variant.group_id != selected_group for owner in owners
                                    if type(owner) is LeadResultContractOwner))):
                    raise ValueError("현재 국면과 그룹 전체의 업무·제어 owner 관계가 다릅니다")
            semantic = LeadContractIndex.bind(policy)
            eligible, phase = self.offers, "ordinary"
            outer = _bound_action_index(eligible, semantic, self.offers)
        else:
            if type(self.lineage) is not BoundCallLineage or self.lineage.origin.factory is not self.factory:
                raise ValueError("cross-owner logical/corrective lineage")
            origin = self.lineage.origin
            origin.require_current_offer(origin.offers)
            if (self.request_basis is not None and self.request_basis != origin.request_basis
                    or self.group_workflow is not None and self.group_workflow is not origin.group_workflow
                    or self.prepared_compilation is not None
                    and self.prepared_compilation is not origin.prepared_compilation):
                raise ValueError("교정·replay는 원래 선택 기준과 국면을 보존해야 합니다")
            group_stage = origin.group_stage
            object.__setattr__(self, "request_basis", origin.request_basis)
            object.__setattr__(self, "group_workflow", origin.group_workflow)
            object.__setattr__(self, "prepared_compilation", origin.prepared_compilation)
            if self.offers.scope != origin.offers.scope or self.offers.call_id == origin.offers.call_id:
                raise ValueError("retained lineage requires same scope and a new physical call")
            eligible = origin.eligibility_offers
            if self.lineage.kind == "repair":
                semantic, outer, phase = origin.semantic_index, origin.action_index, "repair"
            else:
                policy = replace(origin.semantic_index.policy, physical_call_id=self.offers.call_id,
                                 offer_digest=self.offers.offer_digest)
                semantic = LeadContractIndex.bind(policy)
                outer = _bound_action_index(eligible, semantic, self.offers)
                phase = "replay_repair" if origin.phase in {"repair", "replay_repair"} else "replay"
        pairs = tuple((member.schema_id, owner.semantic_member.schema_id
                       if owner.semantic_member is not None else None)
                      for member, owner in zip(outer.contracts, outer.owners, strict=True))
        graph = SchemaRules.declare(
            hoist_schema_definitions(closed_rule(
                {"agent_action": outer.rule_graph.schema_value()},
                description=_CONTEXT_ACTION_ROOT_DESCRIPTION,
            )),
            maximum_bytes=MAX_PROVIDER_SCHEMA_UTF8_BYTES,
        )
        object.__setattr__(self, "eligibility_offers", eligible)
        object.__setattr__(self, "semantic_index", semantic)
        object.__setattr__(self, "action_index", outer)
        object.__setattr__(self, "rule_graph", graph)
        object.__setattr__(self, "outer_inner_schema_ids", pairs)
        object.__setattr__(self, "logical_origin_call_id", semantic.policy.logical_origin_call_id)
        object.__setattr__(self, "phase", phase)
        object.__setattr__(self, "group_stage", group_stage)
        object.__setattr__(self, "_group_anchor", (
            self.request_basis, self.group_workflow, group_stage, self.prepared_compilation))
        object.__setattr__(self, "_semantic_anchor", semantic)
        object.__setattr__(self, "_action_anchor", outer)
        semantic.require_provenance()
        outer.require_provenance()
        if graph.node_fingerprint(("agent_action",)) != outer.rule_graph.node_fingerprint():
            raise ValueError("actual call/graph/frozen owner provenance changed")
        self.require_current_offer(self.offers)

    @classmethod
    def bind(cls, factory: BoundCallFactory, offers: ContextCallOffers,
             lineage: BoundCallLineage | None = None, *, request_basis: LeadGroupRequestBasis | None = None,
             group_workflow: LeadGroupWorkflow | None = None,
             prepared_compilation: LeadGroupCompilation | None = None) -> BoundCallContract:
        return cls(factory, offers, lineage, request_basis=request_basis, group_workflow=group_workflow,
                   prepared_compilation=prepared_compilation)

    def require_current_offer(self, offers: ContextCallOffers) -> None:
        if self.factory.require_declaration() is not None:
            raise TypeError("frozen owner declaration guard must return None")
        if any(current is not expected for current, expected in zip(
                (self.request_basis, self.group_workflow, self.group_stage, self.prepared_compilation),
                self._group_anchor, strict=True)):
            raise ValueError("현재 호출의 그룹 선택 연결이 변경됐습니다")
        if self.group_stage is not None:
            self.group_stage.require_provenance()
            selection = self.group_workflow.active_selection
            if (self.group_workflow.catalog != self.group_stage.catalog
                    or self.prepared_compilation is not self.group_stage.compilation
                    or selection is not None and (
                        selection.catalog.request_basis_ref != self.group_stage.catalog.request_basis_ref
                        or selection.group_id not in self.group_stage.catalog.group_ids)):
                raise ValueError("선택 국면과 현재 카탈로그가 다릅니다")
        if (type(offers) is not ContextCallOffers or offers != self.offers
                or offers.offer_digest != self.offers.offer_digest
                or self.semantic_index is not self._semantic_anchor
                or self.action_index is not self._action_anchor
                or type(self.rule_graph) is not SchemaRules
                or hashlib.sha256(_canonical_bytes(offers.snapshot_value())).hexdigest() != offers.offer_digest
                or hashlib.sha256(_canonical_bytes(self.eligibility_offers.snapshot_value())).hexdigest()
                != self.eligibility_offers.offer_digest):
            raise ValueError("actual call/graph/frozen owner provenance changed")
        semantic_index = self.semantic_index
        semantic_contracts = semantic_index.contracts
        if (type(semantic_contracts) is not tuple
                or tuple(member.schema_id for member in semantic_contracts)
                != semantic_index.expected_schema_ids):
            raise ValueError("current semantic contract identities changed")
        if (self.semantic_index.policy.eligibility_offer_digest != self.eligibility_offers.offer_digest
                or self.semantic_index.policy.logical_origin_call_id != self.logical_origin_call_id):
            raise ValueError("frozen logical eligibility snapshot changed")
        if self.lineage is None or self.lineage.kind == "replay":
            if (self.semantic_index.policy.physical_call_id != self.offers.call_id
                    or self.semantic_index.policy.offer_digest != self.offers.offer_digest
                    or self.action_index.policy.physical_call_id != self.offers.call_id
                    or self.action_index.policy.offer_digest != self.offers.offer_digest):
                raise ValueError("current physical policy/index differs from actual offer")
        else:
            origin = self.lineage.origin
            if (origin.factory is not self.factory or self.eligibility_offers is not origin.eligibility_offers
                    or self.semantic_index is not origin.semantic_index or self.action_index is not origin.action_index):
                raise ValueError("corrective request changed the frozen rejected index")
        for index in (semantic_index, self.action_index):
            if (index.owners is not index.policy.owner_declarations
                    or len(index.contracts) != len(index.owners)):
                raise ValueError("current contract owner binding changed")
            for member, owner in zip(index.contracts, index.owners, strict=True):
                if (type(member) is not BoundContract or member.owner is not owner
                        or member.identity != index.policy.identity_for(owner.owner_id)):
                    raise ValueError("current contract owner binding changed")
                if type(owner) is SchemaGroupControlOwner:
                    owner.require_declaration()
        pairs: list[tuple[str, str | None]] = []
        for member, owner in zip(self.action_index.contracts, self.action_index.owners, strict=True):
            if type(owner) is not ContextActionContractOwner or owner.eligibility_offers is not self.eligibility_offers:
                raise ValueError("outer action lost its frozen eligibility owner")
            inner = owner.semantic_member
            if inner is not None:
                if not any(item is inner for item in semantic_contracts):
                    raise ValueError("outer ID lost its exact authoritative semantic member")
                pairs.append((member.schema_id, inner.schema_id))
            else:
                pairs.append((member.schema_id, None))
        if (tuple(pairs) != self.outer_inner_schema_ids
                or tuple(inner for _, inner in pairs if inner is not None)
                != self.semantic_index.expected_schema_ids):
            raise ValueError("incomplete/duplicate/current outer-inner relationship")

    def schema_value(self) -> dict[str, object]:
        self.require_current_offer(self.offers)
        return self.rule_graph.provider_schema_value()

    @property
    def schema_group_phase(self) -> LeadGroupPhase | None:
        self.require_current_offer(self.offers)
        return None if self.group_workflow is None else self.group_workflow.phase

    def _validate_response_structure(
        self, response_text: str,
    ) -> tuple:
        self.require_current_offer(self.offers)
        try:
            value = strict_json_object(response_text, exact_numbers=True)
        except (TypeError, ValueError) as failure:
            raise LeadContractRejection(
                "LEAD_JSON_REJECTED", None,
                failure_issues(failure) or ("$: expected strict JSON object",),
            ) from None
        return self._validate_response_value(value, response_text=response_text)

    def _validate_response_value(self, value: object, *, response_text: str | None = None) -> tuple:
        if type(value) is not dict or type(value.get("agent_action")) is not dict:
            raise LeadContractRejection("LEAD_ACTION_ENVELOPE_REJECTED", None,
                                        ("$: required agent_action object envelope",))
        action = value["agent_action"]
        action_root = self.action_index.policy.wire_root_path
        semantic_root = action_root + ".task_result"
        try:
            member = self.action_index.select(action.get("schema_id"))
        except ContractRejection as failure:
            raise remap_validation(failure, {"$": action_root}) from None
        try:
            response_size = (len(response_text.encode("utf-8", errors="strict")) if response_text is not None
                             else json_value_utf8_size(value, maximum_bytes=262_144))
        except (TypeError, ValueError, UnicodeError):
            raise LeadContractRejection("LEAD_ACTION_UTF8_REJECTED", member.schema_id,
                                        ("$: required strict UTF8 action encoding",)) from None
        if response_size > 262_144:
            raise LeadContractRejection("LEAD_ACTION_UTF8_LIMIT", member.schema_id,
                                        ("$: full action UTF8 limit 262144",))
        expected_inner = dict(self.outer_inner_schema_ids)[member.schema_id]
        if expected_inner is not None:
            if type(action.get("task_result")) is not dict or action["task_result"].get("schema_id") != expected_inner:
                raise remap_validation(
                    LeadContractRejection(
                        "LEAD_PAIRED_SCHEMA_ID_REJECTED", member.schema_id,
                        ("$.task_result.schema_id: required exact paired semantic ID",)),
                    {"$": action_root})
            if response_text is not None:
                literal = strict_json_member_text(response_text, ("agent_action", "task_result"))
                result_size = len(literal.encode("utf-8", errors="strict"))
            else:
                try:
                    result_size = json_value_utf8_size(action["task_result"], maximum_bytes=65_536)
                except (TypeError, ValueError, UnicodeError):
                    result_size = 65_537
            if result_size > 65_536:
                raise remap_validation(
                    LeadContractRejection(
                        "LEAD_RESULT_UTF8_LIMIT", member.schema_id,
                        ("$.task_result: semantic result UTF8 limit 65536",)),
                    {"$": action_root})
            if type(self.semantic_index.select(expected_inner).owner) is SchemaGroupControlOwner:
                findings = self.rule_graph.findings(value)
                if findings:
                    raise LeadContractRejection("LEAD_SCHEMA_RULE_REJECTED", member.schema_id, findings)
        try:
            action_value = member.validate_value(action)
        except ContractRejection as failure:
            raise remap_validation(failure, {"$": action_root}) from None
        inner, result_value = None, None
        if expected_inner is not None:
            inner = self.semantic_index.select(expected_inner)
            try:
                result_value = inner.validate_value(action_value.as_object()["task_result"])
            except ContractRejection as failure:
                raise remap_validation(failure, {"$": semantic_root}) from None
        return member, action_value, inner, result_value

    def structural_findings(self, response_text: str) -> tuple[str, ...]:
        """Return selected pure rule findings without decoding or host checks."""
        try:
            self._validate_response_structure(response_text)
        except ContractRejection as failure:
            return failure_issues(failure)
        return ()

    def decode_response(self, response_text: str) -> BoundCallDecodedAction:
        return self._decode_values(self._validate_response_structure(response_text))

    def decode_value(self, value: object) -> BoundCallDecodedAction:
        """Decode an existing JSON value with the same live binding and owner checks."""
        self.require_current_offer(self.offers)
        return self._decode_values(self._validate_response_value(value))

    def _decode_values(self, values: tuple) -> BoundCallDecodedAction:
        member, action_value, inner, result_value = values
        action_root = self.action_index.policy.wire_root_path
        semantic_root = action_root + ".task_result"
        try:
            selected = member.decode_validated(action_value)
        except ContractRejection as failure:
            raise remap_validation(failure, {"$": action_root}) from None
        result = None
        if inner is not None:
            try:
                result = inner.decode_validated(result_value)
            except ContractRejection as failure:
                raise remap_validation(failure, {"$": semantic_root}) from None
        decoded = object.__new__(BoundCallDecodedAction)
        object.__setattr__(decoded, "selected_action", selected)
        object.__setattr__(decoded, "selected_result", result)
        object.__setattr__(decoded, "_contract", self)
        return decoded

    def require_authorized_answer(self, decoded: BoundCallDecodedAction,
                                  authorization: ContextAnswerAuthorization) -> None:
        if (type(decoded) is not BoundCallDecodedAction or not decoded.belongs_to(self)
                or decoded.selected_result is None or type(decoded.selected_result.value) is not LeadDecodedResult):
            raise TypeError("authorized answer requires exact selected current call/result")
        self.semantic_index.require_host_checks(decoded.selected_result, phase="authorized",
                                                context=AuthorizedAnswerHostContext(authorization))

    def record_group_control(self, decoded: BoundCallDecodedAction) -> LeadGroupSelectionRecord:
        self.require_current_offer(self.offers)
        if (type(decoded) is not BoundCallDecodedAction or not decoded.belongs_to(self)
                or decoded.group_control is None or self.group_stage is None):
            raise TypeError("정확한 현재 bound 호출의 선택 제어만 기록할 수 있습니다")
        return LeadGroupSelectionRecord(self.group_stage.catalog, decoded.group_control,
                                        self.offers.call_id, decoded.selected_action.schema_id,
                                        decoded.selected_result.schema_id)

    def generation_binding(self) -> LeadGroupGenerationBinding:
        self.require_current_offer(self.offers)
        if self.group_workflow is None or self.group_workflow.phase is not LeadGroupPhase.GENERATE:
            raise TypeError("업무 결과 연결에는 현재 생성 국면이 필요합니다")
        members = tuple(member for member in self.semantic_index.contracts
                        if type(member.owner) is LeadResultContractOwner)
        pairs = tuple((self.action_index.select(outer), member)
                      for outer, inner in self.outer_inner_schema_ids
                      for member in members if inner == member.schema_id)
        return LeadGroupGenerationBinding(self.group_workflow.active_selection,
            self.group_stage.catalog, pairs, provided_contracts=self.semantic_index.contracts,
            current_offer=_canonical_bytes(self.offers.snapshot_value()),
            eligibility_offer=_canonical_bytes(self.eligibility_offers.snapshot_value()),
            provided_call_id=self.semantic_index.policy.physical_call_id,
            provided_offer_digest=self.semantic_index.policy.offer_digest,
            logical_origin_call_id=self.logical_origin_call_id, phase=self.phase)

    def bind_failure(self, failure: BaseException, decoded: BoundCallDecodedAction | None = None) -> LeadContractRejection:
        """Attach the actual frozen call; never recover authority from exception text."""
        selected_id = None
        if decoded is not None:
            if type(decoded) is not BoundCallDecodedAction or not decoded.belongs_to(self):
                raise TypeError("failure provenance requires exact selected current call")
            selected_id = (decoded.selected_result.schema_id if decoded.selected_result is not None
                           else decoded.selected_action.schema_id)
        bound_call = getattr(failure, "bound_call_contract", None)
        nested = getattr(failure, "bound_contract_failure", None)
        nested_call = getattr(nested, "bound_call_contract", None)
        if (bound_call is not None and bound_call is not self) or (
            nested_call is not None and nested_call is not self
        ):
            raise ValueError('failure changed its exact frozen physical contract')
        if isinstance(failure, LeadContractRejection):
            rejection = failure
        elif type(nested) is LeadContractRejection and nested_call is self:
            rejection = nested
        else:
            facts = tuple(item.path + ": " + item.requirement for item in failure_findings(failure))
            rejection = LeadContractRejection("LEAD_OWNER_RULE_REJECTED", selected_id,
                                              facts or ("$: current named owner requirements",))
        failure.bound_call_contract = self
        if failure is not rejection:
            failure.bound_contract_failure = rejection
        rejection.bound_call_contract = self
        return rejection

    def require_projection(
        self,
        native_schema: Mapping[str, object],
        *,
        declared_lowering: str = "identity",
    ) -> None:
        self.require_current_offer(self.offers)
        require_schema_projection(
            self.schema_value(), native_schema, declared_lowering=declared_lowering
        )

    def require_submission(
        self,
        payload: Mapping[str, object],
        native_schema: Mapping[str, object],
        *,
        declared_lowering: str = "identity",
    ) -> None:
        if self.group_stage is not None:
            current = SchemaSelectionInput.from_value(payload.get("response_contract", {}).get("schema_selection", {}))
            catalog = self.group_stage.catalog
            active = self.group_workflow.active_selection
            if (current.phase != self.schema_group_phase.value or current.call_id != self.offers.call_id
                    or current.contracts != self.outer_inner_schema_ids
                    or current.remaining_provider_calls != self.offers.remaining_provider_calls
                    or (json.loads(current.catalog) != catalog.as_object() if current.catalog is not None else current.catalog_id != catalog.catalog_id)
                    or current.active_selection != (None if active is None else (active.catalog.catalog_id, active.group_id))):
                raise ValueError("schema_selection does not match the exact current bound call")
            rules = payload.get("contract_guidance", {}).get("response_rules", [])
            if (rules.count(LEAD_GROUP_PHASE_GUIDES[current.phase]) != 1
                    or any(guide in rules for phase, guide in LEAD_GROUP_PHASE_GUIDES.items() if phase != current.phase)):
                raise ValueError("schema_selection current phase guide is invalid")
        self.require_projection(native_schema, declared_lowering=declared_lowering)
        input_json, _ = account_provider_payload(native_schema, payload)
        if len(input_json) > 262_144:
            raise SessionContextCapacityError("Lead final projected request exceeds existing capacity")

    def repair_lineage(self) -> BoundCallLineage:
        if self.phase in {"repair", "replay_repair"}:
            raise ValueError("one corrective request cannot grant another corrective request")
        return BoundCallLineage(self, "repair", seal=_CALL_PROOF_SEAL)

    def replay_lineage(self) -> BoundCallLineage:
        return BoundCallLineage(self, "replay", seal=_CALL_PROOF_SEAL)

    def corrective_input(self, failure: LeadContractRejection) -> str:
        self.require_current_offer(self.offers)
        if type(failure) is not LeadContractRejection:
            raise TypeError("corrective input requires trusted bound rejection")
        semantic_id = failure.schema_id if failure.schema_id in self.semantic_index.expected_schema_ids else None
        if semantic_id is not None:
            outer_id = next(outer for outer, inner in self.outer_inner_schema_ids if inner == semantic_id)
            outer_failure = LeadContractRejection(failure.code, outer_id, tuple(
                "$.task_result" + item[1:] for item in failure.requirements if item.startswith("$")))
        elif failure.schema_id in self.action_index.expected_schema_ids:
            outer_failure = failure
        else:
            outer_failure = LeadContractRejection(failure.code, None, ())
        payload = json.loads(self.action_index.repair_input(outer_failure))
        paired = dict(self.outer_inner_schema_ids).get(outer_failure.schema_id)
        if paired is not None:
            paths = failure.requirements if semantic_id is not None else ("$: selected semantic rules",)
            payload["paired_semantic_requirements"] = self.semantic_index.select(paired).corrective_requirements(paths)
        payload["current_physical_call_id"] = self.offers.call_id
        payload["current_offer_digest"] = self.offers.offer_digest
        payload["logical_origin_call_id"] = self.logical_origin_call_id
        payload["phase"] = self.phase
        return json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _bound_call_failure(failure: BaseException) -> tuple[BoundCallContract, LeadContractRejection]:
    current = failure
    seen = set()
    contract = None
    rejection = None
    while isinstance(current, BaseException) and id(current) not in seen:
        seen.add(id(current))
        found = getattr(current, "bound_call_contract", None)
        if type(found) is BoundCallContract:
            if contract is not None and found is not contract:
                raise ValueError("failure changed its exact frozen physical contract")
            contract = found
        if isinstance(current, LeadContractRejection):
            rejection = current
        nested = getattr(current, "bound_contract_failure", None)
        current = nested if isinstance(nested, BaseException) else current.__cause__
    if contract is None:
        raise ValueError("failure has no frozen current physical contract")
    if rejection is None:
        rejection = LeadContractRejection("LEAD_OWNER_RULE_REJECTED", None,
                                          ("$: current named owner requirements",))
    return contract, rejection


def _bound_action_index(
    eligible: ContextCallOffers,
    semantic: LeadContractIndex[LeadDecodedResult | SchemaGroupControl],
    actual: ContextCallOffers,
) -> LeadContractIndex[ContextActionValue]:
    templates = []
    for member in semantic.contracts:
        native = _context_action_schema(
            allowed_actions=eligible.allowed_actions,
            result_contract=member.rule_graph,
            allowed_search_source_kinds=eligible.search_source_kinds,
            action_limits=[value for _, value in eligible.action_limits],
            issued_handle_aliases=eligible.issued_handle_aliases,
            preserve_action_definitions=True,
        )
        action_rule = native["properties"]["agent_action"]
        roots = action_rule.get("anyOf", [action_rule])
        templates.append((member, roots))
    owners = []
    for number, branch in enumerate(templates[0][1]):
        terminal = branch["properties"]["action_type"]["enum"] == ["submit_result"]
        for captured_member, roots in templates if terminal else templates[:1]:
            member = captured_member if terminal else None
            current = hoist_schema_definitions(roots[number])
            current = rebase_schema_definitions(current, "a")
            rules = SchemaRules.declare(
                factor_rule_schema(hoist_schema_definitions(current)),
                maximum_bytes=262144,
            )
            name = "lead.action." + str(number) + (
                "." + member.schema_id if member is not None else ""
            )
            owners.append(ContextActionContractOwner(name, rules, eligible, member))
    policy = LeadStagePolicy(
        semantic.policy.stage + ".context_action",
        actual.call_id,
        semantic.policy.task_revisions,
        semantic.policy.language,
        actual.offer_digest,
        semantic.policy.choices,
        eligible.offer_digest,
        semantic.policy.logical_origin_call_id,
        maximum_schema_bytes=262144,
        maximum_index_schema_bytes=MAX_PROVIDER_SCHEMA_UTF8_BYTES,
        wire_root_path="$.agent_action",
        owner_declarations=tuple(owners),
    )
    return LeadContractIndex.bind(policy)


class ContextExecutionEventKind(StrEnum):
    """Content-free lifecycle facts emitted by contextual execution."""

    BOOTSTRAP = "bootstrap"
    DELTA = "delta"
    OMISSION = "omission"
    SEARCH_STARTED = "search_started"
    SEARCH_COMPLETED = "search_completed"
    READ_STARTED = "read_started"
    READ_COMPLETED = "read_completed"
    ANSWER_AUTHORIZED = "answer_authorized"
    INVALIDATED = "invalidated"
    BUDGET_EXHAUSTED = "budget_exhausted"
    ACTION_EXHAUSTED = "action_exhausted"
    TAINTED = "tainted"
    CHECKPOINT_ADVANCED = "checkpoint_advanced"
    CHECKPOINT_NOT_ADVANCED = "checkpoint_not_advanced"


@dataclass(frozen=True, slots=True)
class ContextExecutionEvent:
    """One typed lifecycle event containing exact content-free facts only."""

    kind: ContextExecutionEventKind
    scope: ContextScope
    operation_id: str
    phase: str | None = None
    round_number: int | None = None
    action_index: int | None = None
    selected_source_count: int | None = None
    selected_source_utf8_bytes: int | None = None
    omitted_searchable_source_count: int | None = None
    omitted_searchable_source_utf8_bytes: int | None = None
    search_hit_count: int | None = None
    retrieved_source_count: int | None = None
    retrieved_source_utf8_bytes: int | None = None
    mandatory_reference_count: int | None = None
    mandatory_reference_utf8_bytes: int | None = None
    provider_input_tokens: int | None = None
    history_record_count: int | None = None
    observation_utf8_bytes: int | None = None
    truncated: bool | None = None

    def __post_init__(self) -> None:
        if (
            type(self.kind) is not ContextExecutionEventKind
            or type(self.scope) is not ContextScope
        ):
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        _safe_identity(self.operation_id)
        if self.scope.subject_kind is ContextSubjectKind.DEBATE:
            if self.phase is None or self.round_number is None:
                raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
            _safe_identity(self.phase)
            if type(self.round_number) is not int or self.round_number < 0:
                raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        elif self.phase is not None or self.round_number is not None:
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if self.action_index is not None and (
            type(self.action_index) is not int or self.action_index < 1
        ):
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        for value in (
            self.selected_source_count,
            self.selected_source_utf8_bytes,
            self.omitted_searchable_source_count,
            self.omitted_searchable_source_utf8_bytes,
            self.search_hit_count,
            self.retrieved_source_count,
            self.retrieved_source_utf8_bytes,
            self.mandatory_reference_count,
            self.mandatory_reference_utf8_bytes,
            self.provider_input_tokens,
            self.history_record_count,
            self.observation_utf8_bytes,
        ):
            if value is not None and (type(value) is not int or value < 0):
                raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if self.truncated is not None and type(self.truncated) is not bool:
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        plan_facts = (
            self.selected_source_count,
            self.selected_source_utf8_bytes,
            self.omitted_searchable_source_count,
            self.omitted_searchable_source_utf8_bytes,
            self.mandatory_reference_count,
            self.mandatory_reference_utf8_bytes,
        )
        read_facts = (
            self.retrieved_source_count,
            self.retrieved_source_utf8_bytes,
            self.history_record_count,
            self.observation_utf8_bytes,
        )
        all_optional_facts = (
            *plan_facts,
            self.search_hit_count,
            *read_facts,
            self.provider_input_tokens,
            self.truncated,
        )
        if self.kind in {
            ContextExecutionEventKind.BOOTSTRAP,
            ContextExecutionEventKind.DELTA,
        }:
            if any(value is None for value in plan_facts) or any(
                value is not None
                for value in (
                    self.action_index,
                    self.search_hit_count,
                    *read_facts,
                    self.provider_input_tokens,
                    self.truncated,
                )
            ):
                raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
            return
        if self.kind is ContextExecutionEventKind.OMISSION:
            if (
                self.omitted_searchable_source_count is None
                or self.omitted_searchable_source_count < 1
                or self.omitted_searchable_source_utf8_bytes is None
                or any(
                    value is not None
                    for value in (
                        self.action_index,
                        self.selected_source_count,
                        self.selected_source_utf8_bytes,
                        self.search_hit_count,
                        *read_facts,
                        self.mandatory_reference_count,
                        self.mandatory_reference_utf8_bytes,
                        self.provider_input_tokens,
                        self.truncated,
                    )
                )
            ):
                raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
            return
        if self.kind in {
            ContextExecutionEventKind.SEARCH_STARTED,
            ContextExecutionEventKind.READ_STARTED,
            ContextExecutionEventKind.ANSWER_AUTHORIZED,
        }:
            if self.action_index is None or any(
                value is not None for value in all_optional_facts
            ):
                raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
            return
        if self.kind is ContextExecutionEventKind.SEARCH_COMPLETED:
            if (
                self.action_index is None
                or self.search_hit_count is None
                or self.observation_utf8_bytes is None
                or self.truncated is None
                or any(
                    value is not None
                    for value in (
                        *plan_facts,
                        self.retrieved_source_count,
                        self.retrieved_source_utf8_bytes,
                        self.provider_input_tokens,
                        self.history_record_count,
                    )
                )
            ):
                raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
            return
        if self.kind is ContextExecutionEventKind.READ_COMPLETED:
            if (
                self.action_index is None
                or any(value is None for value in read_facts)
                or any(
                    value is not None
                    for value in (
                        *plan_facts,
                        self.search_hit_count,
                        self.provider_input_tokens,
                        self.truncated,
                    )
                )
            ):
                raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
            return
        if self.kind in {
            ContextExecutionEventKind.CHECKPOINT_ADVANCED,
            ContextExecutionEventKind.CHECKPOINT_NOT_ADVANCED,
        }:
            if self.action_index is not None or any(
                value is not None
                for value in (
                    *plan_facts,
                    self.search_hit_count,
                    *read_facts,
                    self.truncated,
                )
            ):
                raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
            return
        if self.action_index is not None or any(
            value is not None for value in all_optional_facts
        ):
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)


ContextExecutionEventSink = Callable[[ContextExecutionEvent], None]


def _action_error(code: ContextActionErrorCode) -> ContextActionError:
    paths_and_rules = {
        ContextActionErrorCode.CORRELATION_MISMATCH: ("$", "action correlation must match the current request"),
        ContextActionErrorCode.INVALID_TRANSITION: ("$.kind", "action must be allowed by the current phase and completed read state"),
        ContextActionErrorCode.ACTION_LIMIT_EXCEEDED: ("$.kind", "action count must remain within the disclosed invocation budget"),
        ContextActionErrorCode.SEARCH_LIMIT_EXCEEDED: ("$.queries", "search count, query count, and requested results must fit the remaining search budget"),
        ContextActionErrorCode.READ_LIMIT_EXCEEDED: ("$.handles", "read count, handle count, and source bytes must fit the remaining read budget"),
        ContextActionErrorCode.OBSERVATION_LIMIT_EXCEEDED: ("$.handles", "selected source observations must fit the remaining context budget"),
        ContextActionErrorCode.HANDLE_INVALID: ("$.handles", "expected unconsumed source handles issued by this invocation's search"),
        ContextActionErrorCode.OUTSTANDING_READ: ("$.kind", "all outstanding read observations must be delivered before another action"),
        ContextActionErrorCode.ANSWER_NOT_AUTHORIZED: ("$.result", "answer requires completed mandatory reads and current source authorization"),
    }
    failure = ContextActionError(code)
    if code in paths_and_rules:
        path, rule = paths_and_rules[code]
        validation_failure(failure, path, rule)
    return failure


def _canonical_bytes(value: object) -> bytes:
    encoded: bytes | None = None
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeEncodeError):
        pass
    if encoded is None:
        raise _action_error(ContextActionErrorCode.INVALID_VALUE)
    return encoded


def _require_context_capacity(
    schema: Mapping[str, object],
    payload: Mapping[str, object],
    *,
    stage: str,
) -> None:
    """Check actual provider payload JSON without changing delivery or accounting."""
    payload_json, _ = account_provider_payload(schema, payload)
    actual_utf8_bytes = len(payload_json)
    if actual_utf8_bytes > MAX_SESSION_CONTEXT_UTF8_BYTES:
        raise SessionContextCapacityError(
            stage=stage, actual_utf8_bytes=actual_utf8_bytes,
        )


def _boundary_value(boundary: ContextSourceBoundary) -> tuple[object, ...]:
    return (
        boundary.kind.value,
        boundary.start,
        boundary.end,
        boundary.first_id,
        boundary.last_id,
    )


def _scope_value(scope: ContextScope) -> tuple[str, ...]:
    return (
        scope.workspace_id,
        scope.session_id,
        scope.subject_kind.value,
        scope.subject_id,
        scope.agent_id,
    )


def _entry_value(entry: ContextManifestEntry) -> tuple[object, ...]:
    return (
        entry.source_id,
        entry.source_kind.value,
        entry.semantic_channel.value,
        entry.priority.value,
        entry.ordinal,
        entry.revision,
        entry.content_digest,
        entry.provenance.authority_kind.value,
        entry.provenance.locator,
        entry.utf8_bytes,
        entry.searchable,
    )


def _manifest_fingerprint(manifest: ContextManifest) -> str:
    body = _canonical_bytes(
        [
            "context-action-manifest-v1",
            _scope_value(manifest.scope),
            *map(_entry_value, manifest.entries),
        ]
    )
    return f"sha256:{hashlib.sha256(body).hexdigest()}"


def _authorized_source_fingerprint(
    entries: tuple[ContextManifestEntry, ...],
) -> str:
    """Bind stable source identity and non-self-appending revision facts."""

    body = _canonical_bytes(
        [
            "context-zero-hit-authorized-sources-v1",
            [
                [
                    entry.source_id,
                    entry.source_kind.value,
                    (
                        None
                        if entry.source_kind is ContextSourceKind.CONVERSATION
                        else entry.revision
                    ),
                    (
                        None
                        if entry.source_kind is ContextSourceKind.CONVERSATION
                        else entry.content_digest
                    ),
                    entry.provenance.authority_kind.value,
                    entry.provenance.locator,
                    (
                        None
                        if entry.source_kind is ContextSourceKind.CONVERSATION
                        else entry.utf8_bytes
                    ),
                ]
                for entry in entries
            ],
        ]
    )
    return f"sha256:{hashlib.sha256(body).hexdigest()}"


def _digest(body: bytes) -> str:
    return f"sha256:{hashlib.sha256(body).hexdigest()}"


def _execution_policy_fingerprint(
    planning_policy: ContextPlanningPolicy,
    action_policy: ContextActionPolicy,
    source_policy: ContextSourcePolicy,
) -> str:
    """Bind durable execution state to wire and every effective policy value."""

    body = _canonical_bytes(
        [
            _CONTEXT_WIRE_VERSION,
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
    return _digest(body)


def _evidence_value(receipt: ContextEvidenceReceipt) -> tuple[object, ...]:
    return (
        receipt.evidence_id,
        receipt.handle.value,
        receipt.source_id,
        receipt.source_kind.value,
        receipt.revision,
        receipt.provenance.authority_kind.value,
        receipt.provenance.locator,
        receipt.source_digest,
        receipt.source_utf8_bytes,
        _boundary_value(receipt.boundary),
        receipt.boundary_digest,
        receipt.boundary_utf8_bytes,
        receipt.originating_search_action_index,
        receipt.completing_read_action_index,
    )


def _status_value(receipt: ContextSourceStatusReceipt) -> tuple[object, ...]:
    return (
        receipt.evidence_id,
        receipt.source_id,
        receipt.locator,
        receipt.status.value,
        receipt.ordinal,
        receipt.manifest_digest,
        receipt.revision,
        receipt.source_digest,
        receipt.source_utf8_bytes,
    )


def _zero_hit_value(receipt: ContextZeroHitSearchReceipt) -> tuple[object, ...]:
    return (
        receipt.evidence_id,
        receipt.correlation.action_index,
        receipt.manifest_digest,
        receipt.authorized_source_ids,
        tuple(item.value for item in receipt.authorized_source_kinds),
        receipt.authorized_source_fingerprint,
        receipt.artifact_path_prefix,
        receipt.query_digest,
        receipt.query_count,
    )


def _completed_evidence_digest(
    evidence: tuple[ContextEvidenceReceipt, ...],
) -> str:
    values = []
    for receipt in evidence:
        boundary = receipt.boundary
        values.append(
            {
                "boundary": {
                    "end": boundary.end,
                    "first_id": boundary.first_id,
                    "kind": boundary.kind.value,
                    "last_id": boundary.last_id,
                    "start": boundary.start,
                },
                "boundary_digest": receipt.boundary_digest,
                "boundary_utf8_bytes": receipt.boundary_utf8_bytes,
                "completing_read_action_index": (receipt.completing_read_action_index),
                "evidence_id": receipt.evidence_id,
                "handle": receipt.handle.value,
                "originating_search_action_index": (
                    receipt.originating_search_action_index
                ),
                "provenance": {
                    "authority_kind": receipt.provenance.authority_kind.value,
                    "locator": receipt.provenance.locator,
                },
                "revision": receipt.revision,
                "source_digest": receipt.source_digest,
                "source_id": receipt.source_id,
                "source_kind": receipt.source_kind.value,
                "source_utf8_bytes": receipt.source_utf8_bytes,
            }
        )
    return _digest(
        _canonical_bytes({"evidence": values, "kind": "completed_context_reads_v1"})
    )


def _reference_value(
    reference: MandatoryReferenceReservation,
) -> tuple[object, ...]:
    return (
        reference.ordinal,
        reference.reference_id,
        reference.source_id,
        reference.revision,
        reference.provenance.authority_kind.value,
        reference.provenance.locator,
        reference.content_digest,
        reference.utf8_bytes,
    )


def _safe_identity(value: object) -> str:
    validated: str | None = None
    try:
        validated = validate_context_identity(value)
    except ContextManifestError:
        pass
    if validated is None:
        raise _action_error(ContextActionErrorCode.INVALID_VALUE)
    return validated


def _safe_digest(value: object) -> str:
    validated: str | None = None
    try:
        validated = validate_context_digest(value)
    except ContextManifestError:
        pass
    if validated is None:
        raise _action_error(ContextActionErrorCode.INVALID_VALUE)
    return validated


class ContextExecutionErrorCode(StrEnum):
    """Stable content-free failures owned by contextual orchestration."""

    INVALID_VALUE = "invalid_value"
    PROVIDER_CONTRACT = "provider_contract"
    MALFORMED_ACTION = "malformed_action"
    ACTION_EXHAUSTED = "action_exhausted"
    CONTINUATION_MISMATCH = "continuation_mismatch"


class _ContextExecutionErrorDetail(StrEnum):
    """Private allow-list for fixed, content-free execution diagnostics."""

    READ_SOURCE_HANDLES_REQUIRED = "read_source_handles_required"
    RESPONSE_CORRELATION_MISMATCH = "response_correlation_mismatch"


_EXECUTION_ERROR_MESSAGES = {
    ContextExecutionErrorCode.INVALID_VALUE: "contextual invocation is invalid",
    ContextExecutionErrorCode.PROVIDER_CONTRACT: (
        "contextual provider contract is invalid"
    ),
    ContextExecutionErrorCode.MALFORMED_ACTION: (
        "contextual provider action is malformed"
    ),
    ContextExecutionErrorCode.ACTION_EXHAUSTED: (
        "contextual provider action limit is exhausted"
    ),
    ContextExecutionErrorCode.CONTINUATION_MISMATCH: (
        "contextual provider continuation does not match"
    ),
}

_EXECUTION_ERROR_DETAIL_MESSAGES = {
    (
        ContextExecutionErrorCode.MALFORMED_ACTION,
        _ContextExecutionErrorDetail.READ_SOURCE_HANDLES_REQUIRED,
    ): (
        "contextual provider read action is invalid: `handles` must contain "
        "source handles returned by the preceding context search"
    ),
    (
        ContextExecutionErrorCode.MALFORMED_ACTION,
        _ContextExecutionErrorDetail.RESPONSE_CORRELATION_MISMATCH,
    ): (
        "contextual provider response is invalid: `correlation` does not match "
        "the current request correlation token"
    ),
}


class ContextExecutionError(ValueError):
    """Report one fixed contextual lifecycle failure without story content."""

    def __init__(self, code: ContextExecutionErrorCode) -> None:
        if type(code) is not ContextExecutionErrorCode:
            raise TypeError(
                "context execution error code must be a ContextExecutionErrorCode"
            )
        self.code = code
        super().__init__(_EXECUTION_ERROR_MESSAGES[code])


def _execution_error(
    code: ContextExecutionErrorCode,
    *,
    detail: _ContextExecutionErrorDetail | None = None,
) -> ContextExecutionError:
    failure = ContextExecutionError(code)
    if detail is None:
        return failure
    if type(detail) is not _ContextExecutionErrorDetail:
        raise TypeError("context execution error detail is invalid")
    try:
        message = _EXECUTION_ERROR_DETAIL_MESSAGES[(code, detail)]
    except KeyError:
        raise TypeError("context execution error detail is invalid") from None
    failure.args = (message,)
    failure._detail = detail
    return failure


def _execution_error_has_detail(
    failure: ContextExecutionError,
    detail: _ContextExecutionErrorDetail,
) -> bool:
    """Recognize only one internally attached, unchanged fixed detail."""

    if (
        type(failure) is not ContextExecutionError
        or type(detail) is not _ContextExecutionErrorDetail
        or getattr(failure, "_detail", None) is not detail
    ):
        return False
    try:
        expected = _EXECUTION_ERROR_DETAIL_MESSAGES[(failure.code, detail)]
    except KeyError:
        return False
    return str(failure) == expected


_PROVIDER_SCHEMA_TYPES = frozenset(
    {"array", "boolean", "integer", "null", "number", "object", "string"}
)
_PROVIDER_SCHEMA_KEYS = frozenset(
    {
        "additionalProperties",
        "anyOf",
        "description",
        "enum",
        "items",
        "maxItems",
        "minItems",
        "properties",
        "required",
        "type",
    }
)


def _schema_type_matches(value: object, schema_type: str) -> bool:
    if schema_type == "null":
        return value is None
    if schema_type == "boolean":
        return type(value) is bool
    if schema_type == "integer":
        return type(value) is int
    if schema_type == "number":
        return type(value) is int or (type(value) is float and math.isfinite(value))
    if schema_type == "string":
        return type(value) is str
    if schema_type == "array":
        return type(value) is list
    if schema_type == "object":
        return type(value) is dict
    return False


def _schema_types(schema: dict[str, object]) -> tuple[str, ...]:
    raw = schema.get("type")
    if type(raw) is str:
        values = (raw,)
    elif type(raw) is list and all(type(item) is str for item in raw):
        values = tuple(raw)
    else:
        raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
    if (
        not values
        or len(set(values)) != len(values)
        or any(value not in _PROVIDER_SCHEMA_TYPES for value in values)
        or (
            len(values) > 1
            and (len(values) != 2 or "null" not in values)
        )
    ):
        raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
    return values


def _validate_provider_schema_node(schema: object) -> None:
    if not isinstance(schema, Mapping):
        raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
    try:
        canonical_schema_rules(schema, maximum_bytes=MAX_SESSION_CONTEXT_UTF8_BYTES)
    except (TypeError, ValueError, UnicodeError) as failure:
        raise carry_validation(_execution_error(ContextExecutionErrorCode.INVALID_VALUE), failure) from None



def _schema_accepts_only_objects(schema: dict[str, object]) -> bool:
    return schema_accepts_only_objects(schema)



def _validated_result_schema_bytes(schema: Mapping[str, object]) -> bytes:
    try:
        encoded = canonical_schema_rules(schema)
        value = strict_json_object(encoded.decode("utf-8"))
        def require_choice_descriptions(node: dict[str, object]) -> None:
            for branch in node.get("anyOf", ()):
                description = branch.get("description")
                if type(description) is not str or not description.strip():
                    raise validation_failure(
                        ValueError(), "$", "expected nonempty owner-authored choice description",
                    )
                require_choice_descriptions(branch)
            for keyword in ("properties", "$defs"):
                for child in node.get(keyword, {}).values():
                    require_choice_descriptions(child)
            if "items" in node:
                require_choice_descriptions(node["items"])

        require_choice_descriptions(value)
        if not schema_accepts_only_objects(value):
            raise ValueError("result object required")
        return encoded
    except (TypeError, ValueError, UnicodeError) as failure:
        raise carry_validation(_execution_error(ContextExecutionErrorCode.INVALID_VALUE), failure) from None



def _value_matches_provider_schema(value: object, schema: dict[str, object]) -> bool:
    return not diagnose_value(value, schema)



def _validate_sync_callback(value: object, *, optional: bool = True) -> None:
    if value is None and optional:
        return
    if not callable(value):
        raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
    call = getattr(value, "__call__", None)
    if (
        inspect.iscoroutinefunction(value)
        or inspect.isasyncgenfunction(value)
        or inspect.iscoroutinefunction(call)
        or inspect.isasyncgenfunction(call)
    ):
        raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)


def _require_sync_none(value: object) -> None:
    if inspect.isawaitable(value):
        if inspect.iscoroutine(value):
            value.close()
        raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
    if inspect.isasyncgen(value) or value is not None:
        raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)


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
    for accounting in accountings:
        usage = accounting.usage
        if usage is None or usage.input_tokens is None:
            return None
        total += usage.input_tokens
    return total


def _conversation_read_record_count(
    observation: ContextReadObservation,
) -> int:
    """Count readable collaboration records without retaining their bodies."""

    records = 0
    for evidence in observation.evidence:
        receipt = evidence.receipt
        if receipt.source_kind is not ContextSourceKind.CONVERSATION:
            continue
        if receipt.boundary.kind is ContextSourceBoundaryKind.RECORDS:
            assert receipt.boundary.start is not None
            assert receipt.boundary.end is not None
            records += receipt.boundary.end - receipt.boundary.start + 1
            continue
        if receipt.boundary.kind is not ContextSourceBoundaryKind.WHOLE:
            continue
        try:
            value = json.loads(evidence.content)
        except (json.JSONDecodeError, RecursionError, TypeError, UnicodeError):
            continue
        if type(value) is dict and type(value.get("records")) is list:
            records += len(value["records"])
    return records


@dataclass(frozen=True, slots=True)
class ContextSourceClassification:
    """Content-free priority/channel assignment for one inventoried source."""

    source_id: str
    semantic_channel: ContextSemanticChannel
    priority: ContextPriority
    ordinal: int
    allow_absent: bool = False

    def __post_init__(self) -> None:
        _safe_identity(self.source_id)
        if (
            type(self.semantic_channel) is not ContextSemanticChannel
            or type(self.priority) is not ContextPriority
            or self.priority
            not in {ContextPriority.P2_WORKING, ContextPriority.P3_OPTIONAL}
            or type(self.ordinal) is not int
            or self.ordinal < 0
            or type(self.allow_absent) is not bool
            or (
                self.allow_absent
                and (
                    self.priority is not ContextPriority.P2_WORKING
                    or self.semantic_channel
                    is not ContextSemanticChannel.HARD_CONSTRAINT
                )
            )
        ):
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)


@dataclass(frozen=True, slots=True)
class ContextDeliverySnapshot:
    """실제 한 입력의 논리 내용과 전송 근거를 보존하며 업무 승인은 소유하지 않는다."""

    request_basis: LeadGroupRequestBasis
    conditions: ContextPlanningConditions
    attempt_id: str
    ledger_generation: int
    call_id: str
    logical_context: bytes = field(repr=False)
    payload_json: bytes = field(repr=False)
    payload_sha256: str
    schema_sha256: str
    ledger_record: ContextLedgerRecord = field(repr=False)
    witness: ProviderContextWitness | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if (type(self.request_basis) is not LeadGroupRequestBasis
                or type(self.conditions) is not ContextPlanningConditions
                or type(self.logical_context) is not bytes
                or type(json.loads(self.logical_context)) is not dict
                or type(self.ledger_generation) is not int or self.ledger_generation < 0
                or self.witness is not None and type(self.witness) is not ProviderContextWitness):
            raise ValueError("context delivery snapshot is invalid")
        record = self.ledger_record
        if (type(record) is not ContextLedgerRecord or record.generation != self.ledger_generation
                or record.key.scope != self.conditions.scope or record.key.provider_id != self.conditions.provider_id
                or record.state is not ContextLedgerRecordState.INCOMPLETE or record.ready_checkpoint is not None
                or record.attempt is None or record.attempt.outcome is not ContextLedgerAttemptOutcome.IN_PROGRESS or record.attempt.plan.attempt_id != self.attempt_id
                or record.attempt.plan.operation_id != self.request_basis.operation_id
                or record.attempt.plan.result_correlation_id != self.request_basis.result_correlation_id
                or not record.attempt.provider_calls or record.attempt.provider_calls[-1].call_id != self.call_id
                or record.attempt.provider_calls[-1].accounting.state is not ProviderCallState.SUBMITTED):
            raise ValueError("delivery snapshot does not match its actual ledger append")
        _safe_identity(self.attempt_id)
        _safe_identity(self.call_id)
        for digest in (self.payload_sha256, self.schema_sha256):
            if type(digest) is not str or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise ValueError("context delivery snapshot digest is invalid")
        if (type(self.payload_json) is not bytes
                or hashlib.sha256(self.payload_json).hexdigest() != self.payload_sha256):
            raise ValueError("delivery snapshot payload is not the actual encoded input")
        selection = SchemaSelectionInput.from_value(json.loads(self.payload_json)["response_contract"]["schema_selection"])
        if selection.context_ref != self.context_ref or selection.call_id != self.call_id:
            raise ValueError("delivery snapshot logical input is not bound to the physical input")
        if self.witness is not None and (self.witness.call_id != self.call_id
                or self.witness.payload_sha256 != self.payload_sha256):
            raise ValueError("context delivery snapshot belongs to another physical input")

    def source_valid(self) -> bool:
        try:
            self.__post_init__()
            return True
        except (ValueError, TypeError, KeyError, AttributeError):
            return False

    def matches_ledger(self, record: ContextLedgerRecord | None, *, latest: bool = False) -> bool:
        """저장된 실제 append와 현재 authoritative 이력의 허용된 prefix만 비교한다."""
        if not self.source_valid():
            return False
        original = self.ledger_record
        if (type(record) is not ContextLedgerRecord or record.key != original.key
                or record.generation < original.generation or record.created_at != original.created_at
                or record.state not in {ContextLedgerRecordState.INCOMPLETE, ContextLedgerRecordState.TAINTED}
                or record.ready_checkpoint != original.ready_checkpoint or record.attempt is None
                or record.attempt.plan != original.attempt.plan
                or record.attempt.outcome not in {ContextLedgerAttemptOutcome.IN_PROGRESS,
                    ContextLedgerAttemptOutcome.MALFORMED, ContextLedgerAttemptOutcome.OUTPUT_INVALID}):
            return False
        calls = original.attempt.provider_calls
        return (record.attempt.provider_calls[:len(calls)] == calls
                and (not latest or len(record.attempt.provider_calls) == len(calls)))

    @property
    def context_ref(self) -> str:
        return "ctx_" + hashlib.sha256(self.logical_context).hexdigest()


@dataclass(frozen=True, slots=True)
class ContextGroupInvocationState:
    """한 논리 호출에서 선택 이력과 현재 준비 계획을 전달한다."""

    request_basis: LeadGroupRequestBasis
    workflow: LeadGroupWorkflow | None = None
    prepared_compilation: LeadGroupCompilation | None = field(default=None, repr=False)
    transport_delivery: ContextDeliverySnapshot | None = field(default=None, repr=False)
    accepted_delivery: ContextDeliverySnapshot | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if any(value is not None and type(value) is not ContextDeliverySnapshot for value in (self.transport_delivery, self.accepted_delivery)):
            raise TypeError("group delivery snapshot has an invalid type")
        if (type(self.request_basis) is not LeadGroupRequestBasis
                or (self.workflow is not None and type(self.workflow) is not LeadGroupWorkflow)
                or (self.prepared_compilation is not None
                    and type(self.prepared_compilation) is not LeadGroupCompilation)
                or ((self.workflow is None) != (self.prepared_compilation is None))):
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if self.workflow is not None:
            self.workflow.catalog.require_snapshot()
            self.prepared_compilation.require_provenance()


def _group_native_deliveries(
    state: ContextGroupInvocationState | None, conditions: ContextPlanningConditions,
    provider_thread_id: str | None,
) -> tuple[ContextDeliverySnapshot, ...]:
    """같은 요청·조건과 실제 native 계보를 가진 수락·전송 쌍만 반환한다."""
    if (state is None or state.workflow is None
            or state.workflow.phase not in {LeadGroupPhase.SELECT, LeadGroupPhase.GENERATE}
            or state.accepted_delivery is None or state.transport_delivery is None):
        return ()
    accepted, transport = state.accepted_delivery, state.transport_delivery
    for snapshot in (accepted, transport):
        if (snapshot.witness is None or snapshot.request_basis != state.request_basis
                or snapshot.conditions != conditions or not snapshot.source_valid()):
            return ()
    anchor, witness = accepted.witness, transport.witness
    if (witness.thread_id != provider_thread_id or anchor.thread_id != witness.thread_id
            or anchor.rollout_path != witness.rollout_path or anchor.rollout_id != witness.rollout_id
            or anchor.file_identity != witness.file_identity or anchor.scope_digest != witness.scope_digest
            or anchor.settings_digest != witness.settings_digest
            or anchor.prefix_utf8_bytes > witness.prefix_utf8_bytes
            or anchor.prefix_utf8_bytes == witness.prefix_utf8_bytes and anchor.prefix_sha256 != witness.prefix_sha256):
        return ()
    return accepted, transport


def _verified_group_deliveries(
    state: ContextGroupInvocationState | None, conditions: ContextPlanningConditions,
    record: ContextLedgerRecord | None, provider_thread_id: str | None, *,
    incoming: tuple[ContextDeliverySnapshot, ...] = (),
) -> tuple[ContextDeliverySnapshot, ...]:
    """실제 원장 prefix/latest 또는 reserve 전에 검증한 같은 쌍만 인계한다."""
    deliveries = _group_native_deliveries(state, conditions, provider_thread_id)
    if (not deliveries or type(record) is not ContextLedgerRecord or record.attempt is None
            or record.key.scope != conditions.scope or record.key.provider_id != conditions.provider_id
            or record.state not in {ContextLedgerRecordState.INCOMPLETE, ContextLedgerRecordState.TAINTED}
            or record.ready_checkpoint is not None):
        return ()
    for snapshot, latest in zip(deliveries, (False, True), strict=True):
        if snapshot.matches_ledger(record, latest=latest):
            continue
        original = snapshot.ledger_record
        if (not any(snapshot is item for item in incoming)
                or record.key != original.key or record.created_at != original.created_at
                or record.generation < original.generation
                or latest and record.attempt.provider_calls):
            return ()
    return deliveries


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
        _SEARCH_SOURCE_KIND_ORDER
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
        _safe_identity(self.session_id)
        if self.group_state is not None and (
            type(self.group_state) is not ContextGroupInvocationState
            or not isinstance(self.result_contract, BoundCallFactory)
            or self.agent_id != LEAD_ORCHESTRATOR
            or self.group_state.request_basis.operation_id != self.operation_id
            or self.group_state.request_basis.result_correlation_id != self.result_correlation_id
        ):
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if type(self.subject_kind) is not ContextSubjectKind:
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if self.subject_kind is ContextSubjectKind.SESSION:
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        # Reuse the canonical subject grammar without accepting workspace input.
        ContextScope(
            "workspace",
            self.session_id,
            self.subject_kind,
            self.subject_id,
            self.agent_id,
        )
        _safe_identity(self.operation_id)
        _safe_identity(self.result_correlation_id)
        if type(self.materials) is not tuple or any(
            type(item) is not ContextMaterial for item in self.materials
        ):
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if any(
            item.entry.source_kind
            not in {
                ContextSourceKind.RUNTIME_INPUT,
                ContextSourceKind.AGENT_DEFINITION,
            }
            for item in self.materials
        ):
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if type(self.source_classifications) is not tuple or any(
            type(item) is not ContextSourceClassification
            for item in self.source_classifications
        ):
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        source_ids = tuple(item.source_id for item in self.source_classifications)
        if len(set(source_ids)) != len(source_ids):
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if self.reference_operation_id is not None:
            _safe_identity(self.reference_operation_id)
        if type(self.mandatory_references) is not tuple or any(
            type(item) is not MandatoryReferenceReservation
            for item in self.mandatory_references
        ):
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if bool(self.reference_operation_id) is not bool(self.mandatory_references):
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if type(self.fresh) is not bool:
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if (
            type(self.allowed_search_source_kinds) is not tuple
            or not self.allowed_search_source_kinds
            or any(
                type(kind) is not ContextSourceKind
                or kind not in _SEARCH_SOURCE_KIND_ORDER
                for kind in self.allowed_search_source_kinds
            )
            or self.allowed_search_source_kinds
            != tuple(
                kind
                for kind in _SEARCH_SOURCE_KIND_ORDER
                if kind in self.allowed_search_source_kinds
            )
        ):
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if self.invocation_kind is not None and type(
            self.invocation_kind
        ) is not ContextInvocationKind:
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if type(self.result_contract) is not ContextJsonObjectResultContract and not isinstance(self.result_contract, BoundCallFactory):
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if self.physical_call_limit is not None and (
            type(self.physical_call_limit) is not int
            or self.physical_call_limit < 1
            or self.physical_call_limit > CONTEXT_ACTION_POLICY.max_provider_calls
        ):
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if type(self.include_core_statuses) is not bool:
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if self.session_state_json is not None:
            try:
                state = json.loads(self.session_state_json)
                if type(state) is not dict:
                    raise ValueError
                _canonical_bytes(state)
            except (TypeError, ValueError):
                raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE) from None
        if self.execution_workspace is not None and (
            type(self.execution_workspace) is not ProviderExecutionWorkspace
            or self.execution_workspace.session_id != self.session_id
        ):
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)


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

    def __post_init__(self) -> None:
        if self.group_state is not None and type(self.group_state) is not ContextGroupInvocationState:
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if self.continuation_rejection is not None and (
            type(self.continuation_rejection) is not ContextContinuationRejection
            or self.timed_out or self.submission_uncertain
        ):
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if self.accounting is not None and type(self.accounting) is not (
            ProviderCallAccounting
        ):
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if (
            type(self.timed_out) is not bool
            or type(self.taint_existing_thread) is not bool
            or type(self.submission_uncertain) is not bool
        ):
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if type(self.submitted_accountings) is not tuple or any(
            type(item) is not ProviderCallAccounting
            or item.state is not ProviderCallState.SUBMITTED
            for item in self.submitted_accountings
        ):
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if self.response_rejection_code is not None and (
            type(self.response_rejection_code) is not ContextProviderTerminalCode
            or self.response_rejection_code
            not in _CONTEXT_PROVIDER_RESPONSE_REJECTION_CODES
            or not self.submitted_accountings
        ):
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)


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
        raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
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


def _context_provider_decode_terminal_code(
    failure: ContextExecutionError,
) -> ContextProviderTerminalCode:
    """Classify one rejected decoded response without response content."""

    if _execution_error_has_detail(
        failure,
        _ContextExecutionErrorDetail.RESPONSE_CORRELATION_MISMATCH,
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
            raise _execution_error(ContextExecutionErrorCode.PROVIDER_CONTRACT)


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
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if self.continuation_identity is not None and (
            type(self.continuation_identity) is not ContextContinuationIdentity
            or not self.continuation_identity.accepts(self.scope)
        ):
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        _safe_digest(self.policy_fingerprint)
        _safe_identity(self.call_id)
        if type(self.call_kind) is not ProviderCallKind or self.call_kind in {
            ProviderCallKind.UNCLASSIFIED,
            ProviderCallKind.WRITING_ARTIFACT_REFERENCE_READ,
        }:
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if not isinstance(self.schema, Mapping) or not isinstance(
            self.payload,
            Mapping,
        ):
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if not isinstance(self.component_paths, Mapping):
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if type(self.mandatory_reference_paths) is not tuple:
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if self.expected_thread_id is not None:
            _safe_identity(self.expected_thread_id)
        if self.execution_workspace is not None and (
            type(self.execution_workspace) is not ProviderExecutionWorkspace
            or self.execution_workspace.session_id != self.scope.session_id
        ):
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
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

    def __post_init__(self) -> None:
        if self.context_witness is not None and (type(self.context_witness) is not ProviderContextWitness or self.context_witness.thread_id != self.thread_id):
            raise TypeError("context result witness belongs to another thread")
        if self.context_witness is not None and type(self.context_witness) is not ProviderContextWitness:
            raise TypeError("context provider witness has an invalid type")
        if type(self.response_text) is not str:
            raise _execution_error(ContextExecutionErrorCode.PROVIDER_CONTRACT)
        _safe_identity(self.thread_id)
        if (
            type(self.accounting) is not ProviderCallAccounting
            or self.accounting.state is not ProviderCallState.SUBMITTED
        ):
            raise _execution_error(ContextExecutionErrorCode.PROVIDER_CONTRACT)
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
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if self.continuation_identity is not None and (
            type(self.continuation_identity) is not ContextContinuationIdentity
            or not self.continuation_identity.accepts(self.scope)
        ):
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        _safe_digest(self.policy_fingerprint)
        _safe_identity(self.call_id)
        _safe_identity(self.reference_operation_id)
        if self.expected_thread_id is not None:
            _safe_identity(self.expected_thread_id)
        if self.execution_workspace is not None and (
            type(self.execution_workspace) is not ProviderExecutionWorkspace
            or self.execution_workspace.session_id != self.scope.session_id
        ):
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)


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
            raise _execution_error(ContextExecutionErrorCode.PROVIDER_CONTRACT)

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
            raise _execution_error(ContextExecutionErrorCode.PROVIDER_CONTRACT)
        correlation = result.request.correlation
        if request is not None and (
            correlation.operation_id != request.reference_operation_id
            or correlation.agent_id != request.scope.agent_id
        ):
            raise _execution_error(ContextExecutionErrorCode.PROVIDER_CONTRACT)
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
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if type(self.answer) is not str or not self.answer:
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if type(self.authorization) is not ContextAnswerAuthorization:
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if type(self.projection_accounting) is not ContextProjectionAccounting:
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        _safe_identity(self.provider_thread_id)
        if type(self.provider_calls) is not tuple or any(
            type(item) is not ContextLedgerProviderCall for item in self.provider_calls
        ):
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if type(self.ledger_generation) is not int or self.ledger_generation < 1:
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)


@dataclass(frozen=True, slots=True)
class ContextInvocationBinding:
    """Immutable scope, correlation, manifest, and pending reference facts."""

    scope: ContextScope
    operation_id: str
    result_correlation_id: str
    manifest: ContextManifest
    reference_operation_id: str | None = None
    mandatory_references: tuple[MandatoryReferenceReservation, ...] = ()

    def __post_init__(self) -> None:
        if (
            type(self.scope) is not ContextScope
            or type(self.operation_id) is not str
            or not self.operation_id
            or type(self.result_correlation_id) is not str
            or not self.result_correlation_id
            or type(self.manifest) is not ContextManifest
            or self.manifest.scope != self.scope
        ):
            raise _action_error(ContextActionErrorCode.INVALID_VALUE)
        _safe_identity(self.operation_id)
        _safe_identity(self.result_correlation_id)
        if self.reference_operation_id is not None:
            _safe_identity(self.reference_operation_id)
        if type(self.mandatory_references) is not tuple or any(
            type(item) is not MandatoryReferenceReservation
            for item in self.mandatory_references
        ):
            raise _action_error(ContextActionErrorCode.INVALID_VALUE)
        if tuple(item.ordinal for item in self.mandatory_references) != tuple(
            range(1, len(self.mandatory_references) + 1)
        ):
            raise _action_error(ContextActionErrorCode.INVALID_VALUE)
        if sum(item.utf8_bytes for item in self.mandatory_references) > (
            MAX_MANDATORY_REFERENCE_RESERVATION_UTF8_BYTES
        ):
            raise _action_error(ContextActionErrorCode.INVALID_VALUE)
        if bool(self.reference_operation_id) is not bool(self.mandatory_references):
            raise _action_error(ContextActionErrorCode.INVALID_VALUE)


@dataclass(frozen=True, slots=True)
class ContextReadReceipt:
    """Content-free receipt for one delivered ordered read observation."""

    correlation: ContextActionCorrelation
    evidence: tuple[ContextEvidenceReceipt, ...]
    observation_digest: str
    observation_utf8_bytes: int

    def __post_init__(self) -> None:
        if type(self.correlation) is not ContextActionCorrelation:
            raise _action_error(ContextActionErrorCode.INVALID_VALUE)
        if (
            type(self.evidence) is not tuple
            or not self.evidence
            or any(type(item) is not ContextEvidenceReceipt for item in self.evidence)
        ):
            raise _action_error(ContextActionErrorCode.INVALID_VALUE)
        if len({item.handle.value for item in self.evidence}) != len(self.evidence):
            raise _action_error(ContextActionErrorCode.INVALID_VALUE)
        if (
            type(self.observation_digest) is not str
            or not self.observation_digest.startswith("sha256:")
            or len(self.observation_digest) != 71
            or any(
                character not in "0123456789abcdef"
                for character in self.observation_digest[7:]
            )
            or type(self.observation_utf8_bytes) is not int
            or self.observation_utf8_bytes < 0
            or self.observation_utf8_bytes
            > CONTEXT_ACTION_POLICY.max_observation_utf8_bytes
        ):
            raise _action_error(ContextActionErrorCode.INVALID_VALUE)


@dataclass(frozen=True, slots=True)
class _HandleBinding:
    handle: ContextSourceHandle
    entry: ContextManifestEntry
    boundary: ContextSourceBoundary
    origin_action_index: int
    safe_locator: str


class ContextInvocationSession:
    """Own one synchronized invocation-local action and evidence lifecycle."""

    def __init__(
        self,
        binding: ContextInvocationBinding,
        sources: ContextSourceRegistry,
        *,
        action_policy: ContextActionPolicy = CONTEXT_ACTION_POLICY,
        source_policy: ContextSourcePolicy = CONTEXT_SOURCE_POLICY,
        source_aliases: Mapping[str, str] | None = None,
        allowed_search_source_kinds: tuple[ContextSourceKind, ...] = (
            _SEARCH_SOURCE_KIND_ORDER
        ),
        physical_call_limit: int | None = None,
        core_statuses: tuple[ContextSourceStatusReceipt, ...] = (),
    ) -> None:
        if (
            type(binding) is not ContextInvocationBinding
            or type(sources) is not ContextSourceRegistry
            or sources.scope != binding.scope
            or type(action_policy) is not ContextActionPolicy
            or type(source_policy) is not ContextSourcePolicy
            or type(allowed_search_source_kinds) is not tuple
            or not allowed_search_source_kinds
            or any(
                type(kind) is not ContextSourceKind
                or kind not in _SEARCH_SOURCE_KIND_ORDER
                for kind in allowed_search_source_kinds
            )
            or allowed_search_source_kinds
            != tuple(
                kind
                for kind in _SEARCH_SOURCE_KIND_ORDER
                if kind in allowed_search_source_kinds
            )
            or (
                physical_call_limit is not None
                and (
                    type(physical_call_limit) is not int
                    or physical_call_limit < 1
                    or physical_call_limit > action_policy.max_provider_calls
                )
            )
            or type(core_statuses) is not tuple
            or any(
                type(item) is not ContextSourceStatusReceipt for item in core_statuses
            )
        ):
            raise _action_error(ContextActionErrorCode.INVALID_VALUE)
        if source_aliases is not None:
            aliases = dict(source_aliases)
            readable_ids = {
                entry.source_id
                for entry in binding.manifest.entries
                if context_source_uses_catalog_alias(entry)
            }
            if (
                set(aliases) != readable_ids
                or any(
                    type(alias) is not str
                    or not alias.startswith("s")
                    or not alias[1:].isdigit()
                    or alias[1:].startswith("0")
                    for alias in aliases.values()
                )
                or len(set(aliases.values())) != len(aliases)
            ):
                raise _action_error(ContextActionErrorCode.INVALID_VALUE)
            self._source_aliases: dict[str, str] | None = aliases
        else:
            self._source_aliases = None
        self._allowed_search_source_kinds = allowed_search_source_kinds
        self._physical_call_limit = (
            action_policy.max_provider_calls
            if physical_call_limit is None
            else physical_call_limit
        )
        self._remaining_provider_calls: int | None = None
        self._minimum_completion_calls = 1
        self.binding = binding
        self._sources = sources
        self._action_policy = action_policy
        self._source_policy = source_policy
        secret: bytes | None = None
        try:
            secret = secrets.token_bytes(32)
        except (OSError, RuntimeError, ValueError):
            pass
        if type(secret) is not bytes or len(secret) != 32:
            raise _action_error(ContextActionErrorCode.INVALID_VALUE)
        self._secret: bytearray | None = bytearray(secret)
        self._manifest_fingerprint = _manifest_fingerprint(binding.manifest)
        if any(
            item.manifest_digest != self._manifest_fingerprint for item in core_statuses
        ):
            raise _action_error(ContextActionErrorCode.INVALID_VALUE)
        self._core_statuses = core_statuses
        self._lock = threading.RLock()
        self._state = ContextInvocationState.OPEN
        self._next_action_index = 1
        self._nonterminal_actions = 0
        self._search_actions = 0
        self._read_actions = 0
        self._queries = 0
        self._read_handles = 0
        self._hits = 0
        self._scan_utf8_bytes = 0
        self._read_utf8_bytes = 0
        self._observation_utf8_bytes = 0
        self._source_read_utf8_bytes: dict[str, int] = {}
        self._handles: dict[str, _HandleBinding] = {}
        self._wire_handle_by_full: dict[str, str] = {}
        self._full_handle_by_wire: dict[str, str] = {}
        self._safe_locator_by_full: dict[str, str] = {}
        self._completed_receipts: list[ContextReadReceipt] = []
        self._zero_hit_receipts: list[ContextZeroHitSearchReceipt] = []
        self._pending_receipt: ContextReadReceipt | None = None

    @property
    def state(self) -> ContextInvocationState:
        """Return the current call-local lifecycle state."""

        with self._lock:
            return self._state

    @property
    def next_action_index(self) -> int:
        """Return the next exact substantive provider-call ordinal."""

        with self._lock:
            return self._next_action_index

    def contract_offers(
        self, call_id: str, *, allowed_actions: tuple[str, ...],
        delivery_read: ContextReadReceipt | None = None,
        reference_prelude: ContextReferencePreludeResult | None = None,
        reference_request: ContextReferencePreludeRequest | None = None,
    ) -> ContextCallOffers:
        with self._lock:
            if delivery_read is not None and delivery_read is not self._pending_receipt:
                raise _execution_error(ContextExecutionErrorCode.PROVIDER_CONTRACT)
            reference_facts = ()
            reference_digest = None
            if self.binding.mandatory_references:
                if (type(reference_prelude) is not ContextReferencePreludeResult
                        or type(reference_request) is not ContextReferencePreludeRequest
                        or reference_prelude._prelude_request is not reference_request
                        or reference_request.scope != self.binding.scope
                        or reference_request.reference_operation_id
                        != self.binding.reference_operation_id
                        or type(reference_prelude._read_request) is not ReferenceReadRequest):
                    raise _execution_error(ContextExecutionErrorCode.PROVIDER_CONTRACT)
                read_request = reference_prelude._read_request
                correlation = read_request.correlation
                proofs = reference_prelude._authorized_references
                reference_facts = reference_prelude.read_facts
                reservations = self.binding.mandatory_references
                if (correlation.operation_id != self.binding.reference_operation_id
                        or correlation.agent_id != self.binding.scope.agent_id
                        or len(proofs) != len(reservations)
                        or len(reference_facts) != len(reservations)
                        or len(read_request.references) != len(reservations)):
                    raise _execution_error(ContextExecutionErrorCode.PROVIDER_CONTRACT)
                snapshots = []
                for proof, fact, reservation, reference in zip(
                    proofs, reference_facts, reservations, read_request.references, strict=True,
                ):
                    if (type(proof) is not AuthorizedArtifactReference
                            or type(fact) is not ContextCallReadFact
                            or proof.ordinal != reservation.ordinal
                            or proof.reference != reference
                            or proof.source.path != reference.path
                            or reference.token != reservation.provenance.locator
                            or f"sha256:{proof.source.source_sha256}" != reservation.revision
                            or f"sha256:{proof.selected_sha256}" != reservation.content_digest
                            or proof.selected_utf8_bytes != reservation.utf8_bytes
                            or fact.evidence_id != reservation.reference_id
                            or fact.source_id != reservation.source_id
                            or fact.source_kind is not ContextSourceKind.WRITING_ARTIFACT
                            or fact.revision != reservation.revision
                            or fact.source_digest != reservation.revision
                            or fact.boundary_digest != reservation.content_digest
                            or fact.whole_source is not (reference.line_range is None)
                            or fact.artifact_locator != reference.path):
                        raise _execution_error(ContextExecutionErrorCode.PROVIDER_CONTRACT)
                    source = proof.source
                    bounds = (None if reference.line_range is None else
                              (reference.line_range.start, reference.line_range.end))
                    snapshots.append((
                        proof.ordinal, reference.path, bounds,
                        (source.path, source.kind.value, source.source_sha256,
                         source.source_utf8_bytes, source.device_id, source.inode,
                         source.modified_ns, source.changed_ns),
                        proof.selected_sha256, proof.selected_utf8_bytes, proof.source_line_count,
                    ))
                reference_digest = _digest(_canonical_bytes((
                    "context-reference-read-v1", _scope_value(self.binding.scope),
                    self.binding.operation_id, self.binding.result_correlation_id,
                    reference_request.policy_fingerprint, reference_request.call_id,
                    (correlation.operation_id, correlation.invocation_id,
                     correlation.invocation_ordinal, correlation.agent_id, correlation.phase,
                     correlation.round_number, correlation.attempt),
                    snapshots,
                )))
            elif reference_prelude is not None or reference_request is not None:
                raise _execution_error(ContextExecutionErrorCode.PROVIDER_CONTRACT)
            names = ("nonterminal_actions", "search_actions", "read_actions", "queries_per_search",
                     "cumulative_queries", "handles_per_read", "cumulative_handles", "query_characters")
            limits = tuple(zip(names, self.action_limits()))
            completed = reference_facts + tuple(ContextCallReadFact.from_evidence(evidence)
                for receipt in self._completed_receipts for evidence in receipt.evidence)
            delivery = (() if delivery_read is None else tuple(
                ContextCallReadFact.from_evidence(evidence) for evidence in delivery_read.evidence))
            offer = ContextCallOffers(
                call_id, self.binding.scope, self._next_action_index, allowed_actions, limits,
                self._allowed_search_source_kinds, tuple(self._full_handle_by_wire),
                completed, delivery, self._core_statuses, remaining_provider_calls=self._remaining_provider_calls)
            if reference_digest is not None:
                object.__setattr__(offer, "reference_read_digest", reference_digest)
                offer.__post_init__()
            return offer

    @property
    def completed_handles(self) -> tuple[ContextSourceHandle, ...]:
        """Return locally completed READ handles in deterministic receipt order."""

        with self._lock:
            return tuple(
                evidence.handle
                for receipt in self._completed_receipts
                for evidence in receipt.evidence
            )

    def _handle_message(
        self,
        entry: ContextManifestEntry,
        boundary: ContextSourceBoundary,
        origin_action_index: int,
    ) -> bytes:
        return _canonical_bytes(
            [
                "context-source-handle-v1",
                _scope_value(self.binding.scope),
                self.binding.operation_id,
                self.binding.result_correlation_id,
                self._manifest_fingerprint,
                _entry_value(entry),
                _boundary_value(boundary),
                origin_action_index,
            ]
        )

    def _build_handle_binding(
        self,
        entry: ContextManifestEntry,
        boundary: ContextSourceBoundary,
        *,
        origin_action_index: int,
        safe_locator: str,
    ) -> _HandleBinding:
        if self._secret is None:
            raise _action_error(ContextActionErrorCode.INVALID_TRANSITION)
        digest = hmac.new(
            self._secret,
            self._handle_message(entry, boundary, origin_action_index),
            hashlib.sha256,
        ).hexdigest()
        return _HandleBinding(
            ContextSourceHandle(f"ctxh_v1_{digest}"),
            entry,
            boundary,
            origin_action_index,
            safe_locator,
        )

    def _register_handle(self, binding: _HandleBinding) -> None:
        existing = self._handles.get(binding.handle.value)
        if existing is not None and existing != binding:
            raise _action_error(ContextActionErrorCode.HANDLE_INVALID)
        self._handles[binding.handle.value] = binding
        self._safe_locator_by_full[binding.handle.value] = binding.safe_locator

    def _register_wire_handle(self, handle: ContextSourceHandle) -> str:
        """Return the invocation alias for one already authorized full handle."""

        existing = self._wire_handle_by_full.get(handle.value)
        if existing is not None:
            return existing
        alias = self._wire_handle_alias(handle)
        collision = self._full_handle_by_wire.get(alias)
        if collision is not None and not hmac.compare_digest(
            collision,
            handle.value,
        ):
            raise _action_error(ContextActionErrorCode.HANDLE_INVALID)
        self._wire_handle_by_full[handle.value] = alias
        self._full_handle_by_wire[alias] = handle.value
        return alias

    def _wire_handle_alias(self, handle: ContextSourceHandle) -> str:
        """Derive one invocation-bound 96-bit opaque provider handle."""

        if self._secret is None:
            raise _action_error(ContextActionErrorCode.INVALID_TRANSITION)
        digest = hmac.new(
            self._secret,
            b"context-wire-handle-alias-v5\x00"
            + handle.value.encode("ascii", errors="strict"),
            hashlib.sha256,
        ).digest()[:12]
        return "h" + base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

    def _preview_handle_aliases(
        self,
        pending: tuple[_HandleBinding, ...],
    ) -> dict[str, str]:
        """Project exact aliases for a not-yet-committed search observation."""

        aliases = dict(self._wire_handle_by_full)
        for binding in pending:
            if binding.handle.value not in aliases:
                alias = self._wire_handle_alias(binding.handle)
                if alias in aliases.values():
                    raise _action_error(ContextActionErrorCode.HANDLE_INVALID)
                aliases[binding.handle.value] = alias
        return aliases

    def _preview_safe_locators(
        self,
        pending: tuple[_HandleBinding, ...],
    ) -> dict[str, str]:
        locators = dict(self._safe_locator_by_full)
        locators.update(
            (binding.handle.value, binding.safe_locator) for binding in pending
        )
        return locators

    def resolve_wire_handle(self, value: object) -> ContextSourceHandle:
        """Resolve one emitted invocation alias to its full opaque authority."""

        if self._source_aliases is None:
            return ContextSourceHandle(value)
        candidate = "" if type(value) is not str else value
        matched: str | None = None
        for alias in self._full_handle_by_wire:
            if hmac.compare_digest(alias, candidate):
                matched = alias
        if matched is None:
            raise _action_error(ContextActionErrorCode.HANDLE_INVALID)
        return ContextSourceHandle(self._full_handle_by_wire[matched])

    def progress_sources(
        self,
        handles: tuple[ContextSourceHandle, ...],
        *,
        utf8_bytes: Mapping[str, int] | None = None,
    ) -> tuple[ContextActionProgressSource, ...]:
        """Describe authorized handles without exposing their opaque authority."""

        with self._lock:
            bindings = tuple(self._lookup_handle(handle) for handle in handles)
            return tuple(
                ContextActionProgressSource(
                    source_kind=binding.entry.source_kind,
                    locator=binding.safe_locator,
                    boundary=binding.boundary,
                    utf8_bytes=(
                        None
                        if utf8_bytes is None
                        else utf8_bytes.get(binding.handle.value)
                    ),
                )
                for binding in bindings
            )

    def serialize_observation(
        self,
        observation: ContextSearchObservation | ContextReadObservation,
    ) -> str:
        """Serialize one observation with this invocation's compact aliases."""

        if self._source_aliases is None:
            return serialize_context_action_observation(observation)
        return serialize_context_action_observation(
            observation,
            source_aliases=self._source_aliases,
            handle_aliases=self._wire_handle_by_full,
            safe_locators=self._safe_locator_by_full,
        )

    def source_alias(self, source_id: str) -> str:
        """Return the compact source alias configured for this invocation."""

        if self._source_aliases is None:
            return source_id
        alias = self._source_aliases.get(source_id)
        if alias is None:
            raise _action_error(ContextActionErrorCode.HANDLE_INVALID)
        return alias

    @property
    def core_statuses(self) -> tuple[ContextSourceStatusReceipt, ...]:
        """Return the invocation-bound content-free canonical core statuses."""

        return self._core_statuses

    def wire_handle_alias(self, handle: ContextSourceHandle) -> str:
        """Return an already-emitted compact alias for one full handle."""

        if self._source_aliases is None:
            return handle.value
        alias = self._wire_handle_by_full.get(handle.value)
        if alias is None:
            raise _action_error(ContextActionErrorCode.HANDLE_INVALID)
        return alias

    def _lookup_handle(self, handle: ContextSourceHandle) -> _HandleBinding:
        matched: str | None = None
        for candidate in self._handles:
            if hmac.compare_digest(candidate, handle.value):
                matched = candidate
        if matched is None:
            raise _action_error(ContextActionErrorCode.HANDLE_INVALID)
        return self._handles[matched]

    def _require_open(self) -> None:
        if self._state is ContextInvocationState.READING:
            raise _action_error(ContextActionErrorCode.OUTSTANDING_READ)
        if self._state is not ContextInvocationState.OPEN:
            raise _action_error(ContextActionErrorCode.INVALID_TRANSITION)

    def _validate_correlation(self, correlation: ContextActionCorrelation) -> None:
        if (
            type(correlation) is not ContextActionCorrelation
            or correlation.scope != self.binding.scope
            or correlation.operation_id != self.binding.operation_id
            or correlation.result_correlation_id != self.binding.result_correlation_id
            or correlation.action_index != self._next_action_index
        ):
            raise _action_error(ContextActionErrorCode.CORRELATION_MISMATCH)
        if correlation.action_index > self._physical_call_limit:
            raise _action_error(ContextActionErrorCode.ACTION_LIMIT_EXCEEDED)

    def _require_nonterminal_capacity(self) -> None:
        if self._nonterminal_actions >= self._action_policy.max_nonterminal_actions:
            raise _action_error(ContextActionErrorCode.ACTION_LIMIT_EXCEEDED)

    def advance_group_control(self, correlation: ContextActionCorrelation) -> None:
        """선택 제어의 순번만 진행하고 업무·컨텍스트 권한은 만들지 않는다."""
        with self._lock:
            self._require_open()
            self._validate_correlation(correlation)
            self._next_action_index += 1

    def _fail_locked(self) -> None:
        self._state = ContextInvocationState.FAILED
        self._clear_sensitive_locked()

    def _clear_sensitive_locked(self) -> None:
        if self._secret is not None:
            for index in range(len(self._secret)):
                self._secret[index] = 0
            self._secret = None
        self._handles.clear()
        self._wire_handle_by_full.clear()
        self._full_handle_by_wire.clear()
        self._safe_locator_by_full.clear()
        self._pending_receipt = None
        self._completed_receipts.clear()

    def set_remaining_provider_calls(
        self, remaining: int, *, minimum_completion_calls: int = 1,
    ) -> None:
        """실제 호출 원장에서 계산한 잔여량과 현재 국면의 완료 비용만 반영한다."""
        with self._lock:
            if (type(remaining) is not int or remaining < 0 or remaining > self._physical_call_limit
                    or type(minimum_completion_calls) is not int or minimum_completion_calls < 1
                    or (self._remaining_provider_calls is not None
                        and remaining > self._remaining_provider_calls)):
                raise _action_error(ContextActionErrorCode.INVALID_VALUE)
            self._remaining_provider_calls = remaining
            self._minimum_completion_calls = minimum_completion_calls

    @property
    def remaining_provider_calls(self) -> int:
        with self._lock:
            return (max(0, self._physical_call_limit - self._next_action_index + 1)
                    if self._remaining_provider_calls is None else self._remaining_provider_calls)

    def allowed_actions(
        self,
        *,
        after: str | None,
        search_had_hits: bool = False,
    ) -> tuple[str, ...]:
        """Return the one authoritative action tuple for the next call."""

        with self._lock:
            if (
                after not in {None, "search", "read"}
                or type(search_had_hits) is not bool
            ):
                raise _action_error(ContextActionErrorCode.INVALID_VALUE)
            nonterminal_capacity = (
                (self._physical_call_limit - self._next_action_index
                 if self._remaining_provider_calls is None
                 else self._remaining_provider_calls - self._minimum_completion_calls) > 0
                and self._nonterminal_actions < self._action_policy.max_nonterminal_actions
            )
            if not nonterminal_capacity:
                return ("answer",)
            can_search = bool(
                self._search_actions < self._action_policy.max_search_actions
                and self._queries < self._action_policy.max_cumulative_queries
                and self._allowed_search_source_kinds
            )
            can_read = bool(
                self._read_actions < self._action_policy.max_read_actions
                and self._read_handles < self._action_policy.max_cumulative_handles
                and self._handles
            )
            actions: list[str] = []
            if can_search:
                actions.append("search")
            if can_read and (
                (after == "search" and search_had_hits) or after == "read"
            ):
                actions.append("read")
            actions.append("answer")
            return tuple(actions)

    def action_limits(self) -> list[int]:
        """Return exact remaining dynamic limits for the next wire call."""

        with self._lock:
            return [
                max(
                    0,
                    min(
                        self._action_policy.max_nonterminal_actions
                        - self._nonterminal_actions,
                        (self._physical_call_limit - self._next_action_index
                     if self._remaining_provider_calls is None else max(
                         0, self._remaining_provider_calls - self._minimum_completion_calls)),
                    ),
                ),
                max(
                    0,
                    self._action_policy.max_search_actions - self._search_actions,
                ),
                max(
                    0,
                    self._action_policy.max_read_actions - self._read_actions,
                ),
                self._action_policy.max_queries_per_search,
                max(
                    0,
                    self._action_policy.max_cumulative_queries - self._queries,
                ),
                self._action_policy.max_handles_per_read,
                max(
                    0,
                    self._action_policy.max_cumulative_handles - self._read_handles,
                ),
                self._action_policy.max_query_characters,
            ]

    @property
    def allowed_search_source_kinds(self) -> tuple[ContextSourceKind, ...]:
        """Return the immutable route-specific search authority."""

        return self._allowed_search_source_kinds

    def _record_failed_attempt_locked(
        self,
        action_kind: str,
        *,
        queries: int = 0,
    ) -> None:
        self._next_action_index += 1
        if action_kind == "search":
            self._nonterminal_actions += 1
            self._search_actions += 1
            self._queries += queries
        elif action_kind == "read":
            self._nonterminal_actions += 1
            self._read_actions += 1

    def fail(self) -> None:
        """Terminally invalidate this invocation and discard its handle table."""

        with self._lock:
            self._fail_locked()

    @staticmethod
    def _search_safe_locator(
        entry: ContextManifestEntry,
        record_content: str,
    ) -> str:
        """Retain own-state debate identity in an otherwise canonical locator."""

        locator = entry.provenance.locator
        if (
            entry.source_kind is ContextSourceKind.WRITING_ARTIFACT
            and locator.startswith("manuscript/")
        ):
            return locator.removeprefix("manuscript/")
        if entry.source_kind is not ContextSourceKind.OWN_AGENT_STATE:
            return locator
        try:
            value = json.loads(record_content)
            debate_id = _safe_identity(value.get("debate_id"))
        except (
            AttributeError,
            ContextActionError,
            json.JSONDecodeError,
            TypeError,
            ValueError,
        ):
            raise _action_error(ContextActionErrorCode.HANDLE_INVALID) from None
        return f"{locator}/{debate_id}"

    def _search_manifest(
        self,
        artifact_path_prefix: str | None,
    ) -> ContextManifest:
        """Filter one shared artifact-prefix scope before any source scan."""

        if artifact_path_prefix is None:
            return self.binding.manifest
        return ContextManifest(
            self.binding.scope,
            tuple(
                entry
                for entry in self.binding.manifest.entries
                if entry.source_kind is ContextSourceKind.WRITING_ARTIFACT
                and entry.provenance.locator.startswith(artifact_path_prefix)
            ),
        )

    def search(self, action: ContextSearchAction) -> ContextSearchObservation:
        """Execute one bounded search and atomically retain its emitted handles."""

        with self._lock:
            executed = False
            try:
                self._require_open()
                if type(action) is not ContextSearchAction:
                    raise _action_error(ContextActionErrorCode.INVALID_VALUE)
                self._validate_correlation(action.correlation)
                self._require_nonterminal_capacity()
                if self._search_actions >= self._action_policy.max_search_actions:
                    raise _action_error(ContextActionErrorCode.SEARCH_LIMIT_EXCEEDED)
                if len(action.queries) > self._action_policy.max_queries_per_search:
                    raise _action_error(ContextActionErrorCode.SEARCH_LIMIT_EXCEEDED)
                if any(
                    len(query.text) > self._action_policy.max_query_characters
                    or len(query.normalized_text)
                    > self._action_policy.max_query_characters
                    or any(
                        kind not in self._allowed_search_source_kinds
                        for kind in query.source_kinds
                    )
                    for query in action.queries
                ):
                    raise _action_error(ContextActionErrorCode.SEARCH_LIMIT_EXCEEDED)
                if self._queries + len(action.queries) > (
                    self._action_policy.max_cumulative_queries
                ):
                    raise _action_error(ContextActionErrorCode.SEARCH_LIMIT_EXCEEDED)
                executed = True
                self._state = ContextInvocationState.SEARCHING
                remaining_scan = (
                    self._source_policy.max_cumulative_scan_utf8_bytes
                    - self._scan_utf8_bytes
                )
                matches, truncated, scanned = self._sources.search(
                    self._search_manifest(action.queries[0].artifact_path_prefix),
                    action.queries,
                    source_policy=self._source_policy,
                    action_policy=self._action_policy,
                    remaining_scan_utf8_bytes=remaining_scan,
                )
                if self._state is not ContextInvocationState.SEARCHING:
                    raise _action_error(ContextActionErrorCode.INVALID_TRANSITION)
                if self._hits + len(matches) > self._action_policy.max_cumulative_hits:
                    allowed = self._action_policy.max_cumulative_hits - self._hits
                    matches = matches[:allowed]
                    truncated = True
                pending_bindings: list[_HandleBinding] = []
                hits: list[ContextSearchHit] = []
                for match in matches:
                    entry = next(
                        (
                            candidate
                            for candidate in self.binding.manifest.entries
                            if candidate.source_id == match.descriptor.source_id
                        ),
                        None,
                    )
                    if entry is None:
                        raise _action_error(ContextActionErrorCode.HANDLE_INVALID)
                    handle_binding = self._build_handle_binding(
                        entry,
                        match.record.boundary,
                        origin_action_index=action.correlation.action_index,
                        safe_locator=self._search_safe_locator(
                            entry,
                            match.record.content,
                        ),
                    )
                    pending_bindings.append(handle_binding)
                    hits.append(
                        ContextSearchHit(
                            query_index=match.query_index,
                            handle=handle_binding.handle,
                            source_id=entry.source_id,
                            source_kind=entry.source_kind,
                            revision=entry.revision,
                            provenance=entry.provenance,
                            source_digest=match.descriptor.content_digest,
                            source_utf8_bytes=match.descriptor.utf8_bytes,
                            boundary=match.record.boundary,
                            boundary_digest=match.record.content_digest,
                            boundary_utf8_bytes=match.record.utf8_bytes,
                            snippet=match.snippet,
                            snippet_utf8_bytes=match.snippet_utf8_bytes,
                        )
                    )
                available = (
                    self._action_policy.max_observation_utf8_bytes
                    - self._observation_utf8_bytes
                )
                while True:
                    search_manifest = self._search_manifest(
                        action.queries[0].artifact_path_prefix
                    )
                    requested_kinds = tuple(
                        kind
                        for kind in _SEARCH_SOURCE_KIND_ORDER
                        if any(kind in query.source_kinds for query in action.queries)
                    )
                    authorized_entries = tuple(
                        entry
                        for entry in search_manifest.entries
                        if entry.searchable and entry.source_kind in requested_kinds
                    )
                    zero_hit_receipt = (
                        ContextZeroHitSearchReceipt(
                            evidence_id=(f"search_{action.correlation.action_index}"),
                            correlation=action.correlation,
                            manifest_digest=self._manifest_fingerprint,
                            authorized_source_ids=tuple(
                                entry.source_id for entry in authorized_entries
                            ),
                            authorized_source_kinds=requested_kinds,
                            authorized_source_fingerprint=(
                                _authorized_source_fingerprint(authorized_entries)
                            ),
                            artifact_path_prefix=(
                                action.queries[0].artifact_path_prefix
                            ),
                            query_digest=_digest(
                                _canonical_bytes(
                                    [
                                        "context-zero-hit-query-v1",
                                        [
                                            [
                                                query.normalized_text,
                                                [
                                                    kind.value
                                                    for kind in query.source_kinds
                                                ],
                                                query.artifact_path_prefix,
                                            ]
                                            for query in action.queries
                                        ],
                                    ]
                                )
                            ),
                            query_count=len(action.queries),
                        )
                        if not hits and not truncated
                        else None
                    )
                    observation = ContextSearchObservation(
                        action.correlation,
                        tuple(hits),
                        truncated,
                        zero_hit_receipt,
                    )
                    framed_bytes = len(
                        serialize_context_action_observation(
                            observation,
                            source_aliases=self._source_aliases,
                            handle_aliases=self._preview_handle_aliases(
                                tuple(pending_bindings)
                            ),
                            safe_locators=self._preview_safe_locators(
                                tuple(pending_bindings)
                            ),
                        ).encode("utf-8")
                        if self._source_aliases is not None
                        else serialize_context_action_observation(observation).encode(
                            "utf-8"
                        )
                    )
                    if framed_bytes <= available:
                        break
                    if not hits:
                        raise _action_error(
                            ContextActionErrorCode.OBSERVATION_LIMIT_EXCEEDED
                        )
                    hits.pop()
                    pending_bindings.pop()
                    truncated = True
                if self._state is not ContextInvocationState.SEARCHING:
                    raise _action_error(ContextActionErrorCode.INVALID_TRANSITION)
                for handle_binding in pending_bindings:
                    self._register_handle(handle_binding)
                    self._register_wire_handle(handle_binding.handle)
                self._nonterminal_actions += 1
                self._search_actions += 1
                self._queries += len(action.queries)
                self._hits += len(hits)
                self._scan_utf8_bytes += scanned
                self._observation_utf8_bytes += framed_bytes
                if observation.zero_hit_receipt is not None:
                    self._zero_hit_receipts.append(observation.zero_hit_receipt)
                self._next_action_index += 1
                self._state = ContextInvocationState.OPEN
                return observation
            except BaseException:
                if executed:
                    self._record_failed_attempt_locked(
                        "search",
                        queries=(
                            len(action.queries)
                            if type(action) is ContextSearchAction
                            else 0
                        ),
                    )
                self._fail_locked()
                raise

    @staticmethod
    def _boundary_content(
        snapshot: ContextSourceSnapshot,
        boundary: ContextSourceBoundary,
    ) -> bytes:
        if boundary.kind is ContextSourceBoundaryKind.WHOLE:
            return snapshot.content
        if boundary.kind in {
            ContextSourceBoundaryKind.ARTIFACT_LINES,
            ContextSourceBoundaryKind.RECORDS,
        }:
            assert boundary.start is not None and boundary.end is not None
            selected = tuple(
                record
                for record in snapshot.records
                if record.boundary.start is not None
                and boundary.start <= record.boundary.start <= boundary.end
            )
            if len(selected) != boundary.end - boundary.start + 1 or tuple(
                record.boundary.start for record in selected
            ) != tuple(range(boundary.start, boundary.end + 1)):
                raise _action_error(ContextActionErrorCode.HANDLE_INVALID)
            if boundary.kind is ContextSourceBoundaryKind.ARTIFACT_LINES:
                return b"".join(record.content.encode("utf-8") for record in selected)
            if len(selected) == 1:
                return selected[0].content.encode("utf-8")
            return _canonical_bytes([json.loads(record.content) for record in selected])
        matches = tuple(
            record for record in snapshot.records if record.boundary == boundary
        )
        if len(matches) != 1:
            raise _action_error(ContextActionErrorCode.HANDLE_INVALID)
        return matches[0].content.encode("utf-8")

    def read(
        self,
        action: ContextReadAction,
    ) -> tuple[ContextReadObservation, ContextReadReceipt]:
        """Capture one ordered batch without committing any partial result."""

        with self._lock:
            executed = False
            try:
                self._require_open()
                if type(action) is not ContextReadAction:
                    raise _action_error(ContextActionErrorCode.INVALID_VALUE)
                self._validate_correlation(action.correlation)
                self._require_nonterminal_capacity()
                if self._read_actions >= self._action_policy.max_read_actions:
                    raise _action_error(ContextActionErrorCode.READ_LIMIT_EXCEEDED)
                if len(action.handles) > self._action_policy.max_handles_per_read:
                    raise _action_error(ContextActionErrorCode.READ_LIMIT_EXCEEDED)
                if self._read_handles + len(action.handles) > (
                    self._action_policy.max_cumulative_handles
                ):
                    raise _action_error(ContextActionErrorCode.READ_LIMIT_EXCEEDED)
                bindings = tuple(
                    self._lookup_handle(handle) for handle in action.handles
                )
                executed = True
                self._state = ContextInvocationState.READING
                requested_source_ids = {binding.entry.source_id for binding in bindings}
                ordered_entries = tuple(
                    entry
                    for entry in self.binding.manifest.entries
                    if entry.source_id in requested_source_ids
                )
                snapshots: dict[str, ContextSourceSnapshot] = {}
                for entry in ordered_entries:
                    snapshots[entry.source_id] = self._sources.capture(
                        self.binding.manifest,
                        entry,
                        self._source_policy,
                    )
                    if self._state is not ContextInvocationState.READING:
                        raise _action_error(ContextActionErrorCode.INVALID_TRANSITION)
                source_additions: dict[str, int] = {}
                evidence: list[ContextSourceEvidence] = []
                batch_bytes = 0
                for evidence_ordinal, handle_binding in enumerate(bindings, start=1):
                    entry = handle_binding.entry
                    snapshot = snapshots[entry.source_id]
                    excerpt = self._boundary_content(
                        snapshot,
                        handle_binding.boundary,
                    )
                    if len(excerpt) > self._action_policy.max_excerpt_utf8_bytes:
                        raise _action_error(ContextActionErrorCode.READ_LIMIT_EXCEEDED)
                    source_additions[entry.source_id] = source_additions.get(
                        entry.source_id, 0
                    ) + len(excerpt)
                    prior_source_bytes = self._source_read_utf8_bytes.get(
                        entry.source_id,
                        0,
                    )
                    if prior_source_bytes + source_additions[entry.source_id] > (
                        self._action_policy.max_source_read_utf8_bytes
                    ):
                        raise _action_error(ContextActionErrorCode.READ_LIMIT_EXCEEDED)
                    batch_bytes += len(excerpt)
                    if self._read_utf8_bytes + batch_bytes > (
                        self._action_policy.max_read_utf8_bytes
                    ):
                        raise _action_error(ContextActionErrorCode.READ_LIMIT_EXCEEDED)
                    receipt = ContextEvidenceReceipt(
                        evidence_id=(
                            f"read_{action.correlation.action_index}_{evidence_ordinal}"
                        ),
                        handle=handle_binding.handle,
                        source_id=entry.source_id,
                        source_kind=entry.source_kind,
                        revision=entry.revision,
                        provenance=entry.provenance,
                        source_digest=entry.content_digest,
                        source_utf8_bytes=entry.utf8_bytes,
                        boundary=handle_binding.boundary,
                        boundary_digest=(
                            f"sha256:{hashlib.sha256(excerpt).hexdigest()}"
                        ),
                        boundary_utf8_bytes=len(excerpt),
                        originating_search_action_index=(
                            handle_binding.origin_action_index
                        ),
                        completing_read_action_index=(action.correlation.action_index),
                    )
                    evidence.append(
                        ContextSourceEvidence(
                            receipt,
                            excerpt.decode("utf-8", errors="strict"),
                        )
                    )
                for entry in ordered_entries:
                    if (
                        self._sources.capture(
                            self.binding.manifest,
                            entry,
                            self._source_policy,
                        )
                        != snapshots[entry.source_id]
                    ):
                        raise _action_error(ContextActionErrorCode.HANDLE_INVALID)
                    if self._state is not ContextInvocationState.READING:
                        raise _action_error(ContextActionErrorCode.INVALID_TRANSITION)
                observation = ContextReadObservation(
                    action.correlation,
                    tuple(evidence),
                )
                framed = self.serialize_observation(observation).encode("utf-8")
                if self._observation_utf8_bytes + len(framed) > (
                    self._action_policy.max_observation_utf8_bytes
                ):
                    raise _action_error(
                        ContextActionErrorCode.OBSERVATION_LIMIT_EXCEEDED
                    )
                receipt = ContextReadReceipt(
                    action.correlation,
                    tuple(item.receipt for item in evidence),
                    f"sha256:{hashlib.sha256(framed).hexdigest()}",
                    len(framed),
                )
                for handle in action.handles:
                    del self._handles[handle.value]
                self._nonterminal_actions += 1
                self._read_actions += 1
                self._read_handles += len(action.handles)
                self._read_utf8_bytes += batch_bytes
                for source_id, added in source_additions.items():
                    self._source_read_utf8_bytes[source_id] = (
                        self._source_read_utf8_bytes.get(source_id, 0) + added
                    )
                self._observation_utf8_bytes += len(framed)
                self._next_action_index += 1
                self._pending_receipt = receipt
                return observation, receipt
            except BaseException:
                if executed:
                    self._record_failed_attempt_locked("read")
                self._fail_locked()
                raise

    def complete_read(self, receipt: ContextReadReceipt) -> None:
        """Mark one exact read frame as delivered before accepting another action."""

        with self._lock:
            try:
                if (
                    self._state is not ContextInvocationState.READING
                    or type(receipt) is not ContextReadReceipt
                    or receipt != self._pending_receipt
                ):
                    raise _action_error(ContextActionErrorCode.OUTSTANDING_READ)
                self._completed_receipts.append(receipt)
                self._pending_receipt = None
                self._state = ContextInvocationState.OPEN
            except BaseException:
                self._fail_locked()
                raise

    def authorize_answer(
        self,
        action: ContextAnswerAction,
    ) -> ContextAnswerAuthorization:
        """Revalidate every completed source and authorize one terminal answer."""

        with self._lock:
            executed = False
            try:
                self._require_open()
                if type(action) is not ContextAnswerAction:
                    raise _action_error(ContextActionErrorCode.INVALID_VALUE)
                self._validate_correlation(action.correlation)
                completed_evidence = tuple(
                    evidence
                    for receipt in self._completed_receipts
                    for evidence in receipt.evidence
                )
                completed = {
                    evidence.handle.value: evidence for evidence in completed_evidence
                }
                if len(completed) != len(completed_evidence):
                    raise _action_error(ContextActionErrorCode.ANSWER_NOT_AUTHORIZED)
                if any(handle.value not in completed for handle in action.used_handles):
                    raise _action_error(ContextActionErrorCode.ANSWER_NOT_AUTHORIZED)
                if len(action.used_handles) > (
                    self._action_policy.max_cumulative_handles
                ):
                    raise _action_error(ContextActionErrorCode.ANSWER_NOT_AUTHORIZED)
                executed = True
                self._state = ContextInvocationState.SEARCHING
                completed_source_ids = {
                    evidence.source_id for evidence in completed_evidence
                }
                for entry in self.binding.manifest.entries:
                    if entry.source_id not in completed_source_ids:
                        continue
                    if not any(
                        evidence.source_id == entry.source_id
                        and evidence.revision == entry.revision
                        and evidence.source_digest == entry.content_digest
                        and evidence.provenance == entry.provenance
                        for evidence in completed_evidence
                    ):
                        raise _action_error(
                            ContextActionErrorCode.ANSWER_NOT_AUTHORIZED
                        )
                    self._sources.capture(
                        self.binding.manifest,
                        entry,
                        self._source_policy,
                    )
                    if self._state is not ContextInvocationState.SEARCHING:
                        raise _action_error(ContextActionErrorCode.INVALID_TRANSITION)
                completed_read_digest = _completed_evidence_digest(completed_evidence)
                answer_digest = _digest(action.answer.encode("utf-8"))
                if self._secret is None:
                    raise _action_error(ContextActionErrorCode.INVALID_TRANSITION)
                authorization_message = _canonical_bytes(
                    [
                        "context-answer-authorization-v1",
                        _scope_value(self.binding.scope),
                        self.binding.operation_id,
                        self.binding.result_correlation_id,
                        action.correlation.action_index,
                        self._manifest_fingerprint,
                        answer_digest,
                        completed_read_digest,
                        [_evidence_value(item) for item in completed_evidence],
                        [handle.value for handle in action.used_handles],
                        [_status_value(item) for item in self._core_statuses],
                        [_zero_hit_value(item) for item in self._zero_hit_receipts],
                        self.binding.reference_operation_id,
                        [
                            _reference_value(reference)
                            for reference in self.binding.mandatory_references
                        ],
                    ]
                )
                authorization_id = (
                    "ctxa_v1_"
                    + hmac.new(
                        self._secret,
                        authorization_message,
                        hashlib.sha256,
                    ).hexdigest()
                )
                authorization = ContextAnswerAuthorization(
                    authorization_id=authorization_id,
                    correlation=action.correlation,
                    manifest_digest=self._manifest_fingerprint,
                    answer_digest=answer_digest,
                    completed_evidence=completed_evidence,
                    completed_read_digest=completed_read_digest,
                    used_evidence=tuple(
                        completed[handle.value] for handle in action.used_handles
                    ),
                    core_statuses=self._core_statuses,
                    zero_hit_searches=tuple(self._zero_hit_receipts),
                    reference_operation_id=self.binding.reference_operation_id,
                    mandatory_references=self.binding.mandatory_references,
                )
                self._next_action_index += 1
                self._state = ContextInvocationState.ANSWER_AUTHORIZED
                self._clear_sensitive_locked()
                return authorization
            except BaseException:
                if executed:
                    self._record_failed_attempt_locked("answer")
                self._fail_locked()
                raise


def _workspace_context_id(workspace: Workspace) -> str:
    """Derive a content-free identity from the public canonical root."""

    if type(workspace) is not Workspace:
        raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
    try:
        root = workspace.root.as_posix().encode("utf-8", errors="strict")
    except (AttributeError, UnicodeEncodeError):
        raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE) from None
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


def _action_correlation(
    binding: ContextInvocationBinding,
    action_index: int,
) -> ContextActionCorrelation:
    """Reconstruct the complete exact correlation hidden behind one wire token."""

    return ContextActionCorrelation(
        binding.scope,
        binding.operation_id,
        binding.result_correlation_id,
        action_index,
    )


def _correlation_token(correlation: ContextActionCorrelation) -> str:
    """Derive one deterministic URL-safe 256-bit token from full correlation."""

    body = _canonical_bytes(
        [
            _CONTEXT_WIRE_VERSION,
            _scope_value(correlation.scope),
            correlation.operation_id,
            correlation.result_correlation_id,
            correlation.action_index,
        ]
    )
    return (
        base64.urlsafe_b64encode(hashlib.sha256(body).digest())
        .rstrip(b"=")
        .decode("ascii")
    )


def _projected_component_kind(entry: ContextManifestEntry) -> ContextComponentKind:
    if entry.semantic_channel is ContextSemanticChannel.CURRENT_INPUT:
        return ContextComponentKind.CURRENT_INPUT
    if entry.semantic_channel is ContextSemanticChannel.CANDIDATE:
        return ContextComponentKind.CANDIDATE
    if entry.source_kind is ContextSourceKind.AGENT_DEFINITION:
        return ContextComponentKind.ROLE_SYSTEM_PROMPT
    if entry.source_kind is ContextSourceKind.CONVERSATION:
        return ContextComponentKind.CONVERSATION_CONTEXT
    if entry.source_kind is ContextSourceKind.ACTIVE_DEBATE:
        return ContextComponentKind.DEBATE_MESSAGES
    if entry.source_kind is ContextSourceKind.OWN_AGENT_STATE:
        return ContextComponentKind.AGENT_STATE
    return ContextComponentKind.RETRIEVED_OBSERVATION


class _DuplicateJsonKey(ValueError):
    pass


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey
        result[key] = value
    return result


def _exact_object(
    value: object,
    fields: frozenset[str],
) -> dict[str, object]:
    if type(value) is not dict or set(value) != fields:
        raise validation_failure(
            _execution_error(ContextExecutionErrorCode.MALFORMED_ACTION), "$",
            "expected an object containing exactly " + ", ".join(sorted(fields)),
        )
    assert isinstance(value, dict)
    return value


def _answer_text_for_contract(
    value: object,
    contract: ContextJsonObjectResultContract,
    decode_result: Callable[[object], dict[str, object]] | None = None,
) -> str:
    """Validate one owner-supplied result schema and canonicalize its object."""

    if type(contract) is not ContextJsonObjectResultContract:
        raise validation_failure(
            ValueError(),
            '$',
            'expected a JSON object matching the current result contract',
        )
    if not contract.wire_format:
        try:
            value = contract.rule_graph.normalized_value(value)
        except (TypeError, ValueError, RecursionError):
            raise validation_failure(
                ValueError(),
                "$",
                "expected a JSON object matching the current result contract",
            ) from None
    try:
        if contract.wire_format:
            if decode_result is None:
                raise ValueError("current wire result requires a complete binding")
            domain_value = decode_result(value)
        else:
            domain_value = normalize_result(value, contract.contract_id)
        return _canonical_bytes(domain_value).decode("utf-8", errors="strict")
    except ContextActionError as failure:
        raise encoding_failure(failure, value, contract.schema_value())


def _decode_context_action(
    response_text: str,
    *,
    correlation: ContextActionCorrelation,
    allowed_actions: tuple[str, ...],
    result_contract: ContextJsonObjectResultContract,
    allowed_search_source_kinds: tuple[ContextSourceKind, ...] = _SEARCH_SOURCE_KIND_ORDER,
    resolve_handle: Callable[[object], ContextSourceHandle] = ContextSourceHandle,
    completed_handles: tuple[ContextSourceHandle, ...] = (),
    issued_handle_aliases: tuple[str, ...] = (),
    read_limit: int = 8,
    decoded_action: BoundCallDecodedAction | None = None,
) -> ContextSearchAction | ContextReadAction | ContextAnswerAction:
    """Strictly decode typed wire v7 and retain the actual rejected property."""
    try:
        raw = _exact_object(decoded_action.domain_value() if decoded_action is not None
                            else normalize_context_action(strict_json_object(response_text)),
                            frozenset({"action"}))
        action = raw["action"]
        if type(action) is not dict or type(action.get("kind")) is not str:
            raise validation_failure(ValueError(), "$.kind", "expected one of search, read, answer")
        action_kind = action["kind"]
        if action_kind not in {"answer", "read", "search"}:
            raise validation_failure(ValueError(), "$.kind", "expected one of search, read, answer")
        if action_kind == "search":
            if set(action) not in ({"kind", "queries"}, {"kind", "path_prefix", "queries"}):
                raise validation_failure(
                    ValueError(),
                    '$',
                    'search requires kind and queries, with only optional path_prefix',
                )
            path_prefix = action.get("path_prefix")
            query_values = action["queries"]
            if action_kind not in allowed_actions:
                raise validation_failure(
                    ValueError(),
                    '$.kind',
                    'expected an action from ' + ', '.join(allowed_actions),
                )
            if path_prefix is not None and type(path_prefix) is not str:
                raise validation_failure(
                    ValueError(),
                    '$.path_prefix',
                    'expected text or null',
                    value=path_prefix,
                )
            if type(query_values) is not list or not query_values:
                raise validation_failure(
                    ValueError(),
                    '$.queries',
                    'expected a nonempty array',
                    value=query_values,
                )
            queries: list[ContextSearchQuery] = []
            for query_index, query in enumerate(query_values):
                query_path = f"$.queries[{query_index}]"
                query_object = validation_call(query_path, _exact_object, query, frozenset({'source_kinds', 'text'}))
                kinds = query_object["source_kinds"]
                if type(query_object["text"]) is not str:
                    raise validation_failure(
                        ValueError(),
                        query_path + '.text',
                        'expected text',
                        value=query_object['text'],
                    )
                if type(kinds) is not list:
                    raise validation_failure(
                        ValueError(),
                        query_path + '.source_kinds',
                        'expected an array',
                        value=kinds,
                    )
                decoded_kinds = tuple(
                    validation_call(f"{query_path}.source_kinds[{index}]", ContextSourceKind, item)
                    for index, item in enumerate(kinds)
                )
                if not decoded_kinds:
                    raise validation_failure(
                        ValueError(),
                        query_path + '.source_kinds',
                        'expected at least one source kind',
                    )
                if len(set(decoded_kinds)) != len(decoded_kinds):
                    raise validation_failure(
                        ValueError(),
                        query_path + '.source_kinds',
                        'source kinds must be unique',
                    )
                if any(kind not in allowed_search_source_kinds for kind in decoded_kinds):
                    raise validation_failure(
                        ValueError(),
                        query_path + '.source_kinds',
                        'expected source kinds from ' + ', '.join((kind.value for kind in allowed_search_source_kinds)),
                    )
                canonical_kinds = tuple(kind for kind in _SEARCH_SOURCE_KIND_ORDER if kind in decoded_kinds)
                try:
                    queries.append(validation_call(
                        query_path,
                        ContextSearchQuery,
                        query_object['text'],
                        canonical_kinds,
                        path_prefix,
                    ))
                except ContextActionError as failure:
                    raise remap_validation(failure, {query_path + ".path_prefix": "$.path_prefix"})
            return ContextSearchAction(correlation, tuple(queries))
        if action_kind == "read":
            read = _exact_object(action, frozenset({"handles", "kind"}))
            handles_value = read["handles"]
            if type(handles_value) is not list or not handles_value:
                raise _execution_error(
                    ContextExecutionErrorCode.MALFORMED_ACTION,
                    detail=_ContextExecutionErrorDetail.READ_SOURCE_HANDLES_REQUIRED,
                )
            if action_kind not in allowed_actions:
                raise validation_failure(
                    ValueError(),
                    '$.kind',
                    'expected an action from ' + ', '.join(allowed_actions),
                )
            handles: list[ContextSourceHandle] = []
            for index, item in enumerate(handles_value):
                try:
                    handles.append(resolve_handle(item))
                except ContextActionError as failure:
                    if failure.code in {
                        ContextActionErrorCode.HANDLE_INVALID,
                        ContextActionErrorCode.INVALID_VALUE,
                    }:
                        raise validation_failure(
                            _execution_error(
                                ContextExecutionErrorCode.MALFORMED_ACTION,
                                detail=_ContextExecutionErrorDetail.READ_SOURCE_HANDLES_REQUIRED,
                            ),
                            f"$.handles[{index}]",
                            "expected a source handle returned by a preceding search",
                        ) from None
                    raise
            return ContextReadAction(correlation, tuple(handles))

        answer = _exact_object(action, frozenset({"kind", "result"}))
        if action_kind not in allowed_actions:
            raise validation_failure(
                ValueError(),
                '$.kind',
                'expected an action from ' + ', '.join(allowed_actions),
            )
        return ContextAnswerAction(
            correlation,
            (decoded_action.answer_json if decoded_action is not None else
             validation_call("$.result", _answer_text_for_contract, answer["result"], result_contract)),
            completed_handles,
        )
    except WireShapeError as failure:
        raise carry_validation(_execution_error(ContextExecutionErrorCode.MALFORMED_ACTION), validation_failure(failure, failure.wire_path, failure.wire_requirement)) from None
    except ContextExecutionError:
        raise
    except (ContextActionError, ContextManifestError, _DuplicateJsonKey, json.JSONDecodeError, TypeError, ValueError) as failure:
        rejected = carry_validation(_execution_error(ContextExecutionErrorCode.MALFORMED_ACTION), failure)
        if decoded_action is not None:
            rejected.bound_contract_failure = failure
        raise rejected from None



def _context_validation_details(
    response_text: str,
    schema_issues: tuple[str, ...],
    failure: ContextExecutionError,
) -> tuple[str, ...]:
    """Retain the actual schema/domain rejection; never revalidate heuristically."""
    issues = schema_issues or failure_issues(failure, path="$.action")
    if not issues and _execution_error_has_detail(
        failure, _ContextExecutionErrorDetail.READ_SOURCE_HANDLES_REQUIRED,
    ):
        issues = ("$.action.handles: expected unique handles returned by a preceding search",)
    if not issues and _execution_error_has_detail(
        failure, _ContextExecutionErrorDetail.RESPONSE_CORRELATION_MISMATCH,
    ):
        issues = ("$.action: response correlation must match the current request",)
    return bounded_chunks(tuple(diagnostic_to_wire(item, result_fields=False) for item in (issues or ("$.agent_action: action decoder failed without a property diagnostic",))))


def _closed_provider_object_schema(
    properties: dict[str, object],
    *,
    description: str | None = None,
) -> dict[str, object]:
    schema: dict[str, object] = {
        "additionalProperties": False,
        "properties": properties,
        "required": list(properties),
        "type": "object",
    }
    if description is not None:
        schema["description"] = description
    return schema


def _context_action_schema(
    *,
    allowed_actions: tuple[str, ...],
    result_contract: ContextJsonObjectResultContract | SchemaRules,
    allowed_search_source_kinds: tuple[ContextSourceKind, ...],
    action_limits: list[int],
    issued_handle_aliases: tuple[str, ...] = (),
    preserve_action_definitions: bool = False,
) -> dict[str, object]:
    """Build one closed typed action envelope for the exact physical call."""

    if type(result_contract) is SchemaRules:
        wire_format = True
    elif isinstance(result_contract, ContextJsonObjectResultContract):
        wire_format = result_contract.wire_format
    else:
        raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)

    variants: list[dict[str, object]] = []
    if "search" in allowed_actions:
        query_limit = min(action_limits[3], action_limits[4], CONTEXT_ACTION_POLICY.max_queries_per_search)
        source_kind_description = (
            "Choose distinct allowed source kinds; provider order is "
            "not significant and the runtime canonicalizes it."
        )

        def source_kinds_rule(choices: tuple[str, ...]) -> dict[str, object]:
            selection = finite_subset_rule(choices)
            selection["description"] = source_kind_description
            for alternative in selection.get("anyOf", ()):
                alternative["description"] = source_kind_description
            return selection

        query_schema = _closed_provider_object_schema(
            {
                "text": search_text_rule(min(action_limits[7], CONTEXT_ACTION_POLICY.max_query_characters)),
                "source_kinds": (
                    source_kinds_rule(tuple(kind.value for kind in allowed_search_source_kinds))
                    if wire_format else {
                    "description": (
                        source_kind_description
                    ),
                    "items": {
                        "enum": [kind.value for kind in allowed_search_source_kinds],
                        "type": "string",
                    },
                    "maxItems": len(allowed_search_source_kinds),
                    "minItems": 1,
                    "type": "array",
                }),
            }
        )
        search_properties = {
            "kind": {"enum": ["search"], "type": "string"},
            "queries": {
                "items": query_schema,
                "maxItems": query_limit,
                "minItems": 1,
                "type": "array",
            },
        }
        variants.append(_closed_provider_object_schema(search_properties,
            description=(
                "Search authorized sources when evidence is missing and no artifact-directory "
                "restriction is needed; results issue handles for a later read_context action."
            ),
        ))
        if ContextSourceKind.WRITING_ARTIFACT in allowed_search_source_kinds:
            artifact_query = _closed_provider_object_schema({
                "text": search_text_rule(min(action_limits[7], CONTEXT_ACTION_POLICY.max_query_characters)),
                "source_kinds": (source_kinds_rule(("writing_artifact",)) if wire_format else {
                    "type": "array", "items": {"type": "string", "enum": ["writing_artifact"]},
                    "minItems": 1, "maxItems": 1,
                }),
            })
            variants.append(_closed_provider_object_schema({
                **search_properties,
                "queries": {"type": "array", "items": artifact_query,
                            "minItems": 1, "maxItems": query_limit},
                "path_prefix": artifact_directory_prefix_rule(),
            },
                description=(
                    "Search story documents when evidence gathering is restricted to one artifact"
                    " directory; results issue handles for a later read_context action."
                ),
            ))
    if "read" in allowed_actions:
        handle_limit = min(action_limits[5], action_limits[6], CONTEXT_ACTION_POLICY.max_handles_per_read)
        variants.append(
            _closed_provider_object_schema(
                {
                    "kind": {"enum": ["read"], "type": "string"},
                    "handles": (
                        finite_selection_rule(issued_handle_aliases, minimum=1,
                                              maximum=min(handle_limit, len(issued_handle_aliases)))
                        if wire_format else {
                        "items": {"type": "string"},
                        "maxItems": handle_limit,
                        "minItems": 1,
                        "type": "array",
                    }),
                },
                description=(
                    "Read invocation-issued search handles when source contents are needed to "
                    "complete the task; the runtime returns bounded content and evidence IDs."
                ),
            )
        )
    if "answer" in allowed_actions:
        variants.append(
            _closed_provider_object_schema(
                {
                    "kind": {"enum": ["answer"], "type": "string"},
                    "result": result_contract.schema_value(),
                },
                description=(
                    "Submit a terminal task_result permitted by its contract, including an "
                    "evidence limitation when allowed. The runtime attaches successful READ receipts."
                ),
            )
        )
    if not variants:
        raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
    action_schema: dict[str, object] = (
        variants[0] if len(variants) == 1 else {"anyOf": variants}
    )
    schema = _closed_provider_object_schema(
        {"action": action_schema},
        description=(
            _CONTEXT_ACTION_ROOT_DESCRIPTION
        ),
    )
    projected = project_action_schema(schema)
    schema = hoist_schema_definitions(projected)
    _validate_provider_schema_node(schema)
    return projected if preserve_action_definitions else schema


def _source_number(source_alias: str) -> int:
    """Project one validated internal ``sN`` alias as its wire integer."""

    if (
        type(source_alias) is not str
        or not source_alias.startswith("s")
        or not source_alias[1:].isdigit()
        or source_alias[1:].startswith("0")
    ):
        raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
    return int(source_alias[1:])


def _wire_projected_content(
    unit_entries: tuple[ContextManifestEntry, ...],
    body_utf8: bytes,
) -> object:
    """Losslessly remove one redundant JSON-string layer from structured sources."""

    content = body_utf8.decode("utf-8", errors="strict")
    if any(
        entry.source_kind not in _STRUCTURED_WIRE_SOURCE_KINDS for entry in unit_entries
    ):
        return content
    try:
        parsed = json.loads(
            content,
            object_pairs_hook=_unique_json_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except (_DuplicateJsonKey, json.JSONDecodeError, TypeError, ValueError):
        return content
    try:
        if _canonical_bytes(parsed) == body_utf8:
            return parsed
    except ContextActionError:
        pass
    return content


def _wire_duplicate_value(
    value: object,
    duplicate_channels: Mapping[str, str | None],
) -> object:
    """Replace only an exact uniquely identified P0 byte duplicate."""

    if type(value) is str:
        channel = duplicate_channels.get(value)
        if channel is not None:
            return ["=", channel]
    return value


def _wire_record_value(
    entry: ContextManifestEntry,
    record: ContextCheckpointRecord,
    body_utf8: bytes,
    duplicate_channels: Mapping[str, str | None],
) -> list[object]:
    """Project one positional conversation or debate record patch."""

    value = _wire_projected_content((entry,), body_utf8)
    alias = record.record_alias
    if type(value) is not dict:
        return [alias, value]
    if entry.source_kind is ContextSourceKind.CONVERSATION:
        user = value.get("user_message")
        reply = value.get("agent_reply")
        if type(user) is dict and type(reply) is dict:
            return [
                alias,
                user.get("content"),
                reply.get("sender"),
                reply.get("content"),
            ]
        return [alias, value]
    if entry.source_kind is ContextSourceKind.ACTIVE_DEBATE:
        if record.cursor.record_id == "state":
            return [
                alias,
                "s",
                value.get("input_kind"),
                value.get("revision_id"),
                value.get("rounds_completed"),
            ]
        return [
            alias,
            "m",
            value.get("sender"),
            value.get("phase"),
            value.get("round"),
            value.get("revision"),
            value.get("kind"),
            _wire_duplicate_value(value.get("content"), duplicate_channels),
        ]
    return [alias, value]


@dataclass(frozen=True, slots=True)
class _WireCandidateRecord:
    """Exact candidate content addressed by one persistent debate record."""

    source_alias: str
    record_alias: int
    content: str = field(repr=False)
    content_digest: str
    utf8_bytes: int

    def __post_init__(self) -> None:
        _source_number(self.source_alias)
        if type(self.record_alias) is not int or self.record_alias < 1:
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if type(self.content) is not str:
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        try:
            encoded = self.content.encode("utf-8", errors="strict")
        except UnicodeEncodeError:
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE) from None
        if _digest(encoded) != self.content_digest or len(encoded) != self.utf8_bytes:
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)


def _debate_candidate_text(material: ContextRecordMaterial) -> str | None:
    """Return one exact canonical debate candidate record body, when present."""

    try:
        value = json.loads(
            material.body_utf8,
            object_pairs_hook=_unique_json_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except (_DuplicateJsonKey, json.JSONDecodeError, TypeError, ValueError):
        return None
    if (
        type(value) is not dict
        or value.get("kind") != "candidate"
        or type(value.get("content")) is not str
    ):
        return None
    content = value["content"]
    assert isinstance(content, str)
    try:
        content.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        return None
    return content


def _latest_debate_candidate_record(
    sources: tuple[ContextCheckpointSource, ...],
    catalog_sources: tuple[ContextCatalogCursor, ...],
    materials: tuple[ContextMaterial, ...],
) -> _WireCandidateRecord | None:
    """Resolve the latest provable candidate from content-free checkpoint facts."""

    debate_sources = tuple(
        source
        for source in sources
        if source.entry.source_kind is ContextSourceKind.ACTIVE_DEBATE
    )
    if len(debate_sources) != 1:
        return None
    source = debate_sources[0]
    catalog = next(
        (
            item
            for item in catalog_sources
            if item.entry.source_id == source.entry.source_id
        ),
        None,
    )
    material = next(
        (item for item in materials if item.entry.source_id == source.entry.source_id),
        None,
    )
    if catalog is None or material is None:
        return None
    material_by_id = {record.cursor.record_id: record for record in material.records}
    candidate: _WireCandidateRecord | None = None
    for accepted in source.records:
        record = material_by_id.get(accepted.cursor.record_id)
        if record is None or record.cursor != accepted.cursor:
            continue
        content = _debate_candidate_text(record)
        if content is None:
            continue
        encoded = content.encode("utf-8", errors="strict")
        candidate = _WireCandidateRecord(
            catalog.source_alias,
            accepted.record_alias,
            content,
            _digest(encoded),
            len(encoded),
        )
    return candidate


def _candidate_single_splice(
    base: str,
    target: str,
) -> tuple[int, int, str]:
    """Return the maximal nonoverlapping code-point prefix/suffix splice."""

    prefix = 0
    shared_limit = min(len(base), len(target))
    while prefix < shared_limit and base[prefix] == target[prefix]:
        prefix += 1
    suffix = 0
    remaining_base = len(base) - prefix
    remaining_target = len(target) - prefix
    while (
        suffix < remaining_base
        and suffix < remaining_target
        and base[len(base) - suffix - 1] == target[len(target) - suffix - 1]
    ):
        suffix += 1
    replacement_end = len(target) - suffix if suffix else len(target)
    return prefix, suffix, target[prefix:replacement_end]


def _wire_candidate_facts(record: _WireCandidateRecord) -> dict[str, object]:
    """Project one self-describing candidate record identity and byte facts."""

    return {
        "source_alias": _source_number(record.source_alias),
        "record_alias": record.record_alias,
        "digest": record.content_digest,
        "utf8_bytes": record.utf8_bytes,
    }


def _wire_candidate_value(
    candidate: str,
    projection: ContextProjection,
    checkpoint: ContextCheckpointView,
    materials: tuple[ContextMaterial, ...],
) -> object:
    """Offer an exact full candidate or an eligible, verified reuse representation."""

    if (
        projection.mode is not ContextDeliveryMode.DELTA
        or checkpoint.state is not ContextCheckpointState.READY
        or checkpoint.provider_thread_id is None
    ):
        return candidate
    base = _latest_debate_candidate_record(
        checkpoint.sources,
        checkpoint.catalog_sources,
        materials,
    )
    target = _latest_debate_candidate_record(
        projection.checkpoint_proposal.sources,
        projection.checkpoint_proposal.catalog_sources,
        materials,
    )
    if base is None or target is None or target.content != candidate:
        return candidate
    base_facts = _wire_candidate_facts(base)
    target_facts = _wire_candidate_facts(target)
    candidate_bytes = candidate.encode("utf-8", errors="strict")
    if base.content == target.content == candidate:
        pointer: object = {
            "op": "candidate_pointer",
            "base": base_facts,
            "target": target_facts,
            "result": "candidate_text",
            "resolve_record_candidate": "logical_n",
        }
        return pointer
    prefix, suffix, replacement = _candidate_single_splice(
        base.content,
        candidate,
    )
    retained_suffix = base.content[len(base.content) - suffix :] if suffix else ""
    reconstructed = base.content[:prefix] + replacement + retained_suffix
    if (
        reconstructed.encode("utf-8", errors="strict") != candidate_bytes
        or _digest(candidate_bytes) != target.content_digest
        or len(candidate_bytes) != target.utf8_bytes
    ):
        return candidate
    patch: object = {
        "op": "candidate_single_splice",
        "base": base_facts,
        "prefix_codepoints": prefix,
        "replacement": replacement,
        "suffix_codepoints": suffix,
        "target": target_facts,
        "result": "candidate_text",
        "resolve_record_candidate": "logical_n",
    }
    return patch


def _conversation_counts(material: ContextMaterial) -> tuple[int, int]:
    """Return complete/omitted turn counts from one validated snapshot body."""

    try:
        value = json.loads(
            material.body_utf8,
            object_pairs_hook=_unique_json_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except (_DuplicateJsonKey, json.JSONDecodeError, TypeError, ValueError):
        raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE) from None
    if type(value) is not dict:
        raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
    window = value.get("window")
    if window is None:
        return len(material.records), 0
    if (
        type(window) is not dict
        or type(window.get("total_complete_turns")) is not int
        or type(window.get("omitted_turns")) is not int
    ):
        raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
    return window["total_complete_turns"], window["omitted_turns"]


def _invocation_phase_contract(
    request: ContextInvocationRequest,
) -> tuple[str, ContextJsonObjectResultContract]:
    """Pair the route-derived phase with its owner-supplied result contract."""

    if request.invocation_kind is not None:
        return request.invocation_kind.value, request.result_contract
    if request.subject_kind is ContextSubjectKind.CONVERSATION:
        if request.allowed_search_source_kinds == (ContextSourceKind.CONVERSATION,):
            return "retrieval", request.result_contract
        return "conversation", request.result_contract
    phases = (
        "propose_revision",
        "synthesize",
        "finalize",
        "audit",
        "draft",
    )
    phase = next(
        (
            candidate
            for candidate in phases
            if request.operation_id.startswith(f"debate_{candidate}_")
        ),
        None,
    )
    if phase is None:
        raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
    return phase, request.result_contract


def _action_query(
    phase_contract: tuple[str, ContextJsonObjectResultContract],
    session: ContextInvocationSession,
    allowed_actions: tuple[str, ...],
) -> list[object]:
    """Build the authoritative phase, action, kind, limit, and result tuple."""

    phase, contract = phase_contract
    return [
        phase,
        "".join(_ACTION_WIRE_CODES[action] for action in allowed_actions),
        (
            "".join(
                _SOURCE_KIND_WIRE_CODES[kind]
                for kind in session.allowed_search_source_kinds
            )
            if "search" in allowed_actions
            else ""
        ),
        session.action_limits(),
        [phase, contract.contract_id],
    ]


def _history_current_fields(
    history: SessionHistorySnapshot,
    request: ContextInvocationRequest,
) -> dict[str, frozenset[str]]:
    """Find exact visible messages already carried in runtime-owned current input."""
    workflows = {
        ContextInvocationKind.LEAD: "lead_front_channel",
        ContextInvocationKind.LEAD_SYNTHESIS: "lead_synthesis",
    }
    if request.invocation_kind not in workflows:
        return {}
    supplied_by_locator: dict[str, str] = {}
    supplied_by_event: dict[str, str] = {}
    for material in request.materials:
        if material.entry.semantic_channel is not ContextSemanticChannel.CURRENT_INPUT:
            continue
        current = _wire_projected_content((material.entry,), material.body_utf8)
        if type(current) is not dict:
            continue
        if current.get("workflow") != workflows[request.invocation_kind]:
            continue
        command = current.get("command")
        aliases = current.get("input_aliases")
        if type(command) is str and type(aliases) is list:
            for alias in aliases:
                if (
                    type(alias) is dict
                    and alias.get("kind") == "user_input"
                    and type(alias.get("locator")) is str
                ):
                    supplied_by_locator[alias["locator"]] = command
        input_values = current.get("inputs")
        inputs = list(input_values) if type(input_values) in {list, tuple} else []
        execution = current.get("structured_recommendation_execution")
        if type(execution) is dict:
            selected = execution.get("selected_inputs")
            if type(selected) in {list, tuple}:
                inputs.extend(selected)
        for item in inputs:
            if type(item) is not dict or type(item.get("locator")) is not str:
                continue
            if type(item.get("text")) is str:
                supplied_by_locator[item["locator"]] = item["text"]
            reply = item.get("reply")
            if type(reply) is dict and type(reply.get("message")) is str:
                supplied_by_locator[item["locator"]] = reply["message"]
        replies = current.get("replies", ())
        if type(replies) not in {list, tuple}:
            continue
        for reply in replies:
            if (
                type(reply) is dict
                and type(reply.get("reply_event_id")) is str
                and type(reply.get("message")) is str
            ):
                supplied_by_event[reply["reply_event_id"]] = reply["message"]
    result: dict[str, frozenset[str]] = {}
    for conversation in history.conversations:
        for record in conversation.records:
            supplied = supplied_by_event.get(record.event_id)
            if supplied is None:
                supplied = supplied_by_locator.get(f"conversation/{record.event_id}")
            if supplied is None and record.sender.kind.value == "user":
                supplied = supplied_by_locator.get(
                    f"conversation/{record.turn_id}/user_input",
                )
            if supplied is None:
                continue
            fields = frozenset(
                item.key.value for item in record.fields
                if item.key.value in {"message.content", "specialist_reply.content"}
                and item.text == supplied
            )
            if fields:
                result[record.event_id] = fields
    return result


def _initial_payload(
    binding: ContextInvocationBinding,
    projection: ContextProjection,
    checkpoint: ContextCheckpointView,
    session: ContextInvocationSession,
    materials: tuple[ContextMaterial, ...],
    *,
    phase_contract: tuple[str, ContextJsonObjectResultContract],
    action_index: int,
    allowed_actions: tuple[str, ...],
    reference_context: tuple[Mapping[str, object], ...],
    session_history_records: Mapping[
        str, Mapping[str, Mapping[str, object]]
    ] | None = None,
) -> tuple[
    dict[str, object],
    dict[JsonPath, ContextComponentKind],
    tuple[JsonPath, ...],
]:
    component_paths: dict[JsonPath, ContextComponentKind] = {}
    duplicate_channels: dict[str, str | None] = {}
    current: object | None = None
    candidate: object | None = None
    candidate_entry: ContextManifestEntry | None = None

    def remember_duplicate(text: str, channel: str) -> None:
        previous = duplicate_channels.get(text)
        if previous is None and text not in duplicate_channels:
            duplicate_channels[text] = channel
        elif previous != channel:
            duplicate_channels[text] = None

    for unit in projection.units:
        if any(context_source_uses_catalog_alias(entry) for entry in unit.entries):
            continue
        content = _wire_projected_content(unit.entries, unit.body_utf8)
        channel = _SEMANTIC_CHANNEL_WIRE_CODES[unit.entries[0].semantic_channel]
        if type(content) is str:
            remember_duplicate(content, channel)
        elif type(content) is dict and type(content.get("input")) is str:
            remember_duplicate(content["input"], channel)
        if channel == "i":
            if current is not None:
                raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
            current = content
            component_paths[("i",)] = ContextComponentKind.CURRENT_INPUT
        elif channel == "k":
            if candidate is not None:
                raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
            candidate = content
            candidate_entry = unit.entries[0]
            component_paths[("n",)] = ContextComponentKind.CANDIDATE
    if current is None:
        raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
    catalog_by_alias = {
        source.source_alias: source
        for source in projection.checkpoint_proposal.catalog_sources
    }
    material_by_source = {material.entry.source_id: material for material in materials}
    records_by_alias: dict[str, list[ContextCheckpointRecord]] = {}
    projected_by_key = {
        (record.source_alias, record.record.record_alias): record
        for record in projection.projected_records
    }
    for record in projection.projected_records:
        records_by_alias.setdefault(record.source_alias, []).append(record.record)
    changed_group_aliases = {
        *records_by_alias,
        *(record.source_alias for record in projection.removed_checkpoint_records),
        *(
            source.source_alias
            for source in projection.catalog_updates
            if source.entry.source_kind
            in {
                ContextSourceKind.CONVERSATION,
                ContextSourceKind.ACTIVE_DEBATE,
            }
        ),
    }
    proposal_source_ids = {
        source.entry.source_id for source in projection.checkpoint_proposal.sources
    }
    group_aliases = tuple(
        source.source_alias
        for source in projection.checkpoint_proposal.catalog_sources
        if source.entry.source_id in proposal_source_ids
        and source.entry.source_kind
        in {
            ContextSourceKind.CONVERSATION,
            ContextSourceKind.ACTIVE_DEBATE,
        }
        and (
            projection.mode is ContextDeliveryMode.BOOTSTRAP
            or source.source_alias in changed_group_aliases
        )
    )
    projected_records: list[list[object]] = []
    for source_alias in group_aliases:
        records = records_by_alias.get(source_alias, [])
        source = catalog_by_alias[source_alias]
        represented_records = (session_history_records or {}).get(
            source.entry.provenance.locator,
        )
        if (
            source.entry.source_kind is ContextSourceKind.CONVERSATION
            and represented_records is not None
        ):
            unrepresented_records = []
            for record in records:
                expected = represented_records.get(record.cursor.record_id)
                actual = _wire_projected_content(
                    (source.entry,),
                    projected_by_key[(source_alias, record.record_alias)].body_utf8,
                )
                if (
                    expected is None
                    or type(actual) is not dict
                    or any(actual.get(key) != value for key, value in expected.items())
                ):
                    unrepresented_records.append(record)
            records = unrepresented_records
            if not records:
                continue
        record_values = [
            _wire_record_value(
                source.entry,
                record,
                projected_by_key[(source_alias, record.record_alias)].body_utf8,
                duplicate_channels,
            )
            for record in records
        ]
        group_index = len(projected_records)
        if source.entry.source_kind is ContextSourceKind.CONVERSATION:
            material = material_by_source.get(source.entry.source_id)
            if material is None:
                raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
            total, omitted = _conversation_counts(material)
            projected_records.append(
                ["c", _source_number(source_alias), total, omitted, record_values]
            )
            record_path_index = 4
        elif source.entry.source_kind is ContextSourceKind.ACTIVE_DEBATE:
            projected_records.append(["d", _source_number(source_alias), record_values])
            record_path_index = 2
        else:
            projected_records.append(["o", _source_number(source_alias), record_values])
            record_path_index = 2
        for record_index in range(len(records)):
            component_paths[("p", group_index, record_path_index, record_index)] = (
                _projected_component_kind(source.entry)
            )
    correlation = _action_correlation(binding, action_index)
    docs: list[list[object]] = []
    catalog_by_id = {
        source.entry.source_id: source
        for source in projection.checkpoint_proposal.catalog_sources
    }
    for unit in projection.units:
        for entry in unit.entries:
            if (
                entry.source_kind is not ContextSourceKind.WRITING_ARTIFACT
                or entry.semantic_channel is not ContextSemanticChannel.HARD_CONSTRAINT
            ):
                continue
            catalog = catalog_by_id.get(entry.source_id)
            if catalog is None:
                raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
            index = len(docs)
            docs.append(
                [
                    _source_number(catalog.source_alias),
                    entry.provenance.locator,
                    unit.body_utf8.decode("utf-8", errors="strict"),
                ]
            )
            component_paths[("docs", index, 2)] = (
                ContextComponentKind.RETRIEVED_OBSERVATION
            )
    proposal_catalog_by_id = {
        source.entry.source_id: source
        for source in projection.checkpoint_proposal.catalog_sources
    }
    payload: dict[str, object] = {
        "c": _correlation_token(correlation),
        "q": _action_query(phase_contract, session, allowed_actions),
        "i": current,
    }
    if session.core_statuses:
        payload["core_statuses"] = [
            {
                "evidence_id": item.evidence_id,
                "locator": item.locator,
                "status": item.status.value,
            }
            for item in session.core_statuses
        ]
    if candidate is not None:
        payload["n"] = (
            _wire_candidate_value(
                candidate,
                projection,
                checkpoint,
                materials,
            )
            if type(candidate) is str and candidate_entry is not None
            else candidate
        )
    if docs:
        payload["docs"] = docs
    if projected_records:
        payload["p"] = projected_records
    tombstones = [
        *[
            ["s", _source_number(source.source_alias)]
            for source in projection.removed_catalog_sources
        ],
        *[
            [
                "d",
                _source_number(
                    proposal_catalog_by_id[source.entry.source_id].source_alias
                ),
            ]
            for source in projection.removed_checkpoint_sources
            if source.entry.source_id in proposal_catalog_by_id
            and source.entry.source_id
            not in {
                removed.entry.source_id
                for removed in projection.removed_catalog_sources
            }
        ],
        *[
            [
                "r",
                _source_number(record.source_alias),
                record.record_alias,
            ]
            for record in projection.removed_checkpoint_records
            if record.source_alias
            not in {
                removed.source_alias for removed in projection.removed_catalog_sources
            }
        ],
    ]
    if tombstones:
        payload["t"] = tombstones
    mandatory_paths: list[JsonPath] = []
    if reference_context:
        payload["untrusted_writing_artifact_context"] = [
            dict(item) for item in reference_context
        ]
        component_paths[("untrusted_writing_artifact_context",)] = (
            ContextComponentKind.MANDATORY_REFERENCE_DESCRIPTOR
        )
        for index, item in enumerate(reference_context):
            if "content" in item:
                path = ("untrusted_writing_artifact_context", index, "content")
                component_paths[path] = ContextComponentKind.MANDATORY_REFERENCE_CONTENT
                mandatory_paths.append(path)
    return payload, component_paths, tuple(mandatory_paths)


def _render_context_payload(
    payload: dict[str, object],
    component_paths: Mapping[JsonPath, ContextComponentKind],
    mandatory_paths: tuple[JsonPath, ...],
    *,
    schema: Mapping[str, object],
    projection: ContextProjection,
) -> tuple[
    dict[str, object],
    dict[JsonPath, ContextComponentKind],
    tuple[JsonPath, ...],
]:
    """Choose candidate delivery by complete guided and shared request bytes."""
    def render(value: dict[str, object]) -> tuple[
        dict[str, object],
        dict[JsonPath, ContextComponentKind],
        tuple[JsonPath, ...],
    ]:
        rendered, paths, references = project_context_payload(
            value, component_paths, mandatory_paths,
        )
        rendered = add_contract_guidance(rendered, schema)
        rendered, paths = compact_context_payload(rendered, paths, references)
        return add_contract_guidance(rendered, schema), paths, references

    result = render(payload)
    candidate = payload.get("n")
    if not isinstance(candidate, dict) or candidate.get("op") not in {
        "candidate_pointer", "candidate_single_splice",
    }:
        return result
    for unit in projection.units:
        if (
            unit.entries[0].semantic_channel is ContextSemanticChannel.CANDIDATE
            and not any(context_source_uses_catalog_alias(entry) for entry in unit.entries)
        ):
            full_candidate = _wire_projected_content(unit.entries, unit.body_utf8)
            if type(full_candidate) is not str:
                raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
            full = render({**payload, "n": full_candidate})
            # Equal bytes favor literal full text and avoid needless references.
            return full if len(_canonical_bytes(full[0])) <= len(_canonical_bytes(result[0])) else result
    raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)


def _continuation_payload(
    binding: ContextInvocationBinding,
    session: ContextInvocationSession,
    *,
    phase_contract: tuple[str, ContextJsonObjectResultContract],
    action_index: int,
    allowed_actions: tuple[str, ...],
    observation: str,
) -> tuple[dict[str, object], dict[JsonPath, ContextComponentKind]]:
    correlation = _action_correlation(binding, action_index)
    try:
        observation_value = json.loads(
            observation,
            object_pairs_hook=_unique_json_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except (_DuplicateJsonKey, json.JSONDecodeError, TypeError, ValueError):
        raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE) from None
    return (
        {
            "c": _correlation_token(correlation),
            "q": _action_query(phase_contract, session, allowed_actions),
            "o": observation_value,
        },
        {("o",): ContextComponentKind.RETRIEVED_OBSERVATION},
    )


def _invocation_source_aliases(
    manifest: ContextManifest,
    projection: ContextProjection,
) -> Mapping[str, str]:
    """Return the planner-owned complete readable catalog alias map."""

    aliases = {
        source.entry.source_id: source.source_alias
        for source in projection.checkpoint_proposal.catalog_sources
    }
    expected_ids = {
        entry.source_id
        for entry in manifest.entries
        if context_source_uses_catalog_alias(entry)
    }
    if set(aliases) != expected_ids:
        raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
    return MappingProxyType(aliases)


def _attempt_identifier() -> str:
    value: str | None = None
    try:
        value = secrets.token_hex(16)
    except (OSError, RuntimeError, ValueError):
        pass
    if type(value) is not str or len(value) != 32:
        raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
    return f"context_attempt_{value}"


def _render_group_context_payload(
    payload: dict[str, object], component_paths: Mapping[JsonPath, ContextComponentKind],
    mandatory_paths: tuple[JsonPath, ...], *, schema: Mapping[str, object],
    contract: BoundCallContract, state: ContextGroupInvocationState, remaining: int,
    observation: str | None, conditions: ContextPlanningConditions, fresh: bool,
    ledger_verified: bool, provider_thread_id: str | None,
) -> tuple[dict, dict, tuple, bytes]:
    """논리 입력을 비교한 뒤 현재 요청 안에서만 공유 pool을 다시 만든다."""
    full, paths, references = project_context_payload(payload, component_paths, mandatory_paths)
    prior = state.transport_delivery
    compatible = prior is not None and prior.source_valid() and prior.request_basis == state.request_basis and prior.conditions == conditions
    old_envelope = json.loads(prior.logical_context) if compatible else {}
    current_basis_ref = contract.group_stage.catalog.request_basis_ref
    accepted = state.accepted_delivery
    accepted_compatible = (accepted is not None and accepted.source_valid() and accepted.request_basis == state.request_basis
                           and accepted.conditions == conditions)
    accepted_envelope = json.loads(accepted.logical_context) if accepted_compatible else {}
    transport_input = old_envelope.get("input", {})
    old = accepted_envelope.get("input", {})
    evidence_offers = contract.eligibility_offers if contract.lineage is not None else contract.offers
    read_facts = {f.evidence_id: f for f in (*evidence_offers.completed_read_facts, *evidence_offers.delivery_read_facts)}
    facts = {identity: fact.snapshot_value() for identity, fact in read_facts.items()}
    previous_facts = old_envelope.get("read_facts", {})
    observations = []
    for item in transport_input.get("context_observations", []):
        if all(facts.get(identity) == previous_facts.get(identity) for identity in item["read_fact_ids"]):
            observations.append(item)
    if observation is not None:
        projected, _, _ = project_context_payload({"o": json.loads(observation)}, {}, ())
        value = projected["context_observation"]
        # READ 묶음을 evidence별로 보유하여 한 자료의 교체/제거가 다른 자료를 지우지 않게 한다.
        read_key = "read_results" if "read_results" in value else "evidence" if value.get("kind") == "read_observation" else None
        entries = []
        if read_key is not None:
            for evidence in value[read_key]:
                fact = read_facts.get(evidence.get("evidence_id"))
                if fact is None:
                    continue
                source = evidence.get("source", evidence)
                boundary = evidence.get("source_range", evidence)
                if (source.get("source_id") != fact.source_id
                        or source.get("source_revision", source.get("revision")) != fact.revision
                        or source.get("source_digest") != fact.source_digest
                        or boundary.get("range_digest") != fact.boundary_digest):
                    continue
                entries.append({"read_fact_ids": [fact.evidence_id],
                                "observation": {**value, read_key: [evidence]}})
        else:
            entries.append({"read_fact_ids": [], "observation": value})
        for entry in entries:
            if not any(item["observation"] == entry["observation"] for item in observations):
                observations.append(entry)
    if observations:
        full["context_observations"] = observations
        paths[("context_observations",)] = ContextComponentKind.RETRIEVED_OBSERVATION
    catalog = contract.group_stage.catalog.as_object()
    envelope = canonical_provider_json({"input": full, "catalog": catalog, "read_facts": facts,
                                         "selection_request_basis_ref": current_basis_ref})
    context_ref = "ctx_" + hashlib.sha256(envelope).hexdigest()
    delta = bool(ledger_verified and not fresh and compatible and accepted_compatible
                 and _group_native_deliveries(state, conditions, provider_thread_id)
                 and old_envelope.get("selection_request_basis_ref") == current_basis_ref
                 and accepted_envelope.get("selection_request_basis_ref") == current_basis_ref)
    current = full
    if delta:
        fresh_keys = {"protocol_version", "request_correlation_token", "response_contract"}
        add = {k: v for k, v in full.items() if k not in old and k not in fresh_keys}
        changed = {k: v for k, v in full.items() if k in old and (old[k] != v or transport_input.get(k) != v) and k not in fresh_keys}
        removed = [k for k in dict.fromkeys((*old, *transport_input)) if k not in full and k not in fresh_keys]
        current = {k: v for k, v in full.items() if k in fresh_keys}
        current["context_changes"] = {"add": add, "replace": changed, "remove": removed}
        rebased = {}
        for path, kind in paths.items():
            if path[0] in fresh_keys:
                rebased[path] = kind
            elif path[0] in add or path[0] in changed:
                rebased[("context_changes", "add" if path[0] in add else "replace", *path)] = kind
        references = tuple(("context_changes", "add" if p[0] in add else "replace", *p)
                           for p in references if p[0] in add or p[0] in changed)
        paths = rebased
    active = state.workflow.active_selection
    include_catalog = not delta or accepted_envelope.get("catalog", {}).get("catalog_id") != catalog["catalog_id"] or old_envelope.get("catalog", {}).get("catalog_id") != catalog["catalog_id"]
    selection = SchemaSelectionInput(
        contract.schema_group_phase.value, contract.offers.call_id, contract.outer_inner_schema_ids,
        remaining, context_ref, accepted.context_ref if delta else None,
        canonical_provider_json(catalog) if include_catalog else None,
        None if include_catalog else catalog["catalog_id"],
        None if active is None else (active.catalog.catalog_id, active.group_id))
    current["response_contract"]["schema_selection"] = selection.as_object()
    # 현재 제어 슬롯은 공유 pool에 넘기지 않아 정확한 닫힌 타입을 보존한다.
    control = current["response_contract"].pop("schema_selection")
    rendered = add_contract_guidance(current, schema)
    rendered, paths = compact_context_payload(rendered, paths, references)
    rendered["response_contract"]["schema_selection"] = control
    rendered = add_contract_guidance(rendered, schema)
    if SchemaSelectionInput.from_value(rendered["response_contract"]["schema_selection"]) != selection:
        raise _execution_error(ContextExecutionErrorCode.PROVIDER_CONTRACT)
    return rendered, paths, references, envelope


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
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
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
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
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
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
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
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
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
            _safe_identity(provider_id)
            _safe_digest(definition_fingerprint)
        except ContextActionError:
            raise _execution_error(
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
                raise _execution_error(ContextExecutionErrorCode.PROVIDER_CONTRACT)
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
        event_sink: ContextExecutionEventSink | None = None,
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
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        for operation_id in abandoned_operation_ids:
            _safe_identity(operation_id)
        if abandoned_operation_ids and continuation is None:
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        if type(request) is not ContextInvocationRequest:
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
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
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
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
        _validate_sync_callback(event_sink)
        _validate_sync_callback(progress_sink)
        if request.subject_kind is ContextSubjectKind.DEBATE:
            if event_sink is not None and (phase is None or round_number is None):
                raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
            if (phase is None) is not (round_number is None):
                raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
            if phase is not None:
                _safe_identity(phase)
                if type(round_number) is not int or round_number < 0:
                    raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        elif phase is not None or round_number is not None:
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        provider_id, definition_fingerprint = self._provider_identity(provider)
        reference_operation_id = getattr(provider, "reference_operation_id", None)
        if reference_operation_id != request.reference_operation_id:
            raise _execution_error(ContextExecutionErrorCode.PROVIDER_CONTRACT)
        scope = self._scope_for(request)
        if continuation is not None:
            continuation.require_active()
            if (
                not continuation.identity.accepts(scope)
                or type(session_history) is not SessionHistorySnapshot
                or session_history.session_id != scope.session_id
                or not callable(publication_validator)
            ):
                raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
        elif (
            session_history is not None or publication_validator is not None
            or request.session_state_json is not None
        ):
            raise _execution_error(ContextExecutionErrorCode.INVALID_VALUE)
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
            raise _execution_error(ContextExecutionErrorCode.PROVIDER_CONTRACT)
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
                                raise _execution_error(ContextExecutionErrorCode.PROVIDER_CONTRACT)
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
                        event_sink=event_sink,
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
        event_sink: ContextExecutionEventSink | None,
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

        def completed_group_state() -> ContextGroupInvocationState | None:
            if group_state is None:
                return None
            if call_contract is None or call_decoded is None or group_state.workflow is None:
                raise _execution_error(ContextExecutionErrorCode.PROVIDER_CONTRACT)
            return replace(group_state, workflow=group_state.workflow.complete(call_contract.generation_binding(), call_decoded.selected_result))

        try:
            if (group_state is not None and group_state.workflow is not None
                    and group_state.workflow.phase in {LeadGroupPhase.STOP, LeadGroupPhase.DONE}):
                stage = "exhausted"
                raise _execution_error(ContextExecutionErrorCode.ACTION_EXHAUSTED)
            stage = "source"
            registry, manifest, materials = self._compose_context(
                request,
                scope,
                writing_artifacts,
                focused_conversation,
                active_debate,
                own_agent_state,
            )
            manifest_digest = _manifest_fingerprint(manifest)
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
                _safe_identity(active_thread_id)
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
                incoming_deliveries = _verified_group_deliveries(
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
                            _history_current_fields(session_history, request)
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
                source_aliases=_invocation_source_aliases(
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
                known_payload, known_component_paths, known_reference_paths = _initial_payload(
                    binding, projection, planning_checkpoint, action_session,
                    materials, phase_contract=_invocation_phase_contract(request),
                    action_index=1,
                    allowed_actions=action_session.allowed_actions(
                        after=None, search_had_hits=False,
                    ),
                    reference_context=(),
                    session_history_records=history_records,
                )
                known_payload["h"] = history_delivery
                known_component_paths[("h",)] = ContextComponentKind.CONVERSATION_CONTEXT
                known_payload, _, _ = _render_context_payload(
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
                        raise _execution_error(
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
                return bool(_verified_group_deliveries(
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
                    raise _execution_error(ContextExecutionErrorCode.PROVIDER_CONTRACT)
                reference_context = prelude.context

            observation: str | None = None
            pending_read: ContextReadReceipt | None = None
            terminal_call_id: str | None = None
            previous_action: str | None = None
            previous_search_had_hits = False
            phase_contract = _invocation_phase_contract(request)
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
                correlation = _action_correlation(binding, action_index)
                physical_reference_context = reference_context
                def build_call_request(call_id: str, lineage: BoundCallLineage | None):
                    nonlocal call_contract, group_state, allowed_actions, stage, pending_group_input, pending_group_payload
                    if (group_state is not None and group_state.workflow is not None
                            and group_state.workflow.phase in {LeadGroupPhase.STOP, LeadGroupPhase.DONE}):
                        stage = "exhausted"
                        raise _execution_error(ContextExecutionErrorCode.ACTION_EXHAUSTED)
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
                                raise _execution_error(ContextExecutionErrorCode.ACTION_EXHAUSTED)
                            if lineage is None:
                                current_actions = action_session.allowed_actions(
                                    after=previous_action, search_had_hits=previous_search_had_hits)
                                current_offers = action_session.contract_offers(
                                    call_id, allowed_actions=current_actions, delivery_read=pending_read,
                                    reference_prelude=prelude, reference_request=prelude_request)
                                if current_offers != offers:
                                    allowed_actions = current_actions
                                    call_contract = request.result_contract.for_offer(
                                        current_offers, request_basis=group_state.request_basis,
                                        group_workflow=group_state.workflow,
                                        prepared_compilation=group_state.prepared_compilation)
                                    group_state = ContextGroupInvocationState(
                                        call_contract.request_basis, call_contract.group_workflow,
                                        call_contract.prepared_compilation,
                                transport_delivery=group_state.transport_delivery, accepted_delivery=group_state.accepted_delivery)
                        schema = call_contract.schema_value()
                    else:
                        call_contract = None
                        schema = _context_action_schema(allowed_actions=allowed_actions, result_contract=phase_contract[1],
                            allowed_search_source_kinds=request.allowed_search_source_kinds, action_limits=action_session.action_limits())
                    if observation is None or group_state is not None:
                        payload, component_paths, reference_paths = _initial_payload(
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
                        payload, component_paths = _continuation_payload(
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
                        payload, component_paths, reference_paths, pending_group_input = _render_group_context_payload(
                            payload, component_paths, reference_paths, schema=schema, contract=call_contract,
                            state=group_state, remaining=physical_call_limit - len(calls), observation=observation, conditions=planning_conditions, fresh=request.fresh,
                            ledger_verified=group_delivery_ledger_verified(), provider_thread_id=current_thread_id)
                    else:
                        payload, component_paths, reference_paths = _render_context_payload(
                            payload, component_paths, reference_paths, schema=schema, projection=projection)
                    call_request = (BoundContextProviderCallRequest(contract=call_contract, policy_fingerprint=policy_fingerprint, call_kind=call_kind, payload=payload, component_paths=component_paths, mandatory_reference_paths=reference_paths, expected_thread_id=current_thread_id, continuation_identity=None if continuation is None else continuation.identity, execution_workspace=request.execution_workspace, context_witness=(None if group_state is None or group_state.transport_delivery is None or payload["response_contract"]["schema_selection"]["context_delivery"]["mode"] != "delta" else group_state.transport_delivery.witness), context_anchor=(group_state.accepted_delivery.witness if group_state is not None and group_state.accepted_delivery is not None and payload["response_contract"]["schema_selection"]["context_delivery"]["mode"] == "delta" else None))
                                    if call_contract is not None else ContextProviderCallRequest(scope=scope, policy_fingerprint=policy_fingerprint, call_id=call_id, call_kind=call_kind, schema=schema, payload=payload, component_paths=component_paths, mandatory_reference_paths=reference_paths, expected_thread_id=current_thread_id, continuation_identity=None if continuation is None else continuation.identity, execution_workspace=request.execution_workspace))
                    if group_state is not None:
                        pending_group_payload = canonical_provider_json(call_request.payload)
                    if call_contract is not None or history_delivery is not None or continuation is not None:
                        _require_context_capacity(
                            schema=call_request.schema,
                            payload=call_request.payload,
                            stage="provider_payload",
                        )
                    return call_request
                call_request = build_call_request(call_id, current_lineage)
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
                        call_request = build_call_request(f'{attempt_id}_call_{len(calls) + 1}',
                            None if call_contract is None else call_contract.replay_lineage())
                        schema, call_kind = call_request.schema, call_request.call_kind
                terminal_provider_succeeded = True
                if type(result) is not ContextProviderCallResult:
                    failure = _execution_error(
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
                    failure = _execution_error(
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
                    failure = _execution_error(
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
                    failure = _execution_error(
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
                            action = None if call_decoded.group_control is not None else _decode_context_action(result.response_text, correlation=correlation, allowed_actions=allowed_actions, result_contract=phase_contract[1], allowed_search_source_kinds=request.allowed_search_source_kinds, resolve_handle=action_session.resolve_wire_handle, completed_handles=action_session.completed_handles, decoded_action=call_decoded)
                        except ContractRejection as bound_failure:
                            rejected = carry_validation(_execution_error(ContextExecutionErrorCode.MALFORMED_ACTION), bound_failure)
                            rejected.bound_contract_failure = bound_failure
                            raise rejected from None
                        if (type(action) not in {ContextSearchAction, ContextReadAction, ContextAnswerAction}
                                and not (action is None and call_decoded.group_control is not None)):
                            raise _execution_error(ContextExecutionErrorCode.PROVIDER_CONTRACT)
                    else:
                        schema_issues = diagnose_json_text(result.response_text, schema)
                        if schema_issues:
                            raise _execution_error(ContextExecutionErrorCode.MALFORMED_ACTION)
                        action = _decode_context_action(
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
                    call_validation_details = _context_validation_details(
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
                                exhausted_action = _decode_context_action(result.response_text, correlation=correlation, allowed_actions=('search', 'read', 'answer'), result_contract=phase_contract[1], allowed_search_source_kinds=request.allowed_search_source_kinds, resolve_handle=action_session.resolve_wire_handle, completed_handles=action_session.completed_handles, decoded_action=call_decoded)
                            else:
                                exhausted_action = _decode_context_action(
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
                                    _context_provider_decode_terminal_code(
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
                            raise _execution_error(
                                ContextExecutionErrorCode.ACTION_EXHAUSTED
                            )
                    finalizer = call_finalizer
                    call_finalizer = None
                    if finalizer is not None:
                        reject_response(
                            finalizer,
                            _context_provider_decode_terminal_code(decode_failure),
                        )
                    raise
                terminal_provider_succeeded = False
                if call_decoded is not None and call_decoded.group_control is not None:
                    if group_state is None or call_contract.group_workflow is not group_state.workflow:
                        raise _execution_error(ContextExecutionErrorCode.PROVIDER_CONTRACT)
                    record = call_contract.record_group_control(call_decoded)
                    group_state = replace(group_state, workflow=group_state.workflow.accept(
                        record, remaining_calls=physical_call_limit - len(calls)))
                    action_session.advance_group_control(correlation)
                    history_advanced = True
                    require_execution_workspace_current()
                    finalizer = call_finalizer
                    call_finalizer = None
                    if finalizer is not None:
                        finalized = _accept_provider_call_without_replacing(finalizer)
                        if finalized and group_state is not None:
                            group_state = replace(group_state, accepted_delivery=group_state.transport_delivery)
                    current_lineage = None
                    if group_state.workflow.phase is LeadGroupPhase.STOP:
                        stage = "exhausted"
                        raise _execution_error(ContextExecutionErrorCode.ACTION_EXHAUSTED)
                    continue
                if type(action) is ContextSearchAction:
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
                    record_count = _conversation_read_record_count(read_observation)
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
            raise _execution_error(ContextExecutionErrorCode.ACTION_EXHAUSTED)
        except BaseException as failure:
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
                        else _context_provider_decode_terminal_code(failure)
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
                    group_state=group_state,
                ),
            )
            raise
