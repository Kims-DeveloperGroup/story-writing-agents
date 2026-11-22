# Codex JSONL 이벤트와 스레드 ID·사용량 해석 분리

[#1143](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1143)의 구현 전 계획입니다.
기준은 `47915d45c7d648ebd65d84a8b45266ceb0c54463`이고 부모는 [#969](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/969)입니다.

기존 `codex_event_decoding.py`에 이벤트 본체와 정규식을 배치하고 기존 정적 호출과 실행 수명을 보존합니다.
기존 사용량 테스트를 분리할 때 정규 ID와 fixture를 유지합니다.
실행 전후의 기존 실패 신호와 정상 테스트는 [이슈 구현 계획](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1143#implementation-plan)에 따라 비교합니다.
