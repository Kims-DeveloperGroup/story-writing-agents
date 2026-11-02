"""Pure projections of the facts offered to one Lead provider invocation.

These snapshots describe choices, not authority. Orchestration still owns source
freshness, completed READ receipts, confirmation, candidate construction and state.
"""

from __future__ import annotations

import json
import hashlib
import inspect
from copy import deepcopy
from itertools import combinations
from collections.abc import Callable, Mapping
from typing import Generic, TypeVar
from types import MappingProxyType
from dataclasses import dataclass, field, replace

from .agent_context.actions import ContextCallReadFact, ContextSourceStatusKind, ContextSourceStatusReceipt
from .agent_context.manifest import ContextSourceKind
from .writing_artifacts.catalog import (
    CORE_ARTIFACT_PATHS, ArtifactKind, artifact_kind_path_rule, canonical_artifact_kind,
)
from .writing_artifacts.change_sets import ArtifactChangeOperationKind
from .writing_artifacts.relationship_policy import artifact_source_operation_rule
from .provider_wire import (
    project_result_schema, normalize_result, hoist_schema_definitions, rebase_schema_definitions,
    project_lead_scope_schema, restore_lead_scope_result, project_lead_scope_diagnostic,
    diagnostic_to_wire,
    ACTIVITIES as AUTHORING_ACTIVITIES, ASSIGNMENT_TYPES,
)
from .provider_wire import share_schema_descriptions
from .provider_wire import CHANGE_TYPES, ENUM_MEANINGS, field_description
from .provider_wire import DRAFTING_ACTIONS, LEAD_RESPONSES
from .lead_schema_groups import (
    LEAD_SCHEMA_GROUPS, LeadGroupCatalog, LeadGroupRequestBasis, LeadResultSemantics,
    LeadSchemaGroup, SchemaGroupControlOwner, schema_group_for,
)
from .structured_output_validation import closed_rule, closed_map_rule, finite_subset_rule, schema_rule_fingerprint, schema_rule_fingerprints
from .lead_exploration import exploration_result_schema
from .authoring_admission import assessment_from_dict
from .authoring_admission import assessment_schema as admission_rule
from .core_document_readiness import current_assessment_schema, decode_current_assessment, assessment_from_dict as core_assessment_from_dict
from .agents import SPECIALIST_IDS
from .conversations import (
    COLLABORATION_VISIBLE_TEXT_MAX_UTF8_BYTES, LeadOutcomeKind, lead_outcome_scope_paths,
)
from .conversations.events import authoring_scope_from_dict
from .conversations.session_context import MAX_PROVIDER_SCHEMA_UTF8_BYTES
from .specialist_routing import (
    ArtifactReviewDomain, AuthoringActivity, AuthoringIntent, AuthoringScope,
    required_authoring_specialists, required_authoring_domains, assignment_mode_for_specialist,
    AuthoringContributionRole,
    AuthoringScopeRuleSet, authoring_scope_rules, finite_authoring_scope_rules,
    AUTHORING_SCOPE_SCHEMA_VERSION,
    legal_specialist_rosters, assignment_rule, artifact_path_rule, artifact_domain,
)
from .lead_premise_decisions import (
    premise_question_schema, premise_analysis_schema, premise_summary_schema,
    PremiseAnalysis, PremiseDecisionSummary, premise_question_intent_from_dict,
    premise_analysis_from_dict, premise_summary_from_dict,
)
from .structured_output_validation import (
    ContractOwner, BoundContract, ContractIdentity, ContractRejection, NamedHostCheck, TypedDecoder,
    HostCheckContext, SelectedHostContext, ValidatedValue, SelectedContract,
    DomainValidationRule, ValidationFinding, validation_failure, SchemaRules, canonical_schema_rules,
    strict_json_object, diagnose_value, failure_findings, failure_issues, nonempty_text_rule,
    nonempty_text_value, visible_text_rule, finite_selection_rule, decode_finite_selection,
    schema_fragment_findings,
    SCHEMA_PYTHON_WHITESPACE,
    nonblank_text_rule,
    unicode_scalar_classes,
)


PREPARATION_INPUT_IDS = ("recommendation_request", "recommendation_decision")


class LeadContractError(ValueError):
    """A rejected Lead choice described only by closed, content-free rules."""

    def __init__(self, rules: tuple[DomainValidationRule, ...]) -> None:
        self.rules = rules
        self.provider_validation_findings = rules
        super().__init__("Lead result violates its offered constraints")


@dataclass(frozen=True, slots=True)
class LeadWireVariant:
    branch_key: str
    rules: SchemaRules
    decode: Callable[[dict[str, object]], dict[str, object]] = field(repr=False, compare=False)
    group_id: str | None = field(default=None, kw_only=True)
    logical_root: int | None = field(default=None, kw_only=True)


def _small_nonempty_selection(
    choices: tuple[str, ...], *, meaning: str,
) -> dict[str, object]:
    variants = [
        dict(
            closed_map_rule({item: {"type": "boolean", "enum": [True]} for item in subset}),
            description=meaning,
        )
        for count in range(1, len(choices) + 1)
        for subset in combinations(choices, count)
    ]
    if not variants:
        raise ValueError("nonempty finite selection has no eligible choice")
    return alternatives(variants)


def _fixed_selection(choices: tuple[str, ...]) -> dict[str, object]:
    return closed_map_rule({item: {"type": "boolean", "enum": [True]} for item in choices})


def _ordered_selection(value: object, choices: tuple[str, ...]) -> list[str]:
    if type(value) is not dict or not set(value).issubset(choices) or any(item is not True for item in value.values()):
        raise ValueError("selected finite choice rejected")
    return [item for item in choices if item in value]


def _scope_wire_rule(rule: AuthoringScopeRuleSet, template: Mapping[str, object],
                     scope: AuthoringScope | None, targets: tuple[str, ...] | None,
                     tasks: tuple[tuple[str, int], ...]) -> dict[str, object]:
    result = deepcopy(dict(template))
    properties = result["properties"]
    properties["activity_type"] = {
        **properties["activity_type"],
        "enum": [AUTHORING_ACTIVITIES[rule.activity.value]],
        "description": ENUM_MEANINGS[AUTHORING_ACTIVITIES[rule.activity.value]],
    }
    if "writing_intent" in properties:
        properties["writing_intent"] = {**properties["writing_intent"], "enum": [rule.intent.value]}
    properties["specialist_domains"] = {
        **_fixed_selection(tuple(item.value for item in rule.domains)),
        "description": field_description("specialist_domains"),
    }
    if targets is not None:
        properties["target_artifact_paths"] = _fixed_selection(targets)
    elif rule.activity is AuthoringActivity.WORKFLOW:
        properties["target_artifact_paths"] = {
            "type": "array", "items": {"type": "string"}, "minItems": 0, "maxItems": 0,
        }
    else:
        properties["target_artifact_paths"] = {"type": "array", "items": artifact_path_rule(rule.domains), "minItems": 0}
    if rule.activity is AuthoringActivity.WORKFLOW:
        for name in ("contributions", "dependency_slots"):
            properties[name] = {
                "type": "array", "items": closed_object({}), "minItems": 0, "maxItems": 0,
            }
            if name not in result["required"]:
                result["required"].append(name)
    elif "contributions" in properties:
        properties["contributions"] = closed_map_rule({item.domain.value: closed_object({
            "domain": {"type": "string", "enum": [item.domain.value]},
            "role": {"type": "string", "enum": [item.role.value]},
        }) for item in rule.contributions})
        draft_domains = tuple(item.domain.value for item in rule.contributions
                              if item.role is AuthoringContributionRole.DRAFT_OWNER)
        if scope is not None:
            properties["dependency_slots"] = closed_map_rule({slot.slot_id: closed_object({
                "slot_id": {"type": "string", "enum": [slot.slot_id]},
                "dependent_domains": _fixed_selection(tuple(domain.value for domain in slot.dependent_domains)),
            }) for slot in scope.dependency_slots})
        elif draft_domains:
            properties["dependency_slots"] = {"type": "array", "minItems": 0, "items": closed_object({
                "slot_id": {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}\z"},
                "dependent_domains": _small_nonempty_selection(
                    draft_domains, meaning=field_description("dependent_domains"),
                ),
            })}
        else:
            properties["dependency_slots"] = {
                "type": "array", "items": closed_object({}), "minItems": 0, "maxItems": 0,
            }
    if not tasks:
        raise ValueError("current Lead scope requires offered task pairs")
    properties["task_reference"] = alternatives([closed_object({
        "task_id": {"type": "string", "enum": [identity]},
        "task_revision": {"type": "integer", "enum": [revision]},
    }) for identity, revision in tasks])
    return result


def _decode_scope_wire(value: dict[str, object], rule: AuthoringScopeRuleSet,
                       scope: AuthoringScope | None, targets: tuple[str, ...] | None) -> None:
    value["specialist_domains"] = _ordered_selection(value["specialist_domains"], tuple(item.value for item in rule.domains))
    if targets is not None:
        value["target_artifact_paths"] = _ordered_selection(value["target_artifact_paths"], targets)
    if "contributions" in value:
        contributions = value["contributions"]
        value["contributions"] = [contributions[item.domain.value] for item in rule.contributions]
        slots = value["dependency_slots"]
        if scope is not None:
            value["dependency_slots"] = [{**slots[slot.slot_id], "dependent_domains": _ordered_selection(
                slots[slot.slot_id]["dependent_domains"], tuple(domain.value for domain in slot.dependent_domains))}
                for slot in scope.dependency_slots]
        else:
            domains = tuple(item.domain.value for item in rule.contributions if item.role is AuthoringContributionRole.DRAFT_OWNER)
            value["dependency_slots"] = [{**slot, "dependent_domains": _ordered_selection(slot["dependent_domains"], domains)} for slot in slots]


def _allocation_wire_rule(template: Mapping[str, object], *, active: tuple[str, ...],
                          rule: AuthoringScopeRuleSet | None, inputs: tuple[str, ...],
                          required: tuple[str, ...], reviewers: tuple[tuple[str, str], ...],
                          premise: bool = False, forced_inputs: tuple[str, ...] = (),
                          review_candidate_ids: tuple[str, ...] | None = None,
) -> dict[str, object]:
    if not active or not inputs or not set(required).issubset(inputs):
        raise ValueError("assignment has no legal current source graph")
    properties = deepcopy(dict(template))["properties"]
    instruction = nonempty_text_schema()
    leaf_template = properties["assignments"]["items"]
    candidates = leaf_template.get("anyOf", [leaf_template])
    assignments = {}
    candidate_choices = inputs if review_candidate_ids is None else review_candidate_ids
    for specialist in active:
        candidate = next((item for item in candidates if specialist in item.get("properties", {}).get("agent_id", {}).get("enum", ())), candidates[0])
        candidate_properties = candidate.get("properties", {})
        mode = ("premise_analysis" if premise else assignment_rule(rule, specialist).value if rule is not None else None)
        if mode is None:
            raise ValueError("assignment requires shared scope/stage roles")
        selected_instruction = deepcopy(candidate_properties.get("task_instructions", instruction))
        selected_instruction = _owned_visible_text_rule(
            selected_instruction, nonblank=mode == "review")
        forced = {identity for agent, identity in reviewers if agent == specialist} | set(forced_inputs)
        if not forced.issubset(inputs):
            raise ValueError("assignment obligations exceed current allocation inputs")
        review_basis = {"type": "null"}
        if mode == "review":
            review_basis = closed_object({
                "candidate_input_ids": string_array(
                    candidate_choices, minimum=1, maximum=len(candidate_choices)),
                "criteria": _owned_visible_text_rule(
                    nonempty_text_schema(), nonblank=True),
            })
        assignments[specialist] = closed_object({
            "task_instructions": selected_instruction,
            "assignment_type": {"type": "string", "enum": [ASSIGNMENT_TYPES[mode]]},
            "review_basis": review_basis,
            "input_anchor_id": {"type": "string", "enum": list(inputs)},
            "optional_inputs": closed_map_rule({identity: {"type": "boolean", **({"enum": [True]} if identity in forced else {})} for identity in inputs}),
        })
    properties["assignments"] = closed_map_rule(assignments)
    properties["required_input_recipients"] = closed_map_rule({identity: {"type": "string", "enum": list(active)} for identity in required})
    result = deepcopy(dict(template))
    result.setdefault(
        "description", field_description("specialist_consultation_proposal"))
    result.update(properties=properties, required=[
        name for name in properties if name in template["required"] or name == "required_input_recipients"])
    return result


def _decode_allocation_wire(value: dict[str, object], inputs: tuple[str, ...],
                            active: tuple[str, ...], *,
                            review_candidate_ids: tuple[str, ...] | None = None) -> None:
    assignments = value["assignments"]
    recipients = value.pop("required_input_recipients")
    decoded = []
    candidate_choices = inputs if review_candidate_ids is None else review_candidate_ids
    for specialist in active:
        item = assignments[specialist]
        candidate_ids = ()
        review_basis = None
        if item["assignment_type"] == ASSIGNMENT_TYPES["review"]:
            basis = item["review_basis"]
            candidate_ids = decode_finite_selection(
                basis["candidate_input_ids"], candidate_choices,
                minimum=1, maximum=len(candidate_choices))
            review_basis = {
                "candidate_input_ids": list(candidate_ids),
                "criteria": basis["criteria"],
            }
        edges = {item["input_anchor_id"], *candidate_ids,
                 *(identity for identity, recipient in recipients.items()
                   if recipient == specialist),
                 *(identity for identity, selected in item["optional_inputs"].items() if selected)}
        assignment = {"agent_id": specialist, "task_instructions": item["task_instructions"],
                      "assignment_type": item["assignment_type"],
                      "input_source_ids": [identity for identity in inputs if identity in edges]}
        if review_basis is not None:
            assignment["review_basis"] = review_basis
        decoded.append(assignment)
    value["assignments"] = decoded


def map_current_task_literals(value: object, offered: tuple[tuple[str, int], ...]) -> None:
    if type(value) is dict:
        if "task_revision" in value:
            identity = value.get("task_id")
            pair = next((pair for pair in offered if pair[0] == identity and value["task_revision"] == pair[1]), None)
            if pair is None or type(value["task_revision"]) is bool:
                raise ValueError("selected task literal rejected")
            value["task_revision"] = pair[1]
        for child in value.values():
            map_current_task_literals(child, offered)
    elif type(value) is list:
        for child in value:
            map_current_task_literals(child, offered)


_LeadValue = TypeVar("_LeadValue")
LeadContractRejection = ContractRejection


@dataclass(frozen=True, slots=True)
class LeadResultRuleSeed:
    """Internal domain rule input, never a provider-call binding capability."""
    contract_id: str
    domain_rules: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.contract_id) is not str or not self.contract_id or type(self.domain_rules) is not bytes:
            raise ValueError("Lead rule seed requires canonical domain declaration bytes")
        value = json.loads(self.domain_rules)
        if (type(value) is not dict or json.dumps(value, sort_keys=True, ensure_ascii=False,
            allow_nan=False, separators=(",", ":")).encode("utf-8") != self.domain_rules):
            raise ValueError("Lead rule seed must be canonical source-owned object rules")

    @classmethod
    def declare(cls, contract_id: str, schema: Mapping[str, object]) -> LeadResultRuleSeed:
        return cls(contract_id, json.dumps(dict(schema), sort_keys=True, ensure_ascii=False,
                                           allow_nan=False, separators=(",", ":")).encode("utf-8"))

    def schema_value(self) -> dict[str, object]:
        return json.loads(self.domain_rules)


@dataclass(frozen=True, slots=True)
class LeadDecodedResult:
    """Immutable current-codec domain result, after the complete selected graph."""
    domain_json: str

    def domain_value(self) -> dict[str, object]:
        return strict_json_object(self.domain_json)


def _has_free_dependency_slots(rules: SchemaRules) -> bool:
    """Only free native slot arrays need the residual cross-row ID rule."""
    # Closed object keys cannot preserve arbitrary IDs; whole-row uniqueness
    # would still allow one ID with different domain selections.
    root = rules.schema_value()
    seen: set[tuple[int, tuple[str, ...]]] = set()

    def visit(node: dict[str, object], path: tuple[str, ...]) -> bool:
        key = (id(node), path)
        if key in seen:
            return False
        seen.add(key)
        if "$ref" in node:
            return visit(root["$defs"][node["$ref"].removeprefix("#/$defs/")], path)
        if "anyOf" in node:
            return any(visit(child, path) for child in node["anyOf"])
        if not path:
            return node.get("type") == "array" and node.get("maxItems") != 0
        properties = node.get("properties", {})
        return path[0] in properties and visit(properties[path[0]], path[1:])

    return visit(root, ("response_details", "task_scope", "slots"))


def _require_dependency_slot_ids(
    value: object, *, rule=AuthoringScopeRuleSet.dependency_slot_id_rule,
    wire_path=diagnostic_to_wire, scope_path=project_lead_scope_diagnostic,
) -> None:
    """Use the domain rule before intrinsic scope construction can reject it."""
    if AuthoringScopeRuleSet.dependency_slot_id_rule is not rule:
        raise ValueError("frozen dependency-slot identity rule changed")
    def visit(node: object, path: str) -> None:
        if type(node) is dict:
            for name, child in node.items():
                child_path = path + "." + name
                if name == "authoring_scope" and child is not None:
                    finding = rule(tuple(slot["slot_id"] for slot in child["dependency_slots"]))
                    if finding is not None:
                        raise validation_failure(
                            ValueError("dependency slots must be unique"),
                            scope_path(wire_path(child_path + finding.path[1:])), finding.requirement,
                        )
                visit(child, child_path)
        elif type(node) is list:
            for index, child in enumerate(node):
                visit(child, f"{path}[{index}]")

    visit(value, "$")


def _require_intrinsic_lead_result(value: object) -> None:
    if type(value) is not dict or type(value.get("message")) is not str:
        raise TypeError("selected Lead response requires typed text")

    def visit(node: object) -> None:
        if type(node) is dict:
            for name, child in node.items():
                if child is None:
                    continue
                if name == "authoring_scope":
                    task_id, task_revision = None, None
                    if child["task"] is not None:
                        task_id, separator, raw_revision = child["task"].rpartition("@")
                        if not separator:
                            raise ValueError("current provider scope requires task@revision")
                        task_revision = int(raw_revision)
                    authoring_scope_from_dict(
                        {
                            "schema_version": 3,
                            **{key: item for key, item in child.items() if key != "task"},
                            "task_id": task_id,
                            "task_revision": task_revision,
                            "interpretation": value["message"],
                        }
                    )
                elif name == "authoring_assessment":
                    assessment_from_dict(child)
                elif name == "core_document_assessment":
                    core_assessment_from_dict(child)
                elif name == "premise_decision":
                    premise_question_intent_from_dict(child)
                visit(child)
        elif type(node) is list:
            for child in node:
                visit(child)

    visit(value)
    if value.get("status") in {"options", "clarification"} and "options" in value:
        premise_summary_from_dict(value)


@dataclass(frozen=True, slots=True)
class LeadResultContractOwner(ContractOwner[LeadDecodedResult]):
    """The existing pure owner declares a family graph, codec and named hooks."""
    variant: LeadWireVariant
    task_revisions: tuple[tuple[str, int], ...]
    checks: tuple[NamedHostCheck[LeadDecodedResult], ...] = field(repr=False, compare=False)
    owner_id: str = field(init=False)
    owner_revision: str = field(init=False, default="848-v3:typed-family-owner")
    _declared_checks: tuple[NamedHostCheck[LeadDecodedResult], ...] = field(init=False, repr=False, compare=False)
    _declared_decode: Callable[[ValidatedValue], LeadDecodedResult] = field(init=False, repr=False, compare=False)
    _declared_inputs: tuple[tuple[object, ...], tuple[object, ...]] = field(init=False, repr=False, compare=False)

    def __init_subclass__(cls, **kwargs):
        raise TypeError("Lead result owner declaration cannot be overridden")

    def __post_init__(self) -> None:
        if (type(self.variant) is not LeadWireVariant or type(self.variant.rules) is not SchemaRules
                or not callable(self.variant.decode) or inspect.iscoroutinefunction(self.variant.decode)
                or type(self.checks) is not tuple
                or any(type(item) is not NamedHostCheck for item in self.checks)
                or len(self.checks) != 1 or self.checks[0].name != "owner.authorized_final"
                or self.checks[0].phase != "authorized"):
            raise ValueError("incomplete Lead result owner declaration")
        if (type(self.task_revisions) is not tuple
                or any(type(item) is not tuple or len(item) != 2
                       or type(item[0]) is not str or type(item[1]) is not int
                       for item in self.task_revisions)):
            raise ValueError("Lead task revisions must be immutable declared pairs")
        object.__setattr__(self, "owner_id", "lead.result." + self.variant.branch_key)
        facts = json.dumps({"requirements": [{"path": "$.response_text", "rule": "VISIBLE_TEXT_UTF8",
            "expected": {"minimum_utf8_bytes": 1, "maximum_utf8_bytes": COLLABORATION_VISIBLE_TEXT_MAX_UTF8_BYTES,
                         "encoding": "STRICT_UTF8"}}]}, sort_keys=True, ensure_ascii=False, allow_nan=False,
            separators=(",", ":")).encode("utf-8")
        visible = NamedHostCheck("owner.visible_text_utf8", "848-v3:existing-visible-text-cap", "selected",
                                SelectedHostContext, facts, self._require_visible_text)
        free_slots = _has_free_dependency_slots(self.variant.rules)
        slot_checks = ()
        check_slots = _require_dependency_slot_ids
        if free_slots:
            finding = AuthoringScopeRuleSet.dependency_slot_id_rule()
            slot_facts = json.dumps({"requirements": [{
                "path": project_lead_scope_diagnostic(diagnostic_to_wire("$.authoring_scope" + finding.path[1:])),
                "rule": "DEPENDENCY_SLOT_IDS_UNIQUE",
                "expected": {"requirement": finding.requirement, "comparison": "SLOT_ID_WITHIN_ONE_SCOPE"},
            }]}, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")

            def selected_slots(value: LeadDecodedResult, context: HostCheckContext, *, check_slots=check_slots) -> None:
                check_slots(value.domain_value())

            slot_checks = (NamedHostCheck(
                "owner.dependency_slot_ids_unique", "848-v1:dependency-slot-identity", "selected",
                SelectedHostContext, slot_facts, selected_slots,
                dependencies=(AuthoringScopeRuleSet.dependency_slot_id_rule,),
            ),)
        object.__setattr__(self, "_declared_checks", (visible, *slot_checks, *self.checks))
        variant_decode = self.variant.decode
        task_revisions = self.task_revisions

        def decode_typed(value: ValidatedValue) -> LeadDecodedResult:
            if type(value) is not ValidatedValue:
                raise TypeError("Lead codec requires a selected whole-graph input")
            body = value.as_object()
            map_current_task_literals(body, task_revisions)
            domain = variant_decode(body)
            if free_slots:
                check_slots(domain)
            _require_intrinsic_lead_result(domain)
            return LeadDecodedResult(json.dumps(domain, ensure_ascii=False, allow_nan=False, separators=(",", ":")))
        object.__setattr__(self, "_declared_decode", decode_typed)
        object.__setattr__(self, "_declared_inputs", self._capture_declaration())

    def _capture_declaration(self) -> tuple[tuple[object, ...], tuple[object, ...]]:
        references = (self.variant, self.variant.rules, self.variant.decode,
                      self.task_revisions, self.checks, self._declared_checks,
                      self._declared_decode, type(self)._require_visible_text, visible_text,
                      *(item for check in self._declared_checks
                        for item in (check, check.check, check.dependencies)))
        values = (self.owner_id, self.owner_revision, self.variant.branch_key,
                  self.variant.group_id, self.variant.logical_root,
                  self.variant.rules.canonical_bytes, self.variant.rules.maximum_bytes,
                  COLLABORATION_VISIBLE_TEXT_MAX_UTF8_BYTES,
                  tuple((check.name, check.revision, check.phase, check.context_type,
                         check.corrective_facts) for check in self._declared_checks))
        return references, values

    def _require_declaration(self) -> None:
        expected_references, expected_values = self._declared_inputs
        try:
            references, values = self._capture_declaration()
        except (AttributeError, TypeError) as exc:
            raise ValueError("frozen Lead owner declaration changed") from exc
        if (len(references) != len(expected_references)
                or any(current is not expected
                       for current, expected in zip(references, expected_references))
                or values != expected_values):
            raise ValueError("frozen Lead owner declaration changed")

    def declare_rules(self) -> SchemaRules:
        self._require_declaration()
        return self.variant.rules

    def typed_decoder(self) -> TypedDecoder[LeadDecodedResult]:
        self._require_declaration()
        return TypedDecoder("lead.current_codec", "848-v3:typed-family-owner", self._declared_decode,
                            (self.variant.decode, *_lead_codec_dependencies()))

    def named_host_checks(self) -> tuple[NamedHostCheck[LeadDecodedResult], ...]:
        self._require_declaration()
        return self._declared_checks

    def _require_visible_text(self, value: LeadDecodedResult, context: HostCheckContext) -> None:
        if not visible_text(value.domain_value().get("message")):
            raise validation_failure(ValueError(), "$.response_text", "strict UTF8 visible text within existing byte limit")


def _lead_codec_dependencies() -> tuple[Callable, ...]:
    """Prepared semantic codecs and final owners attest the same domain sources."""
    return (schema_fragment_findings, artifact_source_operation_rule,
            artifact_kind_path_rule, canonical_artifact_kind, artifact_domain,
            authoring_scope_rules, finite_authoring_scope_rules, legal_specialist_rosters,
            assignment_rule, AuthoringScopeRuleSet.__post_init__,
            LeadProviderConstraints._allocation_input_ids, lead_outcome_scope_paths,
            normalize_result, map_current_task_literals, _require_intrinsic_lead_result,
            project_lead_scope_schema, restore_lead_scope_result, project_lead_scope_diagnostic,
            diagnostic_to_wire, AuthoringScopeRuleSet.dependency_slot_id_rule,
            _has_free_dependency_slots, _require_dependency_slot_ids)


@dataclass(frozen=True, slots=True)
class LeadStagePolicy:
    """Owner-captured stage/call/choice facts, with no independently supplied graph."""
    stage: str
    physical_call_id: str
    task_revisions: tuple[tuple[str, int], ...]
    language: str
    offer_digest: str
    choices: tuple[tuple[str, tuple[str, ...]], ...]
    eligibility_offer_digest: str
    logical_origin_call_id: str
    owner_declarations: tuple[ContractOwner, ...] = field(kw_only=True, repr=False, compare=False)
    maximum_schema_bytes: int = field(default=65_536, kw_only=True)
    maximum_index_schema_bytes: int = field(default=MAX_PROVIDER_SCHEMA_UTF8_BYTES, kw_only=True)
    wire_root_path: str = field(default="$.agent_action.task_result", kw_only=True)

    def __post_init__(self) -> None:
        if (any(type(value) is not str or not value for value in
                (self.stage, self.physical_call_id, self.language, self.logical_origin_call_id))
                or self.maximum_schema_bytes not in {65_536, 262_144}
                or type(self.maximum_index_schema_bytes) is not int
                or self.maximum_index_schema_bytes != MAX_PROVIDER_SCHEMA_UTF8_BYTES
                or self.wire_root_path not in {"$", "$.agent_action", "$.agent_action.task_result"}
                or any(type(value) is not str or len(value) != 64 or any(
                    char not in "0123456789abcdef" for char in value)
                    for value in (self.offer_digest, self.eligibility_offer_digest))
                or type(self.task_revisions) is not tuple
                or any(type(pair) is not tuple or len(pair) != 2 or type(pair[0]) is not str
                       or not pair[0] or type(pair[1]) is not int or pair[1] < 1
                       for pair in self.task_revisions)
                or len(set(self.task_revisions)) != len(self.task_revisions)
                or type(self.choices) is not tuple
                or any(type(pair) is not tuple or len(pair) != 2 or type(pair[0]) is not str
                       or type(pair[1]) is not tuple or any(type(item) is not str for item in pair[1])
                       for pair in self.choices)
                or len({pair[0] for pair in self.choices}) != len(self.choices)):
            raise ValueError("incomplete current Lead physical/logical policy")
        if (type(self.owner_declarations) is not tuple or not self.owner_declarations
                or any(not isinstance(owner, ContractOwner) for owner in self.owner_declarations)
                or len({owner.owner_id for owner in self.owner_declarations}) != len(self.owner_declarations)):
            raise ValueError("stage requires its complete immutable typed owner declaration")

    def identity_for(self, owner_id: str) -> ContractIdentity:
        value = {
            "stage": self.stage, "physical_call_id": self.physical_call_id,
            "task_revisions": self.task_revisions, "language": self.language,
            "offer_digest": self.offer_digest, "eligibility_offer_digest": self.eligibility_offer_digest,
            "logical_origin_call_id": self.logical_origin_call_id, "choices": self.choices,
            "maximum_schema_bytes": self.maximum_schema_bytes, "wire_root_path": self.wire_root_path,
            "branch": owner_id,
            "owner_set_digest": hashlib.sha256(json.dumps(tuple((owner.owner_id, id(owner))
                for owner in self.owner_declarations), separators=(",", ":")).encode("utf-8")).hexdigest(),
            "maximum_index_schema_bytes": self.maximum_index_schema_bytes,
        }
        return ContractIdentity(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                                           separators=(",", ":")).encode("utf-8"),
                                self.maximum_schema_bytes, self.wire_root_path)



def _compile_closed_choices(
    schema: Mapping[str, object],
) -> dict[str, object]:
    """Certify a same-payload one-property distribution of closed alternatives."""
    original = deepcopy(dict(schema))
    original_fingerprints = schema_rule_fingerprints(original)

    def plain_json_graph(value: object) -> bool:
        pending = [(value, False)]
        visiting: set[int] = set()
        complete: set[int] = set()
        try:
            while pending:
                node, leaving = pending.pop()
                kind = type(node)
                if kind is dict or kind is list:
                    identity = id(node)
                    if leaving:
                        visiting.remove(identity)
                        complete.add(identity)
                        continue
                    if identity in visiting:
                        return False
                    if identity in complete:
                        continue
                    visiting.add(identity)
                    pending.append((node, True))
                    if kind is dict:
                        if any(type(name) is not str for name in node):
                            return False
                        pending.extend((child, False) for child in node.values())
                    else:
                        pending.extend((child, False) for child in node)
                elif node is None or kind is str or kind is int or kind is bool:
                    continue
                elif kind is not float or node.hex() in ("nan", "inf", "-inf"):
                    return False
        except Exception:
            return False
        return True

    # The complete proof above remains authoritative for this private snapshot.
    plain_context = plain_json_graph(original)
    working = deepcopy(original)
    fingerprints: dict[str, str] = {}
    grouping_prefill: tuple[list, list[str]] | None = None
    trace: list[tuple[tuple[object, ...], str, tuple[tuple[int, ...], ...],
                      bool, dict[str, object], str]] = []

    def encoded(value: object) -> str:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                          separators=(",", ":"))

    probe_definitions = dict(original.get("$defs", {}))
    probe_names: dict[str, str] = {}

    def collect_original_rules(node: object) -> None:
        if type(node) is not dict:
            return
        key = encoded(node)
        if key in probe_names:
            return
        name = f"p{len(probe_names)}"
        while name in probe_definitions:
            name += "_"
        probe_names[key] = name
        probe_definitions[name] = node
        for field in sorted(node.get("properties", {})):
            collect_original_rules(node["properties"][field])
        for child in node.get("anyOf", ()):
            collect_original_rules(child)
        if "items" in node:
            collect_original_rules(node["items"])

    collect_original_rules(original)
    for name in sorted(original.get("$defs", {})):
        collect_original_rules(original["$defs"][name])
    batch_root, batch_fingerprints = schema_rule_fingerprints(
        {**original, "$defs": probe_definitions})
    if batch_root != original_fingerprints[0] or any(
            batch_fingerprints[name] != value
            for name, value in original_fingerprints[1].items()):
        raise ValueError("closed-choice fingerprint batch changes its original context")
    fingerprints.update({key: batch_fingerprints[name] for key, name in probe_names.items()})

    def fingerprint(node: Mapping[str, object]) -> str:
        nonlocal grouping_prefill
        key = encoded(node)
        if key not in fingerprints:
            if grouping_prefill is not None:
                choices, names = grouping_prefill
                grouping_prefill = None
                prefill_grouping_fingerprints(choices, names)
            if key not in fingerprints:
                fingerprints[key] = (schema_rule_fingerprint(original, selected=node)
                                     if plain_context and plain_json_graph(node)
                                     else schema_rule_fingerprints(original, selected=node)[0])
        return fingerprints[key]

    def prefill_grouping_fingerprints(choices: list, names: list[str]) -> None:
        nonlocal fingerprints
        if len(names) < 2:
            return
        try:
            # Check source trees before intentional probe aliases are introduced.
            pending: list[object] = [original, choices]
            seen: set[int] = set()
            while pending:
                value = pending.pop()
                if type(value) in (dict, list):
                    if id(value) in seen:
                        return
                    seen.add(id(value))
                    if type(value) is dict:
                        if any(type(key) is not str for key in value):
                            return
                        pending.extend(value.values())
                    else:
                        pending.extend(value)
                elif value is not None and type(value) not in (bool, int, float, str):
                    return
            missing: dict[str, Mapping[str, object]] = {}
            for name in names:
                for choice in choices:
                    if closed(choice) and name in choice["properties"]:
                        for field, child in sorted(choice["properties"].items()):
                            if field == name:
                                continue
                            key = encoded(child)
                            if key not in fingerprints and key not in missing:
                                missing[key] = child
            if len(missing) < 2:
                return
            definitions = dict(original.get("$defs", {}))
            probe_keys: dict[str, str] = {}
            for key, node in missing.items():
                name = f"_grouping_{len(probe_keys)}"
                while name in definitions:
                    name += "_"
                definitions[name] = node
                probe_keys[key] = name
            _, digests = schema_rule_fingerprints({
                "$ref": f"#/$defs/{next(iter(probe_keys.values()))}",
                "$defs": definitions,
            })
            if any(digests[name] != expected
                   for name, expected in original_fingerprints[1].items()):
                return
            additions = {key: digests[name] for key, name in probe_keys.items()}
            if any(type(value) is not str for value in additions.values()):
                return
            updated = {**fingerprints, **additions}
        except Exception:
            # The unchanged ordered queries retain original failure authority.
            return
        fingerprints = updated

    try:
        final_costs: dict[str, int] | None = {}
    except MemoryError:
        final_costs = None

    def final_cost(graph: Mapping[str, object]) -> int:
        key: str | None = None
        if final_costs is not None:
            try:
                pending = [graph]
                containers: set[int] = set()
                while pending:
                    value = pending.pop()
                    if type(value) is dict:
                        if any(type(name) is not str for name in value):
                            break
                        identity = id(value)
                        if identity in containers:
                            break
                        containers.add(identity)
                        pending.extend(value.values())
                    elif type(value) is list:
                        identity = id(value)
                        if identity in containers:
                            break
                        containers.add(identity)
                        pending.extend(value)
                    elif value is not None and type(value) not in (str, int, float, bool):
                        break
                else:
                    key = json.dumps(
                        graph, sort_keys=False, ensure_ascii=False, allow_nan=False,
                        separators=(",", ":"),
                    )
                    cached_cost = final_costs.get(key)
                    if cached_cost is not None:
                        return cached_cost
            except (TypeError, ValueError, OverflowError, RecursionError, MemoryError):
                key = None
        compact = factor_rule_schema(project_lead_scope_schema(factor_rule_schema(graph)))
        cost = len(encoded(compact).encode("utf-8", "strict"))
        if final_costs is not None and key is not None:
            try:
                final_costs[key] = cost
            except MemoryError:
                pass
        return cost

    def at(graph: dict[str, object], path: tuple[object, ...]) -> dict[str, object]:
        node = graph
        for key in path:
            node = node[key]
        return node

    def replace_at(graph: dict[str, object], path: tuple[object, ...], node: dict):
        if not path:
            return node
        parent = graph
        for key in path[:-1]:
            parent = parent[key]
        parent[path[-1]] = node
        return graph

    def paths(node: dict[str, object], path: tuple[object, ...] = ()):
        for name, child in node.get("properties", {}).items():
            yield from paths(child, path + ("properties", name))
        if "items" in node:
            yield from paths(node["items"], path + ("items",))
        for number, child in enumerate(node.get("anyOf", ())):
            yield from paths(child, path + ("anyOf", number))
        if "anyOf" in node:
            yield path

    def closed(node: dict[str, object]) -> bool:
        properties = node.get("properties")
        required = node.get("required")
        return (node.get("type") == "object" and node.get("additionalProperties") is False
                and type(properties) is dict and "variant_id" not in properties and type(required) is list
                and len(required) == len(set(required)) and set(required) == set(properties)
                and not set(node) - {"type", "properties", "required",
                                     "additionalProperties", "description", "x-swa-property-policy"})

    size = initial_size = final_cost(working)
    while True:
        accepted = False
        for path in paths(working):
            before = at(working, path)
            choices = before["anyOf"]
            names = sorted({name for choice in choices if closed(choice)
                            for name in choice["properties"]})
            grouping_prefill = (choices, names)
            best = None
            best_size = size
            for name in names:
                groups: dict[object, list[int]] = {}
                for number, choice in enumerate(choices):
                    if not closed(choice) or name not in choice["properties"]:
                        key = ("unchanged", number)
                    else:
                        common = tuple((field, fingerprint(child))
                                       for field, child in sorted(choice["properties"].items())
                                       if field != name)
                        metadata = {field: value for field, value in choice.items()
                                    if field != "properties"}
                        key = (encoded(metadata), tuple(sorted(choice["properties"])), common)
                    groups.setdefault(key, []).append(number)
                partition = tuple(tuple(numbers) for numbers in groups.values())
                if all(len(numbers) == 1 for numbers in partition):
                    continue
                distributed = []
                for numbers in partition:
                    branch = deepcopy(choices[numbers[0]])
                    if len(numbers) > 1:
                        branch["properties"][name] = {"anyOf": [
                            deepcopy(choices[number]["properties"][name]) for number in numbers]}
                    distributed.append(branch)
                collapsed = len(distributed) == 1
                if collapsed:
                    annotations = {key: value for key, value in before.items() if key != "anyOf"}
                    if set(annotations) - {"description", "$defs"} or any(
                        key in distributed[0] and distributed[0][key] != value
                        for key, value in annotations.items()
                    ):
                        continue
                    after = {**distributed[0], **annotations}
                else:
                    after = {**before, "anyOf": distributed}
                trial = replace_at(deepcopy(working), path, after)
                trial_size = final_cost(trial)
                if trial_size < best_size:
                    best_size = trial_size
                    best = (name, partition, collapsed, after, trial)
            if best is not None:
                name, partition, collapsed, after, working = best
                trace.append((path, name, partition, collapsed, deepcopy(before),
                              fingerprint(after)))
                size = best_size
                accepted = True
                break
        if not accepted:
            break

    inverse = deepcopy(working)
    for path, name, partition, collapsed, before, after_fingerprint in reversed(trace):
        after = at(inverse, path)
        if fingerprint(after) != after_fingerprint:
            raise ValueError("closed-choice inverse has a changed derived occurrence")
        if collapsed:
            branch = deepcopy(after)
            original_branch = before["anyOf"][partition[0][0]]
            for key, value in before.items():
                if key == "anyOf":
                    continue
                if key not in branch or branch[key] != value:
                    raise ValueError("closed-choice inverse changes a wrapper annotation")
                if key not in original_branch:
                    del branch[key]
            distributed = [branch]
        else:
            distributed = after["anyOf"]
        if len(distributed) != len(partition):
            raise ValueError("closed-choice inverse has an incomplete partition")
        rebuilt = [None] * len(before["anyOf"])
        for branch, numbers in zip(distributed, partition, strict=True):
            if len(numbers) == 1:
                values = [branch]
            else:
                varying = branch["properties"][name]
                if set(varying) != {"anyOf"} or len(varying["anyOf"]) != len(numbers):
                    raise ValueError("closed-choice inverse has a changed varying rule")
                values = []
                for child in varying["anyOf"]:
                    value = deepcopy(branch)
                    value["properties"][name] = deepcopy(child)
                    values.append(value)
            for number, value in zip(numbers, values, strict=True):
                if rebuilt[number] is not None or fingerprint(value) != fingerprint(
                    before["anyOf"][number]
                ):
                    raise ValueError("closed-choice inverse changes an original alternative")
                rebuilt[number] = value
        restored = {**before, "anyOf": rebuilt}
        if any(value is None for value in rebuilt) or fingerprint(restored) != fingerprint(before):
            raise ValueError("closed-choice inverse does not recover its ordered source")
        # Keep the captured occurrence's alias spelling for earlier descendant traces.
        inverse = replace_at(inverse, path, deepcopy(before))
    if schema_rule_fingerprints(inverse) != original_fingerprints:
        raise ValueError("closed-choice inverse changes the root or an input definition")
    if working.get("$defs", {}) != original.get("$defs", {}):
        raise ValueError("closed-choice compiler changes an input definition")
    if final_cost(working) != size or (trace and size >= initial_size):
        raise ValueError("closed-choice compiler does not decrease complete final UTF-8 bytes")
    return working


def factor_rule_schema(schema: Mapping[str, object]) -> dict[str, object]:
    """Share equivalent acyclic rule nodes without expanding or pruning the graph."""
    root = strict_json_object(json.dumps(dict(schema), ensure_ascii=False, allow_nan=False))
    original_fingerprint, definition_fingerprints = schema_rule_fingerprints(root)
    source_definitions = root.pop("$defs", {})
    names = {identity: f"d{number}" for number, identity in enumerate(sorted(set(definition_fingerprints.values())))}
    aliases = {name: names[identity] for name, identity in definition_fingerprints.items()}

    def rename(node: object, symbols: Mapping[str, str]) -> object:
        if type(node) is not dict:
            return node
        result = dict(node)
        if "$ref" in result:
            result["$ref"] = "#/$defs/" + symbols[result["$ref"][len("#/$defs/"):]]
            return result
        if type(result.get("properties")) is dict:
            result["properties"] = {name: rename(child, symbols) for name, child in result["properties"].items()}
        if type(result.get("anyOf")) is list:
            result["anyOf"] = [rename(child, symbols) for child in result["anyOf"]]
        if "items" in result:
            result["items"] = rename(result["items"], symbols)
        return result

    definitions: dict[str, object] = {}
    for name, node in sorted(source_definitions.items()):
        # Bare aliases resolve to their concrete target, avoiding a self-alias after merging.
        while set(node) == {"$ref"}:
            node = source_definitions[node["$ref"][len("#/$defs/"):]]
        definitions.setdefault(aliases[name], rename(node, aliases))
    root = rename(root, aliases)
    counts: dict[str, int] = {}
    nodes: dict[str, object] = {}
    node_keys: dict[int, str] = {}

    def children(node: Mapping[str, object]):
        properties = node.get("properties")
        if type(properties) is dict:
            yield from properties.values()
        variants = node.get("anyOf")
        if type(variants) is list:
            yield from variants
        if "items" in node:
            yield node["items"]

    def key(node: object) -> str:
        return json.dumps(node, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)

    fingerprint_definitions = dict(definitions)
    fingerprint_names: dict[int, str] = {}

    def collect_fingerprint_nodes(node: object) -> None:
        if type(node) is not dict or id(node) in fingerprint_names:
            return
        name = f"p{len(fingerprint_names)}"
        if name in fingerprint_definitions:
            raise ValueError("schema fingerprint scratch symbol conflicts with an input definition")
        fingerprint_names[id(node)] = name
        fingerprint_definitions[name] = node
        for name in sorted(node.get("properties", {})):
            collect_fingerprint_nodes(node["properties"][name])
        for child in node.get("anyOf", ()):
            collect_fingerprint_nodes(child)
        if "items" in node:
            collect_fingerprint_nodes(node["items"])

    collect_fingerprint_nodes(root)
    for name in sorted(definitions):
        collect_fingerprint_nodes(definitions[name])
    _, node_fingerprints = schema_rule_fingerprints(
        {**root, "$defs": fingerprint_definitions})
    node_keys.update({identity: node_fingerprints[name]
                      for identity, name in fingerprint_names.items()})

    def source_key(node: object) -> str:
        return node_keys[id(node)]

    def count(node: object, *, outer: bool = False) -> None:
        if type(node) is not dict:
            return
        identity = source_key(node)
        if not outer and "$ref" not in node:
            counts[identity] = counts.get(identity, 0) + 1
            nodes[identity] = node
        for child in children(node):
            count(child)

    count(root, outer=True)
    for node in definitions.values():
        count(node, outer=True)
    existing = {source_key(node): name for name, node in definitions.items()}
    source_order: dict[str, tuple[int, str]] = {}
    for source_identity, source_node in nodes.items():
        source_literal = key(source_node)
        source_order[source_identity] = (len(source_literal.encode("utf-8")), source_literal)
    shared: dict[str, str] = {}
    # Preserve the current large-node seed; additional bindings must reduce its actual bytes.
    for identity, times in sorted(counts.items(), key=lambda item: source_order[item[0]][1]):
        if source_order[identity][0] < 80:
            continue
        if identity in existing:
            shared[identity] = existing[identity]
        elif times > 1:
            shared[identity] = f"n{len(shared)}"

    def render(node: object, *, own: str | None = None) -> object:
        if type(node) is not dict:
            return node
        identity = source_key(node)
        if identity in shared and identity != own:
            return {"$ref": "#/$defs/" + shared[identity]}
        result = dict(node)
        if type(result.get("properties")) is dict:
            result["properties"] = {name: render(child) for name, child in result["properties"].items()}
        if type(result.get("anyOf")) is list:
            result["anyOf"] = [render(child) for child in result["anyOf"]]
        if "items" in result:
            result["items"] = render(result["items"])
        return result

    def symbol_aliases(reference_counts: Mapping[str, int]) -> dict[str, str]:
        alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"

        def short_name(number: int) -> str:
            name = ""
            while True:
                number, digit = divmod(number, len(alphabet))
                name = alphabet[digit] + name
                if not number:
                    return name
                number -= 1

        return {
            name: short_name(number)
            for number, name in enumerate(sorted(reference_counts, key=lambda name: (-reference_counts[name], name)))
        }

    def compact_symbols(graph: dict[str, object]) -> tuple[dict[str, object], dict[str, str]]:
        """Use the same local-symbol layout for every cost trial and final graph."""
        if not graph.get("$defs"):
            return graph, {}
        reference_counts = dict.fromkeys(graph["$defs"], 0)

        def count_references(node: object) -> None:
            if type(node) is not dict:
                return
            if "$ref" in node:
                reference_counts[node["$ref"][len("#/$defs/"):]] += 1
                return
            for child in children(node):
                count_references(child)

        count_references(graph)
        for node in graph["$defs"].values():
            count_references(node)
        aliases = symbol_aliases(reference_counts)
        compact = rename(graph, aliases)
        compact["$defs"] = {aliases[name]: rename(node, aliases) for name, node in graph["$defs"].items()}
        return compact, aliases

    def assemble() -> tuple[dict[str, object], dict[str, str]]:
        result = render(root, own=source_key(root))
        assert type(result) is dict
        declarations = {name: render(node, own=source_key(node)) for name, node in definitions.items()}
        for identity, name in shared.items():
            if name not in declarations:
                declarations[name] = render(nodes[identity], own=identity)
        if declarations:
            result["$defs"] = declarations
        return compact_symbols(result)

    quoted_sizes: dict[str, int] = {}
    fixed_sizes: dict[int, int] = {}

    def quoted_size(value: str) -> int:
        if value not in quoted_sizes:
            quoted_sizes[value] = len(key(value).encode("utf-8"))
        return quoted_sizes[value]

    def fixed_size(node: dict[str, object]) -> int:
        """Count unchanged bytes; the detached source keeps every node alive."""
        identity = id(node)
        if identity not in fixed_sizes:
            size = 2 + max(0, len(node) - 1)
            for name, value in node.items():
                size += quoted_size(name) + 1
                if name == "properties" and type(value) is dict:
                    size += 2 + max(0, len(value) - 1)
                    size += sum(quoted_size(field) + 1 for field in value)
                elif name == "anyOf" and type(value) is list:
                    size += 2 + max(0, len(value) - 1)
                elif name != "items":
                    size += len(key(value).encode("utf-8"))
            fixed_sizes[identity] = size
        return fixed_sizes[identity]

    def measure(node: object, reference_counts: dict[str, int], *,
                own: str | None = None, count_refs: bool = True) -> int:
        if type(node) is not dict:
            return len(key(node).encode("utf-8"))
        identity = source_key(node)
        if identity in shared and identity != own:
            name = shared[identity]
            if count_refs:
                reference_counts[name] += 1
            return 2 + quoted_size("$ref") + 1 + quoted_size("#/$defs/" + name)
        size = fixed_size(node)
        if "$ref" in node:
            if count_refs:
                reference_counts[node["$ref"][len("#/$defs/"):]] += 1
            count_refs = False
        for child in children(node):
            size += measure(child, reference_counts, count_refs=count_refs)
        return size

    def trial_size() -> tuple[int, dict[str, str]]:
        """Measure the exact rendered/renamed trial without materializing it."""
        declarations = {name: (node, source_key(node)) for name, node in definitions.items()}
        for identity, name in shared.items():
            if name not in declarations:
                declarations[name] = (nodes[identity], identity)
        reference_counts = dict.fromkeys(declarations, 0)
        size = measure(root, reference_counts, own=source_key(root))
        if declarations:
            size += (1 if root else 0) + quoted_size("$defs") + 1
            size += 2 + max(0, len(declarations) - 1)
            for name, (node, own) in declarations.items():
                size += quoted_size(name) + 1 + measure(node, reference_counts, own=own)
        aliases = symbol_aliases(reference_counts)
        for name, alias in aliases.items():
            size += quoted_size(alias) - quoted_size(name)
            size += reference_counts[name] * (
                quoted_size("#/$defs/" + alias) - quoted_size("#/$defs/" + name))
        return size, aliases

    def share_annotations(graph: dict[str, object]) -> dict[str, object]:
        literal_ids: dict[tuple[object, ...], int] = {}
        full_keys: dict[int, int] = {}
        body_keys: dict[int, int] = {}

        def literal_key(value: object) -> int:
            if type(value) in {dict, list}:
                identity = id(value)
                if identity in full_keys:
                    return full_keys[identity]
                if type(value) is dict:
                    pairs = tuple((field, literal_key(child))
                                  for field, child in sorted(value.items()))
                    signature = ("object", pairs)
                    body = ("object", tuple(pair for pair in pairs
                                            if pair[0] != "description"))
                    body_keys[identity] = literal_ids.setdefault(body, len(literal_ids))
                else:
                    signature = ("array", tuple(literal_key(child) for child in value))
                result = literal_ids.setdefault(signature, len(literal_ids))
                full_keys[identity] = result
                return result
            signature = ("scalar", key(value))
            return literal_ids.setdefault(signature, len(literal_ids))

        groups: dict[int, list[object]] = {}

        def collect(node: object) -> None:
            if type(node) is not dict:
                return
            if "$defs" not in node:
                groups.setdefault(body_keys[id(node)], []).append(node)
            for child in children(node):
                collect(child)
            for child in node.get("$defs", {}).values():
                collect(child)

        literal_key(graph)
        collect(graph)
        used = set(graph.get("$defs", {}))
        selected: dict[int, str] = {}
        eligible: list[tuple[str, int, list[object]]] = []
        for identity, occurrences in groups.items():
            meanings = {key({"description": node["description"]}) if "description" in node
                        else "" for node in occurrences}
            if len(occurrences) < 2 or len(meanings) < 2:
                continue
            representative = occurrences[0]
            body = key({field: value for field, value in representative.items()
                        if field != "description"})
            eligible.append((body, identity, occurrences))
        for body, identity, occurrences in sorted(eligible, key=lambda item: item[0]):
            number = len(selected)
            name = f"a{number}"
            while name in used:
                number += 1
                name = f"a{number}"
            reference = key({"$ref": "#/$defs/" + name})
            gain = (len(occurrences) * (len(body.encode("utf-8"))
                    - len(reference.encode("utf-8")))
                    - len(body.encode("utf-8")) - len(key(name).encode("utf-8")) - 2)
            if gain > 0:
                selected[identity] = name
                used.add(name)
        targets: dict[str, object] = {}

        def visit(node: object) -> object:
            if type(node) is not dict:
                return node
            name = selected.get(body_keys[id(node)]) if "$defs" not in node else None
            result = dict(node)
            if "properties" in node:
                result["properties"] = {name: visit(child)
                                        for name, child in node["properties"].items()}
            if "anyOf" in node:
                result["anyOf"] = [visit(child) for child in node["anyOf"]]
            if "items" in node:
                result["items"] = visit(node["items"])
            if "$defs" in node:
                result["$defs"] = {name: visit(child) for name, child in node["$defs"].items()}
            if name is not None:
                target = {field: value for field, value in result.items()
                          if field != "description"}
                if name in targets and key(targets[name]) != key(target):
                    raise ValueError("annotation sharing changed an exact rule body")
                targets[name] = target
                result = {"$ref": "#/$defs/" + name}
                if "description" in node:
                    result["description"] = node["description"]
            return result

        result = visit(graph)
        assert type(result) is dict
        if targets:
            result.setdefault("$defs", {}).update(targets)
        return result

    seeded = dict(shared)
    shared.clear()
    baseline, baseline_aliases = assemble()
    baseline_bytes = len(key(baseline).encode("utf-8"))
    current_bytes, expected_aliases = baseline_bytes, baseline_aliases
    if seeded:
        shared.update(seeded)
        current_bytes, expected_aliases = trial_size()
        if current_bytes >= baseline_bytes:
            shared.clear()
            current_bytes, expected_aliases = baseline_bytes, baseline_aliases
    for identity, times in sorted(counts.items(), key=lambda item: source_order[item[0]]):
        if identity in shared or (times <= 1 and identity not in existing):
            continue
        shared[identity] = existing.get(identity, f"n{len(shared)}")
        candidate_bytes, candidate_aliases = trial_size()
        if candidate_bytes < current_bytes:
            current_bytes, expected_aliases = candidate_bytes, candidate_aliases
        else:
            del shared[identity]
    result, final_aliases = assemble() if shared else (baseline, baseline_aliases)
    if (final_aliases != expected_aliases or len(key(result).encode("utf-8")) != current_bytes or current_bytes > baseline_bytes
            or (shared and current_bytes >= baseline_bytes)):
        raise ValueError("schema sharing does not preserve its complete UTF-8 cost guard")
    candidate, candidate_aliases = compact_symbols(share_annotations(result))
    candidate_bytes = len(key(candidate).encode("utf-8"))
    if candidate_bytes < current_bytes:
        result, current_bytes = candidate, candidate_bytes
        final_aliases = {name: candidate_aliases[alias] for name, alias in final_aliases.items()}
    if len(key(result).encode("utf-8")) != current_bytes:
        raise ValueError("annotation sharing changed complete UTF-8 cost accounting")
    result_fingerprint, result_definitions = schema_rule_fingerprints(result)
    if result_fingerprint != original_fingerprint or any(
        result_definitions.get(final_aliases[aliases[name]]) != identity for name, identity in definition_fingerprints.items()
    ):
        raise ValueError("schema factoring changed the declared rule graph")
    return result


def _combined_contract_schema(contracts: tuple[BoundContract, ...]) -> dict[str, object]:
    definitions: dict[str, object] = {}
    variants = []
    for number, contract in enumerate(contracts):
        variant = rebase_schema_definitions(contract.rule_graph.schema_value(), f"c{number}")
        for name, rule in variant.pop("$defs", {}).items():
            if name in definitions and definitions[name] != rule:
                raise ValueError("conflicting bound Lead rule definition")
            definitions[name] = rule
        variants.append(variant)
    result = alternatives(variants)
    if definitions:
        result["$defs"] = definitions
    return share_schema_descriptions(factor_rule_schema(result))


@dataclass(frozen=True, slots=True)
class LeadContractIndex(Generic[_LeadValue]):
    """Sealed complete index; every relationship derives from typed frozen owners."""
    policy: LeadStagePolicy
    owners: tuple[ContractOwner[_LeadValue], ...] = field(init=False, repr=False, compare=False)
    contracts: tuple[BoundContract[_LeadValue], ...] = field(init=False, repr=False, compare=False)
    expected_schema_ids: tuple[str, ...] = field(init=False)
    rule_graph: SchemaRules = field(init=False)
    _owner_anchors: tuple[ContractOwner[_LeadValue], ...] = field(init=False, repr=False, compare=False)
    _graph_digest: str = field(init=False, repr=False)

    def __init_subclass__(cls, **kwargs):
        raise TypeError("Lead index relationships cannot be overridden")

    def __post_init__(self) -> None:
        if type(self.policy) is not LeadStagePolicy:
            raise ValueError("incomplete or duplicate Lead owner index")
        owners = self.policy.owner_declarations
        if any(type(owner) is LeadResultContractOwner and owner.task_revisions != self.policy.task_revisions for owner in owners):
            raise ValueError("selected owner literal mapping differs from stage task policy")
        object.__setattr__(self, "owners", owners)
        contracts = tuple(BoundContract.bind(owner, self.policy.identity_for(owner.owner_id))
                          for owner in self.owners)
        ids = tuple(contract.schema_id for contract in contracts)
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate derived Lead schema IDs")
        rules = SchemaRules.declare(_combined_contract_schema(contracts),
                                    maximum_bytes=self.policy.maximum_index_schema_bytes)
        object.__setattr__(self, "contracts", contracts)
        object.__setattr__(self, "expected_schema_ids", ids)
        object.__setattr__(self, "rule_graph", rules)
        object.__setattr__(self, "_owner_anchors", self.owners)
        object.__setattr__(self, "_graph_digest", hashlib.sha256(rules.canonical_bytes).hexdigest())

    @classmethod
    def bind(cls, policy: LeadStagePolicy) -> LeadContractIndex[_LeadValue]:
        return cls(policy)

    def require_provenance(self) -> None:
        if (
            type(self.rule_graph) is SchemaRules
            and self.rule_graph.maximum_bytes != self.policy.maximum_index_schema_bytes
        ):
            raise ValueError("frozen Lead index provenance changed")
        if (type(self.rule_graph) is not SchemaRules or self.owners is not self._owner_anchors
                or self.owners is not self.policy.owner_declarations
                or len(self.contracts) != len(self.owners)
                or tuple(item.schema_id for item in self.contracts) != self.expected_schema_ids
                or len(set(self.expected_schema_ids)) != len(self.expected_schema_ids)
                or hashlib.sha256(self.rule_graph.canonical_bytes).hexdigest() != self._graph_digest):
            raise ValueError("frozen Lead index provenance changed")
        for owner, contract in zip(self.owners, self.contracts, strict=True):
            if (type(contract) is not BoundContract or contract.owner is not owner
                    or contract.identity != self.policy.identity_for(owner.owner_id)):
                raise ValueError("Lead policy-to-contract/owner drift")
            contract.require_owner_provenance()

    def schema_value(self) -> dict[str, object]:
        return self.rule_graph.provider_schema_value()

    def select(self, schema_id: object) -> BoundContract[_LeadValue]:
        if type(schema_id) is str and schema_id in self.expected_schema_ids:
            for contract in self.contracts:
                if contract.schema_id == schema_id:
                    return contract
        raise LeadContractRejection("LEAD_SCHEMA_ID_REJECTED", None,
                                    ("$.schema_id: required exact current offered string identity",))

    def decode_and_validate(self, result: object) -> SelectedContract[_LeadValue]:
        try:
            value = strict_json_object(result, exact_numbers=True) if type(result) is str else result
        except ValueError as failure:
            raise LeadContractRejection(
                "LEAD_JSON_REJECTED", None,
                failure_issues(failure) or ("$: expected strict JSON object",),
            ) from None
        if type(value) is not dict or "schema_id" not in value:
            raise LeadContractRejection("LEAD_SCHEMA_ID_REJECTED", None,
                                        ("$.schema_id: required current offered identity",))
        return self.select(value["schema_id"]).decode_selected(value)

    def require_host_checks(self, selected: SelectedContract[_LeadValue], *, phase: str,
                            context: HostCheckContext) -> None:
        if type(selected) is not SelectedContract:
            raise TypeError("owner checks require selected typed input")
        member = self.select(selected.schema_id)
        try:
            member.require_host_checks(selected, phase=phase, context=context)
        except (TypeError, ValueError) as failure:
            if isinstance(failure, LeadContractRejection):
                raise
            findings = tuple(item.path + ": " + item.requirement for item in failure_findings(failure))
            raise LeadContractRejection("LEAD_OWNER_RULE_REJECTED", member.schema_id,
                                        findings or ("$: current authorized owner requirements",)) from None

    def repair_input(self, failure: LeadContractRejection) -> str:
        if type(failure) is not LeadContractRejection:
            raise TypeError("repair requires a trusted selected contract rejection")
        if failure.schema_id in self.expected_schema_ids:
            requirements = self.select(failure.schema_id).corrective_requirements(failure.requirements)
        else:
            requirements = {
                "binding_id": "lead.current_offer_selection", "revision": "848-v3:single-frozen-graph",
                "rule_digest": self._graph_digest, "wire_root_path": self.policy.wire_root_path,
                "schema_requirements": ({"path": self.policy.wire_root_path,
                    "rule": "STRICT_CURRENT_CONTRACT_ENVELOPE", "expected": {"type": "object"}},
                    {"path": self.policy.wire_root_path + ".schema_id", "rule": "CURRENT_OFFERED_SCHEMA_ID",
                     "expected": {"type": "string", "enum": self.expected_schema_ids}}),
                "owner_requirements": (),
            }
        return json.dumps({"code": failure.code, "selected_schema_id": failure.schema_id,
            "offered_schema_ids": self.expected_schema_ids, "requirements": requirements,
            "stage": self.policy.stage, "physical_call_id": self.policy.physical_call_id,
            "logical_origin_call_id": self.policy.logical_origin_call_id,
            "offer_digest": self.policy.offer_digest, "eligibility_offer_digest": self.policy.eligibility_offer_digest,
            "task_revisions": self.policy.task_revisions, "language": self.policy.language,
            "choices": self.policy.choices}, ensure_ascii=False, allow_nan=False, separators=(",", ":"))




def closed_object(properties: dict[str, object], *, required: tuple[str, ...] | None = None) -> dict[str, object]:
    return {"type": "object", "properties": properties,
            "required": list(properties if required is None else required), "additionalProperties": False}


def string_array(
    values: tuple[str, ...] | None = None, *, minimum: int = 0,
    maximum: int | None = None,
) -> dict[str, object]:
    item: dict[str, object] = {"type": "string"}
    if values:
        item["enum"] = list(values)
    result: dict[str, object] = {"type": "array", "items": item, "minItems": minimum}
    if maximum is not None:
        result["maxItems"] = maximum
    return result


def alternatives(variants: list[dict[str, object]]) -> dict[str, object]:
    return variants[0] if len(variants) == 1 else {"anyOf": variants}


_OWNED_SCALAR, _OWNED_NONNUL, _OWNED_NONBLANK, _OWNED_NONBLANK_WITH_NUL = unicode_scalar_classes(
    (), ("", "\x00", "\x00" + SCHEMA_PYTHON_WHITESPACE, SCHEMA_PYTHON_WHITESPACE))
_NUL_FREE_TEXT_PATTERN = r"^[^\u0000]+\z"
_OWNED_NUL_FREE_PATTERN = "^" + _OWNED_NONNUL + r"+\z"
_OWNED_VISIBLE_PATTERN = "^" + _OWNED_SCALAR + r"+\z"
_OWNED_NONEMPTY_PATTERN = "^" + _OWNED_NONNUL + "*" + _OWNED_NONBLANK + _OWNED_NONNUL + r"*\z"
_OWNED_NONBLANK_PATTERN = "^" + _OWNED_SCALAR + "*" + _OWNED_NONBLANK_WITH_NUL + _OWNED_SCALAR + r"*\z"

def _owned_visible_text_rule(
    template: Mapping[str, object], *, nonblank: bool = False,
) -> dict[str, object]:
    """Intersect only declared, proved text primitives without implicit lookaround."""
    result = deepcopy(dict(template))
    if result.get("type") != "string":
        raise ValueError("owned visible text requires a string rule")
    minimum = result.get("minLength", 0)
    maximum = result.get("maxLength", COLLABORATION_VISIBLE_TEXT_MAX_UTF8_BYTES)
    if type(minimum) is not int or type(maximum) is not int or min(minimum, maximum) < 0:
        raise ValueError("owned visible text requires declared nonnegative length bounds")
    result["minLength"] = max(1, minimum)
    result["maxLength"] = min(COLLABORATION_VISIBLE_TEXT_MAX_UTF8_BYTES, maximum)
    prior = result.get("pattern")
    visible = visible_text_rule()["pattern"]
    nonempty = nonempty_text_rule()["pattern"]
    if prior is None or prior in (visible, _OWNED_VISIBLE_PATTERN):
        selected = _OWNED_NONEMPTY_PATTERN if nonblank else _OWNED_VISIBLE_PATTERN
    elif prior in (_NUL_FREE_TEXT_PATTERN, r"^[^\u0000]+$(?![\s\S])", _OWNED_NUL_FREE_PATTERN):
        selected = _OWNED_NONEMPTY_PATTERN if nonblank else _OWNED_NUL_FREE_PATTERN
    elif prior in (nonempty, _OWNED_NONEMPTY_PATTERN):
        selected = _OWNED_NONEMPTY_PATTERN
    elif prior in (nonblank_text_rule()["pattern"], _OWNED_NONBLANK_PATTERN):
        selected = _OWNED_NONEMPTY_PATTERN if nonblank else _OWNED_NONBLANK_PATTERN
    else:
        # Preserve an unproved custom prior's exact host conjunction.
        required = nonempty if nonblank else visible
        prefix = r"^(?=[^\ud800-\udfff]*$(?![\s\S]))"
        if prior is not None and prior not in (visible, required):
            prefix += r"(?=[\s\S]*(?:" + prior + "))"
        selected = prefix + required
    result["pattern"] = selected
    return result


def nonempty_text_schema() -> dict[str, object]:
    return {**visible_text_rule(), "description": "Nonempty UTF-8 text."}


def _scope_extensions(scope: AuthoringScope | None) -> dict[str, object]:
    if scope is not None and scope.schema_version < 3:
        return {}
    domains = tuple(item.value for item in required_authoring_domains(scope)) if scope else tuple(item.value for item in ArtifactReviewDomain)
    contributions = [
        {
            **closed_object({
                "domain": {"type": "string", "enum": [item.domain.value]},
                "role": {"type": "string", "enum": [item.role.value]},
            }),
            "description": "The bound contribution role for this declared story domain.",
        }
        for item in scope.contributions
    ] if scope else [{
        **closed_object({
            "domain": {"type": "string", "enum": list(domains)},
            "role": {"type": "string", "enum": [item.value for item in AuthoringContributionRole]},
        }),
        "description": "One explicit contribution role for a declared story domain.",
    }]
    slots = [
        {
            **closed_object({
                "slot_id": {"type": "string", "enum": [item.slot_id]},
                "dependent_domains": string_array(
                    tuple(domain.value for domain in item.dependent_domains),
                    minimum=len(item.dependent_domains), maximum=len(item.dependent_domains),
                ),
            }),
            "description": "The bound shared-premise dependency for these draft-owner domains.",
        }
        for item in scope.dependency_slots
    ] if scope and scope.dependency_slots else [{
        **closed_object({
            "slot_id": nonempty_text_schema(),
            "dependent_domains": string_array(domains, minimum=1),
        }),
        "description": "One explicit shared-premise dependency for declared draft-owner domains.",
    }]
    return {
        "contributions": {
            "type": "array", "items": alternatives(contributions) if contributions else closed_object({}),
            "minItems": len(scope.contributions) if scope else 0,
            "maxItems": len(scope.contributions) if scope else len(domains),
        },
        "dependency_slots": {
            "type": "array", "items": alternatives(slots), "minItems": 0,
            **({"minItems": len(scope.dependency_slots), "maxItems": len(scope.dependency_slots)} if scope else {}),
        },
    }


def _scope_for_rules(raw: dict[str, object]) -> AuthoringScope:
    version = 3 if "contributions" in raw else 2 if "intent" in raw else 1
    return authoring_scope_from_dict({"schema_version": version, "activity": raw["activity"],
        "target_paths": raw["target_paths"], "domains": raw["domains"],
        "interpretation": "Provider constraint projection", "task_id": "constraint", "task_revision": 1,
        **({"intent": raw.get("intent", "authoring")} if version >= 2 else {}),
        **({"contributions": raw["contributions"], "dependency_slots": raw["dependency_slots"]} if version == 3 else {})})


def canonical_scope(value: object) -> object:
    """Normalize domain order only after retaining duplicate rejection."""
    if type(value) is not dict or type(value.get("domains")) is not list:
        return value
    domains = value["domains"]
    if any(type(item) is not str for item in domains):
        return value
    if len(set(domains)) != len(domains):
        raise LeadContractError((DomainValidationRule.DOMAIN_DUPLICATE,))
    order = [item.value for item in ArtifactReviewDomain]
    if any(item not in order for item in domains):
        return value
    return {**value, "domains": sorted(domains, key=order.index)}


def canonical_assignments(value: list[object]) -> list[object]:
    """Preserve malformed entries for the decoder; never deduplicate roles."""
    if any(type(item) is not dict or item.get("agent_id") not in SPECIALIST_IDS
           for item in value):
        return value
    ids = [item["agent_id"] for item in value]
    if len(set(ids)) != len(ids):
        raise validation_failure(
            LeadContractError((DomainValidationRule.SPECIALIST_DUPLICATE,)),
            '$',
            'agent_id values must be unique before canonical ordering',
        )
    return sorted(value, key=lambda item: SPECIALIST_IDS.index(item["agent_id"]))


def visible_text(value: object) -> bool:
    if type(value) is not str:
        return False
    try:
        return 0 < len(value.encode("utf-8", errors="strict")) <= COLLABORATION_VISIBLE_TEXT_MAX_UTF8_BYTES
    except UnicodeError:
        return False



_LEAD_OUTCOME_SCOPE_DECLARATION = (
    lead_outcome_scope_paths, lead_outcome_scope_paths.__code__,
)


@dataclass(frozen=True, slots=True)
class LeadExplorationConstraints:
    """Offered provisional choices; runtime authorizes actual artifact READs."""

    input_ids: tuple[str, ...] = ()

    def prompt_facts(self) -> dict[str, object]:
        return {
            "interaction_mode": "exploration", "provisional": True,
            "offered_basis_input_ids": list(self.input_ids),
            "authority": "No specialists, completion, candidate, objective, Apply or Commit.",
            "artifact_observations": "Cite actual authorized artifact READs only; never SEARCH, lists, user prose or earlier ideas.",
        }

    def schema(self) -> dict[str, object]:
        return exploration_result_schema(self.input_ids)

    def diagnose(self, answer: str, *, read_ids: tuple[str, ...] = (), read_reviewers: tuple[tuple[str, str], ...] = ()) -> tuple[DomainValidationRule, ...]:
        del read_reviewers
        try:
            value = json.loads(answer)
            if type(value) is not dict or value.get("kind") != "exploration":
                raise ValueError("exploration mode is required")
            status = value.get("status")
            expected = {"kind", "status", "message"} | ({"ideas", "grounded_observations"} if status == "ideas" else set())
            if status not in {"ideas", "clarification"} or set(value) != expected or not visible_text(value["message"]):
                raise ValueError("invalid closed exploration result")
            if status == "ideas":
                ideas, observations = value["ideas"], value["grounded_observations"]
                if type(ideas) is not list or type(observations) is not list or not (ideas or observations):
                    raise ValueError("exploration material is empty")
                for idea in ideas:
                    if type(idea) is not dict or set(idea) != {"kind", "material_text", "basis_input_ids"} or idea["kind"] not in {"hypothesis", "alternative", "short_example"} or not visible_text(idea["material_text"]) or type(idea["basis_input_ids"]) is not list or not set(idea["basis_input_ids"]).issubset((*self.input_ids, *read_ids)):
                        raise ValueError("idea must use offered provisional basis")
                for observation in observations:
                    ids = observation.get("evidence_input_ids") if type(observation) is dict else None
                    if type(observation) is not dict or set(observation) != {"text", "evidence_input_ids"} or not visible_text(observation["text"]) or type(ids) is not list or not ids or len(set(ids)) != len(ids) or not set(ids).issubset(read_ids):
                        raise ValueError("observation requires current artifact READs")
        except (ValueError, TypeError, KeyError):
            return (DomainValidationRule.LEAD_OUTCOME_REJECTED,)
        return ()


def empty_core_status_evidence(
    *, locator: str | None, present: bool, utf8_bytes: int | None,
    content_digest: str | None,
) -> bool:
    """Recognize only existing canonical empty-core artifact status evidence."""
    return (
        locator in CORE_ARTIFACT_PATHS and present and utf8_bytes == 0
        and content_digest == "sha256:" + hashlib.sha256(b"").hexdigest()
    )


def _current_lead_rule_schema(contract: LeadResultRuleSeed) -> dict[str, object]:
    """계획용 seed를 기존 wire 규칙으로 한 번 투영한다."""
    projected = contract.schema_value()
    if "writing_task_scope" not in projected.get("properties", {}) and any(
            "authoring_scope" in item.get("properties", {}) or "kind" in item.get("properties", {})
            for item in projected.get("anyOf", [projected])):
        projected = project_result_schema(projected, contract.contract_id)
    return projected


def _compile_lead_families(
    contract_id: str,
    roots: tuple[dict[str, object], ...] | list[dict[str, object]],
    variants: tuple[LeadWireVariant, ...] | list[LeadWireVariant],
    container_description: str | None,
    tasks: tuple[tuple[str, int], ...],
    *, selected_group_id: str | None = None,
) -> tuple[LeadWireVariant, ...]:
    """검증된 의미 변형을 기존 공통화와 완전한 물리 분할로 구성한다."""
    grouped = selected_group_id is not None
    families = []
    for root_number, root in enumerate(roots):
        root_members = tuple(item for item in variants if item.logical_root == root_number)
        if not root_members:
            continue
        sources = tuple(
            rebase_schema_definitions(member.rules.schema_value(), f"g{number}")
            for number, member in enumerate(root_members)
        )
        scalar_groups = {}
        for source, member in zip(sources, root_members, strict=True):
            names = tuple(name for name in source["properties"] if name == "response_text" or
                          name == "response_type" or name.endswith("_result_type"))
            key = tuple((name, member.rules.node_fingerprint((name,))) for name in names)
            scalar_groups.setdefault(key, []).append((source, member))
        family_partitions = []
        for common_key, group in scalar_groups.items():
            sources, members = tuple(zip(*group, strict=True))
            first = sources[0]
            common_names = tuple(name for name, _ in common_key)
            common = {name: first["properties"][name] for name in common_names}
            common_fingerprints = {
                name: members[0].rules.node_fingerprint((name,)) for name in common_names
            }
            details = []
            guidance = []
            for source, source_member in zip(sources, members, strict=True):
                for name in common_names:
                    if source_member.rules.node_fingerprint((name,)) != common_fingerprints[name]:
                        raise ValueError("Lead family has incompatible common scalar rules")
                detail_properties = {name: value for name, value in source["properties"].items() if name not in common_names}
                if len(members) > 1:
                    detail_properties["variant_id"] = {"type": "string", "enum": [source_member.branch_key],
                                                       "description": "Required offered family variant identity; use this exact literal."}
                scope_node = detail_properties.get("writing_task_scope")
                if type(scope_node) is dict and "description" in scope_node:
                    notice = "writing_task_scope: " + scope_node["description"]
                    if notice not in guidance: guidance.append(notice)
                detail = {
                **closed_object(detail_properties, required=tuple(
                    name for name in source["required"] if name not in common_names
                ) + (("variant_id",) if len(members) > 1 else ())),
                    "description": source["description"],
                }
                if "$defs" in source: detail["$defs"] = source["$defs"]
                details.append(detail)
            detail_graph = alternatives(details)
            if guidance: detail_graph["description"] = " ".join(guidance)
            family_graph = closed_object({**common, "response_details": detail_graph})
            if "description" in root: family_graph["description"] = root["description"]
            if container_description is not None:
                family_graph["description"] = container_description
                if "description" in root and root["description"] != container_description:
                    family_graph["description"] += "\n" + root["description"]
            family_graph = project_lead_scope_schema(factor_rule_schema(
                _compile_closed_choices(hoist_schema_definitions(family_graph))))
            family_graph = factor_rule_schema(family_graph)
            maximum_bytes = root_members[0].rules.maximum_bytes
            if any(member.rules.maximum_bytes != maximum_bytes for member in root_members):
                raise ValueError("Lead family has incompatible member budgets")

            def complete_size(graph: dict[str, object]) -> int:
                bound = deepcopy(graph)
                bound["properties"]["schema_id"] = {
                    "type": "string", "enum": ["lead_v11_" + "0" * 32],
                }
                bound["required"] = [*bound["required"], "schema_id"]
                return len(json.dumps(bound, ensure_ascii=False, sort_keys=True,
                                      separators=(",", ":")).encode("utf-8"))

            def references(node: object) -> set[str]:
                if type(node) is list:
                    return set().union(*(references(child) for child in node))
                if type(node) is not dict:
                    return set()
                names = {node["$ref"].removeprefix("#/$defs/")} if "$ref" in node else set()
                return names.union(*(references(child) for key, child in node.items()
                                     if key != "$defs"))

            definitions = family_graph.get("$defs", {})

            def closure(names: set[str]) -> set[str]:
                pending = list(names)
                while pending:
                    name = pending.pop()
                    if name not in definitions:
                        raise ValueError("Lead family refers to an undeclared definition")
                    extra = references(definitions[name]) - names
                    names.update(extra)
                    pending.extend(extra)
                return names

            retained = set(definitions) - closure(references(family_graph))
            detail_rule = family_graph["properties"]["response_details"]
            clauses = tuple(detail_rule.get("anyOf", (detail_rule,)))

            def part_graph(first: int, last: int) -> dict[str, object]:
                part = deepcopy(family_graph)
                detail = part["properties"]["response_details"]
                if "anyOf" in detail:
                    selected = deepcopy(clauses[first:last])
                    if len(selected) > 1:
                        detail["anyOf"] = list(selected)
                    else:
                        annotation = {key: value for key, value in detail.items()
                                      if key != "anyOf"}
                        target = selected[0]
                        if all(key not in target or target[key] == value
                               for key, value in annotation.items()):
                            part["properties"]["response_details"] = {**target, **annotation}
                        else:
                            name = f"family_part_{first}_{last}"
                            while name in part.get("$defs", {}):
                                name += "_"
                            part.setdefault("$defs", {})[name] = target
                            part["properties"]["response_details"] = {
                                "$ref": f"#/$defs/{name}", **annotation,
                            }
                local_definitions = part.pop("$defs", {})
                needed = references(part) | retained
                pending = list(needed)
                while pending:
                    name = pending.pop()
                    extra = references(local_definitions[name]) - needed
                    needed.update(extra)
                    pending.extend(extra)
                if needed:
                    part["$defs"] = {name: rule for name, rule in local_definitions.items()
                                     if name in needed}
                return part

            pending = [(0, len(clauses))]
            partitions = []
            while pending:
                first, last = pending.pop(0)
                part = part_graph(first, last)
                if complete_size(part) <= maximum_bytes:
                    partitions.append((first, last, part))
                elif last - first > 1:
                    middle = (first + last) // 2
                    pending[:0] = [(first, middle), (middle, last)]
                else:
                    raise ValueError("complete Lead clause exceeds its member schema budget")
            if tuple(index for first, last, _ in partitions for index in range(first, last)) != (
                    tuple(range(len(clauses)))):
                raise ValueError("Lead family partition lost an ordered clause")
            for name, rule in definitions.items():
                if not any(part.get("$defs", {}).get(name) == rule for _, _, part in partitions):
                    raise ValueError("Lead family partition lost a declared definition")
            family_partitions.extend(
                (part, members, common_names) for _, _, part in partitions
            )
        for part_number, (part, members, common_names) in enumerate(family_partitions):
            family_rules = SchemaRules.declare(part, maximum_bytes=maximum_bytes)
            member_by_variant = MappingProxyType({member.branch_key: member for member in members})
            if len(member_by_variant) != len(members):
                raise ValueError("duplicate Lead family variant identity")

            def family_decode(body: dict[str, object], *, candidates=members, by_variant=member_by_variant,
                              names=common_names, scope_schema=family_rules,
                              offered_tasks=tasks, restore=restore_lead_scope_result
                              ) -> dict[str, object]:
                declared = scope_schema.schema_value()
                details = dict(declared["properties"]["response_details"])
                if "$defs" in declared:
                    details["$defs"] = declared["$defs"]
                supplied = body["response_details"].get("variant_id")
                candidate = (candidates[0] if len(candidates) == 1 else
                             by_variant.get(supplied) if type(supplied) is str else None)
                if candidate is None:
                    raise ValueError("selected family requires its offered variant identity")
                expanded = {name: body[name] for name in names}
                restored = restore(body["response_details"], details)
                if len(candidates) > 1:
                    restored.pop("variant_id")
                expanded.update(restored)
                map_current_task_literals(expanded, offered_tasks)
                if candidate.rules.findings(expanded):
                    raise ValueError("selected family details violate its bound choice graph")
                return candidate.decode(expanded)
            branch_key = f"{contract_id}:family:{root_number}"
            if grouped:
                branch_key += ":group:" + selected_group_id
            if len(family_partitions) > 1:
                branch_key += f":partition:{part_number}"
            families.append(LeadWireVariant(branch_key, family_rules, family_decode,
                                            group_id=selected_group_id, logical_root=root_number))
    return tuple(families)


@dataclass(frozen=True, slots=True)
class LeadProviderConstraints:
    """One immutable set of offered choices shared by schema, prompt and guard."""

    input_ids: tuple[str, ...] = ()
    scope_rule: AuthoringScopeRuleSet | None = field(default=None, kw_only=True)
    required_input_ids: tuple[str, ...] | None = field(default=None, kw_only=True)
    review_candidate_ids: tuple[str, ...] | None = field(default=None, kw_only=True)
    per_assignment_input_ids: tuple[str, ...] = field(default=(), kw_only=True)
    reviewer_inputs: tuple[tuple[str, str], ...] = ()
    task_revisions: tuple[tuple[str, int], ...] = ()
    source_ids: tuple[str, ...] | None = None
    objective_ids: tuple[str, ...] | None = None
    allow_read_sources: bool = False
    scope: AuthoringScope | None = None
    preparation_kind: str | None = None
    preparation_objective_id: str | None = None
    preparation_targets: tuple[str, ...] = ()
    preparation_specialists: tuple[str, ...] = ()
    recommendation_only: bool = False
    continuation: bool = False
    premise_question_id: str | None = None
    adoption_intent: AuthoringIntent | None = None
    adoption_targets: tuple[str, ...] = ()
    readiness_source_basis_ref: str | None = None

    def __post_init__(self) -> None:
        for values in (self.per_assignment_input_ids, self.required_input_ids,
                       self.review_candidate_ids):
            if values is not None and (type(values) is not tuple
                    or any(type(item) is not str or not item for item in values)
                    or len(set(values)) != len(values) or not set(values).issubset(self.input_ids)):
                raise ValueError("invalid bound required-source policy")
        if self.scope_rule is not None and type(self.scope_rule) is not AuthoringScopeRuleSet:
            raise TypeError("scope rules require the exact owner declaration")
        for values in (self.input_ids, self.task_revisions, self.reviewer_inputs,
                       self.preparation_targets, self.preparation_specialists, self.adoption_targets):
            if type(values) is not tuple:
                raise TypeError("Lead constraint collections must be immutable tuples")
        for values in (self.source_ids, self.objective_ids):
            if values is not None and type(values) is not tuple:
                raise TypeError("Lead constraint offers must be immutable tuples")
        if self.scope is not None and type(self.scope) is not AuthoringScope:
            raise TypeError("Lead constraint scope must be an AuthoringScope")

        if self.adoption_intent is not None and type(self.adoption_intent) is not AuthoringIntent:
            raise TypeError("adoption intent must be explicit")
        if self.adoption_intent is None and self.adoption_targets:
            raise ValueError("adoption targets require an explicit intent")

    def required_assignment_input_ids(self) -> tuple[str, ...]:
        if self.required_input_ids is not None:
            if type(self.required_input_ids) is not tuple or not set(self.required_input_ids).issubset(self.input_ids):
                raise ValueError("invalid original required-input binding")
            return self.required_input_ids
        return tuple(item for item in self.input_ids
            if (not self.preparation_kind or item not in PREPARATION_INPUT_IDS)
            and not (self.adoption_intent is AuthoringIntent.REVIEW_ONLY and item == "user_input"))

    def _allocation_input_ids(self) -> tuple[str, ...]:
        required = self.required_assignment_input_ids()
        inputs = (
            tuple(identity for identity in self.input_ids if identity in required)
            if self.continuation else self.input_ids
        )
        obligations = (required, self.per_assignment_input_ids)
        if self.review_candidate_ids is not None:
            obligations += (self.review_candidate_ids,)
        if any(not set(values).issubset(inputs) for values in obligations):
            raise ValueError("assignment obligations exceed current allocation inputs")
        return inputs

    def choice_snapshot(self) -> tuple[tuple[str, tuple[str, ...]], ...]:
        return (
            ("input_ids", self.input_ids),
            ("allocation_input_ids", self._allocation_input_ids()),
            ("required_input_ids", self.required_assignment_input_ids()),
            ("per_assignment_input_ids", self.per_assignment_input_ids),
            ("review_candidate_ids", self.input_ids
             if self.review_candidate_ids is None else self.review_candidate_ids),
            ("review_candidate_binding",
             ("offered" if self.review_candidate_ids is None else "bound",)),
            ("reviewer_inputs", tuple(agent + ":" + alias for agent, alias in self.reviewer_inputs)),
            ("source_ids", () if self.source_ids is None else self.source_ids),
            ("objective_ids", () if self.objective_ids is None else self.objective_ids),
            ("source_binding", ("unbound" if self.source_ids is None else "bound",)),
            ("objective_binding", ("unbound" if self.objective_ids is None else "bound",)),
            ("preparation_targets", self.preparation_targets),
            ("preparation_specialists", self.preparation_specialists),
            ("adoption_targets", self.adoption_targets),
        )

    def bind(self, stage_policy: LeadStagePolicy) -> LeadContractIndex[LeadDecodedResult]:
        """Build the complete typed index from the exact current owner declarations."""
        owners = () if type(stage_policy) is not LeadStagePolicy else stage_policy.owner_declarations
        if (type(stage_policy) is not LeadStagePolicy or self.allow_read_sources
                or type(owners) is not tuple or any(type(owner) is not LeadResultContractOwner for owner in owners)
                or stage_policy.choices != self.choice_snapshot()
                or any(owner.task_revisions != stage_policy.task_revisions for owner in owners)):
            raise ValueError("Lead owner declaration differs from current stage choices")
        return LeadContractIndex.bind(stage_policy)

    def current_wire_variants(
        self, contract: LeadResultRuleSeed,
        *,
        read_ids: tuple[str, ...] = (),
        writing_read_ids: tuple[str, ...] = (),
        writing_read_facts: tuple[tuple[str, str, bool], ...] = (),
        core_status_facts: tuple[ContextSourceStatusReceipt, ...] = (),
        selected_group_id: str | None = None,
        collect_schema_groups: bool = False,
    ) -> tuple[LeadWireVariant, ...]:
        if (type(collect_schema_groups) is not bool
                or selected_group_id is not None and
                (type(selected_group_id) is not str
                 or selected_group_id not in tuple(group.group_id for group in LEAD_SCHEMA_GROUPS))
                or collect_schema_groups and selected_group_id is not None):
            raise ValueError("현재 의미 그룹의 카탈로그 또는 전체 생성 계약을 요청해야 합니다")
        grouped = collect_schema_groups or selected_group_id is not None
        function, code = _LEAD_OUTCOME_SCOPE_DECLARATION
        if lead_outcome_scope_paths is not function or function.__code__ is not code:
            raise ValueError("frozen Lead outcome scope rule changed")
        if type(contract) is not LeadResultRuleSeed or self.allow_read_sources:
            raise ValueError("current Lead wire compilation requires a qualified snapshot")
        if (self.scope is not None
                and self.scope.schema_version != AUTHORING_SCOPE_SCHEMA_VERSION):
            raise ValueError("current Lead wire requires current scope version")
        if (self.scope_rule is not None
                and self.scope_rule.schema_version != AUTHORING_SCOPE_SCHEMA_VERSION):
            raise ValueError("current Lead wire requires current scope rule version")
        allocation_inputs = self._allocation_input_ids()
        projected = _current_lead_rule_schema(contract)
        roots = projected.get("anyOf", [projected])
        container_description = (
            projected.get("description") if "anyOf" in projected else None
        )
        def member_compilation_is_identity(schema: dict[str, object]) -> bool:
            if type(schema) is not dict:
                return False
            try:
                pending: list[object] = [schema]
                seen: set[int] = set()
                alternatives: list[list[dict[str, object]]] = []
                while pending:
                    node = pending.pop()
                    if type(node) in (dict, list):
                        if id(node) in seen:
                            return False
                        seen.add(id(node))
                        if type(node) is dict:
                            if any(type(name) is not str for name in node):
                                return False
                            if "anyOf" in node:
                                choices = node["anyOf"]
                                if type(choices) is not list or any(
                                    type(choice) is not dict for choice in choices
                                ):
                                    return False
                                alternatives.append(choices)
                            pending.extend(node.values())
                        else:
                            pending.extend(node)
                    elif node is not None and type(node) not in (bool, int, float, str):
                        return False
                # Reuse the central dialect and default-cap check, without reordering schema.
                SchemaRules.declare(schema)
                for choices in alternatives:
                    objects = [choice for choice in choices
                               if (all(type(name) is str for name in choice)
                                   and type(choice.get("type")) is str
                                   and choice.get("type") == "object")]
                    if len(objects) < 2:
                        continue
                    descriptors: set[tuple[str, tuple[str, ...]]] = set()
                    for choice in objects:
                        properties = choice.get("properties")
                        if type(properties) is not dict:
                            return False
                        metadata = {field: value for field, value in choice.items()
                                    if field != "properties"}
                        descriptor = (
                            json.dumps(metadata, sort_keys=True, ensure_ascii=False,
                                       allow_nan=False, separators=(",", ":")),
                            tuple(sorted(properties)),
                        )
                        if descriptor in descriptors:
                            return False
                        descriptors.add(descriptor)
            except Exception:
                return False
            return True

        def requires_nonempty_member(template: object, name: str) -> bool:
            if type(template) is not dict:
                return False
            branches = template.get("anyOf", ())
            if branches:
                return all(requires_nonempty_member(branch, name) for branch in branches)
            member = template.get("properties", {}).get(name, {})
            minimum = member.get("minItems", 0)
            return (name in template.get("required", ()) and member.get("type") == "array"
                    and type(minimum) is int and minimum >= 1)
        tasks = self.task_revisions or (() if self.scope is None else ((self.scope.task_id, self.scope.task_revision),))
        variants = []
        root_declarations = []
        for root_number, root in enumerate(roots):
            request = root.get("properties", {}).get("proposal_drafting_request")
            if not grouped or type(request) is not dict:
                root_declarations.append((root_number, root, None))
                continue
            preparation_kinds = {native: domain for domain, native in DRAFTING_ACTIONS.items()}
            for preparation in request.get("anyOf", (request,)):
                choices = preparation.get("properties", {}).get("action_type", {}).get("enum", ())
                if len(choices) != 1 or choices[0] not in preparation_kinds:
                    raise ValueError("준비 추천의 의미 변형에는 소유자의 정확한 작업 타입이 필요합니다")
                selected_root = deepcopy(root)
                selected_request = deepcopy(preparation)
                for name, value in request.items():
                    if name == "anyOf":
                        continue
                    if name == "description" and name in selected_request:
                        if value != selected_request[name]:
                            selected_request[name] = value + "\n" + selected_request[name]
                    elif name in selected_request and selected_request[name] != value:
                        raise ValueError("준비 추천의 공통 규칙을 의미 분류에서 잃을 수 없습니다")
                    else:
                        selected_request[name] = deepcopy(value)
                selected_root["properties"]["proposal_drafting_request"] = selected_request
                root_declarations.append((root_number, selected_root, preparation_kinds[choices[0]]))
        for root_number, root, preparation_kind in root_declarations:
            properties = root.get("properties", {})
            ordinary_answer = (
                set(properties) - {"document_change_request_update"} == {"response_type", "response_text", "writing_task_scope"}
                and set(root.get("required", ())) == {"response_type", "response_text", "writing_task_scope"}
                and properties["response_type"].get("enum") == ["answer"]
            )
            scope_template = properties.get("writing_task_scope")
            scoped = type(scope_template) is dict and scope_template.get("type") == "object"
            scope_kinds = ()
            if scoped:
                # Translate trusted owner literals through the existing native codec.
                for native_kind in properties.get("response_type", {}).get("enum", ()):
                    header = normalize_result({"response_type": native_kind}, contract.contract_id)
                    domain_kind = header.get("kind")
                    if domain_kind is None and native_kind == "answer":
                        domain_kind = LeadOutcomeKind.ANSWER.value
                    if domain_kind is None:
                        # Other typed result owners retain their separate scope declarations.
                        scope_kinds = ()
                        break
                    scope_kinds += ((native_kind, LeadOutcomeKind(domain_kind)),)
            targets = (self.scope.target_paths if self.scope is not None else self.adoption_targets if self.adoption_intent is not None else None)
            if scoped:
                rules = ((authoring_scope_rules(self.scope.activity, self.scope.intent, self.scope.domains, self.scope.contributions,
                           target_paths=self.scope.target_paths, dependency_slots=self.scope.dependency_slots,
                           schema_version=self.scope.schema_version),) if self.scope is not None else
                         (self.scope_rule,) if self.scope_rule is not None else finite_authoring_scope_rules(intent=self.adoption_intent, target_paths=targets or ()))
                activities = scope_template["properties"]["activity_type"].get("enum", ())
                intents = scope_template["properties"].get("writing_intent", {}).get("enum", tuple(intent.value for intent in AuthoringIntent))
                rules = tuple(rule for rule in rules if AUTHORING_ACTIVITIES[rule.activity.value] in activities and rule.intent.value in intents)
            else:
                rules = (self.scope_rule or (authoring_scope_rules(self.scope.activity, self.scope.intent, self.scope.domains, self.scope.contributions,
                          target_paths=self.scope.target_paths, dependency_slots=self.scope.dependency_slots,
                          schema_version=self.scope.schema_version) if self.scope is not None else None),)
            if scoped:
                # Current work families require the same substantive scope as their typed owners.
                # Prospective objective/recommendation fields keep their separate declarations.
                current_assignments = any(
                    name in root.get("required", ())
                    and requires_nonempty_member(properties.get(name), "assignments")
                    for name in ("specialist_consultation_proposal", "follow_up_consultation"))
                current_artifact_work = (
                    "artifact_change_proposal" in root.get("required", ())
                    and requires_nonempty_member(properties.get("artifact_change_proposal"), "artifact_changes"))
                rules = tuple(rule for rule in rules
                              if (not current_assignments or rule.activity is not AuthoringActivity.WORKFLOW)
                              and (not current_artifact_work or (
                                  rule.activity is not AuthoringActivity.WORKFLOW
                                  and rule.intent is AuthoringIntent.AUTHORING)))
            for scope_number, rule in enumerate(rules):
                if rule is not None and rule.schema_version != AUTHORING_SCOPE_SCHEMA_VERSION:
                    raise ValueError("current Lead graph has a noncurrent rule")
                scope_paths = ()
                eligible_kinds = ()
                if scope_kinds:
                    for native_kind, kind in scope_kinds:
                        paths = lead_outcome_scope_paths(
                            kind, rule.activity, rule.required_domains, writing_read_facts,
                            target_paths=targets or (), ordinary_answer=ordinary_answer,
                        )
                        if paths is not None:
                            eligible_kinds += (native_kind,)
                            if ordinary_answer:
                                scope_paths = paths
                    if not eligible_kinds:
                        continue
                group_id = None
                if grouped:
                    response_types = properties.get("response_type", {}).get("enum", ())
                    if len(response_types) != 1:
                        raise ValueError("그룹 분류에는 소유자의 정확한 업무 응답 타입이 필요합니다")
                    native_type = response_types[0]
                    if "core_document_assessment" in properties:
                        kind = "readiness"
                    elif "artifact_change_proposal" in properties:
                        kind = "artifact"
                    elif "specialist_consultation_proposal" in properties:
                        proposal = properties["specialist_consultation_proposal"]
                        kind = "premise" if "premise_decision" in proposal.get("properties", {}) else "consultation"
                    else:
                        kinds = {native: domain for domain, native in LEAD_RESPONSES.items()}
                        if native_type not in kinds:
                            raise ValueError("ordinary Lead가 아닌 결과를 그룹 프로토콜에 넣을 수 없습니다")
                        kind = kinds[native_type]
                    semantics = LeadResultSemantics(
                        kind, None if rule is None else rule.intent.value,
                        None if rule is None else rule.activity.value, preparation_kind,
                    )
                    # The original typed outcome rejects artifact preparation in
                    # review_only. Remove that ineligible semantic combination,
                    # rather than letting it invalidate every other offered group.
                    if (kind == "recommendation" and rule is not None
                            and rule.intent is AuthoringIntent.REVIEW_ONLY
                            and preparation_kind != "prepare_consultation"):
                        continue
                    group_id = schema_group_for(semantics).group_id
                    if selected_group_id is not None and group_id != selected_group_id:
                        continue
                root_schema = deepcopy(root)
                if scope_kinds:
                    root_schema["properties"]["response_type"]["enum"] = list(eligible_kinds)
                if scoped:
                    root_schema["properties"]["writing_task_scope"] = _scope_wire_rule(rule, scope_template, self.scope, targets, tasks)
                    if (ordinary_answer and rule.activity is AuthoringActivity.REVIEW
                            and targets is None):
                        scope_properties = root_schema["properties"]["writing_task_scope"]["properties"]
                        scope_properties["target_artifact_paths"] = {
                            "type": "array",
                            "items": {"type": "string", "enum": list(scope_paths)},
                            "minItems": 0, "maxItems": len(scope_paths),
                        }
                if "response_text" in root_schema["properties"]:
                    root_schema["properties"]["response_text"] = _owned_visible_text_rule(
                        root_schema["properties"]["response_text"], nonblank=scoped)
                assessment_paths = []
                def qualify_assessments(node, path=()):
                    if type(node) is not dict:
                        return
                    props = node.get("properties", {})
                    for name, child in tuple(props.items()):
                        if name == "authoring_assessment":
                            fresh = admission_rule(self.input_ids)
                            decisions = fresh["properties"]["decisions"]["items"]
                            for decision in decisions.get("anyOf", (decisions,)):
                                coverage = decision["properties"]["coverage"]
                                if coverage["enum"] == ['supplied']:
                                    coverage["description"] = (
                                        'the decision is supported by cited actual inputs')
                                elif coverage["enum"] == ['delegated']:
                                    coverage["description"] = (
                                        'actual literal USER input explicitly permits this '
                                        'bounded creative decision')
                            if child.get("anyOf") and any(v.get("type") == "null" for v in child["anyOf"]):
                                fresh = {"anyOf": [fresh, next(
                                    dict(v) for v in child["anyOf"]
                                    if v.get("type") == "null")]}
                            if "description" in child:
                                fresh["description"] = child["description"]
                            props[name] = fresh
                        elif name == "core_document_assessment":
                            props[name] = current_assessment_schema(self.input_ids, read_ids,
                                self.readiness_source_basis_ref,
                                document_reads=tuple((identity, locator) for identity, locator, whole
                                                     in writing_read_facts if whole))
                            if "description" in child:
                                props[name]["description"] = child["description"]
                            assessment_paths.append(path + (name,))
                        else:
                            qualify_assessments(child, path + (name,))
                    for branch in node.get("anyOf", ()):
                        qualify_assessments(branch, path)
                qualify_assessments(root_schema)
                keyed_reply_paths = []
                def current_fields(node, path=()):
                    if type(node) is not dict:
                        return
                    props = node.get("properties", {})
                    for name, child in tuple(props.items()):
                        child_path = path + (name,)
                        if (child_path == ("proposal_drafting_request", "task_instructions")
                                or (self.continuation and child_path == (
                                    "follow_up_consultation", "consultation_purpose"))):
                            props[name] = _owned_visible_text_rule(child)
                        if name == "artifact_changes" and child.get("type") == "array":
                            offered_operations = child["items"].get("anyOf", [child["items"]])
                            eligible = []
                            native_kinds = {native: kind for kind, native in CHANGE_TYPES.items()}
                            target_domains = rule.domains if rule else tuple(ArtifactReviewDomain)
                            for operation in offered_operations:
                                fields = operation["properties"]
                                kinds = fields["change_type"].get("enum", ())
                                rename_kind = None
                                if "evidence_id" in fields:
                                    if len(kinds) != 1:
                                        raise ValueError("artifact operation needs one kind")
                                    operation_kind = ArtifactChangeOperationKind(
                                        native_kinds[kinds[0]]
                                    )
                                    sources = tuple(
                                        (identity, locator, whole)
                                        for identity, locator, whole in writing_read_facts
                                        if (
                                            whole
                                            or operation_kind is ArtifactChangeOperationKind.PATCH
                                        )
                                    ) + tuple(
                                        (item.evidence_id, item.locator, True)
                                        for item in core_status_facts
                                        if empty_core_status_evidence(
                                            locator=item.locator,
                                            present=(
                                                item.status is not ContextSourceStatusKind.ABSENT
                                            ),
                                            utf8_bytes=item.source_utf8_bytes,
                                            content_digest=item.source_digest,
                                        )
                                    )
                                    permitted = []
                                    for identity, locator, _whole in sources:
                                        allowed, destination_kind = artifact_source_operation_rule(
                                            operation_kind, locator
                                        )
                                        if not allowed:
                                            continue
                                        if destination_kind is not None:
                                            if destination_kind is not ArtifactKind.MANUSCRIPT:
                                                raise ValueError("unsupported rename kind")
                                            if artifact_domain(locator) not in target_domains:
                                                continue
                                            if (
                                                rename_kind is not None
                                                and destination_kind is not rename_kind
                                            ):
                                                raise ValueError("mixed rename kinds")
                                            rename_kind = destination_kind
                                        permitted.append(identity)
                                    identities = tuple(dict.fromkeys(permitted))
                                    if not identities:
                                        continue
                                    fields["evidence_id"] = {"type": "string", "enum": list(identities)}
                                for field in ("proposed_artifact_path", "proposed_destination_path"):
                                    if field in fields:
                                        guidance = artifact_path_rule(target_domains)
                                        if field == "proposed_destination_path" and rename_kind is None:
                                            raise ValueError("missing rename kind")
                                        path_kinds = (rename_kind,) if rename_kind is not None else tuple(ArtifactKind)
                                        path_rules = []
                                        for kind in path_kinds:
                                            canonical = {
                                                **artifact_kind_path_rule(kind),
                                                "description": guidance["description"],
                                            }
                                            if kind is ArtifactKind.MANUSCRIPT:
                                                if ArtifactReviewDomain.TONE_EXECUTION in target_domains:
                                                    path_rules.append(canonical)
                                            else:
                                                allowed = [path for path in CORE_ARTIFACT_PATHS
                                                           if artifact_domain(path) in target_domains]
                                                if allowed:
                                                    path_rules.append({**canonical, "enum": allowed})
                                        if not path_rules:
                                            raise ValueError("current artifact path has no qualified member")
                                        path_rule = path_rules[0] if len(path_rules) == 1 else {"anyOf": path_rules}
                                        fields[field] = {**path_rule, "description": guidance["description"]}
                                eligible.append(operation)
                            if not eligible:
                                raise ValueError("current artifact operations have no qualified member")
                            child["items"] = eligible[0] if len(eligible) == 1 else {"anyOf": eligible}
                        elif name == "reply_dispositions" and child.get("type") == "array":
                            rows = child["items"].get("anyOf", [child["items"]])
                            groups = {}
                            for row in rows:
                                identities = row["properties"]["reply_event_id"].get("enum", ())
                                if len(identities) != 1:
                                    raise ValueError("current premise replies require exact offered identities")
                                groups.setdefault(identities[0], []).append(row)
                            graphs = []
                            for anchor, anchor_rows in groups.items():
                                used = [row for row in anchor_rows if row["properties"]["disposition"]["enum"][0] in {"used", "modified"}]
                                if not used:
                                    continue
                                fields = {identity: {"anyOf": variants} if len(variants) > 1 else variants[0]
                                          for identity, variants in groups.items()}
                                fields[anchor] = {"anyOf": used} if len(used) > 1 else used[0]
                                for previous_key, previous_rows in groups.items():
                                    if previous_key == anchor:
                                        graphs.append(closed_map_rule(fields))
                                        break
                                    nonqualifying_rows = [
                                        row for row in previous_rows
                                        if row["properties"]["disposition"]["enum"][0]
                                        not in {"used", "modified"}
                                    ]
                                    if not nonqualifying_rows:
                                        break
                                    fields[previous_key] = (
                                        {"anyOf": nonqualifying_rows}
                                        if len(nonqualifying_rows) > 1 else nonqualifying_rows[0]
                                    )
                            if not graphs:
                                # Only clarification can be legal when no reply has an alternative.
                                continue
                            props[name] = graphs[0] if len(graphs) == 1 else {"anyOf": graphs}
                            keyed_reply_paths.append((child_path, tuple(groups)))
                        elif name == "dependent_domains" and child.get("type") == "array" and rule is not None:
                            draft_domains = tuple(row.domain.value for row in rule.contributions
                                                  if row.role is AuthoringContributionRole.DRAFT_OWNER)
                            choices = tuple(value for value in child.get("items", {}).get("enum", ())
                                            if value in draft_domains)
                            if choices:
                                props[name] = finite_subset_rule(choices,
                                    minimum=child.get("minItems", 0), maximum=min(child.get("maxItems", len(choices)), len(choices)))
                                keyed_reply_paths.append((child_path, choices))
                        elif name == "target_artifact_paths" and child.get("type") == "array" and child.get("maxItems") != 0:
                            choices = tuple(child.get("items", {}).get("enum", ()))
                            if choices and child.get("minItems") == child.get("maxItems") == len(choices):
                                props[name] = _fixed_selection(choices)
                                keyed_reply_paths.append((child_path, choices))
                            elif not choices:
                                # Only the current scope's own targets inherit its domain rule.
                                # Prospective targets keep their distinct owner declarations.
                                target_domains = (rule.domains if rule is not None and
                                                  child_path == ("writing_task_scope", "target_artifact_paths")
                                                  else tuple(ArtifactReviewDomain))
                                child["items"] = artifact_path_rule(target_domains)
                        current_fields(props.get(name, child), child_path)
                    for branch in node.get("anyOf", ()):
                        current_fields(branch, path)
                    if "items" in node:
                        current_fields(node["items"], path + ("*",))
                current_fields(root_schema)
                def allocations(
                    node: object, path: tuple[str, ...] = (),
                ) -> list[tuple[dict[str, object], tuple[str, ...]]]:
                    if type(node) is not dict:
                        return []
                    props = node.get("properties", {})
                    members = [(node, path)] if "assignments" in props else []
                    for name, child in props.items():
                        if child.get("type") == "object" or "anyOf" in child:
                            members.extend(allocations(child, path + (name,)))
                    for branch in node.get("anyOf", ()):
                        members.extend(allocations(branch, path))
                    return members
                allocation_members = allocations(root_schema)
                allocation_paths = tuple(dict.fromkeys(path for _, path in allocation_members))
                premise = "premise_decision" in root_schema.get("properties", {})
                for node, _path in allocation_members:
                    premise = premise or "premise_decision" in node.get("properties", {})
                if allocation_paths:
                    rosters = (tuple(combination for count in range(1, len(SPECIALIST_IDS) + 1) for combination in combinations(tuple(SPECIALIST_IDS), count))
                               if premise else legal_specialist_rosters(rule) if rule is not None else ())
                    rosters = tuple(roster for roster in rosters if set(agent for agent, _ in self.reviewer_inputs).issubset(roster)
                                    and (not self.preparation_specialists or roster == self.preparation_specialists))
                else:
                    rosters = ((),)
                for roster_number, active in enumerate(rosters):
                    keyed_paths = list(keyed_reply_paths)
                    # Separate shared nodes so every occurrence registers its decoder path.
                    offered = json.loads(json.dumps(root_schema, ensure_ascii=False))
                    for node, path in allocations(offered):
                        replacement = _allocation_wire_rule(node, active=active, rule=rule, inputs=allocation_inputs,
                                                             required=(self.input_ids if self.required_input_ids is None else self.required_input_ids) if premise else
                                tuple(identity for identity in self.required_assignment_input_ids()
                                      if not (rule is not None and rule.intent is AuthoringIntent.REVIEW_ONLY and identity == "user_input")), reviewers=() if premise else self.reviewer_inputs, premise=premise,
                            forced_inputs=self.per_assignment_input_ids,
                        review_candidate_ids=self.review_candidate_ids)
                        if premise:
                            question = replacement["properties"]["premise_decision"]
                            domains = tuple(domain.value for number, domain in enumerate(ArtifactReviewDomain)
                                            if SPECIALIST_IDS[number] in active)
                            question["properties"]["dependent_domains"] = _fixed_selection(domains)
                            keyed_paths.append((path + ("premise_decision", "dependent_domains"), domains))
                        node.clear(); node.update(replacement)
                    def finite_choices(name, child):
                        if (child.get("type") != "array"
                                or name not in {"source_ids", "basis_input_ids",
                                                "candidate_input_ids",
                                                "used_evidence_ids", "evidence_input_ids",
                                                "dependency_source_ids", "specialist_ids",
                                                "alternative_keys", "target_artifact_paths"}
                                or (name == "target_artifact_paths"
                                    and not child.get("items", {}).get("enum"))):
                            return None
                        choices = tuple(child.get("items", {}).get("enum", ()))
                        if not choices:
                            if name in {"used_evidence_ids", "evidence_input_ids"}:
                                choices = writing_read_ids
                            elif name == "specialist_ids":
                                choices = tuple(SPECIALIST_IDS)
                            elif child.get("maxItems") == 0:
                                choices = ()
                            elif name == "basis_input_ids":
                                choices = self.input_ids
                            elif name == "candidate_input_ids":
                                choices = (self.input_ids if self.review_candidate_ids is None
                                           else self.review_candidate_ids)
                            else:
                                choices = (self.source_ids if self.source_ids is not None
                                           else self.input_ids)
                        minimum = child.get("minItems", 0)
                        maximum = min(child.get("maxItems", len(choices)), len(choices))
                        return choices, minimum, maximum

                    def restrict_specialists(selection, required):
                        definitions = selection.get("$defs", {})
                        narrowed = {}
                        visiting = set()

                        def restrict(node):
                            if "$ref" in node:
                                reference = node["$ref"]
                                prefix = "#/$defs/"
                                if not reference.startswith(prefix):
                                    raise ValueError("finite selection needs local definitions")
                                name = reference[len(prefix):]
                                if name in visiting or name not in definitions:
                                    raise ValueError("finite selection definition is not acyclic")
                                if name not in narrowed:
                                    visiting.add(name)
                                    narrowed[name] = restrict(definitions[name])
                                    visiting.remove(name)
                                return None if narrowed[name] is None else deepcopy(node)
                            result = deepcopy(node)
                            if "properties" in node:
                                for name, child in node["properties"].items():
                                    if name in required:
                                        if child.get("type") != "boolean":
                                            raise ValueError(
                                                "specialist selection needs boolean leaves")
                                        if True not in child.get("enum", (False, True)):
                                            return None
                                        result["properties"][name] = {**child, "enum": [True]}
                                    else:
                                        child = restrict(child)
                                        if child is None:
                                            return None
                                        result["properties"][name] = child
                            if "anyOf" in node:
                                retained = [restrict(arm) for arm in node["anyOf"]]
                                retained = [arm for arm in retained if arm is not None]
                                if not retained:
                                    return None
                                if len(retained) == 1:
                                    if set(node) != {"anyOf"}:
                                        raise ValueError(
                                        "finite count union has unexpected siblings")
                                    return retained[0]
                                result["anyOf"] = retained
                            return result

                        root = {key: value for key, value in selection.items() if key != "$defs"}
                        result = restrict(root)
                        for name in definitions:
                            restrict({"$ref": "#/$defs/" + name})
                        if result is None:
                            raise ValueError("required specialists have no finite selection")
                        if definitions:
                            result["$defs"] = {
                                name: narrowed[name] for name in definitions
                                if narrowed[name] is not None
                            }
                        return result

                    def available(node, path=()):
                        if type(node) is not dict:
                            return True
                        for name, child in node.get("properties", {}).items():
                            choice = finite_choices(name, child)
                            if choice is not None:
                                if choice[1] > choice[2]:
                                    return False
                                if (path + (name,) == (
                                        "proposal_drafting_request", "specialist_ids")
                                        and rule is not None
                                        and (not set(rule.required_specialists).issubset(choice[0])
                                             or len(rule.required_specialists) > choice[2])):
                                    return False
                            elif not available(child, path + (name,)):
                                return False
                        if "anyOf" in node:
                            if set(node) - {"anyOf", "description", "$defs"}:
                                raise ValueError("finite alternatives have structural siblings")
                            retained = [arm for arm in node["anyOf"] if available(arm, path)]
                            if not retained:
                                return False
                            if len(retained) == 1:
                                merged = hoist_schema_definitions({**node, "anyOf": retained})
                                arm = merged.pop("anyOf")[0]
                                parent_description = merged.pop("description", None)
                                child_description = arm.get("description")
                                merged = {**arm, **merged}
                                if parent_description is not None:
                                    merged["description"] = parent_description
                                    if (child_description is not None
                                            and child_description != parent_description):
                                        merged["description"] += "\n\n" + child_description
                                node.clear(); node.update(merged)
                            else:
                                node["anyOf"] = retained
                        if "items" in node and node.get("maxItems") != 0:
                            original = deepcopy(node["items"])
                            if not available(node["items"], path + ("*",)):
                                if node.get("minItems", 0) > 0:
                                    return False
                                node["maxItems"] = 0
                                node["items"] = original
                        return True

                    if not available(offered):
                        continue
                    selectors = []
                    def selections(node: object, path: tuple[str, ...] = (),
                                   schema_path: tuple[str | int, ...] = (), guards=()) -> None:
                        if type(node) is not dict:
                            return
                        props = node.get("properties", {})
                        for name, child in tuple(props.items()):
                            child_path = path + (name,)
                            choice = finite_choices(name, child)
                            if choice is not None:
                                choices, minimum, maximum = choice
                                if minimum > maximum:
                                    raise ValueError("current finite source field has no legal choice")
                                if (name == "specialist_ids" and rule is not None
                                        and set(rule.required_specialists) == set(choices)):
                                    props[name] = _fixed_selection(choices)
                                    selectors.append((child_path, choices, None, guards))
                                else:
                                    selection = finite_selection_rule(
                                        choices, minimum=minimum, maximum=maximum)
                                    if (child_path == (
                                            "proposal_drafting_request", "specialist_ids")
                                            and rule is not None and rule.required_specialists):
                                        selection = restrict_specialists(
                                            selection, rule.required_specialists)
                                    props[name] = selection
                                    if "description" in child:
                                        props[name]["description"] = child["description"]
                                    if not (name == "candidate_input_ids"
                                            and child_path[-2:] == (
                                                "review_basis", "candidate_input_ids")
                                            and "assignments" in child_path[:-2]):
                                        selectors.append(
                                            (child_path, choices, (minimum, maximum), guards))
                            else:
                                selections(child, child_path,
                                           schema_path + ("properties", name), guards)
                        for number, variant in enumerate(node.get("anyOf", [])):
                            branch = schema_path + ("anyOf", number)
                            selections(variant, path, branch, guards + ((path, branch),))
                        if "items" in node and node.get("maxItems") != 0:
                            selections(node["items"], path + ("*",),
                                       schema_path + ("items",), guards)
                    selections(offered)
                    selector_schema = json.dumps(hoist_schema_definitions(deepcopy(offered)),
                        ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
                    raw_member = hoist_schema_definitions(offered)
                    encoded_rules = SchemaRules.declare(factor_rule_schema(
                        raw_member if member_compilation_is_identity(raw_member)
                        else _compile_closed_choices(raw_member)))
                    def decode(body: dict[str, object], *, bound_rule=rule, bound_scoped=scoped,
                               bound_paths=tuple(allocation_paths), bound_active=active,
                               bound_selectors=tuple(selectors),
                               bound_selector_schema=selector_schema,
                               bound_assessments=tuple(assessment_paths),
                               bound_keyed=tuple(keyed_paths)) -> dict[str, object]:
                        result = deepcopy(body)
                        declaration = json.loads(bound_selector_schema)
                        selection_plan = {}
                        def instances(node, path, concrete=()):
                            if not path:
                                yield concrete
                                return
                            name, *rest = path
                            if name == "*":
                                rows = (node.items() if type(node) is dict else
                                        enumerate(node) if type(node) is list else ())
                                for key, item in rows:
                                    yield from instances(item, tuple(rest), concrete + (key,))
                            elif type(node) is dict and name in node:
                                yield from instances(node[name], tuple(rest), concrete + (name,))
                        def at(node, path):
                            for part in path:
                                node = node[part]
                            return node
                        for path, choices, counts, guards in bound_selectors:
                            for concrete in instances(body, path):
                                if all(not schema_fragment_findings(
                                        at(body, concrete[:len(parent)]), declaration, branch)
                                       for parent, branch in guards):
                                    selection_plan.setdefault(concrete, (choices, counts))
                        if bound_scoped:
                            _decode_scope_wire(result["writing_task_scope"], bound_rule, self.scope, targets)
                        for path in bound_paths:
                            node = result
                            for name in path: node = node[name]
                            _decode_allocation_wire(
                            node, allocation_inputs, bound_active,
                            review_candidate_ids=self.review_candidate_ids)
                        for path, (choices, counts) in selection_plan.items():
                            parent = at(result, path[:-1])
                            name = path[-1]
                            parent[name] = (
                                _ordered_selection(parent[name], choices) if counts is None
                                            else list(decode_finite_selection(parent[name], choices,
                                                minimum=counts[0], maximum=counts[1])))
                        def decode_keyed(node, path, order):
                            name, *rest = path
                            if name == "*":
                                for row in (node.values() if type(node) is dict else node):
                                    decode_keyed(row, tuple(rest), order)
                            elif rest:
                                if name in node:
                                    decode_keyed(node[name], tuple(rest), order)
                            elif name in node and type(node[name]) is dict:
                                value = node[name]
                                node[name] = ([value[key] for key in order] if name == "reply_dispositions"
                                              else [key for key in order if key in value])
                        for path, order in bound_keyed:
                            decode_keyed(result, path, order)
                        for assessment_path in bound_assessments:
                            parent = result
                            for name in assessment_path[:-1]:
                                parent = parent[name]
                            parent[assessment_path[-1]] = decode_current_assessment(parent[assessment_path[-1]])
                        map_current_task_literals(result, tasks)
                        return normalize_result(result, contract.contract_id)
                    branch_key = f"{contract.contract_id}:{root_number}:{scope_number}:{roster_number}"
                    if grouped:
                        branch_key += ":group:" + group_id
                    variants.append(LeadWireVariant(branch_key, encoded_rules, decode,
                                                    group_id=group_id, logical_root=root_number))
        if not variants:
            raise ValueError("current Lead stage has no eligible response branch")
        if collect_schema_groups:
            return tuple(variants)
        return _compile_lead_families(
            contract.contract_id, roots, variants, container_description, tasks,
            selected_group_id=selected_group_id,
        )

    def prompt_facts(self) -> dict[str, object]:
        return {
            **({"source_basis_ref": self.readiness_source_basis_ref,
                "core_document_readiness_rule": "Assess whole-book extent from actual USER directions/material; cite actual acquired whole-document READ IDs. Include all four documents and all seventeen criteria. Missing downstream documents permit grounded bootstrap, never manuscript drafting. Character and style both depend on both outlines; style need not wait for character completion. Reader secrecy requires USER nondisclosure plus an actual resolved author-side fact. Completion grants no review, Confirm, Apply or Commit authority."}
               if self.readiness_source_basis_ref is not None else {}),
            **({"adoption_intent": self.adoption_intent.value, "adoption_targets": list(self.adoption_targets),
                "adoption_rule": "Exact selected bytes are USER input only. Propose fresh work at the offered task@1 with the explicit intent and targets; retain adopted_idea in every assignment. Never import draft, READ, review or write authority."}
               if self.adoption_intent is not None else {}),
            **({"offered_premise_question_id": self.premise_question_id,
                "premise_analysis_rule": "Only an undecided question needs target-free alternatives analysis. Reuse disclosed resolved inputs. Analysis requires its own Confirm and cannot draft, review a candidate, select a premise, or update an objective."}
               if self.premise_question_id is not None else {}),
            "offered_admission_source_ids": list(self.input_ids),
            "authoring_admission_rule": (
                "Before official drafting or document-change preparation, assess the actual supplied story foundations, "
                "USER direction, bounded creative permissions/protected constraints and required decisions. "
                "Use exact offered USER/material IDs or completed READ evidence IDs; a file indicator, summary or "
                "confidence is not a READ or semantic sufficiency. A detailed literal USER brief may suffice with "
                "empty targets. Identify only actual missing decisions; do not ask again for already supplied facts. "
                "Use needs_information/analysis_only and grounded questions when inadequate. Rejected public "
                "answers stay input-only. The assessment grants no confirmation, write or budget authority."
            ),
            "offered_assignment_input_ids": list(self._allocation_input_ids()),
            "required_input_ids": [item for item in self.input_ids if (not self.preparation_kind or item not in PREPARATION_INPUT_IDS)
                                   and not (self.adoption_intent is AuthoringIntent.REVIEW_ONLY and item == "user_input")],
            "mandatory_reviewer_inputs": [
                {"agent_id": agent, "input_id": alias} for agent, alias in self.reviewer_inputs
            ],
            "confirmed_scope": None if self.scope is None else {
                **({"intent": self.scope.intent.value} if self.scope.schema_version == 2 else {}),
                "activity": self.scope.activity.value,
                "target_paths": list(self.scope.target_paths),
                "domains": [item.value for item in self.scope.domains],
                "task": f"{self.scope.task_id}@{self.scope.task_revision}",
            },
            "preparation": None if self.preparation_kind is None else {
                "kind": self.preparation_kind,
                "objective_id": self.preparation_objective_id,
                "target_paths": list(self.preparation_targets),
                "specialist_ids": list(self.preparation_specialists),
                "recommendation_only": self.recommendation_only,
            },
            "objective_steps": {"question": "clarify", "consultation": "consult", "artifact": "prepare"},
            "specialist_order": list(SPECIALIST_IDS),
            "domain_order": [item.value for item in ArtifactReviewDomain],
            "source_membership": (
                "Assignment input_ids select only the offered assignment inputs"
                + (" or evidence IDs from completed authorized READs. " if self.allow_read_sources else ". ")
                + "Historical draft IDs offered for preparation recommendations are not assignment inputs until "
                "selected and materialized by confirmed preparation. "
                "Consultation and continuation assignments must cover every required input; each core-document reviewer must "
                "receive its own source. Do not invent or omit source aliases."
            ),
            "offered_source_ids": None if self.source_ids is None else list(self.source_ids),
            "recommendation_source_membership": (
                "Prior same-task revision drafts may be selected only as input material for a fresh "
                "AUTHORING DRAFT/REVISE consultation preparation. Their event, owner, scope and digest "
                "remain historical; they never satisfy current owner drafts, candidate review or Apply. "
                "Artifact preparation still requires the exact current completed authoring source. "
                "Recommendation source_ids select only the offered preparation recommendation sources"
                + (" or evidence IDs from completed authorized READs. " if self.allow_read_sources else ". ")
                + "These sources are not extra assignment inputs. To prepare a document from an approved historical draft, "
                "select its exact offered draft ID and preserve its authoring task and specialist provenance. "
                "Use artifact preparation without another drafting consultation when that task already has complete "
                "required specialist drafts; otherwise use consultation preparation. Confirmation materializes the "
                "selected sources without authorizing Apply or Commit."
            ),
            "artifact_path_rule": (
                "Use normalized workspace-relative POSIX artifact paths. Existing core-document targets are only "
                "core_documents/global_outline.md, core_documents/part_outlines.md, "
                "core_documents/character_sheets.md and core_documents/narrative_rules.md. "
                "New documents use unused manuscript/PART/PATH.md paths. PART is 1-64 ASCII letters, digits, "
                "underscores or hyphens, starting with a letter or digit. Every remaining path component is "
                "1-128 ASCII letters, digits, underscores, hyphens or dots, starting with a letter or digit; "
                "the final component ends in .md. The whole path is at most 1024 UTF-8 bytes. "
                "Keep the requested title and language in the document content and disclose the proposed destination. "
                "An existing empty document needs replacement, not creation."
            ),
            "offered_objective_ids": None if self.objective_ids is None else list(self.objective_ids),
            "runtime_input_ids": list(PREPARATION_INPUT_IDS) if self.preparation_kind else [],
            "runtime_input_rule": "Runtime appends only its recommendation request/decision aliases after exact confirmation.",
        }

    def scope_schema(self) -> dict[str, object]:
        scope = self.scope
        intent = scope.intent if scope else self.adoption_intent
        targets = scope.target_paths if scope else self.adoption_targets if intent is not None else None
        activities = ([scope.activity.value] if scope else [AuthoringActivity.REVIEW.value]
                      if intent is AuthoringIntent.REVIEW_ONLY else [AuthoringActivity.DRAFT.value, AuthoringActivity.REVISE.value]
                      if intent is AuthoringIntent.AUTHORING else [item.value for item in AuthoringActivity])
        tokens = tuple(f"{task}@{revision}" for task, revision in self.task_revisions)
        task: dict[str, object] = {"type": "string"}
        if tokens:
            task["enum"] = list(tokens)
        return {**closed_object({
            **_scope_extensions(scope),
            **({"intent": {
                "type": "string",
                "enum": [intent.value] if intent is not None else [item.value for item in AuthoringIntent],
                "description": (
                    "Disclose review_only only for an explicit request to evaluate the supplied "
                    "original without authoring or artifact authority. Activity review alone does "
                    "not imply this intent. Preserve bound intent; requested revisions need fresh "
                    "authoring work."
                ),
            }} if scope is None or scope.schema_version >= 2 else {}),
            "activity": {"type": "string", "enum": (activities)},
            "target_paths": string_array(targets, minimum=len(targets) if targets is not None else 0, maximum=len(targets) if targets is not None else None),
            "domains": string_array(
                tuple(item.value for item in scope.domains) if scope else
                tuple(item.value for item in ArtifactReviewDomain),
                minimum=len(scope.domains) if scope else 0,
                maximum=len(scope.domains) if scope else len(ArtifactReviewDomain),
            ),
            "task": task,
        }), "description": (
            "Classify the current work and include every affected specialist domain and "
            "canonical workspace-relative target. Preserve the bound scope and task/revision "
            "when supplied."
        )}

    def assignment_schema(self) -> dict[str, object]:
        required = () if self.scope is None else required_authoring_specialists(self.scope)
        mode = "review" if self.scope and self.scope.activity is AuthoringActivity.REVIEW else "draft"
        agents = self.preparation_specialists or tuple(SPECIALIST_IDS)
        input_rule = (
            "Assignment sources use only offered assignment inputs"
            + (" or evidence IDs from completed authorized READs. " if self.allow_read_sources else ". ")
            + "Preparation recommendation sources are not assignment inputs unless confirmed preparation has "
            "materialized and offered them here. Preserve all required inputs and each reviewer's own source."
        )
        if self.adoption_intent is AuthoringIntent.REVIEW_ONLY:
            input_rule += " USER instruction input is host-wired separately from the exact adopted_idea supplied candidate; preserve every other declared source role."
        assignment_purposes = {
            "plot_architect": (
                "Assign Plot when plot, causality, pacing, or outline structure is affected; "
                "request its scoped draft or review."
            ),
            "character_custodian": (
                "Assign Character when characterization, motivation, or relationships are "
                "affected; request its scoped draft or review."
            ),
            "execution_specialist": (
                "Assign Execution when scene prose, narrative voice, or narrative rules are "
                "affected; request its scoped draft or review."
            ),
        }
        return alternatives([{**closed_object({
            "agent_id": {"type": "string", "enum": [agent]},
            "instruction": {
                **nonempty_text_schema(),
                "description": (
                    "Nonempty UTF-8 task instructions specifying this specialist's "
                    "concrete deliverable and success criteria "
                    "against the shared writing-task brief. Preserve its protected elements, "
                    "change boundaries, and exact offered task/source identities."
                ),
            },
            "input_ids": {
                **string_array(None if self.allow_read_sources else self.input_ids, minimum=1,
                               maximum=None if self.allow_read_sources else len(self.input_ids) or None),
                "description": input_rule,
            },
            "mode": {"type": "string", "enum": ([assignment_mode_for_specialist(self.scope, agent).value] if self.scope and self.scope.schema_version == 3 and agent in required else
                [mode] if agent in required or (self.scope and self.scope.intent is AuthoringIntent.REVIEW_ONLY) else
                ["draft", "review", "constraint_assessment"] if self.scope is None else ["draft", "review"])},
        }), "description": assignment_purposes[agent]} for agent in agents])

    def objective_schema(self, step: str | None, *, cancel: bool = False) -> dict[str, object]:
        if self.preparation_kind:
            if self.preparation_objective_id is not None:
                return {**closed_object({"action": {"type": "string", "enum": ["continue"]},
                    "objective_id": {"type": "string", "enum": [self.preparation_objective_id]},
                    "next_step": {"type": "string", "enum": [step]}}), "description": (
                        "Advance the matching pending document change request through this "
                        "workflow step; preserve its identity and do not claim it is applied."
                    )}
            return {**closed_object({"action": {"type": "string", "enum": ["start"]},
                "target_paths": string_array(self.preparation_targets, minimum=len(self.preparation_targets),
                                             maximum=len(self.preparation_targets)),
                "next_step": {"type": "string", "enum": [step]}}), "description": (
                    "Start a document change request when no matching request exists; track "
                    "the selected targets without treating preparation as Apply."
                )}
        variants = [{**closed_object({"action": {"type": "string", "enum": ["start"]},
            "target_paths": string_array(minimum=1), "next_step": {"type": "string", "enum": [step]}}), "description": (
                "Start a document change request when no matching request exists; track the "
                "selected targets without treating preparation as Apply."
            )}]
        if self.objective_ids is None or self.objective_ids:
            identity: dict[str, object] = {"type": "string"}
            if self.objective_ids:
                identity["enum"] = list(self.objective_ids)
            variants.append({**closed_object({"action": {"type": "string", "enum": ["continue"]},
                "objective_id": identity, "next_step": {"type": "string", "enum": [step]}}), "description": (
                    "Advance the matching pending document change request through this "
                    "workflow step; preserve its identity and do not claim it is applied."
                )})
            if cancel:
                variants.append({**closed_object({"action": {"type": "string", "enum": ["cancel"]},
                                               "objective_id": identity}), "description": (
                                                   "Cancel the selected document change request only on explicit"
                                                   " user cancellation; close tracking without changing "
                                                   "documents."
                                               )})
        return alternatives(variants)

    def recommendation_schema(self) -> dict[str, object]:
        sources = None if self.allow_read_sources else self.source_ids
        source_schema = string_array(sources, minimum=1, maximum=len(sources) if sources else None)
        source_schema["description"] = (
            "Recommendation source_ids select only offered preparation recommendation sources"
            + (" or evidence IDs from completed authorized READs. " if self.allow_read_sources else ". ")
            + "Select the exact approved historical draft to prepare its document; preserve its task and provenance. "
            "This selection does not make it an ordinary assignment input or authorize a write."
        )
        common = {"instruction": nonempty_text_schema(), "source_ids": source_schema}
        if self.scope and self.scope.intent is AuthoringIntent.REVIEW_ONLY:
            return {**closed_object({
                **common, "objective_id": {"type": "string", "enum": []},
                "kind": {"type": "string", "enum": ["prepare_consultation"]},
                "specialist_ids": string_array(
                    tuple(SPECIALIST_IDS), minimum=1, maximum=len(SPECIALIST_IDS),
                ),
            }, required=(*common, "kind", "specialist_ids")), "description": (
                "Prepare only another exact supplied-material review consultation; "
                "no replacement or artifact-objective authority."
            )}
        identity: dict[str, object] = {"type": "string"}
        if self.objective_ids is not None:
            identity["enum"] = list(self.objective_ids)
        objective = {"objective_id": identity}
        association_description = (
            "Include the offered document change request identity only when continuing that request."
        )
        variants = []
        variants.extend([
            {**closed_object({**common, **objective, "kind": {"type": "string", "enum": ["prepare_artifact"]},
                           "target_paths": string_array(minimum=1)}, required=(*common, "kind", "target_paths")), "description": (
                               "Draft a document-change proposal when the selected sources "
                               "already support exact changes to these targets. "
                           ) + association_description},
            {**closed_object({**common, **objective, "kind": {"type": "string", "enum": ["prepare_consultation"]},
                           "specialist_ids": string_array(tuple(SPECIALIST_IDS), minimum=1, maximum=len(SPECIALIST_IDS))}, required=(*common, "kind", "specialist_ids")), "description": (
                               "Draft a specialist consultation proposal when further "
                               "drafting or review is needed; include every affected "
                               "specialist. "
                           ) + association_description},
        ])
        result = alternatives(variants)
        result["description"] = (
            (
                "Request confirmation for one Lead proposal-drafting step, not specialist "
                "execution, Apply, or Commit. "
            )
            + result.get("description", "")
        ).rstrip()
        return result

    def diagnose(self, answer: str, *, read_ids: tuple[str, ...] = (),
                 read_reviewers: tuple[tuple[str, str], ...] = ()) -> tuple[DomainValidationRule, ...]:
        try:
            value = json.loads(answer)
        except (TypeError, ValueError):
            return (DomainValidationRule.MALFORMED_RESULT,)
        return self.rules(value, read_ids=read_ids, read_reviewers=read_reviewers)

    def rules(self, value: object, *, read_ids: tuple[str, ...] = (),
                 read_reviewers: tuple[tuple[str, str], ...] = ()) -> tuple[DomainValidationRule, ...]:
        if type(value) is dict and "core_document_assessment" in value:
            if (self.readiness_source_basis_ref is None
                    or set(value) != {"kind", "message", "authoring_scope", "core_document_assessment"}
                    or value.get("kind") != "answer" or value.get("authoring_scope") is not None
                    or not visible_text(value.get("message"))):
                return (DomainValidationRule.LEAD_OUTCOME_REJECTED,)
            return ()
        if type(value) is dict and type(value.get("proposal")) is dict and "premise_decision" in value["proposal"]:
            if self.premise_question_id is None:
                return (DomainValidationRule.LEAD_OUTCOME_REJECTED,)
            return PremiseDecisionConstraints(self.premise_question_id, self.input_ids,
                allow_read_sources=self.allow_read_sources).front_rules(value, read_ids=read_ids)
        if not self.allow_read_sources:
            read_ids = ()
            read_reviewers = ()
        R = DomainValidationRule
        if type(value) is not dict:
            return (R.RESULT_OBJECT_REQUIRED,)
        rules: list[DomainValidationRule] = []
        raw_scope = value.get("authoring_scope")
        message = value.get("message")
        if not visible_text(message) or (type(raw_scope) is dict and type(message) is str
                                        and (not message.strip() or "\x00" in message)):
            rules.append(R.MESSAGE_REQUIRED)
        if type(raw_scope) is dict:
            domains = raw_scope.get("domains")
            if type(domains) is list and all(type(item) is str for item in domains):
                if len(set(domains)) != len(domains):
                    rules.append(R.DOMAIN_DUPLICATE)
            paths = raw_scope.get("target_paths")
            if type(paths) is list and all(type(item) is str for item in paths) and len(set(paths)) != len(paths):
                rules.append(R.TARGET_DUPLICATE)
            if self.scope and (raw_scope.get("activity") != self.scope.activity.value
                or raw_scope.get("intent", "authoring") != self.scope.intent.value
                or paths != list(self.scope.target_paths)
                or type(domains) is not list
                or sorted(domains, key=str) != sorted(item.value for item in self.scope.domains)
                or raw_scope.get("task") not in {f"{task}@{revision}" for task, revision in self.task_revisions}):
                rules.append(R.PREPARATION_SCOPE)
        if self.adoption_intent is not None and type(raw_scope) is dict and (raw_scope.get("intent") != self.adoption_intent.value
                or raw_scope.get("activity") not in ([AuthoringActivity.REVIEW.value] if self.adoption_intent is AuthoringIntent.REVIEW_ONLY
                                                     else [AuthoringActivity.DRAFT.value, AuthoringActivity.REVISE.value])
                or raw_scope.get("target_paths") != list(self.adoption_targets)
                or raw_scope.get("task") not in {f"{task}@{revision}" for task, revision in self.task_revisions}):
            rules.append(R.PREPARATION_SCOPE)
        proposal = value.get("next_intent" if self.continuation else "proposal")
        if type(proposal) is dict:
            if self.continuation and not visible_text(proposal.get("purpose")):
                rules.append(R.PURPOSE_REQUIRED)
            assignments = proposal.get("assignments")
            if type(assignments) is list:
                roles = [item.get("agent_id") for item in assignments if type(item) is dict]
                if all(type(item) is str for item in roles) and len(set(roles)) != len(roles):
                    rules.append(R.SPECIALIST_DUPLICATE)
                selected: set[str] = set()
                by_role: dict[str, set[str]] = {}
                required = set((*self.input_ids, *read_ids))
                if self.adoption_intent is AuthoringIntent.REVIEW_ONLY:
                    required.discard("user_input")
                if self.preparation_kind:
                    required.difference_update(PREPARATION_INPUT_IDS)
                for item in assignments:
                    if type(item) is not dict:
                        continue
                    instruction = item.get("instruction")
                    if not visible_text(instruction) or (
                        item.get("mode") == "review" and type(instruction) is str
                        and (not instruction.strip() or "\x00" in instruction)
                    ):
                        rules.append(R.INSTRUCTION_REQUIRED)
                    aliases = item.get("input_ids")
                    if type(aliases) is not list or not aliases or any(type(alias) is not str for alias in aliases):
                        rules.append(R.ASSIGNMENT_INPUTS_REQUIRED)
                        continue
                    if len(set(aliases)) != len(aliases):
                        rules.append(R.INPUT_DUPLICATE)
                    if not set(aliases).issubset((*self.input_ids, *read_ids)):
                        rules.append(R.INPUT_NOT_OFFERED)
                    selected.update(aliases)
                    if type(item.get("agent_id")) is str:
                        by_role[item["agent_id"]] = set(aliases)
                if not required.issubset(selected):
                    rules.append(R.CONTINUATION_INPUT_COVERAGE if self.continuation else R.INPUT_COVERAGE)
                if any(alias not in by_role.get(agent, set()) for agent, alias in (*self.reviewer_inputs, *read_reviewers)):
                    rules.append(R.REVIEWER_INPUT)
                scope = self.scope
                if scope is None and type(raw_scope) is dict:
                    domains = raw_scope.get("domains", [])
                    paths = raw_scope.get("target_paths", [])
                    try:
                        scope = _scope_for_rules(raw_scope)
                    except (KeyError, TypeError, ValueError):
                        scope = None
                if scope:
                    required_roles = required_authoring_specialists(scope)
                    if not set(required_roles).issubset(roles):
                        rules.append(R.SPECIALIST_REQUIRED)
                    expected_mode = "review" if scope.activity is AuthoringActivity.REVIEW else "draft"
                    if any(
                        type(item) is dict
                        and (item.get("agent_id") in required_roles
                             or scope.intent is AuthoringIntent.REVIEW_ONLY)
                        and item.get("mode") != (assignment_mode_for_specialist(scope, item["agent_id"]).value if scope.schema_version == 3 else expected_mode)
                        for item in assignments
                    ):
                        rules.append(R.ASSIGNMENT_MODE)
                if self.preparation_specialists and tuple(role for role in SPECIALIST_IDS if role in roles) != self.preparation_specialists:
                    rules.append(R.PREPARATION_SPECIALISTS)
        action = value.get("recommended_action")
        if type(action) is dict:
            roles = action.get("specialist_ids")
            if (
                action.get("kind") == "prepare_consultation"
                and type(roles) is list
                and all(type(role) is str for role in roles)
            ):
                if not roles or len(set(roles)) != len(roles) or any(role not in SPECIALIST_IDS for role in roles):
                    rules.append(R.RECOMMENDATION_SPECIALISTS)
                if type(raw_scope) is dict:
                    try:
                        selected_scope = _scope_for_rules(raw_scope)
                        if not set(required_authoring_specialists(selected_scope)).issubset(roles):
                            rules.append(R.RECOMMENDATION_SPECIALISTS)
                    except (KeyError, TypeError, ValueError):
                        pass
            paths = action.get("target_paths")
            if type(paths) is list and all(type(path) is str for path in paths):
                if not paths or len(set(paths)) != len(paths):
                    rules.append(R.RECOMMENDATION_TARGETS)
            if not visible_text(action.get("instruction")):
                rules.append(R.RECOMMENDATION_INSTRUCTION)
            sources = action.get("source_ids")
            if type(sources) is list and all(type(item) is str for item in sources):
                if not sources or len(set(sources)) != len(sources):
                    rules.append(R.RECOMMENDATION_SOURCES)
                if self.source_ids is not None and not set(sources).issubset((*self.source_ids, *read_ids)):
                    rules.append(R.SOURCE_NOT_OFFERED)
            if self.objective_ids is not None and "objective_id" in action and action["objective_id"] not in self.objective_ids:
                rules.append(R.RECOMMENDATION_OBJECTIVE_NOT_OFFERED)
        artifact = value.get("artifact_intent")
        nested = artifact if type(artifact) is dict else proposal
        objective = nested.get("objective_intent") if type(nested) is dict else value.get("objective_intent")
        if type(objective) is dict:
            targets = objective.get("target_paths")
            if objective.get("action") == "start" and type(targets) is list and all(type(path) is str for path in targets):
                if not targets or len(set(targets)) != len(targets):
                    rules.append(R.ARTIFACT_OBJECTIVE_TARGETS if type(artifact) is dict else
                                 R.PROPOSAL_OBJECTIVE_TARGETS if type(proposal) is dict else R.OBJECTIVE_TARGETS)
            if self.objective_ids is not None and "objective_id" in objective and objective["objective_id"] not in self.objective_ids:
                rules.append(R.OBJECTIVE_NOT_OFFERED)
            expected_step = "prepare" if type(artifact) is dict else "consult" if type(proposal) is dict else "clarify"
            if objective.get("action") == "cancel":
                if type(artifact) is dict or type(proposal) is dict or value.get("kind") == "question":
                    rules.append(R.OBJECTIVE_BRANCH)
            elif (objective.get("next_step") != expected_step
                  or (expected_step == "clarify" and value.get("kind") != "question")):
                rules.append(R.OBJECTIVE_BRANCH)
        if self.preparation_kind and not self.recommendation_only and value.get("kind") != "question":
            expected_field = "artifact_intent" if self.preparation_kind == "prepare_artifact" else "proposal"
            if value.get("kind") != "proposal" or type(value.get(expected_field)) is not dict:
                rules.append(R.PREPARATION_KIND)
            needs_objective = expected_field == "artifact_intent" or self.preparation_objective_id is not None
            if needs_objective:
                expected_action = "start" if self.preparation_objective_id is None else "continue"
                if (type(objective) is not dict or objective.get("action") != expected_action
                    or (self.preparation_objective_id is not None and objective.get("objective_id") != self.preparation_objective_id)
                    or (self.preparation_objective_id is None and objective.get("target_paths") != list(self.preparation_targets))):
                    rules.append(R.PREPARATION_OBJECTIVE)
            elif objective is not None:
                rules.append(R.PREPARATION_OBJECTIVE)
        if self.continuation:
            continuation_rules = {
                R.INSTRUCTION_REQUIRED: R.CONTINUATION_INSTRUCTION_REQUIRED,
                R.SPECIALIST_DUPLICATE: R.CONTINUATION_SPECIALIST_DUPLICATE,
                R.ASSIGNMENT_INPUTS_REQUIRED: R.CONTINUATION_ASSIGNMENT_INPUTS_REQUIRED,
                R.INPUT_DUPLICATE: R.CONTINUATION_INPUT_DUPLICATE,
                R.INPUT_NOT_OFFERED: R.CONTINUATION_INPUT_NOT_OFFERED,
                R.REVIEWER_INPUT: R.CONTINUATION_REVIEWER_INPUT,
                R.SPECIALIST_REQUIRED: R.CONTINUATION_SPECIALIST_REQUIRED,
                R.ASSIGNMENT_MODE: R.CONTINUATION_ASSIGNMENT_MODE
            }
            rules = [continuation_rules.get(rule, rule) for rule in rules]
        return tuple(dict.fromkeys(rules))


@dataclass(frozen=True, slots=True)
class LeadGroupCompilation:
    """현재 소유자의 의미 변형을 보존하고 선택 그룹만 완전히 컴파일한다."""
    constraints: LeadProviderConstraints
    rule_seed: LeadResultRuleSeed
    read_ids: tuple[str, ...] = ()
    writing_read_ids: tuple[str, ...] = ()
    writing_read_facts: tuple[tuple[str, str, bool], ...] = ()
    core_status_facts: tuple[ContextSourceStatusReceipt, ...] = ()
    read_facts: tuple[ContextCallReadFact, ...] = field(default=(), kw_only=True)
    semantic_variants: tuple[LeadWireVariant, ...] = field(init=False, repr=False, compare=False)
    _semantic_decoders: tuple[TypedDecoder, ...] = field(init=False, repr=False, compare=False)
    group_ids: tuple[str, ...] = field(init=False)
    _root_source: bytes = field(init=False, repr=False)
    _declaration: tuple = field(init=False, repr=False, compare=False)

    def __init_subclass__(cls, **kwargs):
        raise TypeError("의미 그룹 compiler의 소유 경계를 바꿀 수 없습니다")

    def __post_init__(self) -> None:
        if (type(self.constraints) is not LeadProviderConstraints
                or type(self.rule_seed) is not LeadResultRuleSeed
                or self.constraints.allow_read_sources
                or any(type(value) is not tuple for value in
                       (self.read_ids, self.writing_read_ids, self.writing_read_facts,
                        self.core_status_facts, self.read_facts))
                or any(type(item) is not ContextCallReadFact for item in self.read_facts)
                or len({item.evidence_id for item in self.read_facts}) != len(self.read_facts)):
            raise TypeError("현재 소유자가 고정한 전체 의미 변형 근거가 필요합니다")
        writing_facts = tuple(item for item in self.read_facts
                              if item.source_kind is ContextSourceKind.WRITING_ARTIFACT)
        if (self.read_ids != tuple(item.evidence_id for item in self.read_facts)
                or self.writing_read_ids != tuple(item.evidence_id for item in writing_facts)
                or self.writing_read_facts != tuple(
                    (item.evidence_id, item.artifact_locator, item.whole_source)
                    for item in writing_facts)):
            raise ValueError("READ 별칭과 현재 전체 source 사실이 일치해야 합니다")
        variants = self.constraints.current_wire_variants(
            self.rule_seed, read_ids=self.read_ids, writing_read_ids=self.writing_read_ids,
            writing_read_facts=self.writing_read_facts, core_status_facts=self.core_status_facts,
            collect_schema_groups=True,
        )
        if any(item.group_id is None for item in variants):
            raise ValueError("ordinary Lead의 모든 의미 변형을 분류해야 합니다")
        ids = {item.group_id for item in variants}
        object.__setattr__(self, "semantic_variants", variants)
        decoders = []
        for variant in variants:
            def decode(value: ValidatedValue, *, source=variant.decode) -> dict[str, object]:
                return source(value.as_object())
            decoders.append(TypedDecoder(
                "lead.prepared_semantic_codec", "894-v1:prepared-owner-source", decode,
                (variant.decode, *_lead_codec_dependencies()),
            ))
        object.__setattr__(self, "_semantic_decoders", tuple(decoders))
        object.__setattr__(self, "group_ids", tuple(
            group.group_id for group in LEAD_SCHEMA_GROUPS if group.group_id in ids))
        object.__setattr__(self, "_root_source", json.dumps(
            _current_lead_rule_schema(self.rule_seed), sort_keys=True, ensure_ascii=False,
            allow_nan=False, separators=(",", ":")).encode("utf-8"))
        object.__setattr__(self, "_declaration", self._snapshot())

    def _snapshot(self) -> tuple:
        functions = (LeadProviderConstraints.current_wire_variants, _compile_lead_families,
                     _current_lead_rule_schema, _lead_codec_dependencies,
                     schema_group_for, LeadSchemaGroup.accepts,
                     type(self).matches_current, type(self).catalog, type(self).compile)
        return (repr(self.constraints), self.rule_seed.contract_id, self.rule_seed.domain_rules,
                self.read_ids, self.writing_read_ids, self.writing_read_facts,
                tuple(tuple(item.snapshot_value().items()) for item in self.read_facts),
                repr(self.core_status_facts), self.group_ids, self._root_source,
                tuple((id(item), item.branch_key, item.group_id, item.logical_root,
                       id(item.rules), item.rules.canonical_bytes, item.rules.maximum_bytes, id(item.decode))
                      for item in self.semantic_variants),
                tuple((id(item), item.binding_fingerprint) for item in self._semantic_decoders),
                tuple((function, function.__code__) for function in functions),
                json.dumps(tuple((group.card(), group.membership_identity()) for group in LEAD_SCHEMA_GROUPS),
                           ensure_ascii=False, sort_keys=True, separators=(",", ":")))

    def require_provenance(self) -> None:
        if self._snapshot() != self._declaration:
            raise ValueError("동결된 의미 그룹 compiler 또는 선택 근거가 변경됐습니다")
        for decoder in self._semantic_decoders:
            decoder.require_provenance()

    def catalog(self, *, request_basis: Mapping[str, object]) -> LeadGroupCatalog:
        self.require_provenance()
        request = {
            "request": dict(request_basis), "contract_id": self.rule_seed.contract_id,
            "rule_seed_digest": hashlib.sha256(self.rule_seed.domain_rules).hexdigest(),
            "task_revisions": self.constraints.task_revisions,
            "scope": repr(self.constraints.scope), "scope_rule": repr(self.constraints.scope_rule),
            "adoption_intent": self.constraints.adoption_intent,
            "preparation_kind": self.constraints.preparation_kind,
            "preparation_objective_id": self.constraints.preparation_objective_id,
            "preparation_targets": self.constraints.preparation_targets,
            "preparation_specialists": self.constraints.preparation_specialists,
            "recommendation_only": self.constraints.recommendation_only,
            "continuation": self.constraints.continuation,
            "premise_question_id": self.constraints.premise_question_id,
            "readiness_source_basis_ref": self.constraints.readiness_source_basis_ref,
        }
        context = {
            "choices": self.constraints.choice_snapshot(), "read_ids": self.read_ids,
            "writing_read_ids": self.writing_read_ids, "writing_read_facts": self.writing_read_facts,
            "read_facts": [item.snapshot_value() for item in self.read_facts],
            "core_status_facts": repr(self.core_status_facts),
        }
        return LeadGroupCatalog.build(self.group_ids, request_basis=request, context_basis=context)

    def matches_current(
        self, constraints: LeadProviderConstraints, rule_seed: LeadResultRuleSeed, *,
        read_facts: tuple[ContextCallReadFact, ...],
        core_status_facts: tuple[ContextSourceStatusReceipt, ...],
    ) -> bool:
        """Allow caller-owned reuse only for the same complete semantic inputs."""
        self.require_provenance()
        return (self.constraints == constraints and self.rule_seed is rule_seed
                and self.read_facts == read_facts and self.core_status_facts == core_status_facts)

    def compile(self, group_id: str) -> tuple[LeadWireVariant, ...]:
        self.require_provenance()
        if type(group_id) is not str or group_id not in self.group_ids:
            raise ValueError("현재 소유자가 제공하지 않은 그룹을 컴파일할 수 없습니다")
        projected = json.loads(self._root_source)
        roots = projected.get("anyOf", [projected])
        tasks = self.constraints.task_revisions or (() if self.constraints.scope is None else (
            (self.constraints.scope.task_id, self.constraints.scope.task_revision),))
        members = tuple(item for item in self.semantic_variants if item.group_id == group_id)
        return _compile_lead_families(
            self.rule_seed.contract_id, roots, members,
            projected.get("description") if "anyOf" in projected else None,
            tasks, selected_group_id=group_id,
        )


@dataclass(frozen=True, slots=True)
class LeadGroupedStage:
    """하나의 현재 의미 계획에서 카탈로그·선택·그룹 업무 owner를 결합한다."""
    compilation: LeadGroupCompilation
    request_basis: LeadGroupRequestBasis
    selection_policy: LeadStagePolicy
    authorization_check: NamedHostCheck[LeadDecodedResult] = field(repr=False, compare=False)
    catalog: LeadGroupCatalog = field(init=False)
    _declaration: tuple = field(init=False, repr=False, compare=False)

    def __init_subclass__(cls, **kwargs):
        raise TypeError("그룹 국면의 owner 결합을 독립된 구현으로 바꿀 수 없습니다")

    @staticmethod
    def catalog_for(
        compilation: LeadGroupCompilation, request_basis: LeadGroupRequestBasis, *,
        stage: str, language: str, authorization_check: NamedHostCheck,
    ) -> LeadGroupCatalog:
        """Bind request/task facts to the actual trusted factory's authority owner."""
        if (type(compilation) is not LeadGroupCompilation
                or type(request_basis) is not LeadGroupRequestBasis
                or type(authorization_check) is not NamedHostCheck
                or authorization_check.name != "owner.authorized_final"
                or authorization_check.phase != "authorized"
                or any(type(value) is not str or not value for value in (stage, language))):
            raise TypeError("현재 요청과 실제 factory의 업무 권한 선언이 필요합니다")
        authorization_check.require_provenance()
        return compilation.catalog(request_basis={
            **request_basis.as_object(), "stage": stage, "language": language,
            "authorization_owner": {
                "name": authorization_check.name, "revision": authorization_check.revision,
                "binding_fingerprint": authorization_check.binding_fingerprint,
                "context_type": (authorization_check.context_type.__module__ + "."
                                 + authorization_check.context_type.__qualname__),
            },
        })

    def __post_init__(self) -> None:
        if (type(self.compilation) is not LeadGroupCompilation
                or type(self.request_basis) is not LeadGroupRequestBasis
                or type(self.selection_policy) is not LeadStagePolicy
                or type(self.authorization_check) is not NamedHostCheck
                or self.authorization_check.name != "owner.authorized_final"
                or self.authorization_check.phase != "authorized"):
            raise TypeError("현재 의미 계획과 실제 업무 권한 검사가 결합된 국면이 필요합니다")
        owners = self.selection_policy.owner_declarations
        tasks = self.compilation.constraints.task_revisions or (() if self.compilation.constraints.scope is None else (
            (self.compilation.constraints.scope.task_id, self.compilation.constraints.scope.task_revision),))
        if (len(owners) != 1 or type(owners[0]) is not SchemaGroupControlOwner
                or owners[0].current_group_id is not None
                or self.selection_policy.task_revisions != tasks
                or self.selection_policy.choices != self.compilation.constraints.choice_snapshot()):
            raise ValueError("선택 국면은 해당 의미 계획의 단일 공통 제어 owner여야 합니다")
        catalog = self.catalog_for(
            self.compilation, self.request_basis, stage=self.selection_policy.stage,
            language=self.selection_policy.language, authorization_check=self.authorization_check)
        if catalog != owners[0].catalog:
            raise ValueError("선택 카탈로그와 실제 업무 구성원 계획의 근거가 다릅니다")
        object.__setattr__(self, "catalog", owners[0].catalog)
        object.__setattr__(self, "_declaration", (
            self.compilation, self.request_basis, self.selection_policy,
            self.authorization_check, self.catalog, owners,
            self.selection_policy.identity_for(owners[0].owner_id).canonical_bytes,
            tuple((function, function.__code__) for function in
                  (type(self).catalog_for, type(self).result_policy))))

    def require_provenance(self) -> None:
        expected = self._declaration
        if (self.compilation is not expected[0] or self.request_basis is not expected[1]
                or self.selection_policy is not expected[2]
                or self.authorization_check is not expected[3] or self.catalog is not expected[4]
                or self.selection_policy.owner_declarations is not expected[5]
                or self.selection_policy.identity_for(expected[5][0].owner_id).canonical_bytes != expected[6]
                or tuple((function, function.__code__) for function in
                         (type(self).catalog_for, type(self).result_policy)) != expected[7]):
            raise ValueError("동결된 그룹 국면 선언이 변경됐습니다")
        self.compilation.require_provenance()
        self.authorization_check.require_provenance()
        self.selection_policy.owner_declarations[0].require_declaration()
        if self.catalog_for(
                self.compilation, self.request_basis, stage=self.selection_policy.stage,
                language=self.selection_policy.language,
                authorization_check=self.authorization_check) != self.catalog:
            raise ValueError("현재 선택 국면의 카탈로그 근거가 변경됐습니다")

    def result_policy(self, group_id: str) -> LeadStagePolicy:
        self.require_provenance()
        tasks = self.selection_policy.task_revisions
        owners = tuple(LeadResultContractOwner(variant, tasks, (self.authorization_check,))
                       for variant in self.compilation.compile(group_id))
        if len(self.catalog.group_ids) > 1:
            owners += (SchemaGroupControlOwner(self.catalog, group_id),)
        return replace(self.selection_policy, owner_declarations=owners)


@dataclass(frozen=True, slots=True)
class PremiseDecisionConstraints:
    """Invocation-only question, selected sources and actual analyst choices."""

    question_id: str
    input_ids: tuple[str, ...] = ()
    reply_alternatives: tuple[tuple[str, tuple[str, ...]], ...] = ()
    unavailable_reply_ids: tuple[str, ...] = ()
    allow_read_sources: bool = False

    def question_schema(self) -> dict[str, object]:
        return premise_question_schema(self.question_id)

    def front_schema(self) -> dict[str, object]:
        assignment = closed_object({
            "agent_id": {"type": "string", "enum": list(SPECIALIST_IDS)},
            "instruction": nonempty_text_schema(),
            "input_ids": string_array(None if self.allow_read_sources else self.input_ids, minimum=1, maximum=64),
            "mode": {"type": "string", "enum": ["premise_analysis"]},
        })
        result = closed_object({
            "kind": {"type": "string", "enum": ["proposal"]},
            "message": nonempty_text_schema(),
            "authoring_scope": {"type": "null"},
            "proposal": closed_object({
                "assignments": {"type": "array", "items": assignment, "minItems": 1, "maxItems": 3},
                "premise_decision": self.question_schema(),
            }),
        })
        result["description"] = "Propose candidate-free analysis of the offered unresolved premise. No drafting, review, artifact or objective authority."
        return result

    def front_rules(self, value: dict[str, object], *, read_ids: tuple[str, ...] = ()) -> tuple[DomainValidationRule, ...]:
        try:
            if set(value) != {"kind", "message", "authoring_scope", "proposal"} or value["kind"] != "proposal" or value["authoring_scope"] is not None or not visible_text(value["message"]):
                raise ValueError("premise proposal is not candidate-free")
            proposal = value["proposal"]
            if type(proposal) is not dict or set(proposal) != {"assignments", "premise_decision"}:
                raise ValueError("premise proposal fields are not closed")
            intent = premise_question_intent_from_dict(proposal["premise_decision"])
            if intent.question_id != self.question_id:
                raise ValueError("premise question was not offered")
            assignments = proposal["assignments"]
            if type(assignments) is not list:
                raise TypeError("premise assignments must be an array")
            from_ids = tuple(item["agent_id"] for item in canonical_assignments(assignments))
            expected = tuple(SPECIALIST_IDS[index] for index, domain in enumerate(ArtifactReviewDomain)
                             if domain in intent.dependent_domains)
            if from_ids != expected:
                raise ValueError("premise analysis omits an affected domain")
            allowed = set(self.input_ids) | (set(read_ids) if self.allow_read_sources else set())
            selected = set()
            for item in assignments:
                if set(item) != {"agent_id", "instruction", "input_ids", "mode"} or item["mode"] != "premise_analysis" or not visible_text(item["instruction"]):
                    raise ValueError("premise assignment is not an analysis")
                if type(item["input_ids"]) is not list or not item["input_ids"] or len(set(item["input_ids"])) != len(item["input_ids"]) or not set(item["input_ids"]) <= allowed:
                    raise ValueError("premise assignment selected unavailable inputs")
                selected.update(item["input_ids"])
            if not set(self.input_ids) <= selected:
                raise ValueError("premise proposal omits required inputs")
        except (KeyError, TypeError, ValueError):
            return (DomainValidationRule.LEAD_OUTCOME_REJECTED,)
        return ()

    def analysis_schema(self) -> dict[str, object]:
        return premise_analysis_schema(self.input_ids)

    def validate_analysis(self, value: object) -> PremiseAnalysis:
        result = premise_analysis_from_dict(value)
        if any(not set(item.basis_input_ids) <= set(self.input_ids) for item in result.alternatives):
            raise ValueError("premise analysis cites an unavailable selected input")
        return result

    def summary_schema(self) -> dict[str, object]:
        return premise_summary_schema(self.reply_alternatives, self.unavailable_reply_ids)

    def validate_summary(self, value: object) -> PremiseDecisionSummary:
        result = premise_summary_from_dict(value)
        ids = tuple(item[0] for item in self.reply_alternatives)
        actual = dict(self.reply_alternatives)
        for option in result.options:
            if tuple(item.reply_event_id for item in option.reply_dispositions) != ids:
                raise ValueError("premise option must retain every ordered analyst reply")
            for item in option.reply_dispositions:
                if not set(item.alternative_keys) <= set(actual[item.reply_event_id]):
                    raise ValueError("premise disposition invents an alternative")
                if item.disposition == "unavailable" and item.reply_event_id not in self.unavailable_reply_ids:
                    raise ValueError("premise disposition hides available analysis")
        return result


@dataclass(frozen=True, slots=True)
class LeadDiscussionConstraints:
    """Host-selected read-only answer/question contract, never task authority."""

    origin_control_event_id: str
    associated_task_id: str | None = None
    associated_task_revision: int | None = None

    def schema(self) -> dict[str, object]:
        schema = closed_object({
            "kind": {"type": "string", "enum": ["answer", "question"]},
            "message": {"type": "string", "pattern": _NUL_FREE_TEXT_PATTERN,
                        "maxLength": COLLABORATION_VISIBLE_TEXT_MAX_UTF8_BYTES},
            "used_evidence_ids": string_array(),
        })
        schema["description"] = (
            "Answer or ask a question in USER-selected discussion; no task "
            "proposal, specialist dispatch, artifact candidate, or write authority."
        )
        return schema

    def prompt_facts(self) -> dict[str, object]:
        return {
            "discussion_control_event_id": self.origin_control_event_id,
            "associated_task": None if self.associated_task_id is None else {
                "task_id": self.associated_task_id,
                "task_revision": self.associated_task_revision,
            },
            "discussion_rule": (
                "Answer or ask a question. Discussion retains the selected task "
                "without changing its goal, scope, completion, or confirmation. "
                "Do not propose execution, dispatch specialists, or claim artifact "
                "facts without actual qualified READ evidence."
            ),
        }
