"""Pure result-schema value validation."""

from __future__ import annotations

import math
from ...conversations.session_context import MAX_SESSION_CONTEXT_UTF8_BYTES
from ...structured_output_validation import (
    canonical_schema_rules,
    carry_validation,
    diagnose_value,
    schema_accepts_only_objects,
    strict_json_object,
    validation_failure,
)
from .errors import (
    ContextExecutionErrorCode,
    execution_error,
)
from collections.abc import Mapping


_PROVIDER_SCHEMA_TYPES = frozenset(
    {"array", "boolean", "integer", "null", "number", "object", "string"}
)


_PROVIDER_SCHEMA_KEYS = frozenset(
    {
        "additionalProperties",
        "anyOf",
        "description",
        "enum",
        "items",
        "maxItems",
        "minItems",
        "properties",
        "required",
        "type",
    }
)


def _schema_type_matches(value: object, schema_type: str) -> bool:
    if schema_type == "null":
        return value is None
    if schema_type == "boolean":
        return type(value) is bool
    if schema_type == "integer":
        return type(value) is int
    if schema_type == "number":
        return type(value) is int or (type(value) is float and math.isfinite(value))
    if schema_type == "string":
        return type(value) is str
    if schema_type == "array":
        return type(value) is list
    if schema_type == "object":
        return type(value) is dict
    return False


def _schema_types(schema: dict[str, object]) -> tuple[str, ...]:
    raw = schema.get("type")
    if type(raw) is str:
        values = (raw,)
    elif type(raw) is list and all(type(item) is str for item in raw):
        values = tuple(raw)
    else:
        raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
    if (
        not values
        or len(set(values)) != len(values)
        or any(value not in _PROVIDER_SCHEMA_TYPES for value in values)
        or (
            len(values) > 1
            and (len(values) != 2 or "null" not in values)
        )
    ):
        raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
    return values


def validate_provider_schema_node(schema: object) -> None:
    if not isinstance(schema, Mapping):
        raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
    try:
        canonical_schema_rules(schema, maximum_bytes=MAX_SESSION_CONTEXT_UTF8_BYTES)
    except (TypeError, ValueError, UnicodeError) as failure:
        raise carry_validation(execution_error(ContextExecutionErrorCode.INVALID_VALUE), failure) from None


def _schema_accepts_only_objects(schema: dict[str, object]) -> bool:
    return schema_accepts_only_objects(schema)


def validated_result_schema_bytes(schema: Mapping[str, object]) -> bytes:
    try:
        encoded = canonical_schema_rules(schema)
        value = strict_json_object(encoded.decode("utf-8"))
        def require_choice_descriptions(node: dict[str, object]) -> None:
            for branch in node.get("anyOf", ()):
                description = branch.get("description")
                if type(description) is not str or not description.strip():
                    raise validation_failure(
                        ValueError(), "$", "expected nonempty owner-authored choice description",
                    )
                require_choice_descriptions(branch)
            for keyword in ("properties", "$defs"):
                for child in node.get(keyword, {}).values():
                    require_choice_descriptions(child)
            if "items" in node:
                require_choice_descriptions(node["items"])

        require_choice_descriptions(value)
        if not schema_accepts_only_objects(value):
            raise ValueError("result object required")
        return encoded
    except (TypeError, ValueError, UnicodeError) as failure:
        raise carry_validation(execution_error(ContextExecutionErrorCode.INVALID_VALUE), failure) from None


def _value_matches_provider_schema(value: object, schema: dict[str, object]) -> bool:
    return not diagnose_value(value, schema)


__all__ = [
    'validate_provider_schema_node',
    'validated_result_schema_bytes',
]
