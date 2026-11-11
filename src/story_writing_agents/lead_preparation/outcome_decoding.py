"""Strict decoding and validation details for Lead outcomes."""

from __future__ import annotations

import json
from typing import cast

from ..agents import SPECIALIST_IDS
from ..artifact_proposals import (
    ArtifactCreateIntent,
    ArtifactDeleteIntent,
    ArtifactPatchIntent,
    ArtifactProposalError,
    ArtifactProposalIntent,
    ArtifactRenameIntent,
    ArtifactReplaceIntent,
)
from ..authoring_admission import assessment_from_dict
from ..conversations import (
    ArtifactObjectiveNextStep,
    LeadAssignmentIntent,
    LeadEvidenceStatus,
    LeadObjectiveAction,
    LeadObjectiveIntent,
    LeadOutcomeKind,
    LeadOutcomeScope,
    LeadProposalIntent,
    LeadProposalRelation,
    LeadProviderOutcome,
    LeadRecommendationActionKind,
    LeadRecommendedAction,
    authoring_scope_from_dict,
)
from ..conversations.events import LeadSharedPremiseIntent
from ..core_document_readiness import (
    AuthoringTarget,
    assessment_from_dict as core_assessment_from_dict,
)
from ..errors import BackendProtocolError
from ..lead_premise_decisions import premise_question_intent_from_dict
from ..lead_provider_contracts import (
    LeadContractError,
    canonical_assignments,
    canonical_scope,
    visible_text,
)
from ..review_contracts import preliminary_review_from_dict
from ..specialist_routing import (
    AuthoringActivity,
    AuthoringIntent,
    SpecialistAssignmentMode,
    stage_plan_from_dict,
)
from ..structured_output_validation import (
    DomainValidationRule,
    carry_validation,
    remap_validation,
    validation_call,
    validation_failure,
)
from . import reply_contract_values


class _DuplicateJsonKey(ValueError):
    pass


def strict_json_object(
    answer: str,
    keys: set[str] | tuple[frozenset[str], ...],
) -> dict[str, object]:
    if type(answer) is not str:
        raise validation_failure(TypeError(), '$', 'expected strict JSON text')

    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise validation_failure(_DuplicateJsonKey(), '$', 'duplicate object key')
            result[key] = value
        return result

    try:
        value = json.loads(
            answer,
            object_pairs_hook=unique,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except (_DuplicateJsonKey, json.JSONDecodeError, ValueError) as exc:
        raise validation_failure(ValueError(), '$', 'malformed JSON or duplicate object key') from exc
    allowed = (frozenset(keys),) if type(keys) is set else keys
    if type(value) is not dict or frozenset(value) not in allowed:
        raise validation_failure(
            ValueError(),
            '$',
            'expected an object containing exactly one of these property sets: ' + ' or '.join(('[' + ', '.join(sorted(fields)) + ']' for fields in sorted(allowed, key=lambda fields: tuple(sorted(fields))))),
        )
    return value


def declares_artifact_intent(
    answer: object, *, objective_prepare_only: bool = False,
) -> bool:
    """Inspect explicit intent fields for correction/failure classification."""

    if type(answer) is not str:
        return False

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
    except (_DuplicateJsonKey, json.JSONDecodeError, TypeError, ValueError):
        return False
    if type(value) is not dict:
        return False
    artifact = value.get("artifact_intent")
    proposal = value.get("proposal")
    objective = value.get("objective_intent")
    if type(artifact) is dict:
        objective = artifact.get("objective_intent", objective)
    elif type(proposal) is dict:
        objective = proposal.get("objective_intent", objective)
    prepare = type(objective) is dict and objective.get("next_step") == "prepare"
    return prepare if objective_prepare_only else prepare or artifact is not None


def lead_outcome_validation_details(
    answer: str,
) -> tuple[DomainValidationRule, ...]:
    """Classify known lead rules without carrying provider text."""

    try:
        value = json.loads(answer)
    except (json.JSONDecodeError, TypeError, ValueError):
        return (DomainValidationRule.MALFORMED_RESULT,)
    if type(value) is not dict:
        return (DomainValidationRule.RESULT_OBJECT_REQUIRED,)
    issues: list[DomainValidationRule] = []
    kind = value.get("kind", LeadOutcomeKind.ANSWER.value)
    has_recommendation = "recommended_action" in value
    if kind == LeadOutcomeKind.RECOMMENDATION.value and not has_recommendation:
        issues.append(DomainValidationRule.RECOMMENDATION_REQUIRED)
    if kind != LeadOutcomeKind.RECOMMENDATION.value and has_recommendation:
        issues.append(DomainValidationRule.RECOMMENDATION_ONLY)
    if has_recommendation and "objective_intent" in value:
        issues.append(DomainValidationRule.OBJECTIVE_RECOMMENDATION_CONFLICT)
    if kind != LeadOutcomeKind.PROPOSAL.value and (
        "proposal" in value or "artifact_intent" in value
    ):
        issues.append(DomainValidationRule.PROPOSAL_KIND_REQUIRED)
    return tuple(issues or (DomainValidationRule.LEAD_OUTCOME_REJECTED,))


def decode_lead_outcome(
    answer: str,
    *,
    expected_relation: LeadProposalRelation = LeadProposalRelation.NEW_STEP,
) -> LeadProviderOutcome:
    """Strictly decode the neutral JSON-object result into closed lead intent."""

    if type(answer) is not str:
        raise validation_failure(
            BackendProtocolError('lead answer must be strict JSON text'),
            '$',
            'expected strict JSON text',
        )

    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise validation_failure(_DuplicateJsonKey(), '$', 'duplicate object key')
            result[key] = value
        return result

    try:
        value = json.loads(
            answer,
            object_pairs_hook=unique,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
        planning_value = value.pop("planning_target", None) if type(value) is dict else None
        planning_target = None if planning_value is None else AuthoringTarget(planning_value)
        if type(value) is dict and "preliminary_review" in value:
            if set(value) - {"kind"} != {"message", "authoring_scope", "preliminary_review"}:
                raise ValueError("preliminary answer has no action or proposal fields")
            if value.get("kind", "answer") != "answer":
                raise ValueError("preliminary record requires an answer")
            raw_scope = value["authoring_scope"]
            task_id, revision = raw_scope["task"].rsplit("@", 1)
            scope = authoring_scope_from_dict({
                "schema_version": 3 if "contributions" in raw_scope else 2 if "intent" in raw_scope else 1,
                **{key: item for key, item in raw_scope.items() if key != "task"},
                "task_id": task_id, "task_revision": int(revision), "interpretation": raw_scope.get("interpretation", value["message"])})
            record = preliminary_review_from_dict(value["preliminary_review"])
            status = (LeadEvidenceStatus.CONFLICTING if record.conflicts else
                      LeadEvidenceStatus.INCOMPLETE if record.excluded_input_ids or record.missing_information
                      else LeadEvidenceStatus.VERIFIED)
            return LeadProviderOutcome(LeadOutcomeKind.ANSWER, value["message"],
                LeadOutcomeScope.STORY_SPECIFIC, status, (), planning_target=planning_target, authoring_scope=scope,
                preliminary_review=record)
        assessment_present = type(value) is dict and "authoring_assessment" in value
        if type(value) is dict and "core_document_assessment" in value:
            if (set(value) != {"kind", "message", "authoring_scope", "core_document_assessment"}
                    or value["kind"] != "answer" or value["authoring_scope"] is not None
                    or assessment_present or not visible_text(value["message"])):
                raise ValueError("readiness assessment is a distinct authority-free answer")
            return LeadProviderOutcome(LeadOutcomeKind.ANSWER, value["message"],
                LeadOutcomeScope.STORY_SPECIFIC, LeadEvidenceStatus.VERIFIED, (),
                planning_target=planning_target, core_document_assessment=core_assessment_from_dict(value["core_document_assessment"]))
        raw_assessment = value.get("authoring_assessment") if type(value) is dict else None
        assessment = None if raw_assessment is None else assessment_from_dict(raw_assessment)
        if type(value) is dict:
            value = {key: item for key, item in value.items() if key != "authoring_assessment"}
        allowed = {
            frozenset({"authoring_scope", "message"}),
            frozenset({"authoring_scope", "kind", "message"}),
            frozenset({"authoring_scope", "kind", "message", "objective_intent"}),
            frozenset({"authoring_scope", "kind", "message", "recommended_action"}),
            frozenset({"authoring_scope", "kind", "message", "proposal"}),
            frozenset({"authoring_scope", "kind", "message", "artifact_intent"}),
        }
        if type(value) is not dict or frozenset(value) not in allowed:
            raise validation_failure(
                ValueError(),
                '$',
                'expected authoring_scope and message, with only the fields permitted by kind',
            )
        if value.get("authoring_scope") is None and type(value.get("proposal")) is dict and "premise_decision" in value["proposal"]:
            if assessment_present:
                raise ValueError("premise analysis cannot acquire authoring admission")
            raw = value["proposal"]
            if set(value) != {"kind", "message", "authoring_scope", "proposal"} or value["kind"] != "proposal" or set(raw) != {"assignments", "premise_decision"}:
                raise ValueError("premise analysis cannot carry authoring or objective fields")
            question = premise_question_intent_from_dict(raw["premise_decision"])
            assignments = tuple(LeadAssignmentIntent(
                item["agent_id"], item["instruction"], tuple(item["input_ids"]),
                SpecialistAssignmentMode(item["mode"]),
            ) for item in canonical_assignments(raw["assignments"]))
            intent = LeadProposalIntent(expected_relation, value["message"], assignments,
                premise_decision=question)
            return LeadProviderOutcome(LeadOutcomeKind.PROPOSAL, value["message"],
                LeadOutcomeScope.PREMISE_ANALYSIS, LeadEvidenceStatus.NOT_APPLICABLE, (), intent, planning_target=planning_target)
        raw_scope = canonical_scope(value.get("authoring_scope"))
        if type(raw_scope) is not dict:
            raise validation_failure(ValueError(), '$.authoring_scope', 'expected an object')
        kind = validation_call('$.kind', LeadOutcomeKind, value.get('kind', LeadOutcomeKind.ANSWER.value))
        if (
            frozenset(value) == frozenset({"authoring_scope", "kind", "message"})
            and kind not in {LeadOutcomeKind.QUESTION, LeadOutcomeKind.CRITIQUE}
        ):
            raise validation_failure(
                ValueError(),
                '$.kind',
                (
                    'an explicit kind without a proposal, objective_intent, or recommended_action'
                    ' must be question or critique'
                ),
            )
        message = value["message"]
        if not visible_text(message):
            raise LeadContractError((DomainValidationRule.MESSAGE_REQUIRED,))
        task_id, separator, raw_revision = cast(str, raw_scope["task"]).rpartition("@")
        if not separator:
            raise validation_failure(
                ValueError(),
                '$.authoring_scope.task',
                'expected a task identifier followed by @ and a decimal revision',
            )
        authoring_scope = validation_call(
            '$.authoring_scope',
            authoring_scope_from_dict,
            {
            'schema_version': 3 if 'contributions' in raw_scope else 2 if 'intent' in raw_scope else 1,
                **{key: item for key, item in raw_scope.items() if key != 'task'},
                'task_id': task_id,
                'task_revision': validation_call('$.authoring_scope.revision', int, raw_revision),
                'interpretation': raw_scope.get('interpretation', message),
            },
        )
        if assessment_present and (
            authoring_scope.intent is AuthoringIntent.REVIEW_ONLY
            or not {"proposal", "artifact_intent", "recommended_action"}.intersection(value)
        ):
            raise validation_failure(ValueError(), '$.authoring_assessment',
                'authoring assessment is not a field of this non-authoring result variant')
        scope = (
            LeadOutcomeScope.WORKFLOW_META
            if authoring_scope.activity is AuthoringActivity.WORKFLOW
            else LeadOutcomeScope.STORY_SPECIFIC
        )
        evidence_status = (
            LeadEvidenceStatus.NOT_APPLICABLE
            if scope is LeadOutcomeScope.WORKFLOW_META
            else LeadEvidenceStatus.VERIFIED
        )
        proposal_value = value.get("proposal")
        artifact_value = value.get("artifact_intent")
        nested_intent = (
            proposal_value.get("objective_intent")
            if type(proposal_value) is dict
            else artifact_value.get("objective_intent")
            if type(artifact_value) is dict
            else None
        )
        objective_path = (
            "$.objective_intent" if "objective_intent" in value
            else "$.proposal.objective_intent" if type(proposal_value) is dict
            else "$.artifact_intent.objective_intent"
        )
        objective_intent = validation_call(
            objective_path,
            _decode_objective_intent,
            value.get('objective_intent', nested_intent),
            message=cast(str, message),
        )
        recommended_action = validation_call(
            '$.recommended_action',
            decode_recommended_action,
            value.get('recommended_action'),
            message=cast(str, message),
        )
        if (kind is LeadOutcomeKind.RECOMMENDATION) is not (recommended_action is not None):
            raise validation_failure(
                ValueError('fresh recommendations require exactly one typed action'),
                '$.recommended_action',
                'required exactly when kind is recommendation',
            )
        if recommended_action is not None and objective_intent is not None:
            raise validation_failure(
                ValueError('recommendation cannot transition an objective before confirmation'),
                '$.objective_intent',
                'cannot accompany recommended_action before its confirmation',
            )
        if kind is not LeadOutcomeKind.PROPOSAL:
            if proposal_value is not None or artifact_value is not None:
                raise validation_failure(
                    ValueError(),
                    '$.kind',
                    'must be proposal when proposal or artifact_intent is present',
                )
            return LeadProviderOutcome(
                kind,
                message,
                scope,
                evidence_status,
                (),
                None,
                None,
                objective_intent,
                recommended_action,
                planning_target=planning_target, authoring_scope=authoring_scope, authoring_assessment=assessment,
            )
        if (proposal_value is None) is (artifact_value is None):
            raise validation_failure(
                ValueError(),
                '$.proposal',
                'kind proposal requires exactly one of proposal and artifact_intent',
            )
        if artifact_value is not None:
            artifact_intent = validation_call(
                '$.artifact_intent',
                _decode_artifact_intent,
                {key: item for key, item in artifact_value.items() if key != 'objective_intent'},
            )
            return LeadProviderOutcome(
                kind,
                message,
                scope,
                evidence_status,
                (),
                None,
                artifact_intent,
                objective_intent,
                planning_target=planning_target, authoring_scope=authoring_scope, authoring_assessment=assessment,
            )
        if type(proposal_value) is not dict or (frozenset(proposal_value) - {"stage_plan"}) not in {
            frozenset({"assignments", *({"shared_premises"} if authoring_scope.schema_version == 3 else set())}),
            frozenset({"assignments", "objective_intent", *({"shared_premises"} if authoring_scope.schema_version == 3 else set())}),
        }:
            raise validation_failure(
                ValueError(),
                '$.proposal',
                'expected an object with assignments and optional objective_intent',
            )
        raw_assignments = proposal_value["assignments"]
        if type(raw_assignments) is not list or not raw_assignments:
            raise validation_failure(ValueError(), '$.proposal.assignments', 'expected a nonempty array')
        assignments: list[LeadAssignmentIntent] = []
        for item in validation_call('$.proposal.assignments', canonical_assignments, raw_assignments):
            assignment_index = next(
                index for index, raw in enumerate(raw_assignments) if raw is item
            )
            expected_fields = {"agent_id", "input_ids", "instruction", "mode"}
            if (type(item) is dict
                    and item.get("mode") == SpecialistAssignmentMode.REVIEW.value):
                expected_fields.add("review_basis")
            if type(item) is not dict or set(item) != expected_fields:
                raise validation_failure(
                    ValueError(),
                    f'$.proposal.assignments[{assignment_index}]',
                    'each assignment must contain exactly agent_id, input_ids, instruction, and mode',
                )
            if type(item["input_ids"]) is not list:
                raise validation_failure(
                    ValueError(),
                    f'$.proposal.assignments[{assignment_index}].input_ids',
                    'expected an array',
                )
            assignments.append(
                validation_call(
                    f'$.proposal.assignments[{assignment_index}]',
                    LeadAssignmentIntent,
                    agent_id=item['agent_id'],
                    instruction=item['instruction'],
                    input_ids=tuple(item['input_ids']),
                    mode=validation_call(
                             f'$.proposal.assignments[{assignment_index}].mode',
                             SpecialistAssignmentMode,
                             item['mode'],
                         ),
                    review_basis=(
                        validation_call(
                            f'$.proposal.assignments[{assignment_index}].review_basis',
                            reply_contract_values.provider_review_basis,
                            item["review_basis"],
                        )
                        if item["mode"] == SpecialistAssignmentMode.REVIEW.value else None
                    ),
                )
            )
        proposal = validation_call(
            '$.proposal',
            LeadProposalIntent,
            relation=expected_relation,
            purpose=cast(str, message),
        assignments=tuple(assignments),
        authoring_scope=authoring_scope,
            shared_premises=_decode_shared_premise_intents(proposal_value.get("shared_premises", [])),
            stage_plan=(stage_plan_from_dict(proposal_value["stage_plan"]) if "stage_plan" in proposal_value else None),
        )
        return LeadProviderOutcome(
            kind,
            message,
            scope,
            evidence_status,
            (),
            proposal,
            None,
            objective_intent,
            planning_target=planning_target, authoring_scope=authoring_scope, authoring_assessment=assessment,
        )
    except (
        ArtifactProposalError,
        _DuplicateJsonKey,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        UnicodeError,
        ValueError,
    ) as exc:
        raise carry_validation(
            reply_contract_values.LeadOutcomeValidationError('lead returned a noncanonical evidence outcome'),
            remap_validation(
                exc,
                {
                    '$.authoring_scope.task_id': '$.authoring_scope.task',
                    '$.authoring_scope.task_revision': '$.authoring_scope.task',
                    '$.authoring_scope.interpretation': '$.message',
                    '$.proposal.purpose': '$.message',
                    '$.recommended_action.purpose': '$.recommended_action.instruction',
                    '$.objective_intent.request': '$.message',
                    '$.proposal.objective_intent.request': '$.message',
                    '$.artifact_intent.objective_intent.request': '$.message',
                    '$.recommended_action.expected_result': '$.message',
                },
            ),
        ) from exc


def decode_recommended_action(
    value: object,
    *,
    message: str,
) -> LeadRecommendedAction | None:
    if value is None:
        return None
    if type(value) is not dict or type(value.get("kind")) is not str:
        raise validation_failure(
            ValueError('recommended action must have exactly its closed fields'),
            '$',
            'expected an object with a textual kind',
        )
    kind = validation_call('$.kind', LeadRecommendationActionKind, value['kind'])
    specific = (
        {"target_paths"}
        if kind is LeadRecommendationActionKind.PREPARE_ARTIFACT
        else {"specialist_ids"}
    )
    fields = {"kind", "instruction", "source_ids", *specific}
    if frozenset(value) not in {frozenset(fields), frozenset((*fields, "objective_id"))}:
        raise validation_failure(
            ValueError('recommended action must have exactly its closed fields'),
            '$',
            'expected exactly the properties required by kind and optional objective_id',
        )
    for key in ("source_ids", *specific):
        if type(value[key]) is not list:
            raise validation_failure(
                ValueError('recommended action collections must be arrays'),
                f'$.{key}',
                'expected an array',
            )
    raw_specialists = value.get("specialist_ids", [])
    if any(type(item) is not str or item not in SPECIALIST_IDS for item in raw_specialists):
        raise validation_failure(
            ValueError("recommended specialists must be offered IDs"),
            '$.specialist_ids',
            'expected specialist identifiers from: ' + ', '.join(SPECIALIST_IDS),
        )
    if len(set(raw_specialists)) != len(raw_specialists):
        raise validation_failure(
            LeadContractError((DomainValidationRule.RECOMMENDATION_SPECIALISTS,)),
            '$.specialist_ids',
            'expected unique specialist identifiers before canonical ordering',
        )
    return LeadRecommendedAction(
        kind=kind,
        purpose=cast(str, value["instruction"]),
        instruction=cast(str, value["instruction"]),
        expected_result=message,
        source_ids=tuple(value["source_ids"]),
        target_paths=tuple(value.get("target_paths", ())),
        specialist_ids=tuple(item for item in SPECIALIST_IDS if item in raw_specialists),
        objective_id=cast(str | None, value.get("objective_id")),
    )


def _decode_objective_intent(
    value: object,
    *,
    message: str,
) -> LeadObjectiveIntent | None:
    if value is None:
        return None
    if type(value) is not dict:
        raise validation_failure(
            ValueError('objective intent must be an exact object'),
            '$',
            'expected an object',
        )
    action = validation_call('$.action', LeadObjectiveAction, value['action'])
    fields = {
        LeadObjectiveAction.START: {"action", "target_paths", "next_step"},
        LeadObjectiveAction.CONTINUE: {"action", "objective_id", "next_step"},
        LeadObjectiveAction.CANCEL: {"action", "objective_id"},
    }[action]
    if set(value) != fields:
        raise validation_failure(
            ValueError('objective intent fields are noncanonical'),
            '$',
            (
                'fields must match action: start requires action, target_paths, next_step; '
                'continue requires action, objective_id, next_step; cancel requires action, '
                'objective_id'
            ),
        )
    if action is LeadObjectiveAction.CANCEL:
        return LeadObjectiveIntent(action=action, objective_id=value["objective_id"])
    if action is LeadObjectiveAction.START and type(value["target_paths"]) is not list:
        raise validation_failure(
            ValueError('objective target paths must be a list'),
            '$.target_paths',
            'expected an array',
        )
    return LeadObjectiveIntent(
        action=action, objective_id=value.get("objective_id"),
        request=(message if action is LeadObjectiveAction.START else None),
        target_paths=tuple(value.get("target_paths", ())),
        next_step=validation_call('$.next_step', ArtifactObjectiveNextStep, value['next_step']),
        draft_source_event_ids=(),
    )


def _decode_artifact_intent(value: object) -> ArtifactProposalIntent:
    if type(value) is not dict or set(value) != {"operations"}:
        raise validation_failure(ValueError(), '$', 'expected an object containing exactly operations')
    raw_operations = value["operations"]
    if type(raw_operations) is not list:
        raise validation_failure(ValueError(), '$.operations', 'expected an array')
    operations: list[object] = []
    for operation_index, item in enumerate(raw_operations):
        if type(item) is not dict or type(item.get("kind")) is not str:
            raise validation_failure(
                ValueError(),
                f'$.operations[{operation_index}]',
                'each operation must be an object with a textual kind',
            )
        kind = item["kind"]
        if kind == "create" and set(item) == {"kind", "new_text", "suggested_path"}:
            operations.append(
                validation_call(
                    f'$.operations[{operation_index}]',
                    ArtifactCreateIntent,
                    item['suggested_path'],
                    item['new_text'],
                )
            )
        elif kind == "patch" and set(item) == {
            "evidence_id",
            "kind",
            "replacement_text",
        }:
            operations.append(
                validation_call(
                    f'$.operations[{operation_index}]',
                    ArtifactPatchIntent,
                    item['evidence_id'],
                    item['replacement_text'],
                )
            )
        elif kind == "replace" and set(item) == {
            "evidence_id",
            "kind",
            "new_text",
        }:
            operations.append(
                validation_call(
                    f'$.operations[{operation_index}]',
                    ArtifactReplaceIntent,
                    item['evidence_id'],
                    item['new_text'],
                )
            )
        elif kind == "rename" and set(item) == {
            "evidence_id",
            "kind",
            "suggested_destination",
        }:
            operations.append(
                validation_call(
                    f'$.operations[{operation_index}]',
                    ArtifactRenameIntent,
                    item['evidence_id'],
                    item['suggested_destination'],
                )
            )
        elif kind == "delete" and set(item) == {"evidence_id", "kind"}:
            operations.append(validation_call(
                f'$.operations[{operation_index}]',
                ArtifactDeleteIntent,
                item['evidence_id'],
            ))
        else:
            raise validation_failure(
                ValueError(),
                f'$.operations[{operation_index}]',
                (
                    'operation fields must match kind: create (suggested_path, new_text), patch '
                    '(evidence_id, replacement_text), replace (evidence_id, new_text), rename '
                    '(evidence_id, suggested_destination), delete (evidence_id)'
                ),
            )
    return ArtifactProposalIntent(
        operations=tuple(operations),
        additional_review_domains=(),
    )


def _decode_shared_premise_intents(value: object) -> tuple[LeadSharedPremiseIntent, ...]:
    if type(value) is not list:
        raise BackendProtocolError("shared premises must be an array")
    result = []
    for item in value:
        if type(item) is not dict or set(item) != {"slot_id", "selected_premise", "basis_input_ids"}:
            raise BackendProtocolError("shared premise intent fields are invalid")
        if type(item["basis_input_ids"]) is not list:
            raise BackendProtocolError("shared premise source aliases must be an array")
        result.append(LeadSharedPremiseIntent(item["slot_id"], item["selected_premise"],
                                              tuple(item["basis_input_ids"])))
    return tuple(result)
