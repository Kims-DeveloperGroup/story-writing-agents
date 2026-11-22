# 공급자 세대 바인딩 해제와 어댑터 종료 분리

[#1147](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1147)의 구현 전 계획입니다. 기준은 `5da086601759f9ed9f63fb2299222e9e393ea965`입니다.

[부모 이슈의 추가 사유와 근거](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/969#issuecomment-7083655920)에 현재 범위를 설명했습니다.

공급자 세대 등록부에 바인딩 수와 개별 세대 종료를 모읍니다. 실제 어댑터 해제 뒤 기존 잠금에서 바인딩 수를 감소시키고 개별 세대 닫힘을 결정합니다. Backend의 전역 정리와 활성 설정 게시 책임을 유지합니다. 집중 기존 2개, 영향 기존 8개를 [이슈 검증 계획](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1147#related-work-and-verification-plan)에서 확인할 수 있습니다.
