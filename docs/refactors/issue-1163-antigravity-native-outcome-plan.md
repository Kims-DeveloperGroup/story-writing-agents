# Antigravity CLI 결과와 인증 분류 분리

[#1163의 계획](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1163#implementation-plan)은 실행 완료 결과 해석과 인증 분류를 제안 모듈 `antigravity_native_outcome.py`에 둡니다. 역할 재검증과 프로세스 수명은 어댑터가 맡습니다.

- 기준은 `0587ae14b2ef5ad449e6f0fd8448650c63b583ab`입니다. [기준 전체 CI](https://github.com/Kims-DeveloperGroup/story-writing-agents/actions/runs/45118984997)에서 기존 집중 2개와 영향 16개를 확인했습니다.
- 기존 스트림 진단과 종료 코드, 인증 분류와 재시도 판단, 부분 시간 초과·잘린 결과의 예외와 전송 계상을 유지합니다.
- 기존 `exc.timed_out = True` 문장만 생략해 `tests/test_antigravity_adapter.py:1021`의 기존 단언이 기준과 후보에서 같은 결과를 내는지 확인합니다.
