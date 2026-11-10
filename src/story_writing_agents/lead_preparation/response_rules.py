"""Source-authored Lead response rules and revision constraints."""

from __future__ import annotations

import hashlib
import json
from ..authoring_task_lifecycle import TaskBindingPlan
from ..document_plan import (
    DocumentNodeBinding,
    DocumentNodeSelection,
    DocumentCandidateSelection,
)
from ..core_document_readiness import (
    AuthoringTarget,
    assessment_schema as core_assessment_schema,
)
from ..authoring_admission import assessment_schema
from ..lead_task_transitions import LeadTaskAction, LeadTaskTransitionReceipt
from ..lead_provider_contracts import (
    LeadDiscussionConstraints,
    LeadResultRuleSeed,
    PremiseDecisionConstraints,
    LeadProviderConstraints,
    alternatives,
    nonempty_text_schema,
    string_array,
)
from dataclasses import asdict, field
from typing import Mapping
from ..conversations import CollaborationEvent, LeadProposalRelation
from ..specialist_routing import AuthoringActivity, AuthoringIntent, AuthoringScope
from . import reply_contract_values

def target_authoring_scope_prior(
    scope: AuthoringScope,
    source: AuthoringScope | None,
    receipt: LeadTaskTransitionReceipt | None,
) -> AuthoringScope | None:
    """Separate a caller-verified NEW_TASK target from its retained source evidence."""
    if (
        source is not None
        and type(receipt) is LeadTaskTransitionReceipt
        and receipt.origin_kind == "task_decision"
        and receipt.action is LeadTaskAction.NEW_TASK
        and (scope.task_id, scope.task_revision)
        == (receipt.target_task_id, receipt.target_task_revision)
        and scope.task_id != source.task_id
        and receipt.source_task_ref is not None
        and (receipt.source_task_ref.task_id, receipt.source_task_ref.task_revision)
        == (source.task_id, source.task_revision)
    ):
        return None
    return source

def document_successor_selection(
    binding: DocumentNodeBinding | None,
    source_event: CollaborationEvent | None,
    current_scope: AuthoringScope | None,
    target_scope: AuthoringScope | None,
    task_binding: TaskBindingPlan | None,
) -> DocumentNodeSelection | None:
    """Select an exact successor's input intent without carrying its authority."""
    if (type(binding) is not DocumentNodeBinding or type(source_event) is not CollaborationEvent
            or type(current_scope) is not AuthoringScope or type(target_scope) is not AuthoringScope
            or type(task_binding) is not TaskBindingPlan
            or task_binding.transition not in {"revise", "reserved"}):
        return None
    captured = getattr(source_event.payload, "authoring_scope", None)
    if (type(captured) is not AuthoringScope
            or (source_event.session_id, source_event.conversation_id, source_event.event_id)
            != (binding.task_ref.session_id, binding.task_ref.conversation_id, binding.task_ref.scope_event_id)
            or (captured.task_id, captured.task_revision, captured.target_paths)
            != (binding.task_ref.task_id, binding.task_ref.task_revision, current_scope.target_paths)
            or (current_scope.task_id, current_scope.task_revision)
            != (binding.task_ref.task_id, binding.task_ref.task_revision)
            or binding.task_ref.scope_digest != "sha256:" + hashlib.sha256(json.dumps(
                asdict(captured), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
            or len(current_scope.target_paths) != 1
            or target_scope.target_paths != current_scope.target_paths
            or target_scope.intent is not current_scope.intent
            or (target_scope.task_id, target_scope.task_revision)
            != (current_scope.task_id, current_scope.task_revision + 1)
            or (task_binding.task_id, task_binding.task_revision)
            != (target_scope.task_id, target_scope.task_revision)):
        return None
    return DocumentNodeSelection(binding.plan_ref, node_id=binding.node_id,
        candidate_uses=tuple(DocumentCandidateSelection(use.predecessor_node_id, use.candidate,
            use.permitted_scope, use.purpose) for use in binding.candidate_uses))

def lead_revision_result_contract(
    expected_relation: LeadProposalRelation, input_ids: tuple[str, ...],
    task_revisions: tuple[tuple[str, int], ...],
    constraints: LeadProviderConstraints,
    discussion_constraints: LeadDiscussionConstraints, *,
    fresh_revision_only: bool = False, source_scope: AuthoringScope | None = None,
    artifact_revision_only: bool = False,
    selected_document_review_path: str | None = None,
) -> LeadResultRuleSeed:
    if selected_document_review_path is not None and (
        not fresh_revision_only
        or constraints.document_node_digest is None
        or constraints.document_target_paths != (selected_document_review_path,)
    ):
        raise ValueError("selected document review requires its frozen exact node policy")
    authoring = lead_front_result_contract(
        expected_relation, input_ids, task_revisions, constraints,
        fresh_revision_only=False,
    )
    discussion = reply_contract_values.lead_discussion_result_contract(discussion_constraints)
    def mutable(value: object) -> object:
        if isinstance(value, Mapping):
            return {key: mutable(member) for key, member in value.items()}
        if isinstance(value, (tuple, list)):
            return [mutable(member) for member in value]
        return value
    variants = mutable(list(authoring.schema_value()["anyOf"]))
    if artifact_revision_only:
        variants = [
            variant for variant in variants
            if variant.get("properties", {}).get("response_type", {}).get("enum", [])
            in (["artifact_change_proposal"], ["proposal_drafting_recommendation"])
            and variant.get("properties", {}).get("writing_task_scope", {}).get("type") == "object"
            and variant["properties"]["writing_task_scope"].get("properties", {}).get(
                "writing_intent", {}).get("enum", []) == [AuthoringIntent.AUTHORING.value]
        ]
        artifact_variants = []
        for variant in variants:
            properties = variant["properties"]
            if properties["response_type"]["enum"] == ["proposal_drafting_recommendation"]:
                request = properties["proposal_drafting_request"]
                options = request.get("anyOf", [request])
                permitted = [
                    option for option in options
                    if option["properties"]["action_type"]["enum"] == ["draft_document_change_proposal"]
                ]
                if not permitted:
                    continue
                if len(permitted) == 1:
                    properties["proposal_drafting_request"] = permitted[0]
                elif "anyOf" in request:
                    request["anyOf"] = permitted
            scope = properties["writing_task_scope"]["properties"]
            scope["activity_type"]["enum"] = ["review_writing"]
            scope["writing_intent"]["enum"] = ["authoring"]
            artifact_variants.append(variant)
        variants = artifact_variants
    elif fresh_revision_only or (source_scope is not None and source_scope.intent is AuthoringIntent.AUTHORING):
        selected_reviews = []
        if selected_document_review_path is not None:
            for variant in variants:
                properties = variant.get("properties", {})
                scope = properties.get("writing_task_scope", {})
                intent = scope.get("properties", {}).get("writing_intent", {}).get("enum", [])
                response = properties.get("response_type", {}).get("enum", [])
                if (
                    response == ["artifact_change_proposal"]
                    and intent == [AuthoringIntent.AUTHORING.value]
                    and "authoring_assessment" in properties
                ) or (
                    response == ["specialist_consultation_proposal"]
                    and intent == [AuthoringIntent.REVIEW_ONLY.value]
                ):
                    scope_properties = scope["properties"]
                    scope_properties["activity_type"]["enum"] = ["review_writing"]
                    scope_properties["target_artifact_paths"] = {
                        **scope_properties["target_artifact_paths"],
                        **string_array((selected_document_review_path,), minimum=1, maximum=1),
                    }
                    selected_reviews.append(variant)
        variants = [variant for variant in variants
                    if variant.get("properties", {}).get("response_type", {}).get("enum", [])
                    in (["specialist_consultation_proposal"], ["proposal_drafting_recommendation"])
                    and variant.get("properties", {}).get("writing_task_scope", {}).get("type") == "object"
                    and variant["properties"]["writing_task_scope"].get("properties", {}).get(
                        "writing_intent", {}).get("enum", []) == [AuthoringIntent.AUTHORING.value]]
        consultations = []
        for variant in variants:
            properties = variant["properties"]
            if properties["response_type"]["enum"] == ["proposal_drafting_recommendation"]:
                request = properties["proposal_drafting_request"]
                options = request.get("anyOf", [request])
                permitted = [
                    option for option in options
                    if option["properties"]["action_type"]["enum"]
                    == ["draft_specialist_consultation_proposal"]
                ]
                if not permitted:
                    continue
                if len(permitted) == 1:
                    properties["proposal_drafting_request"] = permitted[0]
                elif "anyOf" in request:
                    request["anyOf"] = permitted
            consultations.append(variant)
        variants = consultations
        for variant in variants:
            scope = variant["properties"]["writing_task_scope"]["properties"]
            scope["activity_type"]["enum"] = ["draft_writing", "revise_writing"]
            if (
                expected_relation is LeadProposalRelation.REVISE_PENDING
                and source_scope is not None
                and source_scope.activity is AuthoringActivity.REVIEW
            ):
                scope["activity_type"]["enum"].insert(0, "review_writing")
            scope["writing_intent"]["enum"] = ["authoring"]
        variants.extend(selected_reviews)
    return LeadResultRuleSeed.declare(
        "lead-response-v9",
        mutable({
            "anyOf": [*variants, discussion.schema_value()],
            "description": (
                "Discuss the revision or propose fresh authoring under "
                "the host-bound work scope."
            ),
        }),
    )

def lead_front_result_contract(
    expected_relation: LeadProposalRelation,
    input_ids: tuple[str, ...],
    task_revisions: tuple[tuple[str, int], ...] = (),
    constraints: LeadProviderConstraints | None = None,
    *, fresh_revision_only: bool = False,
) -> LeadResultRuleSeed:
    facts = constraints or LeadProviderConstraints(input_ids=input_ids, task_revisions=task_revisions)
    scoped = {"message": nonempty_text_schema(), "authoring_scope": facts.scope_schema()}
    if fresh_revision_only:
        scope_properties = scoped["authoring_scope"]["properties"]
        scope_properties["activity"]["enum"] = [AuthoringActivity.DRAFT.value, AuthoringActivity.REVISE.value]
        scope_properties["intent"]["enum"] = [AuthoringIntent.AUTHORING.value]
    proposal_meanings = {
        "proposal": (
            "Propose specialist consultation when the affected domains need drafting or "
            "review; disclose the assignments for separate confirmation."
        ),
        "artifact_intent": (
            "Propose exact artifact changes when content and READ evidence are ready for "
            "specialist review; separate review, Apply, and Commit confirmations remain "
            "required."
        ),
    }
    recommendation = reply_contract_values.closed_result_object({**scoped,
        "kind": {"enum": ["recommendation"], "type": "string"},
        "recommended_action": facts.recommendation_schema()},
        description=(
            "Recommend one proposal-drafting step when preparation should be confirmed before "
            "forming the full specialist or document-change proposal."
        ),
    )
    if facts.recommendation_only:
        variants = [recommendation]
    elif facts.preparation_kind:
        field = "artifact_intent" if facts.preparation_kind == "prepare_artifact" else "proposal"
        intent = (reply_contract_values.artifact_intent_result_schema(facts) if field == "artifact_intent" else
                  reply_contract_values.proposal_result_schema(expected_relation, input_ids, facts))
        variants = [
            reply_contract_values.closed_result_object({**scoped, "kind": {"enum": ["question"], "type": "string"}},
                description=(
                    "Ask a clarification question when required information is missing from the "
                    "confirmed preparation; preserve its scope."
                ),
            ),
            reply_contract_values.closed_result_object({**scoped, "kind": {"enum": ["proposal"], "type": "string"}, field: intent},
                description=proposal_meanings[field],
            ),
        ]
    else:
        can_track = not (facts.scope and facts.scope.intent is AuthoringIntent.REVIEW_ONLY)
        cancellation = {"objective_intent": {"type": "string", "enum": []}}
        if can_track and (facts.objective_ids is None or facts.objective_ids):
            identity: dict[str, object] = {"type": "string"}
            if facts.objective_ids:
                identity["enum"] = list(facts.objective_ids)
            cancellation = {"objective_intent": reply_contract_values.closed_result_object({
                "action": {"enum": ["cancel"], "type": "string"}, "objective_id": identity,
            }, description="Close only the selected request explicitly cancelled by the user; no document change.")}
        variants = [
            reply_contract_values.closed_result_object({**scoped, **cancellation}, required=tuple(scoped),
                description=(
                    "Answer workflow or status questions or report current READ-supported facts in REVIEW "
                    "scope. Optionally acknowledge an explicitly cancelled document change request."
                )),
            reply_contract_values.closed_result_object({**scoped, "kind": {"enum": ["question"], "type": "string"},
                "objective_intent": facts.objective_schema("clarify") if can_track else {"type": "string", "enum": []}},
                required=(*scoped, "kind"), description=(
                    "Ask for missing information, optionally starting or continuing the associated "
                    "document change request; keep that request pending."
                )),
            reply_contract_values.closed_result_object({**scoped, "kind": {"enum": ["critique"], "type": "string"}, **cancellation},
                required=(*scoped, "kind"), description=(
                    "Explain a scoped concern, optionally acknowledging an explicitly cancelled "
                    "document change request; this performs no document change."
                )),
            recommendation,
        ]
        for field, intent in (("proposal", reply_contract_values.proposal_result_schema(expected_relation, input_ids, facts)),
                              ("artifact_intent", reply_contract_values.artifact_intent_result_schema(facts))):
            variants.append(reply_contract_values.closed_result_object({**scoped,
                "kind": {"enum": ["proposal"], "type": "string"}, field: intent},
                description=proposal_meanings[field],
            ))
    if fresh_revision_only:
        variants = [variant for variant in variants
                    if not {"artifact_intent", "recommended_action"}.intersection(variant.get("properties", {}))]
    if facts.scope and facts.scope.intent is AuthoringIntent.REVIEW_ONLY:
        variants = [
            variant for variant in variants
            if "artifact_intent" not in variant.get("properties", {})
        ]
    if facts.adoption_intent is not None:
        variants = [variant for variant in variants if "proposal" in variant.get("properties", {})]
    if facts.premise_question_id is not None:
        variants.append(PremiseDecisionConstraints(facts.premise_question_id,
            facts.input_ids, allow_read_sources=facts.allow_read_sources).front_schema())
    if (facts.readiness_source_basis_ref is not None and not fresh_revision_only
            and not facts.preparation_kind and not facts.recommendation_only
            and facts.adoption_intent is None and facts.scope is None):
        variants.append(reply_contract_values.closed_result_object({
            "kind": {"type": "string", "enum": ["answer"]},
            "message": nonempty_text_schema(), "authoring_scope": {"type": "null"},
            "core_document_assessment": core_assessment_schema(
                None if facts.allow_read_sources else facts.input_ids,
                None if facts.allow_read_sources else (), facts.readiness_source_basis_ref),
        }, description="Assess the actual current whole-book basis and four core documents; readiness grants no authoring or write authority."))
    for variant in variants:
        if variant.get("properties", {}).get("kind", {}).get("enum") == ["question"]:
            variant["properties"]["planning_target"] = alternatives([
                {"type": "string", "enum": [target.value for target in AuthoringTarget],
                         "description": 'Semantic document kind for a genuine formal-writing clarification; this is not an artifact path, task, readiness finding, or execution authority.'},
                        {"type": "null", "description": 'No formal document-target classification is supplied; freeform, workflow, review, and already-targeted work retain their existing scope.'}])
            variant["required"] = [*variant["required"], "planning_target"]
    qualified_variants = []
    for variant in variants:
        properties = variant.get("properties", {})
        authoring_capable = (
            bool({"proposal", "artifact_intent", "recommended_action"}.intersection(properties))
            and properties.get("authoring_scope", {}).get("type") == "object"
            and facts.adoption_intent is not AuthoringIntent.REVIEW_ONLY
            and not (facts.scope is not None and facts.scope.intent is AuthoringIntent.REVIEW_ONLY)
        )
        if not authoring_capable:
            qualified_variants.append(variant)
            continue
        intent_schema = properties["authoring_scope"]["properties"].get("intent")
        offered_intents = () if intent_schema is None else tuple(intent_schema.get("enum", ()))
        if AuthoringIntent.REVIEW_ONLY.value in offered_intents and len(offered_intents) > 1:
            for intent in offered_intents:
                scoped_variant = json.loads(json.dumps(variant))
                scoped_variant["properties"]["authoring_scope"]["properties"]["intent"]["enum"] = [intent]
                if intent == AuthoringIntent.AUTHORING.value:
                    scoped_variant["properties"]["authoring_assessment"] = assessment_schema(
                        None if facts.allow_read_sources else facts.assessment_source_ids)
                    scoped_variant["required"].append("authoring_assessment")
                qualified_variants.append(scoped_variant)
        else:
            properties["authoring_assessment"] = assessment_schema(
                None if facts.allow_read_sources else facts.assessment_source_ids)
            variant["required"].append("authoring_assessment")
            qualified_variants.append(variant)
    variants = qualified_variants
    schema = alternatives(variants)
    schema["description"] = (
        (
            "Respond as Lead within the current writing scope. Choose the permitted result "
            "that advances the user's request; proposals and recommendations grant no "
            "execution or write authority. "
        )
        + schema.get("description", "")
    ).rstrip()
    concise_descriptions = {
        'Classify the current work and include every affected specialist domain and canonical workspace-relative target. Preserve the bound scope and work scope when supplied.':
            'Declare affected specialists and canonical targets; preserve work scope.',
        'Disclose review_only only for an explicit request to evaluate the supplied original without authoring or artifact authority. Activity review alone does not imply this intent. Preserve bound intent; requested revisions need fresh authoring work.':
            'review_only evaluates an explicitly supplied original; activity review is not intent. Preserve bound intent. Revisions need fresh authoring authority.',
        "Nonempty UTF-8 task instructions specifying this specialist's concrete deliverable and success criteria against the shared writing-task brief. Preserve its protected elements, change boundaries, and exact offered source identities.":
            'State the concrete deliverable and success criteria; preserve protected elements, change boundaries, and exact source IDs.',
        "Preparation recommendation sources are not assignment inputs unless confirmed preparation has materialized and offered them here. Preserve all required inputs and each reviewer's own source.":
            'Use every required/reviewer input. Recommendation sources become inputs only when preparation offers them.',
        ' USER instruction input is host-wired separately from the exact adopted_idea supplied candidate; preserve every other declared source role.':
            'Review-only adoption: USER instructions are host-wired; adopted_idea is the exact candidate. Keep other source roles.',
        'Assign Plot when plot, causality, pacing, or outline structure is affected; request its scoped draft or review.':
            'Plot: draft or review plot, causality, pacing and outline.',
        'Assign Character when characterization, motivation, or relationships are affected; request its scoped draft or review.':
            'Character: draft or review characterization, motivation and relationships.',
        'Assign Execution when scene prose, narrative voice, or narrative rules are affected; request its scoped draft or review.':
            'Execution: draft or review prose, voice and narrative rules.',
    }

    concise_descriptions.update({'Answer workflow or status questions directly when no clarification, request update, or specialist proposal is needed.': 'Answer '
                                                                                                                              'workflow/status '
                                                                                                                              'only '
                                                                                                                              'when '
                                                                                                                              'no '
                                                                                                                              'clarification, '
                                                                                                                              'request '
                                                                                                                              'update '
                                                                                                                              'or '
                                                                                                                              'specialist '
                                                                                                                              'proposal '
                                                                                                                              'is '
                                                                                                                              'needed.',
     'Ask for missing information while starting or continuing the associated document change request; keep that request pending.': 'Ask '
                                                                                                                                    'missing '
                                                                                                                                    'information '
                                                                                                                                    'while '
                                                                                                                                    'starting/continuing '
                                                                                                                                    'the '
                                                                                                                                    'document-change '
                                                                                                                                    'request; '
                                                                                                                                    'keep '
                                                                                                                                    'it '
                                                                                                                                    'pending.',
     'Ask for missing information with clarification_question, or explain a scoped concern with critique, when no document request update or proposal is needed.': 'Ask '
                                                                                                                                                                   'clarification_question '
                                                                                                                                                                   'or '
                                                                                                                                                                   'give '
                                                                                                                                                                   'scoped '
                                                                                                                                                                   'critique '
                                                                                                                                                                   'only '
                                                                                                                                                                   'when '
                                                                                                                                                                   'no '
                                                                                                                                                                   'document-request '
                                                                                                                                                                   'update '
                                                                                                                                                                   'or '
                                                                                                                                                                   'proposal '
                                                                                                                                                                   'is '
                                                                                                                                                                   'needed.',
     'Assess supplied foundations and USER-bounded creative work; host qualification remains separate.': 'Assess '
                                                                                                         'supplied '
                                                                                                         'foundations/USER-bounded '
                                                                                                         'creative '
                                                                                                         'work; '
                                                                                                         'host '
                                                                                                         'qualification '
                                                                                                         'is '
                                                                                                         'separate.',
     "Assignment sources use only offered assignment inputs or evidence IDs from completed authorized READs. Preparation recommendation sources are not assignment inputs unless confirmed preparation has materialized and offered them here. Preserve all required inputs and each reviewer's own source.": 'Only '
                                                                                                                                                                                                                                                                                                              'offered '
                                                                                                                                                                                                                                                                                                              'inputs '
                                                                                                                                                                                                                                                                                                              'or '
                                                                                                                                                                                                                                                                                                              'completed '
                                                                                                                                                                                                                                                                                                              'authorized '
                                                                                                                                                                                                                                                                                                              'READ '
                                                                                                                                                                                                                                                                                                              'IDs; '
                                                                                                                                                                                                                                                                                                              'recommendation '
                                                                                                                                                                                                                                                                                                              'sources '
                                                                                                                                                                                                                                                                                                              'need '
                                                                                                                                                                                                                                                                                                              'confirmed '
                                                                                                                                                                                                                                                                                                              'materialization/offering. '
                                                                                                                                                                                                                                                                                                              'Keep '
                                                                                                                                                                                                                                                                                                              'all '
                                                                                                                                                                                                                                                                                                              'required '
                                                                                                                                                                                                                                                                                                              'inputs '
                                                                                                                                                                                                                                                                                                              'and '
                                                                                                                                                                                                                                                                                                              'each '
                                                                                                                                                                                                                                                                                                              "reviewer's "
                                                                                                                                                                                                                                                                                                              'own '
                                                                                                                                                                                                                                                                                                              'source.',
     'Bind each declared dependency slot to a disclosed choice and currently available sources; unresolved choices require clarification.': 'Bind '
                                                                                                                                            'every '
                                                                                                                                            'declared '
                                                                                                                                            'slot '
                                                                                                                                            'to '
                                                                                                                                            'a '
                                                                                                                                            'disclosed '
                                                                                                                                            'choice/current '
                                                                                                                                            'sources; '
                                                                                                                                            'clarify '
                                                                                                                                            'unresolved '
                                                                                                                                            'choices.',
     'Create a document when the canonical destination is new; provide its complete UTF-8 text and proposed workspace-relative path.': 'Create '
                                                                                                                                       'only '
                                                                                                                                       'at '
                                                                                                                                       'a '
                                                                                                                                       'new '
                                                                                                                                       'canonical '
                                                                                                                                       'destination; '
                                                                                                                                       'supply '
                                                                                                                                       'complete '
                                                                                                                                       'UTF-8 '
                                                                                                                                       'text '
                                                                                                                                       'and '
                                                                                                                                       'proposed '
                                                                                                                                       'workspace-relative '
                                                                                                                                       'path.',
     'Declare affected specialists and canonical targets; preserve work scope.': 'Declare affected '
                                                                                    'specialists/canonical '
                                                                                    'targets; preserve '
                                                                                    'work scope.',
     'Delete the document identified by completed READ evidence when its removal is intended; this proposes deletion without executing it.': 'Propose '
                                                                                                                                             'intended '
                                                                                                                                             'document '
                                                                                                                                             'deletion '
                                                                                                                                             'using '
                                                                                                                                             'completed '
                                                                                                                                             'READ '
                                                                                                                                             'evidence; '
                                                                                                                                             'do '
                                                                                                                                             'not '
                                                                                                                                             'execute '
                                                                                                                                             'it.',
     'Draft a document-change proposal when the selected sources already support exact changes to these targets. Use this variant when no existing document change request is being continued.': 'Prepare '
                                                                                                                                                                                                 'exact '
                                                                                                                                                                                                 'target '
                                                                                                                                                                                                 'changes '
                                                                                                                                                                                                 'from '
                                                                                                                                                                                                 'selected '
                                                                                                                                                                                                 'supporting '
                                                                                                                                                                                                 'sources; '
                                                                                                                                                                                                 'use '
                                                                                                                                                                                                 'only '
                                                                                                                                                                                                 'when '
                                                                                                                                                                                                 'no '
                                                                                                                                                                                                 'document-change '
                                                                                                                                                                                                 'request '
                                                                                                                                                                                                 'is '
                                                                                                                                                                                                 'continued.',
     'Draft a specialist consultation proposal when further drafting or review is needed; include every affected specialist. Use this variant when no existing document change request is being continued.': 'Further '
                                                                                                                                                                                                             'draft/review: '
                                                                                                                                                                                                             'prepare '
                                                                                                                                                                                                             'consultation '
                                                                                                                                                                                                             'with '
                                                                                                                                                                                                             'every '
                                                                                                                                                                                                             'affected '
                                                                                                                                                                                                             'specialist; '
                                                                                                                                                                                                             'use '
                                                                                                                                                                                                             'only '
                                                                                                                                                                                                             'when '
                                                                                                                                                                                                             'no '
                                                                                                                                                                                                             'document-change '
                                                                                                                                                                                                             'request '
                                                                                                                                                                                                             'is '
                                                                                                                                                                                                             'continued.',
     'Exact disclosed shared-premise choice supported by the selected sources.': 'Exact disclosed '
                                                                                 'shared-premise '
                                                                                 'choice, supported by '
                                                                                 'selected sources.',
     'Justify this requirement with exact offered sources.': 'Justify using exact offered sources.',
     'Nonempty UTF-8 text.': 'Nonempty UTF-8.',
     'One explicit contribution role for a declared story domain.': 'Explicit contribution role for a '
                                                                    'declared domain.',
     'One explicit shared-premise dependency for declared draft-owner domains.': 'Explicit '
                                                                                 'shared-premise '
                                                                                 'dependency for '
                                                                                 'declared draft '
                                                                                 'owners.',
     'Patch only the exact span identified by completed READ evidence when a local edit suffices; provide its replacement text.': 'Local '
                                                                                                                                  'patch: '
                                                                                                                                  'replace '
                                                                                                                                  'only '
                                                                                                                                  'the '
                                                                                                                                  'exact '
                                                                                                                                  'completed-READ '
                                                                                                                                  'span; '
                                                                                                                                  'supply '
                                                                                                                                  'replacement '
                                                                                                                                  'text.',
     'Prepare exact changes while starting or advancing a document change request; review and Apply still require separate confirmation.': 'Start/advance '
                                                                                                                                           'the '
                                                                                                                                           'document-change '
                                                                                                                                           'request '
                                                                                                                                           'with '
                                                                                                                                           'exact '
                                                                                                                                           'proposals; '
                                                                                                                                           'separately '
                                                                                                                                           'confirm '
                                                                                                                                           'review '
                                                                                                                                           'and '
                                                                                                                                           'Apply.',
     'Prepare exact changes without a document change request update when none is needed; review and Apply still require separate confirmation.': 'Prepare '
                                                                                                                                                  'exact '
                                                                                                                                                  'changes '
                                                                                                                                                  'only '
                                                                                                                                                  'when '
                                                                                                                                                  'no '
                                                                                                                                                  'request '
                                                                                                                                                  'update '
                                                                                                                                                  'is '
                                                                                                                                                  'needed; '
                                                                                                                                                  'separately '
                                                                                                                                                  'confirm '
                                                                                                                                                  'review '
                                                                                                                                                  'and '
                                                                                                                                                  'Apply.',
     'Propose exact artifact changes when content and READ evidence are ready for specialist review; separate review, Apply, and Commit confirmations remain required.': 'Exact '
                                                                                                                                                                         'changes '
                                                                                                                                                                         'need '
                                                                                                                                                                         'ready '
                                                                                                                                                                         'content/READ '
                                                                                                                                                                         'evidence '
                                                                                                                                                                         'for '
                                                                                                                                                                         'specialist '
                                                                                                                                                                         'review. '
                                                                                                                                                                         'Review, '
                                                                                                                                                                         'Apply '
                                                                                                                                                                         'and '
                                                                                                                                                                         'Commit '
                                                                                                                                                                         'each '
                                                                                                                                                                         'require '
                                                                                                                                                                         'separate '
                                                                                                                                                                         'confirmation.',
     'Propose specialist consultation when the affected domains need drafting or review; disclose the assignments for separate confirmation.': 'Affected '
                                                                                                                                               'domains '
                                                                                                                                               'need '
                                                                                                                                               'draft/review: '
                                                                                                                                               'disclose '
                                                                                                                                               'specialist '
                                                                                                                                               'assignments '
                                                                                                                                               'for '
                                                                                                                                               'separate '
                                                                                                                                               'confirmation.',
     'Propose specialist work that starts or advances a document change request; confirmation authorizes the assignments, not document changes.': 'Propose '
                                                                                                                                                  'specialist '
                                                                                                                                                  'work '
                                                                                                                                                  'to '
                                                                                                                                                  'start/advance '
                                                                                                                                                  'its '
                                                                                                                                                  'document-change '
                                                                                                                                                  'request; '
                                                                                                                                                  'Confirm '
                                                                                                                                                  'authorizes '
                                                                                                                                                  'assignments '
                                                                                                                                                  'only.',
     'Propose specialist work when no document change request update is needed; confirmation authorizes only these disclosed assignments.': 'No '
                                                                                                                                            'request '
                                                                                                                                            'update '
                                                                                                                                            'needed: '
                                                                                                                                            'Confirm '
                                                                                                                                            'authorizes '
                                                                                                                                            'only '
                                                                                                                                            'the '
                                                                                                                                            'disclosed '
                                                                                                                                            'specialist '
                                                                                                                                            'assignments.',
     'Recommend one proposal-drafting step when preparation should be confirmed before forming the full specialist or document-change proposal.': 'Recommend '
                                                                                                                                                  'one '
                                                                                                                                                  'confirmed '
                                                                                                                                                  'preparation '
                                                                                                                                                  'before '
                                                                                                                                                  'forming '
                                                                                                                                                  'the '
                                                                                                                                                  'full '
                                                                                                                                                  'specialist/document-change '
                                                                                                                                                  'proposal.',
     'Recommendation source_ids select only offered preparation recommendation sources or evidence IDs from completed authorized READs. Select the exact approved historical draft to prepare its document; preserve its task and provenance. This selection does not make it an ordinary assignment input or authorize a write.': 'Select '
                                                                                                                                                                                                                                                                                                                                   'only '
                                                                                                                                                                                                                                                                                                                                   'offered '
                                                                                                                                                                                                                                                                                                                                   'preparation '
                                                                                                                                                                                                                                                                                                                                   'sources '
                                                                                                                                                                                                                                                                                                                                   'or '
                                                                                                                                                                                                                                                                                                                                   'completed '
                                                                                                                                                                                                                                                                                                                                   'authorized '
                                                                                                                                                                                                                                                                                                                                   'READ '
                                                                                                                                                                                                                                                                                                                                   'IDs. '
                                                                                                                                                                                                                                                                                                                                   'Use '
                                                                                                                                                                                                                                                                                                                                   'the '
                                                                                                                                                                                                                                                                                                                                   'exact '
                                                                                                                                                                                                                                                                                                                                   'approved '
                                                                                                                                                                                                                                                                                                                                   'historical '
                                                                                                                                                                                                                                                                                                                                   'draft '
                                                                                                                                                                                                                                                                                                                                   'to '
                                                                                                                                                                                                                                                                                                                                   'prepare '
                                                                                                                                                                                                                                                                                                                                   'its '
                                                                                                                                                                                                                                                                                                                                   'document, '
                                                                                                                                                                                                                                                                                                                                   'preserving '
                                                                                                                                                                                                                                                                                                                                   'task/provenance. '
                                                                                                                                                                                                                                                                                                                                   'Selection '
                                                                                                                                                                                                                                                                                                                                   'is '
                                                                                                                                                                                                                                                                                                                                   'not '
                                                                                                                                                                                                                                                                                                                                   'an '
                                                                                                                                                                                                                                                                                                                                   'assignment '
                                                                                                                                                                                                                                                                                                                                   'input '
                                                                                                                                                                                                                                                                                                                                   'or '
                                                                                                                                                                                                                                                                                                                                   'write '
                                                                                                                                                                                                                                                                                                                                   'authority.',
     'Rename the document identified by completed READ evidence when only its location changes; propose a new canonical workspace-relative destination.': 'Rename '
                                                                                                                                                          'only '
                                                                                                                                                          'its '
                                                                                                                                                          'location '
                                                                                                                                                          'using '
                                                                                                                                                          'completed '
                                                                                                                                                          'READ; '
                                                                                                                                                          'propose '
                                                                                                                                                          'a '
                                                                                                                                                          'canonical '
                                                                                                                                                          'workspace-relative '
                                                                                                                                                          'destination.',
     'Replace all text when a whole-document rewrite is needed; select completed whole-file READ evidence and provide complete new text.': 'Whole-document '
                                                                                                                                           'rewrite: '
                                                                                                                                           'completed '
                                                                                                                                           'whole-file '
                                                                                                                                           'READ '
                                                                                                                                           'evidence '
                                                                                                                                           'and '
                                                                                                                                           'complete '
                                                                                                                                           'new '
                                                                                                                                           'text '
                                                                                                                                           'are '
                                                                                                                                           'required.',
     "Respond as Lead within the current writing scope. Choose the permitted result that advances the user's request; proposals and recommendations grant no execution or write authority.": 'Choose '
                                                                                                                                                                                             "Lead's "
                                                                                                                                                                                             'permitted '
                                                                                                                                                                                             'in-scope '
                                                                                                                                                                                             'result '
                                                                                                                                                                                             'to '
                                                                                                                                                                                             'advance '
                                                                                                                                                                                             'the '
                                                                                                                                                                                             'USER '
                                                                                                                                                                                             'request. '
                                                                                                                                                                                             'Proposals/recommendations '
                                                                                                                                                                                             'grant '
                                                                                                                                                                                             'no '
                                                                                                                                                                                             'execution/write '
                                                                                                                                                                                             'authority.',
     'Start a document change request when no matching request exists; track the selected targets without treating preparation as Apply.': 'Start '
                                                                                                                                           'a '
                                                                                                                                           'document-change '
                                                                                                                                           'request '
                                                                                                                                           'only '
                                                                                                                                           'if '
                                                                                                                                           'none '
                                                                                                                                           'matches; '
                                                                                                                                           'track '
                                                                                                                                           'selected '
                                                                                                                                           'targets. '
                                                                                                                                           'Preparation '
                                                                                                                                           'is '
                                                                                                                                           'not '
                                                                                                                                           'Apply.',
     'State the concrete deliverable and success criteria; preserve protected elements, change boundaries, and exact source IDs.': 'Concrete '
                                                                                                                                        'deliverable '
                                                                                                                                        'and '
                                                                                                                                        'success '
                                                                                                                                        'criteria; '
                                                                                                                                        'preserve '
                                                                                                                                        'protected '
                                                                                                                                        'elements, '
                                                                                                                                        'change '
                                                                                                                                        'boundaries, '
                                                                                                                                        'exact '
                                                                                                                                        'task/source '
                                                                                                                                        'IDs.',
     'This required USER decision is unavailable.': 'Required USER decision unavailable.',
     'review_only evaluates an explicitly supplied original; activity review is not intent. Preserve bound intent. Revisions need fresh authoring authority.': 'review_only '
                                                                                                                                                               'evaluates '
                                                                                                                                                               'the '
                                                                                                                                                               'explicit '
                                                                                                                                                               'supplied '
                                                                                                                                                               'original; '
                                                                                                                                                               'review '
                                                                                                                                                               'activity '
                                                                                                                                                               'is '
                                                                                                                                                               'not '
                                                                                                                                                               'intent. '
                                                                                                                                                               'Keep '
                                                                                                                                                               'bound '
                                                                                                                                                               'intent; '
                                                                                                                                                               'revisions '
                                                                                                                                                               'need '
                                                                                                                                                               'fresh '
                                                                                                                                                               'authoring '
                                                                                                                                                               'authority.'})
    def compact_metadata(node):
        if type(node) is dict:
            description = node.get("description")
            if type(description) is str:
                if description in concise_descriptions:
                    description = concise_descriptions[description]
                if description.startswith("Continue task ") and " at revision " in description:
                    description = description.replace("Continue task ", "Task ", 1).replace(" at revision ", "@", 1).replace(
                        " when this is the task being addressed; copy this exact pair together.",
                        " when addressed; copy the exact ID/revision pair.")
                if description.startswith("Assignment sources use only offered assignment inputs") or description.startswith("Only offered inputs or completed authorized READ IDs;"):
                    description = "Assignment source rule; keep every required/own-reviewer input."
                elif description.startswith("Select only offered preparation sources or completed authorized READ IDs.") or description.startswith("Recommendation source_ids select only offered preparation recommendation sources"):
                    description = "Recommendation source rule; selection grants no input/write authority."
                elif description.startswith("Concrete deliverable and success criteria;") or description.startswith("State the concrete deliverable and success criteria;"):
                    description = "Specialist instruction rule; exact deliverable/success criteria."
                elif description.startswith("review_only evaluates the explicit supplied original;"):
                    description = "Writing intent rule; preserve explicit bound intent."
                node["description"] = description
            for child in node.values():
                compact_metadata(child)
        elif type(node) is list:
            for child in node:
                compact_metadata(child)

    compact_metadata(schema)
    shared_rules = (
        "Shared rules apply only to the named native fields; all offered schema choices remain binding. "
        "Assignment source rule (specialist_consultation_proposal.assignments[*].input_source_ids): "
        "use offered assignment inputs" + (" or completed authorized READ IDs" if facts.allow_read_sources else "") +
        "; recommendation sources need confirmed materialization/offering. Keep all required inputs and each reviewer's own source. "
        "Recommendation source rule (proposal_drafting_request.source_ids): select offered preparation sources" +
        (" or completed authorized READ IDs" if facts.allow_read_sources else "") +
        ". Use the exact approved historical draft to prepare its document, preserving task/provenance; selection is not an assignment input or write authority. "
        "Specialist instruction rule (specialist_consultation_proposal.assignments[*].task_instructions): "
        "state the concrete deliverable and success criteria; preserve protected elements, change boundaries, exact source IDs. "
        "Writing intent rule (writing_task_scope.writing_intent): review_only evaluates the explicit supplied original; "
        "review activity is not intent. Keep bound intent; revisions need fresh authoring authority."
    )
    schema["description"] += " " + shared_rules

    return reply_contract_values.lead_result_rule_seed("lead-response-v8" if facts.premise_question_id is not None else "lead-response-v6" if facts.scope is not None and facts.scope.schema_version < 3 else "lead-response-v7", schema)
