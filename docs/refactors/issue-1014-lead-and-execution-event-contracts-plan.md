# Lead·실행 이벤트 계약 분리 계획

[이슈 #1014의 구현 계획](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1014#구현-계획)에 따라 기존 45개 정의의 소유권을 정리합니다.

- 정의 32개는 Lead 결과, 실행, 제어 레코드, 집필 레코드의 네 하위 모듈에 옮깁니다. 나머지 13개는 공통 제안과 intent 검증을 함께 쓰는 기존 물리 소유자에 둡니다.
- 기준 커밋은 `6b3830e35f691635eab622ab8274fb609dbe8ca6`입니다. 기존 집중 3개, 순수 확대 4개, 실제 Runtime 검증 1개의 입력과 단언을 유지합니다. [기준 전체 CI](https://github.com/Kims-DeveloperGroup/story-writing-agents/actions/runs/45209252986)
- 합성을 시작하지 않은 실행 결과에 물리 호출이 기록된 경우를 거절하는 같은 검사를 양쪽에서 생략하고, 기존 `ValueError` 단언의 실패를 확인합니다.
