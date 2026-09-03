# Codex Project Instructions

## Project Role

Before beginning work, read `README.md` first. Treat it as the source of truth
for the project objective, language, contribution guidelines, and folder
structure.

This repository contains a Python multi-agent runtime that supports writing
novels. Your role in this repository is to develop, maintain, test, and
document the runtime. Do not write novel content unless the task explicitly
requires it for an example, fixture, or test.

## Working Method

For every change:

1. Inspect the repository and the relevant existing behavior.
2. Clarify the requirement, affected components, constraints, and acceptance
   criteria, then classify the GitHub and modularity gates.
3. Pass the GitHub planning gate when required.
4. Make the smallest complete change that satisfies the requirement.
5. Run relevant tests or checks and review the final diff.

For requests to explain, review, diagnose, or plan, do not make changes unless
the request also authorizes implementation. For requests to build, change, or
fix, make the requested in-scope local changes and verify them.

Preserve unrelated user changes. Avoid speculative abstractions, unnecessary
dependencies, and changes to behavior outside the requested scope. Ask when a
missing requirement would materially change the design or result.

## GitHub Planning Gate

Before a task branch, worktree, write-capable agent, or tracked-file edit,
record `github_planning_gate = not_required | pending | passed | blocked` with
the classification reason. Read-only repository and GitHub inspection,
requirements, diagnosis, test planning, and modularity analysis may run while
the gate is pending.

Classify work as large when any of these applies: estimated Size `L` or `XL`;
two or more independently complete implementation slices or parent integration
criteria; at least three production modules or two responsibility domains;
modularity or migration coordination; dependency sequencing; multiple owners,
worktrees, or delivery phases; or a parent integration branch. Companion tests
and documentation do not count toward the thresholds. Treat uncertainty as
large unless a maintainer records a smaller classification.

Large work must pass the initial planning gate in
[GITHUB_WORKFLOW.md](GITHUB_WORKFLOW.md). Do not dispatch a writer until the
root Issue, all known descendants, native relationships, canonical Project
items, and required metadata have been created or reused and verified. If a
missing gate or new scope is discovered after writing starts, stop further
writes, preserve and report the existing diff, and initialize that scope before
continuing.

## Subagent Delegation

The agents in `.codex/agents/` develop this repository. They are distinct from
the four story-runtime agents under `config/agents/` and must not participate
in novel-writing debates.

Use the project agents when their focused expertise improves the result:

- `requirements_analyst`: inspect the repository, clarify requirements, and
  define acceptance criteria before substantial or ambiguous work; report
  whether the modularity gate is required.
- `regression_diagnostician`: reproduce failures, trace regressions, and report
  whether the cause crosses a package or module boundary.
- `modularity_maintainer`: analyze, implement, or review cohesive package and
  module boundaries under the applicable language policy, and keep the
  project module indices synchronized with implementation.
- `runtime_implementer`: implement approved production-code or configuration
  changes within an accepted boundary design.
- `test_engineer`: design, add, update, and run tests for the change.
- `documentation_maintainer`: update README files, policies, guides, and
  examples after behavior and boundaries stabilize.

### Modularity Gate

Call `modularity_maintainer` when a change does any of the following:

- adds, removes, renames, moves, splits, or merges an importable package or
  module;
- redistributes feature responsibility, state, resource, or lifecycle
  ownership;
- changes dependency direction, introduces or resolves a cycle, or replaces
  private cross-boundary access;
- adds or materially changes a shared abstraction, adapter, facade, protocol,
  package export, or public import path;
- extracts code for multiple consumers or requires a structural compatibility
  or migration decision; or
- fixes a regression whose diagnosed cause is architectural coupling or a
  boundary-policy violation.

Do not call it for a localized change inside an established module
responsibility; an internal function refactor with unchanged imports, exports,
ownership, and contracts; tests-only or docs-only work; formatting, comments,
or typing cleanup; ordinary configuration or data edits; dependency metadata
alone; or story-runtime execution.

When the gate applies, call the custom agent by the name
`modularity_maintainer` and set `mode` in the handoff:

- `analysis`: read-only boundary design before any writer starts;
- `implementation`: exclusive ownership of an approved architecture-dominant
  structural change; or
- `review`: read-only compliance review after another agent implements a
  boundary-affecting change.

If a writer discovers an unapproved gate trigger, stop that writer and run
modularity analysis before continuing. Do not allow an implementation agent to
make an incidental architecture decision silently.

### Orchestration Sequence

1. The main agent classifies the request and records preliminary GitHub-planning
   and modularity-gate decisions.
2. For substantial or ambiguous work, `requirements_analyst` runs read-only.
   For reported or observed failures, `regression_diagnostician` runs
   read-only. Independent investigations may run in parallel. The main agent
   must resolve `modularity_gate = uncertain` to `required` or `not_required`
   before any writer starts.
3. When the gate applies, `modularity_maintainer` runs in `analysis` mode after
   it receives relevant requirements and diagnosis results. It selects and
   verifies the relevant root-level `<LANGUAGE>_MODULE_INDEX.md` files.
   The main agent resolves user decisions and accepts the resulting boundary
   brief, including its index delta, before writing begins.
4. After requirements and any boundary brief are accepted, the main agent
   passes the GitHub planning gate or records `not_required`. No writer starts
   while the gate is `pending` or `blocked`. For tracked implementation, create
   and verify the Issue's native Development-linked task branch before handing
   its worktree to a writer.
5. Exactly one agent owns production writes at a time. Use
   `modularity_maintainer` for architecture-dominant splits, moves, merges, and
   extractions. Use `runtime_implementer` for behavior-dominant work constrained
   by an accepted boundary brief.
6. If both production specialists are necessary, assign explicit, nonoverlapping
   files or symbols and run them sequentially. Prefer one owner when separate
   passes would create an invalid intermediate state.
7. `modularity_maintainer` owns the semantic accuracy of module indices. When
   it owns the structural implementation, it updates affected indices after the
   code is stable. When `runtime_implementer` owns production code, run a
   sequential `modularity_maintainer` implementation pass that owns only the
   affected indices and reconciles them to the completed production diff.
8. When `runtime_implementer` implements a boundary brief,
   `modularity_maintainer` then reviews the completed production diff and
   reconciled indices in `review` mode before test or documentation edits.
   Return production deviations to the assigned production owner and index
   drift to `modularity_maintainer`, then repeat the affected implementation,
   index-reconciliation, and review passes until material violations are
   resolved or an exception is explicitly accepted. A separate self-review is
   not required when `modularity_maintainer` performed the implementation; its
   implementation report and the main agent's review provide the gate.
9. `test_engineer` performs test planning read-only when useful, then owns test
   edits sequentially after production stabilizes. `documentation_maintainer`
   follows after behavior, APIs, and boundaries are stable.
10. If test or documentation work changes an indexed verification or
   documentation reference, or reveals any other drift, run a final sequential
   `modularity_maintainer` index-only implementation pass followed by read-only
   review.
11. The main agent integrates all results, verifies module-index consistency,
   reviews the complete diff, runs final checks, and owns the user-facing
   summary.

Reuse the same specialist thread for follow-up phases when practical. The main
agent remains the hub: it passes concise upstream decisions to the next agent,
and specialists do not delegate to or coordinate directly with peers.

### Adaptive Verification

The main agent owns verification selection, authorization, budgeting, evidence
reuse, escalation, and the final sufficiency decision. Specialists may propose
narrower or additional evidence, but they must not independently broaden an
authorized evidence level, selection, concurrency, or budget. Unknown-cost or
over-budget work returns to the main agent for `split`, `narrow`, `defer`,
`stop`, or an explicit maintainer-approved budget increase.

Verification is a partial order of selectable evidence, not a mandatory
ladder. Select the smallest complete set justified by the accepted criteria and
risks:

1. `static_inventory`: map changed inputs, directly associated tests,
   transitive production consumers, shared helpers and fixtures, invariants,
   risks, and exclusions. Require it for every nontrivial tracked change.
2. `exact`: check new or changed acceptance behavior and every known failing
   test ID.
3. `semantic_cluster`: check tests that share the changed invariant,
   collaborator contract, fixture, or failure family.
4. `affected_suite`: check a bounded subsystem or accepted blast radius only
   when narrower evidence cannot cover it.
5. `full_suite`: collect exceptional repository-wide evidence only when an
   objective trigger below applies.

`complete` is a terminal state, not a sixth required evidence step. It may
follow any selected level when every item of mandatory evidence is current and
green or an accepted skip, failures are resolved, the inventory is sufficient,
and the main agent explicitly accepts the recorded residual risk. Passing one
level neither requires nor authorizes the next. Mark an acceptance checklist
item complete only after its exact mandatory evidence is recorded and current.

Classify each task before executing verification and apply these default
cumulative command budgets and maximum shard sizes:

| `verification_class` | `total_stage_budget_seconds` | `max_shard_seconds` | Normal evidence floor |
| --- | ---: | ---: | --- |
| `local` | 300 | 180 | inventory plus exact or static evidence |
| `bounded` | 900 | 300 | inventory, exact, and required semantic evidence |
| `cross_cutting` | 1800 | 600 | inventory plus selected cluster or affected evidence |
| `release` | Issue-defined | Issue-defined | named integration or release gates |

An accepted task criterion may override a default. These values authorize
launch and continuation; they are not correctness waivers or unsafe process
kill deadlines. Do not start the next shard when its estimate exceeds the
remaining budget. If an already-running command exceeds its estimate, allow a
safe completion or use a runner-supported interrupt that preserves completed
evidence. Budget exhaustion never makes incomplete mandatory evidence
optional.

Before authorizing a verification command, record a plan containing:

- `verification_class` and its risk rationale;
- each acceptance criterion mapped to `mandatory_evidence`;
- `changed_inputs` covering source, tests, fixtures, configuration, policy,
  documentation, and environment inputs;
- directly associated tests, transitive production consumers, shared helpers,
  fixtures, invariants, cross-boundary consumers, and justified exclusions;
- risk flags for public APIs, packaging, persistence or migration,
  authorization or security, concurrency, process or PTY behavior, shared
  configuration, and uncertain dynamic coupling;
- `authorized_selection` containing the exact test IDs or static checks and
  their selection rationale;
- `estimated_seconds`, `estimate_source`, and `estimate_confidence`;
- `total_stage_budget_seconds`, `max_shard_seconds`, `spent_seconds`, and
  `remaining_seconds`;
- escalation and full-suite triggers, stop conditions, reusable evidence and
  its invalidation analysis, and residual risk.

Incomplete dependency knowledge is uncertainty, not evidence that no test is
affected. Broaden the inventory or stop for diagnosis before authorizing work
whose affected set or cost cannot be bounded.

For provenance, every result records repository, absolute worktree, baseline
and HEAD SHAs, dirty-state fingerprint, relevant environment capabilities,
purpose, evidence level, exact selection and command, outcome, actual duration,
and any stable failure fingerprint. Whole-repository identity does not by
itself decide reuse. Each selection also has a conservative
`relevant_input_digest` covering its selected tests, shared fixtures and
helpers, runner configuration, transitive production dependency closure, and
relevant environment capabilities. Reuse evidence after a HEAD change only
when the main agent proves the later change is disjoint and the digest and
purpose remain unchanged. Any relevant or uncertain input or dependency change
invalidates it; renaming the purpose does not create distinct evidence.

When a selection is estimated above `max_shard_seconds`, partition it into
deterministic, disjoint shards of exact portable test IDs. For Python
`unittest`, use module, class, or method IDs. The shard union must equal the
authorized selection and each ID must occur exactly once. Run risk-bearing and
short-feedback shards first. Sequential execution is the portable default;
parallel execution requires separate main-agent authorization, isolated
resources, nonoverlapping IDs, and runner support.

Record for every shard its manifest digest, ordinal, exact IDs,
`relevant_input_digest`, result, duration, `completed_ids`, `failed_ids`,
`accepted_skips`, and `unexecuted_ids`. Aggregate success requires valid green
or accepted-skip evidence for every authorized ID. After a repair, rerun exact
failures and only invalidated shards. A timeout, user replacement, or
interruption is neither a pass nor an assertion failure: stop only the owned
process, classify the termination, preserve `completed_ids`, `failed_ids`,
`accepted_skips`, and `unexecuted_ids`, and resume only unexecuted or invalidated
IDs whose inputs remain valid. Partial evidence never establishes `complete`.
A bounded, recorded repeat may diagnose a suspected flaky failure; changing a
label or purpose cannot bypass reuse and deduplication rules.

Authorize `full_suite` only when at least one objective trigger is recorded:

- an Issue, parent integration criterion, or release gate explicitly requires
  it;
- static inventory cannot conservatively bound the affected set;
- a package-wide, public, or cross-domain invariant changed;
- global discovery, bootstrap, packaging, or test-harness behavior makes
  narrower evidence unreliable;
- narrower verification reveals an unknown out-of-cluster failure family; or
- a concrete residual risk cannot be covered by affected selections.

Tracked implementation, final review, or the existence of `full_suite` is not
a trigger. If required broad evidence exceeds the accepted budget, return the
decision to the main agent; do not silently omit it or claim completion.
Prompt, TOML, policy, or documentation-only work may complete with static
parsing, exact policy tests, link or formatting checks, and diff inspection
when no runtime-relevant change or full-suite trigger exists.

Handle failures without restarting broad verification:

- For a known failure family, repair it and rerun the exact invalidated
  evidence.
- When the same stable fingerprint appears in two or more distinct
  responsibility holders, stop broad reruns and perform one repository-wide
  occurrence inventory with explicit roots, exclusions, patterns,
  classifications, and limitations.
- For a cross-boundary ownership or capability defect, apply the existing
  modularity gate once across all holders and consumers.
- For an unknown failure, run `regression_diagnostician` instead of launching
  broad validation.
- When cost is unknown or budget is exhausted, return completed and mandatory
  remaining evidence, revised estimates, residual risk, and root decision
  alternatives.

The six existing project roles remain sufficient. Verification evidence and
behavioral coverage review belong to `test_engineer`; failure diagnosis belongs
to `regression_diagnostician`; boundary review belongs to
`modularity_maintainer`; the main agent retains final risk acceptance. Do not
create another reviewer role.

Modes and read-only requirements declared in prompts or TOML are behavioral
policy; they do not prove capability-level sandbox enforcement. Static policy
tests must not claim otherwise. The main agent must treat actual effective
sandbox and approval capabilities as separate runtime evidence.

### Handoff and Result Contracts

Every subagent handoff must include:

- `mode`: `analysis`, `implementation`, or `review`;
- objective and relevant evidence;
- `github_planning_gate`, its rationale, and verified Issue, Project, and
  Iteration evidence when applicable;
- the native Development-linked branch or PR when implementation writes are
  authorized;
- approved scope and explicit out-of-scope work;
- acceptance criteria and compatibility constraints;
- the `modularity_gate` decision and accepted brief, when applicable;
- applicable module-index paths, status, and accepted index delta;
- read-only targets or exclusively owned files and symbols;
- upstream findings and accepted decisions;
- required checks and output; and
- stop conditions, including missing or conflicting policies, breaking API
  decisions, scope deviation, and unexpected structural work.

When a handoff authorizes verification, it must also carry the applicable
verification plan and current compact evidence ledger. A specialist result
must distinguish `mandatory_evidence`, `completed_ids`, `failed_ids`,
`accepted_skips`, invalidated evidence, and `unexecuted_ids`; report exact
commands and `authorized_selection`, actual duration, spent and remaining
budget, identities and digests, failure fingerprints, residual risk, and
recommended next evidence; and return any proposed selection, concurrency,
evidence-level, or budget expansion for main-agent authorization.

Every result must report findings, files inspected or changed, module indices
consulted or updated, implementation/index consistency, checks run, blockers,
and the recommended next action. Pass distilled decisions and evidence between
agents rather than raw logs.

If a required language policy is missing, the modularity agent returns the
exact language and expected path. The main agent asks the user whether to
create the packaged policy, then resumes the same agent thread. If creation is
declined, proceeding under general engineering judgment requires that user
decision and the lack of a governing policy to be recorded in the result.

A missing module index does not block read-only analysis or require separate
user permission. The modularity agent derives the complete current inventory,
records the root index path and contents in the brief, and the main agent then
runs the same specialist in an index-only `implementation` pass to create it.
Structural work is not complete while an affected module index is missing or
stale.

## Quality and Commits

Add or update tests when behavior changes. Update documentation when public
behavior, setup, or workflow changes. Before finishing, check the diff for
unintended changes and report what was verified.

For work tracked on GitHub, read and follow
[GITHUB_WORKFLOW.md](GITHUB_WORKFLOW.md). It defines Issue readiness, Project
status, milestones, iterations, labels, branch and pull-request traceability,
and completion rules. Execute tracked tasks with
[AGENT_TASK_RUNBOOK.md](AGENT_TASK_RUNBOOK.md), including its parent/child
branch, squash-merge, and worktree procedure.

The commit policy is a repository workflow document at
[COMMIT_POLICY.md](COMMIT_POLICY.md). A commit is a unit of a complete function
or logic; keep commits focused, complete, and reviewable.

Before adding, moving, splitting, merging, or materially changing an internal
package or module, use `modularity_maintainer` and follow the language-policy
selection process in
[its agent definition](.codex/agents/modularity_maintainer.toml). Packaged
language defaults live under `.codex/agents/modularity_maintainer/`.

Before adding, updating, or removing a third-party Python dependency, or
changing project packaging metadata, read and follow
[DEPENDENCY_POLICY.md](DEPENDENCY_POLICY.md). Dependency and packaging changes
must comply with that policy and include the required verification.
