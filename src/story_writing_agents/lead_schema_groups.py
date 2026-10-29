"""Lead의 의미 그룹·불변 선택 근거·명시적인 제어 결과를 소유한다.

이 도메인은 사용자 권한·Provider 실행·저장소를 소유하지 않는다.
서비스가 실제 호출의 계약을 검증한 뒤 이 불변 값으로 국면을 진행한다.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum

from .structured_output_validation import (
    BoundContract, ContractOwner, NamedHostCheck, SchemaRules, SelectedContract, TypedDecoder, ValidatedValue,
    closed_rule,
)

LEAD_GROUP_CATALOG_VERSION = "lead-groups-v1"


@dataclass(frozen=True, slots=True)
class LeadGroupRequestBasis:
    """서비스가 소유한 논리 요청 참조이며 실제 요청 내용을 대체하지 않는다."""
    operation_id: str
    result_correlation_id: str
    scope_ref: str

    def __post_init__(self) -> None:
        if any(type(value) is not str or not value for value in
               (self.operation_id, self.result_correlation_id, self.scope_ref)):
            raise ValueError("현재 논리 요청의 정확한 참조가 필요합니다")

    def as_object(self) -> dict[str, str]:
        return {"operation_id": self.operation_id,
                "result_correlation_id": self.result_correlation_id, "scope_ref": self.scope_ref}


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":")).encode("utf-8", "strict")


def _reference(prefix: str, value: bytes) -> str:
    return prefix + hashlib.sha256(value).hexdigest()[:24]


@dataclass(frozen=True, slots=True)
class LeadResultSemantics:
    """현재 소유자가 생성한 변형의 의미이며 사용자 문장을 분류하지 않는다."""
    kind: str
    intent: str | None = None
    activity: str | None = None
    preparation_kind: str | None = None

    def __post_init__(self) -> None:
        if (self.kind not in {"answer", "question", "critique", "recommendation",
                              "consultation", "artifact", "premise", "readiness"}
                or self.intent not in {None, "authoring", "review_only"}
                or self.activity not in {None, "workflow", "draft", "revise", "review"}
                or self.preparation_kind not in {None, "prepare_artifact", "prepare_consultation"}):
            raise ValueError("현재 Lead 변형의 의미 메타데이터가 필요합니다")


@dataclass(frozen=True, slots=True)
class LeadSchemaGroup:
    """카탈로그 설명과 구성원 판정을 같은 불변 정의에 둔다."""
    group_id: str
    definition: str
    selection_criteria: str
    result_effect: str
    result_kind: str
    intents: tuple[str, ...]
    activities: tuple[str, ...]
    preparation_kind: str | None

    def __post_init__(self) -> None:
        if (any(type(value) is not str or not value for value in
                (self.group_id, self.definition, self.selection_criteria, self.result_effect, self.result_kind))
                or any(type(values) is not tuple or any(type(value) is not str for value in values)
                       for values in (self.intents, self.activities))):
            raise ValueError("그룹 정의와 의미 판정은 불변 문자열 선언이어야 합니다")

    def accepts(self, semantics: LeadResultSemantics) -> bool:
        if type(semantics) is not LeadResultSemantics:
            raise TypeError("소유자가 선언한 Lead 의미가 필요합니다")
        return (semantics.kind == self.result_kind
                and (not self.intents or semantics.intent in self.intents)
                and (not self.activities or semantics.activity in self.activities)
                and semantics.preparation_kind == self.preparation_kind)

    def card(self) -> dict[str, str]:
        return {name: getattr(self, name) for name in
                ("group_id", "definition", "selection_criteria", "result_effect")}

    def membership_identity(self) -> tuple[object, ...]:
        return (self.result_kind, self.intents, self.activities, self.preparation_kind)


LEAD_SCHEMA_GROUPS = (
    LeadSchemaGroup(
        "answer.workflow",
        "협업 절차, 진행 상태, 요청 처리 방법을 설명하는 일반 응답이다. 기존 계약이 허용하는 명시적 취소의 응답도 포함한다.",
        "이번에 제공할 결과가 작업 흐름의 설명이나 안내에 해당할 때 선택한다.",
        "기존 답변 처리와 허용된 요청 추적을 따른다.",
        "answer", (), ("workflow",), None,
    ),
    LeadSchemaGroup(
        "answer.observed_facts",
        "현재 제공된 READ 근거에 연결해 문서의 개별 사실을 설명하는 응답이다. 허용된 authoring과 review_only 범위를 모두 보존한다.",
        "확인된 문서 사실을 답하려는 경우에 선택한다. 핵심 문서 전체의 준비도 평가와 구별한다.",
        "제공된 근거 범위에서 사실을 답한다.",
        "answer", (), ("review",), None,
    ),
    LeadSchemaGroup(
        "question.workflow",
        "요청의 목적, 절차, 진행 방향을 확인하는 질문이다. 기존 계약이 허용하는 새 요청의 시작과 계속 추적도 포함한다.",
        "이번 질문이 구체적인 집필 또는 원문 평가 범위에 앞선 요청 자체의 확인에 해당할 때 선택한다.",
        "사용자에게 질문하고 기존 요청 추적 규칙을 따른다.",
        "question", (), ("workflow",), None,
    ),
    LeadSchemaGroup(
        "question.authoring",
        "authoring 목적의 작성, 개정 또는 검토 작업에서 필요한 사항을 확인하는 질문이다.",
        "작업을 적절히 진행하려면 중요한 미확정 사항이나 충돌을 확인해야 할 때 선택한다. 이미 위임된 세부 선택은 에이전트가 판단할 수 있다.",
        "집필 작업 범위의 질문을 제시한다.",
        "question", ("authoring",), ("draft", "revise", "review"), None,
    ),
    LeadSchemaGroup(
        "question.review_only",
        "제공 원문의 평가를 목적으로 평가 범위나 기준 등을 확인하는 질문이다.",
        "현재 목적이 review_only이고 평가에 필요한 확인 질문을 작성하려는 경우에 선택한다.",
        "원문 평가 범위의 질문을 제시한다.",
        "question", ("review_only",), ("draft", "revise", "review"), None,
    ),
    LeadSchemaGroup(
        "critique.workflow",
        "협업 절차나 요청 진행 방식에 관한 문제점과 우려를 제시한다.",
        "이번 결과가 작업 흐름에 관한 비판적 의견일 때 선택한다. 작품 자체의 전문 평가 제안과 구별한다.",
        "기존 workflow critique를 제시한다.",
        "critique", (), ("workflow",), None,
    ),
    LeadSchemaGroup(
        "prepare.artifact",
        "구체적인 문서 변경 제안서를 준비하는 단계를 추천한다.",
        "이번 응답으로 변경 제안서의 준비를 추천하려는 경우에 선택한다. 실제 변경 operation을 제시하려면 artifact.changes를 사용할 수 있다.",
        "기존 추천 결정 절차로 연결한다.",
        "recommendation", ("authoring",), (), "prepare_artifact",
    ),
    LeadSchemaGroup(
        "prepare.consultation.authoring",
        "authoring 목적의 전문가 작업 또는 검토 배정안을 준비하는 단계를 추천한다.",
        "이번 결과가 구체적인 assignments에 앞선 배정안 준비 추천일 때 선택한다.",
        "기존 추천 결정 절차로 연결한다.",
        "recommendation", ("authoring",), (), "prepare_consultation",
    ),
    LeadSchemaGroup(
        "prepare.consultation.review_only",
        "제공 원문의 평가를 위한 전문가 검토 배정안 준비를 추천한다.",
        "review_only 목적에서 구체적인 검토 assignments의 준비를 추천하려는 경우에 선택한다.",
        "기존 추천 결정 절차로 연결한다.",
        "recommendation", ("review_only",), (), "prepare_consultation",
    ),
    LeadSchemaGroup(
        "consultation.authoring.content",
        "authoring 목적의 작성 또는 개정에 필요한 전문가 assignments를 구체적으로 제안한다. 허용된 작성과 제약 평가 역할의 조합을 포함한다.",
        "이번 응답에서 실제 작성 또는 개정 작업의 배정안을 제시하려는 경우에 선택한다.",
        "배정안을 제안하고 기존 확인 절차로 연결한다.",
        "consultation", ("authoring",), ("draft", "revise"), None,
    ),
    LeadSchemaGroup(
        "consultation.authoring.review",
        "authoring 작업에 속하는 전문가 검토 assignments를 구체적으로 제안한다.",
        "집필 또는 개정 목표에 연결된 검토 배정안을 작성할 때 선택한다. review 활동만으로 intent가 review_only가 되지는 않는다.",
        "검토 배정안을 제안하고 기존 확인 절차로 연결한다.",
        "consultation", ("authoring",), ("review",), None,
    ),
    LeadSchemaGroup(
        "consultation.review_only",
        "제공 원문의 평가를 위한 전문가 검토 assignments를 구체적으로 제안한다. 평가에 수반되는 문제 지적과 개선 방향 설명을 포함할 수 있다.",
        "이번 결과가 review_only 목적의 실제 검토 배정안일 때 선택한다.",
        "평가 배정안을 제안하고 기존 확인 절차로 연결한다.",
        "consultation", ("review_only",), (), None,
    ),
    LeadSchemaGroup(
        "artifact.changes",
        "문서 생성, 부분 수정, 전체 교체, 이름 변경, 삭제의 실제 operation을 담은 변경 제안이다. 한 제안에 허용된 여러 종류를 함께 담을 수 있다.",
        "이번 응답에서 구체적인 문서 변경안을 제시하려는 경우에 선택한다. prepare.artifact를 반드시 먼저 거칠 필요는 없다.",
        "변경안을 제안하고 기존 검증과 확인 절차로 연결한다.",
        "artifact", (), (), None,
    ),
    LeadSchemaGroup(
        "premise.analysis",
        "아직 해소되지 않은 전제를 분석하기 위한 전문가 assignments와 전제 판단 자료를 제안한다. 기존 null scope 표현을 보존한다.",
        "현재 계약에서 제공된 미해결 전제를 분석하는 결과를 제안하려는 경우에 선택한다.",
        "전제 분석을 제안한다. 선택 자체가 전제를 확정하지 않는다.",
        "premise", (), (), None,
    ),
    LeadSchemaGroup(
        "readiness.core_documents",
        "작품 전체와 핵심 문서의 준비 상태를 정해진 평가 구조로 표현한다. 확인된 상태와 부족하거나 미확인인 상태를 구별한다.",
        "개별 사실 답변보다 핵심 문서 전체의 집필 준비도 평가가 필요한 경우에 선택한다.",
        "준비도 평가를 제공하고 기존 후속 검증을 따른다.",
        "readiness", (), (), None,
    ),
)


def schema_group_for(semantics: LeadResultSemantics) -> LeadSchemaGroup:
    matches = tuple(group for group in LEAD_SCHEMA_GROUPS if group.accepts(semantics))
    if len(matches) != 1:
        raise ValueError("현재 정규 Lead 결과는 정확히 한 의미 그룹에 속해야 합니다")
    return matches[0]


@dataclass(frozen=True, slots=True)
class LeadGroupCatalog:
    """정의·요청 근거·현재 사실을 보존하며 ID를 다시 해석하지 않는 스냅샷."""
    groups: tuple[LeadSchemaGroup, ...]
    request_basis: bytes = field(repr=False)
    context_basis: bytes = field(repr=False)
    catalog_version: str = LEAD_GROUP_CATALOG_VERSION
    catalog_id: str = field(init=False)
    selection_basis_ref: str = field(init=False)
    request_basis_ref: str = field(init=False)
    _snapshot: bytes = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if (type(self.groups) is not tuple or not self.groups
                or any(type(group) is not LeadSchemaGroup for group in self.groups)
                or len(set(self.group_ids)) != len(self.groups)
                or type(self.catalog_version) is not str or not self.catalog_version):
            raise ValueError("카탈로그에는 고유한 현재 그룹 정의가 필요합니다")
        for basis in (self.request_basis, self.context_basis):
            if type(basis) is not bytes:
                raise TypeError("선택 근거는 불변 정규 JSON 바이트여야 합니다")
            value = json.loads(basis)
            if type(value) is not dict or _canonical(value) != basis:
                raise ValueError("선택 근거는 정규 JSON 객체여야 합니다")
        basis = _canonical({"request": json.loads(self.request_basis),
                            "context": json.loads(self.context_basis)})
        object.__setattr__(self, "request_basis_ref", _reference("request_", self.request_basis))
        object.__setattr__(self, "selection_basis_ref", _reference("basis_", basis))
        snapshot = _canonical({"version": self.catalog_version,
                               "groups": [group.card() for group in self.groups],
                               "membership": [group.membership_identity() for group in self.groups],
                               "selection_basis_ref": self.selection_basis_ref})
        object.__setattr__(self, "catalog_id", _reference("cat_", snapshot))
        object.__setattr__(self, "_snapshot", snapshot)

    def require_snapshot(self) -> None:
        basis = _canonical({"request": json.loads(self.request_basis),
                            "context": json.loads(self.context_basis)})
        snapshot = _canonical({"version": self.catalog_version,
                               "groups": [group.card() for group in self.groups],
                               "membership": [group.membership_identity() for group in self.groups],
                               "selection_basis_ref": _reference("basis_", basis)})
        if (snapshot != self._snapshot or self.catalog_id != _reference("cat_", snapshot)
                or self.selection_basis_ref != _reference("basis_", basis)
                or self.request_basis_ref != _reference("request_", self.request_basis)):
            raise ValueError("동결된 카탈로그 정의 또는 선택 근거가 변경됐습니다")

    @classmethod
    def build(cls, group_ids: tuple[str, ...], *, request_basis: Mapping[str, object],
              context_basis: Mapping[str, object]) -> LeadGroupCatalog:
        if (type(group_ids) is not tuple or not group_ids
                or any(type(item) is not str for item in group_ids)
                or len(set(group_ids)) != len(group_ids)):
            raise ValueError("현재 소유자에서 나온 전체 허용 그룹이 필요합니다")
        groups = tuple(group for group in LEAD_SCHEMA_GROUPS if group.group_id in group_ids)
        if len(groups) != len(group_ids):
            raise ValueError("정의되지 않은 Lead 그룹입니다")
        return cls(groups, _canonical(dict(request_basis)), _canonical(dict(context_basis)))

    @property
    def group_ids(self) -> tuple[str, ...]:
        return tuple(group.group_id for group in self.groups)

    def as_object(self) -> dict[str, object]:
        self.require_snapshot()
        return {"catalog_id": self.catalog_id, "catalog_version": self.catalog_version,
                "groups": [group.card() for group in self.groups]}


def _require_control(catalog_id: str, group_id: str, selection_reason: str) -> None:
    if (type(catalog_id) is not str or not catalog_id
            or type(group_id) is not str or not group_id
            or type(selection_reason) is not str):
        raise ValueError("선택에는 현재 ID와 문자열 이유가 필요합니다")


@dataclass(frozen=True, slots=True)
class SchemaGroupSelection:
    catalog_id: str
    group_id: str
    selection_reason: str

    def __post_init__(self) -> None:
        _require_control(self.catalog_id, self.group_id, self.selection_reason)

    @property
    def response_type(self) -> str:
        return "schema_group_selection"


@dataclass(frozen=True, slots=True)
class SchemaGroupReselection:
    catalog_id: str
    group_id: str
    selection_reason: str

    def __post_init__(self) -> None:
        _require_control(self.catalog_id, self.group_id, self.selection_reason)

    @property
    def response_type(self) -> str:
        return "schema_group_reselection"


SchemaGroupControl = SchemaGroupSelection | SchemaGroupReselection


def decode_schema_group_control(value: ValidatedValue) -> SchemaGroupControl:
    if type(value) is not ValidatedValue:
        raise TypeError("선택 제어는 전체 bound 계약 검증 뒤에만 해독합니다")
    body = value.as_object()
    kind = body["response_type"]
    if kind == "schema_group_selection":
        return SchemaGroupSelection(body["catalog_id"], body["group_id"], body["selection_reason"])
    if kind == "schema_group_reselection":
        return SchemaGroupReselection(body["catalog_id"], body["group_id"], body["selection_reason"])
    raise ValueError("제공된 선택 제어 타입이 아닙니다")


@dataclass(frozen=True, slots=True)
class SchemaGroupControlOwner(ContractOwner[SchemaGroupControl]):
    """한 동결 규칙에서 선택 schema·decoder·repair를 중앙 결합한다."""
    catalog: LeadGroupCatalog
    current_group_id: str | None = None
    owner_id: str = field(init=False)
    owner_revision: str = field(init=False, default="890-v1:typed-group-control")
    _rules: SchemaRules = field(init=False, repr=False, compare=False)
    _decoder: TypedDecoder[SchemaGroupControl] = field(init=False, repr=False, compare=False)
    _declaration: tuple = field(init=False, repr=False, compare=False)

    def __init_subclass__(cls, **kwargs):
        raise TypeError("선택 제어 소유자는 독립된 구현으로 바꿀 수 없습니다")

    def __post_init__(self) -> None:
        if type(self.catalog) is not LeadGroupCatalog:
            raise TypeError("현재 불변 카탈로그가 필요합니다")
        if self.current_group_id is not None and self.current_group_id not in self.catalog.group_ids:
            raise ValueError("현재 그룹이 제공된 카탈로그에 없습니다")
        choices = tuple(item for item in self.catalog.group_ids if item != self.current_group_id)
        if not choices:
            raise ValueError("다른 그룹이 없으면 재선택 액션을 제공하지 않습니다")
        response_type = ("schema_group_selection" if self.current_group_id is None
                         else "schema_group_reselection")
        object.__setattr__(self, "owner_id", "lead." + response_type + "." + self.catalog.catalog_id)
        graph = closed_rule({
            "response_type": {"type": "string", "enum": [response_type],
                              "description": "이번 제어 계약에 제공된 고정 응답 타입을 그대로 반환합니다."},
            "catalog_id": {"type": "string", "enum": [self.catalog.catalog_id],
                           "description": "이번 요청에 제공된 현재 카탈로그 ID를 그대로 반환합니다."},
            "group_id": {"type": "string", "enum": list(choices),
                         "description": "현재 카탈로그에서 이번 결과를 표현할 허용 그룹 하나를 선택합니다."},
            "selection_reason": {"type": "string", "description":
                "현재 요청·컨텍스트의 사실을 선택 기준에 연결한 1~2문장입니다. "
                "읽지 않은 자료나 제공되지 않은 승인을 근거로 만들지 않습니다. "
                "이 설명은 사용자 지시·권한·필수 순서를 만들지 않습니다."},
        })
        object.__setattr__(self, "_rules", SchemaRules.declare(graph, maximum_bytes=65_536))
        object.__setattr__(self, "_decoder", TypedDecoder(
            "lead.schema_group_control", self.owner_revision, decode_schema_group_control))
        object.__setattr__(self, "_declaration", (
            self.catalog, self.current_group_id, self.owner_id, self.owner_revision,
            self._rules, self._decoder, self._rules.canonical_bytes))

    def require_declaration(self) -> None:
        self.catalog.require_snapshot()
        expected = self._declaration
        if (self.catalog is not expected[0] or self.current_group_id != expected[1]
                or self.owner_id != expected[2] or self.owner_revision != expected[3]
                or self._rules is not expected[4] or self._decoder is not expected[5]
                or self._rules.canonical_bytes != expected[6]):
            raise ValueError("동결된 선택 계약 선언이 변경됐습니다")

    def declare_rules(self) -> SchemaRules:
        self.require_declaration()
        return self._rules

    def typed_decoder(self) -> TypedDecoder[SchemaGroupControl]:
        self.require_declaration()
        return self._decoder

    def named_host_checks(self) -> tuple[NamedHostCheck[SchemaGroupControl], ...]:
        self.require_declaration()
        return ()


@dataclass(frozen=True, slots=True)
class LeadGroupSelectionRecord:
    """선택 당시 근거와 실제 호출·외부/내부 ID를 변경 없이 보존한다."""
    catalog: LeadGroupCatalog
    control: SchemaGroupControl
    call_id: str
    outer_schema_id: str
    inner_schema_id: str

    def __post_init__(self) -> None:
        if (type(self.catalog) is not LeadGroupCatalog
                or type(self.control) not in {SchemaGroupSelection, SchemaGroupReselection}
                or self.control.catalog_id != self.catalog.catalog_id
                or self.control.group_id not in self.catalog.group_ids
                or any(type(item) is not str or not item for item in
                       (self.call_id, self.outer_schema_id, self.inner_schema_id))):
            raise ValueError("선택 기록에는 검증된 현재 카탈로그와 정확한 호출 연결이 필요합니다")

    @property
    def group_id(self) -> str:
        return self.control.group_id

    @property
    def selection_basis_ref(self) -> str:
        return self.catalog.selection_basis_ref

    def as_object(self) -> dict[str, object]:
        return {"catalog_at_selection": self.catalog.as_object(),
                "selection_basis_ref": self.selection_basis_ref,
                "group_id": self.group_id, "selection_reason": self.control.selection_reason,
                "response_type": self.control.response_type, "call_id": self.call_id,
                "outer_schema_id": self.outer_schema_id, "inner_schema_id": self.inner_schema_id}


@dataclass(frozen=True, slots=True)
class LeadGroupGenerationBinding:
    """원래 선택과 분리하여 현재 제공한 전체 계약과 생성 연결을 보존한다."""
    selection: LeadGroupSelectionRecord
    current_catalog: LeadGroupCatalog
    member_bindings: tuple[tuple[BoundContract, BoundContract], ...]
    provided_contracts: tuple[BoundContract, ...] = field(kw_only=True, repr=False)
    current_offer: bytes = field(kw_only=True, repr=False)
    eligibility_offer: bytes = field(kw_only=True, repr=False)
    provided_call_id: str = field(kw_only=True)
    provided_offer_digest: str = field(kw_only=True)
    logical_origin_call_id: str = field(kw_only=True)
    phase: str = field(kw_only=True)
    _declaration: tuple = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if (type(self.selection) is not LeadGroupSelectionRecord
                or type(self.current_catalog) is not LeadGroupCatalog
                or self.selection.catalog.request_basis_ref != self.current_catalog.request_basis_ref
                or self.selection.group_id not in self.current_catalog.group_ids
                or type(self.member_bindings) is not tuple or not self.member_bindings
                or any(type(pair) is not tuple or len(pair) != 2
                       or any(type(item) is not BoundContract for item in pair)
                       for pair in self.member_bindings)):
            raise ValueError("생성 연결에는 유효한 선택과 현재 전체 member의 정확한 ID가 필요합니다")
        self._require_relationships()
        object.__setattr__(self, "_declaration", self._snapshot())

    def _snapshot(self) -> tuple:
        return (id(self.selection), _canonical(self.selection.as_object()),
                id(self.current_catalog), self.current_offer,
                self.eligibility_offer, self.provided_call_id, self.provided_offer_digest,
                self.logical_origin_call_id, self.phase,
                tuple((id(member), id(member.owner), member.schema_id,
                       member.owner.owner_id, member.identity.canonical_bytes)
                      for member in self.provided_contracts),
                tuple((id(outer), id(inner), id(outer.owner), id(inner.owner),
                       outer.schema_id, inner.schema_id, outer.identity.canonical_bytes,
                       inner.identity.canonical_bytes, outer.rule_graph.canonical_bytes,
                       inner.rule_graph.canonical_bytes)
                      for outer, inner in self.member_bindings))

    def _require_relationships(self) -> None:
        self.selection.catalog.require_snapshot()
        self.current_catalog.require_snapshot()
        snapshots = []
        for snapshot in (self.current_offer, self.eligibility_offer):
            if type(snapshot) is not bytes:
                raise TypeError("생성 연결에는 전체 불변 offer 스냅샷이 필요합니다")
            value = json.loads(snapshot)
            if type(value) is not dict or _canonical(value) != snapshot:
                raise ValueError("offer 스냅샷은 정규 JSON 객체여야 합니다")
            snapshots.append(value)
        current, eligible = snapshots
        eligible_digest = hashlib.sha256(self.eligibility_offer).hexdigest()
        current_digest = hashlib.sha256(self.current_offer).hexdigest()
        if (self.phase not in {"ordinary", "repair", "replay", "replay_repair"}
                or any(type(value) is not str or not value for value in (
                    self.provided_call_id, self.provided_offer_digest, self.logical_origin_call_id))
                or eligible.get("call_id") != self.logical_origin_call_id
                or type(current.get("call_id")) is not str
                or current.get("scope") != eligible.get("scope")
                or self.phase == "repair" and self.provided_call_id == current["call_id"]
                or self.phase != "repair" and (
                    self.provided_call_id != current["call_id"]
                    or self.provided_offer_digest != current_digest)):
            raise ValueError("현재 호출·제공 계약·원래 eligibility의 연결이 다릅니다")
        if (type(self.provided_contracts) is not tuple or not self.provided_contracts
                or any(type(member) is not BoundContract for member in self.provided_contracts)):
            raise TypeError("생성 연결에는 현재 제공된 전체 semantic 계약이 필요합니다")
        controls = tuple(member for member in self.provided_contracts
                         if type(member.owner) is SchemaGroupControlOwner)
        results = tuple(member for member in self.provided_contracts
                        if type(member.owner) is not SchemaGroupControlOwner)
        if (len(controls) != int(len(self.current_catalog.groups) > 1)
                or any(member.owner.catalog is not self.current_catalog
                       or member.owner.current_group_id != self.selection.group_id
                       for member in controls)
                or len(results) != len(self.member_bindings)
                or any(inner is not expected for (_, inner), expected in
                       zip(self.member_bindings, results, strict=True))):
            raise ValueError("현재 선택 그룹의 전체 업무·제어 member 관계가 다릅니다")
        owner_set_digest = hashlib.sha256(json.dumps(
            tuple((member.owner.owner_id, id(member.owner)) for member in self.provided_contracts),
            separators=(",", ":")).encode("utf-8")).hexdigest()
        for member in self.provided_contracts:
            member.require_owner_provenance()
            if type(member.owner) is SchemaGroupControlOwner:
                member.owner.require_declaration()
            if json.loads(member.identity.canonical_bytes).get("owner_set_digest") != owner_set_digest:
                raise ValueError("생성 연결에서 현재 제공 owner가 누락되거나 변경됐습니다")
        outer_ids = tuple(outer.schema_id for outer, _ in self.member_bindings)
        inner_ids = tuple(inner.schema_id for _, inner in self.member_bindings)
        if (len(set(outer_ids)) != len(outer_ids)
                or len(set(inner_ids)) != len(inner_ids)):
            raise ValueError("생성 연결에는 각 업무 member의 고유한 전체 쌍이 필요합니다")
        for outer, inner in self.member_bindings:
            for member in (outer, inner):
                policy = json.loads(member.identity.canonical_bytes)
                if (policy.get("physical_call_id") != self.provided_call_id
                        or policy.get("offer_digest") != self.provided_offer_digest
                        or policy.get("eligibility_offer_digest") != eligible_digest
                        or policy.get("logical_origin_call_id") != self.logical_origin_call_id):
                    raise ValueError("업무 member가 정확한 현재 제공 계약에 속하지 않습니다")
                member.require_owner_provenance()
            if (outer.rule_graph.literal_at(("task_result", "schema_id")) != inner.schema_id
                    or outer.rule_graph.node_fingerprint(("task_result",))
                    != inner.rule_graph.node_fingerprint()):
                raise ValueError("생성 연결의 외부·내부 계약 쌍이 다릅니다")

    @property
    def schema_id_pairs(self) -> tuple[tuple[str, str], ...]:
        return tuple((outer.schema_id, inner.schema_id) for outer, inner in self.member_bindings)

    def require_result(self, result: SelectedContract) -> None:
        if self._snapshot() != self._declaration:
            raise ValueError("동결된 현재 생성 연결이 변경됐습니다")
        self._require_relationships()
        if (type(result) is not SelectedContract
                or type(result.value) in {SchemaGroupSelection, SchemaGroupReselection}
                or not any(result.belongs_to(inner) for _, inner in self.member_bindings)):
            raise ValueError("현재 생성 연결의 정확한 업무 member 결과가 필요합니다")


class LeadGroupPhase(StrEnum):
    SELECT = "select"
    GENERATE = "generate"
    DONE = "done"
    STOP = "stop"


@dataclass(frozen=True, slots=True)
class LeadGroupWorkflow:
    """실제 호출 루프가 소유하는 불변 국면 상태이며 예산을 늘리지 않는다."""
    catalog: LeadGroupCatalog
    history: tuple[LeadGroupSelectionRecord, ...] = ()
    active_selection: LeadGroupSelectionRecord | None = None
    phase: LeadGroupPhase = LeadGroupPhase.SELECT
    stop_reason: str | None = None

    def __post_init__(self) -> None:
        if (type(self.catalog) is not LeadGroupCatalog
                or type(self.history) is not tuple
                or any(type(item) is not LeadGroupSelectionRecord for item in self.history)
                or type(self.phase) is not LeadGroupPhase
                or self.active_selection is not None and
                not any(self.active_selection is item for item in self.history)
                or self.phase in {LeadGroupPhase.GENERATE, LeadGroupPhase.DONE}
                and self.active_selection is None
                or (self.phase is LeadGroupPhase.STOP) != (self.stop_reason is not None)
                or self.stop_reason is not None and
                (type(self.stop_reason) is not str or not self.stop_reason)):
            raise ValueError("선택 이력·현재 선택·국면·중단 사유의 관계가 올바르지 않습니다")
        self.catalog.require_snapshot()

    def refresh(self, catalog: LeadGroupCatalog) -> LeadGroupWorkflow:
        if type(catalog) is not LeadGroupCatalog:
            raise TypeError("현재 소유자가 만든 카탈로그가 필요합니다")
        if self.phase in {LeadGroupPhase.DONE, LeadGroupPhase.STOP}:
            raise ValueError("종료된 선택 흐름을 다시 진행할 수 없습니다")
        active = self.active_selection
        if (active is not None and
                (active.catalog.request_basis_ref != catalog.request_basis_ref
                 or active.group_id not in catalog.group_ids)):
            active = None
        return replace(self, catalog=catalog, active_selection=active,
                       phase=LeadGroupPhase.GENERATE if active is not None else LeadGroupPhase.SELECT)

    @property
    def minimum_remaining_calls(self) -> int:
        return 2 if self.phase is LeadGroupPhase.SELECT else 1 if self.phase is LeadGroupPhase.GENERATE else 0

    def accept(self, record: LeadGroupSelectionRecord, *, remaining_calls: int) -> LeadGroupWorkflow:
        if (type(record) is not LeadGroupSelectionRecord or record.catalog != self.catalog
                or type(remaining_calls) is not int or remaining_calls < 0
                or self.phase not in {LeadGroupPhase.SELECT, LeadGroupPhase.GENERATE}):
            raise ValueError("현재 선택 국면과 잔여 실제 호출 수가 필요합니다")
        if self.phase is LeadGroupPhase.SELECT:
            if type(record.control) is not SchemaGroupSelection:
                raise ValueError("최초 국면에는 최초 선택이 필요합니다")
        elif (type(record.control) is not SchemaGroupReselection
              or self.active_selection is None or record.group_id == self.active_selection.group_id):
            raise ValueError("생성 국면의 재선택은 현재 카탈로그의 다른 그룹이어야 합니다")
        return replace(self, history=(*self.history, record), active_selection=record,
                       phase=LeadGroupPhase.GENERATE if remaining_calls else LeadGroupPhase.STOP,
                       stop_reason=None if remaining_calls else "provider_call_budget_exhausted")

    def stop(self, reason: str) -> LeadGroupWorkflow:
        if (type(reason) is not str or not reason
                or self.phase in {LeadGroupPhase.DONE, LeadGroupPhase.STOP}):
            raise ValueError("명시적인 중단 사유가 필요합니다")
        return replace(self, phase=LeadGroupPhase.STOP, stop_reason=reason)

    def complete(self, binding: LeadGroupGenerationBinding,
                 result: SelectedContract) -> LeadGroupWorkflow:
        if (self.phase is not LeadGroupPhase.GENERATE
                or type(binding) is not LeadGroupGenerationBinding
                or binding.selection is not self.active_selection
                or binding.current_catalog != self.catalog):
            raise ValueError("현재 선택 그룹의 검증된 업무 결과만 완료할 수 있습니다")
        binding.require_result(result)
        return replace(self, phase=LeadGroupPhase.DONE)
