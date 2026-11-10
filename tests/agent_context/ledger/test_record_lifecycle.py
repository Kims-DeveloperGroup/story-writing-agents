'Existing LedgerLifecycleTests scenarios, moved without new cases.'
from __future__ import annotations
import stat
from dataclasses import FrozenInstanceError, fields, replace
from story_writing_agents.agent_context.accounting import ProviderCallKind, ProviderUsage
from story_writing_agents.agent_context.contracts import planning_inputs as ctx_planning_inputs
from story_writing_agents.agent_context.ledger import checkpoint_ledger as owner_checkpoint_ledger
from story_writing_agents.agent_context.ledger import record_validation as owner_record_validation
from story_writing_agents.agent_context.ledger import record_values as owner_record_values
from tests.agent_context.ledger import ledger_fixture as fixture_ledger_fixture


class LedgerLifecycleTests(fixture_ledger_fixture.LedgerTestCase):
    def test_persisted_values_have_exact_fields_are_frozen_and_self_validate(self,
    ) -> None:
        ready = self.ready()
        assert ready.record is not None
        assert ready.record.attempt is not None
        assert ready.record.ready_checkpoint is not None
        exact_fields = {owner_record_values.ContextLedgerKey: ("scope", "provider_id"),
            owner_record_values.ContextLedgerProviderCall: ("ordinal", "call_id", "accounting"),
            owner_record_values.ContextLedgerAttemptPlan: ("attempt_id",
                "operation_id",
                "result_correlation_id", "proposal_digest",
                "delivery_mode", "bootstrap_reason",
                "proposed_delta_depth", "mandatory_reference_count",
                "planned_provider_thread_id", "reference_operation_id",
                "projection_accounting",),
            owner_record_values.ContextLedgerAttempt: ("plan",
                "provider_calls", "outcome",
                "started_at", "updated_at",
                "completed_at",
                "commit_authorization_digest",),
            owner_record_values.ContextLedgerReadyCheckpoint: ("conditions",
                "provider_thread_id", "sources",
                "delta_depth", "proposal_digest",
                "committed_at", "catalog_sources",
                "source_alias_high_watermark",
                "session_history_coverage",),
            owner_record_values.ContextLedgerRecord: ("key",
                "generation", "state",
                "created_at", "updated_at",
                "attempt", "ready_checkpoint",
                "schema_version",),
            owner_record_values.ContextLedgerInspection: ("key",
                "generation", "checkpoint_view",
                "record",),
            owner_record_values.ContextLedgerCommitAuthorization: ("attempt_id",
                "operation_id",
                "result_correlation_id", "proposal",
                "revalidated_proposal_digest", "terminal_call_id",
                "final_provider_thread_id", "completed_reference_operation_id",
                "output_action_authorization_id",),
        }
        for value in (ready.record.key,
            ready.record.attempt.plan, ready.record.attempt.provider_calls[0],
            ready.record.attempt, ready.record.ready_checkpoint,
            ready.record, ready,
            fixture_ledger_fixture.make_authorization(self.plan, self.projection),):
            with self.subTest(value=type(value).__name__):
                self.assertEqual(tuple(field.name for field in fields(type(value))),
                    exact_fields[type(value)],)
                self.assertFalse(hasattr(value, "__dict__"))
                with self.assertRaises((FrozenInstanceError, TypeError)):
                    value.unexpected = 1  # type: ignore[attr-defined]
        self.assert_ledger_error(
            lambda: replace(ready.record.ready_checkpoint,
                proposal_digest=fixture_ledger_fixture.digest_bytes(b"mismatch"),),
            owner_record_validation.ContextLedgerErrorCode.INVALID_VALUE,)
        self.assert_ledger_error(
            lambda: replace(ready.record,
                state=owner_record_values.ContextLedgerRecordState.STALE,),
            owner_record_validation.ContextLedgerErrorCode.INVALID_VALUE,)
        self.assert_ledger_error(lambda: replace(ready, generation=ready.generation + 1),
            owner_record_validation.ContextLedgerErrorCode.INVALID_VALUE,)

    def test_complete_lifecycle_restart_idempotency_invalidation_and_modes(self,
    ) -> None:
        self.assertIs(
            self.ledger.inspect(self.key).checkpoint_view.state,
            ctx_planning_inputs.ContextCheckpointState.MISSING,)
        reserved = self.ledger.reserve_attempt(
            self.key,
            expected_generation=0,
            plan=self.plan,)
        self.assertEqual(
            (reserved.generation,
                reserved.record.state if reserved.record else None,
                reserved.checkpoint_view.state,),
            (1, owner_record_values.ContextLedgerRecordState.INCOMPLETE, ctx_planning_inputs.ContextCheckpointState.TAINTED),)
        self.assertIsNone(reserved.record.ready_checkpoint if reserved.record else None)
        self.assertEqual(
            self.ledger.reserve_attempt(
                self.key,
                expected_generation=0,
                plan=self.plan,),
            reserved,)
        reference_call = fixture_ledger_fixture.make_provider_call(1,
            call_kind=ProviderCallKind.WRITING_ARTIFACT_REFERENCE_READ,)
        first = self.ledger.append_provider_call(
            self.key, expected_generation=1,
            attempt_id=self.plan.attempt_id,
            provider_call=reference_call,)
        self.assertEqual(first.generation, 2)
        self.assertEqual(self.ledger.append_provider_call(
                self.key,
                expected_generation=1,
                attempt_id=self.plan.attempt_id,
                provider_call=reference_call,),
            first,)
        substantive = fixture_ledger_fixture.make_provider_call(2,
            usage=ProviderUsage(input_tokens=17, output_tokens=5),)
        second = self.ledger.append_provider_call(self.key,
            expected_generation=2, attempt_id=self.plan.attempt_id,
            provider_call=substantive,)
        authorization = fixture_ledger_fixture.make_authorization(self.plan,
            self.projection,
            terminal_call_id="call-2",)
        ready = self.ledger.commit_attempt(
            self.key, expected_generation=second.generation,
            authorization=authorization,)
        self.assertEqual(ready.generation, 4)
        self.assertIs(ready.record.state, owner_record_values.ContextLedgerRecordState.READY)
        self.assertIs(ready.checkpoint_view.state, ctx_planning_inputs.ContextCheckpointState.READY)
        self.assertEqual(
            (ready.checkpoint_view.conditions,
                ready.checkpoint_view.provider_thread_id, ready.checkpoint_view.sources,
                ready.checkpoint_view.delta_depth,),
            (self.projection.checkpoint_proposal.conditions,
                "thread-final", self.projection.checkpoint_proposal.sources,
                0,),
        )
        self.assertEqual(self.ledger.commit_attempt(
                self.key,
                expected_generation=0,
                authorization=authorization,),
            ready,)
        restarted = owner_checkpoint_ledger.ContextLedger(self.workspace, clock=fixture_ledger_fixture.LedgerClock()).inspect(self.key)
        self.assertEqual(restarted, ready)
        stale = self.ledger.invalidate(self.key, expected_generation=ready.generation)
        self.assertEqual((stale.generation, stale.record.state, stale.checkpoint_view.state),
            (5, owner_record_values.ContextLedgerRecordState.STALE, ctx_planning_inputs.ContextCheckpointState.STALE),)
        self.assertIsNone(stale.record.ready_checkpoint)
        self.assertEqual(self.ledger.invalidate(self.key, expected_generation=0),
            stale,)
        directory = self.record_path().parent
        self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(directory.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.record_path().stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.lock_path().stat().st_mode), 0o600)
        self.assertEqual({path.name for path in directory.iterdir()},
            {self.record_path().name, self.lock_path().name},)

    def test_optimistic_generations_ordered_calls_and_conflicting_replays(self) -> None:
        self.assert_ledger_error(
            lambda: self.ledger.reserve_attempt(
                self.key,
                expected_generation=1,
                plan=self.plan,),
            owner_record_validation.ContextLedgerErrorCode.CONFLICT,)
        reserved = self.ledger.reserve_attempt(
            self.key,
            expected_generation=0,
            plan=self.plan,)
        self.assert_ledger_error(
            lambda: self.ledger.reserve_attempt(
                self.key, expected_generation=reserved.generation,
                plan=replace(self.plan, attempt_id="attempt-2"),),
            owner_record_validation.ContextLedgerErrorCode.INVALID_TRANSITION,)
        self.assert_ledger_error(
            lambda: self.ledger.append_provider_call(
                self.key,
                expected_generation=reserved.generation, attempt_id="other-attempt",
                provider_call=fixture_ledger_fixture.make_provider_call(1),),
            owner_record_validation.ContextLedgerErrorCode.INVALID_TRANSITION,)
        self.assert_ledger_error(
            lambda: self.ledger.append_provider_call(
                self.key,
                expected_generation=reserved.generation, attempt_id=self.plan.attempt_id,
                provider_call=fixture_ledger_fixture.make_provider_call(2),),
            owner_record_validation.ContextLedgerErrorCode.INVALID_TRANSITION,)
        call = fixture_ledger_fixture.make_provider_call(1)
        appended = self.ledger.append_provider_call(
            self.key,
            expected_generation=reserved.generation,
            attempt_id=self.plan.attempt_id,
            provider_call=call,)
        self.assert_ledger_error(
            lambda: self.ledger.append_provider_call(
                self.key,
                expected_generation=appended.generation, attempt_id=self.plan.attempt_id,
                provider_call=replace(call,
                    accounting=fixture_ledger_fixture.make_accounting(usage=ProviderUsage(input_tokens=1)),),
            ),
            owner_record_validation.ContextLedgerErrorCode.CONFLICT,)
        self.assert_ledger_error(
            lambda: self.ledger.append_provider_call(
                self.key,
                expected_generation=1,
                attempt_id=self.plan.attempt_id,
                provider_call=fixture_ledger_fixture.make_provider_call(2),),
            owner_record_validation.ContextLedgerErrorCode.CONFLICT,)

    def test_old_ready_is_removed_before_new_provider_work_and_delta_thread_is_exact(self,
    ) -> None:
        first_ready = self.ready()
        delta_projection = fixture_ledger_fixture.make_projection(self.scope,
            checkpoint=first_ready.checkpoint_view,
            active_provider_thread_id="thread-final",)
        self.assertIs(delta_projection.mode, ctx_planning_inputs.ContextDeliveryMode.DELTA)
        delta_plan = fixture_ledger_fixture.make_plan(delta_projection,
            attempt_id="attempt-delta", operation_id="operation-delta",
            result_correlation_id="result-delta",
            planned_provider_thread_id="thread-final",)
        reserved = self.ledger.reserve_attempt(
            self.key, expected_generation=first_ready.generation,
            plan=delta_plan,)
        self.assertIs(reserved.record.state, owner_record_values.ContextLedgerRecordState.INCOMPLETE)
        self.assertIsNone(reserved.record.ready_checkpoint)
        self.assertIs(reserved.checkpoint_view.state, ctx_planning_inputs.ContextCheckpointState.TAINTED)
        appended = self.ledger.append_provider_call(
            self.key,
            expected_generation=reserved.generation, attempt_id=delta_plan.attempt_id,
            provider_call=fixture_ledger_fixture.make_provider_call(1),)
        wrong_thread = fixture_ledger_fixture.make_authorization(delta_plan,
            delta_projection,
            final_provider_thread_id="other-thread",)
        self.assert_ledger_error(lambda: self.ledger.commit_attempt(
                self.key,
                expected_generation=appended.generation,
                authorization=wrong_thread,),
            owner_record_validation.ContextLedgerErrorCode.COMMIT_NOT_AUTHORIZED,)
        ready = self.ledger.commit_attempt(
            self.key, expected_generation=appended.generation,
            authorization=fixture_ledger_fixture.make_authorization(delta_plan,
                delta_projection,
                final_provider_thread_id="thread-final",),
        )
        self.assertEqual(ready.checkpoint_view.delta_depth, 1)
