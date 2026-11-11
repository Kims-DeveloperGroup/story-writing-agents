"""Immutable current-call schema selection input and phase guidance."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import json
from types import MappingProxyType


LEAD_GROUP_PHASE_GUIDES = MappingProxyType({
    "select": "현재 단계는 Phase 1(그룹 선택)입니다. Phase 2에서는 선택한 그룹의 상세 계약에 따라 실제 답변을 작성합니다.",
    "generate": "현재 단계는 Phase 2(답변 생성)입니다. 선택한 그룹의 상세 계약에 따라 실제 답변을 작성하세요.",
})


@dataclass(frozen=True, slots=True)
class SchemaSelectionInput:
    """현재 호출의 닫힌 입력 선언이며 업무 권한을 부여하지 않는다."""

    phase: str
    call_id: str
    contracts: tuple[tuple[str, str | None], ...]
    remaining_provider_calls: int
    context_ref: str
    base_context_ref: str | None = None
    catalog: bytes | None = field(default=None, repr=False)
    catalog_id: str | None = None
    active_selection: tuple[str, str] | None = None

    def __post_init__(self) -> None:
        def identity(value):
            return type(value) is str and bool(value) and len(value) <= 512
        if (type(self.phase) is not str or self.phase not in LEAD_GROUP_PHASE_GUIDES or not identity(self.call_id)
                or not identity(self.context_ref) or self.base_context_ref is not None and not identity(self.base_context_ref)
                or type(self.remaining_provider_calls) is not int or self.remaining_provider_calls < 0
                or type(self.contracts) is not tuple or not self.contracts
                or any(type(p) is not tuple or len(p) != 2 or not identity(p[0])
                       or p[1] is not None and not identity(p[1]) for p in self.contracts)
                or len(set(self.contracts)) != len(self.contracts)):
            raise ValueError("schema_selection has invalid current call facts")
        if (self.catalog is None) == (self.catalog_id is None):
            raise ValueError("schema_selection needs one exact catalog representation")
        if self.catalog is not None:
            if type(self.catalog) is not bytes:
                raise TypeError("schema_selection catalog must be immutable bytes")
            value = json.loads(self.catalog)
            if (type(value) is not dict or set(value) != {"catalog_id", "catalog_version", "groups"}
                    or not identity(value["catalog_id"]) or not identity(value["catalog_version"])
                    or type(value["groups"]) is not list or not value["groups"]):
                raise ValueError("schema_selection catalog is invalid")
            ids = []
            for card in value["groups"]:
                if (type(card) is not dict or set(card) != {"group_id", "definition", "selection_criteria", "result_effect"}
                        or any(type(v) is not str or not v for v in card.values())):
                    raise ValueError("schema_selection card is invalid")
                ids.append(card["group_id"])
            if len(ids) != len(set(ids)):
                raise ValueError("schema_selection catalog repeats a group")
            object.__setattr__(self, "catalog", json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())
        elif not identity(self.catalog_id):
            raise ValueError("schema_selection catalog identity is invalid")
        if self.base_context_ref is None and self.catalog is None:
            raise ValueError("bootstrap needs the complete catalog")
        if self.phase == "generate":
            if (type(self.active_selection) is not tuple or len(self.active_selection) != 2
                    or not all(identity(v) for v in self.active_selection)):
                raise ValueError("generate needs the original selection identity")
        elif self.active_selection is not None:
            raise ValueError("select cannot carry an active selection")

    def as_object(self) -> dict:
        value = {
            "phase": self.phase, "remaining_provider_calls": self.remaining_provider_calls,
            "issued_contract": {"call_id": self.call_id, "contracts": [
                {"outer_schema_id": outer, "result_schema_id": inner} for outer, inner in self.contracts]},
            "context_delivery": {"mode": "bootstrap" if self.base_context_ref is None else "delta",
                                 "context_ref": self.context_ref},
        }
        if self.base_context_ref is not None:
            value["context_delivery"]["base_context_ref"] = self.base_context_ref
        if self.catalog is not None:
            value["catalog"] = json.loads(self.catalog)
        else:
            value["catalog_id"] = self.catalog_id
        if self.active_selection is not None:
            value["active_selection"] = dict(zip(("origin_catalog_id", "group_id"), self.active_selection))
        return value

    @classmethod
    def from_value(cls, value: Mapping) -> SchemaSelectionInput:
        required = {"phase", "remaining_provider_calls", "issued_contract", "context_delivery"}
        optional = {"catalog", "catalog_id", "active_selection"}
        if (not isinstance(value, Mapping) or not required <= value.keys()
                or set(value) - required - optional):
            raise RuntimeError("schema_selection is not a closed current input")
        if ("catalog" in value) == ("catalog_id" in value) or value["phase"] == "select" and "active_selection" in value:
            raise ValueError("schema_selection conditional fields are invalid")
        issued, delivery = value["issued_contract"], value["context_delivery"]
        if type(issued) is not dict or set(issued) != {"call_id", "contracts"} or type(issued["contracts"]) is not list:
            raise ValueError("issued_contract is invalid")
        pairs = []
        for pair in issued["contracts"]:
            if type(pair) is not dict or set(pair) != {"outer_schema_id", "result_schema_id"}:
                raise ValueError("issued_contract member is invalid")
            pairs.append((pair["outer_schema_id"], pair["result_schema_id"]))
        if type(delivery) is not dict or type(delivery.get("mode")) is not str or delivery["mode"] not in {"bootstrap", "delta"}:
            raise ValueError("context_delivery is invalid")
        keys = {"mode", "context_ref"} | ({"base_context_ref"} if delivery["mode"] == "delta" else set())
        if set(delivery) != keys:
            raise ValueError("context_delivery is not closed")
        active = value.get("active_selection")
        if active is not None and (type(active) is not dict or set(active) != {"origin_catalog_id", "group_id"}):
            raise ValueError("active_selection is invalid")
        return cls(value["phase"], issued["call_id"], tuple(pairs), value["remaining_provider_calls"],
                   delivery["context_ref"], delivery.get("base_context_ref"),
                   json.dumps(value["catalog"], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
                   if "catalog" in value else None, value.get("catalog_id"),
                   None if active is None else (active["origin_catalog_id"], active["group_id"]))
