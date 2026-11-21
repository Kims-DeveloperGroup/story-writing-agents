# 바인딩된 문맥 공급자의 호출·참조 상태 분리

[#1119](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1119)의 기준 커밋은 `ed09e20887bca731a44e360c9c845f56737cb57b`이고, 기준 검증 트리는 `64c007e59262af2714134397f7bda87de9f50da6`입니다.
[부모 이슈 중간보고](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/969#issuecomment-7067304635)에 하위 이슈 추가 사유와 근거를 기록했습니다.

기존 `_BoundContextProvider`의 상태와 전이를 전용 모듈로 옮깁니다. 백엔드의 바인딩에서 기존 실행 권한과 호출 가능한 동작을 현재 시점에 조회합니다. 사용자 경로와 기존 테스트가 참조하는 클래스 객체를 보존합니다.

집중 테스트 본문은 별도 파일로 옮기고 기존 등록 ID로 연결합니다. 기존 집중 1개·영향 12개와 서브테스트 10개를 정해진 범위에서 검증합니다. 같은 기존 집중 테스트의 의도적 결함과 정상 결과를 비교합니다. 패키징·선택 정책 및 전체 2,544개 ID의 샤드 배치를 확인합니다.
