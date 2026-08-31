"""Immutable schema-v2 collaboration values and canonical wire contracts."""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import inspect
import json
import pickle
import subprocess
import sys
import tempfile
import unittest
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import story_writing_agents
import story_writing_agents.conversations as conversations
import story_writing_agents.conversations.context as context_owner
import story_writing_agents.conversations.events as events_owner
from story_writing_agents.agents import (
    CHARACTER_CUSTODIAN,
    LEAD_ORCHESTRATOR,
    PLOT_ARCHITECT,
)
from story_writing_agents.conversations import (
    COLLABORATION_EVENT_SCHEMA_VERSION,
    COLLABORATION_PHYSICAL_CALL_LIMIT,
    COLLABORATION_ROUND_LIMIT,
    COLLABORATION_VISIBLE_TEXT_MAX_UTF8_BYTES,
    CollaborationActorKind,
    CollaborationAppendError,
    CollaborationAppendFailure,
    CollaborationAppendReceipt,
    CollaborationAppendStatus,
    CollaborationAssignmentDispatchPayload,
    CollaborationAssignmentOutcome,
    CollaborationAssignmentOutcomeKind,
    CollaborationBudget,
    CollaborationConfirmationDecision,
    CollaborationConfirmationPayload,
    CollaborationDirection,
    CollaborationEventKind,
    CollaborationExecutionOutcomeKind,
    CollaborationInputKind,
    CollaborationInputState,
    CollaborationInterruptionPayload,
    CollaborationMessagePayload,
    CollaborationProposal,
    CollaborationSideEffect,
    CollaborationSpecialistReplyPayload,
    CollaborationStepKind,
    CollaborationStepState,
    CollaborationStepStatePayload,
    CollaborationSynthesisOutcomeKind,
    CollaborationTerminalOutcomePayload,
    CollaborationTerminalScope,
    collaboration_batch_digest,
    collaboration_proposal_digest,
    decode_collaboration_batch,
    decode_collaboration_event,
    decode_collaboration_event_draft,
    decode_collaboration_proposal,
    encode_collaboration_batch,
    encode_collaboration_event,
    encode_collaboration_event_draft,
    encode_collaboration_proposal,
)

from tests.collaboration_contract_fixture import (
    ARTIFACT,
    ATTEMPT_ID,
    CHARACTER,
    COLLABORATION_CONTEXT_EXPORTS,
    COLLABORATION_EVENT_EXPORTS,
    DIGEST_A,
    DIGEST_B,
    GIT,
    LEAD,
    PLOT,
    REQUEST_EVENT_ID,
    REQUEST_ID,
    RUNTIME,
    USER,
    awaiting_history,
    batch_from_history,
    executed_history,
    make_draft,
    make_proposal,
    replace_proposal,
    result_batch,
    running_history,
)


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


class CollaborationValueContractTests(unittest.TestCase):
    def test_constants_and_closed_enums_are_exact(self) -> None:
        self.assertEqual(COLLABORATION_EVENT_SCHEMA_VERSION, 2)
        self.assertEqual(COLLABORATION_ROUND_LIMIT, 2)
        self.assertEqual(COLLABORATION_PHYSICAL_CALL_LIMIT, 12)
        self.assertEqual(COLLABORATION_VISIBLE_TEXT_MAX_UTF8_BYTES, 32_768)
        expected = {
            CollaborationEventKind: (
                "message",
                "assignment_dispatch",
                "specialist_reply",
                "step_state",
                "confirmation",
                "execution_result",
                "interruption",
                "terminal_outcome",
            ),
            CollaborationActorKind: (
                "user",
                "lead",
                "specialist",
                "runtime",
                "artifact",
                "git",
            ),
            CollaborationDirection: (
                "user_to_lead",
                "user_to_runtime",
                "lead_to_user",
                "lead_to_specialist",
                "specialist_to_lead",
                "specialist_to_user",
                "runtime_to_user",
                "runtime_to_lead",
                "runtime_to_specialist",
                "artifact_to_user",
                "artifact_to_lead",
                "git_to_user",
                "git_to_lead",
            ),
            CollaborationStepKind: (
                "lead_specialist_consultation",
                "specialist_advisory",
                "artifact_mutation",
                "git_operation",
            ),
            CollaborationSideEffect: (
                "provider_invocation",
                "artifact_mutation",
                "git_index_mutation",
                "git_commit",
                "git_remote_mutation",
                "git_merge",
            ),
            CollaborationInputKind: (
                "user_input",
                "conversation_event",
                "core_document",
                "manuscript",
                "agent_state",
                "writing_artifact",
            ),
            CollaborationInputState: ("present", "absent"),
            CollaborationStepState: (
                "awaiting_confirmation",
                "reconfirmation_required",
                "confirmed",
                "running",
                "completed",
                "rejected",
                "superseded",
                "interrupted",
                "failed",
                "outcome_unknown",
                "recovery_required",
            ),
            CollaborationConfirmationDecision: ("confirm", "reject", "revise"),
            CollaborationAssignmentOutcomeKind: (
                "succeeded",
                "failed",
                "cancelled",
            ),
            CollaborationSynthesisOutcomeKind: (
                "not_attempted",
                "succeeded",
                "failed",
                "cancelled",
            ),
            CollaborationExecutionOutcomeKind: (
                "succeeded",
                "failed",
                "cancelled",
            ),
            CollaborationTerminalScope: ("assignment", "synthesis"),
            CollaborationAppendStatus: ("APPENDED", "ALREADY_COMMITTED"),
            CollaborationAppendFailure: (
                "stale_sequence",
                "batch_id_conflict",
                "event_id_conflict",
                "invalid_transition",
                "corrupt_history",
            ),
        }
        for enum_type, values in expected.items():
            with self.subTest(enum=enum_type.__name__):
                self.assertEqual(tuple(item.value for item in enum_type), values)

    def test_all_schema_values_have_exact_fields_frozen_slots_and_nested_tuples(
        self,
    ) -> None:
        history, proposal, result = executed_history()
        batch = batch_from_history(history, "batch_result")
        values_and_fields = (
            (USER, ("kind", "agent_id")),
            (
                proposal.basis.inputs[0],
                (
                    "input_id",
                    "kind",
                    "locator",
                    "state",
                    "revision_digest",
                    "start_line",
                    "end_line",
                ),
            ),
            (proposal.basis.agent_profiles[0], ("agent_id", "profile_fingerprint")),
            (
                proposal.basis,
                (
                    "route",
                    "route_fingerprint",
                    "agent_profiles",
                    "context_policy_fingerprint",
                    "budget_policy_fingerprint",
                    "inputs",
                ),
            ),
            (
                proposal.assignments[0],
                (
                    "assignment_id",
                    "specialist_id",
                    "round",
                    "instruction",
                    "input_ids",
                    "max_physical_calls",
                ),
            ),
            (
                proposal.budget,
                (
                    "round_limit",
                    "physical_call_limit",
                    "rounds_spent",
                    "rounds_added",
                    "physical_calls_spent",
                    "max_additional_physical_calls",
                    "synthesis_max_physical_calls",
                ),
            ),
            (
                proposal,
                (
                    "step_id",
                    "proposal_id",
                    "proposal_revision",
                    "step_kind",
                    "purpose",
                    "assignments",
                    "user_impact",
                    "side_effects",
                    "budget",
                    "basis",
                    "proposal_digest",
                ),
            ),
            (
                result.assignment_outcomes[0],
                (
                    "assignment_id",
                    "outcome",
                    "submitted_physical_calls",
                    "reply_event_id",
                ),
            ),
            (
                result,
                (
                    "proposal_id",
                    "proposal_revision",
                    "proposal_digest",
                    "execution_attempt_id",
                    "assignment_outcomes",
                    "synthesis_outcome",
                    "synthesis_submitted_physical_calls",
                    "synthesis_result_event_id",
                    "overall_outcome",
                    "visible_exchange_event_ids",
                ),
            ),
            (
                batch.events[0],
                (
                    "schema_version",
                    "event_id",
                    "session_id",
                    "conversation_id",
                    "turn_id",
                    "step_id",
                    "kind",
                    "sender",
                    "recipient",
                    "payload",
                ),
            ),
            (
                history[-1],
                (
                    "schema_version",
                    "event_id",
                    "session_id",
                    "conversation_id",
                    "turn_id",
                    "step_id",
                    "sequence",
                    "timestamp",
                    "batch_id",
                    "batch_ordinal",
                    "batch_size",
                    "batch_digest",
                    "kind",
                    "sender",
                    "recipient",
                    "payload",
                ),
            ),
            (batch, ("batch_id", "expected_last_sequence", "events")),
        )
        for value, expected_fields in values_and_fields:
            with self.subTest(value=type(value).__name__):
                self.assertEqual(
                    tuple(field.name for field in dataclasses.fields(value)),
                    expected_fields,
                )
                self.assertFalse(hasattr(value, "__dict__"))
                with self.assertRaises(FrozenInstanceError):
                    setattr(value, expected_fields[0], None)
        self.assertEqual(
            proposal.participants,
            (PLOT_ARCHITECT, CHARACTER_CUSTODIAN),
        )
        self.assertNotIn("participants", proposal.__dataclass_fields__)
        for nested in (
            proposal.assignments,
            proposal.side_effects,
            proposal.basis.agent_profiles,
            proposal.basis.inputs,
            result.assignment_outcomes,
            result.visible_exchange_event_ids,
            batch.events,
        ):
            self.assertIs(type(nested), tuple)

    def test_actor_direction_matrix_is_closed_and_exact(self) -> None:
        advisory = make_proposal(step_kind=CollaborationStepKind.SPECIALIST_ADVISORY)
        running, _ = running_history(advisory)
        result_candidate, result = result_batch(running, advisory)
        del result_candidate
        assignment = advisory.assignments[0]
        payloads = {
            CollaborationEventKind.MESSAGE: CollaborationMessagePayload("Visible."),
            CollaborationEventKind.ASSIGNMENT_DISPATCH: (
                CollaborationAssignmentDispatchPayload(
                    advisory.proposal_id,
                    advisory.proposal_revision,
                    advisory.proposal_digest,
                    ATTEMPT_ID,
                    assignment,
                )
            ),
            CollaborationEventKind.SPECIALIST_REPLY: (
                CollaborationSpecialistReplyPayload(
                    advisory.proposal_id,
                    advisory.proposal_revision,
                    advisory.proposal_digest,
                    ATTEMPT_ID,
                    assignment.assignment_id,
                    f"dispatch_{assignment.assignment_id}",
                    "Visible reply.",
                )
            ),
            CollaborationEventKind.STEP_STATE: CollaborationStepStatePayload(
                CollaborationStepState.AWAITING_CONFIRMATION,
                advisory.proposal_id,
                advisory.proposal_revision,
                advisory.proposal_digest,
                confirmation_request_id=REQUEST_ID,
            ),
            CollaborationEventKind.CONFIRMATION: CollaborationConfirmationPayload(
                REQUEST_ID,
                REQUEST_EVENT_ID,
                advisory.proposal_id,
                advisory.proposal_revision,
                advisory.proposal_digest,
                CollaborationConfirmationDecision.CONFIRM,
            ),
            CollaborationEventKind.EXECUTION_RESULT: result,
            CollaborationEventKind.INTERRUPTION: CollaborationInterruptionPayload(
                ATTEMPT_ID,
                "Interrupted.",
            ),
            CollaborationEventKind.TERMINAL_OUTCOME: (
                CollaborationTerminalOutcomePayload(
                    ATTEMPT_ID,
                    CollaborationTerminalScope.ASSIGNMENT,
                    CollaborationAssignmentOutcomeKind.SUCCEEDED,
                    1,
                    assignment.assignment_id,
                )
            ),
        }
        actors = {
            CollaborationActorKind.USER: USER,
            CollaborationActorKind.LEAD: LEAD,
            CollaborationActorKind.SPECIALIST: PLOT,
            CollaborationActorKind.RUNTIME: RUNTIME,
            CollaborationActorKind.ARTIFACT: ARTIFACT,
            CollaborationActorKind.GIT: GIT,
        }
        allowed = {
            CollaborationEventKind.MESSAGE: {
                (CollaborationActorKind.USER, CollaborationActorKind.LEAD),
                (CollaborationActorKind.LEAD, CollaborationActorKind.USER),
                (CollaborationActorKind.RUNTIME, CollaborationActorKind.USER),
                (CollaborationActorKind.ARTIFACT, CollaborationActorKind.USER),
                (CollaborationActorKind.ARTIFACT, CollaborationActorKind.LEAD),
                (CollaborationActorKind.GIT, CollaborationActorKind.USER),
                (CollaborationActorKind.GIT, CollaborationActorKind.LEAD),
            },
            CollaborationEventKind.ASSIGNMENT_DISPATCH: {
                (CollaborationActorKind.LEAD, CollaborationActorKind.SPECIALIST),
                (CollaborationActorKind.RUNTIME, CollaborationActorKind.SPECIALIST),
            },
            CollaborationEventKind.SPECIALIST_REPLY: {
                (CollaborationActorKind.SPECIALIST, CollaborationActorKind.LEAD),
                (CollaborationActorKind.SPECIALIST, CollaborationActorKind.USER),
            },
            CollaborationEventKind.STEP_STATE: {
                (CollaborationActorKind.RUNTIME, CollaborationActorKind.USER),
                (CollaborationActorKind.RUNTIME, CollaborationActorKind.LEAD),
            },
            CollaborationEventKind.CONFIRMATION: {
                (CollaborationActorKind.USER, CollaborationActorKind.RUNTIME),
            },
            CollaborationEventKind.EXECUTION_RESULT: {
                (CollaborationActorKind.RUNTIME, CollaborationActorKind.USER),
                (CollaborationActorKind.RUNTIME, CollaborationActorKind.LEAD),
            },
            CollaborationEventKind.INTERRUPTION: {
                (CollaborationActorKind.USER, CollaborationActorKind.RUNTIME),
                (CollaborationActorKind.RUNTIME, CollaborationActorKind.USER),
            },
            CollaborationEventKind.TERMINAL_OUTCOME: {
                (CollaborationActorKind.RUNTIME, CollaborationActorKind.USER),
                (CollaborationActorKind.RUNTIME, CollaborationActorKind.LEAD),
            },
        }
        for kind, payload in payloads.items():
            for sender_kind, sender in actors.items():
                for recipient_kind, recipient in actors.items():
                    pair = (sender_kind, recipient_kind)
                    with self.subTest(kind=kind.value, pair=pair):
                        if pair in allowed[kind]:
                            event = make_draft(
                                "event_a",
                                kind,
                                sender,
                                recipient,
                                payload,
                            )
                            self.assertEqual(
                                event.direction.value,
                                f"{sender_kind.value}_to_{recipient_kind.value}",
                            )
                        else:
                            with self.assertRaises(ValueError):
                                make_draft(
                                    "event_a",
                                    kind,
                                    sender,
                                    recipient,
                                    payload,
                                )

    def test_proposal_closure_budget_and_advisory_invariants_fail_closed(self) -> None:
        proposal = make_proposal()
        with self.assertRaises(ValueError):
            make_proposal(assignments=(proposal.assignments[0],) * 2)
        with self.assertRaises(ValueError):
            make_proposal(
                assignments=(
                    replace(proposal.assignments[0], input_ids=("unknown",)),
                    proposal.assignments[1],
                )
            )
        with self.assertRaises(ValueError):
            make_proposal(profile_ids=(PLOT_ARCHITECT, CHARACTER_CUSTODIAN))
        with self.assertRaises(ValueError):
            make_proposal(profile_ids=(PLOT_ARCHITECT, LEAD_ORCHESTRATOR))
        with self.assertRaises(ValueError):
            CollaborationBudget(2, 12, 2, 1, 0, 1, 0)
        with self.assertRaises(ValueError):
            CollaborationBudget(2, 12, 0, 1, 12, 1, 0)
        advisory = make_proposal(step_kind=CollaborationStepKind.SPECIALIST_ADVISORY)
        self.assertEqual(advisory.budget.synthesis_max_physical_calls, 0)
        self.assertNotIn(
            LEAD_ORCHESTRATOR,
            tuple(profile.agent_id for profile in advisory.basis.agent_profiles),
        )
        with self.assertRaises(ValueError):
            make_proposal(
                step_kind=CollaborationStepKind.SPECIALIST_ADVISORY,
                profile_ids=(PLOT_ARCHITECT, LEAD_ORCHESTRATOR),
            )
        with self.assertRaises(ValueError):
            make_proposal(
                step_kind=CollaborationStepKind.SPECIALIST_ADVISORY,
                side_effects=(CollaborationSideEffect.ARTIFACT_MUTATION,),
            )
        dispatch = CollaborationAssignmentDispatchPayload(
            advisory.proposal_id,
            advisory.proposal_revision,
            advisory.proposal_digest,
            ATTEMPT_ID,
            advisory.assignments[0],
        )
        with self.assertRaises(ValueError):
            make_draft(
                "dispatch",
                CollaborationEventKind.ASSIGNMENT_DISPATCH,
                RUNTIME,
                CHARACTER,
                dispatch,
            )

    def test_result_value_requires_exact_terminal_facts(self) -> None:
        _, _, result = executed_history()
        successful = result.assignment_outcomes[0]
        with self.assertRaises(ValueError):
            replace(successful, reply_event_id=None)
        with self.assertRaises(ValueError):
            CollaborationAssignmentOutcome(
                successful.assignment_id,
                CollaborationAssignmentOutcomeKind.FAILED,
                1,
                successful.reply_event_id,
            )
        with self.assertRaises(ValueError):
            replace(
                result,
                assignment_outcomes=(result.assignment_outcomes[0],) * 2,
            )
        with self.assertRaises(ValueError):
            replace(
                result,
                synthesis_outcome=CollaborationSynthesisOutcomeKind.NOT_ATTEMPTED,
                synthesis_submitted_physical_calls=1,
                synthesis_result_event_id=None,
            )
        failed = CollaborationAssignmentOutcome(
            result.assignment_outcomes[0].assignment_id,
            CollaborationAssignmentOutcomeKind.FAILED,
            1,
            None,
        )
        with self.assertRaises(ValueError):
            replace(
                result,
                assignment_outcomes=(failed, result.assignment_outcomes[1]),
                overall_outcome=CollaborationExecutionOutcomeKind.SUCCEEDED,
            )

    def test_visible_text_uses_strict_utf8_byte_bounds_and_contains_no_metadata(
        self,
    ) -> None:
        exact = "é" * (COLLABORATION_VISIBLE_TEXT_MAX_UTF8_BYTES // 2)
        self.assertEqual(CollaborationMessagePayload(exact).content, exact)
        for invalid in (exact + "é", "\ud800", ""):
            with self.subTest(invalid_length=len(invalid)):
                with self.assertRaises(ValueError):
                    CollaborationMessagePayload(invalid)
        proposal = make_proposal()
        self.assertEqual(
            set(proposal.__dataclass_fields__),
            {
                "step_id",
                "proposal_id",
                "proposal_revision",
                "step_kind",
                "purpose",
                "assignments",
                "user_impact",
                "side_effects",
                "budget",
                "basis",
                "proposal_digest",
            },
        )
        wire = encode_collaboration_proposal(proposal)
        for forbidden in (
            b"hidden_reasoning",
            b"chain_of_thought",
            b"private_metadata",
            b"authorization_capability",
        ):
            self.assertNotIn(forbidden, wire)
        self.assertIn(b"${core_documents/outline.md}", wire)
        self.assertNotIn(b"actual outline file bytes", wire)


class CollaborationWireContractTests(unittest.TestCase):
    def test_all_wire_values_have_stable_golden_hashes_and_round_trip(self) -> None:
        history, proposal = awaiting_history()
        batch = batch_from_history(history, "batch_proposal")
        cases = (
            (
                encode_collaboration_proposal,
                decode_collaboration_proposal,
                proposal,
                2240,
                "d3b6ef0502f3f1204b12639c13fa39f852f5129032f3a2ceb90d27da9533cb9a",
            ),
            (
                encode_collaboration_event_draft,
                decode_collaboration_event_draft,
                batch.events[0],
                2521,
                "7db650b65a1bae5760c4c69ce0e74a47f1ff4d3f31105b02fe08ffd763416bc3",
            ),
            (
                encode_collaboration_event,
                decode_collaboration_event,
                history[0],
                2724,
                "0789c3886839ded790ab1dc499ae02730f3ea17cd0aa611f593330244307ab07",
            ),
            (
                encode_collaboration_batch,
                decode_collaboration_batch,
                batch,
                3174,
                "9ba571df05b451f0482c84e3b5a378a22af836397f38b86a11a978fffdc8bc3b",
            ),
        )
        for encoder, decoder, value, size, digest in cases:
            with self.subTest(encoder=encoder.__name__):
                wire = encoder(value)  # type: ignore[arg-type]
                self.assertEqual(len(wire), size)
                self.assertEqual(hashlib.sha256(wire).hexdigest(), digest)
                self.assertEqual(decoder(wire), value)
                self.assertEqual(encoder(decoder(wire)), wire)
                self.assertFalse(wire.startswith(b"\xef\xbb\xbf"))
                self.assertFalse(wire.endswith(b"\n"))
                self.assertEqual(_canonical_json(json.loads(wire)), wire)
        self.assertEqual(collaboration_proposal_digest(proposal), proposal.proposal_digest)
        self.assertEqual(collaboration_batch_digest(batch), batch.batch_digest)

    def test_every_proposal_dimension_and_order_changes_the_digest(self) -> None:
        base = make_proposal(
            side_effects=(
                CollaborationSideEffect.PROVIDER_INVOCATION,
                CollaborationSideEffect.ARTIFACT_MUTATION,
            )
        )
        inputs = base.basis.inputs
        assignments = base.assignments
        variants = {
            "step_id": replace_proposal(base, step_id="step_b"),
            "proposal_id": replace_proposal(base, proposal_id="proposal_b"),
            "revision": replace_proposal(base, proposal_revision=2),
            "step_kind": replace_proposal(
                base,
                step_kind=CollaborationStepKind.GIT_OPERATION,
            ),
            "purpose": replace_proposal(base, purpose="Different visible purpose."),
            "assignment_order": replace_proposal(
                base,
                assignments=tuple(reversed(assignments)),
            ),
            "assignment_instruction": replace_proposal(
                base,
                assignments=(
                    replace(assignments[0], instruction="Different instruction."),
                    assignments[1],
                ),
            ),
            "assignment_calls": make_proposal(
                assignments=(
                    replace(assignments[0], max_physical_calls=3),
                    assignments[1],
                ),
                inputs=inputs,
                side_effects=base.side_effects,
            ),
            "impact": replace_proposal(base, user_impact="Different user impact."),
            "side_effect_order": replace_proposal(
                base,
                side_effects=tuple(reversed(base.side_effects)),
            ),
            "physical_spent": make_proposal(
                physical_calls_spent=1,
                side_effects=base.side_effects,
            ),
            "route": make_proposal(route="different_route", side_effects=base.side_effects),
            "route_fingerprint": make_proposal(
                route_fingerprint=DIGEST_B,
                side_effects=base.side_effects,
            ),
            "profile_fingerprint": replace_proposal(
                base,
                basis=replace(
                    base.basis,
                    agent_profiles=(
                        replace(base.basis.agent_profiles[0], profile_fingerprint=DIGEST_B),
                        *base.basis.agent_profiles[1:],
                    ),
                ),
            ),
            "context_policy": make_proposal(
                context_policy_fingerprint=DIGEST_B,
                side_effects=base.side_effects,
            ),
            "budget_policy": make_proposal(
                budget_policy_fingerprint=DIGEST_B,
                side_effects=base.side_effects,
            ),
            "input_order": replace_proposal(
                base,
                basis=replace(base.basis, inputs=tuple(reversed(inputs))),
            ),
            "input_locator": replace_proposal(
                base,
                basis=replace(
                    base.basis,
                    inputs=(
                        replace(inputs[0], locator="$${core_documents/outline.md}:4-12"),
                        inputs[1],
                    ),
                ),
            ),
            "input_state": replace_proposal(
                base,
                basis=replace(
                    base.basis,
                    inputs=(
                        replace(inputs[0], state=CollaborationInputState.ABSENT),
                        inputs[1],
                    ),
                ),
            ),
            "input_revision": replace_proposal(
                base,
                basis=replace(
                    base.basis,
                    inputs=(
                        replace(inputs[0], revision_digest=DIGEST_B),
                        inputs[1],
                    ),
                ),
            ),
            "input_range": replace_proposal(
                base,
                basis=replace(
                    base.basis,
                    inputs=(replace(inputs[0], start_line=4), inputs[1]),
                ),
            ),
        }
        self.assertEqual(len(set(variants)), len(variants))
        for dimension, variant in variants.items():
            with self.subTest(dimension=dimension):
                self.assertNotEqual(variant.proposal_digest, base.proposal_digest)
                self.assertNotEqual(
                    encode_collaboration_proposal(variant),
                    encode_collaboration_proposal(base),
                )

    def test_duplicate_extra_missing_nonfinite_and_noncanonical_json_are_rejected(
        self,
    ) -> None:
        wire = encode_collaboration_proposal(make_proposal())
        duplicate = wire.replace(
            b'{"proposal":',
            b'{"proposal":null,"proposal":',
            1,
        )
        top = json.loads(wire)
        candidates = [
            duplicate,
            b"\xef\xbb\xbf" + wire,
            b"\xff",
            wire + b"\n",
            wire.replace(b'":', b'": ', 1),
            b'{"proposal":NaN,"schema_version":2}',
            b'{"proposal":Infinity,"schema_version":2}',
        ]
        extra = copy.deepcopy(top)
        extra["extra"] = "private-sentinel"
        candidates.append(_canonical_json(extra))
        missing = copy.deepcopy(top)
        del missing["proposal"]
        candidates.append(_canonical_json(missing))
        nested_extra = copy.deepcopy(top)
        nested_extra["proposal"]["basis"]["extra"] = "private-sentinel"
        candidates.append(_canonical_json(nested_extra))
        v1 = copy.deepcopy(top)
        v1["schema_version"] = 1
        candidates.append(_canonical_json(v1))
        for index, candidate in enumerate(candidates):
            with self.subTest(index=index):
                with self.assertRaises(ValueError) as raised:
                    decode_collaboration_proposal(candidate)
                self.assertNotIn("private-sentinel", str(raised.exception))
        with self.assertRaises(TypeError):
            decode_collaboration_proposal("{}")  # type: ignore[arg-type]

    def test_unicode_is_not_normalized_and_tampered_digest_is_rejected(self) -> None:
        composed = replace_proposal(make_proposal(), purpose="Café review")
        decomposed = replace_proposal(make_proposal(), purpose="Cafe\u0301 review")
        self.assertNotEqual(composed.purpose, decomposed.purpose)
        self.assertNotEqual(composed.proposal_digest, decomposed.proposal_digest)
        self.assertNotEqual(
            encode_collaboration_proposal(composed),
            encode_collaboration_proposal(decomposed),
        )
        top = json.loads(encode_collaboration_proposal(composed))
        top["proposal"]["purpose"] = "Tampered"
        with self.assertRaises(ValueError):
            decode_collaboration_proposal(_canonical_json(top))


class CollaborationPackageBoundaryTests(unittest.TestCase):
    def test_leaf_and_facade_exports_identity_metadata_pickle_and_root_omission(
        self,
    ) -> None:
        self.assertEqual(tuple(events_owner.__all__), COLLABORATION_EVENT_EXPORTS)
        self.assertEqual(tuple(context_owner.__all__), COLLABORATION_CONTEXT_EXPORTS)
        self.assertEqual(len(conversations.__all__), 74)
        self.assertNotIn("__getattr__", vars(events_owner))
        self.assertNotIn("__getattr__", vars(context_owner))
        self.assertNotIn("__getattr__", vars(conversations))
        collaboration_names = {
            name
            for name in conversations.__all__
            if name.startswith("Collaboration") or name.startswith("COLLABORATION_")
        }
        self.assertEqual(len(collaboration_names), 44)
        for owner in (events_owner, context_owner):
            for name in owner.__all__:
                if name.startswith("Collaboration") or name.startswith(
                    "COLLABORATION_"
                ):
                    with self.subTest(owner=owner.__name__, name=name):
                        self.assertIs(getattr(owner, name), getattr(conversations, name))
        callable_names = {
            name
            for name in conversations.__all__
            if name.startswith("Collaboration")
            or name.startswith("collaboration_")
            or name.startswith("encode_collaboration_")
            or name.startswith("decode_collaboration_")
            or name.startswith("project_collaboration_")
            or name.startswith("validate_collaboration_")
        }
        callable_names.discard("CollaborationPayload")
        for name in callable_names:
            with self.subTest(metadata=name):
                self.assertEqual(
                    getattr(conversations, name).__module__,
                    "story_writing_agents.conversations",
                )
        root_names = {
            name for name in conversations.__all__ if name.startswith("Collaboration")
        }
        root_names |= {
            name
            for name in conversations.__all__
            if "collaboration" in name.lower()
            and name not in {"CONVERSATION_EVENT_SCHEMA_VERSION"}
        }
        for name in root_names:
            with self.subTest(root_omission=name):
                self.assertNotIn(name, story_writing_agents.__all__)
                self.assertFalse(hasattr(story_writing_agents, name))
        proposal = make_proposal()
        self.assertEqual(pickle.loads(pickle.dumps(proposal)), proposal)
        self.assertIs(
            pickle.loads(pickle.dumps(CollaborationProposal)),
            CollaborationProposal,
        )

    def test_append_receipt_error_and_public_signatures_are_exact(self) -> None:
        receipt = CollaborationAppendReceipt(
            CollaborationAppendStatus.ALREADY_COMMITTED,
            "batch_a",
            DIGEST_A,
            2,
            3,
            (
                "2026-08-31T00:00:01+00:00",
                "2026-08-31T00:00:02+00:00",
            ),
        )
        self.assertEqual(receipt.first_sequence, 2)
        for failure in CollaborationAppendFailure:
            with self.subTest(failure=failure):
                error = CollaborationAppendError(failure)
                self.assertIs(error.failure, failure)
                self.assertEqual(str(error), failure.value)
        expected_signatures = {
            collaboration_proposal_digest: ("proposal",),
            collaboration_batch_digest: ("batch",),
            encode_collaboration_proposal: ("proposal",),
            decode_collaboration_proposal: ("payload",),
            encode_collaboration_event_draft: ("event",),
            decode_collaboration_event_draft: ("payload",),
            encode_collaboration_event: ("event",),
            decode_collaboration_event: ("payload",),
            encode_collaboration_batch: ("batch",),
            decode_collaboration_batch: ("payload",),
        }
        for function, parameters in expected_signatures.items():
            with self.subTest(function=function.__name__):
                self.assertEqual(tuple(inspect.signature(function).parameters), parameters)

    def test_clean_import_orders_have_no_cwd_io_or_dynamic_forwarding(self) -> None:
        source = Path(__file__).resolve().parents[1] / "src"
        orders = (
            ("story_writing_agents", "story_writing_agents.conversations"),
            (
                "story_writing_agents.conversations.events",
                "story_writing_agents.conversations.context",
                "story_writing_agents.conversations",
            ),
            (
                "story_writing_agents.conversations.context",
                "story_writing_agents.conversations.events",
                "story_writing_agents.conversations",
            ),
        )
        for order in orders:
            with self.subTest(order=order), tempfile.TemporaryDirectory() as temporary:
                code = f"""
import importlib, pathlib, sys
sys.path.insert(0, {str(source)!r})
before = tuple(pathlib.Path.cwd().iterdir())
for name in {order!r}:
    importlib.import_module(name)
import story_writing_agents
import story_writing_agents.conversations as facade
import story_writing_agents.conversations.context as context
import story_writing_agents.conversations.events as events
assert len(facade.__all__) == 74
assert len(events.__all__) == 55
assert len(context.__all__) == 12
assert facade.CollaborationProposal is events.CollaborationProposal
assert facade.CollaborationEventStore is context.CollaborationEventStore
assert not hasattr(story_writing_agents, "CollaborationProposal")
assert "__getattr__" not in vars(facade)
assert before == tuple(pathlib.Path.cwd().iterdir())
"""
                completed = subprocess.run(
                    [sys.executable, "-B", "-I", "-c", code],
                    cwd=temporary,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
