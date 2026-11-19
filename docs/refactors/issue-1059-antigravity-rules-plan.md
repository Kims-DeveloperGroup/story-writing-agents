# Antigravity rules 분리 검증 계획 (#1059)

- 상세 구현 계획: https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1059
- 기준 main: `ef92fa9738178c1708a5597e20e3838afd913444`.
- 이 체크포인트는 문서만 추가하며 런타임·테스트 코드를 변경하지 않는다.
- 실제 Draft PR을 만든 뒤에만 런타임·테스트 편집을 시작한다.
- 제안 소유자: `story_writing_agents.antigravity_rules`의 공개 read/install 함수 2개.
- 기존 정의 11개와 tuple alias를 단일 소유자로 이동하고 FD·예외·정리 순서를 보존한다.
- 기존 first3는 binding, command/stdin/resume, mode/control Case이며 cap은 3/0/10이다.
- 기능 확대는 first3와 기존 추가 5개를 합친 8개이며 metadata 검증도 별도로 유지한다.
- 같은 newline byte 오류를 원래 staging writer와 새 writer에 넣어 기존 길이 assertion으로 RED를 확인한다.
- 해당 RED와 정상 전후 3회는 아직 실행하지 않았다. 정상 시간과 의도된 RED를 혼합하지 않는다.
- 모든 FD 오류 분기의 전용 oracle이 있다고 주장하거나 새 Case를 추가하지 않는다.
- first3 본문은 plain 정의 class로 한 번 이동하고 원래 TestCase가 같은 함수를 직접 바인딩한다.
- 원 Case/MRO/fixture·실제 호출·guard·ID·profile/weights·clock·GC·시간/worker 한도를 유지한다.
- whole os fake는 기존 범위에서 같은 객체를 양 실제 owner에 공급한다.
- 필요한 소스 행 추가는 포괄 승인을 적용하고 실제 계수와 정확한 source-only 예산 쌍을 고정한다.
- 기준 source와 같은 tree의 기존 전체 CI는 44503897774이며 후보 after 검증으로 해석하지 않는다.
- 실제 실행은 공식 Actions에서 하고 로컬에서는 프로젝트 import/collection/테스트를 실행하지 않는다.
