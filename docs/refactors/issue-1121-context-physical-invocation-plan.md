# 문맥 공급자의 실제 호출·재시도 경계 분리

[#1121](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1121)의 기준 커밋은 `03c3a80d58ee5af73ff99fa6f93c4c5bd749b92d`이며 기준 검증 트리는 `08d4ef3423c28b9f82644900f75f038bd356064d`입니다.
[부모 이슈 중간보고](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/969#issuecomment-7068681171)에 하위 이슈 추가 사유와 근거를 기록했습니다.

실제 문맥 제출과 재시도 콜백·지문·교환 개시·오염 처리의 기존 정의 여섯 개를 새 모듈로 옮깁니다. 현재 공급자 세대, 준비·소유 권한 및 회계 sink를 원래 사용 지점에서 조회합니다. 기존 바인딩과 요청·실패 처리를 보존합니다.

기존 집중 사례와 재시도 사례가 의도한 스키마 및 스레드 비교 결함을 각각 검출하는지 확인합니다. 집중 1개와 영향 12개 및 서브테스트 10개에서 정상 반복을 검증합니다. 기존 등록 ID와 전체 CI 배치를 유지합니다.
