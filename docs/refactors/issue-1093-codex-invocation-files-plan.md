# Codex 호출의 임시 요청·응답 파일 처리 분리 계획

[#1093](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1093)의 기준 소스는 `ca2c170550b4e95431286557a8f5b90ab0d6c079`입니다.
[#969 중간보고](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/969#issuecomment-7041427179)에 작업 근거와 기존 사례를 기록했습니다.

호출별 임시 디렉터리의 생성과 정리, JSON 스키마 기록, 공급자 응답 파일 확인·읽기를
`codex_invocation_files.py`에서 소유하도록 옮깁니다. 기존 어댑터에서 호출하는 시점과 순서,
작업공간 확인, 파일 종류·크기 처리와 기존 정리 동작을 유지합니다.

Codex 백엔드의 기존 사례 7개를 `tests/test_codex_invocation_files.py`에 재배치합니다.
두 테스트 모듈에서 이미 쓰던 프로세스 fixture를 함께 사용합니다.
임시 디렉터리 정리 단언 1개의 동일 실패를 확인한 뒤, 영향 검증 8개와 확대 검증 27개,
전체 CI 순으로 기존 시나리오와 실행 시간을 검증합니다.
