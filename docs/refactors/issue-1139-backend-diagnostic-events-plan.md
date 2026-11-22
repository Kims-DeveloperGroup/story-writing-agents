# Backend 진단 이벤트 발행과 테스트 분리

[#1139](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1139)의 기준은 `262865cd5ab0bfb94cdf3c58768a475e944fc255`와 검증 트리 `17751c3da66a6a046df869bea54ebc8095b9bae2`입니다.

[#969 중간보고](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/969#issuecomment-7078732461)에 추가 사유와 근거를 기록했습니다. [사전 테스트 영향 검토](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/969#issuecomment-7078815464)를 실제 후보의 첫 diff 검토에 사용합니다.

`backend_diagnostic_events.py`에 trace·오류·실행 프로필 세 발행 본체를 둡니다. 기존 Backend 메서드 바인딩과 진단 저장부 조회·실패 격리·제어 예외 분류를 유지합니다. 프로필 확인의 상관 ID와 라우팅 세대는 같은 Backend 잠금에서 읽습니다.

기존 첫 사례는 trace 발행 생략을 기존 호출 횟수 단언에서 검출합니다. 집중 및 확대 검증, 시간 비교, 테스트 ID와 서브테스트 보존 범위는 [이슈의 구현 계획](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1139#implementation-plan)에 기록했습니다.
