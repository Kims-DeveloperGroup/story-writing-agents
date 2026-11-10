from __future__ import annotations
from dataclasses import replace
from story_writing_agents.agent_context.manifest import ContextAuthorityKind, ContextProvenance, ContextSemanticChannel, ContextSourceKind
from story_writing_agents.agent_context.contracts import planning_inputs as ctx_planning_inputs
from tests.agent_context.planning import fixtures as planning_fixtures


class RequiredAndStableSelectionTests(planning_fixtures.PlanningTestCase):
    """Verify exact-or-fail P0/P1 treatment and compatible suppression."""

    def test_p0_exact_fit_plus_one_missing_and_no_deduplication(self) -> None:
        policy = ctx_planning_inputs.ContextPlanningPolicy(5, 4, 1, 0, 0, 0, 16)
        exact, exact_material = planning_fixtures.source("required",
            b"abcd", source_kind=ContextSourceKind.RUNTIME_INPUT,
        )
        projection = planning_fixtures.project((exact,),
            (exact_material,), policy=policy,
        )
        self.assertEqual(projection.units[0].body_utf8, b"abcd")
        self.assertIs(
            projection.decisions[0].decision, ctx_planning_inputs.ContextDecisionKind.SELECTED,
        )
        too_large, too_large_material = planning_fixtures.source("required-large",
            b"abcde", source_kind=ContextSourceKind.RUNTIME_INPUT,
        )
        self.assert_planning_error(
            lambda: planning_fixtures.project(
                (too_large,),
                (too_large_material,),
                policy=policy,
            ), ctx_planning_inputs.ContextPlanningErrorCode.REQUIRED_BUDGET_EXCEEDED,
        )
        self.assert_planning_error(
            lambda: planning_fixtures.project((exact,), (), policy=policy),
            ctx_planning_inputs.ContextPlanningErrorCode.REQUIRED_MATERIAL_MISSING,)

        two_policy = ctx_planning_inputs.ContextPlanningPolicy(3, 2, 1, 0, 0, 0, 16)
        first, first_material = planning_fixtures.source("current",
            b"x",
            source_kind=ContextSourceKind.RUNTIME_INPUT,
        )
        second, second_material = planning_fixtures.source("candidate",
            b"x",
            source_kind=ContextSourceKind.RUNTIME_INPUT,
            semantic_channel=ContextSemanticChannel.CANDIDATE,
        )
        two = planning_fixtures.project((second, first),
            (second_material, first_material), policy=two_policy,
        )
        self.assertEqual(len(two.units), 2)
        self.assertEqual(tuple(decision.decision for decision in two.decisions),
            (ctx_planning_inputs.ContextDecisionKind.SELECTED, ctx_planning_inputs.ContextDecisionKind.SELECTED),
        )
        self.assertEqual(two.accounting.saved_duplicate_utf8_bytes, 0)

    def test_p1_exact_fit_plus_one_missing_and_no_deduplication(self) -> None:
        policy = ctx_planning_inputs.ContextPlanningPolicy(5, 1, 4, 0, 0, 0, 16)
        first, first_material = planning_fixtures.source("definition-a",
            b"aa",
            source_kind=ContextSourceKind.AGENT_DEFINITION,
        )
        second, second_material = planning_fixtures.source("definition-b",
            b"aa",
            source_kind=ContextSourceKind.AGENT_DEFINITION,
            ordinal=1,
        )
        projection = planning_fixtures.project(
            (second, first),
            (second_material, first_material), policy=policy,
        )
        self.assertEqual(len(projection.units), 2)
        self.assertEqual(projection.accounting.saved_duplicate_utf8_bytes, 0)
        too_large, too_large_material = planning_fixtures.source("definition-large",
            b"abcde", source_kind=ContextSourceKind.AGENT_DEFINITION,
        )
        self.assert_planning_error(
            lambda: planning_fixtures.project(
                (too_large,),
                (too_large_material,),
                policy=policy,
            ), ctx_planning_inputs.ContextPlanningErrorCode.STABLE_BUDGET_EXCEEDED,
        )
        self.assert_planning_error(lambda: planning_fixtures.project((first,), (), policy=policy),
            ctx_planning_inputs.ContextPlanningErrorCode.STABLE_MATERIAL_MISSING,)

    def test_compatible_delta_always_selects_p0_and_suppresses_p1(self) -> None:
        policy = ctx_planning_inputs.ContextPlanningPolicy(4, 1, 3, 0, 0, 0, 16)
        conditions = planning_fixtures.conditions(policy)
        required, required_material = planning_fixtures.source(
            "input",
            b"q",
            source_kind=ContextSourceKind.RUNTIME_INPUT,
        )
        stable, stable_material = planning_fixtures.source(
            "definition",
            b"role",
            source_kind=ContextSourceKind.AGENT_DEFINITION,
        )
        projection = planning_fixtures.project((stable, required),
            (stable_material, required_material), policy=policy,
            conditions=conditions,
            checkpoint=planning_fixtures.ready_checkpoint(conditions, (stable,)),
            active_provider_thread_id="thread",
        )
        decisions = planning_fixtures.decision_map(projection)
        self.assertIs(decisions[required.source_id].decision,
            ctx_planning_inputs.ContextDecisionKind.SELECTED,
        )
        self.assertIs(
            decisions[stable.source_id].decision,
            ctx_planning_inputs.ContextDecisionKind.CHECKPOINT_SUPPRESSED,
        )
        self.assertEqual( tuple(unit.entries for unit in projection.units), ((required,),) )

    def test_every_valid_p1_set_or_metadata_change_bootstraps(self) -> None:
        policy = planning_fixtures.policy()
        conditions = planning_fixtures.conditions(policy)
        current, material = planning_fixtures.source("definition",
            b"role",
            source_kind=ContextSourceKind.AGENT_DEFINITION,
        )
        previous_variants = ((),
            (replace(current, revision="old-revision"),),
            (
                replace(
                    current, provenance=ContextProvenance(
                        ContextAuthorityKind.AGENT_DEFINITION, "agents/old-definition",
                    ),
                ),
            ),
            (replace(current, ordinal=7),),)
        for previous in previous_variants:
            with self.subTest(previous=previous):
                projection = planning_fixtures.project((current,),
                    (material,),
                    policy=policy,
                    conditions=conditions, checkpoint=planning_fixtures.ready_checkpoint(conditions, previous),
                    active_provider_thread_id="thread",
                )
                self.assertIs(projection.bootstrap_reason,
                    ctx_planning_inputs.ContextBootstrapReason.STABLE_CONTEXT_CHANGED,)
                self.assertEqual(projection.units[0].entries, (current,))
        removed = planning_fixtures.project(
            (),
            (),
            policy=policy,
            conditions=conditions, checkpoint=planning_fixtures.ready_checkpoint(conditions, (current,)),
            active_provider_thread_id="thread",
        )
        self.assertIs(removed.bootstrap_reason,
            ctx_planning_inputs.ContextBootstrapReason.STABLE_CONTEXT_CHANGED,
        )
        self.assertEqual(removed.units, ())
        self.assertEqual(tuple(source.entry for source in removed.removed_checkpoint_sources),
            (),
        )

        sibling, sibling_material = planning_fixtures.source("definition-sibling",
            b"law", source_kind=ContextSourceKind.AGENT_DEFINITION,
            ordinal=1,
        )
        changed_set = planning_fixtures.project((sibling, current),
            (sibling_material, material), policy=policy,
            conditions=conditions,
            checkpoint=planning_fixtures.ready_checkpoint(
                conditions, (replace(current, revision="old-revision"), sibling),
            ),
            active_provider_thread_id="thread",
        )
        self.assertIs(changed_set.bootstrap_reason,
            ctx_planning_inputs.ContextBootstrapReason.STABLE_CONTEXT_CHANGED,
        )
        self.assertEqual(tuple(decision.decision for decision in changed_set.decisions),
            (ctx_planning_inputs.ContextDecisionKind.SELECTED, ctx_planning_inputs.ContextDecisionKind.SELECTED),
        )
        self.assertEqual(tuple(unit.entries[0] for unit in changed_set.units),
            (current, sibling),)
