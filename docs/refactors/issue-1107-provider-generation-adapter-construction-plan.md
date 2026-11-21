# Backend 구성 세대의 공급자 어댑터 생성 분리

[#1107](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1107)의 통합 수락 기준은 `83b0d83b1a0c2ac2503de2d2e677e7d9d31ca002`입니다. [부모 이슈 중간보고](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/969#issuecomment-7058889421)에 추가 사유와 근거를 남겼습니다.

두 공급자 어댑터의 생성 인자를 `provider_generation_adapters.py`에서 조립할 계획입니다. 기존 시작과 설정 전환의 호출 순서, 지연된 공급자 함수 조회, 오류 처리와 세대 수명은 유지합니다. 기존 집중 3개와 영향 9개에서 모델, 스레드, 시간 제한과 정리 동작을 확인합니다.
