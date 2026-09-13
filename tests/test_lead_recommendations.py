"""Public recommendation decisions and their preparation/recovery boundaries."""

from __future__ import annotations

import asyncio
import json
import os
import traceback
from dataclasses import replace
from types import SimpleNamespace
from unittest import mock

from story_writing_agents import StoryRuntime
from story_writing_agents import conversations as c
from story_writing_agents import orchestrator as owner
from story_writing_agents.agents import LEAD_ORCHESTRATOR, PLOT_ARCHITECT, EXECUTION_SPECIALIST
from story_writing_agents.specialist_routing import AuthoringActivity
from story_writing_agents.errors import BackendProtocolError
from story_writing_agents.workspace.initialization import (
    SessionArtifactBinding,
    SessionArtifactBindingError,
    compose_session_artifact_binding,
)
from story_writing_agents.writing_artifacts._storage import ArtifactRootSecurityError
from tests.recommendation_fixture import (
    DRAFT, OUTLINE, RecommendationCase, action, decision, digest,
    preparation, recommendation, rewrite_history,
)
from tests import test_lead_orchestration as lead
from tests import test_artifact_proposal_orchestration as artifact
from tests import test_lead_artifact_application as application
from tests import test_lead_story_commit as commit


def _generation_pin_descriptors(pin) -> tuple[int, ...]:
    git_pin = pin._pin
    return () if git_pin is None else git_pin._descriptors


class LeadRecommendationTests(RecommendationCase):
    async def test_lead_recommendation_reuses_invocation_artifact_binding(self):
        invocation_bindings = []
        request_bindings = []
        basis_bindings = []
        fingerprint_bindings = []
        pins = []
        guarded_order = []
        original_binding = self.runtime._session_artifact_binding
        original_request = self.runtime._recommendation_request_draft
        original_basis = self.runtime._recommendation_basis
        original_fingerprint = self.runtime._recommendation_source_fingerprint
        original_acquire_pin = SessionArtifactBinding.acquire_generation_pin
        original_prepare = self.runtime._prepare_session_reference_operation
        original_definition = self.runtime._build_guarded_contextual_definition
        original_context_state = self.runtime._session_context_state
        original_invoke = self.runtime._invoke_context_with_recovery
        original_append = self.runtime._append_drafts

        def require_current_pin():
            self.assertEqual(len(pins), 1)
            pin_binding, pin = pins[0]
            self.assertIsNone(pin_binding._current_repository_access())
            pin_binding.require_generation_pin(pin)

        def capture_pin(binding):
            pin = original_acquire_pin(binding)
            pins.append((binding, pin))
            guarded_order.append("pin")
            return pin

        def capture_binding(session_id):
            binding = original_binding(session_id)
            invocation_bindings.append(binding)
            return binding

        def capture_request(*args, **kwargs):
            require_current_pin()
            request_bindings.append(kwargs["artifact_binding"])
            return original_request(*args, **kwargs)

        def capture_basis(*args, **kwargs):
            basis_bindings.append(kwargs["artifact_binding"])
            return original_basis(*args, **kwargs)

        def capture_fingerprint(*args, **kwargs):
            fingerprint_bindings.append(kwargs["artifact_binding"])
            return original_fingerprint(*args, **kwargs)

        def capture_prepare(*args, **kwargs):
            require_current_pin()
            guarded_order.append("references")
            return original_prepare(*args, **kwargs)

        def capture_definition(*args, **kwargs):
            require_current_pin()
            guarded_order.append("definition")
            return original_definition(*args, **kwargs)

        def capture_context_state(*args, **kwargs):
            require_current_pin()
            guarded_order.append("context")
            return original_context_state(*args, **kwargs)

        async def capture_invoke(*args, **kwargs):
            require_current_pin()
            guarded_order.append("await")
            await asyncio.sleep(0)
            require_current_pin()
            return await original_invoke(*args, **kwargs)

        def capture_append(*args, **kwargs):
            if pins:
                require_current_pin()
                guarded_order.append("append")
            return original_append(*args, **kwargs)

        with mock.patch.object(
            self.runtime,
            "_session_artifact_binding",
            side_effect=capture_binding,
        ), mock.patch.object(
            self.runtime,
            "_recommendation_request_draft",
            side_effect=capture_request,
        ), mock.patch.object(
            self.runtime,
            "_recommendation_basis",
            side_effect=capture_basis,
        ), mock.patch.object(
            self.runtime,
            "_recommendation_source_fingerprint",
            side_effect=capture_fingerprint,
        ), mock.patch.object(
            SessionArtifactBinding,
            "acquire_generation_pin",
            new=capture_pin,
        ), mock.patch.object(
            self.runtime,
            "_prepare_session_reference_operation",
            side_effect=capture_prepare,
        ), mock.patch.object(
            self.runtime,
            "_build_guarded_contextual_definition",
            side_effect=capture_definition,
        ), mock.patch.object(
            self.runtime,
            "_session_context_state",
            side_effect=capture_context_state,
        ), mock.patch.object(
            self.runtime,
            "_invoke_context_with_recovery",
            side_effect=capture_invoke,
        ), mock.patch.object(
            self.runtime,
            "_append_drafts",
            side_effect=capture_append,
        ):
            _conversation_id, turn = await self.recommend()

        self.assertIsNotNone(turn.recommendation_request)
        self.assertGreaterEqual(len(invocation_bindings), 1)
        self.assertEqual(len(request_bindings), 2)
        self.assertEqual(len(basis_bindings), 2)
        self.assertEqual(len(fingerprint_bindings), 4)
        invocation_binding = request_bindings[0]
        self.assertTrue(
            any(binding is invocation_binding for binding in invocation_bindings)
        )
        self.assertTrue(
            all(
                binding is invocation_binding
                for binding in (
                    *request_bindings,
                    *basis_bindings,
                    *fingerprint_bindings,
                )
            )
        )
        self.assertEqual(guarded_order[0], "pin")
        self.assertLess(guarded_order.index("pin"), guarded_order.index("references"))
        self.assertLess(guarded_order.index("pin"), guarded_order.index("context"))
        self.assertLess(guarded_order.index("pin"), guarded_order.index("definition"))
        self.assertIn("append", guarded_order)
        self.assertEqual(_generation_pin_descriptors(pins[0][1]), ())

    async def test_lead_recommendation_fails_closed_after_binding_replacement(self):
        conversation_id = self.conversation("Replace during recommendation")
        self.backend.enqueue(recommendation())
        original_request = self.runtime._recommendation_request_draft
        original_acquire_pin = SessionArtifactBinding.acquire_generation_pin
        pins = []
        replacement = self.replace_session_worktree()
        entered = False
        calls = 0

        def replace_after_validation(*args, **kwargs):
            nonlocal entered, calls
            result = original_request(*args, **kwargs)
            calls += 1
            if calls == 1:
                replacement.__enter__()
                entered = True
            return result

        def capture_pin(binding):
            pin = original_acquire_pin(binding)
            pins.append(pin)
            return pin

        try:
            with mock.patch.object(
                self.runtime,
                "_recommendation_request_draft",
                side_effect=replace_after_validation,
            ), mock.patch.object(
                SessionArtifactBinding,
                "acquire_generation_pin",
                new=capture_pin,
            ):
                turn = await self.runtime.lead_turn(
                    self.session_id,
                    conversation_id,
                    "Recommend preparing this finished outline.\n"
                    f"Finished candidate:\n{DRAFT}",
                )
        finally:
            if entered:
                replacement.__exit__(None, None, None)

        self.assertEqual(calls, 1)
        self.assertIs(turn.state, c.CollaborationStepState.RECOVERY_REQUIRED)
        self.assertIs(
            turn.next_action,
            c.CollaborationNextAction.REPAIR_AUTHORITATIVE_HISTORY,
        )
        self.assertIsNone(turn.recommendation_request)
        self.assertFalse(
            self.payload_events(conversation_id, c.LeadRecommendationRequestPayload)
        )
        self.assertEqual(len(pins), 1)
        self.assertEqual(_generation_pin_descriptors(pins[0]), ())

    async def test_lead_generation_pin_closes_on_provider_cancel_and_append_failure(self):
        binding = compose_session_artifact_binding(
            self.control_workspace.root,
            self.session_id,
        )
        original_acquire_pin = SessionArtifactBinding.acquire_generation_pin

        for marker in (RuntimeError("provider failed"), asyncio.CancelledError()):
            pins = []

            def capture_pin(current):
                pin = original_acquire_pin(current)
                pins.append(pin)
                return pin

            with self.subTest(marker=type(marker).__name__), mock.patch.object(
                SessionArtifactBinding,
                "acquire_generation_pin",
                new=capture_pin,
            ), mock.patch.object(
                self.runtime,
                "_run_lead_provider_guarded_pinned",
                side_effect=marker,
            ), self.assertRaises(type(marker)) as caught:
                await self.runtime._run_lead_provider_guarded(
                    artifact_binding=binding,
                )
            self.assertIs(caught.exception, marker)
            self.assertEqual(len(pins), 1)
            self.assertEqual(_generation_pin_descriptors(pins[0]), ())

        for marker in (RuntimeError("provider failed"), asyncio.CancelledError()):
            pins = []
            replacement = self.replace_session_worktree()
            entered = False

            def capture_pin(current):
                pin = original_acquire_pin(current)
                pins.append(pin)
                return pin

            async def fail_after_replacement(**_kwargs):
                nonlocal entered
                replacement.__enter__()
                entered = True
                raise marker

            try:
                with self.subTest(
                    replacement_failure=type(marker).__name__
                ), mock.patch.object(
                    SessionArtifactBinding,
                    "acquire_generation_pin",
                    new=capture_pin,
                ), mock.patch.object(
                    self.runtime,
                    "_run_lead_provider_guarded_pinned",
                    side_effect=fail_after_replacement,
                ), self.assertRaises(SessionArtifactBindingError) as caught:
                    await self.runtime._run_lead_provider_guarded(
                        artifact_binding=binding,
                    )
            finally:
                if entered:
                    replacement.__exit__(None, None, None)
            self.assertIsNone(caught.exception.__cause__)
            if str(marker):
                self.assertNotIn(str(marker), str(caught.exception))
            self.assertEqual(len(pins), 1)
            self.assertEqual(_generation_pin_descriptors(pins[0]), ())

        pins = []
        binding_marker = SessionArtifactBindingError("generation changed")

        def capture_pin(current):
            pin = original_acquire_pin(current)
            pins.append(pin)
            return pin

        def reject_generation(current, pin):
            self.assertIs(current, binding)
            self.assertIs(pin, pins[0])
            raise binding_marker

        with mock.patch.object(
            SessionArtifactBinding,
            "acquire_generation_pin",
            new=capture_pin,
        ), mock.patch.object(
            SessionArtifactBinding,
            "require_generation_pin",
            new=reject_generation,
        ), mock.patch.object(
            self.runtime,
            "_run_lead_provider_guarded_pinned",
            return_value=object(),
        ), self.assertRaises(SessionArtifactBindingError) as caught:
            await self.runtime._run_lead_provider_guarded(
                artifact_binding=binding,
            )
        self.assertIs(caught.exception, binding_marker)
        self.assertEqual(len(pins), 1)
        self.assertEqual(_generation_pin_descriptors(pins[0]), ())

        pins = []
        append_marker = RuntimeError("durable append failed")

        def capture_pin(current):
            pin = original_acquire_pin(current)
            pins.append((current, pin))
            return pin

        async def fail_during_pinned_append(**_kwargs):
            current, pin = pins[0]
            current.require_generation_pin(pin)
            self.runtime._append_drafts(0, ())

        with mock.patch.object(
            SessionArtifactBinding,
            "acquire_generation_pin",
            new=capture_pin,
        ), mock.patch.object(
            self.runtime,
            "_append_drafts",
            side_effect=append_marker,
        ), mock.patch.object(
            self.runtime,
            "_run_lead_provider_guarded_pinned",
            side_effect=fail_during_pinned_append,
        ), self.assertRaises(RuntimeError) as caught:
            await self.runtime._run_lead_provider_guarded(
                artifact_binding=binding,
            )
        self.assertIs(caught.exception, append_marker)
        self.assertEqual(len(pins), 1)
        self.assertEqual(_generation_pin_descriptors(pins[0][1]), ())

    async def test_legacy_unscoped_recommendation_stays_readable_but_decisions_are_stale(self):
        for kind in (c.CollaborationConfirmationDecision.CONFIRM, c.CollaborationConfirmationDecision.REVISE):
            with self.subTest(kind=kind):
                conversation_id, current = await self.recommend()
                history = self.history(conversation_id)
                request = replace(current.recommendation_request, authoring_scope=None)
                source = next(event for event in history
                              if event.event_id == request.source_recommendation_event_id)
                legacy = rewrite_history(history, source.event_id,
                    payload=replace(source.payload, authoring_scope=None))
                legacy = rewrite_history(legacy, request.request_event_id, payload=request)
                self.assertIs(c.project_collaboration_history(legacy).current_recommendation_state,
                              c.LeadRecommendationState.PENDING)
                # Historical fixture installation only; the decisions below use the real runtime/store.
                self.runtime.conversation_storage_services.event_store._write_history(legacy)
                stored = self.history(conversation_id)
                images = tuple(c.encode_collaboration_event(event) for event in stored)
                self.assertEqual(stored, legacy)
                for event, image in zip(stored, images):
                    self.assertEqual(c.encode_collaboration_event(c.decode_collaboration_event(image)), image)
                    if event.event_id in {source.event_id, request.request_event_id}:
                        self.assertNotIn("authoring_scope", json.loads(image)["payload"])
                readable = c.project_collaboration_readable_history(stored)
                self.assertTrue(any(type(item.payload) is c.LeadRecommendationRequestPayload for item in readable))
                self.assertEqual(request.source_recommendation_digest, digest(source.payload.message))
                before, calls, refs = self.snapshots(), len(self.backend.calls), len(self.backend.reference_calls)
                choice = decision(replace(current, recommendation_request=request), kind)
                result = await self.runtime.decide_lead_recommendation(choice)
                self.assertIsNone(result.proposal)
                self.assertIsNone(result.artifact_proposal)
                self.assertIsNone(result.recommendation_request)
                self.assertIsNone(result.artifact_application_request)
                self.assertIsNone(result.story_commit_request)
                self.assertEqual((len(self.backend.calls), len(self.backend.reference_calls)), (calls, refs))
                self.assertEqual(self.snapshots(), before)
                events = self.history(conversation_id)
                self.assertEqual(tuple(c.encode_collaboration_event(event) for event in events[:len(stored)]), images)
                self.assertEqual(len(events), len(stored) + 2)
                accepted, closure = events[-2:]
                self.assertEqual(accepted.payload.decision, choice)
                self.assertIs(type(closure.payload), c.LeadRecommendationResultPayload)
                self.assertIs(closure.payload.state, c.LeadRecommendationState.STALE)
                self.assertEqual(closure.payload.decision_event_id, accepted.event_id)
                self.assertEqual((closure.payload.request_event_id, closure.payload.action_digest),
                                 (request.request_event_id, request.action_digest))
                self.assertEqual(accepted.batch_id, closure.batch_id)
                self.assertFalse(self.payload_events(conversation_id, c.LeadRecommendationStartedPayload))
                self.assertEqual(self.projection(conversation_id).artifact_objectives, ())
                self.assert_no_downstream(conversation_id)

    async def test_revise_cannot_drop_disclosed_domains_targets_or_switch_task(self):
        for changed_field in ("domains", "target_paths", "task_id"):
            with self.subTest(changed_field=changed_field):
                conversation_id = self.conversation("Preserve exact Revise obligations")
                def mixed_scope(call):
                    return lead._live_scope(call, activity="review", paths=(OUTLINE,),
                                            domains=("plot_structure", "tone_execution"))
                self.backend.enqueue(recommendation(authoring_scope=mixed_scope))
                offered = await self.runtime.lead_turn(self.session_id, conversation_id,
                    "Prepare review of this finished outline and its wording: " + DRAFT)
                request = offered.recommendation_request
                self.assertIsNotNone(request)
                self.assertEqual(tuple(domain.value for domain in request.authoring_scope.domains),
                                 ("plot_structure", "tone_execution"))
                self.assertEqual(request.authoring_scope.target_paths, (OUTLINE,))
                decoded = []
                def mutate(body):
                    scope = body["authoring_scope"]
                    scope[changed_field] = (["plot_structure"] if changed_field == "domains" else
                                            [] if changed_field == "target_paths" else "foreign_task")
                    # Keep this a structurally valid closed outcome; only its authority is wrong.
                    decoded.append(owner.StoryRuntime._decode_lead_outcome(json.dumps(body)))
                self.backend.enqueue(recommendation(mutate=mutate))
                observed = []
                validation_errors = []
                validate = owner.StoryRuntime._validate_recommendation_preparation
                def observe(outcome, capability):
                    observed.append(outcome)
                    try:
                        return validate(outcome, capability)
                    except owner._LeadOutcomeValidationError as error:
                        validation_errors.append(str(error))
                        raise
                before, calls = self.snapshots(), len(self.backend.calls)
                choice = decision(offered, c.CollaborationConfirmationDecision.REVISE)
                with mock.patch.object(owner.StoryRuntime, "_validate_recommendation_preparation", side_effect=observe):
                    rejected = await self.runtime.decide_lead_recommendation(choice)
                self.assertEqual(len(decoded), 1)
                self.assertEqual(decoded[0].authoring_scope.task_revision, request.authoring_scope.task_revision + 1)
                if changed_field == "task_id" and not observed:
                    # The live JSON contract admits only the disclosed task before semantic validation.
                    self.assertNotEqual(decoded[0].authoring_scope.task_id, request.authoring_scope.task_id)
                    self.assertNotIn("foreign_task", repr(self.backend.calls[-1].schema))
                else:
                    self.assertEqual(observed, decoded)
                    self.assertEqual(validation_errors, ["preparation must retain its disclosed authoring task and scope"])
                self.assertIsNone(rejected.proposal)
                self.assertIsNone(rejected.artifact_proposal)
                self.assertIsNone(rejected.recommendation_request)
                self.assertEqual(len(self.backend.calls), calls + 1)
                self.assertIs(self.projection(conversation_id).current_recommendation_state,
                              c.LeadRecommendationState.FAILED)
                self.assertEqual(self.projection(conversation_id).artifact_objectives, ())
                self.assertEqual(self.snapshots(), before)
                self.assert_no_downstream(conversation_id)

    async def test_fresh_outcome_schema_requires_typed_actions_only_for_recommendations(self):
        conversation_id, turn = await self.recommend()
        call = self.backend.calls[-1]
        expected = {"scope", "evidence_status", "evidence_ids", "kind", "message", "proposal",
                    "artifact_intent", "objective_intent", "recommended_action", "authoring_scope"}
        value = json.loads(recommendation()(call, None))["action"]["result"]
        self.assertEqual(set(value), expected)
        self.assertIn("recommended_action", repr(call.schema))
        self.assertIsNone(self.projection(conversation_id).current_proposal)
        invalid = []
        missing = dict(value)
        missing.pop("recommended_action")
        invalid.append(missing)
        invalid += [dict(value, recommended_action=None),
                    dict(value, kind="answer"),
                    dict(value, recommended_action={**value["recommended_action"], "apply": True}),
                    dict(value, objective_intent={"action": "start", "request": "Unconfirmed task",
                         "target_paths": [OUTLINE], "next_step": "clarify", "draft_source_event_ids": []})]
        for index, bad in enumerate(invalid):
            with self.subTest(index=index):
                cid = self.conversation(f"Invalid typed recommendation {index}")
                def response(request, _provider, body=bad):
                    return lead._provider_envelope(request, body)
                self.backend.enqueue(response)
                self.backend.enqueue(response)
                result = await self.runtime.lead_turn(self.session_id, cid, "Reject a malformed action.")
                self.assertIsNone(result.recommendation_request)
                self.assertIs(result.next_action, c.CollaborationNextAction.RETRY_LEAD_TURN)
                self.assertEqual(self.projection(cid).artifact_objectives, ())
                self.assert_no_downstream(cid)

    async def test_artifact_confirm_runs_one_preparation_and_stops_at_exact_review(self):
        before = self.snapshots()
        conversation_id, turn = await self.recommend()
        request = turn.recommendation_request
        before_events = self.history(conversation_id)
        self.assertIs(request.authoring_scope.activity, AuthoringActivity.REVIEW)
        self.assertFalse(self.payload_events(conversation_id, c.CollaborationAssignmentDispatchPayload))
        self.assertEqual(self.projection(conversation_id).artifact_objectives, ())
        self.assertEqual(self.snapshots(), before)
        producer = preparation(turn)
        def observed(call, provider):
            events = self.history(conversation_id)
            decision_event = self.payload_events(conversation_id, c.LeadRecommendationDecisionPayload)[0]
            started = self.payload_events(conversation_id, c.LeadRecommendationStartedPayload)[0]
            self.assertEqual(decision_event.batch_id, started.batch_id)
            self.assertEqual(started.payload.decision_event_id, decision_event.event_id)
            self.assertEqual(started.payload.request_event_id, request.request_event_id)
            material = lead._workflow_material(call.payload)["structured_recommendation_execution"]
            self.assertEqual(material["request"]["action_digest"], request.action_digest)
            self.assertEqual(material["decision"]["decision"]["kind"], "confirm")
            self.assertEqual(material["start"]["execution_attempt_id"], started.payload.execution_attempt_id)
            self.assertIn(request.instruction, json.dumps(material, ensure_ascii=False))
            self.assertEqual(sum(type(e.payload) is c.CollaborationMessagePayload and
                e.direction is c.CollaborationDirection.USER_TO_LEAD for e in events), 1)
            return producer(call, provider)
        self.backend.enqueue(observed)
        calls = len(self.backend.calls)
        with mock.patch.object(self.runtime, "lead_turn", side_effect=AssertionError("recursive public Lead")):
            prepared = await self.runtime.session(self.session_id).decide_lead_recommendation(decision(turn))
        self.assertIs(type(prepared), c.LeadTurnResult)
        self.assertEqual(len(self.backend.calls) - calls, 1)
        self.assertIs(prepared.kind, c.LeadOutcomeKind.PROPOSAL)
        self.assertIs(prepared.next_action, c.CollaborationNextAction.SUBMIT_CONFIRMATION)
        self.assertIsNone(prepared.recommendation_request)
        self.assertIsNone(prepared.artifact_application_request)
        self.assertIsNone(prepared.story_commit_request)
        self.assertEqual(prepared.proposal.authoring_scope, request.authoring_scope)
        self.assertEqual(prepared.proposal.participants, (PLOT_ARCHITECT,))
        self.assertTrue(all(item.mode.value == "review" for item in prepared.proposal.assignments))
        candidate = prepared.artifact_proposal.candidate.candidate
        operation = candidate.change_set.operations[0]
        self.assertEqual((operation.operation_kind.value, operation.path, operation.content),
                         ("replace", OUTLINE, DRAFT.encode()))
        objective = self.objective(conversation_id)
        self.assertIs(objective.state, c.ArtifactObjectiveState.OPEN)
        self.assertIs(type(objective.facts), c.ArtifactObjectiveFactsV2)
        self.assertEqual(objective.candidate_binding.candidate_digest, candidate.candidate_digest)
        self.assertEqual(objective.draft_sources, request.draft_sources)
        self.assertEqual(self.history(conversation_id)[:len(before_events)], before_events)
        projection = self.assert_action_closed(conversation_id)
        result_event = self.payload_events(conversation_id, c.LeadRecommendationResultPayload)[0]
        self.assertLess(result_event.sequence, self.history(conversation_id)[-1].sequence)
        self.assertIs(projection.current_state, c.CollaborationStepState.AWAITING_CONFIRMATION)
        self.assertEqual(self.snapshots(), before)

    async def test_review_objective_lineage_cannot_relabel_required_drafts(self):
        conversation_id, turn = await self.recommend()
        events = self.history(conversation_id)
        projection = self.projection(conversation_id)
        validate = owner.StoryRuntime._validate_objective_routing
        with mock.patch.object(owner.StoryRuntime, "_validate_objective_routing", wraps=validate) as observed:
            prepared = await self.confirm(turn)
        self.assertIsNotNone(prepared.artifact_proposal)
        self.assertTrue(observed.called)
        outcome = next(call.args[0] for call in observed.call_args_list
                       if call.args[0].objective_intent is not None)
        # These are real typed provider and durable event values from fresh REVIEW.
        validate(outcome, projection, events, events)
        source_id = turn.recommendation_request.source_recommendation_event_id
        for activity in (AuthoringActivity.DRAFT, AuthoringActivity.REVISE):
            changed = tuple(replace(event, payload=replace(event.payload,
                authoring_scope=replace(event.payload.authoring_scope, activity=activity)))
                if event.event_id == source_id else event for event in events)
            for source_events, canonical_events in ((changed, events), (events, changed)):
                with self.subTest(activity=activity, selected_source=source_events is changed):
                    with self.assertRaises(owner._LeadOutcomeValidationError):
                        validate(outcome, projection, source_events, canonical_events)
        self.assertEqual(self.history(conversation_id)[:len(events)], events)
        self.assert_no_downstream(conversation_id)

    async def test_mixed_recommendation_disclosure_and_preparation_cannot_omit_plot(self):
        selected = action(kind="prepare_consultation", specialist_ids=(PLOT_ARCHITECT, EXECUTION_SPECIALIST))
        def scope(call):
            return lead._live_scope(call, activity="review", paths=(OUTLINE,), domains=("tone_execution",))
        bad = recommendation(recommended_action=selected, authoring_scope=scope,
            mutate=lambda value: value["recommended_action"].update(specialist_ids=[EXECUTION_SPECIALIST]))
        conversation_id = self.conversation("Reject missing Plot disclosure")
        self.backend.enqueue(bad)
        self.backend.enqueue(bad)
        rejected = await self.runtime.lead_turn(self.session_id, conversation_id,
            "Review this finished outline and its phrasing: " + DRAFT)
        self.assertIsNone(rejected.recommendation_request)
        self.assertIs(rejected.next_action, c.CollaborationNextAction.RETRY_LEAD_TURN)
        self.assert_no_downstream(conversation_id)
        # A complete request binds both owners before any preparation call.
        conversation_id = self.conversation("Preserve mixed scope at Confirm")
        self.backend.enqueue(recommendation(recommended_action=action(kind="prepare_consultation",
            specialist_ids=(PLOT_ARCHITECT, EXECUTION_SPECIALIST)), authoring_scope=scope))
        offered = await self.runtime.lead_turn(self.session_id, conversation_id,
            "Review this finished outline and its phrasing: " + DRAFT)
        request = offered.recommendation_request
        self.assertIsNotNone(request)
        self.assertEqual(request.specialist_ids, (PLOT_ARCHITECT, EXECUTION_SPECIALIST))
        before, calls = self.history(conversation_id), len(self.backend.calls)
        with self.assertRaises(BackendProtocolError):
            await self.runtime.decide_lead_recommendation(decision(offered, action_digest=digest("scope omitted")))
        self.assertEqual(self.history(conversation_id), before)
        self.assertEqual(len(self.backend.calls), calls)
        def omit_plot(body):
            body["proposal"]["assignments"] = [item for item in body["proposal"]["assignments"]
                                                if item["agent_id"] != PLOT_ARCHITECT]
        self.backend.enqueue(preparation(offered, mutate=omit_plot))
        failed = await self.runtime.decide_lead_recommendation(decision(offered))
        self.assertIsNone(failed.proposal)
        self.assertIsNone(failed.artifact_proposal)
        self.assertEqual(len(self.backend.calls), calls + 1)
        self.assert_no_downstream(conversation_id)

    async def test_confirm_rechecks_plot_obligation_added_to_same_task(self):
        from tests.test_specialist_routing_runtime import scoped_proposal
        conversation_id = self.conversation("Tone-only finished candidate review")
        base = recommendation(recommended_action=action(
            kind="prepare_consultation", specialist_ids=(EXECUTION_SPECIALIST,)))
        def tone_only(call, provider):
            value = json.loads(base(call, provider))
            status = next(item for item in call.payload["core_statuses"]
                          if item["locator"] == "core_documents/narrative_rules.md")
            body = value["action"]["result"]
            body["evidence_ids"] = [status["evidence_id"]]
            body["evidence_status"] = status["status"] if status["status"] in {"empty", "missing"} else "incomplete"
            return json.dumps(value, ensure_ascii=False)
        self.backend.enqueue(tone_only)
        turn = await self.runtime.lead_turn(self.session_id, conversation_id,
            "Review only wording and tone of this finished candidate; chronology is outside this request: " + DRAFT)
        self.assertIsNotNone(turn.recommendation_request)
        request = turn.recommendation_request
        self.assertEqual(request.specialist_ids, (EXECUTION_SPECIALIST,))
        other = self.conversation("Expanded same-task review")
        self.backend.enqueue(scoped_proposal(PLOT_ARCHITECT, EXECUTION_SPECIALIST,
            activity="review", paths=(), domains=("plot_structure", "tone_execution"),
            selected=request.authoring_scope.task_id))
        expanded = await self.runtime.lead_turn(self.session_id, other,
            "For the same finished candidate, also assess chronology: " + DRAFT)
        self.assertIsNotNone(expanded.proposal)
        self.assertEqual(expanded.proposal.authoring_scope.task_id, request.authoring_scope.task_id)
        self.assertEqual(expanded.proposal.participants, (PLOT_ARCHITECT, EXECUTION_SPECIALIST))
        calls = len(self.backend.calls)
        stale = await self.runtime.decide_lead_recommendation(decision(turn))
        self.assertIsNone(stale.proposal)
        self.assertIsNone(stale.artifact_proposal)
        self.assertEqual(len(self.backend.calls), calls)
        self.assertIs(self.projection(conversation_id).current_recommendation_state,
                      c.LeadRecommendationState.STALE)
        self.assert_no_downstream(conversation_id)

    async def test_scoped_consultation_revision_can_offer_separately_confirmed_recommendation(self):
        conversation_id = self.conversation("Scoped revised recommendation")
        proposed = await self.proposal(conversation_id, text="Review this finished user candidate: " + DRAFT)
        self.backend.enqueue(recommendation(recommended_action=action(kind="prepare_consultation",
            source_ids=("user_input",))))
        revised = await self.runtime.decide_lead_step(lead._decision(
            proposed, c.CollaborationConfirmationDecision.REVISE))
        request = revised.turn.recommendation_request
        self.assertIsNotNone(request)
        self.assertEqual(request.authoring_scope.task_id, proposed.proposal.authoring_scope.task_id)
        self.assertEqual(request.authoring_scope.task_revision, proposed.proposal.authoring_scope.task_revision + 1)
        self.assertIsNone(revised.turn.proposal)
        calls = len(self.backend.calls)
        prepared = await self.confirm(revised.turn)
        self.assertEqual(len(self.backend.calls), calls + 1)
        self.assertEqual(prepared.proposal.authoring_scope, request.authoring_scope)
        self.assert_no_downstream(conversation_id)

    async def test_consultation_confirm_without_objective_stops_at_matching_proposal(self):
        conversation_id, turn = await self.recommend(recommended_action=action(
            kind="prepare_consultation", instruction="Prepare this consultation; ${core_documents/narrative_rules.md} is text only.",
            source_ids=("user_input",)))
        request = turn.recommendation_request
        before, calls, references = self.snapshots(), len(self.backend.calls), len(self.backend.reference_calls)
        result = await self.confirm(turn, through_session=True)
        self.assertIs(type(result), c.LeadTurnResult)
        self.assertEqual(len(self.backend.calls) - calls, 1)
        self.assertEqual(len(self.backend.reference_calls), references)
        self.assertEqual(result.proposal.purpose, request.purpose)
        self.assertEqual(tuple(a.specialist_id for a in result.proposal.assignments), request.specialist_ids)
        expected_sources = {i.input_id for i in request.basis.inputs} | {"recommendation_request", "recommendation_decision"}
        self.assertEqual({i for a in result.proposal.assignments for i in a.input_ids}, expected_sources)
        self.assertEqual(self.projection(conversation_id).artifact_objectives, ())
        self.assertIsNone(result.artifact_proposal)
        self.assert_action_closed(conversation_id)
        self.assertEqual(self.snapshots(), before)

    async def test_confirmed_continuation_preserves_existing_objective_facts(self):
        conversation_id, _ = await self.start()
        candidate = await self.prepare(conversation_id)
        await self.runtime.decide_lead_step(lead._decision(candidate, c.CollaborationConfirmationDecision.REJECT))
        previous = self.objective(conversation_id)
        # No new draft alias: the immutable request must disclose inherited selected drafts now.
        _, turn = await self.recommend(conversation_id, recommended_action=action(
            source_ids=("user_input",), objective_id=previous.facts.objective_id))
        request = turn.recommendation_request
        self.assertEqual(request.draft_sources, previous.draft_sources)
        self.assertTrue({s.event_id for s in request.draft_sources} <= {i.input_id for i in request.basis.inputs})
        downstream_types = {c.CollaborationAssignmentDispatchPayload,
            c.ArtifactApplicationDecisionPayload, c.ArtifactApplicationResultPayload,
            c.StoryCommitDecisionPayload, c.StoryCommitResultPayload}
        downstream_event_ids = tuple(e.event_id for e in self.history(conversation_id)
                                     if type(e.payload) in downstream_types)
        call_record_boundary = len(self.backend.call_records)
        await self.confirm(turn)
        current = self.objective(conversation_id)
        self.assertEqual(current.facts, previous.facts)
        self.assertEqual(current.draft_sources, request.draft_sources)
        transition = next(e for e in self.history(conversation_id) if e.event_id == current.latest_event_id)
        self.assertEqual(transition.payload.previous_objective_event_id, previous.latest_event_id)
        self.assertIs(type(current.facts), c.ArtifactObjectiveFacts)
        projection = self.projection(conversation_id)
        self.assertIs(projection.current_recommendation_state, c.LeadRecommendationState.COMPLETED)
        closure = projection.current_recommendation_result
        self.assertIsNotNone(closure)
        events = {event.event_id: event for event in self.history(conversation_id)}
        if closure.start_event_id is not None:
            start = events[closure.start_event_id]
            source = events[closure.lead_result_event_id]
            self.assertEqual((source.turn_id, source.step_id), (start.turn_id, start.step_id))
            self.assertLess(start.sequence, source.sequence)
        self.assertEqual(tuple(e.event_id for e in self.history(conversation_id)
                               if type(e.payload) in downstream_types), downstream_event_ids)
        self.assertTrue(all(agent == lead.LEAD_ORCHESTRATOR
                            for agent, _ in self.backend.call_records[call_record_boundary:]))

    async def test_exact_stale_duplicate_and_concurrent_decisions_never_reuse_authority(self):
        conversation_id, turn = await self.recommend()
        choice = decision(turn)
        before, calls = self.history(conversation_id), len(self.backend.calls)
        foreign = self.conversation("Foreign decision")
        from story_writing_agents.errors import ConversationNotFoundError
        for change in ({"request_event_id": "foreign"}, {"request_tail_sequence": choice.request_tail_sequence + 1},
                       {"request_id": "foreign"}, {"action_id": "foreign"}, {"action_revision": 2},
                       {"action_digest": digest("foreign")}, {"conversation_id": foreign}, {"session_id": "foreign_session"}):
            expected = ConversationNotFoundError if "session_id" in change else BackendProtocolError
            with self.subTest(change=change), self.assertRaises(expected):
                await self.runtime.decide_lead_recommendation(replace(choice, **change))
        self.assertEqual(self.history(conversation_id), before)
        self.assertEqual(len(self.backend.calls), calls)
        entered, release = asyncio.Event(), asyncio.Event()
        producer = preparation(turn)
        async def held(call, provider):
            entered.set()
            await release.wait()
            return producer(call, provider)
        self.backend.enqueue(held)
        first = asyncio.create_task(self.runtime.decide_lead_recommendation(choice))
        await asyncio.wait_for(entered.wait(), 2)
        second = asyncio.create_task(self.runtime.decide_lead_recommendation(choice))
        try:
            await asyncio.sleep(0)
            self.assertFalse(second.done())
            release.set()
            results = await asyncio.gather(first, second, return_exceptions=True)
        finally:
            release.set()
            for task in (first, second):
                if not task.done(): task.cancel()
            await asyncio.gather(first, second, return_exceptions=True)
        self.assertIs(type(results[0]), c.LeadTurnResult)
        self.assertIsInstance(results[1], BackendProtocolError)
        self.assertEqual(len(self.backend.calls), calls + 1)
        self.assertEqual(len(self.payload_events(conversation_id, c.LeadRecommendationStartedPayload)), 1)
        with self.assertRaises(BackendProtocolError):
            await self.runtime.decide_lead_recommendation(choice)
        self.assertEqual(len(self.backend.calls), calls + 1)

    async def test_input_profile_route_and_budget_drift_stop_before_submission(self):
        for mode in ("content", "identical_bytes_metadata", "profile", "policy"):
            conversation_id, turn = await self.recommend()
            calls = len(self.backend.calls)
            path = self.workspace.path(OUTLINE)
            scope = mock.patch.object(self.backend, "execution_profile_fingerprint", return_value=digest("changed profile")) if mode == "profile" else (
                mock.patch.object(owner, "_BUDGET_POLICY_FINGERPRINT", digest("changed policy")) if mode == "policy" else mock.patch.object(self.backend, "backend_overrides", {}))
            if mode == "content": path.write_text("Changed after disclosure.\n")
            elif mode == "identical_bytes_metadata":
                sibling = path.with_suffix(".tmp")
                sibling.write_bytes(path.read_bytes())
                sibling.replace(path)
            with scope:
                result = await self.runtime.decide_lead_recommendation(decision(turn))
            self.assertEqual(len(self.backend.calls), calls)
            self.assertIsNone(result.recommendation_request)
            self.assert_action_closed(conversation_id, c.LeadRecommendationState.STALE)
            path.write_bytes(b"")
        with mock.patch.object(owner, "_CONTEXT_INVOCATION_CALL_LIMIT", 1):
            conversation_id, turn = await self.recommend()
        self.assertEqual(turn.recommendation_request.max_preparation_physical_calls, 1)
        self.backend.enqueue(lead._search_history)
        calls = len(self.backend.calls)
        result = await self.runtime.decide_lead_recommendation(decision(turn))
        self.assertLessEqual(len(self.backend.calls) - calls, 1)
        self.assertIsNone(result.artifact_proposal)
        self.assert_no_downstream(conversation_id)

    async def test_reject_closes_without_calls_or_objective_transition(self):
        conversation_id, _ = await self.start()
        objective = self.objective(conversation_id)
        _, turn = await self.recommend(conversation_id, recommended_action=action(objective_id=objective.facts.objective_id))
        downstream_types = {c.CollaborationAssignmentDispatchPayload,
            c.ArtifactApplicationDecisionPayload, c.ArtifactApplicationResultPayload,
            c.StoryCommitDecisionPayload, c.StoryCommitResultPayload}
        downstream_event_ids = tuple(e.event_id for e in self.history(conversation_id)
                                     if type(e.payload) in downstream_types)
        call_records = tuple(self.backend.call_records)
        before, calls = self.snapshots(), len(self.backend.calls)
        result = await self.runtime.decide_lead_recommendation(decision(turn, c.CollaborationConfirmationDecision.REJECT))
        self.assertIs(result.kind, c.LeadOutcomeKind.ANSWER)
        self.assertEqual(len(self.backend.calls), calls)
        self.assertEqual(self.objective(conversation_id), objective)
        self.assertFalse(self.payload_events(conversation_id, c.LeadRecommendationStartedPayload))
        projection = self.projection(conversation_id)
        self.assertIs(projection.current_recommendation_state, c.LeadRecommendationState.REJECTED)
        closure = projection.current_recommendation_result
        self.assertIsNotNone(closure)
        events = {event.event_id: event for event in self.history(conversation_id)}
        if closure.start_event_id is not None:
            start = events[closure.start_event_id]
            source = events[closure.lead_result_event_id]
            self.assertEqual((source.turn_id, source.step_id), (start.turn_id, start.step_id))
            self.assertLess(start.sequence, source.sequence)
        self.assertEqual(tuple(e.event_id for e in self.history(conversation_id)
                               if type(e.payload) in downstream_types), downstream_event_ids)
        self.assertEqual(tuple(self.backend.call_records), call_records)
        self.assertEqual(self.snapshots(), before)

    async def test_revise_supersedes_and_requires_fresh_confirmation(self):
        conversation_id, turn = await self.recommend()
        request = turn.recommendation_request
        before, calls, refs = self.snapshots(), len(self.backend.calls), len(self.backend.reference_calls)
        self.backend.enqueue(recommendation(message="Revised synthetic outline", recommended_action=action()))
        revised = await self.runtime.decide_lead_recommendation(decision(turn, c.CollaborationConfirmationDecision.REVISE,
            revision_request="Keep the draft; ${core_documents/narrative_rules.md} remains undisclosed text."))
        self.assertEqual(len(self.backend.calls) - calls, 1)
        self.assertEqual(len(self.backend.reference_calls), refs)
        self.assertIsNone(revised.artifact_proposal)
        self.assertEqual(self.projection(conversation_id).artifact_objectives, ())
        new = revised.recommendation_request
        self.assertEqual((new.action_id, new.action_revision), (request.action_id, request.action_revision + 1))
        self.assertNotEqual(new.action_digest, request.action_digest)
        self.assertEqual(new.authoring_scope.task_id, request.authoring_scope.task_id)
        self.assertEqual(new.authoring_scope.task_revision, request.authoring_scope.task_revision + 1)
        self.assertEqual(new.authoring_scope.domains, request.authoring_scope.domains)
        self.assertEqual(new.authoring_scope.target_paths, request.authoring_scope.target_paths)
        self.assertGreater(new.physical_calls_spent, request.physical_calls_spent)
        origin = self.payload_events(conversation_id, c.LeadRecommendationDecisionPayload)[0]
        self.assertIs(origin.payload.decision.kind, c.CollaborationConfirmationDecision.REVISE)
        closure = self.payload_events(conversation_id, c.LeadRecommendationResultPayload)[0]
        self.assertIs(closure.payload.state, c.LeadRecommendationState.SUPERSEDED)
        self.assertLess(closure.sequence, new.tail_sequence)
        with self.assertRaises(BackendProtocolError):
            await self.runtime.decide_lead_recommendation(decision(turn))
        self.assertEqual(len(self.backend.calls), calls + 1)
        await self.confirm(revised, text="Revised synthetic outline")
        self.assertIs(type(self.objective(conversation_id).facts), c.ArtifactObjectiveFactsV2)
        self.assertEqual(self.objective(conversation_id).facts.origin_recommendation_request_event_id, new.request_event_id)
        self.assertEqual(self.snapshots(), before)
        self.assert_no_downstream(conversation_id)

    async def test_ordinary_input_supersedes_with_real_user_origin_and_contextual_approval(self):
        conversation_id, turn = await self.recommend()
        request = turn.recommendation_request
        before = self.snapshots()
        source = request.draft_sources[0]
        producer = artifact._artifact_outcome(
            operations=lambda call, _: [{"kind": "replace", "evidence_id": artifact._status_id(call, OUTLINE), "new_text": DRAFT}],
            evidence_status="empty", evidence_ids=lambda call, _: [artifact._status_id(call, OUTLINE)],
            objective_intent={"action": "start", "request": "Prepare the approved draft.", "target_paths": [OUTLINE],
                "next_step": "prepare", "draft_source_event_ids": [source.event_id]})
        def scoped_outcome(call, provider):
            envelope = json.loads(producer(call, provider))
            envelope["action"]["result"]["authoring_scope"] = c.authoring_scope_to_dict(request.authoring_scope)
            return json.dumps(envelope)
        self.backend.enqueue(scoped_outcome)
        result = await self.runtime.lead_turn(self.session_id, conversation_id, "네, 그 초안 그대로 준비해 줘.")
        self.assertIsNotNone(result.artifact_proposal)
        objective = self.objective(conversation_id)
        self.assertIs(type(objective.facts), c.ArtifactObjectiveFacts)
        events = self.history(conversation_id)
        user = next(e for e in events if e.event_id == objective.facts.origin_user_event_id)
        closure = self.payload_events(conversation_id, c.LeadRecommendationResultPayload)[0]
        self.assertEqual((closure.payload.superseding_user_event_id, closure.batch_id), (user.event_id, user.batch_id))
        self.assertEqual(closure.sequence, user.sequence + 1)
        self.assertNotIn(OUTLINE, user.payload.content)
        self.assertNotIn(DRAFT, user.payload.content)
        self.assertFalse(self.payload_events(conversation_id, c.LeadRecommendationDecisionPayload))
        self.assertFalse(self.payload_events(conversation_id, c.LeadRecommendationStartedPayload))
        self.assertEqual(objective.draft_sources, request.draft_sources)
        self.assertEqual(self.snapshots(), before)
        self.assert_no_downstream(conversation_id)

    async def test_preparation_outputs_obey_action_scope_and_never_loop(self):
        for mode in ("changed_target", "changed_source", "new_recommendation", "advice", "question"):
            conversation_id, turn = await self.recommend()
            before, calls = self.snapshots(), len(self.backend.calls)
            if mode == "question": response = lead._visible_outcome("question", "Which ending should resolve the missing choice?")
            elif mode == "advice": response = lead._visible_outcome("answer", "Already finished.")
            elif mode == "new_recommendation": response = recommendation(message="Confirm this preparation again.")
            elif mode == "changed_source": response = preparation(turn, mutate=lambda v: v["objective_intent"].update(draft_source_event_ids=[]))
            else: response = preparation(turn, mutate=lambda v: v["objective_intent"].update(target_paths=["core_documents/narrative_rules.md"]))
            self.backend.enqueue(response)
            if mode != "question": self.backend.enqueue(response)
            result = await self.runtime.decide_lead_recommendation(decision(turn))
            self.assertLessEqual(len(self.backend.calls) - calls, 2)
            self.assertIsNone(result.recommendation_request)
            self.assertIsNone(result.artifact_proposal)
            self.assertEqual(self.projection(conversation_id).artifact_objectives, ())
            self.assertEqual(self.snapshots(), before)
            self.assert_no_downstream(conversation_id)
            if mode == "question":
                self.assertIs(result.kind, c.LeadOutcomeKind.QUESTION)
                self.assertIs(result.next_action, c.CollaborationNextAction.REPLY_TO_LEAD)
                self.assert_action_closed(conversation_id)

    async def test_pending_apply_commit_and_uncertainty_take_precedence_over_recommendation(self):
        from story_writing_agents.writing_artifacts import ArtifactApplicationError, ArtifactApplicationErrorCode
        conversation_id, turn = await self.recommend()
        reviewed = await self.review(await self.confirm(turn))
        before, calls, history = self.snapshots(), len(self.backend.calls), self.history(conversation_id)
        result = await self.runtime.decide_lead_recommendation(decision(turn))
        self.assertEqual(result.artifact_application_request, reviewed.artifact_application_request)
        self.assertIsNone(result.recommendation_request)
        self.assertEqual(self.history(conversation_id), history)
        self.assertEqual(len(self.backend.calls), calls)
        self.assertEqual(self.snapshots(), before)
        applied = await self.apply(reviewed)
        history, calls = self.history(conversation_id), len(self.backend.calls)
        result = await self.runtime.decide_lead_recommendation(decision(turn))
        self.assertEqual(result.story_commit_request, applied.commit_request)
        self.assertIsNone(result.recommendation_request)
        self.assertEqual(self.history(conversation_id), history)
        self.assertEqual(len(self.backend.calls), calls)
        await self.reject_commit(applied)
        other, offered = await self.recommend()
        # Restore the disposable artifact to empty before accepting the new request.
        # This new request intentionally observes the resulting drift and is closed stale.
        self.workspace.path(OUTLINE).write_bytes(b"")
        stale = await self.runtime.decide_lead_recommendation(decision(offered))
        self.assertIsNone(stale.recommendation_request)
        _, offered = await self.recommend(other)
        reviewed = await self.review(await self.confirm(offered))
        with mock.patch.object(
            type(self.runtime.artifacts),
            "apply_artifact_changes",
            side_effect=ArtifactApplicationError(
                ArtifactApplicationErrorCode.RECOVERY_REQUIRED
            ),
        ):
            await self.apply(reviewed)
        before, calls = self.history(other), len(self.backend.calls)
        for runtime in (self.runtime, StoryRuntime(self.control_workspace, self.backend)):
            blocked = await runtime.decide_lead_recommendation(decision(offered))
            self.assertIs(blocked.next_action, c.CollaborationNextAction.REPAIR_AUTHORITATIVE_HISTORY)
            self.assertIsNone(blocked.recommendation_request)
            self.assertEqual(self.history(other), before)
            self.assertEqual(len(self.backend.calls), calls)


class LeadRecommendationSynthesisTests(RecommendationCase):
    async def test_pin_cleanup_preserves_primary_and_never_skips_agent_release(
        self,
    ) -> None:
        cleanup = SystemExit("generation pin close interrupted")
        provider_failure = RuntimeError("provider failed")
        pin = SimpleNamespace(close=mock.Mock(side_effect=cleanup))
        self.runtime._close_story_commit_generation_pin(
            pin,
            failure=provider_failure,
        )
        pin.close.assert_called_once_with()
        self.assertIn(
            "pending story commit cleanup failed: SystemExit",
            provider_failure.__notes__,
        )

        persistence_failure = RuntimeError("result persistence failed")
        persisted_pin = SimpleNamespace(close=mock.Mock(side_effect=cleanup))
        binding = SimpleNamespace(require_generation_pin=mock.Mock())
        result = owner._CollaborationInvocationResult(
            object(),
            0,
            artifact_binding=binding,
            generation_pin=persisted_pin,
        )
        with mock.patch.object(
            self.runtime,
            "_persist_collaboration_results_with_generation_pin",
            side_effect=persistence_failure,
        ), self.assertRaises(RuntimeError) as caught:
            self.runtime._persist_collaboration_results_locked(
                synthesis_result=result,
            )
        self.assertIs(caught.exception, persistence_failure)
        persisted_pin.close.assert_called_once_with()
        self.assertIn(
            "pending story commit cleanup failed: SystemExit",
            persistence_failure.__notes__,
        )

        binding_failure = SessionArtifactBindingError("generation changed")
        lead_pin = SimpleNamespace(close=mock.Mock(side_effect=cleanup))
        lead_binding = SimpleNamespace(
            acquire_generation_pin=mock.Mock(return_value=lead_pin),
            require_generation_pin=mock.Mock(side_effect=binding_failure),
        )
        with mock.patch.object(
            self.runtime,
            "_run_lead_provider_guarded_pinned",
            side_effect=RuntimeError("lead provider failed"),
        ), self.assertRaises(SessionArtifactBindingError) as caught:
            await self.runtime._run_lead_provider_guarded(
                artifact_binding=lead_binding
            )
        self.assertIs(caught.exception, binding_failure)
        lead_pin.close.assert_called_once_with()
        self.assertIn(
            "pending story commit cleanup failed: SystemExit",
            binding_failure.__notes__,
        )

        stranded_pin = SimpleNamespace(close=mock.Mock(side_effect=cleanup))
        stranded_binding = SimpleNamespace(
            acquire_generation_pin=mock.Mock(return_value=stranded_pin)
        )
        bound_agent = object()
        release = mock.AsyncMock()
        reference_operation = SimpleNamespace(
            begin_invocation=mock.Mock(
                side_effect=RuntimeError("reference invocation failed")
            )
        )
        finalization_failure = KeyboardInterrupt("finalization interrupted")
        with mock.patch.object(
            owner.SessionSourceWorkspace,
            "from_artifact_binding",
            return_value=object(),
        ), mock.patch.object(
            self.runtime,
            "_prepare_session_reference_operation",
            return_value=reference_operation,
        ), mock.patch.object(
            self.runtime,
            "_validate_assignment_reference_proofs",
            return_value=None,
        ), mock.patch.object(
            self.runtime,
            "_build_guarded_contextual_definition",
            return_value=object(),
        ), mock.patch.object(
            self.runtime,
            "_acquire_bound_agent",
            new=mock.AsyncMock(return_value=bound_agent),
        ), mock.patch.object(
            self.runtime,
            "_fail_reference_operation",
            return_value=None,
        ), mock.patch.object(
            owner,
            "context_provider_failure_facts",
            side_effect=finalization_failure,
        ), mock.patch.object(
            self.runtime,
            "_release_bound_agent_use",
            new=release,
        ), self.assertRaises(KeyboardInterrupt) as caught:
            await self.runtime._run_collaboration_invocation_guarded(
                artifact_binding=stranded_binding,
                session_id=self.session_id,
                conversation_id="conversation",
                turn_id="turn",
                step_id="step",
                agent_id=LEAD_ORCHESTRATOR,
                operation_id="operation",
                result_correlation_id="result",
                inputs=(),
                material_value={},
                invocation_kind=owner.CollaborationInvocationKind.LEAD_SYNTHESIS,
                context_invocation_kind=owner.ContextInvocationKind.LEAD_SYNTHESIS,
                result_contract=object(),
                physical_call_limit=1,
                round_number=1,
                decoder=lambda answer, artifact_binding: answer,
                action_progress_sink=None,
            )
        self.assertIs(caught.exception, finalization_failure)
        stranded_pin.close.assert_called_once_with()
        release.assert_awaited_once_with(bound_agent)

    async def consultation(self, *, actionable, pause=False, mixed=False):
        conversation_id = self.conversation("Synthetic synthesis")
        proposed = await self.proposal(conversation_id, text="Review this finished user candidate: " + DRAFT)
        self.backend.enqueue_for(PLOT_ARCHITECT, lead._specialist_outcome())
        selected = action() if actionable else None
        base = lead._synthesis_outcome(message=DRAFT, recommended_action=selected)
        def synthesis(call, provider):
            value = json.loads(base(call, provider))
            result = value["action"]["result"]
            self.assertEqual(set(result), {"scope", "evidence_status", "evidence_ids", "message",
                "next_intent", "reply_event_ids", "unresolved_input_ids", "recommended_action", "authoring_scope"})
            if mixed:
                material = lead._workflow_material(call.payload)
                result["next_intent"] = {"relation": "continue_lineage", "purpose": "Review again.",
                    "assignments": [{"agent_id": PLOT_ARCHITECT, "instruction": "Review all sources.",
                        "input_ids": [*(i["input_id"] for i in material["inputs"]), *result["reply_event_ids"]]}]}
            return json.dumps(value, ensure_ascii=False)
        self.backend.enqueue_for(LEAD_ORCHESTRATOR, synthesis)
        if pause:
            with mock.patch.object(self.runtime, "_finalize_known_result_locked", side_effect=RuntimeError("known result paused")):
                with self.assertRaisesRegex(RuntimeError, "known result paused"):
                    await self.runtime.decide_lead_step(lead._decision(proposed, c.CollaborationConfirmationDecision.CONFIRM))
            result = None
        else:
            result = await self.runtime.decide_lead_step(lead._decision(proposed, c.CollaborationConfirmationDecision.CONFIRM))
        return conversation_id, proposed, result

    async def test_actionable_synthesis_requests_after_terminal_and_plain_synthesis_is_answer(self):
        before = self.snapshots()
        for actionable in (False, True):
            conversation_id, proposed, result = await self.consultation(actionable=actionable)
            turn = result.turn
            self.assertIs(turn.kind, c.LeadOutcomeKind.RECOMMENDATION if actionable else c.LeadOutcomeKind.ANSWER)
            events = self.history(conversation_id)
            synthesis = self.payload_events(conversation_id, c.CollaborationLeadSynthesisPayload)[0]
            self.assertFalse(hasattr(synthesis.payload, "recommended_action"))
            if actionable:
                request = turn.recommendation_request
                self.assertEqual((request.source_proposal_id, request.source_proposal_revision,
                    request.source_proposal_digest, request.source_execution_attempt_id),
                    (proposed.proposal.proposal_id, proposed.proposal.proposal_revision,
                     proposed.proposal.proposal_digest, synthesis.payload.execution_attempt_id))
                self.assertEqual(request.source_recommendation_event_id, synthesis.event_id)
                self.assertEqual(request.source_recommendation_digest, digest(DRAFT))
                self.assertNotEqual(turn.step_id, synthesis.step_id)
                self.assertEqual(events[-1].batch_id, synthesis.batch_id)
                self.assertIs(events[-2].payload.state, c.CollaborationStepState.COMPLETED)
                self.assertEqual(events[-2].step_id, proposed.step_id)
                self.assertEqual(request.physical_calls_spent, 3)
                original = tuple(c.encode_collaboration_event(e) for e in events)
                prepared = await self.confirm(turn)
                self.assertEqual(prepared.step_id, turn.step_id)
                self.assertEqual(tuple(c.encode_collaboration_event(e) for e in self.history(conversation_id)[:len(events)]), original)
                self.assertEqual(self.objective(conversation_id).draft_sources, request.draft_sources)
            else:
                self.assertIsNone(turn.recommendation_request)
                self.assertIs(turn.state, c.CollaborationStepState.COMPLETED)
            self.assertEqual(self.snapshots(), before)

    async def test_synthesis_recommendation_reuses_invocation_artifact_binding(self):
        conversation_id = self.conversation("Synthesis binding")
        proposed = await self.proposal(
            conversation_id,
            text="Review this finished user candidate: " + DRAFT,
        )
        self.backend.enqueue_for(PLOT_ARCHITECT, lead._specialist_outcome())
        self.backend.enqueue_for(
            LEAD_ORCHESTRATOR,
            lead._synthesis_outcome(
                message=DRAFT,
                recommended_action=action(),
            ),
        )
        request_bindings = []
        basis_bindings = []
        fingerprint_bindings = []
        pins = []
        guarded_order = []
        original_request = self.runtime._recommendation_request_draft
        original_basis = self.runtime._recommendation_basis
        original_fingerprint = self.runtime._recommendation_source_fingerprint
        original_acquire_pin = SessionArtifactBinding.acquire_generation_pin
        original_prepare = self.runtime._prepare_session_reference_operation
        original_definition = self.runtime._build_guarded_contextual_definition
        original_context_state = self.runtime._session_context_state
        original_invoke = self.runtime._invoke_context_with_recovery
        original_persist = (
            self.runtime._persist_collaboration_results_with_generation_pin
        )
        original_append = self.runtime._append_drafts

        def require_current_pin():
            self.assertEqual(len(pins), 1)
            pin_binding, pin = pins[0]
            self.assertIsNone(pin_binding._current_repository_access())
            pin_binding.require_generation_pin(pin)

        def capture_pin(binding):
            pin = original_acquire_pin(binding)
            pins.append((binding, pin))
            guarded_order.append("pin")
            return pin

        def capture_request(*args, **kwargs):
            require_current_pin()
            request_bindings.append(kwargs["artifact_binding"])
            return original_request(*args, **kwargs)

        def capture_basis(*args, **kwargs):
            basis_bindings.append(kwargs["artifact_binding"])
            return original_basis(*args, **kwargs)

        def capture_fingerprint(*args, **kwargs):
            fingerprint_bindings.append(kwargs["artifact_binding"])
            return original_fingerprint(*args, **kwargs)

        def capture_prepare(*args, **kwargs):
            if pins and kwargs.get("artifact_binding") is pins[0][0]:
                require_current_pin()
                guarded_order.append("references")
            return original_prepare(*args, **kwargs)

        def capture_definition(*args, **kwargs):
            if pins and kwargs.get("artifact_binding") is pins[0][0]:
                require_current_pin()
                guarded_order.append("definition")
            return original_definition(*args, **kwargs)

        def capture_context_state(*args, **kwargs):
            if pins and kwargs.get("artifact_binding") is pins[0][0]:
                require_current_pin()
                guarded_order.append("context")
            return original_context_state(*args, **kwargs)

        async def capture_invoke(*args, **kwargs):
            if pins:
                require_current_pin()
                guarded_order.append("await")
                await asyncio.sleep(0)
                require_current_pin()
            return await original_invoke(*args, **kwargs)

        def capture_persist(*args, **kwargs):
            require_current_pin()
            self.assertIs(kwargs["synthesis_result"].generation_pin, pins[0][1])
            guarded_order.append("persist")
            return original_persist(*args, **kwargs)

        def capture_append(*args, **kwargs):
            if pins:
                require_current_pin()
                guarded_order.append("append")
            return original_append(*args, **kwargs)

        with mock.patch.object(
            self.runtime,
            "_recommendation_request_draft",
            side_effect=capture_request,
        ), mock.patch.object(
            self.runtime,
            "_recommendation_basis",
            side_effect=capture_basis,
        ), mock.patch.object(
            self.runtime,
            "_recommendation_source_fingerprint",
            side_effect=capture_fingerprint,
        ), mock.patch.object(
            SessionArtifactBinding,
            "acquire_generation_pin",
            new=capture_pin,
        ), mock.patch.object(
            self.runtime,
            "_prepare_session_reference_operation",
            side_effect=capture_prepare,
        ), mock.patch.object(
            self.runtime,
            "_build_guarded_contextual_definition",
            side_effect=capture_definition,
        ), mock.patch.object(
            self.runtime,
            "_session_context_state",
            side_effect=capture_context_state,
        ), mock.patch.object(
            self.runtime,
            "_invoke_context_with_recovery",
            side_effect=capture_invoke,
        ), mock.patch.object(
            self.runtime,
            "_persist_collaboration_results_with_generation_pin",
            side_effect=capture_persist,
        ), mock.patch.object(
            self.runtime,
            "_append_drafts",
            side_effect=capture_append,
        ):
            result = await self.runtime.decide_lead_step(
                lead._decision(
                    proposed,
                    c.CollaborationConfirmationDecision.CONFIRM,
                )
            )

        self.assertIsNotNone(result.turn.recommendation_request)
        self.assertEqual(len(request_bindings), 2)
        self.assertEqual(len(basis_bindings), 2)
        self.assertEqual(len(fingerprint_bindings), 4)
        synthesis_binding = request_bindings[0]
        self.assertTrue(
            all(
                binding is synthesis_binding
                for binding in (
                    *request_bindings,
                    *basis_bindings,
                    *fingerprint_bindings,
                )
            )
        )
        self.assertEqual(guarded_order[0], "pin")
        self.assertLess(guarded_order.index("pin"), guarded_order.index("references"))
        self.assertLess(guarded_order.index("pin"), guarded_order.index("context"))
        self.assertLess(guarded_order.index("pin"), guarded_order.index("definition"))
        self.assertLess(guarded_order.index("pin"), guarded_order.index("persist"))
        self.assertEqual(_generation_pin_descriptors(pins[0][1]), ())

    def test_synthesis_result_pin_is_opaque_nonsemantic_and_closes_on_normalization(
        self,
    ) -> None:
        binding = compose_session_artifact_binding(
            self.control_workspace.root,
            self.session_id,
        )
        first_pin = binding.acquire_generation_pin()
        second_pin = binding.acquire_generation_pin()
        first_descriptors = _generation_pin_descriptors(first_pin)
        second_descriptors = _generation_pin_descriptors(second_pin)
        left = owner._CollaborationInvocationResult(
            object(),
            1,
            artifact_binding=binding,
            generation_pin=first_pin,
        )
        right = owner._CollaborationInvocationResult(
            left.value,
            1,
            artifact_binding=binding,
            generation_pin=second_pin,
        )
        self.assertEqual(left, right)
        self.assertNotIn("GenerationPin", repr(left))
        self.assertNotIn("generation_pin", repr(left))
        self.assertEqual(repr(left), repr(right))

        malformed = self.runtime._required_synthesis_result(left)
        self.assertIs(malformed.artifact_binding, binding)
        self.assertIs(malformed.generation_pin, first_pin)
        self.assertIsInstance(malformed.failure, BackendProtocolError)
        marker = RuntimeError("malformed result append rejected")
        with mock.patch.object(
            self.runtime,
            "_persist_collaboration_results_with_generation_pin",
            side_effect=marker,
        ), self.assertRaises(RuntimeError) as caught:
            self.runtime._persist_collaboration_results_locked(
                synthesis_result=malformed,
            )
        self.assertIs(caught.exception, marker)
        self.assertEqual(_generation_pin_descriptors(first_pin), ())
        for descriptor in first_descriptors:
            with self.assertRaises(OSError):
                os.fstat(descriptor)

        with mock.patch.object(
            self.runtime,
            "_persist_collaboration_results_with_generation_pin",
            side_effect=marker,
        ), self.assertRaises(SessionArtifactBindingError):
            self.runtime._persist_collaboration_results_locked(
                synthesis_result=malformed,
            )
        second_pin.close()
        second_pin.close()
        for descriptor in second_descriptors:
            with self.assertRaises(OSError):
                os.fstat(descriptor)

    def test_synthesis_generation_loss_overrides_append_failure_and_closes_pin(
        self,
    ) -> None:
        binding = compose_session_artifact_binding(
            self.control_workspace.root,
            self.session_id,
        )
        pin = binding.acquire_generation_pin()
        descriptors = _generation_pin_descriptors(pin)
        result = owner._CollaborationInvocationResult(
            object(),
            0,
            artifact_binding=binding,
            generation_pin=pin,
        )
        marker = RuntimeError("durable append failed after generation loss")
        replacement = self.replace_session_worktree()
        replacement.__enter__()

        def fail_persistence(**_kwargs):
            self.runtime._append_drafts(0, ())

        try:
            with mock.patch.object(
                self.runtime,
                "_append_drafts",
                side_effect=marker,
            ) as append, mock.patch.object(
                self.runtime,
                "_persist_collaboration_results_with_generation_pin",
                side_effect=fail_persistence,
            ), self.assertRaises(SessionArtifactBindingError) as caught:
                self.runtime._persist_collaboration_results_locked(
                    synthesis_result=result,
                )
        finally:
            replacement.__exit__(None, None, None)
        self.assertIsNone(caught.exception.__cause__)
        self.assertNotIn(str(marker), str(caught.exception))
        append.assert_called_once_with(0, ())
        self.assertEqual(_generation_pin_descriptors(pin), ())
        pin.close()
        for descriptor in descriptors:
            with self.assertRaises(OSError):
                os.fstat(descriptor)

    async def test_bound_agent_release_joins_repeated_cancellation_and_failures(
        self,
    ) -> None:
        for cancellations in (1, 2):
            with self.subTest(cancellations=cancellations):
                bound_agent = object()
                key = (LEAD_ORCHESTRATOR, f"cancel-{cancellations}")
                self.runtime._bound_agent_uses[id(bound_agent)] = (
                    bound_agent,
                    1,
                )
                self.runtime._retiring_bound_agents[key] = bound_agent
                await self.runtime._binding_lock.acquire()
                released = []
                try:
                    with mock.patch.object(
                        owner,
                        "release_bound_agent",
                        side_effect=lambda current: released.append(current),
                    ):
                        task = asyncio.create_task(
                            self.runtime._release_bound_agent_use(bound_agent)
                        )
                        await asyncio.sleep(0)
                        for ordinal in range(cancellations):
                            task.cancel(f"cancel-{ordinal + 1}")
                            await asyncio.sleep(0)
                        self.runtime._binding_lock.release()
                        with self.assertRaises(asyncio.CancelledError) as caught:
                            await task
                finally:
                    if self.runtime._binding_lock.locked():
                        self.runtime._binding_lock.release()
                self.assertEqual(str(caught.exception), "cancel-1")
                self.assertEqual(released, [bound_agent])
                self.assertNotIn(id(bound_agent), self.runtime._bound_agent_uses)
                self.assertNotIn(key, self.runtime._retiring_bound_agents)

        completion_started = asyncio.Event()
        completion_allowed = asyncio.Event()

        async def complete_in_race(_bound_agent):
            completion_started.set()
            await completion_allowed.wait()

        with mock.patch.object(
            self.runtime,
            "_release_bound_agent_use_once",
            side_effect=complete_in_race,
        ):
            completion = asyncio.create_task(
                self.runtime._release_bound_agent_use(object())
            )
            await completion_started.wait()
            completion.cancel("completion-race")
            completion_allowed.set()
            with self.assertRaises(asyncio.CancelledError) as raced:
                await completion
        self.assertEqual(str(raced.exception), "completion-race")

        cleanup_marker = RuntimeError("physical release failed")

        async def fail_cleanup(_bound_agent):
            raise cleanup_marker

        with mock.patch.object(
            self.runtime,
            "_release_bound_agent_use_once",
            side_effect=fail_cleanup,
        ), self.assertRaises(RuntimeError) as cleanup:
            await self.runtime._release_bound_agent_use(object())
        self.assertIs(cleanup.exception, cleanup_marker)

        failure_started = asyncio.Event()
        failure_allowed = asyncio.Event()

        async def fail_after_cancel(_bound_agent):
            failure_started.set()
            await failure_allowed.wait()
            raise cleanup_marker

        with mock.patch.object(
            self.runtime,
            "_release_bound_agent_use_once",
            side_effect=fail_after_cancel,
        ):
            cancelled = asyncio.create_task(
                self.runtime._release_bound_agent_use(object())
            )
            await failure_started.wait()
            cancelled.cancel("cleanup-cancel")
            failure_allowed.set()
            with self.assertRaises(asyncio.CancelledError) as caught:
                await cancelled
        self.assertEqual(str(caught.exception), "cleanup-cancel")
        self.assertIn(
            "bound-agent release failed: RuntimeError",
            getattr(caught.exception, "__notes__", ()),
        )

    async def test_collaboration_release_cancellation_cleans_retired_use_and_pin(
        self,
    ) -> None:
        for mode in ("success", "cancelled", "cleanup_failure"):
            with self.subTest(mode=mode):
                conversation_id = self.conversation(f"release {mode}")
                binding = compose_session_artifact_binding(
                    self.control_workspace.root,
                    self.session_id,
                )
                invocation_started = asyncio.Event()
                invocation_allowed = asyncio.Event()
                release_started = asyncio.Event()
                original_release = self.runtime._release_bound_agent_use
                physical_releases = []
                cleanup_marker = RuntimeError("retired binding release failed")
                binding_key = (LEAD_ORCHESTRATOR, f"release-{mode}")
                bound_agent = SimpleNamespace()
                delegate = SimpleNamespace(
                    provider_id="synthetic-provider",
                    definition_fingerprint="synthetic-definition",
                    reference_operation_id=None,
                )

                async def acquire(_definition):
                    self.runtime._bound_agents[binding_key] = bound_agent
                    self.runtime._retain_bound_agent_use_locked(bound_agent)
                    return bound_agent

                async def invoke(*_args, **_kwargs):
                    invocation_started.set()
                    await invocation_allowed.wait()
                    return SimpleNamespace(answer="synthetic synthesis")

                async def observe_release(bound_agent):
                    release_started.set()
                    if not invocation_started.is_set():
                        return None
                    return await original_release(bound_agent)

                def release_physical(bound_agent):
                    physical_releases.append(bound_agent)
                    if mode == "cleanup_failure":
                        raise cleanup_marker

                with mock.patch.object(
                    owner,
                    "ContextInvocationRequest",
                    side_effect=lambda **values: SimpleNamespace(**values),
                ), mock.patch.object(
                    owner,
                    "select_bounded_collaboration_context",
                    return_value=None,
                ), mock.patch.object(
                    self.runtime,
                    "_build_guarded_contextual_definition",
                    return_value=SimpleNamespace(
                        agent_id=LEAD_ORCHESTRATOR,
                        fingerprint=binding_key[1],
                    ),
                ), mock.patch.object(
                    self.runtime,
                    "_acquire_bound_agent",
                    side_effect=acquire,
                ), mock.patch.object(
                    self.runtime,
                    "_validated_context_provider_factory",
                    return_value=lambda _reference_read: delegate,
                ), mock.patch.object(
                    self.runtime,
                    "_session_context_state",
                    return_value=None,
                ), mock.patch.object(
                    self.runtime,
                    "_lead_continuation_arguments",
                    return_value={},
                ), mock.patch.object(
                    self.runtime,
                    "_invoke_context_with_recovery",
                    side_effect=invoke,
                ), mock.patch.object(
                    self.runtime,
                    "_release_bound_agent_use",
                    side_effect=observe_release,
                ), mock.patch.object(
                    owner,
                    "release_bound_agent",
                    side_effect=release_physical,
                ):
                    task = asyncio.create_task(
                        self.runtime._run_collaboration_invocation_guarded(
                            artifact_binding=binding,
                            session_id=self.session_id,
                            conversation_id=conversation_id,
                            turn_id="turn",
                            step_id="step",
                            agent_id=LEAD_ORCHESTRATOR,
                            operation_id="operation",
                            result_correlation_id="result",
                            inputs=(),
                            material_value={},
                            invocation_kind=owner.CollaborationInvocationKind.LEAD_SYNTHESIS,
                            context_invocation_kind=owner.ContextInvocationKind.LEAD_SYNTHESIS,
                            result_contract=object(),
                            physical_call_limit=1,
                            round_number=None,
                            decoder=lambda answer, _binding: answer,
                            action_progress_sink=None,
                        )
                    )
                    invocation_wait = asyncio.create_task(
                        invocation_started.wait()
                    )
                    done, _pending = await asyncio.wait(
                        (task, invocation_wait),
                        timeout=30,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if task in done:
                        premature = task.result()
                        self.fail(
                            "collaboration failed before provider barrier: "
                            f"{premature.failure!r}\n"
                            + "".join(
                                traceback.format_tb(premature.failure.__traceback__)
                            )
                        )
                    self.assertIn(invocation_wait, done)
                    await invocation_wait
                    await self.runtime._binding_lock.acquire()
                    try:
                        self.assertEqual(len(self.runtime._bound_agent_uses), 1)
                        retained_agent, uses = next(
                            iter(self.runtime._bound_agent_uses.values())
                        )
                        self.assertIs(retained_agent, bound_agent)
                        self.assertEqual(uses, 1)
                        key = next(
                            key
                            for key, current in self.runtime._bound_agents.items()
                            if current is retained_agent
                        )
                        self.runtime._bound_agents.pop(key)
                        self.assertEqual(
                            self.runtime._mark_binding_retiring_locked(
                                key,
                                retained_agent,
                            ),
                            [],
                        )
                        invocation_allowed.set()
                        await release_started.wait()
                        if mode == "cancelled":
                            task.cancel("release cancelled")
                            await asyncio.sleep(0)
                    finally:
                        self.runtime._binding_lock.release()
                    result = await task

                if mode == "success":
                    self.assertIsNone(result.failure)
                    self.assertEqual(result.value, "synthetic synthesis")
                elif mode == "cancelled":
                    self.assertIsInstance(result.failure, asyncio.CancelledError)
                    self.assertEqual(str(result.failure), "release cancelled")
                else:
                    self.assertIs(result.failure, cleanup_marker)
                self.assertEqual(physical_releases, [bound_agent])
                self.assertEqual(self.runtime._bound_agent_uses, {})
                self.assertNotIn(key, self.runtime._retiring_bound_agents)
                self.assertIs(result.artifact_binding, binding)
                self.assertIsNotNone(result.generation_pin)
                descriptors = _generation_pin_descriptors(result.generation_pin)
                self.assertEqual(len(descriptors), 6)
                binding.require_generation_pin(result.generation_pin)
                with mock.patch.object(
                    self.runtime,
                    "_persist_collaboration_results_with_generation_pin",
                    return_value=None,
                ):
                    self.runtime._persist_collaboration_results_locked(
                        synthesis_result=result,
                    )
                self.assertEqual(
                    _generation_pin_descriptors(result.generation_pin),
                    (),
                )
                for descriptor in descriptors:
                    with self.assertRaises(OSError):
                        os.fstat(descriptor)

    async def test_synthesis_failure_transfers_pin_once_until_persistence(self):
        binding = compose_session_artifact_binding(
            self.control_workspace.root,
            self.session_id,
        )
        for marker in (RuntimeError("provider failed"), asyncio.CancelledError()):
            with self.subTest(marker=type(marker).__name__), mock.patch.object(
                self.runtime,
                "_prepare_session_reference_operation",
                side_effect=marker,
            ):
                result = await self.runtime._run_collaboration_invocation_guarded(
                    artifact_binding=binding,
                    session_id=self.session_id,
                    conversation_id="conversation",
                    turn_id="turn",
                    step_id="step",
                    agent_id=LEAD_ORCHESTRATOR,
                    operation_id="operation",
                    result_correlation_id="result",
                    inputs=(),
                    material_value={},
                    invocation_kind=owner.CollaborationInvocationKind.LEAD_SYNTHESIS,
                    context_invocation_kind=owner.ContextInvocationKind.LEAD_SYNTHESIS,
                    result_contract=object(),
                    physical_call_limit=1,
                    round_number=None,
                    decoder=lambda answer, artifact_binding: answer,
                    action_progress_sink=None,
                )
            self.assertIs(result.failure, marker)
            self.assertIs(result.artifact_binding, binding)
            self.assertIsNotNone(result.generation_pin)
            descriptors = _generation_pin_descriptors(result.generation_pin)
            binding.require_generation_pin(result.generation_pin)
            with mock.patch.object(
                self.runtime,
                "_persist_collaboration_results_with_generation_pin",
                return_value=None,
            ):
                self.runtime._persist_collaboration_results_locked(
                    synthesis_result=result,
                )
            self.assertEqual(
                _generation_pin_descriptors(result.generation_pin),
                (),
            )
            for descriptor in descriptors:
                with self.assertRaises(OSError):
                    os.fstat(descriptor)

    async def test_synthesis_next_intent_and_action_are_exclusive_with_bounded_accounting(self):
        conversation_id, _, mixed = await self.consultation(actionable=True, mixed=True)
        self.assertIsNone(mixed.turn.recommendation_request)
        result = self.payload_events(conversation_id, c.CollaborationExecutionResult)[0].payload
        self.assertIs(result.synthesis_outcome, c.CollaborationSynthesisOutcomeKind.FAILED)
        self.assertFalse(self.payload_events(conversation_id, c.LeadRecommendationRequestPayload))
        self.assertEqual(result.synthesis_submitted_physical_calls, 1)
        # Source consultation calls and its failed/replayed attempts all remain in the new step's budget.
        other, proposed, result = await self.consultation(actionable=True)
        request = result.turn.recommendation_request
        events = self.history(other)
        submitted = sum(type(e.payload) is c.CollaborationProviderCallPayload and
                        e.payload.state is c.CollaborationProviderCallState.SUBMITTED for e in events)
        self.assertEqual(request.physical_calls_spent, submitted)
        self.assertLessEqual(request.physical_calls_spent + request.max_preparation_physical_calls, 12)
        self.backend.enqueue(recommendation(message=DRAFT))
        revised = await self.runtime.decide_lead_recommendation(decision(result.turn, c.CollaborationConfirmationDecision.REVISE))
        self.assertEqual(revised.recommendation_request.physical_calls_spent, submitted + 1)
        self.assertEqual(revised.step_id, result.turn.step_id)
        self.assertEqual(len(self.payload_events(other, c.CollaborationExecutionResult)), 1)
        self.assertEqual(len(self.payload_events(other, c.CollaborationAssignmentDispatchPayload)), 1)

    async def test_known_synthesis_result_finalizes_action_once_without_provider_replay(self):
        conversation_id, proposed, _ = await self.consultation(actionable=True, pause=True)
        known = self.history(conversation_id)
        projection = c.project_collaboration_history(known)
        self.assertIs(projection.current_recommendation_state, c.LeadRecommendationState.PENDING)
        backend = lead._LeadBackend()
        restarted = StoryRuntime(self.control_workspace, backend)
        for _ in range(2):
            restored = restarted._finalize_known_result_locked(
                self.session_id,
                known,
                projection,
            )
            self.assertEqual(restored.recommendation_request, projection.current_recommendation_request)
            self.assertEqual(self.history(conversation_id), known)
            self.assertEqual(backend.calls, [])
        request_event = known[-1]
        for changes in ({"source_proposal_id": "foreign"}, {"source_execution_attempt_id": "foreign"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                c.project_collaboration_history(rewrite_history(known, request_event.event_id,
                    payload=replace(request_event.payload, **changes)))
        # No replay occurs merely from rehydrating a pending action.
        self.assertFalse(hasattr(restarted.session(self.session_id), "recommendation_capability"))


class LeadRecommendationRecoveryTests(RecommendationCase):
    async def reference_offer(self, *, kind="prepare_consultation", objective_id=None):
        return await self.recommend(recommended_action=action(kind=kind,
            source_ids=("reference_1", "current_response"), objective_id=objective_id),
            text=f"Inspect ${{{OUTLINE}}} and recommend exactly one preparation.")

    def assert_failed_origin(self, conversation_id, turn, code):
        events = self.history(conversation_id)
        started = self.payload_events(conversation_id, c.LeadRecommendationStartedPayload)[-1]
        failures = self.payload_events(conversation_id, c.CollaborationLeadOutcomeFailurePayload)
        self.assertEqual(len(failures), 1)
        failure = failures[0]
        self.assertIs(failure.payload.code, code)
        self.assertEqual((failure.turn_id, failure.step_id), (started.turn_id, started.step_id))
        calls = [e for e in events if e.turn_id == started.turn_id and type(e.payload) is c.CollaborationProviderCallPayload]
        if calls:
            self.assertEqual(failure.payload.call_id, calls[-1].payload.call_id)
            self.assertEqual(failure.payload.invocation_id, calls[-1].payload.invocation_id)
        self.assertEqual(sum(type(e.payload) is c.CollaborationMessagePayload and
            e.direction is c.CollaborationDirection.USER_TO_LEAD for e in events), 1)
        self.assert_no_downstream(conversation_id)
        return failure

    async def test_confirmed_unstarted_restart_requires_new_request_and_confirmation(self):
        conversation_id, turn = await self.recommend()
        before = len(self.backend.calls)
        with mock.patch.object(self.runtime, "_run_confirmed_lead_preparation_locked", side_effect=RuntimeError("before preparation")):
            with self.assertRaisesRegex(RuntimeError, "before preparation"):
                await self.runtime.decide_lead_recommendation(decision(turn))
        known = self.history(conversation_id)
        self.assertIs(self.projection(conversation_id).current_recommendation_state, c.LeadRecommendationState.RUNNING)
        restarted = StoryRuntime(self.control_workspace, self.backend)
        self.assertEqual(self.history(conversation_id), known)
        closed = await restarted.decide_lead_recommendation(decision(turn))
        self.assertIs(closed.next_action, c.CollaborationNextAction.CONTINUE_WITH_LEAD)
        self.assertEqual(len(self.backend.calls), before)
        self.assert_failed_origin(conversation_id, turn, c.CollaborationLeadOutcomeFailureCode.REQUEST_NOT_SUBMITTED)
        self.assert_action_closed(conversation_id, c.LeadRecommendationState.INTERRUPTED)
        with self.assertRaises(BackendProtocolError):
            await restarted.decide_lead_recommendation(decision(turn))
        self.runtime = restarted
        _, fresh = await self.recommend(conversation_id)
        self.assertNotEqual(fresh.recommendation_request.request_id, turn.recommendation_request.request_id)
        self.assertEqual(len(self.payload_events(conversation_id, c.LeadRecommendationStartedPayload)), 1)
        await self.confirm(fresh)
        self.assertEqual(len(self.payload_events(conversation_id, c.LeadRecommendationStartedPayload)), 2)

    async def test_unsubmitted_attempt_requires_exact_confirm_origin_without_fake_user_event(self):
        for with_objective in (False, True):
            conversation_id = self.conversation("Unsubmitted exact origin")
            objective = None
            if with_objective:
                await self.start(conversation_id)
                objective = self.objective(conversation_id).facts
            _, turn = await self.recommend(conversation_id, recommended_action=action(
                kind="prepare_consultation", objective_id=None if objective is None else objective.objective_id))
            downstream_types = {c.CollaborationAssignmentDispatchPayload,
                c.ArtifactApplicationDecisionPayload, c.ArtifactApplicationResultPayload,
                c.StoryCommitDecisionPayload, c.StoryCommitResultPayload}
            downstream_event_ids = tuple(e.event_id for e in self.history(conversation_id)
                                         if type(e.payload) in downstream_types)
            call_records = tuple(self.backend.call_records)
            calls = len(self.backend.calls)
            error = BackendProtocolError("Synthetic binding rejected before submission")
            with mock.patch.object(self.backend, "bind", side_effect=error):
                result = await self.runtime.decide_lead_recommendation(decision(turn))
            self.assertEqual(len(self.backend.calls), calls)
            failure = self.payload_events(conversation_id, c.CollaborationLeadOutcomeFailurePayload)[0]
            self.assertIs(failure.payload.code, c.CollaborationLeadOutcomeFailureCode.REQUEST_NOT_SUBMITTED)
            self.assertIsNone(failure.payload.call_id)
            self.assertIsNone(failure.payload.invocation_id)
            projection = self.projection(conversation_id)
            self.assertIs(projection.current_recommendation_state, c.LeadRecommendationState.INTERRUPTED)
            closure = projection.current_recommendation_result
            self.assertIsNotNone(closure)
            events = {event.event_id: event for event in self.history(conversation_id)}
            if closure.start_event_id is not None:
                start = events[closure.start_event_id]
                source = events[closure.lead_result_event_id]
                self.assertEqual((source.turn_id, source.step_id), (start.turn_id, start.step_id))
                self.assertLess(start.sequence, source.sequence)
            self.assertEqual(tuple(e.event_id for e in self.history(conversation_id)
                                   if type(e.payload) in downstream_types), downstream_event_ids)
            self.assertEqual(tuple(self.backend.call_records), call_records)
            if objective is not None: self.assertEqual(self.objective(conversation_id).facts, objective)
            self.assertIsNone(result.recommendation_request)
            self.assertEqual(len(self.payload_events(conversation_id, c.LeadRecommendationDecisionPayload)), 1)

    async def test_submitted_failure_and_timeout_finalize_only_exact_terminal_reference_origin(self):
        from story_writing_agents.writing_artifacts.authorization import ReferenceInvocationState, ReferenceOperationState, ReferenceOperationFailure
        for timed_out in (False, True):
            conversation_id, turn = await self.reference_offer()
            before_calls, before_refs = len(self.backend.calls), len(self.backend.reference_calls)
            error = BackendProtocolError("Synthetic preparation provider failed")
            self.backend.enqueue(lead._SubmittedFailure(error, timed_out=timed_out))
            result = await self.runtime.decide_lead_recommendation(decision(turn))
            code = c.CollaborationLeadOutcomeFailureCode.PROVIDER_TIMED_OUT if timed_out else c.CollaborationLeadOutcomeFailureCode.PROVIDER_FAILED
            failure = self.assert_failed_origin(conversation_id, turn, code)
            self.assertIs(result.next_action, c.CollaborationNextAction.CONTINUE_WITH_LEAD)
            self.assert_action_closed(conversation_id, c.LeadRecommendationState.FAILED)
            closure = self.payload_events(conversation_id, c.LeadRecommendationResultPayload)[0]
            self.assertLess(failure.sequence, closure.sequence)
            self.assertEqual((len(self.backend.calls) - before_calls, len(self.backend.reference_calls) - before_refs), (1, 1))
            reference = self.runtime.inspect_reference_operation(self.backend.providers[-1].reference_operation_id)
            self.assertIs(reference.operation_events[-1].operation_state, ReferenceOperationState.TERMINATED)
            self.assertTrue(reference.invocation_events)
            for invocation in reference.invocation_events:
                self.assertIs(invocation[-1].invocation_state, ReferenceInvocationState.TERMINATED)
                self.assertEqual(invocation[-1].correlation.operation_id, reference.manifest.operation_id)
                self.assertEqual(invocation[-1].correlation.phase, "lead")
            old = self.history(conversation_id)
            restarted = StoryRuntime(self.control_workspace, self.backend)
            with self.assertRaises(BackendProtocolError):
                await restarted.decide_lead_recommendation(decision(turn))
            self.assertEqual(self.history(conversation_id), old)
        conversation_id, turn = await self.reference_offer()
        self.backend.enqueue(lead._SubmittedFailure(BackendProtocolError("Synthetic contradictory timeout"), timed_out=True))
        store_type = type(self.runtime.conversation_storage_services.event_store)
        append = store_type.append_batch
        def contradictory(store, batch):
            drafts = tuple(replace(e, payload=replace(e.payload, code=c.CollaborationLeadOutcomeFailureCode.PROVIDER_FAILED))
                if type(e.payload) is c.CollaborationLeadOutcomeFailurePayload else e for e in batch.events)
            return append(store, replace(batch, events=drafts))
        with mock.patch.object(store_type, "append_batch", new=contradictory):
            with self.assertRaises(ReferenceOperationFailure):
                await self.runtime.decide_lead_recommendation(decision(turn))
        self.assertFalse(self.payload_events(conversation_id, c.LeadRecommendationResultPayload))

    async def test_missing_corrupt_or_unresolved_origin_accounting_never_authorizes_retry(self):
        from story_writing_agents.writing_artifacts.authorization import ReferenceAuthorizationError, ReferenceAuthorizationErrorCode, ReferenceOperationFailure
        for fault in ("missing", "corrupt", "unfinished", "uncertain"):
            conversation_id, turn = await self.reference_offer()
            error = BackendProtocolError("Synthetic unresolved preparation")
            self.backend.enqueue(lead._SubmittedFailure(error, submission_uncertain=fault == "uncertain"))
            inspect_type = type(self.runtime.artifacts)
            inspect_original = inspect_type.inspect_reference_operation
            def broken(repository, operation_id):
                history = inspect_original(repository, operation_id)
                if fault == "missing": return None
                if fault == "corrupt": raise ReferenceAuthorizationError(ReferenceAuthorizationErrorCode.EVIDENCE_CORRUPT)
                if fault == "unfinished": return replace(history, invocation_events=(history.invocation_events[0][:-1], *history.invocation_events[1:]))
                return history
            with mock.patch.object(inspect_type, "inspect_reference_operation", new=broken):
                expected = ReferenceAuthorizationError if fault == "corrupt" else ReferenceOperationFailure
                with self.assertRaises(expected):
                    await self.runtime.decide_lead_recommendation(decision(turn))
            self.assertFalse(self.payload_events(conversation_id, c.LeadRecommendationResultPayload))
            calls, history = len(self.backend.calls), self.history(conversation_id)
            restarted = StoryRuntime(self.control_workspace, self.backend)
            blocked = await restarted.lead_turn(self.session_id, conversation_id, "Do not replay uncertain work.")
            self.assertIs(blocked.next_action, c.CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME if fault == "uncertain"
                          else c.CollaborationNextAction.REPAIR_AUTHORITATIVE_HISTORY)
            self.assertEqual(len(self.backend.calls), calls)
            self.assertEqual(self.history(conversation_id), history)
            self.assertIsNone(blocked.recommendation_request)

    async def test_primary_reference_capacity_and_cancellation_keep_their_precedence(self):
        from story_writing_agents.conversations.session_context import SessionContextCapacityError
        from story_writing_agents.writing_artifacts.authorization import ReferenceAuthorizationError, ReferenceAuthorizationErrorCode, ReferenceOperationFailure
        errors = (ReferenceAuthorizationError(ReferenceAuthorizationErrorCode.DRIFT),
                  ReferenceAuthorizationError(ReferenceAuthorizationErrorCode.CORRELATION_MISMATCH),
                  SessionContextCapacityError(), asyncio.CancelledError())
        for error in errors:
            conversation_id, turn = await self.reference_offer()
            self.backend.enqueue(lead._SubmittedFailure(error))
            expected = ReferenceOperationFailure if isinstance(error, ReferenceAuthorizationError) else type(error)
            with self.assertRaises(expected) as caught:
                await self.runtime.decide_lead_recommendation(decision(turn))
            if isinstance(error, ReferenceAuthorizationError):
                self.assertIs(caught.exception.__cause__, error)
                self.assertIs(caught.exception.code, error.code)
            else: self.assertIs(caught.exception, error)
            self.assertFalse(self.payload_events(conversation_id, c.LeadRecommendationResultPayload))
            self.assert_no_downstream(conversation_id)

    async def test_history_and_action_closure_failures_remain_blocked_until_local_finalization(self):
        for referenced, fail_lead in ((False, False), (True, False), (True, True)):
            conversation_id, turn = await self.reference_offer() if referenced else await self.recommend()
            self.backend.enqueue(lead._SubmittedFailure(BackendProtocolError("Synthetic closure failure")))
            store_type = type(self.runtime.conversation_storage_services.event_store)
            append = store_type.append_batch
            target = c.CollaborationLeadOutcomeFailurePayload if fail_lead else c.LeadRecommendationResultPayload
            def unavailable(store, batch):
                if any(type(e.payload) is target for e in batch.events):
                    raise OSError("Synthetic action history unavailable")
                return append(store, batch)
            with mock.patch.object(store_type, "append_batch", new=unavailable):
                if fail_lead:
                    blocked = await self.runtime.decide_lead_recommendation(decision(turn))
                    self.assertIs(blocked.next_action, c.CollaborationNextAction.REPAIR_AUTHORITATIVE_HISTORY)
                else:
                    with self.assertRaisesRegex(OSError, "Synthetic action history"):
                        await self.runtime.decide_lead_recommendation(decision(turn))
            history, calls = self.history(conversation_id), len(self.backend.calls)
            self.assertFalse(self.payload_events(conversation_id, c.LeadRecommendationResultPayload))
            restarted = StoryRuntime(self.control_workspace, self.backend)
            result = await restarted.lead_turn(self.session_id, conversation_id, "Finalize known local failure only.")
            self.assertEqual(len(self.backend.calls), calls)
            self.assertIsNone(result.recommendation_request)
            if referenced:
                self.assertIn(result.next_action, (c.CollaborationNextAction.REPAIR_AUTHORITATIVE_HISTORY,
                                                  c.CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME))
                self.assertEqual(self.history(conversation_id), history)
            else:
                self.assertIs(result.next_action, c.CollaborationNextAction.CONTINUE_WITH_LEAD)
                self.assert_action_closed(conversation_id, c.LeadRecommendationState.FAILED)
                with self.assertRaises(BackendProtocolError):
                    await restarted.decide_lead_recommendation(decision(turn))
                self.assertEqual(len(self.payload_events(conversation_id, c.LeadRecommendationResultPayload)), 1)

    async def test_reference_recovery_root_loss_preserves_failure_without_replay(self):
        repository_type = type(self.runtime.artifacts)
        inspect_operation = repository_type.inspect_reference_operation

        for fail_on_terminal_inspection in (1, 2):
            with self.subTest(
                fail_on_terminal_inspection=fail_on_terminal_inspection
            ):
                conversation_id, turn = await self.reference_offer()
                self.backend.enqueue(
                    lead._SubmittedFailure(
                        BackendProtocolError("Synthetic native provider failure")
                    )
                )
                terminal_inspections = 0

                def lose_root_after_failure(repository, operation_id):
                    nonlocal terminal_inspections
                    history = inspect_operation(repository, operation_id)
                    if self.payload_events(
                        conversation_id,
                        c.CollaborationLeadOutcomeFailurePayload,
                    ):
                        terminal_inspections += 1
                        if terminal_inspections >= fail_on_terminal_inspection:
                            raise ArtifactRootSecurityError(
                                "workspace root identity changed"
                            )
                    return history

                with mock.patch.object(
                    repository_type,
                    "inspect_reference_operation",
                    new=lose_root_after_failure,
                ):
                    blocked = await self.runtime.decide_lead_recommendation(
                        decision(turn)
                    )
                    durable = self.history(conversation_id)
                    provider_calls = (
                        len(self.backend.calls),
                        len(self.backend.reference_calls),
                    )
                    restarted = StoryRuntime(self.control_workspace, self.backend)
                    repeated = await restarted.lead_turn(
                        self.session_id,
                        conversation_id,
                        "Do not replay the failed preparation.",
                    )

                self.assertGreaterEqual(
                    terminal_inspections,
                    fail_on_terminal_inspection,
                )
                for result in (blocked, repeated):
                    self.assertIs(
                        result.state,
                        c.CollaborationStepState.RECOVERY_REQUIRED,
                    )
                    self.assertIs(
                        result.next_action,
                        c.CollaborationNextAction.REPAIR_AUTHORITATIVE_HISTORY,
                    )
                    self.assertIsNone(result.recommendation_request)
                self.assertEqual(
                    (len(self.backend.calls), len(self.backend.reference_calls)),
                    provider_calls,
                )
                self.assertEqual(self.history(conversation_id), durable)
                failures = self.payload_events(
                    conversation_id,
                    c.CollaborationLeadOutcomeFailurePayload,
                )
                self.assertEqual(len(failures), 1)
                self.assertIs(
                    failures[0].payload.code,
                    c.CollaborationLeadOutcomeFailureCode.PROVIDER_FAILED,
                )


class LeadRecommendationAuthRecoveryTests(RecommendationCase):
    def auth_provider(self, *, prelude_failure=None):
        """Real context recovery with a synthetic native pre-execution rejection."""
        from contextlib import ExitStack, contextmanager
        from story_writing_agents.errors import ProviderAuthenticationRequiredError
        from story_writing_agents.agent_context.service import context_provider_failure_facts, record_context_provider_failure
        from story_writing_agents.provider_auth_recovery import provider_activity, validate_provider_replay
        invoke = lead._LeadContextProvider.invoke_context
        prepare = lead._LeadContextProvider.prepare_references
        pending = [prelude_failure] if prelude_failure is not None else []

        async def invoke_with_activity(provider, request):
            async with provider_activity(provider.provider_id) as generation:
                validate_provider_replay()
                try:
                    return await invoke(provider, request)
                except ProviderAuthenticationRequiredError as failure:
                    failure.auth_generation = generation
                    facts = context_provider_failure_facts(failure)
                    record_context_provider_failure(failure, replace(facts,
                        taint_existing_thread=(not failure.replay_safe or facts.timed_out),
                        submission_uncertain=(not failure.replay_safe or facts.submission_uncertain)))
                    raise

        async def prepare_with_activity(provider, request):
            async with provider_activity(provider.provider_id) as generation:
                validate_provider_replay()
                if pending:
                    failure = pending.pop()
                    provider.backend.reference_calls.append(request)
                    accounting = provider._account({"type": "object"}, {"synthetic_reference_count": 1},
                        call_kind=lead.ProviderCallKind.WRITING_ARTIFACT_REFERENCE_READ)
                    failure.auth_generation = generation
                    record_context_provider_failure(failure, lead.ContextProviderFailureFacts(accounting))
                    raise failure
                return await prepare(provider, request)

        @contextmanager
        def scope():
            with ExitStack() as stack:
                stack.enter_context(mock.patch.object(lead._LeadContextProvider, "provider_id", "antigravity"))
                stack.enter_context(mock.patch.object(lead._LeadContextProvider, "invoke_context", new=invoke_with_activity))
                stack.enter_context(mock.patch.object(lead._LeadContextProvider, "prepare_references", new=prepare_with_activity))
                stack.enter_context(mock.patch.object(self.backend, "backend_overrides", {LEAD_ORCHESTRATOR: "antigravity"}))
                yield
        return scope()

    async def test_safe_auth_replay_preserves_confirm_and_revise_origins_and_budget(self):
        from tests.test_provider_auth_recovery import auth_failure, drive_recovery
        from story_writing_agents.agent_context import canonical_provider_json
        for choice, prelude in ((c.CollaborationConfirmationDecision.CONFIRM, False),
                                (c.CollaborationConfirmationDecision.REVISE, False),
                                (c.CollaborationConfirmationDecision.CONFIRM, True)):
            with self.subTest(choice=choice, prelude=prelude):
                with self.auth_provider():
                    conversation_id, turn = await self.recommend(recommended_action=action(
                        source_ids=("reference_1", "current_response") if prelude else ("current_response",)),
                        text=f"Review ${{{OUTLINE}}} before recommending." if prelude else "Offer a synthetic preparation.")
                error = auth_failure()
                producer = recommendation(message=DRAFT) if choice is c.CollaborationConfirmationDecision.REVISE else preparation(turn)
                if prelude:
                    self.backend.enqueue(producer)
                else:
                    self.backend.enqueue(lead._SubmittedFailure(error), producer)
                before_calls, before_refs, before = len(self.backend.calls), len(self.backend.reference_calls), self.snapshots()
                with self.auth_provider(prelude_failure=error if prelude else None):
                    result, keys = await drive_recovery(self.runtime.decide_lead_recommendation(decision(turn, choice)))
                self.assertEqual(keys, [("antigravity", 0)])
                physical = self.backend.calls[before_calls:]
                references = self.backend.reference_calls[before_refs:]
                self.assertEqual((len(physical), len(references)), (1, 2) if prelude else (2, 0))
                first, replay = references if prelude else physical
                self.assertNotEqual(first.call_id, replay.call_id)
                self.assertEqual(replace(first, call_id=replay.call_id), replay)
                if not prelude:
                    self.assertEqual(canonical_provider_json(first.payload), canonical_provider_json(replay.payload))
                self.assertEqual(len(self.payload_events(conversation_id, c.LeadRecommendationDecisionPayload)), 1)
                self.assertEqual(len(self.payload_events(conversation_id, c.LeadRecommendationStartedPayload)), 1)
                if choice is c.CollaborationConfirmationDecision.REVISE:
                    self.assertIsNotNone(result.recommendation_request)
                    self.assertIsNone(result.artifact_proposal)
                    self.assertEqual(result.recommendation_request.physical_calls_spent,
                                     turn.recommendation_request.physical_calls_spent + 2)
                else:
                    self.assertIsNotNone(result.artifact_proposal)
                    self.assertIsNone(result.recommendation_request)
                    self.assert_action_closed(conversation_id)
                start = self.payload_events(conversation_id, c.LeadRecommendationStartedPayload)[0]
                submitted = [e.payload for e in self.history(conversation_id) if e.turn_id == start.turn_id
                    and type(e.payload) is c.CollaborationProviderCallPayload
                    and e.payload.state is c.CollaborationProviderCallState.SUBMITTED]
                self.assertEqual(len(submitted), len(physical) + len(references))
                self.assertEqual(len({p.call_id for p in submitted}), len(submitted))
                self.assertLessEqual(len(submitted), turn.recommendation_request.max_preparation_physical_calls)
                self.assertEqual(self.snapshots(), before)
                self.assert_no_downstream(conversation_id)

    async def test_auth_replay_veto_cancellation_and_uncertainty_never_restore_action_authority(self):
        from tests.test_provider_auth_recovery import auth_failure, drive_recovery
        from story_writing_agents.errors import ProviderAuthenticationRequiredError
        from story_writing_agents.agent_context.service import context_provider_failure_facts
        for mode in ("admission_veto", "source_drift", "declined", "repeated", "unsafe_codex", "timeout", "cancel"):
            conversation_id = self.conversation("Synthetic authentication boundary")
            allowed = [True]
            error = auth_failure("codex" if mode == "unsafe_codex" else "antigravity",
                                 safe=mode != "unsafe_codex", validator=lambda: allowed[0])
            with self.auth_provider():
                _, turn = await self.recommend(conversation_id)
                repeated = auth_failure() if mode == "repeated" else preparation(turn)
                self.backend.enqueue(lead._SubmittedFailure(error, timed_out=mode == "timeout",
                    submission_uncertain=mode in {"timeout", "unsafe_codex"}),
                    lead._SubmittedFailure(repeated) if mode == "repeated" else repeated)
                before = len(self.backend.calls)
                def callback(key):
                    if mode == "admission_veto": allowed[0] = False
                    if mode == "source_drift": self.workspace.path(OUTLINE).write_text("Changed during authentication.\n")
                    if mode == "cancel": raise asyncio.CancelledError()
                    return mode != "declined"
                choice = (c.CollaborationConfirmationDecision.REVISE if mode in {"admission_veto", "repeated", "timeout"}
                          else c.CollaborationConfirmationDecision.CONFIRM)
                with self.assertRaises(asyncio.CancelledError if mode == "cancel" else ProviderAuthenticationRequiredError):
                    await drive_recovery(self.runtime.decide_lead_recommendation(decision(turn, choice)), callback)
                self.assertEqual(len(self.backend.calls) - before, 2 if mode == "repeated" else 1)
                self.assertEqual(len(self.payload_events(conversation_id, c.LeadRecommendationStartedPayload)), 1)
                self.assertFalse(self.payload_events(conversation_id, c.ArtifactReviewCandidatePayload))
                self.assertEqual(self.projection(conversation_id).artifact_objectives, ())
                self.assertFalse(error.validate_replay())
                if mode == "timeout": self.assertTrue(context_provider_failure_facts(error).timed_out)
                old_calls = len(self.backend.calls)
                restarted = StoryRuntime(self.control_workspace, self.backend)
                self.assertEqual(len(self.backend.calls), old_calls)
                self.assert_no_downstream(conversation_id)
                self.workspace.path(OUTLINE).write_bytes(b"")
        # A replayed reference prelude must still leave room for the substantive preparation.
        with self.auth_provider(), mock.patch.object(owner, "_CONTEXT_INVOCATION_CALL_LIMIT", 2):
            conversation_id, turn = await self.recommend(recommended_action=action(
                source_ids=("reference_1", "current_response")), text=f"Read ${{{OUTLINE}}} and offer a preparation.")
        error = auth_failure()
        self.backend.enqueue(preparation(turn))
        before_calls, before_refs = len(self.backend.calls), len(self.backend.reference_calls)
        with self.auth_provider(prelude_failure=error), self.assertRaises(ProviderAuthenticationRequiredError):
            await drive_recovery(self.runtime.decide_lead_recommendation(decision(turn)))
        self.assertEqual(len(self.backend.calls), before_calls)
        self.assertEqual(len(self.backend.reference_calls), before_refs + 1)
        self.assertFalse(self.payload_events(conversation_id, c.ArtifactReviewCandidatePayload))
