# Backend 시작 옵션 검증 분리

[#1159의 구현 계획](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1159#implementation-plan)은 명시적 Codex 모델, Antigravity 모델·effort, 실행 시간 제한의 기존 검증을 분리합니다.

- 기준 커밋은 `ac6b7ea4b1ca11d73f82840bf96bec2ecc9a5deb`입니다. [기준 전체 CI](https://github.com/Kims-DeveloperGroup/story-writing-agents/actions/runs/45100826424)에서 기존 집중 2개와 영향 8개를 확인했습니다.
- 설정 시작 진단 뒤의 기존 검증 순서, 오류와 원인 및 시간 제한 변환 위치를 유지합니다.
- 실제 구현 변경에서 기존 정규 ID와 진단 단언을 확인하고, 같은 생산 진단 누락의 전후 검출 결과를 이슈에 기록합니다.
