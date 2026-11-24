# Conversation 시간순 이력과 공개 조회 분리 계획

[이슈 #1018의 구현 계획](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1018#구현-계획)에 따라 원래 정의 9개와 역의존 제거에 필요한 기존 정의 2개를 옮깁니다. 시간순 이력의 재귀 호출 세 함수는 하나의 파일에 둡니다. 기존 호출, 정확한 타입과 공개 경로를 보존합니다.

기존 집중 4건과 확대 7건을 사용합니다. 동일 배치 ID의 본문 충돌을 기존 사례에서 재현하고, 실제 파일시스템·SQLite와 Runtime의 기존 재시작 결과를 확인합니다. [기준 전체 CI](https://github.com/Kims-DeveloperGroup/story-writing-agents/actions/runs/45283649483)
