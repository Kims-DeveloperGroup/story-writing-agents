# Controller 설정 투영과 CLI 인자 문법 분리 계획

[이슈 #976](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/976) · [구현 계획](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/976#구현-계획) · [기준 전체 CI](https://github.com/Kims-DeveloperGroup/story-writing-agents/actions/runs/45365277806)

기준 커밋은 `4e52c075cef1299dc1d14d6ed7667acf84f46d45`입니다.

Controller의 설정 값 투영·provider 우선순위와 CLI의 도움말·인자 문법을 각각 전용 모듈에 둡니다. 기존 집중 2개와 확대 3개를 사용합니다.

현행 Case와 subTest·시간·worker 예산을 적용합니다. 구현 전 문서 전용 Draft가 실제 브랜치에 열렸는지 확인합니다.
