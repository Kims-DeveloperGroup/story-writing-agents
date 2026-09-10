"""Provider-free evidence for the explicit live runner's hard gates."""
from __future__ import annotations

import asyncio
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock

from tests import lead_action_live_harness as live


class LiveHarnessTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="a641-guard-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.counter = 0

    def authorization(self, label="case", *, case=live.CASE):
        return {"authorization_id": label, "release_id": "synthetic-release", "case": case, "limits": live.LIMITS, "release_ledger": str(self.root / "release.json"), "model": {"executable": live.EXECUTABLE, "version": live.VERSION, "model": live.MODEL, "effort": live.EFFORT}}

    def guard(self, label="case", **kwargs):
        return live.CallGuard(self.authorization(label, case=kwargs.pop("case", live.CASE)), self.root / label, validate=kwargs.pop("validate", lambda: None), **kwargs)

    @staticmethod
    def request(**changes):
        fields = {"command": [live.EXECUTABLE, "exec", "--model", live.MODEL, "--config", 'model_reasoning_effort="xhigh"'], "stdin": b'{"synthetic":"initial/prelude/correction"}', "timeout_seconds": 90}
        return SimpleNamespace(**(fields | changes))

    async def test_import_and_missing_authorization_submit_nothing(self):
        with mock.patch("asyncio.create_subprocess_exec", side_effect=AssertionError("Import must be inert")), mock.patch("subprocess.check_output", side_effect=AssertionError("Import must be inert")), mock.patch.dict(os.environ, {"STORY_RUN_LIVE": ""}):
            importlib.reload(live)
            with self.assertRaises(live.AuthorizationError):
                live.load_authorization(None)
        with mock.patch.dict(os.environ, {"STORY_RUN_LIVE": "1"}), self.assertRaises(live.AuthorizationError):
            live.load_authorization(self.root / "missing.json")
        self.assertEqual(list(self.root.iterdir()), [])

    async def test_case_and_release_caps_count_every_reserved_submission(self):
        calls = []
        async def native(request, **_kwargs):
            calls.append(request)
            return SimpleNamespace(returncode=0, stdout=b"actual", stderr=b"")
        with self.guard() as guard:
            for kind in ("initial", "prelude", "correction", "continuation"):
                await guard.invoke(native, self.request(stdin=kind.encode()))
            with self.assertRaisesRegex(live.AuthorizationError, "Case call cap"):
                await guard.invoke(native, self.request())
            self.assertEqual(len(calls), 4)
            self.assertEqual(json.loads((self.root / "release.json").read_bytes())["reserved"], 4)
            self.assertEqual([p["ordinal"] for p in guard.calls], [1, 2, 3, 4])
        ledger = json.loads((self.root / "release.json").read_bytes())
        ledger["reserved"] = 11
        live.save(self.root / "release.json", ledger)
        with self.guard("last-release-call") as guard:
            await guard.invoke(native, self.request())
            with self.assertRaisesRegex(live.AuthorizationError, "Release call cap"):
                await guard.invoke(native, self.request())
        ledger = json.loads((self.root / "release.json").read_bytes())
        ledger["reserved"] = 0
        live.save(self.root / "release.json", ledger)
        now = [0.0]
        with self.guard("case-time", monotonic=lambda: now[0]) as guard:
            now[0] = 355.1
            with self.assertRaisesRegex(live.AuthorizationError, "Case time cap"):
                guard.reserve(self.request())
            self.assertEqual(guard.calls, [])
        ledger = json.loads((self.root / "release.json").read_bytes())
        ledger["seconds"] = 1255.1
        live.save(self.root / "release.json", ledger)
        with self.guard("release-time") as guard:
            with self.assertRaisesRegex(live.AuthorizationError, "Release time cap"):
                guard.reserve(self.request())
        self.assertEqual(len(calls), 5)

    async def test_source_or_model_drift_stops_before_submission(self):
        authorization = self.authorization() | {"schema": "lead-action-live-authorization-v1", "authorized": True, "expires_at": time.time() + 60, "source_root": str(Path(live.__file__).resolve().parents[1]), "source_pins": {"src/changed.py": "wrong"}}
        manifest = self.root / "authorization.json"
        live.save(manifest, authorization)
        with mock.patch.dict(os.environ, {"STORY_RUN_LIVE": "1"}), mock.patch("asyncio.create_subprocess_exec", side_effect=AssertionError("Drift must not submit")):
            with self.assertRaisesRegex(live.AuthorizationError, "Source or configuration drift"):
                live.load_authorization(manifest)
            authorization["model"]["model"] = "other-model"
            live.save(manifest, authorization)
            with self.assertRaisesRegex(live.AuthorizationError, "model identity"):
                live.load_authorization(manifest)
        drift = [False]
        def validate():
            live.require(not drift[0], "Source or configuration drift")
        with self.guard(validate=validate) as guard:
            drift[0] = True
            with self.assertRaisesRegex(live.AuthorizationError, "Source or configuration drift"):
                guard.reserve(self.request())
            drift[0] = False
            for command in ([live.EXECUTABLE, "exec", "--model", "wrong", "--config", 'model_reasoning_effort="xhigh"'], [live.EXECUTABLE, "exec", "--model", live.MODEL], ["other-provider", "--model", live.MODEL]):
                with self.subTest(command=command), self.assertRaises(live.AuthorizationError):
                    guard.reserve(self.request(command=command))
            self.assertEqual(guard.calls, [])
            self.assertEqual(guard.ledger["reserved"], 0)
        with self.assertRaises(live.AuthorizationError):
            with self.guard("entry-drift", validate=lambda: live.require(False, "source drift")):
                self.fail("Drift must not enter the case")
        self.assertFalse((self.root / "entry-drift").exists())

    async def test_native_results_are_unchanged_and_case_failure_is_not_retried(self):
        actual = SimpleNamespace(returncode=3, stdout=b'{"native":"unmodified"}\n', stderr=b"native error\n")
        calls = []
        async def native(request, **_kwargs):
            # This assertion observes the durable reservation before submission.
            calls.append(json.loads((self.root / "release.json").read_bytes())["reserved"])
            return actual
        with self.assertRaisesRegex(AssertionError, "synthetic case failure"):
            with self.guard() as guard:
                returned = await guard.invoke(native, self.request())
                self.assertIs(returned, actual)
                self.assertEqual((self.root / "case/calls/01/stdout.raw").read_bytes(), actual.stdout)
                self.assertEqual((self.root / "case/calls/01/stderr.raw").read_bytes(), actual.stderr)
                raise AssertionError("synthetic case failure")
        self.assertEqual(calls, [1])
        self.assertEqual(json.loads((self.root / "case/result.json").read_bytes())["status"], "failed")
        with self.assertRaises(live.AuthorizationError):
            with live.CallGuard(self.authorization(), self.root / "another-directory", validate=lambda: None):
                self.fail("The same authorization must not replay a failed case")
        self.assertEqual(calls, [1])

    async def test_contextual_case_preserves_observed_draft_and_accepts_both_objective_origins(self):
        from story_writing_agents import (
            AgentBackend as ActualBackend, SessionAccessState, SessionDurableState,
            StoryRuntime as ActualRuntime, codex_adapter, conversations as c,
        )
        from story_writing_agents.workspace.initialization import provision_session_resources as provision_fixture_resources
        from tests import test_lead_orchestration as lead
        from tests import test_artifact_proposal_orchestration as artifact

        # Real lifecycle setup must succeed before a fake Lead boundary can run.
        class LeadBoundaryReached(Exception):
            pass

        with self.guard("real-backend-prerequisites") as guard:
            real = {}
            def actual_backend_factory(workspace, **kwargs):
                backend = ActualBackend(workspace, **kwargs)
                real.update(backend=backend, workspace=workspace)
                original_close = backend.close
                def close_and_observe_release():
                    original_close()
                    observer = ActualBackend(workspace, **kwargs)
                    try:
                        released = observer.get_session_lifecycle("synthetic_a641")
                        self.assertIs(released.durable_state, SessionDurableState.READY)
                        self.assertIs(released.access_state, SessionAccessState.CLAIMABLE)
                        real["closed_and_released"] = True
                    finally:
                        observer.close()
                backend.close = close_and_observe_release
                return backend
            async def stop_at_lead(runtime, session_id, conversation_id, text):
                workspace = real["workspace"]
                self.assertEqual(workspace.root.name, "main")
                self.assertEqual(session_id, "synthetic_a641")
                self.assertIn(live.DRAFT, text)
                state = real["backend"].get_session_lifecycle(session_id)
                self.assertIs(state.durable_state, SessionDurableState.READY)
                self.assertIs(state.access_state, SessionAccessState.HELD_HERE)
                self.assertEqual(real["backend"].get_session_lifecycle(session_id), state)
                self.assertEqual(workspace.path(live.OUTLINE).read_bytes(), b"")
                defaults = workspace.read_json("config/system_defaults.json")
                self.assertEqual((defaults["codex_model"], defaults["codex_reasoning_effort"]), (live.MODEL, live.EFFORT))
                self.assertEqual(live.snapshot(runtime, workspace), json.loads((guard.evidence / "baseline.json").read_bytes()))
                self.assertEqual(guard.calls, [])
                real["lead_boundary_reached"] = True
                raise LeadBoundaryReached
            with mock.patch("story_writing_agents.AgentBackend", side_effect=actual_backend_factory), mock.patch.object(ActualRuntime, "lead_turn", new=stop_at_lead), mock.patch.object(codex_adapter, "run_process", side_effect=AssertionError("Real prerequisite must not submit a model call")), mock.patch("asyncio.create_subprocess_exec", side_effect=AssertionError("Real prerequisite must not launch a provider")):
                with self.assertRaises(LeadBoundaryReached):
                    await live.contextual_case(guard)
            self.assertTrue(real["lead_boundary_reached"])
            self.assertTrue(real["closed_and_released"])
            self.assertFalse(real["workspace"].root.exists())
            self.assertEqual(guard.calls, [])
            self.assertEqual(json.loads((guard.evidence / "cleanup.json").read_bytes()), {"workspace_removed": True, "owned_children_remaining": []})

        ready_order = ["validate_configuration", "provision_resources", "create_and_hold", "observe_lifecycle"]
        for initial_objective, valid_draft, initial_kind in ((False, True, "question"), (True, True, "question"), (False, False, "question"), (False, True, "recommendation")):
            with self.subTest(initial_objective=initial_objective, valid_draft=valid_draft):
                observed = live.DRAFT.rstrip("\n") if not initial_objective else live.DRAFT + "\n"
                if not valid_draft:
                    observed = "Unexpected fixture response."
                holder = {"lifecycle": []}
                with self.guard("objective-" + str(initial_objective) + "-" + str(valid_draft) + "-" + initial_kind) as guard:
                    async def return_native(call, value):
                        raw = lead._provider_envelope(call, value)
                        async def native(_request, **_kwargs):
                            return SimpleNamespace(returncode=0, stdout=raw.encode(), stderr=b"")
                        result = await guard.invoke(native, self.request(stdin=raw.encode()))
                        return result.stdout.decode()
                    async def first(call, provider):
                        self.assertEqual(holder["lifecycle"], ready_order)
                        self.assertTrue((guard.evidence / "baseline.json").is_file())
                        holder["lifecycle"].append("first_turn")
                        intent = {"action": "start", "request": "Prepare the synthetic outline after approval.", "target_paths": [live.OUTLINE], "next_step": "clarify", "draft_source_event_ids": []} if initial_objective else None
                        return await return_native(call, {"scope": "story_specific", "evidence_status": "empty", "evidence_ids": [artifact._status_id(call, live.OUTLINE)], "kind": initial_kind, "message": observed, "proposal": None, "artifact_intent": None, "objective_intent": intent, "recommended_action": ({"kind": "prepare_artifact", "purpose": "Prepare the offered draft.", "instruction": "Prepare the exact offered draft.", "expected_result": "An exact candidate for review.", "source_ids": ["current_response"], "target_paths": [live.OUTLINE], "specialist_ids": [], "objective_id": None} if initial_kind == "recommendation" else None)})
                    async def second(call, provider):
                        self.assertEqual(holder["lifecycle"], ready_order + ["first_turn"])
                        holder["lifecycle"].append("second_turn")
                        runtime = holder["runtime"]
                        services = runtime.conversation_storage_services
                        conversation = services.catalog.list("synthetic_a641")[0].conversation_id
                        projection = c.project_collaboration_history(services.event_store.read("synthetic_a641", conversation))
                        source = projection.offered_draft_sources[-1]
                        intent = {"action": "continue", "objective_id": projection.artifact_objectives[0].facts.objective_id, "next_step": "prepare", "draft_source_event_ids": [source.event_id]} if initial_objective else {"action": "start", "request": "Prepare the approved synthetic outline.", "target_paths": [live.OUTLINE], "next_step": "prepare", "draft_source_event_ids": [source.event_id]}
                        evidence_id = artifact._status_id(call, live.OUTLINE)
                        return await return_native(call, {"scope": "story_specific", "evidence_status": "empty", "evidence_ids": [evidence_id], "kind": "proposal", "message": "The exact draft is ready for review.", "proposal": None, "artifact_intent": {"additional_review_domains": [], "operations": [{"kind": "replace", "evidence_id": evidence_id, "new_text": observed}]}, "objective_intent": intent, "recommended_action": None})
                    def backend_factory(workspace, **_kwargs):
                        holder["workspace_root"] = str(workspace.root)
                        backend = lead._LeadBackend(persist_context_threads=True)
                        backend.enqueue(first)
                        backend.enqueue(second)
                        def validate_provider_configuration(session_id):
                            self.assertEqual(session_id, "synthetic_a641")
                            self.assertEqual(holder["lifecycle"], [])
                            holder["lifecycle"].append("validate_configuration")
                            return True
                        def provision_session_resources(session_id):
                            self.assertEqual(session_id, "synthetic_a641")
                            self.assertEqual(holder["lifecycle"], ready_order[:1])
                            holder["lifecycle"].append("provision_resources")
                            return provision_fixture_resources(workspace.root, session_id)
                        def create_session_with_access(session_id):
                            self.assertEqual(session_id, "synthetic_a641")
                            self.assertEqual(holder["lifecycle"], ready_order[:2])
                            self.assertEqual(backend.calls, [])
                            self.assertFalse((guard.evidence / "baseline.json").exists())
                            holder["lifecycle"].append("create_and_hold")
                            holder["session_lifecycle"] = SimpleNamespace(
                                durable_state=SessionDurableState.READY,
                                access_state=SessionAccessState.HELD_HERE,
                            )
                            return holder["session_lifecycle"]
                        def get_session_lifecycle(session_id):
                            self.assertEqual(session_id, "synthetic_a641")
                            self.assertEqual(holder["lifecycle"], ready_order[:3])
                            holder["lifecycle"].append("observe_lifecycle")
                            return holder["session_lifecycle"]
                        backend.validate_provider_configuration = validate_provider_configuration
                        backend.provision_session_resources = provision_session_resources
                        backend.create_session_with_access = create_session_with_access
                        backend.get_session_lifecycle = get_session_lifecycle
                        backend.close = lambda: holder["lifecycle"].append("close")
                        return backend
                    def runtime_factory(workspace, backend):
                        self.assertEqual(holder["lifecycle"], ready_order)
                        holder["runtime"] = ActualRuntime(workspace, backend)
                        return holder["runtime"]
                    with mock.patch("story_writing_agents.AgentBackend", side_effect=backend_factory), mock.patch("story_writing_agents.StoryRuntime", side_effect=runtime_factory), mock.patch("asyncio.create_subprocess_exec", side_effect=AssertionError("No native provider in unit tests")):
                        if valid_draft:
                            await live.contextual_case(guard)
                        else:
                            with self.assertRaisesRegex(live.AuthorizationError, "Provider did not offer"):
                                await live.contextual_case(guard)
                    expected_order = ready_order + ["first_turn"]
                    if valid_draft:
                        expected_order.append("second_turn")
                    self.assertEqual(holder["lifecycle"], expected_order + ["close"])
                    lifecycle = json.loads((guard.evidence / "lifecycle.json").read_bytes())
                    self.assertEqual(lifecycle, {"session_id": "synthetic_a641", "durable_state": "ready", "access_state": "held_here", "workspace_root": holder["workspace_root"], "repeated_observation_matches": True})
                    diagnostic = json.loads((guard.evidence / "first-result.json").read_bytes())
                    self.assertEqual(diagnostic["kind"], initial_kind)
                    self.assertIsNone(diagnostic["state"])
                    self.assertEqual(diagnostic["message_sha256"], live.sha(observed.encode()))
                    self.assertEqual(diagnostic["message_utf8_bytes"], len(observed.encode()))
                    self.assertNotIn("message", diagnostic)
                    if valid_draft:
                        evidence = json.loads((guard.evidence / "case-assertions.json").read_bytes())
                        self.assertEqual(evidence["initial_objective_present"], initial_objective)
                        self.assertEqual(evidence["observed_draft_sha256"], live.sha(observed.encode()))
                    else:
                        self.assertFalse((guard.evidence / "case-assertions.json").exists())
                    self.assertEqual(len(guard.calls), 2 if valid_draft else 1)
                    raw = json.loads((guard.evidence / "calls/01/stdout.raw").read_bytes())
                    self.assertEqual(raw["action"]["result"]["message"], observed)
                    cleanup = json.loads((guard.evidence / "cleanup.json").read_bytes())
                    self.assertTrue(cleanup["workspace_removed"])

    async def test_recommendation_case_confirms_exact_request_without_fabricated_input(self):
        from story_writing_agents import StoryRuntime as ActualRuntime, SessionAccessState, SessionDurableState
        from story_writing_agents import conversations as c
        from story_writing_agents.workspace.initialization import provision_session_resources
        from tests import test_lead_orchestration as lead
        from tests import recommendation_fixture as fixture
        observed = live.DRAFT.rstrip("\n")
        holder = {}
        with self.guard("structured", case=live.RECOMMENDATION_CASE) as guard:
            async def native_reply(call, raw):
                async def native(_request, **_kwargs):
                    return SimpleNamespace(returncode=0, stdout=raw.encode(), stderr=b"")
                actual = await guard.invoke(native, self.request(stdin=raw.encode()))
                return actual.stdout.decode()
            async def first(call, provider):
                self.assertEqual(holder["lifecycle"], ["validated", "provisioned", "held", "observed"])
                return await native_reply(call, fixture.recommendation(message=observed)(call, provider))
            async def second(call, provider):
                runtime = holder["runtime"]
                offered = holder["first"]
                events = runtime.conversation_storage_services.event_store.read("synthetic_b642", offered.conversation_id)
                decisions = [e for e in events if type(e.payload) is c.LeadRecommendationDecisionPayload]
                starts = [e for e in events if type(e.payload) is c.LeadRecommendationStartedPayload]
                self.assertEqual(len(decisions), 1)
                self.assertEqual(len(starts), 1)
                self.assertEqual(decisions[0].payload.decision, fixture.decision(offered))
                self.assertEqual(holder["ordinary_calls"], 1)
                return await native_reply(call, fixture.preparation(offered, text=observed)(call, provider))
            def backend_factory(workspace, **_kwargs):
                backend = lead._LeadBackend(persist_context_threads=True)
                holder["lifecycle"] = []
                backend.enqueue(first)
                backend.enqueue(second)
                def validate(session_id):
                    self.assertEqual(session_id, "synthetic_b642")
                    holder["lifecycle"].append("validated")
                    return True
                def provision(session_id):
                    provision_session_resources(workspace.root, session_id)
                    holder["lifecycle"].append("provisioned")
                state = SimpleNamespace(durable_state=SessionDurableState.READY, access_state=SessionAccessState.HELD_HERE)
                def acquire(session_id):
                    holder["lifecycle"].append("held")
                    return state
                def observe(session_id):
                    holder["lifecycle"].append("observed")
                    return state
                backend.validate_provider_configuration = validate
                backend.provision_session_resources = provision
                backend.create_session_with_access = acquire
                backend.get_session_lifecycle = observe
                backend.close = lambda: holder["lifecycle"].append("closed")
                return backend
            def runtime_factory(workspace, backend):
                runtime = ActualRuntime(workspace, backend)
                holder["runtime"] = runtime
                holder["ordinary_calls"] = 0
                original = runtime.lead_turn
                async def observe(*args, **kwargs):
                    holder["ordinary_calls"] += 1
                    holder["first"] = await original(*args, **kwargs)
                    return holder["first"]
                runtime.lead_turn = observe
                return runtime
            with mock.patch("story_writing_agents.AgentBackend", side_effect=backend_factory), mock.patch("story_writing_agents.StoryRuntime", side_effect=runtime_factory), mock.patch("asyncio.create_subprocess_exec", side_effect=AssertionError("No native unit execution")):
                await live.recommendation_case(guard)
            self.assertEqual(len(guard.calls), 2)
            self.assertEqual(holder["ordinary_calls"], 1)
            self.assertEqual(holder["lifecycle"][-1], "closed")
            assertions = json.loads((guard.evidence / "case-assertions.json").read_bytes())
            self.assertTrue(assertions["structured_confirm"])
            self.assertEqual(assertions["case"], live.RECOMMENDATION_CASE)
            self.assertIsNone(assertions["abbreviated_input"])
            self.assertEqual(assertions["observed_draft_sha256"], live.sha(observed.encode()))
            self.assertTrue(json.loads((guard.evidence / "cleanup.json").read_bytes())["workspace_removed"])
            raw = json.loads((guard.evidence / "calls/01/stdout.raw").read_bytes())
            self.assertEqual(raw["action"]["result"]["message"], observed)
            self.assertEqual(raw["action"]["result"]["recommended_action"]["source_ids"], ["current_response"])

    async def test_case_selection_preserves_shared_release_caps_and_authorization_uniqueness(self):
        self.assertEqual(live.CASES, ("A641-CONTEXT-DRAFT", "B642-RECOMMEND-PREPARE"))
        calls = []
        async def native(request, **_kwargs):
            calls.append(request)
            return SimpleNamespace(returncode=0, stdout=b"synthetic raw", stderr=b"")
        for label, case in (("context", live.CASE), ("recommendation", live.RECOMMENDATION_CASE)):
            with self.guard(label, case=case) as guard:
                for _ in range(4):
                    await guard.invoke(native, self.request())
        ledger = json.loads((self.root / "release.json").read_bytes())
        self.assertEqual(ledger["reserved"], 8)
        self.assertEqual([a["case"] for a in ledger["attempts"]], list(live.CASES))
        with self.assertRaisesRegex(live.AuthorizationError, "never retry"):
            with live.CallGuard(self.authorization("context", case=live.RECOMMENDATION_CASE),
                                self.root / "duplicate", validate=lambda: None):
                self.fail("Changing the case cannot reuse an authorization")
        with self.guard("last", case=live.RECOMMENDATION_CASE) as guard:
            for _ in range(4): await guard.invoke(native, self.request())
        with self.guard("exhausted") as guard:
            with self.assertRaisesRegex(live.AuthorizationError, "Release call cap"):
                await guard.invoke(native, self.request())
        self.assertEqual(len(calls), 12)
        with self.guard("wrong-entry", case=live.RECOMMENDATION_CASE) as guard:
            with mock.patch("story_writing_agents.AgentBackend", side_effect=AssertionError("No setup on wrong case")):
                with self.assertRaisesRegex(live.AuthorizationError, "different case"):
                    await live.contextual_case(guard)
            self.assertEqual(guard.calls, [])
        manifest = self.root / "wrong-case.json"
        live.save(manifest, self.authorization("manifest") | {"schema": "lead-action-live-authorization-v1", "authorized": True})
        with mock.patch.dict(os.environ, {"STORY_RUN_LIVE": "1"}), self.assertRaisesRegex(live.AuthorizationError, "different case"):
            live.load_authorization(manifest, case=live.RECOMMENDATION_CASE)

    async def test_timeout_cancellation_and_cleanup_preserve_evidence_and_join_owned_children(self):
        for cancellation in (False, True):
            with self.subTest(cancellation=cancellation):
                label = "cancel" if cancellation else "timeout"
                created = asyncio.Event()
                owned = []
                async def native(_request, *, create_process):
                    child = await create_process(sys.executable, "-c", "import time; time.sleep(60)", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                    owned.append(child)
                    created.set()
                    if cancellation:
                        await child.wait()
                    else:
                        await asyncio.wait_for(child.wait(), 0.02)
                    return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
                with self.guard(label) as guard:
                    task = asyncio.create_task(guard.invoke(native, self.request()))
                    await asyncio.wait_for(created.wait(), 5)
                    if cancellation:
                        task.cancel()
                    with self.assertRaises(asyncio.CancelledError if cancellation else asyncio.TimeoutError):
                        await task
                    self.assertTrue(all(child.returncode is not None for child in owned))
                    self.assertEqual(guard.children, {})
                    reservation = json.loads((self.root / label / "calls/01/reservation.json").read_bytes())
                    self.assertTrue(reservation["submitted"])
                    self.assertEqual(reservation["status"], "failed")
                    self.assertTrue((self.root / label / "calls/01/stdin.raw").exists())
                    self.assertEqual(len(guard.calls), 1)
        self.assertEqual(json.loads((self.root / "release.json").read_bytes())["reserved"], 2)
        with self.guard("workspace-cleanup") as guard:
            with mock.patch("story_writing_agents.AgentBackend", side_effect=RuntimeError("Synthetic backend construction failure")):
                with self.assertRaisesRegex(RuntimeError, "Synthetic backend construction failure"):
                    await live.contextual_case(guard)
            cleanup = json.loads((self.root / "workspace-cleanup/cleanup.json").read_bytes())
            self.assertTrue(cleanup["workspace_removed"])
            self.assertEqual(cleanup["owned_children_remaining"], [])
            self.assertEqual(guard.calls, [])


if __name__ == "__main__":
    unittest.main()
