# GitHub Workflow Policy

This policy governs tracked work and supplements [AGENTS.md](AGENTS.md),
[COMMIT_POLICY.md](COMMIT_POLICY.md), and [DEPENDENCY_POLICY.md](DEPENDENCY_POLICY.md).

Use [`$github-task-lifecycle`](.agents/skills/github-task-lifecycle/SKILL.md) for normal tracked work.
Explicit Project-stage and full-workflow invocations use the root-only skills below.

## Authority

Code, tests, and docs define behavior; Issues define scope, completion, and native Requirement;
Project fields hold Size/Iteration/Status; PRs hold the diff, review, and evidence.
Read-only inspection is always allowed.
Outside explicit Project-skill invocation, each mutating GitHub or Git delivery step needs
its own authority as described by `$github-task-lifecycle`. Natural language implementation,
planning, review, or continuation requests do not implicitly invoke a Project skill.

Explicit invocation authorizes the root to complete the named stage within accepted scope
without repeated approval. The root coordinator may load stages under its existing authority.
Subagents cannot load these skills or receive their GitHub/lifecycle authority.

| Invocation | Authorized work | Stop point |
| --- | --- | --- |
| [`$project-plan`](.agents/skills/project-plan/SKILL.md) | Issue/Project planning records and native relationships. | Verified planning readiness, before branch/worktree creation or tracked-file edits. |
| [`$project-implement`](.agents/skills/project-implement/SKILL.md) | Necessary Issue/Project preparation, linked branch/worktree, implementation, verification, staging, coherent commits, and normal push. | Verified committed/pushed change, before PR creation or external review requests. |
| [`$project-pr-review`](.agents/skills/project-pr-review/SKILL.md) | PR/review records, managed review requests, scoped local branch/worktree restoration for the existing linked PR branch, in-scope fixes, verification, commits/pushes, and reconciliation. | Verified review closure, before integration. |
| [`$project-complete`](.agents/skills/project-complete/SKILL.md) | Final integration audit, manual squash merge, closure, Project completion, durable records, and safe cleanup. | Verified completion with preserved residual state reported. |
| [`$project-workflow`](.agents/skills/project-workflow/SKILL.md) | All four stages in sequence. | Full in-scope lifecycle completion. |

Standalone stages do not automatically advance to another stage. Direct `$project-implement`
invocation includes preparing missing planning prerequisites: it requires neither a prior
`$project-plan` invocation nor separate approval of the prepared plan. This does not waive
readiness, dependency, verification, or protection gates. Scope expansion and missing material
requirements require a decision; routine in-scope planning preparation does not.

No invocation authorizes unrelated work, force operations, bypassed protections,
Project-schema changes, dependency-policy exceptions, or destructive cleanup without verified
recoverability. Project-schema changes and unrelated scope always require a maintainer decision.
Codex Plan Mode remains read-only regardless of skill invocation.

## Planning Gate

Record `github_planning_gate=not_required|pending|passed|blocked` with its reason before writes.
A pending gate permits only explicitly authorized Issue/Project planning writes needed to
establish readiness. A blocked gate is read-only. Neither permits branch/worktree creation or
tracked-file edits. Record a passed gate only after required readiness is verified, accounting
for any explicitly approved mechanical exception; never infer an exception or waive unrelated
readiness requirements.

Nontrivial tracked work needs one complete Issue and Project item unless the maintainer
explicitly grants a mechanical exception. Large work needs its known Issue graph before
implementation. Use [Project #1](https://github.com/orgs/Kims-DeveloperGroup/projects/1).
Each executable leaf must have required type/area labels, native Issue Requirement, Size, Iteration,
milestone, owner, accepted base, dependencies, and `Ready` status before first implementation.
Preserve successful mutations and repair missing metadata instead of duplicating records.
New scope repeats the planning gate.

Keep a single delivery unit on its owning Issue. Children are allowed only when at least two
independent, necessary sibling slices are known and created/verified together. Use native
sub-Issues and `blocked by`; never replace them with prose links.

## Planning and Implementation Boundary

The durable detailed implementation plan belongs in the owning Issue's existing
`## Implementation plan` section, using the raw Issue template. It records affected
components, intended behavior and interface changes, implementation order, relevant failure
cases, and verification mapped to acceptance criteria. Keep `## Implementation summary`
brief and the template's verification section current.

Planning inspects behavior and resolves scope, constraints, criteria, and readiness. Authorized
planning writes are limited to Issue/Project records and native relationships. Planning ends
when the detailed plan and metadata are complete, dependencies are unblocked, the executable
Issue is `Ready`, and the gate passes. Implementation starts with linked branch/worktree
creation or verified reuse, followed by `In progress` and tracked-file changes. Resumed work
revalidates equivalent readiness and preserves its appropriate current status.

`$project-plan` stops at that boundary. `$project-implement` prepares missing planning records
itself and automatically continues after readiness, without loading `$project-plan` or asking
for a separate plan approval. Neither path may bypass a blocked dependency or missing material
scope decision. The other standalone stages validate their prerequisites and report missing
owner/actions instead of automatically executing earlier stages.

## Planning Metadata

Requirement is the organization-level native **Issue field**, with `Must|Recommended|Optional`
choices. Inspect organization Issue-field definitions and the owning Issue's field values,
or verify them in the Issue's Fields sidebar. Project Size, Iteration, and Status use Project
fields; a same-named Project field is not the authoritative Requirement field. An empty
Project-field option list does not prove the Issue field is unavailable. Set the existing
Issue field and verify it without changing either schema. The
[Issue field-values API](https://docs.github.com/en/rest/issues/issue-field-values) supports
adding the value without replacing unrelated Issue field values.

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

Explicit Copilot budget/quota exhaustion is a terminal provider failure: retain its diagnostic
or non-delivery evidence, reconcile the outstanding attempt, and request Codex instead without
another confirmation. Replace the Copilot selector with `review:codex`, record the fallback
rationale, and preserve every finding and the numbered history. A proven non-delivered attempt
may retry the same logical round; completed reviews and unknown outcomes are not non-delivery.
This exception does not authorize another provider while an earlier request is still uncertain,
or repeated requests when Codex also lacks capacity. Report that remaining provider blocker.

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
of every confirmed High/Critical finding, passing applicable CI evidence (hosted or the local
fallback below), all enforced required checks/approvals, and inspection
of the final diff. A further clean AI review is unnecessary. Close after round 1 if those
conditions already pass. Medium findings never block completion. Round 3 is not mandatory.

There is no fixed round cap. Repeated feedback without new evidence or changed defect status
stops requests and requires diagnosis; it does not justify another identical review. A base
update or new commit requires current verification and inspection of the changed behavior,
not an automatic new AI round. Material uncertainty may justify an explicitly recorded next
round. Closure does not waive any branch protection or required verification.

After closure, a fresh explicit `request` resumes at the next numbered round under the
same evidence and severity rules. A fresh `close` records updated verification of the
current base/head without another provider request; it preserves the reviewed revision.
Automatic events never reopen a closed review.

### Managed requester

`.github/workflows/ai-review-request.yml` owns request reservations and review state. It never
checks out or executes PR code with its write token. Automatic events may start only round 1 on a PR without review
history. Later operations use `workflow_dispatch` on the default branch, by a repository
writer, with `expected_round` as an optimistic concurrency check:

| Operation | Required decision/evidence |
| --- | --- |
| `request` | Initial rationale, or a reason and reconciliation evidence for another round. From round 3, High/Critical severity plus the finding URL; changed evidence is required for a repeated finding. |
| `reconcile` | Current round, reason, evidence, and outcome. `completed` requires a matching provider review ID (plus actual Copilot effort), Codex's clean thumbs-up, or an authentic clean Codex response comment identifying the reviewed commit. New Codex rounds also verify the personal request. `failed`/`not_sent` require evidence of terminal failure or confirmed non-delivery. |
| `close` | Current round, current-base/head closure evidence, and the root's verified-closure assessment. Requires a completed review, including later local fix/diff verification. |
| `adopt` | Existing open PR history, its prior round count, and evidence reconciling all findings and outstanding requests. Import at least two prior rounds so subsequent work is defect-only. Never replay an initial request. |

Codex triggers must be posted by a **personal GitHub account with repository write access**,
never by `github-actions[bot]` or another bot. No personal token is stored in Actions. A Codex
`request` reserves `awaiting_ui` with `request_mode: personal`; the bot does not post the trigger.
The root verifies the CLI account using `gh api user` (type `User`) or the signed-in browser
account, then checks the PR still matches the reserved base/head. Post exactly one comment:

```text
@codex review

Review round ROUND; base BASE_SHA, head HEAD_SHA.
SEVERITY_CONSTRAINTS
Follow GITHUB_WORKFLOW.md for severity and verified closure.

<!-- ai-review-request:v1:PR_NUMBER:ROUND:ATTEMPT -->
```

Replace every uppercase placeholder with the reservation's exact value. For rounds 1–2,
SEVERITY_CONSTRAINTS is `Prioritize confirmed defects. Medium feedback is optional and never blocks completion.`
For round 3 onward, it is `Only confirmed High/Critical bugs, missing or incorrect agreed requirements, and material defects. Exclude enhancements, Medium feedback, and minor improvements.`
The writer verifies this full prompt and policy line. Do not post again while a request is outstanding. The writer
requires exactly one trigger with that marker, a personal writer, the recorded base/head,
and a creation time after reservation; it records the verified sender and request URL/ID
on completion. `awaiting_ui` remains outstanding while the personal request runs. Bot status
records remain bot-authored. Existing historical attempts retain their original request mode
and provenance; do not retroactively replace an uncertain bot request without reconciliation.

The workflow records reservation, UI wait, requested, uncertain, completed, failed, and
closed states in bot-authored PR comments. It distinguishes request submission from actual
review completion. One PR has at most one outstanding managed request. Rerunning a workflow
or receiving another automatic event does not send another request. Failed/non-delivered
attempts can be retried within the same logical round only through a fresh dispatch; their
attempt history remains visible. Imported legacy history always advances into round 3 or later, including
failed imports. Unknown outcomes remain outstanding, not failed or passed.

The `review_id` input is the numeric GitHub review ID, not an inline comment ID. For Copilot,
`observed_effort` is the root's attestation of the effort shown in that review's overview or
timeline; link that evidence. A mismatch requires diagnosis, not an automatic replacement
review. For Codex's clean thumbs-up, omit `review_id` and link the request as evidence. For a
clean response comment, omit `review_id` and link that response: the writer checks Codex bot
identity, the no-major-issues message, a matching reviewed-commit prefix of at least ten
characters, and response timing after the request's latest edit. Native reviews and clean
reactions also must follow the personal request's latest edit. A clean reaction requires the
reserved base/head to remain unchanged; otherwise use a commit-bound response and retain
later verification separately. An eyes reaction is not completion.
Clean response comments apply to reserved rounds with a known head. Historical `adopt`
continues to use native review IDs or clean reactions; a single manual round keeps an
explicit manual closure record rather than inventing import history.
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

### Explicit validation time allowance

A maintainer's recorded authorization to exceed validation time budgets may use
[the per-PR wall-time exception](TEST_POLICY.md#explicit-wall-time-exception).
Record the authorization and reason in the owning Issue and PR before applying
`test:wall-time-exception`. This changes time allowances only: complete successful
reports and every non-time verification, review, approval, and integration gate
remain mandatory. Keep actual durations and earlier failures visible.

### Actions non-execution fallback

When annotations, logs, or service status establish that Actions cannot execute because of
billing/quota, disabled execution, or an infrastructure outage, the root records the evidence
and runs the applicable CI commands locally without another confirmation. Use the canonical
[local CI procedure](.agents/skills/github-task-lifecycle/references/local-ci-fallback.md):

pin base/head and the checkout tree, use the locked Python 3.11 environment, select every lane
from the current workflow, and retain commands, exit codes, exact test IDs, artifacts, and
platform limits. Static-only changes run all static steps; runtime/test changes also run the
fast tier, affected runtime tests, both full shards, reconciliation, and frozen-budget checks.

Independent local task threads use the host-wide `tests.local_ci` coordinator.
It admits four executing top-level test processes across native and Lima
workers, with two full shards per campaign. Isolated campaigns drain and reserve
the host for timing evidence and run their full shards sequentially by default;
shared campaigns permit both full shards concurrently. `--full-concurrency 1`
or `2` overrides that choice; campaign metadata records the resolved value.
Queue wait and uncertain-worker quarantine remain visible in durable job state.
An actual validation failure must be diagnosed and fixed; switching execution locations does
not erase it. Unknown failure causes are not an established outage.

An authorized implementation or PR/review stage includes narrowly scoped creation and cleanup
of one root-owned disposable, detached validation worktree when the prospective merge must be
tested separately. Record its path, base/head, temporary test-merge commit/tree, and ownership;
create no remote branch and leave the delivery history untouched. Remove only that clean,
recoverable temporary checkout after retaining evidence, without force. This exception grants
no delivery-worktree/branch cleanup, integration, or unrelated-resource authority; preserve and
report a temporary checkout that cannot be safely removed.

Complete passing local evidence satisfies the repository's validation gate during confirmed
non-execution. Keep the hosted failure/skips visible and label the evidence as local, never as
a successful GitHub check. Enforced GitHub checks and required approvals remain mandatory;
if they cannot be satisfied, integration stops with the exact owner/action. No admin bypass,
fabricated check result, auto-merge, or merge queue is authorized.

If the managed review workflow also cannot execute, the root may orchestrate the same authorized
review directly. First inventory native requests, reviews, comments, and existing bot records;
reconcile any outstanding or uncertain attempt before sending one request. If a bot reservation
or uncertain record already exists, manual takeover requires a terminal run plus evidence that
its native submission never occurred, and a fresh inventory confirming no delivered request.
Record the original bot comment/round/attempt/marker and its proven non-delivery in the manual
ledger; this root assessment permits one replacement request without editing bot state. An
unknown submission outcome still blocks takeover. The replacement belongs to that same logical
round, records its current snapshot separately, and links the original marker in its ledger.
For Codex, post `@codex review` once at the verified snapshot. Both Lite and Balanced Copilot
can be requested through the Reviewers UI during this outage; verify and record the actual
Lite/Balanced effort from the resulting overview. Do not silently change effort/provider. Keep one root-authored reconciliation record per round, marked
`<!-- ai-review-manual:v1:PR_NUMBER:ROUND -->`, with provider/attempt history, base/head, request
and response URLs, proven non-delivery, findings/dispositions, local validation, and closure
assessment. Preserve the original reviewed SHA separately from later fixes. A posted request,
eyes reaction, or absent reply is not a completed review. Never impersonate bot-authored
`ai-review-state` records or silently restart numbering.

Root-recorded manual closure is allowed once the same acceptance, finding, local/hosted
validation, enforced-check/approval, and final-diff gates pass. When Actions resumes and the
PR is still open, reconcile any original non-delivered bot attempt as `not_sent`/`failed`
with its proof. A numeric provider review may be reconciled as `completed` only if the writer's
original provider/head/time checks actually match. A clean reaction on a root-authored request
or a changed snapshot may not be representable by that bot attempt: retain explicit manual
completion/closure instead of fabricating a bot-authored request or response.

For only manual/native history on an open PR, use `adopt` only when at least two actual prior
rounds and the writer's other prerequisites are satisfied, then `close` when justified. A
single actual manual round keeps explicit manual closure; never invent a second round merely
to satisfy the writer's legacy-import minimum. Already merged PRs retain their manual closure
and audit history without reopening: the managed writer accepts only open PRs. Do not replay
the initial request or request a clean review merely to repair the ledger. Manual assessments,
original bot records, and failed automation evidence remain visible.

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

Confirmed Actions non-execution uses the equivalent local CI procedure above. Its complete
passing evidence is a separate local result; it does not convert a failed hosted check to a
pass, reduce selected coverage or budgets, or waive enforced protection and approvals.

## Templates and Records

Before creating or substantially updating an Issue, PR, evidence record, or status comment,
read the [authoring guide](.agents/skills/github-task-lifecycle/references/issue-pr-authoring.md)
and its raw template. Preserve every visible heading and the template's meaning. Templates do
not authorize mutations or replace native relations.

## Completion and Cleanup

Before closure, classify the PR as completing or partial against every remaining acceptance
criterion of its owning Issue and evidence integrated on the accepted base. Review closure,
mergeability, or an Issue keyword alone is not proof of completing delivery. Only a completing
delivery may close its owning Issue or move its Project item to `Done`. Partial integration
keeps the Issue open and its Project item `In progress`; record remaining criteria and their
owner/action, and preserve resources needed by that unfinished delivery. A child PR never
closes its parent.

Complete means scope, acceptance criteria, required verification, verified review closure, and
current applicable validation and enforced checks/protections pass; the PR is squash-merged; the completing Issue is closed; and Project status
is `Done`. Parent Issues close only after all required children and integration are complete.

Before cleanup, inventory the worktree, branch, HEAD, porcelain, PR/Issue state, integration,
and recoverability. Never force-remove, reset, clean, rebase, or force-push to manufacture a
clean state. Remove a worktree or delete a branch only under recorded authority, after
integration and a clean-status check; verify the result. Preserve parent/release worktrees
until their integration completes.
