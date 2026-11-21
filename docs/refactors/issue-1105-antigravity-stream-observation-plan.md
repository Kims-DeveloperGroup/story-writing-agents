# Antigravity 스트림 관찰과 기존 테스트 분리

[#1105](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1105)의 수락 기준은 `21bff609409f68b84e92e80cc501f8accd33eacc`입니다. [부모 이슈 중간보고](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/969#issuecomment-7057367463)에 추가 사유와 근거를 남겼습니다.

`antigravity_stream_observation.py`에 한 호출의 기존 줄 수신, 이벤트 전달과 종료 후보 관찰을 옮깁니다. 콜백을 받지 않은 stdout의 재생, 스트림 오류와 진단 순서를 보존합니다. 어댑터는 제출·실패 회계와 역할 확인을 담당합니다.

기존 수신 사례를 검증 14개와 전달 15개로 옮기고, 집중 29개와 일반 호출을 포함한 영향 31개를 확인합니다. 같은 오류와 전후 정상 시간 및 전체 CI를 확인합니다.
