# Native 연속성 실패의 진단과 재선택 수용

이 문서는 [#904](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/904)의
원본 관측, 조사 범위와 수용한 구현 경계를 기록한다. 기준 소스는
`2b36fcb40ea9f2a91fbe12cc449d58b5a857c40e`이다. 아래는 구현의 책임과 보장
범위를 설명한다. 실제 실행·수용 판정의 근거는 이슈와 PR의 현재 검증 기록에서 확인한다.

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

## Witness가 확인하는 파일과 범위

이전 입력 보존 검사가 읽는 파일은 해당 native thread의 Codex rollout JSONL이다.
경로는 witness의 `rollout_path`에 저장하고, native SQLite의 thread 레코드로 연결을
확인한다. 예를 들면 `CODEX_HOME/sessions/YYYY/MM/DD/rollout-…-<thread>.jsonl`이다.
Story 협업 로그는 이 검사에서 native 이력을 대신하지 않는다.

기준 소스의 witness는 thread·rollout·call·turn ID, 파일의 device/inode, 당시
prefix의 길이와 SHA-256, payload 해시, 실행 범위와 설정의 digest를 연결했다.
사용자의 완화 지시에 따라 파일의 물리적 동일성 대신 의미 있는 입력 turn의
논리적 계보를 확인하는 방향으로 변경한다. 파일 정보와 설정은 관측 자료로 구분한다.

필수 연결은 같은 thread·현재 실행 범위, 현재 입력에 연결된 유일한 완료 turn,
실제 마지막 full bootstrap 이후 이전 입력들의 순서와 payload 해시다. 입력 계보의
canonical digest/count로 중간 READ delta의 소실도 확인한다. SEARCH/READ로 최근
수용 지점이 갱신됐다는 이유만으로 full-bootstrap 기준을 재설정하지 않는다.

이 증거의 범위는 이력과 입력의 연속성이다. 모델의 이해·판단·답변 품질은 별도로
평가해야 하며, 토큰 usage의 관측 가능 여부도 witness와 별개다.

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

## 최소 조건과 응답 수용의 분리

기준 소스는 target thread와 무관한 DB·WAL 변경도 관측 실패로 만들 수 있다.
이전 inode·전체 raw prefix 바이트·전역 DB/WAL/SHM stat·비핵심 metadata/설정이나
고정 CLI 버전의 동일성은 연속성 실패 조건에서 제외한다. continuation은 알려진
rollout 경로를 안전하게 읽으며, DB는 최초 thread 경로 발견에 사용한다.
경로 범위·symlink 제외·안전한 regular-file 열기·읽기 한도는 유지한다.

의미 있는 입력 계보에서 확인된 thread·범위·payload·순서 모순과, 필요한 기록을
관측하지 못한 상태를 구분한다. 정상 metadata 추가는 맥락 소실의 근거가 아니다.
reset/fork/rollback도 이름만으로 거절하지 않고 실제 유효 계보의 의미를 적용한다.
효과를 안전하게 해석하지 못하는 기록은 `HISTORY_EFFECT_UNKNOWN` 관측 불가로 남긴다.
미지원 record 안에 적힌 임의 문자열을 입력이나 turn 완료 증거로 채택하지 않는다.

응답 수용과 다음 delta 가능 여부도 분리한다. 현재 call의 실제 pre-submit 검증,
정상 native 종료, 실제 event에서 보고된 thread 및 정확한 wire 결합을 확인했다면
post-call의 단순 관측 불가만으로 받은 응답을 재요청하지 않는다. 요청의 thread 값을
그대로 반환한 사실만으로 실제 native thread를 관측했다고 간주하지 않는다.

응답은 기존 decoder와 Host checks를 그대로 통과해야 한다. witness는 `None`으로
남기고 다음 물리 호출은 필요한 전체 맥락을 전달하므로 미확인 상태를 검증된 delta로
승격하지 않는다. 명확한 thread·범위·wire·입력 계보 모순이나 timeout, 취소,
불확실한 제출·종료에는 이 수용 완화를 적용하지 않는다. 예상 밖 로컬 사후 검사
예외는 원래 원인을 남기는 관측 불가로 처리하며, native 실행의 불확실성과 구분한다.
기존 한 번의 복구와
호출 예산은 늘리지 않는다. 강화 조건은 실제 필요가 입증될 때 검토한다.

## 검증과 남은 한계

기존 정규 테스트에서 다음 경우를 독립적으로 확인한다.

- 일반 SELECT→GENERATE는 두 번, 정상 재선택과 새 그룹 결과는 세 번 호출한다.
- 사후 단순 관측 불가의 재선택은 기존 업무 검증으로 수용하고, 다음 full 생성까지
  세 번 호출하며 같은 재선택을 다시 요청하지 않는다.
- 명확한 입력 계보 모순 때문에 재선택을 거절했다면 실제 복구와 네 번째 호출을 연결한다.
- metadata 추가·다른 thread의 DB 쓰기·내용 그대로 파일 교체는 정상 허용한다.
- 실제 필수 입력·중간 READ delta 소실/변조, 범위·turn 모순은 검출한다.
- 거절 원문·제출 회계·usage와 예산, 재시도 상한을 보존한다.
- Phase·검사 단계·실패 범주·call/attempt와 수용/복구 상태가 shell과 로그에 남는다.

원본은 읽기 전용으로 보존했다. 새 실제 provider 재현이 필요해지면 별도 격리
workspace와 새 유한 예산을 사용하며 원본 세션에서 Confirm·Apply·Commit하지 않는다.
결정적 회귀와 원본 관측, 새 실제 실행의 결과를 섞어 통과 근거로 취급하지 않는다.
