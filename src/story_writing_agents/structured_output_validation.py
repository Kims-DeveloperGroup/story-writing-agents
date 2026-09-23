"""Content-free diagnostics for the closed provider JSON schema subset.

Only paths and constraints from the trusted schema enter a diagnostic. Provider
values, unknown member names, and parser exception messages never do.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from enum import StrEnum


_PATH_MEMBER = re.compile(r"^[A-Za-z_][A-Za-z_0-9]*$")
_MAX_CHUNK_BYTES = 32_000


class DomainValidationRule(StrEnum):
    """Closed domain findings that can cross the context service boundary."""

    MALFORMED_RESULT = "malformed_result"
    RESULT_OBJECT_REQUIRED = "result_object_required"
    RECOMMENDATION_REQUIRED = "recommendation_required"
    RECOMMENDATION_ONLY = "recommendation_only"
    OBJECTIVE_RECOMMENDATION_CONFLICT = "objective_recommendation_conflict"
    PROPOSAL_KIND_REQUIRED = "proposal_kind_required"
    LEAD_OUTCOME_REJECTED = "lead_outcome_rejected"


_DOMAIN_RULE_ISSUES = {
    DomainValidationRule.MALFORMED_RESULT: "$.action.result: malformed JSON",
    DomainValidationRule.RESULT_OBJECT_REQUIRED: "$.action.result: expected object",
    DomainValidationRule.RECOMMENDATION_REQUIRED: (
        "$.action.result.recommended_action: required for a recommendation"
    ),
    DomainValidationRule.RECOMMENDATION_ONLY: (
        "$.action.result.recommended_action: allowed only for a recommendation"
    ),
    DomainValidationRule.OBJECTIVE_RECOMMENDATION_CONFLICT: (
        "$.action.result.objective_intent: cannot accompany recommended_action"
    ),
    DomainValidationRule.PROPOSAL_KIND_REQUIRED: (
        "$.action.result.proposal: proposal intent requires proposal kind"
    ),
    DomainValidationRule.LEAD_OUTCOME_REJECTED: (
        "$.action.result: rejected by a lead outcome rule"
    ),
}


def domain_rule_chunks(rules: object) -> tuple[str, ...]:
    """Render only known fixed domain rules; discard untrusted callback text."""

    if type(rules) is not tuple or not rules or any(
        type(rule) is not DomainValidationRule for rule in rules
    ):
        return bounded_chunks(("$.action.result: rejected by a domain result rule",))
    return bounded_chunks(tuple(_DOMAIN_RULE_ISSUES[rule] for rule in rules))


class _DuplicateKey(ValueError):
    """A JSON object repeated a member name."""


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey
        result[key] = value
    return result


def _reject_constant(_value: str) -> object:
    raise ValueError


def _path(parent: str, member: str) -> str:
    if _PATH_MEMBER.fullmatch(member):
        return f"{parent}.{member}"
    return f"{parent}[{json.dumps(member, ensure_ascii=True)}]"


def json_type(value: object) -> str:
    """Return the JSON type name, without rendering the supplied value."""

    if value is None:
        return "null"
    if type(value) is bool:
        return "boolean"
    if type(value) is int:
        return "integer"
    if type(value) is float:
        return "number"
    if type(value) is str:
        return "string"
    if type(value) is list:
        return "array"
    if type(value) is dict:
        return "object"
    return "non-JSON value"


def _matches_type(value: object, expected: str) -> bool:
    actual = json_type(value)
    return actual == expected or (expected == "number" and actual == "integer")


def _issue(path: str, problem: str) -> str:
    return f"{path}: {problem}"


def _diagnose(value: object, schema: Mapping[str, object], path: str) -> list[str]:
    variants = schema.get("anyOf")
    if type(variants) is list and variants:
        if type(value) is dict:
            for discriminator in ("kind", "action", "decision", "operation"):
                supplied = value.get(discriminator)
                if type(supplied) is not str:
                    continue
                candidates = [
                    variant["properties"][discriminator]
                    for variant in variants
                    if isinstance(variant, Mapping)
                    and isinstance(variant.get("properties"), Mapping)
                    and isinstance(variant["properties"].get(discriminator), Mapping)
                    and type(variant["properties"][discriminator].get("enum")) is list
                ]
                matches = [
                    variant for variant in variants
                    if isinstance(variant, Mapping)
                    and isinstance(variant.get("properties"), Mapping)
                    and isinstance(variant["properties"].get(discriminator), Mapping)
                    and supplied in variant["properties"][discriminator].get("enum", ())
                ]
                if len(matches) == 1:
                    return _diagnose(value, matches[0], path)
                if candidates and not matches:
                    issues = [_issue(
                        _path(path, discriminator),
                        f"expected an allowed string value, got {json_type(supplied)}",
                    )]
                    properties = [
                        variant.get("properties")
                        for variant in variants
                        if isinstance(variant, Mapping)
                        and isinstance(variant.get("properties"), Mapping)
                    ]
                    if len(properties) == len(variants):
                        common = set.intersection(*(set(item) for item in properties))
                        for name in sorted(common):
                            if name == discriminator:
                                continue
                            child = properties[0][name]
                            if any(item[name] != child for item in properties[1:]):
                                continue
                            required_everywhere = all(
                                name in variant.get("required", ())
                                for variant in variants
                            )
                            if name not in value and required_everywhere:
                                issues.append(_issue(
                                    _path(path, name), "required property is missing",
                                ))
                            elif name in value and isinstance(child, Mapping):
                                issues.extend(_diagnose(
                                    value[name], child, _path(path, name),
                                ))
                    return issues
        compatible_variants = [
            variant for variant in variants
            if isinstance(variant, Mapping) and (
                "type" not in variant
                or (
                    type(variant["type"]) is str
                    and _matches_type(value, variant["type"])
                )
                or (
                    type(variant["type"]) is list
                    and any(
                        type(item) is str and _matches_type(value, item)
                        for item in variant["type"]
                    )
                )
            )
        ]
        alternatives = [
            _diagnose(value, variant, path)
            for variant in (compatible_variants or variants)
            if isinstance(variant, Mapping)
        ]
        if alternatives:
            fewest = min(map(len, alternatives))
            closest = [item for item in alternatives if len(item) == fewest]
            if len(closest) == 1:
                return closest[0]
            common = set(closest[0]).intersection(*map(set, closest[1:]))
            if common:
                return [item for item in closest[0] if item in common]
            return [_issue(path, "does not match any allowed schema variant")]
        return [_issue(path, "does not match the required schema")]

    raw_type = schema.get("type")
    expected = (
        (raw_type,) if type(raw_type) is str else
        tuple(item for item in raw_type if type(item) is str)
        if type(raw_type) is list else ()
    )
    if expected and not any(_matches_type(value, item) for item in expected):
        return [_issue(path, f"expected {' or '.join(expected)}, got {json_type(value)}")]
    if type(value) is float and not math.isfinite(value):
        return [_issue(path, "expected a finite number, got number")]

    issues: list[str] = []
    allowed = schema.get("enum")
    if type(allowed) is list and not any(
        type(value) is type(item) and value == item for item in allowed
    ):
        enum_types = tuple(dict.fromkeys(json_type(item) for item in allowed))
        expected_type = " or ".join(enum_types) if enum_types else "JSON"
        issues.append(_issue(
            path, f"expected an allowed {expected_type} value, got {json_type(value)}",
        ))
    if type(value) is dict:
        properties = schema.get("properties")
        if isinstance(properties, Mapping):
            required = schema.get("required")
            names = (
                tuple(item for item in required if type(item) is str)
                if type(required) is list else tuple(properties)
            )
            for name in names:
                if name not in value:
                    issues.append(_issue(_path(path, name), "required property is missing"))
            if schema.get("additionalProperties") is False:
                unknown_count = len(value.keys() - properties.keys())
                if unknown_count:
                    issues.append(_issue(path, f"contains {unknown_count} unexpected properties (names withheld)"))
            for name, child in properties.items():
                if name in value and type(name) is str and isinstance(child, Mapping):
                    issues.extend(_diagnose(value[name], child, _path(path, name)))
    elif type(value) is list:
        minimum = schema.get("minItems")
        maximum = schema.get("maxItems")
        if type(minimum) is int and len(value) < minimum:
            issues.append(_issue(path, f"expected at least {minimum} items, got {len(value)}"))
        if type(maximum) is int and len(value) > maximum:
            issues.append(_issue(path, f"expected at most {maximum} items, got {len(value)}"))
        item_schema = schema.get("items")
        if isinstance(item_schema, Mapping):
            for index, item in enumerate(value):
                issues.extend(_diagnose(item, item_schema, f"{path}[{index}]"))
    elif type(value) is str:
        minimum = schema.get("minLength")
        maximum = schema.get("maxLength")
        if type(minimum) is int and len(value) < minimum:
            issues.append(_issue(path, f"expected at least {minimum} characters, got {len(value)}"))
        if type(maximum) is int and len(value) > maximum:
            issues.append(_issue(path, f"expected at most {maximum} characters, got {len(value)}"))
    elif type(value) in {int, float}:
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if type(minimum) in {int, float} and value < minimum:
            issues.append(_issue(path, f"expected at least {minimum}, got {json_type(value)}"))
        if type(maximum) in {int, float} and value > maximum:
            issues.append(_issue(path, f"expected at most {maximum}, got {json_type(value)}"))
    return issues


def diagnose_value(value: object, schema: Mapping[str, object]) -> tuple[str, ...]:
    """Collect every independently detectable schema issue in stable order."""

    return tuple(_diagnose(value, schema, "$"))


def diagnose_json_text(text: str, schema: Mapping[str, object]) -> tuple[str, ...]:
    """Parse strict JSON and report syntax, duplicate-key, and schema issues."""

    try:
        value = json.loads(
            text, object_pairs_hook=_unique_object, parse_constant=_reject_constant,
        )
    except _DuplicateKey:
        return ("$: duplicate object key",)
    except (json.JSONDecodeError, UnicodeError, TypeError, ValueError, RecursionError):
        return ("$: malformed JSON",)
    return diagnose_value(value, schema)


def bounded_chunks(
    issues: Sequence[str], *, prefix: str = "Structured reply validation"
) -> tuple[str, ...]:
    """Package ordered safe issues into separately bounded display strings."""

    if not issues:
        return ()
    chunks: list[str] = []
    current = prefix
    for issue in issues:
        line = "\n" + issue
        if len((current + line).encode("utf-8")) < _MAX_CHUNK_BYTES:
            current += line
            continue
        if len((prefix + line).encode("utf-8")) < _MAX_CHUNK_BYTES:
            if current != prefix:
                chunks.append(current)
            current = prefix + line
            continue
        if current != prefix:
            chunks.append(current)
            current = prefix
        remaining = issue
        while remaining:
            capacity = _MAX_CHUNK_BYTES - len(current.encode("utf-8")) - 1
            if capacity < 4:
                chunks.append(current)
                current = prefix
                continue
            part = ""
            used = 0
            for character in remaining:
                width = len(character.encode("utf-8"))
                if used + width >= capacity:
                    break
                part += character
                used += width
            if not part:
                chunks.append(current)
                current = prefix
                continue
            current += "\n" + part
            remaining = remaining[len(part):]
            if remaining:
                chunks.append(current)
                current = prefix
    chunks.append(current)
    return tuple(chunks)


def validate_details(details: object) -> None:
    """Reject invalid or overlarge diagnostic chunks at public boundaries."""

    if type(details) is not tuple:
        raise ValueError("validation details must be bounded text chunks")
    for item in details:
        if type(item) is not str or not item:
            raise ValueError("validation details must be bounded text chunks")
        try:
            size = len(item.encode("utf-8", errors="strict"))
        except UnicodeEncodeError:
            raise ValueError("validation details must be bounded text chunks") from None
        if size >= 32_768:
            raise ValueError("validation details must be bounded text chunks")
