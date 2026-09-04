# Codex Project Instructions

## Project Role

Read `README.md` first; it governs objective, language, contribution guidance, and
structure. Develop, maintain, test, and document this Python novel-writing runtime. Write
fiction only for requested examples, fixtures, or tests.

## Working Method

1. Inspect behavior and define requirements, inventory, constraints, scope, acceptance,
   and both gates.
2. Explanation, review, diagnosis, and planning authorize no change. Build, change, and
   fix authorize only their approved scope.
3. Make the smallest complete change, preserve unrelated work, verify, and
   inspect the diff.

Avoid speculative abstractions, unnecessary dependencies, and scope creep. Ask the user
when a missing requirement would materially change the design or result.

Skills supply focused procedures. They never override this file,
[GITHUB_WORKFLOW.md](GITHUB_WORKFLOW.md), [COMMIT_POLICY.md](COMMIT_POLICY.md), or
[DEPENDENCY_POLICY.md](DEPENDENCY_POLICY.md).

## GitHub Planning Gate

- Record `github_planning_gate=not_required|pending|passed|blocked` and its reason before
  branches, worktrees, writers, or tracked edits.
- Permit only read-only work while the gate is pending or blocked.
- Classify work as large for `L` or `XL`; two independent slices or parent criteria; three
  production modules; two domains; modularity/migration coordination; dependency
  sequencing; multiple owners/worktrees/phases; or parent integration.
- Exclude companion tests and documentation. Treat uncertainty as large.
- Follow [GITHUB_WORKFLOW.md](GITHUB_WORKFLOW.md). Stop writes when missing or new scope
  appears; preserve the diff and initialize that scope.
- Before a large-work writer, verify the root, every known descendant, native relationships,
  canonical Project items, and required metadata.
- Load `$github-task-lifecycle` for every GitHub-tracked task.

## Subagent Delegation

Exactly six `.codex/agents/` development roles exist. They never join the four
`config/agents/` story roles. Never create a reviewer role.

|Role|Ownership|
|---|---|
|`requirements_analyst`|Read-only requirements/acceptance and modularity-gate advice|
|`regression_diagnostician`|Read-only reproduction/cause and boundary impact|
|`modularity_maintainer`|Boundaries and exclusive index semantics|
|`runtime_implementer`|Approved production/configuration changes|
|`test_engineer`|Exclusive test design/edits/execution and behavioral/coverage review|
|`documentation_maintainer`|Exclusive documentation/skills after behavior/boundaries stabilize|

### Delegation-Gain Gate

Load `$subagent-routing` before evaluating or dispatching any subagent. It contains the
mandatory gain calculation, exceptions, fast path, and ownership rules.

### Root-Capability Routing

Load `$subagent-model-selection` before choosing, requesting, overriding, or inheriting a
child context, model, or reasoning effort.

### Role, Mode, and Control Gate

Load `$subagent-routing` before preflight, handoff, join, retry, interruption, or conflict
handling. Reject unsupported roles, modes, capabilities, identities, or ownership before
other task work.

Child first performs only minimum identity/capability preflight before other task reads or
writes. Writers match exact porcelain bytes, SHA-256, and decoded paths; accept dirty state
only when all three are pinned.

### Worktree Disposition

Record exactly one disposition under task `identity`: `remove_after_integration`,
`retain_with_reason`, or `authorization_required` when removal authority is absent or
unresolved. Load `$subagent-routing` for identity rules and `$github-task-lifecycle` for
tracked recoverability, removal, and branch-deletion safeguards.

### Modularity Gate

- Require `modularity_maintainer` for importable structure changes; redistributed feature,
  state, resource, or lifecycle ownership; dependency-direction or cycle changes; or
  private cross-boundary access.
- Also require it for material shared abstractions, adapters, facades, protocols, exports,
  public imports, multi-consumer extraction, structural migration, or architectural
  coupling defects.
- Exclude localized unchanged-contract refactors, tests, documentation, formatting,
  comments, typing, ordinary configuration or data, dependency metadata, and story runs.
- Use `analysis` before writers, exclusive `implementation` for architecture-dominant
  work, and `review` after another owner changes a boundary.
- Stop an unexpected trigger. Load `$subagent-routing` for missing-policy, index, mode,
  and reconciliation procedures.

### Orchestration Sequence

1. Root classifies scope, gates, inventory, delegation, and uncertain modularity.
2. Run required read-only analysis; justified independent investigations may parallelize.
3. Modularity analysis selects and verifies root `<LANGUAGE>_MODULE_INDEX.md`; accept its
   boundary brief and index delta.
4. Pass planning and verify the Development-linked branch before writers.
5. Use the root fast path or exactly one production owner at a time.
6. After runtime work, reconcile indices index-only before boundary review.
7. Run tests preferably in one turn; pause/resume that thread for dependency changes.
8. Run documentation after tests. Later test/documentation index drift requires index-only
   implementation, then read-only review.
9. Root validates, integrates, verifies, and summarizes.

Load `$subagent-routing` for ordering, review loops, ownership, and thread reuse.

### Adaptive Verification

Load `$adaptive-verification` before planning, executing, reusing, escalating, or accepting
evidence for any nontrivial tracked change. Root alone authorizes selection, budget,
concurrency, reuse, escalation, and final sufficiency.

### Handoff and Result Contracts

Load `$subagent-routing` before creating or validating any `task_packet` or
`result_packet`. Thread reuse never authorizes full-history inheritance.

Packet `identity` enumerates repository, Issue, Project, Iteration, Development branch/PR,
worktree, base, and HEAD. Preserve the skill's exact result evidence and nesting meanings.

Result findings include files inspected/changed, indices consulted/updated, and
implementation/index consistency. `changed_inputs` covers relevant source, tests, fixtures,
configuration, policy, documentation, and environment changes. Evidence includes mandatory
evidence, authorized selection, commands, completion sets, invalidation, duration/budget,
identities, digests, fingerprints, checks, and next evidence. Risks include blockers,
ambiguity, residual risk, and proposed expansion requiring root authorization.

Nest preflight/disposition under task `identity`, cleanup/recoverability under result
`evidence`, and outcomes, reasons, risks, and next owners in their result fields.

### Prospective Orchestration Telemetry

Load `$subagent-routing` before recording or comparing orchestration telemetry.

## Quality and Commits

Follow [COMMIT_POLICY.md](COMMIT_POLICY.md). Inspect and report the diff and verification.
Before structural changes, use the
[modularity policy selector](.codex/agents/modularity_maintainer.toml); packaged defaults
live in `.codex/agents/modularity_maintainer/`. Follow and verify
[DEPENDENCY_POLICY.md](DEPENDENCY_POLICY.md) for dependency or packaging changes.
