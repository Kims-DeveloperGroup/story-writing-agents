# 추천 요청·결정·실패 종료의 책임 분리

[#1045](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1045)는
추천 요청 구성과 결정·실패 종료 정책을 실제 상태 조회·권한 소비·저장과 분리한다.
기존 추천·Confirm/Reject/Revise·실패 복구의 동작과 사건 순서를 보존한다.
상세 API와 사례별 대응은 이슈의 구현 계획을 기준으로 한다.

## 파일과 실제 경계

수락 main은 `d34eee7385bc869b60cda45c69f6ffaf68c3cf9b`다.
문서 head `6c311e455b5caab22e52890f4d01e7e1c1f61dc4`에서 실제 Draft PR을 만든 뒤
런타임·테스트 코드를 수정했다.

| 소유자 | 현재 크기 | 책임 |
| --- | ---: | --- |
| `lead_preparation/recommendation_requests.py` | 510행 | 요청 정책·basis의2함수와 호출별 결과1개 |
| `lead_preparation/recommendation_decisions.py` | 294행 | 결정·실패 종료5함수와 호출별 결과4개 |
| 기존 `recommendation_preparation.py` | 256행 | 원본 바이트 그대로인 capability·준비 API |
| `orchestrator.py` | 10,110행 | 실제 자원·현재 권한·lock·await·게시 조정 |

원본 네 정의는 합계831행이다. 중복 없는 원본 정책524행을 옮기고 나머지307행의
실제 자원·조정을 보존했다. 네 함수와 전체 Root를 실제 새 본문에서 역변환한 AST가
원본과 일치한다. 이것은 소스 보존 증거이며 실행·시간 검증을 대신하지 않는다.
의존 그래프는274모듈/1,581edge의 비순환 구조이고 모듈 인덱스는300행이다.

| 원본 정의 | 분리한 정책 | Root가 보존하는 실제 연산 |
| --- | --- | --- |
| `_recommendation_basis` | 예산·schema·basis 값 구성 | profile, route/hash, 늦은 context 조회 |
| `_recommendation_request_draft` | 요청 구성·source 선택 | type 생성·문서 계획·게시 tail |
| `decide_lead_recommendation` | 결정 준비·결과 계획 | lock, 현재 권한, await, append |
| `_finalize_recommendation_failure_locked` | 실패 일치·terminal accounting·종료 계획 | 실제 history·operation 조회와 append |

결과형5개·31개 필드는 호출별 도메인 값만 운반한다. list와 객체 정체성,
늦은 tuple 변환·분기별 조회·예외·UUID 생성·권한 소비·사건 순서를 유지한다.
Root 전체 주입, forwarding wrapper, getter, 새 I/O·Task·lock·cache는 추가하지 않는다.
기존 공개 closure는 기존 preparation 모듈에서 호출 시점에 조회한다.

## 작은 파일의 선택 범위

새 요청 owner의 기본 focused는 기존 응답 별칭 Case1개다. 새 결정 owner는
기존 준비 성공·실패 accounting 거절의 first2를 기본 선택으로 둔다.
각 owner의 declared affected는 같은 기존6Case다. 두 owner를 함께 고르면
focused3/affected6이며, 실제 선택은 canonical `list` 결과에서 확인한다.

```bash
PYTHONPATH=src python -m tests.runner list \
  --path src/story_writing_agents/lead_preparation/recommendation_requests.py \
  --scope focused --json /tmp/recommendation-requests-selection.json
PYTHONPATH=src python -m tests.runner run \
  --path src/story_writing_agents/lead_preparation/recommendation_decisions.py \
  --scope focused --json /tmp/recommendation-decisions-result.json
```

기존 preparation의 capability 소비 경로를 보존하므로 그 파일을 실제 수정하면
material 입력 owner를 거쳐 전체 선택으로 확대된다. Root의 공유 조정 변경도 전체
fallback이다. 선언만 좁혀 이 관계를 숨기지 않는다. 새 결정 정책을 기존 preparation에
함께 두었던 초기 배치는 actual affected가2,544개로 확대돼 별도 파일 배치로 보완했다.

## 기존 사례와 오류 검출

기존6Case·219assertion·음성9개·cap합8과 MRO·새 mutable resource·cleanup을
보존했다. Case/ID/class/module을 이동하거나 빈 Case 이동 receipt를 추가하지 않았다.

원래 first2의 정확한 ID는 다음과 같다.

1. `tests.lead_preparation.test_recommendation_preparation.LeadRecommendationTests.test_artifact_confirm_runs_one_preparation_and_stops_at_exact_review`
2. `tests.test_lead_recommendations.LeadRecommendationRecoveryTests.test_missing_corrupt_or_unresolved_origin_accounting_never_authorizes_retry`

수정 전 정상3회는 각각2PASS였다. 메서드 중앙값은591.051562418초와332.842304096초,
두 사례의 wall 중앙값은918.226951944초다. 별도의 전체 before full3와 모집단을 구분한다.
stale·중복·동시 결정은 필수 확대이며, alias·basis/origin·Confirm·continuation/Revise·
readable/session context가 영향을 받으면 기존 action 계약3개까지 확대한다.

생산 오류 witness는 기존
`tests.test_recommendation_actions.RecommendationActionContractTests.test_current_response_alias_binds_exact_whole_message_before_action`이다.
선택 draft 수집의 첫 `drafts.append(draft)`만 `drafts.insert(0, draft)`로 바꿨을 때
원래140행의 두 digest 순서 assertion에서1실패/0errors·cleanup실패0을 확인했다.
unmodified GREEN은1PASS였다. RED142.590656858초와 GREEN418.570010401초는
서로 다른 작업량의 실제 관측이며, 실패로 단축된 시간을 성능 개선으로 사용하지 않는다.
코드 이동 후에도 같은 생산 표현식·기존 assertion을 연결해 검증한다.

초기 `preparation_cap=1` 후보는 기존86행 await에서 fixture family가 제공되지 않아
본문과 cleanup이 실패했다. 의도한 원 assertion에 도달하지 못했으므로 적격 RED와
정상 성능에서 제외한다. 이 실패를 삭제하거나 테스트·fixture를 완화하지 않는다.
[수정 전 원자료와 적용 범위](https://github.com/Kims-DeveloperGroup/story-writing-agents/pull/1052#issuecomment-6947862391)에 연결했다.

## 소스 계상과 전달 검증

현재 배정은 raw147,854/support4,674/legacy1,550, 합계154,078행이다.
기준153,941행에서+137행은 API·packaging73/graph48/기존BudgetCase16으로 구성한다.
generic strict guard11행은 frozen counter 밖의 물리 비용으로 별도 공개한다.
정확한 source-only 전이와 과거 snapshot·인접·복합 거절은
[현행 테스트 정책](../../TEST_POLICY.md)을 따른다.

기존2,544Case·cap합8,690, receipt13·weight399그룹의 전체 수치,
hosted162/v8·class29·실행 순서와18,000/3,600초·최대4worker를 유지한다.
새 모듈 초기화·결과형 할당·공개 함수 호출도 실제 비용으로 시간 관측에 포함한다.
양의 변화와 고정 오차범위·원인 판정을 기록하며 실제 Story 회귀는 실패로 판단한다.
진단 실행은 정상 표본이나 최종 전체 CI를 대체하지 않는다.

helper 이동에서는 module alias의 Attribute 접근과 local import 소비자도 확인하고
실제 owner `depends_on`을 함께 갱신한다. 개발 중 focused 선택과 실제 생산 오류
검출 뒤에는 같은 최종 source의 affected·전체 CI·리뷰·통합을 별도로 완료한다.
실행 결과와 미완료 상태는 [PR #1052](https://github.com/Kims-DeveloperGroup/story-writing-agents/pull/1052)에 기록한다.

## 부모 범위

[#968의 고정 범위](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/968#issuecomment-6916302752)는
총18개 자식이다. 이번 파일 배치 보완이나 내부 helper를 별도 자식으로 늘리지 않는다.
여러 PR이 필요하면 같은 이슈 안에서 추적하고 마지막 완료 PR에서만 해당 이슈를 닫는다.
