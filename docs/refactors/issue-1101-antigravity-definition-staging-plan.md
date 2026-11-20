# Antigravity 정의 설치와 전용 디렉터리 준비 분리

[#1101](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1101)의 수락 기준 커밋은 `4e2dd9296201af887a16693b569365cac0042033`입니다. [부모 이슈 중간보고](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/969#issuecomment-7048643786)에 추가 사유와 근거를 남겼습니다.

기존 정의 조립과 설치 확인은 `antigravity_agent_definition.py`, bind와 invoke가 공유하는 디렉터리 준비는 `antigravity_state_directories.py`로 옮깁니다. 바인딩 앞뒤 두 번의 참조 읽기와 바인딩 결과 생성 순서를 보존합니다.

기존 네 사례는 전용 파일에서 실행하고, Backend 바인딩을 포함해 기존 열 사례를 영향 검증에서 확인합니다. 같은 설치 누락이 동일한 기존 테스트에서 실패하는지 전후 대조한 뒤 정상 선택과 전체 CI를 실행합니다.
