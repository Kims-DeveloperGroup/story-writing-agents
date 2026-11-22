# 일반 호출과 작업공간 진입 분리 계획

[#1127](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1127)의 기준은 `cba02bf32439a0c14b3af8c5bf92221a233c9353`와 검증 트리 `bd82f3933298cb047fcbe40ccdd519e147a56337`입니다.

[부모 이슈 중간보고](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/969#issuecomment-7073150785)에 추가 사유와 근거를 남겼습니다.

일반 호출·대화·검색 포함 대화와 직접 작업공간 진입의 기존 정의 12개를 `bound_agent_logical_invocation.py`로 옮깁니다. BoundAgent와 공개 진입점, 참조·잠금 수명, 기존 종료 순서를 보존합니다.

닫힌 작업공간의 참조 종료 누락과 검색 호출의 중단 로그 누락을 기존 집중 사례에서 확인합니다. 기존 영향 88개와 개발 정책 검증 및 전체 CI를 변경 헤드에 대조합니다.
