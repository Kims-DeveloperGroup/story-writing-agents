---
name: adaptive-verification
description: >-
  Use before planning, running, reusing, escalating, or accepting evidence for nontrivial
  tracked changes. Excludes untracked conversation and story-runtime execution.
---

# Adaptive Verification

[AGENTS.md](../../../AGENTS.md) controls ownership/authorization; this procedure never
overrides policy.

Root owns selection, authorization, budgets, deduplication, reuse, escalation, and
sufficiency. Specialists only propose; they never broaden level, selection, concurrency,
or budget. Unknown or excessive cost returns `split|narrow|defer|stop` or needs explicit
maintainer budget approval.

## Evidence Levels

Select minimally:

- `static_inventory`: map inputs, direct tests, transitive consumers, helpers, fixtures,
  invariants, risks, and exclusions. Require it for every nontrivial tracked change.
- `exact`: cover changed acceptance and every known failing test ID.
- `semantic_cluster`: cover a shared invariant, collaborator contract, fixture, or family.
- `affected_suite`: cover a bounded subsystem only when narrower evidence is insufficient.
- `full_suite`: use only for an objective trigger below.

`complete` is terminal, not level six. Require current green or accepted-skip mandatory
evidence, resolved failures, sufficient inventory, and root-accepted risk. A pass never
requires or authorizes escalation. Complete criteria only with current exact evidence.

|Class|Total seconds|Maximum shard|Normal floor|
|---|---:|---:|---|
|`local`|300|180|Inventory plus exact or static|
|`bounded`|900|300|Inventory, exact, required semantic|
|`cross_cutting`|1800|600|Inventory plus selected cluster or affected|
|`release`|Issue-defined|Issue-defined|Named integration or release gates|

Accepted criteria may override defaults. Budgets authorize work, never correctness waivers
or unsafe kills. Do not start a shard estimated above remaining budget.
If running work exceeds estimate, allow safe completion or a runner-supported preserving
interrupt. Exhaustion never excuses mandatory evidence.

## Plan and Provenance

Before commands, record:

- class and risk rationale; each criterion mapped to `mandatory_evidence`;
- changed source, tests, fixtures, configuration, policy, documentation, and environment;
- direct tests, transitive consumers, helpers, fixtures, invariants, cross-boundary
  consumers, and justified exclusions;
- flags for API, packaging, persistence, migration, authorization, security, concurrency,
  process, PTY, shared configuration, and uncertain dynamic coupling;
- exact `authorized_selection`, rationale, owner, estimates/source/confidence, total,
  maximum, spent, remaining, triggers, stops, reuse/invalidation, and residual risk.

Incomplete dependency knowledge is uncertainty. Broaden inventory or diagnose before work
whose affected set or cost cannot be bounded.

Every result records repository, absolute worktree, baseline, HEAD, dirty fingerprint,
environment capabilities, purpose, level, exact selection and command, outcome, duration,
and stable failure fingerprint. Its conservative `relevant_input_digest` covers selected
tests, shared helpers and fixtures, runner configuration, transitive production closure,
and relevant environment.

Reuse after HEAD changes only when root proves disjointness and unchanged digest and
purpose. Relevant or uncertain changes invalidate evidence; renaming purpose does not.

Assign one `evidence_execution_owner` per item. Deduplicate exactly on
`normalized_command`, `exact_ids`, `relevant_input_digest`, and `environment_class`.
Different owner, label, purpose, venue, or wrapper never justifies rerunning equivalents.
Normally run exact and cluster locally, affected and full in CI. Duplicate broad local and
CI evidence only for an environment criterion; record both identities and requirements.

## Shards and Interruptions

- Split selections estimated above `max_shard_seconds` into deterministic, disjoint,
  portable exact IDs. Their union equals selection and each ID appears once.
- For Python unittest, use module, class, or method IDs. Run risky and fast-feedback shards
  first.
- Default sequentially. Parallel work requires root authorization, isolation, nonoverlap,
  and runner support. At most two fast-fail shards may run concurrently.
- Record manifest digest, ordinal, exact IDs, input digest, result, duration,
  `completed_ids`, `failed_ids`, `accepted_skips`, and `unexecuted_ids` for every shard.
- Aggregate success requires green or accepted-skip evidence for every authorized ID.
  After repair, rerun exact failures and only invalidated shards.
- Timeout, replacement, or interruption is neither pass nor assertion failure. Stop only
  the owned process; classify and preserve every result set.
- Resume only valid unexecuted or invalidated IDs. Partial evidence never completes work.
  One bounded recorded repeat may diagnose flakiness; relabeling cannot evade deduplication.

## Full Suite and Failures

Authorize `full_suite` only for:

- an explicit Issue, parent-integration, or release criterion;
- static inventory that cannot conservatively bound the affected set;
- a changed package-wide, public, or cross-domain invariant;
- global discovery, bootstrap, packaging, or harness behavior making narrower evidence
  unreliable;
- an unknown out-of-cluster failure family; or
- concrete residual risk not covered by affected selections.

Implementation, final review, or suite availability is insufficient. When required broad
work exceeds budget, return the decision; never omit it or claim completion. Prompt, TOML,
policy, or documentation-only work may use static parsing, exact policy tests, link and
format checks, and diff inspection when no runtime or full-suite trigger exists.

- Repair a known family and rerun exact invalidated evidence.
- When one stable fingerprint appears in >=2 responsibility holders, stop broad reruns.
  Inventory repository-wide occurrences with roots, exclusions, patterns,
  classifications, and limitations.
- Apply the existing modularity gate once for cross-boundary ownership or capability
  defects. Send unknown failures to `regression_diagnostician`.
- On unknown cost or exhaustion, return completed and mandatory remaining evidence,
  revised estimates, residual risk, and root choices.
- Keep six roles: tests own evidence and coverage; diagnosis owns causes; modularity owns
  boundary review; root owns final risk.
