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

### #894 통합 완료와 #895의 시작 경계

#894는 부모 브랜치의 `934340c8548f50cdbcc26e58d0868aaf18c0387e`에
squash 통합했고 완료 조건7개와 종료·Project Done을 확인했다. 검증 소스
`d87884a2`와 통합 tree는 동일하다. 전체5개 CI shard와 집중3개 ID가 통과했고,
실제 Codex CLI0.164.0/Python3.11의 SELECT·GEN 두 요청도 typed 검증을 통과했다.
현재 설정의 모델·effort 기본값을 유지했으며 실제 resolved model은 추정하지 않는다.
프로세스 종료 후 PID·공유 그룹 소멸을 별도 감사했다.
[최종7조건 증거](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/894#issuecomment-6495921983)에
실패 이력·원본 정리 결과·추가 감사와 실제 schema/input 해시를 함께 보존한다.

#895는 이 기반에서 실행 엔진을 연결한다. 예를 들어 인사 요청은
SELECT→GENERATE의 두 번, 유효한 직접 재선택은 SELECT→GENERATE(재선택)→GENERATE의
세 번을 같은 기존 한도에서 사용해야 한다. 선택 제어를 받은 시점에는 업무 결과를
발행하거나 완료 checkpoint를 만들지 않고, 실제 업무 결과에서만 기존 승인 경계를 지난다.
READ 뒤에도 원래 선택 기록을 보존하고 현재 그룹의 유효성과 fresh binding을 다시 확인한다.

실행 연결은 구현했고 첫 회귀의 fixture·API 기대값 오류를 보완해 재검증한다. #895만으로 사용자 프로필을 활성화하지 않으며,
#896의 전송·짧은 현재 Phase 안내가 모두 갖춰질 때 안전하게 연결한다. 부모의
59개 조건 전체와 원래 입력의 종단 검증은 계속 후속 작업이다.


#894 자식에서 순수한 `lead_schema_groups` 도메인과 의미 변형 단계의 그룹 분류를 구현했다. 레지스트리 15개가 설명과 구성원 판정을 함께 소유한다. 카탈로그·선택 기록은 불변이며 현재 생성 연결은 실제 outer/inner 계약 전체와 offer·호출·교정/replay 근거를 보존한다. 제어 owner는 중앙 `BoundContract`의 정확한 `schema_id`와 선택/재선택 decoder를 결합한다. compiler는 준비된 의미 계획과 선택 그룹 전체의 factoring/partition을 구분한다. 공통 compiler 인자, 추천 eligibility, 전체 READ 사실, 정확한 member 소속과 decoder source drift를 보완했다. 구문·의존 경계·인덱스 정적 검토와 기존 대표 테스트의 oracle 작성은 마쳤으나 런타임 통과 증거는 아직 없다.

정확한 소스 배정은 [테스트 정책](../../TEST_POLICY.md)의142604→142887 전환이다. 기존 ID·subTest·시간 한도와 모든 기존 assert를 보존한다. 추가 Whole Front 경로의 정적 최대는 plan11개·그룹 factoring24회이며91×15 재컴파일을 추가하지 않는다. 실제 실행 비용·성공 여부는 영향 테스트와 전체 CI에서 측정한다. 로컬 격리4건은 보존하며 Actions의 수동 affected 경로로 정확한 정규 ID를 실행한다.

현재 문서 준비 중 설계의 JSON 예시 16개를 JSON으로 파싱했고, 예시 selector의 필수 필드 다섯 개·닫힌 객체 및 카탈로그 카드 15개의 네 필드를 정적으로 대조했다. 이 검사는 예시의 구문·기본 형태 검토이며 실제 compiler/decoder/권한/상태 전이의 통과 증거가 아니다.

런타임 변경 후에는 기존 정규 ID의 영향 테스트와 전체 다섯 shard CI가 필요하다. Whole Front의 기존 시나리오와 음성 대조를 최대한 유지하고 새 시간 초과 발생 시 즉시 작업을 중단한다. 테스트 소스 증가가 불가피하면 기존 사용자 허용에 따라 측정한 정확한 정책 배정을 먼저 기록한다.

실제 Codex 검증은 최종 소스·현재 Provider/model·원래 두 입력·실제 전체 생성 스키마를 사용하는 별도 유한 캠페인으로 수행한다. 소스/CLI/모델/설정, 스키마 해시, 요청한 한도와 실제 인식/실효 한도, native 결과와 타입 결과, 호출·사용량·지연·실패 및 소유 프로세스 정리를 기록한다. 관측 불가 사용량을 0으로 적지 않는다.

### #895에서 수용한 실행 경계

현재 구현은 진행 중이다. 서비스가 불변 `ContextGroupInvocationState`로 요청 근거,
선택 이력, 준비된 계약을 함께 전달한다. 정상 결과와 실패 사실에도 같은 상태를
전달하므로 복구나 교정이 새 호출 예산을 만드는 것을 막는다. 현재 작업·리비전·권한
근거의 생성은 오케스트레이터가 맡고, 실제 호출 수는 기존 서비스 원장이 집계한다.

| 예 | 필요한 호출과 결과 |
| --- | --- |
| 첫 선택 후 답변 | SELECT와 GENERATE가 각각 한 번이다. 선택만으로 업무를 승인하지 않는다. |
| 상세 계약을 본 뒤 다른 그룹 선택 | 현재 GENERATE가 재선택을 반환하고 다음 GENERATE로 이어진다. 별도 SELECT는 추가하지 않는다. |
| SELECT 시작 시 잔여 한 번 | 답변 생성까지 갈 수 없으므로 Provider 제출 전에 중단한다. |
| 마지막 GENERATE가 유효한 재선택을 반환 | 새 선택을 이력에 기록한 뒤 예산 소진으로 STOP한다. 답변이나 승인·완료 체크포인트를 만들지 않는다. |
| READ로 자료가 추가됨 | 원래 선택 기록은 보존한다. 현재 그룹이 유효하면 새 binding으로 생성하고, 요청 근거나 그룹 자격이 달라지면 SELECT로 돌아간다. |

교정은 거절 당시의 정확한 계약을 사용하고, 새 자료를 반영하는 fresh 호출과 구별한다.
인증 replay도 기존 공유 한도에 포함한다. 원래 계약에 SEARCH/READ가 허용됐다면
재실행에서 그 계약을 축소하지 않는다. 잔여 예산이 조회와 이후 단계까지 감당하지
못하면 원래 인증 실패와 선택 이력을 보존하고 재실행 전에 중단한다.
기존 호출 상한과 교정 예약을 유지하며,
한 번만 허용된 일반 경로를 자동으로 두 번으로 늘리지 않는다. 사용자 경로 활성화와
매 요청의 짧은 Phase 안내는 #896에서 전송 계약과 함께 연결한다.

교정은 기존 일반 호출 상한과 별도 예약 1회를 합친 범위 안에서 한 번만 진입한다.
예를 들어 최초 한도 2회·전체 허용량 3회에서 첫 SELECT가 거절됐다면 남은 2회로
SELECT 교정과 GENERATE를 마칠 수 있다. 전체 허용량이 2회라면 남은 1회로는
그 경로를 시작하지 않고 원래 거절을 보존한다. 최초 호출 한도는 그대로이며,
유효한 마지막 재선택으로 STOP한 흐름을 이 교정 예약으로 다시 시작하지 않는다.

#895의 테스트 배정은 raw143435와 청구 helper8을 합친143443이다. 기존 대표 사례를
보존하며541행, 정책 회귀13행과 helper2행을 추가한 정확한 배정이다. ID2527·전체
subTest mapping·18000/3600초와 Whole Front 사례는 유지한다.

### #895 첫 CI와 fixture 보완

`d55e0b34`의 [전체 CI](https://github.com/Kims-DeveloperGroup/story-writing-agents/actions/runs/40884771932)는
Fast10개와 Whole Front를 통과했다. Whole Front는1153.550초였으며 timeout은 없었다.
정규1∼4 shard는 새 fixture의 stage 오류6개, 교정 script·불변 상태 관측 오류2개,
새 `group_state` 필드가 빠진 API 기대값2개로 실패했다. 모든 보고서는 실제 실행·환경·
정리를 확인했으며 이 실행을 전체 통과 증거로 쓰지 않는다.

[Codex R1의 clean receipt](https://github.com/Kims-DeveloperGroup/story-writing-agents/pull/900#issuecomment-6498575068)는
검토 commit `d55e0b3435`와 일치한다. 생산 guard를 유지하고 ordinary front fixture,
교정 invocation별 응답 묶음, 반환 후 원본 오류의 최신 STOP facts를 보완한다.
명시적인 필드 목록에는 새 API 필드만 추가한다. 기존 호출 수·STOP 기대와 assert·
ID·subTest·Whole Front 사례는 유지하며, 보완 소스의 영향11개 및 전체 CI는 다시 확인한다.

### #895 READ 전달·인증 재생 fixture의 추가 교정

`2fe8806f`의 영향11개는6개 통과·5개 실패였다. READ 다음 호출의 동결 offer에는
완료 목록과 이번 전달 목록을 구분하므로 delivery 사실과 prepared plan의 전체
READ 입력이 일치하는지 검증한다. ordinary record가 제거하는 unknown key 대신
기존 string 필드의 타입을 위반해 실제 거절을 검증한다. 빈 검색 종류나 0 정책은
생산 가드가 허용하지 않으므로 실제 0-hit SEARCH를 소진한 후 SELECT→GEN의 인증
실패·재생을 진행한다. 앞선 호출도 같은 예산에 포함해4회 성공/3회 중단을 대조한다.
기존 Source drift callback은 실제 helper의 positional 인자로 전달한다. 생산 코드는
Codex 검토 소스와 동일하고, 보완한 테스트의 통과 여부는 새 CI에서 확인한다.

### #895 grouped STOP 반환과 실제 callback 계약

`fd0de712`의 영향11개는8개 통과·3개 실패였다. 새 revision seed에는 실제 input ID와
revision을 모두 명시하고, 인증 hook은 동기 함수에서 실제 승인을 반환해 source-veto를
검증한다. 행 label과 최초 호출 한도도 관측한다. frozen 교정이 새 제출 없이 STOP한
경우에는 원래 거절 사실을 전달한 뒤 일반 CONTINUE fallback으로 가던 분기를 확인했다.
이 grouped STOP만 기존 바깥 거절 처리로 올려 원래 accounting·불확실성·참조 정리와
RETRY 의미를 보존한다. 일반 fallback은 유지하며 예산식·API·schema는 변경하지 않는다.
이후 소스의 영향·전체 CI와 후속 검토를 확인하기 전에는 완료로 계산하지 않는다.

### #895 domain 결과의 revision 확인

revision이 바뀐 사례의 결과는 wire 필드 이름이 아니라 기존 정규화된 domain 결과로
검사한다. 예를 들어 `context-task@2`는 `authoring_scope.task`에서 확인한다.
잘못 사용한 `scope` 키만 바로잡고 revision 변경 시 새 SELECT와 원래 이력 보존
검증은 유지한다. 생산 코드나 소스 배정은 추가 변경하지 않는다.

### #895 검토 입력과 계약의 일치

검토 요청에 `current-input` 자료가 있으면 계약의 `input_ids`에도 같은 ID를 선언한다.
실제 자료 목록과 계약 입력 목록의 일치를 검증하고, 이후 실제 READ 근거가 추가되거나
handoff에서 제거되는 기존 사례를 유지한다. 이 보완은 READ나 권한을 새로 만들지 않는다.

### #896 구현 시작 경계

#895 통합 리비전 `2d43ce2b`를 기준으로 실제 wire와 증분 전달을 연결한다.
최초 SELECT에는 원래 요청·사실·전체 카드를 보내고, 다음 GENERATE는 검증된
연속성 근거가 있을 때 변경분과 현재 제어만 보낸다. 독립 호출·복구는 전체 입력을
실제로 재전달하되 현재 국면의 출력 계약을 유지한다. 매 요청의 짧은 Phase 1/2
안내와 native 기록의 호출 전후 증명, 기존 예산 안의 활성화는 이 자식에서 구현·검증한다.
현재 이 항목은 시작 경계의 기록이며 구현·실제 Provider 성공을 주장하지 않는다.

### #896의 요청 예시와 검증 경계

아래 JSON은 전달 방식을 설명하는 발췌다. 실제 호출에서는 Host가 현재
`BoundCallContract`에서 생성한 ID·카탈로그·전체 출력 스키마를 사용한다.
예시의 문자열 ID나 빈 카탈로그로 실제 요청을 대신하지 않는다.

첫 SELECT는 원래 요청과 관련 사실, 현재 허용 그룹의 전체 카드를 보낸다.
`issued_contract`는 이번 호출에서 허용한 outer/inner 계약을 가리키며,
`remaining_provider_calls`는 현재 공유 예산의 파생값이다.

```json
{
  "response_contract": {
    "schema_selection": {
      "phase": "select",
      "issued_contract": {
        "call_id": "현재 호출 ID",
        "contracts": [
          {"outer_schema_id": "현재 outer ID", "result_schema_id": "현재 선택 계약 ID"}
        ]
      },
      "remaining_provider_calls": 3,
      "context_delivery": {"mode": "bootstrap", "context_ref": "현재 맥락 ID"},
      "catalog": {
        "catalog_id": "현재 카탈로그 ID",
        "catalog_version": "현재 카탈로그 버전",
        "groups": [
          {
            "group_id": "consultation.review_only",
            "definition": "원문에 대한 평가와 개선 의견",
            "selection_criteria": "사용자가 원문 검토를 요청하고 개정 작업은 요청하지 않은 경우",
            "result_effect": "검토 의견을 답변하며 문서 변경 권한을 부여하지 않음"
          }
        ]
      }
    }
  }
}
```

위 카드는 형태를 보여 주는 한 항목이다. 실제 `groups`에는 현재 허용한 카드를
모두 넣는다. 선택 응답은 내부 제어다. 유효한 선택만으로 사용자 요청을 완료 처리하지 않는다.
이어지는 GENERATE에서 문맥 보존을 확인하면 `context_delivery.mode`는 `delta`가
되고, `base_context_ref`와 현재 제어를 보낸다. 변경 없는 요청·사실·전체 카드를
새 stdin에 반복하지 않으며 `catalog_id`와 `active_selection`으로 현재 결합을
명시한다. 추가·교체·제거는 `context_changes`에 전달하고 생략은 보존으로 해석한다.

매 요청에는 현재 계약에서 결정한 다음 안내 중 하나를 정확히 제공한다.

- SELECT: “현재 단계는 Phase 1(그룹 선택)입니다. Phase 2에서는 선택한 그룹의 상세 계약에 따라 실제 답변을 작성합니다.”
- GENERATE: “현재 단계는 Phase 2(답변 생성)입니다. 선택한 그룹의 상세 계약에 따라 실제 답변을 작성하세요.”

단순히 thread ID가 같다는 이유로 delta를 허용하지 않는다. 현재 실행 workspace와
정확한 native 이력 파일, 호출별 stdin, 완료된 turn, 이전 파일 prefix와 설정의
연속성을 연결한다. `state_5.sqlite`는 소유한 후보에서 정확한 thread ID의 경로를
찾는 데만 쓰며 실효 `sqlite_home`이라고 추정하지 않는다. 다른 thread 탐색이나
DB 복구·마이그레이션·checkpoint 쓰기는 하지 않는다. read-only WAL 트랜잭션의
일반적인 읽기 lock과 SHM read-mark 조정은 SQLite의 정상 읽기 절차다. 이를
애플리케이션 데이터 변경과 구별하며, sidecar를 새로 만들거나 SHM의 mtime만으로
이력 교체를 판정하지 않는다. 이 구분은 [SQLite WAL 형식](https://www.sqlite.org/walformat.html)의
1.3·2.3.1절과 [읽기 트랜잭션 설명](https://www.sqlite.org/wal.html#concurrency)에 따른다.

증거가 없거나 파일 교체·압축·rollback·fork 등으로 연속성을 확인할 수 없으면
원래 요청·필요 사실·현재 전체 카드를 실제로 재전달하는 bootstrap 또는 기존 STOP을
적용한다. 이때 출력 계약은 현재 Phase의 계약을 유지한다. 이미 전송한 호출과
거절된 원문 응답은 실제 사용량·실패 기록으로 남긴다.

전송이 끝난 맥락과 업무 검증까지 수락된 맥락은 각각 보존한다. READ로 막 얻은
사실은 다음 실제 입력에 포함되기 전까지 “이미 전달됨”으로 표시하지 않는다.
진행 중 교정의 논리 기준점은 수락된 SELECT·SEARCH·READ 입력에서 전진한다.
완료 GENERATE의 실제 전송 스냅샷은 별도로 남기고, 최종 업무 수락은 DONE과
기존 COMMITTED 원장으로 확인한다. publish 뒤 기준점을 다시 검증·대입하여
이미 완료된 업무에 새 실패를 만들지 않는다.
native 증거만으로 delta를 허용하지도 않는다. 기존 호출 원장의 scope·attempt·세대와
실제 제출한 call/accounting, 현재 checkpoint의 일치를 확인한다. 교정 과정에서
수락 맥락이 과거 호출이고 전송 맥락이 최신 호출일 수 있으므로 유효한 원장 prefix를
구분한다. SELECT를 READY로 승격하거나 별도 원장·예산 계수를 만들지는 않는다.
이 절은 구현 검토용 설명이며, #896의 영향 테스트·전체 CI와 실제 현재 Provider의
연속 호출 검증이 끝나기 전에는 완료 증거로 사용하지 않는다.

### 추가 선택 비용과 기존 task 예산

선택과 재선택도 실제 task 호출에 포함한다. 현재 12회 한도에서 3명 전문가의
초안을 만드는 기본 경로는 front 두 호출·전문가 세 호출·synthesis 한 호출로
6회를 쓴다. 이후 같은 task의 문서 준비 추천을 요청하면, 미래 검토4회와 준비1회를
보호한 front 여유는 1회다. SELECT 완료 최소량2보다 작으므로 추가 Provider 제출
없이 STOP한다. 교정용 예약을 새 SELECT 시작에 쓰거나 완료로 기록하지 않는다.

해당 회귀는 기본 경로의 예산 거절을 명시적으로 검사한다. 기존 matching Apply의
task/revision·source·후보·3명 검토·파일·memory·objective 검증은 source 생성만
명시적인 trusted 단일 국면 fixture로 분리해 유지한다. 여기서는 실제 기록한
source5회와 후속 기본 두 국면2회·준비1회·검토3회·synthesis1회가 합계12회다.
초기 source fixture 밖에서는 기본 두 국면이 켜졌는지도 검사한다. 이 조건을
현재 기본 경로의 전체 성공으로 계산하지 않으며, 예산 환불·재설정이나 가짜
history로 여유를 만들지 않는다. 최종 실행 통과는 별도로 확인해야 한다.

교정 용량 회귀의 `action_exhausted` 한 row도 단일 국면의 물리 finalizer 거절을
검사하는 조건으로 남긴다. 기본 grouped 경로는 frozen SEARCH의 미래 비용이
부족하면 제출 전에 STOP하므로, 그 경로는 별도 기존 T5 oracle로 확인한다.
이 row의 단일 국면 설정을 다른 ordinary 입력으로 넓히거나 기본 활성화의
통과 증거로 계산하지 않는다. 순수 스키마 export 검증은 물리 호출이 아니다.

## 남은 작업

현재 59개 완료 조건은 모두 최종 검증 전이다. 수용한 구조 경계를 따라 런타임과 증분 전달·Lead 인지 연결을 완성하고 회귀 테스트, 정확한 새 소스의 영향·전체 CI, 실제 Provider의 두 원래 입력 복구, 크기와 종단 품질 비교, 전항목 감사 및 리뷰·통합·종료를 진행해야 한다. 실제 Provider/사용자 편집 품질의 사후평가 작업은 별도 Backlog이며 이 구현을 중단시키는 조건으로 추가하지 않는다.

취소 도메인 회귀도 준비 과정과 취소 대상을 구분한다. 기존 source 생성과 두 번의
준비 추천 front에만 명시적인 단일 국면 fixture를 적용하면 실제 준비까지 7회이며,
factory를 복원한 뒤 취소 자체는 SELECT→GENERATE 2회로 검사한다. 빈 continuation의
선택 보존과 종료된 objective의 재사용 거절도 유지한다. 기본 두 국면 준비는 같은
과정에 10회를 써서 기존 미래 비용 보호 정책에 걸리므로, 이 fixture를 기본 경로의
전체 성공으로 계산하지 않는다. 예산을 늘리거나 초기화하지 않는다.

2라운드를 마친 뒤의 successor front는 별도의 고정 2회 예산을 사용한다. 이때 기본
두 국면의 SELECT→GENERATE는 가능하지만 SEARCH까지 포함한 3회는 허용하지 않는다.
기존 전문가 회귀의 마지막 SEARCH→질문 구간만 명시적인 단일 국면 fixture로 두어
원래 2회의 검색·질문 검증을 보존한다. 앞선 draft·검토 과정의 기본 두 국면과 원래
라운드·파일·이력 검증은 유지하며 successor 예산 식을 바꾸지 않는다.

실제 SEARCH schema가 포함된 교환 JSONL은 LF 경계로 읽는다. 유효한 JSON 문자열의
Unicode U+2029를 줄 경계로 해석하지 않으며, 잘못된 물리 JSON 행은 계속 거절한다.

첨부 참조의 승인된 prelude는 grouped 논리 호출 전체에서 유지한다. SELECT 이후에도
GENERATE·SEARCH·재선택·교정의 전체 논리 맥락에 같은 원문이 남아야 한다. 검증된
native delta에서는 변경 없는 참조 bytes를 다시 보내지 않으며, 원장을 확인하지
못하는 bootstrap에는 원문을 실제로 포함한다. 단일 국면의 consume-once 전달과
별도 invoke의 기존 참조 준비·source/workspace 재검증은 유지한다.

이번 witness 계약 소비로 검사 대상 AST 그래프에는 service→provider_adapter edge만
추가됐다(노드164 유지, edge738→739). provider-neutral 하향 계약 의존성이며 cycle과
RAG 직접 importer는 변하지 않는다. 경계 회귀는 이 정확한 edge와 기존 엄격한
의존성·export·파일 접근 검증을 함께 고정한다.
