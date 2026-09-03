# GitHub Workflow Policy

This policy defines how contributors and coding agents use GitHub. It
supplements [AGENTS.md](AGENTS.md) and
[COMMIT_POLICY.md](COMMIT_POLICY.md). Agents execute it with
[AGENT_TASK_RUNBOOK.md](AGENT_TASK_RUNBOOK.md).

## Authority and Authorization

Code, tests, and detailed docs define behavior; an Issue defines scope and
completion; Project and organization Issue fields hold planning metadata; and
a PR holds the diff, review, and evidence. Record maintainer decisions for
conflicts. Planning data must not silently redefine an Issue.

Relevant read-only inspection is allowed. An explicit implementation request
also authorizes only the initial-planning mutations below: create or reuse
in-scope Issues and a milestone, set native relationships, add items to the
canonical Project, set their planning metadata, and create the in-scope task
branch through the Issue's Development section after the gate passes.
Project-schema changes, unrelated Issue edits, staging, commits, other pushes,
PR actions, review, closure, merge, deletion, and cleanup still require explicit
authorization. Worktree removal and branch deletion are distinct actions and
require separate authority.

## Initial Planning Gate

Before task branches, worktrees, writers, or tracked edits, record
`github_planning_gate = not_required | pending | passed | blocked`. Only
read-only analysis may continue while it is pending.

Work is large when any applies: estimated Size `L`/`XL`; two or more
independently complete implementation slices or parent integration criteria;
at least three production modules or two responsibility domains; modularity or
migration coordination; dependency sequencing; multiple owners, worktrees, or
delivery phases; or a parent integration branch. Companion tests/docs do not
count; uncertainty is large.

Large work needs one root and all known descendants, including nested parents
and leaves, before implementation. Use native hierarchy and dependencies. Add
every item to [Project #1](https://github.com/orgs/Kims-DeveloperGroup/projects/1),
then set labels and organization Issue Priority plus Project Status and
Iteration on all items, and milestone and Project Size on each leaf. Verify
bodies, relationships, fields, approved base, and the first executable leaf
with a fresh read.

On partial failure, preserve IDs, repair only missing state, and set `blocked`;
never create duplicates or implementation edits. Gate later scope too.
`not_required` is limited to read-only work or an approved mechanical
exception. A nontrivial standalone implementation passes with one Issue and
Project item; it does not waive planning metadata.

## Issues

Nontrivial feature, bug, dependency, packaging, security, architecture,
workflow, or public-documentation work requires an Issue. A maintainer may
approve an exception for a mechanical correction.

Use parents for initiatives and leaves for independently complete functions. A
root defines its objective, non-goals, integration criteria, child inventory
and order, risks, evidence, and approved base. A leaf defines its root/parent,
Project, objective, context, in/out scope, testable completion, verification,
docs, components, dependencies, constraints, and risks.

Use native parent/sub-issue relationships for hierarchy and `blocked by` for
dependencies. Do not start with an unfinished prerequisite or expand scope
without recorded approval.

A parent closes only after all required children and its integration criteria
are complete. A child pull request must never close its parent.

## Planning Metadata

### Milestone

A milestone groups a release or deliverable. Every leaf has one active
milestone before `Ready`; a parent links it but normally remains outside it.
Name the outcome and record owner, exit criteria, exclusions, and any due date.
Close it after its required leaves complete and record deferrals.

### Iteration

Iteration is a logical initiative group, not a schedule. Use sequential,
never-reused `Iteration NN`. GitHub's required internal dates/duration never
control assignment, readiness, priority, or rollover.

Every Project item has one Iteration. The topmost root and all descendants
share it; standalone Issues are roots; linked PRs and new children inherit it.
`wave:NN` orders work inside it. Milestone remains the deliverable grouping.

Status never changes Iteration; there is no calendar rollover. Replanning moves
the whole root tree. `Active work` contains `Ready`, `In progress`, and
`In review`; `Missing iteration` must stay empty.

### Labels and Fields

Use labels only for durable classification:

- One work type: `bug`, `enhancement`, `documentation`, or `chore`
- One or more components: `area:<name>`
- Delivery sequence, when needed: `wave:NN`
- Active blockage: `blocked`

Do not create milestone, iteration, status, or priority labels. Use `wave:NN`,
not `priority:NN`. Use the organization Issue `Priority` field: `Urgent`,
`High`, `Medium`, or `Low`, defaulting to `Medium`. Use Project Size for
capacity and Estimate only with a defined unit.

## Project Status

| Status | Rule |
| --- | --- |
| `Backlog` | New, incomplete, reprioritized, or pre-start blocked work. |
| `Ready` | Accepted and unblocked, with required planning metadata assigned. |
| `In progress` | Authorized work started; assignee and a Development-linked branch or PR exist. Draft PRs remain here. |
| `In review` | A non-draft PR appears in Development and Project `Linked pull requests`, with verification evidence. |
| `Done` | Completion gates passed, approved work merged or exception completed, and Issue closed. |

Requested implementation changes return an item to `In progress`.

Blocked is an overlay, not a status. Add the native dependency, apply
`blocked`, and record the cause, unblock condition, owner, and next action.
Pre-start blocked work stays `Backlog`; interrupted active work stays
`In progress` unless reprioritized. Blocked work never becomes `Done`.

## Branches, Commits, and Pull Requests

Assign one accountable owner per branch and worktree, plus one integration
owner for a parent branch. Only one write-capable owner is active at a time.
Before its first tracked write, bind that owner to the exact identity and dirty
state required by [AGENTS.md](AGENTS.md#role-mode-and-control-gate). Preserve
unrelated changes; a mismatch never authorizes reset, checkout, cleaning, or
reversion.

Use `issue-<number>-<short-slug>` for nontrivial work. A standalone branch
identifies its leaf Issue. For a parent initiative, its branch is the
integration base for child Issue branches. Child PRs target the parent branch
and use **Squash and merge**; the final parent PR targets `main` and uses a
merge commit. Follow [AGENT_TASK_RUNBOOK.md](AGENT_TASK_RUNBOOK.md) for the
branch and worktree procedure.

Every Issue with implementation activity must have native GitHub Development
traceability. Create its task branch from the Issue's Development section (or
the equivalent `createLinkedBranch` API) before tracked edits. Link a parent
integration branch to the parent Issue and each child branch to its child
Issue. A matching branch name, URL, comment, or `Refs #N` text alone is not a
link.

Once a PR exists, verify that it appears in the Issue's Development section
and the Project's built-in `Linked pull requests` field. If it does not, link
the PR through Development before moving the item to `In review`. Keep at
least one linked branch or PR throughout `In progress`; after branch cleanup,
the merged PR is the durable link for `Done`.

Follow [COMMIT_POLICY.md](COMMIT_POLICY.md). An Issue may span multiple commits,
but each commit must remain coherent, functional, tested, and reviewable.

Open a draft pull request while work or evidence is incomplete. Before marking
it ready:

- Complete scope and acceptance criteria.
- Run and report relevant checks.
- Update required documentation.
- Review the final diff and intended base.

Use `Refs #N` for partial work and for child PRs targeting a parent branch.
Close a child manually after its squash merge and integration checks. Use
`Closes #N` only in a PR to `main` when that merge completes the named Issue;
never target a parent for one child's work. Merge requires explicit
authorization.

Issue closure is the completion trigger; automation may then set `Done`. Do not
use Project `Done` to close unfinished work. Verify Issue, PR, and Project state
after automation.

### Worktree Disposition and Cleanup

At task initialization, record exactly one disposition defined by
[AGENTS.md](AGENTS.md#worktree-disposition). `remove_after_integration` requires
existing cleanup authorization; otherwise retain with a reason or record
`authorization_required`. Update it only for a recorded authority or lifecycle
state change.

Completion must inventory the final exact worktree/branch/HEAD and porcelain
state, Issue/PR state, commit/PR recoverability, cleanup authorization decision,
and actual retained/removed outcome, reason, and next owner. Removal requires
clean status, completed integration verification, and recoverability, and must
be non-forced and verified absent afterward. Branch deletion is a separate
authorized action. Retain the parent worktree until the parent branch is
integrated into `main`.

## Completion and Exceptions

An Issue is complete only when its scope and acceptance criteria are satisfied,
required checks pass, documentation is current, the diff is reviewed, required
review is complete, approved work is merged or an approved no-PR exception is
complete, and the Issue is closed with the Project item at `Done`.

Close duplicate, invalid, superseded, cancelled, or rejected work as
`Not planned`. Explain the reason and link a replacement when applicable. When
reopening an Issue, clear `Done`, return it to `Backlog`, and replan it
explicitly.

## Agent Handoff

Follow the runbook handoff contract. Report the planning-gate state and
evidence, Issue and Project mutations, and preserved unrelated worktree changes.
