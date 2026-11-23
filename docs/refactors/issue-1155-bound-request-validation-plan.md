# 바인딩된 에이전트의 대화·검색 요청 검증 분리

[이슈 #1155의 상세 구현 계획](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1155#implementation-plan)에 범위, 기존 사례와 완료 조건을 기록했다.

대화·검색 검증에서 요청 타입 확인, 바인딩 정의·공급자 확인, 기존 정규화와 잘못된 에이전트 거절의 순서를 유지한다. 두 대응 기존 테스트를 정의 모듈에 두고 정규 ID·fixture를 보존한다. 검증 경로의 집중 2개와 영향 8개를 확인한다.
