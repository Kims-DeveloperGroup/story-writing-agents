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

### #897의 관측·사용량 회귀 소스 배정

사용자의 필요시 테스트 예산 초과 허용을 **145817→146667**과 부모 PR의
main 기준 **142604→146667**에 정확히 적용한다. 당시 정의와 실제 binding,
제출 불확실성, 누적 usage·footer·codec·전체 그룹 비교를 기존 사례에서 검증하기 위한
배정이다. 기존 raw144946에 최초 후보715행·정책 회귀11행, 리뷰 회귀와 fixture
보완79행·owner-only 경계2행·저장·계측 및 호출 전 거절 회귀35행·비공개 표시 검증2행을 더하면 raw145790이다. 기존 청구 helper871행에
native-history/export fixture4행과 정책 helper2행을 더한877행까지 청구하여
최종 비용은 **146667**, 추가 여유는 없다. 정식 런타임 AST 그래프는 `TYPE_CHECKING`을 제외한166모듈/756간선이며,
추가된 UI→공개 domain facade 의존성을 포함한다. 그래프 수치 기대의 교체는 소스 순증0이다.

Root가 원본/후보 SHA, Python3.11 정적컴파일, 기존 동기·비동기 ID와
subTest 호출 AST, 원래 Front 원문 보존을 확인한다. 기존 추천 결과·요청의 인접성과
실패 trace 게시, 전체 감사·공개 projection의 구분은 기존 ID의 assertion으로 검증한다.
정적 증거와 실패한 최초 CI는 새 소스의 실행 통과나 시간을 대신하지 않는다.
기존2527 ID·전체 subTest mapping·18000/3600초와 원래 Front 사례를 유지한다.
기존 #881/#894/#895/#896 소스와 #872 시간 전환은 보존하고 다른 증가는 거절한다.
이 배정은 신규 native 호출예산이나 quarantine 상태를 바꾸지 않는다.

### #898의 최종 실제 검증 소스 배정

사용자의 필요시 테스트 소스 예산 확대 허용을 정확한 **146667→146829**와
부모 PR의 main 기준 **142604→146829**에 적용한다. 두 원래 입력을 별도 대화에서
검증하는 단일 활성 도구 757행이 기존 608행을 대체하므로 helper 순증은149행이다.
기존 도구·닫힌 실행 허가·실제 증거는 보존하며 새 실행 도구로 재사용하지 않는다.
raw145790에 기존 ID의 좁은 전환/인접값 거절 검증11행을 더한 raw145801,
기존 비도구 helper269행·활성 도구757행·이번 정책 helper2행을 합쳐
정확히146829행을 청구한다. 별도 동시 유지 도구라면 source 전체를 추가 청구한다.

기존2527개 ID·전체 subTest mapping·18000/3600초·제품의 task당12회 한도는
유지한다. 기존 승인 전환과 #872 시간 전환을 보존하고 다른 소스 증가는 거절한다.
이 배정은 실제 Provider 호출 승인이나 큐 예외가 아니다. 기존40/40·격리4건과
종료된 #894/#896 실행 허가는 보존하고, #898의 검토된 소스/도구/설정에 묶인 새
유한 캠페인 승인 전에는 호출하지 않는다. 호출/시간/프로세스 경계만 강제할 때는
토큰·금액·미사용 출력 인자의 실효 상한을 관측됐다고 보고하지 않는다.

## Verification rule

### #896의 검증 후보 소스 배정

사용자의 필요시 소스 예산 확대 허용을 #896의 현재 검증 후보에 한정하여
정확한 **143443→145817**, 부모 누적 **142604→145817** 전환에 적용한다.
기준 raw143435에 기존 ID의 wire·native·Phase 회귀1498행과 정책 회귀13행을
더한 raw144946, 기존 helper8행·새 fixture helper253행·비공개 실제 검증 도구
helper608행·정책 helper2행을 모두 청구한 helper871행의 합계145817이다.
중첩 fixture의 cleanup은 실제 실행 중인 바깥 테스트에 등록하여 감사·자원 정리를 보존한다.
비공개 검증 도구도 비용에서 제외하지 않는다. 이 수치는 현재 검증 후보의 배정이며
CI나 최종 검토에서 추가 변경이 생기면 비용을 다시 정확히 실측한다.

18000/3600초·전체 ID·subTest mapping과 완료된 #881/#894/#895 전환은 유지한다.
인접한 임의 수치나 시간·ID 변경은 기존 gate와 추가 정책 회귀로 거절한다.
이 배정은 실제 Provider 실행이나 큐 예외의 허가가 아니다.


### #904의 최소 연속성·사후 관측 회귀 소스 배정

사용자의 필요시 테스트 소스 예산 확대 허용을 정확한 **146829→147338** 전환에
적용한다. 최신 main의 raw145796에서508행을 더한 **raw146304**, 기존 청구 helper1028행에
실제 owned native-history fixture5행과 이번 monotonic 정책 helper1행을 더한
**helper1034행**으로 총147338행을 계수했다. 정상2회·재선택3회·사후 관측 불가를
재요청하지 않는3회·명확한 모순의 복구4회·READ 입력 계보5회와 진단 표시를
기존 정규 ID에서 검증한다. 원본 자료와 앞선 검증 도구의 비용/닫힌 실행 허가는
그대로 보존하며 새 실제 Provider 호출을 승인하지 않는다. 기존2527개 ID와 전체
subTest cap,18000/3600초 한도,Whole Front 타임아웃 즉시 중단 원칙은 유지한다.
이 정확한 소스 전환만 허용하고 인접값·다른 임의 증가는 계속 거절한다.

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

### #838의 직접 1차 검토·단계 배정 회귀 소스 배정

사용자의 필요시 테스트 소스 예산 확대 허용을 정확한 **147338→148667**
전환에 적용한다. 최신 main 통합을 포함한 실제 배정은 다음과 같다.

| 소스 구분 | 청구 행 수 |
| --- | ---: |
| `tests/test*.py`의 비어 있지 않은 비주석 행 | 147519 |
| 승인된 fixture·실제 검증 helper | 1148 |
| 전체 상한 | 148667 |

여유는 추가하지 않는다. helper1148행은 기존1034행, 이번 전환 정책1행,
실제 native 도구819행이 기존757행을 대체하는 순증62행과 공유 fixed-business fixture42행과 명시적 typed-root selector9행을 포함한다.
배정148455 이후131행은 기존 W의 현재 SELECT→GEN 검증 정렬134행과
기존 correction fixture의 두 SELECT 호출 정렬에서 줄어든3행이다.
그 뒤 SOURCE-only fixture57행을 추가했다. 이는 현재 compiler의 typed business
owner만 선언하는 test-owned BoundCallFactory 조합이며, 원래 decoder·검증·task·
한도와 기본6회 source/6+7회 예산 거절을 유지한다. SELECT 완료나 별도
권한을 꾸미지 않고, 기존 후속 작업 입력 fixture의 실제5회 기록을 보존한다.
SOURCE 조합을 공유 helper로 이동하며 raw34행을 줄이고 helper42행을
청구하여 순증8행을 더했다. 같은 owner 집합은 post-selection 회복 unit에도
사용한다. 이 unit은 SELECT 완료를 주장하지 않고 기존 cap2·두 provider·
실패/finalizer 검증을 보존한다.
현재 GEN의 실제 owner.logical_root를 선택하는 정렬에서 raw2행·helper9행을
추가했다. 기존 값·부정 입력·모든 같은-root partition과 미지정 default 경로는
유지하며 opaque identity 문자열의 호환 해석을 추가하지 않는다.
전체 family의 canonical census와 실제 각 선택 GEN의 strict·바인딩·바이트
경계·부정 사례를 유지한다. 2527개 ID, ID별 subTest 상한,18000/3600초,
worker·정리·권한 검증은 변경하지 않는다.

재생의 정확한 owner 순서·동일한 compilation·Two Phase 보존 검사와
업무 owner census 및 기존 단일 국면 fixture 정렬에 정확히 5행을 더했다.

이 소스 배정은 새 provider 실행이나 큐 예외를 부여하지 않는다. #838의 필요한
실제 호출·테스트에 대한 별도 사용자 허가와 source-bound admission을 따른다.
원래40/40·격리4건과 닫힌 #894/#896/#898 허가는 보존한다. 같은 운영 소스의
기존 실제 검증을 재사용할 때는 코드·설정·도구·환경의 동일성을 명시한다.
실제 품질·사용자 비교 평가 #806은 사후평가 Backlog로 유지한다.

#### 최신 main 합류 후 시간 복구 소스 정산

기존 사용자의 필요시 테스트 예산 확대 허용을 #914 합류 후 확인한 정확한
**147338→148715** 및 **148667→148715** 소스 전환에 적용한다.
위148667 배정은 시간 복구 전 기록이며 당시 상한은148715였다.
현재 상한은 아래 실패 위치 진단 배정의148783이다.
#914의 기존 테스트67행을 합치고, 이번에 불필요해진 구현 변조 기대32행을
제거하면 기존 청구분보다35행 많다. 시나리오를 삭제하거나 형식만 압축해
이 합류 비용을 숨기지 않는다.

| 소스 구분 | 청구 행 수 |
| --- | ---: |
| `tests/test*.py`의 비어 있지 않은 비주석 행 | 147565 |
| 기존 승인 helper1148행과 새 전환 정책2행 | 1150 |
| 앞선 합류 정산 상한 | 148715 |

새 경계 회귀11행도 기존 정규 ID에 청구했다. 기존148667보다 정확히48행
증가하며 추가 여유는 없다. 이전147338→148667 전환과 다른 승인 이력은
보존한다. 두 새 전환은 전체 ID/subTest mapping과18000/3600초가 동일할
때만 허용하며 각 base와 ceiling의 인접값은 거절한다. 런타임·스키마·
Whole Front 사례·worker·정리·권한 조건은 변경하지 않는다.

#### 인증 복구 진단과 긴 작업의 시작 순서

기존 사용자의 필요시 테스트 소스 예산 확대 허용을 정확한
**147338→148747** 및 **148715→148747** 전환에 적용한다.
인증 복구의 원래 실패·120초 대기·phase plan·호출 수·assertion을 유지하며,
실패할 때만 준비 상태와 script cursor·남은 control·단계의 안전한 진단을
note로 남긴다. 추출 오류는 타입명만 기록하고 원래 실패를 보존한다.
요청·응답 본문, 식별자, 설정은 진단에 포함하지 않는다.

| 소스 구분 | 청구 행 수 |
| --- | ---: |
| 앞선 테스트 소스147565 + 진단19 + 기존 ID의 경계 회귀11 | 147595 |
| 앞선 helper1150 + 정확한 소스 전환 정책2 | 1152 |
| 앞선 인증 진단 상한 | 148747 |

앞선148715보다 정확히32행 많고 여유는0이다. 기존 전환 이력을 유지하며
두 새 전환만 허용하고 기준·상한의 인접값±1은 거절한다. 정규 ID2527개,
기존 subTest 한도·검사 내용·18000/3600초·최대4worker는 유지한다.

609.725초로 관측된 기존 근거 조회 작업은 `_HOSTED_LAUNCH_IDS`에서 W와 D
바로 뒤로 옮긴다. 기존147개/17클래스의 멤버·가중치·shard 배정은 같고,
full3에서 먼저 시작하며 다른4개 shard의 순서는 같다. 순서 이동과 기존
독립 launch-digest 기대값 변경은 소스 증감0이다. 시작 시점을 바꾸는
조정이며 작업량 감소나 실행 중 동적 정렬로 보고하지 않는다.

#### 실패한 준비 작업의 위치 진단

기존 필요시 예산 확대 승인을 정확한 **147338→148783** 및
**148747→148783** 전환에 적용한다. 앞선 전환 이력은 보존한다.
실패 시 coroutine/generator 대기 연결을 최대16개, 익명 Python stack을
최대8개×16frame까지 살펴 함수 qualname·행 번호와 고정 label만 남긴다.
thread ID·이름, 파일명, 본문, locals·인자·객체 repr은 기록하지 않는다.
새 thread·timer·profiler·signal·전역 cache를 만들지 않고, 추출 오류가
원래 bounded-progress 실패를 가리지 않도록 기존 fallback을 유지한다.

| 소스 구분 | 청구 행 수 |
| --- | ---: |
| 앞선147595 + 위치 진단23 + 기존 ID의 경계 회귀11 | 147629 |
| 앞선 helper1152 + 정확한 소스 전환 정책2 | 1154 |
| 현재 전체 상한 | 148783 |

앞선148747보다 정확히36행 많고 여유는0이다. 기준/상한±1은 거절한다.
대기120초, phase plan·호출 수·assertion, 정규 ID2527개·기존 subTest 한도,
18000/3600초·최대4worker·실행 모드·기본GC는 유지한다. snapshot은
해당 시점의 위치 단서이며 모든 worker나 전체 지연의 시간 기여도를
보장하지 않는다. 진단 코드와 함께 측정한 결과를 일반 속도 개선으로
주장하지 않는다.

### #838 인증 복구 통합 fixture의 진행 대기

`ContextRecoveryTests._bound_replay`의 기존120초는 각 인증 진행 대기
구간에 적용되던 fixture 값이며 제품 SLA나 전체 테스트 시간 기준이 아니다.
같은120초 설정의 이전 성공 실행은 전체236.132초였다. 운영 코드를 유지한
[격리 진단41866198794](https://github.com/Kims-DeveloperGroup/story-writing-agents/actions/runs/41866198794)는
이 값만900으로 바꿔185.214초에 모든 기존 assertion과 정리를 통과했다.
이는 해당 경로의 정상 완료 증거이며 일반적인 속도 개선이나900초 기준을 뜻하지 않는다.

통합 helper 한 곳은 `progress_timeout=None`으로 임의의 내부 진행창을 제거한다.
인증 조정 단위 검사의 기본30초, 다른120초 호출, 모든 응답·phase plan·복구·예산
assertion은 유지한다. 승인된 hosted affected job은 기존 외부 timeout의 최대3150초와
공유 마감을, full은 기존3600초 공유 work cutoff와 supervisor·정리 제한을 강제한다.
별도로 실행한 serial runner 자체에는 deadline이 없다. 기존18000/3600초·CI75분·
최대4worker·GC·ID/subTest·소스 상한은 바꾸지 않는다.

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

All hosted test jobs use the exact patch in `RUNTIME_TEST_PYTHON_VERSION` for
`setup-python` and `uv sync`; rolling runner images must not split a campaign
between Python patches. Reconciliation still requires matching environments.

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
or timed-out reports remain nonpassing evidence.

The expanded #838 direct-advice method completed in 1613.82 and 1619.65 seconds.
Give it the first regular-shard-1 method slot, retaining the isolated Whole Front
as the global first entry. This priority-only adjustment preserves the audited
147-method membership, other shards' order, four-worker cap and all deadlines.

An affected-test preflight may
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

### #839 문서 작업 계획 회귀 소스 배정

사용자의 필요시 테스트 소스 예산 확대 허용을 정확한 **148783→149997** 전환에 적용한다. #923과 #928을 포함한 main `eb3b2488`의 `tests/test*.py` raw147688에 기존 ID의 계획·출처·Confirm·저장·UI 회귀와 정책 회귀를 더한 실측 raw148815를 청구한다. 실패에 한해 전문가·synthesis의 정규화된 반환 상태와 준비 실패 예외·필수 문맥 크기를 기존 실패 진단에 덧붙이는103행, 같은 계획의 Global→Part 실제 순차 작성 회귀와 빠른 입력 안내 렌더링 검증을 포함한다. 기존 provider 호출·응답은 보존하며, 순차 Global 작성 경로의 가짜 호출5회만 추가한다. 정책 회귀13행은 이 raw에 이미 포함된다. 대화 전환의 실제 확인 연결·복원과 누락된 캡처 검사40행은 기존 통합 사례에 추가되며 provider 호출 수를 늘리지 않는다. 구간 완료 투영은 순수 값 검사로, 다중 대상의 완전한 목표 보존은 기존 통합 사례의 별도 실제 사용자 과업과 가짜 Lead 호출2회로 확인한다.

기존 helper1154에서 최신 main의 두 fixture 순감소8행과 완료된 #838 활성 도구819행을 빼고, 새 #839 기술 검증 도구854행과 이번 정책 helper1행을 청구하여 helper1182, 합계149997이다. 이전 도구와 증거는 변경 없이 보존하며 #839 실행 도구나 허가로 재사용하지 않는다. 두 도구를 동시에 활성화하면 각각 전체 비용을 다시 편성해야 한다.

이 전환은 기존 2527개 ID·전체 subTest mapping·18000/3600초와 기존 허용 전환을 보존한다. 제품의 task당12회/2라운드 한도도 유지한다. source 실측은 실행 통과나 시간 개선의 증거가 아니며, 현재 리비전의 영향 테스트·전체 CI·엄격한 provider schema 검사와 필요한 실제 기술 호출 결과를 별도로 기록한다.

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
