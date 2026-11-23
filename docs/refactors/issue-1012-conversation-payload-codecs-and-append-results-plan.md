# 대화 코덱·방향 검증·append 결과 분리 계획

[이슈 #1012의 잔여 구현 계획](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1012#최신-기준의-잔여-구현-계획)에 따라 기존 코드의 소유권을 정리합니다.

- 기존 코덱 본문 46개와 방향 판정·검증, append 결과·오류를 옮깁니다. 공통 이벤트와 배치 판정의 기존 조건·순서·공개 경계는 유지합니다.
- 기준 커밋은 `d08b67c40e142a85931c25afc83ff9d6be685ba4`입니다. 기존 집중 2개, 인접 검증 3개, 실제 FS/SQLite 저장 6개의 테스트와 입력을 보존합니다. [기준 전체 CI](https://github.com/Kims-DeveloperGroup/story-writing-agents/actions/runs/45240279979)
- StepState의 proposal digest 디코딩 값을 같은 방식으로 바꿔 기존 배치 왕복 단언의 실패를 확인합니다.
