# Lead 두 국면의 최종 native 검증

이 문서는 #898의 기술적 검증 준비와 실제 실행 증거를 구분한다. 구현 베이스는
부모 #890의 `82694c07b2476d732bc67f36f8c929cdc5d6338d`이며, 실행 직전에
검증 도구·설정·최종 source와 tree를 다시 고정한다. 아직 이 source의 실제 실행은 하지 않았다.

## 두 원래 입력

| 입력 | 확인할 결과 |
| --- | --- |
| `안녕` | 실제 전체 runtime schema로 유효한 typed 답변을 수용한다. |
| `2000자 내외의 에세이를 작성하자. 주제는 '포기'야` | 실제 native 결과에 따른 질문 또는 다음 단계에 도달한다. |

각 입력은 별도 qualified workspace와 conversation에서 실행한다. 현재 설정한
Provider/model을 사용하며 가짜 응답이나 축소 대체 schema를 넣지 않는다. 실제 GEN의
typed 질문·추천과 host fallback을 구분한다. 도구가 Confirm·Apply·Commit을 승인하지 않는다.

## 실행 전 준비

기존 #896 scoped 도구의 source·증거·종료된 admission을 보존한다. 현재 검증 도구로
명시적으로 supersede한다면 새 source 비용과 이전608행의 관계를 기록한다. 별도 도구를
추가 유지한다면 새 코드 전체를 청구한다. helper를 포함한 실제 비용을 측정한 뒤 좁은
정책 배정을 적용하고, 기존2527 ID·subTest 상한·18000/3600초를 유지한다.

새 admission은 도구와 고정 source가 검토 가능한 상태가 된 뒤 확정한다. 이전40회 소진,
격리4건, 종료된 #894/#896 범위를 reset하거나 재사용하지 않는다. CLI 실행·시간·동시
프로세스의 hard 한도와 token·cost의 집행/관측 가능 범위를 구분한다. 실제 승인 전에는
Provider를 실행하지 않는다.

## 호출과 결과의 증거

SELECT·GENERATE·컨텍스트·교정·재선택의 모든 실제 CLI 시도를 센다. 최종 runtime의
`--output-schema` 파일과 실제 stdin, source/config/CLI hash, 현재 Phase 안내, full/delta,
실제 call_id와 #897의 관측·회계·사용량 보고를 연결한다. native 종료와 typed 수용,
현재 task/revision/state, 실패 원문, 지연 및 소유 process-group 정리를 보존한다.

출력 cap 인자를 쓰지 않았다면 effective cap은 unknown으로 기록한다. 모델의 context
window나 관측된 최대 출력 길이를 설정된 출력 한도로 바꾸지 않는다. 원시 누적 usage는
출처가 입증된 호출별 차분과 구분하며 미관측 수치·금액을0으로 적지 않는다. 종료나 spawn이
불확실하면 추가 호출 전에 중단하고 그 상태와 사용한 범위를 보존한다.

## 완료 판정

두 입력의 실제 결과와 현재 검증·리뷰를 확보한 뒤 [59개 조건의 연결표](lead-two-phase-schema-selection.md)를
현재 증거와 대조한다. 과거 성공·CI·준비된 schema만으로 최종 실호출을 통과 처리하지 않는다.
실제 Provider/사용자 품질 비교는 #806 사후평가 Backlog이며 현재 기술 검증과 구분한다.
