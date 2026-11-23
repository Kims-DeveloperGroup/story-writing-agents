# Conversation 도메인별 이력 검증 분리 계획

[이슈 #1017의 구현 계획](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1017#구현-계획)에 따라 기존 정의 35개와 전이 맵 2개를 도메인별 소유 모듈로 옮깁니다. 기존 명목 이력 타입에 결합된 작업 컨텍스트 해석은 현재 위치에 둡니다.

파일별 기존 정규 테스트 선택과 공개 계약을 검증합니다. 기존 전이 결함을 같은 사례에서 확인하고, 저장소 왕복과 전체 CI를 각각 실행합니다. [기준 전체 CI](https://github.com/Kims-DeveloperGroup/story-writing-agents/actions/runs/45270466680)
