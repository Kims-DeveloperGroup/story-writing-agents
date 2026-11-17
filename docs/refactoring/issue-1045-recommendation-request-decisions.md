# 추천 요청·결정·실패 종료의 책임 분리

[#1045](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1045)는
추천 요청을 구성하거나 추천을 확정·거절·재시도할 때 필요한 정책을
실제 상태 조회·권한 소비·이벤트 저장과 분리한다. 동작을 바꾸는 작업은 아니다.
상세 설계와 기존 사례의 대응표는 이슈의 구현 계획을 기준으로 한다.

## 구현 전 상태

기준 main은 `d34eee7385bc869b60cda45c69f6ffaf68c3cf9b`이며,
tree는 `2f2a782ba69d3df612a61a787c2f8884b98e089f`이다.
선행 #1043·#1044는 통합됐고, #1045의 native Ready와 planning gate를 확인했다.
이 문서는 실제 Draft PR을 구현 전에 만들기 위한 문서 전용 변경이다.
아직 런타임·테스트 코드 변경이나 #1045의 새 실행 결과는 없다.

## 옮길 책임과 보존할 경계

현재 Root의 네 정의는 합계 831행이다.

| 현재 정의 | 분리할 정책 | Root가 보존할 실제 연산 |
| --- | --- | --- |
| `_recommendation_basis` | 예산·schema·basis 값 구성 | profile, route/hash, 늦은 context 조회 |
| `_recommendation_request_draft` | 기존 370행 요청 정책 | type 생성·문서 계획·게시 tail |
| `decide_lead_recommendation` | 결정 준비·결과 계획 | lock, 현재 권한, await, append |
| `_finalize_recommendation_failure_locked` | 실패 일치·terminal accounting·종료 계획 | 실제 history 조회·operation·append |

제안 파일 `lead_preparation/recommendation_requests.py`는 요청 정책과 basis를 맡고,
기존 `lead_preparation/recommendation_preparation.py`는 결정·실패 정책을 맡는다.
새 결과 타입은 호출별 도메인 값만 운반한다. 현재 list와 객체 정체성을 유지하고,
tuple 변환·분기별 조회·예외·권한 소비·이벤트 순서도 원래 시점에 둔다.
Root 전체 주입, forwarding wrapper, getter, 새 I/O·Task·lock·cache는 추가하지 않는다.

## 첫 선택과 확대

실제 Draft 이후, 런타임·테스트 코드 수정 전에 다음 기존 두 사례의
정상 기준선 세 번과 실제 생산 오류 검출을 공식 Actions에서 확인한다.

1. `tests.lead_preparation.test_recommendation_preparation.LeadRecommendationTests.test_artifact_confirm_runs_one_preparation_and_stops_at_exact_review`
2. `tests.test_lead_recommendations.LeadRecommendationRecoveryTests.test_missing_corrupt_or_unresolved_origin_accounting_never_authorizes_retry`

stale·중복·동시 결정 사례는 필수로 확대한다. alias, basis/origin, Confirm,
continuation/Revise, readable/session context에 영향이 있으면 기존 action 계약
세 사례까지 확대한다. 기존 preparation owner의 영향 선택을 줄이지 않는다.
신규 사례·시나리오를 만들거나 실제 Runtime 경계를 helper/spy로 대체하지 않는다.

생산 오류 후보는 기존 요청의 `preparation_cap`을 유효한 양수 1로 바꾸는 것이다.
기존 준비 호출 수 assertion에 실제로 도달해야 검출 증거가 된다.
무관한 schema·fixture 오류나 도달하지 않은 분기는 RED 성공으로 보고하지 않는다.
이 후보는 아직 실행하지 않았다.

## 검증과 개발 방법

구현 후 네 함수 및 전체 Root의 역변환 AST, 공개 API, namespace, 의존 그래프,
모듈 인덱스와 정확한 소스 예산을 확인한다. 기존 2,544 Case, cap 합계 8,690,
실제 subTest 8,340과 18,000/3,600초·최대 4 worker 제한을 유지한다.
필요한 테스트 소스 행은 사용자 포괄 승인에 따라 실제 비용만 계상한다.
정상·오류 검출의 영향 선택 뒤 최종 동일 source의 전체 CI를 완료한다.

전체 기준선은 같은 tree의 공식 정상 세 번에서 고정했다. first 선택 기준선과
최종 비교는 서로 구분한다. 개별 증가를 보존하고 실제 Story 회귀는 실패로 판단하며,
이미 정한 오차범위를 적용한다. 진단 실행 시간을 정상 표본으로 대체하지 않는다.

공유 helper를 옮길 때 단순 이름 참조뿐 아니라 module alias의 Attribute 접근과
local import 소비자도 확인한다. 소비자의 실제 owner `depends_on`을 함께 갱신한다.
이 확인은 #1044에서 누락됐던 fixture 소비 경계를 반복하지 않기 위한 개선이다.

## 부모 범위

[#968의 고정 범위](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/968#issuecomment-6916302752)는
총 18개 자식이다. 이 이슈의 내부 단계나 helper를 별도 자식으로 늘리지 않는다.
여러 PR이 필요하면 같은 이슈 안에서 추적하고 마지막 완료 PR에서만 닫는다.
