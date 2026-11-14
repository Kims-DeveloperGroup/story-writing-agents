# #998 공급자 계약 책임 분리: 구현 전 기준

이 문서는 코드 편집 전 Draft PR을 만들기 위한 준비 체크포인트다. 런타임·테스트·환경의 기준은 main `65ad7c277d1da5ad7b1cab931f48364607067d67`이며 이 커밋은 그 코드를 변경하지 않는다.

## 범위와 승인

- [구체 계획](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/998#구현-계획)에 따라 기존 `provider_wire.py`의 43개 정의·48개 global·초기화를 다섯 실제 소유자와 실제 소비자로 이동한다.
- 새 소유자는 `provider_message_catalog.py`, `provider_contract_guidance.py`, `provider_schema_projection.py`, `provider_request_mapping.py`, `provider_response_mapping.py`다.
- 사용자는 [#998 source +170행 배정](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/998#issuecomment-6872085271)을 승인했다. 현재151,516행에서151,686행으로 한정하며 raw+127/support+43, 기존 legacy1,550행은 유지한다.
- Case2,544·subTest 상한 합8,690·시간·worker 상한과 원래 시나리오를 보존한다. Source 배정은 실제 성능 회귀나 기능·외부 format·저장 의미 변경을 허용하지 않는다.
- 별도 #1021 +378행 배정이나 미승인 #1022 +220행은 이 이슈 비용에 합산하지 않는다. 부모 #975의 다른 두 대형 파일과 #962 원래 범위는 계속 남는다.

## 코드·사례 전이의 기준

- 새 DTO·catalog 복제, 옛 facade shim, 새 cross-private 접근을 만들지 않는다. 단일 오류·기존 selection 객체·초기화·mutable 값의 수명·기존 함수 본문/기본값을 보존한다.
- 공유 helper는 기존 본문을 공개 `parse_task_reference_token`·`result_enum_map`으로 한 번 이동한다. `SEARCH_MEMBERSHIP_CATALOG_FIELDS` 외 불필요한 private 공개는 하지 않는다.
- 실제 새 함수로 fresh binding을 구성하고 현재 producer/decoder·발급 ID 쌍·call/contract 권한 및 stale 거부를 함께 유지한다. `schema_id`의 live-binding 정체성과 canonical rule 내용 해시를 구분한다.
- 기존 schema95행 Case와 response146행 Case를 작은 테스트 파일로1:1 이동한다. 기존 support33행은 raw에서 제거해 새 등록 support36행으로 한 번 계상한다. 기존7 Case 소비자·PTY fixture 전문 삽입·feedback traceback 계약은 보존한다.
- 첫 catalog 검증은 기존 selected-material Case다. 실제 capture serializer→request mapper→기존42행 deep equality가 `input_id`의 `input_source_id` 매핑을 확인한다. 같은 실제 결함 family는 `CATALOG-SELECTED-MATERIAL-ID-FIELD`다.
- 전체 catalog·enum/version/error·giant/current binding·normalization·비JSON 복구의 확대를 유지한다. 첫 선택을 전체 통과로 간주하지 않는다.

## 검증과 통합 게이트

예상 graph는256 modules/1,352edges/cycle0이다. 두 ID 이동 후 Case/cap 합과 class 가중치 총합을 보존하고, 실제 source의 graph·packaging·cap/receipt·source counter를 다시 검증한다. 선언한 행 형식을 실제 코드가 넘기면 검증을 삭제하거나 비용을 숨기지 않고 재계산한다.

동일 실제 오류의 before/after RED/GREEN, affected 검증, 최종 source의 정상 전체 CI·각 메서드/원본 합산/전체 시간 비교와 관리 리뷰·수동 squash·종료·복구 가능한 정리를 요구한다. 기존 before 자료는 source·환경·ID/cap/profile·순서 적용성을 검증한 범위에서만 재사용한다.

현재 체크포인트에는 런타임/테스트 구현이나 실행 결과가 없다. [부모의 승인 후 구현 전 보고](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/975#issuecomment-6872099912)와 연결하며, Draft PR 생성 확인 후에만 코드를 편집한다.

