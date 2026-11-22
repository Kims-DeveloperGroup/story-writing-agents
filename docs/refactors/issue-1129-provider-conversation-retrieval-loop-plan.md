# 대화 검색 호출 루프와 스레드 정리 분리 계획

[#1129](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1129)의 기준은 `054653cee055d052d43db802cb28d54f0531f158`와 실행 트리 `640e08312bc37dfe02077b6928596891a622e146`입니다.

[부모 이슈 중간보고](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/969#issuecomment-7074251839)에 추가 사유와 근거를 남겼습니다.

Codex와 Antigravity의 대화 검색 루프를 `provider_conversation_retrieval_loop.py`로 옮깁니다. 기존 호출 예산과 참조·스레드 정리 순서, 검색 프레임, 실패 예외를 보존합니다. 이동하는 기존 정의 네 개는 원본 307행입니다.

기존 집중 사례 두 개에서 READ 후 허용 동작과 핸들러 중단 시 스레드 오염 단언을 검증합니다. 직접 진입 및 클래스 연결 사례, 개발 정책과 전체 CI를 확인합니다.
