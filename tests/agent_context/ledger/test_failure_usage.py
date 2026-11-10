'Existing LedgerUnsafeOutcomeAndUsageTests scenarios, moved without new cases.'
from __future__ import annotations
import json
from story_writing_agents.agent_context.accounting import ProviderUsage
from story_writing_agents.agent_context.contracts import planning_inputs as ctx_planning_inputs
from story_writing_agents.agent_context.ledger import checkpoint_ledger as owner_checkpoint_ledger
from story_writing_agents.agent_context.ledger import record_values as owner_record_values
from tests.agent_context.ledger import ledger_fixture as fixture_ledger_fixture


class LedgerUnsafeOutcomeAndUsageTests(fixture_ledger_fixture.LedgerTestCase):
    def test_every_unsafe_outcome_is_restart_safe_tainted_and_idempotent(self) -> None:
        unsafe = tuple(outcome
            for outcome in owner_record_values.ContextLedgerAttemptOutcome if outcome
            not in {owner_record_values.ContextLedgerAttemptOutcome.IN_PROGRESS,
                owner_record_values.ContextLedgerAttemptOutcome.COMMITTED,}
        )
        self.assertEqual(len(unsafe), 9)
        for index, outcome in enumerate(unsafe, start=1):
            with self.subTest(outcome=outcome):
                scope = fixture_ledger_fixture.make_scope(subject_id=f"unsafe-{index}")
                key = owner_record_values.ContextLedgerKey(scope, "provider")
                projection = fixture_ledger_fixture.make_projection(scope)
                plan = fixture_ledger_fixture.make_plan(
                    projection, attempt_id=f"attempt-unsafe-{index}",
                    operation_id=f"operation-unsafe-{index}",
                    result_correlation_id=f"result-unsafe-{index}",)
                reserved = self.ledger.reserve_attempt(
                    key,
                    expected_generation=0,
                    plan=plan,)
                tainted = self.ledger.taint_attempt(key,
                    expected_generation=reserved.generation,
                    attempt_id=plan.attempt_id,
                    outcome=outcome,)
                self.assertEqual(
                    (tainted.record.state,
                        tainted.record.attempt.outcome,
                        tainted.checkpoint_view.state,),
                    (owner_record_values.ContextLedgerRecordState.TAINTED,
                        outcome,
                        ctx_planning_inputs.ContextCheckpointState.TAINTED,),
                )
                restarted = owner_checkpoint_ledger.ContextLedger(self.workspace, clock=fixture_ledger_fixture.LedgerClock()).inspect(key)
                self.assertEqual(restarted, tainted)
                self.assertEqual(self.ledger.taint_attempt(
                        key, expected_generation=0,
                        attempt_id=plan.attempt_id,
                        outcome=outcome,),
                    tainted,)

    def test_incomplete_restart_maps_to_tainted_and_never_authorizes_delta(self,
    ) -> None:
        reserved = self.ledger.reserve_attempt(
            self.key,
            expected_generation=0,
            plan=self.plan,)
        restarted = owner_checkpoint_ledger.ContextLedger(self.workspace, clock=fixture_ledger_fixture.LedgerClock()).inspect(self.key)
        self.assertEqual(restarted, reserved)
        self.assertIs(restarted.checkpoint_view.state, ctx_planning_inputs.ContextCheckpointState.TAINTED)
        fresh_projection = fixture_ledger_fixture.make_projection(self.scope,
            checkpoint=restarted.checkpoint_view,
            active_provider_thread_id="thread",)
        self.assertIs(fresh_projection.mode, ctx_planning_inputs.ContextDeliveryMode.BOOTSTRAP)
        self.assertIs(fresh_projection.bootstrap_reason,
            ctx_planning_inputs.ContextBootstrapReason.CHECKPOINT_TAINTED,)

    def test_usage_unavailable_zero_partial_and_full_remains_exact_per_call(self,
    ) -> None:
        usages = (None,
            ProviderUsage(input_tokens=0), ProviderUsage(output_tokens=7),
            ProviderUsage(input_tokens=11, output_tokens=5, cached_input_tokens=3),)
        for index, usage in enumerate(usages, start=1):
            with self.subTest(usage=usage):
                scope = fixture_ledger_fixture.make_scope(subject_id=f"usage-{index}")
                key = owner_record_values.ContextLedgerKey(scope, "provider")
                projection = fixture_ledger_fixture.make_projection(scope)
                plan = fixture_ledger_fixture.make_plan(
                    projection, attempt_id=f"attempt-usage-{index}",
                    operation_id=f"operation-usage-{index}",
                    result_correlation_id=f"result-usage-{index}",)
                ready = self.ready(usage=usage,
                    key=key, projection=projection,
                    plan=plan,)
                persisted = ready.record.attempt.provider_calls[0].accounting.usage
                self.assertEqual(persisted, usage)
                raw = json.loads(self.record_path(key).read_bytes())
                raw_usage = raw["attempt"]["provider_calls"][0]["accounting"]["usage"]
                if usage is None:
                    self.assertIsNone(raw_usage)
                else:
                    self.assertEqual(raw_usage,
                        {"cached_input_tokens": usage.cached_input_tokens,
                            "input_tokens": usage.input_tokens,
                            "output_tokens": usage.output_tokens,},
                    )
        scope = fixture_ledger_fixture.make_scope(subject_id="usage-multiple")
        key = owner_record_values.ContextLedgerKey(scope, "provider")
        projection = fixture_ledger_fixture.make_projection(scope)
        plan = fixture_ledger_fixture.make_plan(projection,
            attempt_id="attempt-usage-multiple", operation_id="operation-usage-multiple",
            result_correlation_id="result-usage-multiple",)
        reserved = self.ledger.reserve_attempt(key, expected_generation=0, plan=plan)
        first = self.ledger.append_provider_call(key,
            expected_generation=reserved.generation, attempt_id=plan.attempt_id,
            provider_call=fixture_ledger_fixture.make_provider_call(1, usage=ProviderUsage(input_tokens=5)),)
        second = self.ledger.append_provider_call(key,
            expected_generation=first.generation, attempt_id=plan.attempt_id,
            provider_call=fixture_ledger_fixture.make_provider_call(2, usage=ProviderUsage(input_tokens=7)),)
        self.assertEqual(
            tuple(call.accounting.usage.input_tokens
                for call in second.record.attempt.provider_calls),
            (5, 7),)
