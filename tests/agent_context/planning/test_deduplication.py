from __future__ import annotations
from story_writing_agents.agent_context.contracts import projection as ctx_projection
from story_writing_agents.agent_context.manifest import ContextSemanticChannel
from unittest import mock
from story_writing_agents.agent_context.contracts import planning_inputs as ctx_planning_inputs
from tests.agent_context.planning import fixtures as planning_fixtures


class DeduplicationPlanningTests(planning_fixtures.PlanningTestCase):
    """Verify byte-equality deduplication without losing semantic pointers."""

    def test_same_channel_exact_bodies_serialize_once_with_all_pointers(self) -> None:
        first, first_material = planning_fixtures.source("first", b"same", ordinal=0)
        second, second_material = planning_fixtures.source("second", b"same", ordinal=1)
        projection = planning_fixtures.project(
            (second, first),
            (second_material, first_material),
        )
        self.assertEqual(len(projection.units), 1)
        self.assertEqual(projection.units[0].entries, (first, second))
        decisions = planning_fixtures.decision_map(projection)
        self.assertIs(decisions[first.source_id].decision, ctx_planning_inputs.ContextDecisionKind.SELECTED)
        self.assertIs(decisions[second.source_id].decision,
            ctx_planning_inputs.ContextDecisionKind.DEDUPLICATED,)
        self.assertEqual(decisions[second.source_id].represented_by_source_id,
            first.source_id,)
        self.assertEqual(projection.accounting.selected_logical_source_count, 2)
        self.assertEqual(projection.accounting.selected_logical_utf8_bytes, 8)
        self.assertEqual(projection.accounting.projected_unique_utf8_bytes, 4)
        self.assertEqual(projection.accounting.saved_duplicate_utf8_bytes, 4)

    def test_cross_channel_bodies_never_deduplicate(self) -> None:
        focused, focused_material = planning_fixtures.source("focused", b"same")
        history, history_material = planning_fixtures.source("history",
            b"same", semantic_channel=ContextSemanticChannel.HISTORY,
        )
        projection = planning_fixtures.project((history, focused),
            (history_material, focused_material),)
        self.assertEqual(len(projection.units), 2)
        self.assertTrue(
            all(decision.decision is ctx_planning_inputs.ContextDecisionKind.SELECTED
                for decision in projection.decisions
            )
        )
        self.assertEqual(projection.accounting.saved_duplicate_utf8_bytes, 0)

    def test_forced_digest_collision_still_requires_exact_body_equality(self) -> None:
        forced = "sha256:" + ("f" * 64)
        with (mock.patch.object(ctx_planning_inputs, "body_digest", return_value=forced),
              mock.patch.object(ctx_projection, "body_digest", return_value=forced)):
            first = planning_fixtures.make_entry("first", b"alpha", content_digest=forced)
            second = planning_fixtures.make_entry("second", b"bravo", content_digest=forced, ordinal=1)
            first_material = ctx_planning_inputs.ContextMaterial(first, b"alpha")
            second_material = ctx_planning_inputs.ContextMaterial(second, b"bravo")
            projection = planning_fixtures.project((second, first),
                (second_material, first_material),
                policy=ctx_planning_inputs.ContextPlanningPolicy(12, 1, 1, 10, 0, 0, 16),)
        self.assertEqual(len(projection.units), 2)
        self.assertEqual(projection.accounting.projected_unique_utf8_bytes, 10)
        self.assertEqual(projection.accounting.saved_duplicate_utf8_bytes, 0)
