# Agent Task Runbook

Use for GitHub-tracked implementation. [GITHUB_WORKFLOW.md](GITHUB_WORKFLOW.md), [AGENTS.md](AGENTS.md), and [COMMIT_POLICY.md](COMMIT_POLICY.md) are authoritative. An implementation request authorizes only workflow-defined initial planning mutations and native Development-linked task-branch creation; every other GitHub mutation, push, PR, merge, Issue closure, stage, commit, or branch deletion needs explicit authorization.

## Initialize GitHub Planning

Before branches, worktrees, writers, or tracked edits, read `README.md` and policies; classify the planning gate; search all Issue states for matching scope; preflight repository access, `gh` auth with `project` scope, Project, fields, milestones, Iterations, dependencies, and worktrees.

For large work, after read-only requirements/modularity analysis, draft the complete root and known descendant tree. Create root then descendants; add each URL to Project #1; establish native relationships. Execute/verify one mutation at a time and stop at first failure. Essential pattern:

```bash
set -euo pipefail
repo=Kims-DeveloperGroup/story-writing-agents
owner=Kims-DeveloperGroup
project=1
parent_url="$(gh issue create --repo "$repo" --title "<title>" --body-file "<body>" --label "<type>" --label "area:<name>")"
parent_item="$(gh project item-add "$project" --owner "$owner" --url "$parent_url" --format json --jq .id)"
child_url="$(gh issue create --repo "$repo" --title "<title>" --body-file "<body>" --label "<type>" --label "area:<name>" --milestone "<milestone>")"
child_item="$(gh project item-add "$project" --owner "$owner" --url "$child_url" --format json --jq .id)"
child_id="$(gh api "repos/$repo/issues/${child_url##*/}" --jq .id)"
gh api --method POST "repos/$repo/issues/${parent_url##*/}/sub_issues" -F sub_issue_id="$child_id"
gh project item-edit --id <item-id> --project-id <project-id> --field-id <iteration-field-id> --iteration-id <iteration-id>
```

Use native `blocked by`; resolve Project/item/field/option/Iteration IDs at runtime. Set one logical Iteration across the root tree and all required metadata. If no unused logical Iteration exists for a new root, require explicit Project-schema authorization. Set Priority through the organization Issue field, not the same-named Project field.

Re-read bodies, hierarchy, dependencies, Project membership/fields, milestone, and root-wide Iteration; record stable URLs/IDs. Pass only when graph is complete and an unblocked leaf is `Ready`; otherwise block, preserve successes, and repair only verified gaps. Standalone work omits parent; later children repeat the gate.

## Branch and Worktree Model

```text
main
└── issue-100-parent          parent → main, merge commit
    ├── issue-101-first-task  child → parent, squash merge
    └── issue-102-next-task   child → parent, squash merge
```

Record parent branch/start SHA in its Issue; normally base on current `origin/main`, never assume divergent/ahead local `main`. Name branches `issue-<number>-<slug>` and use sibling `../<repository>-worktrees/issue-<parent>-<slug>/`, preserving primary-worktree changes. Assign one parent integration owner and one child owner; worktrees may coexist, but write-capable owners are sequential and dependent children wait.

Parent accepts only child squash merges and approved `origin/main` synchronization merges; fixes use child Issue/branch/worktree. Never rebase/force-update published branches. After fresh fetch/approval, merge `origin/main` into parent, parent into children, and resolve child conflicts there. Child PRs squash to parent; child commits follow commit policy and squash commit coherently identifies Issue/PR. Final parent PR merge-commits to `main`, preserving child squashes. Standalone leaf branches from approved `main` in a sibling worktree and PRs to `main`.

## Create the Worktrees

```bash
git fetch origin
git worktree list
git status --short
git log --left-right --count origin/main...main
```

Record approved base if `main` differs. After gate passage, resolve IDs/base OID and create the remote branch through the Issue's native Development relationship:

```bash
repo=Kims-DeveloperGroup/story-writing-agents
number=<issue-number>; branch=issue-<number>-<slug>; base=<approved-base-oid>
repo_id="$(gh repo view "$repo" --json id --jq .id)"
issue_id="$(gh issue view "$number" --repo "$repo" --json id --jq .id)"
gh api graphql -f query='mutation($r:ID!,$i:ID!,$o:GitObjectID!,$n:String!){createLinkedBranch(input:{repositoryId:$r,issueId:$i,oid:$o,name:$n}){linkedBranch{ref{name target{oid}}}}}' -F r="$repo_id" -F i="$issue_id" -F o="$base" -f n="$branch"
git fetch origin "$branch"
git worktree add --track -b "$branch" <exact-worktree-path> "origin/$branch"
```

Stop if an existing branch is unlinked; never recreate/rename/replace it. Repair legacy traceability only via authorized linked PR. If local branch exists, omit `--track -b`; one worktree per branch. Create children only from clean, current, GitHub-present parent commits. Before writing, verify the Development link; after PR linkage verify Project `Linked pull requests`:

```bash
gh api graphql -f query='query($o:String!,$r:String!,$n:Int!){repository(owner:$o,name:$r){issue(number:$n){linkedBranches(first:20){nodes{ref{name}}}}}}' -F o=Kims-DeveloperGroup -F r=story-writing-agents -F n=<issue-number>
gh project item-list 1 --owner Kims-DeveloperGroup --limit 100 --format json --jq '.items[] | select(.content.number == <issue-number>) | .["linked pull requests"]'
```

## Preflight Every Writer

First action, before other task reads/writes:

```bash
pwd -P
git rev-parse --show-toplevel
git branch --show-current
git rev-parse HEAD
git merge-base HEAD <approved-base-sha>
git merge-base --is-ancestor <approved-base-sha> HEAD
git status --porcelain=v1 -z --untracked-files=all | od -An -tx1
git status --porcelain=v1 -z --untracked-files=all | shasum -a 256
```

Report/match observed capability/approval path, every identity, exact porcelain bytes/hash/decoded paths, and exclusive targets. Dirty state is accepted only when all three were pinned. Any mismatch stops without reset/checkout/clean/revert. After upstream change/interruption/identity invalidation, root updates identity/hash and same writer repeats full preflight. Record exactly one `worktree_disposition` under `identity`.

## Route Work on the Critical Path

Apply [Delegation-Gain Gate](AGENTS.md#delegation-gain-gate), [Root-Capability Routing](AGENTS.md#root-capability-routing), [Role, Mode, and Control Gate](AGENTS.md#role-mode-and-control-gate), and [Orchestration Sequence](AGENTS.md#orchestration-sequence) exactly. Record estimates/formula/percentage and exception; root pair/context count and class; risk/role targets; independently desired/capped/selected axes, compatibility/ceiling/fallback; result IDs and roles/modes. Use bounded packets. Enforce exclusive docs/tests/modularity ownership and one production writer; cross the join barrier only with all valid results. Retry eligible control failure once; inspect/resume possible partial writers; never bypass ownership or conflict escalation.

## Dispose or Retain Worktrees

Inventory exact path, branch, HEAD, porcelain bytes/hash/paths, Issue/PR state, recoverability, cleanup authority, actual outcome/reason/next owner. Retain unless disposition/authority/removal gates allow removal. Authorized removal requires clean status, completed integration verification, recoverability, then:

```bash
git worktree remove <exact-worktree-path>
git worktree list --porcelain
test ! -e <exact-worktree-path>
```

Never force. Branch deletion is separately authorized. Retain parent until `main`; change disposition only after recorded authority/lifecycle change.

## Plan and Record Verification

Apply [Adaptive Verification](AGENTS.md#adaptive-verification) exactly. Before launch root records class/rationale, criteria→mandatory evidence, changed/affected inventory and exclusions/risks, exact authorized checks/IDs/rationale, one owner/item, estimates/source/confidence, total/max/spent/remaining budget, stops/escalations/full-suite triggers, reuse/invalidation, residual risk, and exact deduplication key. Never broaden or exceed budget without root/maintainer authorization.

Ledger each selection/shard's level, purpose, IDs/command, provenance, manifest/input digest, duration/result, completed/failed/skipped/unexecuted IDs, and budget. Deterministically shard above maximum; run sequentially unless authorized isolation/nonoverlap permits at most two fast-fail shards. Interruptions are not passes/failures; preserve state and resume valid remaining IDs. Reuse only root-proven disjoint, digest/purpose-stable evidence. Run `full_suite` only for recorded objective trigger. Complete only with all current mandatory green/accepted-skip evidence, resolved failures, sufficient inventory, and root-accepted residual risk; otherwise return remaining evidence, estimate, risk, and choices.

## Task Lifecycle

1. Pass/record planning gate, graph/metadata/Iteration/dependencies/base/authorization/disposition; never write while pending/blocked.
2. Prepare Development-linked parent branch/worktree/base/owner/status. After first child, authorized draft parent PR to `main` uses `Refs #<parent>`.
3. Start one `Ready`, root-Iteration, unblocked child from current parent; link branch/worktree, assign owner, set `In progress`, preflight.
4. Implement only child scope under commit/ownership policies; execute authorized evidence; inspect diff/status.
5. Merge parent into child without rebase; rerun affected checks; draft child PR to parent with `Refs #<child>`. Set ready/`In review` only with complete evidence and Development/Project PR links.
6. Authorized squash requires current parent tip. Record child/squash SHAs; fetch and `--ff-only` clean parent; verify diff/focused integration. Success: manually close child and verify `Done`. Failure: block siblings, keep open/`In progress`, use authorized revert PR/follow-up child; if already closed, reopen to `Backlog` and replan.
7. When children are `Done`, fresh-fetch and merge exact approved `origin/main` into parent; resolve there; run parent acceptance. Ready PR, change `Refs` to `Closes #<parent>`, set `In review`, then authorized merge commit to `main`; verify merge SHA, closure, `Done`, milestone.
8. Clean only under disposition, authorization, clean/integrated/recoverable evidence; branch deletion separate; retain parent until `main`.

Use exact [packet schemas](AGENTS.md#handoff-and-result-contracts). Nest planning/GitHub identity and writer disposition under defined `gates`/`identity`; verification, cleanup, recoverability, final inventory under `evidence`; disposition outcome/reason/risk/owner under `findings`/`risks`/`next_action`. Link stable evidence; omit logs, full Issues, and full plans.
