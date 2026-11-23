# Codex CLI 완료 결과와 인증 분류 분리 계획

[이슈 #1167의 구현 계획](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1167#implementation-plan)에 따라 어댑터에서 완료 결과 해석과 인증 분류를 옮깁니다. 원래 제출 진단과 실패 회계, 임시 파일 정리 및 작업공간 재확인을 같은 순서로 유지합니다.

- 기준은 `427565c3911e5bea7c7149b7f351e1424b8ed5d0`입니다. [기준 전체 CI](https://github.com/Kims-DeveloperGroup/story-writing-agents/actions/runs/45140209716)의 기존 집중 2개와 장시간 native 관측을 포함한 영향 10개를 대조했습니다.
- 완료 턴의 과거 401을 제외하는 조건만 생략한 기준과 후보에서 기존 `tests/test_provider_auth_recovery.py:240`의 같은 단언이 실패하는지 검증합니다.
