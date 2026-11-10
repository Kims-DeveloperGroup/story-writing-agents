"""Context action JSON decoding and content-free diagnostic projection."""

from __future__ import annotations

import json
from ...provider_wire import (
    WireShapeError,
    diagnostic_to_wire,
    normalize_context_action,
    normalize_result,
)
from ...structured_output_validation import (
    bounded_chunks,
    carry_validation,
    encoding_failure,
    failure_issues,
    remap_validation,
    strict_json_object,
    validation_call,
    validation_failure,
)
from ..actions import (
    ContextActionCorrelation,
    ContextActionError,
    ContextActionErrorCode,
    ContextAnswerAction,
    ContextReadAction,
    ContextSearchAction,
    ContextSearchQuery,
    ContextSourceHandle,
)
from ..contracts.bound_call import BoundCallDecodedAction
from ..contracts.call_offers import ContextJsonObjectResultContract
from ..contracts.context_values import (
    SEARCH_SOURCE_KIND_ORDER,
    canonical_bytes,
)
from ..contracts.errors import (
    ContextExecutionError,
    ContextExecutionErrorCode,
    ContextExecutionErrorDetail,
    execution_error,
    execution_error_has_detail,
)
from ..manifest import (
    ContextManifestError,
    ContextSourceKind,
)
from collections.abc import Callable


class DuplicateJsonKey(ValueError):
    pass


def unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateJsonKey
        result[key] = value
    return result


def _exact_object(
    value: object,
    fields: frozenset[str],
) -> dict[str, object]:
    if type(value) is not dict or set(value) != fields:
        raise validation_failure(
            execution_error(ContextExecutionErrorCode.MALFORMED_ACTION), "$",
            "expected an object containing exactly " + ", ".join(sorted(fields)),
        )
    assert isinstance(value, dict)
    return value


def _answer_text_for_contract(
    value: object,
    contract: ContextJsonObjectResultContract,
    decode_result: Callable[[object], dict[str, object]] | None = None,
) -> str:
    """Validate one owner-supplied result schema and canonicalize its object."""

    if type(contract) is not ContextJsonObjectResultContract:
        raise validation_failure(
            ValueError(),
            '$',
            'expected a JSON object matching the current result contract',
        )
    if not contract.wire_format:
        try:
            value = contract.rule_graph.normalized_value(value)
        except (TypeError, ValueError, RecursionError):
            raise validation_failure(
                ValueError(),
                "$",
                "expected a JSON object matching the current result contract",
            ) from None
    try:
        if contract.wire_format:
            if decode_result is None:
                raise ValueError("current wire result requires a complete binding")
            domain_value = decode_result(value)
        else:
            domain_value = normalize_result(value, contract.contract_id)
        return canonical_bytes(domain_value).decode("utf-8", errors="strict")
    except ContextActionError as failure:
        raise encoding_failure(failure, value, contract.schema_value())


def decode_context_action(
    response_text: str,
    *,
    correlation: ContextActionCorrelation,
    allowed_actions: tuple[str, ...],
    result_contract: ContextJsonObjectResultContract,
    allowed_search_source_kinds: tuple[ContextSourceKind, ...] = SEARCH_SOURCE_KIND_ORDER,
    resolve_handle: Callable[[object], ContextSourceHandle] = ContextSourceHandle,
    completed_handles: tuple[ContextSourceHandle, ...] = (),
    issued_handle_aliases: tuple[str, ...] = (),
    read_limit: int = 8,
    decoded_action: BoundCallDecodedAction | None = None,
) -> ContextSearchAction | ContextReadAction | ContextAnswerAction:
    """Strictly decode typed wire v7 and retain the actual rejected property."""
    try:
        raw = _exact_object(decoded_action.domain_value() if decoded_action is not None
                            else normalize_context_action(strict_json_object(response_text)),
                            frozenset({"action"}))
        action = raw["action"]
        if type(action) is not dict or type(action.get("kind")) is not str:
            raise validation_failure(ValueError(), "$.kind", "expected one of search, read, answer")
        action_kind = action["kind"]
        if action_kind not in {"answer", "read", "search"}:
            raise validation_failure(ValueError(), "$.kind", "expected one of search, read, answer")
        if action_kind == "search":
            if set(action) not in ({"kind", "queries"}, {"kind", "path_prefix", "queries"}):
                raise validation_failure(
                    ValueError(),
                    '$',
                    'search requires kind and queries, with only optional path_prefix',
                )
            path_prefix = action.get("path_prefix")
            query_values = action["queries"]
            if action_kind not in allowed_actions:
                raise validation_failure(
                    ValueError(),
                    '$.kind',
                    'expected an action from ' + ', '.join(allowed_actions),
                )
            if path_prefix is not None and type(path_prefix) is not str:
                raise validation_failure(
                    ValueError(),
                    '$.path_prefix',
                    'expected text or null',
                    value=path_prefix,
                )
            if type(query_values) is not list or not query_values:
                raise validation_failure(
                    ValueError(),
                    '$.queries',
                    'expected a nonempty array',
                    value=query_values,
                )
            queries: list[ContextSearchQuery] = []
            for query_index, query in enumerate(query_values):
                query_path = f"$.queries[{query_index}]"
                query_object = validation_call(query_path, _exact_object, query, frozenset({'source_kinds', 'text'}))
                kinds = query_object["source_kinds"]
                if type(query_object["text"]) is not str:
                    raise validation_failure(
                        ValueError(),
                        query_path + '.text',
                        'expected text',
                        value=query_object['text'],
                    )
                if type(kinds) is not list:
                    raise validation_failure(
                        ValueError(),
                        query_path + '.source_kinds',
                        'expected an array',
                        value=kinds,
                    )
                decoded_kinds = tuple(
                    validation_call(f"{query_path}.source_kinds[{index}]", ContextSourceKind, item)
                    for index, item in enumerate(kinds)
                )
                if not decoded_kinds:
                    raise validation_failure(
                        ValueError(),
                        query_path + '.source_kinds',
                        'expected at least one source kind',
                    )
                if len(set(decoded_kinds)) != len(decoded_kinds):
                    raise validation_failure(
                        ValueError(),
                        query_path + '.source_kinds',
                        'source kinds must be unique',
                    )
                if any(kind not in allowed_search_source_kinds for kind in decoded_kinds):
                    raise validation_failure(
                        ValueError(),
                        query_path + '.source_kinds',
                        'expected source kinds from ' + ', '.join((kind.value for kind in allowed_search_source_kinds)),
                    )
                canonical_kinds = tuple(kind for kind in SEARCH_SOURCE_KIND_ORDER if kind in decoded_kinds)
                try:
                    queries.append(validation_call(
                        query_path,
                        ContextSearchQuery,
                        query_object['text'],
                        canonical_kinds,
                        path_prefix,
                    ))
                except ContextActionError as failure:
                    raise remap_validation(failure, {query_path + ".path_prefix": "$.path_prefix"})
            return ContextSearchAction(correlation, tuple(queries))
        if action_kind == "read":
            read = _exact_object(action, frozenset({"handles", "kind"}))
            handles_value = read["handles"]
            if type(handles_value) is not list or not handles_value:
                raise execution_error(
                    ContextExecutionErrorCode.MALFORMED_ACTION,
                    detail=ContextExecutionErrorDetail.READ_SOURCE_HANDLES_REQUIRED,
                )
            if action_kind not in allowed_actions:
                raise validation_failure(
                    ValueError(),
                    '$.kind',
                    'expected an action from ' + ', '.join(allowed_actions),
                )
            handles: list[ContextSourceHandle] = []
            for index, item in enumerate(handles_value):
                try:
                    handles.append(resolve_handle(item))
                except ContextActionError as failure:
                    if failure.code in {
                        ContextActionErrorCode.HANDLE_INVALID,
                        ContextActionErrorCode.INVALID_VALUE,
                    }:
                        raise validation_failure(
                            execution_error(
                                ContextExecutionErrorCode.MALFORMED_ACTION,
                                detail=ContextExecutionErrorDetail.READ_SOURCE_HANDLES_REQUIRED,
                            ),
                            f"$.handles[{index}]",
                            "expected a source handle returned by a preceding search",
                        ) from None
                    raise
            return ContextReadAction(correlation, tuple(handles))

        answer = _exact_object(action, frozenset({"kind", "result"}))
        if action_kind not in allowed_actions:
            raise validation_failure(
                ValueError(),
                '$.kind',
                'expected an action from ' + ', '.join(allowed_actions),
            )
        return ContextAnswerAction(
            correlation,
            (decoded_action.answer_json if decoded_action is not None else
             validation_call("$.result", _answer_text_for_contract, answer["result"], result_contract)),
            completed_handles,
        )
    except WireShapeError as failure:
        raise carry_validation(execution_error(ContextExecutionErrorCode.MALFORMED_ACTION), validation_failure(failure, failure.wire_path, failure.wire_requirement)) from None
    except ContextExecutionError:
        raise
    except (ContextActionError, ContextManifestError, DuplicateJsonKey, json.JSONDecodeError, TypeError, ValueError) as failure:
        rejected = carry_validation(execution_error(ContextExecutionErrorCode.MALFORMED_ACTION), failure)
        if decoded_action is not None:
            rejected.bound_contract_failure = failure
        raise rejected from None


def context_validation_details(
    response_text: str,
    schema_issues: tuple[str, ...],
    failure: ContextExecutionError,
) -> tuple[str, ...]:
    """Retain the actual schema/domain rejection; never revalidate heuristically."""
    issues = schema_issues or failure_issues(failure, path="$.action")
    if not issues and execution_error_has_detail(
        failure, ContextExecutionErrorDetail.READ_SOURCE_HANDLES_REQUIRED,
    ):
        issues = ("$.action.handles: expected unique handles returned by a preceding search",)
    if not issues and execution_error_has_detail(
        failure, ContextExecutionErrorDetail.RESPONSE_CORRELATION_MISMATCH,
    ):
        issues = ("$.action: response correlation must match the current request",)
    return bounded_chunks(tuple(diagnostic_to_wire(item, result_fields=False) for item in (issues or ("$.agent_action: action decoder failed without a property diagnostic",))))


__all__ = [
    'DuplicateJsonKey',
    'unique_json_object',
    'decode_context_action',
    'context_validation_details',
]
