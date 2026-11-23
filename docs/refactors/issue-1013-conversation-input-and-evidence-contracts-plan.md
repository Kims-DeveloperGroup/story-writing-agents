# 대화 입력·근거·범위 계약 분리 계획

[이슈 #1013의 구현 계획](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1013#구현-계획)에 따라 기존 73개 정의의 소유권을 정리합니다.

- 기존 정의 42개를 대화 레코드, 제안 입력, 근거, Lead 범위의 네 하위 모듈에 옮깁니다. 공개 객체나 공통 코덱이 결합된 31개 정의는 기존 물리 소유자에 둡니다.
- 기준 커밋은 `cbd5d125597f9fb3cf0cfee21dc506aff4f0fc54`입니다. 기존 집중 3개와 확대 3개, 공개 객체·pickle·모듈 소유권 검증을 확인합니다. [기준 전체 CI](https://github.com/Kims-DeveloperGroup/story-writing-agents/actions/runs/45192539239)
- 기준과 후보에서 같은 라운드 합계 제한만 제거해 기존 `ValueError` 단언의 실패를 비교합니다. 정상 반복과 최종 전체 CI는 기존 정규 ID와 하위 사례를 유지합니다.
- 파일별 선택은 기존 러너와 기존 테스트를 사용해 실제 후보에서 확인합니다.
