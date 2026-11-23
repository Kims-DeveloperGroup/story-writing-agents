# 공급자 실행 프로필 조회와 최초 성공 확인 분리 계획

[#1165의 계획](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1165#implementation-plan)에 따라 `backend.py`의 여섯 정의를 기존 공급자 실행 프로필 관측 모듈로 옮깁니다. 조회 순서와 기존 오류, 확인 갱신과 최초 진단에 사용하는 두 잠금 구간을 유지합니다.

- 기준은 `0bf763223c515554c35b53b1a7ddf99518fe25a5`입니다. [기준 전체 CI](https://github.com/Kims-DeveloperGroup/story-writing-agents/actions/runs/45128484073)에서 기존 집중 2개와 영향 9개를 확인했습니다.
- 기존 A→B→A 전환 사례와 테스트 상한을 보존합니다. 최초 확인 진단만 생략하는 같은 결함이 기준과 후보의 `tests/test_provider_adapter_generations.py:184`에서 검출되는지 확인합니다.
