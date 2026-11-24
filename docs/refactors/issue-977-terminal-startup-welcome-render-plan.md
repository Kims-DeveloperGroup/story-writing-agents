# Terminal 시작 화면 렌더링 분리 계획

[이슈 #977](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/977) · [구현 계획](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/977#구현-계획) · [기준 전체 CI](https://github.com/Kims-DeveloperGroup/story-writing-agents/actions/runs/45378116248)

기준 커밋은 `a58e94f7ef21787c70664cb3e295297c55ef7b1c`입니다.

Terminal 시작 화면의 너비별 렌더링과 공통 SGR 처리를 전용 모듈에 둡니다. 기존 집중 1개와 확대 5개를 사용합니다.

현행 Case와 subTest·시간·worker 예산을 적용합니다. 구현 전 문서 전용 Draft가 실제 브랜치에 열렸는지 확인합니다.
