# Issue 462 integration conformance

This record maps the retained #436 workflow requirements to executable,
provider-free evidence on the integrated parent branch. It distinguishes the
three user controls that are easy to conflate:

```text
Confirm consultation -> permits the named specialist calls for one proposal
Apply artifacts      -> permits the runtime to write the reviewed exact changes
Commit story changes -> permits the runtime to stage and create one local commit
```

No control authorizes another, free-form agent text has no authority, and the
Lead requests each operation while runtime code alone performs reads, provider
calls, artifact writes, Git staging, and Git commit execution.

## Scenario evidence

| User scenario | Required external behavior | Exact automated evidence |
| --- | --- | --- |
| Empty, missing, incomplete, or conflicting core evidence | The Lead reports the evidence state, avoids unsupported claims, and proposes completion or reconciliation before writing. | `DurableLeadEvidenceMatrixTests.test_all_sixty_scope_status_kind_cells_are_closed_and_exact`; `DurableLeadEvidenceMatrixTests.test_missing_search_scope_and_conflict_distinctness_fail_closed`; `RuntimeLeadEvidenceGroundingTests.test_empty_whole_is_evidence_but_missing_and_invalid_range_fail`; `ArtifactProposalOrchestrationTests.test_incomplete_core_workflow_is_reviewed_only_after_confirmation` |
| Write or edit core documents | The Lead selects only affected reviewers, preserves every assessment and disagreement, synthesizes a proposal, and waits for confirmation. | `ArtifactProposalOrchestrationTests.test_incomplete_core_workflow_is_reviewed_only_after_confirmation`; `ArtifactProposalOrchestrationTests.test_core_conflict_revision_and_manuscript_atomic_rename_workflows`; `ArtifactProposalOrchestrationTests.test_all_assessments_are_preserved_and_partial_failure_never_reviews` |
| Edit manuscripts | Existing core and manuscript evidence is checked before the reviewed exact change can be applied atomically. | `ArtifactProposalOrchestrationTests.test_core_conflict_revision_and_manuscript_atomic_rename_workflows`; `LeadArtifactApplicationTests.test_atomic_multi_file_apply_has_exact_provenance_and_no_git_effect`; `LeadArtifactApplicationTests.test_prewrite_identity_base_and_relationship_drift_are_zero_write` |
| Ordinary discussion | The Lead may answer directly when no specialist is useful and does not create an artifact snapshot or specialist call. A material unresolved choice remains a clarification/revision outcome. | `ArtifactProposalOrchestrationTests.test_ordinary_discussion_bypasses_snapshot_and_specialists`; `LeadOutcomeAndProposalTests.test_all_five_outcomes_have_exact_visible_next_actions`; `LeadOutcomeAndProposalTests.test_direct_search_read_answer_uses_bounded_history_without_proposal` |
| Confirm consultation | Only the exact, current, single-use proposal can dispatch the named specialists within the disclosed call budget. | `LeadDecisionAuthorityTests.test_confirm_is_exact_single_use_and_executes_without_artifact_effect`; `LeadDecisionAuthorityTests.test_mismatch_reject_revise_and_free_text_are_non_authorizing`; `SpecialistExecutionAcceptanceTests.test_confirm_starts_one_atomic_dispatch_batch_then_runs_three_in_parallel` |
| Apply reviewed changes | Apply, Reject, and Revise are separate structured decisions; Apply performs exact runtime writes but no Git staging or commit. | `LeadArtifactApplicationTests.test_request_is_exact_visible_and_free_form_text_has_no_authority`; `LeadArtifactApplicationTests.test_reject_and_revise_are_structured_zero_write_terminals`; `LeadArtifactApplicationTests.test_atomic_multi_file_apply_has_exact_provenance_and_no_git_effect` |
| Commit applied changes | Commit needs fresh exact authority, uses the user's Git identity, stages only the candidate, and performs no remote or destructive operation. | `LeadStoryCommitTests.test_request_is_inspectable_and_reject_is_zero_git_mutation`; `LeadStoryCommitTests.test_exact_commit_uses_user_identity_and_no_remote_or_destructive_git`; `LeadStoryCommitTests.test_mismatch_and_prestaged_drift_fail_before_commit` |
| Interrupt or restart | Durable state is inspected before continuation and consumed authority is never reconstructed or replayed. | `LeadRecoveryAndAccountingTests.test_unstarted_prepared_and_submitted_requests_never_replay`; `LeadArtifactApplicationTests.test_restart_cannot_reconstruct_apply_authority`; `LeadStoryCommitTests.test_restart_cannot_reconstruct_commit_authority`; consensus execution/event-store restart tests |
| First interactive session | Lobby startup leaves the initialization root clean, then explicit Create provisions and attaches the session. | `InteractiveCliPtyTests.test_bare_entry_creates_session_then_quit_releases_only_access` |
| Visible collaboration | Every durable event has one structured semantic presentation; artifact proposal, review, Apply, local Commit, and paired push are distinguishable without private bytes. | `CollaborationViewTests`; `ArtifactAndGitCollaborationViewTests.test_runtime_artifact_and_local_commit_events_are_complete_and_safe` |

## Boundary and inventory evidence

- `python -m ruff check src tests` validates the supported source/test surface.
- `python -m compileall -q src tests` validates import-time syntax.
- `InteractiveTerminalFoundationTests` derives the complete production module
  graph and requires zero cycles.
- workspace, package-boundary, facade, and module-index tests require exact
  public exports and one `PYTHON_MODULE_INDEX.md` row per production module.
- collaboration contract tests require all schema-v2 event kinds and history
  projection fields introduced by Apply and Commit integration.
- searches for removed `/run`, direct-run symbols, migration, and compatibility
  surfaces are part of the public CLI/API and documentation inventory tests.

Remote push and merge behavior in #441/#450/#451 and the separate documentation
consolidation leaf #461 are the explicitly recorded Not Planned exclusions for
this delivery. The retained local Git Commit workflow remains required and is
covered above.

## Verification policy

The final warning-strict suite is split into nonoverlapping, resumable
selections. A failed node is rerun directly after correction; a completed valid
selection is not repeated after unrelated documentation or test-only changes.
The issue checklist is checked only after every retained selection and the
final graph/index/API checks pass on the integrated head.
