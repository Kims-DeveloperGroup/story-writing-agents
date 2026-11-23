# Conversation 불변 뷰·결과·윈도 검증 분리 계획

[이슈 #1016의 구현 계획](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1016#구현-계획)에 따라 기존 정의 28개와 전역 6개를 `conversations/views/` 아래 도메인별 파일로 옮깁니다. 이력 검증에 결합된 정의 세 개는 기존 위치에 둡니다. 기존 공개 경로와 객체 동일성, 검증 순서와 오류를 보존합니다.

새 소유 파일에서 기존 테스트의 정규 ID를 바로 선택합니다. 기존 윈도 사례에서 UTF-8 상한 위반을 재현하고 정상 집중 테스트 3건을 전후 세 번 비교합니다. 구현 후 패키지 경계, 실물 확대, 최종 전체 CI를 검증합니다. [기준 전체 CI](https://github.com/Kims-DeveloperGroup/story-writing-agents/actions/runs/45255251588)
