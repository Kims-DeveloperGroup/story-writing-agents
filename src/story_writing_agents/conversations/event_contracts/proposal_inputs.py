"""Proposal input contracts and their value codecs."""

from __future__ import annotations

from dataclasses import dataclass

from ...agents import (
    LEAD_ORCHESTRATOR,
    ROSTER,
    SPECIALIST_IDS,
)
from ...lead_premise_decisions import PremiseQuestionIntent
from ...specialist_routing import (
    ArtifactReviewDomain,
    AuthoringActivity,
    AuthoringContribution,
    AuthoringContributionRole,
    AuthoringDependencySlot,
    AuthoringIntent,
    AuthoringScope,
    ReviewBasis,
    ReviewStage,
    SpecialistAssignmentMode,
    SpecialistStagePlan,
    assignment_mode_for_specialist,
    authoring_scope_rules,
    legal_specialist_rosters,
    required_authoring_specialists,
    specialists_for_domains,
    validate_specialist_assignment,
    validate_stage_routing,
)
from ...structured_output_validation import (
    validation_call,
    validation_failure,
)
from ..event_value_validation import (
    require_array_value as _array,
    require_enum_value as _enum,
    require_integer_value as _integer,
    require_object_fields as _object,
    require_object_with_extension as _object_with_extension,
    optional_integer_value as _optional_integer,
    optional_string_value as _optional_string,
    require_enum as _require_enum,
    require_unique as _require_unique,
    require_string_value as _string,
    validate_digest as _validate_digest,
    validate_identifier as _validate_identifier,
    validate_non_negative_integer as _validate_non_negative_integer,
    validate_positive_integer as _validate_positive_integer,
    validate_visible_text as _validate_visible_text,
)
from ..event_vocabulary import (
    COLLABORATION_PHYSICAL_CALL_LIMIT,
    COLLABORATION_ROUND_LIMIT,
    CollaborationActorKind,
    CollaborationInputKind,
    CollaborationInputState,
)


@dataclass(frozen=True, slots=True)
class CollaborationActor:
    """One typed visible actor; only lead/specialist actors carry an agent ID."""

    kind: CollaborationActorKind
    agent_id: str | None = None

    def __post_init__(self) -> None:
        _require_enum(self.kind, CollaborationActorKind, "collaboration actor kind")
        if self.kind is CollaborationActorKind.LEAD:
            if self.agent_id != LEAD_ORCHESTRATOR:
                raise ValueError("lead actor must use the canonical lead agent ID")
        elif self.kind is CollaborationActorKind.SPECIALIST:
            if self.agent_id not in SPECIALIST_IDS:
                raise ValueError("specialist actor must use a canonical specialist ID")
        elif self.agent_id is not None:
            raise ValueError("only lead and specialist actors carry an agent ID")


@dataclass(frozen=True, slots=True)
class CollaborationInputRevision:
    """Content-free identity and revision evidence for one closed proposal input."""

    input_id: str
    kind: CollaborationInputKind
    locator: str
    state: CollaborationInputState
    revision_digest: str
    start_line: int | None = None
    end_line: int | None = None

    def __post_init__(self) -> None:
        _validate_identifier(self.input_id, label="collaboration input ID")
        _require_enum(self.kind, CollaborationInputKind, "collaboration input kind")
        _validate_visible_text(
            self.locator,
            label="collaboration input locator",
            maximum=4_096,
        )
        _require_enum(self.state, CollaborationInputState, "collaboration input state")
        _validate_digest(self.revision_digest, label="input revision digest")
        if (self.start_line is None) is not (self.end_line is None):
            raise ValueError("input line range must provide both bounds or neither")
        if self.start_line is not None and self.end_line is not None:
            _validate_positive_integer(self.start_line, label="input start line")
            _validate_positive_integer(self.end_line, label="input end line")
            if self.end_line < self.start_line:
                raise ValueError("input line range must be ordered")


@dataclass(frozen=True, slots=True)
class CollaborationAgentProfile:
    """Definition and content-free execution fingerprints for one participant."""

    agent_id: str
    definition_fingerprint: str
    execution_fingerprint: str

    def __post_init__(self) -> None:
        if self.agent_id not in ROSTER:
            raise ValueError("profile agent ID must be canonical")
        _validate_digest(
            self.definition_fingerprint,
            label="definition fingerprint",
        )
        _validate_digest(
            self.execution_fingerprint,
            label="execution fingerprint",
        )


@dataclass(frozen=True, slots=True)
class CollaborationAssignment:
    """One complete visible and independently budgeted specialist assignment."""

    assignment_id: str
    specialist_id: str
    round: int
    instruction: str
    input_ids: tuple[str, ...]
    max_physical_calls: int
    mode: SpecialistAssignmentMode | None = None
    review_basis: ReviewBasis | None = None

    def __post_init__(self) -> None:
        _validate_identifier(self.assignment_id, label="assignment ID")
        if self.specialist_id not in SPECIALIST_IDS:
            raise ValueError("assignment specialist ID must be canonical")
        _validate_positive_integer(self.round, label="assignment round")
        if self.round > COLLABORATION_ROUND_LIMIT:
            raise ValueError("assignment round exceeds the collaboration limit")
        _validate_visible_text(self.instruction, label="assignment instruction")
        if type(self.input_ids) is not tuple:
            raise TypeError("assignment input IDs must be an exact tuple")
        for input_index, input_id in enumerate(self.input_ids):
            _validate_identifier(input_id, label="assignment input ID")
        _require_unique(self.input_ids, label="assignment input IDs")
        _validate_positive_integer(
            self.max_physical_calls,
            label="assignment maximum physical calls",
        )
        validate_assignment_routing(self.mode, self.input_ids, self.review_basis)


@dataclass(frozen=True, slots=True)
class CollaborationBudget:
    """Whole-lineage and whole-next-step physical provider-call reservation."""

    round_limit: int
    physical_call_limit: int
    rounds_spent: int
    rounds_added: int
    physical_calls_spent: int
    max_additional_physical_calls: int
    synthesis_max_physical_calls: int

    def __post_init__(self) -> None:
        if self.round_limit != COLLABORATION_ROUND_LIMIT:
            raise ValueError("collaboration round limit must be exactly two")
        if self.physical_call_limit != COLLABORATION_PHYSICAL_CALL_LIMIT:
            raise ValueError("collaboration physical-call limit must be exactly twelve")
        _validate_non_negative_integer(self.rounds_spent, label="rounds spent")
        if self.rounds_added != 1:
            raise ValueError("collaboration rounds added must be exactly one")
        _validate_non_negative_integer(
            self.physical_calls_spent,
            label="physical calls spent",
        )
        _validate_non_negative_integer(
            self.max_additional_physical_calls,
            label="maximum additional physical calls",
        )
        _validate_non_negative_integer(
            self.synthesis_max_physical_calls,
            label="synthesis maximum physical calls",
        )
        if self.rounds_spent + self.rounds_added > self.round_limit:
            raise ValueError("collaboration proposal would exceed the round limit")
        if (
            self.physical_calls_spent + self.max_additional_physical_calls
            > self.physical_call_limit
        ):
            raise ValueError("collaboration proposal would exceed the call limit")


def _decode_contribution(value: object) -> AuthoringContribution:
    item = _object(value, {"domain", "role"}, label="authoring contribution")
    return AuthoringContribution(_enum(ArtifactReviewDomain, item["domain"], label="contribution domain"),
                                _enum(AuthoringContributionRole, item["role"], label="contribution role"))


def _decode_dependency_slot(value: object) -> AuthoringDependencySlot:
    item = _object(value, {"slot_id", "dependent_domains"}, label="dependency slot")
    return AuthoringDependencySlot(_string(item["slot_id"], label="slot ID"),
                                  tuple(_enum(ArtifactReviewDomain, domain, label="dependent domain")
                                        for domain in _array(item["dependent_domains"], label="dependent domains")))


def authoring_scope_to_dict(scope: AuthoringScope) -> dict[str, object]:
    """Encode the exact versioned scope shape owned by collaboration codecs."""

    if type(scope) is not AuthoringScope:
        raise TypeError("authoring scope must be an AuthoringScope")
    result = {
        **({"intent": scope.intent.value} if scope.schema_version >= 2 else {}),
        "activity": scope.activity.value,
        "domains": [domain.value for domain in scope.domains],
        "interpretation": scope.interpretation,
        "schema_version": scope.schema_version,
        "target_paths": list(scope.target_paths),
        "task_id": scope.task_id,
        "task_revision": scope.task_revision,
    }

    if scope.schema_version == 3:
        result['contributions'] = [{'domain': item.domain.value, 'role': item.role.value} for item in scope.contributions]
        result['dependency_slots'] = [{'slot_id': item.slot_id, 'dependent_domains': [domain.value for domain in item.dependent_domains]} for item in scope.dependency_slots]
    return result


def authoring_scope_from_dict(value: object) -> AuthoringScope:
    """Decode a complete strict scope; absent legacy fields are handled outside."""
    fields = {"activity", "domains", "interpretation", "schema_version", "target_paths", "task_id", "task_revision"}
    if type(value) is dict and value.get("schema_version") in {2, 3}:
        fields.add("intent")
    if type(value) is dict and value.get("schema_version") == 3:
        fields.update({"contributions", "dependency_slots"})
    item = _object(value, fields, label="authoring scope")
    return AuthoringScope(
        contributions=tuple(_decode_contribution(entry) for entry in _array(item['contributions'], label='contributions')) if item['schema_version'] == 3 else (),
        dependency_slots=tuple(_decode_dependency_slot(entry) for entry in _array(item['dependency_slots'], label='dependency slots')) if item['schema_version'] == 3 else (),
        intent=validation_call('$.intent', _enum, AuthoringIntent, item.get("intent", "authoring"), label="authoring intent"),
        activity=validation_call(
            '$.activity',
            _enum,
            AuthoringActivity,
            item['activity'],
            label='authoring activity',
        ),
        target_paths=tuple(
            validation_call(f"$.target_paths[{index}]", _string, path, label="authoring target path")
            for index, path in enumerate(validation_call(
                '$.target_paths',
                _array,
                item['target_paths'],
                label='authoring target paths',
            ))
        ),
        domains=tuple(
            validation_call(
                f'$.domains[{index}]',
                _enum,
                ArtifactReviewDomain,
                domain,
                label='authoring domain',
            )
            for index, domain in enumerate(validation_call('$.domains', _array, item['domains'], label='authoring domains'))
        ),
        interpretation=validation_call(
            '$.interpretation',
            _string,
            item['interpretation'],
            label='authoring interpretation',
        ),
        task_id=validation_call("$.task_id", _string, item["task_id"], label="authoring task ID"),
        task_revision=validation_call(
            '$.task_revision',
            _integer,
            item['task_revision'],
            label='authoring task revision',
        ),
        schema_version=validation_call(
            '$.schema_version',
            _integer,
            item['schema_version'],
            label='authoring schema version',
        ),
    )


def review_basis_to_dict(basis: ReviewBasis) -> dict[str, object]:
    """Encode concrete review-input correlation without granting read authority."""

    if type(basis) is not ReviewBasis:
        raise TypeError("review basis must be a ReviewBasis")
    return {
        "candidate_input_ids": list(basis.candidate_input_ids),
        "criteria": basis.criteria,
        "schema_version": basis.schema_version,
    }


def review_basis_from_dict(value: object) -> ReviewBasis:
    """Decode a complete strict, versioned candidate review basis."""

    item = _object(
        value,
        {"candidate_input_ids", "criteria", "schema_version"},
        label="review basis",
    )
    return ReviewBasis(
        candidate_input_ids=tuple(
            _string(input_id, label="review candidate input ID")
            for input_id in _array(
                item["candidate_input_ids"], label="review candidate input IDs"
            )
        ),
        criteria=_string(item["criteria"], label="review criteria"),
        schema_version=_integer(item["schema_version"], label="review schema version"),
    )


def decode_optional_authoring_scope(item: dict[str, object]) -> AuthoringScope | None:
    if "authoring_scope" not in item:
        return None
    return authoring_scope_from_dict(item["authoring_scope"])


def decode_assignment_mode(
    item: dict[str, object],
) -> SpecialistAssignmentMode | None:
    if "mode" not in item:
        return None
    return _enum(SpecialistAssignmentMode, item["mode"], label="assignment mode")


def decode_assignment_review_basis(item: dict[str, object]) -> ReviewBasis | None:
    value = item.get("review_basis")
    return None if value is None else review_basis_from_dict(value)


def actor_value(actor: CollaborationActor) -> dict[str, object]:
    return {"agent_id": actor.agent_id, "kind": actor.kind.value}


def input_value(item: CollaborationInputRevision) -> dict[str, object]:
    return {
        "end_line": item.end_line,
        "input_id": item.input_id,
        "kind": item.kind.value,
        "locator": item.locator,
        "revision_digest": item.revision_digest,
        "start_line": item.start_line,
        "state": item.state.value,
    }


def profile_value(profile: CollaborationAgentProfile) -> dict[str, object]:
    return {
        "agent_id": profile.agent_id,
        "definition_fingerprint": profile.definition_fingerprint,
        "execution_fingerprint": profile.execution_fingerprint,
    }


def assignment_value(assignment: CollaborationAssignment) -> dict[str, object]:
    return {
        "assignment_id": assignment.assignment_id,
        "input_ids": list(assignment.input_ids),
        "instruction": assignment.instruction,
        "max_physical_calls": assignment.max_physical_calls,
        "round": assignment.round,
        "specialist_id": assignment.specialist_id,
        **assignment_routing_value(assignment.mode, assignment.review_basis),
    }


def assignment_routing_value(
    mode: SpecialistAssignmentMode | None,
    review_basis: ReviewBasis | None,
) -> dict[str, object]:
    if mode is None:
        return {}
    return {
        "mode": mode.value,
        "review_basis": (
            None if review_basis is None else review_basis_to_dict(review_basis)
        ),
    }


def authoring_scope_fields(scope: AuthoringScope | None) -> dict[str, object]:
    return {} if scope is None else {"authoring_scope": authoring_scope_to_dict(scope)}


def budget_value(budget: CollaborationBudget) -> dict[str, object]:
    return {
        "max_additional_physical_calls": budget.max_additional_physical_calls,
        "physical_call_limit": budget.physical_call_limit,
        "physical_calls_spent": budget.physical_calls_spent,
        "round_limit": budget.round_limit,
        "rounds_added": budget.rounds_added,
        "rounds_spent": budget.rounds_spent,
        "synthesis_max_physical_calls": budget.synthesis_max_physical_calls,
    }


def decode_actor(value: object) -> CollaborationActor:
    item = _object(value, {"agent_id", "kind"}, label="collaboration actor")
    return CollaborationActor(
        kind=_enum(CollaborationActorKind, item["kind"], label="actor kind"),
        agent_id=_optional_string(item["agent_id"], label="actor agent ID"),
    )


def decode_input(value: object) -> CollaborationInputRevision:
    item = _object(
        value,
        {
            "end_line",
            "input_id",
            "kind",
            "locator",
            "revision_digest",
            "start_line",
            "state",
        },
        label="collaboration input",
    )
    return CollaborationInputRevision(
        input_id=_string(item["input_id"], label="input ID"),
        kind=_enum(CollaborationInputKind, item["kind"], label="input kind"),
        locator=_string(item["locator"], label="input locator"),
        state=_enum(CollaborationInputState, item["state"], label="input state"),
        revision_digest=_string(item["revision_digest"], label="input digest"),
        start_line=_optional_integer(item["start_line"], label="input start line"),
        end_line=_optional_integer(item["end_line"], label="input end line"),
    )


def decode_profile(value: object) -> CollaborationAgentProfile:
    item = _object(
        value,
        {"agent_id", "definition_fingerprint", "execution_fingerprint"},
        label="collaboration profile",
    )
    return CollaborationAgentProfile(
        agent_id=_string(item["agent_id"], label="profile agent ID"),
        definition_fingerprint=_string(
            item["definition_fingerprint"],
            label="definition fingerprint",
        ),
        execution_fingerprint=_string(
            item["execution_fingerprint"],
            label="execution fingerprint",
        ),
    )


def decode_assignment(value: object) -> CollaborationAssignment:
    item = _object_with_extension(
        value,
        {
            "assignment_id",
            "input_ids",
            "instruction",
            "max_physical_calls",
            "round",
            "specialist_id",
        },
        {"mode", "review_basis"},
        label="collaboration assignment",
    )
    return CollaborationAssignment(
        assignment_id=_string(item["assignment_id"], label="assignment ID"),
        specialist_id=_string(item["specialist_id"], label="specialist ID"),
        round=_integer(item["round"], label="assignment round"),
        instruction=_string(item["instruction"], label="assignment instruction"),
        input_ids=tuple(
            _string(input_id, label="assignment input ID")
            for input_id in _array(item["input_ids"], label="assignment input IDs")
        ),
        max_physical_calls=_integer(
            item["max_physical_calls"],
            label="assignment maximum physical calls",
        ),
        mode=decode_assignment_mode(item),
        review_basis=decode_assignment_review_basis(item),
    )


def decode_budget(value: object) -> CollaborationBudget:
    item = _object(
        value,
        {
            "max_additional_physical_calls",
            "physical_call_limit",
            "physical_calls_spent",
            "round_limit",
            "rounds_added",
            "rounds_spent",
            "synthesis_max_physical_calls",
        },
        label="collaboration budget",
    )
    return CollaborationBudget(
        round_limit=_integer(item["round_limit"], label="round limit"),
        physical_call_limit=_integer(
            item["physical_call_limit"],
            label="physical-call limit",
        ),
        rounds_spent=_integer(item["rounds_spent"], label="rounds spent"),
        rounds_added=_integer(item["rounds_added"], label="rounds added"),
        physical_calls_spent=_integer(
            item["physical_calls_spent"],
            label="physical calls spent",
        ),
        max_additional_physical_calls=_integer(
            item["max_additional_physical_calls"],
            label="maximum additional calls",
        ),
        synthesis_max_physical_calls=_integer(
            item["synthesis_max_physical_calls"],
            label="synthesis maximum calls",
        ),
    )


def validate_assignment_routing(
    mode: SpecialistAssignmentMode | None,
    input_ids: tuple[str, ...],
    review_basis: ReviewBasis | None,
) -> None:
    if mode is None:
        if review_basis is not None:
            raise validation_failure(
                ValueError('legacy assignment cannot carry a review basis'),
                '$',
                'legacy assignment cannot carry a review basis',
            )
        return
    validate_specialist_assignment(mode, input_ids, review_basis)


def validate_scoped_assignments(
    scope: AuthoringScope | None,
    specialist_ids: tuple[str, ...],
    modes: tuple[SpecialistAssignmentMode | None, ...],
    premise_decision: PremiseQuestionIntent | None = None,
    *,
    stage_plan: SpecialistStagePlan | None = None,
    _scope_rules=authoring_scope_rules,
    _rosters=legal_specialist_rosters,
) -> None:
    if premise_decision is not None:
        if type(premise_decision) is not PremiseQuestionIntent or scope is not None:
            raise ValueError("premise analysis is separate from authoring scope")
        if specialist_ids != specialists_for_domains(premise_decision.dependent_domains) or any(mode is not SpecialistAssignmentMode.PREMISE_ANALYSIS for mode in modes):
            raise ValueError("premise analyst roster must match the complete question domains")
        return
    if scope is None:
        if any(mode is not None for mode in modes):
            raise validation_failure(
                ValueError('explicit assignment modes require an authoring scope'),
                '$',
                'explicit assignment modes require an authoring scope',
            )
        return
    validate_stage_routing(scope, specialist_ids, stage_plan)
    if stage_plan is not None:
        if stage_plan.stage not in {ReviewStage.SPECIALIST_WORK, ReviewStage.FINAL_CANDIDATE_REVIEW}:
            raise ValueError("a nonexecution review stage cannot dispatch assignments")
    if scope.activity is AuthoringActivity.WORKFLOW:
        raise validation_failure(
            ValueError('workflow scope cannot dispatch story specialists'),
            '$',
            'workflow scope cannot dispatch story specialists',
        )
    if any(type(mode) is not SpecialistAssignmentMode for mode in modes):
        raise validation_failure(
            TypeError('scoped assignments require explicit draft or review modes'),
            '$',
            'scoped assignments require explicit draft or review modes',
        )
    expected_mode = (
        SpecialistAssignmentMode.REVIEW
        if scope.activity is AuthoringActivity.REVIEW
        else SpecialistAssignmentMode.DRAFT
    )
    if scope.intent is AuthoringIntent.REVIEW_ONLY and any(mode is not SpecialistAssignmentMode.REVIEW for mode in modes):
        raise validation_failure(ValueError('review-only assignments cannot draft'), '$', 'review-only assignments must review the supplied material')
    if scope.schema_version < 3 and SpecialistAssignmentMode.CONSTRAINT_ASSESSMENT in modes:
        raise ValueError('legacy assignments cannot acquire constraint duties')
    required = required_authoring_specialists(scope)
    if scope.schema_version == 3:
        declared = _scope_rules(
            scope.activity, scope.intent, scope.domains, scope.contributions,
            target_paths=scope.target_paths, dependency_slots=scope.dependency_slots,
            schema_version=scope.schema_version,
        )
        if stage_plan is None and specialist_ids not in _rosters(declared):
            raise ValueError('scope3 assignments must match the complete declared specialist union')
    if any(
        mode is not (assignment_mode_for_specialist(scope, specialist_id) if scope.schema_version == 3 else expected_mode)
        for specialist_id, mode in zip(specialist_ids, modes, strict=True)
        if specialist_id in required
    ):
        raise validation_failure(
            ValueError('required specialists must perform the scoped activity'),
            '$',
            'required specialists must perform the scoped activity',
        )
