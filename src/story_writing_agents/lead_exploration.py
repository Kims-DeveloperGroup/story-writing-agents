"""Immutable current contracts for provisional Lead work and explicit adoption.

The runtime owns invocation, evidence authorization and persistence. This leaf
owns values only; an exploration report never grants authoring authority.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
import hashlib
import json
import re
from typing import Any

from .specialist_routing import AuthoringIntent


class LeadInteractionMode(StrEnum):
    ORCHESTRATION = "orchestration"
    EXPLORATION = "exploration"


class ExplorationIdeaKind(StrEnum):
    HYPOTHESIS = "hypothesis"
    ALTERNATIVE = "alternative"
    SHORT_EXAMPLE = "short_example"


class ExplorationReportStatus(StrEnum):
    IDEAS = "ideas"
    CLARIFICATION = "clarification"


def material_sha256(text: str) -> str:
    _text(text)
    return hashlib.sha256(text.encode("utf-8", errors="strict")).hexdigest()


def _text(value: str) -> None:
    if type(value) is not str or not value or "\x00" in value:
        raise ValueError("exploration text must be exact nonempty text")
    if len(value.encode("utf-8", errors="strict")) > 32_768:
        raise ValueError("exploration text exceeds the visible text limit")


def _identifier(value: str) -> None:
    _text(value)
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,256}", value):
        raise ValueError("exploration identifier is invalid")


def _digest(value: str) -> None:
    if type(value) is not str or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("exploration material digest is invalid")


def _identifiers(values: tuple[str, ...]) -> None:
    if type(values) is not tuple or len(values) != len(set(values)):
        raise ValueError("exploration identifiers must be a unique tuple")
    for value in values:
        _identifier(value)


@dataclass(frozen=True, slots=True)
class ExplorationArtifactRead:
    """Host-captured facts of an actual authorized artifact READ."""

    evidence_id: str
    source_id: str
    locator: str
    revision: str
    source_digest: str
    boundary_digest: str
    start_line: int | None = None
    end_line: int | None = None

    def __post_init__(self) -> None:
        _identifier(self.evidence_id)
        _identifier(self.source_id)
        _text(self.locator)
        _text(self.revision)
        for digest in (self.source_digest, self.boundary_digest):
            if type(digest) is not str or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
                raise ValueError("artifact READ digest must preserve the current sha256 identity")
        if (self.start_line is None) != (self.end_line is None):
            raise ValueError("artifact READ range is incomplete")
        if self.start_line is not None and (
            type(self.start_line) is not int or type(self.end_line) is not int
            or self.start_line < 1 or self.end_line < self.start_line
        ):
            raise ValueError("artifact READ range is invalid")


@dataclass(frozen=True, slots=True)
class ExplorationIdea:
    idea_id: str
    kind: ExplorationIdeaKind
    material_text: str
    material_digest: str
    basis_input_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _identifier(self.idea_id)
        if type(self.kind) is not ExplorationIdeaKind:
            raise TypeError("idea kind must be ExplorationIdeaKind")
        _text(self.material_text)
        _digest(self.material_digest)
        if material_sha256(self.material_text) != self.material_digest:
            raise ValueError("idea material digest differs from exact text")
        _identifiers(self.basis_input_ids)


@dataclass(frozen=True, slots=True)
class ExplorationObservation:
    text: str
    receipts: tuple[ExplorationArtifactRead, ...]

    def __post_init__(self) -> None:
        _text(self.text)
        if type(self.receipts) is not tuple or not self.receipts or any(
            type(item) is not ExplorationArtifactRead for item in self.receipts
        ):
            raise ValueError("grounded observation requires artifact READ receipts")
        _identifiers(tuple(item.evidence_id for item in self.receipts))


@dataclass(frozen=True, slots=True)
class LeadExplorationReport:
    request_event_id: str
    provider_call_id: str
    status: ExplorationReportStatus
    message: str
    ideas: tuple[ExplorationIdea, ...] = ()
    grounded_observations: tuple[ExplorationObservation, ...] = ()

    def __post_init__(self) -> None:
        _identifier(self.request_event_id)
        _identifier(self.provider_call_id)
        if type(self.status) is not ExplorationReportStatus:
            raise TypeError("report status must be ExplorationReportStatus")
        _text(self.message)
        if type(self.ideas) is not tuple or any(type(i) is not ExplorationIdea for i in self.ideas):
            raise TypeError("report ideas must be immutable ExplorationIdea values")
        if type(self.grounded_observations) is not tuple or any(
            type(i) is not ExplorationObservation for i in self.grounded_observations
        ):
            raise TypeError("report observations must be immutable values")
        _identifiers(tuple(item.idea_id for item in self.ideas))
        if self.status is ExplorationReportStatus.CLARIFICATION and (
            self.ideas or self.grounded_observations
        ):
            raise ValueError("clarification cannot carry report material")
        if self.status is ExplorationReportStatus.IDEAS and not (
            self.ideas or self.grounded_observations
        ):
            raise ValueError("exploration report requires ideas or observations")
        text_bytes = sum(len(text.encode("utf-8")) for text in (
            self.message, *(i.material_text for i in self.ideas),
            *(i.text for i in self.grounded_observations),
        ))
        if text_bytes > 32_768:
            raise ValueError("exploration report exceeds the visible text limit")


@dataclass(frozen=True, slots=True)
class ExplorationAdoptionSelection:
    source_conversation_id: str
    result_event_id: str
    idea_id: str
    material_digest: str
    target_paths: tuple[str, ...]
    start_character: int | None = None
    end_character: int | None = None
    intent: AuthoringIntent = AuthoringIntent.AUTHORING

    def __post_init__(self) -> None:
        for value in (self.source_conversation_id, self.result_event_id, self.idea_id):
            _identifier(value)
        _digest(self.material_digest)
        if type(self.intent) is not AuthoringIntent:
            raise TypeError("adoption intent must be AuthoringIntent")
        start, end = self.start_character, self.end_character
        if (start is None) != (end is None) or (start is not None and (
            type(start) is not int or type(end) is not int or start < 0 or end <= start
        )):
            raise ValueError("adoption range must be nonempty code-point offsets")
        if type(self.target_paths) is not tuple or not self.target_paths or len(set(self.target_paths)) != len(self.target_paths):
            raise ValueError("adoption requires unique exact target paths")
        for path in self.target_paths:
            _text(path)
            if path.startswith("/") or "\\" in path or any(part in {"", ".", ".."} for part in path.split("/")):
                raise ValueError("adoption target must be canonical relative POSIX text")

    def select_text(self, material_text: str) -> str:
        """Select exact supplied code points, retaining the full source digest."""
        if material_sha256(material_text) != self.material_digest:
            raise ValueError("adoption source text changed")
        if self.start_character is None:
            return material_text
        if self.end_character > len(material_text):
            raise ValueError("adoption range exceeds its exact source")
        return material_text[self.start_character:self.end_character]



@dataclass(frozen=True, slots=True)
class ExplorationAdoptionCapture:
    user_event_id: str
    selection: ExplorationAdoptionSelection
    selected_text: str
    fresh_task_id: str
    source_event_digest: str
    selected_material_digest: str
    task_revision: int = 1

    def __post_init__(self) -> None:
        _identifier(self.user_event_id)
        _identifier(self.fresh_task_id)
        if type(self.selection) is not ExplorationAdoptionSelection:
            raise TypeError("adoption capture requires exact selection")
        if type(self.task_revision) is not int or self.task_revision != 1:
            raise ValueError("adoption must begin a fresh task at revision one")
        _digest(self.source_event_digest)
        _digest(self.selected_material_digest)
        if material_sha256(self.selected_text) != self.selected_material_digest:
            raise ValueError("adoption selected text digest differs")
        start, end = self.selection.start_character, self.selection.end_character
        if start is None:
            if self.selected_material_digest != self.selection.material_digest:
                raise ValueError("whole adoption differs from its source material")
        elif len(self.selected_text) != end - start:
            raise ValueError("adoption selected text differs from its range")

    def validate_source(self, material_text: str, source_event_digest: str) -> None:
        if source_event_digest != self.source_event_digest or self.selected_text != self.selection.select_text(material_text):
            raise ValueError("adoption source identity, text or range changed")



def exploration_report_to_dict(report: LeadExplorationReport) -> dict[str, Any]:
    if type(report) is not LeadExplorationReport:
        raise TypeError("expected LeadExplorationReport")
    return json.loads(json.dumps(asdict(report), ensure_ascii=False))


def _closed(value: Any, fields: set[str]) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        raise ValueError("exploration value violates its closed current shape")
    return value


def exploration_report_from_dict(value: Any) -> LeadExplorationReport:
    value = _closed(value, {"request_event_id", "provider_call_id", "status", "message", "ideas", "grounded_observations"})
    if type(value["ideas"]) is not list or type(value["grounded_observations"]) is not list:
        raise ValueError("exploration collections must be arrays")
    ideas = []
    for item in value["ideas"]:
        item = _closed(item, {"idea_id", "kind", "material_text", "material_digest", "basis_input_ids"})
        if type(item["basis_input_ids"]) is not list:
            raise ValueError("idea basis inputs must be an array")
        ideas.append(ExplorationIdea(item["idea_id"], ExplorationIdeaKind(item["kind"]), item["material_text"], item["material_digest"], tuple(item["basis_input_ids"])))
    observations = []
    for item in value["grounded_observations"]:
        item = _closed(item, {"text", "receipts"})
        if type(item["receipts"]) is not list:
            raise ValueError("observation receipts must be an array")
        receipts = tuple(ExplorationArtifactRead(**_closed(receipt, {
            "evidence_id", "source_id", "locator", "revision", "source_digest",
            "boundary_digest", "start_line", "end_line",
        })) for receipt in item["receipts"])
        observations.append(ExplorationObservation(item["text"], receipts))
    return LeadExplorationReport(value["request_event_id"], value["provider_call_id"], ExplorationReportStatus(value["status"]), value["message"], tuple(ideas), tuple(observations))


def adoption_capture_to_dict(capture: ExplorationAdoptionCapture) -> dict[str, Any]:
    if type(capture) is not ExplorationAdoptionCapture:
        raise TypeError("expected ExplorationAdoptionCapture")
    value = json.loads(json.dumps(asdict(capture), ensure_ascii=False))
    value["selection"]["writing_intent"] = value["selection"].pop("intent")
    return value



def adoption_capture_from_dict(value: Any) -> ExplorationAdoptionCapture:
    value = _closed(value, {"user_event_id", "selection", "selected_text", "fresh_task_id", "task_revision", "source_event_digest", "selected_material_digest"})
    selection = _closed(value["selection"], {"source_conversation_id", "result_event_id", "idea_id", "material_digest", "target_paths", "start_character", "end_character", "writing_intent"})
    if type(selection["target_paths"]) is not list:
        raise ValueError("adoption targets must be an array")
    selection = ExplorationAdoptionSelection(
        selection["source_conversation_id"], selection["result_event_id"],
        selection["idea_id"], selection["material_digest"], tuple(selection["target_paths"]),
        selection["start_character"], selection["end_character"],
        AuthoringIntent(selection["writing_intent"]),
    )
    return ExplorationAdoptionCapture(
        value["user_event_id"], selection, value["selected_text"], value["fresh_task_id"],
        value["source_event_digest"], value["selected_material_digest"], value["task_revision"],
    )
