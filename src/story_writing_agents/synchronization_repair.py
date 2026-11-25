"""Exact selection for continuing one disclosed synchronization condition."""

from __future__ import annotations

from dataclasses import dataclass

from .synchronization_assessment import (
    SynchronizationEventRef,
    require_synchronization_identifier,
)
from .writing_artifacts.catalog import canonical_artifact_kind


@dataclass(frozen=True, slots=True)
class SynchronizationRepairSelection:
    """The exact successful Apply, current condition head, and selected repair path."""

    application: SynchronizationEventRef
    condition_id: str
    head: SynchronizationEventRef
    repair_path: str

    def __post_init__(self) -> None:
        if type(self.application) is not SynchronizationEventRef or type(self.head) is not SynchronizationEventRef:
            raise TypeError("synchronization repair requires exact event references")
        require_synchronization_identifier(self.condition_id)
        if type(self.repair_path) is not str or canonical_artifact_kind(self.repair_path) is None:
            raise ValueError("synchronization repair path is not a canonical writing artifact")


def _ref_value(ref: SynchronizationEventRef) -> dict[str, str]:
    return {"conversation_id": ref.conversation_id, "event_id": ref.event_id, "digest": ref.digest}


def _decode_ref(value: object) -> SynchronizationEventRef:
    if type(value) is not dict or set(value) != {"conversation_id", "event_id", "digest"}:
        raise ValueError("synchronization repair reference has unexpected fields")
    return SynchronizationEventRef(value["conversation_id"], value["event_id"], value["digest"])


def synchronization_repair_selection_to_dict(selection: SynchronizationRepairSelection) -> dict[str, object]:
    if type(selection) is not SynchronizationRepairSelection:
        raise TypeError("synchronization repair selection must be exact")
    return {
        "application": _ref_value(selection.application),
        "condition_id": selection.condition_id,
        "head": _ref_value(selection.head),
        "repair_path": selection.repair_path,
    }


def synchronization_repair_selection_from_dict(value: object) -> SynchronizationRepairSelection:
    if type(value) is not dict or set(value) != {"application", "condition_id", "head", "repair_path"}:
        raise ValueError("synchronization repair selection has unexpected fields")
    return SynchronizationRepairSelection(
        _decode_ref(value["application"]), value["condition_id"],
        _decode_ref(value["head"]), value["repair_path"],
    )
