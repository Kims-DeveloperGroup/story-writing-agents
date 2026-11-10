"""Source-authored Lead call factories and immutable call offers."""

from __future__ import annotations

import hashlib
import inspect
import json
from ..blocking_work import run_blocking
from ..agent_context.actions import ContextCallReadFact, ContextSourceStatusReceipt
from ..review_contracts import ReviewTarget, ReviewTargetKind, ReviewTargetAvailability
from ..lead_provider_contracts import (
    LeadStagePolicy,
    LeadResultContractOwner,
    LeadResultRuleSeed,
    LeadDecodedResult,
    empty_core_status_evidence,
    LeadGroupCompilation,
    LeadGroupedStage,
    LeadProviderConstraints,
)
from ..lead_schema_groups import (
    LeadGroupRequestBasis,
    LeadGroupWorkflow,
    SchemaGroupControlOwner,
)
from ..conversations import (
    lead_outcome_scope_paths,
    authoring_scope_to_dict,
    CollaborationInputKind,
    CollaborationInputRevision,
    CollaborationInputState,
)
from ..structured_output_validation import (
    NamedHostCheck,
    TypedDecoder,
    HostCheckContext,
)
from typing import Callable
from dataclasses import dataclass, field, replace
from ..agent_context.manifest import ContextSourceKind
from ..agent_context.invocation.delivery import ContextGroupInvocationState
from ..agent_context.contracts.call_offers import ContextCallOffers
from ..agent_context.invocation.requests import ContextInvocationRequest
from ..agent_context.contracts.bound_call import (
    BoundCallFactory,
    BoundCallLineage,
    AuthorizedAnswerHostContext,
)
from ..backend import BoundAgent
from ..errors import BackendProtocolError
from ..writing_artifacts import parse_artifact_references

def provided_review_targets(inputs: tuple[CollaborationInputRevision, ...]) -> tuple[ReviewTarget, ...]:
    targets = []
    for item in inputs:
        if item.state is not CollaborationInputState.PRESENT:
            continue
        if item.kind is CollaborationInputKind.USER_INPUT:
            kind, target = ReviewTargetKind.USER_PROVIDED, item.locator
        elif item.kind is CollaborationInputKind.ARTIFACT_CANDIDATE:
            kind, target = ReviewTargetKind.CANDIDATE, item.locator
        elif item.kind in {CollaborationInputKind.WRITING_ARTIFACT, CollaborationInputKind.CORE_DOCUMENT, CollaborationInputKind.MANUSCRIPT}:
            kind, target = ReviewTargetKind.WRITING_ARTIFACT, item.locator
            if item.locator.startswith("${"):
                batch = parse_artifact_references(item.locator)
                if len(batch.references) != 1 or batch.references[0].token != item.locator:
                    raise ValueError("preliminary target requires exact captured artifact locator")
                target = batch.references[0].path
        else:
            continue
        whole = item.start_line is None
        targets.append(ReviewTarget(
            input_id=item.input_id, kind=kind, target_id=target,
            revision=item.revision_digest, content_digest=item.revision_digest,
            source_digest=item.revision_digest, boundary_digest=item.revision_digest,
            whole_source=whole, boundary_kind="whole" if whole else "artifact_lines",
            boundary_start=item.start_line, boundary_end=item.end_line))
    return tuple(targets)

def read_review_targets(facts: tuple[ContextCallReadFact, ...]) -> tuple[ReviewTarget, ...]:
    return tuple(ReviewTarget(
        input_id=item.evidence_id,
        kind=ReviewTargetKind.WRITING_ARTIFACT if item.source_kind is ContextSourceKind.WRITING_ARTIFACT else ReviewTargetKind.READ_SOURCE,
        target_id=item.artifact_locator or item.source_id, revision=item.revision,
        content_digest=item.boundary_digest, source_digest=item.source_digest,
        boundary_digest=item.boundary_digest, whole_source=item.whole_source and item.boundary_kind == "whole",
        boundary_kind=item.boundary_kind, boundary_start=item.boundary_start,
        boundary_end=item.boundary_end, availability=ReviewTargetAvailability.COMPLETED_READ)
        for item in facts)

def coverage_review_targets(
    provided: tuple[ReviewTarget, ...], facts: tuple[ContextCallReadFact, ...],
    statuses: tuple[ContextSourceStatusReceipt, ...],
) -> tuple[ReviewTarget, ...]:
    """Use existing authorized inputs/READs and status-only uncovered core paths."""
    targets = {item.input_id: item for item in provided
               if item.availability is not ReviewTargetAvailability.UNREAD}
    targets.update((item.input_id, item) for item in read_review_targets(facts))
    whole_paths = {item.target_id for item in targets.values()
                   if item.kind is ReviewTargetKind.WRITING_ARTIFACT and item.whole_source and item.boundary_kind == "whole"}
    for item in statuses:
        if item.locator not in whole_paths:
            targets[item.evidence_id] = ReviewTarget(
                input_id=item.evidence_id, kind=ReviewTargetKind.WRITING_ARTIFACT,
                target_id=item.locator, revision=item.revision,
                content_digest=None, source_digest=item.source_digest,
                boundary_digest=None, whole_source=False, boundary_kind="unread",
                availability=ReviewTargetAvailability.UNREAD,
                source_status=item.status.value, source_manifest_digest=item.manifest_digest)
    return tuple(targets.values())

def authorized_preliminary_targets(inputs, authorization) -> tuple[ReviewTarget, ...]:
    targets = {item.input_id: item for item in coverage_review_targets(
        provided_review_targets(inputs), tuple(ContextCallReadFact.from_evidence(item)
            for item in authorization.completed_evidence), authorization.core_statuses)}
    by_id = {item.input_id: item for item in inputs}
    for reference in authorization.mandatory_references:
        if reference.reference_id not in targets:
            continue
        captured = by_id[reference.reference_id]
        targets[reference.reference_id] = ReviewTarget(reference.reference_id,
            ReviewTargetKind.WRITING_ARTIFACT, targets[reference.reference_id].target_id,
            reference.revision, reference.content_digest, reference.revision, reference.content_digest,
            captured.start_line is None, "whole" if captured.start_line is None else "artifact_lines",
            captured.start_line, captured.end_line, availability=ReviewTargetAvailability.COMPLETED_READ)
    return tuple(targets.values())

@dataclass(frozen=True, slots=True)
class OrchestrationLeadCallFactory(BoundCallFactory):
    """Existing orchestration owns stage eligibility and genuine final-owner hooks."""
    constraints: LeadProviderConstraints
    rule_seed: LeadResultRuleSeed
    stage: str
    language: str
    authorized_validator: Callable[[str, object], object] = field(repr=False, compare=False)
    reviewer_for_locator: Callable[[str], str] = field(repr=False, compare=False)
    schema_group_protocol: bool = field(default=False, kw_only=True)
    _factory_profile: tuple = field(init=False, repr=False, compare=False)
    _factory_sources: tuple = field(init=False, repr=False, compare=False)
    _declared_authorization_check: Callable[[LeadDecodedResult, HostCheckContext], None] = field(init=False, repr=False, compare=False)
    _authorization_declaration: NamedHostCheck[LeadDecodedResult] = field(init=False, repr=False, compare=False)
    _status_rule: tuple[object, object] = field(
        default=(empty_core_status_evidence, empty_core_status_evidence.__code__),
        init=False, repr=False, compare=False,
    )

    _outcome_scope_rule: tuple[object, object] = field(
        default=(lead_outcome_scope_paths, lead_outcome_scope_paths.__code__),
        init=False, repr=False, compare=False,
    )

    def __init_subclass__(cls, **kwargs):
        raise TypeError("orchestration Lead factory declaration cannot be overridden")

    def __post_init__(self) -> None:
        if (type(self.constraints) is not LeadProviderConstraints or type(self.rule_seed) is not LeadResultRuleSeed
                or any(type(value) is not str or not value for value in (self.stage, self.language))
                or not callable(self.authorized_validator) or inspect.iscoroutinefunction(self.authorized_validator)
                or not callable(self.reviewer_for_locator) or inspect.iscoroutinefunction(self.reviewer_for_locator)):
            raise TypeError("Lead factory requires its exact current owner declaration")
        if (type(self.schema_group_protocol) is not bool
                or self.schema_group_protocol and (
                    self.stage != "lead.front" or self.constraints.preparation_kind is not None
                    or self.constraints.continuation
                    or self.rule_seed.contract_id not in {
                        "lead-response-v6", "lead-response-v7", "lead-response-v8"})):
            raise ValueError("두 국면 선택은 ordinary Lead front에만 적용합니다")
        object.__setattr__(self, "_factory_profile", (
            self.constraints, self.rule_seed, self.stage, self.language,
            self.schema_group_protocol, self.reviewer_for_locator))
        object.__setattr__(self, "_factory_sources", tuple(
            (method, method.__code__) for method in (
                type(self)._current_owner_declaration, type(self).declare_result_owners,
                type(self).declare_schema_groups, type(self).prepare_schema_compilation)))
        TypedDecoder.require_hook_shape(self.authorized_validator, 2)
        TypedDecoder.require_hook_shape(self.reviewer_for_locator, 1)
        validator = self.authorized_validator
        guard = type(self).require_declaration

        def authorized_check(result: LeadDecodedResult, context: HostCheckContext, *, validator=validator) -> None:
            if type(result) is not LeadDecodedResult or type(context) is not AuthorizedAnswerHostContext:
                raise TypeError("final Lead owner requires the actual selected result/authorization")
            if validator(result.domain_json, context.authorization) is not None:
                raise TypeError("final Lead owner check must return None")

        object.__setattr__(self, "_declared_authorization_check", authorized_check)
        object.__setattr__(self, "_authorization_declaration", NamedHostCheck(
            "owner.authorized_final", "848-v3:actual-service-authorization", "authorized",
            AuthorizedAnswerHostContext,
            b'{"requirements":[{"expected":{"authorization":"ACTUAL_CURRENT_OWNER"},"path":"$","rule":"ACTUAL_CURRENT_OWNER_AUTHORIZATION"}]}',
            authorized_check,
            dependencies=(validator, guard, self._require_status_rule,
                          self._status_rule[0], self._outcome_scope_rule[0], parse_artifact_references),
        ))

    def require_declaration(self) -> None:
        profile = self._factory_profile
        if (self.constraints is not profile[0] or self.rule_seed is not profile[1]
                or (self.stage, self.language, self.schema_group_protocol) != profile[2:5]
                or self.reviewer_for_locator is not profile[5]
                or self._factory_sources != tuple((method, method.__code__) for method in (
                    type(self)._current_owner_declaration, type(self).declare_result_owners,
                    type(self).declare_schema_groups, type(self).prepare_schema_compilation))):
            raise ValueError("동결된 Lead 호출 프로필이 변경됐습니다")
        declaration = self._authorization_declaration
        declaration.require_provenance()
        if (self._declared_authorization_check is not declaration.check
                or type(self).require_declaration is not declaration.dependencies[1]):
            raise ValueError("frozen Lead authorization declaration changed")
        self._require_status_rule()

    @property
    def contract_id(self) -> str:
        return self.rule_seed.contract_id

    def _current_owner_declaration(self, offers: ContextCallOffers):
        self._authorization_declaration.require_provenance()
        self._authorization_declaration.dependencies[1](self)
        if type(offers) is not ContextCallOffers:
            raise TypeError("Lead declaration requires actual service-owned physical offers")
        if self.constraints.continuation and self.constraints.review_candidate_ids is None:
            raise ValueError("physical continuation requires frozen review candidates")
        facts = tuple(dict.fromkeys(offers.completed_read_facts + offers.delivery_read_facts))
        read_ids = tuple(dict.fromkeys(item.evidence_id for item in facts))
        writing_ids = tuple(dict.fromkeys(item.evidence_id for item in facts
                                        if item.source_kind is ContextSourceKind.WRITING_ARTIFACT))
        inputs = tuple(dict.fromkeys(self.constraints.input_ids + read_ids))
        sources = None if self.constraints.source_ids is None else tuple(dict.fromkeys(self.constraints.source_ids + read_ids))
        reviewers = tuple(dict.fromkeys(self.constraints.reviewer_inputs + tuple(
            (self.reviewer_for_locator("${" + item.artifact_locator + "}"), item.evidence_id)
            for item in facts if item.source_kind is ContextSourceKind.WRITING_ARTIFACT)))
        current = replace(self.constraints, input_ids=inputs, source_ids=sources, reviewer_inputs=reviewers,
                          allow_read_sources=False,
            preliminary_targets=coverage_review_targets(
                self.constraints.preliminary_targets, facts, offers.core_status_facts), required_input_ids=self.constraints.required_assignment_input_ids())
        writing_reads = tuple(
            (item.evidence_id, item.artifact_locator, item.whole_source)
            for item in facts if item.source_kind is ContextSourceKind.WRITING_ARTIFACT
        )
        facts_bytes = json.dumps({"requirements": [{"path": "$", "rule": "ACTUAL_CURRENT_OWNER_AUTHORIZATION",
            "expected": {"physical_call_id": offers.call_id, "input_ids": current.input_ids,
                "review_candidate_ids": current.review_candidate_ids,
                "factual_answer_writing_read_facts": writing_reads,
                "read_evidence": "DELIVERED_AND_COMPLETED_CURRENT_OWNER_READ", "rejected_answer_authority": "INPUT_ONLY",
                "new_target_path": "ACTUAL_CURRENT_PHYSICAL_ROOT_POLICY",
                "admission": "EXISTING_OWNER_STATE_AND_FINAL_ADMISSION"}}]},
            sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        check = replace(self._authorization_declaration, corrective_facts=facts_bytes)
        return current, read_ids, writing_ids, writing_reads, check

    def declare_result_owners(self, offers: ContextCallOffers) -> LeadStagePolicy:
        current, read_ids, writing_ids, writing_reads, check = self._current_owner_declaration(offers)
        variants = current.current_wire_variants(
            self.rule_seed, read_ids=read_ids, writing_read_ids=writing_ids,
            writing_read_facts=writing_reads, core_status_facts=offers.core_status_facts,
        )
        tasks = current.task_revisions or (() if current.scope is None else ((current.scope.task_id, current.scope.task_revision),))
        owners = tuple(LeadResultContractOwner(variant, tasks, (check,)) for variant in variants)
        return LeadStagePolicy(self.stage, offers.call_id, tasks, self.language, offers.offer_digest,
                               current.choice_snapshot(), offers.offer_digest, offers.call_id,
                               owner_declarations=owners)

    async def prepare_schema_compilation(
        self, offers: ContextCallOffers, lineage: BoundCallLineage | None = None, *,
        request_basis: LeadGroupRequestBasis | None = None,
        group_workflow: LeadGroupWorkflow | None = None,
        prepared_compilation: LeadGroupCompilation | None = None,
    ) -> LeadGroupCompilation | None:
        """Prepare pure compiler data while declaration and host callbacks stay local."""
        self.require_declaration()
        if lineage is not None or not self.schema_group_protocol:
            return prepared_compilation
        if type(request_basis) is not LeadGroupRequestBasis:
            raise TypeError("그룹 선택에는 서비스의 정확한 논리 요청 참조가 필요합니다")
        current, read_ids, writing_ids, writing_reads, _check = self._current_owner_declaration(offers)
        read_facts = tuple(dict.fromkeys(offers.completed_read_facts + offers.delivery_read_facts))
        if prepared_compilation is not None and type(prepared_compilation) is not LeadGroupCompilation:
            raise TypeError("재사용할 의미 계획은 정확한 현재 compiler여야 합니다")
        if prepared_compilation is None or not prepared_compilation.matches_current(
            current, self.rule_seed, read_facts=read_facts, core_status_facts=offers.core_status_facts,
        ):
            prepared_compilation = await run_blocking(
                LeadGroupCompilation, current, self.rule_seed, read_ids, writing_ids,
                writing_reads, offers.core_status_facts, read_facts=read_facts,
            )
        self.require_declaration()
        return prepared_compilation

    def declare_schema_groups(self, offers: ContextCallOffers,
                              request_basis: LeadGroupRequestBasis, *,
                              prepared_compilation: LeadGroupCompilation | None = None) -> LeadGroupedStage | None:
        self.require_declaration()
        if not self.schema_group_protocol:
            return None
        if type(request_basis) is not LeadGroupRequestBasis:
            raise TypeError("그룹 선택에는 서비스의 정확한 논리 요청 참조가 필요합니다")
        current, read_ids, writing_ids, writing_reads, check = self._current_owner_declaration(offers)
        read_facts = tuple(dict.fromkeys(offers.completed_read_facts + offers.delivery_read_facts))
        if prepared_compilation is not None and type(prepared_compilation) is not LeadGroupCompilation:
            raise TypeError("재사용할 의미 계획은 정확한 현재 compiler여야 합니다")
        compilation = prepared_compilation
        if compilation is None or not compilation.matches_current(
                current, self.rule_seed, read_facts=read_facts,
                core_status_facts=offers.core_status_facts):
            compilation = LeadGroupCompilation(
                current, self.rule_seed, read_ids, writing_ids, writing_reads, offers.core_status_facts,
                read_facts=read_facts)
        catalog = LeadGroupedStage.catalog_for(
            compilation, request_basis, stage=self.stage, language=self.language,
            authorization_check=check)
        tasks = current.task_revisions or (() if current.scope is None else (
            (current.scope.task_id, current.scope.task_revision),))
        policy = LeadStagePolicy(
            self.stage, offers.call_id, tasks, self.language, offers.offer_digest,
            current.choice_snapshot(), offers.offer_digest, offers.call_id,
            owner_declarations=(SchemaGroupControlOwner(catalog),))
        return LeadGroupedStage(compilation, request_basis, policy, check)

    def _require_status_rule(self) -> None:
        function, code = self._status_rule
        if (
            empty_core_status_evidence is not function
            or function.__code__ is not code
        ):
            raise ValueError("frozen empty-core evidence rule changed")
        function, code = self._outcome_scope_rule
        if lead_outcome_scope_paths is not function or function.__code__ is not code:
            raise ValueError("frozen Lead outcome scope rule changed")

def lead_call_contract_factory(*, constraints: LeadProviderConstraints, contract: LeadResultRuleSeed,
                                stage: str, language: str, authorized_validator: Callable[[str, object], object],
                                reviewer_for_locator: Callable[[str], str],
                                schema_group_protocol: bool = False) -> OrchestrationLeadCallFactory:
    if type(contract) is not LeadResultRuleSeed:
        raise TypeError("Lead planning requires the exact pure source-owned rule seed")
    return OrchestrationLeadCallFactory(constraints, contract,
                                       stage, language, authorized_validator, reviewer_for_locator,
                                       schema_group_protocol=schema_group_protocol)

def lead_group_invocation_state(
    request: ContextInvocationRequest, *, original_text: str,
    constraints: LeadProviderConstraints,
) -> ContextGroupInvocationState | None:
    """현재 요청·작업·범위에서 선택 근거를 발급하며 READ와 호출 예산은 제외한다."""
    factory = request.result_contract
    if type(factory) is not OrchestrationLeadCallFactory:
        return None
    if not factory.schema_group_protocol:
        if request.group_state is not None:
            raise BackendProtocolError("비활성 그룹 프로필에 선택 상태를 전달할 수 없습니다")
        return None
    if factory.constraints is not constraints or type(original_text) is not str:
        raise BackendProtocolError("현재 Lead 소유자가 발급한 요청·범위가 필요합니다")
    basis_value = {
        "request_sha256": hashlib.sha256(original_text.encode("utf-8")).hexdigest(),
        "task_revisions": constraints.task_revisions,
        "scope": None if constraints.scope is None else authoring_scope_to_dict(constraints.scope),
        "objective_ids": constraints.objective_ids,
        "review_candidate_ids": constraints.review_candidate_ids,
        "preparation_kind": constraints.preparation_kind,
        "preparation_objective_id": constraints.preparation_objective_id,
        "preparation_targets": constraints.preparation_targets,
        "preparation_specialists": constraints.preparation_specialists,
        "premise_question_id": constraints.premise_question_id,
        "adoption_intent": None if constraints.adoption_intent is None else constraints.adoption_intent.value,
        "adoption_targets": constraints.adoption_targets,
        "readiness_source_basis_ref": constraints.readiness_source_basis_ref,
    }
    scope_ref = "lead-group-request-" + hashlib.sha256(json.dumps(
        basis_value, sort_keys=True, ensure_ascii=False, allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    basis = LeadGroupRequestBasis(request.operation_id, request.result_correlation_id, scope_ref)
    previous = request.group_state
    return ContextGroupInvocationState(
        basis, None if previous is None else previous.workflow,
        None if previous is None else previous.prepared_compilation,
        transport_delivery=None if previous is None else previous.transport_delivery,
        accepted_delivery=None if previous is None else previous.accepted_delivery,
    )

def validated_context_provider_factory(
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
