# Lead 두 국면 스키마 선택 구현·검증 기록

이 문서는 [#890의 확정 설계와 완료 조건](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/890)을 코드·회귀 검증·실제 Codex 검증에 연결하는 작업 기록이다. **현재 단계는 런타임 구현 중이며 최종 완료 검증은 수행 전이다.** 아래 계획과 미검증 표를 완료된 동작으로 읽지 않는다.

## 기준과 적용 범위

현재 기준은 2026-10-29 개정 설계 1.4와 AC-01∼AC-59다. [Lead 전용 인지 정책](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/890#issuecomment-6488708035)에는 매 요청의 명시적 Phase와 짧은 안내 요구를 반영했다. 기존 56개 조건만으로 완료를 판단하지 않는다.

기준 소스는 `05980c814b17bcfbe83a1f7da9b356654018ad17`이다. ordinary Lead front에 적용하며, 현재 허용되는 업무 결과·호출 예산·권한 경계를 보존한다. 준비 또는 discussion profile을 내부 그룹 선택만으로 활성화하지 않는다. 마이그레이션이나 이전 버전 호환 계층은 추가하지 않는다.

[#881에서 이관한 증거](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/890#issuecomment-6486848011)는 과거 실행의 결과와 한계를 보여 준다. 기존 실제 호출 원장 40/40은 소진 상태이며 새 실행에 재사용하지 않는다. 역사적 성공, 축소 스키마 진단 또는 이전 CI를 이 변경의 성공으로 계산하지 않는다.

## 사용 예와 의도된 동작

사용자가 `안녕`을 보내면 현재 요청과 전체 허용 카탈로그를 받은 Lead가 먼저 결과 그룹과 짧은 이유를 제출한다. 그다음 선택 그룹의 모든 현재 상세 계약을 받아 실제 업무 답변을 생성한다. Host는 단어를 보고 그룹을 대신 고르지 않는다. 첫 선택은 사용자에게 보일 답변이나 확인 요청이 아니다.

`2000자 내외의 에세이를 작성하자. 주제는 '포기'야`도 같은 두 국면을 거친다. 현재 워크플로에 맞는 확인 질문이나 다음 단계 제안은 유효한 타입 결과가 될 수 있다. 이 검증에 완성 원고나 별도 사용자 승인 우회를 요구하지 않는다.

상세 계약을 본 Lead가 다른 그룹이 적절하다고 판단하면 현재 카탈로그의 다른 그룹으로 직접 재선택한다. 최초 선택→직접 재선택→업무 결과는 세 번의 실제 호출이다. 별도의 SELECT 호출을 한 번 더 넣지 않는다. 새 그룹의 결과를 만들 호출이 없다면 선택 이력을 남기고 명시적으로 중단한다.

## 단일 카탈로그의 의미 경계

실제 카탈로그는 현재 owner와 eligibility에서 허용되는 전체 그룹으로 만든다. 다음은 초기 의미 분류이며 매 호출에서 15개를 무조건 제공한다는 뜻이 아니다. 카드의 정의·선택 기준·업무 결과 효과 및 구성원 판정은 같은 레지스트리에서 관리한다.

| 그룹 | 의미 |
|---|---|
| `answer.workflow` | 협업 절차, 진행 상태, 요청 처리 방법을 설명하는 일반 응답이다. 기존 계약이 허용하는 명시적 취소의 응답도 포함한다. |
| `answer.observed_facts` | 현재 제공된 READ 근거에 연결해 문서의 개별 사실을 설명하는 응답이다. 허용된 authoring과 review_only 범위를 모두 보존한다. |
| `question.workflow` | 요청의 목적, 절차, 진행 방향을 확인하는 질문이다. 기존 계약이 허용하는 새 요청의 시작과 계속 추적도 포함한다. |
| `question.authoring` | authoring 목적의 작성, 개정 또는 검토 작업에서 필요한 사항을 확인하는 질문이다. |
| `question.review_only` | 제공 원문의 평가를 목적으로 평가 범위나 기준 등을 확인하는 질문이다. |
| `critique.workflow` | 협업 절차나 요청 진행 방식에 관한 문제점과 우려를 제시한다. |
| `prepare.artifact` | 구체적인 문서 변경 제안서를 준비하는 단계를 추천한다. |
| `prepare.consultation.authoring` | authoring 목적의 전문가 작업 또는 검토 배정안을 준비하는 단계를 추천한다. |
| `prepare.consultation.review_only` | 제공 원문의 평가를 위한 전문가 검토 배정안 준비를 추천한다. |
| `consultation.authoring.content` | authoring 목적의 작성 또는 개정에 필요한 전문가 assignments를 구체적으로 제안한다. 허용된 작성과 제약 평가 역할의 조합을 포함한다. |
| `consultation.authoring.review` | authoring 작업에 속하는 전문가 검토 assignments를 구체적으로 제안한다. |
| `consultation.review_only` | 제공 원문의 평가를 위한 전문가 검토 assignments를 구체적으로 제안한다. 평가에 수반되는 문제 지적과 개선 방향 설명을 포함할 수 있다. |
| `artifact.changes` | 문서 생성, 부분 수정, 전체 교체, 이름 변경, 삭제의 실제 operation을 담은 변경 제안이다. 한 제안에 허용된 여러 종류를 함께 담을 수 있다. |
| `premise.analysis` | 아직 해소되지 않은 전제를 분석하기 위한 전문가 assignments와 전제 판단 자료를 제안한다. 기존 null scope 표현을 보존한다. |
| `readiness.core_documents` | 작품 전체와 핵심 문서의 준비 상태를 정해진 평가 구조로 표현한다. 확인된 상태와 부족하거나 미확인인 상태를 구별한다. |

업무 응답 타입이 같아도 `consultation.review_only`와 `consultation.authoring.review`는 목적이 다르다. 준비 추천은 직접 제안의 필수 선행 단계가 아니다. 사용자가 위임한 세부 선택을 의무 질문으로 만들지 않으며, 개별 READ 사실과 핵심 문서 전체의 준비도 평가도 구분한다.

## 국면과 호출 예산

| 현재 국면과 경로 | 필요한 최소 잔여 실제 호출 |
|---|---:|
| SELECT→선택→GENERATE 업무 결과 | 2 |
| SELECT→컨텍스트 액션→선택→업무 결과 | 3 |
| GENERATE→업무 결과 | 1 |
| GENERATE→직접 재선택→새 그룹 업무 결과 | 2 |
| GENERATE→컨텍스트 액션→같은 그룹 업무 결과 | 통상 2 |

SELECT·GENERATE·컨텍스트·교정·인증 replay·반복 재선택은 기존 한도의 같은 실제 호출을 소비한다. 국면 전환으로 한도를 초기화하거나 늘리지 않는다. 유효한 제어 결과가 물리적 호출을 끝내더라도 업무 턴을 완료하지는 않는다.

원래 선택의 카탈로그·요청 근거·이유·호출을 불변 기록으로 남기고 현재 GENERATE binding과 분리한다. READ가 사실을 추가하면 현재 그룹의 유효성을 확인한 fresh rebind를 사용한다. 목표·작업 revision·권한이 바뀌면 과거 선택을 무효화한다. 거절된 정확한 계약의 repair와 기존 eligibility를 유지하는 replay는 새 사실을 반영하는 rebind와 구별한다.

## 구현 책임과 경계 검토

전송 정책의 추가 경계는 context 서비스의 continuation 적격성·체크포인트·전달 스냅샷과 복구, renderer/provider_wire의 전체·증분 필드·검사·guidance, Codex adapter의 실제 continuation과 호출별 schema 전달이다. Lead의 신뢰된 bound 역할/프로토콜 지시와 호출별 안내를 구분하고, 전문가와 기존 단일 국면 프로필에는 새 선택 계약을 적용하지 않는다. 기존 그룹 도메인의 순수성·권한 경계를 유지한다.

정상 역할은 Root의 요구사항·문서·GitHub·통합, `runtime_implementer`의 생산 코드·모듈 경계·인덱스, `test_engineer`의 테스트다. 이전 모델 용량 오류 동안의 Root 임시 책임은 #894 소스 이관과 두 전문 분석의 종료 지점에서 정상 담당에게 반환했다. 런타임 담당은 기반 결함을 수정하고 정적 검증을 마쳤으며 테스트 담당은 기존 두 대표 ID에 검증을 추가 중이다. 런타임 실행 통과 증거는 아직 없다.

경계 검토의 대상은 그룹 레지스트리와 명시적 제어 타입, 의미 변형 단계의 compiler, 현재 catalog/selection/binding, 기존 `BoundCallFactory`·`BoundCallContract`의 owner 연결, ordinary front와 context 실행 루프다. 새 그룹 도메인은 Provider 프로세스·저장소·사용자 권한을 소유하지 않는다. 기존 서비스가 호출·교정·예산 생명주기를 소유한다.

하위 도메인의 상위 서비스 import, 다른 모듈의 새 비공개 API 접근, 숨겨진 순환 의존은 금지한다. 구조적 쓰기 전에 #890 본문의 경계 결정을 수용했다. `lead_schema_groups`는 불변 그룹 정의·카탈로그·제어 타입을 소유하고, 기존 compiler와 서비스가 이를 소비한다. 구현 후 공개 경로·의존 방향·생명주기 소유권 및 `PYTHON_MODULE_INDEX.md`를 대조한다. 전문 담당의 독립 경계·테스트 분석, 최종 Codex 리뷰와 기존 전체 검증을 유지한다. 경계 착수 조건 수용은 완료 조건의 통과를 뜻하지 않는다.

## 자식 이슈와 통합 순서

사용자 요청에 따라 #890을 통합 베이스로 유지하고 다음 자식을 순차 구현한다. 각 자식은 native Development 브랜치와 별도 워크트리, 시작 Draft PR, 검증·리뷰·조건 감사 및 부모 브랜치로의 squash merge를 거친다. 이 자식 PR의 대상은 `main`이 아니라 `issue-890-two-phase-schema-selection`이다. 부모 [Draft PR #893](https://github.com/Kims-DeveloperGroup/story-writing-agents/pull/893)은 전체 통합과 59개 조건 감사 후 최종 main 반영에 사용한다.

| 순서 | 자식 | 책임 | 부모 완료 조건의 주 소유 범위 |
|---|---|---|---|
| 1 | [#894](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/894) | 그룹·불변 카탈로그·bound 계약 기반 | 01–07, 09–10, 13–14, 16, 18, 20–25, 46 |
| 2 | [#895](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/895) | 선택·재선택 실행·교정·공유 호출 예산 | 15, 17, 19, 26–35 |
| 3 | [#896](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/896) | 증분 전달·Lead 인지·wire 통합 | 08, 11–12, 36–42, 57, 59 |
| 4 | [#897](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/897) | 관측·비용 비교·전체 회귀 | 43–45, 47–52, 58 |
| 5 | [#898](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/898) | 실제 Codex 복구·전항목 감사 | 53–56 및 전체 59개 최종 감사 |

59개 조건에는 각각 하나의 주 담당 자식이 있고 후속 자식의 통합 증거를 함께 요구할 수 있다. 자식 완료를 부모 전체 조건 통과로 간주하지 않는다. 선행 자식이 통합되어 차단이 해소된 실행 가능 자식만 Ready로 전환하고 구현 시작 시 In progress로 옮긴다.

현재 #894의 기준은 부모 tip `02743ec3ae326e7bb74c7ac61e564cc018c4d82b`다. 문서 시작 커밋 `a590ed48`과 [Draft PR #899](https://github.com/Kims-DeveloperGroup/story-writing-agents/pull/899)를 먼저 만들었다. 부모의 6개 소유 미커밋 파일은 패치와 파일별 해시로 보관한 뒤 자식에 이관했고, 읽기 작업과 바이트 대조를 마친 부모는 clean 통합 베이스로 복구했다. 실제 ordinary front 활성화는 실행·전송 자식이 완성할 때까지 보류한다.

## 완료 조건별 증거 표

원래 조건은 [#890의 구현 완료 조건](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/890#구현-완료-조건)이다. 아래는 같은 59개 조건의 증거 위치와 검증 계획이며 조건을 축소하지 않는다. 각 행은 실제 최종 소스·결과·보고서 링크가 확보된 뒤에만 충족으로 바꾼다.

| 조건 | 구현 또는 검증 경계 | 필요한 증거 | 판정 |
|---|---|---|---|
| AC-01 | 그룹 레지스트리·의미 분류·업무 계약 | 현재 허용 정규 결과 합집합, 15개 그룹, 혼합 역할·작업 및 intent 경계의 결정적 대조 | 미검증 |
| AC-02 | 그룹 레지스트리·의미 분류·업무 계약 | 현재 허용 정규 결과 합집합, 15개 그룹, 혼합 역할·작업 및 intent 경계의 결정적 대조 | 미검증 |
| AC-03 | 그룹 레지스트리·의미 분류·업무 계약 | 현재 허용 정규 결과 합집합, 15개 그룹, 혼합 역할·작업 및 intent 경계의 결정적 대조 | 미검증 |
| AC-04 | 그룹 레지스트리·의미 분류·업무 계약 | 현재 허용 정규 결과 합집합, 15개 그룹, 혼합 역할·작업 및 intent 경계의 결정적 대조 | 미검증 |
| AC-05 | 그룹 레지스트리·의미 분류·업무 계약 | 현재 허용 정규 결과 합집합, 15개 그룹, 혼합 역할·작업 및 intent 경계의 결정적 대조 | 미검증 |
| AC-06 | 그룹 레지스트리·의미 분류·업무 계약 | 현재 허용 정규 결과 합집합, 15개 그룹, 혼합 역할·작업 및 intent 경계의 결정적 대조 | 미검증 |
| AC-07 | 그룹 레지스트리·의미 분류·업무 계약 | 현재 허용 정규 결과 합집합, 15개 그룹, 혼합 역할·작업 및 intent 경계의 결정적 대조 | 미검증 |
| AC-08 | 선택 제어 계약·Lead 지시문 | 실제 SELECT 입력·닫힌 다섯 필드 계약과 누락/추가/타입/ID/enum 음성 대조 | 미검증 |
| AC-09 | 선택 제어 계약·Lead 지시문 | 실제 SELECT 입력·닫힌 다섯 필드 계약과 누락/추가/타입/ID/enum 음성 대조 | 미검증 |
| AC-10 | 선택 제어 계약·Lead 지시문 | 실제 SELECT 입력·닫힌 다섯 필드 계약과 누락/추가/타입/ID/enum 음성 대조 | 미검증 |
| AC-11 | 선택 제어 계약·Lead 지시문 | 실제 SELECT 입력·닫힌 다섯 필드 계약과 누락/추가/타입/ID/enum 음성 대조 | 미검증 |
| AC-12 | GENERATE 입력 구성 | 원래 요청·intent/scope·현재 전체 카탈로그의 실제 전달 및 독립 요청 대조 | 미검증 |
| AC-13 | 결과 compiler·bound call 합성 | 의미 분류 전후 비교, 모든 member/partition, 참조 폐쇄 및 SEARCH/READ 공유 정의 대조 | 미검증 |
| AC-14 | 결과 compiler·bound call 합성 | 의미 분류 전후 비교, 모든 member/partition, 참조 폐쇄 및 SEARCH/READ 공유 정의 대조 | 미검증 |
| AC-15 | 결과 compiler·bound call 합성 | 의미 분류 전후 비교, 모든 member/partition, 참조 폐쇄 및 SEARCH/READ 공유 정의 대조 | 미검증 |
| AC-16 | 선택·재선택 타입·제어 실행 | 정확한 외부/내부 binding과 직접 재선택 경로, 업무 완료·권한 이벤트가 없는 제어 호출 대조 | 미검증 |
| AC-17 | 선택·재선택 타입·제어 실행 | 정확한 외부/내부 binding과 직접 재선택 경로, 업무 완료·권한 이벤트가 없는 제어 호출 대조 | 미검증 |
| AC-18 | 선택·재선택 타입·제어 실행 | 정확한 외부/내부 binding과 직접 재선택 경로, 업무 완료·권한 이벤트가 없는 제어 호출 대조 | 미검증 |
| AC-19 | 선택·재선택 타입·제어 실행 | 정확한 외부/내부 binding과 직접 재선택 경로, 업무 완료·권한 이벤트가 없는 제어 호출 대조 | 미검증 |
| AC-20 | Lead 업무 owner·wire decoder | 현재 owner/codec/checks 및 scope·source·authority·variant identity 회귀 | 미검증 |
| AC-21 | Lead 업무 owner·wire decoder | 현재 owner/codec/checks 및 scope·source·authority·variant identity 회귀 | 미검증 |
| AC-22 | 카탈로그 스냅샷·선택 이력 | 카탈로그 정의·ID·작업 근거 불변성, 원래 선택과 현재 binding의 분리 대조 | 미검증 |
| AC-23 | 카탈로그 스냅샷·선택 이력 | 카탈로그 정의·ID·작업 근거 불변성, 원래 선택과 현재 binding의 분리 대조 | 미검증 |
| AC-24 | 카탈로그 스냅샷·선택 이력 | 카탈로그 정의·ID·작업 근거 불변성, 원래 선택과 현재 binding의 분리 대조 | 미검증 |
| AC-25 | 카탈로그 스냅샷·선택 이력 | 카탈로그 정의·ID·작업 근거 불변성, 원래 선택과 현재 binding의 분리 대조 | 미검증 |
| AC-26 | 국면 실행·교정·replay·fresh rebind | READ/목표/revision/권한 갱신과 오래된 ID, 동결 교정 및 새 정보 재결합 전이 대조 | 미검증 |
| AC-27 | 국면 실행·교정·replay·fresh rebind | READ/목표/revision/권한 갱신과 오래된 ID, 동결 교정 및 새 정보 재결합 전이 대조 | 미검증 |
| AC-28 | 국면 실행·교정·replay·fresh rebind | READ/목표/revision/권한 갱신과 오래된 ID, 동결 교정 및 새 정보 재결합 전이 대조 | 미검증 |
| AC-29 | 국면 실행·교정·replay·fresh rebind | READ/목표/revision/권한 갱신과 오래된 ID, 동결 교정 및 새 정보 재결합 전이 대조 | 미검증 |
| AC-30 | 국면 실행·교정·replay·fresh rebind | READ/목표/revision/권한 갱신과 오래된 ID, 동결 교정 및 새 정보 재결합 전이 대조 | 미검증 |
| AC-31 | 공유 예산·명시적 중단 | 경로별 최소 잔여 호출 표, 정상 2회/직접 재선택 3회, 컨텍스트/교정 및 마지막 재선택 소진 대조 | 미검증 |
| AC-32 | 공유 예산·명시적 중단 | 경로별 최소 잔여 호출 표, 정상 2회/직접 재선택 3회, 컨텍스트/교정 및 마지막 재선택 소진 대조 | 미검증 |
| AC-33 | 공유 예산·명시적 중단 | 경로별 최소 잔여 호출 표, 정상 2회/직접 재선택 3회, 컨텍스트/교정 및 마지막 재선택 소진 대조 | 미검증 |
| AC-34 | 공유 예산·명시적 중단 | 경로별 최소 잔여 호출 표, 정상 2회/직접 재선택 3회, 컨텍스트/교정 및 마지막 재선택 소진 대조 | 미검증 |
| AC-35 | 공유 예산·명시적 중단 | 경로별 최소 잔여 호출 표, 정상 2회/직접 재선택 3회, 컨텍스트/교정 및 마지막 재선택 소진 대조 | 미검증 |
| AC-36 | 지시문·예시·기존 wire 사례 | JSON 16개 의미, 전체 응답 스키마와 stdin 분리, schema_selection wire 등록·입력 검사·guidance, 독립 GENERATE의 전체 맥락 및 경계 사례 대조 | 미검증 |
| AC-37 | 지시문·예시·기존 wire 사례 | 설계 JSON 16개 구문/의미, workflow/variant/원문 평가/개정/준비/직접 제안/위임/사실·준비도 사례 대조 | 미검증 |
| AC-38 | 지시문·예시·기존 wire 사례 | 설계 JSON 16개 구문/의미, workflow/variant/원문 평가/개정/준비/직접 제안/위임/사실·준비도 사례 대조 | 미검증 |
| AC-39 | 지시문·예시·기존 wire 사례 | 설계 JSON 16개 구문/의미, workflow/variant/원문 평가/개정/준비/직접 제안/위임/사실·준비도 사례 대조 | 미검증 |
| AC-40 | 지시문·예시·기존 wire 사례 | 설계 JSON 16개 구문/의미, workflow/variant/원문 평가/개정/준비/직접 제안/위임/사실·준비도 사례 대조 | 미검증 |
| AC-41 | 지시문·예시·기존 wire 사례 | 설계 JSON 16개 구문/의미, workflow/variant/원문 평가/개정/준비/직접 제안/위임/사실·준비도 사례 대조 | 미검증 |
| AC-42 | 지시문·예시·기존 wire 사례 | 설계 JSON 16개 구문/의미, workflow/variant/원문 평가/개정/준비/직접 제안/위임/사실·준비도 사례 대조 | 미검증 |
| AC-43 | 이유 설명·중앙 제어 규칙 | 이유 문자열과 ID 검증의 분리, 이유로 권한·필수 재시도를 만들지 않는 대조 | 미검증 |
| AC-44 | 선택 평가 기록 | 타당성·근거성·구분·후속 일관성·회복 성격의 다섯 축과 복수 유효 선택 대조 | 미검증 |
| AC-45 | 호출 관측·선택 이력 | 요청/근거/정의/선택/호출/현재 binding/교정/재선택/결과·중단 연결 대조 | 미검증 |
| AC-46 | 이유 설명·중앙 제어 규칙 | 이유 문자열과 ID 검증의 분리, 이유로 권한·필수 재시도를 만들지 않는 대조 | 미검증 |
| AC-47 | 크기·종단 실행 비교 | 동일 입력·컨텍스트·profile의 전체/SELECT/모든 GENERATE 크기·분기·최댓값, 실제 호출·사용량·지연·결과 비교 | 미검증 |
| AC-48 | 크기·종단 실행 비교 | 동일 입력·컨텍스트·profile의 전체/SELECT/모든 GENERATE 크기·분기·최댓값, 실제 호출·사용량·지연·결과 비교 | 미검증 |
| AC-49 | 크기·종단 실행 비교 | 동일 입력·컨텍스트·profile의 전체/SELECT/모든 GENERATE 크기·분기·최댓값, 실제 호출·사용량·지연·결과 비교 | 미검증 |
| AC-50 | 이 문서·현재 구현 diff | 기준 05980c8와 수용된 main 및 최종 코드·테스트·증거의 59개 조건 대응 감사 | 미검증 |
| AC-51 | 기존 정규 회귀 사례 | 선택/재선택/ID/READ/무효화/교정/replay/예산/표현력의 전체 결정적 전이 검증 | 미검증 |
| AC-52 | 영향 테스트·전체 CI·개발 guardrail | 기존 ID/Whole Front 사례 보존, 정확한 최종 소스의 영향 및 전체 5개 shard·예산·정리 확인 | 미검증 |
| AC-53 | 실제 Codex 런타임 | 현재 Provider/model과 전체 생성 스키마로 원래 인사·에세이 입력의 유효한 타입 결과 확인 | 미검증 |
| AC-54 | 실제 호출별 관측 기록 | 소스·CLI·모델·추론 강도·입력·schema hash·native/typed 결과와 실효 한도·사용량 또는 관측 불가 확인 | 미검증 |
| AC-55 | 인식 가능한 출력 한도 설정 | 요청한 설정과 실제 인식·적용 증거 분리; 무시된 키·파서 수용만으로 효과를 주장하지 않음 | 미검증 |
| AC-56 | 새 유한 호출 캠페인 원장 | 기존 40/40 보존, 별도 사전 허용·모든 실패/사용량 불확실성·소스 불변·소유 프로세스 정리 확인 | 미검증 |
| AC-57 | context 서비스·renderer·adapter | 적격 continuation의 정상 GENERATE·재선택 증분에서 변경 없는 요청·근거·카탈로그 생략, 독립/복구 전체 재전달 및 명시적 추가·교체·제거·교정 대조 | 미검증 |
| AC-58 | 전달 스냅샷·비용 관측 | 전체/증분·복구 사유와 계약·결과 연결, 최대/종단 전송량·입력 토큰·캐시·비용·지연·전체 재전달 빈도/비용 구분 및 중단·불확실성·예산 회귀 | 미검증 |
| AC-59 | Lead bound 지시·호출별 guidance·현재 계약 | 공통 지시와 현재 국면/허용 액션/스키마 일치, 잘못된 국면·본문/허위 완료·delta 생략/삭제·교정/재선택/복구 및 전문가·단일 국면 보존 대조 | 미검증 |

## 전송과 Lead 인지의 구현 기준

입력은 `ProviderInvocation.payload_json`의 UTF-8 stdin, 전체 출력 스키마는 별도 `ProviderInvocation.schema`의 `--output-schema` 파일이다. 내부 selector만 전체 출력 스키마로 쓰거나 API 래퍼를 덧붙이지 않는다. 현재 출력 스키마는 닫힌 `agent_action` 루트이며 최종 투영의 모든 object는 properties 전체를 required에 포함한다. 실제 의미상 부재/null 규칙과 동적 맵의 제공 키는 기존 owner가 보존한다.

사용자 지시에 따라 Codex 구조화 출력은 `strict: true` 경로를 유지한다. 확인한 로컬 CLI는 `0.164.0`이며, 대응 버전의 요청 생성 코드와 CLI 회귀 테스트는 `--output-schema`의 원래 스키마를 `text.format`의 `type: "json_schema"`, `strict: true`와 결합한다. Host의 스키마 파일 자체에 요청 옵션을 섞지 않는다. [공식 Structured Outputs 사양](https://developers.openai.com/api/docs/guides/structured-outputs)에 따라 strict 모드의 지원 범위를 벗어난 스키마는 오류로 거절된다. strict 설정이 지원되지 않는 키워드나 정규식을 자동 변환하지는 않는다. 개발 guardrail로 최종 스키마의 지원 범위를 검사하고 실제 로컬 Provider 호출로 native 수용과 타입 결과를 별도 확인한다.

Host 제어 위치는 정확히 `response_contract.schema_selection`이다. 매 호출의 phase·issued_contract·remaining_provider_calls·context_delivery를 현재 bound 계약과 일치시킨다. 최초 bootstrap과 카탈로그 갱신은 전체 카드를, 변경 없는 적격 delta는 catalog_id를 제공한다. GENERATE에는 원래 선택을 참조하는 active_selection을 포함한다. 새 필드와 값은 wire 정의·입력 검사·guidance에 함께 등록한다. 사용자·문서 본문의 phase 문자열을 제어로 승격하지 않고 불일치는 호출 전 구성 오류로 처리한다.

정상 GENERATE와 재선택은 검증된 스레드·adapter/원장 적격성·보존된 checkpoint 맥락을 사용해 변경분과 현재 계약만 보낸다. 변경 없는 요청·근거·전체 카탈로그를 새 stdin에 반복하지 않는다. 생략은 삭제가 아니며 실제 추가·교체·제거는 명시한다. 독립 호출·복구는 필요한 원래 자료를 실제로 재전달하고 사유·비용을 기록한다. ID를 모델의 자료 조회 기능으로 간주하지 않는다. 제출 불확실성을 자동 replay로 해결하지 않는다.

Lead 공통 지시는 신뢰된 bound 역할/프로토콜 지시에 둔다. 호출별 안내는 현재 phase·허용 액션·스키마에서 생성한다. SELECT의 선택 제출과 GENERATE의 재선택은 물리 호출의 종료이며 업무 완료가 아니다. 전문가 및 기존 단일 국면 호출은 기존 계약과 종료 방식을 유지한다. 역할 지시가 호출마다 실제 재전달되는 비용도 관측하며 증분 전송량 감소를 모델 토큰·비용 감소로 단정하지 않는다.

각 실제 요청에는 현재 단계가 Phase 1/2 중 무엇인지 명시한 짧은 안내를 전달한다. 컨텍스트 후속·교정·재선택 후 생성·복구도 같은 기준을 적용한다. Phase 1은 두 단계의 기능을, Phase 2는 현재 생성 기능만 설명하며 긴 지시문과 체크리스트를 반복하지 않는다.

| 현재 단계 | 매 요청의 안내 |
|---|---|
| Phase 1 / SELECT | 현재 단계는 Phase 1(그룹 선택)입니다. Phase 2에서는 선택한 그룹의 상세 계약에 따라 실제 답변을 작성합니다. |
| Phase 2 / GENERATE | 현재 단계는 Phase 2(답변 생성)입니다. 선택한 그룹의 상세 계약에 따라 실제 답변을 작성하세요. |

## 검증 기록

#894 자식에서 순수한 `lead_schema_groups` 도메인과 의미 변형 단계의 그룹 분류를 구현했다. 레지스트리 15개가 설명과 구성원 판정을 함께 소유한다. 카탈로그·선택 기록은 불변이며 현재 생성 연결은 실제 outer/inner 계약 전체와 offer·호출·교정/replay 근거를 보존한다. 제어 owner는 중앙 `BoundContract`의 정확한 `schema_id`와 선택/재선택 decoder를 결합한다. compiler는 준비된 의미 계획과 선택 그룹 전체의 factoring/partition을 구분한다. 공통 compiler 인자, 추천 eligibility, 전체 READ 사실, 정확한 member 소속과 decoder source drift를 보완했다. 구문·의존 경계·인덱스 정적 검토와 기존 대표 테스트의 oracle 작성은 마쳤으나 런타임 통과 증거는 아직 없다.

정확한 소스 배정은 [테스트 정책](../../TEST_POLICY.md)의142604→142886 전환이다. 기존 ID·subTest·시간 한도와 모든 기존 assert를 보존한다. 추가 Whole Front 경로의 정적 최대는 plan11개·그룹 factoring24회이며91×15 재컴파일을 추가하지 않는다. 실제 실행 비용·성공 여부는 영향 테스트와 전체 CI에서 측정한다. 로컬 격리4건은 보존하며 Actions의 수동 affected 경로로 정확한 정규 ID를 실행한다.

현재 문서 준비 중 설계의 JSON 예시 16개를 JSON으로 파싱했고, 예시 selector의 필수 필드 다섯 개·닫힌 객체 및 카탈로그 카드 15개의 네 필드를 정적으로 대조했다. 이 검사는 예시의 구문·기본 형태 검토이며 실제 compiler/decoder/권한/상태 전이의 통과 증거가 아니다.

런타임 변경 후에는 기존 정규 ID의 영향 테스트와 전체 다섯 shard CI가 필요하다. Whole Front의 기존 시나리오와 음성 대조를 최대한 유지하고 새 시간 초과 발생 시 즉시 작업을 중단한다. 테스트 소스 증가가 불가피하면 기존 사용자 허용에 따라 측정한 정확한 정책 배정을 먼저 기록한다.

실제 Codex 검증은 최종 소스·현재 Provider/model·원래 두 입력·실제 전체 생성 스키마를 사용하는 별도 유한 캠페인으로 수행한다. 소스/CLI/모델/설정, 스키마 해시, 요청한 한도와 실제 인식/실효 한도, native 결과와 타입 결과, 호출·사용량·지연·실패 및 소유 프로세스 정리를 기록한다. 관측 불가 사용량을 0으로 적지 않는다.

## 남은 작업

현재 59개 완료 조건은 모두 최종 검증 전이다. 수용한 구조 경계를 따라 런타임과 증분 전달·Lead 인지 연결을 완성하고 회귀 테스트, 정확한 새 소스의 영향·전체 CI, 실제 Provider의 두 원래 입력 복구, 크기와 종단 품질 비교, 전항목 감사 및 리뷰·통합·종료를 진행해야 한다. 실제 Provider/사용자 편집 품질의 사후평가 작업은 별도 Backlog이며 이 구현을 중단시키는 조건으로 추가하지 않는다.
