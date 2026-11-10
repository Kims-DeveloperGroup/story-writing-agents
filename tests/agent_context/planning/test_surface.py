from __future__ import annotations
from dataclasses import FrozenInstanceError, fields
from story_writing_agents.agent_context import planning as planning_module
from story_writing_agents.agent_context.contracts import checkpoints as ctx_checkpoints
from story_writing_agents.agent_context.contracts import planning_inputs as ctx_planning_inputs
from story_writing_agents.agent_context.contracts import projection as ctx_projection
from story_writing_agents.agent_context.contracts import projection_accounting as ctx_projection_accounting
from tests.agent_context.planning import fixtures as planning_fixtures


class PlanningSurfaceTests(planning_fixtures.PlanningTestCase):
    """Verify the direct-module surface and stable planning vocabularies."""

    def test_exact_surface_enums_and_absent_total_budget_category(self) -> None:
        owner_exports = (
            ('CONTEXT_CHECKPOINT_SCHEMA_VERSION', ctx_planning_inputs),
            ('DEFAULT_CONTEXT_PLANNING_POLICY', ctx_planning_inputs),
            ('MAX_MANDATORY_REFERENCE_RESERVATION_UTF8_BYTES', ctx_planning_inputs),
            ('ContextBootstrapReason', ctx_planning_inputs),
            ('ContextBudgetAccounting', ctx_projection_accounting),
            ('ContextCheckpointProposal', ctx_projection),
            ('ContextCheckpointSource', ctx_checkpoints),
            ('ContextCheckpointState', ctx_planning_inputs),
            ('ContextCheckpointView', ctx_checkpoints),
            ('ContextDecisionAccounting', ctx_projection_accounting),
            ('ContextDecisionKind', ctx_planning_inputs),
            ('ContextDeliveryMode', ctx_planning_inputs),
            ('ContextMaterial', ctx_planning_inputs),
            ('ContextPlanningConditions', ctx_planning_inputs),
            ('ContextPlanningError', ctx_planning_inputs),
            ('ContextPlanningErrorCode', ctx_planning_inputs),
            ('ContextPlanningPolicy', ctx_planning_inputs),
            ('ContextProjectedUnit', ctx_projection),
            ('ContextProjection', ctx_projection),
            ('ContextProjectionAccounting', ctx_projection_accounting),
            ('ContextReferenceBudgetUsage', ctx_projection_accounting),
            ('ContextSearchableAccounting', ctx_projection_accounting),
            ('ContextSourceBudgetUsage', ctx_projection_accounting),
            ('ContextSourceDecision', ctx_projection_accounting),
            ('MandatoryReferenceReservation', ctx_planning_inputs),
            ('plan_context_projection', planning_module),
        )
        self.assertEqual(
            tuple(name for name, owner in owner_exports if name in owner.__all__ and hasattr(owner, name)),
            tuple(name for name, _owner in owner_exports),
        )
        self.assertEqual(tuple(planning_module.__all__), ('plan_context_projection',))
        self.assertEqual(ctx_planning_inputs.CONTEXT_CHECKPOINT_SCHEMA_VERSION, 1)
        self.assertEqual( ctx_planning_inputs.MAX_MANDATORY_REFERENCE_RESERVATION_UTF8_BYTES, 262_144, )
        self.assertEqual( tuple(item.value for item in ctx_planning_inputs.ContextDeliveryMode), ("bootstrap", "delta"), )
        self.assertEqual(tuple(item.value for item in ctx_planning_inputs.ContextCheckpointState),
            ("ready", "missing", "corrupt", "stale", "tainted"),)
        self.assertEqual(tuple(item.value for item in ctx_planning_inputs.ContextDecisionKind),
            ("selected",
                "deduplicated", "checkpoint_suppressed",
                "omitted_class_budget", "omitted_material",
            ),
        )
        self.assertFalse(hasattr(ctx_planning_inputs.ContextDecisionKind, "OMITTED_TOTAL_BUDGET"))
        self.assertFalse( any("total_budget" in item.value for item in ctx_planning_inputs.ContextDecisionKind) )
        self.assertEqual(tuple(item.value for item in ctx_planning_inputs.ContextBootstrapReason),
            ("explicit_fresh",
                "checkpoint_missing", "checkpoint_corrupt",
                "checkpoint_stale", "checkpoint_tainted",
                "scope_mismatch", "provider_mismatch",
                "provider_context_unavailable", "provider_thread_mismatch",
                "definition_fingerprint_mismatch", "stable_context_changed",
                "policy_fingerprint_mismatch", "schema_mismatch",
                "max_delta_depth",
            ),
        )

    def test_fixed_error_codes_messages_and_privacy(self) -> None:
        expected = {ctx_planning_inputs.ContextPlanningErrorCode.INVALID_VALUE: (
                "context planning contains an invalid value"),
            ctx_planning_inputs.ContextPlanningErrorCode.INVALID_POLICY: (
                "context planning policy is invalid"),
            ctx_planning_inputs.ContextPlanningErrorCode.INVALID_CHECKPOINT: (
                "context checkpoint is invalid"),
            ctx_planning_inputs.ContextPlanningErrorCode.INVALID_MATERIAL: "context material is invalid",
            ctx_planning_inputs.ContextPlanningErrorCode.MATERIAL_LENGTH_MISMATCH: (
                "context material length does not match its manifest entry"),
            ctx_planning_inputs.ContextPlanningErrorCode.MATERIAL_DIGEST_MISMATCH: (
                "context material digest does not match its manifest entry"),
            ctx_planning_inputs.ContextPlanningErrorCode.REVISION_CONTENT_MISMATCH: (
                "a source revision conflicts with checkpoint content identity"),
            ctx_planning_inputs.ContextPlanningErrorCode.MANIFEST_SCOPE_MISMATCH: (
                "context manifest and planning scope do not match"),
            ctx_planning_inputs.ContextPlanningErrorCode.REQUIRED_MATERIAL_MISSING: (
                "required context material is unavailable"),
            ctx_planning_inputs.ContextPlanningErrorCode.STABLE_MATERIAL_MISSING: (
                "stable context material is unavailable"),
            ctx_planning_inputs.ContextPlanningErrorCode.WORKING_CONSTRAINT_MATERIAL_MISSING: (
                "working hard-constraint material is unavailable"),
            ctx_planning_inputs.ContextPlanningErrorCode.REQUIRED_BUDGET_EXCEEDED: (
                "required context exceeds its planning budget"),
            ctx_planning_inputs.ContextPlanningErrorCode.STABLE_BUDGET_EXCEEDED: (
                "stable context exceeds its planning budget"),
            ctx_planning_inputs.ContextPlanningErrorCode.WORKING_CONSTRAINT_BUDGET_EXCEEDED: (
                "working hard constraints exceed the configured budget"),
            ctx_planning_inputs.ContextPlanningErrorCode.REFERENCE_BUDGET_EXCEEDED: (
                "mandatory references exceed their reservation budget"),
        }
        self.assertEqual(set(expected), set(ctx_planning_inputs.ContextPlanningErrorCode))
        for code, message in expected.items():
            with self.subTest(code=code):
                error = ctx_planning_inputs.ContextPlanningError(code)
                self.assertEqual(str(error), message)
                self.assertNotIn("PRIVATE", str(error))
                self.assertNotIn("PRIVATE", repr(error))
                self.assertIsNone(error.__cause__)
                self.assertIsNone(error.__context__)

    def test_exact_dataclass_fields_and_immutability(self) -> None:
        self.assertEqual(tuple(field.name for field in fields(ctx_projection_accounting.ContextBudgetAccounting)),
            ("total", "p0", "p1", "p2", "p3", "mandatory_references"),)
        self.assertEqual(tuple(field.name for field in fields(ctx_projection.ContextProjection)),
            ("mode",
                "bootstrap_reason", "units",
                "mandatory_references", "decisions",
                "removed_checkpoint_sources", "accounting",
                "checkpoint_proposal", "projected_records",
                "removed_checkpoint_records", "catalog_updates",
                "removed_catalog_sources",
            ),
        )
        value = ctx_projection_accounting.ContextSourceBudgetUsage(3, 1, 2)
        self.assertFalse(hasattr(value, "__dict__"))
        with self.assertRaises((FrozenInstanceError, AttributeError)):
            value.selected_unique_utf8_bytes = 2  # type: ignore[misc]
