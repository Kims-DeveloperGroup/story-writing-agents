# Backend 바인딩 준비와 전역 자원 정리 분리

[이슈 #1153의 구현 계획](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1153#implementation-plan)에 범위, 실행 순서, 기존 테스트 선택과 완료 조건을 기록했다.

바인딩 준비의 활성 세대·프로필 조회와 어댑터 타입 검사를 분리하고, Backend에서 같은 RLock 아래 생성·retain·활성 등록을 유지한다. 전역 자원 정리는 마지막 바인딩 종료 뒤 기존 claim·잠금·어댑터 호출 순서로 진행한다. 기존 종료 사례 세 본문을 한 파일로 옮기고 정규 ID를 유지한다.
