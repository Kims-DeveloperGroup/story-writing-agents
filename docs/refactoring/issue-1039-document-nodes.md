# Lead 문서 노드 정책 분리

이 문서는 #1039의 구현 전 Draft PR을 준비하는 계획 seed다.
기준 main은 `6e5ede108f572ee411f0311c3fd504bcc559363e`다.
부모 #968의 고정 책임 목록과 최신 중간보고를 확인했다.

## 목표와 범위

문서 view·node 검증·자동 선택·candidate 입력의 기존 네 본문을 실제 도메인 소유자로 옮긴다.
제안 경로는 `lead_preparation/lead_document_nodes.py`다.
Root의 실제 selection·prior-confirmation helper 두 개는 공개 이름으로 바꾼다.
Root의 lock·pin·lease와 실행 수명 소유권은 유지한다.
새 기능·상태·cache·Task·lock·I/O·Root bag·forwarding wrapper를 추가하지 않는다.

## 기존 테스트와 검증

기존 projection/조립 Case와 실제 document-plan/restart Case를 첫 선택으로 사용한다.
기존 validator 기록 spy를 실제 새 owner의 lookup에 연결하고 모든 기존 assertion을 보존한다.
조립 stub과 실제 Runtime 권한 검증의 입증 범위를 구분한다.
Case·시나리오·subTest·실행 횟수·시간·worker 한도를 늘리지 않는다.
현재 테스트 source 152,665행에서 필요한 추가분만 사용자 승인에 따라 정확히 계상한다.
전체 수정 전 정상3회는 현재 main과 같은 tree의 기존 공식 실행 원자료를 재사용한다.
standalone 첫 선택·동일 alias 오류 RED/GREEN·확대·전체 CI·리뷰는 실제 소스에서 검증한다.

현재 커밋은 문서만 추가하며 런타임·테스트 구현을 포함하지 않는다.

## 구현과 검증 상태

Draft #1041은 문서 전용 커밋 `1277642283671e56caf049f09d9b44360437607a`로
2026-11-16T11:13:34Z에 생성됐고, 실제 Development 연결과 코드 미수정을 확인한 뒤 구현했다.
문서 정책 네 본문은 새 owner로 옮기고 기존 Root helper 두 정의를 공개 이름으로 바꿨다.
원본 업무 인자·default·분기·worker 위치·객체 수명을 유지하며 호출 연산을 저장하지 않는다.
새 owner는275행이고 모듈 인덱스는300행이다.
역AST·기존 인자 평가 순서·공개 타입 identity와 실제 의존 방향을 정적으로 확인했다.

기존 복합 projection Case는 ObjectiveOwner도 검증하므로 Root import를 유지한다.
Candidate 부분만 새 실제 owner와 같은 수명의 validator 기록 spy로 연결한다.
순수 조립의 stub을 실제 권한 검증으로 간주하지 않으며 Runtime Case를 별도로 실행한다.
새 owner의 focused2·affected26과 shared Root 변경의 full2544 fallback을 구분한다.

테스트 source는 raw147752/support3425/legacy1550, 총152727행이다.
기준152665행에서 adapter32·기존 graph/packaging14·기존 정책 Case16의62행을 계상했다.
비계상 일반 guard11행도 공개하며 정확한 source-only pair와 기존 부정 검증을 유지한다.
Case2544·cap합8690·weight/profile·실행 순서·시간·최대4worker는 유지한다.

수정 전 standalone 첫2개 정상3회는 공식 실행43856320139/43856325973/43856331863에서 성공했다.
수정 전 alias 오류 진단43856918939는 기존574행 extended tuple assertion에서 실패했다.
이후 negative/idempotence assertion이 해당 RED에서 실행됐다고 주장하지 않는다.
진단 실패는 정상 성능 및 전체 성공 근거에서 제외한다.
현재 구현 후 공식 영향/확대·동일 오류 RED/GREEN·전체 CI·정상 시간·리뷰는 남아 있다.
