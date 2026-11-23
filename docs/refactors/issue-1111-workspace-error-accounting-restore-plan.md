# 제출 후 작업공간 확인 실패 회계 복원 계획

[이슈 #1111의 구현 계획](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1111#implementation-plan)에 따라 기존 제출 후 작업공간 확인에서 누락된 실패 회계 함수의 import를 복원합니다.

- 기준 커밋은 `7e2d50f2ca6dfac55dcb8e6fe0922560bd33b714`입니다. [기준 전체 CI](https://github.com/Kims-DeveloperGroup/story-writing-agents/actions/runs/45162727605)
- `backend.py`에서 이미 사용하는 `provider_adapter` import 목록에 `record_provider_failure_accounting` 한 줄을 복원합니다.
- 동일 작업공간 확인 실패를 기준과 후보에서 재현합니다. 원래 `ProviderInvocationError`가 전달되고 제출 상태 `SUBMITTED`와 캡처된 2바이트가 기록되는지 확인합니다.
- 기존 준비·제출 상태, 제출된 컨텍스트 격리, 실제 입력 캡처 테스트 세 개와 전체 CI를 실행합니다.
