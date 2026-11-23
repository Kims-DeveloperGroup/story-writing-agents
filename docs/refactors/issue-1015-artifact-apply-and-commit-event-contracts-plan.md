# 산출물 검토·Apply·Commit 이벤트 계약 분리 계획

[이슈 #1015의 구현 계획](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1015#구현-계획)에 따라 기존 정의 27개와 family codec 본문 486행을 세 하위 모듈로 옮깁니다.

- 산출물 검토, Apply 레코드, Story Commit 요청과 코덱을 분리합니다. 저장·Git·권한의 기존 실행 경계를 유지합니다.
- 기준 커밋은 `29129f44c0c42b357fb0e2cdaa7ef9e2b3ac0985`입니다. 기존 집중 2개와 확대 3개의 입력, 정규 ID, 단언을 유지합니다. [기준 전체 CI](https://github.com/Kims-DeveloperGroup/story-writing-agents/actions/runs/45224827946)
- 산출물 검토 decoder의 assessment 검증만 양쪽에서 같은 방식으로 교체해 기존 예외 검사 실패를 확인합니다.
