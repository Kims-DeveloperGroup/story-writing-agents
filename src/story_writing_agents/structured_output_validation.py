"""Content-free diagnostics from trusted schemas and source-authored rules.

Only paths and constraints owned by the schema or rejecting validator enter a
diagnostic. Provider values, unknown member names, and parser exception messages
never do.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from itertools import islice
from typing import Callable, NoReturn, TypeVar


_PATH_MEMBER = re.compile(r"^[A-Za-z_][A-Za-z_0-9]*$")
_MAX_CHUNK_BYTES = 32_000
_MAX_ISSUES = 2_048
_OMITTED_ISSUES_NOTICE = "$: additional validation issues omitted"

_Result = TypeVar("_Result")
_Failure = TypeVar("_Failure", bound=BaseException)
_ABSENT = object()


@dataclass(frozen=True, slots=True)
class ValidationFinding:
    """A source-authored rule and path, never an exception's arbitrary text."""

    path: str
    requirement: str


def validation_failure(
    failure: _Failure, path: str, requirement: str, *, value: object = _ABSENT,
) -> _Failure:
    """Attach a trusted rule at its rejecting validator; preserve exception type."""
    if value is not _ABSENT:
        requirement += f"; got {json_type(value)}"
    failure.provider_validation_findings = (ValidationFinding(path, requirement),)
    return failure


def failure_findings(failure: BaseException) -> tuple[ValidationFinding, ...]:
    """Read only typed findings, without inspecting arbitrary exception messages."""
    try:
        findings = getattr(failure, "provider_validation_findings", ())
    except BaseException:
        return ()
    if type(findings) is tuple and all(type(item) is ValidationFinding for item in findings):
        return findings
    return ()


def raise_validation(failure: BaseException) -> NoReturn:
    """Preserve existing content-free exception-graph boundaries."""
    try:
        raise failure from None
    except BaseException as rejected:
        rejected.__cause__ = None
        rejected.__context__ = None
        raise


def carry_validation(failure: _Failure, cause: BaseException) -> _Failure:
    """Preserve source findings when an owner translates its exception."""
    findings = failure_findings(cause)
    if findings:
        failure.provider_validation_findings = findings
    return failure


def prefix_validation(failure: _Failure, path: str) -> _Failure:
    """Add a provider envelope to a source-owned property finding."""
    findings = failure_findings(failure)
    if findings:
        failure.provider_validation_findings = tuple(
            ValidationFinding(path + item.path[1:], item.requirement)
            for item in findings
        )
    return failure


def encoding_failure(
    failure: _Failure, value: object, schema: Mapping[str, object],
) -> _Failure:
    """Locate an existing encoder rejection using only trusted property names."""
    def rejected_text(item: object, contract: Mapping[str, object], path: str) -> Iterator[ValidationFinding]:
        if type(item) is str:
            try:
                item.encode("utf-8", errors="strict")
            except UnicodeEncodeError:
                yield ValidationFinding(path, "expected strict UTF-8 text without unpaired surrogates")
        elif type(item) is list:
            child = contract.get("items")
            if isinstance(child, Mapping):
                for index, element in enumerate(item):
                    yield from rejected_text(element, child, f"{path}[{index}]")
        elif type(item) is dict:
            properties = contract.get("properties")
            if isinstance(properties, Mapping):
                for name, child in properties.items():
                    if name in item and isinstance(name, str) and isinstance(child, Mapping):
                        yield from rejected_text(item[name], child, _path(path, name))
        variants = contract.get("anyOf")
        if type(variants) is list:
            for child in variants:
                if isinstance(child, Mapping):
                    yield from rejected_text(item, child, path)

    def unique_findings() -> Iterator[ValidationFinding]:
        seen: set[ValidationFinding] = set()
        for finding in rejected_text(value, schema, "$"):
            if finding not in seen:
                seen.add(finding)
                yield finding

    findings = tuple(islice(unique_findings(), _MAX_ISSUES + 1))
    if findings:
        failure.provider_validation_findings = findings
    return failure


def remap_validation(failure: _Failure, aliases: Mapping[str, str]) -> _Failure:
    """Translate runtime-owned constructor names back to provider wire fields."""
    def wire_path(path: str) -> str:
        for source in sorted(aliases, key=len, reverse=True):
            if path == source or path.startswith((source + ".", source + "[")):
                return aliases[source] + path[len(source):]
        return path

    findings = failure_findings(failure)
    if findings:
        failure.provider_validation_findings = tuple(
            ValidationFinding(wire_path(item.path), item.requirement)
            for item in findings
        )
    return failure


def validation_call(path: str, call: Callable[..., _Result], /, *args: object, **kwargs: object) -> _Result:
    """Give a nested validator's findings their actual enclosing property path."""
    try:
        return call(*args, **kwargs)
    except Exception as failure:
        findings = failure_findings(failure)
        if not findings and isinstance(failure, UnicodeError):
            validation_failure(failure, "$", "expected strict UTF-8 text")
            findings = failure_findings(failure)
        if not findings and call is int:
            validation_failure(failure, "$", "expected a decimal integer")
            findings = failure_findings(failure)
        if not findings and isinstance(call, type) and issubclass(call, StrEnum):
            validation_failure(
                failure, "$", "expected one of [" + allowed_values(tuple(item.value for item in call)) + "]",
                value=args[0] if args else None,
            )
            findings = failure_findings(failure)
        if findings:
            failure.provider_validation_findings = tuple(
                ValidationFinding(path + item.path[1:], item.requirement)
                for item in findings
            )
        raise


def failure_issues(failure: BaseException, *, path: str = "$") -> tuple[str, ...]:
    """Render findings under the physical provider envelope, without payloads."""
    return tuple(
        f"{path}{item.path[1:]}: {item.requirement}"
        for item in failure_findings(failure)
    )


class DomainValidationRule(StrEnum):
    """Closed domain findings that can cross the context service boundary."""

    MALFORMED_RESULT = "malformed_result"
    RESULT_OBJECT_REQUIRED = "result_object_required"
    RECOMMENDATION_REQUIRED = "recommendation_required"
    RECOMMENDATION_ONLY = "recommendation_only"
    OBJECTIVE_RECOMMENDATION_CONFLICT = "objective_recommendation_conflict"
    PROPOSAL_KIND_REQUIRED = "proposal_kind_required"
    MESSAGE_REQUIRED = "message_required"
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
    DomainValidationRule.MESSAGE_REQUIRED: (
        "$.action.result.message: expected nonempty text"
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


def allowed_values(values: Sequence[object]) -> str:
    """Render bounded choices from the trusted contract, never supplied values."""
    rendered = [json.dumps(item, ensure_ascii=True) for item in values[:32]]
    choices = ", ".join(item if len(item) <= 160 else item[:157] + "..." for item in rendered)
    if len(values) > 32:
        choices += f", ... ({len(values) - 32} additional allowed values)"
    return choices


def _discriminator_schema(
    variant: object, discriminator: str,
) -> Mapping[str, object] | None:
    if not isinstance(variant, Mapping):
        return None
    properties = variant.get("properties")
    if not isinstance(properties, Mapping):
        return None
    child = properties.get(discriminator)
    return child if isinstance(child, Mapping) else None


def _diagnose(value: object, schema: Mapping[str, object], path: str) -> Iterator[str]:
    variants = schema.get("anyOf")
    if type(variants) is list and variants:
        if type(value) is dict:
            for discriminator in ("kind", "action", "decision", "operation"):
                if discriminator not in value:
                    continue
                supplied = value[discriminator]
                candidates = [
                    child
                    for variant in variants
                    if (child := _discriminator_schema(variant, discriminator)) is not None
                    and type(child.get("enum")) is list
                ]
                matches = (
                    [
                        variant for variant in variants
                        if (child := _discriminator_schema(variant, discriminator)) is not None
                        and supplied in child.get("enum", ())
                    ]
                    if type(supplied) is str else []
                )
                if len(matches) == 1:
                    yield from _diagnose(value, matches[0], path)
                    return
                if len(matches) > 1:
                    exact_shapes = [
                        variant for variant in matches
                        if isinstance(variant, Mapping)
                        and isinstance(variant.get("properties"), Mapping)
                        and value.keys() == variant["properties"].keys()
                    ]
                    if len(exact_shapes) == 1:
                        yield from _diagnose(value, exact_shapes[0], path)
                        return
                if candidates and not matches:
                    yield _issue(
                        _path(path, discriminator),
                        "expected one of [" + allowed_values(tuple(dict.fromkeys(
                            item for candidate in candidates for item in candidate["enum"]
                        ))) + f"], got {json_type(supplied)}",
                    )
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
                                yield _issue(
                                    _path(path, name), "required property is missing",
                                )
                            elif name in value and isinstance(child, Mapping):
                                yield from _diagnose(
                                    value[name], child, _path(path, name),
                                )
                    return
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
        alternatives = []
        for variant in compatible_variants or variants:
            if isinstance(variant, Mapping):
                findings = tuple(islice(
                    _diagnose(value, variant, path), _MAX_ISSUES + 1,
                ))
                alternatives.append((
                    findings,
                    len(findings) > _MAX_ISSUES
                    or _OMITTED_ISSUES_NOTICE in findings,
                ))
        if alternatives:
            fewest = min(len(findings) for findings, _overflow in alternatives)
            closest = [
                item for item in alternatives if len(item[0]) == fewest
            ]
            if len(closest) == 1:
                findings, overflow = closest[0]
                yield from findings
                if overflow and _OMITTED_ISSUES_NOTICE not in findings:
                    yield _OMITTED_ISSUES_NOTICE
                return
            common = set(closest[0][0]).intersection(
                *(set(findings) for findings, _overflow in closest[1:])
            )
            if common:
                shared = tuple(item for item in closest[0][0] if item in common)
                yield from shared
                if (any(overflow for _findings, overflow in closest)
                    and _OMITTED_ISSUES_NOTICE not in shared):
                    yield _OMITTED_ISSUES_NOTICE
                return
            if not any(findings for findings, _overflow in closest):
                yield _issue(path, "expected one unambiguous schema alternative")
            for index, (findings, _overflow) in enumerate(closest, 1):
                for finding in findings:
                    finding_path, separator, requirement = finding.partition(": ")
                    yield _issue(
                        finding_path if separator else path,
                        f"allowed alternative {index} requires {requirement if separator else finding}",
                    )
            if any(overflow for _findings, overflow in closest):
                yield _OMITTED_ISSUES_NOTICE
            return
        yield _issue(path, "does not match the required schema")
        return

    raw_type = schema.get("type")
    expected = (
        (raw_type,) if type(raw_type) is str else
        tuple(item for item in raw_type if type(item) is str)
        if type(raw_type) is list else ()
    )
    if expected and not any(_matches_type(value, item) for item in expected):
        yield _issue(path, f"expected {' or '.join(expected)}, got {json_type(value)}")
        return
    if type(value) is float and not math.isfinite(value):
        yield _issue(path, "expected a finite number, got number")
        return

    allowed = schema.get("enum")
    if type(allowed) is list and not any(
        type(value) is type(item) and value == item for item in allowed
    ):
        enum_types = tuple(dict.fromkeys(json_type(item) for item in allowed))
        expected_type = " or ".join(enum_types) if enum_types else "JSON"
        yield _issue(
            path, f"expected one of [{allowed_values(allowed)}] ({expected_type}), got {json_type(value)}",
        )
    if type(value) is dict:
        properties = schema.get("properties")
        if isinstance(properties, Mapping):
            required = schema.get("required")
            names = (
                tuple(item for item in required if type(item) is str)
                if type(required) is list else ()
            )
            for name in names:
                if name not in value:
                    yield _issue(_path(path, name), "required property is missing")
            if schema.get("additionalProperties") is False:
                unknown_count = len(value.keys() - properties.keys())
                if unknown_count:
                    yield _issue(path, f"contains {unknown_count} unexpected properties (names withheld)")
            for name, child in properties.items():
                if name in value and type(name) is str and isinstance(child, Mapping):
                    yield from _diagnose(value[name], child, _path(path, name))
    elif type(value) is list:
        minimum = schema.get("minItems")
        maximum = schema.get("maxItems")
        if type(minimum) is int and len(value) < minimum:
            yield _issue(path, f"expected at least {minimum} items, got {len(value)}")
        if type(maximum) is int and len(value) > maximum:
            yield _issue(path, f"expected at most {maximum} items, got {len(value)}")
        item_schema = schema.get("items")
        if isinstance(item_schema, Mapping):
            for index, item in enumerate(value):
                yield from _diagnose(item, item_schema, f"{path}[{index}]")
    elif type(value) is str:
        minimum = schema.get("minLength")
        maximum = schema.get("maxLength")
        if type(minimum) is int and len(value) < minimum:
            yield _issue(path, f"expected at least {minimum} characters, got {len(value)}")
        if type(maximum) is int and len(value) > maximum:
            yield _issue(path, f"expected at most {maximum} characters, got {len(value)}")
    elif type(value) in {int, float}:
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if type(minimum) in {int, float} and value < minimum:
            yield _issue(path, f"expected at least {minimum}, got {json_type(value)}")
        if type(maximum) in {int, float} and value > maximum:
            yield _issue(path, f"expected at most {maximum}, got {json_type(value)}")


def diagnose_value(value: object, schema: Mapping[str, object]) -> tuple[str, ...]:
    """Collect a bounded ordered prefix of independently detectable issues."""

    issues = tuple(islice(_diagnose(value, schema, "$"), _MAX_ISSUES + 1))
    if len(issues) > _MAX_ISSUES:
        return (*issues[:_MAX_ISSUES], _OMITTED_ISSUES_NOTICE)
    return issues


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
    if len(issues) > _MAX_ISSUES and not (
        len(issues) == _MAX_ISSUES + 1
        and issues[-1] == _OMITTED_ISSUES_NOTICE
    ):
        issues = (*islice(issues, _MAX_ISSUES), _OMITTED_ISSUES_NOTICE)
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
