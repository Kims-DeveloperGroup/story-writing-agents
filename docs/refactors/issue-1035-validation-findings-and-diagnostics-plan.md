# 검증 결과와 진단 청크의 소유권 분리 계획

[이슈 #1035의 구현 계획](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1035#구현-계획)에 따라 실제 검증 결과와 진단 청크를
만드는 기존 정의를 전용 파일로 옮깁니다. 기존 chunk·진단·가드 사례로
실제 소비 흐름을 검증합니다.

같은 오류의 검출과 기존 fallback, 정확한 최신 커밋의 전체 검증 및
실행 시간 비교를 수행합니다. [기준 전체 CI](https://github.com/Kims-DeveloperGroup/story-writing-agents/actions/runs/45353929225)
