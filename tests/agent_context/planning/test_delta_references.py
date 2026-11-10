from __future__ import annotations
from story_writing_agents.agent_context.manifest import ContextAuthorityKind, ContextProvenance, ContextSourceKind
from story_writing_agents.agent_context.contracts import planning_inputs as ctx_planning_inputs
from tests.agent_context.planning import fixtures as planning_fixtures


class DeltaAndReferencePlanningTests(planning_fixtures.PlanningTestCase):
    """Verify delta metadata, removed sources, and separate references."""

    def test_unchanged_sources_suppress_while_p2_p3_changes_select(self) -> None:
        policy = planning_fixtures.make_policy()
        conditions = planning_fixtures.make_conditions(policy)
        stable, stable_material = planning_fixtures.source(
            "definition",
            b"role",
            source_kind=ContextSourceKind.AGENT_DEFINITION,
        )
        unchanged, unchanged_material = planning_fixtures.source("unchanged", b"old")
        changed, changed_material = planning_fixtures.source("changed",
            b"new", revision="revision-2",
        )
        previous_changed = planning_fixtures.make_entry("changed", b"old", revision="revision-1")
        optional, optional_material = planning_fixtures.source(
            "optional",
            b"z",
            source_kind=ContextSourceKind.OWN_AGENT_STATE,
        )
        projection = planning_fixtures.project((optional, changed, stable, unchanged),
            (optional_material, changed_material, stable_material, unchanged_material),
            policy=policy,
            conditions=conditions,
            checkpoint=planning_fixtures.ready_checkpoint(
                conditions, (stable, unchanged, previous_changed),
                delta_depth=3,),
            active_provider_thread_id="thread",
        )
        decisions = planning_fixtures.decision_map(projection)
        self.assertIs(projection.mode, ctx_planning_inputs.ContextDeliveryMode.DELTA)
        self.assertIs(
            decisions[stable.source_id].decision,
            ctx_planning_inputs.ContextDecisionKind.CHECKPOINT_SUPPRESSED,
        )
        self.assertIs(decisions[unchanged.source_id].decision,
            ctx_planning_inputs.ContextDecisionKind.CHECKPOINT_SUPPRESSED,
        )
        self.assertIs(decisions[changed.source_id].decision, ctx_planning_inputs.ContextDecisionKind.SELECTED
        )
        self.assertIs(decisions[optional.source_id].decision, ctx_planning_inputs.ContextDecisionKind.SELECTED
        )
        self.assertEqual(projection.checkpoint_proposal.delta_depth, 4)
        self.assertEqual(
            tuple(source.entry for source in projection.removed_checkpoint_sources), (),
        )
        self.assertEqual(tuple(source.entry for source in projection.catalog_updates),
            (changed, optional),)

    def test_catalog_alias_and_record_epochs_cover_add_change_remove_reappear(self,
    ) -> None:
        policy = planning_fixtures.make_policy(p3_source_utf8_bytes=0)
        conditions = planning_fixtures.make_conditions(policy)

        first_record = planning_fixtures.record("record-one", b"one", 1)
        first_entry = planning_fixtures.make_entry("artifact", b"[one]", revision="revision-1")
        first_material = ctx_planning_inputs.ContextMaterial(first_entry,
            b"[one]", (first_record,),
        )
        first = planning_fixtures.project((first_entry,), (first_material,), policy=policy, conditions=conditions)
        self.assertIs(first.mode, ctx_planning_inputs.ContextDeliveryMode.BOOTSTRAP)
        self.assertEqual(
            tuple(
                (
                    item.source_alias, item.record_alias_high_watermark,
                ) for item in first.checkpoint_proposal.catalog_sources
            ), (("s1", 1),),
        )
        self.assertEqual(
            tuple((item.record.cursor.record_id, item.record.record_alias)
                for item in first.projected_records),
            (("record-one", 1),),)
        self.assertEqual(first.catalog_updates, first.checkpoint_proposal.catalog_sources
        )

        changed_record = planning_fixtures.record("record-one", b"one-v2", 1)
        new_record = planning_fixtures.record("record-two", b"two", 2)
        changed_entry = planning_fixtures.make_entry("artifact",
            b"[one-v2,two]", revision="revision-2",
        )
        changed_material = ctx_planning_inputs.ContextMaterial(changed_entry,
            b"[one-v2,two]", (changed_record, new_record),
        )
        changed = planning_fixtures.project((changed_entry,),
            (changed_material,), policy=policy,
            conditions=conditions, checkpoint=planning_fixtures.checkpoint_from_projection(first),
            active_provider_thread_id="thread",
        )
        changed_source = changed.checkpoint_proposal.sources[0]
        self.assertEqual(
            tuple((item.cursor.record_id, item.record_alias)
                for item in changed_source.records),
            (("record-one", 1), ("record-two", 2)),)
        self.assertEqual(
            tuple(
                (
                    item.source_alias, item.record.record_alias,
                    item.record.cursor.record_id,)
                for item in changed.projected_records),
            (("s1", 1, "record-one"), ("s1", 2, "record-two")),)
        self.assertEqual(changed.checkpoint_proposal.catalog_sources[0].record_alias_high_watermark,
            2,
        )

        retained_entry = planning_fixtures.make_entry("artifact",
            b"[two]", revision="revision-3",
        )
        retained_material = ctx_planning_inputs.ContextMaterial(retained_entry,
            b"[two]", (new_record,),
        )
        retained = planning_fixtures.project((retained_entry,),
            (retained_material,), policy=policy,
            conditions=conditions, checkpoint=planning_fixtures.checkpoint_from_projection(changed),
            active_provider_thread_id="thread",
        )
        self.assertEqual(retained.projected_records, ())
        self.assertEqual(
            tuple((item.source_alias, item.record_alias, item.record_id)
                for item in retained.removed_checkpoint_records),
            (("s1", 1, "record-one"),),)
        self.assertEqual(
            retained.checkpoint_proposal.catalog_sources[0].record_alias_high_watermark, 2,
        )

        removed = planning_fixtures.project(
            (),
            (),
            policy=policy,
            conditions=conditions, checkpoint=planning_fixtures.checkpoint_from_projection(retained),
            active_provider_thread_id="thread",
        )
        self.assertEqual(removed.checkpoint_proposal.catalog_sources, ())
        self.assertEqual(tuple(item.source_alias for item in removed.removed_catalog_sources),
            ("s1",),
        )
        self.assertEqual(removed.checkpoint_proposal.source_alias_high_watermark,
            1,
        )

        reappeared_record = planning_fixtures.record("record-three", b"three", 1)
        reappeared_entry = planning_fixtures.make_entry("artifact",
            b"[three]", revision="revision-4",
        )
        reappeared_material = ctx_planning_inputs.ContextMaterial(reappeared_entry,
            b"[three]", (reappeared_record,),
        )
        reappeared = planning_fixtures.project((reappeared_entry,),
            (reappeared_material,), policy=policy,
            conditions=conditions, checkpoint=planning_fixtures.checkpoint_from_projection(removed),
            active_provider_thread_id="thread",
        )
        self.assertEqual(
            tuple((item.source_alias, item.record_alias_high_watermark)
                for item in reappeared.checkpoint_proposal.catalog_sources),
            (("s2", 1),),)
        self.assertEqual(reappeared.checkpoint_proposal.source_alias_high_watermark,
            2,
        )
        self.assertEqual(tuple(item.source_alias for item in reappeared.catalog_updates),
            ("s2",),)

    def test_accepted_source_omission_retains_epoch_and_emits_delivery_removal(self,
    ) -> None:
        policy = planning_fixtures.make_policy(p2_source_utf8_bytes=4, p3_source_utf8_bytes=0)
        conditions = planning_fixtures.make_conditions(policy)
        accepted_entry = planning_fixtures.make_entry("bounded", b"four", revision="revision-1")
        accepted = planning_fixtures.project((accepted_entry,),
            (ctx_planning_inputs.ContextMaterial(accepted_entry, b"four"),), policy=policy,
            conditions=conditions,)
        omitted_entry = planning_fixtures.make_entry("bounded", b"fives", revision="revision-2")
        omitted = planning_fixtures.project((omitted_entry,),
            (ctx_planning_inputs.ContextMaterial(omitted_entry, b"fives"),), policy=policy,
            conditions=conditions, checkpoint=planning_fixtures.checkpoint_from_projection(accepted),
            active_provider_thread_id="thread",
        )
        decision = planning_fixtures.decision_map(omitted)["bounded"]
        self.assertIs(decision.decision,
            ctx_planning_inputs.ContextDecisionKind.OMITTED_CLASS_BUDGET,
        )
        self.assertEqual(omitted.checkpoint_proposal.sources, ())
        self.assertEqual(tuple(item.entry.source_id for item in omitted.removed_checkpoint_sources),
            ("bounded",),)
        self.assertEqual(omitted.removed_catalog_sources, ())
        self.assertEqual(
            tuple(item.source_alias
                for item in omitted.checkpoint_proposal.catalog_sources),
            ("s1",),
        )
        self.assertEqual(omitted.checkpoint_proposal.source_alias_high_watermark,
            1,
        )

    def test_same_scope_revision_content_conflict_fails_even_when_thread_changes(self,
    ) -> None:
        policy = planning_fixtures.make_policy()
        conditions = planning_fixtures.make_conditions(policy)
        previous = planning_fixtures.make_entry("same", b"old", revision="shared-revision")
        variants = (planning_fixtures.make_entry("same", b"new", revision="shared-revision"),
            planning_fixtures.make_entry("same", b"longer", revision="shared-revision"),)
        for current in variants:
            with self.subTest(bytes=current.utf8_bytes):
                material = ctx_planning_inputs.ContextMaterial(current,
                    b"new" if current.utf8_bytes == 3 else b"longer",)
                error = self.assert_planning_error(
                    lambda current=current, material=material: planning_fixtures.project((current,),
                        (material,), policy=policy,
                        conditions=conditions, checkpoint=planning_fixtures.ready_checkpoint(
                            conditions, (previous,),
                            provider_thread_id="old-thread",),
                        active_provider_thread_id="new-thread",),
                    ctx_planning_inputs.ContextPlanningErrorCode.REVISION_CONTENT_MISMATCH,)
                self.assertNotIn("old", str(error))
                self.assertNotIn("new", str(error))

    def test_reference_whole_range_multiple_order_and_separate_accounting(self) -> None:
        references = (
            planning_fixtures.reference(1, source_id="outline", locator="core_documents/outline.md", utf8_bytes=3),
            planning_fixtures.reference(2,
                source_id="chapter", locator="manuscript/part_01/chapter.md#L4-L8",
                utf8_bytes=4,),
            planning_fixtures.reference(3,
                source_id="chapter", locator="manuscript/part_01/chapter.md#L12-L16",
                utf8_bytes=2,
            ),
        )
        policy = ctx_planning_inputs.ContextPlanningPolicy(2, 1, 1, 0, 0, 9, 16)
        projection = planning_fixtures.project((), (), policy=policy, mandatory_references=references)
        self.assertEqual(projection.mandatory_references, references)
        self.assertEqual(projection.units, ())
        self.assertEqual(projection.decisions, ())
        self.assertEqual(projection.checkpoint_proposal.sources, ())
        self.assertEqual(projection.accounting.mandatory_reference_count, 3)
        self.assertEqual(projection.accounting.mandatory_reference_utf8_bytes, 9)
        self.assertEqual(
            projection.accounting.budgets.total.selected_unique_utf8_bytes, 0
        )
        self.assertEqual(projection.accounting.budgets.mandatory_references.used_utf8_bytes, 9
        )

    def test_reference_constructor_sequence_identity_and_budget_failures(self) -> None:
        self.assert_planning_error(lambda: planning_fixtures.reference(0),
            ctx_planning_inputs.ContextPlanningErrorCode.INVALID_VALUE,
        )
        wrong_authority = ContextProvenance(ContextAuthorityKind.WRITING_ARTIFACT_REPOSITORY,
            "core_documents/outline.md",)
        self.assert_planning_error(lambda: ctx_planning_inputs.MandatoryReferenceReservation(
                1, "reference",
                "source", "revision",
                wrong_authority, planning_fixtures.digest(b"x"),
                1,
            ),
            ctx_planning_inputs.ContextPlanningErrorCode.INVALID_VALUE,
        )
        policy = ctx_planning_inputs.ContextPlanningPolicy(2, 1, 1, 0, 0, 4, 16)
        invalid_sequences = ((planning_fixtures.reference(2),),
            (planning_fixtures.reference(1), planning_fixtures.reference(3)), (
                planning_fixtures.reference(1, reference_id="duplicate"), planning_fixtures.reference(2, reference_id="duplicate"),
            ),
        )
        for references in invalid_sequences:
            with self.subTest(references=references):
                self.assert_planning_error(
                    lambda references=references: planning_fixtures.project((), (), policy=policy, mandatory_references=references),
                    ctx_planning_inputs.ContextPlanningErrorCode.INVALID_VALUE,
                )
        self.assert_planning_error(
            lambda: planning_fixtures.project((),
                (), policy=policy,
                mandatory_references=(planning_fixtures.reference(1, utf8_bytes=3),
                    planning_fixtures.reference(2, utf8_bytes=2),
                ),
            ),
            ctx_planning_inputs.ContextPlanningErrorCode.REFERENCE_BUDGET_EXCEEDED,)
