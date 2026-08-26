"""Pure runtime fact interpretation for the interactive controller."""

from __future__ import annotations

from dataclasses import dataclass

from ..conversation_retrieval import (
    ConversationRagAttemptOutcome,
    ConversationRetrievalProgressEvent,
    ConversationRetrievalProgressPhase,
    ConversationRetrievalResult,
    ConversationRetrievalSource,
)
from ..conversation_search import ConversationCitation
from ..errors import (
    BackendProtocolError,
    ConfigurationError,
    ConversationNotFoundError,
    ConversationRetrievalBudgetError,
    ConversationRetrievalObserverError,
    ConversationRetrievalSemanticError,
    SessionAccessBlockedError,
    SessionExistsError,
    SessionIdError,
    SessionNotFoundError,
    SessionOwnershipUnknownError,
    SessionPausedError,
    WorkspaceFormatError,
    WorkspaceSecurityError,
    WorkspaceVersionUnsupportedError,
)
from ..interactive_terminal import InteractiveError, InteractiveErrorCode
from ..models import (
    DebateContextMode,
    DebateFailureKind,
    DebateProgressEvent,
    DebateProgressPhase,
    DebateResult,
    DebateStatus,
    InputKind,
)
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
    if status not in {
        "not_found",
        "active",
        "consensus",
        "deadlock",
        "failed",
        "interrupted",
    } or type(terminal) is not bool:
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
                raise ValueError(
                    "runtime session terminal summary is inconsistent"
                )
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
    if set(snapshot) != {
        "events",
        "total_complete_turns",
        "retained_turns",
        "omitted_turns",
        "retained_utf8_bytes",
        "truncated",
    } or type(snapshot["events"]) is not list:
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
    if any(
        isinstance(count, bool) or not isinstance(count, int) or count < 0
        for count in (total, retained, omitted, retained_bytes)
    ) or type(truncated) is not bool:
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
    suffix = value[len(prefix):] if value.startswith(prefix) else ""
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


def validate_conversation_retrieval_progress(
    event: object,
    *,
    expected_session_id: str,
    expected_conversation_id: str,
    expected_agent_id: str,
    previous: ConversationRetrievalProgressEvent | None,
) -> ConversationRetrievalProgressEvent:
    """Reconstruct and correlate one retrieval progress fact without mutation."""

    if type(event) is not ConversationRetrievalProgressEvent:
        raise TypeError(
            "retrieval progress sink requires a "
            "ConversationRetrievalProgressEvent"
        )
    try:
        canonical = ConversationRetrievalProgressEvent(
            session_id=event.session_id,
            conversation_id=event.conversation_id,
            turn_id=event.turn_id,
            agent_id=event.agent_id,
            action_index=event.action_index,
            phase=event.phase,
            search_hit_count=event.search_hit_count,
            transcript_turn_count=event.transcript_turn_count,
            transcript_event_count=event.transcript_event_count,
            observation_utf8_bytes=event.observation_utf8_bytes,
            truncated=event.truncated,
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise TypeError("retrieval progress event is invalid") from exc
    if (
        canonical.session_id != expected_session_id
        or canonical.conversation_id != expected_conversation_id
        or canonical.agent_id != expected_agent_id
    ):
        raise ValueError("retrieval progress identity does not match the turn")

    if previous is None:
        if (
            canonical.phase
            is not ConversationRetrievalProgressPhase.SEARCH_STARTED
            or canonical.action_index != 1
        ):
            raise ValueError("retrieval progress must begin with search action 1")
    elif canonical.turn_id != previous.turn_id:
        raise ValueError("retrieval progress turn identity changed")
    elif previous.phase is ConversationRetrievalProgressPhase.SEARCH_STARTED:
        if (
            canonical.phase
            is not ConversationRetrievalProgressPhase.SEARCH_COMPLETED
            or canonical.action_index != previous.action_index
        ):
            raise ValueError("retrieval search progress pair is invalid")
    elif previous.phase is ConversationRetrievalProgressPhase.READ_STARTED:
        if (
            canonical.phase
            is not ConversationRetrievalProgressPhase.READ_COMPLETED
            or canonical.action_index != previous.action_index
        ):
            raise ValueError("retrieval read progress pair is invalid")
    else:
        expected_index = previous.action_index + 1
        if canonical.action_index != expected_index:
            raise ValueError("retrieval progress action index is not sequential")
        if previous.phase is ConversationRetrievalProgressPhase.SEARCH_COMPLETED:
            allowed = {
                ConversationRetrievalProgressPhase.SEARCH_STARTED,
                ConversationRetrievalProgressPhase.READ_STARTED,
            }
        else:
            assert (
                previous.phase
                is ConversationRetrievalProgressPhase.READ_COMPLETED
            )
            allowed = {ConversationRetrievalProgressPhase.READ_STARTED}
        if canonical.phase not in allowed:
            raise ValueError("retrieval progress phase order is invalid")
    return canonical


def _canonical_retrieval_source(value: object) -> ConversationRetrievalSource:
    """Deeply reconstruct one exact retrieval source without external work."""

    if type(value) is not ConversationRetrievalSource:
        raise TypeError("retrieval result source is invalid")
    citation = value.citation
    if type(citation) is not ConversationCitation:
        raise TypeError("retrieval result citation is invalid")
    return ConversationRetrievalSource(
        subject=value.subject,
        citation=ConversationCitation(
            session_id=citation.session_id,
            conversation_id=citation.conversation_id,
            turn_id=citation.turn_id,
            sequence=citation.sequence,
            event_kind=citation.event_kind,
        ),
    )


def validate_conversation_retrieval_result(
    value: object,
    *,
    expected_session_id: str,
    expected_conversation_id: str,
    expected_agent_id: str,
    progress_events: tuple[ConversationRetrievalProgressEvent, ...],
) -> ConversationRetrievalResult:
    """Return one exact correlated result after pure deep reconstruction.

    Sources, RAG provenance, and citations are copied into exact built-in
    contracts. Sources outside the preflight-authorized RAG subset require
    progress ending on a completed pair whose turn matches the result. No
    output, provider state, controller state, or transcript is mutated; invalid
    collaborator values raise ``TypeError`` or ``ValueError``.
    """

    if type(value) is not ConversationRetrievalResult:
        raise TypeError("runtime returned an invalid retrieval result")
    sources: list[ConversationRetrievalSource] = []
    if type(value.sources) is not tuple:
        raise TypeError("retrieval result sources must be a tuple")
    for source in value.sources:
        sources.append(_canonical_retrieval_source(source))
    rag_sources: list[ConversationRetrievalSource] = []
    if type(value.rag_sources) is not tuple:
        raise TypeError("retrieval result RAG sources must be a tuple")
    for source in value.rag_sources:
        rag_sources.append(_canonical_retrieval_source(source))
    rag_outcome: ConversationRagAttemptOutcome | None = None
    if value.rag_outcome is not None:
        if type(value.rag_outcome) is not ConversationRagAttemptOutcome:
            raise TypeError("retrieval result RAG outcome is invalid")
        rag_outcome = ConversationRagAttemptOutcome(
            outcome=value.rag_outcome.outcome,
            reason=value.rag_outcome.reason,
        )
    canonical = ConversationRetrievalResult(
        session_id=value.session_id,
        conversation_id=value.conversation_id,
        turn_id=value.turn_id,
        agent_id=value.agent_id,
        content=value.content,
        context=value.context,
        sources=tuple(sources),
        reference_operation_id=value.reference_operation_id,
        rag_outcome=rag_outcome,
        rag_sources=tuple(rag_sources),
    )
    if (
        canonical.session_id != expected_session_id
        or canonical.conversation_id != expected_conversation_id
        or canonical.agent_id != expected_agent_id
    ):
        raise ValueError("retrieval result identity does not match its turn")
    if canonical.reference_operation_id is not None:
        _validate_reference_operation_identifier(
            canonical.reference_operation_id,
            session_id=expected_session_id,
        )
    rag_source_set = set(canonical.rag_sources)
    if any(
        source not in rag_source_set for source in canonical.sources
    ) and not progress_events:
        raise ValueError("retrieval sources require completed search progress")
    if progress_events:
        final_progress = progress_events[-1]
        if final_progress.phase not in {
            ConversationRetrievalProgressPhase.SEARCH_COMPLETED,
            ConversationRetrievalProgressPhase.READ_COMPLETED,
        }:
            raise ValueError("retrieval progress ended before completion")
        if final_progress.turn_id != canonical.turn_id:
            raise ValueError("retrieval progress turn does not match its result")
    return canonical


def classify_progress_callback_failure(
    failure: BaseException,
) -> InteractiveError:
    """Map one synchronous progress failure to its fixed fatal code."""

    code = (
        InteractiveErrorCode.OPERATION_INTERRUPTED
        if isinstance(failure, (OSError, KeyboardInterrupt))
        else InteractiveErrorCode.INTERNAL_ERROR
    )
    return InteractiveError(code)


def validate_debate_progress(
    event: object,
    *,
    expected_session_id: str,
    expected_input_kind: InputKind,
    expected_context_mode: DebateContextMode,
    previous: DebateProgressEvent | None,
) -> DebateProgressEvent:
    """Reconstruct and correlate one debate progress fact without mutation."""

    if type(event) is not DebateProgressEvent:
        raise TypeError("progress sink requires a DebateProgressEvent")
    try:
        canonical = DebateProgressEvent(
            session_id=event.session_id,
            debate_id=event.debate_id,
            input_kind=event.input_kind,
            phase=event.phase,
            revision_id=event.revision_id,
            round_number=event.round_number,
            context_mode=event.context_mode,
            context_event_count=event.context_event_count,
            context_omitted_turns=event.context_omitted_turns,
            conflicting_agents=event.conflicting_agents,
            manuscript_path=event.manuscript_path,
            core_updates=event.core_updates,
            failure_kind=event.failure_kind,
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise TypeError("progress event is invalid") from exc
    if (
        canonical.session_id != expected_session_id
        or canonical.input_kind is not expected_input_kind
    ):
        raise ValueError("progress event identity does not match the run")
    if previous is None:
        if canonical.phase not in {
            DebateProgressPhase.STARTED,
            DebateProgressPhase.INTERRUPTED,
        }:
            raise ValueError(
                "the first progress event must be started or interrupted"
            )
        if (
            canonical.phase is DebateProgressPhase.STARTED
            and canonical.context_mode is not expected_context_mode
        ):
            raise ValueError("started progress context mode does not match")
    else:
        if canonical.debate_id != previous.debate_id:
            raise ValueError("progress debate identity changed")
        if not _debate_progress_transition_is_valid(
            previous.phase,
            canonical.phase,
        ):
            raise ValueError("progress phase order is invalid")
    return canonical


def _debate_progress_transition_is_valid(
    previous: DebateProgressPhase,
    current: DebateProgressPhase,
) -> bool:
    """Return whether two adjacent trusted phases can occur in one workflow."""

    terminal = {
        DebateProgressPhase.CONSENSUS,
        DebateProgressPhase.DEADLOCK,
        DebateProgressPhase.FAILED,
        DebateProgressPhase.INTERRUPTED,
    }
    if previous in terminal:
        return False
    if current is DebateProgressPhase.INTERRUPTED:
        return True
    allowed = {
        DebateProgressPhase.STARTED: {
            DebateProgressPhase.DRAFTED,
            DebateProgressPhase.FAILED,
        },
        DebateProgressPhase.DRAFTED: {
            DebateProgressPhase.AUDIT_STARTED,
        },
        DebateProgressPhase.AUDIT_STARTED: {
            DebateProgressPhase.AUDIT_COMPLETED,
            DebateProgressPhase.FAILED,
        },
        DebateProgressPhase.AUDIT_COMPLETED: {
            DebateProgressPhase.SYNTHESIZED,
            DebateProgressPhase.ARTIFACTS_COMMITTED,
            DebateProgressPhase.DEADLOCK,
            DebateProgressPhase.FAILED,
        },
        DebateProgressPhase.SYNTHESIZED: {
            DebateProgressPhase.AUDIT_STARTED,
        },
        DebateProgressPhase.ARTIFACTS_COMMITTED: {
            DebateProgressPhase.CONSENSUS,
        },
    }
    return current in allowed.get(previous, set())


def validate_consensus_result(
    result: DebateResult,
    *,
    expected_session_id: str,
    expected_input_kind: InputKind,
    progress_events: tuple[DebateProgressEvent, ...],
) -> tuple[DebateProgressEvent, DebateProgressEvent | None]:
    """Validate terminal result identity and typed progress correlation.

    The operation reads no persistence and exposes no result prose. Any type,
    identity, status, revision, round, or committed-artifact mismatch raises a
    protocol value failure for the controller's fatal cleanup translation.
    """

    if type(result) is not DebateResult or not progress_events:
        raise ValueError("runtime consensus result is invalid")
    started = progress_events[0]
    terminal = progress_events[-1]
    if (
        started.phase is not DebateProgressPhase.STARTED
        or result.session_id != expected_session_id
        or result.session_id != started.session_id
        or result.debate_id != started.debate_id
        or result.input_kind is not expected_input_kind
        or result.input_kind is not started.input_kind
        or not isinstance(result.status, DebateStatus)
        or result.revision_id != terminal.revision_id
        or isinstance(result.rounds_completed, bool)
        or not isinstance(result.rounds_completed, int)
        or result.rounds_completed != terminal.round_number
        or type(result.core_updates) is not tuple
    ):
        raise ValueError("runtime consensus result is invalid")
    if result.reference_operation_id is not None:
        _validate_reference_operation_identifier(
            result.reference_operation_id,
            session_id=expected_session_id,
        )
    expected_terminal = {
        DebateStatus.CONSENSUS: DebateProgressPhase.CONSENSUS,
        DebateStatus.DEADLOCK: DebateProgressPhase.DEADLOCK,
        DebateStatus.FAILED: DebateProgressPhase.FAILED,
    }[result.status]
    if terminal.phase is not expected_terminal:
        raise ValueError("runtime consensus result is invalid")

    artifact_event: DebateProgressEvent | None = None
    if result.status is DebateStatus.CONSENSUS:
        if len(progress_events) < 2:
            raise ValueError("runtime consensus result is invalid")
        artifact_event = progress_events[-2]
        if (
            artifact_event.phase
            is not DebateProgressPhase.ARTIFACTS_COMMITTED
            or result.manuscript_path != artifact_event.manuscript_path
            or result.core_updates != artifact_event.core_updates
            or result.manuscript_path is None
        ):
            raise ValueError("runtime consensus result is invalid")
    elif (
        result.manuscript_path is not None
        or result.core_updates
        or result.final_candidate is not None
        or any(
            event.phase is DebateProgressPhase.ARTIFACTS_COMMITTED
            for event in progress_events
        )
    ):
        raise ValueError("runtime consensus result is invalid")
    return terminal, artifact_event


def classify_consensus_execution_failure(
    failure: Exception,
    *,
    draft_payload_was_empty: bool,
    progress_events: tuple[DebateProgressEvent, ...],
) -> InteractiveError:
    """Select a fixed pre-result error from trusted controller evidence."""

    if isinstance(failure, ConfigurationError):
        code = InteractiveErrorCode.PROVIDER_PREFLIGHT_FAILED
    elif isinstance(failure, SessionNotFoundError):
        code = InteractiveErrorCode.SESSION_NOT_FOUND
    elif isinstance(failure, SessionPausedError):
        code = InteractiveErrorCode.SESSION_PAUSED
    elif isinstance(failure, SessionAccessBlockedError):
        code = InteractiveErrorCode.SESSION_ACCESS_BLOCKED
    elif isinstance(failure, SessionOwnershipUnknownError):
        code = InteractiveErrorCode.SESSION_OWNERSHIP_UNKNOWN
    elif isinstance(failure, BackendProtocolError):
        code = InteractiveErrorCode.PROVIDER_PROTOCOL_ERROR
    elif isinstance(failure, ConversationNotFoundError):
        code = InteractiveErrorCode.TRANSCRIPT_INVALID
    elif isinstance(failure, WorkspaceSecurityError):
        code = InteractiveErrorCode.WORKSPACE_INVALID
    elif isinstance(failure, WorkspaceVersionUnsupportedError):
        code = InteractiveErrorCode.WORKSPACE_VERSION_UNSUPPORTED
    elif isinstance(failure, (WorkspaceFormatError, OSError)):
        code = InteractiveErrorCode.WORKSPACE_INVALID
    elif (
        draft_payload_was_empty
        and not progress_events
        and isinstance(failure, (TypeError, ValueError))
    ):
        code = InteractiveErrorCode.DRAFT_INPUT_INVALID
    elif not progress_events and isinstance(failure, (TypeError, ValueError)):
        code = InteractiveErrorCode.TRANSCRIPT_INVALID
    else:
        code = InteractiveErrorCode.INTERNAL_ERROR
    return InteractiveError(code)


def interactive_code_for_debate_failure(
    failure_kind: DebateFailureKind,
) -> InteractiveErrorCode:
    """Map one trusted runtime failure category to a fixed terminal code."""

    return {
        DebateFailureKind.PREFLIGHT: InteractiveErrorCode.PROVIDER_PREFLIGHT_FAILED,
        DebateFailureKind.PROTOCOL: InteractiveErrorCode.PROVIDER_PROTOCOL_ERROR,
        DebateFailureKind.CONTEXT: InteractiveErrorCode.TRANSCRIPT_INVALID,
        DebateFailureKind.WORKSPACE: InteractiveErrorCode.WORKSPACE_INVALID,
        DebateFailureKind.ARTIFACT_CONFLICT: InteractiveErrorCode.ARTIFACT_CONFLICT,
        DebateFailureKind.ARTIFACT_FAILURE: InteractiveErrorCode.CONSENSUS_FAILED,
        DebateFailureKind.INTERNAL: InteractiveErrorCode.CONSENSUS_FAILED,
    }[failure_kind]


def classify_advisory_turn_failure(
    failure: Exception,
) -> InteractiveError:
    """Map exact turn exception types to safe codes without mutation.

    Established session, conversation, and workspace categories take
    precedence over provider categories. The returned error carries no raw
    diagnostic; unknown exceptions become ``internal_error`` for fatal
    cleanup at the caller.
    """

    if isinstance(failure, SessionIdError):
        code = InteractiveErrorCode.SESSION_INVALID
    elif isinstance(failure, ConversationRetrievalSemanticError):
        code = InteractiveErrorCode.RETRIEVAL_INVALID
    elif isinstance(failure, ConversationRetrievalBudgetError):
        code = InteractiveErrorCode.RETRIEVAL_BUDGET_EXHAUSTED
    elif isinstance(failure, ConversationRetrievalObserverError):
        code = InteractiveErrorCode.INTERNAL_ERROR
    elif isinstance(failure, SessionExistsError):
        code = InteractiveErrorCode.SESSION_EXISTS
    elif isinstance(failure, SessionNotFoundError):
        code = InteractiveErrorCode.SESSION_NOT_FOUND
    elif isinstance(failure, SessionPausedError):
        code = InteractiveErrorCode.SESSION_PAUSED
    elif isinstance(failure, SessionAccessBlockedError):
        code = InteractiveErrorCode.SESSION_ACCESS_BLOCKED
    elif isinstance(failure, SessionOwnershipUnknownError):
        code = InteractiveErrorCode.SESSION_OWNERSHIP_UNKNOWN
    elif isinstance(failure, ConversationNotFoundError):
        code = InteractiveErrorCode.CONVERSATION_NOT_FOUND
    elif isinstance(failure, WorkspaceSecurityError):
        code = InteractiveErrorCode.WORKSPACE_UNSAFE
    elif isinstance(failure, WorkspaceVersionUnsupportedError):
        code = InteractiveErrorCode.WORKSPACE_VERSION_UNSUPPORTED
    elif isinstance(failure, (WorkspaceFormatError, OSError)):
        code = InteractiveErrorCode.WORKSPACE_INVALID
    elif isinstance(failure, ConfigurationError):
        code = InteractiveErrorCode.PROVIDER_PREFLIGHT_FAILED
    elif isinstance(failure, BackendProtocolError):
        code = InteractiveErrorCode.PROVIDER_PROTOCOL_ERROR
    else:
        code = InteractiveErrorCode.INTERNAL_ERROR
    return InteractiveError(code)


__all__ = [
    "DebateContextInspectionFacts",
    "DebateInspectionFacts",
    "classify_advisory_turn_failure",
    "classify_consensus_execution_failure",
    "classify_inspection_failure",
    "classify_progress_callback_failure",
    "interactive_code_for_debate_failure",
    "validate_consensus_result",
    "validate_conversation_retrieval_progress",
    "validate_conversation_retrieval_result",
    "validate_debate_context_inspection",
    "validate_debate_inspection",
    "validate_debate_progress",
    "validate_session_inspection",
]
