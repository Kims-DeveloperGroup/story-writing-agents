# 영속 결과 확정 책임 분리

[#1021의 상세 계획](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1021#구현-계획)에 따른 구현 전 문서 checkpoint다. 부모는 [#968](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/968)이다. 이 문서 commit에서는 runtime, 테스트, 설정과 패키징을 바꾸지 않는다.

기준 main은 `8e730b3e8f5b092d34e8d249da56a1275fc3ded4`다. #998의 공급자 계약 분리를 포함한 현재 source에서 계획을 재검증했다.

## 제안 소유 경계

`lead_execution/known_result_finalization.py`의 `KnownResultFinalization`가 영속 결과 확정, continuation proposal 구성, 추천 turn 결과를 소유한다. 기존 세 메서드를 이동하고 storage와 artifact authority의 같은 실제 객체만 보관한다. context와 필요한 공개 연산은 각 호출에서 전달한다. Root 전달 메서드나 별도 port DTO를 추가하지 않는다.

기존 본문, 인자와 기본값, 정책 조회 및 context 선택 시점, resource 보유 수명을 유지한다. 직접 의존20개와 Root만의 incoming, canonical context 타입 edge 및 DAG를 확인한다. 전체 API·이동 위치는 이슈의 현행 계획을 따른다.

## 기존 테스트 이동

기존 네 Case를 `tests/test_known_result_finalization.py`의 `KnownResultRecoveryTests`, `KnownResultBudgetTests`, `KnownResultAssessmentTests`로1:1 이동한다. 기존 body219행·assertion10/11/15/15·cap0/2/0/6과 fixture·MRO·cleanup을 보존한다. 비수집 mixin과 기존 seed helper를 공유하며 Case나 시나리오를 추가하지 않는다.

개발 중에는 해당 owner의 네 Case부터 실행하고 기존 budget·두 graph Case를 더한 일곱 Case로 넓힌다. 실제 ID 목록과 자연스러운 실행 순서는 이슈 및 실행 JSON에 기록한다. 최종 delivery는 전체 CI를 통과해야 한다.

## 예산과 관측

승인된 순증은 raw89/support289의378행이다. 현재151,686에서 상한152,064로 적용하며 legacy 차감은 없다. 전체2,544Case·cap8,690·162-v7/최대4worker·시간 한도는 유지한다.

기존 checkpoint 옵션과1/2/3 반환 형태를 유지한다. `with_finalization_base=False`를 명시적으로 요청하는 새 경로에서만 네 번째 checkpoint를 반환하며, 검증된 현재2823 cap-map과 fresh reverse-stage hash를 사용한다.

정상 일곱 Case 전후3회와 별도 import/collection·Case·전체CLI 진단을 구분한다. 실제 진단 복원값을 비우는 동일한 production 오류의 before/after RED/GREEN을 기존 assertion으로 확인한다. 양의 시간차·실패·취소와 적용 한계를 보존하고 clock·GC·대기값을 보정하지 않는다.

위 문서 checkpoint `51ce6f79`는 구현 결과나 테스트 성공을 주장하지 않는 구현 전 기록이다. Draft PR #1031 생성과 source 동일성 확인 후 구현을 시작했다.

## 구현 정적 확인

세 메서드를 실제 `KnownResultFinalization`로 이동했다. keyword-only constructor는 기존 storage와 artifact authority 두 객체만 보관한다. 공개 연산과 context를 호출별로 전달하며, 세 본문과 나머지 Root·두 helper의 역정규화 AST 및 기본값을 대조했다.

실제 runtime 그래프는257모듈/1375edge/DAG이고 owner의 outgoing20개·incomingRoot1개, module index300행을 확인했다. 기존 네Case의 body219행·assertion·MRO·fixture 수명과 source152,064행을 정적으로 확인했다. 같은162개 hosted method가 실제25개 클래스에 분포하며 동시worker 상한4와 weight총량은 유지한다.

runtime 동작·전후 정상3회·동일 필드 RED/GREEN·최종 전체CI의 성공 여부는 공식 실행 증거로 판단한다. 정적 확인을 runtime 테스트 성공으로 표시하지 않는다.
