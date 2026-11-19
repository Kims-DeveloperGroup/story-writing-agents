# Provider 세션·스레드 기록 검증과 갱신 분리 검증 계획 (#1067)

구현 전 Draft PR의 기준은 `main`의 `a57b837271bfc06725a00034cf3af0e986bb5d51`입니다. [#1067 구현 계획](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1067#implementation-plan).

- 기존 세션 manifest와 일반·컨텍스트 스레드 기록의 값 검사, 조립, 갱신을 `provider_thread_records.py`로 옮깁니다.
- 시간 파싱과 갱신에 사용하는 기존 세 함수를 `provider_session_timestamps.py`로 옮깁니다. Backend의 I/O, 권한과 잠금은 원래 위치와 순서를 유지합니다.
- 파일별 집중 선택은 기존 3개씩입니다. 전후 정상 시간은 두 집중 선택의 기존 Case 6개(subTest 15개)를 같은 순서로 각각 3회 실행해 대조합니다.
- 기존 공개 저장·재개 사례의 원래 스레드 레코드 동등성 단언이 같은 오류를 검출하는지 이동 전후에 확인합니다.
- 기존 영향 기능 11개와 기존 경계·패키징·예산 검증으로 확대하고, Ready 헤드의 전체 CI를 확인합니다.
