---
name: subagent-routing
description: >-
  Development-subagent routing: validates/evaluates/dispatches/controls/joins/retries packets;
  excludes story/root-only work.
---

`predicted_critical_path_savings_seconds=root_sequential_estimate-(parallel_critical_path+spawn_wait_handoff_synthesis_overhead)`

Require >=20s and >=20% of root estimate, or name
`material_correctness_risk_reduction|mandatory_ownership_rule`; exceptions authorize, not
savings. Mandatory owners: docs/tests/triggered-modularity/production outside fast path.
Fast path permits clear one/two-file production/configuration within established
responsibility/boundary;
excludes mandatory owners/ambiguity/unknown failures. Never dispatch deterministic commands.
Load `$subagent-model-selection` for model/effort.

|Role|Dispatch|
|---|---|
|`requirements_analyst`|Substantial-ambiguity-only|
|`regression_diagnostician`|Unknown-failure-only|

RO: requirements/diagnosis `analysis`; modularity `analysis|review`; tests/docs `analysis`.
RW: modularity/runtime/tests/docs `implementation`.

RO means read-only without approval-enabled writes; RW means assigned-worktree writes. Child
first reports filesystem/sandbox/write availability/approval path/identity; reject unsupported
mode, missing evidence, or mismatch before other work. Route observed capability fail-closed;
prompts/TOMLs/tests cannot enforce isolation.

Writers match physical path/Git root/branch/HEAD/base ancestry/targets and exact porcelain
bytes/SHA-256/decoded paths; pin all three. Never reset/checkout/clean/revert.
Re-preflight after change/interruption/invalidation. Full-history requires recorded
reason/bounded scope/stale-risk assessment/root approval.

Pre-record result IDs/role/mode; join after none runs/missing and all packets validate.
Retry transient spawn/delivery/join once with fingerprint/no-write proof. Possible writes
require inspection/diff preservation/same-thread resume; policy/capability/scope/schema never
retry. After failed retry, root does only authorized static/fast-path work. Resolve
`observed>inferred>proposed>unknown` within ownership; never vote; escalate owner/root/user.

|Disposition|Meaning|
|---|---|
|`remove_after_integration`|Cleanup-authority-granted|
|`retain_with_reason`|Reason/next-owner|
|`authorization_required`|Removal-authority-absent/unresolved|

Nest under task `identity`; change only after recorded authority/state.

Missing policy: report language/path; root asks about packaged creation, resumes thread,
records declined general-judgment exceptions. Missing
index needs no permission: derive inventory/path/content; same-thread index-only creation.
Select/verify root `<LANGUAGE>_MODULE_INDEX.md`.
Runtime boundary work needs index reconciliation/review before tests/docs; return deviations
until resolved/accepted. Modularity implementation needs its report plus root review. Tests
precede docs; reconcile drift. Specialists never coordinate/delegate; reuse unless stale/unsafe.

|`task_packet` field|Content|
|---|---|
|`mode`|`analysis/implementation/review`|
|`objective`|Concrete-outcome/relevant-evidence|
|`scope`|Approved-in/out-targets|
|`acceptance`|Criteria/compatibility|
|`ownership`|Read-only/exclusive-targets|
|`gates`|GitHub/modularity-decisions/rationale/evidence/accepted-boundary/index-status/delta|
|`identity`|Repository/Issue/Project/Iteration/Development-linked-branch-or-PR/worktree/base/head/preflight/disposition|
|`verification`|Authorized-adaptive-plan/compact-ledger/required-checks/budget/expected-output|
|`upstream_decisions`|Distilled-findings/accepted-decisions|
|`stop_conditions`|Policy-conflict/breaking-decision/scope-deviation/unexpected-structure/other-explicit-returns|

|`result_packet` field|Content|
|---|---|
|`outcome`|Completed-work/precise-blocked-or-stopped-state|
|`findings`|Distilled-findings/files/indices/implementation-index-consistency|
|`changed_inputs`|Relevant-source/test/fixture/config/policy/docs/environment|
|`evidence`|`mandatory_evidence,authorized_selection,exact_commands,completed_ids,failed_ids,accepted_skips,invalidated_evidence,unexecuted_ids,actual_duration,spent_and_remaining_budget,identities,digests,failure_fingerprints,checks,recommended_next_evidence`;cleanup/recoverability|
|`risks`|Blockers/ambiguity/residual/proposed-expansions-requiring-root-authorization|
|`next_action`|Recommended-owner/action|

Exclude logs/full Issues/plans; reuse never authorizes history.

Record `root_phase_wall_time_seconds,subagent_startup_seconds,subagent_wait_seconds,turn_count,tool_call_count,duplicate_read_count,verification_time_seconds,input_tokens,output_tokens`
as `observed|estimated|unavailable`; explain unavailable; estimates aren't observations.
Reduction targets:
`median>=25%,P95>=15%,turns>=30%,duplicate-broad-suites>=50%,packet-tokens=25--35%`.
Static tests don't prove performance.
