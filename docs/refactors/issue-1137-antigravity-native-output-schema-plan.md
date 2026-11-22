# Antigravity 네이티브 출력 스키마 분리 계획

[#1137](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1137)의 기준은 `ef1381a17a7427f1627763280e98cf351cb2e9c4`와 검증 트리 `ec7037a47839458652f821b19f032d20a60a3b79`입니다.

[#969 중간보고](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/969#issuecomment-7077370632)에 추가 사유와 근거를 남겼습니다. [사전 테스트 영향 검토](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/969#issuecomment-7077394699)에서 기존 정규 ID와 기존 fixture 소비자를 확인했습니다.

`antigravity_native_output_schema.py`로 현재 정적 변환 본문과 내부 순회를 옮기고, 기존 `AntigravityAdapter.native_output_schema`의 정적 호출을 유지합니다. `tests/test_antigravity_native_output_schema.py`로 기존 네 렌더링 테스트와 같은 스키마의 공용 입력을 옮기며 기존 정규 ID는 보존합니다.

같은 결함의 기존 RED 단언과 4개 집중 정상 검증, 기존 네이티브 전송·Lead 확대 선택 및 전체 5번 경로를 확인합니다. 최초 실제 diff의 검토 뒤 기존 영향을 실행합니다.
