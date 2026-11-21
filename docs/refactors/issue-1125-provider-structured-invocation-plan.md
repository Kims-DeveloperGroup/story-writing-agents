# 일반 공급자의 구조화 요청 제출과 복구 분리

[#1125](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1125)의 기준은 `4d35c49182a6d77e59a45951b24ff86aa0e0aad6`와 검증 트리 `360b4585588d936e895b0d27efd378de17f2d1ed`입니다.
[부모 이슈 중간보고](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/969#issuecomment-7070871643)에 추가 사유와 근거를 남겼습니다.

실제 구조화 요청과 수리·응답·거부 기록의 기존 정의 13개와 공유 지연 승인 상태를 포함한 모듈 바인딩 2개를 옮깁니다. 기존 출력·스레드 및 예외 결과와 Backend의 호출·권한 확인 순서를 보존합니다.

공급자 오류에서 taint 호출을 생략한 결함과 첫 거절에서 수리를 건너뛰는 결함을 기존 단언으로 각각 확인합니다. 집중 2개·영향 74개와 원래 서브테스트, 기존 개발 정책 검증과 전체 CI를 같은 변경 소스에 대조합니다.
