# Antigravity 복구 증거 파일 읽기 분리 구현 계획

[#1099](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1099)의 기준은 `897bad9d24a48a4910223dea1bbb402ca91bd21f`입니다.
[부모 이슈 중간보고](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/969#issuecomment-7046761433)에 추가 사유와 근거를 남겼습니다.

기존 스냅샷, 기록 파일 읽기와 현재 종료 확인을 `antigravity_native_recovery.py`로 옮깁니다.
두 호출 위치와 기존 읽기·정리·오류 순서를 보존합니다.
기존 전용 사례 7개와 확대 11개의 실제 선택 및 같은 유효 복구 결함을 CI에서 확인합니다.
