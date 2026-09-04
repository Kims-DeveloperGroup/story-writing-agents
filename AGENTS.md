# Codex Project Instructions

## Project Role

Read governing `README.md` first. Maintain runtime; fiction requires task-requested examples/fixtures/tests.

## Working Method

Inspect behavior; define requirements/inventory/constraints/acceptance/gates; pass gates; minimally implement/verify/diff-review. Explain/review/diagnose/plan is read-only. Preserve unrelated work; avoid speculation/dependencies/scope creep; ask ambiguity.

## GitHub Planning Gate

Before branches/worktrees/write-capable-agents/tracked-edits, record `github_planning_gate = not_required | pending | passed | blocked`/reason. Pending permits only read-only inspection/requirements/diagnosis/test-planning/modularity-analysis. Large means Size `L|XL`, >=2 independent slices/parent criteria, >=3 production modules, >=2 domains, modularity/migration/dependency sequencing, multiple owners/worktrees/phases, or parent integration; companion tests/docs excluded; uncertainty defaults large. Per [GITHUB_WORKFLOW.md](GITHUB_WORKFLOW.md), verify root/all descendants/native relations/Project items/metadata before writers. Missing/new scope stops writes for diff preservation/reporting/initialization.

## Subagent Delegation

Six `.codex/agents/` development roles never join four `config/agents/` story roles:

| Role | Exclusive/primary duty |
|---|---|
| `requirements_analyst` | read-only substantial ambiguity/acceptance/gate advice |
| `regression_diagnostician` | read-only unknown-failure cause/boundary impact |
| `modularity_maintainer` | boundaries; exclusive module-index semantics |
| `runtime_implementer` | approved production/config implementation |
| `test_engineer` | exclusive tests/execution/behavioral-coverage review |
| `documentation_maintainer` | exclusive README/policy/guide/example edits after stabilization |

### Delegation-Gain Gate

Root hub records estimates and
`predicted_critical_path_savings_seconds = root_sequential_estimate - (parallel_critical_path + spawn_wait_handoff_synthesis_overhead)`.
Dispatch only when savings >=20 seconds and >=20%, or record `material_correctness_risk_reduction|mandatory_ownership_rule` (docs/tests/triggered-modularity/production outside fast path). Root fast path is only clear localized <=2-file production/config work inside established responsibility/boundary; it excludes mandatory ownership, ambiguity, unknown failure. Never dispatch for deterministic commands; batch safe root reads.

### Root-Capability Routing

Resolve root model/effort after aliases/defaults/session/child settings; TOMLs unpinned. Count packet+intentional-history+attachments/preloads+irreducible child; record `observed|estimated|unavailable`, method/confidence. Context: `compact <=8,192`; `moderate 8,193--32,768`; `large 32,769--131,072`; `oversized >131,072|unverified-fit` (narrow/split). Context changes model only and never root ceilings.

| Class | Model/effort | Trigger |
|---|---|---|
| `routine` | `gpt-5.6-terra`/`medium` | mechanical/reversible/no-material-risk |
| `standard` | `gpt-5.6-sol`/`high` | bounded semantic/one responsibility |
| `complex` | `gpt-5.6-sol`/`xhigh` | ambiguity/cause/boundary/API/compatibility/migration/security/authorization/concurrency/process/PTY/lifecycle/persistence/resource/nonlocal |
| `critical` | `gpt-5.6-sol`/`max` | two complex triggers or high-consequence security/data-loss/irreversible-migration/breaking-compatibility |

Roles default routine. Complex uplift: requirements/diagnosis ambiguity/unknown-cause; modularity boundary; runtime security/concurrency/migration/API/nonlocal; tests security/concurrency/PTY/process/lifecycle/complex-risk; docs governance/security/compatibility/migration. Index-only stays actual class. Compact/moderate floor `gpt-5.6-luna`; large floor `gpt-5.6-sol`.

```text
desired_model=max(task_model_target,role_model_target,context_model_target)
desired_effort=max(task_effort_target,role_effort_target)
capped_model=min(desired_model,root_effective_model)
capped_effort=min(desired_effort,root_effective_effort)
```

Comparable: `gpt-5.6-luna<gpt-5.6-terra<gpt-5.6-sol`; effort `none<low<medium<high<xhigh<max` (`ultra` only if runtime-comparable). Select exact cap, else nearest stronger within each root ceiling (fewest upward steps; tie smaller model then effort). Never select weaker; weaker-only returns root. Override both axes or neither; model-only may default effort; unresolved root/order/availability/support inherits. `ceiling_limited` high-consequence work narrows/requires stronger root; otherwise record risk. Record root/source, context measurement/class, triggers/role target, desired/capped/selected/compatible pairs, limitation/fallback. Packets bound decisions/exclusions/criteria with `observed>inferred>proposed>unknown` evidence.

### Role, Mode, and Control Gate

| Role | Mode | Capability |
|---|---|---|
| requirements/diagnosis | `analysis` | read-only filesystem; no approval-enabled writes |
| modularity | `analysis|review` / `implementation` | read-only / assigned-worktree writes |
| runtime | `implementation` | assigned-worktree writes |
| tests/docs | `analysis` / `implementation` | read-only / assigned-worktree writes |

Child first reports observed filesystem/sandbox/write/approval capability and identity; unavailable/mismatched role-mode-capability stops before other reads/writes. Before first tracked write match `pwd -P`, Git root/branch/exact HEAD/base/ancestry, exclusive targets, and exact bytes/SHA-256/decoded paths of `git status --porcelain=v1 -z --untracked-files=all`; dirty state requires all three pinned. Never reset/checkout/clean/revert. Identity invalidation requires root update and same-writer re-preflight.

Use bounded context; full history requires reason/bound/stale-risk/root-approval evidence. Pre-record result IDs+role/mode; join only when all valid/finished. Retry transient spawn/delivery/join once after no-write proof/fingerprint; possible writer mutation requires inspect/preserve/same-thread resume. Never retry policy/capability/scope/schema failures. Resolve conflicts by evidence/role ownership, escalating owner→root→user. Root never absorbs mandatory ownership/adds reviewer roles; specialists never delegate/coordinate.

### Worktree Disposition

Task `identity` contains exactly one: `remove_after_integration` (pre-authorized), `retain_with_reason` (reason/next owner), or `authorization_required`; change only after recorded authority/state change. Completion inventories path/branch/HEAD/porcelain/Issue-PR/recoverability/authority/outcome. Removal requires authorization+clean+integrated+recoverable, non-force, verified registry/filesystem absence; branch deletion is separate; retain parent until `main`.

### Modularity Gate

Use modularity for package/module add/remove/rename/move/split/merge; responsibility/state/resource/lifecycle redistribution; dependency direction/cycles/private-boundary access; shared abstraction/adapter/facade/protocol/export/public-import changes; multi-consumer extraction; structural compatibility/migration; architectural-coupling regression. Exclude localized unchanged-contract refactors, tests/docs/format/comments/typing/config/data/dependency-metadata/story execution. Modes: `analysis` before writers, exclusive `implementation` for approved architecture-dominant work, `review` after another boundary writer. Unexpected triggers stop for analysis. Missing language policy requires user decision; missing index is derived then created index-only; structural work never completes with stale/missing indices.

### Orchestration Sequence

Root: (1) classify scope/gates/inventory/delegation; resolve uncertain modularity; (2) run requirements/diagnosis only when triggered; (3) accept modularity brief/index delta; (4) pass planning and verify Development branch; (5) choose fast path or one production owner; (6) if two production specialists are necessary, give nonoverlapping sequential ownership; (7) stabilize code then modularity reconciles indices; (8) review runtime boundary work and iterate deviations; (9) stabilize then exclusive test and docs owners work, resuming threads after pauses; (10) reconcile/review index drift from tests/docs; (11) integrate, verify, summarize. Reuse threads unless stale/isolation-risk.

### Adaptive Verification

Root owns selection/authorization/budget/reuse/escalation/sufficiency; specialists only propose expansion. Partial-order levels: `static_inventory` (mandatory nontrivial: inputs/tests/consumers/helpers/fixtures/invariants/risks/exclusions), `exact` (acceptance/known-failures), `semantic_cluster` (shared-invariant/contract/fixture/family), `affected_suite` (bounded-blast-radius if narrower insufficient), `full_suite` (triggered below). `complete` is terminal only with current mandatory green/accepted-skips, resolved failures, sufficient inventory, accepted residual risk.

| Class | Total/max-shard seconds | Floor |
|---|---|---|
| `local` | 300/180 | inventory+exact/static |
| `bounded` | 900/300 | +required semantic |
| `cross_cutting` | 1800/600 | selected cluster/affected |
| `release` | Issue-defined | named gates |

Plan criteria→evidence, changed/affected inputs/exclusions/risks, exact selection+owner, estimates/confidence, total/max/spent/remaining, stops/triggers/reuse/risk. Deduplicate exactly by `normalized_command,exact_ids,relevant_input_digest,environment_class`; reuse only root-proven disjoint changes with stable purpose/digest. Shards are deterministic/disjoint/exhaustive exact IDs; sequential unless root authorizes isolated nonoverlap, maximum two fast-fail. Ledger provenance/digests/duration and `completed_ids|failed_ids|accepted_skips|unexecuted_ids`; interruption is no verdict; resume valid remainder. Run `full_suite` only for explicit Issue/release criterion, unbounded inventory, changed package/public/cross-domain invariant, unreliable global harness, unknown out-of-cluster failure, or uncovered concrete risk. Unknown failure→diagnosis; cross-boundary defect→modularity; budget/cost exhaustion returns remaining evidence/options, never completion.

### Handoff and Result Contracts

Exact `task_packet`: `mode,objective,scope,acceptance,ownership,gates,identity,verification,upstream_decisions,stop_conditions`. Exact `result_packet`: `outcome,findings,changed_inputs,evidence,risks,next_action`. Nest preflight/disposition under `identity`; cleanup/recoverability under `evidence`; disposition outcome/reason/risk/owner under findings/risks/next action. Keep packets bounded; no logs/full Issues/plans.

### Prospective Orchestration Telemetry

Record wall/startup/wait/turn/tool/duplicate-read/verification/token metrics as `observed|estimated|unavailable`. Targets: median -25%, P95 -15%, turns -30%, duplicate suites -50%, packet tokens -25--35%, without correctness/safety/ownership/gate regression.

## Quality and Commits

Follow [GITHUB_WORKFLOW.md](GITHUB_WORKFLOW.md), [AGENT_TASK_RUNBOOK.md](AGENT_TASK_RUNBOOK.md), focused/complete [COMMIT_POLICY.md](COMMIT_POLICY.md), modularity language policy, and [DEPENDENCY_POLICY.md](DEPENDENCY_POLICY.md) before Python dependency/packaging changes; update tests/docs and report diff/checks.
