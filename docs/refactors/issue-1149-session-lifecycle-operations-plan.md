# 공급자 세션 수명주기 조정과 테스트 파일 분리

[#1149](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1149)의 구현 전 문서입니다. 계획의 기준 커밋은 `d64b5b8ec184427a78664eccb782c399049f457a`입니다.

기존 세션 조회와 접근·전환 메서드 8개, 관련 테스트의 기존 구체 클래스에 속한 9개 본문을 분리합니다. 각 호출의 현재 상태, claim의 소유권과 잠금, 기존 API 시그니처와 테스트 Case ID를 그대로 검증합니다.

[구현 계획](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1149#implementation-plan) · [추가 사유와 근거](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/969#issuecomment-7084738196)
