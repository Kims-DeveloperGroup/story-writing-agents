"""Regression checks for the suite's frozen scope and time gates."""

from __future__ import annotations

from contextlib import nullcontext
import json
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

from tests import suite_budget, suite_policy
from tests.id_moves import historical_feedback_caps, load_receipt
from tests.runner import contracts as runner_contracts
from tests.runner import collection as runner_collection
from tests.runner import selection as runner_selection
from tests.runner import evidence as runner_evidence
from tests.runner import execution as runner_execution
from tests.runner import profile as runner_profile
from tests.runner import reporting as runner_reporting
from tests.runner import supervision as runner_supervision
from tests.runner import hosted as runner_hosted
from tests.runner import cli as runner_cli


class SuiteBudgetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.budget = { "schema_version": 1, "max_sequential_wall_seconds": 1080, "max_shard_wall_seconds": 540, "max_test_source_lines": 1000,
            "subtest_caps": {"a.test": 2, "b.test": 0, "c.control": 0, "d.control": 0,
                             suite_policy.ISOLATED_TEST_ID: 0}, }

    def _reports(self, directory, counts):
        ids = tuple(self.budget["subtest_caps"])
        self.weights = suite_policy.TimingWeights(1, 2, {"a": 9, "b": 5, "c": 3}, "retained strict fixture", "sha256:" + "c" * 64, "fixture")
        self.plan = suite_policy.partition_full_shards(ids, self.weights)
        inventory = Path(directory) / "inventory.json"
        inventory.write_text(json.dumps(self.budget), encoding="utf-8")
        self.enterContext(patch.object(runner_reporting, "CANONICAL_INVENTORY_PATH", inventory))
        self.enterContext(patch.object(suite_policy, "load_timing_weights", return_value=self.weights))

        class Control(unittest.FunctionTestCase):
            def id(self): return self._description

        cases = tuple(Control(lambda: None, description=identifier) for identifier in ids)
        canonical = runner_contracts.CanonicalCollection(cases, ids)
        self.report_data = []
        for index, selected_ids in enumerate(self.plan.shard_ids):
            selected = runner_contracts.CanonicalCollection(tuple(cases[ids.index(value)] for value in selected_ids), selected_ids)
            report = runner_reporting.run_report(canonical, selected, runner_execution.execute(selected, stream=io.StringIO()), slowest=0,
                                        policy_metadata={"suite_policy": runner_selection.suite_policy_metadata(self.weights, self.plan),
                                                         "selection": {"tiers": [], "shard": f"{index + 1}/5"}})
            maximum = self.budget["max_shard_wall_seconds"]
            report["durations"].update(wall_seconds=maximum if index == 0 else (
                self.budget["max_sequential_wall_seconds"] - maximum) / 4, cumulative_test_seconds=1)
            report["tests"][0].update(duration_seconds=1, subtest_count=(*counts, 0, 0, 0)[index])
            self.report_data.append(report)
        paths = [str(Path(directory) / f"{index}.json") for index in range(1, 6)]
        for path, value in zip(paths, self.report_data):
            Path(path).write_text(json.dumps(value), encoding="utf-8")
        return paths

    def _assert_source_limit_neighbors_rejected(self, current, base):
        """Reject the original four adjacent source-floor/limit pairs."""
        original_floor = base["max_test_source_lines"]
        accepted_limit = current["max_test_source_lines"]
        for old, limit in (
            (original_floor - 1, accepted_limit), (original_floor + 1, accepted_limit),
            (original_floor, accepted_limit - 1), (original_floor, accepted_limit + 1),
        ):
            with self.assertRaises(suite_budget.BudgetError):
                suite_budget.check_monotonic(
                    dict(current, max_test_source_lines=limit),
                    dict(base, max_test_source_lines=old),
                )

    def test_new_id_increased_subtests_and_relaxed_time_are_rejected(self) -> None:
        hourly = dict(self.budget, max_sequential_wall_seconds=18000, max_shard_wall_seconds=3600)
        self.assertIsNone(suite_budget.check_monotonic(hourly, self.budget))
        source_base = dict(hourly, max_test_source_lines=142474)
        source = dict(source_base, max_test_source_lines=142604)
        self.assertIsNone(suite_budget.check_monotonic(source, source_base))
        group_source = dict(source, max_test_source_lines=142887)
        self.assertIsNone(suite_budget.check_monotonic(group_source, source))
        execution_source = dict(group_source, max_test_source_lines=143443)
        for origin in (source, group_source):
            self.assertIsNone(suite_budget.check_monotonic(execution_source, origin))
            for old, limit in ((origin["max_test_source_lines"] - 1, 143443),
                               (origin["max_test_source_lines"] + 1, 143443),
                               (origin["max_test_source_lines"], 143442),
                               (origin["max_test_source_lines"], 143444)):
                self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic,
                    dict(execution_source, max_test_source_lines=limit),
                    dict(origin, max_test_source_lines=old))
        wire_source = dict(execution_source, max_test_source_lines=145817)
        for origin in (source, execution_source):
            self.assertIsNone(suite_budget.check_monotonic(wire_source, origin))
            for old, limit in ((origin["max_test_source_lines"] - 1, 145817),
                               (origin["max_test_source_lines"] + 1, 145817),
                               (origin["max_test_source_lines"], 145816),
                               (origin["max_test_source_lines"], 145818)):
                self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic,
                    dict(wire_source, max_test_source_lines=limit),
                    dict(origin, max_test_source_lines=old))
        observation_source = dict(wire_source, max_test_source_lines=146667)
        for origin in (source, wire_source):
            self.assertIsNone(suite_budget.check_monotonic(observation_source, origin))
            self._assert_source_limit_neighbors_rejected(observation_source, origin)
        native_source = dict(observation_source, max_test_source_lines=146829)
        for origin in (source, observation_source):
            self.assertIsNone(suite_budget.check_monotonic(native_source, origin))
            self._assert_source_limit_neighbors_rejected(native_source, origin)
        continuity_source = dict(native_source, max_test_source_lines=147338)
        self.assertIsNone(suite_budget.check_monotonic(continuity_source, native_source))
        self._assert_source_limit_neighbors_rejected(continuity_source, native_source)
        preliminary_source = dict(continuity_source, max_test_source_lines=148667)
        self.assertIsNone(suite_budget.check_monotonic(preliminary_source, continuity_source))
        for old, limit in ((147337, 148667),
                           (147339, 148667),
                           (147338, 148666),
                           (147338, 148668)):
            with self.assertRaises(suite_budget.BudgetError):
                suite_budget.check_monotonic(
                    dict(preliminary_source, max_test_source_lines=limit),
                    dict(continuity_source, max_test_source_lines=old))
        recovery_source = dict(preliminary_source, max_test_source_lines=148715)
        for prior_source in (continuity_source, preliminary_source):
            self.assertIsNone(suite_budget.check_monotonic(recovery_source, prior_source))
            for old, limit in ((prior_source["max_test_source_lines"] - 1, 148715),
                               (prior_source["max_test_source_lines"] + 1, 148715),
                               (prior_source["max_test_source_lines"], 148714),
                               (prior_source["max_test_source_lines"], 148716)):
                with self.assertRaises(suite_budget.BudgetError):
                    suite_budget.check_monotonic(
                        dict(recovery_source, max_test_source_lines=limit),
                        dict(prior_source, max_test_source_lines=old))
        diagnostic_source = dict(recovery_source, max_test_source_lines=148747)
        for prior_source in (continuity_source, recovery_source):
            self.assertIsNone(suite_budget.check_monotonic(diagnostic_source, prior_source))
            for old, limit in ((prior_source["max_test_source_lines"] - 1, 148747),
                               (prior_source["max_test_source_lines"] + 1, 148747),
                               (prior_source["max_test_source_lines"], 148746),
                               (prior_source["max_test_source_lines"], 148748)):
                with self.assertRaises(suite_budget.BudgetError):
                    suite_budget.check_monotonic(
                        dict(diagnostic_source, max_test_source_lines=limit),
                        dict(prior_source, max_test_source_lines=old))
        await_source = dict(diagnostic_source, max_test_source_lines=148783)
        for prior_source in (continuity_source, diagnostic_source):
            self.assertIsNone(suite_budget.check_monotonic(await_source, prior_source))
            for old, limit in ((prior_source["max_test_source_lines"] - 1, 148783),
                               (prior_source["max_test_source_lines"] + 1, 148783),
                               (prior_source["max_test_source_lines"], 148782),
                               (prior_source["max_test_source_lines"], 148784)):
                with self.assertRaises(suite_budget.BudgetError):
                    suite_budget.check_monotonic(
                        dict(await_source, max_test_source_lines=limit),
                        dict(prior_source, max_test_source_lines=old))
        document_source = dict(await_source, max_test_source_lines=150604)
        self.assertIsNone(suite_budget.check_monotonic(document_source, await_source))
        for old, limit in ((148782, 150604),
                           (148784, 150604),
                           (148783, 150603),
                           (148783, 150605)):
            with self.assertRaises(suite_budget.BudgetError):
                suite_budget.check_monotonic(
                    dict(document_source, max_test_source_lines=limit),
                    dict(await_source, max_test_source_lines=old))
        candidate_source = dict(document_source, max_test_source_lines=150630)
        for prior_source in (await_source, document_source):
            self.assertIsNone(suite_budget.check_monotonic(candidate_source, prior_source))
            for old, limit in ((prior_source["max_test_source_lines"] - 1, 150630),
                               (prior_source["max_test_source_lines"] + 1, 150630),
                               (prior_source["max_test_source_lines"], 150629),
                               (prior_source["max_test_source_lines"], 150631)):
                with self.assertRaises(suite_budget.BudgetError):
                    suite_budget.check_monotonic(dict(candidate_source, max_test_source_lines=limit),
                                                 dict(prior_source, max_test_source_lines=old))
        material_source = dict(candidate_source, max_test_source_lines=150645)
        for prior_source in (await_source, candidate_source):
            self.assertIsNone(suite_budget.check_monotonic(material_source, prior_source))
            for old, limit in ((prior_source["max_test_source_lines"] - 1, 150645),
                               (prior_source["max_test_source_lines"] + 1, 150645),
                               (prior_source["max_test_source_lines"], 150644),
                               (prior_source["max_test_source_lines"], 150646)):
                with self.assertRaises(suite_budget.BudgetError):
                    suite_budget.check_monotonic(dict(material_source, max_test_source_lines=limit),
                                                 dict(prior_source, max_test_source_lines=old))
        root_plan_source = dict(material_source, max_test_source_lines=150770)
        for prior_source in (await_source, material_source):
            self.assertIsNone(suite_budget.check_monotonic(root_plan_source, prior_source))
            for old, limit in ((prior_source["max_test_source_lines"] - 1, 150770),
                               (prior_source["max_test_source_lines"] + 1, 150770),
                               (prior_source["max_test_source_lines"], 150769),
                               (prior_source["max_test_source_lines"], 150771)):
                with self.assertRaises(suite_budget.BudgetError):
                    suite_budget.check_monotonic(dict(root_plan_source, max_test_source_lines=limit),
                                                 dict(prior_source, max_test_source_lines=old))
        for old, limit in ((142603, 142887), (142605, 142887), (142604, 142886),
                           (142604, 142888), (142887, 142888)):
            self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic,
                dict(group_source, max_test_source_lines=limit), dict(source, max_test_source_lines=old))
        for old, limit in ((142474, 142603), (142474, 142605),
                           (142473, 142604), (142475, 142604), (142604, 142605)):
            self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic,
                              dict(source, max_test_source_lines=limit),
                              dict(source_base, max_test_source_lines=old))
        for key, value in (
            ("max_sequential_wall_seconds", 17999),
            ("max_shard_wall_seconds", 3599),
            ("subtest_caps", {**source_base["subtest_caps"], "a.test": 1}),
            ("subtest_caps", {key: cap for key, cap in source_base["subtest_caps"].items() if key != "b.test"}),
        ):
            self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic,
                dict(document_source, **{key: value}),
                dict(await_source, **{key: value}) if key != "subtest_caps" else await_source)
            self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic,
                              dict(continuity_source, **{key: value}),
                              dict(native_source, **{key: value}) if key != "subtest_caps" else native_source)
            self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic,
                              {**source, key: value}, source_base)
            self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic,
                {**group_source, key: value}, {**source, key: value} if key != "subtest_caps" else source)
            for origin in (source, group_source):
                self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic,
                    {**execution_source, key: value}, {**origin, key: value} if key != "subtest_caps" else origin)
            for origin in (source, execution_source):
                self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic,
                    {**wire_source, key: value}, {**origin, key: value} if key != "subtest_caps" else origin)
        self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic,
                          source, dict(self.budget, max_test_source_lines=142474))
        for change in [lambda value: value["subtest_caps"].update({"c.test": 0}),
                lambda value: value["subtest_caps"].update({"a.test": 3}),
                lambda value: value.update(max_shard_wall_seconds=value["max_shard_wall_seconds"] + 1),
                lambda value: value.update(max_sequential_wall_seconds=value["max_sequential_wall_seconds"] + 1)]:
            with self.subTest(change=change):
                for base, origin in ((self.budget, self.budget), (self.budget, hourly), (hourly, hourly),
                                     (source_base, source)):
                    candidate = json.loads(json.dumps(origin))
                    change(candidate)
                    with self.assertRaises(suite_budget.BudgetError):
                        suite_budget.check_monotonic(candidate, base)
        for base, field, value in [ (self.budget, "max_test_source_lines", 1001),
            (self.budget, "max_shard_wall_seconds", 540), (self.budget, "max_sequential_wall_seconds", 1080),
            ({**self.budget, "max_shard_wall_seconds": 539}, "max_shard_wall_seconds", 3600), ]:
            with self.assertRaises(suite_budget.BudgetError):
                suite_budget.check_monotonic({**hourly, field: value}, base)

        structured_invocation_current = suite_budget.load_budget()
        reference_read_current = dict(structured_invocation_current, max_test_source_lines=157235)
        context_invocation_current = dict(reference_read_current, max_test_source_lines=157207)
        bound_context_current = dict(context_invocation_current, max_test_source_lines=157164)
        profile_observations_current = dict(bound_context_current, max_test_source_lines=157102)
        adapter_generations_current = dict(profile_observations_current, max_test_source_lines=157080)
        binding_lifetime_current = dict(adapter_generations_current, max_test_source_lines=157025)
        accounting_current = dict(binding_lifetime_current, max_test_source_lines=156976)
        retrieval_outcomes_current = dict(accounting_current, max_test_source_lines=156953)
        provider_generation_current = dict(retrieval_outcomes_current, max_test_source_lines=156942)
        antigravity_stream_current = dict(provider_generation_current, max_test_source_lines=156925)
        *legacy_move_receipts, defaults_stage, wire_stage, stream_stage, invocation_stage, exchange_finalization_stage, native_recovery_stage, staging_stage, antigravity_command_stage, antigravity_stream_stage = load_receipt()
        antigravity_stream_moves = antigravity_stream_stage["moves"]
        antigravity_command_current = dict(antigravity_stream_current, max_test_source_lines=156851, subtest_caps=dict(antigravity_stream_current["subtest_caps"]))
        for move in antigravity_stream_moves:
            antigravity_command_current["subtest_caps"][move["old_id"]] = antigravity_command_current["subtest_caps"].pop(move["new_id"])
        antigravity_command_moves = antigravity_command_stage["moves"]
        staging_current = dict(antigravity_command_current, max_test_source_lines=156806, subtest_caps=dict(antigravity_command_current["subtest_caps"]))
        for move in antigravity_command_moves:
            staging_current["subtest_caps"][move["old_id"]] = staging_current["subtest_caps"].pop(move["new_id"])
        staging_moves = staging_stage["moves"]
        native_recovery_current = dict(staging_current, max_test_source_lines=156715,
                                       subtest_caps=dict(staging_current["subtest_caps"]))
        for move in staging_moves:
            native_recovery_current["subtest_caps"][move["old_id"]] = native_recovery_current["subtest_caps"].pop(move["new_id"])
        native_recovery_moves = native_recovery_stage["moves"]
        exchange_finalization_current = dict(native_recovery_current, max_test_source_lines=156682,
                                            subtest_caps=dict(native_recovery_current["subtest_caps"]))
        for move in native_recovery_moves:
            exchange_finalization_current["subtest_caps"][move["old_id"]] = exchange_finalization_current["subtest_caps"].pop(move["new_id"])
        exchange_finalization_moves = exchange_finalization_stage["moves"]
        command_preflight_current = dict(exchange_finalization_current, max_test_source_lines=156611,
                                         subtest_caps=dict(exchange_finalization_current["subtest_caps"]))
        for move in exchange_finalization_moves:
            command_preflight_current["subtest_caps"][move["old_id"]] = command_preflight_current["subtest_caps"].pop(move["new_id"])
        invocation_current = dict(command_preflight_current, max_test_source_lines=156558)
        invocation_moves = invocation_stage["moves"]
        stream_current = dict(invocation_current, max_test_source_lines=156459, subtest_caps=dict(invocation_current["subtest_caps"]))
        for move in invocation_moves:
            stream_current["subtest_caps"][move["old_id"]] = stream_current["subtest_caps"].pop(move["new_id"])
        stream_moves = stream_stage["moves"]
        wire_current = dict(stream_current, max_test_source_lines=156369, subtest_caps=dict(stream_current["subtest_caps"]))
        for move in stream_moves:
            wire_current["subtest_caps"][move["old_id"]] = wire_current["subtest_caps"].pop(move["new_id"])
        split_current = dict(wire_current, max_test_source_lines=156274, subtest_caps=dict(wire_current["subtest_caps"]))
        wire_moves = wire_stage["moves"]
        for move in wire_moves:
            split_current["subtest_caps"][move["old_id"]] = split_current["subtest_caps"].pop(move["new_id"])
        migrated = dict(split_current, subtest_caps=dict(split_current["subtest_caps"]))
        defaults_moves = defaults_stage["moves"]
        for move in defaults_moves:
            migrated["subtest_caps"][move["old_id"]] = migrated["subtest_caps"].pop(move["new_id"])
        execution_profiles_current = dict(migrated, max_test_source_lines=156207, subtest_caps=dict(migrated["subtest_caps"]))
        task_admission_current = dict(migrated, subtest_caps=dict(migrated["subtest_caps"]))
        task_admission_current["max_test_source_lines"] = 154305
        for move in legacy_move_receipts[-1]["moves"]:
            migrated["subtest_caps"][move["old_id"]] = migrated["subtest_caps"].pop(move["new_id"])
        migrated["max_test_source_lines"] = 154078
        request_policy_current = dict(migrated, subtest_caps=dict(migrated["subtest_caps"]))
        migrated["max_test_source_lines"] = 153941
        document_policy_current = dict(migrated, subtest_caps=dict(migrated["subtest_caps"]))
        for move in legacy_move_receipts[-2]["moves"]:
            migrated["subtest_caps"][move["old_id"]] = migrated["subtest_caps"].pop(move["new_id"])
        migrated["max_test_source_lines"] = 153829
        authority_current = dict(migrated, subtest_caps=dict(migrated["subtest_caps"]))
        for move in legacy_move_receipts[-3]["moves"]:
            migrated["subtest_caps"][move["old_id"]] = migrated["subtest_caps"].pop(move["new_id"])
        evidence_current = dict(migrated, subtest_caps=dict(migrated["subtest_caps"]))
        for move in legacy_move_receipts[-4]["moves"]:
            migrated["subtest_caps"][move["old_id"]] = migrated["subtest_caps"].pop(move["new_id"])
        material_current = dict(migrated, subtest_caps=dict(migrated["subtest_caps"]))
        fixture_current = dict(material_current, subtest_caps=dict(material_current["subtest_caps"]))
        document_current = dict(fixture_current, subtest_caps=dict(fixture_current["subtest_caps"]))
        for move in legacy_move_receipts[-5]["moves"]:
            migrated["subtest_caps"][move["old_id"]] = migrated["subtest_caps"].pop(move["new_id"])
        migrated["max_test_source_lines"] = 152138
        source_current, finalization_current = dict(migrated, subtest_caps=dict(migrated["subtest_caps"])), dict(migrated)
        recommendation_current = dict(migrated)
        after_move_current = source_current
        migrated["subtest_caps"], publication_caps, before_998_caps, after_move_current["subtest_caps"], finalization_current["subtest_caps"] = historical_feedback_caps(migrated["subtest_caps"], with_publication_base=True, with_before_998_base=True, with_finalization_base=True, with_recommendation_base=True)
        publication_current = dict(after_move_current, subtest_caps=before_998_caps)
        publication_base = dict(publication_current, max_test_source_lines=151456, subtest_caps=publication_caps)
        source_current = publication_base
        migrated["max_test_source_lines"] = 151431
        expected_additions = {
            'tests.test_historical_material_workflow.HistoricalMaterialWorkflowTests.test_prior_draft_and_rejected_response_remain_input_only_in_new_task': 0,
            'tests.test_lead_artifact_objectives.LeadArtifactObjectiveTests.test_document_plan_goal_candidate_and_sequential_plot_work_survive_restart': 0,
            'tests.test_lead_artifact_objectives.LeadDefaultBudgetRefusalTests.test_default_grouped_budget_refuses_unfunded_artifact_preparation': 0,
            'tests.test_lead_orchestration.LeadSchemaFactoringTests.test_small_factoring_annotations_and_owner_refs_are_exact': 0,
            'tests.test_lead_orchestration.LeadSubmittedFailureRecoveryTests.test_submitted_cancelled_before_correction_never_replays': 1,
            'tests.test_lead_orchestration.LeadSubmittedFailureRecoveryTests.test_submitted_cancelled_during_correction_never_replays': 1,
            'tests.test_lead_orchestration.LeadSubmittedFailureRecoveryTests.test_submitted_crashed_before_correction_never_replays': 1,
            'tests.test_lead_orchestration.LeadSubmittedFailureRecoveryTests.test_submitted_crashed_during_correction_never_replays': 1,
            'tests.test_lead_orchestration.LeadSubmittedFailureRecoveryTests.test_submitted_missing_before_correction_never_replays': 1,
            'tests.test_lead_orchestration.LeadSubmittedFailureRecoveryTests.test_submitted_missing_during_correction_never_replays': 1,
            'tests.test_lead_orchestration.LeadSubmittedFailureRecoveryTests.test_submitted_submission_uncertain_before_correction_never_replays': 1,
            'tests.test_lead_orchestration.LeadSubmittedFailureRecoveryTests.test_submitted_submission_uncertain_during_correction_never_replays': 1,
            'tests.test_lead_orchestration.LeadSubmittedFailureRecoveryTests.test_submitted_timed_out_before_correction_never_replays': 1,
            'tests.test_lead_orchestration.LeadSubmittedFailureRecoveryTests.test_submitted_timed_out_during_correction_never_replays': 1,
            'tests.test_selected_task_material_codec.SelectedTaskMaterialCodecTests.test_two_captures_keep_closed_wire_shape_and_order': 0,
            'tests.test_specialist_routing.SpecialistRoutingTests.test_formal_planning_question_selected_gen_is_closed_and_owned': 0,
            'tests.test_specialist_routing.SpecialistRoutingTests.test_selected_document_revision_frames_and_continuation_are_current': 0,
        }
        prior_caps = {key: value for key, value in migrated["subtest_caps"].items() if key not in expected_additions}
        recovery_id = 'tests.test_lead_orchestration.LeadRecoveryAndAccountingTests.test_unstarted_prepared_and_submitted_requests_never_replay'
        prior_caps[recovery_id] = 12
        self.assertEqual(set(migrated["subtest_caps"]) - set(prior_caps), set(expected_additions))
        self.assertEqual({key: migrated["subtest_caps"][key] for key in expected_additions}, expected_additions)
        self.assertEqual(migrated["max_test_source_lines"], 151431)
        matrix_id = "tests.test_lead_orchestration.LeadSubmittedFailureRecoveryTests.test_submitted_missing_before_correction_never_replays"
        untouched_id = "tests.test_specialist_routing.SpecialistRoutingTests.test_canonical_artifact_and_semantic_domain_ownership"
        zero_id = 'tests.test_lead_artifact_objectives.LeadDefaultBudgetRefusalTests.test_default_grouped_budget_refuses_unfunded_artifact_preparation'
        self.assertEqual(prior_caps[untouched_id], 5)
        for old_limit in (148783, 150770):
            parent = dict(migrated, max_test_source_lines=old_limit, subtest_caps=prior_caps)
            self.assertIsNone(suite_budget.check_monotonic(migrated, parent))
            for old, limit in ((old_limit - 1, 151431), (old_limit + 1, 151431), (old_limit, 151430), (old_limit, 151432)):
                self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic,
                    dict(migrated, max_test_source_lines=limit), dict(parent, max_test_source_lines=old))
            for caps in ({key: value for key, value in migrated["subtest_caps"].items() if key != matrix_id},
                         {**migrated["subtest_caps"], "tests.unreviewed.Case.test_extra": 0},
                         {**migrated["subtest_caps"], matrix_id: 0}, {**migrated["subtest_caps"], matrix_id: 2},
                         {**migrated["subtest_caps"], zero_id: 1},
                         {key: value for key, value in migrated["subtest_caps"].items() if key != zero_id},
                         {**migrated["subtest_caps"], recovery_id: 1}, {**migrated["subtest_caps"], recovery_id: 3},
                         {**migrated["subtest_caps"], untouched_id: 4}, {**migrated["subtest_caps"], untouched_id: 6},
                         {**migrated["subtest_caps"], 'tests.test_selected_task_material_codec.SelectedTaskMaterialCodecTests.test_two_captures_keep_closed_wire_shape_and_order': 1},
                         {**migrated["subtest_caps"], 'tests.test_historical_material_workflow.HistoricalMaterialWorkflowTests.test_prior_draft_and_rejected_response_remain_input_only_in_new_task': 1}):
                self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, dict(migrated, subtest_caps=caps), parent)
            self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, migrated,
                dict(parent, subtest_caps={**prior_caps, untouched_id: 4}))
            for field in ("max_sequential_wall_seconds", "max_shard_wall_seconds"):
                for change in (-1, 1):
                    self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic,
                        dict(migrated, **{field: migrated[field] + change}), parent)
                    self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic,
                        migrated, dict(parent, **{field: parent[field] + change}))

        from types import SimpleNamespace
        import os, sys, time
        source_base = dict(source_current, max_test_source_lines=151431)
        self.assertIsNone(suite_budget.check_monotonic(source_current, source_base))
        self._assert_source_limit_neighbors_rejected(source_current, source_base)
        for field, value in (("schema_version", 2), ("max_sequential_wall_seconds", 18001), ("max_shard_wall_seconds", 3601)):
            self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, dict(source_current, **{field: value}), source_base)
            self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, source_current, dict(source_base, **{field: value}))
        for caps in (
            {key: value for key, value in source_current["subtest_caps"].items() if key != zero_id},
            {**source_current["subtest_caps"], "tests.unreviewed.Case.test_extra": 0},
            {**source_current["subtest_caps"], matrix_id: 2},
            {**source_current["subtest_caps"], matrix_id: 0},
        ):
            self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, dict(source_current, subtest_caps=caps), source_base)
        self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, source_current, dict(source_base, subtest_caps={**source_base["subtest_caps"], matrix_id: 2}))
        publication_current["max_test_source_lines"] = 151516
        self.assertIsNone(suite_budget.check_monotonic(publication_current, publication_base))
        self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, publication_current, dict(publication_current, max_test_source_lines=151456))
        for old, limit in ((151455, 151516), (151457, 151516), (151456, 151515), (151456, 151517)):
            self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, dict(publication_current, max_test_source_lines=limit), dict(publication_base, max_test_source_lines=old))
        for field, value in (("schema_version", 2), ("max_sequential_wall_seconds", 18001), ("max_shard_wall_seconds", 3601)):
            self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, dict(publication_current, **{field: value}), publication_base)
        for caps in (
            {key: value for key, value in publication_current["subtest_caps"].items() if key != zero_id},
            {**publication_current["subtest_caps"], "tests.unreviewed.Case.test_extra": 0},
            {**publication_current["subtest_caps"], matrix_id: 2},
            {**publication_current["subtest_caps"], matrix_id: 0},
        ):
            self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, dict(publication_current, subtest_caps=caps), publication_base)
        self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, publication_current, dict(publication_base, subtest_caps={**publication_base["subtest_caps"], matrix_id: 2}))

        after_move_current["max_test_source_lines"] = 151686
        self.assertIsNone(suite_budget.check_monotonic(after_move_current, publication_current))
        self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, after_move_current, dict(after_move_current, max_test_source_lines=151516))
        self._assert_source_limit_neighbors_rejected(after_move_current, publication_current)
        for field, value in (("schema_version", 2), ("max_sequential_wall_seconds", 18001), ("max_shard_wall_seconds", 3601)):
            self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, dict(after_move_current, **{field: value}), publication_current)
        for caps in (
            {key: value for key, value in after_move_current["subtest_caps"].items() if key != zero_id},
            {**after_move_current["subtest_caps"], "tests.unreviewed.Case.test_extra": 0},
            {**after_move_current["subtest_caps"], matrix_id: 2},
            {**after_move_current["subtest_caps"], matrix_id: 0},
        ):
            self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, dict(after_move_current, subtest_caps=caps), publication_current)
        self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, after_move_current, dict(publication_current, subtest_caps={**publication_current["subtest_caps"], matrix_id: 2}))
        finalization_current["max_test_source_lines"] = 152064
        self.assertIsNone(suite_budget.check_monotonic(finalization_current, after_move_current))
        self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, finalization_current, dict(finalization_current, max_test_source_lines=151686))
        self._assert_source_limit_neighbors_rejected(finalization_current, after_move_current)
        for field, value in (("schema_version", 2), ("max_sequential_wall_seconds", 18001), ("max_shard_wall_seconds", 3601)):
            self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, dict(finalization_current, **{field: value}), after_move_current)
        for caps in (
            {key: value for key, value in finalization_current["subtest_caps"].items() if key != zero_id},
            {**finalization_current["subtest_caps"], "tests.unreviewed.Case.test_extra": 0},
            {**finalization_current["subtest_caps"], matrix_id: 2},
            {**finalization_current["subtest_caps"], matrix_id: 0},
        ):
            self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, dict(finalization_current, subtest_caps=caps), after_move_current)
        self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, finalization_current, dict(after_move_current, subtest_caps={**after_move_current["subtest_caps"], matrix_id: 2}))

        recommendation_current["max_test_source_lines"] = 152138
        self.assertIsNone(suite_budget.check_monotonic(recommendation_current, finalization_current))
        self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, recommendation_current, dict(recommendation_current, max_test_source_lines=152064))
        self._assert_source_limit_neighbors_rejected(recommendation_current, finalization_current)
        for field, value in (("schema_version", 2), ("max_sequential_wall_seconds", 18001), ("max_shard_wall_seconds", 3601)):
            self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, dict(recommendation_current, **{field: value}), finalization_current)
        for caps in (
            {key: value for key, value in recommendation_current["subtest_caps"].items() if key != zero_id},
            {**recommendation_current["subtest_caps"], "tests.unreviewed.Case.test_extra": 0},
            {**recommendation_current["subtest_caps"], matrix_id: 2},
            {**recommendation_current["subtest_caps"], matrix_id: 0},
        ):
            self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, dict(recommendation_current, subtest_caps=caps), finalization_current)
        self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, recommendation_current, dict(finalization_current, subtest_caps={**finalization_current["subtest_caps"], matrix_id: 2}))

        material_current["max_test_source_lines"] = 152220
        self.assertIsNone(suite_budget.check_monotonic(material_current, recommendation_current))
        self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, material_current, dict(material_current, max_test_source_lines=152138))
        self._assert_source_limit_neighbors_rejected(material_current, recommendation_current)
        for field, value in (("schema_version", 2), ("max_sequential_wall_seconds", 18001), ("max_shard_wall_seconds", 3601)):
            self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, dict(material_current, **{field: value}), recommendation_current)
        for caps in (
            {key: value for key, value in material_current["subtest_caps"].items() if key != zero_id},
            {**material_current["subtest_caps"], "tests.unreviewed.Case.test_extra": 0},
            {**material_current["subtest_caps"], matrix_id: 2},
            {**material_current["subtest_caps"], matrix_id: 0},
        ):
            self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, dict(material_current, subtest_caps=caps), recommendation_current)
        self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, material_current, dict(recommendation_current, subtest_caps={**recommendation_current["subtest_caps"], matrix_id: 2}))
        fixture_current["max_test_source_lines"] = 152665
        self.assertEqual(fixture_current["max_test_source_lines"], 152665)
        self.assertIsNone(suite_budget.check_monotonic(fixture_current, material_current))
        self._assert_source_limit_neighbors_rejected(fixture_current, material_current)
        for field, value in (("schema_version", 2), ("max_sequential_wall_seconds", 18001), ("max_shard_wall_seconds", 3601)):
            self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, dict(fixture_current, **{field: value}), material_current)
            self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, fixture_current, dict(material_current, **{field: value}))
        for caps in (
            {key: value for key, value in fixture_current["subtest_caps"].items() if key != zero_id},
            {**fixture_current["subtest_caps"], "tests.unreviewed.Case.test_extra": 0},
            {**fixture_current["subtest_caps"], matrix_id: 2},
            {**fixture_current["subtest_caps"], matrix_id: 0},
        ):
            self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, dict(fixture_current, subtest_caps=caps), material_current)
        self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, fixture_current, dict(material_current, subtest_caps={**material_current["subtest_caps"], matrix_id: 2}))

        document_current["max_test_source_lines"] = 152727
        self.assertEqual(document_current["max_test_source_lines"], 152727)
        self.assertIsNone(suite_budget.check_monotonic(document_current, fixture_current))
        self._assert_source_limit_neighbors_rejected(document_current, fixture_current)
        for field, value in (("schema_version", 2), ("max_sequential_wall_seconds", 18001), ("max_shard_wall_seconds", 3601)):
            self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, dict(document_current, **{field: value}), fixture_current)
            self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, document_current, dict(fixture_current, **{field: value}))
        for caps in (
            {key: value for key, value in document_current["subtest_caps"].items() if key != zero_id},
            {**document_current["subtest_caps"], "tests.unreviewed.Case.test_extra": 0},
            {**document_current["subtest_caps"], matrix_id: 2},
            {**document_current["subtest_caps"], matrix_id: 0},
        ):
            self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, dict(document_current, subtest_caps=caps), fixture_current)
        self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, document_current, dict(fixture_current, subtest_caps={**fixture_current["subtest_caps"], matrix_id: 2}))

        evidence_current["max_test_source_lines"] = 152814
        self.assertEqual(evidence_current["max_test_source_lines"], 152814)
        self.assertIsNone(suite_budget.check_monotonic(evidence_current, document_current))
        self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, evidence_current, dict(evidence_current, max_test_source_lines=152727))
        self._assert_source_limit_neighbors_rejected(evidence_current, document_current)
        for field, value in (("schema_version", 2), ("max_sequential_wall_seconds", 18001), ("max_shard_wall_seconds", 3601)):
            self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, dict(evidence_current, **{field: value}), document_current)
            self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, evidence_current, dict(document_current, **{field: value}))
        for caps in (
            {key: value for key, value in evidence_current["subtest_caps"].items() if key != zero_id},
            {**evidence_current["subtest_caps"], "tests.unreviewed.Case.test_extra": 0},
            {**evidence_current["subtest_caps"], matrix_id: 2},
            {**evidence_current["subtest_caps"], matrix_id: 0},
        ):
            self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, dict(evidence_current, subtest_caps=caps), document_current)
        self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, evidence_current, dict(document_current, subtest_caps={**document_current["subtest_caps"], matrix_id: 2}))
        self.assertEqual(authority_current["max_test_source_lines"], 153829)
        self.assertIsNone(suite_budget.check_monotonic(authority_current, evidence_current))
        self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, authority_current, dict(authority_current, max_test_source_lines=152814))
        self._assert_source_limit_neighbors_rejected(authority_current, evidence_current)
        for field, value in (("schema_version", 2), ("max_sequential_wall_seconds", 18001), ("max_shard_wall_seconds", 3601)):
            self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, dict(authority_current, **{field: value}), evidence_current)
            self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, authority_current, dict(evidence_current, **{field: value}))
        for caps in (
            {key: value for key, value in authority_current["subtest_caps"].items() if key != zero_id},
            {**authority_current["subtest_caps"], "tests.unreviewed.Case.test_extra": 0},
            {**authority_current["subtest_caps"], matrix_id: 2},
            {**authority_current["subtest_caps"], matrix_id: 0},
        ):
            self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, dict(authority_current, subtest_caps=caps), evidence_current)
        self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, authority_current, dict(evidence_current, subtest_caps={**evidence_current["subtest_caps"], matrix_id: 2}))

        self.assertEqual(document_policy_current["max_test_source_lines"], 153941)
        self.assertIsNone(suite_budget.check_monotonic(document_policy_current, authority_current))
        self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, document_policy_current, dict(document_policy_current, max_test_source_lines=152814))
        self._assert_source_limit_neighbors_rejected(document_policy_current, authority_current)
        for field, value in (("schema_version", 2), ("max_sequential_wall_seconds", 18001), ("max_shard_wall_seconds", 3601)):
            self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, dict(document_policy_current, **{field: value}), authority_current)
            self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, document_policy_current, dict(authority_current, **{field: value}))
        for caps in (
            {key: value for key, value in document_policy_current["subtest_caps"].items() if key != zero_id},
            {**document_policy_current["subtest_caps"], "tests.unreviewed.Case.test_extra": 0},
            {**document_policy_current["subtest_caps"], matrix_id: 2},
            {**document_policy_current["subtest_caps"], matrix_id: 0},
        ):
            self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, dict(document_policy_current, subtest_caps=caps), authority_current)
        self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, document_policy_current, dict(authority_current, subtest_caps={**authority_current["subtest_caps"], matrix_id: 2}))

        self.assertEqual(request_policy_current["max_test_source_lines"], 154078)
        self.assertIsNone(suite_budget.check_monotonic(request_policy_current, document_policy_current))
        self._assert_source_limit_neighbors_rejected(request_policy_current, document_policy_current)
        for field, value in (("schema_version", 2), ("max_sequential_wall_seconds", 18001), ("max_shard_wall_seconds", 3601)):
            self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, dict(request_policy_current, **{field: value}), document_policy_current)
            self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, request_policy_current, dict(document_policy_current, **{field: value}))
        for caps in (
            {key: value for key, value in request_policy_current["subtest_caps"].items() if key != zero_id},
            {**request_policy_current["subtest_caps"], "tests.unreviewed.Case.test_extra": 0},
            {**request_policy_current["subtest_caps"], matrix_id: 2},
            {**request_policy_current["subtest_caps"], matrix_id: 0},
        ):
            self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, dict(request_policy_current, subtest_caps=caps), document_policy_current)
        self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, request_policy_current, dict(document_policy_current, subtest_caps={**document_policy_current["subtest_caps"], matrix_id: 2}))

        self.assertEqual(task_admission_current["max_test_source_lines"], 154305)
        self.assertIsNone(suite_budget.check_monotonic(task_admission_current, request_policy_current))
        self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, task_admission_current, dict(task_admission_current, max_test_source_lines=152814))
        self._assert_source_limit_neighbors_rejected(task_admission_current, request_policy_current)
        for field, value in (("schema_version", 2), ("max_sequential_wall_seconds", 18001), ("max_shard_wall_seconds", 3601)):
            self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, dict(task_admission_current, **{field: value}), request_policy_current)
            self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, task_admission_current, dict(request_policy_current, **{field: value}))
        for caps in (
            {key: value for key, value in task_admission_current["subtest_caps"].items() if key != zero_id},
            {**task_admission_current["subtest_caps"], "tests.unreviewed.Case.test_extra": 0},
            {**task_admission_current["subtest_caps"], matrix_id: 2},
            {**task_admission_current["subtest_caps"], matrix_id: 0},
        ):
            self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, dict(task_admission_current, subtest_caps=caps), request_policy_current)
        self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, task_admission_current, dict(request_policy_current, subtest_caps={**request_policy_current["subtest_caps"], matrix_id: 2}))
        workspace_current = dict(suite_budget.load_budget(), max_test_source_lines=154860, subtest_caps=execution_profiles_current["subtest_caps"])
        artifact_planning_current = dict(workspace_current, max_test_source_lines=154774)
        self.assertEqual(artifact_planning_current["max_test_source_lines"], 154774)
        self.assertIsNone(suite_budget.check_monotonic(artifact_planning_current, task_admission_current))
        self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, artifact_planning_current, dict(artifact_planning_current, max_test_source_lines=152814))
        self._assert_source_limit_neighbors_rejected(artifact_planning_current, task_admission_current)
        for field, value in (("schema_version", 2), ("max_sequential_wall_seconds", 18001), ("max_shard_wall_seconds", 3601)):
            self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, dict(artifact_planning_current, **{field: value}), task_admission_current)
            self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, artifact_planning_current, dict(task_admission_current, **{field: value}))
        for caps in (
            {key: value for key, value in artifact_planning_current["subtest_caps"].items() if key != zero_id},
            {**artifact_planning_current["subtest_caps"], "tests.unreviewed.Case.test_extra": 0},
            {**artifact_planning_current["subtest_caps"], matrix_id: 2},
            {**artifact_planning_current["subtest_caps"], matrix_id: 0},
        ):
            self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, dict(artifact_planning_current, subtest_caps=caps), task_admission_current)
        self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, artifact_planning_current, dict(task_admission_current, subtest_caps={**task_admission_current["subtest_caps"], matrix_id: 2}))

        response_contract_current = dict(suite_budget.load_budget(), max_test_source_lines=155128, subtest_caps=execution_profiles_current["subtest_caps"])
        self.assertEqual(response_contract_current["max_test_source_lines"], 155128)
        self.assertIsNone(suite_budget.check_monotonic(response_contract_current, artifact_planning_current))
        self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, response_contract_current, dict(artifact_planning_current, max_test_source_lines=154305))
        self._assert_source_limit_neighbors_rejected(response_contract_current, artifact_planning_current)
        for field, value in (("schema_version", 2), ("max_sequential_wall_seconds", 18001), ("max_shard_wall_seconds", 3601)):
            self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, dict(response_contract_current, **{field: value}), artifact_planning_current)
            self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, response_contract_current, dict(artifact_planning_current, **{field: value}))
        for caps in (
            {key: value for key, value in response_contract_current["subtest_caps"].items() if key != zero_id},
            {**response_contract_current["subtest_caps"], "tests.unreviewed.Case.test_extra": 0},
            {**response_contract_current["subtest_caps"], matrix_id: 2},
            {**response_contract_current["subtest_caps"], matrix_id: 0},
        ):
            self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, dict(response_contract_current, subtest_caps=caps), artifact_planning_current)
        self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, response_contract_current, dict(artifact_planning_current, subtest_caps={**artifact_planning_current["subtest_caps"], matrix_id: 2}))

        self.assertEqual(workspace_current["max_test_source_lines"], 154860)
        self.assertIsNone(suite_budget.check_monotonic(workspace_current, artifact_planning_current))
        self._assert_source_limit_neighbors_rejected(workspace_current, artifact_planning_current)
        integrated_current = dict(suite_budget.load_budget(), max_test_source_lines=155219, subtest_caps=execution_profiles_current["subtest_caps"])
        self.assertEqual(integrated_current["max_test_source_lines"], 155219)
        for prior in (workspace_current, response_contract_current):
            self.assertIsNone(suite_budget.check_monotonic(integrated_current, prior))
            self._assert_source_limit_neighbors_rejected(integrated_current, prior)
        consensus_policy_current = dict(suite_budget.load_budget(), max_test_source_lines=155318, subtest_caps=execution_profiles_current["subtest_caps"])
        self.assertEqual(consensus_policy_current["max_test_source_lines"], 155318)
        self.assertIsNone(suite_budget.check_monotonic(consensus_policy_current, integrated_current))
        self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, consensus_policy_current, dict(consensus_policy_current, max_test_source_lines=152814))
        self._assert_source_limit_neighbors_rejected(consensus_policy_current, integrated_current)
        for field, value in (("schema_version", 2), ("max_sequential_wall_seconds", 18001), ("max_shard_wall_seconds", 3601)):
            self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, dict(consensus_policy_current, **{field: value}), integrated_current)
            self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, consensus_policy_current, dict(integrated_current, **{field: value}))
        for caps in (
            {key: value for key, value in consensus_policy_current["subtest_caps"].items() if key != zero_id},
            {**consensus_policy_current["subtest_caps"], "tests.unreviewed.Case.test_extra": 0},
            {**consensus_policy_current["subtest_caps"], matrix_id: 2},
            {**consensus_policy_current["subtest_caps"], matrix_id: 0},
        ):
            self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, dict(consensus_policy_current, subtest_caps=caps), integrated_current)
        self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, consensus_policy_current, dict(integrated_current, subtest_caps={**integrated_current["subtest_caps"], matrix_id: 2}))


        git_owners_current = dict(suite_budget.load_budget(), max_test_source_lines=155394, subtest_caps=execution_profiles_current["subtest_caps"])
        self.assertEqual(git_owners_current["max_test_source_lines"], 155394)
        self.assertIsNone(suite_budget.check_monotonic(integrated_current, git_owners_current))
        self._assert_source_limit_neighbors_rejected(git_owners_current, integrated_current)
        combined_current = dict(suite_budget.load_budget(), max_test_source_lines=155510, subtest_caps=execution_profiles_current["subtest_caps"])
        self.assertEqual(combined_current["max_test_source_lines"], 155510)
        for prior in (consensus_policy_current, git_owners_current):
            self.assertIsNone(suite_budget.check_monotonic(combined_current, prior))
            self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, combined_current, dict(combined_current, max_test_source_lines=152814))
            self._assert_source_limit_neighbors_rejected(combined_current, prior)
            for field, value in (("schema_version", 2), ("max_sequential_wall_seconds", 18001), ("max_shard_wall_seconds", 3601)):
                self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, dict(combined_current, **{field: value}), prior)
                self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, combined_current, dict(prior, **{field: value}))
            for caps in (
                {key: value for key, value in combined_current["subtest_caps"].items() if key != zero_id},
                {**combined_current["subtest_caps"], "tests.unreviewed.Case.test_extra": 0},
                {**combined_current["subtest_caps"], matrix_id: 2},
                {**combined_current["subtest_caps"], matrix_id: 0},
            ):
                self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, dict(combined_current, subtest_caps=caps), prior)
            self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, combined_current, dict(prior, subtest_caps={**prior["subtest_caps"], matrix_id: 2}))


        rules_current = dict(suite_budget.load_budget(), max_test_source_lines=155583, subtest_caps=execution_profiles_current["subtest_caps"])
        self.assertEqual(rules_current["max_test_source_lines"], 155583)
        self.assertIsNone(suite_budget.check_monotonic(rules_current, combined_current))
        self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, rules_current, dict(rules_current, max_test_source_lines=152814))
        self._assert_source_limit_neighbors_rejected(rules_current, combined_current)
        for field, value in (("schema_version", 2), ("max_sequential_wall_seconds", 18001), ("max_shard_wall_seconds", 3601)):
            self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, dict(rules_current, **{field: value}), combined_current)
            self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, rules_current, dict(combined_current, **{field: value}))
        for caps in (
            {key: value for key, value in rules_current["subtest_caps"].items() if key != zero_id},
            {**rules_current["subtest_caps"], "tests.unreviewed.Case.test_extra": 0},
            {**rules_current["subtest_caps"], matrix_id: 2},
            {**rules_current["subtest_caps"], matrix_id: 0},
        ):
            self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, dict(rules_current, subtest_caps=caps), combined_current)
        self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, rules_current, dict(combined_current, subtest_caps={**combined_current["subtest_caps"], matrix_id: 2}))


        from tests.test_antigravity_command import AntigravityCommandTests
        from tests.test_antigravity_invocation_options import AntigravityInvocationOptionsTests
        from tests.test_antigravity_stream_delivery import AntigravityStreamDeliveryTests
        from tests.test_antigravity_stream_validation import AntigravityStreamValidationTests
        from tests.test_antigravity_definition_staging import AntigravityDefinitionStagingTests
        self.assertEqual(AntigravityDefinitionStagingTests.test_binding_is_stable_isolated_and_uses_private_custom_agent.__module__, AntigravityDefinitionStagingTests.__module__)
        self.assertEqual(AntigravityCommandTests.test_command_stdin_paths_and_fresh_resume_results_are_exact.__module__, AntigravityCommandTests.__module__)
        self.assertEqual(AntigravityInvocationOptionsTests.test_model_effort_precedence_and_backend_projection_are_immutable.__module__, AntigravityInvocationOptionsTests.__module__)
        self.assertEqual(AntigravityDefinitionStagingTests.test_mode_failures_are_best_effort_but_controls_propagate.__module__, AntigravityDefinitionStagingTests.__module__)
        decoder_current = dict(suite_budget.load_budget(), max_test_source_lines=155687, subtest_caps=execution_profiles_current["subtest_caps"])
        self.assertEqual(decoder_current["max_test_source_lines"], 155687)
        self.assertIsNone(suite_budget.check_monotonic(decoder_current, rules_current))
        self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, decoder_current, dict(decoder_current, max_test_source_lines=152814))
        self._assert_source_limit_neighbors_rejected(decoder_current, rules_current)
        codex_source_current = dict(suite_budget.load_budget(), max_test_source_lines=155717, subtest_caps=execution_profiles_current["subtest_caps"])
        self.assertEqual(codex_source_current["max_test_source_lines"], 155717)
        self.assertIsNone(suite_budget.check_monotonic(codex_source_current, decoder_current))
        self._assert_source_limit_neighbors_rejected(codex_source_current, decoder_current)
        recovery_current = dict(suite_budget.load_budget(), max_test_source_lines=155785, subtest_caps=execution_profiles_current["subtest_caps"])
        self.assertEqual(recovery_current["max_test_source_lines"], 155785)
        self.assertIsNone(suite_budget.check_monotonic(recovery_current, codex_source_current))
        self._assert_source_limit_neighbors_rejected(recovery_current, codex_source_current)
        records_current = dict(suite_budget.load_budget(), max_test_source_lines=155842, subtest_caps=execution_profiles_current["subtest_caps"])
        self.assertEqual(records_current["max_test_source_lines"], 155842)
        self.assertIsNone(suite_budget.check_monotonic(records_current, recovery_current))
        self._assert_source_limit_neighbors_rejected(records_current, recovery_current)
        guard_current = dict(suite_budget.load_budget(), max_test_source_lines=155869, subtest_caps=execution_profiles_current["subtest_caps"])
        self.assertEqual(guard_current["max_test_source_lines"], 155869)
        self.assertIsNone(suite_budget.check_monotonic(guard_current, records_current))
        self._assert_source_limit_neighbors_rejected(guard_current, records_current)
        access_records_current = dict(suite_budget.load_budget(), max_test_source_lines=155914, subtest_caps=execution_profiles_current["subtest_caps"])
        self.assertEqual(access_records_current["max_test_source_lines"], 155914)
        self.assertIsNone(suite_budget.check_monotonic(access_records_current, guard_current))
        self._assert_source_limit_neighbors_rejected(access_records_current, guard_current)
        session_artifacts_current = dict(suite_budget.load_budget(), max_test_source_lines=155960, subtest_caps=execution_profiles_current["subtest_caps"])
        self.assertEqual(session_artifacts_current["max_test_source_lines"], 155960)
        self.assertIsNone(suite_budget.check_monotonic(session_artifacts_current, access_records_current))
        self._assert_source_limit_neighbors_rejected(session_artifacts_current, access_records_current)
        lifecycle_queries_current = dict(suite_budget.load_budget(), max_test_source_lines=156009, subtest_caps=execution_profiles_current["subtest_caps"])
        self.assertEqual(lifecycle_queries_current["max_test_source_lines"], 156009)
        self.assertIsNone(suite_budget.check_monotonic(lifecycle_queries_current, session_artifacts_current))
        self._assert_source_limit_neighbors_rejected(lifecycle_queries_current, session_artifacts_current)
        access_transactions_current = dict(suite_budget.load_budget(), max_test_source_lines=156046, subtest_caps=execution_profiles_current["subtest_caps"])
        self.assertEqual(access_transactions_current["max_test_source_lines"], 156046)
        self.assertIsNone(suite_budget.check_monotonic(access_transactions_current, lifecycle_queries_current))
        self._assert_source_limit_neighbors_rejected(access_transactions_current, lifecycle_queries_current)
        context_thread_store_current = dict(suite_budget.load_budget(), max_test_source_lines=156086, subtest_caps=execution_profiles_current["subtest_caps"])
        self.assertEqual(context_thread_store_current["max_test_source_lines"], 156086)
        self.assertIsNone(suite_budget.check_monotonic(context_thread_store_current, access_transactions_current))
        self._assert_source_limit_neighbors_rejected(context_thread_store_current, access_transactions_current)
        manifest_thread_store_current = dict(suite_budget.load_budget(), max_test_source_lines=156132, subtest_caps=execution_profiles_current["subtest_caps"])
        self.assertEqual(manifest_thread_store_current["max_test_source_lines"], 156132)
        self.assertIsNone(suite_budget.check_monotonic(manifest_thread_store_current, context_thread_store_current))
        self._assert_source_limit_neighbors_rejected(manifest_thread_store_current, context_thread_store_current)
        session_transitions_current = dict(suite_budget.load_budget(), max_test_source_lines=156174, subtest_caps=execution_profiles_current["subtest_caps"])
        self.assertEqual(session_transitions_current["max_test_source_lines"], 156174)
        self.assertIsNone(suite_budget.check_monotonic(session_transitions_current, manifest_thread_store_current))
        self._assert_source_limit_neighbors_rejected(session_transitions_current, manifest_thread_store_current)
        self.assertEqual(execution_profiles_current["max_test_source_lines"], 156207)
        self.assertIsNone(suite_budget.check_monotonic(execution_profiles_current, session_transitions_current))
        self._assert_source_limit_neighbors_rejected(execution_profiles_current, session_transitions_current)
        self.assertEqual(split_current["max_test_source_lines"], 156274)
        self.assertIsNone(suite_budget.check_monotonic(split_current, execution_profiles_current))
        self._assert_source_limit_neighbors_rejected(split_current, execution_profiles_current)
        self.assertEqual(wire_current["max_test_source_lines"], 156369)
        self.assertIsNone(suite_budget.check_monotonic(wire_current, split_current))
        self._assert_source_limit_neighbors_rejected(wire_current, split_current)
        self.assertEqual(stream_current["max_test_source_lines"], 156459)
        self.assertIsNone(suite_budget.check_monotonic(stream_current, wire_current))
        self._assert_source_limit_neighbors_rejected(stream_current, wire_current)
        self.assertEqual(invocation_current["max_test_source_lines"], 156558)
        self.assertIsNone(suite_budget.check_monotonic(invocation_current, stream_current))
        self._assert_source_limit_neighbors_rejected(invocation_current, stream_current)
        self.assertIsNone(suite_budget.check_monotonic(command_preflight_current, invocation_current))
        self._assert_source_limit_neighbors_rejected(command_preflight_current, invocation_current)
        self.assertIsNone(suite_budget.check_monotonic(exchange_finalization_current, command_preflight_current))
        self._assert_source_limit_neighbors_rejected(exchange_finalization_current, command_preflight_current)
        self.assertIsNone(suite_budget.check_monotonic(native_recovery_current, exchange_finalization_current))
        self._assert_source_limit_neighbors_rejected(native_recovery_current, exchange_finalization_current)
        self.assertIsNone(suite_budget.check_monotonic(staging_current, native_recovery_current))
        self._assert_source_limit_neighbors_rejected(staging_current, native_recovery_current)
        self.assertIsNone(suite_budget.check_monotonic(antigravity_command_current, staging_current))
        self._assert_source_limit_neighbors_rejected(antigravity_command_current, staging_current)
        self.assertIsNone(suite_budget.check_monotonic(antigravity_stream_current, antigravity_command_current))
        self._assert_source_limit_neighbors_rejected(antigravity_stream_current, antigravity_command_current)
        self.assertIsNone(suite_budget.check_monotonic(provider_generation_current, antigravity_stream_current))
        self._assert_source_limit_neighbors_rejected(provider_generation_current, antigravity_stream_current)
        self.assertIsNone(suite_budget.check_monotonic(retrieval_outcomes_current, provider_generation_current))
        self._assert_source_limit_neighbors_rejected(retrieval_outcomes_current, provider_generation_current)
        self.assertIsNone(suite_budget.check_monotonic(accounting_current, retrieval_outcomes_current))
        self._assert_source_limit_neighbors_rejected(accounting_current, retrieval_outcomes_current)
        self.assertIsNone(suite_budget.check_monotonic(structured_invocation_current, reference_read_current))
        self.assertIsNone(suite_budget.check_monotonic(reference_read_current, context_invocation_current))
        self.assertIsNone(suite_budget.check_monotonic(context_invocation_current, bound_context_current))
        self.assertIsNone(suite_budget.check_monotonic(bound_context_current, profile_observations_current))
        self.assertIsNone(suite_budget.check_monotonic(profile_observations_current, adapter_generations_current))
        self._assert_source_limit_neighbors_rejected(profile_observations_current, adapter_generations_current)
        self.assertIsNone(suite_budget.check_monotonic(adapter_generations_current, binding_lifetime_current))
        self._assert_source_limit_neighbors_rejected(adapter_generations_current, binding_lifetime_current)
        self.assertIsNone(suite_budget.check_monotonic(binding_lifetime_current, accounting_current))
        self._assert_source_limit_neighbors_rejected(binding_lifetime_current, accounting_current)
        invocation_caps = invocation_current["subtest_caps"]
        invocation_id = next(move["new_id"] for move in invocation_moves if move["subtest_cap"])
        for changed_caps in (
            {key: cap for key, cap in invocation_caps.items() if key != invocation_id},
            {**invocation_caps, "tests.unreviewed.Case.test_extra": 0},
            {**invocation_caps, invocation_id: invocation_caps[invocation_id] + 1},
            {**invocation_caps, invocation_id: invocation_caps[invocation_id] - 1},
        ):
            self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic,
                              dict(invocation_current, subtest_caps=changed_caps), stream_current)
        stream_id = stream_moves[0]["new_id"]
        stream_caps = stream_current["subtest_caps"]
        for changed_caps in (
            {key: cap for key, cap in stream_caps.items() if key != stream_id},
            {**stream_caps, "tests.unreviewed.Case.test_extra": 0},
            {**stream_caps, stream_id: 1},
        ):
            self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, dict(stream_current, subtest_caps=changed_caps), wire_current)
        wire_id = next(move["new_id"] for move in wire_moves if move["subtest_cap"])
        wire_caps = wire_current["subtest_caps"]
        for changed_caps in (
            {key: cap for key, cap in wire_caps.items() if key != wire_id},
            {**wire_caps, "tests.unreviewed.Case.test_extra": 0},
            {**wire_caps, wire_id: wire_caps[wire_id] + 1},
            {**wire_caps, wire_id: wire_caps[wire_id] - 1},
        ):
            self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, dict(wire_current, subtest_caps=changed_caps), split_current)
        moved_id = next(move["new_id"] for move in defaults_moves if move["subtest_cap"])
        split_caps = split_current["subtest_caps"]
        for changed_caps in (
            {key: cap for key, cap in split_caps.items() if key != moved_id},
            {**split_caps, "tests.unreviewed.Case.test_extra": 0},
            {**split_caps, moved_id: split_caps[moved_id] + 1},
            {**split_caps, moved_id: split_caps[moved_id] - 1},
        ):
            self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, dict(split_current, subtest_caps=changed_caps), execution_profiles_current)
        for field, value in (("schema_version", 2), ("max_sequential_wall_seconds", 18001), ("max_shard_wall_seconds", 3601)):
            self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, dict(decoder_current, **{field: value}), rules_current)
            self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, dict(split_current, **{field: value}), execution_profiles_current)
            self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, dict(wire_current, **{field: value}), split_current)
            self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, dict(stream_current, **{field: value}), wire_current)
            self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, decoder_current, dict(rules_current, **{field: value}))
        for caps in (
            {key: value for key, value in decoder_current["subtest_caps"].items() if key != zero_id},
            {**decoder_current["subtest_caps"], "tests.unreviewed.Case.test_extra": 0},
            {**decoder_current["subtest_caps"], matrix_id: 2},
            {**decoder_current["subtest_caps"], matrix_id: 0},
        ):
            self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, dict(decoder_current, subtest_caps=caps), rules_current)
        self.assertRaises(suite_budget.BudgetError, suite_budget.check_monotonic, decoder_current, dict(rules_current, subtest_caps={**rules_current["subtest_caps"], matrix_id: 2}))


        from tests import test_codex_backend as canonical_decoder_tests
        from tests import test_backend_reply_decoding as decoder_definitions
        self.assertIs(canonical_decoder_tests.CodexBackendTestCase.test_strict_phase_reply_mapping, decoder_definitions.BackendReplyDecodingDefinitions.test_strict_phase_reply_mapping)
        self.assertIs(canonical_decoder_tests.CodexBackendTestCase.test_retrieval_decoder_rejects_malformed_duplicate_and_schema_errors, decoder_definitions.BackendReplyDecodingDefinitions.test_retrieval_decoder_rejects_malformed_duplicate_and_schema_errors)
        self.assertIs(canonical_decoder_tests.CodexBackendTestCase.test_retrieval_read_explicit_null_is_not_an_omitted_end_sequence, decoder_definitions.BackendReplyDecodingDefinitions.test_retrieval_read_explicit_null_is_not_an_omitted_end_sequence)
        for private, public in (
            ('_readable_context', 'readable_context'),
            ('_retrieval_request', 'retrieval_request'),
            ('_retrieval_action_json', 'retrieval_action_json'),
            ('_needle_search', 'needle_search'),
            ('_read_first_search_hit', 'read_first_search_hit'),
        ):
            self.assertIs(getattr(canonical_decoder_tests, private), getattr(decoder_definitions, public))
        ids = runner_profile.HOSTED_METHOD_IDS
        self.assertEqual(runner_evidence.manifest(ids)["digest"],
                         'sha256:6b7a6aee7e6557ef274a6ff18f3205c75132e0b1e043d3f4de11ff075e104a54')
        self.assertEqual((len(ids), len({value.rsplit(".", 1)[0] for value in ids})), (163, 30))
        cases = tuple(SimpleNamespace(id=lambda identifier=value: identifier) for value in (*ids, *runner_profile.HOSTED_MIXED_SERIAL_IDS, "unreviewed.test"))
        canonical = runner_contracts.CanonicalCollection(cases, (*ids, *runner_profile.HOSTED_MIXED_SERIAL_IDS, "unreviewed.test"))
        serial, scheduled = runner_hosted.hosted_partition(canonical, canonical)
        self.assertEqual((tuple(map(id, serial.tests)), scheduled), (tuple(map(id, cases[len(ids):])), ids))
        isolated = runner_contracts.CanonicalCollection((cases[ids.index(suite_policy.ISOLATED_TEST_ID)],),
                                             (suite_policy.ISOLATED_TEST_ID,))
        serial, scheduled = runner_hosted.hosted_partition(canonical, isolated)
        self.assertEqual((serial.ids, scheduled), ((), (suite_policy.ISOLATED_TEST_ID,)))
        launch_digest = 'sha256:522c56638a26ec136d74f347449718a87b5e9151329a7eb318f397e379cd9956'
        self.assertEqual(runner_evidence.manifest(runner_profile.hosted_launch_order(ids))["digest"], launch_digest)
        self.assertEqual(runner_profile.hosted_launch_order(ids[::-1]), runner_profile.hosted_launch_order(ids))
        self.assertEqual(runner_profile.hosted_launch_order((ids[-1], ids[0])), (ids[0], ids[-1]))
        drift = runner_contracts.CanonicalCollection(cases, (*ids, *runner_profile.HOSTED_MIXED_SERIAL_IDS, ids[0].rsplit(".", 1)[0] + ".test_unreviewed"))
        self.assertRaises(runner_contracts.RunnerError, runner_hosted.hosted_partition, drift, drift)
        real = runner_execution.source_collection_for(self) or runner_collection.discover_canonical()
        from tests.runner.impact import select_impact
        moved_defaults = runner_selection.select(real, modules=["tests.test_codex_backend_defaults"])
        self.assertEqual(moved_defaults.ids, tuple(sorted(move["new_id"] for move in defaults_moves)))
        self.assertTrue(all(move["old_id"] not in real.ids for move in defaults_moves))
        self.assertTrue(all(type(case).__bases__ == (unittest.TestCase,) and type(case).__module__ == "tests.test_codex_backend_defaults" for case in moved_defaults.tests))
        selected_defaults = select_impact(real, paths=("tests/test_codex_backend_defaults.py",))
        self.assertEqual(selected_defaults["focused_manifest"]["ordered_ids"], ["tests.test_codex_backend_defaults.CodexBackendDefaultsTests.test_execution_profiles_resolve_every_source_with_nullable_defaults"])
        self.assertEqual(selected_defaults["affected_manifest"]["ordered_ids"], list(moved_defaults.ids))
        moved_wire = runner_selection.select(real, modules=["tests.test_conversation_retrieval_wire"])
        self.assertEqual(moved_wire.ids, tuple(sorted(move["new_id"] for move in wire_moves)))
        self.assertTrue(all(move["old_id"] not in real.ids for move in wire_moves))
        self.assertTrue(all(type(case).__bases__ == (unittest.TestCase,) and type(case).__module__ == "tests.test_conversation_retrieval_wire" for case in moved_wire.tests))
        selected_wire = select_impact(real, paths=("tests/test_conversation_retrieval_wire.py",))
        self.assertEqual(selected_wire["focused_manifest"]["ordered_ids"], [next(move["new_id"] for move in wire_moves if move["old_id"].endswith(".test_retrieval_schema_uses_supported_strict_object_subset"))])
        self.assertEqual(selected_wire["affected_manifest"]["ordered_ids"], list(moved_wire.ids))
        moved_streaming = runner_selection.select(real, modules=["tests.test_codex_adapter_streaming"])
        retained_streaming = runner_selection.select(real, modules=["tests.test_adapter_streaming"])
        self.assertEqual(moved_streaming.ids, tuple(sorted(move["new_id"] for move in stream_moves)))
        self.assertEqual(len(retained_streaming.ids), 3)
        antigravity_stream_ids = sorted(move["new_id"] for move in antigravity_stream_moves)
        self.assertTrue(all(move["old_id"] not in real.ids for move in antigravity_stream_moves))
        self.assertEqual(len(antigravity_stream_ids), 29)
        for case_type, count in ((AntigravityStreamValidationTests, 14), (AntigravityStreamDeliveryTests, 15)):
            module = case_type.__module__
            moved = runner_selection.select(real, modules=[module])
            expected = tuple(case for case in antigravity_stream_ids if case.startswith(f"{module}."))
            self.assertEqual((moved.ids, len(moved.ids)), (expected, count))
            self.assertTrue(all(type(case) is case_type and getattr(case, case._testMethodName).__module__ == module for case in moved.tests))
        antigravity_affected_ids = sorted((*antigravity_stream_ids, *(case for case in retained_streaming.ids if "provider_invocation" not in case)))
        self.assertEqual(len(antigravity_affected_ids), 31)
        stream_selection = select_impact(real, paths=("src/story_writing_agents/antigravity_stream_observation.py",))
        self.assertEqual(stream_selection["focused_manifest"]["ordered_ids"], antigravity_stream_ids)
        self.assertEqual(stream_selection["affected_manifest"]["ordered_ids"], antigravity_affected_ids)
        generation = select_impact(real, paths=("src/story_writing_agents/provider_generation_adapters.py",))
        self.assertEqual(generation["focused_manifest"]["count"], 3)
        self.assertEqual(generation["affected_manifest"]["count"], 9)
        self.assertEqual(sum(provider_generation_current["subtest_caps"][cid] for cid in generation["affected_manifest"]["ordered_ids"]), 9)
        retrieval = select_impact(real, paths=("src/story_writing_agents/provider_retrieval_handler_outcomes.py",))
        self.assertEqual(retrieval["focused_manifest"]["ordered_ids"], ["tests.test_codex_backend.CodexBackendTestCase.test_retrieval_exact_initial_continuation_payload_rules_schema_and_frame"])
        self.assertEqual(retrieval["affected_manifest"]["count"], 8)
        self.assertEqual(sum(retrieval_outcomes_current["subtest_caps"][cid] for cid in retrieval["affected_manifest"]["ordered_ids"]), 9)
        accounting = select_impact(real, paths=("src/story_writing_agents/provider_invocation_accounting.py",))
        self.assertEqual(
            accounting["focused_manifest"]["ordered_ids"],
            ["tests.test_codex_backend.CodexBackendTestCase.test_context_sink_records_prepared_submitted_and_cancellation_once"],
        )
        self.assertEqual(accounting["affected_manifest"]["count"], 12)
        self.assertEqual(
            sum(accounting_current["subtest_caps"][cid] for cid in accounting["affected_manifest"]["ordered_ids"]),
            8,
        )
        binding_lifetime = select_impact(real, paths=("src/story_writing_agents/provider_binding_call_lifetime.py",))
        self.assertEqual(
            binding_lifetime["focused_manifest"]["ordered_ids"],
            ["tests.test_codex_backend.CodexBackendTestCase.test_retirement_defers_exact_cleanup_for_each_bound_operation"],
        )
        self.assertEqual(binding_lifetime["affected_manifest"]["count"], 6)
        self.assertEqual(
            sum(binding_lifetime_current["subtest_caps"][cid] for cid in binding_lifetime["affected_manifest"]["ordered_ids"]),
            14,
        )
        binding_test_definitions = select_impact(real, paths=("tests/test_provider_binding_call_lifetime.py",))
        self.assertEqual(binding_test_definitions["focused_manifest"]["count"], 4)
        self.assertEqual(binding_test_definitions["affected_manifest"]["count"], 90)

        adapter_generations = select_impact(real, paths=("src/story_writing_agents/provider_adapter_generations.py",))
        self.assertEqual(
            adapter_generations["focused_manifest"]["ordered_ids"],
            ["tests.test_codex_backend.CodexBackendTestCase.test_hot_same_provider_profile_applies_now_and_reuses_thread"],
        )
        self.assertEqual(adapter_generations["affected_manifest"]["count"], 7)
        self.assertEqual(
            sum(binding_lifetime_current["subtest_caps"][cid] for cid in adapter_generations["affected_manifest"]["ordered_ids"]),
            9,
        )
        generation_test_definitions = select_impact(real, paths=("tests/test_provider_adapter_generations.py",))
        self.assertEqual(generation_test_definitions["focused_manifest"]["count"], 2)
        self.assertEqual(generation_test_definitions["affected_manifest"]["count"], 90)

        profile_observations = select_impact(real, paths=("src/story_writing_agents/provider_execution_profile_observations.py",))
        self.assertEqual(
            profile_observations["focused_manifest"]["ordered_ids"],
            ["tests.test_codex_backend.CodexBackendTestCase.test_hot_same_provider_profile_applies_now_and_reuses_thread"],
        )
        self.assertEqual(profile_observations["affected_manifest"]["count"], 4)
        self.assertEqual(
            sum(binding_lifetime_current["subtest_caps"][cid] for cid in profile_observations["affected_manifest"]["ordered_ids"]),
            0,
        )
        bound_context_module = "tests.bound_context_provider_cases"
        bound_context_id = (
            "tests.test_codex_backend.CodexBackendTestCase."
            "test_contextual_submitted_failure_taints_only_exact_context_thread"
        )
        definition_cases = runner_selection.select(real, modules=[bound_context_module])
        self.assertEqual(definition_cases.ids, (bound_context_id,))
        case = definition_cases.tests[0]
        method = getattr(case, case._testMethodName)
        self.assertEqual(method.__qualname__, f"BoundContextProviderCaseDefinitions.{case._testMethodName}")
        bound_definition_ids = runner_selection.selected_ids(
            real, modules=["tests.test_codex_backend", "tests.test_suite_budget"],
        )
        self.assertEqual(len(bound_definition_ids), 90)
        bound_definition_impact = select_impact(real, paths=("tests/bound_context_provider_cases.py",))
        self.assertEqual(bound_definition_impact["focused_manifest"]["ordered_ids"], list(bound_definition_ids))
        self.assertEqual(bound_definition_impact["affected_manifest"]["ordered_ids"], list(bound_definition_ids))
        bound_context_impact = select_impact(real, paths=("src/story_writing_agents/provider_bound_context.py",))
        self.assertEqual(bound_context_impact["focused_manifest"]["ordered_ids"], [bound_context_id])
        bound_context_affected = bound_context_impact["affected_manifest"]["ordered_ids"]
        self.assertEqual(len(bound_context_affected), 12)
        self.assertEqual(sum(profile_observations_current["subtest_caps"][cid] for cid in bound_context_affected), 10)
        physical_context = select_impact(real, paths=("src/story_writing_agents/provider_context_invocation.py",))
        self.assertEqual(physical_context["focused_manifest"]["ordered_ids"], [
            "tests.test_codex_invocation_files.CodexInvocationFilesTests.test_conversation_projects_exact_payload_schema_and_no_artifacts",
        ])
        physical_affected = physical_context["affected_manifest"]["ordered_ids"]
        self.assertEqual(physical_affected, bound_context_affected)
        self.assertEqual(sum(context_invocation_current["subtest_caps"][cid] for cid in physical_affected), 10)
        reference_read_impact = select_impact(real, paths=("src/story_writing_agents/provider_reference_read_lifecycle.py",))
        self.assertEqual(reference_read_impact["focused_manifest"]["ordered_ids"], [
            "tests.test_codex_backend.CodexBackendTestCase.test_local_reference_handler_failures_are_durable",
        ])
        reference_read_ids = reference_read_impact["affected_manifest"]["ordered_ids"]
        self.assertEqual(len(reference_read_ids), 14)
        self.assertEqual(sum(reference_read_current["subtest_caps"][cid] for cid in reference_read_ids), 22)
        structured_impact = select_impact(real, paths=("src/story_writing_agents/provider_structured_invocation.py",))
        self.assertEqual(structured_impact["focused_manifest"]["ordered_ids"], [
            "tests.test_antigravity_parity.AntigravityParityTests.test_schema_failure_repairs_then_falls_back_with_runtime_identity",
            "tests.test_codex_backend.CodexBackendTestCase.test_provider_invocation_error_preserves_adapter_cause_and_taint",
        ])
        structured_ids = structured_impact["affected_manifest"]["ordered_ids"]
        self.assertEqual(len(structured_ids), 74)
        self.assertEqual(sum(structured_invocation_current["subtest_caps"][cid] for cid in structured_ids), 96)

        self.assertTrue(all(move["old_id"] not in real.ids for move in stream_moves))
        self.assertTrue(all(type(case).__bases__ == (unittest.IsolatedAsyncioTestCase,) and type(case).__module__ == "tests.test_codex_adapter_streaming" for case in moved_streaming.tests))
        for path, expected in (
            ("tests/test_adapter_streaming.py", list(retained_streaming.ids)),
            ("tests/test_codex_adapter_streaming.py", list(moved_streaming.ids)),
        ):
            selected = select_impact(real, paths=(path,))
            self.assertEqual(selected["focused_manifest"]["ordered_ids"], expected)
            self.assertEqual(selected["affected_manifest"]["ordered_ids"], expected)
        budget_policy_ids = runner_selection.select(real, modules=["tests.test_suite_budget"]).ids
        self.assertEqual(len(budget_policy_ids), 4)
        shared_ids = sorted((*retained_streaming.ids, *moved_streaming.ids, *antigravity_stream_ids))
        shared = select_impact(real, paths=("tests/adapter_streaming_fixture.py",))
        self.assertEqual(shared["focused_manifest"]["ordered_ids"], shared_ids)
        self.assertEqual(shared["affected_manifest"]["ordered_ids"], sorted((*shared_ids, *budget_policy_ids)))
        agy_fixture_ids = sorted((*retained_streaming.ids, *antigravity_stream_ids, *budget_policy_ids))
        agy_fixture = select_impact(real, paths=("tests/antigravity_streaming_fixture.py",))
        self.assertEqual(agy_fixture["focused_manifest"]["ordered_ids"], agy_fixture_ids)
        self.assertEqual(agy_fixture["affected_manifest"]["ordered_ids"], agy_fixture_ids)
        moved_invocation = runner_selection.select(real, modules=["tests.test_codex_invocation_files"])
        retained_codex = runner_selection.select(real, modules=["tests.test_codex_backend"])
        self.assertEqual(moved_invocation.ids, tuple(sorted(move["new_id"] for move in invocation_moves)))
        self.assertEqual(len(retained_codex.ids), 86)
        self.assertTrue(all(move["old_id"] not in real.ids for move in invocation_moves))
        for case in moved_invocation.tests:
            fixture, async_case = type(case).__bases__
            self.assertEqual(fixture.__module__, "tests.codex_native_process_fixture")
            self.assertIs(async_case, unittest.IsolatedAsyncioTestCase)
            self.assertEqual(type(case).__module__, "tests.test_codex_invocation_files")
            self.assertEqual(getattr(case, case._testMethodName).__module__, type(case).__module__)
        invocation_ids = list(moved_invocation.ids)
        shared_fixture_ids = sorted((*retained_codex.ids, *invocation_ids, *runner_selection.selected_ids(real, modules=["tests.test_suite_budget"])))
        process_peer = "tests.test_provider_process_streaming.TestProviderProcessStreaming.test_non_streaming_runs_normally"
        native_cases = "tests.test_codex_invocation_files.CodexInvocationFilesTests."
        defaults_cases = "tests.test_codex_backend_defaults.CodexBackendDefaultsTests."
        backend_cases = "tests.test_codex_backend.CodexBackendTestCase."
        preflight_focused = sorted((
            native_cases + "test_binding_stages_isolated_agents_and_turn_omits_static_prompt",
            defaults_cases + "test_optional_model_and_reasoning_argv_are_exact_for_fresh_and_resume",
            defaults_cases + "test_agent_model_and_reasoning_argv_are_exact",
        ))
        preflight_affected = sorted((
            *preflight_focused,
            native_cases + "test_missing_executable_nonzero_and_malformed_output_fail_cleanly",
            backend_cases + "test_binding_stages_immutable_bytes_after_all_source_paths_become_unsafe",
            backend_cases + "test_hot_same_provider_profile_applies_now_and_reuses_thread",
            defaults_cases + "test_model_precedence_and_explicit_none_are_exact",
            defaults_cases + "test_agent_model_and_reasoning_precedence_uses_immutable_snapshot",
            defaults_cases + "test_backend_runtime_and_prompts_share_one_snapshot_until_next_lifecycle",
        ))
        for path, focused, affected in (
            ("src/story_writing_agents/codex_command_preflight.py", preflight_focused, preflight_affected),
            ("tests/test_codex_invocation_files.py", invocation_ids, invocation_ids),
            ("src/story_writing_agents/codex_invocation_files.py", invocation_ids, sorted((*invocation_ids, process_peer))),
            ("tests/codex_native_process_fixture.py", shared_fixture_ids, shared_fixture_ids),
        ):
            selected = select_impact(real, paths=(path,))
            self.assertEqual(selected["focused_manifest"]["ordered_ids"], focused)
            self.assertEqual(selected["affected_manifest"]["ordered_ids"], affected)
        self.assertTrue(all(move["old_id"] not in real.ids for move in exchange_finalization_moves))
        for module, count in (
            ("test_agent_exchange_capture_lifecycle", 6),
            ("test_agent_exchange_provider_diagnostics", 7),
        ):
            moved = runner_selection.select(real, modules=[f"tests.{module}"])
            expected = sorted(move["new_id"] for move in exchange_finalization_moves
                              if move["destination"] == f"tests/{module}.py")
            self.assertEqual(len(expected), count)
            self.assertEqual(moved.ids, tuple(expected))
            selected = select_impact(real, paths=(f"tests/{module}.py",))
            self.assertEqual(selected["focused_manifest"]["ordered_ids"], expected)
            self.assertEqual(selected["affected_manifest"]["ordered_ids"], expected)
        definitions = (
            ("tests.lead_preparation.test_specialist_advisory_scope", "SpecialistAdvisoryScopeCases",
             "tests.test_specialist_routing_runtime.SpecialistRoutingRuntimeTests.test_provider_free_advisory_requires_scope_and_separate_confirmation",
             "tests.lead_fixtures.specialist_routing.SpecialistRoutingRuntimeCase"),
            ("tests.lead_preparation.test_ordinary_turn_outcomes", "OrdinaryLeadTurnOutcomeCases",
             "tests.test_lead_orchestration.LeadOutcomeAndProposalTests.test_all_five_outcomes_have_exact_visible_next_actions",
             "tests.lead_fixtures.runtime_setup.LeadOrchestrationTestCase"),
        )
        for module, carrier_name, identifier, fixture_name in definitions:
            selected = runner_selection.select(real, modules=[module])
            self.assertEqual(selected.ids, (identifier,))
            case = selected.tests[0]
            concrete = type(case)
            carrier = getattr(sys.modules[module], carrier_name)
            method = identifier.rpartition(".")[2]
            self.assertFalse(issubclass(carrier, unittest.TestCase))
            self.assertIs(concrete.__dict__[method], carrier.__dict__[method])
            self.assertIs(getattr(case, method).__func__, carrier.__dict__[method])
            self.assertEqual(getattr(case, method).__module__, module)
            self.assertEqual(concrete.__module__, identifier.rsplit(".", 2)[0])
            fixture, = concrete.__bases__
            self.assertEqual(f"{fixture.__module__}.{fixture.__name__}", fixture_name)
            self.assertEqual(concrete.__mro__, (concrete, *fixture.__mro__))
            self.assertEqual(runner_selection.selected_ids(real, ids=[identifier]), (identifier,))
            old_module = concrete.__module__
            self.assertEqual(runner_selection.selected_ids(real, modules=[old_module]),
                             tuple(value for value in real.ids if value.rsplit(".", 2)[0] == old_module))
            owner = {"name": module, "boundary": "existing-cases",
                     "paths": [module.replace(".", "/") + ".py"],
                     "focused_ids": [self.id()], "affected_ids": [self.id()]}
            with patch("tests.runner.impact.load_owners", return_value={"owners": [owner]}):
                self.assertRaisesRegex(runner_contracts.RunnerError, "definition module",
                                       runner_selection.select, real, modules=[module])
        self.assertRaisesRegex(runner_contracts.RunnerError, "unknown test modules",
                               runner_selection.select, real, modules=["tests.module_graph_fixture"])
        real_serial, real_scheduled = runner_hosted.hosted_partition(real, real)
        self.assertEqual(real_scheduled, ids)
        self.assertEqual(set(real_serial.ids) & set(runner_profile.HOSTED_MIXED_SERIAL_IDS), set(runner_profile.HOSTED_MIXED_SERIAL_IDS))
        self.assertFalse(set(real_scheduled) & set(runner_profile.HOSTED_MIXED_SERIAL_IDS))
        self.assertIn("tests.lead_preparation.test_response_rules.SpecialistRoutingTests.test_scope_and_review_basis_codecs_are_closed_and_round_trip", real_serial.ids)
        self.assertEqual(len(runner_profile.HOSTED_MIXED_SERIAL_IDS), 19)
        missing = runner_contracts.CanonicalCollection(cases, (*ids[:-1], *runner_profile.HOSTED_MIXED_SERIAL_IDS))
        self.assertRaises(runner_contracts.RunnerError, runner_hosted.hosted_partition, missing, missing)
        args = SimpleNamespace(command="run", hosted_methods='audited-163-v11', hosted_deadline_epoch=time.time()+3500,
                               shard="1/5", id=[], module=[], tier=[], failfast=False, json="report.json")
        env = dict(GITHUB_ACTIONS="true", TEST_RUN_DEADLINE=str(time.time()+4500),
                   TEST_JOB_DEADLINE=str(time.time()+4500))
        with patch.dict(os.environ, env, clear=True), patch.object(sys, "platform", "linux"):
            options = runner_hosted.hosted_options(args)
            self.assertEqual(options["cleanup_deadline_epoch"], args.hosted_deadline_epoch + 30)
            self.assertEqual((options["deadline"], options["started"]),
                             (options["work_deadline"] + 30, options["work_deadline"] - 3600))
            for cpu, expected in ((1, 1), (2, 2), (4, 4), (8, 4)):
                with (patch.object(os, "cpu_count", return_value=cpu),
                      patch.object(os, "sched_getaffinity", return_value=set(range(cpu)), create=True)):
                    self.assertEqual(runner_hosted.hosted_options(args)["capacity"], expected)
            for key, value in (("id", [ids[0]]), ("module", ["tests"]), ("tier", ["fast"]), ("failfast", True),
                ("shard", None), ("json", None), ("hosted_methods", "unknown"), ("hosted_deadline_epoch", time.time()),
                ("hosted_deadline_epoch", time.time()+3601), ("hosted_deadline_epoch", float("nan"))):
                self.assertRaises(runner_contracts.RunnerError, runner_hosted.hosted_options,
                                  SimpleNamespace(**{**vars(args), key: value}))
            for forbidden in ("STORY_WIRE_TEST_CAPTURE_DIR", "STORY_LOCAL_CI_TOKEN"):
                with patch.dict(os.environ, {forbidden: ""}):
                    self.assertRaises(runner_contracts.RunnerError, runner_hosted.hosted_options, args)
            with patch.dict(os.environ, GITHUB_ACTIONS="false"):
                self.assertRaises(runner_contracts.RunnerError, runner_hosted.hosted_options, args)
        self.assertIsNone(runner_hosted.hosted_options(SimpleNamespace(hosted_methods=None, hosted_deadline_epoch=None)))
        with tempfile.TemporaryDirectory() as directory:
            report_path = str(Path(directory) / "result.json")
            owned = runner_hosted.hosted_artifacts(report_path)
            self.assertEqual((owned.name, owned.stat().st_mode & 0o777), ("result.json.methods", 0o700))
            marker = owned / "worker.log"; marker.write_text("retained")
            self.assertRaises(FileExistsError, runner_hosted.hosted_artifacts, report_path)
            self.assertEqual(marker.read_text(), "retained")

        from tests.runner import impact as definition_impact
        artifact_class = "tests.test_artifact_proposal_orchestration.ArtifactProposalOrchestrationTests."
        definition_groups_1047 = {
            "tests.lead_preparation.test_artifact_candidate_creation": (
                artifact_class + 'test_incomplete_core_workflow_is_reviewed_only_after_confirmation',
                artifact_class + 'test_search_read_answer_can_add_an_artifact_candidate',
            ),
            "tests.lead_preparation.test_artifact_candidate_revisions": (
                artifact_class + 'test_core_conflict_revision_and_manuscript_atomic_rename_workflows',
                artifact_class + 'test_revised_artifact_intent_replaces_candidate_in_the_same_step',
                artifact_class + 'test_stale_confirmation_and_revision_require_fresh_authority',
            ),
            "tests.lead_preparation.test_artifact_authority_boundaries": (
                artifact_class + 'test_ordinary_discussion_captures_current_state_without_specialists',
                artifact_class + 'test_provider_authority_fields_fall_back_without_candidate',
            ),
            "tests.lead_preparation.test_artifact_review_recovery": (
                artifact_class + 'test_missing_synthesis_message_fails_and_reviewed_restart_has_no_authority',
            ),
            "tests.lead_preparation.test_specialist_response_semantics": (
                'tests.test_specialist_orchestration_execution.SpecialistExecutionAcceptanceTests.test_invalid_specialist_shapes_repair_then_fall_back_without_halting',
                'tests.test_specialist_orchestration_execution.SpecialistExecutionAcceptanceTests.test_specialist_and_synthesis_validation_details_are_private_and_correlated',
                'tests.test_specialist_orchestration_execution.SpecialistExecutionAcceptanceTests.test_synthesis_contract_is_semantic_and_runtime_derives_attribution',
                'tests.test_specialist_orchestration_execution.SpecialistExecutionAcceptanceTests.test_synthesis_derives_non_supporting_inputs_locally',
            ),
            "tests.test_backend_reply_decoding": (
                'tests.test_codex_backend.CodexBackendTestCase.test_strict_phase_reply_mapping',
                'tests.test_codex_backend.CodexBackendTestCase.test_retrieval_decoder_rejects_malformed_duplicate_and_schema_errors',
                'tests.test_codex_backend.CodexBackendTestCase.test_retrieval_read_explicit_null_is_not_an_omitted_end_sequence',
            ),
        }
        definition_selections = {}
        for module, expected_ids in definition_groups_1047.items():
            selected = runner_selection.select(real, modules=(module,))
            self.assertEqual(selected.ids, tuple(value for value in real.ids if value in expected_ids))
            definition_selections[module] = selected
        runner_selection.validate_definition_modules(real, tuple(definition_groups_1047))
        recovery_module = "tests.test_antigravity_recovery_evidence"
        recovery_selection = runner_selection.select(real, modules=[recovery_module])
        recovery_ids = sorted(move["new_id"] for move in native_recovery_moves)
        self.assertEqual(recovery_selection.ids, tuple(recovery_ids))
        self.assertTrue(all(move["old_id"] not in real.ids for move in native_recovery_moves))
        self.assertTrue(all(type(case).__module__ == recovery_module
                            and getattr(case, case._testMethodName).__module__ == recovery_module
                            for case in recovery_selection.tests))
        recovery_paths = select_impact(real, paths=("src/story_writing_agents/antigravity_native_recovery.py",))
        self.assertEqual(recovery_paths["focused_manifest"]["ordered_ids"], recovery_ids)
        recovery_caller = "tests.test_antigravity_stream_delivery.AntigravityStreamDeliveryTests.test_antigravity_adapter_sanitizes_stdout_in_streamed_response_diagnostic"
        self.assertEqual(recovery_paths["affected_manifest"]["count"], 12)
        self.assertIn(recovery_caller, recovery_paths["affected_manifest"]["ordered_ids"])
        recovery_fixture = select_impact(real, paths=("tests/fixtures/antigravity_recovered_finish/terminal.json",))
        self.assertEqual(recovery_fixture["focused_manifest"]["count"], 10)
        self.assertTrue(set(recovery_ids) <= set(recovery_fixture["affected_manifest"]["ordered_ids"]))
        staging_module = "tests.test_antigravity_definition_staging"
        staging = runner_selection.select(real, modules=[staging_module])
        staging_ids = sorted(move["new_id"] for move in staging_moves)
        self.assertEqual(staging.ids, tuple(staging_ids))
        self.assertTrue(all(move["old_id"] not in real.ids for move in staging_moves))
        self.assertTrue(all(type(case).__module__ == staging_module
                            and getattr(case, case._testMethodName).__module__ == staging_module
                            for case in staging.tests))
        definition_source = "src/story_writing_agents/antigravity_agent_definition.py"
        directory_source = "src/story_writing_agents/antigravity_state_directories.py"
        definition_selection = select_impact(real, paths=(definition_source,))
        self.assertEqual(definition_selection["focused_manifest"]["ordered_ids"], staging_ids)
        self.assertEqual(definition_selection["affected_manifest"]["count"], 10)
        definition_affected = definition_selection["affected_manifest"]["ordered_ids"]
        for method_name in ("test_backend_routes_and_persists_antigravity_conversation_id",
                       "test_provider_bindings_use_captured_composed_bytes_after_source_mutation",
                       "test_antigravity_adapter_streams_events_and_parses_stream_json"):
            self.assertEqual(sum(case.endswith("." + method_name) for case in definition_affected), 1)
        directory_selection = select_impact(real, paths=(directory_source,))
        self.assertEqual(directory_selection["focused_manifest"]["ordered_ids"], [
            case for case in staging_ids
            if not case.endswith(".test_contextual_binding_shared_semantics_and_vendor_constraints_are_exact")
        ])
        self.assertEqual(directory_selection["affected_manifest"]["count"], 7)
        self.assertTrue(all(move["old_id"] not in real.ids for move in antigravity_command_moves))
        command_module = "tests.test_antigravity_command"
        options_module = "tests.test_antigravity_invocation_options"
        runner_selection.validate_definition_modules(real, (command_module, options_module))
        antigravity_cap_map = antigravity_stream_current["subtest_caps"]
        for module, count, cap, affected_count, affected_cap in (
            (command_module, 4, 29, 8, 35),
            (options_module, 1, 0, 3, 0),
        ):
            moved_ids = tuple(sorted(move["new_id"] for move in antigravity_command_moves if move["new_id"].startswith(f"{module}.")))
            self.assertEqual(runner_selection.select(real, modules=[module]).ids, moved_ids)
            self.assertEqual((len(moved_ids), sum(antigravity_cap_map[test_id] for test_id in moved_ids)), (count, cap))
            selected = select_impact(real, paths=(f"src/story_writing_agents/{module.removeprefix('tests.test_')}.py",))
            self.assertEqual(selected["focused_manifest"]["ordered_ids"], list(moved_ids))
            affected_ids = selected["affected_manifest"]["ordered_ids"]
            self.assertEqual((len(affected_ids), sum(antigravity_cap_map[test_id] for test_id in affected_ids)), (affected_count, affected_cap))
        rules_ids = select_impact(real, paths=("src/story_writing_agents/antigravity_rules.py",))["affected_manifest"]["ordered_ids"]
        self.assertEqual((len(rules_ids), sum(antigravity_cap_map[test_id] for test_id in rules_ids)), (8, 33))
        self.assertIn(f"{command_module}.AntigravityCommandTests.test_command_stdin_paths_and_fresh_resume_results_are_exact", rules_ids)
        shared_fixture = select_impact(real, paths=("tests/antigravity_adapter_fixture.py",))
        self.assertEqual(shared_fixture["affected_manifest"]["count"], 42)
        self.assertTrue(set(staging_ids) <= set(shared_fixture["affected_manifest"]["ordered_ids"]))
        self.assertEqual(sum(".AntigravitySchemaRenderingTests." in case
                             for case in shared_fixture["affected_manifest"]["ordered_ids"]), 4)
        from tests.lead_preparation.test_specialist_response_semantics import SpecialistResponseSemanticsDefinitions
        from tests.test_specialist_orchestration_execution import SpecialistExecutionAcceptanceTests, ExecutionInputFixture
        from tests.lead_fixtures.runtime_setup import LeadOrchestrationTestCase
        semantic_module = "tests.lead_preparation.test_specialist_response_semantics"
        semantic = definition_selections[semantic_module]
        self.assertFalse(issubclass(SpecialistResponseSemanticsDefinitions, unittest.TestCase))
        self.assertEqual(SpecialistExecutionAcceptanceTests.__bases__, (ExecutionInputFixture, LeadOrchestrationTestCase))
        for case in semantic.tests:
            method = case._testMethodName
            self.assertIs(SpecialistExecutionAcceptanceTests.__dict__[method], SpecialistResponseSemanticsDefinitions.__dict__[method])
            self.assertIs(getattr(case, method).__func__, SpecialistResponseSemanticsDefinitions.__dict__[method])
            self.assertEqual(getattr(case, method).__module__, semantic_module)
        revision_module = "tests.lead_preparation.test_artifact_candidate_revisions"
        revision = definition_selections[revision_module]
        missing_revision = runner_contracts.CanonicalCollection(revision.tests[:-1], revision.ids[:-1])
        self.assertRaises(runner_contracts.RunnerError, runner_selection.validate_definition_modules,
                          missing_revision, (revision_module,))
        original_owners = definition_impact.load_owners(runner_collection.load_inventory())
        duplicate_owners = {**original_owners, "owners": [dict(row) for row in original_owners["owners"]]}
        duplicate_row = next(row for row in duplicate_owners["owners"] if row["name"] == revision_module)
        duplicate_row["focused_ids"] = duplicate_row["affected_ids"] = [revision.ids[0], revision.ids[0]]
        with patch.object(definition_impact, "load_owners", return_value=duplicate_owners):
            self.assertRaises(runner_contracts.RunnerError, runner_selection.select, real, modules=(revision_module,))
        first, second = revision.tests[:2]
        with patch.object(second, second._testMethodName, getattr(first, first._testMethodName)):
            self.assertRaises(runner_contracts.RunnerError, runner_selection.validate_definition_modules,
                              revision, (revision_module,))
        creation = definition_selections["tests.lead_preparation.test_artifact_candidate_creation"].tests[0]
        foreign_function = getattr(creation, creation._testMethodName).__func__
        with patch.object(second, second._testMethodName, foreign_function.__get__(second, type(second))):
            self.assertRaises(runner_contracts.RunnerError, runner_selection.validate_definition_modules,
                              revision, (revision_module,))

    def test_exact_duration_boundary_passes_and_excess_fails(self) -> None:
        for total, maximum in ((1080, 540), (18000, 3600)):
            self.budget.update(max_sequential_wall_seconds=total, max_shard_wall_seconds=maximum)
            with tempfile.TemporaryDirectory() as directory:
                paths = self._reports(directory, (2, 0))
                reconciliation = runner_reporting.reconcile_reports(paths)
                with patch.object(suite_budget, "reconcile_reports", return_value=reconciliation), patch(
                    "builtins.open", side_effect=AssertionError("budget reopened validated reports") ):
                    self.assertIs(suite_budget.check_reports(self.budget, paths), reconciliation)
                    reconciliation["shards"][0]["durations"]["wall_seconds"] = maximum + .001
                    with self.assertRaisesRegex(suite_budget.BudgetError, "shard 1/5"):
                        suite_budget.check_reports(self.budget, paths)
                    self.assertIs( suite_budget.check_reports(self.budget, paths, allow_wall_time_overage=True), reconciliation)
                    reconciliation["shards"][0]["durations"]["wall_seconds"] = maximum
                    reconciliation["durations"]["sequential_equivalent_wall_seconds"] = total + .001
                    with self.assertRaisesRegex(suite_budget.BudgetError, "sequential"):
                        suite_budget.check_reports(self.budget, paths)
                    self.assertIs( suite_budget.check_reports(self.budget, paths, allow_wall_time_overage=True), reconciliation)

        with tempfile.TemporaryDirectory() as directory:
            paths = self._reports(directory, (0, 0)); rows = self.report_data
            metadata = {"version": 1, "mode": "hosted-whole-method", "profile": 'audited-163-v11',
                        "profile_manifest": runner_evidence.manifest(runner_profile.HOSTED_METHOD_IDS),
                        "capacity": 2, "peak_children": 0, "work_deadline_epoch": 10000.0,
                        "cleanup_deadline_epoch": 10030.0, "launch_manifest": runner_evidence.manifest(()),
                        "launched": 0, "completed": 0, "cleanup_complete": True, "infrastructure_errors": [],
                        "timing_basis": "observed-parent-wall-and-test-sum", "scheduled_manifest": runner_evidence.manifest(())}
            for path, row in zip(paths, rows):
                row["execution"] = {**metadata, "serial_manifest": row["selection_manifest"]}
                if row["selection"]["shard"] == "5/5":
                    row["execution"].update(serial_manifest=runner_evidence.manifest(()), peak_children=1,
                        scheduled_manifest=row["selection_manifest"], launch_manifest=row["selection_manifest"],
                        launched=1, completed=1)
                Path(path).write_text(json.dumps(row))
            result = runner_reporting.reconcile_reports(paths)
            self.assertEqual(result["durations"], {"sequential_equivalent_wall_seconds": 18000,
                             "concurrent_wall_seconds": 3600, "cumulative_test_seconds": 5})
            self.assertEqual(result["execution"]["timing_basis"], "sum-observed-shard-walls")
            for key, value in (("capacity", True), ("cleanup_complete", False), ("profile", "unknown"), ("timing_basis",
                "serial_prediction"), ("extra", 1), ("completed", 1), ("cleanup_deadline_epoch", 10031),
                ("work_deadline_epoch", True), ("launch_manifest", runner_evidence.manifest((runner_profile.HOSTED_PRIORITY_ID,)))):
                self.assertRaises(runner_contracts.RunnerError, runner_profile.execution_metadata,
                                  {**rows[0], "execution": {**rows[0]["execution"], key: value}})
            rows[0].pop("execution"); Path(paths[0]).write_text(json.dumps(rows[0]))
            self.assertRaisesRegex(runner_contracts.RunnerError, "mixed execution", runner_reporting.reconcile_reports, paths)

    def test_subtest_expansion_and_incomplete_shards_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = self._reports(directory, (3, 0))
            result = runner_reporting.reconcile_reports(list(reversed(paths)))
            self.assertEqual(runner_cli.parser().parse_args(["reconcile", *paths]).reports, paths)
            self.assertRaises(SystemExit, runner_cli.parser().parse_args, ["reconcile", *paths[:-1]])
            self.assertEqual(tuple(row["canonical_id"] for row in result["tests"]), tuple(self.budget["subtest_caps"]))
            self.assertEqual(result["durations"], {"sequential_equivalent_wall_seconds": 1080, "concurrent_wall_seconds": 540, "cumulative_test_seconds": 5})
            self.assertEqual(suite_policy.partition_full_shards(tuple(reversed(self.budget["subtest_caps"])), self.weights), self.plan)
            self.assertEqual(self.plan.shard_ids, (("a.test",), ("b.test",), ("c.control",), ("d.control",),
                                                 (suite_policy.ISOLATED_TEST_ID,)))
            self.assertEqual(self.plan.shard_weights_microseconds, (9, 5, 3, 2, 2))
            self.assertRaisesRegex(suite_policy.SuitePolicyError, "missing the isolated test ID",
                suite_policy.partition_full_shards, tuple(self.budget["subtest_caps"])[:-1], self.weights)
            self.assertRaisesRegex(suite_policy.SuitePolicyError, "duplicate IDs", suite_policy.partition_full_shards,
                (*self.budget["subtest_caps"], suite_policy.ISOLATED_TEST_ID), self.weights)
            def write(change=lambda values: None):
                values = json.loads(json.dumps(self.report_data))
                change(values)
                for path, value in zip(paths, values): Path(path).write_text(json.dumps(value), encoding="utf-8")
            def reject(change, message):
                write(change)
                self.assertRaisesRegex(runner_contracts.RunnerError, message, runner_reporting.reconcile_reports, paths)
            def status_change(values, kind, reason):
                row = values[0]
                row["tests"][0]["status"] = "failed"
                row["totals"].update(passed=0, failures=1)
                row.update(details=[{"canonical_id": "a.test", "kind": kind, "reason": reason}], outcome="failure")
            def omit_case(values):
                for row in values: row["canonical_manifest"] = runner_evidence.manifest(("a.test", "b.test", "c.control"))
            def selection_change(values, identifier):
                values[0]["selection_manifest"] = runner_evidence.manifest((identifier,))
                values[0]["tests"][0]["canonical_id"] = identifier
            with self.assertRaisesRegex(runner_contracts.RunnerError, "reconcile requires five distinct shard report paths"):
                runner_reporting.reconcile_reports(paths[:3])
            with self.assertRaisesRegex(runner_contracts.RunnerError, "reconcile requires five distinct shard report paths"):
                runner_reporting.reconcile_reports([*paths[:3], paths[0]])
            reject(omit_case, "shard canonical inventory differs from the current frozen inventory")
            reject(lambda rows: rows[1]["selection"].update(shard="1/5"), "reports must have complementary unique five-shard identities")
            reject(lambda rows: rows[0]["selection"].update(shard="1/2"), "reports must have complementary unique five-shard identities")
            reject(lambda rows: rows[0].update(unexecuted_ids=["a.test"]), "shard 1/5 is incomplete")
            reject(lambda rows: rows[0]["environment"].update(installed_distributions_sha256="a" * 64), "shard source or execution environments disagree")
            reject( lambda rows: rows[0]["suite_policy"]["partition"].update(digest="sha256:" + "a" * 64),
                "shard canonical manifests or policy partition metadata disagree", )
            reject(lambda rows: selection_change(rows, "b.test"), "shard 1/5 differs from its ordered partition plan")
            write(lambda rows: status_change(rows, "failure", "retained failure"))
            self.assertEqual(runner_reporting.reconcile_reports(paths)["outcome"], "failure")
            with self.assertRaisesRegex(suite_budget.BudgetError, "did not all pass"):
                suite_budget.check_reports(self.budget, paths, allow_wall_time_overage=True)
            write()
            binding = self.report_data[0]["source_binding"]
            with ( patch.object(runner_reporting, "snapshot", return_value={**binding, "digest": "sha256:" + "0" * 64}),
                self.assertRaisesRegex(runner_contracts.RunnerError, "shard source differs from the current checkout inputs"), ):
                runner_reporting.reconcile_reports(paths)
            for allowed in (False, True):
                with patch.object(suite_budget, "reconcile_reports", return_value=result):
                    with self.assertRaisesRegex(suite_budget.BudgetError, "subtest scope"):
                        suite_budget.check_reports(self.budget, paths, allow_wall_time_overage=allowed)
                with patch.object(suite_budget, "reconcile_reports", side_effect=runner_contracts.RunnerError("incomplete")):
                    with self.assertRaisesRegex(runner_contracts.RunnerError, "incomplete"):
                        suite_budget.check_reports(self.budget, paths, allow_wall_time_overage=allowed)

        with tempfile.TemporaryDirectory() as directory:
            self._reports(directory, (0, 0)); rows = self.report_data; ids = tuple(self.budget["subtest_caps"])
            canonical = runner_contracts.CanonicalCollection((), ids); first = rows[0]
            inputs = [(row, tuple(row["selection_manifest"]["ordered_ids"]), 0) for row in reversed(rows)]
            options = dict(binding=first["source_binding"], environment=first["environment"],
                           caps=self.budget["subtest_caps"], wall=3.5, metadata={}, errors=[], slowest=0)
            merged = runner_hosted.merge_method_reports(canonical, canonical, inputs, **options)
            self.assertEqual([row["canonical_id"] for row in merged["tests"]], list(ids))
            self.assertEqual(merged["durations"], {"wall_seconds": 3.5, "cumulative_test_seconds": 5})
            partial = runner_hosted.merge_method_reports(canonical, canonical, inputs[:1], **options)
            self.assertEqual((partial["outcome"], partial["totals"]["completed"],
                              len(partial["unexecuted_ids"])), ("failure", 1, 4))
            self.assertRaises(runner_contracts.RunnerError, runner_hosted.merge_method_reports,
                              canonical, canonical, inputs+inputs, **options)
            for change in (lambda r: r["tests"][0].update(subtest_count=True),
                lambda r: r["tests"][0].update(subtest_count=99), lambda r: r.update(unexecuted_ids=[ids[0]]),
                lambda r: r["tests"][0].update(canonical_id=ids[1]),
                lambda r: r["environment"].update(executable="foreign"), lambda r: r["totals"].update(errors=1),
                lambda r: r["source_binding"].update(checkout_sha="0"*40), lambda r: r.update(outcome="interrupted")):
                bad = json.loads(json.dumps(first)); change(bad)
                self.assertRaises(runner_contracts.RunnerError, runner_hosted.validate_method_report, bad, (ids[0],), ids,
                                  options["binding"], options["environment"], options["caps"], 0)
            self.assertRaises(runner_contracts.RunnerError, runner_hosted.validate_method_report, first, (ids[0],), ids,
                              options["binding"], options["environment"], options["caps"], 1)

    def test_runner_counts_successful_subtests_in_parent_record(self) -> None:
        class Synthetic(unittest.TestCase):
            def runTest(self) -> None:
                for value in (1, 2):
                    with self.subTest(value=value):
                        if value == 2: self.skipTest("synthetic platform skip")
                        self.assertGreater(value, 0)

        case = Synthetic()
        result = runner_execution.TimedTextTestResult(io.StringIO(), descriptions=False, verbosity=0)
        unittest.TestSuite([case]).run(result)
        self.assertEqual(result.records([case.id()])[0]["subtest_count"], 2)


        from tests import local_ci, local_ci_coordinator as queue, local_ci_worker as worker, local_ci_state as state
        from tests.local_ci_state import LocalCIError
        from types import SimpleNamespace

        def invoke(function, error=None, message=""):
            with self.assertRaisesRegex(error, message) if error else nullcontext(): return function()

        def assert_fields(record, **expected): self.assertEqual({key: record[key] for key in expected}, expected)

        ci_identity_values = {"NAME": "Story Tests", "EMAIL": "story-tests@example.invalid"}
        caller_identity = { "GIT_AUTHOR_NAME": "Caller Author", "GIT_AUTHOR_EMAIL": "author@example.invalid",
            "GIT_COMMITTER_NAME": "Caller Committer", "GIT_COMMITTER_EMAIL": "committer@example.invalid", }
        inherited_config = { "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "user.name", "GIT_CONFIG_VALUE_0": "Inherited Git Name", }
        caller_environment = { **caller_identity, **inherited_config, "GITHUB_ACTIONS": "true", "PYTHONDONTWRITEBYTECODE": "0", "PYTHONPATH": "caller-path", }
        with patch.dict(worker.os.environ, caller_environment, clear=True):
            for ci_identity in (True, False):
                environment = worker.test_environment({"ci_identity": ci_identity})
                expected_identity = {key: ci_identity_values[key.rsplit("_", 1)[1]] for key in caller_identity} if ci_identity else caller_identity
                self.assertEqual({key: environment[key] for key in caller_identity}, expected_identity)
                expected_config = {**inherited_config, "GIT_CONFIG_COUNT": "3" if ci_identity else "1"}
                self.assertEqual({key: environment[key] for key in inherited_config}, expected_config)
                appended_config = { "GIT_CONFIG_KEY_1": "user.name", "GIT_CONFIG_VALUE_1": "Story Tests",
                    "GIT_CONFIG_KEY_2": "user.email", "GIT_CONFIG_VALUE_2": "story-tests@example.invalid", } if ci_identity else {}
                self.assertEqual({key: value for key, value in environment.items() if key.startswith("GIT_")},
                                 {**expected_identity, **expected_config, **appended_config})
                self.assertEqual(environment["PYTHONDONTWRITEBYTECODE"], "1")
                self.assertEqual(environment["PYTHONPATH"], "src")
                self.assertNotIn("GITHUB_ACTIONS", environment)
                with patch.dict(worker.os.environ, environment, clear=True):
                    self.assertEqual(worker.test_environment({"ci_identity": ci_identity}), environment)
                self.assertEqual(dict(worker.os.environ), caller_environment)

        invalid_configurations = [{"GIT_CONFIG_COUNT": count} for count in ("invalid", "-1", "01", "+1", "1.0", "")]
        invalid_configurations.extend({f"GIT_CONFIG_{field}_{index}": "orphan"} for index in (1, 2) for field in ("KEY", "VALUE"))
        for invalid_config in invalid_configurations:
            invalid_environment = {**caller_environment, **invalid_config}
            with patch.dict(worker.os.environ, invalid_environment, clear=True):
                with self.assertRaises(LocalCIError): worker.test_environment({"ci_identity": True})
                unchanged = worker.test_environment({"ci_identity": False})
                self.assertEqual({key: value for key, value in unchanged.items() if key.startswith("GIT_")},
                                 {key: value for key, value in invalid_environment.items() if key.startswith("GIT_")})
                self.assertEqual(dict(worker.os.environ), invalid_environment)

        worker_calls = Mock()
        worker_arguments = ["snapshot", "harmless-fixture"]
        worker_calls.parser.return_value.parse_args.return_value = SimpleNamespace(**dict(zip(("command", "checkout"), worker_arguments)))
        worker_calls.snapshot.return_value = {"safe": True}
        with patch.object(worker.os, "umask", worker_calls.umask), patch.object( worker.argparse, "ArgumentParser", worker_calls.parser
        ), patch.object(worker, "snapshot", worker_calls.snapshot), patch.object(worker.sys, "stdout", io.StringIO()) as output:
            self.assertEqual(worker.main(worker_arguments), 0)
            self.assertEqual(output.getvalue(), '{"safe": true}\n')
        startup = {"umask", "parser", "parser().parse_args", "snapshot"}
        self.assertEqual([row for row in worker_calls.mock_calls if row[0] in startup], [
            call.umask(0o077), call.parser(*worker_calls.parser.call_args.args, **worker_calls.parser.call_args.kwargs),
            call.parser().parse_args(worker_arguments), call.snapshot(Path("harmless-fixture")), ])

        collection = runner_contracts.CanonicalCollection((case,), (case.id(),))
        arguments = ["run", "--id", case.id()]
        with patch.object(runner_cli, "prepare_selection", return_value=(collection,
                runner_contracts.PolicySelection(collection, {}))), patch.object(local_ci,
                "runner_admission", return_value=17) as admission, patch.object(runner_cli, "execute") as execute:
            self.assertEqual(runner_cli.main(arguments), 17)
            admission.assert_called_once_with(arguments)
            execute.assert_not_called()
        host_exists = Path.exists
        lima_markers = {"/run/lima-ssh-ready", "/var/run/lima-ssh-ready", "/run/lima-guestagent.pid"}
        for guest_marker, managed, hosted, expected, error in ( (False, False, "", 17, None),
            (False, True, "", None, None), (False, False, "true", None, None),
            (True, False, "", None, LocalCIError), (True, True, "", None, None), ):
            with patch.object(Path, "exists", new=lambda path: guest_marker and path == Path("/run/lima-ssh-ready") or str(path) not in lima_markers and host_exists(path)), patch.dict(
                local_ci.os.environ, {"GITHUB_ACTIONS": hosted}
            ), patch.object(local_ci, "admitted_worker", return_value=managed) as inherited, patch.object( local_ci, "submit", return_value="owned"
            ) as submit, patch.object(local_ci, "wait_job", return_value={"exit_code": 17}) as wait, patch.object(local_ci, "state_root") as state_root:
                self.assertEqual(invoke(lambda: local_ci.runner_admission(arguments), error, "submit through the host"), expected)
                inherited.assert_called_once_with()
                self.assertEqual(wait.call_args_list, [call("owned")] if expected == 17 else [])
                self.assertEqual(submit.call_count, int(expected == 17))
                if expected == 17: self.assertEqual(submit.call_args.args[1], arguments)
                state_root.assert_not_called()

        launched = []
        def launch(instance, job):
            job.update(state="running", pid=10000 + job["sequence"], process_identity="live", started_at=100.0)
            launched.append(job["id"])

        with tempfile.TemporaryDirectory(prefix="local-ci-contract-") as temporary, patch.object( queue, "snapshot", return_value={"digest": "stable"}
        ), patch.object(queue, "process_identity", return_value="live"), patch.object( queue, "group_members", return_value=[10000]
        ), patch.object(queue, "boot_identity", return_value="sysctl:100:2") as boot, patch.object(queue.Coordinator, "launch", new=launch), patch.object(queue.os, "killpg") as kill:
            root = Path(temporary)
            coordinator = queue.Coordinator(root)
            claims = [coordinator.request({ "action": "submit", "owner": {"pid": 100 + index, "identity": "live"},
                "checkout": str(root), "source": {"digest": "stable"}, "args": arguments, "python": "unused-test-python", "task": f"independent-task-{index}",
            }) for index in range(8)]
            for action in ("submit", "reserve"):
                with self.assertRaisesRegex(LocalCIError, "still owns work"):
                    coordinator.request({"action": action, "code_identity": "different-revision"})
            self.assertEqual(len(coordinator.state["jobs"]), 8)
            coordinator.tick()
            self.assertEqual(launched, [claim["id"] for claim in claims[:4]])
            self.assertEqual(len(coordinator.active()), 4)
            self.assertEqual(coordinator.request({"action": "status", "code_identity": "different-revision"})["executing_or_quarantined"], 4)
            with self.assertRaisesRegex(LocalCIError, "ownership"):
                coordinator.request({"action": "cancel", "id": claims[4]["id"], "token": "wrong"})
            self.assertEqual(coordinator.find(claims[4]["id"])["state"], "queued")
            cancelled = coordinator.request({"action": "cancel", "code_identity": "different-revision", **claims[4]})
            self.assertEqual(cancelled["state"], "cancelled")
            coordinator.find(claims[0]["id"]).update(state="finished", exit_code=0)
            coordinator.tick()
            self.assertEqual(launched, [claim["id"] for claim in claims[:4]] + [claims[5]["id"]])
            self.assertEqual(len(coordinator.active()), 4)
            starting = coordinator.find(claims[5]["id"])
            starting.update(state="starting")
            starting.pop("pid")
            for job in coordinator.active():
                job["host_boot_identity"] = "sysctl:{ sec = 100, usec = 2 } Mon Oct 5 12:00:00 KST"
            coordinator.save()
            restarted = queue.Coordinator(root)
            self.assertEqual([job["state"] for job in restarted.active()], ["quarantined"] * 4)
            restarted.tick()
            status = restarted.request({"action": "status"})
            self.assertEqual((status["capacity"], status["executing_or_quarantined"]), (4, 4))
            self.assertEqual(len(launched), 5)
            restarted.request({"action": "cancel", **claims[5]})
            restarted.tick()
            self.assertEqual(len(restarted.active()), 4)
            self.assertEqual(len(launched), 5)
            kill.assert_not_called()
            uncertain = restarted.find(claims[1]["id"])
            for code in (None, 130):
                uncertain.update(state="running", descendant_death_uncertain=False)
                with patch.object(queue, "group_members", return_value=[]), patch.object(queue, "live_owned", return_value=[]):
                    restarted.finish(uncertain, code)
                assert_fields(uncertain, state="quarantined", descendant_death_uncertain=True)
            uncertain.update(state="running", descendant_death_uncertain=False)
            with patch.object(queue, "group_members", return_value=[]), patch.object(queue, "live_owned", return_value=[12345]):
                restarted.finish(uncertain, 0)
            self.assertEqual(uncertain["state"], "quarantined")
            self.assertIn("outside the original process group", uncertain["recovery"])
            self.assertEqual(len(restarted.active()), 4)
            uncertain["owned_processes"] = {}
            child = {str(uncertain["pid"] + 1): {"pid": uncertain["pid"] + 1, "identity": "owned-child", "pgid": uncertain["pid"] + 1}}
            with patch.object(queue, "process_identity", return_value="replacement") as leader, patch.object( queue, "observed_descendants", return_value=child
            ) as observed, patch.object(queue, "group_members", return_value=[]), patch.object(
                queue, "live_owned", side_effect=lambda records: [uncertain["pid"] + 1] if records else [] ), patch.object(queue, "signal_owned") as signals:
                for identity, raced, expected_state, expected_owned in ( ("replacement", False, "quarantined", {}),
                    (None, False, "quarantined", {}), ("live", True, "quarantined", {}),
                    ("live", False, "cancelling", child), ):
                    leader.return_value = identity
                    leader.side_effect = ("live", "replacement") if raced else None
                    observed.reset_mock()
                    signals.reset_mock()
                    restarted.request({"action": "cancel", **claims[1]})
                    self.assertEqual(observed.call_args_list, [call(uncertain["pid"])] if identity == "live" else [])
                    self.assertEqual(signals.call_args_list,
                                     [call(child, queue.signal.SIGINT, outside_group=uncertain["pid"])] if expected_state == "cancelling" else [])
                    kill.assert_not_called()
                    assert_fields(uncertain, owned_processes=expected_owned, state=expected_state)
                    persisted = next(job for job in json.loads(restarted.path.read_text())["jobs"] if job["id"] == uncertain["id"])
                    self.assertEqual(persisted["owned_processes"], expected_owned)
            for index, (exit_code, replacement, grouped, owned, tracked, uncertain_before, expected) in enumerate(( (0,
                None, False, False, True, False, "finished"), (None, None, False, False, True, False, "running"), (-9,
                None, False, False, True, False, "quarantined"), (0, None, True, False, True, False, "quarantined"), (0,
                None, False, True, True, False, "quarantined"), (0, "replacement", False, False, True, False,
                "quarantined"), (None, None, False, False, False, False, "quarantined"), (0, None, False, False, True,
                True, "quarantined"), )):
                refresh_root = state.owner_directory(root / f"refresh-{index}", create=True)
                refresh = queue.Coordinator(refresh_root)
                old_owned = {"21001": {"pid": 21001, "identity": "owned-child", "pgid": 21001}} if owned else {}
                tracked_job = {"id": f"refresh-{index}", "kind": "runner", "state": "running", "executor": "native",
                    "pid": 21000, "process_identity": "live", "owner": {"pid": 123, "identity": "live"},
                    "owner_checked_at": queue.time.time(), "started_at": 100.0, "directory": str(refresh_root),
                    "checkout": str(refresh_root), "source": {"digest": "stable"}, "owned_processes": old_owned.copy(),
                    "descendant_death_uncertain": uncertain_before}
                refresh.state["jobs"].append(tracked_job)
                if tracked:
                    refresh.processes[tracked_job["id"]] = SimpleNamespace(poll=lambda: exit_code, returncode=exit_code)
                temporary_owned = {"21002": {"pid": 21002, "identity": "unverified-child", "pgid": 21002}}
                with patch.object(queue, "process_identity", side_effect=("live", replacement)), patch.object(
                    queue, "observed_descendants", return_value=temporary_owned
                ) as observed, patch.object(queue, "group_members", return_value=[21000] if grouped else []), patch.object(
                    queue, "live_owned", return_value=[21001] if owned else [] ), patch.object(queue, "signal_owned") as signals:
                    refresh.tick()
                observed.assert_called_once_with(21000)
                signals.assert_not_called()
                kill.assert_not_called()
                assert_fields(tracked_job, owned_processes=old_owned, state=expected)
                self.assertEqual(len(refresh.active()), int(expected != "finished"))
                if expected == "finished":
                    self.assertEqual((tracked_job["exit_code"], tracked_job["source_valid"]), (0, True))
                if uncertain_before: self.assertTrue(tracked_job["descendant_death_uncertain"])
            boot.return_value = "sysctl:101:2"
            rebooted = queue.Coordinator(root)
            self.assertEqual(rebooted.active(), [])
            failed = [job for job in rebooted.state["jobs"] if job["state"] == "failed"]
            self.assertEqual(len(failed), 4)
            self.assertTrue(all(job["exit_code"] == 2 and job["source_valid"] is False for job in failed))

            checkout = root / "pinned-checkout"
            checkout.mkdir()
            self.assertFalse((checkout / "tests/local_ci_worker.py").exists())
            checkout = checkout.resolve(strict=True)
            static_report = (root / "native-static-report").resolve()
            static_args = local_ci.argparse.Namespace( checkout=str(checkout), executor="native", guest_checkout=None, report_dir=str(static_report),
                base_sha="a" * 40, head_sha="b" * 40, label=[], id=[], module=[], timing_mode="shared", task="retained-native-static", )
            def campaign_arguments(report, **overrides):
                return local_ci.argparse.Namespace(**{**vars(static_args), "report_dir": str(report), **overrides})
            cached_sources = {"local_ci_worker.py": b"immutable worker source"}
            cached_bundle = root / "cached-service"
            host_state = root / "host-state"
            target_python = str(checkout / ".venv/bin/python")
            expected_environment = worker.test_environment({"ci_identity": True})
            older = campaign_arguments(root / "older-head-report")
            with patch.object(local_ci, "git", side_effect=("a" * 40, "b" * 40, "c" * 40)), patch.object( local_ci.subprocess, "run"
            ), patch.object(local_ci, "snapshot") as source:
                with self.assertRaisesRegex(LocalCIError, "HEAD must equal the pinned head SHA exactly"):
                    local_ci.campaign(older)
                source.assert_not_called()
            with patch.object(local_ci, "git", side_effect=("a" * 40, "b" * 40, "b" * 40)) as git_calls, patch.object(
                local_ci, "snapshot", return_value={"porcelain": "", "digest": "pinned-source"}
            ), patch.object(local_ci, "classify", return_value={"runtime": "false", "full": "false", "changed_paths": []}) as classify, patch.object(
                local_ci, "service_sources", return_value=("cached-code", cached_sources)
            ), patch.object(local_ci, "state_root", return_value=host_state), patch.object( local_ci, "prepare_service", return_value=cached_bundle
            ) as prepare, patch.object(local_ci.subprocess, "check_output", return_value="Python 3.11.16\n") as version, patch.object(
                local_ci.subprocess, "run", return_value=local_ci.subprocess.CompletedProcess([], 0, "static passed", "") ) as execute_static:
                self.assertEqual(local_ci.campaign(static_args), 0)
                prepare.assert_called_once_with(host_state, "cached-code", cached_sources)
                version.assert_called_once_with([target_python, "--version"], text=True)
                classify.assert_called_once_with(checkout, "a" * 40, "b" * 40, [])
                self.assertEqual(execute_static.call_args.args, ([ target_python, str(cached_bundle / "entry.py"), "static", "--checkout", str(checkout),
                    "--report", str(static_report / "static.json"), "--base", "a" * 40, "--head", "b" * 40, ],))
                self.assertEqual(execute_static.call_args.kwargs, { "cwd": checkout, "env": expected_environment, "capture_output": True, "text": True, })
                classify.return_value = {"runtime": "false", "full": "true", "changed_paths": []}
                execute_static.return_value = local_ci.subprocess.CompletedProcess([], 0, "", "")
                for timing_mode, override, concurrency in (("isolated", None, 1), ("shared", None, 4),
                                                           ("isolated", 2, 2), ("shared", 1, 1)):
                    git_calls.side_effect = ("a" * 40, "b" * 40, "b" * 40)
                    campaign_report = (root / f"campaign-{timing_mode}-{override}").resolve()
                    campaign_args = campaign_arguments(campaign_report, timing_mode=timing_mode, full_concurrency=override)
                    events = []
                    def submit_full(target, arguments, **options):
                        shard = arguments[2]
                        self.assertEqual(target, checkout)
                        self.assertEqual(arguments, ["run", "--shard", shard, "--json",
                                                     str(campaign_report / f"full-{shard[0]}.json")])
                        self.assertEqual(options, { "python": target_python, "task": static_args.task + f":full-{shard[0]}",
                            "executor": "native", "guest_checkout": None, "reservation": None, "ci_identity": True, })
                        events.append(("submit", shard))
                        return shard
                    def wait_full(identifier):
                        events.append(("wait", identifier))
                        start = ((int(identifier[0]) - 1) // concurrency) * 2
                        return {"id": identifier, "exit_code": int(identifier == "1/5"), "state": "finished",
                                "started_at": start, "finished_at": start + 1, "queue_wait_seconds": 0.25}
                    with patch.object(local_ci, "submit", side_effect=submit_full), patch.object( local_ci, "wait_job", side_effect=wait_full
                    ), patch.object( local_ci, "rpc", side_effect=lambda message: {"state": "finished", "worker_exit_code": int(message["id"] == "1/5")},
                    ), patch.object( local_ci, "control" ) as control_calls:
                        self.assertEqual(local_ci.campaign(campaign_args), 1)
                        control_calls.assert_not_called()
                    expected_events = {
                        1: [("submit", "1/5"), ("wait", "1/5"), ("submit", "2/5"), ("wait", "2/5"),
                            ("submit", "3/5"), ("wait", "3/5"), ("submit", "4/5"), ("wait", "4/5"),
                            ("submit", "5/5"), ("wait", "5/5")],
                        2: [("submit", "1/5"), ("submit", "2/5"), ("wait", "1/5"), ("wait", "2/5"),
                            ("submit", "3/5"), ("submit", "4/5"), ("wait", "3/5"), ("wait", "4/5"),
                            ("submit", "5/5"), ("wait", "5/5")],
                        3: [("submit", "1/5"), ("submit", "2/5"), ("submit", "3/5"), ("wait", "1/5"),
                            ("wait", "2/5"), ("wait", "3/5"), ("submit", "4/5"), ("submit", "5/5"),
                            ("wait", "4/5"), ("wait", "5/5")],
                        4: [("submit", "1/5"), ("submit", "2/5"), ("submit", "3/5"), ("submit", "4/5"),
                            ("wait", "1/5"), ("wait", "2/5"), ("wait", "3/5"), ("wait", "4/5"),
                            ("submit", "5/5"), ("wait", "5/5")], }[concurrency]
                    self.assertEqual(events, expected_events)
                    metadata = json.loads((campaign_report / "campaign.json").read_text())
                    self.assertEqual({key: metadata[key] for key in ("timing_mode", "full_concurrency", "exit_code", "source_valid")}, {
                        "timing_mode": timing_mode, "full_concurrency": concurrency, "exit_code": 1, "source_valid": True, })
                    starts = {1: (0, 2, 4, 6, 8), 2: (0, 0, 2, 2, 4), 3: (0, 0, 0, 2, 2),
                              4: (0, 0, 0, 0, 2)}[concurrency]
                    expected_jobs = [{"id": f"{index}/5", "exit_code": int(index == 1), "state": "finished",
                                      "started_at": start, "finished_at": start + 1, "queue_wait_seconds": 0.25} for index, start in enumerate(starts, 1)]
                    self.assertEqual(metadata["jobs"], expected_jobs)
                    self.assertEqual(json.loads((campaign_report / "full-exit-status.json").read_text()),
                                     {"full-1": 1, "full-2": 0, "full-3": 0, "full-4": 0, "full-5": 0})
                classify.return_value = {"runtime": "true", "full": "false", "changed_paths": []}
                for cleanup_case, primary_failure, body_code, interrupted, exit_code in ( ("original", True, 0, False, 2), ("release", False, 0, False, 2),
                    ("interrupt", False, 0, True, 130), ("original-interrupt", True, 0, True, 2),
                    ("status-interrupt", False, 1, True, 1), ):
                    git_calls.side_effect = ("a" * 40, "b" * 40, "b" * 40)
                    cleanup_report = (root / f"cleanup-{cleanup_case}").resolve()
                    cleanup_args = campaign_arguments(cleanup_report, timing_mode="isolated")
                    original = LocalCIError("original wait failure")
                    cleanup_error = KeyboardInterrupt() if interrupted else LocalCIError("cleanup RPC failure")
                    waits = [{"state": "reserved"}, original if primary_failure else {"exit_code": body_code}]
                    with patch.multiple( local_ci, owner=lambda: {"pid": 123, "identity": "owner"},
                        claim=lambda *args: "reservation", submit=lambda *args, **kwargs: "fast-job",
                    ), patch.object(local_ci, "wait_job", side_effect=waits), patch.object( local_ci, "control", side_effect=cleanup_error ) as cleanup:
                        if primary_failure:
                            with self.assertRaises(LocalCIError) as caught: local_ci.campaign(cleanup_args)
                            self.assertIs(caught.exception, original)
                        else: self.assertEqual(local_ci.campaign(cleanup_args), exit_code)
                    operations = [("cancel", "fast-job"), ("cancel", "reservation"), ("release", "reservation")] if primary_failure else [("release", "reservation")]
                    self.assertEqual([(call.args, call.kwargs) for call in cleanup.call_args_list], [(operation, {}) for operation in operations])
                    metadata = json.loads((cleanup_report / "campaign.json").read_text())
                    self.assertEqual(metadata["exit_code"], exit_code)
                    self.assertEqual(metadata["error"], "original wait failure" if primary_failure else
                                     "interrupted" if exit_code == 130 else "campaign cleanup failed; inspect cleanup_errors")
                    self.assertEqual(metadata["cleanup_errors"], [{"action": action, "id": identifier,
                                     "error": "interrupted" if interrupted else "cleanup RPC failure"} for action, identifier in operations])
            control = {"directory": "/tmp/durable-lima-control", "python": "/usr/bin/python3", "entry": "/tmp/durable-lima-control/control.py"}
            with self.assertRaisesRegex(LocalCIError, "control metadata missing"):
                queue.lima_command({"executor": "lima:guest", "guest_checkout": "/removed/checkout"}, "probe", "owned-job")
            prepared = {**control, "code_identity": "immutable-source"}
            with patch.object(queue, "service_sources", return_value=("immutable-source", {"local_ci_worker.py": b"sealed source"})), patch.object(
                queue.subprocess, "run", return_value=local_ci.argparse.Namespace(returncode=0, stdout=json.dumps(prepared), stderr="") ) as bootstrap:
                self.assertEqual(queue.prepare_lima_control({"executor": "lima:guest", "guest_checkout": "/initial/checkout"}), prepared)
                self.assertEqual(bootstrap.call_args.args[0], ["limactl", "shell", "--workdir", "/initial/checkout", "guest", "/initial/checkout/.venv/bin/python", "-c", queue.GUEST_BOOTSTRAP])
                package = json.loads(bootstrap.call_args.kwargs["input"])
                self.assertEqual((package["identity"], package["checkout"]), ("immutable-source", "/initial/checkout"))
                self.assertEqual(queue.base64.b64decode(package["sources"]["local_ci_worker.py"]), b"sealed source")
            for operation in ("probe", "collect", "cancel"):
                self.assertEqual(queue.lima_command({"executor": "lima:guest", "guest_checkout": "/removed/checkout", "guest_control": control}, operation, "owned-job"), [
                    "limactl", "shell", "--workdir", control["directory"], "guest", "env",
                    "PYTHONPATH=" + control["directory"], control["python"], control["entry"], operation, "owned-job", ])
            guest = state.owner_directory(root / "guest-state", create=True)
            supervisor = {"pid": 777, "process_identity": "supervisor", "boot_identity": "sysctl:200:3"}
            quarantined = {"state": "quarantined", "descendant_death_uncertain": True}
            running = {"state": "running", "descendant_death_uncertain": False}
            completed = {"state": "finished", "descendant_death_uncertain": False, "exit_code": 0, "source_valid": True}
            terminated = {"state": "terminated", "descendant_death_uncertain": False, "source_valid": False, "boot_recovery": True}
            guest_base = {"state": "running", "pid": 99999, "owned_processes": {}, "descendant_death_uncertain": False,
                          "boot_identity": "sysctl:{ sec = 200, usec = 3 } Wed Oct 7 03:00:00 UTC"}
            with patch.object(worker, "guest_job", return_value=guest), patch.object(worker, "group_members", return_value=[]), patch.object(
                worker, "live_owned", return_value=[] ), patch.object(worker, "boot_identity") as guest_boot, patch.object(worker, "process_identity") as birth:
                for record, identities, extra, reboot, finalize, expected in ( (None, (), {}, False, False,
                    quarantined), (None, (), quarantined, True, False, terminated), (supervisor, ("supervisor",
                    "supervisor"), {}, False, False, running), (supervisor, (None, None), {}, False, False,
                    quarantined), (supervisor, ("replacement", "replacement"), {}, False, False, quarantined),
                    (supervisor, ("supervisor", None), {}, False, False, quarantined), (supervisor, ("supervisor",
                    "supervisor"), quarantined, False, False, quarantined), (supervisor, (None, None), completed, False,
                    False, completed), (supervisor, (), {"descendant_death_uncertain": True}, False, True,
                    {**quarantined, "exit_code": 0, "source_valid": True}), ):
                    worker.write_json(guest / "state.json", {**guest_base, **({"supervisor": record} if record else {}), **extra})
                    birth.side_effect = identities
                    guest_boot.return_value = "sysctl:201:3" if reboot else "sysctl:200:3"
                    observed = worker.update_guest(guest / "state.json", completed) if finalize else worker.probe_guest("owned-job")
                    for record in (observed, worker.read_json(guest / "state.json")):
                        assert_fields(record, **expected)
            for replacement in ("replacement", None):
                guest_state = {"state": "running", "pid": 99999, "process_identity": "owned", "token": "capability", "owned_processes": {}}
                guest_child = {"100000": {"pid": 100000, "identity": "owned-child", "pgid": 100000}}
                worker.write_json(guest / "state.json", guest_state)
                with patch.object(worker, "guest_job", return_value=guest), patch.object(worker, "probe_guest", return_value=guest_state), patch.object(
                    worker, "process_identity", return_value="owned"
                ) as leader, patch.object(worker, "observed_descendants", return_value=guest_child) as observed, patch.object(
                    worker, "group_members", return_value=[99999]
                ), patch.object(worker, "live_owned", return_value=[100000]), patch.object(worker.os, "killpg") as guest_kill, patch.object(
                    worker.time, "monotonic", side_effect=iter(range(0, 100, 4))
                ), patch.object(worker.time, "sleep") as sleep, patch.object(worker, "signal_owned") as signals:
                    signals.side_effect = lambda *args, **kwargs: setattr(leader, "return_value", replacement)
                    invoke(lambda: worker.cancel_guest("owned-job", "capability"), LocalCIError if replacement is not None else None, "identity changed")
                    observed.assert_called_once_with(99999)
                    guest_kill.assert_called_once_with(99999, worker.signal.SIGINT)
                    actions = [worker.signal.SIGINT] if replacement is not None else [worker.signal.SIGINT, worker.signal.SIGTERM, worker.signal.SIGKILL]
                    self.assertEqual([(call.args, call.kwargs) for call in signals.call_args_list], [
                        ((guest_child, action), {"outside_group": 99999 if index == 0 else None}) for index, action in enumerate(actions)])
                    sleep.assert_not_called()
                persisted = worker.read_json(guest / "state.json")
                assert_fields(persisted, state="quarantined", descendant_death_uncertain=True, owned_processes=guest_child)
            old_boot = "sysctl:{ sec = 100, usec = 2 } Mon Oct 5 12:00:00 KST"
            same_boot = "sysctl:{ sec = 100, usec = 2 } Mon Oct 5 03:00:00 UTC"
            self.assertEqual(state.normalize_boot_identity(old_boot), "sysctl:100:2")
            self.assertFalse(state.verified_boot_change(old_boot, same_boot))
            self.assertTrue(state.verified_boot_change(old_boot, "sysctl:101:2"))
            for unsuitable in (None, "unparseable", "sysctl:100:1000000", "linux:12345678-1234-1234-1234-123456789abc"):
                self.assertFalse(state.verified_boot_change(old_boot, unsuitable))

        import os, signal, subprocess, sys, time
        events = []
        class Ordered(unittest.TestCase):
            @classmethod
            def setUpClass(cls): events.append("class start")
            @classmethod
            def tearDownClass(cls): events.append("class end")
            def runTest(self): events.append("method")
        ordered = (Ordered(), Ordered())
        runner_execution.execute(runner_contracts.CanonicalCollection(ordered, tuple(case.id() for case in ordered)), stream=io.StringIO())
        self.assertEqual(events, ["class start", "method", "method", "class end"])
        if sys.platform != "linux" or os.environ.get("GITHUB_ACTIONS") != "true": return
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); worker_pids = []
            for index in range(2):
                work = root / str(index); work.mkdir()
                process, pipe, config = runner_supervision.launch_method(
                    [sys.executable, "-c", "pass"], work, work_deadline=time.monotonic()+15)
                try:
                    self.assertEqual(process.wait(timeout=8), 0)
                    receipt = runner_supervision.method_receipt(config, process); worker_pids.append(receipt["worker_pid"])
                    self.assertEqual((receipt["worker_exit"], receipt["reason"]), (0, "completed"))
                    for field, bad in (("reaped", False), ("group_absent", False), ("deadline", config["deadline"]+1)):
                        with patch.object(runner_supervision, "load_json_object", return_value={**receipt, field: bad}):
                            self.assertRaises(runner_contracts.RunnerError, runner_supervision.method_receipt, config, process)
                finally:
                    os.close(pipe)
                    if process.poll() is None: process.terminate(); process.wait(timeout=8)
            grant = {**config, "work_deadline": float.fromhex("0x1.fffffffffffffp+6"), "deadline": 158.0}
            with (patch.object(runner_supervision, "load_json_object", return_value=grant),
                  patch("ctypes.CDLL", side_effect=LookupError("admitted"))):
                self.assertRaisesRegex(LookupError, "admitted", runner_supervision.method_supervisor, work / "config.json")
                grant["deadline"] = float.fromhex("0x1.3c00000000001p+7")
                self.assertRaises(runner_contracts.RunnerError, runner_supervision.method_supervisor, work / "config.json")
            self.assertEqual(len(set(worker_pids + [os.getpid()])), 3)
            with patch("subprocess.Popen") as launch:
                self.assertRaises(runner_contracts.RunnerError, runner_supervision.launch_method, [sys.executable], root, work_deadline=0)
                launch.assert_not_called()
            work = root / "orphan"; work.mkdir(); ready = work / "ready"
            body = ("import subprocess,sys,time; from pathlib import Path; "
                    "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
                    "Path(" + repr(str(ready)) + ").write_text(str(p.pid)); time.sleep(60)")
            controller = ("from tests.runner import supervision as runner_supervision; import sys,time; from pathlib import Path; "
                          "runner_supervision.launch_method([sys.executable,'-c'," + repr(body) + "],Path(" + repr(str(work)) +
                          "),work_deadline=time.monotonic()+15); time.sleep(60)")
            parent = subprocess.Popen([sys.executable, "-c", controller], start_new_session=True)
            try:
                until = time.monotonic()+8
                while not ready.exists() and time.monotonic()<until: time.sleep(.02)
                self.assertTrue(ready.exists())
                parent.kill(); parent.wait(timeout=2)
                until = time.monotonic()+8
                while not (work/"supervisor.json").exists() and time.monotonic()<until: time.sleep(.02)
                receipt = json.loads((work/"supervisor.json").read_text())
                self.assertEqual((receipt["reason"], receipt["complete"], receipt["reaped"], receipt["group_absent"]),
                                 ("parent_closed", True, True, True))
                self.assertTrue(runner_supervision.method_group_absent(receipt["worker_pid"]))
            finally:
                if parent.poll() is None: parent.kill()
                parent.wait(timeout=2)
