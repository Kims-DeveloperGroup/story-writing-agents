# 공급자 대화 검색 핸들러 반환값 재구성 분리

[#1109](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1109)의 기준은 `35231181388143262d665ab9e57523ad7490d275`입니다. [부모 이슈 중간보고](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/969#issuecomment-7059753346)에 추가 사유와 근거를 남겼습니다.

두 기존 Backend 메서드와 끝 답변 생성식을 `provider_retrieval_handler_outcomes.py`로 옮길 계획입니다. 기존 검색·읽기 결과의 JSON을 핸들러 원본과 비교하는 사례를 첫 검증으로 사용합니다. 반환값 타입과 요청 상관관계, 스레드 오염 표시 및 참조 완료는 현재 실행 흐름에 둡니다.
