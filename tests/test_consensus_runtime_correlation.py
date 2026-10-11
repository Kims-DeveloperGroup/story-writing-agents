"""Provider-free tests for typed backend-to-consensus correlation."""

from __future__ import annotations
from story_writing_agents.specialist_routing import AuthoringIntent
from tests.test_consensus_projection import (
    AuthoringTarget, _qualify_readiness_fixture_port,
    _ADMISSION_BRIEF, _FIXTURE_DRAFT, _FIXTURE_RULING, _FIXTURE_TEXTS, _UNIT_DIRECTION,
    _USER_ASSESSMENT, _MATERIAL_ASSESSMENT, _RULING_ASSESSMENT, _unit_basis,
    _qualify_fixture_port, _capture_fixture_direction, _validate_fixture_port,
)

from dataclasses import replace
import hashlib
import json
from types import SimpleNamespace
import unittest
from unittest import mock

from story_writing_agents.agents import (
    CHARACTER_CUSTODIAN,
    EXECUTION_SPECIALIST,
    LEAD_ORCHESTRATOR,
    PLOT_ARCHITECT,
)
from story_writing_agents.consensus import (
    ConsensusAuditVerdict,
    ConsensusPhase, ConsensusInputKind,
    ConsensusPhaseInvocation,
    ConsensusPhaseOutputKind,
)
from story_writing_agents.consensus.contracts import (
    ConsensusExecutionIdentity,
    artifact_material_handle,
    candidate_material_handle,
)
from story_writing_agents.conversations import ArtifactProposalState
from story_writing_agents.models import (
    AgentReply,
    AgentReplyNextPhase,
)
from story_writing_agents.orchestrator import StoryRuntime
from story_writing_agents.reply_trace import ReplyTrace, ReplyTraceOrigin

from tests.test_collaboration_events import _reviewed_artifact_proposal


_INPUT = artifact_material_handle("manuscript/part-01/source.md", hashlib.sha256(
    _ADMISSION_BRIEF.encode(),
).hexdigest())
_CANDIDATE = candidate_material_handle("2" * 64)
_IDENTITY = ConsensusExecutionIdentity("session_a", "conversation_a")


def _invocation(
    phase: ConsensusPhase,
    agent_id: str,
    ordinal: int,
) -> ConsensusPhaseInvocation:
    return ConsensusPhaseInvocation(
        workflow_id="workflow-1",
        phase_request_id="request-1",
        proposal_id="proposal-1",
        execution_identity=_IDENTITY,
        phase=phase,
        revision_id="r0",
        input_material=_INPUT,
        candidate_material=(None if phase is ConsensusPhase.DRAFT else _CANDIDATE),
        agent_id=agent_id,
        ordinal=ordinal,

        authoring_admission=_unit_basis(_IDENTITY, "workflow-1", _INPUT, _MATERIAL_ASSESSMENT,
            input_kind=ConsensusInputKind.PROMPT
        ),
        authoring_target=AuthoringTarget.GLOBAL_OUTLINE,
        authoring_direction=_UNIT_DIRECTION,
    )


def _reply_digest(reply: AgentReply) -> str:
    value = {
        "content": reply.content,
        "deadlocked": reply.deadlocked,
        "kind": reply.kind.value,
        "next_phase": None if reply.next_phase is None else reply.next_phase.value,
        "reviewed_proposal_digest": reply.reviewed_proposal_digest,
        "reviewed_proposal_id": reply.reviewed_proposal_id,
        "revision_id": reply.revision_id,
        "verdict": None if reply.verdict is None else reply.verdict.value,
    }
    return hashlib.sha256(
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8", errors="strict")
    ).hexdigest()


class ConsensusRuntimeCorrelationTests(unittest.TestCase):
    def test_provider_trace_survives_consensus_mapping(self) -> None:
        invocation = _invocation(ConsensusPhase.DRAFT, EXECUTION_SPECIALIST, 1)
        trace = ReplyTrace(
            EXECUTION_SPECIALIST,
            "candidate",
            ("current_input", "debate_history"),
            (),
            ReplyTraceOrigin.PROVIDER,
        )
        content = "new candidate"
        reply = replace(AgentReply.candidate("r0", content), trace=trace)
        authored = candidate_material_handle(hashlib.sha256(content.encode()).hexdigest())

        result = StoryRuntime.map_consensus_provider_reply(invocation, reply, authored_material=authored,)

        self.assertEqual(result.trace, trace)

    def test_all_six_routes_map_exact_typed_results(self) -> None:
        candidate_text = "new candidate"
        authored = candidate_material_handle(hashlib.sha256(candidate_text.encode()).hexdigest())
        conflict_text = "continuity conflict"
        conflict = candidate_material_handle(hashlib.sha256(conflict_text.encode()).hexdigest())
        reviewed = _reviewed_artifact_proposal()
        routes = (
            (
                _invocation(ConsensusPhase.DRAFT, EXECUTION_SPECIALIST, 1),
                AgentReply.candidate("r0", candidate_text),
                authored,
                ConsensusPhaseOutputKind.CANDIDATE,
                None,
                None,
            ),
            (
                _invocation(ConsensusPhase.AUDIT, PLOT_ARCHITECT, 1),
                AgentReply.approval("r0"),
                None,
                ConsensusPhaseOutputKind.AUDIT,
                ConsensusAuditVerdict.APPROVE,
                None,
            ),
            (
                _invocation(ConsensusPhase.REPAIR, EXECUTION_SPECIALIST, 1),
                AgentReply.candidate("r0", candidate_text),
                authored,
                ConsensusPhaseOutputKind.CANDIDATE,
                None,
                None,
            ),
            (
                _invocation(ConsensusPhase.REAUDIT, CHARACTER_CUSTODIAN, 2),
                AgentReply.conflict("r0", conflict_text),
                conflict,
                ConsensusPhaseOutputKind.CONFLICT,
                ConsensusAuditVerdict.CONFLICT,
                None,
            ),
            (
                _invocation(ConsensusPhase.SYNTHESIS, LEAD_ORCHESTRATOR, 1),
                AgentReply.synthesis("r0", "repair", AgentReplyNextPhase.REPAIR),
                None,
                ConsensusPhaseOutputKind.SYNTHESIS,
                None,
                ConsensusPhase.REPAIR,
            ),
            (
                _invocation(ConsensusPhase.FINALIZATION, LEAD_ORCHESTRATOR, 1),
                AgentReply.finalization("r0", reviewed.proposal_id, reviewed.proposal_digest),
                None,
                ConsensusPhaseOutputKind.FINALIZATION,
                None,
                None,
            ),
        )
        for invocation, reply, material, kind, verdict, next_phase in routes:
            with self.subTest(phase=invocation.phase):
                result = StoryRuntime.map_consensus_provider_reply(invocation, reply, authored_material=material,)
                self.assertIs(result.kind, kind)
                self.assertIs(result.audit_verdict, verdict)
                self.assertIs(result.next_phase, next_phase)
                self.assertEqual(result.result_digest, _reply_digest(reply))
                self.assertEqual(
                    result.candidate_material,
                    (
                        authored
                        if invocation.phase
                        in {ConsensusPhase.DRAFT, ConsensusPhase.REPAIR}
                        else _CANDIDATE
                    ),
                )

    def test_synthesis_next_phase_deadlock_and_mismatches_are_fail_closed(self) -> None:
        invocation = _invocation(ConsensusPhase.SYNTHESIS, LEAD_ORCHESTRATOR, 1)
        finalization = AgentReply.synthesis("r0", "ready", AgentReplyNextPhase.FINALIZATION)
        mapped = StoryRuntime.map_consensus_provider_reply(invocation, finalization)
        self.assertIs(mapped.next_phase, ConsensusPhase.FINALIZATION)

        deadlock = AgentReply.deadlock("r0", "three audits cannot be reconciled")
        mapped_deadlock = StoryRuntime.map_consensus_provider_reply(invocation, deadlock)
        self.assertIs(mapped_deadlock.kind, ConsensusPhaseOutputKind.DEADLOCK)
        self.assertTrue(mapped_deadlock.deadlocked)
        self.assertIs(mapped_deadlock.candidate_material, _CANDIDATE)

        invalid = (
            (invocation, AgentReply.candidate("r0", "free form"), None),
            (invocation, replace(finalization, revision_id="r1"), None),
            (replace(invocation, agent_id=PLOT_ARCHITECT), finalization, None),
            (replace(invocation, ordinal=2), finalization, None),
            (invocation, finalization, _CANDIDATE),
            (
                _invocation(ConsensusPhase.DRAFT, EXECUTION_SPECIALIST, 1),
                AgentReply.candidate("r0", "different bytes"),
                _CANDIDATE,
            ),
        )
        for candidate_invocation, reply, material in invalid:
            with self.subTest(invocation=candidate_invocation, reply=reply):
                with self.assertRaises(ValueError):
                    StoryRuntime.map_consensus_provider_reply(
                        candidate_invocation,
                        reply,
                        authored_material=material,
                    )

    def test_runtime_fallback_maps_draft_failure_and_later_deadlocks(self) -> None:
        fallback = AgentReply.deadlock("r0", "provider response rejected twice")

        draft = StoryRuntime.map_consensus_provider_reply(
            _invocation(ConsensusPhase.DRAFT, EXECUTION_SPECIALIST, 1),
            fallback,
        )
        self.assertIs(draft.kind, ConsensusPhaseOutputKind.FAILURE)
        self.assertIsNone(draft.candidate_material)

        for phase, agent_id in (
            (ConsensusPhase.REPAIR, EXECUTION_SPECIALIST),
            (ConsensusPhase.SYNTHESIS, LEAD_ORCHESTRATOR),
            (ConsensusPhase.FINALIZATION, LEAD_ORCHESTRATOR),
        ):
            with self.subTest(phase=phase):
                result = StoryRuntime.map_consensus_provider_reply(_invocation(phase, agent_id, 1), fallback,)
                self.assertIs(result.kind, ConsensusPhaseOutputKind.DEADLOCK)
                self.assertIs(result.candidate_material, _CANDIDATE)
                self.assertTrue(result.deadlocked)
                self.assertIsNone(fallback.reviewed_proposal_id)
                self.assertIsNone(fallback.reviewed_proposal_digest)

        with self.assertRaisesRegex(ValueError, "cannot retain authored material"):
            StoryRuntime.map_consensus_provider_reply(
                _invocation(ConsensusPhase.REPAIR, EXECUTION_SPECIALIST, 1),
                fallback,
                authored_material=_CANDIDATE,
            )

    def test_finalization_handoff_exposes_review_only_lineage(self) -> None:
        reviewed = _reviewed_artifact_proposal()
        invocation = _invocation(ConsensusPhase.FINALIZATION, LEAD_ORCHESTRATOR, 1)
        reply = AgentReply.finalization("r0", reviewed.proposal_id, reviewed.proposal_digest)
        result = StoryRuntime.map_consensus_provider_reply(invocation, reply)
        projection = SimpleNamespace(current_artifact_state=ArtifactProposalState.REVIEWED,
            current_reviewed_artifact_proposal=reviewed, current_artifact_application_request=None,
            current_artifact_application_result=None)
        event_store = mock.Mock()
        event_store.read_context.return_value = projection
        runtime = SimpleNamespace(conversation_storage_services=SimpleNamespace(event_store=event_store))
        handoff = StoryRuntime.consensus_artifact_review_handoff(runtime, invocation, reply, result)
        self.assertIs(handoff.reviewed_proposal, reviewed)
        self.assertFalse(handoff.reviewed_proposal.artifact_write_occurred)
        self.assertFalse(handoff.reviewed_proposal.apply_authority_granted)
        self.assertFalse(handoff.reviewed_proposal.git_action_occurred)
        event_store.read_context.assert_called_once_with("session_a", "conversation_a")
        for proposal_id, digest in (("different-proposal", reviewed.proposal_digest),
                                    (reviewed.proposal_id, "sha256:" + "f" * 64)):
            stale = AgentReply.finalization("r0", proposal_id, digest)
            stale_result = StoryRuntime.map_consensus_provider_reply(invocation, stale)
            with self.assertRaises(ValueError):
                StoryRuntime.consensus_artifact_review_handoff(runtime, invocation, stale, stale_result)
        projection.current_artifact_application_request = object()
        with self.assertRaises(ValueError):
            StoryRuntime.consensus_artifact_review_handoff(runtime, invocation, reply, result)


if __name__ == "__main__":
    unittest.main()
