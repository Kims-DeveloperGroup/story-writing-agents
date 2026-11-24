# 현재 대화 이력 검색·페이지 소유권 분리 계획

[이슈 #1024의 구현 계획](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1024#구현-계획)에 따라 두 v2 검색 구현과 서비스 보조 정의를 도메인별로 옮깁니다. 메모리와 파일시스템에 있는 기존 6개 사례는 정규 ID와 소스 대응표에 따라 어댑터 클래스 단위로 옮깁니다.

보관 이력 조회와 페이지·snapshot 계약, 실제 메모리·파일시스템·SQLite의 기존 시나리오를 검증합니다. [기준 전체 CI](https://github.com/Kims-DeveloperGroup/story-writing-agents/actions/runs/45294418409)
