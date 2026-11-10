'Existing LedgerScopeAndBootstrapTests scenarios, moved without new cases.'
from __future__ import annotations
from story_writing_agents.agent_context.manifest import ContextSubjectKind
from story_writing_agents.agent_context.contracts import checkpoints as ctx_checkpoints, planning_inputs as ctx_planning_inputs
from story_writing_agents.agent_context.ledger import checkpoint_ledger as owner_checkpoint_ledger
from story_writing_agents.agent_context.ledger import record_values as owner_record_values
from tests.agent_context.ledger import ledger_fixture as fixture_ledger_fixture


class LedgerScopeAndBootstrapTests(fixture_ledger_fixture.LedgerTestCase):
    def test_exact_scope_key_dimensions_are_isolated_without_cross_scope_reads(self,
    ) -> None:
        ready = self.ready()
        cases = (owner_record_values.ContextLedgerKey(fixture_ledger_fixture.make_scope(workspace_id="other-workspace"), "provider"),
            owner_record_values.ContextLedgerKey(fixture_ledger_fixture.make_scope(session_id="other-session"), "provider"), owner_record_values.ContextLedgerKey(
                fixture_ledger_fixture.make_scope(subject_kind=ContextSubjectKind.DEBATE),
                "provider",),
            owner_record_values.ContextLedgerKey(fixture_ledger_fixture.make_scope(subject_id="other-subject"), "provider"),
            owner_record_values.ContextLedgerKey(fixture_ledger_fixture.make_scope(agent_id="plot_architect"), "provider"),
            owner_record_values.ContextLedgerKey(self.scope, "other-provider"),)
        self.assertIs(ready.checkpoint_view.state, ctx_planning_inputs.ContextCheckpointState.READY)
        observed_keys = {owner_record_values.context_ledger_scope_key(self.key)}
        for key in cases:
            with self.subTest(key=key):
                self.assertNotIn(owner_record_values.context_ledger_scope_key(key), observed_keys)
                observed_keys.add(owner_record_values.context_ledger_scope_key(key))
                inspection = self.ledger.inspect(key)
                self.assertEqual(
                    (
                        inspection.generation,
                        inspection.record,
                        inspection.checkpoint_view.state,),
                    (0, None, ctx_planning_inputs.ContextCheckpointState.MISSING),)
                self.assertFalse(self.record_path(key).exists())

    def test_persisted_ready_checkpoint_requests_exact_bootstrap_reasons(self) -> None:
        ready = self.ready()
        checkpoint = ready.checkpoint_view
        cases = (
            (
                fixture_ledger_fixture.make_projection(fixture_ledger_fixture.make_scope(workspace_id="other-workspace"),
                    checkpoint=checkpoint,
                    active_provider_thread_id="thread-final",),
                ctx_planning_inputs.ContextBootstrapReason.SCOPE_MISMATCH,),
            (
                fixture_ledger_fixture.make_projection(
                    self.scope, "other-provider",
                    checkpoint=checkpoint,
                    active_provider_thread_id="thread-final",),
                ctx_planning_inputs.ContextBootstrapReason.PROVIDER_MISMATCH,),
            (
                fixture_ledger_fixture.make_projection(
                    self.scope,
                    checkpoint=checkpoint,
                    active_provider_thread_id=None,),
                ctx_planning_inputs.ContextBootstrapReason.PROVIDER_THREAD_MISMATCH,),
            (
                fixture_ledger_fixture.make_projection(
                    self.scope,
                    checkpoint=checkpoint,
                    active_provider_thread_id="other-thread",),
                ctx_planning_inputs.ContextBootstrapReason.PROVIDER_THREAD_MISMATCH,),
            (
                fixture_ledger_fixture.make_projection(
                    self.scope,
                    checkpoint=checkpoint,
                    active_provider_thread_id="thread-final",
                    definition_fingerprint=fixture_ledger_fixture.digest_bytes(b"other-definition"),),
                ctx_planning_inputs.ContextBootstrapReason.DEFINITION_FINGERPRINT_MISMATCH,),
            (
                fixture_ledger_fixture.make_projection(
                    self.scope,
                    checkpoint=checkpoint,
                    active_provider_thread_id="thread-final",
                    policy=ctx_planning_inputs.ContextPlanningPolicy(max_delta_depth=17),),
                ctx_planning_inputs.ContextBootstrapReason.POLICY_FINGERPRINT_MISMATCH,),
            (
                fixture_ledger_fixture.make_projection(
                    self.scope,
                    checkpoint=checkpoint,
                    active_provider_thread_id="thread-final",
                    schema_version=2,),
                ctx_planning_inputs.ContextBootstrapReason.SCHEMA_MISMATCH,),
        )
        for projection, expected in cases:
            with self.subTest(expected=expected):
                self.assertIs(projection.mode, ctx_planning_inputs.ContextDeliveryMode.BOOTSTRAP)
                self.assertIs(projection.bootstrap_reason, expected)

    def test_persisted_maximum_delta_depth_forces_bootstrap(self) -> None:
        conditions = self.projection.checkpoint_proposal.conditions
        depth_fifteen = ctx_checkpoints.ContextCheckpointView(ctx_planning_inputs.ContextCheckpointState.READY,
            conditions, "thread-depth",
            self.projection.checkpoint_proposal.sources, 15,
            self.projection.checkpoint_proposal.catalog_sources,
            self.projection.checkpoint_proposal.source_alias_high_watermark,)
        delta = fixture_ledger_fixture.make_projection(self.scope,
            checkpoint=depth_fifteen,
            active_provider_thread_id="thread-depth",)
        self.assertEqual(delta.checkpoint_proposal.delta_depth, 16)
        plan = fixture_ledger_fixture.make_plan(delta,
            attempt_id="attempt-depth", operation_id="operation-depth",
            result_correlation_id="result-depth",
            planned_provider_thread_id="thread-depth",)
        ready = self.ready(projection=delta, plan=plan)
        self.assertEqual(ready.checkpoint_view.delta_depth, 16)
        restarted = owner_checkpoint_ledger.ContextLedger(self.workspace, clock=fixture_ledger_fixture.LedgerClock()).inspect(self.key)
        projected = fixture_ledger_fixture.make_projection(self.scope,
            checkpoint=restarted.checkpoint_view,
            active_provider_thread_id="thread-depth",)
        self.assertIs(projected.mode, ctx_planning_inputs.ContextDeliveryMode.BOOTSTRAP)
        self.assertIs(
            projected.bootstrap_reason, ctx_planning_inputs.ContextBootstrapReason.MAX_DELTA_DEPTH)
