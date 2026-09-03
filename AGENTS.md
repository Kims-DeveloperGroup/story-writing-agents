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
- `test_engineer`: exclusively design, add, update, and run tests for the
  change, including the final behavioral and coverage review.
- `documentation_maintainer`: exclusively create or update README files,
  policies, guides, and examples after behavior and boundaries stabilize.

### Delegation-Gain Gate

The main agent remains the critical-path hub and evaluates every candidate
specialist dispatch before starting it. Record the estimates and calculate:

`predicted_critical_path_savings_seconds = root_sequential_estimate - (parallel_critical_path + spawn_wait_handoff_synthesis_overhead)`

Delegate only when the predicted savings are at least 20 seconds **and** at
least 20 percent of `root_sequential_estimate`, unless the dispatch records a
named `material_correctness_risk_reduction` or `mandatory_ownership_rule`.
Mandatory ownership applies to documentation, tests, triggered modularity
work, and production work outside the root fast path. An exception authorizes
the specialist; it does not claim a time saving.

The main agent may directly implement only a clear, localized change limited
to one or two production-code or configuration files inside an established
module responsibility and boundary. This root fast path excludes documentation,
policies, guides, examples, tests, required or uncertain modularity work,
substantial or unresolved ambiguity, and unknown failures. Companion
documentation and tests retain their specialist owners even when production
uses the fast path.

Dispatch `requirements_analyst` only for substantial ambiguity and
`regression_diagnostician` only for an unknown failure. Dispatch
`modularity_maintainer` only when the existing Modularity Gate applies. Do not
create a subagent merely to run a deterministic command. The main agent should
batch safe, independent static reads through its tools when no specialist
judgment or mandatory ownership is required.

Use these dynamic model and reasoning-effort recommendations per dispatch:

| Agent | Default | Escalation |
| --- | --- | --- |
| `requirements_analyst` | `gpt-5.6-terra`, `medium` | `gpt-5.6-sol`, `high` for multi-domain, security, migration, or deep ambiguity |
| `regression_diagnostician` | `gpt-5.6-sol`, `high` | Already quality-oriented because dispatch is limited to unknown causal failures |
| `modularity_maintainer` | `gpt-5.6-sol`, `high` | `gpt-5.6-terra`, `medium` only for an accepted index-only reconciliation |
| `runtime_implementer` | `gpt-5.6-sol`, `medium` | `high` for security, concurrency, migration, public API, or difficult edge cases |
| `test_engineer` | `gpt-5.6-terra`, `medium` | `gpt-5.6-sol`, `high` for security, concurrency, PTY/process, lifecycle, or complex risk coverage |
| `documentation_maintainer` | `gpt-5.6-terra`, `medium` | `gpt-5.6-sol`, `high` for normative governance, security, compatibility, or migration policy |

These are recommendations, not account-specific TOML pins. For every dispatch,
the main agent records model availability, the selected model and effort, and
any compatible fallback with its reason; it never silently assumes a model is
available or lowers the required quality.

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
   and modularity-gate decisions, the affected inventory, and candidate
   delegation estimates. It batches safe static reads and decides whether each
   candidate passes the Delegation-Gain Gate or names an allowed exception.
2. For substantial ambiguity, `requirements_analyst` runs read-only. For an
   unknown failure, `regression_diagnostician` runs read-only. Independently
   justified investigations may run in parallel. The main agent must resolve
   `modularity_gate = uncertain` to `required` or `not_required` before any
   writer starts.
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
5. The main agent selects either the root fast path or one production specialist
   under the Delegation-Gain Gate. Exactly one owner writes production at a
   time. Use `modularity_maintainer` for architecture-dominant splits, moves,
   merges, and extractions. Use `runtime_implementer` for behavior-dominant work
   outside the fast path or constrained by an accepted boundary brief.
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
9. After production stabilizes, `test_engineer` exclusively owns test work. When
   safe, fuse test planning, test edits, exact execution, and final behavioral
   and coverage review into one `implementation` turn. If a production or
   documentation dependency requires a pause, reuse the same specialist thread
   after that dependency stabilizes. `documentation_maintainer` exclusively
   owns all documentation, policy, guide, and example writes.
10. If test or documentation work changes an indexed verification or
   documentation reference, or reveals any other drift, run a final sequential
   `modularity_maintainer` index-only implementation pass followed by read-only
   review.
11. The main agent integrates all results, verifies module-index consistency,
   reviews the complete diff, runs final checks, and owns the user-facing
   summary.

Reuse the same specialist thread for follow-up phases unless its context is
stale or reuse creates an isolation risk; record either exception. The main
agent remains the hub: it passes compact upstream decisions to the next agent,
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

Assign exactly one `evidence_execution_owner` to every evidence item before
launch. Deduplicate evidence on exactly these key fields: `normalized_command`,
`exact_ids`, `relevant_input_digest`, and `environment_class`. A different
owner, label, purpose, venue, or wrapper does not justify an equivalent rerun.
Normally run `exact` and `semantic_cluster` evidence locally and reserve
`affected_suite` and `full_suite` for CI. Do not run equivalent broad evidence
both locally and in CI unless the local environment is itself an acceptance
criterion; record the distinct environment identities and required evidence in
that case.

When a selection is estimated above `max_shard_seconds`, partition it into
deterministic, disjoint shards of exact portable test IDs. For Python
`unittest`, use module, class, or method IDs. The shard union must equal the
authorized selection and each ID must occur exactly once. Run risk-bearing and
short-feedback shards first. Sequential execution is the portable default;
parallel execution requires separate main-agent authorization, isolated
resources, nonoverlapping IDs, and runner support. At most two fast-fail shards
may run concurrently; all other shard execution remains sequential.

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

Use a bounded, self-contained fork or context by default. Every handoff sends a
compact `task_packet` with exactly these top-level fields:

- `mode`: `analysis`, `implementation`, or `review`;
- `objective`: the concrete outcome and relevant evidence;
- `scope`: approved in-scope and out-of-scope targets;
- `acceptance`: criteria and compatibility constraints;
- `ownership`: read-only targets or exclusively owned files and symbols;
- `gates`: GitHub-planning and modularity decisions, rationale, verified
  evidence, accepted boundary brief, and module-index status and delta;
- `identity`: repository, Issue, Project, Iteration, Development-linked branch
  or PR, absolute worktree, and base and head identities as applicable;
- `verification`: the authorized adaptive plan, compact evidence ledger,
  required checks, budget, and expected output;
- `upstream_decisions`: distilled findings and accepted decisions; and
- `stop_conditions`: policy conflicts, breaking decisions, scope deviation,
  unexpected structural work, and other explicit return conditions.

Every specialist returns a compact `result_packet` with exactly these
top-level fields:

- `outcome`: completed work or the precise blocked/stopped state;
- `findings`: distilled findings, files inspected or changed, module indices
  consulted or updated, and implementation/index consistency;
- `changed_inputs`: relevant source, test, fixture, configuration, policy,
  documentation, and environment changes;
- `evidence`: `mandatory_evidence`, `authorized_selection`, exact commands,
  `completed_ids`, `failed_ids`, `accepted_skips`, invalidated evidence,
  `unexecuted_ids`, actual duration, spent and remaining budget, identities,
  digests, failure fingerprints, checks, and recommended next evidence;
- `risks`: blockers, ambiguity, residual risk, and any proposed selection,
  concurrency, evidence-level, or budget expansion requiring main-agent
  authorization; and
- `next_action`: the recommended next owner and action.

Keep each packet limited to context necessary for the assigned phase. Do not
include raw logs, repeat the full Issue body, or restate the complete plan.
Prefer specialist-thread reuse; create a fresh bounded context only when stale
context or an isolation risk is recorded.

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

### Prospective Orchestration Telemetry

For comparable tracked tasks, capture `root_phase_wall_time_seconds`,
`subagent_startup_seconds`, `subagent_wait_seconds`, `turn_count`,
`tool_call_count`, `duplicate_read_count`, `verification_time_seconds`,
`input_tokens`, and `output_tokens` when the execution environment exposes
them. Every value records `measurement_kind` as `observed`, `estimated`, or
`unavailable`; an unavailable value requires a reason, and an estimate must
never be presented as an observation.

Evaluate the policy prospectively against comparable baseline tasks. Targets
are at least 25 percent lower median wall time, at least 15 percent lower P95
wall time, at least 30 percent fewer root/subagent turns, at least 50 percent
fewer duplicate broad-suite executions, and 25--35 percent fewer combined
`task_packet` and `result_packet` tokens, with no regression in correctness,
safety, ownership, verification sufficiency, the GitHub planning gate, or the
modularity gate. Static policy tests establish contract shape, not runtime
performance.

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
