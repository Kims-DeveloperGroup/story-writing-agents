# Test suite cost policy

The canonical suite is a bounded regression sample. Its checked-in inventory is
[`tests/fixtures/test_budget_v1.json`](tests/fixtures/test_budget_v1.json).
The accepted ceilings are **18,000 seconds** of sequential-equivalent wall time
for all five complete shards and **3,600 seconds** for each shard. Each full
worker receives one hour of test execution. Full-shard jobs and the workflow
guard have 75-minute native limits; other jobs retain 60-minute limits. The
complete workflow shares a 75-minute deadline, including setup and shutdown.

## Scope rule

- Preserve the existing canonical test IDs or remove them. New IDs are not part
  of routine feature work. A new behavior should be checked in an existing
  representative test when possible, with focused local evidence in the PR.
- Do not raise any retained test's completed `subTest` count, including
  passed, failed, and skipped subtests. The
  budget file records a maximum for each canonical ID. Reducing a count or
  deleting an ID is allowed.
- Keep the total nonblank, noncomment lines in `tests/test*.py` at or below
  the frozen source ceiling. Changes to an existing representative test can trade
  lines with deletions elsewhere, without expanding the overall test source.
- Do not raise either time ceiling. The base-commit comparison rejects increases
  except the exact maintainer-approved #872 transition from the paired
  1,080/540-second ceilings to 18,000/3,600 seconds. This one-time policy amendment
  leaves test IDs, subtest caps and source limits unchanged; arbitrary, partial
  and subsequent time-ceiling increases remain rejected.
- Avoid live provider calls in the canonical suite. Use deterministic local
  fake responses and keep any credential-bearing provider exercise outside
  normal CI.
- A deliberate expansion of this suite requires a separate maintainer-approved
  policy change with measured cost and an explicit exception to the monotonic
  gate. Otherwise, extend an existing representative case or replace a more
  expensive check while respecting the current inventory.

## Maintainer-approved source allocation

Issue #881 permits the exact source-ceiling transition from **142474 to 142604** under
the maintainer's authorization to exceed the test budget when needed. Current-main
changes consumed 431 counted lines of headroom while the provider-schema development
harness still requires all its checks. The allocation covers the 107-line collision,
19 added policy-regression lines, and four net policy-helper lines: 142600 raw lines
plus four charged helper lines. No helper growth is excluded from this accounting.

This source-only transition requires unchanged sequential and per-shard time ceilings
and an identical complete subtest-cap mapping. It preserves the separate #872 time
transition. Other source-ceiling increases remain rejected; test IDs, subtest caps,
runtime behavior, and workflow limits are unchanged.

### Issue #894의 소스 배정

사용자의 필요시 테스트 예산 초과 허용을 이번의 정확한 **142604→142887** 전환에
적용한다. 기준 raw142556에 그룹·계약 및 graph 기대 검증317행과 정책 회귀8행을 더하면
raw142881이다. 기존 #881 helper4행과 이번 정책 helper2행도 청구하여 최종 비용은
142887이다. 기존 effective 여유44행을 사용한 뒤 ceiling 순증은283행이다.

새 전환은 기존 18000/3600초와 전체 subTest mapping이 동일할 때만 허용한다.
기존 #881 소스·#872 시간 전환을 보존하고 다른 증가는 계속 거절한다. 기존
2527개 ID와 Whole Front의 모든 기존 assert를 유지한다. 소스 비용은 정적으로
실측했으며 실행 시간·통과 여부는 정확한 소스의 영향 테스트와 전체 CI로 검증한다.

#895의 실행 엔진 회귀에는 기존 사용자의 필요시 예산 확대 허용을 정확한
**142887→143443** 전환에 적용한다. 부모 PR의 누적 비교 **142604→143443**도
명시적으로 허용하며, 다른 소스 증가는 계속 거절한다. 기존 raw142881에 실행·교정·
인증 회귀541행과 정책 회귀13행을 더해 raw143435가 된다. 기존 helper6행과 이번
정책 helper2행까지 청구한 최종 비용은143443이며 추가 여유는 없다. 기존 사례를
삭제·압축하지 않고 qualified READ 무효화와 실제 리비전 갱신도 검증한다. 두 전환은
전체 test ID·subTest mapping과 18000/3600초가 동일할 때만 인정한다.

## Verification rule

### #896의 검증 후보 소스 배정

사용자의 필요시 소스 예산 확대 허용을 #896의 현재 검증 후보에 한정하여
정확한 **143443→145717**, 부모 누적 **142604→145717** 전환에 적용한다.
기준 raw143435에 기존 ID의 wire·native·Phase 회귀1398행과 정책 회귀13행을
더한 raw144846, 기존 helper8행·새 fixture helper253행·비공개 실제 검증 도구
helper608행·정책 helper2행을 모두 청구한 helper871행의 합계145717이다.
중첩 fixture의 cleanup은 실제 실행 중인 바깥 테스트에 등록하여 감사·자원 정리를 보존한다.
비공개 검증 도구도 비용에서 제외하지 않는다. 이 수치는 현재 검증 후보의 배정이며
CI나 최종 검토에서 추가 변경이 생기면 비용을 다시 정확히 실측한다.

18000/3600초·전체 ID·subTest mapping과 완료된 #881/#894/#895 전환은 유지한다.
인접한 임의 수치나 시간·ID 변경은 기존 gate와 추가 정책 회귀로 거절한다.
이 배정은 실제 Provider 실행이나 큐 예외의 허가가 아니다.

### Provider schema changes

Changes to schema producers, native projection, or pattern syntax must check the final
provider-facing schema with the development harness documented in
[Native schema regex compatibility](docs/design/provider-regex-compatibility.md).
Existing canonical schema-export tests enforce the documented provider subset and the
supported regex grammar. This checker stays in development code; normal provider calls
do not run a syntax scan. Count its helper source in the effective test-source budget.

Before accepting such a change, also record an actual local call through the configured
provider with the final runtime-generated schema. Retain the exact source revision,
provider/CLI/model/settings, schema hash, native response or error, typed outcome, attempted
call count and process cleanup. A fake response, smaller substitute schema, Python regex
compilation, or an offline harness pass does not establish native acceptance. Preserve
failed calls and incomplete outcomes as verification gaps. Keep credential-bearing checks
outside the canonical deterministic suite and ordinary CI.

### Canonical CI

`pr-validation` runs the fast tier and all five full shards for any change under
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

### Actions의 지정 영향 검증

`pr-validation` 수동 실행의 `validation_scope: affected`와 줄바꿈으로 구분한
`test_ids`로 정확한 정규 ID를 선택한다. 잠긴 Python3.11·ARM64 4CPU 환경에서
기존 runner의 반복 `--id` 인자로 실행하며 선택 인자·JSON 결과·로그·하드웨어를
`runtime-affected` artifact에 보관한다. 입력을 shell 코드로 평가하지 않는다.
이 부분 검증은 전체5개 shard의 완주·결과 조정·예산·정리 증거를 대신하지 않는다.

## Dedicated whole-front shard

The maintainer-approved partition has five selections: 577/554/704/691/1 tests.
Run the original four-bin class-LPT assignment first, then extract only
`tests.test_lead_orchestration.LeadOutcomeAndProposalTests.test_front_schema_is_minimal_closed_and_provider_strict`
into shard `5/5`. Other IDs keep their prior bin. This is the sole explicit
whole-class grouping exception; the inventory, cases, assertions and per-ID caps
remain frozen. Historical timing weights remain estimates, not measured proof of
the new shard's duration. The combined 18,000-second ceiling is derived from five
3,600-second limits; the sole achievement target remains one hour per shard.

Normal PR and merge-group validation run all five partitions. Manual dispatch
with `validation_scope: whole-front` runs static checks, focused policy regressions
and only shard `5/5`; fast and regular shards do not start, and complete-suite
reconciliation is skipped. The dedicated worker receives its own full one-hour
execution window after setup and focused checks. It shares the existing 75-minute
workflow envelope and shutdown controls.

While investigating whole-front, retain completed reports and use this isolated
route rather than launching the other shards again. A retained report must match
the implementation and partition before it can support acceptance. An isolated
run, failed report or missing parent report cannot certify complete-suite success.
Further optimization follows the isolated measurement; no separate profiling or
benchmark campaign is required.

## Hosted full-shard method scheduling

Only hosted full shards opt into the fixed `audited-147-v3` profile: 147
fixture-audited existing methods across 17 classes. The profile includes the eight
provider-timeout recovery, ten context-recovery and thirteen interactive Lead
adapter methods audited after v40's measured serial bottlenecks. The original four-bin class assignment stays fixed except for the extracted
whole-front method, and every shard retains canonical selected-ID order. Noneligible tests run once
in their original serial suite; then eligible whole methods run in fresh
interpreters, with at most four active method processes. The frozen launch order
starts whole-front, then seven other v40 failed or unexecuted methods in canonical
order, then 139 passed methods by descending v40 observed duration with canonical
ID tie-breaks. The checked-in tuple sets this order; runtime history cannot change
it. Final report rows retain canonical order. Fast, affected, default and local
runs stay serial. Eligibility does not expand automatically when a class gains a
method.

All discovery, serial work, method startup, execution and merge share the
original 3,600-second work cutoff. Children cannot restart that hour. A separate
30-second cleanup cutoff bounds supervised shutdown; it adds no passing-test
time. Missing process-group absence/reaping proof fails the run. Reports retain
canonical row order and exact source/environment/subTest checks. Parent wall
time includes scheduling and cleanup; summed method durations may exceed wall
time. Reconciliation rejects unknown or mixed execution modes and still needs
five complete reports within the 3,600/18,000-second limits.

Hosted fanout rejects local queue/guest admission and any shared wire-capture
directory. Local workers continue using the existing four-slot host queue;
nested local method pools are not enabled by this profile.

## Parallel local validation

Independent task threads share one host-wide queue with four executing top-level
test processes. The limit covers every checkout and native or Lima executor;
each campaign schedules exactly five complementary full shards. Waiting jobs do not consume an
execution slot. Use the host coordinator in `tests.local_ci`; guest workers must
be admitted by that same host queue before they run.

Shared campaigns provide concurrent feedback. Budget-restoration measurements
use isolated campaigns: drain active jobs, reserve the host, then execute only
all five complementary shards. Their default concurrency is one for isolated
campaigns and four for shared campaigns; `--full-concurrency 1`, `2`, `3`, or `4` overrides
that scheduling choice, which remains explicit in campaign metadata. All five
complete reports remain required even if the first shard fails. Queue waiting
time is recorded separately from
canonical test durations. Shared execution does not waive a failed timing gate,
and contention is not grounds for automatic wall-time authorization. Preserve
the accepted 18,000/3,600-second ceilings and record setup separately.

## Explicit wall-time exception

A maintainer may authorize extra validation time for a specific PR and record the
reason and authorization in its owning Issue and PR. The root then applies
`test:wall-time-exception`. On that PR, CI passes `--allow-wall-time-overage` to
the budget checker. Full-shard and guardian jobs retain 75-minute native limits;
other jobs retain 60-minute limits, whether or not the exception applies. The
checker still reports
actual durations; only the measured sequential and per-shard time comparisons
are waived. Local validation may use the same option under the recorded
authorization. The checked-in ceilings and strict default remain unchanged.

Merge-group runs and PRs without the label retain both measured time checks.
The exception never permits failed or incomplete reports, inventory expansion,
increased subtest counts or test source, budget-file increases, skipped required
checks, or bypassed approvals and branch protection.

Full-shard jobs and the deadline guardian declare `timeout-minutes: 75`; all
other `pr-validation` jobs retain `timeout-minutes: 60`. Every event shares one
absolute 75-minute deadline derived from the workflow run's GitHub `created_at`,
including queue, setup, dependency waits, cancellation and uploads.
Classification records that deadline before selecting lanes. A rerun cannot
extend the original run's window. Each job clamps its own clock to the shared
deadline.

Full workers reserve 450 seconds and receive a fixed 3,600-second execution
window. If the remaining job/shared allowance cannot provide the full window
and reserve, admission fails before test launch instead of shortening execution.
Fast workers retain their 3,150-second caps, reduced by setup
and shared-clock consumption. Each reconciliation gate recomputes its allowance
and reserves 450 seconds. Missing or exhausted budgets fail verification.

The reserve comprises 30 seconds forced-kill grace, 60 seconds cleanup and
360 seconds cancellation allowance. Workers stop before the shared deadline's
reserved window, no later than minute 67.5, and artifact uploads have one-minute
step timeouts. Normal cancellation begins with six minutes remaining (minute 69);
force cancellation begins with five-and-a-half minutes remaining (minute 69.5).
These controls do not guarantee runner scheduling or external control API timing.

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
within the 75-minute shared limit and each executed job
within its declared native limit, with every required report and
check complete. An unavailable enforcement path fails visibly.

## Hosted runtime baseline

Runtime fast and full jobs use the standard GitHub-hosted Linux ARM64 runner
`ubuntu-24.04-arm`. In this public repository its standard allocation is four
vCPUs and 16 GB RAM. [Runner specifications](https://docs.github.com/en/actions/reference/runners/github-hosted-runners).

Before application tests, record and verify Linux, the requested architecture
and four available CPUs, with CPU affinity and native run/attempt/workflow
identity in the uploaded hardware receipt. Use the locked Python 3.11 environment
natively. The runner change preserves the existing test selections, assertions,
subTest caps, GC settings, process cleanup and the job/shared deadlines above.

Compatibility, complete-method fit and performance evidence belongs to its
recorded source and execution environment. Refresh those results on ARM64 before
claiming acceptance. The runner rollout and remaining gates are tracked in
[the #872 runner plan](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/872#issuecomment-6284422415).

## Runtime optimization evidence

For [#874](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/874),
the maintainer accepted predefined logical records with required/optional fields,
direct alternative dispatch, and tolerant production consumption of extra
response-record properties. Optional presence must not create duplicate logical
schemas or tags, and runtime must not infer or merge optional schemas. Replace obsolete unknown-field
rejection assertions with acceptance and normalized/typed omission checks. Keep
required-field, declared-value, semantic-map identity, domain, binding, and
authorization rejection coverage, strict raw-wire cases, and both native provider
projections. Preserve exact ordered diagnostics for unchanged contracts; record
intentional discriminator and unknown-property behavior changes explicitly.
This authorization does not increase test IDs, subtest caps, source budgets,
execution ceilings, or permit unrelated scenario removal.

Before changing a validation shortcut, add independent compatibility oracles to
existing test IDs and run them against the unchanged implementation. Preserve
exact ordered diagnostics, fallback behavior, input/export mutation checks, test inventory,
and source budgets. Run the same checks against the candidate before expensive
runtime scenarios.

For separately authorized benchmark studies, measure at least three ordinary fresh-process samples per source on the same
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
not establish that a complete class or shard fits its one-hour execution window.
Refresh shard weights only from complete verified timings, retaining whole
classes and required fast-tier membership. The active optimization record is
[child #872](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/872).

Schema-template reuse must preserve cold/warm diagnostic equality, independent
payloads/results/exports, per-query cap resets, and schema-source/template
ownership consistency. Schema, decoder, and evaluator implementations are trusted
to remain stable during a chat; normal decoding does not repeatedly attest their
code, defaults, closures, or class layouts. Construction validates declarations,
while current-call binding, offered IDs, response validation, typed ownership, and
host/authorization checks remain required. Cache hints, including simultaneous replacement of
both hints, must not select an untrusted schema. Preparation belongs to one
`SchemaRules` owner; fresh owners and generic compiler search remain separate
costs. A successful cache-reuse check alone demonstrates no whole-suite speedup.

After implementation, run affected canonical IDs and all five complementary
shards on one immutable source and locked four-core ARM64 environment.
Record each shard's test wall time, native job duration, outcome and completion;
report the complete five-shard sum and workflow elapsed time. Reconcile exact
coverage, source bindings and the accepted 18,000/3,600-second ceilings. Partial
or timed-out reports remain nonpassing evidence. An affected-test preflight may
run separately; verify its exact implementation/tree equivalence before delivery,
then obtain all five canonical reports for the current delivery source. Record
each report's native run and attempt. Isolated attempts retain successful
same-source evidence; they do not automatically combine reports or certify full CI.

Download shard reports for reconciliation outside the source checkout, under `RUNNER_TEMP`,
and pass those paths to both reconciliation and the frozen-budget checker. Source snapshots
include untracked inputs, so downloading evidence into the checkout changes its identity even
when tracked source is untouched. Reconciliation uses the full workers' bytecode-suppression
setting; source admission remains enabled.

The deadline guardian exits when the completion marker and every peer job are terminal,
including after cancellation. Individual cancelled and failed outcomes remain nonpassing;
the guardian must keep watching while any peer job is still running.

For [child #872](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/872),
the sole achievement target is successful completion of every full shard within
one hour. No separate benchmark, profiling campaign or percentage speedup is
required. The [current implementation plan](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/872#issuecomment-6306330433)
tracks regression verification and the actual PR-head five-shard gate. The PR
validation workflow runs canonical checks without a diagnostic job or manual
profiling dispatch path.

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
