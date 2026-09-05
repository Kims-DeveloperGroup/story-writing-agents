# GitHub Workflow Policy

This policy governs tracked work and supplements [AGENTS.md](AGENTS.md) and
[COMMIT_POLICY.md](COMMIT_POLICY.md). Load
[`$github-task-lifecycle`](.agents/skills/github-task-lifecycle/SKILL.md) for commands.

## Authority and Authorization

Code/tests/docs define behavior; Issues define scope/completion; organization Issue fields
and Project hold planning; PRs hold diff/review/evidence. Record conflicts; planning never
silently changes Issue scope.

Read-only inspection is allowed. Implementation also authorizes initial in-scope Issue/
milestone creation or reuse, native relations, canonical Project membership/metadata, and
Development-linked task-branch creation after gate passage. Project-schema changes,
unrelated edits, stages, commits, other pushes, PR/review/closure/merge/deletion/cleanup need
explicit authorization. Worktree removal and branch deletion are separate.

## Initial Planning Gate

Before branches/worktrees/writers/edits record
`github_planning_gate=not_required|pending|passed|blocked`. Pending permits read-only only.

Large means `L|XL`; two independent slices/parent criteria; three production modules; two
domains; modularity/migration coordination; dependency sequencing; multiple
owners/worktrees/phases; or parent integration. Tests/docs do not count; uncertainty is
large.

Large work requires root and all known descendants before implementation. Verify native
hierarchy/dependencies, [Project #1](https://github.com/orgs/Kims-DeveloperGroup/projects/1)
membership, required fields/labels/milestones, approved base, and an executable `Ready`
leaf. Partial failure preserves IDs, repairs gaps, blocks writing, and never duplicates.
Later scope repeats the gate. `not_required` permits read-only or approved mechanical work;
nontrivial standalone work still needs one complete Issue/Project item.

## Issues

Nontrivial feature, bug, dependency/packaging, security, architecture, workflow, or public
documentation work needs an Issue unless a maintainer approves a mechanical exception.
Parents represent initiatives; leaves are independently complete.

Root records objective, non-goals, integration criteria, children/order, risks, evidence,
base. Leaf records root/parent/Project, objective/context, in/out scope, testable completion,
verification/docs/components/dependencies/constraints/risks. Use native sub-Issues and
`blocked by`; never start unfinished prerequisites or expand scope without approval. Root
closes after all `Must` releases/core integration; linked `Recommended`/`Optional` follow-ups are excluded
from required children and never block closure. Child PR never closes its parent.

## Planning Metadata

- Milestone: every leaf has one active deliverable before `Ready`; parent links it but
  normally remains outside. Record owner/exit/exclusions/due date; close after required
  leaves, recording deferrals.
- Iteration: sequential never-reused `Iteration NN`, logical not calendar. `wave:NN` orders
  work within a release. Status never rolls an Iteration.
- Labels: exactly one `bug|enhancement|documentation|chore`, one-or-more `area:<name>`,
  optional `wave:NN`, active `blocked`. Never label milestone/iteration/status/priority.
- Estimate requires a unit; release capacity uses Size points below.

## Requirement Releases

New work uses organization Issue `Requirement=Must|Recommended|Optional`, replacing Priority.
The old field is renamed `Legacy Priority`; preserve historical values and make no new
assignments. Classify existing Issues only when entering a new phase/Iteration; record basis. Unset blocks
`Ready`. Dependencies necessary for delivery are `Must`; agents never downgrade `Must`
without a user/maintainer decision.

|Rule|Policy|
|---|---|
|Initial plan|Classify-all-known-leaves;create-only-nonempty-numbered-`MUST-01`/`RECOMMENDED-01`/`OPTIONAL-01`-release-Issues;use-`-02+`-for-capacity|
|Release integrity|Dependency-closed/testable/user-usable/complete-features-or-functions;never-scaffolding/half-API/dormant-config;ship-supporting-tests/docs/migrations|
|Order|Later-may-depend-on-earlier;never-reverse|
|Capacity|Every-implementation-leaf-needs-Size;`XS=1,S=2,M=3,L=5,XL=8`;release-load=sum-of-unfinished-leaf-points;record-capacity;planned-load-must-not-exceed-it|
|Resize|Preserve-all-`Must`;select-`Recommended`-by-accepted-user-order;defer-`Optional`-first,then-`Recommended`-that-do-not-fit,to-`Backlog`;clear-Iteration/wave;record-reason/reconsideration|
|Disposition|`Not planned`-only-explicit-rejection;active-pause-needs-approved-partial-state/PR-branch-disposition/recoverability|
|Oversize|Split-oversized-work-into-complete-slices/releases;indivisible-over-cap-needs-capacity-exception;preserve-all-`Must`|
|Iteration|One-per-release;leaves-inherit-Iteration/waves;active-statuses-require-it;`Backlog`-may-omit;root-tracks-current-`Must`;completed-releases-retain-theirs|
|Closure|Each-release-has-publishable-`main`-PR-and-closes-on-publication;root-closes-after-all-`Must`/integration;other-tiers-stay-linked/nonblocking|
|Topology|Feature-leaf-PR-targets-release-branch-and-squashes;numbered-release-PR-targets-`main`-and-closes-release;umbrella-normally-planning-only/administrative-close-after-`Must`/core-verification;distinct-diff-uses-own-Development-linked-branch/PR-targeting-`main`,starts-after-all-`Must`-release-PRs/core-integration,and-may-close-root|

## Project Status

|Status|Rule|
|---|---|
|`Backlog`|New/incomplete/reprioritized/pre-start blocked|
|`Ready`|Accepted, unblocked, metadata complete|
|`In progress`|Authorized work; assignee plus Development branch/PR; drafts stay|
|`In review`|Non-draft Development/Project-linked PR plus evidence|
|`Done`|Gates passed, approved merge/exception, Issue closed|

Implementation returns work to `In progress`. Blockage is an overlay: native dependency,
`blocked` label, cause/unblock/owner/next action. Pre-start remains `Backlog`; interruption
remains `In progress` unless reprioritized; blocked never becomes `Done`.

## Branches, Commits, and Pull Requests

One accountable owner per branch/worktree and one parent integrator; one active writer.
Bind writers to [AGENTS identity](AGENTS.md#role-mode-and-control-gate); mismatches never
authorize destructive cleanup.

Use `issue-<number>-<slug>`. Link every branch/PR through its owning Issue's Development
field; names, URLs, comments, or `Refs` are insufficient. Ordinary child-to-parent squashes
apply only to feature leaves, never release PRs; follow Requirement Topology above. Verify Development and
Project `Linked pull requests`; retain a branch/PR link through `In progress`, merged PR for
`Done`.

Follow commit policy: coherent, functional, tested, reviewable commits. Draft PRs hold
incomplete work/evidence. Ready requires scope/criteria, checks, docs, diff/base review.
Use `Refs #N` for partial/child work; close child manually after squash/integration. Use
`Closes #N` only for completing `main` PRs. Merge needs authorization. Issue closure triggers
`Done`; verify Issue/PR/Project afterward.

### Worktree Disposition and Cleanup

Record one [AGENTS disposition](AGENTS.md#worktree-disposition). Completion inventories
worktree/branch/HEAD/porcelain, Issue/PR, recoverability, cleanup authority, outcome/reason/
next owner. Non-forced removal requires authorization, clean status, integration, and
recoverability; verify absence. Branch deletion is separate. Retain parent until `main`.

## Completion and Exceptions

Complete means scope/criteria/checks/docs/diff/review pass, approved work merges or approved
exception completes, Issue closes, Project is `Done`. Close duplicate/invalid/superseded/
cancelled/rejected as `Not planned` with reason/replacement. Reopening clears `Done`, returns
`Backlog`, and replans.

## Agent Handoff

Follow [AGENTS handoff](AGENTS.md#handoff-and-result-contracts); report gate evidence,
Issue/Project mutations, and preserved unrelated changes.
