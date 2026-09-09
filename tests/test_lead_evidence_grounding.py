"""Exhaustive acceptance coverage for runtime-grounded lead judgments."""

from __future__ import annotations

import json
import unittest
from dataclasses import replace

from story_writing_agents import StoryRuntime
from story_writing_agents.agent_context.service import ContextProviderTerminalCode
from story_writing_agents.agents import LEAD_ORCHESTRATOR, PLOT_ARCHITECT
from story_writing_agents.conversations import (
    CollaborationAppendError,
    CollaborationEvidenceAcquisition,
    CollaborationEvidenceReceipt,
    CollaborationEventKind,
    CollaborationInputKind,
    CollaborationInputState,
    CollaborationInvocationKind,
    CollaborationLeadEvidence,
    CollaborationLeadJudgmentPayload,
    CollaborationLeadOutcomeFailureCode,
    CollaborationLeadOutcomeFailurePayload,
    CollaborationNextAction,
    CollaborationProviderCallKind,
    CollaborationStepState,
    LeadEvidenceStatus,
    LeadOutcomeKind,
    LeadOutcomeScope,
    collaboration_proposal_digest,
    encode_collaboration_event,
    validate_collaboration_candidate_batch,
)
from story_writing_agents.errors import BackendProtocolError
from story_writing_agents.writing_artifacts import (
    ReferenceAuthorizationErrorCode,
    ReferenceOperationFailure,
)

from tests.test_lead_orchestration import (
    LeadOrchestrationTestCase,
    _LeadBackend,
    _LeadContextProvider,
    _decision,
    _provider_envelope,
    _specialist_outcome,
    _synthesis_outcome,
    _visible_outcome,
)
from tests.collaboration_contract_fixture import (
    LEAD,
    RUNTIME,
    USER,
    make_batch,
    make_draft,
    provider_call_drafts,
    store_batch,
)


_DIGEST_A = "sha256:" + "1" * 64
_DIGEST_B = "sha256:" + "2" * 64
_OPERATION = "lead_turn"
_RESULT = "outcome_turn"


def _attached(
    evidence_id: str = "reference_1",
    *,
    locator: str = "${core_documents/global_outline.md}",
    ordinal: int = 1,
    utf8_bytes: int = 10,
    ranged: bool = False,
) -> CollaborationEvidenceReceipt:
    return CollaborationEvidenceReceipt(
        evidence_id=evidence_id,
        acquisition=CollaborationEvidenceAcquisition.ATTACHED_REFERENCE,
        operation_id=_OPERATION,
        result_correlation_id=_RESULT,
        manifest_digest=_DIGEST_A,
        source_id=f"source_{ordinal}",
        source_kind=CollaborationInputKind.CORE_DOCUMENT,
        locator=locator,
        source_state=CollaborationInputState.PRESENT,
        revision_digest=_DIGEST_A,
        content_digest=_DIGEST_B,
        utf8_bytes=utf8_bytes,
        boundary_kind="artifact_lines" if ranged else "whole",
        boundary_start=2 if ranged else None,
        boundary_end=3 if ranged else None,
        start_line=2 if ranged else None,
        end_line=3 if ranged else None,
        ordinal=ordinal,
    )


def _core_status(
    *,
    evidence_id: str = "status_1",
    state: CollaborationInputState = CollaborationInputState.PRESENT,
    utf8_bytes: int = 10,
) -> CollaborationEvidenceReceipt:
    absent = state is CollaborationInputState.ABSENT
    return CollaborationEvidenceReceipt(
        evidence_id=evidence_id,
        acquisition=CollaborationEvidenceAcquisition.CORE_STATUS,
        operation_id=_OPERATION,
        result_correlation_id=_RESULT,
        manifest_digest=_DIGEST_A,
        source_id=f"source_{evidence_id}",
        source_kind=CollaborationInputKind.CORE_DOCUMENT,
        locator=f"core_documents/{evidence_id}.md",
        source_state=state,
        revision_digest=None if absent else _DIGEST_A,
        content_digest=None if absent else _DIGEST_B,
        utf8_bytes=0 if absent else utf8_bytes,
        ordinal=int(evidence_id.rsplit("_", 1)[1]),
    )


def _zero_hit(
    *,
    kinds: tuple[CollaborationInputKind, ...] = (
        CollaborationInputKind.WRITING_ARTIFACT,
    ),
) -> CollaborationEvidenceReceipt:
    return CollaborationEvidenceReceipt(
        evidence_id="search_1",
        acquisition=CollaborationEvidenceAcquisition.ZERO_HIT_SEARCH,
        operation_id=_OPERATION,
        result_correlation_id=_RESULT,
        manifest_digest=_DIGEST_A,
        source_state=CollaborationInputState.ABSENT,
        action_index=1,
        query_digest=_DIGEST_A,
        query_count=1,
        authorized_source_ids=("source_a",),
        authorized_source_kinds=kinds,
        authorized_source_fingerprint=_DIGEST_B,
    )


def _evidence_for(
    scope: LeadOutcomeScope,
    status: LeadEvidenceStatus,
) -> CollaborationLeadEvidence:
    if scope is LeadOutcomeScope.WORKFLOW_META:
        receipts: tuple[CollaborationEvidenceReceipt, ...] = (
            () if status is LeadEvidenceStatus.NOT_APPLICABLE else (_core_status(),)
        )
    elif status is LeadEvidenceStatus.NOT_APPLICABLE:
        receipts = ()
    elif status is LeadEvidenceStatus.VERIFIED:
        receipts = (_attached(),)
    elif status is LeadEvidenceStatus.MISSING:
        receipts = (
            _core_status(
                evidence_id="status_2",
                state=CollaborationInputState.ABSENT,
            ),
        )
    elif status is LeadEvidenceStatus.EMPTY:
        receipts = (_core_status(utf8_bytes=0),)
    elif status is LeadEvidenceStatus.INCOMPLETE:
        receipts = (_core_status(),)
    else:
        receipts = (
            _attached(),
            _attached(
                "reference_2",
                locator="${manuscript/part-01/opening.md}#L2-L3",
                ordinal=2,
                ranged=True,
            ),
        )
    return CollaborationLeadEvidence(scope, status, receipts)


class DurableLeadEvidenceMatrixTests(unittest.TestCase):
    """Pin the complete 2 x 6 x 5 closed semantic matrix."""

    def test_all_sixty_scope_status_kind_cells_are_closed_and_exact(self) -> None:
        allowed_story = {
            LeadEvidenceStatus.VERIFIED: set(LeadOutcomeKind),
            LeadEvidenceStatus.MISSING: {
                LeadOutcomeKind.QUESTION,
                LeadOutcomeKind.RECOMMENDATION,
                LeadOutcomeKind.PROPOSAL,
            },
            LeadEvidenceStatus.EMPTY: {
                LeadOutcomeKind.QUESTION,
                LeadOutcomeKind.RECOMMENDATION,
                LeadOutcomeKind.PROPOSAL,
            },
            LeadEvidenceStatus.INCOMPLETE: {
                LeadOutcomeKind.QUESTION,
                LeadOutcomeKind.CRITIQUE,
                LeadOutcomeKind.RECOMMENDATION,
                LeadOutcomeKind.PROPOSAL,
            },
            LeadEvidenceStatus.CONFLICTING: {
                LeadOutcomeKind.QUESTION,
                LeadOutcomeKind.CRITIQUE,
                LeadOutcomeKind.RECOMMENDATION,
                LeadOutcomeKind.PROPOSAL,
            },
        }
        accepted = 0
        rejected = 0
        for scope in LeadOutcomeScope:
            for status in LeadEvidenceStatus:
                evidence = _evidence_for(scope, status)
                for kind in LeadOutcomeKind:
                    expected = (
                        scope is LeadOutcomeScope.WORKFLOW_META
                        and status is LeadEvidenceStatus.NOT_APPLICABLE
                        and kind is not LeadOutcomeKind.PROPOSAL
                    ) or (
                        scope is LeadOutcomeScope.STORY_SPECIFIC
                        and kind in allowed_story.get(status, set())
                    )
                    with self.subTest(
                        scope=scope.value,
                        status=status.value,
                        kind=kind.value,
                    ):
                        if expected:
                            payload = CollaborationLeadJudgmentPayload(
                                kind,
                                "Visible lead judgment.",
                                evidence,
                            )
                            self.assertIs(payload.evidence, evidence)
                            accepted += 1
                        else:
                            with self.assertRaises(ValueError):
                                CollaborationLeadJudgmentPayload(
                                    kind,
                                    "Visible lead judgment.",
                                    evidence,
                                )
                            rejected += 1
        self.assertEqual((accepted, rejected), (23, 37))

    def test_missing_search_scope_and_conflict_distinctness_fail_closed(self) -> None:
        for kinds, accepted in (
            ((CollaborationInputKind.WRITING_ARTIFACT,), True),
            (
                (
                    CollaborationInputKind.WRITING_ARTIFACT,
                    CollaborationInputKind.CONVERSATION_EVENT,
                ),
                True,
            ),
            ((CollaborationInputKind.CONVERSATION_EVENT,), False),
            ((CollaborationInputKind.AGENT_STATE,), False),
        ):
            evidence = CollaborationLeadEvidence(
                LeadOutcomeScope.STORY_SPECIFIC,
                LeadEvidenceStatus.MISSING,
                (_zero_hit(kinds=kinds),),
            )
            with self.subTest(kinds=kinds):
                if accepted:
                    CollaborationLeadJudgmentPayload(
                        LeadOutcomeKind.RECOMMENDATION,
                        "No matching canonical writing artifact was found.",
                        evidence,
                    )
                else:
                    with self.assertRaises(ValueError):
                        CollaborationLeadJudgmentPayload(
                            LeadOutcomeKind.RECOMMENDATION,
                            "No matching canonical writing artifact was found.",
                            evidence,
                        )

        first = _attached()
        duplicate_content = replace(first, evidence_id="reference_2", ordinal=2)
        evidence = CollaborationLeadEvidence(
            LeadOutcomeScope.STORY_SPECIFIC,
            LeadEvidenceStatus.CONFLICTING,
            (first, duplicate_content),
        )
        with self.assertRaises(ValueError):
            CollaborationLeadJudgmentPayload(
                LeadOutcomeKind.CRITIQUE,
                "The duplicated receipt cannot prove a conflict.",
                evidence,
            )


def _search(query: str, kinds: list[str]):
    def respond(call, _provider):
        source_kinds = {
            "w": "writing_artifact",
            "c": "conversation",
            "d": "active_debate",
            "o": "own_agent_state",
        }
        return json.dumps(
            {
                "correlation": call.payload["c"],
                "action": {
                    "kind": "search",
                    "path_prefix": None,
                    "queries": [
                        {
                            "text": query,
                            "source_kinds": [source_kinds[kind] for kind in kinds],
                        }
                    ],
                },
            },
            separators=(",", ":"),
        )

    return respond


def _missing_outcome(*, proposal: bool = False):
    def respond(call, _provider):
        observation = call.payload["o"]
        assert observation[0] == "s" and observation[2] is not None
        value = {
            "kind": "proposal" if proposal else "recommendation",
            "message": "No matching canonical writing artifact was found.",
            "scope": "story_specific",
            "evidence_status": "missing",
            "evidence_ids": [observation[2]["evidence_id"]],
            "artifact_intent": None,
                "objective_intent": None,
            "proposal": (
                {
                    "relation": "new_step",
                    "purpose": "Confirm the missing artifact before proceeding.",
                    "assignments": [
                        {
                            "agent_id": PLOT_ARCHITECT,
                            "instruction": "Assess the request without inventing facts.",
                            "input_ids": ["user_input"],
                        }
                    ],
                }
                if proposal
                else None
            ),
        }
        return _provider_envelope(call, value)

    return respond


def _referenced_outcome(*, status: str, evidence_ids: list[str]):
    def respond(call, _provider):
        aliases = call.payload["i"]["input_aliases"]
        return _provider_envelope(
            call,
            {
                "kind": "proposal",
                "message": "The attached sources require specialist review.",
                "scope": "story_specific",
                "evidence_status": status,
                "evidence_ids": evidence_ids,
                "artifact_intent": None,
                "objective_intent": None,
                "proposal": {
                    "relation": "new_step",
                    "purpose": "Review the exact attached evidence.",
                    "assignments": [
                        {
                            "agent_id": PLOT_ARCHITECT,
                            "instruction": "Review only the attached evidence.",
                            "input_ids": [item["input_id"] for item in aliases],
                        }
                    ],
                },
            },
        )

    return respond


class RuntimeLeadEvidenceGroundingTests(LeadOrchestrationTestCase):
    async def test_writing_and_mixed_zero_hits_are_valid_but_conversation_only_is_not(
        self,
    ) -> None:
        def invalid_correction(call, _provider):
            return _provider_envelope(
                call,
                {
                    "kind": "recommendation",
                    "message": "Conversation-only absence remains insufficient.",
                    "scope": "story_specific",
                    "evidence_status": "missing",
                    "evidence_ids": ["invented"],
                    "artifact_intent": None,
                "objective_intent": None,
                    "proposal": None,
                },
            )

        cases = ((["w"], True), (["w", "c"], True), (["c"], False))
        for index, (kinds, accepted) in enumerate(cases):
            with self.subTest(kinds=kinds):
                conversation_id = self.conversation(f"Zero hit {index}")
                self.backend.enqueue(
                    _search("phrase absent from every source", kinds),
                    _missing_outcome(),
                )
                if not accepted:
                    self.backend.enqueue(invalid_correction)
                result = await self.runtime.lead_turn(
                    self.session_id,
                    conversation_id,
                    "Check for absent evidence.",
                )
                if accepted:
                    self.assertIs(result.kind, LeadOutcomeKind.RECOMMENDATION)
                    self.assertIsNotNone(result.evidence)
                    assert result.evidence is not None
                    self.assertIs(
                        result.evidence.receipts[0].acquisition,
                        CollaborationEvidenceAcquisition.ZERO_HIT_SEARCH,
                    )
                else:
                    self.assertIs(
                        result.next_action,
                        CollaborationNextAction.RETRY_LEAD_TURN,
                    )
                    self.assertEqual(
                        sum(
                            event.kind is CollaborationEventKind.LEAD_OUTCOME_FAILURE
                            for event in self.history(conversation_id)
                        ),
                        1,
                    )

    async def test_durable_zero_hit_history_contains_no_search_or_source_content(
        self,
    ) -> None:
        conversation_id = self.conversation("Content-free zero hit")
        self.backend.enqueue(
            _search("private internal search phrase", ["w"]),
            _missing_outcome(),
        )
        await self.runtime.lead_turn(
            self.session_id,
            conversation_id,
            "Check canonical artifacts.",
        )
        wire = b"\n".join(
            encode_collaboration_event(event) for event in self.history(conversation_id)
        )
        for forbidden in (
            b"private internal search phrase",
            b"Outline first line",
            b"Mara wants safety",
            str(self.workspace.root).encode("utf-8"),
            b"snippet",
            b"capability",
        ):
            self.assertNotIn(forbidden, wire)
        self.assertIn(b"query_digest", wire)

    async def test_mixed_zero_hit_survives_self_appended_history_without_mutation(
        self,
    ) -> None:
        conversation_id = self.conversation("Stable mixed zero hit")
        before = {
            path.relative_to(self.workspace.root): path.read_bytes()
            for path in self.workspace.root.rglob("*.md")
        }
        self.backend.enqueue(
            _search("phrase absent from every source", ["w", "c"]),
            _missing_outcome(proposal=True),
        )
        proposed = await self.runtime.lead_turn(
            self.session_id,
            conversation_id,
            "Confirm an absent artifact fact.",
        )
        self.backend.enqueue_for(PLOT_ARCHITECT, _specialist_outcome())
        self.backend.enqueue_for(LEAD_ORCHESTRATOR, _synthesis_outcome())
        completed = await self.runtime.decide_lead_step(
            _decision(proposed, self._confirm_decision())
        )
        self.assertIs(completed.turn.state, CollaborationStepState.COMPLETED)
        after = {
            path.relative_to(self.workspace.root): path.read_bytes()
            for path in self.workspace.root.rglob("*.md")
        }
        self.assertEqual(after, before)
        self.assertTrue((self.workspace.root / ".git").is_dir())

    @staticmethod
    def _confirm_decision():
        from story_writing_agents.conversations import (
            CollaborationConfirmationDecision,
        )

        return CollaborationConfirmationDecision.CONFIRM

    async def test_zero_hit_source_drift_blocks_confirmation_before_specialists(
        self,
    ) -> None:
        conversation_id = self.conversation("Zero hit drift")
        self.backend.enqueue(
            _search("phrase absent from every source", ["w"]),
            _missing_outcome(proposal=True),
        )
        proposed = await self.runtime.lead_turn(
            self.session_id,
            conversation_id,
            "Confirm an absent writing artifact fact.",
        )
        new_artifact = self.workspace.path("manuscript/part-01/new-evidence.md")
        new_artifact.write_text("New evidence.\n", encoding="utf-8")
        calls_before = len(self.backend.calls)
        with self.assertRaises(BackendProtocolError):
            await self.runtime.decide_lead_step(
                _decision(proposed, self._confirm_decision())
            )
        self.assertEqual(len(self.backend.calls), calls_before)

    async def test_whole_and_range_conflict_bind_proposal_and_detect_drift(
        self,
    ) -> None:
        conversation_id = self.conversation("Attached conflict")
        self.backend.enqueue(
            _referenced_outcome(
                status="conflicting",
                evidence_ids=["reference_1", "reference_2"],
            )
        )
        result = await self.runtime.lead_turn(
            self.session_id,
            conversation_id,
            (
                "Compare ${core_documents/global_outline.md} with "
                "${manuscript/part-01/opening.md}#L2-L3."
            ),
        )
        assert result.evidence is not None and result.proposal is not None
        self.assertEqual(
            tuple(item.boundary_kind for item in result.evidence.receipts),
            ("whole", "artifact_lines"),
        )
        self.assertEqual(
            tuple(
                (item.start_line, item.end_line) for item in result.evidence.receipts
            ),
            ((None, None), (2, 3)),
        )
        self.assertEqual(result.proposal.basis.evidence, result.evidence)
        self.assertEqual(
            collaboration_proposal_digest(result.proposal),
            result.proposal.proposal_digest,
        )
        self.workspace.path("manuscript/part-01/opening.md").write_text(
            "Opening changed.\n",
            encoding="utf-8",
        )
        with self.assertRaises(BackendProtocolError):
            await self.runtime.decide_lead_step(
                _decision(result, self._confirm_decision())
            )

    async def test_empty_whole_is_evidence_but_missing_and_invalid_range_fail(
        self,
    ) -> None:
        empty = self.workspace.path("manuscript/part-01/empty.md")
        empty.write_bytes(b"")
        conversation_id = self.conversation("Empty whole")
        self.backend.enqueue(
            _referenced_outcome(status="empty", evidence_ids=["reference_1"])
        )
        result = await self.runtime.lead_turn(
            self.session_id,
            conversation_id,
            "Review ${manuscript/part-01/empty.md}.",
        )
        assert result.evidence is not None
        self.assertEqual(result.evidence.receipts[0].utf8_bytes, 0)
        self.assertEqual(result.evidence.receipts[0].boundary_kind, "whole")

        failures = (
            (
                "Review ${manuscript/part-01/missing.md}.",
                ReferenceAuthorizationErrorCode.NOT_FOUND,
            ),
            (
                "Review ${manuscript/part-01/empty.md}#L1-L1.",
                ReferenceAuthorizationErrorCode.RANGE_OUT_OF_BOUNDS,
            ),
        )
        for index, (text, code) in enumerate(failures):
            with self.subTest(code=code.value):
                failed_id = self.conversation(f"Reference failure {index}")
                with self.assertRaises(ReferenceOperationFailure) as raised:
                    await self.runtime.lead_turn(
                        self.session_id,
                        failed_id,
                        text,
                    )
                self.assertIs(raised.exception.code, code)

    async def test_unknown_duplicate_reordered_and_prior_turn_ids_fail_once(
        self,
    ) -> None:
        def invalid(ids: list[str]):
            def respond(call, _provider):
                self.assertEqual(
                    [item["evidence_id"] for item in call.payload["core_statuses"]],
                    ["status_1", "status_2", "status_3", "status_4"],
                )
                return _provider_envelope(
                    call,
                    {
                        "kind": "recommendation",
                        "message": "Invalid evidence must close this turn.",
                        "scope": "story_specific",
                        "evidence_status": "missing",
                        "evidence_ids": ids,
                        "artifact_intent": None,
                "objective_intent": None,
                        "proposal": None,
                    },
                )

            return respond

        for index, ids in enumerate(
            (
                ["provider_fact"],
                ["status_2", "status_2"],
                ["status_2", "status_1"],
            )
        ):
            with self.subTest(ids=ids):
                conversation_id = self.conversation(f"Invalid evidence IDs {index}")
                self.backend.enqueue(invalid(ids))
                self.backend.enqueue(invalid(ids))
                result = await self.runtime.lead_turn(
                    self.session_id,
                    conversation_id,
                    "Reject invalid evidence IDs.",
                )
                self.assertIs(
                    result.next_action,
                    CollaborationNextAction.RETRY_LEAD_TURN,
                )
                failure_payloads = tuple(
                    event.payload
                    for event in self.history(conversation_id)
                    if type(event.payload) is CollaborationLeadOutcomeFailurePayload
                )
                self.assertEqual(len(failure_payloads), 1)
                self.assertIs(
                    failure_payloads[0].code,
                    CollaborationLeadOutcomeFailureCode.EVIDENCE_VALIDATION_REJECTED,
                )

        prior_evidence_ids: list[str] = []

        def read_prior_turn(call, provider: _LeadContextProvider):
            observation = call.payload["o"]
            handle = observation[3][0][5]
            provider.used_handles = (handle,)
            return json.dumps(
                {
                    "correlation": call.payload["c"],
                    "action": {"kind": "read", "handles": [handle]},
                },
                separators=(",", ":"),
            )

        def answer_prior_turn(call, provider: _LeadContextProvider):
            observation = call.payload["o"]
            evidence_id = observation[1][0][5]
            prior_evidence_ids.append(evidence_id)
            return _provider_envelope(
                call,
                {
                    "kind": "answer",
                    "message": "The current READ supports this answer.",
                    "scope": "story_specific",
                    "evidence_status": "verified",
                    "evidence_ids": [evidence_id],
                    "artifact_intent": None,
                "objective_intent": None,
                    "proposal": None,
                },
                used_handles=provider.used_handles,
            )

        conversation_id = self.conversation("Stale prior-turn evidence")
        self.backend.enqueue(
            _search("Outline", ["w"]),
            read_prior_turn,
            answer_prior_turn,
        )
        first = await self.runtime.lead_turn(
            self.session_id,
            conversation_id,
            "Read current evidence.",
        )
        self.assertIs(first.kind, LeadOutcomeKind.ANSWER)
        self.assertEqual(len(prior_evidence_ids), 1)

        self.backend.enqueue(invalid(prior_evidence_ids))
        self.backend.enqueue(invalid(prior_evidence_ids))
        stale = await self.runtime.lead_turn(
            self.session_id,
            conversation_id,
            "Reject evidence issued to the prior invocation.",
        )
        self.assertIs(stale.next_action, CollaborationNextAction.RETRY_LEAD_TURN)
        self.assertEqual(
            sum(
                event.kind is CollaborationEventKind.LEAD_OUTCOME_FAILURE
                for event in self.history(conversation_id)
            ),
            1,
        )

    async def test_hits_without_read_unused_read_and_truncated_search_fail_closed(
        self,
    ) -> None:
        def invalid_correction(call, _provider):
            return _provider_envelope(
                call,
                {
                    "kind": "answer",
                    "message": "Correction still claims unread evidence.",
                    "scope": "story_specific",
                    "evidence_status": "verified",
                    "evidence_ids": ["invented"],
                    "artifact_intent": None,
                "objective_intent": None,
                    "proposal": None,
                },
            )

        def answer_with_unread_id(call, _provider):
            observation = call.payload["o"]
            self.assertEqual(observation[0], "s")
            return _provider_envelope(
                call,
                {
                    "kind": "answer",
                    "message": "A SEARCH hit is not READ evidence.",
                    "scope": "story_specific",
                    "evidence_status": "verified",
                    "evidence_ids": ["search_1"],
                    "artifact_intent": None,
                "objective_intent": None,
                    "proposal": None,
                },
            )

        def read_first(call, provider: _LeadContextProvider):
            observation = call.payload["o"]
            handle = observation[3][0][5]
            provider.used_handles = (handle,)
            return json.dumps(
                {
                    "correlation": call.payload["c"],
                    "action": {"kind": "read", "handles": [handle]},
                },
                separators=(",", ":"),
            )

        def answer_without_using_read(call, _provider):
            observation = call.payload["o"]
            self.assertEqual(observation[0], "r")
            return _provider_envelope(
                call,
                {
                    "kind": "answer",
                    "message": "An unused READ cannot ground this answer.",
                    "scope": "story_specific",
                    "evidence_status": "verified",
                    "evidence_ids": [observation[1][0][5]],
                    "artifact_intent": None,
                "objective_intent": None,
                    "proposal": None,
                },
                used_handles=(),
            )

        cases = (
            (_search("Outline", ["w"]), answer_with_unread_id),
            (_search("Outline", ["w"]), read_first, answer_without_using_read),
        )
        for index, scripts in enumerate(cases):
            conversation_id = self.conversation(f"Unread evidence {index}")
            self.backend.enqueue(*scripts)
            self.backend.enqueue(invalid_correction)
            result = await self.runtime.lead_turn(
                self.session_id,
                conversation_id,
                "Do not accept unread evidence.",
            )
            self.assertIs(result.next_action, CollaborationNextAction.RETRY_LEAD_TURN)

        for index in range(12):
            self.workspace.path(f"manuscript/part-01/shared-{index}.md").write_text(
                "Shared grounding phrase.\n",
                encoding="utf-8",
            )

        def truncated_answer(call, provider):
            self.assertTrue(call.payload["o"][1])
            return answer_with_unread_id(call, provider)

        conversation_id = self.conversation("Truncated search")
        self.backend.enqueue(
            _search("Shared grounding phrase", ["w"]),
            truncated_answer,
        )
        self.backend.enqueue(invalid_correction)
        result = await self.runtime.lead_turn(
            self.session_id,
            conversation_id,
            "Reject truncated search evidence.",
        )
        self.assertIs(result.next_action, CollaborationNextAction.RETRY_LEAD_TURN)

    async def test_validation_failure_closes_once_and_restart_uses_fresh_turn(
        self,
    ) -> None:
        incomplete_id = self.conversation("Correct incomplete live shape")

        def incomplete(call, _provider):
            return _provider_envelope(
                call,
                {
                    "kind": "answer",
                    "message": "Incomplete live Antigravity answer.",
                    "scope": "workflow_meta",
                    "evidence_status": "not_applicable",
                    "artifact_intent": None,
                "objective_intent": None,
                },
            )

        self.backend.enqueue(incomplete)
        self.backend.enqueue(
            _visible_outcome("answer", "Corrected complete direct answer.")
        )
        corrected = await self.runtime.lead_turn(
            self.session_id,
            incomplete_id,
            "Correct an incomplete direct answer in this turn.",
        )
        self.assertIs(corrected.kind, LeadOutcomeKind.ANSWER)
        self.assertEqual(corrected.message, "Corrected complete direct answer.")
        self.assertEqual(len(self.backend.calls), 2)
        self.assertEqual(
            tuple(finalizer.calls for finalizer in self.backend.finalizers),
            (
                [
                    (
                        "rejected",
                        ContextProviderTerminalCode.MALFORMED_STRUCTURED_RESPONSE,
                    )
                ],
                [("accepted", None)],
            ),
        )
        self.assertFalse(
            any(
                event.kind is CollaborationEventKind.LEAD_OUTCOME_FAILURE
                for event in self.history(incomplete_id)
            )
        )

        conversation_id = self.conversation("Retry after exhausted correction")

        def invented(call, _provider):
            return _provider_envelope(
                call,
                {
                    "kind": "answer",
                    "message": "Provider-invented evidence must fail.",
                    "scope": "story_specific",
                    "evidence_status": "verified",
                    "evidence_ids": ["invented"],
                    "artifact_intent": None,
                "objective_intent": None,
                    "proposal": None,
                },
            )

        self.backend.enqueue(invented)
        self.backend.enqueue(invented)
        calls_before = len(self.backend.calls)
        failed = await self.runtime.lead_turn(
            self.session_id,
            conversation_id,
            "Reject invented evidence.",
        )
        self.assertIs(failed.next_action, CollaborationNextAction.RETRY_LEAD_TURN)
        self.assertEqual(len(self.backend.calls) - calls_before, 2)
        first_history = self.history(conversation_id)
        self.assertEqual(
            sum(
                event.kind is CollaborationEventKind.LEAD_OUTCOME_FAILURE
                for event in first_history
            ),
            1,
        )

        restart_backend = _LeadBackend()
        restart_backend.enqueue(_visible_outcome("answer", "Fresh retry answer."))
        restarted = StoryRuntime(self.workspace, restart_backend)  # type: ignore[arg-type]
        fresh = await restarted.lead_turn(
            self.session_id,
            conversation_id,
            "Retry in a fresh turn.",
        )
        self.assertIs(fresh.kind, LeadOutcomeKind.ANSWER)
        self.assertEqual(len(restart_backend.calls), 1)
        final_history = restarted.conversation_storage_services.event_store.read(
            self.session_id,
            conversation_id,
        )
        self.assertEqual(
            sum(
                event.kind is CollaborationEventKind.LEAD_OUTCOME_FAILURE
                for event in final_history
            ),
            1,
        )

    def test_failure_correlation_and_post_closure_calls_fail_closed(self) -> None:
        calls = provider_call_drafts(
            invocation_id="invocation_failure",
            call_id="call_failure",
            role=LEAD_ORCHESTRATOR,
            recipient=LEAD,
            invocation_kind=CollaborationInvocationKind.LEAD,
        )
        failure = make_draft(
            "lead_failure",
            CollaborationEventKind.LEAD_OUTCOME_FAILURE,
            RUNTIME,
            USER,
            CollaborationLeadOutcomeFailurePayload(
                CollaborationLeadOutcomeFailureCode.EVIDENCE_VALIDATION_REJECTED,
                "invocation_failure",
                "call_failure",
            ),
        )
        history = store_batch(
            (),
            make_batch((), "batch_failure", *calls, failure),
        )
        post_closure = provider_call_drafts(
            invocation_id="invocation_failure",
            call_id="call_after_failure",
            role=LEAD_ORCHESTRATOR,
            recipient=LEAD,
            invocation_kind=CollaborationInvocationKind.LEAD,
            ordinal=2,
            call_kind=CollaborationProviderCallKind.CONTINUATION,
        )[0]
        with self.assertRaises(CollaborationAppendError):
            validate_collaboration_candidate_batch(
                history,
                make_batch(history, "batch_post_closure", post_closure),
            )

        sibling_failure = replace(
            failure,
            event_id="sibling_failure",
            payload=replace(
                failure.payload,
                invocation_id="sibling_invocation",
            ),
        )
        with self.assertRaises(CollaborationAppendError):
            validate_collaboration_candidate_batch(
                (),
                make_batch((), "batch_sibling_failure", *calls, sibling_failure),
            )


if __name__ == "__main__":
    unittest.main()
