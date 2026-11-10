from __future__ import annotations
from dataclasses import replace
from story_writing_agents.agent_context.manifest import ContextAuthorityKind, ContextProvenance, ContextSourceKind
from story_writing_agents.agent_context.contracts import checkpoints as ctx_checkpoints
from story_writing_agents.agent_context.contracts import planning_inputs as ctx_planning_inputs
from story_writing_agents.agent_context.contracts import projection as ctx_projection
from story_writing_agents.agent_context.contracts import projection_accounting as ctx_projection_accounting
from tests.agent_context.planning import fixtures as planning_fixtures
from tests.agent_context.planning import test_projection_accounting as accounting_cases


class ProjectionInvariantTests(planning_fixtures.PlanningTestCase):
    """Adversarially verify every exported cross-object projection invariant."""

    def _deduplicated_projection(self) -> ctx_projection.ContextProjection:
        first, first_material = planning_fixtures.source("first", b"same", ordinal=0)
        second, second_material = planning_fixtures.source("second", b"same", ordinal=1)
        return planning_fixtures.project((second, first),
            (second_material, first_material),
        )

    def test_accounting_depth_bucket_order_uniqueness_and_selected_relations(self,
    ) -> None:
        projection = self._deduplicated_projection()
        accounting = projection.accounting
        mutations = ({"proposed_delta_depth": 1},
            {"projected_unit_count": accounting.projected_unit_count + 1},
            {"projected_unique_utf8_bytes": accounting.projected_unique_utf8_bytes + 1},
            {"saved_duplicate_utf8_bytes": accounting.saved_duplicate_utf8_bytes + 1}, {
                "selected_logical_source_count": accounting.selected_logical_source_count + 1
            }, {"selected_logical_utf8_bytes": accounting.selected_logical_utf8_bytes + 1},
            {"decision_buckets": tuple(reversed(accounting.decision_buckets))},)
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.assert_planning_error(
                    lambda mutation=mutation: replace(accounting, **mutation),
                    ctx_planning_inputs.ContextPlanningErrorCode.INVALID_VALUE,
                )

        first, first_material = planning_fixtures.source("one", b"x", ordinal=0)
        second, second_material = planning_fixtures.source("two", b"y", ordinal=1)
        two_selected = planning_fixtures.project((second, first),
            (second_material, first_material),).accounting
        bucket = next(item
            for item in two_selected.decision_buckets
            if item.decision is ctx_planning_inputs.ContextDecisionKind.SELECTED)
        duplicate_buckets = tuple(
            item for item in two_selected.decision_buckets if item is not bucket) + (
            replace(bucket, source_count=1, raw_source_utf8_bytes=1),
            replace(bucket, source_count=1, raw_source_utf8_bytes=1),)
        self.assert_planning_error(
            lambda: replace(two_selected, decision_buckets=duplicate_buckets),
            ctx_planning_inputs.ContextPlanningErrorCode.INVALID_VALUE,
        )

        policy = planning_fixtures.make_policy()
        conditions = planning_fixtures.make_conditions(policy)
        stable, stable_material = planning_fixtures.source(
            "definition",
            b"r",
            source_kind=ContextSourceKind.AGENT_DEFINITION,
        )
        delta = planning_fixtures.project((stable,),
            (stable_material,), policy=policy,
            conditions=conditions,
            checkpoint=planning_fixtures.ready_checkpoint(conditions, (stable,)),
            active_provider_thread_id="thread",
        )
        self.assert_planning_error(lambda: replace(delta.accounting, proposed_delta_depth=0),
            ctx_planning_inputs.ContextPlanningErrorCode.INVALID_VALUE,
        )

    def test_unit_decision_mapping_and_same_id_metadata_substitutions_fail(self,
    ) -> None:
        projection = self._deduplicated_projection()
        decisions = list(projection.decisions)
        alias_index = next(index
            for index, decision in enumerate(decisions)
            if decision.decision is ctx_planning_inputs.ContextDecisionKind.DEDUPLICATED)
        decisions[alias_index] = replace(decisions[alias_index],
            represented_by_source_id="wrong-representative",)
        self.assert_planning_error(lambda: replace(projection, decisions=tuple(decisions)),
            ctx_planning_inputs.ContextPlanningErrorCode.INVALID_VALUE,
        )

        selected = next(decision.entry
            for decision in projection.decisions
            if decision.decision is ctx_planning_inputs.ContextDecisionKind.SELECTED)
        body = projection.units[0].body_utf8
        substitutions = (replace(selected, revision="other-revision"),
            replace(
                selected, provenance=ContextProvenance(
                    ContextAuthorityKind.WRITING_ARTIFACT_REPOSITORY, "sources/other-path",
                ),
            ),
            replace(selected, ordinal=99), replace(
                selected, source_kind=ContextSourceKind.CONVERSATION,
                provenance=ContextProvenance(ContextAuthorityKind.CONVERSATION_STORAGE,
                    "conversations/c_main",
                ),
            ),
        )
        for substitute in substitutions:
            with self.subTest(substitute=substitute):
                unit = ctx_projection.ContextProjectedUnit((substitute,), body)
                self.assert_planning_error(lambda unit=unit: replace(projection, units=(unit,)),
                    ctx_planning_inputs.ContextPlanningErrorCode.INVALID_VALUE,
                )
        first, first_material = planning_fixtures.source("ordered-first", b"a", ordinal=0)
        second, second_material = planning_fixtures.source("ordered-second", b"b", ordinal=1)
        ordered = planning_fixtures.project((second, first),
            (second_material, first_material),
        )
        self.assertEqual(len(ordered.units), 2)
        self.assert_planning_error(lambda: replace(ordered, units=tuple(reversed(ordered.units))),
            ctx_planning_inputs.ContextPlanningErrorCode.INVALID_VALUE,
        )

    def test_proposal_removed_reference_and_budget_cross_checks(self) -> None:
        mixed = accounting_cases.ProjectionAccountingTests._mixed_projection()
        proposal = mixed.checkpoint_proposal
        self.assert_planning_error(
            lambda: replace(
                mixed,
                checkpoint_proposal=ctx_projection.ContextCheckpointProposal(
                    proposal.conditions, proposal.sources[:-1],
                    proposal.delta_depth,
                ),
            ),
            ctx_planning_inputs.ContextPlanningErrorCode.INVALID_VALUE,
        )
        omitted = next(decision.entry
            for decision in mixed.decisions
            if decision.decision is ctx_planning_inputs.ContextDecisionKind.OMITTED_MATERIAL)
        self.assert_planning_error(
            lambda: replace(
                mixed,
                checkpoint_proposal=ctx_projection.ContextCheckpointProposal(
                    proposal.conditions, (*proposal.sources, ctx_checkpoints.ContextCheckpointSource(omitted)),
                    proposal.delta_depth,
                ),
            ),
            ctx_planning_inputs.ContextPlanningErrorCode.INVALID_VALUE,
        )
        self.assert_planning_error(
            lambda: replace(
                mixed, checkpoint_proposal=replace(
                    proposal, delta_depth=proposal.delta_depth + 1,
                ),
            ),
            ctx_planning_inputs.ContextPlanningErrorCode.INVALID_VALUE,
        )

        policy = planning_fixtures.make_policy()
        conditions = planning_fixtures.make_conditions(policy)
        removed = planning_fixtures.make_entry("removed", b"old")
        required, required_material = planning_fixtures.source(
            "input",
            b"q",
            source_kind=ContextSourceKind.RUNTIME_INPUT,
        )
        projection = planning_fixtures.project((required,),
            (required_material,), policy=policy,
            conditions=conditions, checkpoint=planning_fixtures.ready_checkpoint(conditions, (removed,)),
            active_provider_thread_id="thread",
        )
        self.assertEqual(
            tuple(source.entry for source in projection.removed_checkpoint_sources), (removed,),
        )
        self.assertEqual(
            (projection.accounting.removed_checkpoint_source_count,
                projection.accounting.removed_checkpoint_source_utf8_bytes,),
            (1, removed.utf8_bytes),)
        bad_removed_accounting = replace(projection.accounting,
            removed_checkpoint_source_count=0,)
        self.assert_planning_error(lambda: replace(projection, accounting=bad_removed_accounting),
            ctx_planning_inputs.ContextPlanningErrorCode.INVALID_VALUE,
        )
        self.assert_planning_error(
            lambda: replace(
                projection, removed_checkpoint_sources=(
                    projection.removed_checkpoint_sources[0],
                    projection.removed_checkpoint_sources[0],
                ),
            ),
            ctx_planning_inputs.ContextPlanningErrorCode.INVALID_VALUE,
        )
        self.assert_planning_error(
            lambda: replace(
                projection, accounting=replace(
                    projection.accounting, removed_checkpoint_source_utf8_bytes=0,
                ),
            ),
            ctx_planning_inputs.ContextPlanningErrorCode.INVALID_VALUE,
        )

        duplicate_reference = replace(mixed.mandatory_references[1],
            reference_id=mixed.mandatory_references[0].reference_id,)
        self.assert_planning_error(
            lambda: replace(
                mixed, mandatory_references=(
                    mixed.mandatory_references[0], duplicate_reference,
                ),
            ),
            ctx_planning_inputs.ContextPlanningErrorCode.INVALID_VALUE,
        )
        self.assert_planning_error(
            lambda: replace(
                mixed, mandatory_references=tuple(reversed(mixed.mandatory_references)),
            ),
            ctx_planning_inputs.ContextPlanningErrorCode.INVALID_VALUE,
        )
        for field_name in ("mandatory_reference_count",
            "mandatory_reference_utf8_bytes",):
            with self.subTest(field=field_name):
                self.assert_planning_error(
                    lambda field_name=field_name: replace(mixed,
                        accounting=replace(mixed.accounting,
                            **{field_name: getattr(mixed.accounting, field_name) + 1},),
                    ), ctx_planning_inputs.ContextPlanningErrorCode.INVALID_VALUE,
                )

        p2, p2_material = planning_fixtures.source("p2", b"aa")
        p3, p3_material = planning_fixtures.source("p3",
            b"z",
            source_kind=ContextSourceKind.OWN_AGENT_STATE,
        )
        budget_projection = planning_fixtures.project((p3, p2),
            (p3_material, p2_material),)
        budgets = budget_projection.accounting.budgets
        swapped = ctx_projection_accounting.ContextBudgetAccounting(budgets.total,
            budgets.p0, budgets.p1,
            ctx_projection_accounting.ContextSourceBudgetUsage(budgets.p2.configured_utf8_bytes,
                1, budgets.p2.configured_utf8_bytes - 1,
            ), ctx_projection_accounting.ContextSourceBudgetUsage(
                budgets.p3.configured_utf8_bytes, 2,
                budgets.p3.configured_utf8_bytes - 2,),
            budgets.mandatory_references,)
        bad_budget_accounting = replace(budget_projection.accounting,
            budgets=swapped,)
        self.assert_planning_error(
            lambda: replace(budget_projection,
                accounting=bad_budget_accounting,),
            ctx_planning_inputs.ContextPlanningErrorCode.INVALID_VALUE,
        )
