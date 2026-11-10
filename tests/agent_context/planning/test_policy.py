from __future__ import annotations
from dataclasses import fields
from story_writing_agents.agent_context.contracts import planning_inputs as ctx_planning_inputs
from tests.agent_context.planning import fixtures as planning_fixtures


class PlanningPolicyTests(planning_fixtures.PlanningTestCase):
    """Verify finite default and override policy boundaries."""

    def test_default_values_and_fingerprint_are_exact(self) -> None:
        policy = ctx_planning_inputs.DEFAULT_CONTEXT_PLANNING_POLICY
        self.assertEqual(
            (policy.total_source_utf8_bytes,
                policy.p0_source_utf8_bytes, policy.p1_source_utf8_bytes,
                policy.p2_source_utf8_bytes, policy.p3_source_utf8_bytes,
                policy.mandatory_reference_utf8_bytes,
                policy.max_delta_depth,),
            (131_072, 73_728, 16_384, 32_768, 0, 262_144, 16),)
        serialized = (
            b"total=131072;p0=73728;p1=16384;p2=32768;p3=0;references=262144;depth=16")
        self.assertEqual(policy.fingerprint, planning_fixtures.digest(serialized))
        self.assertRegex(policy.fingerprint, r"\Asha256:[0-9a-f]{64}\Z")

    def test_every_field_rejects_non_exact_integer_and_negative(self) -> None:
        defaults = {
            field.name: getattr(ctx_planning_inputs.DEFAULT_CONTEXT_PLANNING_POLICY, field.name)
            for field in fields(ctx_planning_inputs.ContextPlanningPolicy)
        }
        for field_name in defaults:
            for value in (True, 1.0, "1", None, -1):
                with self.subTest(field=field_name, value=value):
                    values = dict(defaults)
                    values[field_name] = value
                    self.assert_planning_error(
                        lambda values=values: ctx_planning_inputs.ContextPlanningPolicy(**values),
                        ctx_planning_inputs.ContextPlanningErrorCode.INVALID_POLICY,)

    def test_zero_class_aggregate_and_reference_boundaries(self) -> None:
        defaults = {
            field.name: getattr(ctx_planning_inputs.DEFAULT_CONTEXT_PLANNING_POLICY, field.name)
            for field in fields(ctx_planning_inputs.ContextPlanningPolicy)
        }
        for field_name in ("total_source_utf8_bytes",
            "p0_source_utf8_bytes", "p1_source_utf8_bytes",
            "max_delta_depth",):
            values = dict(defaults)
            values[field_name] = 0
            with self.subTest(field=field_name):
                self.assert_planning_error(lambda values=values: ctx_planning_inputs.ContextPlanningPolicy(**values),
                    ctx_planning_inputs.ContextPlanningErrorCode.INVALID_POLICY,
                )
        self.assertEqual( ctx_planning_inputs.ContextPlanningPolicy(2, 1, 1, 0, 0, 0, 1), ctx_planning_inputs.ContextPlanningPolicy(2, 1, 1, 0, 0, 0, 1), )
        exact = ctx_planning_inputs.ContextPlanningPolicy(10, 1, 1, 4, 4, 262_144, 1)
        self.assertEqual(exact.p0_source_utf8_bytes
            + exact.p1_source_utf8_bytes + exact.p2_source_utf8_bytes
            + exact.p3_source_utf8_bytes,
            exact.total_source_utf8_bytes,)
        self.assert_planning_error(lambda: ctx_planning_inputs.ContextPlanningPolicy(10, 1, 1, 4, 5, 0, 1),
            ctx_planning_inputs.ContextPlanningErrorCode.INVALID_POLICY,
        )
        for class_values in ((10, 11, 1, 0, 0),
            (10, 1, 11, 0, 0), (10, 1, 1, 11, 0),
            (10, 1, 1, 0, 11),):
            total, p0, p1, p2, p3 = class_values
            with self.subTest(class_values=class_values):
                self.assert_planning_error(lambda total=total, p0=p0, p1=p1, p2=p2, p3=p3: (
                        ctx_planning_inputs.ContextPlanningPolicy(total, p0, p1, p2, p3, 0, 1)),
                    ctx_planning_inputs.ContextPlanningErrorCode.INVALID_POLICY,
                )
        self.assert_planning_error(lambda: ctx_planning_inputs.ContextPlanningPolicy(2, 1, 1, 0, 0, 262_145, 1),
            ctx_planning_inputs.ContextPlanningErrorCode.INVALID_POLICY,
        )
