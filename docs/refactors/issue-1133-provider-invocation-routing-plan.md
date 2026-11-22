# 공급자 호출 라우팅과 대응 테스트 분리 계획

[#1133](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1133)의 기준은 `b2c9907070f2797141719fa5f9b754e1b1af2f1f`와 실행 트리 `2f7d709d1dfd5975f5e7de68d017329b0fafce9a`입니다.

[부모 이슈 중간보고](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/969#issuecomment-7076126546)에 추가 사유와 근거를 남겼습니다.

`provider_invocation_routing.py`로 기존 라우팅과 Antigravity 연결 여섯 정의를 이동합니다. 기존 두 사례 본문 212행을 라우팅 테스트 파일에 연결하고 현재 코드의 ID, 예외 동일성, 참조 처리, 작업공간 전달을 그대로 확인합니다.

기존 공급자 실패 경계 두 개를 변경 전후 같은 단언으로 확인합니다. 기존 집중·영향과 호환·정책 테스트, 모듈 인덱스·전체 CI·관리 리뷰를 검증합니다.
