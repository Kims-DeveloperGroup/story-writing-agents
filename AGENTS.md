# Codex Project Instructions

## Project Role

Read `README.md` first; it governs the objective, language, contributions, and structure.
Maintain this novel-writing runtime. Write fiction only for requested examples, fixtures,
or tests.

## Working Method

1. Inspect current behavior and define scope, constraints, acceptance criteria, and gates.
2. Explanation, review, diagnosis, and planning are read-only unless implementation is
   explicitly requested.
3. Make the smallest coherent change, preserve unrelated work, and review the final diff.

Ask only when a missing requirement would materially change the result. Skills provide
procedures and never override this file or repository policies.

## GitHub Planning Gate

Before creating branches/worktrees or editing, record
`github_planning_gate=not_required|pending|passed|blocked` and the reason. Pending or blocked
permits read-only work only. For tracked work load `$github-task-lifecycle` and follow
[GITHUB_WORKFLOW.md](GITHUB_WORKFLOW.md).

Large (`L|XL`) work spans multiple independent delivery slices, three production modules,
two domains, a migration or dependency sequence, multiple worktrees, or parent integration.
Before writing, verify its Issue graph, Project metadata, and executable `Ready` leaf. Keep
one delivery unit on one Issue; create children only as a complete set of at least two
independently useful, necessary siblings. New scope pauses writes until its planning state is
valid.

When the user explicitly invokes `$project-workflow`, the root must load and follow it for
the complete GitHub lifecycle. Subagents must never invoke or operate that skill.

## Development Roles

The two `.codex/agents/` development roles are separate from the four `config/agents/`
story-runtime roles. Do not add a development reviewer role.

| Role | Ownership |
| --- | --- |
| `runtime_implementer` | Production and configuration; modularity decisions, boundary review, and module-index updates. |
| `test_engineer` | Runtime-test planning, editing, execution, and coverage; regression reproduction and diagnosis. Never edits production code. |

The root owns requirements, documentation, integration, GitHub orchestration, and small
in-scope changes. Delegate only for a clear parallel gain or specialized runtime/test work.
Load `$subagent-routing` before delegation and `$subagent-model-selection` only when choosing
or overriding a child model or reasoning effort. Use concise packets, one owner per file,
and reuse a valid agent thread.

Record each delegated task's worktree disposition as
`remove_after_integration|retain_with_reason|authorization_required`. The root validates
returned scope, evidence, and diffs before integration.

## Modularity

The `runtime_implementer` owns modularity analysis and decisions when work changes importable
structure, responsibility or lifecycle ownership, dependency direction, cycles, public
facades/protocols/exports, shared abstractions, or multi-consumer migrations. Local refactors
with unchanged boundaries, tests, docs, formatting, ordinary configuration/data, and
dependency metadata do not trigger this gate.

Perform boundary analysis before structural writing when possible. Unexpected boundary
impact pauses the writer for a root decision. The runtime owner updates and reconciles the
language module index in the same slice; the root performs final integration review.

## Verification

Runtime tests are required only when runtime code changes. The `test_engineer` owns their
selection and execution. Use the canonical runner:

```bash
PYTHONPATH=src python -m tests.runner run
```

Preserve exact test IDs and machine-readable JSON evidence. Retain the runner's groups,
timing weights, shards, and reconciliation rules. Start with affected groups in parallel;
run broader coverage only when risk, failure, or the changed dependency surface requires it.
Do not serially rerun unchanged successful evidence.

Policy, documentation, templates, skills, development-agent configuration, module-index-only,
and test-only changes require static validation and diff review, not runtime tests. Validate
links, paths, headings/frontmatter, references, formatting, and relevant policy invariants.

## Code Review Rules

At the ready-for-review milestone, request Copilot and Codex review concurrently with affected
CI. Use Copilot Lite by default; use Balanced only for high-risk changes. Batch blocking
feedback into one change set. After material fixes, make one final milestone request; do not
request review after every push.

Only unresolved Copilot **High**, Codex **P0/P1**, required human approvals, and required
checks block merge. Record review and check evidence against the current head SHA. GitHub
Actions are event-driven and nonblocking to agent work; concurrency should cancel stale runs.
Mutating automation applies only to PRs labeled `workflow:managed`. Enable auto-merge or the
merge queue only after all gates pass.

## Quality and Commits

Follow [COMMIT_POLICY.md](COMMIT_POLICY.md). Follow and verify
[DEPENDENCY_POLICY.md](DEPENDENCY_POLICY.md) for dependency or packaging work. Preserve
Issue/PR/comment template headings and semantics when authoring or changing workflow records.
