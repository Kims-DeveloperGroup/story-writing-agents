"""Real known-result finalization fixture and call-local public adapter."""
from __future__ import annotations
from dataclasses import replace
from story_writing_agents.agents import LEAD_ORCHESTRATOR
from story_writing_agents.conversations import (
    CollaborationAssignment,
    CollaborationEventDraft,
    CollaborationEventKind,
    CollaborationExecutionResult,
    CollaborationInputKind,
    CollaborationInputState,
    CollaborationLeadEvidence,
    CollaborationLeadJudgmentPayload,
    CollaborationLeadSynthesisPayload,
    CollaborationMessagePayload,
    CollaborationProposal,
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


async def seed_reviewed_artifact(case, conversation_id, operations, *, extra_core_evidence=(),
                                 editorial_refs=(), editorial_context_digest=None):
    """Publish a real reviewed candidate without unrelated provider authoring."""
    import hashlib
    import json
    from dataclasses import fields, is_dataclass

    from story_writing_agents import artifact_proposals as artifacts
    from story_writing_agents.runtime_resources import collaboration_event_journal as event_journal
    from tests.recommendation_fixture import task_assessment
    from story_writing_agents.conversations import events as payloads, event_vocabulary as vocabulary
    from story_writing_agents.conversations.context import project_collaboration_history
    from story_writing_agents.specialist_routing import (
        AuthoringActivity, AuthoringScope, ReviewBasis, SpecialistAssignmentMode,
        AuthoringContribution, AuthoringContributionRole,
    )

    runtime, session_id = case.runtime, case.session_id
    prefix = event_journal.new_id("fixture")
    input_id = prefix + "_candidate_input"
    turn_id = prefix + "_turn"
    digest = lambda content: "sha256:" + hashlib.sha256(content).hexdigest()
    async with runtime.lead_conversation_scope(session_id, conversation_id):
        binding = runtime.bind_session_artifacts(session_id)
        with binding.open_ready_resources() as access:
            captured = access.repository.capture_complete_state()
        base = {item.path: item.content for item in captured}
        paths = tuple(dict.fromkeys(item[1] for item in operations))
        evidence_paths = tuple(dict.fromkeys((*tuple(path for path in paths if path in base), *extra_core_evidence)))
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
        candidate = artifacts.build_artifact_review_candidate(
            candidate_id=prefix + "_candidate", candidate_revision=1,
            intent=artifacts.ArtifactProposalIntent(intents), evidence=evidence,
            grounding_evidence_ids=tuple(item.evidence_id for item in evidence), base_state=base,
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
        ) for index, item in enumerate(evidence))
        template_evidence = CollaborationLeadEvidence(
            LeadOutcomeScope.STORY_SPECIFIC, LeadEvidenceStatus.VERIFIED if any(base[item.path] for item in evidence) else LeadEvidenceStatus.EMPTY, receipts,
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
        scope_paths = tuple(dict.fromkeys((*paths, *(value for kind, _, value in operations if kind == "rename"))))
        scope = AuthoringScope(
            AuthoringActivity.REVIEW, scope_paths, candidate.disclosed_review_domains,
            "Review the exact fixture candidate.", prefix + "_task", 1, schema_version=3 if editorial_refs else 2,
            contributions=tuple(AuthoringContribution(domain, AuthoringContributionRole.DRAFT_OWNER)
                                for domain in candidate.disclosed_review_domains) if editorial_refs else (),
        )
        live_assignments = tuple(replace(
            item, input_ids=(*item.input_ids, 'user_input'), mode=SpecialistAssignmentMode.REVIEW,
            review_basis=ReviewBasis((input_id,), "Review the exact candidate.", 1),
        ) for item in assignments)
        user_text = ("Review exact supplied operations. You may give bounded artifact review judgments; preserve supplied " "facts and paths. Execution requires separate confirmation.\nOperations: " + json.dumps(operations))
        proposal = replace_proposal(
            generic, step_id=prefix + "_step", assignments=live_assignments,
            basis=qualify_fixture_basis(replace(generic.basis, inputs=(make_input(
                input_id, kind=CollaborationInputKind.ARTIFACT_CANDIDATE,
                locator=f"artifact-candidate/{candidate.candidate_id}",
                revision_digest=candidate.candidate_digest, start_line=None, end_line=None,
            ),), evidence=live_evidence, schema_version=2 if editorial_refs else 1,
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
                    return replace(updated, **changes)
                if type(updated) is CollaborationSpecialistReplyPayload:
                    return replace(updated, mode=SpecialistAssignmentMode.REVIEW, input_ids=next(item.input_ids for item in live_assignments if item.assignment_id == updated.assignment_id))
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
        sequence = 0
        for _, batch in batches:
            drafts = [event_journal.draft(session_id=session_id, conversation_id=conversation_id,
                turn_id=turn_id, step_id=proposal.step_id, kind=CollaborationEventKind.MESSAGE,
                sender=USER, recipient=LEAD, payload=CollaborationMessagePayload(user_text))] if sequence == 0 else []
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
                    drafts.append(event_journal.draft(
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
                    ))
                if type(payload) is CollaborationLeadJudgmentPayload:
                    drafts.append(event_journal.draft(
                        session_id=session_id, conversation_id=conversation_id,
                        turn_id=identities[TURN_ID], step_id=proposal.step_id,
                        kind=vocabulary.CollaborationEventKind.ARTIFACT_REVIEW_CANDIDATE,
                        sender=RUNTIME, recipient=USER, payload=candidate_payload,
                    ))
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
