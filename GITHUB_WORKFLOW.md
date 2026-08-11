# GitHub Workflow Policy

This policy defines how contributors and coding agents use GitHub. It
supplements [AGENTS.md](AGENTS.md) and
[COMMIT_POLICY.md](COMMIT_POLICY.md). Agents execute it with
[AGENT_TASK_RUNBOOK.md](AGENT_TASK_RUNBOOK.md).

## Authority and Authorization

- Repository code, tests, and detailed documentation define current behavior.
- An Issue defines approved scope, acceptance criteria, dependencies, and
  completion evidence.
- A Project holds planning metadata; a pull request holds the proposed diff,
  review, and verification evidence.
- Resolve material conflicts through a recorded maintainer decision. Project
  metadata must not silently redefine an Issue.

Relevant read-only inspection is allowed. Implementation authorizes local
changes only. GitHub mutations, staging, commits, pushes, PR actions, review,
closure, and merge each require explicit authorization unless clearly bundled.

## Issues

Nontrivial feature, bug, dependency, packaging, security, architecture,
workflow, or public-documentation work requires an Issue. A maintainer may
approve an exception for a mechanical correction.

Use parent Issues for initiatives and leaf Issues for independently complete
functions. A leaf Issue must define:

- Parent and Project, when applicable
- Objective and context
- Scope and out of scope
- Testable definition of complete
- Verification and documentation requirements
- Likely components
- Dependencies, constraints, and risks

Use native parent/sub-issue relationships for hierarchy and `blocked by` for
dependencies. Do not start with an unfinished prerequisite or expand scope
without recorded approval.

A parent closes only after all required children and its integration criteria
are complete. A child pull request must never close its parent.

## Planning Metadata

### Milestone

A milestone groups one release or deliverable. Every leaf Issue must have
exactly one active milestone before entering `Ready`. Parent initiatives should
link the milestone but normally remain outside it to avoid double-counting.

Name a milestone for its outcome. Record its objective, owner, exit criteria,
exclusions, and any approved due date. Close it only when all required leaf
Issues complete; record deferrals to another milestone.

### Iteration

The Project `Iteration` field is the only sprint field. Use
`Sprint NN - YYYY-MM-DD to YYYY-MM-DD`.

An unscheduled `Backlog` Issue may have no iteration. During planning, verify
that the Issue is accepted, unblocked, and assigned to a milestone; then assign
the current or next iteration and move it to `Ready` in the same action. Every
`In progress` or `In review` Issue must have an iteration.

For rollover, retain the Issue, record completed and remaining scope, and move
it to the next iteration. Keep `In progress` if work continues; otherwise
return it to `Backlog`. Changing an iteration does not change the milestone.
Mid-sprint additions require explicit reprioritization.

### Labels and Fields

Use labels only for durable classification:

- One work type: `bug`, `enhancement`, `documentation`, or `chore`
- One or more components: `area:<name>`
- Delivery sequence, when needed: `wave:NN`
- Active blockage: `blocked`

Do not create milestone, sprint, status, or priority labels. Use `wave:NN`, not
`priority:NN`, for execution waves. Configure Project Priority as:

- `P0 - Critical`
- `P1 - High`
- `P2 - Normal`
- `P3 - Low`

Set Priority during triage. Use Size for capacity planning. Use Estimate only
when its unit is defined.

## Project Status

| Status | Rule |
| --- | --- |
| `Backlog` | New, unscheduled, incomplete, reprioritized, or pre-start blocked work. |
| `Ready` | Accepted and unblocked, with milestone and current/next iteration assigned. |
| `In progress` | Authorized work started; assignee and Issue branch exist. Draft PRs remain here. |
| `In review` | A linked, non-draft PR is ready and includes verification evidence. |
| `Done` | Completion gates passed, approved work merged or exception completed, and Issue closed. |

Requested implementation changes return an item to `In progress`.

Blocked is an overlay, not a status. Add the native dependency, apply
`blocked`, and record the cause, unblock condition, owner, and next action.
Pre-start blocked work stays `Backlog`; interrupted active work stays
`In progress` unless reprioritized. Blocked work never becomes `Done`.

## Branches, Commits, and Pull Requests

Assign one accountable owner per branch and worktree, plus one integration
owner for a parent branch. Only one write-capable owner is active at a time.
Inspect each worktree first and preserve unrelated changes.

Use `issue-<number>-<short-slug>` for nontrivial work. A standalone branch
identifies its leaf Issue. For a parent initiative, its branch is the
integration base for child Issue branches. Child PRs target the parent branch
and use **Squash and merge**; the final parent PR targets `main` and uses a
merge commit. Follow [AGENT_TASK_RUNBOOK.md](AGENT_TASK_RUNBOOK.md) for the
branch and worktree procedure.

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

## Completion and Exceptions

An Issue is complete only when its scope and acceptance criteria are satisfied,
required checks pass, documentation is current, the diff is reviewed, required
review is complete, approved work is merged or an approved no-PR exception is
complete, and the Issue is closed with the Project item at `Done`.

Close duplicate, invalid, superseded, cancelled, or rejected work as
`Not planned`. Explain the reason and link a replacement when applicable. When
reopening an Issue, clear `Done`, return it to `Backlog`, and reschedule it
explicitly.

## Agent Handoff

Follow the runbook handoff contract and report preserved unrelated worktree
changes.
