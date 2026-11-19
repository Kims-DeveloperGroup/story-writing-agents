# Consensus 의미 소유권 분리 계획

[#1049의 상세 구현 계획](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1049#구현-계획)이 범위·API·검증의 정식 기록이다.
이 커밋은 구현 전 Draft PR을 만들기 위한 계획 문서이며 runtime 또는 테스트를 수정하지 않는다.

- 기준: #1048 통합 후 main `ae1fe47943ae858107f2c847edeafb16615b32ae`.
- 고정 범위: `StoryRuntime.map_consensus_provider_reply` 168행, `invoke_provider` 290행,
  `qualify_consensus_authoring` 81행의 합계 539행.
- 실제 소유자: `consensus/provider_reply_mapping.py`, `provider_request_policy.py`,
  `authoring_qualification.py`의 응집된 기존 값·타입 기반 공개 API 15개.
- 실제 READ·bind·reference·await·게시·취소/join·정리는 Root의 원래 단계와 수명을 보존한다.
- 기존 25Case·195개 assertion·fixture/MRO·음성 시나리오와 Case/subTest·시간·worker 상한을 유지한다.
- 최초 선택은 기존 mapper 6-route Case와 실제 prompt/draft·artifact 권한 bridge 두 Case다.
  핵심 확대 21Case 및 조건부 실제 backend 4Case의 정확한 ID·규칙은 이슈에 기록한다.
- 기존 독립 digest assertion으로 동일 한 식의 RED/GREEN을 관측하고 정상·진단·FULL 시간을 분리한다.
- 실제 업무 회귀는 실패로 판단하며 작은 측정 변동은 고정 before-only 조사 경계와 원인으로 평가한다.
  개발 정책 검증의 미미한 전체 비용은 승인된 목적별 정책을 적용하고 실제 비용을 공개한다.
- 구현 후 실제 API·namespace·DAG/private·packaging·module index·소스 예산을 대조하고,
  현재 소스 전체 CI·공식 리뷰 종료·수동 통합으로 완료한다.
