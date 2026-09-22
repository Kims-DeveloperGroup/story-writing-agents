---
name: subagent-routing
description: >-
  Route and validate the two development subagents for clear parallel gain or specialized
  runtime/test work; excludes story-runtime agents and root-only GitHub workflow.
---

Root handles requirements, documentation, GitHub orchestration, integration, and small
changes. Delegate only when work can proceed independently with clear time/budget gain or
needs the specialized owner below. Do not delegate deterministic commands, create a reviewer
role, or split one file across owners.

| Role | Use for | Never use for |
| --- | --- | --- |
| `runtime_implementer` | Production/configuration implementation; modularity decisions and review; module-index updates. | Tests or root-only GitHub operations. |
| `test_engineer` | Runtime-test design/edit/run/coverage; regression reproduction and diagnosis. | Production edits or documentation ownership. |

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
