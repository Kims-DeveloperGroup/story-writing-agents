# 공급자 어댑터 세대의 예약·등록·닫힘 분리

[#1115](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1115)의 작업 기준은 3e499de4e1eee1831b40021366987fea4ca6bc3b입니다.
[부모 이슈 중간보고](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/969#issuecomment-7064157727)에 후속 분리의 사유와 근거를 남겼습니다.

provider_adapter_generations.py가 어댑터 세대 레코드와 기존 잠금 기반 등록부를 맡을 계획입니다.
기존 설정 적용 테스트의 모델과 설정 단언을 그대로 사용해 현행 세대 게시 누락을 확인합니다.
