"""Sealed, mutually bound context call contracts and typed action owners."""

from __future__ import annotations

import hashlib
import json
from ...agents import LEAD_ORCHESTRATOR
from ...conversations.session_context import (
    MAX_PROVIDER_PAYLOAD_UTF8_BYTES,
    MAX_PROVIDER_SCHEMA_UTF8_BYTES,
    SessionContextCapacityError,
)
from ...lead_provider_contracts import (
    LeadContractIndex,
    LeadContractRejection,
    LeadDecodedResult,
    LeadGroupCompilation,
    LeadGroupedStage,
    LeadResultContractOwner,
    LeadStagePolicy,
    factor_rule_schema,
)
from ...lead_schema_groups import (
    LeadGroupGenerationBinding,
    LeadGroupPhase,
    LeadGroupRequestBasis,
    LeadGroupSelectionRecord,
    LeadGroupWorkflow,
    SchemaGroupControl,
    SchemaGroupControlOwner,
    SchemaGroupReselection,
    SchemaGroupSelection,
)
from ...provider_schema_selection import LEAD_GROUP_PHASE_GUIDES, SchemaSelectionInput
from ...provider_schema_projection import (
    hoist_schema_definitions,
    rebase_schema_definitions,
    require_schema_projection,
)
from ...provider_response_mapping import normalize_context_action
from ...structured_output_validation import (
    BoundContract,
    ContractOwner,
    ContractRejection,
    HostCheckContext,
    NamedHostCheck,
    SchemaRules,
    SelectedContract,
    SelectedHostContext,
    TypedDecoder,
    ValidatedValue,
    closed_rule,
    decode_finite_selection,
    failure_findings,
    failure_issues,
    json_value_utf8_size,
    remap_validation,
    strict_json_member_text,
    strict_json_object,
    validation_failure,
)
from ..accounting import account_provider_payload
from ..actions import (
    CONTEXT_ACTION_POLICY,
    ContextAnswerAuthorization,
    SEARCH_NORMALIZATION_REVISION,
    normalized_search_text,
)
from .action_schema import (
    CONTEXT_ACTION_ROOT_DESCRIPTION,
    context_action_schema,
)
from .call_offers import ContextCallOffers
from .context_values import canonical_bytes
from abc import (
    ABC,
    abstractmethod,
)
from collections.abc import (
    Callable,
    Mapping,
)
from copy import deepcopy
from dataclasses import (
    dataclass,
    field,
    replace,
)
from typing import TypeVar


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

    async def prepare_schema_compilation(
        self, offers: ContextCallOffers, lineage: BoundCallLineage | None = None, *,
        request_basis: LeadGroupRequestBasis | None = None,
        group_workflow: LeadGroupWorkflow | None = None,
        prepared_compilation: LeadGroupCompilation | None = None,
    ) -> LeadGroupCompilation | None:
        """Retain custom factories' declaration callbacks on the calling loop.

        A concrete owner may prepare immutable compiler inputs separately;
        ordinary binding and live host checks still run through ``for_offer``.
        """
        return prepared_compilation

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
                description=CONTEXT_ACTION_ROOT_DESCRIPTION,
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
                or hashlib.sha256(canonical_bytes(offers.snapshot_value())).hexdigest() != offers.offer_digest
                or hashlib.sha256(canonical_bytes(self.eligibility_offers.snapshot_value())).hexdigest()
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
            current_offer=canonical_bytes(self.offers.snapshot_value()),
            eligibility_offer=canonical_bytes(self.eligibility_offers.snapshot_value()),
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
        if len(input_json) > MAX_PROVIDER_PAYLOAD_UTF8_BYTES:
            raise SessionContextCapacityError(
                stage="provider_payload", actual_utf8_bytes=len(input_json)
            )

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
        # Offered task pairs remain frozen host state, never model repair choices.
        payload.pop("task_revisions", None)
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
        native = context_action_schema(
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


__all__ = [
    'AuthorizedAnswerHostContext',
    'ContextActionValue',
    'ContextActionContractOwner',
    'BoundCallFactory',
    'BoundCallLineage',
    'BoundCallDecodedAction',
    'BoundCallContract',
]
