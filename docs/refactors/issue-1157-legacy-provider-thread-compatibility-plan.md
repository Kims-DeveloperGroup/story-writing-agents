# 기존 공급자 스레드 호환성 지문과 재사용 판정 분리

[이슈 #1157의 상세 구현 계획](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1157#implementation-plan)에 범위, 기존 테스트와 완료 조건을 기록했다.

기존 호출의 작업공간 지문과 스레드 재사용 상태를 별도 책임으로 이동한다. Backend의 기존 진입점, 바인딩 세대 캡처와 manifest 조회의 순서, 예외 경계를 보존한다. 새 생산 경로의 기존 집중 2개·영향 10개를 확인한다.
