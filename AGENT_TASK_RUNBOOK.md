# Agent Task Runbook

For GitHub-tracked implementation, [GITHUB_WORKFLOW.md](GITHUB_WORKFLOW.md), [AGENTS.md](AGENTS.md), and [COMMIT_POLICY.md](COMMIT_POLICY.md) are authoritative. An implementation request authorizes only policy-defined initial planning and native Development-linked branch creation. Every other GitHub mutation/push/PR/merge/Issue-closure/stage/commit/branch-deletion requires explicit authorization.

## Initialize GitHub Planning

Before branches/worktrees/writers/edits: read `README.md`/policies; classify the planning gate; search all Issue states for matching scope; preflight repository, `gh` project-scoped auth, canonical Project/fields/milestones/Iterations/dependencies/worktrees.

For large work, after read-only requirements/modularity analysis, draft complete root/known-descendant scopes. Create root then descendants; add every URL to Project #1; create native sub-Issue and `blocked by` relationships. Mutate/verify singly; stop at first failure. Resolve repository/Issue/Project/item/field/option/Iteration IDs at runtime. Set one logical Iteration across the tree and all policy metadata; Priority uses the organization Issue field. No unused Iteration requires explicit Project-schema authorization.

Re-read bodies, hierarchy/dependencies, membership/fields, milestone, root-wide Iteration; record stable URLs/IDs. Pass only when complete and an unblocked leaf is `Ready`; otherwise block, preserve successes, repair only verified gaps. Standalone tasks omit parent; later children repeat the gate. Use exact `gh issue create`, `gh project item-add|item-edit`, and REST sub-Issue procedures in [GITHUB_WORKFLOW.md](GITHUB_WORKFLOW.md).

## Branch and Worktree Model

Parent branch/worktree starts at recorded current `origin/main`, PRs to `main` with merge commit; child branches/worktrees start at current parent and PR to parent with squash. Standalone leaf starts at approved `main` and PRs there. Name `issue-<number>-<slug>`; locate under sibling `../<repository>-worktrees/issue-<parent>-<slug>/`; preserve primary changes.

Assign parent integration owner and each child owner; only one write-capable owner runs at once; dependencies wait. Parent accepts only child squashes and approved `origin/main` synchronization; fixes use children. Never rebase/force-update published branches. Fresh-fetch before approved merges; resolve child conflicts in child. Child commits follow commit policy; squash identifies child Issue/PR coherently; final parent merge preserves child squashes.

## Create the Worktrees

Inspect without branch changes:

```bash
git fetch origin
git worktree list
git status --short
git log --left-right --count origin/main...main
```

Record approved base when local `main` differs. After gate passage, use the Issue's GraphQL `createLinkedBranch` Development mutation at the approved OID, then:

```bash
git fetch origin issue-<number>-<slug>
git worktree add --track -b issue-<number>-<slug> <exact-path> origin/issue-<number>-<slug>
```

Existing unlinked branch stops; never recreate/rename/replace it. Legacy repair needs authorized linked PR. For an existing local branch omit `--track -b`; one worktree per branch. Child base must be a clean/current/GitHub-present parent commit. Verify GraphQL `linkedBranches` before writing and Project `Linked pull requests` after PR linkage.

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

Report/match capability/approval path, identity, exclusive targets, exact porcelain bytes/hash/decoded paths. Dirty state requires all three pinned. Mismatch stops without reset/checkout/clean/revert. After upstream change/interruption/identity invalidation, root updates identity/hash and same writer repeats. Record one `worktree_disposition` under `identity`.

## Route Work on the Critical Path

Apply [Delegation-Gain Gate](AGENTS.md#delegation-gain-gate), [Root-Capability Routing](AGENTS.md#root-capability-routing), [Role, Mode, and Control Gate](AGENTS.md#role-mode-and-control-gate), and [Orchestration Sequence](AGENTS.md#orchestration-sequence). Record estimates/formula/percentage/exception; root/context/risk/role targets; independently desired/capped/selected axes; compatibility/ceiling/fallback; result IDs+role/mode. Use bounded packets, exclusive docs/tests/modularity owners, one production writer, validated join barrier, authorized retry/resume/conflict escalation.

## Dispose or Retain Worktrees

Inventory path/branch/HEAD/porcelain/Issue-PR/recoverability/cleanup-authority/outcome/reason/next-owner. Retain unless disposition, authorization, clean state, completed integration, and recoverability permit non-forced removal:

```bash
git worktree remove <exact-path>
git worktree list --porcelain
test ! -e <exact-path>
```

Verify registry/filesystem absence. Branch deletion is separately authorized; retain parent until `main`; change disposition only after recorded authority/lifecycle change.

## Plan and Record Verification

Apply [Adaptive Verification](AGENTS.md#adaptive-verification). Root records class/rationale; criteria→mandatory evidence; changed/affected inventory/exclusions/risks; exact selection/rationale/one owner; estimates/confidence/budget; stops/triggers/reuse/risk; exact dedup key. No specialist broadens selection/level/concurrency/budget.

Ledger level/purpose/IDs/command/provenance/manifest/input-digest/duration/result/completed/failed/skipped/unexecuted/budget. Shard deterministically above maximum; run sequentially unless root authorizes isolated nonoverlap (maximum two fast-fail). Interruptions preserve state, never pass/fail; resume only valid remainder. Reuse only root-proven disjoint, purpose/digest-stable evidence. `full_suite` needs objective trigger. Complete only with current mandatory green/accepted-skip evidence, resolved failures, sufficient inventory, and root-accepted risk; otherwise return remainder/estimate/risk/options.

## Task Lifecycle

1. Record passed/not-required gate, graph/metadata/Iteration/dependencies/base/authorization/disposition.
2. Prepare Development-linked parent branch/worktree/base/owner/status; after first child, authorized draft parent PR uses `Refs #<parent>`.
3. Start one `Ready`, root-Iteration, unblocked child from current parent; link, assign, set `In progress`, preflight.
4. Implement child scope under ownership/commit policy; run authorized evidence; inspect diff/status.
5. Merge parent→child without rebase; rerun affected checks; draft child PR to parent with `Refs #<child>`. Ready/`In review` requires complete evidence and Development/Project links.
6. Authorized squash requires current parent tip. Record child/squash SHAs; fetch/`--ff-only` clean parent; verify diff/integration. Success: close child, verify `Done`. Failure: block siblings; keep/reopen `In progress` (`Backlog` if closed); use authorized revert PR/follow-up child.
7. When children `Done`, fresh-fetch and merge approved `origin/main` into parent; resolve there; run acceptance. Ready PR, change `Refs`→`Closes`, set `In review`, then authorized merge-commit; verify SHA/closure/`Done`/milestone.
8. Clean only when authorized/disposed/clean/integrated/recoverable; branch deletion separate; retain parent until `main`.

Use exact [packet schemas](AGENTS.md#handoff-and-result-contracts). Task fields carry: mode enum; concrete objective/evidence; in/out scope; criteria/compatibility; read-only/exclusive ownership; planning/modularity evidence/brief/index delta; repository/GitHub/worktree/base/head identity; authorized verification ledger/budget/output; upstream decisions; conflict/breaking/scope/structure stops. Result fields carry: outcome; files/index consistency; changed inputs; mandatory/authorized/executed/failed/skipped/invalidated/unexecuted evidence with duration/budget/identities/digests/fingerprints/next checks; blockers/ambiguity/residual risk/expansion; next owner/action. Nest disposition under `identity`, cleanup/recoverability under `evidence`, and disposition outcome/reason/risk/owner under findings/risks/next-action. Link stable evidence; omit logs/full Issues/plans.
