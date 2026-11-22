# 공급자의 직접 구조화 요청과 대화 응답 분리 계획

[#1131](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1131)의 기준은 `3a0e6b008a3d78f67e8861fe20941485711c7eb5`와 실행 트리 `8e6269c2fc55c9927869fc6011040b54a64df445`입니다.

[부모 이슈 중간보고](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/969#issuecomment-7075029993)에 추가 사유와 근거를 남겼습니다.

`provider_direct_structured_calls.py`가 두 직접 호출과 네 내부 디코딩·복구 정의를 소유합니다. 기존 호출 시그니처와 작업공간 전달, fallback과 대화 턴 식별자, 공급자 라우팅 순서를 보존합니다. 원본 본체는 174행입니다.

기존 집중과 영향 사례를 그대로 검증합니다. 토론 수리 소진 후 안전 문구와 직접 대화 복구의 턴 식별자 결함은 변경 전후 같은 단언으로 검출합니다. 이후 전체 CI와 관리 리뷰를 확인합니다.
