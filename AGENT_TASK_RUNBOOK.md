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
with `project` scope, the canonical repository and Project declared in the
consumer repository instructions, Project fields, milestones, Iterations,
dependencies, and current worktrees. When an installation lock exists, use its
selected profiles as authoritative. Compare repository instructions only when
they independently declare a profile selection; this generic profile never
declares consumer profiles or GitHub identifiers.

For large work, draft the complete root and all known descendants, including
nested parents and leaves, after read-only requirements and any modularity
analysis. Create the root first, then each descendant; add every URL to Project
identified by the canonical Project number declared in repository instructions
and establish native relationships. Execute and verify one mutation at a time;
stop on the first failure. This abbreviated flow uses stable outputs:

```bash
set -euo pipefail

: "${REPOSITORY_ENGINEERING_REPOSITORY:?Set canonical owner/repository from repository instructions}"
: "${REPOSITORY_ENGINEERING_PROJECT_OWNER:?Set from repository instructions}"
: "${REPOSITORY_ENGINEERING_PROJECT_NUMBER:?Set from repository instructions}"
repo_slug="$REPOSITORY_ENGINEERING_REPOSITORY"
project_owner_slug="$REPOSITORY_ENGINEERING_PROJECT_OWNER"
project_number_value="$REPOSITORY_ENGINEERING_PROJECT_NUMBER"

test "$(gh repo view "$repo_slug" --json nameWithOwner --jq .nameWithOwner)" = "$repo_slug"

parent_url="$(gh issue create --repo "$repo_slug" --title "<parent title>" \
  --body-file "<parent body>" --label "<type>" --label "area:<name>")"
parent_item_id="$(gh project item-add "$project_number_value" \
  --owner "$project_owner_slug" --url "$parent_url" --format json --jq .id)"

child_url="$(gh issue create --repo "$repo_slug" --title "<child title>" \
  --body-file "<child body>" --label "<type>" --label "area:<name>" \
  --milestone "<milestone>")"
child_item_id="$(gh project item-add "$project_number_value" \
  --owner "$project_owner_slug" --url "$child_url" --format json --jq .id)"
child_number="${child_url##*/}"
child_id="$(gh api --method GET "repos/$repo_slug/issues/$child_number" --jq .id)"
parent_number="${parent_url##*/}"
gh api --method POST "repos/$repo_slug/issues/$parent_number/sub_issues" \
  -F sub_issue_id="$child_id"
```

Set the three task-specific environment variables from the exact values in the
consumer repository instructions before execution, then preflight all three
resolved identifiers. Never infer a repository from the checkout remote or a
Project solely from its number.

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
<default-branch>
└── issue-100-parent          parent worktree; PR to default branch (merge commit)
    ├── issue-101-first-task  child worktree; PR to parent (squash merge)
    └── issue-102-next-task   child worktree; PR to parent (squash merge)
```

- Record the parent branch and exact starting commit in the parent Issue.
  Normally create it from the current `$repo_remote/$default_branch`, after
  resolving both the unique canonical-repository remote and `default_branch`;
  never assume that `origin` or a same-named local branch is the intended base.
- Name parent and child branches `issue-<number>-<short-slug>`.
- Put task worktrees in a sibling root such as
  `../<repository>-worktrees/issue-<parent>-<slug>/`. Keep the primary
  worktree on the resolved default branch and preserve its unrelated changes.
- Assign one integration owner to the parent worktree and one owner to each
  child worktree. Worktrees may coexist, but only one write-capable owner is
  active at a time. A dependent child waits until its prerequisite is
  integrated.
- Do not commit feature work directly to the parent branch. It accepts child
  squash merges and synchronization merges from the resolved default branch.
  Handle an integration fix through a child Issue, branch, and worktree.
- Do not rebase or force-update a published parent or child branch. After a
  fresh fetch and approval, merge `$repo_remote/$default_branch` into the
  parent and the parent into active children; resolve a child's conflicts in
  its own worktree.

Every child PR targets the parent branch and uses **Squash and merge**. Child
commits still follow the commit policy, while the resulting squash commit must
be one coherent change that identifies the child Issue and PR. The final
parent PR targets the resolved default branch and uses a merge commit,
preserving one squash commit per child PR.

For a standalone leaf Issue, create its branch in a sibling worktree from the
approved default-branch ref and target its PR directly to the resolved default
branch; omit the parent steps.

## Create the Worktrees

First fetch and inspect without changing branches:

```bash
set -euo pipefail

: "${REPOSITORY_ENGINEERING_REPOSITORY:?Set canonical owner/repository from repository instructions}"
repo_slug="$REPOSITORY_ENGINEERING_REPOSITORY"
canonical_repo_slug="$(gh repo view "$repo_slug" --json nameWithOwner --jq .nameWithOwner)"
normalize_repo_slug() {
  printf '%s' "$1" | tr '[:upper:]' '[:lower:]'
}
test "$(normalize_repo_slug "$canonical_repo_slug")" = \
  "$(normalize_repo_slug "$repo_slug")"
repo_slug="$canonical_repo_slug"

repo_remote=""
repo_remote_matches=0
while IFS= read -r remote_name; do
  remote_url="$(git remote get-url "$remote_name")"
  remote_slug="$(
    gh repo view "$remote_url" --json nameWithOwner --jq .nameWithOwner \
      2>/dev/null || true
  )"
  if test -n "$remote_slug" && \
      test "$(normalize_repo_slug "$remote_slug")" = \
        "$(normalize_repo_slug "$repo_slug")"; then
    repo_remote="$remote_name"
    repo_remote_matches=$((repo_remote_matches + 1))
  fi
done < <(git remote)
test "$repo_remote_matches" -eq 1
test -n "$repo_remote"

git fetch "$repo_remote"
git worktree list
git status --short
default_branch="$(gh repo view "$repo_slug" \
  --json defaultBranchRef --jq .defaultBranchRef.name)"
test -n "$default_branch"
git show-ref --verify "refs/remotes/$repo_remote/$default_branch"
git show-ref --verify "refs/heads/$default_branch"
git log --left-right --count \
  "$repo_remote/$default_branch...$default_branch"
```

Fail if no local remote or more than one local remote resolves to the canonical
repository identity. Do not select a remote by conventional name or silently
fall back to `origin`. If the local default branch is ahead, behind, or
diverged, identify and record the approved base commit before continuing.
After the gate passes, create the remote task branch through the Issue's native
Development relationship. Resolve the consumer-owned repository slug, Issue,
exact approved base object, and branch name at runtime:

```bash
issue_number=<issue-number>
branch=issue-<issue-number>-<slug>
base_oid=<approved-base-oid>
repo_id="$(gh repo view "$repo_slug" --json id --jq .id)"
issue_id="$(gh issue view "$issue_number" --repo "$repo_slug" --json id --jq .id)"

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
action is separately authorized. The Development operation creates the remote
branch. Fetch it, then adapt the tracking-worktree template:

```bash
branch_head_ref="refs/heads/$branch"
branch_remote_ref="refs/remotes/$repo_remote/$branch"
branch_fetch_refspec="$branch_head_ref:$branch_remote_ref"
git check-ref-format "$branch_head_ref"
git check-ref-format "$branch_remote_ref"
if ! git config --get-all "remote.$repo_remote.fetch" | \
    grep -Fqx -- "$branch_fetch_refspec"; then
  git config --add "remote.$repo_remote.fetch" "$branch_fetch_refspec"
fi
git fetch "$repo_remote" "$branch_fetch_refspec"
git show-ref --verify "$branch_remote_ref"

git worktree add --track -b "$branch" \
  ../<repository>-worktrees/issue-<parent>-<slug>/issue-<number>-<slug> \
  "$branch_remote_ref"
```

The narrow branch-specific fetch mapping makes the explicit remote-tracking
ref recognizable to `--track` even when the remote's existing fetch mappings
exclude the task branch. Do not replace or broaden other remote mappings.

For a parent, use its parent-worktree path; for a child, use its child-worktree
path and the current clean parent commit as `base_oid`. If the linked branch
already exists locally, omit `--track -b` and add a worktree for that branch.
A branch can be checked out in only one worktree.

Verify the Issue's Development link before writing:

```bash
repo_owner="${repo_slug%%/*}"
repo_name="${repo_slug#*/}"

linked_branch_pages="$(gh api graphql --paginate --slurp \
  -f query='query($owner:String!,$repo:String!,$number:Int!,$endCursor:String){
    repository(owner:$owner,name:$repo){
      nameWithOwner
      issue(number:$number){
        number
        repository{nameWithOwner}
        linkedBranches(first:100,after:$endCursor){
          nodes{ref{name}}
          pageInfo{hasNextPage endCursor}
        }
      }
    }
  }' \
  -F owner="$repo_owner" -F repo="$repo_name" \
  -F number="$issue_number")"

test "$(printf '%s\n' "$linked_branch_pages" | jq -r \
  --arg repo "$repo_slug" --argjson issue "$issue_number" '
    length > 0 and all(.[];
      ((.data.repository.nameWithOwner | ascii_downcase) ==
        ($repo | ascii_downcase)) and
      .data.repository.issue.number == $issue and
      ((.data.repository.issue.repository.nameWithOwner | ascii_downcase) ==
        ($repo | ascii_downcase)))
  ')" = true

test "$(printf '%s\n' "$linked_branch_pages" | jq -r \
  --arg branch "$branch" '
    [.[].data.repository.issue.linkedBranches.nodes[].ref.name] |
      index($branch) != null
  ')" = true
```

The paginated query and both assertions must succeed before any tracked-file
write. A printed response, a bounded first page, or a similarly named unlinked
branch is not verification.

After a PR is linked, use the ProjectV2 item node ID and Project node ID
resolved during planning. Query that exact item, prove that it belongs to the
declared Project and canonical repository Issue, paginate all linked pull
requests, and verify the expected canonical-repository PR. Do not select a
Project item from a bounded item list or by Issue number alone:

```bash
: "${REPOSITORY_ENGINEERING_PROJECT_OWNER:?Set from repository instructions}"
: "${REPOSITORY_ENGINEERING_PROJECT_NUMBER:?Set from repository instructions}"
project_owner_slug="$REPOSITORY_ENGINEERING_PROJECT_OWNER"
project_number_value="$REPOSITORY_ENGINEERING_PROJECT_NUMBER"
project_id=<resolved-project-v2-node-id>
project_item_id=<resolved-project-v2-item-node-id>
pr_number=<pull-request-number>

linked_pr_pages="$(gh api graphql --paginate --slurp \
  -f query='query($item:ID!,$endCursor:String){
    node(id:$item){
      __typename
      ... on ProjectV2Item{
        id
        project{
          id
          number
          owner{
            __typename
            ... on Organization{login}
            ... on User{login}
          }
        }
        content{
          __typename
          ... on Issue{number repository{nameWithOwner}}
        }
        fieldValueByName(name:"Linked pull requests"){
          __typename
          ... on ProjectV2ItemFieldPullRequestValue{
            pullRequests(first:100,after:$endCursor){
              nodes{number url repository{nameWithOwner}}
              pageInfo{hasNextPage endCursor}
            }
          }
        }
      }
    }
  }' \
  -F item="$project_item_id")"

test "$(printf '%s\n' "$linked_pr_pages" | jq -r \
  --arg item "$project_item_id" \
  --arg project "$project_id" \
  --arg owner "$project_owner_slug" \
  --argjson project_number "$project_number_value" \
  --arg repo "$repo_slug" \
  --argjson issue "$issue_number" '
    length > 0 and all(.[];
      .data.node.__typename == "ProjectV2Item" and
      .data.node.id == $item and
      .data.node.project.id == $project and
      .data.node.project.number == $project_number and
      ((.data.node.project.owner.login | ascii_downcase) ==
        ($owner | ascii_downcase)) and
      .data.node.content.__typename == "Issue" and
      .data.node.content.number == $issue and
      ((.data.node.content.repository.nameWithOwner | ascii_downcase) ==
        ($repo | ascii_downcase)) and
      .data.node.fieldValueByName.__typename ==
        "ProjectV2ItemFieldPullRequestValue")
  ')" = true

test "$(printf '%s\n' "$linked_pr_pages" | jq -r \
  --arg repo "$repo_slug" --argjson pr "$pr_number" '
    [.[].data.node.fieldValueByName.pullRequests.nodes[] |
      select(.number == $pr and
        ((.repository.nameWithOwner | ascii_downcase) ==
          ($repo | ascii_downcase)))] | length > 0
  ')" = true
```

## Task Lifecycle

1. **Pass the planning gate.** Complete or verify the initialization above.
   Record the gate state, Issue graph, Project metadata, logical Iteration,
   dependencies, approved base, and authorization. Stop before writing unless
   the gate is `passed` or `not_required`.
2. **Prepare the parent.** Create or reuse its Development-linked branch and
   worktree at the recorded base commit, verify the link, assign the
   integration owner, and move the parent to the correct Project state. After
   the first child lands, open the parent PR to the resolved default branch as
   a draft with `Refs #<parent>` when authorized.
3. **Start one child.** Confirm that the child is `Ready`, assigned the root's
   Iteration, and unblocked. Create its Development-linked branch and worktree
   from the current parent commit, verify the link, assign its owner, and move
   it to `In progress`.
4. **Implement and verify.** Change only the child scope. Follow the commit
   policy, update tests and documentation, run relevant checks, and review the
   final diff and worktree status.
5. **Review the child.** Merge the latest parent branch into the child without
   rebasing, rerun affected checks, and open a draft PR whose base is the
   parent branch and whose head is the child branch. Use `Refs #<child>`. Mark
   it ready and move the child to `In review` only when all evidence is present
   and the PR appears in Development and Project `Linked pull requests`.
6. **Integrate the child.** Reconfirm the PR base and head, then use **Squash
   and merge** when authorized. The child must contain the current parent tip;
   otherwise merge the parent into it, rerun affected checks, and repeat
   review. Record the child head SHA and resulting squash SHA, then fetch and
   fast-forward the clean parent worktree from
   `$repo_remote/<parent-branch>` with `--ff-only`; stop on divergence. Verify
   the squash diff and run focused integration checks there. On success, close
   the child Issue manually and
   verify `Done`. On failure, block sibling merges and keep the child open in
   `In progress`; require an authorized revert PR or follow-up child fix instead
   of editing the parent directly. If it was already closed, reopen it to
   `Backlog` and replan it as required by the policy.
7. **Complete the parent.** After all required children are `Done`, merge the
   exact approved `$repo_remote/$default_branch` tip into the parent after a
   fresh fetch, resolve conflicts there, and run the complete parent
   acceptance checks. Mark the parent PR ready, replace `Refs #<parent>` with
   `Closes #<parent>`, move it to `In review`, and merge it to the resolved
   default branch with a merge commit when authorization includes the resulting
   parent closure. Record and verify the final merge SHA, Issue closure,
   Project `Done`, and milestone state.
8. **Clean up and hand off.** Remove a worktree only after its status is clean
   and its integration is verified. Never use forced worktree removal. Branch
   deletion requires separate authorization; because squash merging does not
   mark a child branch as Git-merged, verify PR and commit recoverability before
   deleting it. Retain the parent worktree until the parent reaches the
   resolved default branch.

The handoff must include the planning-gate state and evidence, Issue and PR
links, base/head branches and SHAs, resulting squash SHAs, worktree paths,
Development and `Linked pull requests` evidence, Project metadata and
Iteration, completed and remaining criteria, checks, blockers, cleanup, GitHub
mutations, and the next owner or action.
