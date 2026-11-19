# 세션 접근 claim 잠금 수명 분리 계획

[#1069](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1069)의 구현 전 Draft PR을 열기 위한 기준 기록이다.

기준 main `f6c54fcfc2e7f78deab8e2de9e36aa74b2951c23`에서 Backend의 접근 claim guard 정의 7개와 호출 지점 3곳을 확인했다. 파일 디스크립터 열기, 경로와 실제 파일 확인, 잠금 및 정리를 전용 모듈로 이동한다. Backend에서 기존 트랜잭션과 권한·토큰 처리를 유지한다.

기존 Windows 경합 Case의 바이트 단언으로 같은 문제를 이동 전후에 확인한다. 기존 집중 3개와 확대 7개, 정상 실행 전후 각 3회, 최종 전체 CI가 검증 계획에 포함된다. 검증 결과는 이슈의 근거 기록으로 확인한다.
