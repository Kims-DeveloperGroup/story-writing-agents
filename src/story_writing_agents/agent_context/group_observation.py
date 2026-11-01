"""Immutable private observations of Lead group execution, without authority."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from ..lead_schema_groups import LeadGroupPhase, LeadGroupRequestBasis
from .accounting import canonical_provider_json


class LeadGroupObservationKind(StrEnum):
    BOUND = "bound"
    CONTROL_ACCEPTED = "control_accepted"
    CONTEXT_ACTION = "context_action"
    REJECTED = "rejected"
    TERMINAL = "terminal"


def _snapshot(value: bytes | None) -> None:
    if value is not None and (
        type(value) is not bytes
        or type(json.loads(value)) is not dict
        or canonical_provider_json(json.loads(value)) != value
    ):
        raise ValueError("group observation requires frozen canonical objects")


@dataclass(frozen=True, slots=True)
class LeadGroupObservation:
    """One observed transition; snapshots are private, immutable, and non-authorizing."""

    kind: LeadGroupObservationKind
    request_basis: LeadGroupRequestBasis
    phase: LeadGroupPhase
    call_id: str | None = None
    catalog_snapshot: bytes | None = field(default=None, repr=False)
    selection_snapshots: tuple[bytes, ...] = field(default=(), repr=False)
    binding_snapshot: bytes | None = field(default=None, repr=False)
    delivery_snapshot: bytes | None = field(default=None, repr=False)
    action_type: str | None = None
    lineage_kind: str | None = None
    terminal_code: str | None = None

    def __post_init__(self) -> None:
        if type(self.kind) is not LeadGroupObservationKind or type(self.phase) is not LeadGroupPhase:
            raise TypeError("group observation requires typed kind and current phase")
        if type(self.request_basis) is not LeadGroupRequestBasis:
            raise TypeError("group observation requires the original request basis")
        for value in (self.call_id, self.action_type, self.lineage_kind, self.terminal_code):
            if value is not None and (type(value) is not str or not value or len(value) > 256):
                raise ValueError("group observation identity is invalid")
        for value in (self.catalog_snapshot, self.binding_snapshot, self.delivery_snapshot):
            _snapshot(value)
        if type(self.selection_snapshots) is not tuple:
            raise TypeError("group selection history must be immutable")
        for value in self.selection_snapshots:
            _snapshot(value)

    def as_object(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "request_basis": self.request_basis.as_object(),
            "phase": self.phase.value,
            "call_id": self.call_id,
            "catalog": None if self.catalog_snapshot is None else json.loads(self.catalog_snapshot),
            "selections": [json.loads(value) for value in self.selection_snapshots],
            "binding": None if self.binding_snapshot is None else json.loads(self.binding_snapshot),
            "delivery": None if self.delivery_snapshot is None else json.loads(self.delivery_snapshot),
            "action_type": self.action_type,
            "lineage_kind": self.lineage_kind,
            "terminal_code": self.terminal_code,
        }

    @classmethod
    def from_object(cls, value: object) -> LeadGroupObservation:
        names = {"kind", "request_basis", "phase", "call_id", "catalog", "selections",
                 "binding", "delivery", "action_type", "lineage_kind", "terminal_code"}
        if type(value) is not dict or set(value) != names:
            raise ValueError("invalid group observation fields")
        basis = value["request_basis"]
        if type(basis) is not dict or set(basis) != {"operation_id", "result_correlation_id", "scope_ref"}:
            raise ValueError("invalid group request reference")
        selections = value["selections"]
        if type(selections) is not list:
            raise ValueError("invalid group selection history")
        return cls(
            LeadGroupObservationKind(value["kind"]), LeadGroupRequestBasis(**basis),
            LeadGroupPhase(value["phase"]), value["call_id"],
            None if value["catalog"] is None else canonical_provider_json(value["catalog"]),
            tuple(canonical_provider_json(item) for item in selections),
            None if value["binding"] is None else canonical_provider_json(value["binding"]),
            None if value["delivery"] is None else canonical_provider_json(value["delivery"]),
            value["action_type"], value["lineage_kind"], value["terminal_code"],
        )


class LeadGroupObservationSink(Protocol):
    """A synchronous observer; it cannot change group eligibility or authority."""

    def __call__(self, observation: LeadGroupObservation) -> None: ...


@dataclass(frozen=True, slots=True)
class LeadGroupTrace:
    """A published logical invocation, separate from public conversation content."""

    invocation_id: str
    observations: tuple[LeadGroupObservation, ...] = field(repr=False)
    terminal_code: str
    elapsed_ns: int | None = field(default=None, kw_only=True)
    terminal_snapshot: bytes | None = field(default=None, kw_only=True, repr=False)

    def __post_init__(self) -> None:
        if any(type(value) is not str or not value or len(value) > 256
               for value in (self.invocation_id, self.terminal_code)):
            raise ValueError("group trace identity is invalid")
        if type(self.observations) is not tuple or not self.observations or any(
            type(item) is not LeadGroupObservation for item in self.observations
        ):
            raise ValueError("group trace requires actual ordered observations")
        if self.elapsed_ns is not None and (type(self.elapsed_ns) is not int or self.elapsed_ns < 0):
            raise ValueError("logical elapsed nanoseconds must be nonnegative")
        _snapshot(self.terminal_snapshot)
        basis = self.observations[0].request_basis
        if any(item.request_basis != basis for item in self.observations):
            raise ValueError("group trace cannot join different logical requests")

    def as_object(self) -> dict[str, object]:
        return {"invocation_id": self.invocation_id, "terminal_code": self.terminal_code,
                "observations": [item.as_object() for item in self.observations], "elapsed_ns": self.elapsed_ns,
                "terminal": None if self.terminal_snapshot is None else json.loads(self.terminal_snapshot)}

    @classmethod
    def from_object(cls, value: object) -> LeadGroupTrace:
        if type(value) is not dict or set(value) != {"invocation_id", "terminal_code", "observations", "elapsed_ns", "terminal"}:
            raise ValueError("invalid group trace fields")
        if type(value["observations"]) is not list:
            raise ValueError("invalid group trace observations")
        return cls(value["invocation_id"], tuple(LeadGroupObservation.from_object(item)
                   for item in value["observations"]), value["terminal_code"], elapsed_ns=value["elapsed_ns"],
                   terminal_snapshot=None if value["terminal"] is None else canonical_provider_json(value["terminal"]))
