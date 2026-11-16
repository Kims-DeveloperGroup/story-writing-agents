# Lead 입력 자료와 영속 근거의 소유자 분리

이 문서는 구현 전 Draft PR을 먼저 열기 위한 범위 기록이다.
상세 계획과 완료 조건은 [이슈 #1028](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1028#구현-계획)에 둔다.
최초 문서 seed `db765fd0`는 runtime·테스트·설정·패키징을 변경하지 않았으며 Draft #1037보다 앞선다.

기준 main은 `e61b70a0d6985fe62316bb02fb478864616a2d75`다.
선행 #1027은 통합됐고 native Requirement의 Must를 API와 인증된 Fields 화면에서 확인했다.
부모 #968의 본문·#938 방법론과 기존 source/virtual proof를 대조하고 Ready를 검증했다.

- 입력 자료 선택·revision·경계 bytes는 `lead_preparation/lead_material_inputs.py`가 소유한다.
- 영속 evidence/receipt 매핑은 `lead_preparation/lead_evidence_receipts.py`가 소유한다.
- proposal 입력 조립·coverage는 `lead_preparation/lead_proposal_inputs.py`가 소유한다.
- 기존 15개 정의 본문 688행과 모든 실제 호출·patch를 이관한다.
- 실제 operation·같은 준비 capability·PID 검증 위치·오류 우선순위·lock/pin 수명을 유지한다.
- 기존 Matrix 2개 Case·전용 fixture를 `tests/test_lead_evidence_matrix.py`로 1:1 이동한다.
- 기존 assertion·negative·MRO·Case/subTest·시간·worker 상한을 유지한다.

필요 소스 행 추가는 사용자 포괄 승인 범위에서 실제 비용과 정확한 상한 전이를 기록한다.
구현의 정적 계수는 raw +79/support +3, 합계 +82행(152,138→152,220)이며 같은 정확 전이를 적용했다.
예산 정책 Case의 개별 시간은 전체 영향 기준으로 판단하고 Story runtime 회귀 기준은 유지한다.
실제 생산 RED/GREEN·영향 검증·정상 시간·현재 head 전체 CI는 구현 후 공식 Actions에서 확인한다.
로컬 프로젝트 import·collection·실행·테스트는 하지 않는다.
