# 추천 준비 권한과 제안·문서 투영 분리

이 문서는 구현 전에 Draft PR을 열기 위한 범위 확인 기록이다.
상세 구현 계획과 완료 조건은 [이슈 #1027](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1027#구현-계획)에 둔다.

기준은 main `b7061d17229aa7a61d2b85fd689a7792262a1073`이다.
이 커밋은 문서만 추가하며 runtime·테스트·설정·패키징을 변경하지 않는다.

- 실제 `RecommendationCapability`와 준비 관련 함수 4개를 `lead_preparation/recommendation_preparation.py`로 옮긴다.
- 문서·전제·origin/disclosure 함수 9개는 `lead_preparation/proposal_basis_values.py`가 소유한다.
- Root의 caller lock·pin·registry·async 순서와 UUID 생성 위치를 보존한다.
- 기존 capability 객체·consume·repr·직렬화 거부와 반환·오류·검증 순서를 유지한다.
- 기존 미바인딩 `SessionContextHistoryChangedError`는 자동 수리하지 않는다.
- 기존 추천 준비 클래스의 두 Case를 같은 메서드명으로 1:1 이동하고 본문·단언·cap·MRO·자원 수명을 유지한다.

사용자는 테스트 산정 소스 +74행(152,064→152,138; raw +65/support +9)을 승인했다.
일반 도구 `tests/suite_budget.py` +16행은 별도이며 Case·subTest·시간·worker 한도는 유지한다.
다른 이슈의 미승인 비용에 이 승인을 재사용하지 않는다.

수정 전 정상 3회의 원래 source/checkout과 tree 동일성, 사전 오차 범위는 이슈의 근거로 보존한다.
실제 생산 RED/GREEN·영향·전체 CI·정상 수정 후 시간·리뷰·통합은 아직 수행하지 않았다.
로컬 프로젝트 import·collection·테스트 대신 공식 Actions에서 검증한다.

native Requirement=Must의 API와 인증된 Fields 사이드바를 확인했고 선행 #988은 종료됐다.
Project Ready를 검증한 뒤 Development 연결 branch와 sibling worktree를 생성했다.
이 문서 커밋으로 Draft PR을 먼저 생성한 후 생산 코드와 테스트를 구현한다.
