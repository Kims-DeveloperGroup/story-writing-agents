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
   criteria, then classify the GitHub and modularity gates.
3. Pass the GitHub planning gate when required.
4. Make the smallest complete change that satisfies the requirement.
5. Run relevant tests or checks and review the final diff.

For requests to explain, review, diagnose, or plan, do not make changes unless
the request also authorizes implementation. For requests to build, change, or
fix, make the requested in-scope local changes and verify them.

Preserve unrelated user changes. Avoid speculative abstractions, unnecessary
dependencies, and changes to behavior outside the requested scope. Ask when a
missing requirement would materially change the design or result.

## GitHub Planning Gate

Before a task branch, worktree, write-capable agent, or tracked-file edit,
record `github_planning_gate = not_required | pending | passed | blocked` with
the classification reason. Read-only repository and GitHub inspection,
requirements, diagnosis, test planning, and modularity analysis may run while
the gate is pending.

Classify work as large when any of these applies: estimated Size `L` or `XL`;
two or more independently complete implementation slices or parent integration
criteria; at least three production modules or two responsibility domains;
modularity or migration coordination; dependency sequencing; multiple owners,
worktrees, or delivery phases; or a parent integration branch. Companion tests
and documentation do not count toward the thresholds. Treat uncertainty as
large unless a maintainer records a smaller classification.

Large work must pass the initial planning gate in
[GITHUB_WORKFLOW.md](GITHUB_WORKFLOW.md). Do not dispatch a writer until the
root Issue, all known descendants, native relationships, canonical Project
items, and required metadata have been created or reused and verified. If a
missing gate or new scope is discovered after writing starts, stop further
writes, preserve and report the existing diff, and initialize that scope before
continuing.

## Subagent Delegation

The agents in `.codex/agents/` develop this repository. They are distinct from
the four story-runtime agents under `config/agents/` and must not participate
in novel-writing debates.

Use the project agents when their focused expertise improves the result:

- `requirements_analyst`: inspect the repository, clarify requirements, and
  define acceptance criteria before substantial or ambiguous work; report
  whether the modularity gate is required.
- `regression_diagnostician`: reproduce failures, trace regressions, and report
  whether the cause crosses a package or module boundary.
- `modularity_maintainer`: analyze, implement, or review cohesive package and
  module boundaries under the applicable language policy, and keep the
  project module indices synchronized with implementation.
- `runtime_implementer`: implement approved production-code or configuration
  changes within an accepted boundary design.
- `test_engineer`: design, add, update, and run tests for the change.
- `documentation_maintainer`: update README files, policies, guides, and
  examples after behavior and boundaries stabilize.

### Modularity Gate

Call `modularity_maintainer` when a change does any of the following:

- adds, removes, renames, moves, splits, or merges an importable package or
  module;
- redistributes feature responsibility, state, resource, or lifecycle
  ownership;
- changes dependency direction, introduces or resolves a cycle, or replaces
  private cross-boundary access;
- adds or materially changes a shared abstraction, adapter, facade, protocol,
  package export, or public import path;
- extracts code for multiple consumers or requires a structural compatibility
  or migration decision; or
- fixes a regression whose diagnosed cause is architectural coupling or a
  boundary-policy violation.

Do not call it for a localized change inside an established module
responsibility; an internal function refactor with unchanged imports, exports,
ownership, and contracts; tests-only or docs-only work; formatting, comments,
or typing cleanup; ordinary configuration or data edits; dependency metadata
alone; or story-runtime execution.

When the gate applies, call the custom agent by the name
`modularity_maintainer` and set `mode` in the handoff:

- `analysis`: read-only boundary design before any writer starts;
- `implementation`: exclusive ownership of an approved architecture-dominant
  structural change; or
- `review`: read-only compliance review after another agent implements a
  boundary-affecting change.

If a writer discovers an unapproved gate trigger, stop that writer and run
modularity analysis before continuing. Do not allow an implementation agent to
make an incidental architecture decision silently.

### Orchestration Sequence

1. The main agent classifies the request and records preliminary GitHub-planning
   and modularity-gate decisions.
2. For substantial or ambiguous work, `requirements_analyst` runs read-only.
   For reported or observed failures, `regression_diagnostician` runs
   read-only. Independent investigations may run in parallel. The main agent
   must resolve `modularity_gate = uncertain` to `required` or `not_required`
   before any writer starts.
3. When the gate applies, `modularity_maintainer` runs in `analysis` mode after
   it receives relevant requirements and diagnosis results. It selects and
   verifies the relevant root-level `<LANGUAGE>_MODULE_INDEX.md` files.
   The main agent resolves user decisions and accepts the resulting boundary
   brief, including its index delta, before writing begins.
4. After requirements and any boundary brief are accepted, the main agent
   passes the GitHub planning gate or records `not_required`. No writer starts
   while the gate is `pending` or `blocked`.
5. Exactly one agent owns production writes at a time. Use
   `modularity_maintainer` for architecture-dominant splits, moves, merges, and
   extractions. Use `runtime_implementer` for behavior-dominant work constrained
   by an accepted boundary brief.
6. If both production specialists are necessary, assign explicit, nonoverlapping
   files or symbols and run them sequentially. Prefer one owner when separate
   passes would create an invalid intermediate state.
7. `modularity_maintainer` owns the semantic accuracy of module indices. When
   it owns the structural implementation, it updates affected indices after the
   code is stable. When `runtime_implementer` owns production code, run a
   sequential `modularity_maintainer` implementation pass that owns only the
   affected indices and reconciles them to the completed production diff.
8. When `runtime_implementer` implements a boundary brief,
   `modularity_maintainer` then reviews the completed production diff and
   reconciled indices in `review` mode before test or documentation edits.
   Return production deviations to the assigned production owner and index
   drift to `modularity_maintainer`, then repeat the affected implementation,
   index-reconciliation, and review passes until material violations are
   resolved or an exception is explicitly accepted. A separate self-review is
   not required when `modularity_maintainer` performed the implementation; its
   implementation report and the main agent's review provide the gate.
9. `test_engineer` performs test planning read-only when useful, then owns test
   edits sequentially after production stabilizes. `documentation_maintainer`
   follows after behavior, APIs, and boundaries are stable.
10. If test or documentation work changes an indexed verification or
   documentation reference, or reveals any other drift, run a final sequential
   `modularity_maintainer` index-only implementation pass followed by read-only
   review.
11. The main agent integrates all results, verifies module-index consistency,
   reviews the complete diff, runs final checks, and owns the user-facing
   summary.

Reuse the same specialist thread for follow-up phases when practical. The main
agent remains the hub: it passes concise upstream decisions to the next agent,
and specialists do not delegate to or coordinate directly with peers.

### Handoff and Result Contracts

Every subagent handoff must include:

- `mode`: `analysis`, `implementation`, or `review`;
- objective and relevant evidence;
- `github_planning_gate`, its rationale, and verified Issue, Project, and
  Iteration evidence when applicable;
- approved scope and explicit out-of-scope work;
- acceptance criteria and compatibility constraints;
- the `modularity_gate` decision and accepted brief, when applicable;
- applicable module-index paths, status, and accepted index delta;
- read-only targets or exclusively owned files and symbols;
- upstream findings and accepted decisions;
- required checks and output; and
- stop conditions, including missing or conflicting policies, breaking API
  decisions, scope deviation, and unexpected structural work.

Every result must report findings, files inspected or changed, module indices
consulted or updated, implementation/index consistency, checks run, blockers,
and the recommended next action. Pass distilled decisions and evidence between
agents rather than raw logs.

If a required language policy is missing, the modularity agent returns the
exact language and expected path. The main agent asks the user whether to
create the packaged policy, then resumes the same agent thread. If creation is
declined, proceeding under general engineering judgment requires that user
decision and the lack of a governing policy to be recorded in the result.

A missing module index does not block read-only analysis or require separate
user permission. The modularity agent derives the complete current inventory,
records the root index path and contents in the brief, and the main agent then
runs the same specialist in an index-only `implementation` pass to create it.
Structural work is not complete while an affected module index is missing or
stale.

## Quality and Commits

Add or update tests when behavior changes. Update documentation when public
behavior, setup, or workflow changes. Before finishing, check the diff for
unintended changes and report what was verified.

For work tracked on GitHub, read and follow
[GITHUB_WORKFLOW.md](GITHUB_WORKFLOW.md). It defines Issue readiness, Project
status, milestones, iterations, labels, branch and pull-request traceability,
and completion rules. Execute tracked tasks with
[AGENT_TASK_RUNBOOK.md](AGENT_TASK_RUNBOOK.md), including its parent/child
branch, squash-merge, and worktree procedure.

The commit policy is a repository workflow document at
[COMMIT_POLICY.md](COMMIT_POLICY.md). A commit is a unit of a complete function
or logic; keep commits focused, complete, and reviewable.

Before adding, moving, splitting, merging, or materially changing an internal
package or module, use `modularity_maintainer` and follow the language-policy
selection process in
[its agent definition](.codex/agents/modularity_maintainer.toml). Packaged
language defaults live under `.codex/agents/modularity_maintainer/`.

Before adding, updating, or removing a third-party Python dependency, or
changing project packaging metadata, read and follow
[DEPENDENCY_POLICY.md](DEPENDENCY_POLICY.md). Dependency and packaging changes
must comply with that policy and include the required verification.
