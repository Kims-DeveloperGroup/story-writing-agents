# Antigravity 호출 결과와 사용량 디코딩 분리

[#1141](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1141)의 기준은 `39ef068eb140c7f2bfe798298d013ef194c04334`와 검증 트리 `40e330641a88498e99a792fab3423f0e6f94434a`입니다.

[#969 중간보고](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/969#issuecomment-7079858599)에 추가 사유와 근거를 남겼습니다. [사전 테스트 영향 검토](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/969#issuecomment-7079968655)를 실제 후보와 대조합니다.

기존 `antigravity_response_decoding.py`에 봉투·사용량과 결과 해석 본체를 배치합니다. 호출 경계의 기존 classmethod·staticmethod와 조건별 클래스 및 복구 패턴 lookup을 보존합니다.

기존 사용량 테스트를 사용해 `cache_read_tokens` 누락의 같은 실패를 확인합니다. 기존 사례의 이동, 확대 선택 및 실제 시간 비교 계획은 [이슈의 구현 계획](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1141#implementation-plan)에 있습니다.
