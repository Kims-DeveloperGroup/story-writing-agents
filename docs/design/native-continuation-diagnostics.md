# Native 연속성 실패의 진단과 재선택 수용

이 문서는 [#904](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/904)의
원본 관측, 조사 범위와 수용한 구현 경계를 기록한다. 기준 소스는
`2b36fcb40ea9f2a91fbe12cc449d58b5a857c40e`이다. 아래의 개선 설계는 구현·검증
완료를 뜻하지 않는다. 완료 근거는 이슈와 PR의 현재 검증 기록에서 확인한다.

## 사용자가 겪은 상황

빈 핵심 문서 상태에서 다음 요청을 차례대로 보냈다.

```text
안녕
'포기'라는 주제의 에세이를 2000자 내외로 작성하자.
초안 작성을 도와줘.
```

마지막 요청에는 provider 호출 네 번이 제출됐다. 그룹을 선택한 다음 결과를
작성하는 일반 경로는 두 번이며, 다른 그룹을 직접 재선택하고 그 결과를 작성하면
세 번이 필요하다. SEARCH·READ나 실제 복구가 필요한 경로에는 호출이 더 필요할 수 있다.

| 호출 | 국면과 전달 | 관측한 결과 | 실제 수용 상태 |
| --- | --- | --- | --- |
| 1 | SELECT, thread A의 bootstrap | `consultation.authoring.content` 선택 | 선택 수용 |
| 2 | GENERATE, thread A의 delta | `question.authoring` 재선택 원문 | 수용 전 실행 오류, 이전 선택 유지 |
| 3 | GENERATE, 새 thread B의 bootstrap | 같은 그룹으로 재선택 | 재선택 수용 |
| 4 | GENERATE, thread B의 bootstrap | 질문 결과 | 업무 결과 수용 |

두 번째 응답과 세 번째 응답은 모두 당시 발급된 외부·내부 계약 ID, 카탈로그,
허용 그룹에 맞는다. 재선택 원문은 두 개지만 수용된 재선택은 한 개다. 네 호출은
모두 제출 이력에 남으며 거절된 응답의 비용을 되돌려서는 안 된다.

두 번째 호출의 실패 뒤 usage 관측 범위는 `session_cumulative`에서 `unknown`으로
바뀐다. usage의 관측 범위와 native 맥락 증명은 별도 검사이므로, 이 사실만으로
연속성 실패의 세부 원인을 정하지 않는다.

## 확인한 실행 경계

Codex adapter는 raw 응답과 usage를 받은 후에도 native witness를 확보하지 못할 수
있다. 입력 witness가 있던 호출에서 반환 witness가 없으면 backend는 업무 응답을
디코딩하고 재선택을 수용하기 전에 연속성 오류로 거절한다. 이때 거절 원문이
유효하더라도 현재 선택을 새 그룹으로 바꾸지 않는 것이 기존 수용 규칙이다.

기존 복구는 남은 예산과 실패 facts를 확인해 새 delegate로 한 번만 재시도한다.
새 thread의 bootstrap은 입력 witness가 없으므로 결과 자체를 수용할 수 있지만,
새 witness도 확보하지 못했다면 다음 호출은 증명된 delta 대신 전체 맥락을 전달한다.
원본 세 번째 호출의 `no_live_thread`와 네 번째 호출의 `preservation_unproven`을
그룹 선택 오류나 같은 재선택의 두 번 수용으로 설명하지 않는다.

최종 rollout에는 대응하는 완료 turn들이 있지만 당시 DB·파일·prefix의 검사 전후
스냅샷은 없다. 원본에서 실패한 세부 guard는 **미확인**이다. 최종 기록이 있다는
사실로 당시의 가시성·동시 변경 상태를 재구성하거나 검사가 틀렸다고 단정하지 않는다.

## 진단 결과의 책임

provider 중립 계약이 검사 단계·검증 결과·안정적인 실패 범주·call 연결을 가진
불변 진단 값을 소유한다. 이 값에는 입력 본문, 원시 응답, 파일 경로, SQL,
예외 메시지나 모델 내부 내용을 넣지 않는다. 원래 예외는 명시적인 전달과
exception chaining으로 보존하며 일반 상위 오류명만 남기는 경로를 없앤다.

Codex adapter는 SQLite와 rollout의 관측·검사를 소유한다. backend는 전송 결과와
진단을 전달한다. context service는 현재 invocation·attempt·call·Phase와 실제
수용 상태를 연결하고, orchestration은 기존 예산 안에서 복구 여부를 결정한다.
shell과 trace는 이 공용 사실을 표시하며 자체적으로 재선택이나 재호출을 결정하지 않는다.

성공한 업무 결과에도 witness 관측 실패가 있을 수 있으므로 진단은 예외 경로에만
연결하지 않는다. raw 응답 수신, 타입 검증, 제어 결과 수용, 업무 결과 수용,
후속 맥락 보존 여부를 각각 구분한다.

## 검사 엄격성과 로컬 재관측

현재 검사는 target thread와 무관한 DB·WAL 변경도 관측 실패로 만들 수 있다.
개선은 provider가 완료된 뒤에만, 명시적으로 분류한 일시적 관측 실패에 대해
유한한 로컬 재관측을 수행한다. 재관측은 provider 호출을 추가하지 않으며 같은
invocation·이전 witness·입력 anchor로 모든 guard를 다시 검사해야 한다.

실제 prefix 변경·잘림, inode·실행 범위·설정 불일치, 다른 미완료 turn,
미지원 provenance는 완화하지 않는다. pre-submit 검사를 건너뛰지 않으며,
관측 실패나 알 수 없는 실패를 검증 성공으로 바꾸지 않는다. 유한 재관측 뒤에도
증명하지 못하면 기존 거절·taint·한 번의 복구·예산 처리로 돌아간다.

## 검증과 남은 한계

기존 정규 테스트에서 다음 경우를 독립적으로 확인한다.

- 일반 SELECT→GENERATE는 두 번, 정상 재선택과 새 그룹 결과는 세 번 호출한다.
- 첫 재선택의 수용 전 연속성 실패는 실제 복구 사유와 추가 호출을 연결한다.
- 일시적 관측 실패가 회복되면 provider 추가 호출 없이 witness를 얻는다.
- prefix·범위·미완료 등 실제 실패는 계속 거절하며 불확실성을 지우지 않는다.
- 거절 원문·제출 회계·usage와 예산, 재시도 상한을 보존한다.
- Phase·검사 단계·실패 범주·call/attempt와 수용/복구 상태가 shell과 로그에 남는다.

원본은 읽기 전용으로 보존했다. 새 실제 provider 재현이 필요해지면 별도 격리
workspace와 새 유한 예산을 사용하며 원본 세션에서 Confirm·Apply·Commit하지 않는다.
결정적 회귀와 원본 관측, 새 실제 실행의 결과를 섞어 통과 근거로 취급하지 않는다.
