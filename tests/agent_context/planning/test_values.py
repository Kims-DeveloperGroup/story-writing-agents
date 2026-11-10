from __future__ import annotations
import itertools
from dataclasses import FrozenInstanceError, replace
from story_writing_agents.agent_context.manifest import ContextPriority, ContextSemanticChannel, ContextSourceKind
from story_writing_agents.agent_context.contracts import checkpoints as ctx_checkpoints
from story_writing_agents.agent_context.contracts import planning_inputs as ctx_planning_inputs
from story_writing_agents.agent_context.contracts import projection as ctx_projection
from story_writing_agents.agent_context.contracts import projection_accounting as ctx_projection_accounting
from tests.agent_context.planning import fixtures as planning_fixtures


class PlanningValueTests(planning_fixtures.PlanningTestCase):
    """Verify material, checkpoint, decision, and accounting value invariants."""

    def test_material_is_strict_utf8_exact_and_hidden_from_repr(self) -> None:
        body = "문맥🙂".encode("utf-8")
        entry = planning_fixtures.entry("unicode", body)
        material = ctx_planning_inputs.ContextMaterial(entry, body)
        self.assertEqual(material.body_utf8, body)
        self.assertNotIn("문맥", repr(material))
        with self.assertRaises((FrozenInstanceError, AttributeError)):
            material.body_utf8 = b"changed"  # type: ignore[misc]

        invalid_body = b"PRIVATE\xffBODY"
        invalid_entry = planning_fixtures.entry("invalid", invalid_body)
        error = self.assert_planning_error(lambda: ctx_planning_inputs.ContextMaterial(invalid_entry, invalid_body),
            ctx_planning_inputs.ContextPlanningErrorCode.INVALID_MATERIAL,)
        self.assertNotIn("PRIVATE", str(error))
        self.assertNotIn("PRIVATE", repr(error))

        valid = planning_fixtures.entry("valid", b"abc")
        self.assert_planning_error(lambda: ctx_planning_inputs.ContextMaterial(replace(valid, utf8_bytes=4), b"abc"),
            ctx_planning_inputs.ContextPlanningErrorCode.MATERIAL_LENGTH_MISMATCH,)
        self.assert_planning_error(
            lambda: ctx_planning_inputs.ContextMaterial( replace(valid, content_digest=planning_fixtures.digest(b"xyz")), b"abc", ),
            ctx_planning_inputs.ContextPlanningErrorCode.MATERIAL_DIGEST_MISMATCH,)
        for wrong in ("abc", bytearray(b"abc"), memoryview(b"abc"), None):
            with self.subTest(wrong_type=type(wrong).__name__):
                self.assert_planning_error(
                    lambda wrong=wrong: ctx_planning_inputs.ContextMaterial(valid, wrong),  # type: ignore[arg-type]
                    ctx_planning_inputs.ContextPlanningErrorCode.INVALID_MATERIAL,)

    def test_projected_unit_validates_exact_body_channel_and_priority(self) -> None:
        first = planning_fixtures.entry("first", b"same")
        second = planning_fixtures.entry("second", b"same", ordinal=1)
        unit = ctx_projection.ContextProjectedUnit((second, first), b"same")
        self.assertEqual( tuple(entry.source_id for entry in unit.entries), ("first", "second") )
        self.assertNotIn("same", repr(unit))
        different_channel = planning_fixtures.entry("history", b"same", semantic_channel=ContextSemanticChannel.HISTORY)
        self.assert_planning_error(
            lambda: ctx_projection.ContextProjectedUnit((first, different_channel), b"same"),
            ctx_planning_inputs.ContextPlanningErrorCode.INVALID_VALUE,
        )
        p0_first = planning_fixtures.entry("p0-first", b"same", source_kind=ContextSourceKind.RUNTIME_INPUT)
        p0_second = planning_fixtures.entry("p0-second",
            b"same", source_kind=ContextSourceKind.RUNTIME_INPUT,
            semantic_channel=ContextSemanticChannel.CANDIDATE,
        )
        self.assert_planning_error(lambda: ctx_projection.ContextProjectedUnit((p0_first, p0_second), b"same"),
            ctx_planning_inputs.ContextPlanningErrorCode.INVALID_VALUE,
        )

    def test_checkpoint_shapes_and_source_constraints_are_strict(self) -> None:
        policy = planning_fixtures.policy()
        conditions = planning_fixtures.conditions(policy)
        p1, _ = planning_fixtures.source( "definition", source_kind=ContextSourceKind.AGENT_DEFINITION, )
        p0, _ = planning_fixtures.source( "input", source_kind=ContextSourceKind.RUNTIME_INPUT, )
        for state in (ctx_planning_inputs.ContextCheckpointState.MISSING,
            ctx_planning_inputs.ContextCheckpointState.CORRUPT, ctx_planning_inputs.ContextCheckpointState.STALE,
            ctx_planning_inputs.ContextCheckpointState.TAINTED,):
            self.assertEqual(ctx_checkpoints.ContextCheckpointView(state).sources, ())
            for kwargs in ({"conditions": conditions},
                {"provider_thread_id": "thread"}, {"sources": (ctx_checkpoints.ContextCheckpointSource(p1),)},
                {"delta_depth": 0},):
                with self.subTest(state=state, kwargs=kwargs):
                    self.assert_planning_error(
                        lambda state=state, kwargs=kwargs: ctx_checkpoints.ContextCheckpointView( state, **kwargs, ),
                        ctx_planning_inputs.ContextPlanningErrorCode.INVALID_CHECKPOINT,)
        self.assert_planning_error( lambda: ctx_checkpoints.ContextCheckpointSource(p0), ctx_planning_inputs.ContextPlanningErrorCode.INVALID_CHECKPOINT, )
        malformed_ready = (lambda: ctx_checkpoints.ContextCheckpointView(ctx_planning_inputs.ContextCheckpointState.READY),
            lambda: ctx_checkpoints.ContextCheckpointView(
                        ctx_planning_inputs.ContextCheckpointState.READY, conditions, "thread", [], 0),  # type: ignore[arg-type]
            lambda: ctx_checkpoints.ContextCheckpointView(ctx_planning_inputs.ContextCheckpointState.READY, conditions, "thread", (), True),
            lambda: ctx_checkpoints.ContextCheckpointView(ctx_planning_inputs.ContextCheckpointState.READY, conditions, "thread", (), -1),
            lambda: ctx_checkpoints.ContextCheckpointView(ctx_planning_inputs.ContextCheckpointState.READY, conditions, "unsafe\nthread", (), 0),
        )
        for case in malformed_ready:
            self.assert_planning_error( case, ctx_planning_inputs.ContextPlanningErrorCode.INVALID_CHECKPOINT, )
        self.assert_planning_error(
            lambda: ctx_checkpoints.ContextCheckpointView(
                ctx_planning_inputs.ContextCheckpointState.READY,
                conditions,
                "thread", (ctx_checkpoints.ContextCheckpointSource(p1), ctx_checkpoints.ContextCheckpointSource(p1)),
                0,
            ),
            ctx_planning_inputs.ContextPlanningErrorCode.INVALID_CHECKPOINT,
        )
        no_thread = planning_fixtures.ready_checkpoint(conditions, (p1,), provider_thread_id=None)
        self.assertIsNone(no_thread.provider_thread_id)

    def test_catalog_aliases_are_unique_exact_integers_and_high_water_bounded(self,
    ) -> None:
        policy = planning_fixtures.policy()
        conditions = planning_fixtures.conditions(policy)
        entry, _material = planning_fixtures.source("catalog-source")
        cursor = planning_fixtures.record("record-one", b"record", 1).cursor

        for alias in (True,
            0, -1,
            ctx_planning_inputs.MAX_RECORD_ALIAS + 1,
        ):
            with self.subTest(record_alias=alias):
                self.assert_planning_error(
                    lambda alias=alias: ctx_checkpoints.ContextCheckpointRecord(cursor, alias),
                    ctx_planning_inputs.ContextPlanningErrorCode.INVALID_CHECKPOINT,
                )
        for high_water in (True,
            -1,
            ctx_planning_inputs.MAX_RECORD_ALIAS + 1,
        ):
            with self.subTest(record_high_water=high_water):
                self.assert_planning_error(
                    lambda high_water=high_water: ctx_checkpoints.ContextCatalogCursor(entry, "s1", high_water),
                    ctx_planning_inputs.ContextPlanningErrorCode.INVALID_CHECKPOINT,
                )
        for source_alias in ("",
            "s0", "s01",
            "h1",):
            with self.subTest(source_alias=source_alias):
                self.assert_planning_error(
                    lambda source_alias=source_alias: ctx_checkpoints.ContextCatalogCursor( entry, source_alias, ),
                    ctx_planning_inputs.ContextPlanningErrorCode.INVALID_CHECKPOINT,
                )

        checkpoint_record = ctx_checkpoints.ContextCheckpointRecord(cursor, 2)
        source = ctx_checkpoints.ContextCheckpointSource(entry, (checkpoint_record,))
        too_low = ctx_checkpoints.ContextCatalogCursor(entry, "s1", 1)
        self.assert_planning_error(
            lambda: ctx_checkpoints.ContextCheckpointView(
                ctx_planning_inputs.ContextCheckpointState.READY,
                conditions,
                "thread",
                (source,),
                0, (too_low,),
                1,
            ),
            ctx_planning_inputs.ContextPlanningErrorCode.INVALID_CHECKPOINT,
        )
        valid_catalog = ctx_checkpoints.ContextCatalogCursor(entry, "s1", 2)
        for high_water in (True, 0):
            with self.subTest(source_high_water=high_water):
                self.assert_planning_error(lambda high_water=high_water: ctx_checkpoints.ContextCheckpointView(
                        ctx_planning_inputs.ContextCheckpointState.READY, conditions,
                        "thread", (source,),
                        0, (valid_catalog,),
                        high_water,),
                    ctx_planning_inputs.ContextPlanningErrorCode.INVALID_CHECKPOINT,
                )

        sibling = replace(entry, source_id="catalog-sibling", ordinal=1)
        duplicate_alias = ctx_checkpoints.ContextCatalogCursor(sibling, "s1")
        self.assert_planning_error(
            lambda: ctx_checkpoints.ContextCheckpointView(
                ctx_planning_inputs.ContextCheckpointState.READY,
                conditions,
                "thread",
                (source,),
                0, (valid_catalog, duplicate_alias),
                1,
            ),
            ctx_planning_inputs.ContextPlanningErrorCode.INVALID_CHECKPOINT,
        )
        duplicate_record_alias = ctx_checkpoints.ContextCheckpointRecord( replace(cursor, record_id="record-two"), 2, )
        self.assert_planning_error(
            lambda: ctx_checkpoints.ContextCheckpointSource( entry, (checkpoint_record, duplicate_record_alias), ),
            ctx_planning_inputs.ContextPlanningErrorCode.INVALID_CHECKPOINT,
        )

    def test_decision_priority_channel_matrix_is_exhaustive(self) -> None:
        valid_entries = (planning_fixtures.entry("current", b"x", source_kind=ContextSourceKind.RUNTIME_INPUT),
            planning_fixtures.entry("candidate",
                b"x", source_kind=ContextSourceKind.RUNTIME_INPUT,
                semantic_channel=ContextSemanticChannel.CANDIDATE,),
            planning_fixtures.entry("stable", b"x", source_kind=ContextSourceKind.AGENT_DEFINITION), *(
                planning_fixtures.entry(f"p2-{channel.value}", b"x", semantic_channel=channel) for channel in (
                    ContextSemanticChannel.HARD_CONSTRAINT, ContextSemanticChannel.FOCUSED_EVIDENCE,
                    ContextSemanticChannel.RETRIEVED_EVIDENCE,
                    ContextSemanticChannel.HISTORY,)
            ),
            planning_fixtures.entry("optional", b"x", source_kind=ContextSourceKind.OWN_AGENT_STATE),)
        for entry, decision in itertools.product(valid_entries, ctx_planning_inputs.ContextDecisionKind):
            represented = ("representative"
                if decision is ctx_planning_inputs.ContextDecisionKind.DEDUPLICATED
                else None)
            with self.subTest(entry=entry.source_id, decision=decision):
                if planning_fixtures.decision_allowed(entry.priority, entry.semantic_channel, decision):
                    value = ctx_projection_accounting.ContextSourceDecision(entry, decision, represented)
                    self.assertIs(value.searchable, entry.searchable)
                else:
                    self.assert_planning_error(lambda entry=entry,
                        decision=decision, represented=represented: (
                            ctx_projection_accounting.ContextSourceDecision(entry, decision, represented)),
                        ctx_planning_inputs.ContextPlanningErrorCode.INVALID_VALUE,
                    )
        for priority, channel, decision in itertools.product(ContextPriority,
            ContextSemanticChannel, ctx_planning_inputs.ContextDecisionKind,
        ):
            with self.subTest(priority=priority, channel=channel, decision=decision):
                if planning_fixtures.decision_allowed(priority, channel, decision):
                    ctx_projection_accounting.ContextDecisionAccounting(priority, channel, decision, 1, 1)
                else:
                    self.assert_planning_error(
                        lambda priority=priority, channel=channel, decision=decision: (
                            ctx_projection_accounting.ContextDecisionAccounting(priority, channel, decision, 1, 1)),
                        ctx_planning_inputs.ContextPlanningErrorCode.INVALID_VALUE,
                    )
        p2 = valid_entries[3]
        self.assert_planning_error(
            lambda: ctx_projection_accounting.ContextSourceDecision( p2, ctx_planning_inputs.ContextDecisionKind.DEDUPLICATED, ),
            ctx_planning_inputs.ContextPlanningErrorCode.INVALID_VALUE,
        )
        self.assert_planning_error(
            lambda: ctx_projection_accounting.ContextSourceDecision(p2, ctx_planning_inputs.ContextDecisionKind.DEDUPLICATED, p2.source_id),
            ctx_planning_inputs.ContextPlanningErrorCode.INVALID_VALUE,
        )

    def test_budget_and_searchable_accounting_are_self_validating(self) -> None:
        ctx_projection_accounting.ContextSourceBudgetUsage(4, 1, 3)
        for args in ((4, 5, 0), (4, 1, 2), (-1, 0, 0), (4, True, 3)):
            with self.subTest(args=args):
                self.assert_planning_error(lambda args=args: ctx_projection_accounting.ContextSourceBudgetUsage(*args),
                    ctx_planning_inputs.ContextPlanningErrorCode.INVALID_VALUE,
                )
        valid_reference = ctx_projection_accounting.ContextReferenceBudgetUsage(4, 1, 3)
        self.assertEqual(valid_reference.remaining_utf8_bytes, 3)
        zero = ctx_projection_accounting.ContextSourceBudgetUsage(0, 0, 0)
        one = ctx_projection_accounting.ContextSourceBudgetUsage(1, 0, 1)
        total = ctx_projection_accounting.ContextSourceBudgetUsage(2, 0, 2)
        ctx_projection_accounting.ContextBudgetAccounting(total, one, one, zero, zero, ctx_projection_accounting.ContextReferenceBudgetUsage(0, 0, 0))
        for values in ((zero, one, one, zero, zero, ctx_projection_accounting.ContextReferenceBudgetUsage(0, 0, 0)),
            (total, zero, one, zero, zero, ctx_projection_accounting.ContextReferenceBudgetUsage(0, 0, 0)),
            (total, one, zero, zero, zero, ctx_projection_accounting.ContextReferenceBudgetUsage(0, 0, 0)), (
                total, one,
                one, zero,
                zero,
                ctx_projection_accounting.ContextReferenceBudgetUsage(262_145, 0, 262_145),),
        ):
            with self.subTest(values=values):
                self.assert_planning_error(lambda values=values: ctx_projection_accounting.ContextBudgetAccounting(*values),
                    ctx_planning_inputs.ContextPlanningErrorCode.INVALID_VALUE,
                )
        searchable = ctx_projection_accounting.ContextSearchableAccounting(5, 15, 1, 1, 1, 2, 1, 3, 1, 4, 1, 5, 2, 3, 3, 12)
        for field_name in ("total_source_count",
            "total_source_utf8_bytes", "selected_source_count",
            "selected_source_utf8_bytes", "unprojected_source_count",
            "unprojected_source_utf8_bytes",):
            with self.subTest(field=field_name):
                self.assert_planning_error(
                    lambda field_name=field_name: replace(searchable,
                        **{field_name: getattr(searchable, field_name) + 1},),
                    ctx_planning_inputs.ContextPlanningErrorCode.INVALID_VALUE,
                )
