"""Typed provider reply reconstruction and closed phase constraints."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, Mapping

from .conversation_retrieval import ConversationRetrievalAction, ConversationRetrievalActionKind, ConversationRetrievalAnswerAction, ConversationRetrievalReadAction, ConversationRetrievalRequest, ConversationRetrievalSearchAction
from .errors import BackendProtocolError, ConversationRetrievalSemanticError, SessionIdError
from .models import AgentReply, AgentReplyKind, AgentReplyNextPhase, ConversationReply, ConversationRequest, Phase, Verdict, validate_reviewed_proposal_identity
from .provider_contract_guidance import diagnostic_to_wire
from .provider_message_catalog import WireShapeError
from .provider_response_mapping import normalize_direct_result, normalize_retrieval_action
from .structured_output_validation import carry_validation, remap_validation, validation_failure
from .validation_findings import prefix_validation


class PhaseReplyConstraintError(BackendProtocolError):
    """Closed residual phase constraints unsupported by the common schema subset."""

    _ISSUES = {
        "message": "$.message: expected nonempty text",
        "digest": "$.reviewed_proposal_digest: expected sha256: followed by exactly 64 lowercase hexadecimal digits",
        "identity": "$.reviewed_proposal_id: expected a canonical reviewed proposal identifier",
        "next": "$.next: next step must match the runtime-offered writing intent",
        "expected_identity": "$.reviewed_proposal_id: ID and digest must match the exact runtime-offered reviewed proposal",
    }

    def __init__(self, rule: str) -> None:
        self.issue = diagnostic_to_wire(self._ISSUES[rule])
        super().__init__("phase reply violates a closed result constraint")


def decode_conversation_retrieval_action(
    text: str,
    *,
    request: ConversationRetrievalRequest,
    action_index: int,
    provider_name: str = "Codex",
    allowed_actions: tuple[ConversationRetrievalActionKind, ...] | None = None,
) -> ConversationRetrievalAction:
    """Decode one exact action envelope while leaving semantics to the handler.

        Duplicate keys, malformed JSON, primitives, unknown kinds, missing or
        extra fields, and invalid primitive values raise
        ``BackendProtocolError``. A well-shaped reversed read range or duplicate
        answer IDs is a semantic failure. No external state is read or changed.
        """

    def reject_duplicate_fields(
        pairs: list[tuple[str, Any]],
    ) -> dict[str, Any]:
        """Reject duplicate JSON object keys at every nesting depth."""

        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise validation_failure(
                    BackendProtocolError(
                        f'{provider_name} conversation retrieval response duplicates a field',
                    ),
                    '$.action',
                    f'{provider_name} conversation retrieval response duplicates a field',
                )
            result[key] = value
        return result

    if type(text) is not str:
        raise validation_failure(
            BackendProtocolError(f'{provider_name} conversation retrieval response must be text'),
            '$.action',
            f'{provider_name} conversation retrieval response must be text',
        )
    try:
        value = json.loads(text, object_pairs_hook=reject_duplicate_fields)
    except BackendProtocolError:
        raise
    except (
        json.JSONDecodeError,
        ValueError,
        KeyError,
        RecursionError,
        TypeError,
        UnicodeError,
    ) as exc:
        raise validation_failure(
            BackendProtocolError(
                f'{provider_name} conversation retrieval response is malformed JSON',
            ),
            '$.action',
            f'{provider_name} conversation retrieval response is malformed JSON',
        ) from exc
    try:
        value = normalize_retrieval_action(value)
    except WireShapeError as exc:
        raise validation_failure(
            BackendProtocolError("conversation retrieval response violates the current action contract"),
            exc.wire_path, exc.wire_requirement,
        ) from exc
    if type(value) is not dict:
        raise validation_failure(
            BackendProtocolError(
                f'{provider_name} conversation retrieval response must be a JSON object',
            ),
            '$.action',
            f'{provider_name} conversation retrieval response must be a JSON object',
        )
    if set(value) != {"action"}:
        raise validation_failure(
            BackendProtocolError(
                f'{provider_name} conversation retrieval response has an invalid schema',
            ),
            '$.action',
            (
                'expected action with fields matching kind: search_conversations (query), '
                'read_history (citation_id, start_sequence, optional end_sequence), answer '
                '(message, citation_ids)'
            ),
        )
    value = value["action"]
    if type(value) is not dict:
        raise validation_failure(
            BackendProtocolError(
                f'{provider_name} conversation retrieval action must be a JSON object',
            ),
            '$.action',
            f'{provider_name} conversation retrieval action must be a JSON object',
        )
    kind_value = value.get("kind")
    if type(kind_value) is not str:
        raise validation_failure(
            BackendProtocolError(f'{provider_name} conversation retrieval action kind must be text'),
            '$.action.kind',
            f'{provider_name} conversation retrieval action kind must be text',
        )
    try:
        kind = ConversationRetrievalActionKind(kind_value)
    except ValueError as exc:
        raise validation_failure(
            BackendProtocolError(f'{provider_name} conversation retrieval action kind is unknown'),
            '$.action.kind',
            'expected one of ' + ', '.join((item.value for item in ConversationRetrievalActionKind)),
        ) from exc
    if allowed_actions is not None and kind not in allowed_actions:
        raise validation_failure(
            BackendProtocolError(f'{provider_name} conversation retrieval action is not allowed'),
            '$.action.kind',
            'expected one of ' + ', '.join((item.value for item in allowed_actions)),
        )

    common_fields = {"kind"}
    action_fields = {
        ConversationRetrievalActionKind.SEARCH_CONVERSATIONS: {"query"},
        ConversationRetrievalActionKind.READ_HISTORY: {
            "citation_id",
            "start_sequence",
        },
        ConversationRetrievalActionKind.ANSWER: {
            "message",
            "citation_ids",
        },
    }[kind]
    valid_fields = common_fields | action_fields
    if kind is ConversationRetrievalActionKind.READ_HISTORY:
        valid = set(value) in {frozenset(valid_fields), frozenset((*valid_fields, "end_sequence"))}
    else:
        valid = set(value) == valid_fields
    if not valid:
        raise validation_failure(
            BackendProtocolError(
                f'{provider_name} conversation retrieval response has an invalid schema',
            ),
            '$.action',
            (
                'expected action with fields matching kind: search_conversations (query), '
                'read_history (citation_id, start_sequence, optional end_sequence), answer '
                '(message, citation_ids)'
            ),
        )

    def required_text(field: str) -> str:
        """Return one nonempty strict-UTF-8 action string field."""

        result = value[field]
        if type(result) is not str or not result:
            raise validation_failure(
                BackendProtocolError(
                    f'{provider_name} conversation retrieval {field} must be non-empty text',
                ),
                f'$.action.{field}',
                f'{provider_name} conversation retrieval {field} must be non-empty text',
            )
        try:
            result.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            raise validation_failure(
                BackendProtocolError(
                    f'{provider_name} conversation retrieval {field} must be valid UTF-8',
                ),
                f'$.action.{field}',
                f'{provider_name} conversation retrieval {field} must be valid UTF-8',
            ) from exc
        return result

    def positive_integer(field: str) -> int:
        """Return one positive built-in action integer field."""

        result = value[field]
        if type(result) is not int or result < 1:
            raise validation_failure(
                BackendProtocolError(
                    f'{provider_name} conversation retrieval {field} must be positive integer',
                ),
                f'$.action.{field}',
                f'{provider_name} conversation retrieval {field} must be positive integer',
            )
        return result

    common = {
        "kind": kind,
        "session_id": request.session_id,
        "conversation_id": request.conversation_id,
        "turn_id": request.turn_id,
        "action_index": action_index,
    }
    try:
        if kind is ConversationRetrievalActionKind.SEARCH_CONVERSATIONS:
            return ConversationRetrievalSearchAction(
                **common,
                query=required_text("query"),
            )
        if kind is ConversationRetrievalActionKind.READ_HISTORY:
            start_sequence = positive_integer("start_sequence")
            end_sequence = (
                positive_integer("end_sequence")
                if "end_sequence" in value else None
            )
            if end_sequence is not None and end_sequence < start_sequence:
                raise validation_failure(
                    ConversationRetrievalSemanticError(),
                    '$.action.end_sequence',
                    'end_sequence must be greater than or equal to start_sequence',
                )
            return ConversationRetrievalReadAction(
                **common,
                citation_id=required_text("citation_id"),
                start_sequence=start_sequence,
                end_sequence=end_sequence,
            )
        raw_citation_ids = value["citation_ids"]
        if type(raw_citation_ids) is not list:
            raise validation_failure(
                BackendProtocolError(
                    f'{provider_name} conversation retrieval citation_ids must be an array of non-empty text',
                ),
                '$.action.citation_ids',
                f'{provider_name} conversation retrieval citation_ids must be an array of non-empty text',
            )
        for citation_index, item in enumerate(raw_citation_ids):
            if type(item) is not str or not item:
                raise validation_failure(
                    BackendProtocolError(
                        f'{provider_name} conversation retrieval citation_ids must be an array of non-empty text',
                    ),
                    f'$.action.citation_ids[{citation_index}]',
                    f'{provider_name} conversation retrieval citation_ids must be an array of non-empty text',
                )
        for citation_index, item in enumerate(raw_citation_ids):
            try:
                item.encode("utf-8", errors="strict")
            except UnicodeEncodeError as exc:
                raise validation_failure(
                    BackendProtocolError(
                        f'{provider_name} conversation retrieval citation_ids must be valid UTF-8',
                    ),
                    f'$.action.citation_ids[{citation_index}]',
                    f'{provider_name} conversation retrieval citation_ids must be valid UTF-8',
                ) from exc
        if len(set(raw_citation_ids)) != len(raw_citation_ids):
            raise validation_failure(
                ConversationRetrievalSemanticError(),
                '$.action.citation_ids',
                'citation_ids must be unique',
            )
        return ConversationRetrievalAnswerAction(
            **common,
            content=required_text("message"),
            citation_ids=tuple(raw_citation_ids),
        )
    except ConversationRetrievalSemanticError:
        raise
    except (SessionIdError, TypeError, ValueError) as exc:
        raise carry_validation(
            validation_failure(
                BackendProtocolError(
                    f'{provider_name} conversation retrieval response has invalid values',
                ),
                '$.action',
                (
                    'retrieval action values must match the current session, conversation, turn, '
                    'and positive action index'
                ),
            ),
            prefix_validation(exc, '$.action'),
        ) from exc


def decode_conversation_reply(
    request: ConversationRequest,
    text: str,
    *,
    provider_name: str = "Codex",
) -> ConversationReply:
    """Decode one exact correlated reply with fixed provider-safe errors."""

    def reject_duplicate_fields(
        pairs: list[tuple[str, Any]],
    ) -> dict[str, Any]:
        """Build a reply object or reject any duplicate JSON field."""

        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise validation_failure(
                    BackendProtocolError(
                        f'{provider_name} conversation response duplicates a field',
                    ),
                    '$',
                    f'{provider_name} conversation response duplicates a field',
                )
            result[key] = value
        return result

    value = json.loads(text, object_pairs_hook=reject_duplicate_fields)
    try:
        value = normalize_direct_result(value, "conversation")
    except (ValueError, TypeError, KeyError) as failure:
        raise validation_failure(BackendProtocolError("response does not match the current wire contract"), "$", "expected exact current response_type and schema fields") from None
    expected_fields = {"message"}
    if not isinstance(value, dict):
        raise validation_failure(
            BackendProtocolError(f'{provider_name} conversation response must be a JSON object'),
            '$',
            f'{provider_name} conversation response must be a JSON object',
        )
    if set(value) != expected_fields:
        raise validation_failure(
            BackendProtocolError(f'{provider_name} conversation response has an invalid schema'),
            '$',
            'expected an object containing exactly message',
        )
    content = _strict_required_text(
        value,
        "message",
        provider_name=provider_name,
    )
    return ConversationReply(
        session_id=request.session_id,
        conversation_id=request.conversation_id,
        turn_id=request.turn_id,
        content=content,
    )


def decode_reply(
    phase: Phase,
    text: str,
    *,
    revision_id: str,
    provider_name: str = "Codex",
) -> AgentReply:
    """Decode one semantic phase reply and restore runtime-owned identity."""

    def reject_duplicate_fields(
        pairs: list[tuple[str, Any]],
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise validation_failure(
                    BackendProtocolError(f'{provider_name} agent response duplicates a field'),
                    '$',
                    f'{provider_name} agent response duplicates a field',
                )
            result[key] = item
        return result

    value = json.loads(text, object_pairs_hook=reject_duplicate_fields)
    try:
        value = normalize_direct_result(value, phase.value)
    except (ValueError, TypeError, KeyError) as failure:
        raise validation_failure(BackendProtocolError("response does not match the current wire contract"), "$", "expected exact current response_type and schema fields") from None
    if not isinstance(value, dict):
        raise validation_failure(
            BackendProtocolError(f'{provider_name} final response must be a JSON object'),
            '$',
            f'{provider_name} final response must be a JSON object',
        )
    if type(value.get("message")) is str and not value["message"] and not (
        phase is Phase.AUDIT and value.get("verdict") == "approve"
    ):
        raise PhaseReplyConstraintError("message")
    if phase is Phase.AUDIT:
        if set(value) != {"verdict", "message"}:
            raise validation_failure(
                BackendProtocolError(f'{provider_name} audit response has an invalid schema'),
                '$',
                'expected an object containing exactly verdict and message',
            )
        content = _text(
            value,
            "message",
            provider_name=provider_name,
        )
        verdict_value = value.get("verdict")
        if verdict_value not in {"approve", "conflict"}:
            raise validation_failure(
                BackendProtocolError(f'{provider_name} audit verdict is invalid'),
                '$.verdict',
                'expected verdict approve or conflict',
            )
        verdict = Verdict(verdict_value)
        return _validated_phase_reply(AgentReply, kind=AgentReplyKind.AUDIT, revision_id=revision_id, content=content, verdict=verdict)
    if phase is Phase.FINALIZE:
        if set(value) != {
            "reviewed_proposal_id",
            "reviewed_proposal_digest",
        }:
            raise validation_failure(
                BackendProtocolError(f'{provider_name} finalization response has an invalid schema'),
                '$',
                (
                    'expected an object containing exactly reviewed_proposal_id and '
                    'reviewed_proposal_digest'
                ),
            )
        try:
            validate_reviewed_proposal_identity(("proposal", value.get("reviewed_proposal_digest")))
        except (TypeError, ValueError):
            raise PhaseReplyConstraintError("digest") from None
        try:
            validate_reviewed_proposal_identity((value.get("reviewed_proposal_id"), value["reviewed_proposal_digest"]))
        except (TypeError, ValueError):
            raise PhaseReplyConstraintError("identity") from None
        return _validated_phase_reply(
            AgentReply.finalization,
            revision_id,
            _required_text(
                value,
                "reviewed_proposal_id",
                provider_name=provider_name,
            ),
            _required_text(
                value,
                "reviewed_proposal_digest",
                provider_name=provider_name,
            ),
        )
    if phase is Phase.SYNTHESIZE:
        expected_fields = {"next", "message"}
        if set(value) != expected_fields:
            raise validation_failure(
                BackendProtocolError(f'{provider_name} synthesis response has an invalid schema'),
                '$',
                'expected an object containing exactly next and message',
            )
        content = _required_text(
            value,
            "message",
            provider_name=provider_name,
        )
        next_phase = value.get("next")
        if next_phase == AgentReplyKind.DEADLOCK.value:
            return _validated_phase_reply(AgentReply.deadlock, revision_id, content)
        if next_phase not in {
                AgentReplyNextPhase.REPAIR.value,
                AgentReplyNextPhase.FINALIZATION.value,
                AgentReplyNextPhase.REVIEW_COMPLETION.value,
            }:
            raise validation_failure(
                BackendProtocolError(f'{provider_name} synthesis response is ambiguous'),
                '$.next',
                'expected repair, finalization, review_completion, or deadlock',
            )
        return _validated_phase_reply(AgentReply.synthesis, revision_id, content, AgentReplyNextPhase(next_phase))
    if set(value) != {"message"}:
        raise validation_failure(
            BackendProtocolError(f'{provider_name} candidate response has an invalid schema'),
            '$',
            'expected an object containing exactly message',
        )
    return _validated_phase_reply(AgentReply.candidate, revision_id, _required_text(value, 'message', provider_name=provider_name))


def _required_text(
    value: Mapping[str, Any],
    field: str,
    *,
    provider_name: str = "Codex",
) -> str:
    result = _text(value, field, provider_name=provider_name)
    if not result:
        raise validation_failure(
            BackendProtocolError(f'{provider_name} {field} must be non-empty text'),
            f'$.{field}',
            'expected nonempty text',
        )
    return result


def _validated_phase_reply(factory: Callable[..., AgentReply], *args: object, **kwargs: object) -> AgentReply:
    """Keep model findings while restoring the phase response's wire names."""
    try:
        return factory(*args, **kwargs)
    except (TypeError, ValueError) as failure:
        raise remap_validation(failure, {"$.content": "$.response_text", "$.next_phase": "$.next_consensus_step", "$.verdict": "$.candidate_review_status"})


def _text(
    value: Mapping[str, Any],
    field: str,
    *,
    provider_name: str = "Codex",
) -> str:
    result = value.get(field)
    if not isinstance(result, str):
        raise validation_failure(
            BackendProtocolError(f'{provider_name} {field} must be text'),
            f'$.{field}',
            'expected text',
        )
    return result


def _strict_required_text(
    value: Mapping[str, Any],
    field: str,
    *,
    provider_name: str = "Codex",
) -> str:
    """Return one nonempty strict-UTF-8 conversation response field.

    Missing, non-string, empty, or unencodable values raise
    ``BackendProtocolError`` without exposing the value.
    """

    result = value.get(field)
    if not isinstance(result, str):
        raise validation_failure(
            BackendProtocolError(f'{provider_name} conversation {field} must be text'),
            f'$.{field}',
            'expected text',
        )
    if not result:
        raise validation_failure(
            BackendProtocolError(f'{provider_name} conversation {field} must be non-empty text'),
            f'$.{field}',
            'expected nonempty text',
        )
    try:
        result.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise validation_failure(
            BackendProtocolError(f'{provider_name} conversation {field} must be valid UTF-8'),
            f'$.{field}',
            'expected strict UTF-8 text',
        ) from exc
    return result


__all__ = (
    "decode_conversation_retrieval_action",
    "decode_conversation_reply",
    "decode_reply",
    "PhaseReplyConstraintError",
)
