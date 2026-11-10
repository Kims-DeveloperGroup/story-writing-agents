from __future__ import annotations
from dataclasses import replace
from story_writing_agents.agent_context.manifest import ContextManifestEntry, ContextSourceKind
from story_writing_agents.agent_context.contracts import checkpoints as ctx_checkpoints
from story_writing_agents.agent_context.contracts import planning_inputs as ctx_planning_inputs
from tests.agent_context.planning import fixtures as planning_fixtures


class BootstrapPlanningTests(planning_fixtures.PlanningTestCase):
    """Verify checkpoint compatibility, precedence, and depth semantics."""

    def _stable_fixture(self,
    ) -> tuple[ctx_planning_inputs.ContextPlanningPolicy,
        ctx_planning_inputs.ContextPlanningConditions, ContextManifestEntry,
        ctx_planning_inputs.ContextMaterial,]:
        policy = planning_fixtures.policy()
        conditions = planning_fixtures.conditions(policy)
        stable, material = planning_fixtures.source("definition",
            b"role",
            source_kind=ContextSourceKind.AGENT_DEFINITION,
        )
        return policy, conditions, stable, material

    def test_checkpoint_state_and_explicit_fresh_reasons(self) -> None:
        policy, conditions, stable, material = self._stable_fixture()
        state_reasons = {ctx_planning_inputs.ContextCheckpointState.MISSING: ctx_planning_inputs.ContextBootstrapReason.CHECKPOINT_MISSING,
            ctx_planning_inputs.ContextCheckpointState.CORRUPT: ctx_planning_inputs.ContextBootstrapReason.CHECKPOINT_CORRUPT,
            ctx_planning_inputs.ContextCheckpointState.STALE: ctx_planning_inputs.ContextBootstrapReason.CHECKPOINT_STALE,
            ctx_planning_inputs.ContextCheckpointState.TAINTED: ctx_planning_inputs.ContextBootstrapReason.CHECKPOINT_TAINTED,}
        for state, reason in state_reasons.items():
            projection = planning_fixtures.project((stable,),
                (material,), policy=policy,
                conditions=conditions,
                checkpoint=ctx_checkpoints.ContextCheckpointView(state),)
            self.assertIs(projection.mode, ctx_planning_inputs.ContextDeliveryMode.BOOTSTRAP)
            self.assertIs(projection.bootstrap_reason, reason)
            self.assertEqual(projection.checkpoint_proposal.delta_depth, 0)
        fresh = planning_fixtures.project((stable,),
            (material,),
            policy=policy,
            conditions=conditions, checkpoint=ctx_checkpoints.ContextCheckpointView(ctx_planning_inputs.ContextCheckpointState.CORRUPT),
            fresh=True,)
        self.assertIs( fresh.bootstrap_reason, ctx_planning_inputs.ContextBootstrapReason.EXPLICIT_FRESH, )

    def test_ready_mismatch_matrix_and_thread_none_semantics(self) -> None:
        policy, conditions, stable, material = self._stable_fixture()
        other_scope = planning_fixtures.scope(workspace_id="other")
        cases = (
            (
                planning_fixtures.ready_checkpoint(planning_fixtures.conditions(policy, scope=other_scope),
                    (stable,),
                ),
                "thread",
                ctx_planning_inputs.ContextBootstrapReason.SCOPE_MISMATCH,
            ),
            (
                planning_fixtures.ready_checkpoint(planning_fixtures.conditions(policy, provider_id="other-provider"),
                    (stable,),
                ),
                "thread",
                ctx_planning_inputs.ContextBootstrapReason.PROVIDER_MISMATCH,
            ),
            (
                planning_fixtures.ready_checkpoint(conditions, (stable,), provider_thread_id=None), None,
                ctx_planning_inputs.ContextBootstrapReason.PROVIDER_CONTEXT_UNAVAILABLE,
            ),
            (
                planning_fixtures.ready_checkpoint(conditions, (stable,), provider_thread_id=None), "thread",
                ctx_planning_inputs.ContextBootstrapReason.PROVIDER_THREAD_MISMATCH,
            ),
            (
                planning_fixtures.ready_checkpoint(conditions, (stable,)), None,
                ctx_planning_inputs.ContextBootstrapReason.PROVIDER_THREAD_MISMATCH,
            ),
            (
                planning_fixtures.ready_checkpoint(conditions, (stable,)), "different-thread",
                ctx_planning_inputs.ContextBootstrapReason.PROVIDER_THREAD_MISMATCH,
            ),
            (
                planning_fixtures.ready_checkpoint(
                    planning_fixtures.conditions(
                        policy,
                        definition_fingerprint=planning_fixtures.digest(b"old-definition"),),
                    (stable,),
                ),
                "thread",
                ctx_planning_inputs.ContextBootstrapReason.DEFINITION_FINGERPRINT_MISMATCH,
            ),
            (
                planning_fixtures.ready_checkpoint(
                    planning_fixtures.conditions(
                        policy,
                        policy_fingerprint=planning_fixtures.digest(b"old-policy"),),
                    (stable,),
                ),
                "thread",
                ctx_planning_inputs.ContextBootstrapReason.POLICY_FINGERPRINT_MISMATCH,
            ),
            (
                planning_fixtures.ready_checkpoint(
                    planning_fixtures.conditions(policy, schema_version=2),
                    (stable,),
                ),
                "thread",
                ctx_planning_inputs.ContextBootstrapReason.SCHEMA_MISMATCH,
            ),
            (planning_fixtures.ready_checkpoint(conditions, (stable,), delta_depth=16),
                "thread", ctx_planning_inputs.ContextBootstrapReason.MAX_DELTA_DEPTH,
            ),
        )
        for checkpoint, active_thread, expected in cases:
            with self.subTest(expected=expected):
                projection = planning_fixtures.project(
                    (stable,),
                    (material,),
                    policy=policy,
                    conditions=conditions,
                    checkpoint=checkpoint,
                    active_provider_thread_id=active_thread,
                )
                self.assertIs(projection.bootstrap_reason, expected)
                self.assertEqual(projection.checkpoint_proposal.delta_depth, 0)

    def test_stable_change_reason_and_precedence_are_exact(self) -> None:
        policy, conditions, stable, material = self._stable_fixture()
        old_stable = replace(stable, revision="old-revision")
        changed = planning_fixtures.project((stable,),
            (material,),
            policy=policy,
            conditions=conditions, checkpoint=planning_fixtures.ready_checkpoint(conditions, (old_stable,)),
            active_provider_thread_id="thread",
        )
        self.assertIs(changed.bootstrap_reason,
            ctx_planning_inputs.ContextBootstrapReason.STABLE_CONTEXT_CHANGED,
        )
        self.assertEqual(tuple(source.entry for source in changed.checkpoint_proposal.sources),
            (stable,),)

        definition_precedes = planning_fixtures.project((stable,),
            (material,),
            policy=policy,
            conditions=conditions,
            checkpoint=planning_fixtures.ready_checkpoint(planning_fixtures.conditions(
                    policy,
                    definition_fingerprint=planning_fixtures.digest(b"old-definition"),),
                (old_stable,),),
            active_provider_thread_id="thread",
        )
        self.assertIs(definition_precedes.bootstrap_reason,
            ctx_planning_inputs.ContextBootstrapReason.DEFINITION_FINGERPRINT_MISMATCH,)
        stable_precedes_policy = planning_fixtures.project((stable,),
            (material,),
            policy=policy,
            conditions=conditions,
            checkpoint=planning_fixtures.ready_checkpoint(
                planning_fixtures.conditions( policy, policy_fingerprint=planning_fixtures.digest(b"old-policy"), schema_version=2, ),
                (old_stable,),
                delta_depth=16,),
            active_provider_thread_id="thread",
        )
        self.assertIs(stable_precedes_policy.bootstrap_reason,
            ctx_planning_inputs.ContextBootstrapReason.STABLE_CONTEXT_CHANGED,
        )

    def test_compatibility_reason_precedence_is_pairwise_complete(self) -> None:
        policy, conditions, stable, material = self._stable_fixture()
        lower_mismatches = {"provider_id": "other-provider",
            "definition_fingerprint": planning_fixtures.digest(b"old-definition"),
            "policy_fingerprint": planning_fixtures.digest(b"old-policy"),
            "schema_version": 2,}
        cases = (
            (
                planning_fixtures.ready_checkpoint(
                    planning_fixtures.conditions(
                        policy, scope=planning_fixtures.scope(workspace_id="other"),
                        **lower_mismatches,),
                    (stable,),
                    provider_thread_id="old-thread",
                    delta_depth=16,
                ),
                "new-thread",
                ctx_planning_inputs.ContextBootstrapReason.SCOPE_MISMATCH,
            ),
            (
                planning_fixtures.ready_checkpoint(planning_fixtures.conditions(policy, **lower_mismatches),
                    (stable,),
                    provider_thread_id="old-thread",
                    delta_depth=16,
                ),
                "new-thread",
                ctx_planning_inputs.ContextBootstrapReason.PROVIDER_MISMATCH,
            ),
            (
                planning_fixtures.ready_checkpoint(
                    planning_fixtures.conditions(
                        policy,
                        definition_fingerprint=planning_fixtures.digest(b"old-definition"),
                        policy_fingerprint=planning_fixtures.digest(b"old-policy"),
                        schema_version=2,
                    ),
                    (stable,), provider_thread_id=None,
                    delta_depth=16,
                ), None,
                ctx_planning_inputs.ContextBootstrapReason.PROVIDER_CONTEXT_UNAVAILABLE,
            ),
            (
                planning_fixtures.ready_checkpoint(
                    planning_fixtures.conditions(
                        policy,
                        definition_fingerprint=planning_fixtures.digest(b"old-definition"),
                        policy_fingerprint=planning_fixtures.digest(b"old-policy"),
                        schema_version=2,
                    ),
                    (stable,),
                    provider_thread_id="old-thread",
                    delta_depth=16,
                ),
                "new-thread",
                ctx_planning_inputs.ContextBootstrapReason.PROVIDER_THREAD_MISMATCH,
            ),
            (
                planning_fixtures.ready_checkpoint(
                    planning_fixtures.conditions(
                        policy, policy_fingerprint=planning_fixtures.digest(b"old-policy"),
                        schema_version=2,
                    ),
                    (stable,),
                    delta_depth=16,
                ),
                "thread",
                ctx_planning_inputs.ContextBootstrapReason.POLICY_FINGERPRINT_MISMATCH,
            ),
            (
                planning_fixtures.ready_checkpoint(
                    planning_fixtures.conditions(policy, schema_version=2),
                    (stable,),
                    delta_depth=16,
                ),
                "thread",
                ctx_planning_inputs.ContextBootstrapReason.SCHEMA_MISMATCH,
            ),
        )
        for checkpoint, active_thread, expected in cases:
            with self.subTest(expected=expected):
                projection = planning_fixtures.project(
                    (stable,),
                    (material,),
                    policy=policy,
                    conditions=conditions,
                    checkpoint=checkpoint,
                    active_provider_thread_id=active_thread,
                )
                self.assertIs(projection.bootstrap_reason, expected)

    def test_compatible_depth_15_delta_advances_to_16(self) -> None:
        policy, conditions, stable, material = self._stable_fixture()
        projection = planning_fixtures.project((stable,),
            (material,),
            policy=policy,
            conditions=conditions,
            checkpoint=planning_fixtures.ready_checkpoint(
                conditions, (stable,),
                delta_depth=15,),
            active_provider_thread_id="thread",
        )
        self.assertIs(projection.mode, ctx_planning_inputs.ContextDeliveryMode.DELTA)
        self.assertIsNone(projection.bootstrap_reason)
        self.assertEqual(projection.checkpoint_proposal.delta_depth, 16)
        self.assertEqual(projection.units, ())
        self.assertIs(
            projection.decisions[0].decision, ctx_planning_inputs.ContextDecisionKind.CHECKPOINT_SUPPRESSED,
        )
