# 세션 manifest와 접근 기록의 파일 처리 분리 계획

[#1073](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1073)의 구현 전 Draft PR 기준입니다.

기준 main `9146b58f2313d8e47b2d6b810de9bc788a10e990`에서 기존 파일 책임 9개 메서드를 전용 모듈로 옮깁니다. 기존 경로, Workspace 호출과 형식 오류를 보존합니다. Backend에 남는 잠금과 토큰 권한, 전체 트랜잭션은 같은 흐름으로 연결합니다.

기존 집중 4개와 기능 확대 13개, 중복 JSON 동일 결함 확인과 최종 전체 CI로 검증합니다.
