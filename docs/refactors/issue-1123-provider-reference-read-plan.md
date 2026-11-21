# 공급자 참조 읽기와 일반 호출 연결 분리

[#1123](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1123)의 기준 커밋은 `34fb0cd8867714fa61cfef4ac3d405cfa565dd90`이며 기준 검증 트리는 `3bbb0a4fea192d6a1003acf7d626fb77d94e7bb2`입니다.
[부모 이슈 중간보고](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/969#issuecomment-7069888870)에 하위 이슈 추가 사유와 근거를 기록했습니다.

참조 읽기, 일반 호출의 프레임 연결, 완료, 실패 분류, 영속 종료와 스레드 오염 처리의 기존 정의 일곱 개를 전용 모듈로 옮깁니다. 공개 소비자의 실제 호출과 작업공간 재확인, 원래 실패 및 종료 순서를 보존합니다.

기존 집중 사례의 두 하위 시나리오에서 핸들러 오류 시 원래 예외, 공급자 호출 0회와 영속 종료를 확인합니다. 영향 14개와 기존 서브테스트 22개, 기존 개발 정책 검증 및 전체 CI를 현재 HEAD에서 확인합니다.
