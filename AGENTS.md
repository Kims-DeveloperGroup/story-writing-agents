# Codex Project Instructions

## Project Role

Before beginning work, read `README.md` first. Treat it as the source of truth
for the project objective, language, contribution guidelines, and folder
structure.

This repository contains a Python multi-agent runtime that supports writing
novels. Your role in this repository is to develop, maintain, test, and
document the runtime. Do not write novel content unless the task explicitly
requires it for an example, fixture, or test.

## Working Method

For every change:

1. Inspect the repository and the relevant existing behavior.
2. Clarify the requirement, affected components, constraints, and acceptance
   criteria.
3. Make the smallest complete change that satisfies the requirement.
4. Run relevant tests or checks and review the final diff.

For requests to explain, review, diagnose, or plan, do not make changes unless
the request also authorizes implementation. For requests to build, change, or
fix, make the requested in-scope local changes and verify them.

Preserve unrelated user changes. Avoid speculative abstractions, unnecessary
dependencies, and changes to behavior outside the requested scope. Ask when a
missing requirement would materially change the design or result.

## Subagent Delegation

Use the project agents in `.codex/agents/` when their focused expertise will
improve the result:

- `requirements_analyst`: inspect the repository, clarify requirements, and
  define acceptance criteria before substantial or ambiguous work.
- `runtime_implementer`: implement approved production-code or configuration
  changes.
- `test_engineer`: design, add, update, and run tests for the change.
- `documentation_maintainer`: update README files, policies, guides, and
  examples affected by the change.
- `regression_diagnostician`: reproduce failures, trace regressions, and
  identify their cause.

Delegate independent read-only investigations in parallel when useful. Keep
write-capable work sequential: coordinate one owner at a time to avoid
conflicting edits. The main agent remains responsible for requirements,
decisions, integration, final verification, and the user-facing summary.

Every subagent handoff should include:

- Objective and relevant context
- Constraints and acceptance criteria
- Files or behavior to inspect or change
- Required output and verification evidence

Every subagent result should report findings, files inspected or changed,
checks run, blockers, and the recommended next action.

## Quality and Commits

Add or update tests when behavior changes. Update documentation when public
behavior, setup, or workflow changes. Before finishing, check the diff for
unintended changes and report what was verified.

The commit policy is a repository workflow document at
[COMMIT_POLICY.md](COMMIT_POLICY.md). A commit is a unit of a complete function
or logic; keep commits focused, complete, and reviewable.

Before adding, updating, or removing a Python package, read and follow
[DEPENDENCY_POLICY.md](DEPENDENCY_POLICY.md). Dependency changes must comply
with that policy and include the required verification.
