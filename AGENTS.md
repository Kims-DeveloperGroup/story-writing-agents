# Codex Project Instructions

## Project Role

Read `README.md` first; it governs objective, language, contribution guidance, and structure. Maintain runtime; write fiction only when tasks explicitly require examples/fixtures/tests.

## Working Method

For every change, inspect behavior; clarify requirements/inventory/constraints/acceptance and gates; pass gates; make the smallest complete change; run authorized checks; inspect the diff. Explain/review/diagnose/plan requests authorize no changes; build/change/fix authorizes only scope. Preserve unrelated work; avoid speculation, unnecessary dependencies, and scope creep; ask about material ambiguity.

## GitHub Planning Gate

Before any task branch, worktree, write-capable agent, or tracked edit, record `github_planning_gate = not_required | pending | passed | blocked` and reason. Read-only inspection, requirements, diagnosis, test planning, and modularity analysis may proceed while pending.

Work is large if Size `L`/`XL`; it has two independently complete implementation slices or parent criteria; at least three production modules or two responsibility domains; modularity/migration coordination; dependency sequencing; multiple owners/worktrees/phases; or a parent integration branch. Companion tests/docs do not count. Uncertainty is large unless a maintainer records otherwise. Large work must pass [GITHUB_WORKFLOW.md](GITHUB_WORKFLOW.md): create/reuse and verify the root Issue, all known descendants, native relations, canonical Project items, and metadata before dispatching a writer. If missing/new scope appears after writing, stop writes, preserve/report the diff, and initialize it.

## Subagent Delegation

`.codex/agents/` contains the six development roles, distinct from the four `config/agents/` story roles:

- `requirements_analyst`: read-only requirements/acceptance; modularity-gate advice.
- `regression_diagnostician`: read-only reproduction/cause and boundary impact.
- `modularity_maintainer`: boundary analysis/implementation/review and exclusive semantic ownership of module indices.
- `runtime_implementer`: approved production/configuration implementation.
- `test_engineer`: exclusive test design/edits/execution and behavioral/coverage review.
- `documentation_maintainer`: exclusive README/policy/guide/example edits after behavior and boundaries stabilize.

### Delegation-Gain Gate

For every candidate, the main-agent critical-path hub records:

`predicted_critical_path_savings_seconds = root_sequential_estimate - (parallel_critical_path + spawn_wait_handoff_synthesis_overhead)`

Dispatch only for savings >=20 seconds and >=20% of `root_sequential_estimate`, or named `material_correctness_risk_reduction`/`mandatory_ownership_rule`. Mandatory ownership covers docs, tests, triggered modularity, and production outside root fast path; exceptions authorize, not imply savings. Root may implement only a clear localized one/two-file production/config change in an established responsibility/boundary—never docs, tests, required/uncertain modularity, substantial/unresolved ambiguity, or unknown failures. Dispatch requirements only for substantial ambiguity, diagnosis only for unknown failure, modularity only when applicable; never for a deterministic command. Batch safe root reads.

### Root-Capability Routing

Resolve root effective model/effort after aliases, defaults, session overrides, and child settings/pairs; TOMLs stay unpinned. `dispatch_context_tokens` includes only the `task_packet`, intentional inherited turns, attachments/preloads, and irreducible child material; record `observed|estimated|unavailable`, method, and confidence.

| Context | Tokens |
| --- | --- |
| `compact` | <=8,192 |
| `moderate` | 8,193--32,768 |
| `large` | 32,769--131,072 |
| `oversized` | >131,072, or no verified output/reasoning fit |

Unknown fit is `oversized`; narrow/split. Context cannot relax ceilings.

| Class | Trigger | Model | Effort |
| --- | --- | --- | --- |
| `routine` | mechanical/reversible; no unresolved judgment/material risk | `gpt-5.6-terra` | `medium` |
| `standard` | bounded semantic work; one established responsibility | `gpt-5.6-sol` | `high` |
| `complex` | ambiguity/unknown cause; multi-domain/boundary/public API/compatibility/migration/security/authorization/concurrency/process/PTY/lifecycle/persistence/resource ownership/difficult nonlocal edges | `gpt-5.6-sol` | `xhigh` |
| `critical` | two interacting complex triggers, or explicitly high-consequence security/data-loss/irreversible-migration/breaking-compatibility | `gpt-5.6-sol` | `max` |

Roles default `routine`; uplift to `complex`: requirements/diagnosis for substantial ambiguity/unknown cause; modularity for boundary work (index-only keeps actual class); runtime for security/concurrency/migration/public API/difficult edges; tests for security/concurrency/PTY/process/lifecycle/complex coverage; docs for normative governance/security/compatibility/migration. Compact/moderate context floors model at Luna; large raises it to Sol. Risk controls effort. Derive independently:

```text
desired_model = max(task_model_target, role_model_target, context_model_target)
desired_effort = max(task_effort_target, role_effort_target)
capped_model = min(desired_model, root_effective_model)
capped_effort = min(desired_effort, root_effective_effort)
```

Only `gpt-5.6-luna < gpt-5.6-terra < gpt-5.6-sol` is initially comparable; never assume other ordering. Effort is `none < low < medium < high < xhigh < max`; `ultra` only when runtime-exposed as comparable/selectable. The cap is minimum quality; each selected axis must also be <=root. Choose exact cap, else nearest stronger compatible pair within root (fewest upward steps; tie: smaller model, then effort increase). Never auto-select below cap; weaker-only returns to root. Set both overrides or neither. Unresolved root/order/availability/support requires exact inheritance; model-only may default effort. `ceiling_limited` means either cap is below desired: high-consequence work must narrow/require stronger root; otherwise proceed compatibly only with residual risk. Record root/source; context count/class/method/confidence; complexity/triggers; role target; desired/capped/selected/compatible pairs; limitation; fallback/inheritance reason. Keep packets narrow, with exclusions, decisions, observable criteria, evidence level `observed|inferred|proposed|unknown`, and uncertainty stops. Sources: [subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents), [models](https://developers.openai.com/api/docs/guides/latest-model).

### Role, Mode, and Control Gate

Validate role, mode, and observed effective capability:

| Role | Supported mode | Required observed effective capability |
| --- | --- | --- |
| `requirements_analyst` | `analysis` | repository/worktree reads; effectively read-only filesystem; no approval-enabled writes |
| `regression_diagnostician` | `analysis` | same read-only capability |
| `modularity_maintainer` | `analysis`, `review` | same read-only capability |
| `modularity_maintainer` | `implementation` | assigned-worktree writes |
| `runtime_implementer` | `implementation` | assigned-worktree writes |
| `test_engineer` | `analysis` | same read-only capability |
| `test_engineer` | `implementation` | assigned-worktree writes |
| `documentation_maintainer` | `analysis` | same read-only capability |
| `documentation_maintainer` | `implementation` | assigned-worktree writes |

Child first performs only minimum identity/capability preflight and reports filesystem/sandbox, writes, and approval path. Reject unsupported mode, unavailable evidence, or mismatch before other reads/writes. This is observed fail-closed routing, not isolation enforcement by prompts/TOMLs/tests.

Before first tracked write, match physical `pwd -P`, Git root, branch, exact HEAD, approved-base SHA/ancestry, exclusive targets, and exact bytes, SHA-256, and decoded paths from `git status --porcelain=v1 -z --untracked-files=all`. Accept dirty state only if all three were pinned. Any mismatch stops without reset/checkout/clean/revert. After upstream change, interruption, or invalidated identity, root sends updated identity/fingerprint and the same writer repeats full preflight.

Use bounded self-contained context. Full history requires named reason, bounded relevant scope, stale-context risk, explicit root approval, and all four in task evidence. Root records every result ID and role/mode; join only when none is running/missing and every exact `result_packet` validates. Retry a transient spawn/delivery/join failure exactly once only after proving no write and recording its fingerprint. Possible writer mutation requires state inspection, diff preservation, and same-thread resume—never blind retry. Policy/capability/scope/schema failures are non-transient. Resolve conflicts by `observed > inferred > proposed > unknown` within role ownership, never vote; escalate to owner, root, then user for required scope/breaking/external decisions. After failed retry root may only do authorized static reads/fast-path work; it never absorbs mandatory ownership or creates a reviewer role.

### Worktree Disposition

Nest exactly one under task `identity`: `remove_after_integration` only with cleanup authorization; `retain_with_reason` plus reason/next owner; or `authorization_required`. Change only after recorded authority/lifecycle change. Completion evidence inventories exact path, branch, HEAD, porcelain bytes/hash/paths, Issue/PR state, commit/PR recoverability, cleanup authorization state, and actual removal/retention. Removal requires authorization, clean status, completed integration verification, recoverability, non-forced removal, and verified absence from registry/filesystem. Branch deletion is separate. Retain parent until integrated into `main`. Keep packet schemas unchanged: preflight/disposition under `identity`; cleanup/recoverability under `evidence`; outcome, reason, risk, next owner under `findings`, `risks`, `next_action`.

### Modularity Gate

Use `modularity_maintainer` when adding/removing/renaming/moving/splitting/merging an importable package/module; redistributing feature/state/resource/lifecycle ownership; changing dependency direction/cycles/private boundary access; materially changing shared abstraction/adapter/facade/protocol/export/public import; extracting for multiple consumers; requiring structural compatibility/migration; or fixing architectural coupling/boundary violations. Exclude localized established-responsibility/internal refactors with unchanged imports/exports/ownership/contracts; tests/docs; formatting/comments/typing; ordinary config/data; dependency metadata alone; story execution.

Modes: `analysis` for read-only design before writers; `implementation` for exclusive approved architecture-dominant change; `review` after another agent's boundary change. An unapproved trigger stops its writer for analysis; no incidental architecture decisions.

### Orchestration Sequence

1. Root classifies request, gates, inventory, delegation estimates; resolves modularity `uncertain` before writers.
2. Run requirements for ambiguity and diagnosis for unknown failure; justified independent investigations may parallelize.
3. If modularity applies, analysis selects/verifies root `<LANGUAGE>_MODULE_INDEX.md`; root resolves decisions and accepts boundary brief/index delta.
4. Accept requirements/brief; pass planning gate or record `not_required`; verify native Development-linked branch. Never write while pending/blocked.
5. Choose root fast path or one production specialist; one production writer at a time. Modularity owns architecture-dominant structure; runtime owns behavior.
6. If both are needed, assign nonoverlapping files/symbols sequentially; prefer one owner when separation creates invalid intermediate state.
7. After stable code, modularity owner updates indices; after runtime work, sequential index-only modularity reconciles them.
8. After runtime implements a brief, modularity reviews code/indices before tests/docs. Return code deviations to production owner and index drift to modularity; repeat until resolved/accepted. Modularity implementer needs no self-review; its report plus root review suffice.
9. After production stability, test owner exclusively plans/edits/runs/reviews, preferably one turn; pause/resume same thread for dependencies. Docs remain documentation owner's exclusive work.
10. If tests/docs change indexed references or expose drift, run sequential index-only modularity implementation then read-only review.
11. Root integrates results, checks index consistency/diff/final checks, and owns user summary.

Reuse specialist threads unless stale/isolation risk, recording exceptions. Root passes compact decisions; specialists neither delegate nor coordinate.

### Adaptive Verification

Root exclusively selects, authorizes, budgets, deduplicates, reuses, escalates, and accepts sufficiency. Specialists may propose but never broaden level, selection, concurrency, or budget. Unknown/over-budget cost returns `split|narrow|defer|stop` or requires explicit maintainer budget approval.

Selectable partial order: `static_inventory` (mandatory for every nontrivial tracked change: inputs, direct tests, transitive consumers, helpers/fixtures, invariants, risks, exclusions); `exact` (changed acceptance and known failures); `semantic_cluster` (shared invariant/contract/fixture/family); `affected_suite` (bounded subsystem only when narrower is insufficient); `full_suite` (only an objective trigger below). `complete` is terminal, not level six: all mandatory evidence current green/accepted-skip, failures resolved, inventory sufficient, and root accepts residual risk. A pass never authorizes the next level; complete checklist items only with current exact mandatory evidence.

| `verification_class` | `total_stage_budget_seconds` | `max_shard_seconds` | Normal evidence floor |
| --- | ---: | ---: | --- |
| `local` | 300 | 180 | inventory + exact/static |
| `bounded` | 900 | 300 | inventory + exact + required semantic |
| `cross_cutting` | 1800 | 600 | inventory + selected cluster/affected |
| `release` | Issue-defined | Issue-defined | named integration/release gates |

Accepted criteria may override. Budgets authorize work, never waive correctness or impose unsafe kills. Do not start a shard estimated above remaining budget; allow safe completion/runner-supported preserving interrupt if over estimate. Budget exhaustion never excuses mandatory evidence.

Before commands, root records class/rationale; criteria-to-`mandatory_evidence`; all changed inputs and affected inventory/exclusions; flags for API, packaging, persistence/migration, auth/security, concurrency, process/PTY, shared config, dynamic coupling; exact `authorized_selection`/rationale; estimates/source/confidence; total/max/spent/remaining; escalation/full-suite triggers; stops; reuse/invalidation; residual risk. Unknown dependencies require broader inventory or diagnosis, not an assumption of no impact.

Every result records repository, absolute worktree, baseline/HEAD, dirty hash, environment capability, purpose, level, selection/command, outcome/duration, fingerprint, and conservative `relevant_input_digest` over tests, helpers/fixtures, runner config, transitive production closure, and environment. Reuse after HEAD change only when root proves disjointness and digest/purpose remain; relevant/uncertain changes invalidate it. Assign one `evidence_execution_owner` per item. Deduplicate exactly on `normalized_command`, `exact_ids`, `relevant_input_digest`, `environment_class`; owner/label/purpose/venue/wrapper cannot justify rerun. Normally exact/cluster run locally, affected/full in CI; duplicate broad local+CI only for a local-environment criterion, recording both.

Selections above max shard become deterministic disjoint portable exact-ID shards (unittest module/class/method), union exactly selection, each ID once; risk-bearing/fast feedback first. Sequential is default; parallel needs root authorization, isolation, nonoverlap, runner support, and at most two fast-fail shards. Record manifest digest, ordinal, IDs, input digest, result, duration, `completed_ids`, `failed_ids`, `accepted_skips`, `unexecuted_ids`; aggregate success covers every ID. After repair rerun exact failures and invalidated shards. Timeout/replacement/interruption is neither pass nor assertion failure: stop only owned process, classify/preserve IDs, resume only valid unexecuted/invalidated IDs. Partial evidence is never complete. A bounded recorded repeat may diagnose flakiness; relabeling cannot evade deduplication.

`full_suite` requires: explicit Issue/parent/release criterion; unbounded static inventory; changed package-wide/public/cross-domain invariant; global discovery/bootstrap/packaging/harness making narrow checks unreliable; unknown out-of-cluster failure; or uncovered concrete residual risk. Implementation/review/suite availability is insufficient. If required broad work exceeds budget, return the decision, never omit/claim completion. Docs/prompt/TOML/policy-only work may use static parsing, exact policy tests, link/format, and diff checks.

For known failure repair then rerun invalid evidence. If one fingerprint occurs in >=2 responsibility holders, stop broad reruns and inventory repository-wide occurrences with roots/exclusions/patterns/classifications/limits. Cross-boundary capability/ownership defects apply one modularity gate. Unknown failures go to diagnosis. Unknown cost/exhaustion returns completed/remaining evidence, estimates, risk, and root choices. Existing six roles suffice: tests own evidence/coverage, diagnosis owns causes, modularity owns boundary review, root owns risk.

### Handoff and Result Contracts

Every compact `task_packet` has exactly: `mode` (`analysis|implementation|review`); `objective` (outcome/evidence); `scope` (in/out targets); `acceptance` (criteria/compatibility); `ownership` (read-only/exclusive targets); `gates` (planning/modularity rationale, evidence, brief/index delta); `identity` (repository, Issue/Project/Iteration, Development branch/PR, worktree, base/head); `verification` (authorized plan/ledger/checks/budget/output); `upstream_decisions`; `stop_conditions` (policy/breaking/scope/structural stops).

Every compact `result_packet` has exactly: `outcome`, `findings`, `changed_inputs`, `evidence`, `risks`, `next_action`. Findings include files and index consistency. Evidence includes mandatory evidence, authorized selection, commands, completed/failed/skipped/invalidated/unexecuted IDs, duration/budget, identities/digests/fingerprints/checks/next evidence. Risks include blockers, ambiguity, residual risk, and proposed expansion requiring root authorization. Do not copy raw logs/full Issues/plans. Thread reuse does not authorize history.

Missing language policy: modularity reports language/path; root asks whether to create packaged policy and resumes same thread. If declined, general judgment requires recording user decision and missing policy. Missing index does not block analysis/need separate permission: derive full inventory/path/content, then same specialist creates it index-only. Structural work cannot complete with stale/missing index.

### Prospective Orchestration Telemetry

For comparable tracked tasks capture `root_phase_wall_time_seconds`, `subagent_startup_seconds`, `subagent_wait_seconds`, `turn_count`, `tool_call_count`, `duplicate_read_count`, `verification_time_seconds`, `input_tokens`, `output_tokens`, each as `observed|estimated|unavailable`; unavailable needs reason and estimates are never observations. Prospectively target >=25% lower median wall time, >=15% lower P95, >=30% fewer turns, >=50% fewer duplicate broad-suite executions, and 25--35% fewer combined packet tokens, without correctness, safety, ownership, verification, GitHub, or modularity regression. Static policy tests prove shape, not performance.

## Quality and Commits

Update tests for behavior and docs for public/setup/workflow changes; inspect/report diff and verification. Tracked work follows [GITHUB_WORKFLOW.md](GITHUB_WORKFLOW.md) and [AGENT_TASK_RUNBOOK.md](AGENT_TASK_RUNBOOK.md). [COMMIT_POLICY.md](COMMIT_POLICY.md) requires focused/complete/reviewable commits. Before structural module/package change use `modularity_maintainer` and its [language-policy selection](.codex/agents/modularity_maintainer.toml); defaults live under `.codex/agents/modularity_maintainer/`. Third-party Python dependency/packaging changes follow [DEPENDENCY_POLICY.md](DEPENDENCY_POLICY.md), including verification.
