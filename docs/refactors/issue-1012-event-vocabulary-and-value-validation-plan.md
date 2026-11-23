# 대화 이벤트 상수와 값 검증 분리 계획

[이슈 #1012의 첫 부분 전달](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1012#구현-계획)에서 기존 상수 6개와 스키마 버전·UTF-8 검증 2개를 하위 모듈로 옮깁니다.

- `event_vocabulary.py`가 기존 스키마·예산 상수를, `event_value_validation.py`가 스키마 버전과 표시 텍스트 검증을 소유합니다. `events.py`의 공개 객체와 내부 호출을 유지합니다.
- 기준 커밋은 `d0eb5363d5f06fbdc910308f1177249e78a2eeaf`입니다. 기존 집중 3개와 인접 계약 2개, 공개 패키지 및 그래프 검증을 실행하고 같은 스키마 v1 거절 실패를 기준·후보에서 재현합니다. [기준 전체 CI](https://github.com/Kims-DeveloperGroup/story-writing-agents/actions/runs/45175790838)
- 도메인 코덱과 이벤트·배치 소유권 분리는 기존 #1012의 후속 전달에서 검증합니다.
