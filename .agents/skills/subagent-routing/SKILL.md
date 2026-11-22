---
name: subagent-routing
description: >-
  Route and validate scoped development subagents for clear parallel gain or specialized
  runtime/test/design work; excludes story-runtime agents and root-only GitHub workflow.
---

Root handles requirements, workflow/general documentation, GitHub orchestration,
integration, and small changes. Delegate only when work can proceed independently with clear time/budget gain or
needs the specialized owner below. Do not delegate deterministic commands, create a generic
reviewer role, or split one file across owners.

| Role | Use for | Never use for |
| --- | --- | --- |
| `runtime_implementer` | Production/configuration implementation; modularity decisions and review; module-index updates. | Tests or root-only GitHub operations. |
| `test_engineer` | Runtime-test design/edit/run/coverage; regression reproduction and diagnosis. | Production edits or documentation ownership. |
| `designer` | Assigned Figma/UI/design-system work and design documents; read-only design analysis/review. | Production/configuration/test edits, workflow/general docs, GitHub lifecycle operations. |

Use `designer` only after Root identifies design work under the
[Figma workflow](../../../docs/design/figma-workflow.md). General development and
output-preserving refactors skip that role and Figma discovery. Its scoped design review
is part of existing verification, not a separate GitHub reviewer or lifecycle owner.

Prefer one subagent for a cohesive slice. Parallelize runtime and test analysis only when they
do not write the same inputs. Reuse the same role thread while its context and identity remain
valid.

Before work, the child confirms role, mode, repository, worktree, branch, base/HEAD ancestry,
exclusive targets, and disposition. One owner edits each file. Never reset, checkout, clean,
revert, or overwrite another owner's changes. Pause on scope expansion, ownership conflict,
unexpected boundary impact, or identity mismatch.

Use a concise packet:

| `task_packet` | Required content |
| --- | --- |
| `mode` | `analysis`, `implementation`, or `review`. |
| `objective` | Concrete outcome. |
| `scope` | Included and excluded targets. |
| `acceptance` | Observable criteria and compatibility constraints. |
| `ownership` | Read-only or exclusive files/responsibilities. |
| `gates` | Planning, modularity, and accepted decisions. |
| `identity` | Repo, worktree, branch, base/HEAD, and `remove_after_integration|retain_with_reason|authorization_required`. |
| `verification` | Authorized static/runtime checks, evidence shape, and budget. |
| `design_context` | Required only for designer: target IDs/ownership, contracts/source, comparison baseline, allowed operations, Figma session, and evidence under the Figma workflow. |
| `upstream_decisions` | Stable facts the child may rely on. |
| `stop_conditions` | Conflicts or decisions that return control to root. |

Require a concise result:

| `result_packet` | Required content |
| --- | --- |
| `outcome` | Completed, stopped, or blocked state. |
| `findings` | Decisions, changed files, and boundary/index consistency. |
| `changed_inputs` | Relevant source, tests, fixtures, config, policy, docs, or environment. |
| `evidence` | Exact checks, IDs/outcomes when runtime tests ran, duration, identity, and cleanup/recoverability. |
| `risks` | Residual ambiguity, blocker, or proposed expansion. |
| `next_action` | Owner and concrete next step. |

Root validates the packet and diff before integration. Retry only a transient delivery failure
once; never retry a policy, capability, identity, schema, or scope conflict. Preserve possible
writes and resume the same thread after a safe interruption.

## Test impact handoff

For the [#969 pilot](../../../GITHUB_WORKFLOW.md#test-impact-pilot-for-969), the
root uses the existing packets for planning and implementation review:

- Before implementation, put the accepted base and planned behavior, ownership and
  fixture changes in `identity` and `upstream_decisions`. Ask `test_engineer` to
  record the exact existing IDs to update or retain and run, the evidence for each
  disposition, the first normal selection, and any unresolved coverage in
  `findings` and `risks`.
- For the first runnable candidate, give `test_engineer` the diff from the accepted
  base. For later checks, batch changes since the last reviewed state. In `identity`,
  include HEAD and a reproducible reference to any in-scope uncommitted content.
  Capture the staged and unstaged diff and the contents of in-scope untracked files.
  In `upstream_decisions`, identify the previous review whose findings can be reused.
  Report the actual reviewed state and relevant changes in `changed_inputs`.
- Ask `test_engineer` to compare the actual changes with the planned coverage,
  including small unplanned edits. In `findings`, record added or revised test
  dispositions and exact IDs for the next run. Return unresolved scope or coverage in
  `risks`. The root routes required edits to the assigned file owners and confirms
  the resulting candidate and test selection. Reuse earlier findings for inputs whose
  identity is still the same. If relevant inputs change during review or while applying
  fixes, review that delta before the next related normal run.
