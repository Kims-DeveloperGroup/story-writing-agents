# 공급자 설정 후보 적용·복구 경계 분리

[이슈 #1151의 구현 계획](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1151#implementation-plan)에서 작업 범위와 기존 사례를 관리합니다.

통합 기준 `20c4c63cef6c0b2b1b57214d307f5ac6dd21920f`에서 기존 설정 후보 타입과 호출 시그니처, 같은 RLock의 게시·관측·퇴역 순서 및 Controller의 복구 동작을 유지합니다.

실제 적용 모듈의 집중 3개와 영향 10개를 기존 정규 ID로 확인하도록 계획했습니다. 동일한 관측 설치 누락은 전후 기존 단언에서 확인합니다.
