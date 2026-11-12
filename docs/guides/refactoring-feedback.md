# 리팩토링의 개발 피드백과 검증

패키지 리팩토링은 수정에 필요한 최소 검증의 실행 요청부터 실제 실패·성공을
확인하기까지 걸리는 시간을 줄여야 한다. [#938의 사례 접근](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/938)을
[부모 #962](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/962)의 각 전달 단위에 적용한다.

## 사례를 먼저 정한다

코드를 이동하기 전에 원본 ID/사례, 입력, 준비 상태, 실제 생산 경계, assertion과
부정 사례, 새 ID 또는 유지할 통합 ID를 이동표에 적는다. 독립 조합은 직접 선택할
수 있는 사례로 분해하고, 순차 상태 전이가 핵심인 시나리오는 통합 사례로 유지한다.
새 시나리오를 추가하거나 기존 실패를 삭제·완화·skip으로 숨기지 않는다.

순수 codec과 formatter는 실제 생산 함수를 최소 입력으로 검사한다. 불필요한
Runtime·workspace·provider를 만들지 않고 기대값은 결과와 독립적으로 적는다.
문서 binding·권한·Confirm·Apply 변경은 실제 생산 상태 전이를 통과하는 최소 통합
증거를 포함한다. 순수 helper나 binder/spy의 통과는 실제 host 전이를 증명하지 않는다.

mutable fixture·이력·권한은 매번 새로 준비한다. 재사용은 불변임이 확인된 준비에
한정한다. 코드 이동으로 lock·await·호출 수·예외 순서·취소·정리가 달라지지 않게 한다.

## 변경 중에는 정확한 기존 사례를 선택한다

현재 제공하는 정규 ID 선택으로 필요한 검증부터 실행한다. 다음은 기존 순수 계약의
실행 예시이며 전체 검증의 통과 증거는 아니다.

```bash
PYTHONPATH=src python -m tests.runner run \
  --id tests.test_selected_task_material_codec.SelectedTaskMaterialCodecTests.test_two_captures_keep_closed_wire_shape_and_order \
  --json /tmp/selected-task-material.json
```

공유 fixture는 모든 소비자로, wire shape·offer는 관련 producer/decoder로, 상태·권한은
실제 host·history/replay/restart 경계로 확대한다. 여러 책임은 중복을 제거한 합집합을
선택하고 영향이 불명확하면 범위를 넓힌다. 변경 기반 선택은 다음과 같이 실행한다.

```bash
PYTHONPATH=src python -m tests.runner list --changed-from origin/main --json /tmp/focused-selection.json
PYTHONPATH=src python -m tests.runner run --path tests/module_graph_fixture.py --json /tmp/focused-result.json
PYTHONPATH=src python -m tests.runner run --changed-from origin/main --scope affected --json /tmp/affected-result.json
```

변경 기반 기본 범위는 `focused`다. `--path`는 반복할 수 있고 명시한 ID·module·fast
선택은 합집합으로 처리한다. shard와는 혼용하지 않는다. 변경이 없으면 무실행으로
기록하고 잘못된 ref는 오류로 처리한다. 미등록 책임은 전체 선택으로 확대한다.
선택 결과에는 최소 사례·확장 집합·확대 사유와 실제 수집 방식을 남긴다. inventory
선언에 따른 선택 실행을 실제 전체 discovery나 전체 통과로 해석하지 않는다.

공유 테스트 helper의 정적 소비자 선언은 `python -m tests.runner.owner_map`으로
읽기 전용 검증한다. import 관계를 바꾼 뒤에는 `--write`로 갱신하고 diff를 검토한다.
생산 책임의 사례·소비자 경계는 해당 리팩토링 PR에서 명시적으로 등록한다.

## 실제 피드백 시간을 비교한다

대표 사례에서 같은 오류를 발견하는 RED와 올바른 결과의 GREEN을 기록한다.
RED는 대상 assertion의 실패여야 한다. import 오류·timeout·skip은 대체 증거가 아니다.
작은 수정에 여전히 무관한 Runtime 준비나 긴 전체 prefix가 필요하면 목표 미달로 남긴다.

source SHA·환경·선택/미선택 ID·실제 호출 수·실행/미실행과 함께 setup, import/collection,
실행, 정리, 결과 전달 시간을 기록한다. queue 대기는 구분한다. 같은 CI 환경에서
전후 1회 비교하고 증가 항목은 전후 각각 총 3회의 중앙값으로 재판정한다. 사례를
분해하면 원본별 합산과 중복 준비 비용도 포함한다. 수집 시간·historical weight·추정치만으로
단축을 주장하지 않고 임의의 120초 기준을 두지 않는다.

S01의 같은 오류 비교는 수동 `affected` 검증에 위 codec ID와
`compare_feedback_baseline=true`를 지정해 재현할 수 있다. 기본값은 비활성이다.
동일 CI 작업에서 현재 후보를 먼저 측정하고, 생산·기존 테스트 코드가 수정 전인
`45cf084f`를 별도로 준비해 같은 외부 controller로 측정한다. 기준선에 테스트나
helper를 추가하지 않는다. 같은 Python·locked dependency·vendor revision과
controller hash를 확인하고, checkout 준비 시간과 RED/GREEN 명령 시간을 구분한다.
지정한 영향 ID도 같은 호스트의 기준선에서 실행해 전체·메서드 시간을 짝지어 비교한다.
이는 S01의 고정 기준선 비교이며 일반 개발 실행에 매번 추가하지 않는다.

원인이 남은 시간 차이는 수동 `affected`에 `profile_selection=true`를 지정해
기존 선택을 표준 cProfile로 진단할 수 있다. 같은 고정 기준선 비교 옵션과 함께
사용하면 양쪽 함수별 비용을 확인한다. profiler 결과는 별도 artifact로 보관하고
정상 성능 표본·전체 CI 통과 증거에 포함하지 않는다. GC 관측과 동시에 사용하지 않는다.

## 전달 전에 전체 검증을 완료한다

개발 중 선택 검증과 최종 통합 검증을 구분한다. 각 PR의 최종 동일 HEAD에서 fast,
영향 테스트, 전체 5개 shard와 조정을 완료한다. 기존 최대 4 worker, 전체 18,000초와
shard별 3,600초, 테스트 소스 151,431행 상한을 유지한다. 이동한 테스트·fixture는
출처를 추적해 한 번만 계상하고, 하위 패키지 이동으로 비용을 누락시키지 않는다.
현재 기준 실패와 새 실패를 구분하고 미완료·시간 회귀를 완료로 표시하지 않는다.

## 계약 경계 분리와 사례 이동 — S02

계약·payload·순수 planning을 분리할 때는 승인 기반의 원본 소스와 기존 ID·assertion·부정 입력·fixture 수명·subTest 상한을 먼저 고정한다. 공개 import 경계와 도메인별 테스트 파일을 함께 옮기며, 이전 ID와 실제 새 모듈 ID를 일대일로 대응시킨다. 실제 런타임 상태 전환을 확인하는 기존 host 사례도 유지한다.

ID 이동은 사례 수나 검증 범위를 늘리거나 줄이는 허가가 아니다. 기존 수치 weight와 cap은 같은 사례로 이관하고, 등록되지 않은 새 ID는 계속 거절한다. 옛 Python 경로용 shim이나 `Case.__module__`·`id()` 위장은 사용하지 않는다. 추출 fixture와 support도 기존 소스 상한에서 계상한다.

수정 전 측정은 해당 단계의 승인 기반에 고정한다. 수동 CI가 현재 main과 같은 SHA를 거절하면 런타임·테스트가 동일한 문서 전용 체크포인트에서 실행하고, 두 경로의 diff와 소스 hash를 보존한다. S01에 고정된 `45cf084f` 비교 옵션을 후속 단계의 수정 전 기준선으로 사용하지 않는다. 정상 명령의 환경·로그·timeout 조건을 맞추고 진단 profile과 구분한다.

각 서브이슈는 상세 계획과 브랜치 준비 후 런타임·테스트 코드 편집 전에 DRAFT PR을 만든다. 구현과 검증 결과는 그 PR에 계속 갱신하며, 준비가 끝난 뒤 ready for review로 전환하고 관리 리뷰·같은 head의 전체 CI·수동 squash 종료 절차를 따른다.

helper를 외부 공개 이름으로 옮길 때는 원본 global 참조의 심볼 해석도 비교한다. 같은 이름의 인자·지역 변수·comprehension·closure가 새 이름을 포획하는지 `symtable` 또는 bytecode로 확인한다. 생성 함수에는 `make_scope`, `make_entry`처럼 실제 역할이 드러나고 기존 지역 이름과 겹치지 않는 이름을 쓴다. 함수 내부의 지연 import와 mock patch도 각 실제 소유자에 대응시킨다.
기존 Root 메서드를 새 객체로 이동할 때는 모든 self 속성의 출처도 확인한다.
전역 이름과 정규화 AST가 같아도 receiver의 메서드가 존재한다는 뜻은 아니다.
새 소유자의 필드·메서드와 외부 협력자를 구분하고, 기존 공개 콜백은 실제 호출 지점에
명시적으로 전달한 뒤 최소 기존 사례로 실행 경로를 확인한다.

검증 순서는 실제 메서드 수집·원본 대응과 정적 import 점검 뒤, 기존 최소 사례·영향 사례·최종 전체 실행으로 이어진다. 확대 실행에서 발견한 이동 오류는 같은 시나리오·assertion·부정 입력을 유지하며 한 묶음으로 교정한다. 실패로 작업량이 줄어든 실행 시간은 성능 비교에서 제외한다.

선언 이동은 decorator와 module docstring까지 원본 의미를 보존한다. 원본 소스 범위는 decorator의 시작 행을 포함하며, 클래스의 기반 타입·필드·기본값·생성자·frozen/slots와 static/class method·property 형태를 함께 대조한다. 모듈 docstring은 첫 문장으로 유지하고 그 뒤 future import를 둔다.

## S03 검증 준비

S03 #965는 실행·원장·소스 어댑터의 소유 경계를 분리한다. [동결 이동 계획](../design/refactoring/issue-965-moves.json)은 기존176개 정의·53개 공개명·117개 기존 테스트의 대응과 cap을 담는다.
승인 기반은 `a18dfc6b3faa8dc326668c77d0da12441be388f8`이며 이 문서 전용 checkpoint의 runtime·테스트·의존 파일은 그 기반과 같다.
이슈의 상세 계획과 native Ready 확인 뒤 Draft PR을 먼저 생성하고, runtime/test 편집은 그 다음에 시작한다.
Draft 이후 검증 지원을 준비하면 생산·기존 Case/fixture·runner 실행의 동등성과 controller/source hash를 별도로 고정한다. 실제 source privacy의 RED/복구 GREEN과 최소/영향/최종 전체 검증은 아직 수행하지 않았다.


## Apply·Commit 소유와 사례 선택 — #1006

[#1006](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1006)은
검토된 Apply 권한과 pending Commit의 실제 상태·generation pin 수명을
`artifact_transactions`로 이동한다. 구현 전에는 문서만 담은
[Draft PR #1009](https://github.com/Kims-DeveloperGroup/story-writing-agents/pull/1009)를 먼저 생성했다.

`ArtifactApplicationAuthority`는 capability 발급·소비와 Apply·결과 게시를,
`PendingStoryCommits`는 실제 registry·교체·retire·archive 정리를,
`StoryCommitDecisions`는 재준비와 Commit·Reject/Revise를 소유한다.
`PreparedStoryCommit`은 원래 request·service·pin을 보유하며 pin을 None으로 만든 뒤 닫는다.
Root는 기존 진입점을 구성·위임하고, 두 실제 협력자는 같은 registry를 사용한다.

registry의 `observe_for_commit`은 기존 get 한 번과 단락 평가로 관측 객체와 일치 객체를
구분한다. 닫힌 항목도 감추지 않으므로 등록 부재 검사는 같은 잔류 결함을 검출한다.
교체는 원래 관측→retire→조건부 install을 보존하며, 성공 시 caller의 local 소유권을
해제하고 실패 시 기존 finally가 같은 자원을 정리한다. journal readback·lock의
implicit await·예외와 cleanup note 순서를 바꾸지 않는다.

Application 파일 1032행은 같은 request/tail/secret·pin 이전·게시·finally 불변식의
한정된 크기 예외다. registry·prepared resource·Commit 결정은 별도 파일로 분리한다.
공유 결과의 `CollaborationInvocationResult`는 binding과 pin을 실제로 보유한다.
event 함수와 pin cleanup 함수는 각각 구체적인 별도 모듈에서 원래 본문을 사용한다.

Root의 좁은 의미 함수와 기존 공개 이벤트 게시 콜백을 작업별 인자로 전달한다. callback 선택이 작업 진입 시점으로
앞당겨지고 위임 frame·await와 registry tuple이 추가되므로 비용이 0이라고 가정하지 않는다.
이미 닫힌 predecessor의 Python 객체 보유는 registry frame 반환 때 끝날 수 있지만,
명시적인 pin close·map 변경·예외 순서는 보존한다. 실제 회귀는 별도 정상 실행으로 판정한다.

기존 Apply 6개와 Commit 2개는 `tests/artifact_transactions/test_apply_authority.py`,
`test_commit_authority.py`의 같은 클래스·메서드로 1:1 이동한다. 입력·실제 producer·
assertion·부정 사례·subTest·수치 weight를 유지한다. 두 기존 pin 캡처 구간에서 실제
생성자를 원본 호출하는 표준 mock 관측을 사용한다. 실제 registry 부재→pin None→
여섯 FD 종료 관측을 같은 두 사례가 공유하며 테스트 전용 생산 getter는 추가하지 않는다.

수정한 소유 파일의 선택 목록부터 확인하고 같은 경로로 실행한다.

```bash
PYTHONPATH=src python -m tests.runner list \
  --path src/story_writing_agents/artifact_transactions/artifact_application_authority.py \
  --json /tmp/application-selection.json
PYTHONPATH=src python -m tests.runner run \
  --path src/story_writing_agents/artifact_transactions/artifact_application_authority.py \
  --json /tmp/application-focused.json
```

이 선택은 전체 통과 증거가 아니다. 공유 Root·공통 계약 변경은 소비자 합집합으로
확대하며 최종 동일 HEAD의 affected·전체 5 shard와 조정을 유지한다. 동일 오류의
RED/원본 GREEN과 정상 시간 비교를 구분하고 source·환경·GC·순서·원자료를 보존한다.

이번 전달은 기존 공통 fixture와 실제 Commit의 readiness·review·synthesis 준비를 유지한다.
약 20개 소비 파일에 걸친 provider·response·Runtime fixture 소유와 전이 import 정리는
Parent #968의 미완료 범위다. 경로 이동만으로 그 의존성이 없어졌다고 주장하지 않는다.
#1007의 Confirm·전문가 실행/복구 구현은 #1006 완료 뒤 진행한다.
테스트 소스는 151429 이내로 유지해 #1004의 예약 2행을 보존하며 legacy 1550행을
출처 증명 없이 공제하지 않는다. 현재 source·실행·시간·리뷰 상태는 PR에서 확인한다.
