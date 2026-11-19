# Codex 정의 원본과 스테이징 바이트 조립 분리 검증 계획 (#1063)

구현 전 Draft PR의 기준은 `main`의 `7b4020428617ddc00eb9a69ecb70e9467613cbba`입니다. [#1063 구현 계획](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1063#implementation-plan).

- `CodexAdapter._definition_file`과 그 안의 `append`를 `codex_definition_sources.py`의 `read_definition_source`로 옮깁니다. 기존 원본 읽기, UTF-8 인코딩, 조각 순서, 바이트 집계와 반환값을 보존합니다.
- 실제 조립 호출은 기존 스테이징 시점에 유지합니다. 어댑터가 스테이징 디렉터리·파일, 등록과 실패 정리를 계속 맡습니다.
- 처음 선택은 기존 원본 변조 후 바이트 비교, 안전하지 않은 원본 경로로 전환된 후의 스테이징, 스테이징 후 검증 실패 정리의 3개 Case입니다. 전후의 입력, subTest, 결과를 맞춰 각각 3회 측정합니다.
- 같은 오류를 재현할 때 첫 반환 바이트에 줄바꿈 하나를 추가합니다. 이동 전후에 기존 바이트 단언이 같은 차이를 검출하는지 확인합니다.
- 기존 어댑터 비교와 공개 API·패키징·의존성 및 테스트 예산 검증으로 확대합니다. 현재 PR 헤드의 전체 CI에서 기존 Case·subTest를 대조합니다.
