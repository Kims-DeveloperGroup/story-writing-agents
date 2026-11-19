# Backend 응답 복원 분리 검증 계획 (#1060)

이 문서는 구현 전 Draft PR에서 확정한 검증 계획을 보존한다. 실제 변경의 결과는 [#1060 구현 계획과 증거](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1060#implementation-plan)에서 관리한다.

- 수락 기준은 `main`의 `a78d2ec85a3aadc71a592589790dd908c99d72c8`이다.
- 기존 응답 decoder 세 개, 순수 보조 함수 네 개, 단계 오류 클래스 하나를 `backend_reply_decoding.py`로 옮긴다.
- 기존 공개 DTO와 `provider_response_mapping`의 raw normalization을 재사용한다. Backend의 기존 입력, 호출 순서, 예외와 수명을 보존한다.
- 기존 직접 사례 정의 세 개와 공용 입력 producer 다섯 개의 실제 소유를 분리한다. 원래 Case ID, fixture, cap, 시나리오를 유지한다.
- 소스 변경의 정상 비교는 phase, retrieval, 실제 conversation 소비자 세 사례로 시행한다. 정의 모듈의 직접 세 사례와 구분한다.
- 기존 valid DRAFT 응답 본문의 정확한 값 비교가 newline 오류를 탐지하는지 같은 오류의 전후 RED에서 확인한다.
- 기존 기능 여덟 사례와 정책 검증으로 확대하고, 실제 테스트 소스 계수와 module graph를 고정한 후 현재 head 전체 CI를 실행한다.
- 최초 커밋은 문서만 담았다. [Draft PR #1062](https://github.com/Kims-DeveloperGroup/story-writing-agents/pull/1062)의 생성과 기준을 확인한 뒤 소스와 테스트를 편집했다.

실제 분리 후 `backend.py`는 8,159행에서 7,612행으로 줄고 응답 소유 모듈은 576행이다. 기존 decoder 세 개와 단계 오류, 기존 canonical DTO 및 원래 두 계층의 호출 위치를 보존한다. 단계 오류의 Python 원산지와 traceback의 위치는 새 실제 소유자를 따른다. 오류 메시지와 진단 값, 처리 순서는 보존한다.
