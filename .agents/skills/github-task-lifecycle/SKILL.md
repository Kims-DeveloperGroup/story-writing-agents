---
name: github-task-lifecycle
description: >-
  GitHub-tracked work: supplies/guards planning,linking,branching,PR,integration,cleanup
  commands; excludes untracked/story-runtime work.
---

[GITHUB_WORKFLOW.md](../../../GITHUB_WORKFLOW.md): authorization/metadata;
[AGENTS.md](../../../AGENTS.md): routing/verification.

Before creating or updating reader-facing records through any client, read the
[authoring guide](references/issue-pr-authoring.md) and relevant template source.

Implementation authorizes only policy-defined planning/Development-linked branch
creation. Separately authorize every push/stage/commit/PR/merge/closure/deletion/cleanup.
Preflight repository/`gh`-project-scope/Project-fields/milestones/Iterations/dependencies/
worktrees; stop on missing/mismatch. Mutate/verify once; preserve successes; repair gaps.

Search all Issue-states; create/reuse root; descendants require general-sibling-gate. Resolve IDs. Use
one-Iteration-per-Requirement-release; leaves-inherit; root-tracks-current-`Must`;
completed-releases-retain-theirs; creation needs schema authorization. Create fields/native-relations; re-read
complete graph/metadata and unblocked `Ready` leaf.

|Rule|Procedure|
|---|---|
|Requirement|Set-organization-Issue-Requirement-`Must`/`Recommended`/`Optional`;record-basis;existing-only-at-new-phase/Iteration;unset-blocks-`Ready`;necessary-dependencies-`Must`;never-agent-downgrade|
|Release-plan|Classify-all-known-leaves;one-implementation/delivery-unit=current-Issue/no-sole-child-or-wrapper;create/verify-only-known-at-least-2-direct-independently-complete-necessary-slice/release-siblings;subdivide-only-into-at-least-2-independent-children-else-enlarge/update-own-scope;no-phase/metadata/branch-PR/scope-mirror-placeholders;single-release=current/root-Issue;numbered-release-children-only-if-at-least-2-releases-required;one-unit-release-implements-directly;feature-children-only-if-at-least-2-independent-slices;require-each-implementation-leaf-Size;load=sum(unfinished-leaf-points)<=capacity;verify-integrity/topology|
|Resize|Preserve-all-`Must`;select-`Recommended`-by-accepted-user-order;defer-`Optional`-first,then-nonfitting-`Recommended`-to-`Backlog`;clear-Iteration/wave;record-reason/reconsideration;pause-only-with-approval/partial-state/branch-PR-disposition/recoverability;split-complete-slices-or-record-indivisible-exception|
|Bases/order|Release/standalone=approved-main;feature-leaf=current-release;dependencies-wait;ordinary-child-parent-excludes-release-PRs|
|Local-branch|Omit-`--track`/`-b`|
|Release-PR|After-first-feature-leaf:create-authorized-draft-targeting-`main`;after-all-leaves:sync-main/run-final-evidence/Refs-to-Closes/set-`In review`/merge/close-release|
|Umbrella|Normally-planning-only/administrative-close-after-`Must`/core-verification;distinct-diff=own-Development-linked-branch/PR-target-`main`,start-after-all-`Must`-release-PRs/core-integration,may-close-root;other-tiers-linked/nonblocking|
|Sync|Fresh-fetch;reconfirm-head/base-before-sync/merge|
|Failure|Block-siblings;use-authorized-revert-PR/follow-up-child|
|Transition|Re-read-draft/Development/Project-status/closure/linked-PRs|

```bash
issue_url="$(gh issue create --repo "$repository" --title "$title" --body-file "$body" --label "$type" --label "$area")"
item_id="$(gh project item-add "$project_number" --owner "$organization" --url "$issue_url" --format json --jq .id)"
gh project item-edit --id "$item_id" --project-id "$project_id" --field-id "$field_id" --single-select-option-id "$option_id"
gh project item-edit --id "$item_id" --project-id "$project_id" --field-id "$iteration_field_id" --iteration-id "$iteration_id"
gh api --method POST "repos/$repository/issues/$parent/sub_issues" -F sub_issue_id="$child_id"
gh api --method POST "repos/$repository/issues/$child/dependencies/blocked_by" -F issue_id="$blocker_id"
```

Record parent branch/start-SHA; name `issue-<n>-<slug>`; use sibling-worktree; preserve
primary changes. Children need clean/current/GitHub-present parent commits. Legacy
traceability requires authorized linked PR; stop if unlinked. Keep one worktree/branch; never recreate/
replace/rebase/force-update/reset/checkout/clean/revert to match.

```bash
gh api graphql -f query='mutation($r:ID!,$i:ID!,$o:GitObjectID!,$n:String!){createLinkedBranch(input:{repositoryId:$r,issueId:$i,oid:$o,name:$n}){linkedBranch{ref{name target{oid}}}}}' -F r="$repo_id" -F i="$issue_id" -F o="$base_oid" -f n="$branch"
git fetch origin "$branch"
git worktree add --track -b "$branch" "$worktree" "origin/$branch"
pwd -P
git rev-parse --show-toplevel
git branch --show-current
git rev-parse HEAD
git merge-base HEAD "$base_oid"
git merge-base --is-ancestor "$base_oid" HEAD
git status --porcelain=v1 -z --untracked-files=all | od -An -tx1
git status --porcelain=v1 -z --untracked-files=all | shasum -a 256
```

Match handoff capability/identity/ancestry/targets and porcelain bytes/hash/paths. Before
other task reads/writes, verify Development and Project `Linked pull requests`:

```bash
gh api graphql -f query='query($o:String!,$r:String!,$n:Int!){repository(owner:$o,name:$r){issue(number:$n){linkedBranches(first:20){nodes{ref{name}}}}}}' -F o="$organization" -F r="$repository_name" -F n="$issue"
gh project item-list "$project_number" --owner "$organization" --limit 100 --format json --jq '.items[]|select(.content.number=='"$issue"')|.["linked pull requests"]'
```

Release accepts only authorized leaf squashes at current tip and approved-main sync; fixes
use leaves. Sync release into leaf; run affected checks. Follow commit policy; squash
coherently identifies Issue/PR. Record SHAs; fast-forward clean parent `--ff-only`; inspect/verify.
Final release merge verifies SHA/closure/`Done`/milestone.

```bash
gh pr create --draft --base "$base" --head "$branch" --body "Refs #$issue"
gh pr ready "$pr"
gh pr merge "$child_pr" --squash
gh pr merge "$release_pr" --merge
gh issue close "$issue" --repo "$repository"
git worktree remove "$worktree"
git worktree list --porcelain
test ! -e "$worktree"
```

Follow [completion](../../../GITHUB_WORKFLOW.md#completion-and-exceptions) and
[cleanup](../../../GITHUB_WORKFLOW.md#worktree-disposition-and-cleanup) rules. Never auto-merge/
close/clean or force-remove.
