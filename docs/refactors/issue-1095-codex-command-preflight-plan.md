# Codex 명령 구성과 실행 전 경로 검증 분리 계획

[#1095](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1095)는 `19530febd4f4c11b24506ccb7b8b7ef88b8d28e1`에서 시작합니다.
[부모 중간보고](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/969#issuecomment-7043192052)에 범위 추가의 근거를 기록했습니다.

두 명령 작성 경로, 모델과 추론 옵션, 실행 파일 조회 및 native 상태 경로 검증을
`codex_command_preflight.py`에 두면서 기존 argv·인자 평가 및 작업공간 검증 순서를 유지합니다.
임시 파일과 프로세스·응답 수명은 기존 소유자를 사용합니다.

같은 실제 자식 프로세스의 `--cd` 표기를 기준 전후 각각 바꿔 기존 단언 하나가 실패하는지 확인합니다.
기존 확대 사례 9개와 서브테스트 19개, 실제 파일별 영향 선택, 전체 CI와 시간을 확인합니다.
