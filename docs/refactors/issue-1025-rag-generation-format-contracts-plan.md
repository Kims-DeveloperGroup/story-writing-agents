# RAG generation 계약과 실제 형식 테스트 분리 계획

[이슈 #1025의 구현 계획](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1025#구현-계획)에 따라 기존 generation 값·오류·형식 정의를
도메인 파일로 옮깁니다. 기존 3개 실제 형식 메서드는 테스트 전용 도메인 파일에
두고 기존 실제 테스트 클래스에서 같은 시나리오와 정규 ID를 사용합니다.

게시된 canonical bytes, 기존 파싱 오류와 reader 재열기, 파일·잠금 수명과 복구를
기존 테스트로 검증합니다. [기준 전체 CI](https://github.com/Kims-DeveloperGroup/story-writing-agents/actions/runs/45317388719)
