from __future__ import annotations
from story_writing_agents.agent_context.manifest import ContextSemanticChannel, ContextSourceKind
from story_writing_agents.agent_context.contracts import planning_inputs as ctx_planning_inputs
from tests.agent_context.planning import fixtures as planning_fixtures


class WorkingAndOptionalSelectionTests(planning_fixtures.PlanningTestCase):
    """Verify hard constraints and deterministic first-fit-with-skip selection."""

    def test_hard_constraints_exact_fit_plus_one_and_missing_fail_atomically(self,
    ) -> None:
        policy = ctx_planning_inputs.ContextPlanningPolicy(6, 1, 1, 4, 0, 0, 16)
        exact, material = planning_fixtures.source("hard",
            b"hard", source_kind=ContextSourceKind.ACTIVE_DEBATE,
        )
        projection = planning_fixtures.project((exact,), (material,), policy=policy)
        self.assertEqual(projection.units[0].entries, (exact,))
        too_large, too_large_material = planning_fixtures.source("hard-large",
            b"large", source_kind=ContextSourceKind.ACTIVE_DEBATE,
        )
        error = self.assert_planning_error(lambda: planning_fixtures.project(
                (too_large,),
                (too_large_material,),
                policy=policy,
            ), ctx_planning_inputs.ContextPlanningErrorCode.WORKING_CONSTRAINT_BUDGET_EXCEEDED,
        )
        self.assertEqual(str(error),
            "working hard constraints exceed the configured budget",)
        self.assert_planning_error(
            lambda: planning_fixtures.project((exact,), (), policy=policy),
            ctx_planning_inputs.ContextPlanningErrorCode.WORKING_CONSTRAINT_MATERIAL_MISSING,)

    def test_p2_and_p3_first_fit_skip_preserves_later_fitting_groups(self) -> None:
        policy = ctx_planning_inputs.ContextPlanningPolicy(9, 1, 1, 5, 2, 0, 16)
        hard, hard_material = planning_fixtures.source("hard",
            b"hh", source_kind=ContextSourceKind.ACTIVE_DEBATE,
        )
        focused, focused_material = planning_fixtures.source("focused-too-large",
            b"ffff", semantic_channel=ContextSemanticChannel.FOCUSED_EVIDENCE,
        )
        retrieved, retrieved_material = planning_fixtures.source("retrieved-fits",
            b"rrr", semantic_channel=ContextSemanticChannel.RETRIEVED_EVIDENCE,
        )
        missing = planning_fixtures.entry("history-missing",
            b"mmm", semantic_channel=ContextSemanticChannel.HISTORY,
        )
        optional_large, optional_large_material = planning_fixtures.source("optional-large",
            b"ooo", source_kind=ContextSourceKind.OWN_AGENT_STATE,
            ordinal=0,)
        optional_fit, optional_fit_material = planning_fixtures.source("optional-fit",
            b"zz", source_kind=ContextSourceKind.OWN_AGENT_STATE,
            ordinal=1,
        )
        projection = planning_fixtures.project((
                optional_fit, missing,
                retrieved, focused,
                hard, optional_large,
            ),
            (optional_fit_material,
                retrieved_material, focused_material,
                hard_material, optional_large_material,
            ),
            policy=policy,
        )
        decisions = planning_fixtures.decision_map(projection)
        self.assertIs(decisions[hard.source_id].decision, ctx_planning_inputs.ContextDecisionKind.SELECTED)
        self.assertIs(decisions[focused.source_id].decision,
            ctx_planning_inputs.ContextDecisionKind.OMITTED_CLASS_BUDGET,
        )
        self.assertIs(decisions[retrieved.source_id].decision,
            ctx_planning_inputs.ContextDecisionKind.SELECTED,
        )
        self.assertIs(decisions[missing.source_id].decision,
            ctx_planning_inputs.ContextDecisionKind.OMITTED_MATERIAL,)
        self.assertIs(decisions[optional_large.source_id].decision,
            ctx_planning_inputs.ContextDecisionKind.OMITTED_CLASS_BUDGET,
        )
        self.assertIs(decisions[optional_fit.source_id].decision,
            ctx_planning_inputs.ContextDecisionKind.SELECTED,
        )
        self.assertEqual(tuple(unit.entries[0].source_id for unit in projection.units),
            (hard.source_id, retrieved.source_id, optional_fit.source_id),)
        self.assertEqual(projection.accounting.budgets.p2.selected_unique_utf8_bytes, 5)
        self.assertEqual(projection.accounting.budgets.p3.selected_unique_utf8_bytes, 2)

    def test_unused_other_class_and_total_headroom_is_not_borrowed(self) -> None:
        policy = ctx_planning_inputs.ContextPlanningPolicy(12, 1, 1, 3, 7, 0, 16)
        focused, material = planning_fixtures.source("focused", b"four")
        projection = planning_fixtures.project((focused,), (material,), policy=policy)
        self.assertIs(
            projection.decisions[0].decision, ctx_planning_inputs.ContextDecisionKind.OMITTED_CLASS_BUDGET,
        )
        self.assertEqual(
            projection.accounting.budgets.total.selected_unique_utf8_bytes, 0
        )
        self.assertEqual(projection.accounting.budgets.p3.remaining_utf8_bytes, 7)
