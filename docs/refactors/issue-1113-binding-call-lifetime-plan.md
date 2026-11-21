# 바인딩의 호출 진입·퇴역·마지막 정리 분리

[#1113](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1113)의 작업 기준은 8195f07c5beb89574055907c8d8433a18f89990d입니다.
[부모 이슈 중간보고](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/969#issuecomment-7062870753)에 후속 분리의 사유와 근거를 남겼습니다.

provider_binding_call_lifetime.py가 바인딩별 잠금과 상태 전이를 맡을 계획입니다.
기존 조기 해제 단언과 기존 집중·영향 사례의 기준을 확인합니다.
