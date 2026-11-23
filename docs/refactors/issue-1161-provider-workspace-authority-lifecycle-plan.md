# 공급자 작업공간 권한과 참조 인계 분리

[#1161 구현 계획](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1161#implementation-plan)은 공급자 작업공간 진입과 Antigravity 역할 설치, 문맥 참조 읽기 인계를 분리합니다.

- 기준 커밋은 `191c82908c185bc3294da79f11662d61cf4b03c0`입니다. [기준 전체 CI](https://github.com/Kims-DeveloperGroup/story-writing-agents/actions/runs/45108451710)에서 기존 집중 2개와 영향 7개를 확인했습니다.
- 컨텍스트 진입 시점의 타입 검사와 역할 설치, 참조 상관 검증 뒤의 로더와 지문 조회 순서를 대조합니다. 기존 제출 후 확인 본문과 호출을 기준과 비교합니다.
- 역할 설치 호출을 생략했을 때 기존 진단 단언이 같은 결함을 검출하는지 기준과 후보에서 기록합니다. 실제 Codex 문맥 참조 인계 사례를 영향 검증에 포함합니다.
- 제출 후 권한 실패 경로는 [#969의 범위 조정 기록](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/969#issuecomment-7092203084)에서 추적합니다.
