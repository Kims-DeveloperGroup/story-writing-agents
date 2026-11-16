# #1040 현재 Lead 근거 재검증 경계

문서 전용 HEAD `e616fb93`에서 Draft PR #1042를 먼저 생성했다.
이 문서는 그때 확정한 범위와 이후 검증 결과를 추적한다.

## 기준과 범위

- 승인 기반: `263a252ff09c5c9708df93ca0627aff27a8177c1`.
- 실제 두 근거 본문과 Root read helper 두 개의 공개 경계를 옮긴다.
- 도메인 파일: `lead_preparation/current_lead_evidence.py`339행.
- 기존 순수 입력 owner 세 함수·182행은 그대로 유지한다.
- 기존 converter Case 두 개만 전용 파일로1:1 이동한다.

## 보존 및 검증

- 기존 입력·assertion·부정 조건·per-method 수명과 서비스 조회 순서를 보존한다.
- Case2544·cap합8690·시간18000/3600초·최대4worker는 유지한다.
- 실제 source 배정은152727→152814(+87), generic 비계상16행을 별도 공개한다.
- 기존 weight pool 분배 규칙으로 groups394→395·guest class25→26을 계상한다.
- LPT 재배치와 before/after 전체 문맥 차이를 명시하고 실제 시간은 공식 Actions에서 검증한다.
- 새 시나리오·Case·상태/cache/Task/lock/I/O·Root bag·wrapper를 추가하지 않는다.

## 구현된 책임과 선택

새 owner가 `artifact_intent_evidence`와 `validate_current_lead_evidence`의
실제 두 본문을 소유한다. Root는 기존 객체·자원 접근을 유지하고 호출마다
공개 연산을 전달한다. 기존 입력 owner의 세 함수와 182행은 바꾸지 않았다.
기존 변환 테스트 두 메서드는 `CurrentLeadEvidenceTests`로 1:1 이동했고
기존 fixture 상속·입력·11/10개 assertion·cap6/2를 보존했다.

적용된 테스트 metadata의 새 authority 선택은 focused2/affected12이며,
전용 테스트 도메인의 선택은2/2다. Root 변경의 기존 full2544 fallback은 유지한다.
이 수치는 적용된 metadata의 정적 closure 검증이며 공식 실행 결과와 구분한다.
기존 162개 hosted ID를 이름만 전환해 profile `audited-162-v8`로 연결했다.
runner와 LPT 알고리즘·최대4worker는 바꾸지 않았다.

## 변경 전 실제 검증

문서 전용 HEAD의 기존 두 Case를 공식 Actions에서 정상 3회 실행했다.
각 회차는 2 PASS, subTest 6/0이며 소스·환경·기본 GC와 실행 제어를 확인했다.
다음은 전체 CI 문맥과 분리한 동일 첫 선택의 변경 전 측정값이다.

| 기존 Case | 정상 3회 중앙값(초) | 개별 증가 조사 범위(초) |
| --- | ---: | ---: |
| empty core-status 근거 변환 | 167.46683345499997 | 5.024005003649999 |
| zero-hit 변경 후 confirmation 거절 | 95.45628202400007 | 7.3056730440001445 |
| 두 Case의 runner wall 합계 | 262.925812424 | 13.947571520999873 |

범위는 변경 전 값만으로 고정했으며 이후 관측으로 조정하지 않는다.
별도 진단 커밋에서는 실제 변환 함수의 byte 수에 1을 더했을 때
기존 `assertEqual(len(valid), 1)`이 `0 != 1`로 실패했다.
이 진단 실패는 정상 시간이나 전체 CI 성공 증거에 포함하지 않는다.
변경 후 동등성·영향 선택·시간·전체 CI·리뷰·통합은 별도 확인이 필요하다.

## 추적

[상세 계획과 실제 준비 상태](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1040#구현-계획)를 따른다.

[정상 1회](https://github.com/Kims-DeveloperGroup/story-writing-agents/actions/runs/43907017621),
[2회](https://github.com/Kims-DeveloperGroup/story-writing-agents/actions/runs/43907045922),
[3회](https://github.com/Kims-DeveloperGroup/story-writing-agents/actions/runs/43907073734),
[실제 생산 오류 검출](https://github.com/Kims-DeveloperGroup/story-writing-agents/actions/runs/43907357153).
