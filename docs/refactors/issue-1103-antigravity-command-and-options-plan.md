# Antigravity 실행 옵션과 명령 사전 확인 분리

[#1103](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1103)의 수락 기준 커밋은 `67850903cfacc9d19385571a11f0cc9d6e643e67`입니다. [부모 이슈 중간보고](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/969#issuecomment-7056313138)에 이슈 추가 사유와 근거를 남겼습니다.

옵션의 기존 복사·검증·선택은 `antigravity_invocation_options.py`에서, 명령 인자·실행 경로 확인·도움말 제한 시간과 정리는 `antigravity_command.py`에서 담당합니다. 기존 실행 파일 조회, 사전 확인과 정리의 위치와 공개 호출을 지킵니다.

기존 다섯 사례를 두 전담 테스트 파일로 옮기고 확대 범위에서 옵션 소비자와 실제 실행 경로 아홉 사례를 확인합니다. 옵션 선택의 같은 결함을 두 버전에서 재현하고 정상 비교와 최종 전체 CI를 확인합니다.
