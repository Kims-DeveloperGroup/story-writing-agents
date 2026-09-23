# GitHub Workflow Policy

This policy governs tracked work and supplements [AGENTS.md](AGENTS.md),
[COMMIT_POLICY.md](COMMIT_POLICY.md), and [DEPENDENCY_POLICY.md](DEPENDENCY_POLICY.md).
Use [`$github-task-lifecycle`](.agents/skills/github-task-lifecycle/SKILL.md) for normal
tracked work. Use [`$project-workflow`](.agents/skills/project-workflow/SKILL.md) only when
the user explicitly invokes it.

## Authority

Code, tests, and docs define behavior; Issues define scope and completion; Project fields
hold planning state; PRs hold the diff, review, and evidence. Read-only inspection is always
allowed. Outside explicit `$project-workflow` invocation, each mutating GitHub or Git delivery
step needs the authority described by `$github-task-lifecycle`. Project-schema changes and
unrelated scope always require a maintainer decision.

Explicit `$project-workflow` invocation authorizes the root to complete the in-scope lifecycle
without repeated approval: Issue and Project planning, linked branch/worktree, commit, push,
PR, review handling, gated squash merge, Issue closure, Project completion, and safe cleanup.
It does not authorize unrelated work, bypassing protections, force operations, Project-schema
changes, or dependency-policy exceptions. Subagents cannot use that authority.

## Planning Gate

Record `github_planning_gate=not_required|pending|passed|blocked` before writes. Pending or
blocked is read-only. Nontrivial tracked work needs one complete Issue and Project item unless
a maintainer explicitly grants a mechanical exception. Large work needs its known Issue graph
before implementation.

Use [Project #1](https://github.com/orgs/Kims-DeveloperGroup/projects/1). Each executable leaf
must have the required type/area labels, Requirement, Size, Iteration, milestone, owner,
accepted base, dependencies, and `Ready` status. Preserve successful mutations and repair
missing metadata instead of duplicating records.

Keep a single delivery unit on its owning Issue. Children are allowed only when at least two
independent, necessary sibling slices are known and created or verified together. Use native
sub-Issues and `blocked by`; never replace them with prose links.

## Planning Metadata

- Requirement is `Must|Recommended|Optional`; required delivery dependencies are `Must`.
- Size is `XS=1, S=2, M=3, L=5, XL=8`; planned load must fit release capacity.
- Iterations are sequential `Iteration NN`; `wave:NN` orders work within one release.
- Labels include exactly one `bug|enhancement|documentation|chore`, at least one `area:*`,
  optional `wave:NN`, and `blocked` only while blocked.
- A leaf has one active milestone. Close it after required leaves and record deferrals.

Status is `Backlog` before readiness, `Ready` when accepted and unblocked, `In progress` while
implemented, `In review` for a ready linked PR with evidence, and `Done` only after accepted
merge and closure. A blocker is an overlay with cause, owner, and unblock action.

## Branches, Commits, and Pull Requests

Use one owner, branch, and worktree per delivery unit. Name the branch
`issue-<number>-<slug>` and create it through the Issue Development link. Verify the native
branch and PR links; names, comments, and `Refs` text are not substitutes.

Follow [COMMIT_POLICY.md](COMMIT_POLICY.md). Draft PRs may hold incomplete work. A ready PR has
final scope, criteria, affected verification, docs, diff review, and the correct base. Use
`Refs #N` for partial or child work and `Closes #N` only for the completing PR to `main`.
Every PR uses GitHub **Squash and merge** and the squash commit identifies the owning Issue.

## Asynchronous Review and CI

Review is milestone-based:

1. When the PR becomes ready, record its head SHA and concurrently request Copilot and Codex
   review while affected CI starts.
2. Continue independent work while Actions run. Concurrency cancels stale runs.
3. Batch unresolved blocking feedback into one fix set. Do not re-request on every push.
4. After material fixes, record the new head SHA and request one final concurrent review and
   affected CI milestone.

Copilot uses Lite by default and Balanced only for high-risk changes. Merge blocks only on
unresolved Copilot **High**, Codex **P0/P1**, branch-protection approvals, and required checks.
Lower-severity feedback is nonblocking and may become follow-up work. Review/check evidence
must match the current head SHA.

Automation that mutates Issues, Projects, PRs, merge state, or cleanup state runs only for the
`workflow:managed` label. Removing that label authorizes only revocation of previously enabled
auto-merge on that PR; it does not authorize new automation. `pr-validation` and
`ai-review-gate` are event-driven; the latter may
poll on a schedule without checking out code. Enable auto-merge or the merge queue only after
all gates pass. Post-merge automation verifies Issue closure, Project `Done`, Development
links, and remote-branch cleanup; failures remain visible for root repair.

### Review Comments

Verify each PR review finding against the current diff and relevant evidence, including
threads whose location has drifted or become outdated. For an applied finding, reply in its
thread with the change and verification, then resolve it. For a rejected finding, reply with
the reason and supporting evidence, then resolve it. Never leave an already addressed thread
open or resolve a finding that still needs work.

## Verification Policy

Runtime-code changes, including runtime agent definitions under `config/agents/`, require the
current-head fast CI tier and relevant tests selected with
`PYTHONPATH=src python -m tests.runner run` and exact `--module` or `--id` selectors. Record
the selected IDs, command, result, and head SHA in the PR. Add a behavioral test when changed
behavior lacks one. Use the `test:full` label and reconcile both full-suite shards only when
impact cannot be bounded or a
full run is explicitly required; it is not the default PR gate.

Policy, docs, templates, skills, development-agent configuration, module-index-only, and
test-only changes use static validation and diff review; they do not require runtime tests.
An absent, cancelled, or stale check is not a pass. Evidence names the source/head SHA,
commands or static checks, failures, skips, gaps, and assessment.

## Templates and Records

Before creating or substantially updating an Issue, PR, evidence record, or status comment,
read the [authoring guide](.agents/skills/github-task-lifecycle/references/issue-pr-authoring.md)
and its raw template. Preserve every visible heading and the template's meaning. Templates do
not authorize mutations or replace native relations.

## Completion and Cleanup

Complete means scope, acceptance criteria, required verification, current-head reviews, and
protections pass; the PR is squash-merged; the completing Issue is closed; and Project status
is `Done`. Parent Issues close only after all required children and integration are complete.

Before cleanup, inventory the worktree, branch, HEAD, porcelain, PR/Issue state, integration,
and recoverability. Never force-remove, reset, clean, rebase, or force-push to manufacture a
clean state. Remove a worktree or delete a branch only under recorded authority, after
integration and a clean-status check; verify the result. Preserve parent/release worktrees
until their integration completes.
