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

Project milestones schedule sprint work. Review rounds are numbered checkpoints on one PR;
the number persists across pushes, base updates, reopening, and provider changes. A review
records its exact base/head pair. Later diff inspection and current checks are separate
evidence, never a claim that the provider reviewed a later revision.

### Provider selection

Before ready-for-review, the root inspects the diff, records a `Review rationale` in the PR,
and applies exactly one selector alongside `workflow:managed`:

| Selector | Use for |
| --- | --- |
| `review:copilot-lite` | Documentation, presentation, mechanical edits, straightforward isolated fixes. |
| `review:copilot-balanced` | Bounded runtime behavior changes, substantial local logic, ordinary integrations needing deeper analysis. |
| `review:codex` | Security/authorization, data integrity, Git mutation safety, concurrency, recovery, or changes spanning critical subsystem contracts. |

Select the highest applicable tier. Keep the provider through the PR unless changed risk or
terminal provider failure justifies switching; record the reason and preserve all findings.
Never request both providers routinely or switch to evade a finding. An outstanding or
uncertain request must be reconciled before requesting either provider again.

Copilot effort is a product setting, not an API model parameter. Keep requestor, repository,
and organization defaults at Lite. Balanced requests use GitHub's Reviewers UI; the agent
or maintainer records the actual effort shown by the resulting review. A switch from a
previous Copilot effort also uses the UI because GitHub may retain the PR's previous effort.
Before a UI request, verify the displayed head matches the reserved round; reconcile
non-delivery and reserve the current snapshot first if it moved. Never silently substitute
Lite for Balanced or start Codex while waiting for that UI action.

### Rounds and verified closure

1. **Round 1:** Request the selected provider once when the managed PR becomes ready; start
   affected CI concurrently. Batch confirmed High/Critical findings. Medium findings and
   small in-scope enhancements or minor improvements are optional.
2. **Round 2, when needed:** Review the batch and remaining defects. Medium feedback remains
   optional. Stop as soon as the verified-closure criteria below pass.
3. **Round 3 onward:** Accept only confirmed High/Critical bugs, missing or incorrect agreed
   requirements, and material defects. Ignore or defer Medium feedback, enhancements, style,
   speculative refactors, and minor improvements. Request further external review only for
   material uncertainty that cannot be closed with available evidence.

The root validates severity and impact against the actual diff and agreed requirements.
Codex P0/P1 map to Critical/High and P2 to Medium. A provider label or the word "bug" alone
is not proof of blocking impact. A new preference is not a missing agreed requirement.

**Verified closure** requires acceptance criteria, evidence-backed resolution or rejection
of every confirmed High/Critical finding, passing required checks/approvals, and inspection
of the final diff. A further clean AI review is unnecessary. Close after round 1 if those
conditions already pass. Medium findings never block completion. Round 3 is not mandatory.

There is no fixed round cap. Repeated feedback without new evidence or changed defect status
stops requests and requires diagnosis; it does not justify another identical review. A base
update or new commit requires current verification and inspection of the changed behavior,
not an automatic new AI round. Material uncertainty may justify an explicitly recorded next
round. Closure does not waive any branch protection or required verification.

### Managed requester

`.github/workflows/ai-review-request.yml` owns requests. It never checks out or executes PR
code with its write token. Automatic events may start only round 1 on a PR without review
history. Later operations use `workflow_dispatch` on the default branch, by a repository
writer, with `expected_round` as an optimistic concurrency check:

| Operation | Required decision/evidence |
| --- | --- |
| `request` | Initial rationale, or a reason and reconciliation evidence for another round. From round 3, High/Critical severity plus the finding URL; changed evidence is required for a repeated finding. |
| `reconcile` | Current round, reason, evidence, and outcome. `completed` requires a matching provider review ID (plus actual Copilot effort), or Codex's thumbs-up on this round's request. `failed`/`not_sent` require evidence of terminal failure or confirmed non-delivery. |
| `close` | Current round, current-base/head closure evidence, and the root's verified-closure assessment. Requires a completed review, including later local fix/diff verification. |
| `adopt` | Existing open PR history, its prior round count, and evidence reconciling all findings and outstanding requests. Import at least two prior rounds so subsequent work is defect-only. Never replay an initial request. |

The workflow records reservation, UI wait, requested, uncertain, completed, failed, and
closed states in bot-authored PR comments. It distinguishes request submission from actual
review completion. One PR has at most one outstanding managed request. Rerunning a workflow
or receiving another automatic event does not send another request. Failed/non-delivered
attempts can be retried explicitly within the same logical round; their attempt history
remains visible. Imported legacy history always advances into round 3 or later, including
failed imports. Unknown outcomes remain outstanding, not failed or passed.

The `review_id` input is the numeric GitHub review ID, not an inline comment ID. For Copilot,
`observed_effort` is the root's attestation of the effort shown in that review's overview or
timeline; link that evidence. A mismatch requires diagnosis, not an automatic replacement
review. For Codex's clean thumbs-up, omit `review_id`. An eyes reaction is not completion.
All explicit operations capture the acting user and evidence. The closure record supports
the root's final audit; it is not an automated merge approval or a replacement CI gate.

Before rollout, create the three selector labels and disable provider-native automatic
reviews, including review-on-push and draft reviews, so requests have one owner. Check both
repository/organization and personal settings. Confirm Copilot defaults and observe actual
effort after each review. Keep enabled the integrations needed for explicit requests.
GitHub documents [Copilot effort and automatic review controls](https://docs.github.com/en/copilot/concepts/agents/code-review);
OpenAI documents [Codex automatic review controls](https://learn.chatgpt.com/docs/third-party/github).

Mutating workflow automation requires `workflow:managed`. Continue independent work while
CI or a provider runs. Immediately before manual squash merge, the root audits current
base/head, changed behavior since the provider review, every blocking finding, required
checks/approvals, and mergeability. Do not enable auto-merge or merge queue.
Post-merge verification checks Issue closure, Project `Done`, Development links, and remote
branch cleanup; failures remain visible for repair. Managed post-merge verification needs
`PROJECT_STATUS_TOKEN` with organization ProjectV2 read access.

### Review Comments

Maintain one concise reconciliation record per round: finding URLs, verified severity,
Applied/Rejected/Deferred disposition, evidence, remaining blockers, and the next action.
Deduplicate repeated findings while retaining links to their original threads. Medium or
lower feedback may be ignored or deferred with a short disposition; it needs no repair
round. Respect any required conversation-resolution protection.

Check blocking findings against the current diff, including outdated or drifted locations.
For an applied or rejected blocker, reply in its thread, then resolve it. Use separate
`Decision: Applied` or `Decision: Rejected`, `Reason: ` (at least 40 characters), and
`Evidence: ` (a relevant GitHub URL) lines. The reply must come from a repository member or
collaborator and be newer than the finding's last edit. Never resolve unfinished work or
leave an already addressed thread open.

For a blocking review body without an inline thread, post a substantive PR comment with
`<!-- ai-review-body-disposition:REVIEW_ID -->` and the same Decision/Reason/Evidence lines.
A later clean review or changed SHA does not erase an unresolved blocker. Reassess an
edited finding and record a fresh disposition after the edit. Code fixes require affected
verification and final diff inspection, not an automatic fresh review cycle.

## Verification Policy

The canonical suite follows [TEST_POLICY.md](TEST_POLICY.md). Runtime-code
changes, including runtime agent definitions under `config/agents/`, require
current-head fast CI and affected tests selected with exact `--module` or `--id`
selectors. Record selected IDs, command, result, and head SHA in the PR. CI
runs both complete shards and the frozen inventory/time budget for runtime or
test changes. Update a retained representative case when changed behavior
lacks coverage within the frozen limits. Test-only changes also require this
full CI gate. Documentation,
templates, skills, development-agent configuration, and module-index-only
changes need static validation and diff review. An absent, cancelled, stale,
incomplete, or over-budget check is not a pass. Evidence names source/head
SHA, commands, failures, skips, gaps, and assessment.

## Templates and Records

Before creating or substantially updating an Issue, PR, evidence record, or status comment,
read the [authoring guide](.agents/skills/github-task-lifecycle/references/issue-pr-authoring.md)
and its raw template. Preserve every visible heading and the template's meaning. Templates do
not authorize mutations or replace native relations.

## Completion and Cleanup

Complete means scope, acceptance criteria, required verification, verified review closure, and
current checks/protections pass; the PR is squash-merged; the completing Issue is closed; and Project status
is `Done`. Parent Issues close only after all required children and integration are complete.

Before cleanup, inventory the worktree, branch, HEAD, porcelain, PR/Issue state, integration,
and recoverability. Never force-remove, reset, clean, rebase, or force-push to manufacture a
clean state. Remove a worktree or delete a branch only under recorded authority, after
integration and a clean-status check; verify the result. Preserve parent/release worktrees
until their integration completes.
