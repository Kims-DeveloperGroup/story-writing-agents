---
name: github-task-lifecycle
description: >-
  GitHub-tracked work: supplies/guards planning,linking,branching,PR,integration,cleanup
  commands; excludes untracked/story-runtime work.
---

[GITHUB_WORKFLOW.md](../../../GITHUB_WORKFLOW.md): authorization/metadata;
[AGENTS.md](../../../AGENTS.md): routing/verification.

Implementation authorizes only policy-defined planning/Development-linked branch
creation. Separately authorize every push/stage/commit/PR/merge/closure/deletion/cleanup.
Preflight repository/`gh`-project-scope/Project-fields/milestones/Iterations/dependencies/
worktrees; stop on missing/mismatch. Mutate/verify once; preserve successes; repair gaps.

Search every Issue-state; create/reuse root before descendants. Resolve IDs. Use one tree-wide
logical Iteration; creation needs schema authorization. Create fields/native relations; re-read
complete graph/metadata and unblocked `Ready` leaf.

|Rule|Procedure|
|---|---|
|Priority|Organization-Issue-field;never-same-named-Project-field|
|Bases/order|Parent/standalone=approved-main;child=current-parent;dependencies-wait|
|Local-branch|Omit-`--track`/`-b`|
|Parent-PR|After-first-child:create-authorized-draft|
|Final-parent|After-all-children:sync-approved-main;run-final-evidence;Refs-to-Closes;set-`In review`;merge|
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

Record parent branch/start SHA; name `issue-<n>-<slug>`; use sibling worktree; preserve
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

Parent accepts only authorized child squashes at current tip and approved-main sync; fixes
use children. Sync parent into child; run affected checks. Follow commit policy; squash
coherently identifies Issue/PR. Record SHAs; fast-forward clean parent `--ff-only`; inspect/verify.
Final parent merge verifies SHA/closure/`Done`/milestone.

```bash
gh pr create --draft --base "$base" --head "$branch" --body "Refs #$issue"
gh pr ready "$pr"
gh pr merge "$child_pr" --squash
gh pr merge "$parent_pr" --merge
gh issue close "$issue" --repo "$repository"
git worktree remove "$worktree"
git worktree list --porcelain
test ! -e "$worktree"
```

Reopen failed closure to `Backlog` and replan. Never auto-merge/
close/clean or force-remove. Cleanup needs authorization and clean/integrated/recoverable
state; branch deletion is separate. Retain parent until main integration.
