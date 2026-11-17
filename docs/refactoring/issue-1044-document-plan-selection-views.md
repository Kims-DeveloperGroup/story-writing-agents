# 문서 계획 선택과 뷰의 정책 분리

이 문서는 #1044의 구현 전 Draft PR을 위한 준비 기록이다.

부모 #968의 고정 D 범위이며 새 기능이나 시나리오를 추가하지 않는다.

수락한 main은 `e6bf42bc3e7e9e732f80010c5c16f5c4d792d262`이다.

고정 4개 정의를 두 도메인 파일로 분리하고 실제 Root I/O 구간을 보존한다.

기존 2개 Case와 285개 assertion의 입력·부정 사례·MRO·자원 수명을 보존한다.

[상세 구현 계획](https://github.com/Kims-DeveloperGroup/story-writing-agents/issues/1044#구현-계획)에 따라 실제 Draft 확인 후 Runtime·테스트를 수정한다.
