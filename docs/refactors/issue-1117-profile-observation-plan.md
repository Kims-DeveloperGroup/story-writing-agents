# 실행 프로필의 확인·상관·스레드 상태 분리

[#1117](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1117)의 작업 기준은 4a71ad52c8c4e596d1f8faf5a5826bf1f5b5e5c0입니다.
[부모 이슈 중간보고](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/969#issuecomment-7065741660)에 후속 분리의 사유와 근거를 남겼습니다.

provider_execution_profile_observations.py가 상태 초기화·전이·조회를 맡을 계획입니다.
기존 설정 적용 테스트의 확인 완료→대기→확인 완료 단언을 재사용합니다.
