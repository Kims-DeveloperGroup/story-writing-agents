"""Response recovery interoperates with exact historical wire codecs."""
from __future__ import annotations
import unittest
import copy
import hashlib
import json
from dataclasses import replace
from story_writing_agents.conversations import (
    CollaborationAppendError,
    CollaborationEventKind,
    CollaborationInvocationKind,
    CollaborationLeadOutcomeFailureCode,
    CollaborationLeadOutcomeFailurePayload,
    CollaborationMessagePayload,
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
    LEAD,
    RUNTIME,
    USER,
    awaiting_history,
    batch_from_history,
    make_draft,
    make_batch,
    store_batch,
)
from tests.provider_response_contract_fixture import (
    canonical_collaboration_json,
    collaboration_group_trace_fixture,
)


class ProviderResponseMappingTests(unittest.TestCase):
    def test_all_wire_values_have_stable_golden_hashes_and_round_trip(self) -> None:
        history, proposal = awaiting_history()
        batch = batch_from_history(history, "batch_proposal")
        cases = (
            (
                encode_collaboration_proposal,
                decode_collaboration_proposal,
                proposal,
                3513,
                "169e7e0c59711a061aea90757b45c6eba4adb21375a6a5b501a319d470a4a600",
            ),
            (
                encode_collaboration_event_draft,
                decode_collaboration_event_draft,
                batch.events[0],
                491,
                "e7d231c3b5bffd2414c8a45833cfb4465723e0c95ab9c5d32c88b95deb1de1a8",
            ),
            (
                encode_collaboration_event,
                decode_collaboration_event,
                history[0],
                694,
                "1470ec4f48c495191539108339a20332d39a50a0b7f31731e931979839cf3b9a",
            ),
            (
                encode_collaboration_batch,
                decode_collaboration_batch,
                batch,
                6766,
                "6a6e36e463c4cd899eb09c3417dbd27af433343584d174d8ba562ee055c2f028",
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
                self.assertEqual(canonical_collaboration_json(json.loads(wire)), wire)
        self.assertEqual(collaboration_proposal_digest(proposal), proposal.proposal_digest)
        self.assertEqual(collaboration_batch_digest(batch), batch.batch_digest)
        from story_writing_agents import lead_task_transitions as task
        from story_writing_agents.provider_response_mapping import recover_lead_public_response, recover_lead_public_response_kind
        literal = "Literal 한🙂\r\n  retained response."
        public = task.LeadPublicResponse(invocation_id="inv_public", call_id="call_public", ordinal=1,
            call_kind="initial", acceptance=task.LeadPublicResponseAcceptance.ACCEPTED,
            response_text=literal, material_digest=task.task_material_sha256(literal))
        legacy = task.task_value_to_dict(public)
        self.assertNotIn("response_kind", legacy)
        self.assertEqual(task.task_value_from_dict(task.LeadPublicResponse, legacy), public)
        with self.assertRaises(ValueError):
            task.task_value_from_dict(task.LeadPublicResponse, {**legacy, "response_kind": None})
        from story_writing_agents.conversations import CollaborationLeadPublicResponsePayload
        event = replace(history[0], kind=CollaborationEventKind.LEAD_PUBLIC_RESPONSE, sender=RUNTIME,
            recipient=USER, payload=CollaborationLeadPublicResponsePayload(public))
        legacy_wire = encode_collaboration_event(event)
        self.assertEqual(encode_collaboration_event(decode_collaboration_event(legacy_wire)), legacy_wire)
        known = replace(public, response_kind="recommendation")
        known_event = replace(event, payload=CollaborationLeadPublicResponsePayload(known))
        self.assertEqual(decode_collaboration_event(encode_collaboration_event(known_event)), known_event)
        self.assertEqual((known.response_text, known.material_digest), (public.response_text, public.material_digest))
        self.assertIsNone(decode_collaboration_event(legacy_wire).payload.response.response_kind)
        for invalid in ("accepted", "proposal_drafting_recommendation", "", True, 42):
            with self.assertRaises(ValueError):
                replace(public, response_kind=invalid)
        for native, kind in (("answer", "answer"), ("clarification_question", "question"),
                ("proposal_drafting_recommendation", "recommendation"), ("specialist_consultation_proposal", "proposal")):
            raw = json.dumps({"agent_action": {"task_result": {"response_type": native, "response_text": literal}}})
            self.assertEqual(recover_lead_public_response_kind(raw, "lead-response-v8", maximum_utf8_bytes=32768), kind)
            self.assertEqual(recover_lead_public_response(raw, "lead-response-v8", maximum_utf8_bytes=32768), literal)
        for raw in ('{"response_type":"answer","response_type":"critique","response_text":"x"}',
                '{"response_type":"unknown","response_text":"x"}',
                '{"response_type":"answer","response_text":"x","value":NaN}',
                '{"response_type":"answer","response_text":"x","task_result":{"response_text":"y"}}'):
            self.assertIsNone(recover_lead_public_response_kind(raw, "lead-response-v8", maximum_utf8_bytes=32768))
        self.assertIsNone(recover_lead_public_response_kind(
            '{"response_type":"answer","response_text":"xx"}', "lead-response-v8", maximum_utf8_bytes=1))

        from story_writing_agents.agent_context.group_observation import LeadGroupTrace
        from story_writing_agents.conversations.events import CollaborationLeadGroupTracePayload
        from story_writing_agents.conversations.context import (
            project_collaboration_history, project_collaboration_readable_history, project_lead_group_traces,
        )
        trace = collaboration_group_trace_fixture()
        detached = trace.as_object()
        self.assertEqual(detached["observations"][1]["selections"][0]["catalog_at_selection"]["groups"], [{
            "group_id": "answer.workflow", "definition": "Historical definition 한🙂",
            "selection_criteria": "Historical criterion", "result_effect": "Existing answer only",
        }])
        self.assertIsNone(detached["terminal"])
        restored = LeadGroupTrace.from_object(copy.deepcopy(detached))
        self.assertEqual(restored, trace)
        detached["observations"][1]["selections"][0]["catalog_at_selection"]["groups"][0]["definition"] = "mutated"
        self.assertEqual(restored.as_object()["observations"][1]["selections"][0]
                         ["catalog_at_selection"]["groups"][0]["definition"], "Historical definition 한🙂")
        self.assertNotIn("PRIVATE_SELECTION_REASON", repr(trace))
        with self.assertRaises(ValueError):
            LeadGroupTrace.from_object({**trace.as_object(), "unexpected": True})
        with self.assertRaises(ValueError):
            replace(trace, elapsed_ns=True)
        from story_writing_agents.agents import LEAD_ORCHESTRATOR
        from tests.collaboration_contract_fixture import provider_call_drafts
        turn_id = "group_trace_turn"
        draft = make_draft("group_trace", CollaborationEventKind.LEAD_GROUP_TRACE, RUNTIME, LEAD,
                           CollaborationLeadGroupTracePayload(trace), turn_id=turn_id)
        with self.assertRaises(CollaborationAppendError):
            store_batch(history, make_batch(history, "batch_orphan_trace", draft))
        user = make_draft("group_trace_user", CollaborationEventKind.MESSAGE, USER, LEAD,
                          CollaborationMessagePayload("Visible group request."), turn_id=turn_id)
        calls = tuple(replace(item, turn_id=turn_id) for item in provider_call_drafts(
            invocation_id=trace.invocation_id, call_id="group-call-1", role=LEAD_ORCHESTRATOR,
            recipient=LEAD, invocation_kind=CollaborationInvocationKind.LEAD))
        terminal = make_draft("group_trace_failure", CollaborationEventKind.LEAD_OUTCOME_FAILURE,
            RUNTIME, USER, CollaborationLeadOutcomeFailurePayload(
                CollaborationLeadOutcomeFailureCode.EVIDENCE_VALIDATION_REJECTED,
                trace.invocation_id, "group-call-1"), turn_id=turn_id)
        terminal_history = store_batch(history, make_batch(
            history, "batch_group_trace", user, *calls, terminal))
        trace_batch = make_batch(history, "batch_group_trace", user, *calls, terminal, draft)
        with_trace = store_batch(history, trace_batch)
        event = with_trace[-1]
        raw = encode_collaboration_event(event)
        self.assertEqual(decode_collaboration_event(raw), event)
        self.assertEqual(encode_collaboration_event(decode_collaboration_event(raw)), raw)
        self.assertEqual(json.loads(raw)["payload"], {"payload_type": "lead_group_trace", "trace": trace.as_object()})
        self.assertEqual(project_lead_group_traces(with_trace), (trace,))
        before = project_collaboration_history(terminal_history)
        after = project_collaboration_history(with_trace)
        self.assertEqual(before.current_proposal, proposal)
        self.assertEqual(after.current_proposal, proposal)
        for name in ("current_state", "next_safe_action", "available_tasks", "selectable_materials",
                     "public_responses", "physical_calls_spent", "artifact_objectives"):
            self.assertEqual(getattr(after, name), getattr(before, name), name)
        readable = project_collaboration_readable_history(with_trace)
        expected_records = tuple(replace(record, batch_size=len(trace_batch.events),
            batch_digest=collaboration_batch_digest(trace_batch))
            if record.batch_id == trace_batch.batch_id else record
            for record in project_collaboration_readable_history(terminal_history))
        self.assertEqual(readable[:-1], expected_records)
        self.assertEqual(readable[-1].fields, ())
        self.assertEqual(readable[-1].readable_utf8_bytes, 0)
        self.assertEqual(readable[-1].payload.trace, trace)
        self.assertNotIn("PRIVATE_SELECTION_REASON", repr(readable))
