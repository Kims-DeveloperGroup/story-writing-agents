# 세션 접근 claim 값과 소유자 판정 분리 계획

[#1071](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1071)의 구현 전 Draft PR 기준입니다.

기준 main `ae58f93f3fa3903207d15da478afe6b05a02d199`에서 접근 claim의 값 생성·검증, 기존 소유자 판정, 호스트명 진단 처리를 전용 모듈로 옮깁니다. Backend에 남는 토큰 권한, 파일 읽기·쓰기와 기존 잠금 구간을 보존합니다.

기존 집중 4개와 기능 확대 8개, 같은 오류 검출, 정상 전후 각 3회와 최종 전체 CI로 확인합니다.
