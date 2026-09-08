"""Offline contracts for live evidence acceptance; no provider execution."""
from __future__ import annotations

import asyncio
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import antigravity_live_harness as harness


class LiveHarnessAuditTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.identity = dict(session_id="session", conversation_id="conversation",
                             turn_id="turn", step_id="step", agent_id="lead_orchestrator")
        current = dict(identity=self.identity, workflow="lead_front_channel", command="Review once")
        prelude = lambda ordinal: dict(operation="writing_artifact_reference_read", correlation={
            "operation_id": "reference-operation", "invocation_ordinal": ordinal,
            "agent_id": "lead_orchestrator", "phase": "lead"})
        correction = dict(current, response_correction={
            "rejection_code": "answer_validation_failed", "instruction": "Return ANSWER now"})
        self.payloads = [prelude(1), dict(c="correlation", q=["lead", "sa", "wcdo"], i=current,
                                       untrusted_writing_artifact_context=["reference"]),
                         prelude(2), dict(c="correlation", q=["lead", "a", ""], i=correction,
                                          untrusted_writing_artifact_context=["reference"])]
        self.calls = []
        self.exchanges = []
        self.history = []
        for index, payload in enumerate(self.payloads, 1):
            directory = self.root / str(index)
            directory.mkdir()
            thread = "old-thread" if index <= 2 else "fresh-thread"
            prior = thread if index in (2, 4) else None
            schema = {"type": "object"}
            content = {"correlation": "correlation", "action": {"kind": "answer", "result": {
                "kind": "answer", "message": "Correct answer" if index == 4 else "Rejected answer"}}}
            native = dict(status="SUCCESS", conversation_id=thread,
                          json_schema=schema, structured_output=content)
            argv = ["unused-agy", "--json-schema", harness.canonical_provider_json(schema).decode(), "--print", json.dumps(payload)]
            (directory / "command.private.json").write_text(json.dumps({"argv": argv}))
            raw = harness.canonical_provider_json(native)
            for name in ("stdout.raw", "delivered.stdout"):
                (directory / name).write_bytes(raw)
            for name in ("stderr.raw", "delivered.stderr", "stdin.bin"):
                (directory / name).write_bytes(b"")
            call = dict(number=index, agent="lead_orchestrator", vendor="antigravity",
                        directory=str(directory), fault=None, delivered_returncode=0,
                        native_status="SUCCESS", thread_id=thread, prior_thread_id=prior,
                        payload_bytes=len(argv[-1].encode()), payload_sha256=harness.digest(argv[-1].encode()),
                        payload_semantic_sha256=harness.semantic_digest(payload),
                        schema_semantic_sha256=harness.semantic_digest(schema))
            self.calls.append(call)
            outcome = {"outcome": "rejected", "code": "answer_validation_failed"} if index == 2 else {
                "outcome": "accepted", "code": "accepted"}
            values = [dict(payload=payload, schema=schema, prior_thread_id=prior),
                      dict(response_text=json.dumps(content), thread_id=thread,
                           actual_transport_utf8_bytes=call["payload_bytes"]), outcome]
            for kind, value in zip(("request", "response", "outcome"), values):
                self.exchanges.append(dict(exchange_id=str(index), agent="lead_orchestrator",
                                           agent_id="lead_orchestrator", request_type=kind,
                                           content=json.dumps(value)))
            for state in ("prepared", "submitted"):
                self.history.append(dict(self.identity, sequence=len(self.history) + 1, payload=dict(
                    payload_type="provider_call", state=state, role="lead_orchestrator",
                    invocation_kind="lead", invocation_id="logical-invocation", ordinal=index,
                    call_id="physical-" + str(index),
                    call_kind="reference_prelude" if index in (1, 3) else "initial")))
        self.history.append(dict(self.identity, sequence=9, payload=dict(
            payload_type="lead_judgment", kind="answer", message="Correct answer")))

    def audit(self):
        return harness.audit_exchanges(self.exchanges, self.calls, self.history)

    def test_exact_validation_correction_remains_rejected_then_accepted(self):
        result = self.audit()
        self.assertEqual([r["outcome"] for r in result], ["accepted", "rejected", "accepted", "accepted"])
        self.assertEqual(result[1]["correction_proof"]["physical_calls"], 4)
        self.assertEqual(result[1]["correction_proof"]["corrected_call"], 4)

    def test_typed_events_preserve_discriminators_through_complete_audit(self):
        from story_writing_agents import conversations as contracts
        from tests.collaboration_contract_fixture import LEAD, RUNTIME, USER, make_lead_evidence

        typed = []
        for record in self.history:
            value = record["payload"]
            provider = value["payload_type"] == "provider_call"
            if provider:
                payload = contracts.CollaborationProviderCallPayload(
                    value["invocation_id"], value["call_id"], value["ordinal"], value["role"],
                    contracts.CollaborationInvocationKind(value["invocation_kind"]),
                    contracts.CollaborationProviderCallKind(value["call_kind"]),
                    contracts.CollaborationProviderCallState(value["state"]),
                )
            else:
                payload = contracts.CollaborationLeadJudgmentPayload(
                    contracts.LeadOutcomeKind.ANSWER, value["message"], make_lead_evidence(),
                )
            typed.append(contracts.CollaborationEvent(
                schema_version=contracts.COLLABORATION_EVENT_SCHEMA_VERSION,
                event_id="event_" + str(record["sequence"]),
                session_id=record["session_id"], conversation_id=record["conversation_id"],
                turn_id=record["turn_id"], step_id=record["step_id"],
                sequence=record["sequence"], timestamp="2026-09-01T00:00:00+00:00",
                batch_id="batch_" + str(record["sequence"]), batch_ordinal=0,
                batch_size=1, batch_digest="sha256:" + "a" * 64,
                kind=(contracts.CollaborationEventKind.PROVIDER_CALL_ACCOUNTING if provider
                      else contracts.CollaborationEventKind.MESSAGE),
                sender=RUNTIME if provider else LEAD, recipient=LEAD if provider else USER,
                payload=payload,
            ))
        canonical = [json.loads(contracts.encode_collaboration_event(event)) for event in typed]
        self.assertEqual(harness.canonical_history(typed), canonical)
        self.assertIs(harness.canonical_history([canonical[0]])[0], canonical[0])
        self.assertEqual(harness.audit_exchanges(self.exchanges, self.calls, typed), self.audit())

    def test_missing_marker_rejects(self):
        directory = Path(self.calls[3]["directory"])
        value = json.loads((directory / "command.private.json").read_text())
        payload = json.loads(value["argv"][-1])
        del payload["i"]["response_correction"]
        value["argv"][-1] = json.dumps(payload)
        (directory / "command.private.json").write_text(json.dumps(value))
        self.calls[3]["payload_sha256"] = harness.digest(value["argv"][-1].encode())
        self.calls[3]["payload_semantic_sha256"] = harness.semantic_digest(payload)
        self.calls[3]["payload_bytes"] = len(value["argv"][-1].encode())
        request = json.loads(self.exchanges[9]["content"])
        request["payload"] = payload
        self.exchanges[9]["content"] = json.dumps(request)
        response = json.loads(self.exchanges[10]["content"])
        response["actual_transport_utf8_bytes"] = self.calls[3]["payload_bytes"]
        self.exchanges[10]["content"] = json.dumps(response)
        with self.assertRaisesRegex(AssertionError, "Missing or multiple"):
            self.audit()

    def test_wrong_native_thread_rejects(self):
        self.calls[3]["prior_thread_id"] = "wrong-thread"
        value = json.loads(self.exchanges[9]["content"])
        value["prior_thread_id"] = "wrong-thread"
        self.exchanges[9]["content"] = json.dumps(value)
        with self.assertRaisesRegex(AssertionError, "fresh prelude thread"):
            self.audit()

    def test_wrong_durable_invocation_rejects(self):
        self.history[7]["payload"]["invocation_id"] = "other-invocation"
        with self.assertRaisesRegex(AssertionError, "Wrong durable invocation"):
            self.audit()

    def test_extra_correction_rejects(self):
        self.history.append(copy.deepcopy(self.history[-1]))
        with self.assertRaisesRegex(AssertionError, "durable Lead judgment"):
            self.audit()
        self.history.pop()
        extra = copy.deepcopy(self.calls[-1])
        extra["number"] = 5
        # Direct proof input isolates duplicate-correction rejection from capture matching.
        entries = []
        for call in [*self.calls, extra]:
            payload, _ = harness.call_input(call)
            entries.append(dict(call=call, payload=payload,
                                outcome="rejected" if call["number"] == 2 else "accepted",
                                code="answer_validation_failed" if call["number"] == 2 else "accepted",
                                terminal=json.loads((Path(call["directory"]) / "stdout.raw").read_bytes())))
        with self.assertRaisesRegex(AssertionError, "Missing or multiple"):
            harness.prove_lead_corrections(entries, self.history)

    def test_mismatched_native_bytes_reject(self):
        (Path(self.calls[1]["directory"]) / "stdout.raw").write_bytes(b"changed")
        with self.assertRaisesRegex(AssertionError, "native stream bytes differ"):
            self.audit()

    def test_rejected_action_and_correlation_must_match(self):
        entries = []
        for call in self.calls:
            payload, _ = harness.call_input(call)
            entries.append(dict(call=call, payload=payload,
                                outcome="rejected" if call["number"] == 2 else "accepted",
                                code="answer_validation_failed" if call["number"] == 2 else "accepted",
                                terminal=json.loads((Path(call["directory"]) / "stdout.raw").read_bytes())))
        for field in ("correlation", "kind"):
            with self.subTest(field=field):
                changed = copy.deepcopy(entries)
                output = changed[1]["terminal"]["structured_output"]
                if field == "correlation":
                    output["correlation"] = "foreign"
                else:
                    output["action"]["kind"] = "search"
                with self.assertRaisesRegex(AssertionError, "correlation or action"):
                    harness.prove_lead_corrections(changed, self.history)

    def test_resume_accepts_only_proven_fresh_handoff(self):
        audit = self.audit()
        calls = copy.deepcopy(self.calls[1:])
        harness.assert_resumed_call_chain(calls, "old-thread", audit)
        with self.assertRaisesRegex(AssertionError, "Unexplained fresh"):
            harness.assert_resumed_call_chain(calls, "old-thread", [])
        calls[0]["prior_thread_id"] = None
        with self.assertRaisesRegex(AssertionError, "First resumed"):
            harness.assert_resumed_call_chain(calls, "old-thread", audit)

    def test_fresh_cumulative_global_and_stage_bounds(self):
        inventory = harness.load_inventory()
        self.assertEqual(sum(s["max_calls"] for s in inventory["cases"]), 202)
        self.assertEqual(inventory["budget"]["matrix_max_calls"], 209)
        self.assertEqual(harness.stage_seconds(7), 1440)
        self.assertEqual(harness.stage_seconds(12), 2340)
        with mock.patch.dict(os.environ, {"STORY_LIVE_BUDGET_ROOT": str(self.root),
                                          "STORY_LIVE_PRIOR_CASE_SUBMISSIONS": "8"}):
            observer = harness.CallEvidence(self.root, harness.selected_spec("L04-SONNET-ONE"), self.root)
            self.assertEqual(observer.effective_case_cap, 26)
            observer.calls = [{}] * 17
            observer.reserve()
            observer.calls.append({})
            with self.assertRaisesRegex(AssertionError, "Per-case"):
                observer.reserve()
        with mock.patch.dict(os.environ, {"STORY_LIVE_BUDGET_ROOT": str(self.root),
                                          "STORY_LIVE_PRIOR_CASE_SUBMISSIONS": "0"}):
            observer = harness.CallEvidence(self.root, harness.selected_spec("L06A-QUESTION"), self.root)
            ledger = self.root / "call-budget.json"
            ledger.write_text(json.dumps({"reserved": 208, "started_at": harness.time.time()}))
            observer.reserve()
            self.assertEqual(json.loads(ledger.read_text())["reserved"], 209)
            with self.assertRaisesRegex(AssertionError, "matrix physical-call cap"):
                observer.reserve()


class LiveHarnessChildCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_fault_child_timeout_cancellation_and_success_are_joined(self):
        for failure in (asyncio.TimeoutError(), asyncio.CancelledError()):
            with self.subTest(failure=type(failure).__name__):
                child = mock.Mock(pid=123456, returncode=None)
                child.wait = mock.AsyncMock(return_value=-9)
                async def waits(awaitable, timeout):
                    await awaitable
                    if timeout == 1:
                        raise failure
                    raise asyncio.TimeoutError()
                with mock.patch.object(harness.asyncio, "wait_for", side_effect=waits), mock.patch.object(
                    harness.os, "killpg", create=True
                ) as killpg:
                    with self.assertRaises(type(failure)):
                        await harness.wait_owned_fault_child(child, 1)
                self.assertEqual(child.wait.await_count, 3)
                if os.name == "posix":
                    self.assertEqual(killpg.call_count, 2)
        child = mock.Mock(returncode=86)
        child.wait = mock.AsyncMock(return_value=86)
        self.assertEqual(await harness.wait_owned_fault_child(child, 1), 86)
        child.wait.assert_awaited_once()


class LiveTerminalCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_primary_failure_survives_cleanup_and_standalone_cleanup_fails(self):
        import sys

        with tempfile.TemporaryDirectory() as directory:
            terminal = mock.Mock(label="owned", case=mock.Mock(directory=Path(directory)))
            terminal.close = mock.AsyncMock(side_effect=RuntimeError("cleanup"))
            original = ValueError("primary")
            with self.assertRaises(ValueError) as caught:
                try:
                    raise original
                finally:
                    await harness.close_terminal(terminal, sys.exc_info()[1])
            self.assertIs(caught.exception, original)
            terminal.close.assert_awaited_once()
            record = json.loads((Path(directory) / "owned.cleanup-error.sanitized.json").read_text())
            self.assertEqual(record, {"primary_error_type": "ValueError", "cleanup_error_type": "RuntimeError"})
            with self.assertRaises(RuntimeError):
                await harness.close_terminal(terminal)
            terminal.close = mock.AsyncMock()
            await harness.close_terminal(terminal)
            terminal.close.assert_awaited_once()
