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
이는 S01의 고정 기준선 비교이며 일반 개발 실행에 매번 추가하지 않는다.

## 전달 전에 전체 검증을 완료한다

개발 중 선택 검증과 최종 통합 검증을 구분한다. 각 PR의 최종 동일 HEAD에서 fast,
영향 테스트, 전체 5개 shard와 조정을 완료한다. 기존 최대 4 worker, 전체 18,000초와
shard별 3,600초, 테스트 소스 151,431행 상한을 유지한다. 이동한 테스트·fixture는
출처를 추적해 한 번만 계상하고, 하위 패키지 이동으로 비용을 누락시키지 않는다.
현재 기준 실패와 새 실패를 구분하고 미완료·시간 회귀를 완료로 표시하지 않는다.
