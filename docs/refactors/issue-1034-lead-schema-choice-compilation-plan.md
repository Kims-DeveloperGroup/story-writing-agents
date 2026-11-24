# 유한 선택 스키마 컴파일과 집중 검증 분리 계획

[이슈 #1034의 구현 계획](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1034#구현-계획)에 따라 실제 유한 선택 컴파일 구현을
전용 파일로 옮기고 기존 두 사용 경로를 직접 연결합니다. 기존 factory
테스트의 메서드 본문은 전용 파일에 두고 동일한 정규 ID로 수집합니다.

기존 non-identity Case에서 실제 오류와 정상 컴파일을 확인하고, factory
및 전체 스키마 검증과 같은 시나리오를 사용합니다. [기준 CI](https://github.com/Kims-DeveloperGroup/story-writing-agents/actions/runs/45344393390)
