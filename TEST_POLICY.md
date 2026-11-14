# Test suite cost policy

## 패키지 리팩토링의 사례·소스 계상 — #962 / #963

[#938](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/938)의 독립 사례,
최소 유효 fixture, 실제 RED/GREEN과 개발·최종 검증 분리를 적용한다. 기존 사례의
입력·준비 상태·실제 생산 경계·assertion·부정 사례를 이동표로 보존한다. 독립 조합의
분해는 정확한 ID/subTest 이관을 기록한 범위에서만 허용하며 신규 시나리오는 추가하지
않는다. 알려진 실패를 삭제·완화·skip으로 숨기지 않는다.

소스 비용은 하위 패키지의 `test*.py`를 재귀적으로 세고,
`tests/fixtures/test_source_ledger_v1.json`에 등록한 추출 fixture·검증 support를
합집합으로 한 번만 더한다. 원본 commit·hash·범위로 이동 출처를 추적한다. 기존
미확인 보조 코드 **1,550행**은 중복·종료가 입증되기 전까지 유지한다. 기존 범용
러너·queue·reporting 도구 전체를 새로 청구하는 범위 확대는 하지 않으며, 테스트
내용을 도구로 옮겨 비용을 누락시키는 것은 금지한다. 이슈별 승인 배정을 포함한 현행 상한은 **151,686행**이다.

개발 반복에서는 최소 유효 focused 선택을 사용하고, 공유 계약·fixture·상태·권한
변경은 필요한 실제 소비자와 host 경계로 확대한다. 영향이 불명확하면 전체를 선택한다.
전달 전 affected와 최종 동일 HEAD의 fast·전체 5개 shard·조정을 완료한다. 선택 실행의
inventory 선언과 실제 전체 discovery를 구분한다. 전체 검증 예산·병렬도는 유지한다.

동일 CI 환경에서 전후 1회 비교하고 증가 항목은 각각 총 3회 중앙값으로 재판정한다.
메서드별 시간과 원본 사례별 합산·중복 준비 비용, setup·import/collection·실행·정리·결과
전달 시간을 기록하고 queue 대기는 구분한다. 작은 변경에 무관한 Runtime/전체 prefix가
필요하면 목표 미달이다. historical weight나 시간 추정, 임의의 120초 기준으로 완료하지 않는다.

## Scoped development feedback — Issue #938

An edit/test iteration should run the smallest independent regression selection
that covers the changed behavior. Keep complete integration verification as the
delivery gate; a selected run certifies only its recorded IDs and source.

Issue #938 records each coverage-preserving case split before changing the
canonical inventory or subtest caps. Retain the original assertions, real host
transitions, and full sequential workflows. Use fresh mutable fixtures for each
independent case, and share only proven immutable preparation. The exact current
migration below is the sole new allocation; no failing-test exception is granted.

The canonical suite is a bounded regression sample. Its checked-in inventory is
[`tests/fixtures/test_budget_v1.json`](tests/fixtures/test_budget_v1.json).
The accepted ceilings are **18,000 seconds** of sequential-equivalent wall time
for all five complete shards and **3,600 seconds** for each shard. Each full
worker receives one hour of test execution. Full-shard jobs and the workflow
guard have 75-minute native limits; other jobs retain 60-minute limits. The
complete workflow shares a 75-minute deadline, including setup and shutdown.

이번 리팩토링의 실패 기준은 변경 때문에 발생한 실제 성능 회귀다. 정상 전후 3회 중앙값 비교에서 개별 메서드는 `max(1ms, 수정 전 중앙값의 3%, 수정 전 MAD의 3배)`, 원본 사례 합산·전체는 `max(5ms, 수정 전 중앙값의 1%, 수정 전 MAD의 3배)`를 원인 조사 경계로 사용한다. MAD는 각 표본과 중앙값 차이의 절댓값들의 중앙값이다. 예를 들어 100ms→101ms는 미세한 측정 변동으로 허용한다.

경계를 넘으면 반복성·실제 입력과 준비·I/O·호출·할당·필요시 별도 GC 관측을 확인하고 원인 확인 전 통과 처리하지 않는다. 불필요한 중복 준비나 추가 작업으로 실제 비용이 증가하면 경계 이내라도 수정한다. 정상 clock·GC 설정·실행 순서를 바꾸거나 관측 시간을 빼서 회귀를 숨기지 않는다. 이 오차 처리로 기존 소스·시간·worker·ID·subTest 상한을 늘리지 않는다.

## 패키지 이동의 원본 사례 보존 — #962 / #964

승인된 파일·클래스 이동은 원본 ID와 실제 새 모듈 ID의 일대일 대응으로 검증한다. 이동 전후의 전체 사례 수, 메서드와 assertion·부정 입력, fixture 수명, 각 사례의 subTest cap과 수치 weight를 보존한다. 이동하지 않은 ID도 그대로 유지한다. 등록되지 않은 ID·중복 대상·누락 원본과 시나리오 추가는 계속 거절한다.

대응 기록은 승인 기반의 소스 hash·원본 범위와 새 소유자를 연결한다. 소스 비용은 이동 코드와 추출 support를 중복 없이 계상하며 기존 미확인 1,550행과 전체 상한 151,456행을 유지한다. 옛 테스트 경로용 shim, `Case.__module__` 또는 `id()` 위장으로 이동 사실을 숨기지 않는다. 이 규칙은 런타임 저장 형식의 이행·호환 기능을 추가하지 않는다.

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

## #998의 공급자 계약 소유자 분리 소스 배정

사용자 대화의 `승인`과 [#998 승인 기록](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/998#issuecomment-6872085271)에 따라 이 이슈 source **+170행**만 정확한 **151516→151686** 전이에 적용한다. #1021의 별도378행이나 미승인 #1022 배정과 합산하지 않는다. 이후 다른 main에서 적용하면 실제 source·cap·역사 checkpoint 비용을 다시 확인하며 자동 증액하지 않는다.

선행 #1020 통합 main `65ad7c27`의 raw148525 + 등록 support1441 + legacy1550에서, 기존 두 Case의 전체1:1 이동과 필수 경계·정책 검증을 위해 **raw+127/support+43**을 배정한다. 구성은 test layout·support50, 중복을 제외한 import5, 새 graph 계약85·기존 기대 set3, packaging5, 이동·정책22행이다. 새 support36행에는 기존 raw helper33행을 중복 없이 한 번 이동하며 legacy1550을 절감하지 않는다. 실제 최종 counter는 raw148652 + support1484 + legacy1550 =151686을 넘지 않아야 한다.

기존2544개 ID 중 원본 schema95행·response146행 두 Case만1:1 이동하며 cap0/4 및 전체 cap합8690, schema1,18000/3600초, worker profile/상한을 유지한다. 정확한 이전 cap52d0575e…→새 cap2823d24a… 전이와 닫힌 source receipt를 검증한다. stage/hash·default API·기존 opt-in2값과 원래 모든 부정 입력을 보존하고, 새 before998 checkpoint는 명시적 opt-in3값으로만 제공한다. 범용 `suite_budget.py` guard의 실제16행은 기존 범용 도구 계상 범위 밖으로 별도 공개하며 테스트 내용을 그 도구로 옮기지 않는다.

정상 cold 선택3회에서 재현한 초기 후보의 지연은 별도 기록으로 보존한다. 실제 factoring 함수와 설명 공유 함수를 해당 도메인 소유자로 이동하고 기존 공개 경로를 유지한다. 새 graph edge1행은 이번 PR의 신규 testmodule docstring1행만 제거해 상쇄한다. 기존 도메인 import의 header 이동은 원래 준비 단계의 보존이며, 인위적 warmup·clock 변경 없이 import/collection·Case·전체 CLI 비용을 함께 기록하고 동일 cold 선택과 최종 전체 검증을 다시 수행한다.

카탈로그의 빠른 첫 검증은 기존 selected-material Case의 literal `input_source_id` deep equality이며 전체 catalog·typed decode·비JSON 복구·giant의 확대를 대신하지 않는다. 같은 source 결함의 실제 RED/GREEN·최종 source의 정상 전체 CI·각 메서드/원본합산/전체 시간과 source/cap/graph/packaging을 검증한다. 이 배정은 기능·외부 format·저장 의미 변경이나 실제 성능 회귀를 허용하지 않는다. 실제 source/행 형식이 계획을 넘으면 검증 삭제·누락·임의 증액 없이 재계획한다.

## #1005의 세션 소스 Workspace 분리 소스 배정

사용자가 승인한 #1005 소스 +25행만 정확한 **151431→151456** 전이에 적용한다.
선행 #1004 통합 main `7c9d930d`의 raw148445 + 청구 support1436 +
미확인 legacy1550 =151431에서, 새 모듈·패키징 검증9행과 기존 예산 정책
Case 검증15행·기존 historical ceiling 복원1행을 더해 실측 raw148470을 청구한다.
같은 2544개 전체 cap mapping과 digest, schema1, 18000/3600초가 일치해야
소스 전이만 허용한다. 인접한 기준·상한이나 schema·시간·ID/subTest 변경은 거절한다.
기존 준비·assertion·부정·fixture 수명과 worker/profile/weight/receipt는 유지한다.
이미 소비한 #1004 예약2행은 다시 더하지 않는다. 별도 승인 #1020의 +60행과
미승인 #1022 배정은 이 전이에 전용하지 않는다. 실제 추가량이25행을 넘으면
추가 권한을 추정하지 않고 범위와 비용을 다시 확인한다.

## #1020의 협업 결과 게시 분리 소스 배정

사용자가 별도로 승인한 #1020 소스 +60행만 현재 main `a1fba1a3`의
**151456→151516** 전이에 적용한다. 원 승인 기준151431→151491의 증가량60을
#1005의 별도 승인25행이 이미 반영된 기준에 적용하며, 예약2행이나 다른 이슈의
배정을 더하지 않는다. raw148470 + 청구 support1436 + 미확인 legacy1550에서
기존4개 Case의 실제 이동·준비 코드와 불필요 import 제거21행, 패키징 검증1행,
모듈 경계 검증15행, 기존 예산 정책 Case 검증18행, ID 이동 검증·역투영 결과
공유 support5행으로 raw148525 + support1441 + legacy1550 =151516을 청구한다.

2544개 ID와 subTest 상한 합8690은 유지한다. 실제4개 Case만 1:1 이동하며
전체 cap-map digest는 `ce4382b4bac0778363a7ea7ca90c5c81be63c89077b23abcc4dedbbc170167d9`에서
`52d0575e721cf829d7ca7cc0409cd28861ac1db33b587b684d4611aff6df728c`로 바뀐다.
이 정확한 소스 전이와 폐쇄된4개 ID 이동, schema1 및 18000/3600초가 모두
일치해야 허용한다. 인접 기준·상한, 소스만 증가한 동일 최종 ID 구성,
누락·추가 ID, cap·기존 이동 이력·schema·시간 변경은 거절한다.
크기·cap 합·서로 다른 key의 필요조건으로 명백히 틀린 구성을 먼저 거절하고,
같은 호출에서 이미 계산한 전체 digest를 재사용한다. 실제 이동 결과의 독립 hash·전체 일치와 매번 읽는
영수증 파일의 무결성 검사는 유지하며, 사전 계산 값 자체에 승인 권한을 주지 않는다.
Fixture 준비는 기존 역방향 검증에서 확인한 legacy·게시 전 cap-map을 함께 받아
중복 파일 해석만 공유한다. Case 준비2행 감소와 support2행 증가를 모두 위 비용에 계상한다.
예산 Case의 실제 전체 collection은 기존 fresh census로 준비한다. Runner는 이
원래 객체를 실행 결과의 수명 안에서 공개 API로 전달하며, Case는 같은 partition
검증에 재사용한다. 전달이 없는 실행은 기존 discovery를 수행한다. 전역 cache나
테스트 내용의 도구 이관 없이 중복 구성만 제거하고, full/subset provenance와
원래 객체·fixture·정리 수명 및 모든 assertion을 유지한다.
기존 Case·assertion·부정 입력·fixture 수명, weight와 실행 정책 및 worker 한도는
유지한다. 클래스 분리에 따른 기존 hosted 대상162개의 재분할은 각 소스의
고정 manifest로 검증한다. 실제 추가량이60행을 넘으면 범위와 근거를 다시
확인하며, #1022의 미승인 배정이나 다른 이슈의 승인량을 전용하지 않는다.

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

For Ready PR runtime/test/dependency changes, run the fast tier and all five full
shards. Draft pushes run static validation only; exact affected runtime evidence
is requested explicitly through the existing manual route. `test:full` overrides
Draft mode. A Draft notice is not runtime evidence or integration readiness.

Pure documentation-only Ready or complete merge-group changes remain static.
Ready base/unknown edits and test-policy label events expand conservatively.
Body/title and unrelated-label events use isolated metadata notices; they neither
cancel source validation nor overwrite the full runtime/reconciliation check names.

Before Ready, runtime work requires its affected exact IDs/modules. Final delivery
retains complete, disjoint full reports, source/environment identity, per-ID caps,
source cost, timing, budget and cleanup gates. Failure, cancellation, interruption
or an unexecuted selection is not pass. Historical timing weights are estimates.


### Actions의 지정 영향 검증

`pr-validation` 수동 실행의 `validation_scope: affected`와 줄바꿈으로 구분한
`test_ids`로 정확한 정규 ID를 선택한다. 잠긴 Python3.11·ARM64 4CPU 환경에서
기존 runner의 반복 `--id` 인자로 실행하며 선택 인자·JSON 결과·로그·하드웨어를
`runtime-affected` artifact에 보관한다. 입력을 shell 코드로 평가하지 않는다.
이 부분 검증은 전체5개 shard의 완주·결과 조정·예산·정리 증거를 대신하지 않는다.

## Dedicated whole-front shard

The current approved partition has five selections:595/715/582/649/1 tests.
Class-LPT retains whole-front alone in5/5. The explicit938 coverage-preserving
ID/class migration and estimated weights below account for the repartition;
no old scenario/assertion is removed. Further inventory changes require their
own accepted accounting rather than silently expanding an existing long method.

The combined 18,000-second ceiling is derived from five
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

The mixed SpecialistRoutingTests class has two hosted contract methods and eight
explicitly frozen serial methods. Eligibility requires exact coverage by these
disjoint lists; a new or missing method still fails before execution.

Only hosted full shards opt into the fixed `audited-162-v6` profile: 162
fixture-audited methods across 21 classes. The profile includes the eight
provider-timeout recovery, ten context-recovery and thirteen interactive Lead
adapter methods audited after v40's measured serial bottlenecks. The #938 case split retains class-LPT and isolates whole-front, with the exact
new inventory and estimated weights described below. Each shard retains
canonical selected-ID order. Noneligible tests run once
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

사용자의 필요시 테스트 소스 예산 확대 허용을 정확한 **148783→150604** 전환에 적용한다. #923·#928·#930을 포함한 main `3fea15d6`의 `tests/test*.py` raw147845에 기존 ID의 계획·출처·Confirm·저장·UI 회귀와 정책 회귀를 더한 실측 raw149302를 청구한다. 실패에 한해 전문가·synthesis의 정규화된 반환 상태와 준비 실패 예외·필수 문맥 크기를 기존 실패 진단에 덧붙이는103행, 같은 계획의 Global→Part 실제 순차 작성 회귀와 빠른 입력 안내 렌더링 검증을 포함한다. 현행 native 응답 작성 함수로 바꾼 네 양성 fixture와 같은 첫 QUESTION의 선택·생성 계약을 직접 검사하는 추가58행도 raw에 포함하며, helper를 따로 늘리지 않는다. 순수 계약 검사도 공개 BoundContextProviderCallRequest로 native 분기를 사용하도록 맞춘 추가4행을 raw에 포함한다. 실제 선택 v9 계약의 양성·음성 경계와 공유 Global script를 검사하는 추가100행도 raw에 포함한다. 실제 task 제출 ID와 준비 검사 청구 태그를 구분하고 전체 비용·한도를 유지하는 추가20행도 raw에 포함한다. 기존 provider 호출·응답은 보존하며, 순차 Global 작성 경로의 가짜 예정 호출4회를 추가한다. 현행 v9의 네 불필요한 SELECT를 제외한 같은 업무의 예정 총22회는 원래 계획20회와 별도 목표2회를 구분하며 실제22회 통과를 주장하지 않는다. 정책 회귀13행은 이 raw에 이미 포함된다. 대화 전환의 실제 확인 연결·복원과 누락된 캡처 검사40행은 기존 통합 사례에 추가되며 provider 호출 수를 늘리지 않는다. 구간 완료 투영은 순수 값 검사로, 다중 대상의 완전한 목표 보존은 기존 통합 사례의 별도 실제 사용자 과업과 가짜 Lead 호출2회로 확인한다.

읽기 전용 검토의 필수 입력 배정 수정1행과, 실제 NEW_TASK에서 출처 과업의 의도를 새 과업에 상속하지 않는 순수 경계 검사25행도 raw에 포함한다. 기존 ID 안에서 양방향 의도 전환과 RESUME·Revise·동일 ID·잘못된 참조·영수증 누락을 검사하며 provider 호출은 추가하지 않는다.

v9의 정상 후속 전송에 전체 입력이 반복되지 않는 경우를 검증하는50행도 raw에 포함한다. 테스트 provider의 invocation별 입력 초기화·동일 invocation 내 보존2행과 기존 K의 실제 fixture bookkeeping·현재 토큰·상관관계·READ bytes·새 invocation 거절 검사48행이다. 기존 세 SEARCH/READ/result 계약과 가짜 호출3회는 재사용하며 새 HostRuntime·workspace·schema context나 제품 호출을 추가하지 않는다. 현재 관찰은 그대로 사용하고 원래 작업 material만 정확한 현재 토큰으로 조회한다.

새 과업 작성 준비의 전이 증거를 검증하는72행도 raw에 포함한다. Global 추천 callback의 동일 본문을 공유해 L과 K가 함께 쓰는 추출3행과, K의 현행 v9 추천 findings·typed decode·원래 USER source/assessment 확인12행, 실제 typed USER/transition 기록의 source·target·step·owner·batch 정상/거절 경계57행이다. 기존에 관찰된 계약과 값 fixture를 재사용하며 추가 schema context·가짜 물리 호출·HostRuntime·workspace·ID·subTest를 만들지 않는다. 구성한 기록은 host admission·READ 자격·전체 이력 replay의 증거가 아니다.

기존 helper1154에서 최신 main의 두 fixture 순감소8행과 완료된 #838 활성 도구819행을 빼고, 새 #839 기술 검증 도구974행과 이번 정책 helper1행을 청구하여 helper1302, 합계150604이다. 이전 도구와 증거는 변경 없이 보존하며 #839 실행 도구나 허가로 재사용하지 않는다. 두 도구를 동시에 활성화하면 각각 전체 비용을 다시 편성해야 한다.

#839의 provider 기술 검증 도구에는 선택된 문서의 v9 계약을 직접 확인하는 두 사례120행을 추가했다. 기존 QUESTION 두 사례의 SELECT→GENERATE4회와 구성된 post-READ v9 두 사례의 단일 호출2회로 최대6회를 준비한다. 구성된 사례는 실제 READ·사용자 승인·HostApply 증거가 아니며, 이 소스 배정은 실행 허가나 큐 예외를 부여하지 않는다. 직렬1프로세스·호출당900초·배치3600초를 유지하고 실제 실행에는 새 소스별 admission과 필요한 큐 예외를 별도로 확인한다.

main #927(`aefbe76c`) 통합 후에는 같은 원래 회귀를 보존하면서 upstream의 raw 소스가226행 줄어 raw149076이 됐다. §17의 공통 새 작업 판정·실제 append/이력 projection 회귀37행을 더한 현재 raw는149113이며 helper1302와 합쳐 **150415**다. 기존 상한150604는 유지하고 남은189행은 기존 배정 안의 여유다. 이번 정산으로 상한·ID·subTest·시간 한도를 늘리지 않는다. §18에서는 새 append/replay block만 기존 순수 이력 projection ID로 옮겨 K37행을 줄이고 context69행을 더했다. 순증32행 후 현재 raw149145 + helper1302 = **150447**이며 같은 상한150604 안에157행이 남는다. 원래 계약/native-frame/predicate 검사는 유지하고 실제 append/replay 및 미래 control 거절만 독립 실행한다. 과거0.044651초는 selector 선정 근거이며 현행 실행 시간·통과를 대신하지 않는다. 후속 순수 fixture에는 현행 이력 계약에 필요한 PREPARED/SUBMITTED Lead accounting 기록 두 건을 추가했다. 하나의 구성된 호출 ID와 spent1·tail7/5를 검증하는3행 후 현재 raw149148 + helper1302 = **150450**이며 상한150604 안에154행이 남는다. 이 기록은 실제 backend/provider 호출·HostRuntime·새 schema 준비를 실행하지 않는다. 새 회귀는 기존 K의 typed 값과 준비된 계약을 재사용하고, 실제 공개 append 검증과 history projection 및 미래 control 거절을 확인한다. 추가 schema 준비·HostRuntime·workspace·가짜 provider 호출은 없으며, 구성한 이력의 통과를 실제 HostRuntime 실행이나 provider 성공으로 보고하지 않는다.

§19에서는 확인된 작성 범위로 산출물 목표를 생성하고 같은 출처 법칙으로 저장·재생하는 회귀를 기존 순수 이력 ID에 추가한다. 원래 단언을 유지하면서 두 실제 목표 생성 메서드, 구성된 Confirm/Started와 준비 결과의 공개 append/full projection, 원래 목표와 선택 노드 자체 목표의 구분 및 잘못된 출처·노드 거절을 검사한다. inline 무상태 소유자는 원래 production 메서드와 읽기 전용 기록만 사용하며 추가 HostRuntime·workspace·schema 준비·backend 호출은 없다. 원래 AST prefix와 다른 메서드를 보존한 순증130행/helper0 후 현재 raw149278 + helper1302 = **150580**이며 기존 상한150604 안에24행이 남는다. 구성된 기록 검증을 실제 provider·Confirm/Apply 권한 실행이나 전체 통합 통과로 주장하지 않는다.

§20의 동일 후보 입력 재사용 회귀에는 현재 순수 이력 ID의 실제 typed 참조·binding과 검증 순서 spy를 사용한38raw를 추가한다. 누락 추가·정확한 재사용·유효한 불일치5종·충돌 중복·검증 우선 전달을 유지한다. spy는 데이터 구성과 delegation만 검증하며 실제 host 권한 통과 증거는 아니다. 필요한 정책 예외 회귀10raw와 정책 helper2행도 함께 청구해 **raw149326 + helper1304 =150630**이다. 기존 여유24행을 사용한 뒤 남은26행을 사용자의 필요시 예산 확대 허용으로 배정하여 소스 상한만 **150604→150630**으로 늘린다. 기존 허용 전환을 보존하고 **148783→150630**, **150604→150630**의 정확한 두 쌍만 추가한다. 전체 ID·완전한 subTest mapping·18000/3600초가 동일해야 인정하며 임의 기준/상한의1차이 증가를 거절한다. 새 ID/subTest·schema/HostRuntime/backend 호출·제품12회/2라운드·조건부22회·native 실행 허가는 늘리지 않는다. 최종 동적 결과와 전체 CI는 별도로 검증한다.

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

### #839 같은 준비 요청의 후보 자료 재사용 진단 배정

Part 준비의 호출 수 단언은 예상1회를 유지한다. 반환 kind/message와 기존 원래 예외
관찰기를 해당 await에만 추가한다. 진단은3행이고 새 테스트 ID·subTest·provider 호출이나
HostRuntime/schema 준비를 추가하지 않는다. 실제 라우팅의 회귀는 기존 문서 작업 통합
테스트로 검증하며, 변경되지 않은 resolver 단위 검사를 그 증거로 대신하지 않는다.

사용자의 필요시 예산 확대 승인을 정확한 **150630→150645**에 적용한다.
진단3행과 정책 회귀10행으로 raw149339, 정책 helper2행으로 helper1306이며
현재 전체 상한은 **150645**다. 기존 모든 허용 이력은 유지한다. 원격 main의148783과
직전 배정150630에서150645로의 정확한 두 전환만 추가하며, 각 출발값·도착값 ±1은
거절한다. ID2527개·완전한 subTest mapping·18000/3600초·제품12회/2라운드는 바꾸지 않는다.

이번 예산은 후보 자료의 현재 요청 내 재사용과 실패 관찰을 위한 소스 배정이다.
통합 검증·최종 전체 CI·Whole Front·재조정·기술 provider·리뷰·완료 조건을 대체하지 않는다.

### #839 대화 간 원래 계획 보존과 최신 main 반영 배정

기존 순수 이력 ID에 두 대화의 typed 준비·저장·재생·세션 계획 조회를69행 추가한다.
제안서에 같은 원래 계획을 보존하고 자체 목표의 계획은None으로 남는 정상 사례와,
제안서 계획 누락 시 로컬 append 거절을 확인한다. 새 ID·subTest·backend·HostRuntime·
workspace·schema 준비는 없다. 구성된 Global 노드 사례는 대화 소유 경계의 보조 증거다.
실제 Part/선행 후보·제안서 builder 실행은 기존 전체 문서 작업 통합으로 검증한다.
known-goal fixture에 없는 planning-goal USER-ref 거절 조건을 임의로 주장하지 않는다.

최신 main#934 반영의 raw44행을 중복 없이 포함한다. 필요시 확대 승인을 정확한
**150645→150770**(+125)에 적용한다. main44+회귀69+정책 회귀10행으로
raw149462이고, 정책 helper2행 후 helper1308이며 전체 상한은 **150770**다.
기존 이력을 유지하고 정확한148783→150770 및150645→150770만 추가한다.
각 base/target ±1은 거절한다. ID2527개·전체 subTest mapping·18000/3600초·
제품12회/2라운드와 조건부22회 통합 시나리오는 변경하지 않는다.
최종 전체 CI·Whole Front·재조정·기술 provider·리뷰·완료 조건을 대체하지 않는다.

### #938 현재 독립 검증과 최종 통합 계약

최종 A/B/C 편성은 기존2527개와 신규17개를 합쳐2544개 ID를 검증한다.
원래2527개와 앞서 분리한16개를 모두 유지한다. 복구 cap은 기존12에서 retained
core2와10개 조합의 각1로 이동하며, 전체 조합의 모의 제출40회를 보존한다.
다른 기존 cap은 그대로이며 신규 C와 B도 cap0이다. 원래 cap-map SHA256은
`e8d0ec716f086485451822317114c27efd70bd895fdcad012a9410ff406aff18`이다.
정확히 고정된17개 ID 이관과 결합한 **148783→151369 /150770→151369**만
허용한다. 이전에 승인된 pre-938 전환도 보존한다.

현재 소스 청구는 **raw149819 + 수동 helper1550 =151369**로 상한151369
이내다. private workflow-context 검증181행도 수동 helper에 포함한다.
budget 도구의 raw 검사와 이 추가 청구를 구분해 기록한다. 임의 ID·cap·상한
증가나 알려진 실패의 삭제·skip을 허용하지 않는다.

`SelectedTaskMaterialCodecTests`는 불변 capture2개를 실제 생산 formatter와
실제 provider wire projection에 통과시켜 독립적으로 적은 최종 두 행과 비교한다.
Runtime·workspace·backend·schema 생성·provider를 준비하지 않는다. 기존 단일
행 기대값은 RED로, 올바른 두 행은 GREEN으로 실제 선택 실행하고 결과까지의
큐·setup·import/발견·검사·정리 시간을 구분한다. 임의120초 기준은 두지 않는다.
작은 계약 수정에도 긴 Runtime prefix가 필요하면 이번 목표 미달을 유지한다.

복구10조합은 각각 fresh fixture로 선택한다. 기존 순수 codec과 여섯 offer·세
provider-fixture 경계, 실제 문서20회 순차 연쇄와 별도2회 목표, 작은27개
assertion과 전체 census·catalogue·부정 검사를 유지한다. 문서 fixture의 추가
readiness2회·Runtime1개는 실제 비용에 포함한다.

현행 v3는 DRAFT1round 뒤 남은 round를 최종 검토용으로 보존한다. 역사 자료
회귀는 이전 revision의 draft_sources 입력을 실제 pending 추천에서 검증하고
공개 REJECT로 닫는다. 새 task에는 이전 DRAFT와 거절 답변을 material로만
캡처하며 foreign draft_sources는 비운다. 원본 event·digest→capture→전달 입력과
새 task의 집필·artifact·검토·Apply를 실제 실행한다. 이전 task의 제출·예산은
초기화하지 않는다. 기존 O의 ‘41+readiness2’를 전체 호출수라고 쓴 설명은
잘못된 부분 집계다. 분리한 A는 실제764.131초/42회, B는320.964초/19회로 통과했다.
B는 seedless 독립 fixture의 신규 cap0 ID로 분리한다. 필요한 실제 재시작은 유지한다.

큰 fixture에만 이력 성분512KiB를 설정하고 종료 시 복원한다. production
기본256KiB·schema/payload 각512KiB·합산1MiB는 유지한다. 실제 제출 accounting의
크기를 검사하고 다중 session은 call ID multiset을 backend 기록과 대조한다.
이는 실제 model 수용량의 증거는 아니다.

Hosted profile은 `audited-162-v6`,162개/21클래스이고 serial2382개다. 기존161개와 원래147개
소속·상대 순서, mixed serial8개, class-LPT·5shard·4worker·18000/3600초를 유지한다.
완료·source/환경 확인을 마친 관측만 비용에 쓰고 실패 prefix는 완료 비용으로
쓰지 않는다. 최종 같은 HEAD의 전체 CI·조정·정리·리뷰를 요구한다. 부분 선택·
Draft·metadata 결과는 전체 통과가 아니다. Whole Front가 실제 timeout이면 중단한다.

아래 숫자 편성·진단 기록은 당시 source의 이력이다. 현재 편성은 이 절과 최종
실제 보고서를 기준으로 하며, 변경별 선택 규칙은 계속 적용한다.

단계 C 실측에서 Actions 등록→보고서 업로드는 corrected RED25초/GREEN29초,
workflow 전체 종료는39초/69초였다. 기존 O 실패의 보고서까지는980초였다.
첫 RED의 wire 필드명 기대값 오류를 수정한 뒤 corrected RED에서 행 수 오류만
확인했고 GREEN은 assertion 한 줄만 바꿔 통과했다. source/환경 검증도 유효하다.
이는 작은 계약 검사의 개선이며 전체 통합 작업량의 속도 비교는 아니다.

최종389클래스 timing 편성은 실제 완료된 per-method 관측의 serial4배/hosted1배
occupancy 휴리스틱을 사용한다. A/B/C와 N의 source·환경 확인을 마친 통과 보고서를
이관하며 실패 prefix는 사용하지 않는다. 클래스 LPT·격리·고정 순서 FIFO는 유지한다.
새 weight fixture SHA256은
`279353794dae30ba8b1b3cba410af89cc40259e3a27986d7ea0c59ea55ed304c`다.
혼합 source 관측의 모델에서 shard2 body는3690.454초, 과거 full residual을 더한
시나리오는3725.151초로3600초를 넘는다. 이는 미래 실행의 상한이나 통과 증거가
아니며 실제 초과 위험이다. 숫자를 맞추는 조정 없이 최종 전체 CI의 실제 완료·
미실행·timeout으로 판정한다. 해당 위험은 PR에도 기록한다.

계산된 현재 partition은686/624/656/577/1개이며 실제 CI 완료 여부와 구분한다.

첫 최종 전체 실행은2544개를 모두 완료했고 timeout·미실행은 없었다. worker
wall 최대3143.315초·순차 환산13485.739초로 고정 예산 안이었다. 세 assertion
실패는 positive QUESTION의 nullable planning_target 누락2곳과 공개 함수의
정확한 context.py 소유 기대값 누락1곳이었다. 이 세 fixture만 정합시켜 최소
세 ID를 먼저 검증하고, 그 뒤 같은 최종 리비전의 전체 CI를 요구한다.

### #938 independent-case inventory and source allocation history

The current split retains all 2,527 original IDs and adds fifteen IDs: ten
submitted-recovery combinations, two codec-contract cases, two document cases,
and one small factoring/ref case. The recovery cap moves12→2+ten×1; other old
caps stay exact, and all five nonmatrix new caps are0. Total caps are conserved.

The exact original cap-map SHA256 is
`e8d0ec716f086485451822317114c27efd70bd895fdcad012a9410ff406aff18`.
Only the complete recorded mapping with source pairs **148783→151083** or
**150770→151083** and unchanged18000/3600 limits is admitted. All pre938 approved
branches remain unchanged. The intermediate twelve-ID iteration is retained in
its immutable source/history; the current guard uses the final fifteen-ID map.

After the main #939 integration, the current charge is **raw149526+helpers1535=151061**,
within the allocated151083 ceiling with22 lines of slack. The upstream guidance
adds18 assertion sites while folding other lines; all old assertions remain. This includes
181 counted lines of the private, maintained workflow-context validation harness.
It grants no unrelated ID/source expansion or failing-test waiver.

The ten recovery combinations retain forty mocked submissions and use fresh
mutable fixtures without the unused default Runtime. Pure codec stays serial;
its six offer bindings and three fixture invocations remain in selected contracts.
The real twenty-call document goal chain and two-call independent goal remain.
The separated document fixture adds two honest baseline-readiness setup calls
and a needed Runtime. Whole-suite speedup is not inferred from smaller selections.
All twenty-seven small factoring assertions move together, with the later full
catalogue/census/negative checks retained. Positive nullable QUESTION fixtures are
explicitly corrected; negative cases and authority rules remain unchanged.

Hosted profile is audited-161-v5:161IDs/20classes, at most4workers. The old147
memberships and their relative launch order remain. Pure codec and small factoring
stay serial. Historical weight transfers are source-responsibility estimates,
not measured CPU/time shares; the codec estimate includes its observed baseline.
The387-group partition is **595/715/582/649/1** and covers2542IDs exactly once.
Actual source-bound execution and complete final reports establish passing gates.

### Change-driven development selections

Use repeated canonical `--id` selectors or the manual `affected` input; do not
filter a parent method's subTests and report that parent as fully verified.

| Changed responsibility | First selection | Expand when |
| --- | --- | --- |
| One fixture or assertion | Its exact method ID. | A shared helper changes: include every affected consumer. |
| One submitted-failure combination | Its `LeadSubmittedFailureRecoveryTests` method, retaining real closure and restart. | Shared submission, correction, taint or accounting changes: run all ten combinations, the retained pre-submission core and affected context lifecycle checks. |
| Scope/review codec | The retained pure codec ID and its direct typed consumer. | Encoded shape, current-offer or transport changes: add the separated QUESTION and selected-v9 contract IDs and relevant producer/decoder checks. |
| Small schema factoring/ref rule | `LeadSchemaFactoringTests.test_small_factoring_annotations_and_owner_refs_are_exact`. | Shared rule owner, schema family, repair or wire vocabulary changes: include the full schema catalogue and affected Lead/specialist/synthesis producers. |
| Apply or default-budget boundary | Its matching-Apply or default-budget method. | Document identity, candidate inputs, Confirm or chronology changes: include the real document workflow and affected public history/replay/restart checks. Binder spies cannot replace the actual host transition. |
| Display of unchanged values | The affected view/controller checks. | Controls, DTOs, codecs or forwarded selections change: include their source and host boundaries. |

For multiple responsibilities, deduplicate the conservative union. For unknown
impact, widen the selection rather than infer safety from import neighbors.
If no existing focused witness reaches a new host path, implement that witness
through valid public setup; a pure helper pass cannot certify the missing path.
Record selected/unselected IDs, relevant source changes, setup/queue/execution
time and actual fixture submissions. Final full-CI requirements are unchanged.

### #938 최신 main 통합 소스 편성 — 2026-11-07

최신 main `5d8e5ab7`(#943)을 부모 #839 `a52321a3`와 자식 #938에 반영했다.
이 통합은 기존 테스트 ID·subTest 상한을 바꾸지 않고 raw 소스만29행 늘린다.
혼합 클래스의 정확한 직렬8개 선언과 실제 inventory 회귀를 포함한 현재 편성은
raw149564+기존과 #938 helper 청구1547=151111행이다. 이전151083에서
새 상한151112로29행만 늘려 기존1행 여유를 유지한다. #938 전용 전환은
동일한 cap-map·18000/3600초에서 정확한148783→151112 또는150770→151112만
허용한다. 앞 절의151083 편성은 이전 리비전의 기록이며 이 통합에서 대체한다.

### #938 기준 결함 통합 검증의 정확한 소스 편성 — 2026-11-08

현재 계약의 공개 목록·필드·정책 해시, 세션 이력의 소유자 정렬, 유효한
생성 계약 baseline, 불변 목표의 생성/이력 거절, 합법적인 DISCUSS 검사를
보완한 테스트 변경은 raw42행이다. raw149606+기존 청구 helper1547=151153행을
기준으로 상한을151112에서151154로42행만 늘려1행 여유를 유지한다.
#938 전용 전환은 정확한148783→151154 또는150770→151154에서 동일한
cap-map과18000/3600초를 요구한다. 앞선151083·151112는 이전 리비전 기록이다.
새 ID·subTest 상한·worker·호출/라운드 한도는 늘리지 않는다. Runtime의 기존
두 메서드 결함 보정과 기본 정책 복사본 동기화는 테스트 소스 청구와 구분한다.

첫 전체 실행에서 복구10개는1478.610초였고 기존 클래스 비용 추정은32.083초였다.
shard3의18개 미완료는14개 미시작과4개 결과 미채택으로 구분한다. 기존 불변
리비전7c593e22에서18개를8개 선택 작업으로 모두 통과시켜1577.339초의 메서드
누적 시간을 확보했다. 배치 보정은 직렬 시간×4+호스트 병렬 시간×1의4worker
점유 비용 추정과 실제 수정 사례의 완료 비용을 사용한다. 이는 실제 실행시간의
보장이 아니며 최종 전체 CI를 대체하지 않는다. 비용 보정으로5shard·4worker·
기존147개 상대 실행 순서·161개 병렬 대상과 기존 시간 제한을 변경하지 않는다.

### #938 VCS 설치 fixture의 소스 편성 — 2026-11-08

고정 VCS 소스를 원본 object store로 직접 재지정하던 설치 fixture는 동일 pin의
지연 fetch를3641회 반복한 뒤 인자 길이 한도에 도달했다. 기존 pin export 후
독립적인 depth-one·unfiltered bare mirror를 만들고 완전한 객체 연결 및
promisor/alternates 비의존을 확인한다. 실제 pip VCS 설치·공개 URL·pin·세 설치
방식은 유지한다. 이 보완의 raw22행으로 raw149628+helper1547=151175행이며,
정확한 상한은151176으로1행 여유를 유지한다. #938 전용 전환의 현재 목표도
151176이며 허용 기준148783/150770·cap-map·18000/3600초는 그대로다.
이전151154는 수정본63e40f41의 기록이다. 진단 메시지 추가는 raw0행이다.

### #938 이전 리비전 거절 fixture의 합법적 예산 편성 — 2026-11-08

이미11회 호출·2라운드를 쓴 과업의 RESUME은 provider 제출 없이 거절한다.
뒤의 양성 작성은 실제 NEW_TASK 결정·새 ID@1과 별도 예산으로 실행한다.
이전 리비전 음성4회 호출은 이미 있던1라운드 과업의 실제 RESUME에 배치해
같은 과업의 오래된 초안 거절을 유지한다. 원래40회 예정 호출·역할·출처·
Apply 부정 검사를 보존한다. 이 정합성 보완의 raw40행으로 raw149668+
helper1547=151215행이며 상한151216으로1행 여유를 유지한다. 전용 전환은
정확한148783→151216/150770→151216와 기존 cap-map·18000/3600초만 허용한다.
이전151176은88ce6405의 소스 편성 기록이다.

#938 추가 원인 관측은 응답을 변경하지 않는8건 한정 forwarding observer를 두 실패 사례에 붙인다. 호출 전 쓰기 거절은 공급자 응답을 예약하지 않고 실제0회 제출을 검사하며, 독립 DISCUSS는 SINGLE 모드의 기존2회 응답을 사용한다. 도달 불가능했던 callback의2개 검사는 실행된 증거로 집계하지 않는다. raw149689+helper1547=151236이며 정확한 상한은151237이다. ID·subTest·전체 cap-map·18000/3600초 제한은 유지한다.

#938의 공유 요청 생성 거절은 기존 traceback에서 한도·사용 호출 수·최소 필요 호출 수와 lineage 유무만 추가 관측한다. 상위 고정 실행 프레임은 cap·문서 노드 유무·typed projection의 호출/라운드 정수만 기록한다. 반환·예산 검사·provider 제출은 변경하지 않는다. 이 임시 관측20행을 포함한 raw149709+helper1547=151256의 상한은151257이다. 기존 ID·subTest·cap-map·18000/3600초 제한은 유지한다.

#938의 이전 초안 검증은 초기 pending D@1 제안을 provider-free REJECT한 뒤 공개 RESUME D@2를 먼저 수락한다. 기존 specialist/synthesis4회는 그 제안의 Confirm으로 이동한다. 2front+4draft+6미래검토예약=12로 한도를 지키며, 실제 D@2 초안에 대한 D@3의4개 거절 검사를 유지한다. 기존 총40회와 동일한2개 준비 호출, 최종 task10회/1round는 변하지 않는다. 이 순서 보완1행으로 raw149710+helper1547=151257이며 현재 상한151257에 여유 없이 맞춘다. 시간·ID·subTest·cap-map은 유지한다.

최신 main #931의 이전 USER 근거·준비 평가 정렬을 기준 #839에 통합한 뒤 #938에 반영했다. 기존938 독립27검증·복구40제출·패키징 완전 pin 검사는 보존한다. 병합 후 raw149680+helper1547=151227로 기존 상한151257 안에 들어오며, 최종 원인 관측 제거와 완료 비용 편성 시 정확한 최종 상한을 다시 고정한다. 원래147순서·161대상·5shard·4worker·시간 제한은 유지한다.

#938 마지막 소유 관계 관측44행의 편성을 먼저 고정한다. 최신 통합raw149680에44행을 더한149724+helper1547=151271이며 임시 상한151272로1행 여유를 둔다. 기존cap-map/ID/subTest/시간과 원래147순서는 유지한다. 이 helper는 실제 한도·정확한Confirm 제안·unique budget anchor의 scalar 관계만 관측하고 진단 후 제거한다.

#938의 취소된 Draft guard는 GitHub가 needs평가 전에 취소한 완료 job의 이름을 조건식 원문으로 남기는 경우도 exact일치로 식별한다. 알려진 완료 marker존재·completed·나머지 모든job종료 조건은 그대로 요구하며, 부분 job목록·누락/유사 marker·stale attempt·실행중 peer는 완료로 오인하지 않는다.75분 deadline·기존취소/force-cancel예약·check명·concurrency는 바꾸지 않는다. 실제 취소 실행42399269691에서 다른 job종료 후 약61분 잔류를 관측했고, 실제guard JavaScript의 가상시각8사례에서 기존3570초잔류가 해당완료입력에만 즉시종료로 바뀌는 것을 확인했다. 전체테스트통과 증거는 아니다.

#938의 마지막 예산 원인은 과거 비용 오합산이 아니라 미래 준비1+최종검토4의 정상 예약이었다. 사용6인 B는 front1만 남아2-call 그룹을 시작하지 못한다. B2는 정확한0호출/REQUEST_NOT_SUBMITTED/기존scope유지를 검사하고, 원래혼합premise의긍정검증과실제6호출은새구조화선택C@1로옮긴다. 총provider모의호출은증가하지않는다. 임시observer90행제거와정정31행으로raw149665+helper1547=151212, 최종상한151213의1행여유를고정한다. 기존2542ID/전체cap-map/subTest/147상대순서/161병렬대상/5shard/4worker/18000·3600초를유지한다.

main #947 통합 후 raw149666+helper1547=151213에 D06 한 곳의 history512KiB 설정과 기록된 payload 크기 검사19행을 더한다. 최종 raw149685+helper1547=151232, 상한151233이다. production의 history256KiB와 provider/schema/payload/전체 한도는 변경하지 않고, callback·40+2 provider 호출·ID·subTest·시간을 유지한다.

#938 남은C Confirm의첫실패진단34행은반환/큐/provider호출을바꾸지않고정규화된최초실패와기록된validation label만제한적으로관측한다. raw149719+helper1547=151266, 임시상한151267이며진단후제거한다. product budget/counter·ID·subTest·cap-map·147순서·161대상·시간제한은유지한다.

측정된N Confirm실패는세specialist정상/Lead0호출의history269133>262144였으므로응답을늘리지않고그사례도512KiB history allowance를사용한다. N진단제거·공통기록크기검사·D06완료prefix round1/current-step0분리·한정fixture첫bind진단을합친순증6행으로raw149725+helper1547=151272,상한151273이다. 기존ID/subTest/cap-map/provider호출/시간은유지한다.


## #839 후속 revision 연결의 순수 계약 소스 편성 — 2026-11-08

확인한 같은 문서의 다음 revision을 이전 노드 연결과 비교해 거절하는 AC8 결함은
기존 순수 계약 ID에서 먼저 검증한다. Runtime·workspace·schema·provider를 준비하지 않고,
정상 revise/reserved·단계 전환과 정확한 후보 입력 보존, 오래된 출처·다른 과업·문서·
건너뛴 revision의 거절을 검사한다. 최종 영향 검증과 전체 CI는 계속 요구한다.

기존 #938 통합 소스 151369행에 순수 계약 62행만 더해 **raw149881 + helper1550 = 151431**로
편성한다. fixture 상한은 정확히151431이며 기존 기준148783/150770에서 이 정확한 목표로의
전환만 허용한다. 인접 상한·다른 기준·cap-map 변경은 여전히 거절한다. 기존2544개 ID,
전체 subTest cap-map, K의10개 cap, timing fixture, 5shard·4worker·18000/3600초는 유지한다.
