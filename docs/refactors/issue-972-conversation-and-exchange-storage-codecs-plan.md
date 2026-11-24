# 대화·교환 저장 형식과 집중 검증 분리 계획

[이슈 #972의 구현 계획](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/972#구현-계획)에 따라 파일시스템 JSONL, SQLite의 현재
행 검증, 교환 레코드·순번 상태의 소유 경계를 분리합니다. 새 소유 파일별로
기존 실제 저장소 Case를 선택합니다.

기존 저장소로 읽고 쓰는 정상·부정 사례와 동일 오류 재현, 전체 CI에서 실제
저장 경계와 테스트 시간을 확인합니다. [기준 전체 CI](https://github.com/Kims-DeveloperGroup/story-writing-agents/actions/runs/45328311111)
