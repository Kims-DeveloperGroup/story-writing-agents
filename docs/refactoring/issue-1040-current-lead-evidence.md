# #1040 현재 Lead 근거 재검증 경계

이 문서는 구현 전 Draft PR을 만들기 위한 계획 기록이다.
런타임·테스트 본문은 아직 수정하지 않았다.

## 기준과 범위

- 승인 기반: `263a252ff09c5c9708df93ca0627aff27a8177c1`.
- 실제 두 근거 본문과 Root read helper 두 개의 공개 경계를 옮긴다.
- 후보 도메인 파일: `lead_preparation/current_lead_evidence.py`339행.
- 기존 순수 입력 owner 세 함수·182행은 그대로 유지한다.
- 기존 converter Case 두 개만 전용 파일로1:1 이동한다.

## 보존 및 검증

- 기존 입력·assertion·부정 조건·per-method 수명과 서비스 조회 순서를 보존한다.
- Case2544·cap합8690·시간18000/3600초·최대4worker는 유지한다.
- 후보 source 배정은152727→152814(+87), generic 비계상16행을 별도 공개한다.
- 기존 weight pool 분배 규칙으로 groups394→395·guest class25→26을 계상한다.
- LPT 재배치와 before/after 전체 문맥 차이를 명시하고 실제 시간은 공식 Actions에서 검증한다.
- 새 시나리오·Case·상태/cache/Task/lock/I/O·Root bag·wrapper를 추가하지 않는다.

## 추적

[상세 계획과 실제 준비 상태](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1040#구현-계획)를 따른다.
