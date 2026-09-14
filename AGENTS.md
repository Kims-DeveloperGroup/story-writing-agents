# Codex Project Instructions

## Project Role

Read `README.md` first; it governs objective/language/contributions/structure. Maintain this
novel-writing runtime; write fiction only for requested examples/fixtures/tests.

## Working Method

1. Inspect behavior; define requirements/inventory/constraints/scope/criteria/gates.
2. Explanation/review/diagnosis/planning authorize no change; implement only approved
   scope.
3. Change minimally; preserve unrelated work; verify/diff-review.

Ask when missing requirements materially change design/results. Skills supply procedures;
never override this file/policies.

## GitHub Planning Gate

Before branches/worktrees/writers/edits record
`github_planning_gate=not_required|pending|passed|blocked` plus reason. Pending/blocked
permits read-only work only. For GitHub-tracked work load `$github-task-lifecycle`; obey
[GITHUB_WORKFLOW.md](GITHUB_WORKFLOW.md).

Large means `L|XL`; two independent slices/parent criteria; three production modules; two
domains; modularity/migration coordination; dependency sequencing; multiple
owners/worktrees/phases; or parent integration. Tests/docs do not count; uncertainty is
large. Before writing verify graph/Project metadata. One-implementation/delivery-unit
plans stay on their Issue; direct-child-sets/subdivision require >=2 independently-complete
necessary siblings, created/verified together; otherwise enlarge/update current scope. New
scope stops writes; preserve diff and initialize it.

## Subagent Delegation

Six `.codex/agents/` development roles never join four `config/agents/` story roles. Never
add a reviewer role.

|Role|Ownership|
|---|---|
|`requirements_analyst`|Read-only requirements/acceptance/modularity advice|
|`regression_diagnostician`|Read-only reproduction/cause/boundary impact|
|`modularity_maintainer`|Boundaries; exclusive index semantics|
|`runtime_implementer`|Approved production/configuration|
|`test_engineer`|Exclusive test design/edit/run/behavior/coverage|
|`documentation_maintainer`|Exclusive docs/skills after stabilization|

### Delegation-Gain Gate

Load `$subagent-routing` before evaluating/dispatching subagents.

### Root-Capability Routing

Load `$subagent-model-selection` before selecting, requesting, overriding, or inheriting a
child model/reasoning effort.

### Role, Mode, and Control Gate

Load `$subagent-routing` before capability/identity preflight, handoff, join, retry,
interruption, or conflict handling.

### Worktree Disposition

Nest one `remove_after_integration|retain_with_reason|authorization_required` under task
`identity`. Load routing for identity and GitHub lifecycle for tracked cleanup.

### Modularity Gate

Require modularity for importable structure; responsibility/state/resource/lifecycle
ownership; dependency direction/cycles/private access; shared abstractions/adapters/
facades/protocols/exports/public imports; multi-consumer extraction/migration; or
architectural coupling. Exclude unchanged-contract local refactors, tests/docs, formatting,
typing, ordinary configuration/data, dependency metadata, and story runs.

Use pre-writer `analysis`, architecture-dominant `implementation`, or post-writer `review`.
Unexpected triggers stop writers. Load routing for policy/index procedures.

### Orchestration Sequence

1. Root resolves scope, gates, inventory, delegation, requirements/diagnosis, then any
   boundary brief/index delta.
2. Pass planning; use one production owner at a time; reconcile indices and boundary review.
3. Run exclusive tests before exclusive docs; reconcile later index drift.
4. Root validates packets, integrates, verifies, and summarizes.

### Adaptive Verification

Load `$adaptive-verification` before planning, running, reusing, escalating, or accepting
evidence for nontrivial tracked changes. Root owns authorization and sufficiency. Tests use
the canonical repository test runner `PYTHONPATH=src python -m tests.runner`; README runner
guidance is human-facing and non-normative.

### Handoff and Result Contracts

Load `$subagent-routing` for exact packets. Thread reuse never authorizes history.

### Prospective Orchestration Telemetry

Load `$subagent-routing` before telemetry.

## Quality and Commits

Follow [COMMIT_POLICY.md](COMMIT_POLICY.md). Before structural changes use the
[modularity selector](.codex/agents/modularity_maintainer.toml) and packaged defaults.
Follow/verify [DEPENDENCY_POLICY.md](DEPENDENCY_POLICY.md) for dependency/packaging work.
