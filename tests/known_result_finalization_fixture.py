"""Real known-result finalization fixture and call-local public adapter."""
from __future__ import annotations
from dataclasses import replace
from story_writing_agents.agents import LEAD_ORCHESTRATOR
from story_writing_agents.narrative_rule_revision import NarrativeRuleRevisionSelection
from story_writing_agents.conversations import (
    CollaborationAssignment,
    CollaborationEventDraft,
    CollaborationEventKind,
    CollaborationExecutionResult,
    CollaborationInputKind,
    CollaborationInputState,
    CollaborationInvocationKind,
    CollaborationLeadEvidence,
    CollaborationLeadJudgmentPayload,
    CollaborationLeadSynthesisPayload,
    CollaborationMessagePayload,
    CollaborationProposal,
    CollaborationProviderCallPayload,
    CollaborationSpecialistAssessment,
    CollaborationSpecialistReplyPayload,
    CollaborationStepState,
    CollaborationStepStatePayload,
    LeadEvidenceStatus,
    LeadOutcomeScope,
)
from tests.collaboration_contract_fixture import (
    LEAD,
    RUNTIME,
    TURN_ID,
    USER,
    make_assignment,
    make_input,
    make_lead_evidence,
    make_proposal,
    qualify_fixture_basis,
    replace_proposal,
    result_batch,
    running_history,
)


async def seed_narrative_rule_impact(case, direction: str) -> NarrativeRuleRevisionSelection:
    """Record the older authority scenarios' whole-work impact on actual fixture bytes."""
    from dataclasses import fields, is_dataclass
    from hashlib import sha256

    from story_writing_agents.artifact_transactions.synchronization_material import capture_narrative_rule_impact_material
    from story_writing_agents.artifact_transactions.synchronization_review import _conditions_and_rules
    from story_writing_agents.conversations import SynchronizationPayload, synchronization_event_ref
    from story_writing_agents.conversations.event_contracts.synchronization import (
        SynchronizationReadEvidence, SynchronizationReadReceipt,
    )
    from story_writing_agents.lead_preparation.lead_task_control import prepare_task_transition
    from story_writing_agents.lead_task_transitions import LeadTaskAction, LeadTaskDecision
    from story_writing_agents.narrative_rule_revision import NarrativeRuleRevisionFeature
    from story_writing_agents.runtime_resources import collaboration_event_journal as journal
    from story_writing_agents.specialist_routing import (
        ArtifactReviewDomain, AuthoringActivity, AuthoringContribution, AuthoringContributionRole,
        AuthoringIntent, AuthoringScope, ReviewBasis, SpecialistAssignmentMode, artifact_domain, specialists_for_domains,
    )
    from story_writing_agents.synchronization_assessment import (
        SynchronizationAssessment, SynchronizationBasis, SynchronizationFinding,
        SynchronizationJudgment, SynchronizationRead,
    )

    runtime, session = case.runtime, case.session_id
    conversation = case.conversation("Review rule direction across the current work")
    with runtime.bind_session_artifacts(session).open_ready_resources() as access:
        physical = {item.path: item.content for item in access.repository.capture_complete_state()}
    rules = "core_documents/narrative_rules.md"
    tag = lambda value: "sha256:" + sha256(value).hexdigest()
    selection = NarrativeRuleRevisionSelection(
        tag(physical[rules]), direction, (NarrativeRuleRevisionFeature.NARRATION,),
        preserve=("Keep established plot facts and character voices.",),
    )
    decision = LeadTaskDecision.for_conversation(session, conversation, LeadTaskAction.NEW_TASK, text=direction)
    empty = runtime.inspect_lead_collaboration(session, conversation)
    turn, step, origin, message, _, transition = prepare_task_transition(
        session, conversation, decision, None, (), empty, (), narrative_rule_revision=selection,
    )
    authorization = synchronization_event_ref(origin)
    material = capture_narrative_rule_impact_material(physical, selection, authorization)
    paths = tuple(item.path for item in material.census.documents)
    domains = tuple(domain for domain in ArtifactReviewDomain if domain in {artifact_domain(path) for path in paths})
    assignments = tuple(make_assignment(f"impact_{index}", specialist_id=agent, input_ids=("user_input",),
                                        max_physical_calls=1)
                        for index, agent in enumerate(specialists_for_domains(domains)))
    user_input = make_input("user_input", kind=CollaborationInputKind.USER_INPUT,
                            locator=f"conversation/{turn}/user_input", revision_digest=tag(direction.encode()),
                            start_line=None, end_line=None)
    prototype = make_lead_evidence()
    lines = dict(material.narrative_impact_line_counts)[rules]
    front_read = replace(prototype.receipts[0], operation_id=f"lead_{turn}", result_correlation_id=f"outcome_{turn}",
                         locator="${" + rules + "}", revision_digest=selection.rules_digest,
                         content_digest=selection.rules_digest, utf8_bytes=len(physical[rules]),
                         boundary_start=1, boundary_end=lines, start_line=1, end_line=lines)
    original = make_proposal(assignments=assignments, inputs=(user_input,), evidence=prototype)
    live_evidence = replace(prototype, receipts=(front_read,))
    review_scope = AuthoringScope(
        AuthoringActivity.REVIEW, paths, domains, direction, transition.payload.receipt.target_task_id, 1,
        schema_version=3, intent=AuthoringIntent.REVIEW_ONLY,
        contributions=tuple(AuthoringContribution(domain, AuthoringContributionRole.CONSTRAINT_ASSESSOR)
                            for domain in domains),
    )
    reviewed = replace_proposal(original, step_id=step, authoring_scope=review_scope,
        basis=replace(original.basis, schema_version=2, evidence=live_evidence), assignments=tuple(
            replace(assignment, mode=SpecialistAssignmentMode.REVIEW,
                    review_basis=ReviewBasis(("user_input",), direction, 1)) for assignment in assignments))
    history, _ = running_history(original)
    result, _ = result_batch(history, original)

    def rebind(payload):
        if type(payload) is CollaborationProposal:
            return reviewed
        if type(payload) is CollaborationLeadEvidence:
            return live_evidence
        if type(payload) is CollaborationAssignment:
            return next(assignment for assignment in reviewed.assignments if assignment.assignment_id == payload.assignment_id)
        if isinstance(payload, str):
            return reviewed.proposal_digest if payload == original.proposal_digest else payload
        if isinstance(payload, tuple):
            return tuple(rebind(item) for item in payload)
        if is_dataclass(payload):
            return replace(payload, **{field.name: rebind(getattr(payload, field.name)) for field in fields(payload)})
        return payload

    journal.append_drafts(0, (origin, message, transition), append_batch=runtime.append_collaboration_event_batch)
    sequence = 3
    synthesis_invocation = next(item.payload.invocation_id for item in history
                                if type(item.payload) is CollaborationProviderCallPayload
                                and item.payload.invocation_kind is CollaborationInvocationKind.LEAD_SYNTHESIS)
    by_batch = {}
    for event in history:
        by_batch.setdefault(event.batch_id, []).append(event.to_draft())
    by_batch[result.batch_id] = list(result.events)
    for batch_id, records in by_batch.items():
        drafts = []
        for event in records:
            payload = rebind(event.payload)
            if isinstance(payload, CollaborationLeadJudgmentPayload):
                payload = replace(payload, authoring_scope=review_scope)
            elif isinstance(payload, CollaborationSpecialistReplyPayload):
                payload = replace(payload, mode=SpecialistAssignmentMode.REVIEW,
                                  provider_invocation_id=f"invocation_{payload.assignment_id}")
            elif isinstance(payload, CollaborationLeadSynthesisPayload):
                payload = replace(payload, authoring_scope=review_scope, provider_invocation_id=synthesis_invocation)
            drafts.append(replace(event, session_id=session, conversation_id=conversation, turn_id=turn,
                                  step_id=step, payload=payload))
        if batch_id == result.batch_id:
            synthesis = synchronization_event_ref(next(item for item in drafts
                                                       if type(item.payload) is CollaborationLeadSynthesisPayload))
            proof = journal.draft(session_id=session, conversation_id=conversation, turn_id=turn, step_id=step,
                kind=CollaborationEventKind.SYNCHRONIZATION, sender=RUNTIME, recipient=USER,
                payload=SynchronizationPayload(read_evidence=SynchronizationReadEvidence(
                    synthesis_invocation, synthesis, None, (), current_state_digest=material.current_state_digest,
                    narrative_rule_revision=authorization)))
            documents = {item.path: item for item in material.census.documents}
            reviewer_refs = {}
            reads = {}
            proofs = [proof]
            for reply in (item for item in drafts if type(item.payload) is CollaborationSpecialistReplyPayload):
                specialist = reply.sender.agent_id
                reviewer_refs[specialist] = synchronization_event_ref(reply)
                relevant = tuple(path for path in sorted(physical) if path == rules
                                 or specialist in specialists_for_domains((artifact_domain(path),)))
                receipts = tuple(SynchronizationReadReceipt(f"read_2_{index}", path, tag(physical[path]),
                                 tag(physical[path]), tag(physical[path]), None, None, 1, 2)
                                 for index, path in enumerate(relevant, 1))
                specialist_proof = replace(proof, event_id=journal.new_id("reads"), payload=SynchronizationPayload(
                    read_evidence=replace(proof.payload.read_evidence, invocation_id=reply.payload.provider_invocation_id,
                                          synthesis=reviewer_refs[specialist], receipts=receipts)))
                proofs.append(specialist_proof)
                for receipt in receipts:
                    reads[specialist, receipt.path] = SynchronizationRead(
                        documents[receipt.path], synchronization_event_ref(specialist_proof), receipt.receipt_id,
                        selection_digest=receipt.selection_digest, source_revision=receipt.source_revision,
                    )
            conditions_digest, rules_digest = _conditions_and_rules(reviewed, documents)
            basis = SynchronizationBasis(
                review_scope.task_id, 1, authorization, conditions_digest, rules_digest,
                material.current_state_digest, material.current_state_digest, review_synthesis=synthesis,
                narrative_rule_revision=authorization, narrative_rule_line_counts=material.narrative_impact_line_counts,
            )
            findings = []
            for changed, target in material.census.required_pairs:
                domain = artifact_domain(target)
                specialist, = specialists_for_domains((domain,))
                findings.append(SynchronizationFinding(
                    changed, target, SynchronizationJudgment.NO_CHANGE, "This fixture target preserves the selected rule.",
                    (reads[specialist, changed], reads[specialist, target]), reviewer_refs[specialist], domain,
                ))
            report = replace(proof, event_id=journal.new_id("impact"), payload=SynchronizationPayload(
                assessment=SynchronizationAssessment(basis, material.census, tuple(findings))))
            drafts[-1:-1] = [*proofs, report]
            drafts[-1] = replace(drafts[-1], payload=replace(
                drafts[-1].payload, visible_exchange_event_ids=tuple(item.event_id for item in drafts[:-1]),
            ))
        appended = journal.append_drafts(sequence, tuple(drafts), append_batch=runtime.append_collaboration_event_batch)
        sequence = appended.last_sequence
    reference = synchronization_event_ref(report)
    events = runtime.read_collaboration_events(session, conversation)
    finalize_known_result(runtime, session, events, runtime.inspect_lead_collaboration(session, conversation))
    case.assertIn(reference, dict(runtime.inspect_narrative_rule_impacts(session)))
    return replace(selection, impact=reference)


def finalize_known_result(runtime, session_id, events, projection):
    return runtime.known_result_finalization.finalize_known_result(
        session_id,
        events,
        projection,
        context_service=runtime.agent_context_service,
        render_premise_report=runtime.premise_report_turn,
        append_batch=runtime.append_collaboration_event_batch,
        validate_exact_candidate_reviews=runtime.validate_exact_candidate_reviews,
        qualify_artifact_final_review=runtime.qualify_artifact_final_review,
        attach_document_plan=runtime.attach_document_plan,
        bind_session_artifacts=runtime.bind_session_artifacts,
        build_contextual_definition=runtime.build_guarded_contextual_definition,
        build_agent_profile=runtime.build_collaboration_agent_profile,
        read_authoring_events=runtime.read_session_authoring_events,
        validate_review_material=runtime.validate_review_material,
        qualify_readiness_targets=runtime.qualify_readiness_targets,
        qualify_authoring_inputs=runtime.qualify_authoring_inputs,
    )


def _synchronization_fixture_assessment(
    material, proposal, candidate, request, judgment, candidate_draft, synthesis, reviewers,
    *, discrepancy_pair, unread_pair,
):
    """Bind pre-seeded reviewer facts to captured workspace and projected candidate bytes."""
    from hashlib import sha256

    from story_writing_agents.conversations import authoring_scope_to_dict, synchronization_event_ref
    from story_writing_agents.conversations.event_value_validation import canonical_json
    from story_writing_agents.specialist_routing import artifact_domain, specialists_for_domains
    from story_writing_agents.synchronization_assessment import (
        SynchronizationAbsence,
        SynchronizationAssessment,
        SynchronizationBasis,
        SynchronizationCandidate,
        SynchronizationFinding,
        SynchronizationJudgment,
        SynchronizationRead,
    )

    documents = {row.path: row for row in material.census.documents}
    prior = {row.path: row for row in material.census.prior_documents}
    receipts = {row.locator.removeprefix("${").removesuffix("}"): row
                for row in judgment.payload.evidence.receipts}
    changed_paths = frozenset(material.census.changes)

    def read(path: str):
        current = documents[path]
        if path in changed_paths and current.digest is not None:
            return SynchronizationRead(current, synchronization_event_ref(candidate_draft), "candidate")
        document = prior.get(path, current)
        receipt = receipts.get(path)
        if receipt is None or receipt.revision_digest != f"sha256:{document.digest}":
            return None
        return SynchronizationRead(document, synchronization_event_ref(judgment), receipt.evidence_id,
                                   receipt.start_line, receipt.end_line,
                                   selection_digest=receipt.content_digest,
                                   source_revision=receipt.revision_digest)

    supporting_review = next((item for item in reviewers
                              if item.payload.assessment is CollaborationSpecialistAssessment.SUPPORTS), synthesis)
    findings = []
    for changed, target in material.census.required_pairs:
        pair = (changed, target)
        if pair == unread_pair:
            finding = SynchronizationFinding(
                changed, target, SynchronizationJudgment.UNREAD,
                "The review lacks an exact current read for this pair.",
            )
        elif documents[target].absence is SynchronizationAbsence.INITIAL:
            finding = SynchronizationFinding(changed, target, SynchronizationJudgment.INITIAL,
                                             "The fixture user leaves later initial construction open.")
        else:
            reads = (read(changed), read(target))
            if any(item is None for item in reads):
                continue
            judgment_kind = SynchronizationJudgment.NO_CHANGE
            reason = "The fixture review found no change needed for the demonstrated material."
            domain = action = resolution = None
            reviewer = supporting_review
            if pair == discrepancy_pair:
                judgment_kind = SynchronizationJudgment.NEEDS_CHANGE
                reason = f"{target} still reflects the previous revision of {changed}."
                action = "Revise the affected passage."
                resolution = "Verify the exact revisions of both documents together."
                domain = artifact_domain(target)
                assert domain is not None
            if domain is not None:
                specialist_ids = specialists_for_domains((domain,))
                reviewer = next(item for item in reviewers if item.sender.agent_id in specialist_ids)
            finding = SynchronizationFinding(
                changed, target, judgment_kind, reason, reads, synchronization_event_ref(reviewer),
                domain, action, resolution,
            )
        findings.append(finding)
    if discrepancy_pair is not None:
        assert any(
            item.pair == discrepancy_pair and item.judgment is SynchronizationJudgment.NEEDS_CHANGE
            for item in findings
        )
    conditions = authoring_scope_to_dict(proposal.authoring_scope)
    conditions["editorial_context"] = proposal.basis.editorial_context_digest
    rules = documents["core_documents/narrative_rules.md"]
    basis = SynchronizationBasis(
        proposal.authoring_scope.task_id, proposal.authoring_scope.task_revision,
        synchronization_event_ref(request),
        "sha256:" + sha256(canonical_json(conditions)).hexdigest(),
        "sha256:" + sha256(canonical_json(rules.digest)).hexdigest(),
        candidate.change_set.base_state_digest, candidate.change_set.projected_state_digest,
        SynchronizationCandidate(candidate.candidate_id, candidate.candidate_revision,
                                 candidate.candidate_digest, candidate.change_set.change_set_digest),
        review_synthesis=synchronization_event_ref(synthesis),
    )
    return SynchronizationAssessment(basis, material.census, tuple(findings))


async def seed_reviewed_artifact(case, conversation_id, operations, *, extra_core_evidence=(),
                                 editorial_refs=(), editorial_context_digest=None,
                                 review_task: tuple[str, int] | None = None,
                                 retained_review_paths: tuple[str, ...] = (),
                                 discrepancy_pair: tuple[str, str] | None = None,
                                 unread_pair: tuple[str, str] | None = None,
                                 concern_specialist: str | None = None,
                                 narrative_rule_revision: NarrativeRuleRevisionSelection | None = None):
    """Publish a real reviewed candidate without unrelated provider authoring."""
    import hashlib
    import json
    from dataclasses import fields, is_dataclass

    from story_writing_agents import artifact_proposals as artifacts
    from story_writing_agents.artifact_transactions.synchronization_material import capture_synchronization_material
    from story_writing_agents.conversations import SynchronizationPayload, synchronization_event_ref
    from story_writing_agents.synchronization_assessment import SynchronizationJudgment
    from story_writing_agents.synchronization_obligations import (
        SynchronizationCondition, SynchronizationConditionState, SynchronizationStatus,
        SynchronizationTransition,
    )
    from story_writing_agents.runtime_resources import collaboration_event_journal as event_journal
    from tests.recommendation_fixture import task_assessment
    from story_writing_agents.conversations import events as payloads, event_vocabulary as vocabulary
    from story_writing_agents.conversations.context import project_collaboration_history
    from story_writing_agents.lead_preparation.lead_task_control import prepare_task_transition
    from story_writing_agents.lead_task_transitions import LeadTaskAction, LeadTaskDecision
    from story_writing_agents.specialist_routing import (
        AuthoringActivity, AuthoringScope, ReviewBasis, SpecialistAssignmentMode,
        AuthoringContribution, AuthoringContributionRole,
    )

    from story_writing_agents.writing_artifacts.catalog import CORE_ARTIFACT_PATHS

    runtime, session_id = case.runtime, case.session_id
    prefix = event_journal.new_id("fixture")
    input_id = prefix + "_candidate_input"
    turn_id = prefix + "_turn"
    step_id = prefix + "_step"
    initial_construction = all(kind == "create" and path in CORE_ARTIFACT_PATHS for kind, path, _ in operations)
    user_text = ("Review exact supplied operations. You may give bounded artifact review judgments; preserve supplied "
                 "facts and paths. Execution requires separate confirmation.\nOperations: " + json.dumps(operations))
    if initial_construction:
        user_text += "\nTreat remaining unbuilt core documents as unfinished initial construction."
    task_origin = ()
    if narrative_rule_revision is not None:
        decision = LeadTaskDecision.for_conversation(session_id, conversation_id, LeadTaskAction.NEW_TASK, text=user_text)
        turn_id, step_id, origin, message, _, transition = prepare_task_transition(
            session_id, conversation_id, decision, None, (), project_collaboration_history(()), (),
            narrative_rule_revision=narrative_rule_revision,
        )
        task_origin = (origin, message, transition)
        receipt = transition.payload.receipt
        review_task = receipt.target_task_id, receipt.target_task_revision
    digest = lambda content: "sha256:" + hashlib.sha256(content).hexdigest()
    async with runtime.lead_conversation_scope(session_id, conversation_id):
        binding = runtime.bind_session_artifacts(session_id)
        with binding.open_ready_resources() as access:
            captured = access.repository.capture_complete_state()
        base = {item.path: item.content for item in captured}
        paths = tuple(dict.fromkeys(item[1] for item in operations))
        evidence_paths = tuple(dict.fromkeys(
            (*(path for path in paths if path in base), *extra_core_evidence, *retained_review_paths),
        ))
        if not evidence_paths:
            evidence_paths = ("core_documents/global_outline.md",)
        evidence = tuple(artifacts.ArtifactIntentEvidence(
            evidence_id=(f"reference_{index + 1}" if path.startswith("core_documents/") else f"{prefix}_evidence_{index}"), path=path,
            source_digest=digest(base[path]), boundary_digest=digest(base[path]), start_line=None, end_line=None,
        ) for index, path in enumerate(evidence_paths))
        by_path = {item.path: item.evidence_id for item in evidence}
        intents = tuple(
            artifacts.ArtifactCreateIntent(path, value) if kind == "create" else
            artifacts.ArtifactReplaceIntent(by_path[path], value) if kind == "replace" else
            artifacts.ArtifactRenameIntent(by_path[path], value)
            for kind, path, value in operations
        )
        read_evidence = (*evidence, *(artifacts.ArtifactIntentEvidence(
            evidence_id=f"{prefix}_sync_{index}", path=path, source_digest=digest(content),
            boundary_digest=digest(content), start_line=None, end_line=None,
        ) for index, (path, content) in enumerate(base.items()) if path not in by_path))
        candidate = artifacts.build_artifact_review_candidate(
            candidate_id=prefix + "_candidate", candidate_revision=1,
            intent=artifacts.ArtifactProposalIntent(intents), evidence=read_evidence,
            grounding_evidence_ids=tuple(item.evidence_id for item in read_evidence), base_state=base,
            attached_core_paths=tuple(sorted({item.path for item in evidence if item.evidence_id.startswith("reference_") and item.path.startswith("core_documents/")}, key=lambda path: path.encode("utf-8"))),
        )
        manifest = digest(json.dumps(
            [(item.path, digest(item.content)) for item in captured], separators=(",", ":"),
        ).encode())
        prototype = make_lead_evidence().receipts[0]
        receipts = tuple(replace(
            prototype, evidence_id=item.evidence_id,
            operation_id=f"lead_{TURN_ID}", result_correlation_id=f"outcome_{TURN_ID}",
            manifest_digest=manifest, source_id=f"{prefix}_source_{index}",
            source_kind=(CollaborationInputKind.CORE_DOCUMENT if item.path.startswith("core_documents/")
                         else CollaborationInputKind.MANUSCRIPT),
            locator="${" + item.path + "}", source_state=CollaborationInputState.PRESENT,
            revision_digest=item.source_digest, content_digest=item.boundary_digest,
            utf8_bytes=len(base[item.path]), boundary_start=1,
            boundary_end=max(1, len(base[item.path].splitlines())), start_line=1,
            end_line=max(1, len(base[item.path].splitlines())), ordinal=index + 1,
        ) for index, item in enumerate(read_evidence))
        template_evidence = CollaborationLeadEvidence(
            LeadOutcomeScope.STORY_SPECIFIC,
            LeadEvidenceStatus.VERIFIED if any(base[item.path] for item in read_evidence) else LeadEvidenceStatus.EMPTY,
            receipts,
        )
        live_evidence = replace(template_evidence, receipts=tuple(replace(
            receipt, operation_id=f"lead_{turn_id}", result_correlation_id=f"outcome_{turn_id}",
        ) for receipt in receipts))
        disclosed = artifacts.artifact_review_specialists(candidate.disclosed_review_domains)
        mandatory = artifacts.artifact_review_specialists(candidate.mandatory_review_domains)
        assignments = tuple(make_assignment(
            f"{prefix}_a{index}", specialist_id=specialist, input_ids=(input_id,), max_physical_calls=1,
        ) for index, specialist in enumerate(disclosed))
        generic = make_proposal(
            proposal_id=prefix + "_consultation", assignments=assignments,
            inputs=(make_input(input_id, locator=receipts[0].locator,
                revision_digest=evidence[0].source_digest, start_line=None, end_line=None),),
            profile_ids=(LEAD_ORCHESTRATOR, *disclosed), evidence=template_evidence,
        )
        renamed_paths = tuple(value for kind, _, value in operations if kind == "rename")
        scope_paths = tuple(dict.fromkeys((*paths, *renamed_paths, *retained_review_paths)))
        task_id, task_revision = review_task or (prefix + "_task", 1)
        scoped_review = bool(editorial_refs or task_origin)
        scope = AuthoringScope(
            AuthoringActivity.REVIEW, scope_paths, candidate.disclosed_review_domains,
            "Review the exact fixture candidate.", task_id, task_revision, schema_version=3 if scoped_review else 2,
            contributions=tuple(AuthoringContribution(domain, AuthoringContributionRole.DRAFT_OWNER)
                                for domain in candidate.disclosed_review_domains) if scoped_review else (),
        )
        live_assignments = tuple(replace(
            item, input_ids=(*item.input_ids, 'user_input'), mode=SpecialistAssignmentMode.REVIEW,
            review_basis=ReviewBasis((input_id,), "Review the exact candidate.", 1),
        ) for item in assignments)
        synchronization_material = capture_synchronization_material(
            base, candidate.change_set, requested_paths=scope.target_paths, initial_construction=initial_construction,
        )
        proposal = replace_proposal(
            generic, step_id=step_id, assignments=live_assignments,
            basis=qualify_fixture_basis(replace(generic.basis, inputs=(make_input(
                input_id, kind=CollaborationInputKind.ARTIFACT_CANDIDATE,
                locator=f"artifact-candidate/{candidate.candidate_id}",
                revision_digest=candidate.candidate_digest, start_line=None, end_line=None,
            ),), evidence=live_evidence, schema_version=2 if scoped_review else 1,
                editorial_refs=editorial_refs, editorial_context_digest=editorial_context_digest), scope,
                user_text=user_text, assessment=task_assessment(foundation_ids=('user_input',), actions=('bounded artifact review judgments',)),
                user_locator=f"conversation/{turn_id}/user_input"), authoring_scope=scope,
        )
        history, _ = running_history(generic)
        result, _ = result_batch(history, generic)
        records = (*history, *result.events)
        identities = {generic.proposal_digest: proposal.proposal_digest,
                      generic.step_id: proposal.step_id, TURN_ID: turn_id}

        def collect(value):
            if is_dataclass(value):
                for field in fields(value):
                    item = getattr(value, field.name)
                    if isinstance(item, str) and (field.name in {
                        "event_id", "invocation_id", "call_id", "confirmation_request_id",
                        "execution_attempt_id", "step_id", "turn_id",
                    } or field.name.endswith("_event_id")):
                        identities.setdefault(item, event_journal.new_id("e"))
                    collect(item)
            elif isinstance(value, tuple):
                for item in value:
                    collect(item)

        for record in records:
            collect(record)

        concern = next((item for item in live_assignments if item.specialist_id == concern_specialist), None)
        if concern_specialist is not None and concern is None:
            raise ValueError("The fixture concern needs an exact assigned specialist.")

        def remap(value):
            if isinstance(value, str):
                return identities.get(value, value)
            if isinstance(value, tuple):
                return tuple(remap(item) for item in value)
            if type(value) is CollaborationLeadEvidence:
                return live_evidence
            if type(value) is CollaborationProposal:
                return proposal
            if type(value) is CollaborationAssignment:
                return next(item for item in live_assignments if item.assignment_id == value.assignment_id)
            if is_dataclass(value):
                updated = replace(value, **{field.name: remap(getattr(value, field.name))
                                            for field in fields(value)})
                if type(updated) in {CollaborationLeadJudgmentPayload, CollaborationLeadSynthesisPayload}:
                    changes = {"authoring_scope": scope}
                    if type(updated) is CollaborationLeadJudgmentPayload:
                        changes["authoring_admission"] = proposal.basis.authoring_admission
                    elif concern is not None:
                        changes["unresolved_input_ids"] = concern.input_ids
                    return replace(updated, **changes)
                if type(updated) is CollaborationSpecialistReplyPayload:
                    return replace(
                        updated, mode=SpecialistAssignmentMode.REVIEW,
                        input_ids=next(item.input_ids for item in live_assignments if item.assignment_id == updated.assignment_id),
                        assessment=(CollaborationSpecialistAssessment.CONCERN
                                    if concern is not None and concern.assignment_id == updated.assignment_id
                                    else updated.assessment),
                    )
                return updated
            return value

        judgment_id = identities[next(record.event_id for record in records
            if type(record.payload) is CollaborationLeadJudgmentPayload)]
        candidate_payload = payloads.ArtifactReviewCandidatePayload(
            candidate, judgment_id, proposal.proposal_id, proposal.proposal_revision,
            proposal.proposal_digest, mandatory, disclosed,
        )
        batches = []
        for record in history:
            if not batches or batches[-1][0] != record.batch_id:
                batches.append((record.batch_id, []))
            batches[-1][1].append(record)
        batches.append((result.batch_id, list(result.events)))
        sequence = len(task_origin)
        if task_origin:
            event_journal.append_drafts(0, task_origin, append_batch=runtime.append_collaboration_event_batch)
        request_draft = task_origin[1] if task_origin else event_journal.draft(
            session_id=session_id, conversation_id=conversation_id, turn_id=turn_id,
            step_id=proposal.step_id, kind=CollaborationEventKind.MESSAGE,
            sender=USER, recipient=LEAD, payload=CollaborationMessagePayload(user_text),
        )
        judgment_draft = candidate_draft = None
        for _, batch in batches:
            drafts = [request_draft] if sequence == 0 else []
            for record in batch:
                payload = remap(record.payload)
                kind = record.kind
                if type(payload) is CollaborationExecutionResult:
                    payload = replace(payload, visible_exchange_event_ids=tuple(item.event_id for item in drafts))
                drafts.append(CollaborationEventDraft(
                    record.schema_version, identities[record.event_id], session_id, conversation_id,
                    identities[TURN_ID], proposal.step_id, kind, record.sender, record.recipient, payload,
                ))
                if type(payload) is CollaborationLeadSynthesisPayload:
                    synthesis_draft = event_journal.draft(
                        session_id=session_id, conversation_id=conversation_id,
                        turn_id=turn_id, step_id=proposal.step_id,
                        kind=vocabulary.CollaborationEventKind.ARTIFACT_REVIEW_SYNTHESIS,
                        sender=record.sender, recipient=record.recipient,
                        payload=payloads.ArtifactReviewSynthesisPayload(
                            candidate.candidate_id, candidate.candidate_revision, candidate.candidate_digest,
                            proposal.proposal_id, proposal.proposal_revision, proposal.proposal_digest,
                            payload.execution_attempt_id, vocabulary.ArtifactReviewDecision.PROPOSE,
                            payload.message, "The reviewed changes are suitable.",
                            "Apply only the disclosed paths.", "Fixture review has no provider activity.",
                            payload.reply_event_ids, payload.unresolved_input_ids,
                        ),
                    )
                    drafts.append(synthesis_draft)
                    assert judgment_draft is not None and candidate_draft is not None
                    reply_drafts = {item.event_id: item for item in drafts}
                    assessment = _synchronization_fixture_assessment(
                        synchronization_material, proposal, candidate, request_draft, judgment_draft,
                        candidate_draft, synthesis_draft,
                        tuple(reply_drafts[reply_id] for reply_id in payload.reply_event_ids),
                        discrepancy_pair=discrepancy_pair, unread_pair=unread_pair,
                    )
                    report = event_journal.draft(
                        session_id=session_id, conversation_id=conversation_id, turn_id=turn_id,
                        step_id=proposal.step_id, kind=vocabulary.CollaborationEventKind.SYNCHRONIZATION,
                        sender=RUNTIME, recipient=USER, payload=SynchronizationPayload(assessment=assessment),
                    )
                    drafts.append(report)
                    if discrepancy_pair is not None:
                        finding = next(item for item in assessment.findings if item.pair == discrepancy_pair)
                        assert finding.judgment is SynchronizationJudgment.NEEDS_CHANGE
                        condition = SynchronizationCondition.from_finding(
                            prefix + "_condition", synchronization_event_ref(report), finding,
                        )
                        transition = SynchronizationTransition(
                            prefix + "_obligation", 1, (),
                            (SynchronizationConditionState(condition, SynchronizationStatus.OPEN, finding.reason),),
                            "The exact review found a synchronization issue.",
                        )
                        drafts.append(event_journal.draft(
                            session_id=session_id, conversation_id=conversation_id, turn_id=turn_id,
                            step_id=proposal.step_id, kind=vocabulary.CollaborationEventKind.SYNCHRONIZATION,
                            sender=RUNTIME, recipient=USER, payload=SynchronizationPayload(transition=transition),
                        ))
                if type(payload) is CollaborationLeadJudgmentPayload:
                    judgment_draft = drafts[-1]
                    candidate_draft = event_journal.draft(
                        session_id=session_id, conversation_id=conversation_id,
                        turn_id=identities[TURN_ID], step_id=proposal.step_id,
                        kind=vocabulary.CollaborationEventKind.ARTIFACT_REVIEW_CANDIDATE,
                        sender=RUNTIME, recipient=USER, payload=candidate_payload,
                    )
                    drafts.append(candidate_draft)
                if type(payload) is CollaborationStepStatePayload and payload.state in {
                    CollaborationStepState.AWAITING_CONFIRMATION, CollaborationStepState.RUNNING,
                }:
                    state = (vocabulary.ArtifactProposalState.AWAITING_CONSULTATION_CONFIRMATION
                        if payload.state is CollaborationStepState.AWAITING_CONFIRMATION
                        else vocabulary.ArtifactProposalState.REVIEWING)
                    state_draft = event_journal.draft(
                        session_id=session_id, conversation_id=conversation_id,
                        turn_id=identities[TURN_ID], step_id=proposal.step_id,
                        kind=vocabulary.CollaborationEventKind.ARTIFACT_PROPOSAL_STATE,
                        sender=RUNTIME, recipient=USER,
                        payload=payloads.ArtifactProposalStatePayload(
                            state, candidate.candidate_id, candidate.candidate_revision,
                            candidate.candidate_digest, proposal.proposal_id, proposal.proposal_revision,
                            proposal.proposal_digest, payload.confirmation_request_id,
                            payload.execution_attempt_id, None, None,
                        ),
                    )
                    if payload.state is CollaborationStepState.AWAITING_CONFIRMATION:
                        drafts.insert(len(drafts) - 1, state_draft)
                    else:
                        drafts.append(state_draft)
            receipt = event_journal.append_drafts(sequence, tuple(drafts), append_batch=runtime.append_collaboration_event_batch)
            if receipt.status is not vocabulary.CollaborationAppendStatus.APPENDED:
                raise RuntimeError("typed review fixture append did not succeed")
            events = runtime.conversation_storage_services.event_store.read(session_id, conversation_id)
            projection = project_collaboration_history(events)
            sequence = projection.last_sequence
        return finalize_known_result(runtime, session_id, events, projection)
