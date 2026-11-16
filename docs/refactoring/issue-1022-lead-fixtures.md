# Lead 공통 fixture 경계 분리 — #1022

- 작업: https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1022
- 상세 계획: https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1022#구현-계획
- 부모 #968과 전체 #962의 미완료 책임은 계속 추적한다.
- 착수 기반: `16c93d236373e44f841675dc2cc6c26f14a95dbe`.
- 기반 tree: `9aba54075f6169ef5689857674534c20a38dc9b6`.

이 문서는 구현 전 Draft PR을 만들기 위한 범위 기록이다. 테스트 소스 이관은 아직 적용하지 않았다.

50개 실제 선언과 본문 1,469 ordinary행, 기존 31개 소비 파일·466개 Case의 대응을 계획했다.

- `provider_responses.py`: 응답·finalizer·provider·backend fixture.
- `preparation_data.py`: 준비·outcome·scope/readiness·순수 proposal/assessment와 단일 상수.
- `runtime_setup.py`: 실제 Base·fresh Runtime/backend·constructor 관측·cleanup handoff·budget mixin.
- `material_values.py`: accounting의 실제 context expansion 값 생성.

계획 source-only 전환은 152,220→152,661(+441)이며 사용자 포괄 승인을 적용한다. generic 도구의 별도 16행도 공개한다.
기존 Case·시나리오·assertion·cap·가중치·순서·18,000/3,600초·최대 4 worker와 mutable 수명을 보존한다.
실제 취소 게시의 단일 CANCELLED→FAILED 오류를 같은 기존 Case의 RED/GREEN으로 확인할 계획이다.
순수 first와 실제 atomic-dispatch Runtime first, 필요한 확대를 구분하고 최종 전체 CI를 유지한다.
정상 before 기준은 원래 source와 현재 main의 tree 동등성을 검증해 보존하며 SHA를 바꿔 기록하지 않는다.
이 문서만의 커밋·Draft를 먼저 확인한 뒤 테스트 소스 이동과 공식 Actions 검증을 시작한다.
