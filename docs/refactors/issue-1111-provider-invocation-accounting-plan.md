# Backend의 공급자 호출 회계와 경과 시간 관찰 분리

[#1111](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1111)의 작업 기준은 `8c4b422d545ec0f3b84da3b5a2bd31261a4a49cc`입니다. [부모 이슈 중간보고](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/969#issuecomment-7060580854)에 추가 사유와 근거를 남겼습니다.

경과 시간, payload 구성 요소, 실패 값 확정, 관찰자 통지의 기존 책임을 `provider_invocation_accounting.py`로 옮길 계획입니다. 준비·제출·취소에서 전송량을 비교하는 기존 사례를 첫 검증으로 사용합니다.
