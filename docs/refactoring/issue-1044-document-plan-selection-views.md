# 문서 계획 선택과 뷰의 정책 분리

#1044는 부모 #968의 고정 D 범위다. 구현 전 Draft PR #1051은 준비 문서만 담은
`0a3fa20a1acd05aba149d73808bfd6f90aae8400`에서 생성했다. 기준 main은
`e6bf42bc3e7e9e732f80010c5c16f5c4d792d262`이다.

## 실제 소유권

`lead_preparation/document_plan_selection.py`는 goal 사실과 task/document/budget 선택
자격의 실제 본문을 소유한다. `document_plan_views.py`는 snapshot/readiness 표시와
선택·attach tail의 실제 정책을 소유한다. 고정4정의175행 중162행을 이동하고 실제 Root의
공개 signature/default·조회/early return13행은 유지했다. 기존 public reader를 재사용하며
새 forwarding helper·Root bag·cache·Task·lock·I/O·알고리즘은 추가하지 않았다.

## 빠른 변경 검증

좁은 goal/history 수정은 기존 순수 Case를 직접 선택한다. 이 Case는 기존 최소 history
fixture와 실제 goal factory 본문을 검증하며 전체 Runtime 준비가 필요하지 않다.

```text
tests.test_collaboration_history_projection.CollaborationHistoryProjectionTests.test_projection_is_deterministic_ordered_typed_and_history_only
```

문서 선택·view/readiness/restart/attach tail 영향에는 다음 기존 Runtime Case를 함께
선택한다. 공유 계약이나 실제 caller 영향이 있으면 이슈의 expanded3과 full fallback으로 넓힌다.

```text
tests.test_document_plan_views.DocumentPlanViewsTests.test_document_plan_goal_candidate_and_sequential_plot_work_survive_restart
```

기존2Case·285assertion·음성17개·cap0/0·MRO와 method별 자원 수명은 보존했다.
Case/ID 이동을 숨기는 shim이나 __module__ 위장은 두지 않았다. broad projection Case를
bind/view 정책 전체의 독립 oracle로 부르지 않는다.

## 비용·검증·관측 한계

실제 source는153,829→153,941행(+112)이며 상세 계상은 TEST_POLICY.md를 따른다.
기존 source-only guard16행은 frozen counter 밖 비용으로 별도 기록했다. hosted162와
worker4·v8 token·시간18,000/3,600초·Case/subTest 상한은 유지한다.

이동 전 같은 production goal tuple 오류는 공식 Actions의 기존 원assert511에서
failure1/errors0으로 검출했다. 계획의510 표기는 실제 AST/trace511로 정정했으며 입력·Case를
새로 만들거나 오류를 constructor failure로 바꾸지 않았다. 이동 후 같은 오류·수정 없는
GREEN·first/expanded·최종 동일 소스 전체 CI와 시간 판정은 각각 실제 실행 근거로 확인한다.

정적 역AST·graph·계수는 실행/성능 PASS를 대신하지 않는다. 정상 전후 자료와 before-only
오차범위를 유지하며 프로파일·GC/clock 보정으로 실제 회귀를 숨기지 않는다.

[상세 계획과 최신 실제 근거](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1044#구현-계획)
