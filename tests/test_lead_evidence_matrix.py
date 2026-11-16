"""Closed durable lead-evidence matrix and material-input contracts."""
from __future__ import annotations
import unittest
from dataclasses import replace
from story_writing_agents.errors import BackendProtocolError
from story_writing_agents.lead_preparation import lead_proposal_inputs as owner
from story_writing_agents.conversations import (
    CollaborationEvidenceAcquisition,
    CollaborationInputKind,
    CollaborationEvidenceReceipt,
    CollaborationInputState,
    CollaborationLeadEvidence,
    CollaborationLeadJudgmentPayload,
    LeadEvidenceStatus,
    LeadOutcomeKind,
    LeadOutcomeScope,
)

_DIGEST_A = "sha256:" + "1" * 64
_DIGEST_B = "sha256:" + "2" * 64
_OPERATION = "lead_turn"
_RESULT = "outcome_turn"


def _attached( evidence_id: str = "reference_1", *, locator: str = "${core_documents/global_outline.md}",
    ordinal: int = 1, utf8_bytes: int = 10, ranged: bool = False, ) -> CollaborationEvidenceReceipt:
    return CollaborationEvidenceReceipt(
        evidence_id=evidence_id, acquisition=CollaborationEvidenceAcquisition.ATTACHED_REFERENCE,
        operation_id=_OPERATION, result_correlation_id=_RESULT,
        manifest_digest=_DIGEST_A, source_id=f"source_{ordinal}",
        source_kind=CollaborationInputKind.CORE_DOCUMENT, locator=locator,
        source_state=CollaborationInputState.PRESENT, revision_digest=_DIGEST_A,
        content_digest=_DIGEST_B, utf8_bytes=utf8_bytes,
        boundary_kind="artifact_lines" if ranged else "whole", boundary_start=2 if ranged else None,
        boundary_end=3 if ranged else None, start_line=2 if ranged else None, end_line=3 if ranged else None,
        ordinal=ordinal, )

def _core_status( *, evidence_id: str = "status_1",
    state: CollaborationInputState = CollaborationInputState.PRESENT, utf8_bytes: int = 10,
) -> CollaborationEvidenceReceipt:
    absent = state is CollaborationInputState.ABSENT
    return CollaborationEvidenceReceipt(evidence_id=evidence_id,
        acquisition=CollaborationEvidenceAcquisition.CORE_STATUS, operation_id=_OPERATION,
        result_correlation_id=_RESULT, manifest_digest=_DIGEST_A, source_id=f"source_{evidence_id}",
        source_kind=CollaborationInputKind.CORE_DOCUMENT, locator=f"core_documents/{evidence_id}.md",
        source_state=state, revision_digest=None if absent else _DIGEST_A,
        content_digest=None if absent else _DIGEST_B, utf8_bytes=0 if absent else utf8_bytes,
        ordinal=int(evidence_id.rsplit("_", 1)[1]), )

def _zero_hit( *, kinds: tuple[CollaborationInputKind, ...] = ( CollaborationInputKind.WRITING_ARTIFACT, ),
) -> CollaborationEvidenceReceipt: return CollaborationEvidenceReceipt( evidence_id="search_1",
        acquisition=CollaborationEvidenceAcquisition.ZERO_HIT_SEARCH, operation_id=_OPERATION,
        result_correlation_id=_RESULT, manifest_digest=_DIGEST_A, source_state=CollaborationInputState.ABSENT,
        action_index=1, query_digest=_DIGEST_A, query_count=1, authorized_source_ids=("source_a",),
        authorized_source_kinds=kinds, authorized_source_fingerprint=_DIGEST_B, )

def _evidence_for( scope: LeadOutcomeScope, status: LeadEvidenceStatus, ) -> CollaborationLeadEvidence:
    if scope is LeadOutcomeScope.WORKFLOW_META: receipts: tuple[CollaborationEvidenceReceipt, ...] = (
            () if status is LeadEvidenceStatus.NOT_APPLICABLE else (_core_status(),) )
    elif status is LeadEvidenceStatus.NOT_APPLICABLE: receipts = ()
    elif status is LeadEvidenceStatus.VERIFIED: receipts = (_attached(),)
    elif status is LeadEvidenceStatus.MISSING: receipts = (
            _core_status(evidence_id="status_2", state=CollaborationInputState.ABSENT,), )
    elif status is LeadEvidenceStatus.EMPTY: receipts = (_core_status(utf8_bytes=0),)
    elif status is LeadEvidenceStatus.INCOMPLETE: receipts = (_core_status(),)
    else: receipts = ( _attached(),
            _attached("reference_2", locator="${manuscript/part-01/opening.md}#L2-L3", ordinal=2, ranged=True,),
        )
    return CollaborationLeadEvidence(scope, status, receipts)

class DurableLeadEvidenceMatrixTests(unittest.TestCase):
    """Pin the complete 2 x 6 x 5 closed semantic matrix."""

    def test_all_sixty_scope_status_kind_cells_are_closed_and_exact(self) -> None:
        normal_kinds = (LeadOutcomeKind.ANSWER, LeadOutcomeKind.QUESTION, LeadOutcomeKind.CRITIQUE, LeadOutcomeKind.RECOMMENDATION, LeadOutcomeKind.PROPOSAL)
        allowed_story = { LeadEvidenceStatus.VERIFIED: set(normal_kinds), LeadEvidenceStatus.MISSING: {
                LeadOutcomeKind.QUESTION, LeadOutcomeKind.RECOMMENDATION, LeadOutcomeKind.PROPOSAL, },
            LeadEvidenceStatus.EMPTY: { LeadOutcomeKind.QUESTION, LeadOutcomeKind.RECOMMENDATION,
                LeadOutcomeKind.PROPOSAL, }, LeadEvidenceStatus.INCOMPLETE: { LeadOutcomeKind.QUESTION,
                LeadOutcomeKind.CRITIQUE, LeadOutcomeKind.RECOMMENDATION, LeadOutcomeKind.PROPOSAL, },
            LeadEvidenceStatus.CONFLICTING: { LeadOutcomeKind.QUESTION, LeadOutcomeKind.CRITIQUE,
                LeadOutcomeKind.RECOMMENDATION, LeadOutcomeKind.PROPOSAL, }, }
        accepted = 0
        rejected = 0
        for scope in (LeadOutcomeScope.STORY_SPECIFIC, LeadOutcomeScope.WORKFLOW_META):
            for status in LeadEvidenceStatus:
                evidence = _evidence_for(scope, status)
                for kind in normal_kinds:
                    expected = ( scope is LeadOutcomeScope.WORKFLOW_META
                        and status is LeadEvidenceStatus.NOT_APPLICABLE
                        and kind is not LeadOutcomeKind.PROPOSAL ) or (
                        scope is LeadOutcomeScope.STORY_SPECIFIC and kind in allowed_story.get(status, set())
                    )
                    with self.subTest(scope=scope.value, status=status.value, kind=kind.value,):
                        if expected:
                            payload = CollaborationLeadJudgmentPayload(kind, "Visible lead judgment.", evidence,)
                            self.assertIs(payload.evidence, evidence)
                            accepted += 1
                        else:
                            with self.assertRaises(ValueError):
                                CollaborationLeadJudgmentPayload(kind, "Visible lead judgment.", evidence,)
                            rejected += 1
        self.assertEqual((accepted, rejected), (23, 37))

        from story_writing_agents.specialist_routing import (
            ArtifactReviewDomain, AuthoringActivity, AuthoringContribution,
            AuthoringContributionRole, AuthoringIntent, AuthoringScope, )
        path = "core_documents/global_outline.md"
        domain = ArtifactReviewDomain.PLOT_STRUCTURE
        scope = AuthoringScope(
            AuthoringActivity.REVIEW, (path,), (domain,), "Report the observed outline.", "task", 1,
            schema_version=3, intent=AuthoringIntent.REVIEW_ONLY,
            contributions=(AuthoringContribution(domain, AuthoringContributionRole.CONSTRAINT_ASSESSOR),))
        unrelated_empty = _core_status(evidence_id="status_2", utf8_bytes=0)
        read = replace(_attached("read_1"), acquisition=CollaborationEvidenceAcquisition.READ,
                       source_kind=CollaborationInputKind.WRITING_ARTIFACT, locator=path,
                       ordinal=None, action_index=2, origin_action_index=1)
        for receipt, whole_source in (
            (read, True), (replace(read, boundary_kind="artifact_lines", boundary_start=2, boundary_end=3,
                                   start_line=2, end_line=3), False),
            (_attached(), True), (_attached(locator=f"${{{path}}}#L2-L3", ranged=True), False),
        ):
            evidence = CollaborationLeadEvidence(
                LeadOutcomeScope.STORY_SPECIFIC, LeadEvidenceStatus.INCOMPLETE, (unrelated_empty, receipt))
            self.assertEqual(evidence.factual_writing_read_facts(),
                             ((receipt.evidence_id, path, whole_source),))
            payload = CollaborationLeadJudgmentPayload(
                LeadOutcomeKind.ANSWER, "The observed text is present.", evidence, scope)
            self.assertIs(payload.evidence, evidence)
            self.assertIs(payload.evidence.status, LeadEvidenceStatus.INCOMPLETE)
            self.assertIsNone(payload.core_document_readiness)
        for receipts in ( (unrelated_empty,), (unrelated_empty, _zero_hit()),
            (unrelated_empty, replace(read, utf8_bytes=0)),
            (unrelated_empty, replace(read, source_kind=CollaborationInputKind.CONVERSATION_EVENT,
                                      locator="event_1")), ):
            evidence = CollaborationLeadEvidence(
                LeadOutcomeScope.STORY_SPECIFIC, LeadEvidenceStatus.INCOMPLETE, receipts)
            self.assertEqual(evidence.factual_writing_read_facts(), ())
            with self.assertRaisesRegex(ValueError, "closed evidence matrix"):
                CollaborationLeadJudgmentPayload(LeadOutcomeKind.ANSWER, "Unsupported answer.", evidence, scope)
        inexact_reference = CollaborationLeadEvidence(
            LeadOutcomeScope.STORY_SPECIFIC, LeadEvidenceStatus.INCOMPLETE,
            (unrelated_empty, replace(_attached(), locator=f"Read ${{{path}}}")))
        with self.assertRaisesRegex(ValueError, "factual READ reference is not exact"):
            CollaborationLeadJudgmentPayload(
                LeadOutcomeKind.ANSWER, "Unsupported reference.", inexact_reference, scope)
        read_evidence = CollaborationLeadEvidence(
            LeadOutcomeScope.STORY_SPECIFIC, LeadEvidenceStatus.INCOMPLETE, (unrelated_empty, read))
        other_domain = ArtifactReviewDomain.CHARACTER_WORLD
        for invalid_scope in ( replace(scope, target_paths=(), domains=(other_domain,), contributions=(
                AuthoringContribution(other_domain, AuthoringContributionRole.CONSTRAINT_ASSESSOR),)),
            replace(scope, target_paths=("core_documents/part_outlines.md",)),
            replace(scope, schema_version=2, contributions=()),
            replace(scope, activity=AuthoringActivity.DRAFT, intent=AuthoringIntent.AUTHORING,
                    contributions=(AuthoringContribution(domain, AuthoringContributionRole.DRAFT_OWNER),)), ):
            with self.assertRaisesRegex(ValueError, "closed evidence matrix"):
                CollaborationLeadJudgmentPayload(
                    LeadOutcomeKind.ANSWER, "Unsupported scope.", read_evidence, invalid_scope)
        for status in (LeadEvidenceStatus.MISSING, LeadEvidenceStatus.EMPTY, LeadEvidenceStatus.CONFLICTING):
            previous = _evidence_for(LeadOutcomeScope.STORY_SPECIFIC, status)
            evidence = replace(previous, receipts=previous.receipts + (read,))
            self.assertEqual(evidence.factual_writing_read_facts()[-1], (read.evidence_id, path, True))
            with self.assertRaisesRegex(ValueError, "closed evidence matrix"):
                CollaborationLeadJudgmentPayload(LeadOutcomeKind.ANSWER, "Unsupported status.", evidence, scope)

    def test_missing_search_scope_and_conflict_distinctness_fail_closed(self) -> None:
        for kinds, accepted in ( ((CollaborationInputKind.WRITING_ARTIFACT,), True), ( (
                    CollaborationInputKind.WRITING_ARTIFACT, CollaborationInputKind.CONVERSATION_EVENT, ),
                True, ), ((CollaborationInputKind.CONVERSATION_EVENT,), False),
            ((CollaborationInputKind.AGENT_STATE,), False), ):
            evidence = CollaborationLeadEvidence( LeadOutcomeScope.STORY_SPECIFIC, LeadEvidenceStatus.MISSING,
                (_zero_hit(kinds=kinds),), )
            with self.subTest(kinds=kinds):
                if accepted: CollaborationLeadJudgmentPayload( LeadOutcomeKind.RECOMMENDATION,
                        "No matching canonical writing artifact was found.", evidence, )
                else:
                    with self.assertRaises(ValueError): CollaborationLeadJudgmentPayload(
                            LeadOutcomeKind.RECOMMENDATION,
                            "No matching canonical writing artifact was found.", evidence, )

        first = _attached()
        duplicate_content = replace(first, evidence_id="reference_2", ordinal=2)
        evidence = CollaborationLeadEvidence( LeadOutcomeScope.STORY_SPECIFIC, LeadEvidenceStatus.CONFLICTING,
            (first, duplicate_content), )
        with self.assertRaises(ValueError): CollaborationLeadJudgmentPayload( LeadOutcomeKind.CRITIQUE,
                "The duplicated receipt cannot prove a conflict.", evidence,
            )

        from story_writing_agents.conversations import CollaborationInputRevision
        receipt = replace(_attached("read_3_1"), acquisition=CollaborationEvidenceAcquisition.READ,
            source_kind=CollaborationInputKind.CONVERSATION_EVENT, locator="conversation/original",
            ordinal=None, action_index=3, origin_action_index=2,
            boundary_kind="records", boundary_start=9, boundary_end=9)
        existing = CollaborationInputRevision(receipt.evidence_id, receipt.source_kind, receipt.locator,
            CollaborationInputState.PRESENT, receipt.content_digest)
        evidence = CollaborationLeadEvidence(LeadOutcomeScope.STORY_SPECIFIC,
            LeadEvidenceStatus.VERIFIED, (receipt,))
        materialize = owner.materialize_lead_evidence_inputs
        self.assertEqual(materialize((), evidence), (existing,))
        reused = materialize((existing,), evidence)
        self.assertEqual(reused, (existing,))
        self.assertIs(reused[0], existing)
        self.assertEqual((receipt.boundary_kind, receipt.boundary_start, receipt.boundary_end), ("records", 9, 9))
        for field, changed in (("kind", CollaborationInputKind.AGENT_STATE), ("locator", "conversation/foreign"),
                ("state", CollaborationInputState.ABSENT), ("revision_digest", _DIGEST_A)):
            with self.assertRaisesRegex(BackendProtocolError, "conflicts with existing input: " + field):
                materialize((replace(existing, **{field: changed}),), evidence)
        for field in ("source_kind", "locator", "content_digest"):
            incomplete = replace(receipt)
            object.__setattr__(incomplete, field, None)
            with self.assertRaisesRegex(BackendProtocolError, "incomplete: " + field):
                materialize((existing,), replace(evidence, receipts=(incomplete,)))
        artifact = replace(receipt, evidence_id="artifact_read", source_kind=CollaborationInputKind.WRITING_ARTIFACT,
            locator="core_documents/global_outline.md", boundary_kind="artifact_lines",
            boundary_start=2, boundary_end=3, start_line=2, end_line=3)
        artifact_evidence = replace(evidence, receipts=(artifact,))
        artifact_input, = materialize((), artifact_evidence)
        self.assertEqual(artifact_input.locator, "${core_documents/global_outline.md}#L2-L3")
        self.assertIs(materialize((artifact_input,), artifact_evidence)[0], artifact_input)
        for field, value in (("start_line", 1), ("end_line", 4)):
            with self.assertRaisesRegex(BackendProtocolError, "conflicts with existing input: " + field):
                materialize((replace(artifact_input, **{field: value}),), artifact_evidence)
