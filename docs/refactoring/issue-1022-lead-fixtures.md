# Lead 공통 fixture 경계 분리 — #1022

- 작업: https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1022
- 상세 계획: https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1022#구현-계획
- 부모 #968과 전체 #962의 미완료 책임은 계속 추적한다.
- 착수 기반: `16c93d236373e44f841675dc2cc6c26f14a95dbe`.
- 기반 tree: `9aba54075f6169ef5689857674534c20a38dc9b6`.

이 문서는 구현 전 Draft PR을 만든 범위 기록이다. seed 커밋 당시 테스트 소스 이관은 미적용이었다.

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
문서 전용 커밋과 Draft #1038을 먼저 확인한 뒤 테스트 소스를 이동했다. 공식 Actions 검증은 별도로 기록한다.

실제 이관 후 직접 소비 466개 Case와 역방향 의존을 포함한 영향 범위는 다르다. material owner의 affected는 772개, 나머지 owner는 635개이며 focused는 각각 기존 1개다. 전체 collection DAG가 비순환이라고 주장하지 않으며 기존 invocation/provider wire의 늦은 순환은 보존한다.

현재 카운터는 raw 147,686 + support 3,425 + legacy 1,550 = 152,661행이다. 이동·import 이관 +426행과 기존 정책 Case 검증 +15행을 계상했다. Case 2,544개, cap 합 8,690과 394개 timing weight group은 유지했다.

변경 전 공식 CI의 first 2개 선택은 3회 모두 성공했다. 실제 취소 결과 게시의 CANCELLED→FAILED 진단은 기존 overall outcome assertion에서 실패했다. 진단 실패는 정상 성능 및 전체 CI 성공 근거로 사용하지 않는다. 변경 후 동일 선택·진단과 전체 CI는 이 구현 커밋에서 별도로 검증한다.

초기 구현의 공식 실행 8개는 기존 owner 간접 import edge 누락으로 정적 단계에서 실패했다. 이 결과는 보존하고 기능·성능·취소 assertion RED 근거에서 제외한다. 기존 Case owner 191개와 새 support owner 4개를 실제 AST import 규칙으로 대조해 기존 25개와 새 3개 owner의 depends_on만 교정했다. focused/affected ID 집합과 152,661행 카운터는 동일하다. 수정 소스의 공식 검증은 별도로 확인한다.
