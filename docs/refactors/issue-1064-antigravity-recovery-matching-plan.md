# Antigravity 복구 증거 해석과 판정 분리 검증 계획 (#1064)

구현 전 Draft PR의 기준은 `main`의 `89439136d8b9fc2e014bbf0be1a5d03382d942bf`입니다. [#1064 구현 계획](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1064#implementation-plan).

- 기존 native trace 읽기 결과의 JSONL 구조, 요청과 finish 일치 판정을 `antigravity_recovery_matching.py`로 옮깁니다. 기존 파서와 오류 타입, 데이터 제한, 거부 순서를 유지합니다.
- 기존 baseline 기록, 디렉터리와 파일 검사, 유효한 prefix 대조, 파일 디스크립터와 스냅샷 수명은 Antigravity 어댑터가 계속 관리합니다.
- 기존 복구 사례 7개를 별도 정의 파일로 옮깁니다. 정규 ID, 기존 입력, fixture, patch 값, 단언과 subTest를 유지합니다.
- 기존 캡처 성공, 다른 요청 거부 및 값 타입 거부의 3개 Case를 집중 선택합니다. 원래 테스트 fixture에서 같은 잘못된 판정이 기존 단언에 잡히는지 이동 전후 비교합니다.
- 집중 선택을 전후 각 3회 실행합니다. 기존 복구 사례 전체 및 기존 공개 API·패키징·의존성 정책 검증으로 확대하고 Ready 헤드에서 전체 CI를 검증합니다.
