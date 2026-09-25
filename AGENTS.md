# Codex Project Instructions

## Scope

Read [README.md](README.md) first. Maintain this novel-writing runtime. Write fiction only
for requested examples, fixtures, or tests.

## Operating Rules

1. Inspect current behavior and define scope, constraints, acceptance criteria, and gates.
2. Explanation, review, diagnosis, and planning are read-only unless implementation is
   explicitly requested.
3. Make the smallest coherent change, preserve unrelated work, and review the final diff.

Ask only when missing requirements would materially change the result. Skills provide
procedures but never override repository policy. Use the canonical index below instead of
duplicating detailed rules in root instructions or task packets.

## GitHub Gate

Before creating branches or worktrees or editing tracked files, record
`github_planning_gate=not_required|pending|passed|blocked` with the reason. Pending or blocked
permits read-only work only. Load `$github-task-lifecycle` and follow
[GITHUB_WORKFLOW.md](GITHUB_WORKFLOW.md). New scope repeats this gate.

## Canonical Index

| Concern | Canonical source or action |
| --- | --- |
| Project objective and structure | [README.md](README.md) |
| Runtime subsystem lookup | Start with [PYTHON_MODULE_INDEX.md](PYTHON_MODULE_INDEX.md), then verify the affected source. |
| GitHub planning, reviews, records, delivery, and cleanup | [GITHUB_WORKFLOW.md](GITHUB_WORKFLOW.md) through `$github-task-lifecycle`. |
| Test scope, inventory, and time budget | [TEST_POLICY.md](TEST_POLICY.md). |
| Explicit end-to-end Project workflow | Root loads `$project-workflow` only when the user invokes it; subagents never use it. |
| Development delegation | Keep `.codex/agents/` separate from `config/agents/`; load `$subagent-routing` before delegation. Agent profiles define role behavior. |
| Structural Python work | Route through `$subagent-routing`; use [PYTHON_MODULARITY_POLICY.md](.codex/agents/runtime_implementer/PYTHON_MODULARITY_POLICY.md). |
| Commits and dependencies | [COMMIT_POLICY.md](COMMIT_POLICY.md) and [DEPENDENCY_POLICY.md](DEPENDENCY_POLICY.md). |

## Verification

Runtime-code changes require affected tests. Runtime and test changes require the full CI
gate in [GITHUB_WORKFLOW.md](GITHUB_WORKFLOW.md); other changes need static validation and
diff review.
