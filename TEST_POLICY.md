# Test suite cost policy

The canonical suite is a bounded regression sample. Its checked-in inventory is
[`tests/fixtures/test_budget_v1.json`](tests/fixtures/test_budget_v1.json).
The accepted ceilings are **1,080 seconds** of sequential-equivalent wall time
for all four complete shards and **540 seconds** for each shard. CI also stops a
shard job after 15 minutes, including setup.

## Scope rule

- Preserve the existing canonical test IDs or remove them. New IDs are not part
  of routine feature work. A new behavior should be checked in an existing
  representative test when possible, with focused local evidence in the PR.
- Do not raise any retained test's completed `subTest` count, including
  passed, failed, and skipped subtests. The
  budget file records a maximum for each canonical ID. Reducing a count or
  deleting an ID is allowed.
- Keep the total nonblank, noncomment lines in `tests/test*.py` at or below
  the post-cut baseline. Changes to an existing representative test can trade
  lines with deletions elsewhere, without expanding the overall test source.
- Do not raise either time ceiling. The base-commit budget comparison rejects
  increases even if a contributor edits the checked-in budget file.
- Avoid live provider calls in the canonical suite. Use deterministic local
  fake responses and keep any credential-bearing provider exercise outside
  normal CI.
- A deliberate expansion of this suite requires a separate maintainer-approved
  policy change with measured cost and an explicit exception to the monotonic
  gate. Otherwise, extend an existing representative case or replace a more
  expensive check while respecting the current inventory.

## Verification rule

`pr-validation` runs the fast tier and all four full shards for any change under
`src/`, `config/`, or `tests/`, or to the runtime dependency and test-policy
files. It runs static validation for documentation-only changes. Local runtime
work also runs affected exact test IDs or modules before the PR. CI reconciles
complete, disjoint shards and checks their test outcomes, exact IDs, per-ID
subtest counts, test source size, measured wall times, and the base-commit
budget. A cancelled,
incomplete, or failed run does not pass. A time overage also fails unless the
maintainer-authorized exception below applies.

The timing-weight manifest balances shards; it is not evidence of actual run
time. The budget gate uses the wall time in the current Python 3.11 runner
reports. Report the observed full and shard times and any platform limitation
when recalibrating weights.

## Parallel local validation

Independent task threads share one host-wide queue with four executing top-level
test processes. The limit covers every checkout and native or Lima executor;
each campaign schedules exactly four complementary full shards. Waiting jobs do not consume an
execution slot. Use the host coordinator in `tests.local_ci`; guest workers must
be admitted by that same host queue before they run.

Shared campaigns provide concurrent feedback. Budget-restoration measurements
use isolated campaigns: drain active jobs, reserve the host, then execute only
all four complementary shards. Their default concurrency is one for isolated
campaigns and four for shared campaigns; `--full-concurrency 1`, `2`, `3`, or `4` overrides
that scheduling choice, which remains explicit in campaign metadata. All four
complete reports remain required even if the first shard fails. Queue waiting
time is recorded separately from
canonical test durations. Shared execution does not waive a failed timing gate,
and contention is not grounds for automatic wall-time authorization. Preserve
the existing 1,080/540-second ceilings; aim for 900/450 seconds to leave margin.

## Explicit wall-time exception

A maintainer may authorize extra validation time for a specific PR and record the
reason and authorization in its owning Issue and PR. The root then applies
`test:wall-time-exception`. On that PR, CI allows each full shard up to 40 minutes
including setup, and passes `--allow-wall-time-overage` to the budget checker.
The checker still reports actual durations; only the measured sequential and
per-shard time comparisons are waived. Local validation may use the same option
under the recorded authorization.

The checked-in ceilings and strict default remain unchanged. Merge-group runs
and PRs without the label retain the 15-minute job limit and both time checks.
This exception never permits failed or incomplete reports, inventory expansion,
increased subtest counts or test source, budget-file increases, skipped required
checks, or bypassed approvals or branch protection.

Explicitly dispatched diagnostic jobs also allow up to 40 minutes. Test execution
has a separate earlier deadline to leave time for termination and evidence upload.
Diagnostics preserve progress and available partial evidence on interruption;
partial or timed-out execution never counts as a passing test. Their reports
remain diagnostic evidence and never replace the required complete full-suite gate.

## Initial calibration

Issue #732 first reduced the 2,872-ID snapshot to 2,530 IDs (342 fewer,
11.9%). Two complete local macOS/Python 3.11 shards passed in 502.4 and
513.5 seconds: 1,015.9 seconds (16.9 minutes) sequential equivalent and
8.6 minutes for the longer shard. Compared with the prior 3,744-second
timing estimate, the expected reduction was about 73%.

That 2,530-ID count was an intermediate calibration. PR #733's final
inventory contains 2,527 IDs (345 fewer than the original snapshot,
12.0%) after three additional CI-driven removals. On Ubuntu/Python 3.11,
the complete final shards passed in 292.2 and 152.5 seconds: 444.7 seconds
(7.4 minutes) sequential equivalent and 4.9 minutes for the longer shard.
The CI ceilings above remain the acceptance rule on the current runner.
