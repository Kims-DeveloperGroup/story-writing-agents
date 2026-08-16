# Codex Project Instructions

## Project Role

Read [README.md](README.md) before beginning work. Treat it as the source of
truth for the project objective, supported language, contribution guidance, and
folder structure.

This repository contains a Python multi-agent runtime for writing novels.
Develop, maintain, test, and document that runtime. Do not write novel content
unless the task explicitly requires it for an example, fixture, or test.

The repository-development agents under `.codex/agents/` are separate from
the four story-runtime role prompts under `config/agents/`. Project agents do
not participate in novel-writing debates, and story-runtime roles do not own
repository-development orchestration.

## Repository Engineering Consumer Facts

Use these project-owned facts for the repository-engineering workflow:

- Canonical repository: `Kims-DeveloperGroup/story-writing-agents`
- GitHub Project owner: `Kims-DeveloperGroup`
- GitHub Project number: `1`
- Independently selected profiles: `core`, `github`, `python`

The selected-profile declaration above is intentional. Once generated, the
repository-engineering lock is authoritative for the installed selection and
must agree with this independent declaration.

## Project-Owned Boundary

Repository-specific facts in this file outside the installed managed block
remain project-owned. Runtime code under `src/story_writing_agents/`, editable
story roles under `config/agents/`, packaging metadata, and runtime
configuration are not repository-engineering projections.

Root `<LANGUAGE>_MODULARITY_POLICY.md` overrides and root
`<LANGUAGE>_MODULE_INDEX.md` files are project-owned. The current Python
navigation record is [PYTHON_MODULE_INDEX.md](PYTHON_MODULE_INDEX.md); the
installed packaged default is
[PYTHON_MODULARITY_POLICY.md](.codex/agents/modularity_maintainer/PYTHON_MODULARITY_POLICY.md).

## Managed Workflow References

Use the installed managed policies and runbook for repository work:

- [GITHUB_WORKFLOW.md](GITHUB_WORKFLOW.md) defines GitHub planning and delivery.
- [AGENT_TASK_RUNBOOK.md](AGENT_TASK_RUNBOOK.md) defines tracked branch and
  worktree execution.
- [COMMIT_POLICY.md](COMMIT_POLICY.md) defines commit units and evidence.
- [DEPENDENCY_POLICY.md](DEPENDENCY_POLICY.md) governs Python dependency and
  packaging changes.

The development-agent definitions in `.codex/agents/` are installed
repository-engineering projections. Consumer-specific facts belong here or in
the README, not in those managed files.

<!-- repository-engineering:start -->
## Repository Engineering Workflow

### Working Method

For every change:

1. Inspect the repository and relevant existing behavior.
2. Clarify requirements, scope, constraints, acceptance criteria, and the
   GitHub-planning and modularity gates.
3. Pass every required gate before a writer starts.
4. Make the smallest complete change that satisfies the requirement.
5. Run proportionate checks and review the final diff.

For explanation, review, diagnosis, or planning, remain read-only unless the
request also authorizes implementation. For requests to build, change, or fix,
make the requested in-scope changes and verify them. Preserve unrelated
changes. Avoid speculative abstractions, unnecessary dependencies, and changes
to behavior outside the requested scope. Ask when a missing requirement would
materially change the result. Do not expand scope or make a breaking
compatibility decision without approval.

### GitHub Planning Gate

When the `github` profile is selected, record
`github_planning_gate = not_required | pending | passed | blocked` before a
task branch, worktree, write-capable agent, or tracked-file edit, together with
the classification reason. Read-only repository and GitHub inspection,
requirements, diagnosis, test planning, and modularity analysis may continue
while the gate is pending.

Treat work as large when it has Size `L` or `XL`; two or more independently
complete implementation slices or parent integration criteria; at least three
production modules or two responsibility domains; modularity or migration
coordination; dependency sequencing; multiple owners, worktrees, or delivery
phases; or a parent integration branch. Companion tests and documentation do
not count toward these thresholds. Uncertainty defaults to large unless a
maintainer records a smaller classification.

Large work must satisfy the selected GitHub workflow's planning requirements
before any writer starts. Verify the root Issue, all known descendants, native
relationships, canonical Project items, required metadata, approved base, and
first executable leaf. If a missing gate or new scope appears after writing
starts, stop further writes, preserve and report the existing diff, and
initialize that scope before continuing.

### Development Agents

Use project-scoped agents when their expertise improves the result:

- `requirements_analyst`: inspect the repository, clarify requirements, and
  define acceptance criteria before substantial or ambiguous work; classify
  the modularity gate.
- `regression_diagnostician`: reproduce failures, trace regressions, and
  determine whether their cause crosses a package or module boundary.
- `modularity_maintainer`: analyze, implement, or review cohesive module
  boundaries under the applicable language policy and keep project module
  indices synchronized.
- `runtime_implementer`: implement approved production-code, configuration,
  and integration changes within accepted boundaries.
- `test_engineer`: design, add, update, and run focused verification.
- `documentation_maintainer`: update README files, policies, guides,
  references, and examples after behavior and boundaries stabilize.

These are repository-development agents. Product or domain agents, when
present, remain separate unless the task explicitly puts them in scope.

### Modularity Gate

Set `modularity_gate = required` when work adds, removes, renames, moves,
splits, or merges an importable package or module; redistributes responsibility,
state, resources, or lifecycle ownership; changes dependency direction or
introduces or resolves a cycle, replaces private cross-boundary access, or
changes a package export or public import path; introduces or materially
changes a shared abstraction, adapter, facade, or protocol; extracts code for
multiple consumers; or requires a structural compatibility or migration
decision. A regression caused by architectural coupling or a boundary-policy
violation also requires the gate.

Use `modularity_gate = not_required` for localized work inside an established
module with unchanged imports, exports, ownership, dependencies, and public
contracts; tests-only or docs-only work; formatting, comments, or typing
cleanup; ordinary data or configuration edits; and dependency metadata alone.

When required, call `modularity_maintainer` with an explicit mode:

- `analysis`: read-only boundary design before writing.
- `implementation`: exclusive ownership of an accepted architecture-dominant
  structural change or an index-only reconciliation.
- `review`: read-only comparison of an implemented diff with the accepted
  brief, language policies, and module indices.

If a writer discovers an unapproved gate trigger, stop that writer and run
modularity analysis before continuing. Never let an implementation agent make
an incidental architecture decision silently.

Before modularity work, select one policy per affected implementation language.
Use a root `<LANGUAGE>_MODULARITY_POLICY.md` override when present; otherwise
use `.codex/agents/modularity_maintainer/<LANGUAGE>_MODULARITY_POLICY.md`. The
first match wins and policies are never merged. Each project module index is
the project-owned root `<LANGUAGE>_MODULE_INDEX.md`.

### Orchestration Sequence

1. The main agent classifies the request and records preliminary gate
   decisions.
2. For substantial or ambiguous work, run `requirements_analyst` read-only.
   For a reported or observed failure, run `regression_diagnostician`
   read-only. Independent investigations may run in parallel. Resolve an
   uncertain modularity gate before writing.
3. When modularity is required, run `modularity_maintainer` in `analysis` mode,
   pass it relevant requirements and diagnosis findings, and have it select and
   verify the affected root `<LANGUAGE>_MODULE_INDEX.md` files. Resolve user
   decisions and accept its brief, including the index delta, before writing.
4. After requirements and any boundary brief are accepted, pass the GitHub
   planning gate when selected and required. No writer starts while it is
   `pending` or `blocked`.
5. Keep exactly one production writer active. Use `modularity_maintainer` for
   architecture-dominant structural work and `runtime_implementer` for
   behavior-dominant work constrained by the brief.
6. If both production specialists are necessary, assign explicit,
   nonoverlapping files or symbols and run them sequentially. Prefer one owner
   when separate passes would create an invalid intermediate state.
7. `modularity_maintainer` owns module-index accuracy. When it owns structural
   implementation, update affected indices after the implementation is stable.
   When `runtime_implementer` owns production code, run a later sequential
   index-only implementation pass against the completed production diff.
8. When `runtime_implementer` implements a boundary brief, review the completed
   production diff and reconciled indices with `modularity_maintainer` before
   test or documentation edits. Return production deviations to the production
   owner and index drift to `modularity_maintainer`, then repeat until resolved
   or explicitly excepted. A separate self-review is unnecessary when
   `modularity_maintainer` performed the implementation; its report and the
   main agent's review provide the gate.
9. After production, APIs, and boundaries stabilize, `test_engineer` may plan
   read-only and then owns test edits sequentially;
   `documentation_maintainer` follows.
10. If tests or documentation change indexed verification or documentation
    references or reveal drift, run a final sequential index-only
    `modularity_maintainer` implementation pass followed by read-only review.
11. The main agent integrates results, checks index consistency, reviews the
    complete diff, runs final checks, and reports to the user.

The main agent is the coordination hub. Reuse specialist threads when
practical, pass distilled decisions rather than raw logs, and do not let peer
specialists delegate to, coordinate with, or silently redistribute ownership
among one another.

### Handoff and Result Contract

Every specialist handoff must include:

- `mode = analysis | implementation | review`;
- objective and relevant evidence;
- `github_planning_gate`, its rationale, and verified Issue, Project, and
  Iteration evidence when applicable;
- approved scope and explicit out-of-scope work;
- acceptance criteria and compatibility constraints;
- modularity-gate decision and accepted brief when applicable;
- applicable module-index paths, status, and accepted delta;
- read-only targets or exclusively owned files and symbols;
- upstream findings and accepted decisions;
- required checks and expected output; and
- stop conditions for missing or conflicting policy, breaking API choice,
  scope deviation, unexpected structural work, or overlapping ownership.

Every specialist result must report findings, files inspected or changed,
module indices consulted or updated, implementation/index consistency, public
API and compatibility impact, checks and results, blockers, and the recommended
next owner or action. Pass distilled decisions and evidence between agents
rather than raw logs.

A missing language policy blocks modularity work. The modularity maintainer
reports the exact language and expected packaged-policy path; the main agent
asks the user whether to create it and then resumes the same specialist thread.
If creation is declined, record that decision and that no language policy
governs that part of the work before proceeding under general engineering
judgment. A missing module index requires no separate user permission and does
not block read-only analysis: derive the complete inventory, record the root
index path and contents in the brief, and later create the project-owned root
index in an exclusive implementation pass. Structural work is incomplete
while an affected index is missing or stale.
<!-- repository-engineering:end -->
