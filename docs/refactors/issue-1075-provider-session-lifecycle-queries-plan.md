# 세션 조회·목록과 상태 합성 분리 계획

[#1075](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1075)의 구현 전 Draft 기준입니다.

기준 main `609790cf2eef879f16da49eaaa01781d360f0706`에서 두 조회 본문과 기존 상태 합성을 소유 모듈로 옮깁니다. Backend는 공개 진입점, 사전 검사, 권한과 진단을 유지합니다.

기존 목록 순서 단언의 같은 오류, 집중 4개와 기능 확대 11개, 최종 전체 CI로 검증합니다.
