# 공급자 설정 후보 사전 검증과 실패 정리 분리

[#1145](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1145)의 구현 전 계획입니다. 기준은 `08f79d1bf06686b6866afe78d39e2075282ee7a3`입니다.
[부모 이슈의 추가 사유와 근거](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/969#issuecomment-7082551159)에서 이 범위를 설명합니다.

공급자 실행 가능 여부 확인, 세대 후보 구성, 실패 시 어댑터 정리를 한 보호 구간으로 유지합니다.
세대 예약과 전환 대상 계산은 기존 Backend 잠금 안에서 수행합니다.
집중 검증 1개와 영향 검증 9개, 실제 실패 신호는 [이슈 검증 계획](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1145#related-work-and-verification-plan)을 따릅니다.
