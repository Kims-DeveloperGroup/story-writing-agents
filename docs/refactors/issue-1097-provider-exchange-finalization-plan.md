# 에이전트 교환 종료 기록과 진단 처리 분리 계획

[#1097](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1097)는 `cf09d5f110945486245776eb7159b92201e9b5b8`에서 시작합니다.
[부모 중간보고](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/969#issuecomment-7045004916)에 원본 범위와 분리 근거를 기록했습니다.

기존 종료 처리 함수 일곱 개와 문맥 종료 기록 객체, 종료 코드 매핑 두 개의 실제 소유권을
`agent_exchanges/provider_finalization.py`로 옮깁니다. 기존 기록 순서와 진단, 호출자 오류 보존을 확인합니다.

같은 기존 자식 프로세스 사례에서 성공 종료 기록 한 줄을 임시 검증에서 생략해 원래 순서 단언을 대조합니다.
기존 집중 사례 3개, 확대 사례 8개와 전체 CI에서 기존 기록 결과를 검증합니다.
