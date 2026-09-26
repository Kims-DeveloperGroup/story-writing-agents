"""Content-free diagnostics for the closed provider JSON schema subset.

Only paths and constraints from the trusted schema enter a diagnostic. Provider
values, unknown member names, and parser exception messages never do.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterator, Mapping, Sequence
from enum import StrEnum
from itertools import islice


_PATH_MEMBER = re.compile(r"^[A-Za-z_][A-Za-z_0-9]*$")
_MAX_CHUNK_BYTES = 32_000
_MAX_ISSUES = 2_048
_OMITTED_ISSUES_NOTICE = "$: additional validation issues omitted"


class DomainValidationRule(StrEnum):
    """Closed domain findings that can cross the context service boundary."""

    MALFORMED_RESULT = "malformed_result"
    RESULT_OBJECT_REQUIRED = "result_object_required"
    RECOMMENDATION_REQUIRED = "recommendation_required"
    RECOMMENDATION_ONLY = "recommendation_only"
    OBJECTIVE_RECOMMENDATION_CONFLICT = "objective_recommendation_conflict"
    PROPOSAL_KIND_REQUIRED = "proposal_kind_required"
    LEAD_OUTCOME_REJECTED = "lead_outcome_rejected"
    OBJECTIVE_TARGETS = "objective_targets"
    ARTIFACT_OBJECTIVE_TARGETS = "artifact_objective_targets"
    PROPOSAL_OBJECTIVE_TARGETS = "proposal_objective_targets"
    RECOMMENDATION_SPECIALISTS = "recommendation_specialists"
    RECOMMENDATION_TARGETS = "recommendation_targets"
    RECOMMENDATION_OBJECTIVE_NOT_OFFERED = "recommendation_objective_not_offered"
    CONTINUATION_INSTRUCTION_REQUIRED = "continuation_instruction_required"
    CONTINUATION_SPECIALIST_DUPLICATE = "continuation_specialist_duplicate"
    CONTINUATION_ASSIGNMENT_INPUTS_REQUIRED = "continuation_assignment_inputs_required"
    CONTINUATION_INPUT_DUPLICATE = "continuation_input_duplicate"
    CONTINUATION_INPUT_NOT_OFFERED = "continuation_input_not_offered"
    CONTINUATION_REVIEWER_INPUT = "continuation_reviewer_input"
    CONTINUATION_SPECIALIST_REQUIRED = "continuation_specialist_required"
    CONTINUATION_ASSIGNMENT_MODE = "continuation_assignment_mode"
    MESSAGE_REQUIRED = "message_required"
    INSTRUCTION_REQUIRED = "instruction_required"
    PURPOSE_REQUIRED = "purpose_required"
    DOMAIN_DUPLICATE = "domain_duplicate"
    TARGET_DUPLICATE = "target_duplicate"
    SPECIALIST_DUPLICATE = "specialist_duplicate"
    ASSIGNMENT_INPUTS_REQUIRED = "assignment_inputs_required"
    INPUT_DUPLICATE = "input_duplicate"
    INPUT_NOT_OFFERED = "input_not_offered"
    INPUT_COVERAGE = "input_coverage"
    CONTINUATION_INPUT_COVERAGE = "continuation_input_coverage"
    REVIEWER_INPUT = "reviewer_input"
    SPECIALIST_REQUIRED = "specialist_required"
    ASSIGNMENT_MODE = "assignment_mode"
    PREPARATION_SCOPE = "preparation_scope"
    PREPARATION_SPECIALISTS = "preparation_specialists"
    PREPARATION_KIND = "preparation_kind"
    PREPARATION_OBJECTIVE = "preparation_objective"
    RECOMMENDATION_INSTRUCTION = "recommendation_instruction"
    RECOMMENDATION_SOURCES = "recommendation_sources"
    SOURCE_NOT_OFFERED = "source_not_offered"
    OBJECTIVE_NOT_OFFERED = "objective_not_offered"
    OBJECTIVE_BRANCH = "objective_branch"


_DOMAIN_RULE_ISSUES = {
    DomainValidationRule.OBJECTIVE_TARGETS: '$.action.result.objective_intent.target_paths: START requires nonempty unique target paths',
    DomainValidationRule.ARTIFACT_OBJECTIVE_TARGETS: '$.action.result.artifact_intent.objective_intent.target_paths: START requires nonempty unique target paths',
    DomainValidationRule.PROPOSAL_OBJECTIVE_TARGETS: '$.action.result.proposal.objective_intent.target_paths: START requires nonempty unique target paths',
    DomainValidationRule.RECOMMENDATION_SPECIALISTS: '$.action.result.recommended_action.specialist_ids: select unique offered specialist IDs and include every required authoring domain',
    DomainValidationRule.RECOMMENDATION_TARGETS: '$.action.result.recommended_action.target_paths: target paths must be nonempty and unique',
    DomainValidationRule.RECOMMENDATION_OBJECTIVE_NOT_OFFERED: '$.action.result.recommended_action.objective_id: select only an offered OPEN objective ID',
    DomainValidationRule.CONTINUATION_INSTRUCTION_REQUIRED: '$.action.result.next_intent.assignments: each instruction must be nonempty bounded UTF-8 text and satisfy review-criteria requirements',
    DomainValidationRule.CONTINUATION_SPECIALIST_DUPLICATE: '$.action.result.next_intent.assignments: agent_id values must be unique before canonical ordering',
    DomainValidationRule.CONTINUATION_ASSIGNMENT_INPUTS_REQUIRED: '$.action.result.next_intent.assignments: each input_ids must be a nonempty array of offered source aliases',
    DomainValidationRule.CONTINUATION_INPUT_DUPLICATE: '$.action.result.next_intent.assignments: each input_ids array must contain unique aliases',
    DomainValidationRule.CONTINUATION_INPUT_NOT_OFFERED: '$.action.result.next_intent.assignments: input_ids must use offered aliases',
    DomainValidationRule.CONTINUATION_REVIEWER_INPUT: '$.action.result.next_intent.assignments: every mandatory core-document reviewer must receive its own source input',
    DomainValidationRule.CONTINUATION_SPECIALIST_REQUIRED: '$.action.result.next_intent.assignments: include every specialist required by the authoring activity, target paths and domains',
    DomainValidationRule.CONTINUATION_ASSIGNMENT_MODE: '$.action.result.next_intent.assignments: required specialists must use review for review activity, otherwise draft',
    DomainValidationRule.MESSAGE_REQUIRED: '$.action.result.message: expected nonempty bounded UTF-8 text',
    DomainValidationRule.INSTRUCTION_REQUIRED: '$.action.result.proposal.assignments: each instruction must be nonempty bounded UTF-8 text (also applies to next_intent assignments)',
    DomainValidationRule.PURPOSE_REQUIRED: '$.action.result.next_intent.purpose: expected nonempty bounded UTF-8 text',
    DomainValidationRule.DOMAIN_DUPLICATE: '$.action.result.authoring_scope.domains: domains must be unique before canonical ordering',
    DomainValidationRule.TARGET_DUPLICATE: '$.action.result.authoring_scope.target_paths: target paths must be unique',
    DomainValidationRule.SPECIALIST_DUPLICATE: '$.action.result.proposal.assignments: agent_id values must be unique before canonical ordering (also applies to next_intent)',
    DomainValidationRule.ASSIGNMENT_INPUTS_REQUIRED: '$.action.result.proposal.assignments: each input_ids must be a nonempty array of offered source aliases',
    DomainValidationRule.INPUT_DUPLICATE: '$.action.result.proposal.assignments: each input_ids array must contain unique aliases',
    DomainValidationRule.INPUT_NOT_OFFERED: '$.action.result.proposal.assignments: input_ids must use offered aliases or completed authorized READ evidence IDs',
    DomainValidationRule.INPUT_COVERAGE: '$.action.result.proposal.assignments: input_ids must cover every disclosed source; runtime appends only confirmed recommendation request/decision aliases',
    DomainValidationRule.CONTINUATION_INPUT_COVERAGE: '$.action.result.next_intent.assignments: input_ids must cover every original input and every specialist reply',
    DomainValidationRule.REVIEWER_INPUT: '$.action.result.proposal.assignments: every mandatory core-document reviewer must receive its own source input (also applies to next_intent)',
    DomainValidationRule.SPECIALIST_REQUIRED: '$.action.result.proposal.assignments: include every specialist required by the authoring activity, target paths and domains',
    DomainValidationRule.ASSIGNMENT_MODE: '$.action.result.proposal.assignments: required specialists must use review for review activity, otherwise draft (also applies to next_intent)',
    DomainValidationRule.PREPARATION_SCOPE: '$.action.result.authoring_scope: retain the exact confirmed activity, task revision, target paths and domains',
    DomainValidationRule.PREPARATION_SPECIALISTS: '$.action.result.proposal.assignments: agent_id values must match the exact confirmed specialist set',
    DomainValidationRule.PREPARATION_KIND: '$.action.result.kind: confirmed preparation requires its exact proposal branch or a blocked question',
    DomainValidationRule.PREPARATION_OBJECTIVE: '$.action.result.objective_intent: confirmed artifact preparation requires its exact START targets or CONTINUE objective; consultation must retain its confirmed objective',
    DomainValidationRule.RECOMMENDATION_INSTRUCTION: '$.action.result.recommended_action.instruction: expected nonempty bounded UTF-8 text',
    DomainValidationRule.RECOMMENDATION_SOURCES: '$.action.result.recommended_action.source_ids: expected nonempty unique offered source aliases',
    DomainValidationRule.SOURCE_NOT_OFFERED: '$.action.result.recommended_action.source_ids: select only frozen offered aliases or evidence IDs from completed authorized READs',
    DomainValidationRule.OBJECTIVE_NOT_OFFERED: '$.action.result.objective_intent.objective_id: select only an offered OPEN objective ID (also applies to recommended_action.objective_id)',
    DomainValidationRule.OBJECTIVE_BRANCH: '$.action.result.objective_intent: use prepare for artifact_intent, consult for proposal, and clarify only for question direct outcomes; questions cannot cancel an objective',
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
                        f"expected an allowed string value, got {json_type(supplied)}",
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
            yield _issue(path, "does not match any allowed schema variant")
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
            path, f"expected an allowed {expected_type} value, got {json_type(value)}",
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
