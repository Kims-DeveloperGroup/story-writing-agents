# Agent Task Runbook

Use this runbook for GitHub-tracked implementation. The rules in
[GITHUB_WORKFLOW.md](GITHUB_WORKFLOW.md) and
[COMMIT_POLICY.md](COMMIT_POLICY.md) remain authoritative. An explicit
implementation request bundles only the initial-planning mutations defined by
the workflow policy and creation of its native Development-linked task branch.
Obtain explicit authorization for every other GitHub mutation, push, pull
request, merge, Issue closure, staging, commit, or branch deletion.

## Initialize GitHub Planning

Before task branches, worktrees, writers, or tracked-file edits, read
`README.md` and the policies, classify the planning gate, and search all Issue
states for an existing matching scope. Preflight repository access, `gh` auth
with `project` scope, the canonical Project, its fields, milestones, Iterations,
dependencies, and current worktrees.

For large work, draft the complete root and all known descendants, including
nested parents and leaves, after read-only requirements and any modularity
analysis. Create the root first, then each descendant; add every URL to Project
#1 and establish native relationships. Execute and verify one mutation at a
time; stop on the first failure. This abbreviated flow uses stable outputs:

```bash
set -euo pipefail

repo=Kims-DeveloperGroup/story-writing-agents
project_owner=Kims-DeveloperGroup
project_number=1

parent_url="$(gh issue create --repo "$repo" --title "<parent title>" \
  --body-file "<parent body>" --label "<type>" --label "area:<name>")"
parent_item_id="$(gh project item-add "$project_number" \
  --owner "$project_owner" --url "$parent_url" --format json --jq .id)"

child_url="$(gh issue create --repo "$repo" --title "<child title>" \
  --body-file "<child body>" --label "<type>" --label "area:<name>" \
  --milestone "<milestone>")"
child_item_id="$(gh project item-add "$project_number" \
  --owner "$project_owner" --url "$child_url" --format json --jq .id)"
child_number="${child_url##*/}"
child_id="$(gh api --method GET "repos/$repo/issues/$child_number" --jq .id)"
parent_number="${parent_url##*/}"
gh api --method POST "repos/$repo/issues/$parent_number/sub_issues" \
  -F sub_issue_id="$child_id"
```

Use native `blocked by` relationships for dependencies. Resolve Project, item,
field, option, and Iteration IDs at runtime. Set one logical Iteration on the
root and every descendant:

```bash
gh project item-edit --id <item-id> --project-id <project-id> \
  --field-id <iteration-field-id> --iteration-id <iteration-id>
```

Set all other required metadata from the policy. If no unused logical
Iteration exists for a new root, stop for explicit Project-schema
authorization. Set Priority through the organization Issue field, not the
Project field with the same display name.

Re-read Issue bodies, native hierarchy and dependencies, Project membership,
fields, milestone, and the root-wide Iteration. Record stable URLs and IDs.
Set `github_planning_gate = passed` only when the graph is complete and one
unblocked leaf is `Ready`; otherwise set `blocked`, preserve successful
mutations, and repair only verified missing state. A standalone task uses the
same procedure without a parent. A later child must pass this check before its
scope is written.

## Branch and Worktree Model

```text
main
└── issue-100-parent          parent worktree; PR to main (merge commit)
    ├── issue-101-first-task  child worktree; PR to parent (squash merge)
    └── issue-102-next-task   child worktree; PR to parent (squash merge)
```

- Record the parent branch and exact starting commit in the parent Issue.
  Normally create it from the current `origin/main`; never assume a local
  `main` that is ahead or diverged is the intended base.
- Name parent and child branches `issue-<number>-<short-slug>`.
- Put task worktrees in a sibling root such as
  `../<repository>-worktrees/issue-<parent>-<slug>/`. Keep the primary
  worktree on `main` and preserve its unrelated changes.
- Assign one integration owner to the parent worktree and one owner to each
  child worktree. Worktrees may coexist, but only one write-capable owner is
  active at a time. A dependent child waits until its prerequisite is
  integrated.
- Do not commit feature work directly to the parent branch. It accepts child
  squash merges and synchronization merges from `main`. Handle an integration
  fix through a child Issue, branch, and worktree.
- Do not rebase or force-update a published parent or child branch. After a
  fresh fetch and approval, merge `origin/main` into the parent and the parent
  into active children; resolve a child's conflicts in its own worktree.

Every child PR targets the parent branch and uses **Squash and merge**. Child
commits still follow the commit policy, while the resulting squash commit must
be one coherent change that identifies the child Issue and PR. The final
parent PR targets `main` and uses a merge commit, preserving one squash commit
per child PR.

For a standalone leaf Issue, create its branch in a sibling worktree from the
approved `main` ref and target its PR directly to `main`; omit the parent steps.

## Create the Worktrees

First fetch and inspect without changing branches:

```bash
git fetch origin
git worktree list
git status --short
git log --left-right --count origin/main...main
```

If `main` is ahead, behind, or diverged, identify and record the approved base
commit before continuing. After the gate passes, create the remote task branch
through the Issue's native Development relationship. Resolve IDs and the
approved base OID at runtime; link the parent branch to the parent Issue and a
child branch to its child Issue:

```bash
repo=Kims-DeveloperGroup/story-writing-agents
issue_number=<issue-number>
branch=issue-<issue-number>-<slug>
base_oid=<approved-base-oid>
repo_id="$(gh repo view "$repo" --json id --jq .id)"
issue_id="$(gh issue view "$issue_number" --repo "$repo" --json id --jq .id)"

gh api graphql \
  -f query='mutation($repo:ID!,$issue:ID!,$oid:GitObjectID!,$name:String!){
    createLinkedBranch(input:{repositoryId:$repo,issueId:$issue,oid:$oid,name:$name}){
      linkedBranch{ref{name target{oid}}}
    }
  }' \
  -F repo="$repo_id" -F issue="$issue_id" -F oid="$base_oid" -f name="$branch"
```

Stop if the branch already exists but is not linked; do not silently recreate,
rename, or replace it. Use a linked PR to repair legacy traceability when that
action is authorized. The Development operation created the remote branch;
fetch it, then adapt these tracking-worktree templates:

```bash
git fetch origin "$branch"

git worktree add --track -b issue-<parent>-<slug> \
  ../<repository>-worktrees/issue-<parent>-<slug>/parent \
  origin/issue-<parent>-<slug>

git worktree add --track -b issue-<child>-<slug> \
  ../<repository>-worktrees/issue-<parent>-<slug>/issue-<child>-<slug> \
  origin/issue-<child>-<slug>
```

If the linked branch already exists locally, omit `--track -b` and add a
worktree for that branch. A branch can be checked out in only one worktree.
Create a child only from a clean, current parent commit that exists on GitHub.

Verify the Issue's Development link before writing:

```bash
gh api graphql \
  -f query='query($owner:String!,$repo:String!,$number:Int!){
    repository(owner:$owner,name:$repo){
      issue(number:$number){linkedBranches(first:20){nodes{ref{name}}}}
    }
  }' \
  -F owner=Kims-DeveloperGroup -F repo=story-writing-agents \
  -F number=<issue-number>
```

After a PR is linked, verify the Project's built-in field:

```bash
gh project item-list 1 --owner Kims-DeveloperGroup --limit 100 --format json \
  --jq '.items[] | select(.content.number == <issue-number>) |
    .["linked pull requests"]'
```

## Route Work on the Critical Path

Apply the canonical [Delegation-Gain Gate](AGENTS.md#delegation-gain-gate) and
[Orchestration Sequence](AGENTS.md#orchestration-sequence) before each
specialist dispatch:

1. Record `root_sequential_estimate`, `parallel_critical_path`,
   `spawn_wait_handoff_synthesis_overhead`, the calculated
   `predicted_critical_path_savings_seconds`, and its percentage. Dispatch only
   when both canonical gain thresholds pass or the decision names
   `material_correctness_risk_reduction` or `mandatory_ownership_rule`.
2. Use the root fast path only for a clear one- or two-file production-code or
   configuration change within an established boundary. It never includes
   documentation or tests. Assign all README, policy, guide, and example writes
   exclusively to `documentation_maintainer`, and all test work exclusively to
   `test_engineer`; assign triggered modularity work to
   `modularity_maintainer`.
3. Dispatch `requirements_analyst` only for substantial ambiguity and
   `regression_diagnostician` only for an unknown failure. Do not dispatch an
   agent merely to run a deterministic command; batch safe independent static
   reads in the main agent's tool calls.
4. Apply the canonical
   [Role, Mode, and Control Gate](AGENTS.md#role-mode-and-control-gate). Reject
   an unsupported role/mode before spawn. The child performs only identity and
   effective-capability preflight first; reject unavailable evidence or a
   matrix mismatch before further task reads or writes.
5. Apply the canonical [Root-Capability Routing](AGENTS.md#root-capability-routing)
   algorithm. Resolve the root pair, measure and classify only the child's
   dispatch context, classify complexity/risk and role needs, derive and clamp
   each axis independently, then treat the capped pair as the preferred
   minimum-quality target within the effective root ceiling. Supply both child
   overrides atomically or omit both for exact inheritance; never guess a
   cross-family order, select a weaker pair automatically, or exceed either
   effective root value.
6. Narrow or split oversized context before dispatch. If high-consequence work
   is ceiling-limited, stop for a narrower task or stronger root session.
   Otherwise record all routing inputs, desired/capped/selected pairs,
   availability, limitation, and fallback or inheritance evidence in the
   canonical packet.
7. Send the compact `task_packet` in bounded, self-contained context. Full
   history requires its named reason, relevant bound, stale-context risk, root
   approval, and task evidence. Thread reuse alone does not authorize it.
8. Record every requested result ID and expected role/mode. Cross the join
   barrier only when none is running or missing and every response validates
   against the exact canonical `result_packet` schema; do not synthesize
   otherwise.
9. Retry one transient spawn/delivery/join control failure exactly once only
   after proving no task write occurred. For possible partial-writer timeout or
   interruption, inspect state and resume the same thread; never retry blindly.
   Do not retry policy, capability, scope, or schema failures.
10. Resolve conflicts by evidence level and role ownership, never voting. Route
    unresolved material conflicts to the owner, root, or user under the
    canonical gate. Root fallback may use only existing root authority and
    never bypasses mandatory documentation, test, or modularity ownership.

Keep writers sequential. After production stabilizes, fuse test planning,
editing, exact execution, and final behavioral/coverage review into one
`test_engineer` implementation turn when its dependencies permit it; otherwise
resume that same thread after the dependency stabilizes.

## Plan and Record Verification

Apply [Adaptive Verification](AGENTS.md#adaptive-verification) as the canonical
selection, budget, reuse, escalation, and completion contract. Before
verification begins, the main agent records a compact root-owned plan with the
task's `verification_class` and risk rationale; each acceptance criterion's
mandatory evidence; changed inputs, affected inventory, and justified
exclusions; the exact checks or test IDs in `authorized_selection` and their
selection rationale; one `evidence_execution_owner` per evidence item;
`estimated_seconds`, estimate source and confidence;
`total_stage_budget_seconds`, `max_shard_seconds`, `spent_seconds`, and
`remaining_seconds`; stop and escalation conditions; each objective full-suite
trigger or an explicit record that none applies; reusable evidence and its
invalidation analysis; and residual risk.

Before launch, deduplicate every item on exactly `normalized_command`,
`exact_ids`, `relevant_input_digest`, and `environment_class`. Normally execute
exact and semantic-cluster evidence locally and affected or full suites in CI;
do not duplicate equivalent broad local and CI runs unless a local-environment
acceptance criterion requires both.

Execute and ledger that plan as follows:

1. Compare each command estimate with both `max_shard_seconds` and
   `remaining_seconds` before launch. If a selection is too large, partition it
   into deterministic, disjoint exact-ID shards whose union is the authorized
   selection and in which each ID occurs once. Run sequentially unless the main
   agent separately authorizes isolated, disjoint fast-fail shards; no more than
   two may run concurrently. Stop before starting a shard that would exceed the
   remaining budget and return the decision to the main agent.
2. For every selection or shard, record its purpose, evidence level, exact IDs
   and command, provenance, actual duration and result, manifest identity,
   `relevant_input_digest`, `completed_ids`, `failed_ids`, `accepted_skips`,
   and `unexecuted_ids`. Keep cumulative spent and remaining budget current.
3. Treat a timeout, replacement request, or other interruption as neither a
   pass nor an assertion failure. Stop only the owned process, preserve the
   completed, failed, skipped, and unexecuted IDs, and return that state to the
   main agent. Resume only unexecuted or invalidated IDs whose inputs remain
   valid.
4. Reuse evidence only when its purpose and `relevant_input_digest` remain
   unchanged and the main agent has proved intervening changes are disjoint.
   A commit SHA is provenance, not sufficient reuse proof; invalidate evidence
   after any relevant or uncertain input or dependency change.
5. Run `full_suite` only when the plan records an objective full-suite trigger.
   Tracked implementation, final review, and suite availability do not make it
   the default. If required evidence exceeds the accepted budget, return the
   completed evidence, remaining mandatory evidence, revised estimate, and
   decision alternatives instead of silently omitting it.
6. Before handoff or completion, confirm that all mandatory evidence is current
   and green or an accepted skip, failures are resolved, the inventory is
   sufficient, and the main agent has accepted the recorded residual risk.
   Only then record the terminal evidence state `complete`; partial evidence or
   budget exhaustion never establishes completion.

## Task Lifecycle

1. **Pass the planning gate.** Complete or verify the initialization above.
   Record the gate state, Issue graph, Project metadata, logical Iteration,
   dependencies, approved base, and authorization. Stop before writing unless
   the gate is `passed` or `not_required`.
2. **Prepare the parent.** Create or reuse its Development-linked branch and
   worktree at the recorded base commit, assign the integration owner, verify
   the link, and move the parent to the correct Project state. After the first
   child lands, open the parent PR to `main` as a draft with `Refs #<parent>`
   when authorized.
3. **Start one child.** Confirm that the child is `Ready`, assigned the root's
   Iteration, and unblocked. Create its Development-linked branch and worktree
   from the current parent commit, assign its owner, verify the link, and move
   it to `In progress` before tracked edits.
4. **Implement and verify.** Change only the child scope. Follow the commit
   policy and the routed ownership above; the root fast path never absorbs test
   or documentation writes. Apply the verification plan and evidence ledger,
   run its authorized checks through the assigned execution owner, and review
   the final diff and worktree status.
5. **Review the child.** Merge the latest parent branch into the child without
   rebasing, rerun affected checks, and open a draft PR whose base is the
   parent branch and whose head is the child branch. Use `Refs #<child>`. Mark
   it ready and move the child to `In review` only when all evidence is present
   and the PR appears in Development and Project `Linked pull requests`.
6. **Integrate the child.** Reconfirm the PR base and head, then use **Squash
   and merge** when authorized. The child must contain the current parent tip;
   otherwise merge the parent into it, rerun affected checks, and repeat
   review. Record the child head SHA and resulting squash SHA, then fetch and
   fast-forward the clean parent worktree from `origin/<parent-branch>` with
   `--ff-only`; stop on divergence. Verify the squash diff and run focused
   integration checks there. On success, close the child Issue manually and
   verify `Done`. On failure, block sibling merges and keep the child open in
   `In progress`; require an authorized revert PR or follow-up child fix instead
   of editing the parent directly. If it was already closed, reopen it to
   `Backlog` and replan it as required by the policy.
7. **Complete the parent.** After all required children are `Done`, merge the
   exact approved `origin/main` tip into the parent after a fresh fetch, resolve
   conflicts there, and run the complete parent acceptance checks. Mark the
   parent PR ready, replace `Refs #<parent>` with `Closes #<parent>`, move it to
   `In review`, and merge it to `main` with a merge commit when authorization
   includes the resulting parent closure. Record and verify the final merge
   SHA, Issue closure, Project `Done`, and milestone state.
8. **Clean up and hand off.** Remove a worktree only after its status is clean
   and its integration is verified. Never use forced worktree removal. Branch
   deletion requires separate authorization; because squash merging does not
   mark a child branch as Git-merged, verify PR and commit recoverability before
   deleting it. Retain the parent worktree until the parent reaches `main`.

Use the exact canonical `task_packet` and `result_packet` schemas in
[Handoff and Result Contracts](AGENTS.md#handoff-and-result-contracts). Nest the
planning-gate evidence, Issue and PR links, branches and SHAs, worktree,
Development and Project metadata, Iteration, criteria, adaptive-verification
ledger, blockers, GitHub mutations, cleanup, and next owner under their defined
fields. Keep the packet compact; link stable evidence instead of copying raw
logs, full Issue bodies, or the complete plan.
