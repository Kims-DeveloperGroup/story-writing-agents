"""Pure persisted-runtime fact interpretation for the interactive controller."""

from __future__ import annotations

from dataclasses import dataclass

from ..errors import (
    SessionIdError,
    WorkspaceFormatError,
    WorkspaceSecurityError,
    WorkspaceVersionUnsupportedError,
)
from ..interactive_terminal import InteractiveError, InteractiveErrorCode
from ..models import DebateContextMode, InputKind
from ..workspace import Workspace


@dataclass(frozen=True, slots=True)
class DebateInspectionFacts:
    """Whitelisted lifecycle facts for one persisted or active debate."""

    session_id: str
    debate_id: str
    status: str
    terminal: bool
    input_kind: str | None
    revision_id: str | None
    rounds_completed: int
    artifact: str | None


@dataclass(frozen=True, slots=True)
class DebateContextInspectionFacts:
    """Whitelisted identity and accounting facts for one debate sidecar."""

    conversation_id: str
    mode: DebateContextMode
    event_count: int
    complete_turns: int
    retained_turns: int
    omitted_turns: int
    retained_utf8_bytes: int
    truncated: bool


def validate_debate_inspection(
    value: object,
    *,
    session_id: str,
    debate_id: str,
) -> DebateInspectionFacts:
    """Validate one full runtime inspection and return typed safe facts."""

    expected = {
        "session_id",
        "debate_id",
        "status",
        "terminal",
        "state",
        "result",
        "messages",
    }
    if type(value) is not dict or set(value) != expected:
        raise ValueError("runtime debate inspection has an invalid shape")
    if (
        value["session_id"] != session_id
        or value["debate_id"] != debate_id
        or type(value["messages"]) is not list
        or any(type(message) is not dict for message in value["messages"])
    ):
        raise ValueError("runtime debate inspection identity is invalid")
    status = value["status"]
    terminal = value["terminal"]
    if (
        status
        not in {
            "not_found",
            "active",
            "consensus",
            "deadlock",
            "failed",
            "interrupted",
        }
        or type(terminal) is not bool
    ):
        raise ValueError("runtime debate inspection lifecycle is invalid")

    state = value["state"]
    result = value["result"]
    input_kind: object = None
    revision_id: object = None
    rounds_completed: object = 0
    artifact: object = None
    if result is not None:
        result_fields = {
            "session_id",
            "debate_id",
            "input_kind",
            "status",
            "revision_id",
            "rounds_completed",
            "final_candidate",
            "latest_candidate",
            "manuscript_path",
            "core_updates",
            "error",
            "action_required",
        }
        if type(result) is not dict or frozenset(result) not in {
            frozenset(result_fields),
            frozenset((*result_fields, "reference_operation_id")),
        }:
            raise ValueError("runtime debate result has an invalid shape")
        if terminal is not True:
            raise ValueError("runtime terminal debate sources are inconsistent")
        if (
            result["session_id"] != session_id
            or result["debate_id"] != debate_id
            or result["status"] != status
        ):
            raise ValueError("runtime debate result identity is invalid")
        if "reference_operation_id" in result:
            _validate_reference_operation_identifier(
                result["reference_operation_id"],
                session_id=session_id,
            )
        if state is not None:
            state_fields = {
                "schema_version",
                "session_id",
                "debate_id",
                "input_kind",
                "status",
                "terminal",
                "created_at",
                "updated_at",
                "revision_id",
                "rounds_completed",
                "error",
            }
            if type(state) is not dict or set(state) != state_fields:
                raise ValueError("runtime debate state has an invalid shape")
            if (
                state["session_id"] != session_id
                or state["debate_id"] != debate_id
                or state["status"] != status
                or state["terminal"] is not True
                or state["input_kind"] != result["input_kind"]
                or state["revision_id"] != result["revision_id"]
                or state["rounds_completed"] != result["rounds_completed"]
            ):
                raise ValueError("runtime debate state identity is invalid")
        input_kind = result["input_kind"]
        revision_id = result["revision_id"]
        rounds_completed = result["rounds_completed"]
        artifact = result["manuscript_path"]
    elif state is not None:
        state_fields = {
            "schema_version",
            "session_id",
            "debate_id",
            "input_kind",
            "status",
            "terminal",
            "created_at",
            "updated_at",
            "revision_id",
            "rounds_completed",
            "error",
        }
        if type(state) is not dict or set(state) != state_fields:
            raise ValueError("runtime debate state has an invalid shape")
        if (
            state["session_id"] != session_id
            or state["debate_id"] != debate_id
            or state["status"] != status
            or state["terminal"] is not terminal
        ):
            raise ValueError("runtime debate state identity is invalid")
        input_kind = state["input_kind"]
        revision_id = state["revision_id"]
        rounds_completed = state["rounds_completed"]
    elif status == "not_found":
        if terminal or value["messages"]:
            raise ValueError("not-found debate inspection is inconsistent")
    elif status != "interrupted" or terminal is not True or not value["messages"]:
        raise ValueError("runtime debate inspection has no lifecycle source")

    return _validated_debate_inspection_facts(
        session_id=session_id,
        debate_id=debate_id,
        status=status,
        terminal=terminal,
        input_kind=input_kind,
        revision_id=revision_id,
        rounds_completed=rounds_completed,
        artifact=artifact,
    )


def validate_session_inspection(
    value: object,
    *,
    session_id: str,
) -> tuple[DebateInspectionFacts, ...]:
    """Validate a session inspection and return ordered typed debate facts."""

    if type(value) is not dict or set(value) != {
        "session_id",
        "overrides",
        "debates",
    }:
        raise ValueError("runtime session inspection has an invalid shape")
    if (
        value["session_id"] != session_id
        or type(value["overrides"]) is not dict
        or type(value["debates"]) is not list
    ):
        raise ValueError("runtime session inspection is invalid")

    summaries: list[DebateInspectionFacts] = []
    previous_debate_id: str | None = None
    for row in value["debates"]:
        if type(row) is not dict:
            raise ValueError("runtime session debate summary is invalid")
        result_fields = {
            "session_id",
            "debate_id",
            "input_kind",
            "status",
            "revision_id",
            "rounds_completed",
            "final_candidate",
            "latest_candidate",
            "manuscript_path",
            "core_updates",
            "error",
            "action_required",
            "terminal",
        }
        state_fields = {
            "session_id",
            "debate_id",
            "input_kind",
            "status",
            "terminal",
            "revision_id",
            "rounds_completed",
            "error",
        }
        fields = set(row)
        if frozenset(fields) in {
            frozenset(result_fields),
            frozenset((*result_fields, "reference_operation_id")),
        }:
            if row["terminal"] is not True:
                raise ValueError("runtime session terminal summary is inconsistent")
            artifact = row["manuscript_path"]
            if "reference_operation_id" in row:
                _validate_reference_operation_identifier(
                    row["reference_operation_id"],
                    session_id=session_id,
                )
        elif fields == state_fields:
            artifact = None
        else:
            raise ValueError("runtime session debate summary has an invalid shape")
        if row["session_id"] != session_id:
            raise ValueError("runtime session debate identity is invalid")
        debate_id = row["debate_id"]
        _validate_safe_inspection_identifier(
            debate_id,
            label="debate ID",
        )
        if previous_debate_id is not None and debate_id <= previous_debate_id:
            raise ValueError("runtime session debates are not uniquely ordered")
        previous_debate_id = debate_id
        facts = _validated_debate_inspection_facts(
            session_id=session_id,
            debate_id=debate_id,
            status=row["status"],
            terminal=row["terminal"],
            input_kind=row["input_kind"],
            revision_id=row["revision_id"],
            rounds_completed=row["rounds_completed"],
            artifact=artifact,
        )
        summaries.append(facts)
    return tuple(summaries)


def _validated_debate_inspection_facts(
    *,
    session_id: str,
    debate_id: str,
    status: object,
    terminal: object,
    input_kind: object,
    revision_id: object,
    rounds_completed: object,
    artifact: object,
) -> DebateInspectionFacts:
    """Validate whitelisted lifecycle fields and construct typed safe facts."""

    if status not in {
        "not_found",
        "active",
        "consensus",
        "deadlock",
        "failed",
        "interrupted",
    }:
        raise ValueError("runtime debate status is invalid")
    if type(terminal) is not bool:
        raise ValueError("runtime debate terminal flag is invalid")
    if input_kind is not None and input_kind not in {
        InputKind.PROMPT.value,
        InputKind.DRAFT.value,
    }:
        raise ValueError("runtime debate input kind is invalid")
    if revision_id is not None:
        _validate_safe_inspection_identifier(
            revision_id,
            label="revision ID",
        )
    if (
        isinstance(rounds_completed, bool)
        or not isinstance(rounds_completed, int)
        or rounds_completed < 0
    ):
        raise ValueError("runtime debate round count is invalid")
    if artifact is not None and not isinstance(artifact, str):
        raise ValueError("runtime debate artifact is invalid")
    return DebateInspectionFacts(
        session_id=session_id,
        debate_id=debate_id,
        status=status,
        terminal=terminal,
        input_kind=input_kind,
        revision_id=revision_id,
        rounds_completed=rounds_completed,
        artifact=artifact,
    )


def validate_debate_context_inspection(
    value: object,
    *,
    session_id: str,
    debate_id: str,
) -> DebateContextInspectionFacts | None:
    """Project a strict sidecar to typed identity and accounting facts."""

    if value is None:
        return None
    if type(value) is not dict or set(value) != {
        "schema_version",
        "session_id",
        "debate_id",
        "conversation_id",
        "mode",
        "snapshot",
    }:
        raise ValueError("runtime debate context has an invalid shape")
    if (
        value["schema_version"] != 1
        or value["session_id"] != session_id
        or value["debate_id"] != debate_id
    ):
        raise ValueError("runtime debate context identity is invalid")
    conversation_id = value["conversation_id"]
    _validate_safe_inspection_identifier(
        conversation_id,
        label="conversation ID",
    )
    mode = value["mode"]
    snapshot = value["snapshot"]
    if mode == DebateContextMode.FRESH.value:
        if snapshot is not None:
            raise ValueError("fresh debate context has a snapshot")
        return DebateContextInspectionFacts(
            conversation_id=conversation_id,
            mode=DebateContextMode.FRESH,
            event_count=0,
            complete_turns=0,
            retained_turns=0,
            omitted_turns=0,
            retained_utf8_bytes=0,
            truncated=False,
        )
    if mode != DebateContextMode.INCLUDED.value or type(snapshot) is not dict:
        raise ValueError("runtime debate context mode is invalid")
    if (
        set(snapshot)
        != {
            "events",
            "total_complete_turns",
            "retained_turns",
            "omitted_turns",
            "retained_utf8_bytes",
            "truncated",
        }
        or type(snapshot["events"]) is not list
    ):
        raise ValueError("runtime debate context snapshot is invalid")
    event_fields = {
        "schema_version",
        "session_id",
        "conversation_id",
        "turn_id",
        "sequence",
        "timestamp",
        "sender",
        "recipient",
        "kind",
        "content",
    }
    if any(
        type(event) is not dict or set(event) != event_fields
        for event in snapshot["events"]
    ):
        raise ValueError("runtime debate context events are invalid")
    total = snapshot["total_complete_turns"]
    retained = snapshot["retained_turns"]
    omitted = snapshot["omitted_turns"]
    retained_bytes = snapshot["retained_utf8_bytes"]
    truncated = snapshot["truncated"]
    if (
        any(
            isinstance(count, bool) or not isinstance(count, int) or count < 0
            for count in (total, retained, omitted, retained_bytes)
        )
        or type(truncated) is not bool
    ):
        raise ValueError("runtime debate context counts are invalid")
    if (
        total != retained + omitted
        or len(snapshot["events"]) != retained * 2
        or truncated is not (omitted > 0)
    ):
        raise ValueError("runtime debate context counts are inconsistent")
    return DebateContextInspectionFacts(
        conversation_id=conversation_id,
        mode=DebateContextMode.INCLUDED,
        event_count=len(snapshot["events"]),
        complete_turns=total,
        retained_turns=retained,
        omitted_turns=omitted,
        retained_utf8_bytes=retained_bytes,
        truncated=truncated,
    )


def _validate_safe_inspection_identifier(
    value: object,
    *,
    label: str,
) -> str:
    """Return a canonical safe identifier or convert tampering to ValueError."""

    try:
        return Workspace.validate_identifier(value, label=label)
    except (SessionIdError, TypeError, ValueError) as exc:
        raise ValueError(f"runtime inspection {label} is invalid") from exc


def _validate_reference_operation_identifier(
    value: object,
    *,
    session_id: str,
) -> str:
    """Validate one safe operation identity and its embedded session scope."""

    if type(value) is not str or len(value) > 128:
        raise ValueError("runtime reference operation ID is invalid")
    prefix = f"rr_{len(session_id):02x}_{session_id}_"
    suffix = value[len(prefix) :] if value.startswith(prefix) else ""
    if len(suffix) != 32 or any(
        character not in "0123456789abcdef" for character in suffix
    ):
        raise ValueError("runtime reference operation ID is invalid")
    return value


def classify_inspection_failure(failure: Exception) -> InteractiveError:
    """Map one inspection failure to fixed safe interactive output."""

    if isinstance(failure, SessionIdError):
        code = InteractiveErrorCode.COMMAND_INVALID
    elif isinstance(failure, WorkspaceSecurityError):
        code = InteractiveErrorCode.WORKSPACE_UNSAFE
    elif isinstance(failure, WorkspaceVersionUnsupportedError):
        code = InteractiveErrorCode.WORKSPACE_VERSION_UNSUPPORTED
    elif isinstance(failure, (WorkspaceFormatError, OSError)):
        code = InteractiveErrorCode.WORKSPACE_INVALID
    else:
        code = InteractiveErrorCode.INTERNAL_ERROR
    return InteractiveError(code)


__all__ = [
    "DebateContextInspectionFacts",
    "DebateInspectionFacts",
    "classify_inspection_failure",
    "validate_debate_context_inspection",
    "validate_debate_inspection",
    "validate_session_inspection",
]
