"""Canonical test runner: profile owner."""
from __future__ import annotations

import math
from .contracts import RunnerError
from .evidence import manifest, required_dict


HOSTED_PROFILE = 'audited-162-v6'


HOSTED_CLEANUP_SECONDS = 30.0


HOSTED_LAUNCH_IDS = (
    'tests.test_lead_orchestration.LeadOutcomeAndProposalTests.test_front_schema_is_minimal_closed_and_provider_strict',
    'tests.test_specialist_routing_runtime.SpecialistRoutingRuntimeTests.test_declared_substantive_direct_outcomes_fail_closed',
    'tests.test_lead_evidence_grounding.RuntimeLeadEvidenceGroundingTests.test_mixed_zero_hit_survives_self_appended_history_without_mutation',
    'tests.test_artifact_objectives.ArtifactObjectiveContractTests.test_intent_variants_and_objective_values_are_closed_and_content_safe',
    'tests.test_artifact_objectives.ArtifactObjectiveContractTests.test_readable_and_session_context_include_objectives_without_new_records',
    'tests.test_artifact_proposal_orchestration.ArtifactProposalOrchestrationTests.test_ordinary_discussion_captures_current_state_without_specialists',
    'tests.test_artifact_proposal_orchestration.ArtifactProposalOrchestrationTests.test_provider_authority_fields_fall_back_without_candidate',
    'tests.test_lead_evidence_grounding.RuntimeLeadEvidenceGroundingTests.test_failure_correlation_and_post_closure_calls_fail_closed',
    'tests.test_lead_evidence_grounding.RuntimeLeadEvidenceGroundingTests.test_whole_and_range_conflict_bind_proposal_and_detect_drift',
    'tests.test_provider_auth_recovery.ContextRecoveryTests.test_safe_replay_preserves_wire_and_counts_distinct_physical_calls',
    'tests.test_lead_orchestration.LeadRecoveryAndAccountingTests.test_unstarted_prepared_and_submitted_requests_never_replay',
    'tests.test_lead_orchestration.LeadSubmittedFailureRecoveryTests.test_submitted_missing_before_correction_never_replays',
    'tests.test_lead_orchestration.LeadSubmittedFailureRecoveryTests.test_submitted_cancelled_before_correction_never_replays',
    'tests.test_lead_orchestration.LeadSubmittedFailureRecoveryTests.test_submitted_timed_out_before_correction_never_replays',
    'tests.test_lead_orchestration.LeadSubmittedFailureRecoveryTests.test_submitted_crashed_before_correction_never_replays',
    'tests.test_lead_orchestration.LeadSubmittedFailureRecoveryTests.test_submitted_submission_uncertain_before_correction_never_replays',
    'tests.test_lead_orchestration.LeadSubmittedFailureRecoveryTests.test_submitted_missing_during_correction_never_replays',
    'tests.test_lead_orchestration.LeadSubmittedFailureRecoveryTests.test_submitted_cancelled_during_correction_never_replays',
    'tests.test_lead_orchestration.LeadSubmittedFailureRecoveryTests.test_submitted_timed_out_during_correction_never_replays',
    'tests.test_lead_orchestration.LeadSubmittedFailureRecoveryTests.test_submitted_crashed_during_correction_never_replays',
    'tests.test_lead_orchestration.LeadSubmittedFailureRecoveryTests.test_submitted_submission_uncertain_during_correction_never_replays',
    'tests.test_specialist_routing.SpecialistRoutingTests.test_formal_planning_question_selected_gen_is_closed_and_owned',
    'tests.test_specialist_routing.SpecialistRoutingTests.test_selected_document_revision_frames_and_continuation_are_current',
    'tests.test_specialist_routing_runtime.SpecialistRoutingRuntimeTests.test_old_drafted_revision_cannot_authorize_revised_candidate',
    'tests.test_historical_material_workflow.HistoricalMaterialWorkflowTests.test_prior_draft_and_rejected_response_remain_input_only_in_new_task',
    'tests.test_specialist_routing_runtime.SpecialistRoutingRuntimeTests.test_empty_inventory_does_not_add_unaffected_owners_and_mixed_scope_does',
    'tests.test_lead_orchestration.LeadOutcomeAndProposalTests.test_direct_scope_is_required_and_substantive_outcomes_cannot_bypass_routing',
    'tests.test_lead_orchestration.LeadRecoveryAndAccountingTests.test_rejected_returned_result_closes_and_next_turn_uses_one_fresh_call',
    'tests.test_lead_orchestration.LeadDecisionAuthorityTests.test_mismatch_reject_revise_and_free_text_are_non_authorizing',
    'tests.test_lead_session_continuity.LeadSessionContinuityTests.test_confirmed_plan_survives_three_followups_and_conversation_switch',
    'tests.test_artifact_proposal_orchestration.ArtifactProposalOrchestrationTests.test_core_conflict_revision_and_manuscript_atomic_rename_workflows',
    'tests.test_specialist_routing_runtime.SpecialistRoutingRuntimeTests.test_draft_integrates_exact_all_role_review_apply_and_storage_parity',
    'tests.test_lead_orchestration.LeadDecisionAuthorityTests.test_revision_direct_workflow_cannot_erase_selected_story_scope',
    'tests.test_artifact_proposal_orchestration.ArtifactProposalOrchestrationTests.test_all_assessments_are_preserved_and_partial_failure_never_reviews',
    'tests.test_lead_orchestration.LeadOutcomeAndProposalTests.test_direct_search_read_answer_uses_bounded_history_without_proposal',
    'tests.test_lead_evidence_grounding.RuntimeLeadEvidenceGroundingTests.test_zero_hits_and_scoped_status_evidence_are_derived_locally',
    'tests.test_lead_artifact_objectives.LeadArtifactObjectiveTests.test_only_matching_successful_apply_satisfies_objective',
    'tests.test_lead_artifact_objectives.LeadArtifactObjectiveTests.test_document_plan_goal_candidate_and_sequential_plot_work_survive_restart',
    'tests.test_lead_artifact_objectives.LeadDefaultBudgetRefusalTests.test_default_grouped_budget_refuses_unfunded_artifact_preparation',
    'tests.test_lead_evidence_grounding.RuntimeLeadEvidenceGroundingTests.test_unread_and_truncated_search_fall_back_while_reads_attach_locally',
    'tests.test_lead_evidence_grounding.RuntimeLeadEvidenceGroundingTests.test_unknown_duplicate_reordered_and_prior_turn_ids_fail_once',
    'tests.test_recommendation_actions.RecommendationActionContractTests.test_current_response_alias_binds_exact_whole_message_before_action',
    'tests.test_lead_orchestration.LeadBudgetFinalizationTests.test_budget_notice_preserves_maximum_utf8_synthesis',
    'tests.test_artifact_objectives.ArtifactObjectiveContractTests.test_empty_continuation_retains_selection_and_cancel_is_terminal',
    'tests.test_lead_orchestration.LeadReferenceFailurePrecedenceTests.test_primary_reference_and_capacity_errors_are_not_reclassified',
    'tests.test_lead_recommendations.LeadRecommendationTests.test_exact_stale_duplicate_and_concurrent_decisions_never_reuse_authority',
    'tests.test_lead_orchestration.LeadOutcomeAndProposalTests.test_runtime_repairs_then_safely_ignores_invalid_proposals',
    'tests.test_lead_recommendations.LeadRecommendationTests.test_artifact_confirm_runs_one_preparation_and_stops_at_exact_review',
    'tests.test_provider_timeout_recovery.ProviderTimeoutRecoveryTests.test_forced_timeout_restart_and_explicit_retry_preserve_both_attempts',
    'tests.test_artifact_objectives.ArtifactObjectiveContractTests.test_projection_retains_satisfaction_across_later_apply_commit_cycles',
    'tests.test_artifact_proposal_orchestration.ArtifactProposalOrchestrationTests.test_search_read_answer_can_add_an_artifact_candidate',
    'tests.test_lead_orchestration.LeadOutcomeAndProposalTests.test_strict_provider_outcomes_reject_unknown_private_mixed_and_duplicate',
    'tests.test_provider_auth_recovery.ContextRecoveryTests.test_later_action_recovery_preserves_prior_evidence_and_per_call_cap',
    'tests.test_lead_orchestration.LeadOutcomeAndProposalTests.test_all_five_outcomes_have_exact_visible_next_actions',
    'tests.test_lead_orchestration.LeadBudgetFinalizationTests.test_unaffordable_continuation_finalizes_exact_result_without_authority',
    'tests.test_lead_orchestration.LeadOutcomeAndProposalTests.test_front_schema_rejection_is_repaired_once',
    'tests.test_interactive_controller.InteractiveLeadAdapterTests.test_literal_lead_turn_and_consultation_decisions_are_exact',
    'tests.test_specialist_routing_runtime.SpecialistRoutingRuntimeTests.test_cross_conversation_selection_preserves_scope_and_rejects_workflow_relabel',
    'tests.test_artifact_proposal_orchestration.ArtifactProposalOrchestrationTests.test_stale_confirmation_and_revision_require_fresh_authority',
    'tests.test_lead_orchestration.LeadBudgetFinalizationTests.test_only_budget_subtype_is_caught_after_successful_result',
    'tests.test_lead_orchestration.LeadRecoveryAndAccountingTests.test_search_before_first_rejection_uses_attempt_labels',
    'tests.test_artifact_objectives.ArtifactObjectiveContractTests.test_question_and_synthesis_sources_preserve_exact_whole_message_digests',
    'tests.test_recommendation_actions.RecommendationActionContractTests.test_confirm_origin_v2_and_continuing_v1_preserve_exact_wire_facts',
    'tests.test_provider_timeout_recovery.ProviderTimeoutRecoveryTests.test_legacy_bare_prepared_and_submitted_tails_close_only_on_new_input',
    'tests.test_provider_timeout_recovery.ProviderTimeoutRecoveryTests.test_abandoned_turn_preserves_other_conversation_successful_checkpoint',
    'tests.test_specialist_orchestration.SpecialistExecutionAcceptanceTests.test_invalid_specialist_shapes_repair_then_fall_back_without_halting',
    'tests.test_artifact_proposal_orchestration.ArtifactProposalOrchestrationTests.test_missing_synthesis_message_fails_and_reviewed_restart_has_no_authority',
    'tests.test_specialist_routing_runtime.SpecialistRoutingRuntimeTests.test_review_rejects_empty_reference_but_accepts_explicit_user_candidate',
    'tests.test_lead_orchestration.LeadBudgetFinalizationTests.test_restart_and_repeated_known_result_finalization_are_idempotent',
    'tests.test_lead_evidence_grounding.RuntimeLeadEvidenceGroundingTests.test_validation_failure_closes_once_and_restart_uses_fresh_turn',
    'tests.test_artifact_proposal_orchestration.ArtifactProposalOrchestrationTests.test_read_evidence_translates_whole_content_and_range_boundary_digests',
    'tests.test_artifact_objectives.ArtifactObjectiveContractTests.test_objective_history_round_trips_through_filesystem_sqlite_and_archive',
    'tests.test_artifact_proposal_orchestration.ArtifactProposalOrchestrationTests.test_empty_core_status_evidence_requires_selected_fresh_zero_byte_source',
    'tests.test_artifact_objectives.ArtifactObjectiveContractTests.test_history_rejects_foreign_or_changed_origin_predecessor_sources_and_targets',
    'tests.test_provider_timeout_recovery.ProviderTimeoutRecoveryTests.test_matching_unfinished_legacy_operation_retires_native_context',
    'tests.test_artifact_objectives.ArtifactObjectiveContractTests.test_v1_objective_wire_round_trips_without_changing_legacy_bytes',
    'tests.test_provider_timeout_recovery.ProviderTimeoutRecoveryTests.test_failure_closure_write_error_keeps_history_gated',
    'tests.test_provider_timeout_recovery.ProviderTimeoutRecoveryTests.test_attached_reference_cancellation_closes_and_releases_for_explicit_retry',
    'tests.test_specialist_orchestration.SpecialistExecutionAcceptanceTests.test_domain_rejection_details_survive_restarted_finalization',
    'tests.test_artifact_proposal_orchestration.ArtifactProposalOrchestrationTests.test_revised_artifact_intent_replaces_candidate_in_the_same_step',
    'tests.test_specialist_orchestration.SpecialistExecutionAcceptanceTests.test_recovered_reply_retries_transient_validation_publication',
    'tests.test_specialist_orchestration.SpecialistExecutionAcceptanceTests.test_one_and_two_assignments_run_with_exact_parallel_width',
    'tests.test_lead_orchestration.LeadRecoveryAndAccountingTests.test_restart_after_completed_execution_accepts_a_new_lead_turn',
    'tests.test_provider_timeout_recovery.ProviderTimeoutRecoveryTests.test_revision_timeout_keeps_superseded_confirmation_non_authorizing',
    'tests.test_specialist_routing_runtime.SpecialistRoutingRuntimeTests.test_draft_task_cannot_skip_current_revision_drafting_via_review_artifact_intent',
    'tests.test_lead_evidence_grounding.RuntimeLeadEvidenceGroundingTests.test_empty_whole_is_evidence_but_missing_and_invalid_range_fail',
    'tests.test_specialist_routing_runtime.SpecialistRoutingRuntimeTests.test_empty_target_adequate_brief_produces_draft_without_writes',
    'tests.test_artifact_proposal_orchestration.ArtifactProposalOrchestrationTests.test_reviewed_history_transfers_with_filesystem_sqlite_parity',
    'tests.test_artifact_proposal_orchestration.ArtifactProposalOrchestrationTests.test_incomplete_core_workflow_is_reviewed_only_after_confirmation',
    'tests.test_lead_evidence_grounding.RuntimeLeadEvidenceGroundingTests.test_zero_hit_source_drift_blocks_confirmation_before_specialists',
    'tests.test_lead_evidence_grounding.RuntimeLeadEvidenceGroundingTests.test_durable_zero_hit_history_contains_no_search_or_source_content',
    'tests.test_lead_orchestration.LeadRecoveryAndAccountingTests.test_pre_submission_correction_failure_closes_first_rejected_call',
    'tests.test_specialist_routing_runtime.SpecialistRoutingRuntimeTests.test_archived_task_is_context_without_routable_authority',
    'tests.test_lead_orchestration.LeadRecoveryAndAccountingTests.test_response_correction_requires_capacity',
    'tests.test_lead_orchestration.LeadRecoveryAndAccountingTests.test_untrusted_answer_diagnostic_cannot_publish_private_text',
    'tests.test_recommendation_actions.RecommendationActionContractTests.test_readable_and_session_context_expose_exact_origin_without_private_authority',
    'tests.test_provider_timeout_recovery.ProviderTimeoutRecoveryTests.test_corrupt_failed_history_cannot_admit_provider_work',
    'tests.test_interactive_controller.InteractiveLeadAdapterTests.test_recommendation_controls_route_exact_decisions_and_cancel_without_mutation',
    'tests.test_interactive_controller.InteractiveLeadAdapterTests.test_recommendation_revision_cancel_and_eof_preserve_pending_request',
    'tests.test_specialist_orchestration.SpecialistExecutionAcceptanceTests.test_execution_delivers_only_each_assignments_ordered_reference_subset',
    'tests.test_specialist_orchestration.SpecialistExecutionAcceptanceTests.test_second_round_uses_reply_events_fresh_authority_and_cannot_chain_again',
    'tests.test_specialist_orchestration.SpecialistExecutionAcceptanceTests.test_concurrent_duplicate_domain_and_schema_reports_keep_call_order',
    'tests.test_recommendation_actions.RecommendationActionContractTests.test_recommendation_and_objective_v2_round_trip_through_both_stores_and_archive',
    'tests.test_recommendation_actions.RecommendationActionContractTests.test_history_rejects_foreign_origin_tail_and_nonatomic_action_batches',
    'tests.test_specialist_orchestration.SpecialistExecutionAcceptanceTests.test_specialist_and_synthesis_validation_details_are_private_and_correlated',
    'tests.test_specialist_orchestration.SpecialistExecutionAcceptanceTests.test_synthesis_contract_is_semantic_and_runtime_derives_attribution',
    'tests.test_specialist_orchestration.SpecialistExecutionAcceptanceTests.test_restarted_finalization_restores_only_this_execution_diagnostics',
    'tests.test_recommendation_actions.RecommendationActionContractTests.test_action_values_and_decisions_are_closed_exact_and_immutable',
    'tests.test_specialist_orchestration.SpecialistExecutionAcceptanceTests.test_outer_cancellation_joins_tasks_persists_terminals_and_cleans_marker',
    'tests.test_specialist_orchestration.SpecialistExecutionAcceptanceTests.test_synthesis_derives_non_supporting_inputs_locally',
    'tests.test_specialist_orchestration.SpecialistExecutionAcceptanceTests.test_synthesis_cancellation_preserves_specialist_result_and_never_reruns_it',
    'tests.test_specialist_orchestration.SpecialistExecutionAcceptanceTests.test_submitted_uncertainty_enters_unknown_and_never_replays',
    'tests.test_specialist_orchestration.SpecialistExecutionAcceptanceTests.test_persistent_validation_publication_failure_keeps_result_unknown',
    'tests.test_specialist_orchestration.SpecialistExecutionAcceptanceTests.test_failed_specialist_retries_transient_validation_publication',
    'tests.test_specialist_orchestration.SpecialistExecutionAcceptanceTests.test_confirm_starts_one_atomic_dispatch_batch_then_runs_three_in_parallel',
    'tests.test_lead_orchestration.LeadRecoveryAndAccountingTests.test_original_and_repair_validation_details_persist_across_restart',
    'tests.test_specialist_orchestration.SpecialistExecutionAcceptanceTests.test_assignment_failure_joins_siblings_and_skips_synthesis',
    'tests.test_specialist_orchestration.SpecialistExecutionAcceptanceTests.test_start_append_failure_makes_zero_specialist_calls_and_leaks_no_marker',
    'tests.test_lead_orchestration.LeadRecoveryAndAccountingTests.test_confirmed_restart_preserves_evidence_and_requires_fresh_authority',
    'tests.test_lead_orchestration.LeadOutcomeAndProposalTests.test_non_string_lead_kind_reports_exact_type_and_common_issues',
    'tests.test_lead_orchestration.LeadOutcomeAndProposalTests.test_kind_bearing_lead_result_reports_common_anyof_issues',
    'tests.test_lead_orchestration.LeadDecisionAuthorityTests.test_confirm_is_exact_single_use_and_executes_without_artifact_effect',
    'tests.test_lead_orchestration.LeadRecoveryAndAccountingTests.test_huge_invalid_array_truncates_safe_durable_report',
    'tests.test_lead_orchestration.LeadRecoveryAndAccountingTests.test_republishing_domain_notice_is_idempotent',
    'tests.test_lead_orchestration.LeadRecoveryAndAccountingTests.test_long_validation_details_survive_history_restart_in_chunks',
    'tests.test_lead_orchestration.LeadRecoveryAndAccountingTests.test_validation_details_do_not_grant_action_authority',
    'tests.test_lead_orchestration.LeadRecoveryAndAccountingTests.test_second_returned_rejection_closes_against_last_call',
    'tests.test_lead_orchestration.LeadOutcomeAndProposalTests.test_runtime_constructs_complete_ordered_content_free_proposal',
    'tests.test_recommendation_actions.RecommendationActionContractTests.test_scoped_request_digest_and_legacy_absent_scope_remain_distinct',
    'tests.test_lead_orchestration.LeadRecoveryAndAccountingTests.test_confirmed_live_capability_preserves_exact_evidence_without_dispatch',
    'tests.test_lead_orchestration.LeadRecoveryAndAccountingTests.test_restored_results_keep_strict_evidence_and_provider_free_compatibility',
    'tests.test_specialist_routing_runtime.SpecialistRoutingRuntimeTests.test_legacy_pending_confirmation_requires_fresh_scoped_proposal_without_rewrite',
    'tests.test_recommendation_actions.RecommendationActionContractTests.test_legacy_judgments_synthesis_and_runtime_notices_never_gain_action_authority',
    'tests.test_specialist_routing_runtime.SpecialistRoutingRuntimeTests.test_wrong_task_pair_rejects_before_dispatch',
    'tests.test_lead_orchestration.LeadRecoveryAndAccountingTests.test_no_repair_capacity_preserves_validation_notice_without_authority',
    'tests.test_lead_orchestration.LeadRecoveryAndAccountingTests.test_domain_rejection_without_repair_persists_safe_notice',
    'tests.test_lead_orchestration.LeadReferenceFailurePrecedenceTests.test_reference_failure_with_unwritable_history_stays_recovery_required',
    'tests.test_lead_orchestration.LeadDecisionAuthorityTests.test_reference_drift_blocks_confirmation_without_calls',
    'tests.test_lead_orchestration.LeadRecoveryAndAccountingTests.test_minimal_reference_answer_needs_no_provider_correction',
    'tests.test_interactive_controller.InteractiveLeadAdapterTests.test_retry_lead_turn_keeps_fixed_diagnostic_copy_and_renders_error_role',
    'tests.test_specialist_orchestration.SpecialistExecutionAcceptanceTests.test_provider_free_advisory_confirms_one_specialist_without_synthesis_or_chain',
    'tests.test_lead_orchestration.LeadRecoveryAndAccountingTests.test_unresolved_running_becomes_outcome_unknown_without_provider',
    'tests.test_lead_orchestration.LeadBudgetFinalizationTests.test_initial_budget_rejection_remains_failure',
    'tests.test_lead_orchestration.LeadRecoveryAndAccountingTests.test_capability_is_private_nonserializable_and_single_use',
    'tests.test_provider_auth_recovery.ContextRecoveryTests.test_backend_route_session_thread_and_workspace_drift_veto_replay',
    'tests.test_specialist_routing_runtime.SpecialistRoutingRuntimeTests.test_provider_free_advisory_requires_scope_and_separate_confirmation',
    'tests.test_provider_auth_recovery.ContextRecoveryTests.test_local_reference_load_does_not_consume_provider_capacity',
    'tests.test_provider_auth_recovery.ContextRecoveryTests.test_replay_requires_explicit_exact_true_validator',
    'tests.test_provider_auth_recovery.ContextRecoveryTests.test_admission_race_abort_keeps_original_accounting_and_exchanges',
    'tests.test_provider_auth_recovery.ContextRecoveryTests.test_unsafe_unconfirmed_and_repeated_auth_failure_never_replay',
    'tests.test_provider_auth_recovery.ContextRecoveryTests.test_source_drift_vetoes_replay_before_submission',
    'tests.test_provider_auth_recovery.ContextRecoveryTests.test_auth_cancel_cleans_validator_and_retains_prior_call_facts',
    'tests.test_provider_auth_recovery.ContextRecoveryTests.test_noninteractive_auth_errors_and_restart_never_replay',
    'tests.test_interactive_controller.InteractiveLeadAdapterTests.test_provider_invocation_failure_writes_error_notice_without_crashing',
    'tests.test_interactive_controller.InteractiveLeadAdapterTests.test_artifact_and_commit_controls_are_distinct_and_correlated',
    'tests.test_interactive_controller.InteractiveLeadAdapterTests.test_pending_commit_redisclosure_reuses_exact_picker_without_apply',
    'tests.test_interactive_controller.InteractiveLeadAdapterTests.test_unexpected_exception_in_submit_propagates_to_fatal_boundary',
    'tests.test_interactive_controller.InteractiveLeadAdapterTests.test_run_operation_returns_task_result_when_task_completes_after_auth_ready',
    'tests.test_interactive_controller.InteractiveLeadAdapterTests.test_failure_interruption_and_recovery_turns_never_replay_authority',
    'tests.test_interactive_controller.InteractiveLeadAdapterTests.test_ordinary_lead_progress_sink_finishes_before_lead_and_elapsed_output',
    'tests.test_interactive_controller.InteractiveLeadAdapterTests.test_confirmed_execution_writes_specialists_then_lead_then_elapsed',
    'tests.test_interactive_controller.InteractiveLeadAdapterTests.test_provider_invocation_failure_surfaces_transient_stream_error_in_final_output',
)


HOSTED_METHOD_IDS = tuple(sorted(HOSTED_LAUNCH_IDS))


HOSTED_MIXED_SERIAL_IDS = (
    'tests.test_specialist_routing.SpecialistRoutingTests.test_canonical_artifact_and_semantic_domain_ownership',
    'tests.test_specialist_routing.SpecialistRoutingTests.test_drafted_is_neither_supports_nor_legacy_review_assessment',
    'tests.test_specialist_routing.SpecialistRoutingTests.test_legacy_proposal_bytes_stay_omitted_and_new_scope_changes_digest',
    'tests.test_specialist_routing.SpecialistRoutingTests.test_mixed_paths_and_semantics_union_without_inventory_inference',
    'tests.test_specialist_routing.SpecialistRoutingTests.test_published_recommended_action_position_and_keyword_only_scope_are_preserved',
    'tests.test_specialist_routing.SpecialistRoutingTests.test_review_requires_disclosed_concrete_basis_while_draft_forbids_it',
    'tests.test_specialist_routing.SpecialistRoutingTests.test_scope_and_review_basis_codecs_are_closed_and_round_trip',
    'tests.test_specialist_routing.SpecialistRoutingTests.test_scope_is_strict_versioned_immutable_and_content_safe',
)


HOSTED_PRIORITY_ID = HOSTED_LAUNCH_IDS[0]


METHOD_TOKEN_ENV = "STORY_TEST_METHOD_CHILD_TOKEN"


def hosted_launch_order(scheduled):
    rank = {identifier: index for index, identifier in enumerate(HOSTED_LAUNCH_IDS)}
    return tuple(sorted(scheduled, key=rank.__getitem__))


def method_manifest_equal(value, ids):
    return (type(value) is dict and type(value.get("count")) is int
            and type(value.get("ordered_ids")) is list and type(value.get("digest")) is str
            and all(type(item) is str for item in value["ordered_ids"]) and value == manifest(ids))


def execution_metadata(report):
    value = report.get("execution")
    if "execution" not in report:
        return ("serial", None, "sum-observed-shard-walls")
    keys = {"version", "mode", "profile", "profile_manifest", "serial_manifest", "scheduled_manifest", "capacity",
            "peak_children", "launched", "completed", "cleanup_complete", "timing_basis", "infrastructure_errors",
            "launch_manifest", "work_deadline_epoch", "cleanup_deadline_epoch"}
    ids = tuple(required_dict(report, "selection_manifest")["ordered_ids"])
    scheduled = tuple(identifier for identifier in ids if identifier in HOSTED_METHOD_IDS)
    serial = tuple(identifier for identifier in ids if identifier not in HOSTED_METHOD_IDS)
    if (type(value) is not dict or set(value) != keys or type(value["version"]) is not int or value["version"] != 1
            or value["mode"] != "hosted-whole-method" or value["profile"] != HOSTED_PROFILE
            or not method_manifest_equal(value["profile_manifest"], HOSTED_METHOD_IDS)
            or not method_manifest_equal(value["serial_manifest"], serial)
            or not method_manifest_equal(value["scheduled_manifest"], scheduled)
            or not method_manifest_equal(value["launch_manifest"], hosted_launch_order(scheduled))
            or any(type(value[key]) not in (int, float) or not math.isfinite(value[key]) or value[key] <= 0
                   for key in ("work_deadline_epoch", "cleanup_deadline_epoch"))
            or value["cleanup_deadline_epoch"] != value["work_deadline_epoch"] + HOSTED_CLEANUP_SECONDS
            or any(type(value[key]) is not int for key in ("capacity", "peak_children", "launched", "completed"))
            or not 1 <= value["capacity"] <= 4 or not 0 <= value["peak_children"] <= value["capacity"]
            or value["launched"] != len(scheduled) or value["completed"] != len(scheduled)
            or (bool(scheduled) != bool(value["peak_children"])) or value["cleanup_complete"] is not True
            or value["infrastructure_errors"] != [] or value["timing_basis"] != "observed-parent-wall-and-test-sum"):
        raise RunnerError("invalid hosted execution metadata or incomplete cleanup")
    return (value["mode"], value["profile"], "sum-observed-shard-walls")

