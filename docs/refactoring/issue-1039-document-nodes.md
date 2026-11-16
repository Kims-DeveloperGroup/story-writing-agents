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
