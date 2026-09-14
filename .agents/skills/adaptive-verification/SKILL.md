---
name: adaptive-verification
description: >-
  Use for nontrivial tracked changes; excludes conversation/story execution.
---

[AGENTS.md](../../../AGENTS.md) governs. Root exclusively owns selection/authorization/budget/
concurrency/dedup/reuse/escalation/sufficiency; specialists propose, never broaden.
Unknown-cost/exhaustion returns completed/mandatory-remaining evidence,revised estimates,
residual risk,and root-decision alternatives: `split|narrow|defer|stop|approved-budget-increase`.

## Levels/Budgets

- `static_inventory` maps inputs/tests/consumers/helpers/fixtures/invariants/risks/exclusions;
  require it for every nontrivial tracked change.
- `exact` covers changed acceptance and every known failure ID.
- `semantic_cluster` covers a shared invariant/contract/fixture/failure family.
- `affected_suite` covers a bounded subsystem only when narrower evidence is insufficient.
- `full_suite` requires an objective trigger.

`complete` is terminal, not level six: mandatory/exact evidence is current green/accepted-skip, failures
resolved, inventory sufficient, and root accepts risk. Passing never authorizes escalation.

|Class|Total|Shard|Floor|
|---|---:|---:|---|
|`local`|300|180|Inventory+exact/static|
|`bounded`|900|300|Inventory+exact+semantic|
|`cross_cutting`|1800|600|Inventory+cluster/affected|
|`release`|Issue-defined|Issue-defined|Named gates|

Criteria may override. Budgets never waive evidence/permit unsafe kills. Do not start work
estimated above remaining budget; on overrun safely finish or runner-interrupt preserving results.

## Provenance

Record class/rationale; each-criterion/`mandatory_evidence`; changed inputs; tests/consumers/helpers/fixtures/
invariants/exclusions; API/packaging/persistence/migration/auth/security/concurrency/process/
PTY/config/dynamic-coupling risks; exact-`authorized_selection`/rationale/owner; estimate/source/confidence;
total/max/spent/remaining; triggers/stops/reuse/invalidation/risk. Unknown dependencies need
inventory/diagnosis.

Read [Evidence source](../../../.github/templates/evidence.md) before reader-facing authoring.

Results record repository/absolute-worktree, baseline/HEAD, dirty fingerprint, environment-
capabilities, purpose, level, exact-selection/IDs/command, outcome/duration/stable-failure-fingerprint. Conservative
`relevant_input_digest` covers selected tests, shared helpers/fixtures, runner configuration,
transitive production closure, and environment. Reuse after HEAD change requires proven
disjointness plus unchanged digest/purpose; uncertainty invalidates.

Assign one `evidence_execution_owner`; deduplicate exactly by
`normalized_command,exact_ids,relevant_input_digest,environment_class`; owner/label/purpose/
venue/wrapper never justify reruns. Run exact/cluster locally, affected/full in CI; duplicate
broad venues only for an environment criterion.

## Canonical Runner

Exact repeated `--id`:
`PYTHONPATH=src python -m tests.runner run --id <ID> --id <ID> --json REPORT`.
Fast: `PYTHONPATH=src python -m tests.runner run --tier fast --json REPORT`.
Repeated `--group`:
`PYTHONPATH=src python -m tests.runner run --group <GROUP> --group <GROUP> --json REPORT`.
Full exactly:
`PYTHONPATH=src python -m tests.runner run --shard 1/2 --json REPORT1` and
`PYTHONPATH=src python -m tests.runner run --shard 2/2 --json REPORT2`, then
`PYTHONPATH=src python -m tests.runner reconcile REPORT1 REPORT2 --json RECONCILED_REPORT`.

#679 targets: fast lane <=300 seconds; affected group <=600 seconds;
sequential-equivalent <=5102.566 seconds; two-shard wall <=3300 seconds.

Reports: `umask 077`; fresh owner-only `mktemp -d` `0700` directory; an
absent JSON leaf created `0600`. Record `canonical_manifest`, `selection_manifest`,
`digest`, `tests`, `durations`, and `unexecuted_ids`.

## Execution

Above `max_shard_seconds`, partition deterministic disjoint portable IDs: union equals
selection; each occurs once; risk/fast-feedback first. Sequential is default.
Parallel requires root authorization, isolation, nonoverlap, runner support; maximum two
fast-fail shards.

Per shard record manifest digest/ordinal/IDs/input digest/result/duration and
`completed_ids,failed_ids,accepted_skips,unexecuted_ids`. Success covers every ID. After
repair rerun exact failures/invalidated shards only. Timeout/replacement/interruption is
neither pass nor assertion-failure: classify; stop only owned process; preserve
completed/failed/accepted-skip/unexecuted sets; resume
valid unexecuted/invalidated IDs. Partial evidence
never completes; one bounded repeat may diagnose flakiness; relabeling cannot evade dedup.
Diagnostic `--id` output is not complete-shard evidence; both shard reports are terminal,
reconciliation proves shard ID sets are disjoint, and their union exactly equals the canonical manifest.

Full-suite triggers: explicit Issue/parent/release criterion; unbounded inventory; changed
package-wide/public/cross-domain invariant; unreliable global discovery/bootstrap/packaging/
harness; unknown out-of-cluster family; uncovered concrete risk. Implementation/review/suite
availability is insufficient. Over-budget required breadth returns the decision, never a
completion claim. Prompt/TOML/policy/docs-only work may use static/exact-policy/link/format/
diff evidence absent runtime/full-suite triggers.

Repair known families and rerun invalidated evidence. One fingerprint in >=2 responsibility
holders stops broad reruns for repository occurrence inventory with roots/exclusions/
patterns/classifications/limits. Route cross-boundary defects once through modularity and
unknown failures to diagnosis. Tests own evidence/coverage; diagnosis causes; modularity
boundary review; root risk.
