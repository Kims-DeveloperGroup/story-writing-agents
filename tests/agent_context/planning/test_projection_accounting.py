from __future__ import annotations
from story_writing_agents.agent_context.manifest import ContextSemanticChannel, ContextSourceKind
from story_writing_agents.agent_context.contracts import planning_inputs as ctx_planning_inputs
from story_writing_agents.agent_context.contracts import projection as ctx_projection
from tests.agent_context.planning import fixtures as planning_fixtures


class ProjectionAccountingTests(planning_fixtures.PlanningTestCase):
    """Verify complete explainability, deterministic replay, and privacy."""

    @staticmethod
    def _mixed_projection() -> ctx_projection.ContextProjection:
        policy = ctx_planning_inputs.ContextPlanningPolicy(6, 1, 1, 3, 1, 5, 16)
        conditions = planning_fixtures.conditions(policy)
        required, required_material = planning_fixtures.source(
            "input",
            b"q",
            source_kind=ContextSourceKind.RUNTIME_INPUT,
        )
        stable, stable_material = planning_fixtures.source(
            "definition",
            b"r",
            source_kind=ContextSourceKind.AGENT_DEFINITION,
        )
        hard = planning_fixtures.entry("hard-suppressed",
            b"h", source_kind=ContextSourceKind.ACTIVE_DEBATE,
        )
        focused, focused_material = planning_fixtures.source("focused", b"aa", ordinal=0)
        alias, alias_material = planning_fixtures.source("focused-alias", b"aa", ordinal=1)
        retrieved, retrieved_material = planning_fixtures.source("retrieved-omitted",
            b"bbbb", semantic_channel=ContextSemanticChannel.RETRIEVED_EVIDENCE,
        )
        history = planning_fixtures.entry("history-missing",
            b"ccc", semantic_channel=ContextSemanticChannel.HISTORY,
        )
        optional, optional_material = planning_fixtures.source(
            "optional",
            b"z",
            source_kind=ContextSourceKind.OWN_AGENT_STATE,
        )
        references = (planning_fixtures.reference(1, utf8_bytes=1),
            planning_fixtures.reference(2, utf8_bytes=2),)
        return planning_fixtures.project((
                optional, history,
                retrieved, alias,
                hard, stable,
                focused, required,
            ),
            (optional_material,
                retrieved_material, alias_material,
                stable_material, focused_material,
                required_material,),
            policy=policy,
            conditions=conditions, checkpoint=planning_fixtures.ready_checkpoint(conditions, (stable, hard)),
            active_provider_thread_id="thread", mandatory_references=references,
        )

    def test_every_decision_searchable_and_budget_partition_is_exact(self) -> None:
        projection = self._mixed_projection()
        decisions = planning_fixtures.decision_map(projection)
        self.assertEqual(len(decisions), len(projection.decisions))
        expected = {"input": ctx_planning_inputs.ContextDecisionKind.SELECTED,
            "definition": ctx_planning_inputs.ContextDecisionKind.CHECKPOINT_SUPPRESSED,
            "hard-suppressed": ctx_planning_inputs.ContextDecisionKind.CHECKPOINT_SUPPRESSED,
            "focused": ctx_planning_inputs.ContextDecisionKind.SELECTED,
            "focused-alias": ctx_planning_inputs.ContextDecisionKind.DEDUPLICATED,
            "retrieved-omitted": ctx_planning_inputs.ContextDecisionKind.OMITTED_CLASS_BUDGET,
            "history-missing": ctx_planning_inputs.ContextDecisionKind.OMITTED_MATERIAL,
            "optional": ctx_planning_inputs.ContextDecisionKind.SELECTED,}
        self.assertEqual(
            {source_id: decision.decision for source_id, decision in decisions.items()}, expected,
        )
        self.assertTrue(
            all(decision.searchable is decision.entry.searchable
                for decision in projection.decisions
            )
        )
        searchable = projection.accounting.searchable
        self.assertEqual(
            (searchable.total_source_count,
                searchable.total_source_utf8_bytes, searchable.selected_primary_count,
                searchable.selected_primary_utf8_bytes, searchable.deduplicated_pointer_count,
                searchable.deduplicated_pointer_utf8_bytes, searchable.checkpoint_suppressed_count,
                searchable.checkpoint_suppressed_utf8_bytes, searchable.class_budget_omitted_count,
                searchable.class_budget_omitted_utf8_bytes, searchable.material_omitted_count,
                searchable.material_omitted_utf8_bytes, searchable.selected_source_count,
                searchable.selected_source_utf8_bytes, searchable.unprojected_source_count,
                searchable.unprojected_source_utf8_bytes,),
            (6, 13, 2, 3, 1, 2, 1, 1, 1, 4, 1, 3, 3, 5, 3, 8),)
        accounting = projection.accounting
        self.assertEqual(accounting.logical_source_count, 8)
        self.assertEqual(accounting.logical_source_utf8_bytes, 15)
        self.assertEqual(accounting.selected_logical_source_count, 4)
        self.assertEqual(accounting.selected_logical_utf8_bytes, 6)
        self.assertEqual(accounting.projected_unit_count, 3)
        self.assertEqual(accounting.projected_unique_utf8_bytes, 4)
        self.assertEqual(accounting.saved_duplicate_utf8_bytes, 2)
        self.assertEqual(
            (accounting.budgets.total.selected_unique_utf8_bytes,
                accounting.budgets.p0.selected_unique_utf8_bytes,
                accounting.budgets.p1.selected_unique_utf8_bytes,
                accounting.budgets.p2.selected_unique_utf8_bytes,
                accounting.budgets.p3.selected_unique_utf8_bytes,
                accounting.budgets.mandatory_references.used_utf8_bytes,),
            (4, 1, 0, 2, 1, 3),)

    def test_replay_is_identical_and_all_retained_values_are_content_free(self) -> None:
        first = self._mixed_projection()
        second = self._mixed_projection()
        self.assertEqual(first, second)
        self.assertEqual(first.accounting, second.accounting)

        secret = "PRIVATE-PROJECTION-BODY-한글🙂"
        entry, material = planning_fixtures.source("private-source", secret.encode("utf-8"))
        private_projection = planning_fixtures.project((entry,), (material,))
        self.assertNotIn(secret, repr(private_projection))
        self.assertNotIn(secret, repr(private_projection.accounting))
        self.assertNotIn(secret, repr(private_projection.checkpoint_proposal))
