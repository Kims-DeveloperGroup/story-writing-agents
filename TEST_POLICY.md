# Test suite cost policy

The canonical suite is a bounded regression sample. Its checked-in inventory is
[`tests/fixtures/test_budget_v1.json`](tests/fixtures/test_budget_v1.json).
The accepted ceilings are **1,080 seconds** of sequential-equivalent wall time
for all four complete shards and **540 seconds** for each shard. CI also stops a
job after 60 minutes, including setup.

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
`test:wall-time-exception`. On that PR, CI passes `--allow-wall-time-overage` to
the budget checker. Every job retains the same 60-minute native timeout,
including setup, whether or not the exception applies. The checker still reports
actual durations; only the measured sequential and per-shard time comparisons
are waived. Local validation may use the same option under the recorded
authorization. The checked-in ceilings and strict default remain unchanged.

Merge-group runs and PRs without the label retain both measured time checks.
The exception never permits failed or incomplete reports, inventory expansion,
increased subtest counts or test source, budget-file increases, skipped required
checks, or bypassed approvals and branch protection.

Every `pr-validation` job declares a native `timeout-minutes: 60`, including
classification, static validation, fast validation, each full shard,
reconciliation, automatic or manually dispatched diagnostics, and deadline
control. Every event shares one absolute 60-minute deadline derived from the
workflow run's GitHub `created_at`, including queue, setup, dependency waits,
cancellation, and uploads. Classification records that deadline before selecting
lanes. A rerun cannot extend the original run's window. Test and reconciliation
workers clamp the 60-minute job clock to this shared deadline. Each of the three
test-worker allowances and both reconciliation allowances reserves 450 seconds;
the three explicit test-worker caps are 3,150 seconds, reduced by setup and
shared-clock consumption. Each reconciliation gate recomputes its allowance.
Missing or exhausted budgets fail verification rather than borrowing the
cancellation reserve. Workers stop by minute 52.5, retain a 30-second kill grace,
and allow at most one minute for artifact upload before normal cancellation at
minute 24. Fast, full, diagnostic, and reconciliation artifact uploads each have
a one-minute step timeout. Control metadata distinguishes the 60-second cleanup
allowance from the 360-second cancellation reserve. These allowances do not
guarantee the duration of queued jobs or checkout and other post-actions.

After diagnostic artifact upload, disk-scratch cleanup may remove only the exact
owned temporary directory recorded at creation. It validates the path and directory
identity, rejects symlink roots and mounted scratch trees, and uses symlink-resistant
standard-library deletion. Its small execution cap includes termination grace and
is clamped to the existing shared deadline minus the 360-second cancellation
reserve. Upload and cleanup share the existing allowance; cleanup adds no time.
Missing identity, an exhausted budget, or failed removal stays a visible failure.
Published evidence outside scratch is preserved. Memory-scratch handling is unchanged.

An independent deadline job starts alongside classification and monitors a
completion marker that depends on every validation job. Its normal-cancellation
threshold is 360 seconds remaining and its force-cancellation threshold is 330
seconds remaining. Bounded polling and fresh identity checks can delay the actual
request after a threshold. Five-second polling begins with 390 seconds remaining. The guard
stops starting iterations and sleeping with 30 seconds remaining; metadata that
arrives after that cutoff fails enforcement without granting a new control
window. Only this guard has `actions: write`; each cancellation verifies the
current run and attempt identity. API calls have five-second limits and zero
retries. The reserve accounts for GitHub's documented [five-minute cancellation
period](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-cancellation).
The marker does not replace any validation result, and a cancellation response
or HTTP 409 is not terminal proof. Native job timeouts remain a separate limit.
Runner scheduling or an unavailable or inconsistent GitHub control API can
delay enforcement. Acceptance therefore requires coherent terminal run and job
records, matching run/attempt/source identities, and observed complete workflow
and executed-job durations within 60 minutes, with every required report and
check complete. An unavailable enforcement path fails visibly.

Explicitly dispatched diagnostic jobs also stop after 60 minutes. Test execution
has a separate earlier deadline to leave time for termination and evidence
upload. Diagnostics preserve progress and available partial evidence on
interruption; a partial or timed-out execution never counts as a passing test.
These reports remain diagnostic evidence and never replace the required
complete full-suite gate.

## Hosted runtime baseline

Runtime fast and full jobs use the standard GitHub-hosted Linux ARM64 runner
`ubuntu-24.04-arm`. In this public repository its standard allocation is four
vCPUs and 16 GB RAM. [Runner specifications](https://docs.github.com/en/actions/reference/runners/github-hosted-runners).
Explicit runtime diagnostics default to ARM64 and retain an x64 selection.

Before application tests, record and verify Linux, the requested architecture
and four available CPUs, with CPU affinity and native run/attempt/workflow
identity in the uploaded hardware receipt. Use the locked Python 3.11 environment
natively. The runner change preserves the existing test selections, assertions,
subTest caps, GC settings, process cleanup and all 60-minute job/shared deadlines.

Compatibility, complete-method fit and performance evidence belongs to its
recorded source and execution environment. Refresh those results on ARM64 before
claiming acceptance. The runner rollout and remaining gates are tracked in
[the #872 runner plan](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/872#issuecomment-6284422415).

## Runtime optimization evidence

Before changing a validation shortcut, add independent compatibility oracles to
existing test IDs and run them against the unchanged implementation. Preserve
exact ordered diagnostics, fallback behavior, mutation checks, test inventory,
and source budgets. Run the same checks against the candidate before expensive
runtime scenarios.

Measure at least three ordinary fresh-process samples per source on the same
existing runner, balancing source order and recording setup and workload CPU and
wall time separately. Run profiling in a separate process; profiled time is not
an ordinary benchmark sample, and overlapping cumulative costs cannot be added.
Compare the exact semantic workload and mutation inventory. Record per-sample
source pins, graph hashes and sizes, accepted results, and checked cleanup.
Generated task identities may differ only where the fixture explicitly binds
them; do not substitute byte tolerances or blanket identifier normalization for
behavioral equivalence.

Keep native workflow outcomes, missing reports and cleanup failures visible.
A successful representative benchmark gives no canonical test credit and does
not establish that a complete class or full suite fits the 60-minute deadline.
Refresh shard weights only from complete verified timings, retaining whole
classes and required fast-tier membership. The active optimization record is
[child #872](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/872).

Schema-template reuse must preserve cold/warm diagnostic equality, independent
payloads/results/exports, per-query cap resets, evaluator-drift fallback, and
sealed-decoder provenance. Cache hints, including simultaneous replacement of
both hints, must not select an untrusted schema. Preparation belongs to one
`SchemaRules` owner; fresh owners and generic compiler search remain separate
costs. A successful cache-reuse check alone demonstrates no whole-suite speedup.

After implementation, run the affected canonical IDs and all four complementary
shards on one immutable source with the locked four-core ARM64 environment.
Record each shard's test wall time, native job duration, outcome and completion;
report the sum of test wall times and workflow creation-to-update elapsed time.
Reconcile exact coverage, source bindings and the unchanged 1,080/540-second
ceilings. Partial or timed-out reports remain nonpassing evidence. The
[implementation plan](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/872#issuecomment-6290851302)
tracks this gate; the proposed ten-minute attribution diagnostic is parked.

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
