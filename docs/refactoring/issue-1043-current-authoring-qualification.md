# 현재 작성 권한·편집 자료·입력 자격 분리

상세 범위·API·기존 테스트 이동표·완료 기준은
[이슈 #1043의 구현 계획](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1043#구현-계획)에서 관리한다.
상위 [#968](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/968)의
고정 잔여 7개·전체 자식 18개 범위를 유지한다.
Runtime·테스트 구현 전에 문서만으로 [Draft PR #1050](https://github.com/Kims-DeveloperGroup/story-writing-agents/pull/1050)을 생성했다.

## 구현 범위

`lead_preparation`의 실제 정책 소유자를 다음과 같이 나눴다.

- `editorial_capture.py`: 편집 scope·context·reference 판정.
- `authoring_source_history.py`: 기존 호출 예산·이력 작성자 판정.
- `lead_source_qualification.py`: 입력 자격·readiness source 판정.
- `review_material_validation.py`: 기존 실제 review 자료 검증.
- `current_lead_inputs.py`: 현재 입력·provider-free 입력 재구성.
- `current_proposal_authority.py`: 현재 전제·dispatch·실행·basis/budget 권한 판정.

기존 Root의 실제 I/O·resource/composition과 공개 진입 계약을 유지한다.
고정 11개 정의와 포함 중첩 본문을 추적하며 별도 helper 이슈를 만들지 않는다.
기존 테스트 3개는 editorial capture·review validation·current input의
독립 파일로 1:1 이동했다. 각 원래 MRO·assertion·부정 사례를 유지하고,
실제 공통 fixture·producer는 도메인 support에 단일 정의로 둔다.

## 개발 검증 절차

1. 수정한 도메인 파일에 등록된 focused 기존 ID부터 공식 affected 실행을 선택한다.
2. 공유 API·자료·권한·fixture가 바뀌면 계획과 selection profile의 소비자 합집합으로 확대한다.
   Root 또는 영향이 불명확한 변경은 보수적인 전체 선택을 유지한다.
3. 정확한 source·입력·선택/미선택 ID·실제 제출과 완료를 기록한다.
   실제 production 오류의 RED와 정상 복원을 같은 기존 assertion에서 확인한다.
4. 선택 결과와 최종 동일 소스의 전체 CI·시간 비교·리뷰·main 통합을 구분한다.
   Case/subTest·시간·worker 상한을 늘려 완료하지 않는다.

## 현재 검증 상태

변경 전 기준 main은 `d66b50e93f3a5a09748fb9a90feecf326124333e`,
전체 tree는 `82bf1d1e08889f0ec78fd5d716cfb8ceb06443f8`이다.
이 tree의 기존 정상 전체 3회와 구현 전 별도 first2 정상 3회는 서로 다른 모집단으로 동결했다.
[변경 전 원자료 검증](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1043#issuecomment-6932319149)에는
실제 정상 3회와 editorial 오류의 assertion failure1/errors0, 환경·소스·피드백 시간 및 한계를 기록했다.

현재 구현의 본문·Root 역AST·시그니처·namespace·DAG·인덱스·
Case/metadata·실제 source 계수는 정적으로 검증했다.
변경 후 실제 affected RED/GREEN·전체 CI·성능 판정·공식 리뷰·main 통합은 남아 있다.
