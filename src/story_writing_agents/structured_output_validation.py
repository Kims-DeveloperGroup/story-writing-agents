"""Content-free diagnostics from trusted schemas and source-authored rules.

Only paths and constraints owned by the schema or rejecting validator enter a
diagnostic. Provider values, unknown member names, and parser exception messages
never do.
"""

from __future__ import annotations

import json
import math
import hashlib
import inspect
import marshal
from abc import ABC, abstractmethod
from copy import deepcopy
from decimal import Decimal
import re
import unicodedata
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum, StrEnum
from itertools import islice, combinations
from typing import Callable, Generic, NoReturn, TypeVar


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
    if (type(findings) is tuple and findings
            and all(type(item) is DomainValidationRule for item in findings)):
        return tuple(_DOMAIN_RULE_ISSUES[rule] for rule in findings)
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
    DomainValidationRule.OBJECTIVE_TARGETS: ValidationFinding(
        '$.objective_intent.target_paths', 'START requires nonempty unique target paths',
    ),
    DomainValidationRule.ARTIFACT_OBJECTIVE_TARGETS: ValidationFinding(
        '$.artifact_intent.objective_intent.target_paths',
        'START requires nonempty unique target paths',
    ),
    DomainValidationRule.PROPOSAL_OBJECTIVE_TARGETS: ValidationFinding(
        '$.proposal.objective_intent.target_paths', 'START requires nonempty unique target paths',
    ),
    DomainValidationRule.RECOMMENDATION_SPECIALISTS: ValidationFinding(
        '$.recommended_action.specialist_ids',
        'select unique offered specialist IDs and include every required authoring domain',
    ),
    DomainValidationRule.RECOMMENDATION_TARGETS: ValidationFinding(
        '$.recommended_action.target_paths', 'target paths must be nonempty and unique',
    ),
    DomainValidationRule.RECOMMENDATION_OBJECTIVE_NOT_OFFERED: ValidationFinding(
        '$.recommended_action.objective_id', 'select only an offered OPEN objective ID',
    ),
    DomainValidationRule.CONTINUATION_INSTRUCTION_REQUIRED: ValidationFinding(
        '$.next_intent.assignments',
        'each instruction must be nonempty bounded UTF-8 text and satisfy review-criteria '
        'requirements',
    ),
    DomainValidationRule.CONTINUATION_SPECIALIST_DUPLICATE: ValidationFinding(
        '$.next_intent.assignments', 'agent_id values must be unique before canonical ordering',
    ),
    DomainValidationRule.CONTINUATION_ASSIGNMENT_INPUTS_REQUIRED: ValidationFinding(
        '$.next_intent.assignments',
        'each input_ids must be a nonempty array of offered source aliases',
    ),
    DomainValidationRule.CONTINUATION_INPUT_DUPLICATE: ValidationFinding(
        '$.next_intent.assignments', 'each input_ids array must contain unique aliases',
    ),
    DomainValidationRule.CONTINUATION_INPUT_NOT_OFFERED: ValidationFinding(
        '$.next_intent.assignments', 'input_ids must use offered aliases',
    ),
    DomainValidationRule.CONTINUATION_REVIEWER_INPUT: ValidationFinding(
        '$.next_intent.assignments',
        'every mandatory core-document reviewer must receive its own source input',
    ),
    DomainValidationRule.CONTINUATION_SPECIALIST_REQUIRED: ValidationFinding(
        '$.next_intent.assignments',
        'include every specialist required by the authoring activity, target paths and domains',
    ),
    DomainValidationRule.CONTINUATION_ASSIGNMENT_MODE: ValidationFinding(
        '$.next_intent.assignments',
        'required specialists must use review for review activity, otherwise draft',
    ),
    DomainValidationRule.MESSAGE_REQUIRED: ValidationFinding(
        '$.message', 'expected nonempty bounded UTF-8 text',
    ),
    DomainValidationRule.INSTRUCTION_REQUIRED: ValidationFinding(
        '$.proposal.assignments',
        'each instruction must be nonempty bounded UTF-8 text (also applies to next_intent '
        'assignments)',
    ),
    DomainValidationRule.PURPOSE_REQUIRED: ValidationFinding(
        '$.next_intent.purpose', 'expected nonempty bounded UTF-8 text',
    ),
    DomainValidationRule.DOMAIN_DUPLICATE: ValidationFinding(
        '$.authoring_scope.domains', 'domains must be unique before canonical ordering',
    ),
    DomainValidationRule.TARGET_DUPLICATE: ValidationFinding(
        '$.authoring_scope.target_paths', 'target paths must be unique',
    ),
    DomainValidationRule.SPECIALIST_DUPLICATE: ValidationFinding(
        '$.proposal.assignments',
        'agent_id values must be unique before canonical ordering (also applies to next_intent)',
    ),
    DomainValidationRule.ASSIGNMENT_INPUTS_REQUIRED: ValidationFinding(
        '$.proposal.assignments',
        'each input_ids must be a nonempty array of offered source aliases',
    ),
    DomainValidationRule.INPUT_DUPLICATE: ValidationFinding(
        '$.proposal.assignments', 'each input_ids array must contain unique aliases',
    ),
    DomainValidationRule.INPUT_NOT_OFFERED: ValidationFinding(
        '$.proposal.assignments',
        'input_ids must use offered aliases or completed authorized READ evidence IDs',
    ),
    DomainValidationRule.INPUT_COVERAGE: ValidationFinding(
        '$.proposal.assignments',
        'input_ids must cover every disclosed source; runtime appends only confirmed '
        'recommendation request/decision aliases',
    ),
    DomainValidationRule.CONTINUATION_INPUT_COVERAGE: ValidationFinding(
        '$.next_intent.assignments',
        'input_ids must cover every original input and every specialist reply',
    ),
    DomainValidationRule.REVIEWER_INPUT: ValidationFinding(
        '$.proposal.assignments',
        'every mandatory core-document reviewer must receive its own source input (also applies '
        'to next_intent)',
    ),
    DomainValidationRule.SPECIALIST_REQUIRED: ValidationFinding(
        '$.proposal.assignments',
        'include every specialist required by the authoring activity, target paths and domains',
    ),
    DomainValidationRule.ASSIGNMENT_MODE: ValidationFinding(
        '$.proposal.assignments',
        'required specialists must use review for review activity, otherwise draft (also applies '
        'to next_intent)',
    ),
    DomainValidationRule.PREPARATION_SCOPE: ValidationFinding(
        '$.authoring_scope',
        'retain the exact confirmed activity, task revision, target paths and domains',
    ),
    DomainValidationRule.PREPARATION_SPECIALISTS: ValidationFinding(
        '$.proposal.assignments', 'agent_id values must match the exact confirmed specialist set',
    ),
    DomainValidationRule.PREPARATION_KIND: ValidationFinding(
        '$.kind', 'confirmed preparation requires its exact proposal branch or a blocked question',
    ),
    DomainValidationRule.PREPARATION_OBJECTIVE: ValidationFinding(
        '$.objective_intent',
        'confirmed artifact preparation requires its exact START targets or CONTINUE objective; '
        'consultation must retain its confirmed objective',
    ),
    DomainValidationRule.RECOMMENDATION_INSTRUCTION: ValidationFinding(
        '$.recommended_action.instruction', 'expected nonempty bounded UTF-8 text',
    ),
    DomainValidationRule.RECOMMENDATION_SOURCES: ValidationFinding(
        '$.recommended_action.source_ids', 'expected nonempty unique offered source aliases',
    ),
    DomainValidationRule.SOURCE_NOT_OFFERED: ValidationFinding(
        '$.recommended_action.source_ids',
        'select only frozen offered aliases or evidence IDs from completed authorized READs',
    ),
    DomainValidationRule.OBJECTIVE_NOT_OFFERED: ValidationFinding(
        '$.objective_intent.objective_id',
        'select only an offered OPEN objective ID (also applies to '
        'recommended_action.objective_id)',
    ),
    DomainValidationRule.OBJECTIVE_BRANCH: ValidationFinding(
        '$.objective_intent',
        'use prepare for artifact_intent, consult for proposal, and clarify only for question '
        'direct outcomes; questions cannot cancel an objective',
    ),
    DomainValidationRule.MALFORMED_RESULT: ValidationFinding('$', 'malformed JSON'),
    DomainValidationRule.RESULT_OBJECT_REQUIRED: ValidationFinding('$', 'expected object'),
    DomainValidationRule.RECOMMENDATION_REQUIRED: ValidationFinding(
        '$.recommended_action', 'required for a recommendation',
    ),
    DomainValidationRule.RECOMMENDATION_ONLY: ValidationFinding(
        '$.recommended_action', 'allowed only for a recommendation',
    ),
    DomainValidationRule.OBJECTIVE_RECOMMENDATION_CONFLICT: ValidationFinding(
        '$.objective_intent', 'cannot accompany recommended_action',
    ),
    DomainValidationRule.PROPOSAL_KIND_REQUIRED: ValidationFinding(
        '$.proposal', 'proposal intent requires proposal kind',
    ),
    DomainValidationRule.LEAD_OUTCOME_REJECTED: ValidationFinding(
        '$', 'rejected by a lead outcome rule',
    ),
}


def domain_rule_chunks(rules: object) -> tuple[str, ...]:
    """Render only known fixed domain rules; discard untrusted callback text."""

    if type(rules) is not tuple or not rules or any(
        type(rule) is not DomainValidationRule for rule in rules
    ):
        return bounded_chunks(("$.action.result: rejected by a domain result rule",))
    return bounded_chunks(tuple(
        f"$.action.result{finding.path[1:]}: {finding.requirement}"
        for finding in (_DOMAIN_RULE_ISSUES[rule] for rule in rules)))


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


def _finite_float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError
    return result


def strict_json_value(text: str, *, exact_numbers: bool = False) -> object:
    """Parse current-format JSON without duplicates or nonfinite numbers.

    Syntax failures carry only a trusted machine rule. Parser messages and
    supplied member names never become provider-facing repair instructions.
    """
    try:
        return json.loads(
            text, object_pairs_hook=_unique_object,
            parse_constant=_reject_constant, parse_float=Decimal if exact_numbers else _finite_float,
        )
    except _DuplicateKey:
        raise validation_failure(ValueError("JSON rejected"), "$", "duplicate object key") from None
    except (json.JSONDecodeError, UnicodeError, TypeError, ValueError, RecursionError):
        raise validation_failure(ValueError("JSON rejected"), "$", "expected strict JSON") from None


def strict_json_object(text: str, *, exact_numbers: bool = False) -> dict[str, object]:
    value = strict_json_value(text, exact_numbers=exact_numbers)
    if type(value) is not dict:
        raise validation_failure(ValueError("JSON rejected"), "$", "expected object")
    return value


def strict_json_member_text(text: str, member_path: tuple[str, ...]) -> str:
    """Return an exact member token after strict full-document validation."""
    strict_json_value(text, exact_numbers=True)
    decoder = json.JSONDecoder(object_pairs_hook=_unique_object,
                               parse_constant=_reject_constant, parse_float=Decimal)

    def skip(index: int) -> int:
        while index < len(text) and text[index] in " \t\r\n":
            index += 1
        return index

    start, end = skip(0), len(text)
    for name in member_path:
        if text[start] != "{":
            raise ValueError("JSON member path rejected")
        cursor = skip(start + 1)
        found = None
        while cursor < end and text[cursor] != "}":
            key, cursor = decoder.raw_decode(text, cursor)
            cursor = skip(cursor)
            if text[cursor] != ":":
                raise ValueError("JSON member path rejected")
            value_start = skip(cursor + 1)
            _, value_end = decoder.raw_decode(text, value_start)
            if key == name:
                found = (value_start, value_end)
                break
            cursor = skip(value_end)
            if text[cursor] != ",":
                break
            cursor = skip(cursor + 1)
        if found is None:
            raise ValueError("JSON member path rejected")
        start, end = found
    return text[start:end]


_RULE_TYPES = frozenset({"object", "array", "string", "integer", "number", "boolean", "null"})
# Explicit Python str.strip whitespace, without ECMAScript \s differences.
# UTF-8 byte ceilings remain an owner check, distinct from character counts.
NONEMPTY_TEXT_PATTERN = (
    r"^[^\u0000]*[^\u0000\u0009-\u000d\u001c-\u0020\u0085\u00a0"
    r"\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000][^\u0000]*$"
)


def array_rule(item: Mapping[str, object], *, minimum: int = 0, maximum: int | None = None) -> dict[str, object]:
    rule = {"type": "array", "items": dict(item), "minItems": minimum}
    if maximum is not None:
        rule["maxItems"] = maximum
    return rule


def closed_rule(properties: Mapping[str, object], *, description: str | None = None) -> dict[str, object]:
    rule = {"type": "object", "properties": dict(properties),
            "required": list(properties), "additionalProperties": False}
    if description is not None:
        rule["description"] = description
    return rule


def finite_subset_rule(choices: tuple[str, ...], *, minimum: int = 1, maximum: int | None = None) -> dict[str, object]:
    maximum = len(choices) if maximum is None else maximum
    if len(choices) > 4 or not 0 <= minimum <= maximum <= len(choices) or len(set(choices)) != len(choices):
        raise ValueError("invalid small finite selection declaration")
    variants = [closed_rule({item: {"type": "boolean", "enum": [True]} for item in subset})
                for count in range(minimum, maximum + 1) for subset in combinations(choices, count)]
    return variants[0] if len(variants) == 1 else {"anyOf": variants}


def finite_selection_rule(choices: tuple[str, ...], *, minimum: int, maximum: int) -> dict[str, object]:
    """A closed balanced finite graph enforcing unique selections and counts."""
    if (type(choices) is not tuple or any(type(item) is not str or not item for item in choices)
            or len(set(choices)) != len(choices) or type(minimum) is not int or type(maximum) is not int
            or not 0 <= minimum <= maximum <= len(choices)):
        raise ValueError("invalid finite selection declaration")
    if not choices:
        return closed_rule({})
    # Unrestricted and nonempty ranges have a linear shared graph.  The wire
    # remains the same balanced tree with one required boolean leaf per offer.
    if maximum == len(choices) and minimum in {0, 1}:
        prefix = "s_range_" + hashlib.sha256(json.dumps(choices, ensure_ascii=False).encode("utf-8")).hexdigest()[:20]
        definitions = {}
        def range_node(first, end, mode):
            name = f"{prefix}_{first}_{end}_{mode}"
            if name in definitions:
                return {"$ref": "#/$defs/" + name}
            if end - first == 1:
                flag = {"type": "boolean"}
                if mode != "all":
                    flag["enum"] = [mode == "positive"]
                node = closed_rule({choices[first]: flag})
            else:
                middle = (first + end) // 2
                if mode == "positive":
                    node = {"anyOf": [closed_rule({"left": range_node(first, middle, "positive"),
                                                  "right": range_node(middle, end, "all")},
                                      description=(f"Select 1 to {middle - first} left choices and "
                                                   f"0 to {end - middle} right choices.")),
                                      closed_rule({"left": range_node(first, middle, "zero"),
                                                  "right": range_node(middle, end, "positive")},
                                      description=(f"Select 0 left choices and "
                                                   f"1 to {end - middle} right choices."))]}
                else:
                    node = closed_rule({"left": range_node(first, middle, mode),
                                        "right": range_node(middle, end, mode)})
            definitions[name] = node
            return {"$ref": "#/$defs/" + name}
        root = range_node(0, len(choices), "positive" if minimum else "all")
        return {**root, "$defs": definitions}
    prefix = "s_" + hashlib.sha256(json.dumps(choices, ensure_ascii=False).encode("utf-8")).hexdigest()[:20]
    definitions: dict[str, object] = {}

    def build(first: int, end: int) -> dict[int, dict[str, str]]:
        counts = {}
        if end - first == 1:
            for count in range(min(maximum, 1) + 1):
                name = f"{prefix}_{first}_{end}_{count}"
                definitions[name] = closed_rule({choices[first]: {"type": "boolean", "enum": [bool(count)]}})
                counts[count] = {"$ref": "#/$defs/" + name}
            return counts
        middle = (first + end) // 2
        left, right = build(first, middle), build(middle, end)
        for count in range(min(maximum, end - first) + 1):
            variants = [closed_rule({"left": left[a], "right": right[count - a]},
                                    description=(f"Select exactly {a} left choices and "
                                                 f"{count - a} right choices; {count} total."))
                        for a in left if count - a in right]
            name = f"{prefix}_{first}_{end}_{count}"
            definitions[name] = variants[0] if len(variants) == 1 else {"anyOf": variants}
            counts[count] = {"$ref": "#/$defs/" + name}
        return counts

    roots = build(0, len(choices))
    variants = [roots[count] for count in range(minimum, maximum + 1)]
    return {**(variants[0] if len(variants) == 1 else {"anyOf": variants}), "$defs": definitions}


def decode_finite_selection(value: object, choices: tuple[str, ...], *,
                            minimum: int, maximum: int) -> tuple[str, ...]:
    """Decode the validated finite graph in its exact offered order."""
    def walk(node: object, first: int, end: int) -> tuple[str, ...]:
        if type(node) is not dict:
            raise ValueError("finite selection object rejected")
        if end == first:
            if node:
                raise ValueError("finite selection leaf rejected")
            return ()
        if end - first == 1:
            name = choices[first]
            if set(node) != {name} or type(node[name]) is not bool:
                raise ValueError("finite selection leaf rejected")
            return (name,) if node[name] else ()
        if set(node) != {"left", "right"}:
            raise ValueError("finite selection partition rejected")
        middle = (first + end) // 2
        return walk(node["left"], first, middle) + walk(node["right"], middle, end)
    selected = walk(value, 0, len(choices))
    if not minimum <= len(selected) <= maximum:
        raise ValueError("finite selection count rejected")
    return selected


def unicode_category_exclusion(categories: tuple[str, ...]) -> str:
    """An exact lexical character class derived from the owner's Unicode table."""
    ranges: list[tuple[int, int]] = []
    for codepoint in range(0x110000):
        if unicodedata.category(chr(codepoint)) in categories:
            if ranges and ranges[-1][1] + 1 == codepoint:
                ranges[-1] = (ranges[-1][0], codepoint)
            else:
                ranges.append((codepoint, codepoint))
    def token(value: int) -> str:
        return (f"\\u{value:04x}" if value <= 0xffff else re.escape(chr(value)))
    return "".join(token(first) + ("-" + token(last) if last != first else "")
                   for first, last in ranges)


def nonblank_text_rule(*, maximum_characters: int | None = None) -> dict[str, object]:
    rule = {"type": "string", "pattern": r"[^\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]"}
    if maximum_characters is not None:
        rule["maxLength"] = maximum_characters
    return rule


def nonempty_text_rule() -> dict[str, object]:
    return {"type": "string", "pattern": NONEMPTY_TEXT_PATTERN}


def visible_text_rule() -> dict[str, object]:
    """Existing visible-answer text permits whitespace and NUL, but not empty."""
    return {"type": "string", "pattern": r"[\s\S]"}


def nonempty_text_value(value: object) -> bool:
    return type(value) is str and re.search(NONEMPTY_TEXT_PATTERN, value) is not None


_RULE_KEYS = frozenset({
    "type", "enum", "description", "properties", "required", "additionalProperties",
    "items", "minItems", "maxItems", "minLength", "maxLength", "pattern", "minimum", "maximum",
    "anyOf", "$defs", "$ref",
})


def _local_rule(root: Mapping[str, object], reference: object) -> Mapping[str, object]:
    if type(reference) is not str or not reference.startswith("#/$defs/"):
        raise validation_failure(ValueError("schema rejected"), "$", "expected local definition reference")
    name = reference[len("#/$defs/"):]
    if "/" in name or "~" in name or not name:
        raise validation_failure(ValueError("schema rejected"), "$", "expected literal local definition name")
    definitions = root.get("$defs")
    node = definitions.get(name) if isinstance(definitions, Mapping) else None
    if not isinstance(node, Mapping):
        raise validation_failure(ValueError("schema rejected"), "$", "local definition missing")
    return node


def canonical_schema_rules(schema: Mapping[str, object], *, maximum_bytes: int = 65_536) -> bytes:
    """Check and freeze the supported, closed structural rule graph.

    Owners declare the rules. This interpreter checks their completeness and
    resolves acyclic local definitions; it does not manufacture stage choices
    or downgrade an unsupported predicate into a description or host check.
    """
    try:
        encoded = json.dumps(dict(schema), ensure_ascii=False, separators=(",", ":"),
                             sort_keys=True, allow_nan=False).encode("utf-8", "strict")
        root = strict_json_object(encoded.decode("utf-8"))
    except (TypeError, ValueError, UnicodeError):
        raise validation_failure(ValueError("schema rejected"), "$", "expected strict JSON schema rules") from None
    if type(maximum_bytes) is not int or maximum_bytes < 1 or len(encoded) > maximum_bytes:
        raise validation_failure(ValueError("schema rejected"), "$", "schema byte limit exceeded")
    definitions = root.get("$defs", {})
    if type(definitions) is not dict or any(type(k) is not str or not k or "/" in k or "~" in k for k in definitions):
        raise validation_failure(ValueError("schema rejected"), "$", "invalid local definitions")
    checked: set[str] = set()

    def reject(path: str, requirement: str) -> NoReturn:
        raise validation_failure(ValueError("schema rejected"), path, requirement)

    def visit(node: object, path: str, active: frozenset[str]) -> None:
        if type(node) is not dict or set(node) - _RULE_KEYS:
            reject(path, "unsupported structural rule")
        description = node.get("description")
        if description is not None and (type(description) is not str or not description.strip()):
            reject(path, "expected nonempty schema description")
        if "$defs" in node and node is not root:
            reject(path, "definitions must be declared at the graph root")
        if "$ref" in node:
            if set(node) - {"$ref", "description", "$defs"}:
                reject(path, "reference cannot carry independent structural siblings")
            reference = node["$ref"]
            target = _local_rule(root, reference)
            if reference in active:
                reject(path, "cyclic local definition")
            if reference not in checked:
                visit(target, path, active | {reference})
                checked.add(reference)
            return
        variants = node.get("anyOf")
        if variants is not None:
            if set(node) - {"anyOf", "description", "$defs"} or type(variants) is not list or len(variants) < 2:
                reject(path, "expected complete schema alternatives")
            for child in variants:
                visit(child, path, active)
            return
        raw_types = node.get("type")
        types = [raw_types] if type(raw_types) is str else raw_types
        if (type(types) is not list or not types or len(types) > 2
                or any(type(item) is not str or item not in _RULE_TYPES for item in types)
                or len(set(types)) != len(types) or len(set(types) - {"null"}) > 1):
            reject(path, "expected exact JSON type and explicit nullability")
        concrete = next((item for item in types if item != "null"), "null")
        enum = node.get("enum")
        if enum is not None:
            if type(enum) is not list or not enum or any(
                type(item) in {dict, list} or not any(_matches_type(item, expected) for expected in types)
                for item in enum
            ):
                reject(path, "expected typed scalar choices")
            if any(_same_json_scalar(left, right) for number, left in enumerate(enum) for right in enum[number + 1:]):
                reject(path, "duplicate declared choice")
        keys = set(node) - {"type", "enum", "description", "$defs"}
        if concrete == "object":
            if keys != {"properties", "required", "additionalProperties"}:
                reject(path, "expected closed all-required object")
            properties, required = node["properties"], node["required"]
            if (type(properties) is not dict or any(type(k) is not str or not k for k in properties)
                    or type(required) is not list or any(type(k) is not str for k in required)
                    or len(required) != len(properties) or set(required) != set(properties)
                    or node["additionalProperties"] is not False):
                reject(path, "expected closed all-required object")
            for name, child in properties.items():
                visit(child, _path(path, name), active)
        elif concrete == "array":
            if "items" not in keys or keys - {"items", "minItems", "maxItems"}:
                reject(path, "expected explicit array item rules")
            minimum, maximum = node.get("minItems", 0), node.get("maxItems")
            if type(minimum) is not int or minimum < 0 or (maximum is not None and (type(maximum) is not int or maximum < minimum)):
                reject(path, "invalid array count bounds")
            visit(node["items"], path + "[]", active)
        elif concrete == "string":
            minimum, maximum = node.get("minLength", 0), node.get("maxLength")
            if (type(minimum) is not int or minimum < 0
                    or (maximum is not None and (type(maximum) is not int or maximum < minimum))):
                reject(path, "invalid string character bounds")
            if keys - {"pattern", "minLength", "maxLength"}:
                reject(path, "unsupported string restriction")
            if "pattern" in node:
                if type(node["pattern"]) is not str:
                    reject(path, "expected string pattern")
                try:
                    re.compile(node["pattern"])
                except re.error:
                    reject(path, "invalid string pattern")
        elif concrete in {"integer", "number"}:
            if keys - {"minimum", "maximum"}:
                reject(path, "unsupported numeric restriction")
            for name in keys:
                number = node[name]
                if type(number) not in {int, float} or not math.isfinite(number):
                    reject(path, "expected finite numeric bound")
            if "minimum" in node and "maximum" in node and node["minimum"] > node["maximum"]:
                reject(path, "inverted numeric bounds")
        elif keys:
            reject(path, "unsupported scalar restriction")

    visit(root, "$", frozenset())
    for name, child in definitions.items():
        reference = "#/$defs/" + name
        if reference not in checked:
            visit(child, "$defs", frozenset({reference}))
            checked.add(reference)
    return encoded


def schema_rule_fingerprints(
    schema: Mapping[str, object], *, selected: Mapping[str, object] | None = None,
) -> tuple[str, dict[str, str]]:
    """Derive alias-neutral node identities without declaring or admitting a schema."""
    if not isinstance(schema, Mapping) or (selected is not None and not isinstance(selected, Mapping)):
        raise ValueError("schema fingerprint inputs must be objects")
    root = schema
    memo: dict[int, str] = {}
    visiting: set[int] = set()

    def fingerprint(node: Mapping[str, object]) -> str:
        identity = id(node)
        if identity in memo:
            return memo[identity]
        if identity in visiting:
            raise ValueError("cyclic schema rule graph")
        visiting.add(identity)
        if "$ref" in node:
            if set(node) - {"$ref", "description", "$defs"}:
                raise ValueError("schema reference has sibling rules")
            result = fingerprint(_local_rule(root, node["$ref"]))
        else:
            encoded = {}
            for key, value in node.items():
                if key in {"$defs", "description"}:
                    continue
                if key == "properties":
                    encoded[key] = {name: fingerprint(child) for name, child in sorted(value.items())}
                elif key == "items":
                    encoded[key] = fingerprint(value)
                elif key == "anyOf":
                    encoded[key] = [fingerprint(child) for child in value]
                else:
                    encoded[key] = value
            result = hashlib.sha256(json.dumps(encoded, sort_keys=True, ensure_ascii=False,
                allow_nan=False, separators=(",", ":")).encode("utf-8", "strict")).hexdigest()
        if "description" in node:
            result = hashlib.sha256(json.dumps({"referenced_rule": result,
                "description": node["description"]}, sort_keys=True,
                ensure_ascii=False, allow_nan=False,
                separators=(",", ":")).encode("utf-8", "strict")).hexdigest()
        visiting.remove(identity)
        memo[identity] = result
        return result

    result = fingerprint(root if selected is None else selected)
    definitions = root.get("$defs", {})
    if not isinstance(definitions, Mapping) or any(
        type(name) is not str or not isinstance(node, Mapping) for name, node in definitions.items()
    ):
        raise ValueError("schema definitions must be named objects")
    definition_fingerprints = {name: fingerprint(node) for name, node in sorted(definitions.items())}
    return result, definition_fingerprints


@dataclass(frozen=True, slots=True)
class SchemaRules:
    """One immutable owner-declared graph shared by projection and validation."""
    canonical_bytes: bytes
    maximum_bytes: int = field(default=65_536, kw_only=True)

    def __post_init__(self) -> None:
        if type(self.canonical_bytes) is not bytes:
            raise TypeError("schema rules require canonical bytes")
        value = strict_json_object(self.canonical_bytes.decode("utf-8", "strict"))
        if canonical_schema_rules(value, maximum_bytes=self.maximum_bytes) != self.canonical_bytes:
            raise ValueError("schema rules are not canonical")

    @classmethod
    def declare(cls, schema: Mapping[str, object], *, maximum_bytes: int = 65_536) -> SchemaRules:
        return cls(canonical_schema_rules(schema, maximum_bytes=maximum_bytes), maximum_bytes=maximum_bytes)

    def schema_value(self) -> dict[str, object]:
        return strict_json_object(self.canonical_bytes.decode("utf-8"))

    def findings(self, value: object) -> tuple[str, ...]:
        return diagnose_value(value, self.schema_value())
    def corrective_facts(self, requirements: tuple[str, ...]) -> tuple[dict[str, object], ...]:
        """Project bounded trusted rules; supplied paths only locate known nodes.

        Rule pointers refer to this exact graph. Alternative rules stay conditional
        on the same whole-graph alternative, rather than becoming independent facts.
        Unknown member names and rejected values never enter the returned paths.
        """
        root = self.schema_value()
        wanted = tuple(dict.fromkeys(re.sub(r"\[\d+\]", "[*]", item.partition(": ")[0])
                                     for item in requirements if type(item) is str)) or ("$",)
        candidates: list[tuple[str, str, Mapping[str, object]]] = []
        visited: set[tuple[str, str, str]] = set()
        def pointer_member(value: str) -> str:
            return value.replace("~", "~0").replace("/", "~1")
        def trace(node: Mapping[str, object], pointer: str, path: str, target: str) -> None:
            key = (pointer, path, target)
            if key in visited:
                return
            visited.add(key)
            if "$ref" in node:
                ref = node["$ref"]
                trace(_local_rule(root, ref), str(ref)[1:], path, target)
                return
            variants = node.get("anyOf")
            if type(variants) is list and target != path:
                for number, variant in enumerate(variants):
                    trace(variant, pointer + "/anyOf/" + str(number), path, target)
                candidates.append((pointer, path, node))
                return
            properties = node.get("properties")
            if type(properties) is dict and target != path:
                for name, child in properties.items():
                    child_path = _path(path, name)
                    if target == child_path or target.startswith(child_path + ".") or target.startswith(child_path + "["):
                        trace(child, pointer + "/properties/" + pointer_member(name), child_path, target)
                        candidates.append((pointer, path, node))
                        return
            if "items" in node and target.startswith(path + "[*]"):
                trace(node["items"], pointer + "/items", path + "[*]", target)
            candidates.append((pointer, path, node))
        for target in wanted[:32]:
            trace(root, "", "$", target)
        facts: list[dict[str, object]] = []
        seen: set[tuple[str, str]] = set()
        used_bytes = 0
        for pointer, path, node in candidates:
            if (pointer, path) in seen:
                continue
            seen.add((pointer, path))
            expected = {name: node[name] for name in ("type", "required", "additionalProperties",
                        "minItems", "maxItems", "minLength", "maxLength", "minimum", "maximum") if name in node}
            if type(node.get("properties")) is dict:
                expected["allowed_fields"] = tuple(node["properties"])
            for name in ("enum", "pattern"):
                if name in node:
                    value = node[name]
                    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                    if len(encoded) <= 4096:
                        expected[name] = value
                    else:
                        expected[name + "_rule"] = {"schema_pointer": pointer + "/" + name,
                                                     "sha256": hashlib.sha256(encoded).hexdigest()}
            if type(node.get("anyOf")) is list:
                expected["relationship"] = {"operator": "ANY_OF_SAME_GRAPH_ALTERNATIVE",
                    "schema_pointer": pointer + "/anyOf", "alternatives": len(node["anyOf"])}
            fact = {"path": path, "schema_pointer": pointer,
                    "rule": "SELECTED_SCHEMA_RULE",
                    "applies_within": "ONE_COMPLETE_SELECTED_GRAPH_ALTERNATIVE", "expected": expected}
            size = len(json.dumps(fact, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            if len(facts) >= 32 or used_bytes + size > 32000:
                break
            facts.append(fact)
            used_bytes += size
        # This reference always retains the full coupled rule, including facts
        # outside the diagnostic budget. The selected graph was already offered.
        facts.append({"path": "$", "schema_pointer": "", "rule": "COMPLETE_SELECTED_GRAPH",
                      "expected": {"sha256": hashlib.sha256(self.canonical_bytes).hexdigest(),
                                   "relationship": "VALIDATE_ALL_FIELDS_IN_ONE_SELECTED_GRAPH"}})
        return tuple(facts)

    def require(self, value: object) -> None:
        findings = self.findings(value)
        if findings:
            failure = ValueError("declared schema rejected")
            failure.provider_validation_findings = tuple(
                ValidationFinding(*item.split(": ", 1)) for item in findings
            )
            raise failure

    def _property_rule(self, path: tuple[str, ...], *, resolve_terminal: bool = True) -> tuple[dict[str, object], Mapping[str, object]]:
        if type(path) is not tuple or any(type(name) is not str for name in path):
            raise ValueError("invalid schema property path")
        root = self.schema_value()
        node: Mapping[str, object] = root
        for name in (*path, None):
            if name is None and not resolve_terminal:
                break
            seen: set[str] = set()
            while "$ref" in node:
                reference = node["$ref"]
                if type(reference) is not str or reference in seen:
                    raise ValueError("cyclic schema property reference")
                seen.add(reference)
                node = _local_rule(root, reference)
            if name is not None:
                properties = node.get("properties")
                if type(properties) is not dict or name not in properties:
                    raise ValueError("schema property is not declared")
                node = properties[name]
        return root, node

    def property_names(self, path: tuple[str, ...] = ()) -> tuple[str, ...]:
        """Return declared closed-object property names, never instance keys."""
        _, node = self._property_rule(path)
        properties = node.get("properties")
        if type(properties) is not dict:
            raise ValueError("schema property is not an object")
        return tuple(sorted(properties))

    def literal_at(self, path: tuple[str, ...]) -> object:
        """Read an exact singleton literal from a declared property rule."""
        _, node = self._property_rule(path)
        values = node.get("enum")
        if type(values) is not list or len(values) != 1:
            raise ValueError("schema property does not declare one literal")
        return values[0]

    def node_fingerprint(self, path: tuple[str, ...] = ()) -> str:
        """Memoize the exact reachable rule graph without expanding local refs.

        Definition placement does not change a node's rules. All definition
        nodes are still checked; unknown or cyclic references never disappear
        merely because a selected node does not use them.
        """
        root, selected = self._property_rule(path, resolve_terminal=False)
        return schema_rule_fingerprints(root, selected=selected)[0]



def schema_accepts_only_objects(
    schema: Mapping[str, object], root: Mapping[str, object] | None = None,
) -> bool:
    root = schema if root is None else root
    if "$ref" in schema:
        return schema_accepts_only_objects(_local_rule(root, schema["$ref"]), root)
    variants = schema.get("anyOf")
    if type(variants) is list:
        return bool(variants) and all(
            isinstance(child, Mapping) and schema_accepts_only_objects(child, root)
            for child in variants
        )
    return schema.get("type") == "object"


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
    if type(value) in {float, Decimal}:
        return "number"
    if type(value) is str:
        return "string"
    if type(value) is list:
        return "array"
    if type(value) is dict:
        return "object"
    return "non-JSON value"


def _finite_number(value: object) -> bool:
    if type(value) is int:
        return True
    if type(value) is Decimal:
        return value.is_finite()
    return type(value) is float and math.isfinite(value)


def _matches_type(value: object, expected: str) -> bool:
    if expected in {"integer", "number"}:
        if not _finite_number(value):
            return False
        integral = (type(value) is int or
                    (type(value) is Decimal and value == value.to_integral_value()) or
                    (type(value) is float and value.is_integer()))
        return expected == "number" or integral
    return json_type(value) == expected


def _same_json_scalar(left: object, right: object) -> bool:
    if type(left) in {int, float, Decimal} and type(right) in {int, float, Decimal}:
        return _matches_type(left, "number") and _matches_type(right, "number") and Decimal(str(left)) == Decimal(str(right))
    return type(left) is type(right) and left == right


def _issue(path: str, problem: str) -> str:
    return f"{path}: {problem}"


def allowed_values(values: Sequence[object]) -> str:
    """Render bounded choices from the trusted contract, never supplied values."""
    rendered = [json.dumps(item, ensure_ascii=True) for item in values[:32]]
    choices = ", ".join(item if len(item) <= 160 else item[:157] + "..." for item in rendered)
    if len(values) > 32:
        choices += f", ... ({len(values) - 32} additional allowed values)"
    return choices


_WIRE_DISCRIMINATORS = (
    "response_type", "action_type", "change_type", "update_type", "review_outcome",
)


def _diagnostic_schema_node(
    node: object, root_schema: Mapping[str, object],
) -> Mapping[str, object] | None:
    seen: set[str] = set()
    while isinstance(node, Mapping) and "$ref" in node:
        reference = node["$ref"]
        target = _local_rule(root_schema, reference)
        if reference in seen:
            raise validation_failure(
                ValueError("schema rejected"), "$", "expected acyclic local definition reference",
            )
        seen.add(reference)
        node = target
    return node if isinstance(node, Mapping) else None


def _discriminator_schema(
    variant: object, discriminator: str, root_schema: Mapping[str, object],
) -> Mapping[str, object] | None:
    variant = _diagnostic_schema_node(variant, root_schema)
    if variant is None:
        return None
    properties = variant.get("properties")
    if not isinstance(properties, Mapping):
        return None
    return _diagnostic_schema_node(properties.get(discriminator), root_schema)


def _diagnose(
    value: object, schema: Mapping[str, object], path: str,
    root_schema: Mapping[str, object] | None = None,
) -> Iterator[str]:
    root_schema = schema if root_schema is None else root_schema
    if "$ref" in schema:
        yield from _diagnose(value, _local_rule(root_schema, schema["$ref"]), path, root_schema)
        return
    variants = schema.get("anyOf")
    if type(variants) is list and variants:
        if type(value) is dict:
            for discriminator in (*_WIRE_DISCRIMINATORS, "kind", "action", "decision", "operation"):
                if discriminator not in value:
                    continue
                supplied = value[discriminator]
                candidates = [
                    child
                    for variant in variants
                    if (child := _discriminator_schema(variant, discriminator, root_schema)) is not None
                    and type(child.get("enum")) is list
                ]
                if len(candidates) != len(variants):
                    continue
                matches = (
                    [
                        variant for variant in variants
                        if (child := _discriminator_schema(variant, discriminator, root_schema)) is not None
                        and supplied in child.get("enum", ())
                    ]
                    if type(supplied) is str else []
                )
                if len(matches) == 1:
                    yield from _diagnose(value, matches[0], path, root_schema)
                    return
                if len(matches) > 1 and all("$ref" not in variant for variant in matches):
                    exact_shapes = [
                        variant for variant in matches
                        if isinstance(variant, Mapping)
                        and isinstance(variant.get("properties"), Mapping)
                        and value.keys() == variant["properties"].keys()
                    ]
                    if len(exact_shapes) == 1:
                        yield from _diagnose(value, exact_shapes[0], path, root_schema)
                        return
                if candidates and not matches:
                    yield _issue(
                        _path(path, discriminator),
                        "expected one of [" + allowed_values(tuple(dict.fromkeys(
                            item for candidate in candidates for item in candidate["enum"]
                        ))) + f"], got {json_type(supplied)}",
                    )
                    resolved_variants = tuple(
                        _diagnostic_schema_node(variant, root_schema) for variant in variants
                    )
                    properties = [
                        variant.get("properties")
                        for variant in resolved_variants
                        if isinstance(variant, Mapping)
                        and isinstance(variant.get("properties"), Mapping)
                    ]
                    if len(properties) == len(variants):
                        common = set.intersection(*(set(item) for item in properties))
                        # Current wire names must not change the established
                        # missing-context-before-invalid-value diagnostic order.
                        # Keep domain-schema ordering unchanged.
                        names = (
                            sorted(common, key=lambda name: (name in value, name))
                            if discriminator in _WIRE_DISCRIMINATORS
                            else sorted(common)
                        )
                        for name in names:
                            if name == discriminator:
                                continue
                            child = properties[0][name]
                            if any(item[name] != child for item in properties[1:]):
                                if name in value and all(
                                    isinstance(item[name], Mapping) for item in properties
                                ):
                                    field_path = _path(path, name)
                                    findings = tuple(islice(
                                        _diagnose(value[name], child, field_path, root_schema),
                                        _MAX_ISSUES + 1,
                                    ))
                                    overflow = (
                                        len(findings) > _MAX_ISSUES
                                        or _OMITTED_ISSUES_NOTICE in findings
                                    )
                                    for item in properties[1:]:
                                        other = tuple(islice(
                                            _diagnose(
                                                value[name], item[name], field_path, root_schema,
                                            ),
                                            _MAX_ISSUES + 1,
                                        ))
                                        overflow |= (
                                            len(other) > _MAX_ISSUES
                                            or _OMITTED_ISSUES_NOTICE in other
                                        )
                                        common_findings = set(other)
                                        findings = tuple(
                                            finding for finding in findings
                                            if finding in common_findings
                                        )
                                    yield from findings
                                    if overflow and _OMITTED_ISSUES_NOTICE not in findings:
                                        yield _OMITTED_ISSUES_NOTICE
                                continue
                            required_everywhere = all(
                                name in variant.get("required", ())
                                for variant in resolved_variants
                            )
                            if name not in value and required_everywhere:
                                yield _issue(
                                    _path(path, name), "required property is missing",
                                )
                            elif name in value and isinstance(child, Mapping):
                                yield from _diagnose(
                                    value[name], child, _path(path, name),
                                root_schema, )
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
                    _diagnose(value, variant, path, root_schema), _MAX_ISSUES + 1,
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
    if (type(value) in {float, Decimal} and not _finite_number(value)
            and (not expected or any(item in {"integer", "number"} for item in expected))):
        yield _issue(path, "expected a finite number, got number")
        return
    if expected and not any(_matches_type(value, item) for item in expected):
        yield _issue(path, f"expected {' or '.join(expected)}, got {json_type(value)}")
        return

    allowed = schema.get("enum")
    if type(allowed) is list and not any(
        _same_json_scalar(value, item) for item in allowed
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
                    yield from _diagnose(value[name], child, _path(path, name), root_schema)
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
                yield from _diagnose(item, item_schema, f"{path}[{index}]", root_schema)
    elif type(value) is str:
        pattern = schema.get("pattern")
        if type(pattern) is str and re.search(pattern, value) is None:
            yield _issue(path, f"expected pattern {json.dumps(pattern, ensure_ascii=True)}")
        minimum = schema.get("minLength")
        maximum = schema.get("maxLength")
        if type(minimum) is int and len(value) < minimum:
            yield _issue(path, f"expected at least {minimum} characters, got {len(value)}")
        if type(maximum) is int and len(value) > maximum:
            yield _issue(path, f"expected at most {maximum} characters, got {len(value)}")
    elif type(value) in {int, float, Decimal}:
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if type(minimum) in {int, float} and Decimal(str(value)) < Decimal(str(minimum)):
            yield _issue(path, f"expected at least {minimum}, got {json_type(value)}")
        if type(maximum) in {int, float} and Decimal(str(value)) > Decimal(str(maximum)):
            yield _issue(path, f"expected at most {maximum}, got {json_type(value)}")


def diagnose_value(value: object, schema: Mapping[str, object]) -> tuple[str, ...]:
    """Collect a bounded ordered prefix of independently detectable issues."""

    issues = tuple(islice(_diagnose(value, schema, "$"), _MAX_ISSUES + 1))
    if len(issues) > _MAX_ISSUES:
        return (*issues[:_MAX_ISSUES], _OMITTED_ISSUES_NOTICE)
    return issues

def schema_fragment_findings(
    value: object, root_schema: Mapping[str, object], schema_path: tuple[str | int, ...],
) -> tuple[str, ...]:
    """Validate a checked declaration fragment with its same-document references."""
    if type(root_schema) is not dict or type(schema_path) is not tuple:
        raise TypeError("fragment validation requires a declaration and schema path")
    schema = root_schema
    for part in schema_path:
        if type(part) not in {str, int}:
            raise TypeError("schema path requires literal keys or alternative ordinals")
        schema = schema[part]
    if type(schema) is not dict:
        raise TypeError("schema path does not identify a declared rule")
    issues = tuple(islice(_diagnose(value, schema, "$", root_schema), _MAX_ISSUES + 1))
    if len(issues) > _MAX_ISSUES:
        return (*issues[:_MAX_ISSUES], _OMITTED_ISSUES_NOTICE)
    return issues



def diagnose_json_text(text: str, schema: Mapping[str, object]) -> tuple[str, ...]:
    """Parse strict JSON and report syntax, duplicate-key, and schema issues."""

    try:
        value = json.loads(
            text, object_pairs_hook=_unique_object, parse_constant=_reject_constant,
            parse_float=Decimal,
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




_CONTRACT_INPUT_SEAL = object()
_ContractValue = TypeVar("_ContractValue")


def _hook_default_identity(
    value: object, seen: set[int], *,
    _digests: tuple[dict[int, tuple[object, str]], dict[int, tuple[object, str]]] | None = None,
    _attest_captures: bool = False,
) -> object:
    if _digests is None:
        _digests = ({}, {})
    if value is None or type(value) in {str, int, bool}:
        return (type(value).__name__, value)
    if type(value) is bytes:
        cached_bytes = _digests[1].get(id(value))
        if cached_bytes is None or cached_bytes[0] is not value:
            cached_bytes = (value, hashlib.sha256(value).hexdigest())
            _digests[1][id(value)] = cached_bytes
        return ("bytes", cached_bytes[1])
    if type(value) in {float, Decimal}:
        if not (value.is_finite() if type(value) is Decimal else math.isfinite(value)):
            raise TypeError("hook defaults cannot contain nonfinite values")
        return (type(value).__name__, str(value))
    if isinstance(value, Enum):
        return ("enum", type(value).__module__, type(value).__qualname__, _hook_default_identity(
            value.value,
            seen,
            _digests=_digests,
            _attest_captures=_attest_captures,
        ))
    if id(value) in seen:
        raise TypeError("hook default declarations cannot be cyclic")
    seen.add(id(value))
    try:
        if type(value) in {tuple, list}:
            return (type(value).__name__, tuple(_hook_default_identity(
                item,
                seen,
                _digests=_digests,
                _attest_captures=_attest_captures,
            ) for item in value))
        if isinstance(value, Mapping):
            pairs = [(_hook_default_identity(
                key,
                seen,
                _digests=_digests,
                _attest_captures=_attest_captures,
            ), _hook_default_identity(
                item,
                seen,
                _digests=_digests,
                _attest_captures=_attest_captures,
            )) for key, item in value.items()]
            return ("mapping", sorted(pairs, key=lambda pair: json.dumps(pair[0], sort_keys=True)))
        if type(value) in {set, frozenset}:
            return (type(value).__name__, sorted((_hook_default_identity(
                item,
                seen,
                _digests=_digests,
                _attest_captures=_attest_captures,
            ) for item in value),
                                                key=lambda item: json.dumps(item, sort_keys=True)))
        if inspect.isfunction(value) or inspect.ismethod(value):
            return ("function", _permitted_hook_identity(
                value,
                seen,
                _digests=_digests,
                _attest_captures=_attest_captures,
            ))
        if is_dataclass(value) and getattr(type(value), "__dataclass_params__").frozen:
            declared_fields = fields(value)
            if _attest_captures:
                declaration = vars(type(value))
                field_names = {item.name for item in declared_fields}
                slot_names = [
                    name for base in type(value).__mro__
                    for name, descriptor in vars(base).items()
                    if inspect.ismemberdescriptor(descriptor)
                ]
                if ("__dataclass_fields__" not in declaration
                        or "__dataclass_params__" not in declaration
                        or hasattr(value, "__dict__")
                        or any(name not in field_names | {"__weakref__"} for name in slot_names)
                        or len(slot_names) != len(set(slot_names))):
                    raise TypeError("typed decoder dataclass captures require only declared slot fields")
            return ("frozen_dataclass", type(value).__module__, type(value).__qualname__,
                    tuple((item.name, _hook_default_identity(
                        getattr(value, item.name),
                        seen,
                        _digests=_digests,
                        _attest_captures=_attest_captures,
                    )) for item in declared_fields))
        raise TypeError("unsupported semantic hook default declaration")
    finally:
        seen.remove(id(value))


def _permitted_hook_identity(
    callback: Callable, seen: set[int] | None = None, *,
    _digests: tuple[dict[int, tuple[object, str]], dict[int, tuple[object, str]]] | None = None,
    _attest_captures: bool = False,
) -> str:
    """Derived content-free actual hook provenance, never a caller-supplied pin."""
    if _digests is None:
        _digests = ({}, {})
    if inspect.ismethod(callback):
        function, receiver = callback.__func__, callback.__self__
    elif inspect.isfunction(callback):
        function, receiver = callback, None
    else:
        raise TypeError("owner hooks require a Python function or bound method")
    if (inspect.iscoroutinefunction(function) or inspect.isgeneratorfunction(function)
            or inspect.isasyncgenfunction(function)):
        raise TypeError("owner hooks must be synchronous nongenerator functions")
    code_object = function.__code__
    cached_code = _digests[0].get(id(code_object))
    if cached_code is None or cached_code[0] is not code_object:
        code = hashlib.sha256(marshal.dumps(code_object, 2)).hexdigest()
        immutable_constants = True
        try:
            pending_constants = [(code_object, False)]
            active_constants: set[int] = set()
            complete_constants: set[int] = set()
            code_type = type(code_object)
            while pending_constants:
                value, leaving = pending_constants.pop()
                if leaving:
                    active_constants.remove(id(value))
                    complete_constants.add(id(value))
                    continue
                if value is None or value is Ellipsis or value is StopIteration:
                    continue
                kind = type(value)
                if kind in {bool, int, float, complex, str, bytes}:
                    continue
                if kind is code_type:
                    children = (value.co_consts,)
                elif kind in {tuple, frozenset}:
                    children = value
                else:
                    immutable_constants = False
                    break
                identity = id(value)
                if identity in active_constants:
                    immutable_constants = False
                    break
                if identity in complete_constants:
                    continue
                active_constants.add(identity)
                pending_constants.append((value, True))
                pending_constants.extend((child, False) for child in children)
        except (MemoryError, RecursionError):
            immutable_constants = False
        if immutable_constants:
            _digests[0][id(code_object)] = (code_object, code)
    else:
        code = cached_code[1]
    declaration_seen = set() if seen is None else seen
    defaults = _hook_default_identity(
        (function.__defaults__, function.__kwdefaults__),
        declaration_seen,
        _digests=_digests,
        _attest_captures=_attest_captures,
    )
    provenance = (function.__module__, function.__qualname__, id(function),
                  None if receiver is None else id(receiver), code, defaults)
    if _attest_captures:
        try:
            captures = tuple((name, cell.cell_contents) for name, cell in zip(
                code_object.co_freevars, function.__closure__ or (), strict=True,
            ))
        except ValueError as exc:
            raise TypeError("typed decoder closures must contain bound values") from exc
        provenance += (
            _hook_default_identity(
                captures, declaration_seen, _digests=_digests, _attest_captures=True,
            ),
            _hook_default_identity(
                receiver, declaration_seen, _digests=_digests, _attest_captures=True,
            ),
        )
    return hashlib.sha256(json.dumps(provenance, separators=(",", ":")).encode("utf-8")).hexdigest()


def _require_hook_arity(callback: Callable, count: int) -> None:
    _permitted_hook_identity(callback)
    try:
        inspect.signature(callback).bind(*(None,) * count)
    except (TypeError, ValueError):
        raise TypeError("owner hook does not accept its declared typed inputs") from None


@dataclass(frozen=True, slots=True)
class TypedDecoder(Generic[_ContractValue]):
    """The sole permitted typed hook with centrally derived immutable provenance."""
    name: str
    revision: str
    decode: Callable[[ValidatedValue], _ContractValue] = field(repr=False, compare=False)
    dependencies: tuple[Callable, ...] = field(default=(), repr=False, compare=False)
    binding_fingerprint: str = field(init=False)
    _hook_fingerprints: tuple[str, ...] = field(init=False, repr=False)

    def __init_subclass__(cls, **kwargs):
        raise TypeError("typed decoder declarations cannot be overridden")

    def __post_init__(self) -> None:
        if (type(self.name) is not str or not self.name or type(self.revision) is not str
                or not self.revision or type(self.dependencies) is not tuple):
            raise ValueError("incomplete typed decoder declaration")
        _require_hook_arity(self.decode, 1)
        identities = tuple(_permitted_hook_identity(
            item, _attest_captures=True,
        ) for item in (self.decode, *self.dependencies))
        fingerprint = hashlib.sha256(json.dumps((self.name, self.revision, identities),
                                               separators=(",", ":")).encode("utf-8")).hexdigest()
        object.__setattr__(self, "_hook_fingerprints", identities)
        object.__setattr__(self, "binding_fingerprint", fingerprint)

    def require_provenance(self) -> None:
        digests = ({}, {})
        if tuple(_permitted_hook_identity(
            item,
            _digests=digests,
            _attest_captures=True,
        ) for item in (self.decode, *self.dependencies)) != self._hook_fingerprints:
            raise ValueError("frozen typed decoder hook provenance changed")

    @staticmethod
    def require_hook_shape(callback: Callable, input_count: int) -> None:
        _require_hook_arity(callback, input_count)


class HostCheckContext(ABC):
    """Typed context for a named nonstructural owner check; never authority itself."""
    __slots__ = ()


@dataclass(frozen=True, slots=True)
class SelectedHostContext(HostCheckContext):
    """Input-only context used before any service execution/authorization."""
    pass


@dataclass(frozen=True, slots=True)
class NamedHostCheck(Generic[_ContractValue]):
    """Only a named genuine owner predicate can extend the frozen graph path."""
    name: str
    revision: str
    phase: str
    context_type: type[HostCheckContext]
    corrective_facts: bytes
    check: Callable[[_ContractValue, HostCheckContext], None] = field(repr=False, compare=False)
    dependencies: tuple[Callable, ...] = field(default=(), kw_only=True, repr=False, compare=False)
    binding_fingerprint: str = field(init=False)
    _hook_fingerprints: tuple[str, ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if (type(self.name) is not str or not self.name
                or type(self.revision) is not str or not self.revision
                or self.phase not in {"selected", "authorized"}
                or not isinstance(self.context_type, type)
                or not issubclass(self.context_type, HostCheckContext)
                or not callable(self.check) or inspect.iscoroutinefunction(self.check)
                or inspect.iscoroutinefunction(getattr(self.check, "__call__", None))
                or type(self.corrective_facts) is not bytes or len(self.corrective_facts) > 32_000):
            raise ValueError("incomplete named owner check")
        _require_hook_arity(self.check, 2)
        if type(self.dependencies) is not tuple:
            raise TypeError("host hook dependencies must be immutable declarations")
        identities = tuple(_permitted_hook_identity(item) for item in (self.check, *self.dependencies))
        object.__setattr__(self, "_hook_fingerprints", identities)
        object.__setattr__(self, "binding_fingerprint", hashlib.sha256(json.dumps(identities, separators=(",", ":")).encode("utf-8")).hexdigest())
        value = strict_json_object(self.corrective_facts.decode("utf-8", "strict"))
        if set(value) != {"requirements"} or type(value["requirements"]) is not list or len(value["requirements"]) > 32:
            raise ValueError("unbounded named owner requirements")
        for item in value["requirements"]:
            if (type(item) is not dict or set(item) != {"path", "rule", "expected"}
                    or type(item["path"]) is not str or not item["path"].startswith("$")
                    or type(item["rule"]) is not str or not item["rule"]
                    or type(item["expected"]) is not dict):
                raise ValueError("incomplete named owner requirement")
        if json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":")).encode("utf-8") != self.corrective_facts:
            raise ValueError("noncanonical named owner requirements")


    def require_provenance(self) -> None:
        digests = ({}, {})
        if tuple(_permitted_hook_identity(
            item,
            _digests=digests,
        ) for item in (self.check, *self.dependencies)) != self._hook_fingerprints:
            raise ValueError("frozen named owner hook provenance changed")


class ContractOwner(Generic[_ContractValue], ABC):
    """Declare one authoritative graph and only typed decode/named host hooks."""
    owner_id: str
    owner_revision: str

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        prohibited = {"schema", "schema_value", "validate", "validate_authorized", "repair",
            "repair_input", "corrective_input", "decode_and_validate"}
        if prohibited.intersection(cls.__dict__):
            raise TypeError("owners cannot override bound contract relationships")

    @abstractmethod
    def declare_rules(self) -> SchemaRules:
        """Return the owner-declared complete graph; binding captures it once."""
        raise NotImplementedError

    @abstractmethod
    def typed_decoder(self) -> TypedDecoder[_ContractValue]:
        """Declare the sole typed hook captured by the concrete bound path."""
        raise NotImplementedError

    @abstractmethod
    def named_host_checks(self) -> tuple[NamedHostCheck[_ContractValue], ...]:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class ContractIdentity:
    """Canonical source-owned binding policy; no independently supplied schema ID."""
    canonical_bytes: bytes
    maximum_schema_bytes: int
    wire_root_path: str

    def __post_init__(self) -> None:
        if (type(self.canonical_bytes) is not bytes
                or self.maximum_schema_bytes not in {65_536, 262_144}
                or self.wire_root_path not in {"$", "$.agent_action", "$.agent_action.task_result"}):
            raise ValueError("incomplete contract identity")
        value = strict_json_object(self.canonical_bytes.decode("utf-8", "strict"))
        if json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":")).encode("utf-8") != self.canonical_bytes:
            raise ValueError("noncanonical contract identity")


class ContractRejection(ValueError):
    """Closed diagnostic codes and trusted schema/owner paths only."""
    def __init__(self, code: str, schema_id: str | None, requirements: tuple[str, ...]) -> None:
        self.code, self.schema_id, self.requirements = code, schema_id, requirements
        self.provider_validation_findings = tuple(ValidationFinding(*item.split(": ", 1))
            for item in requirements if ": " in item)
        super().__init__(code)


@dataclass(frozen=True, slots=True, init=False)
class ValidatedValue:
    """Issued by BoundContract only after selected complete-graph validation."""
    _body: dict[str, object] = field(repr=False, compare=False)
    _contract: BoundContract = field(repr=False, compare=False)

    def __init__(self, body: dict[str, object], contract: BoundContract, *, seal=None):
        if seal is not _CONTRACT_INPUT_SEAL:
            raise TypeError("validated inputs are issued by a bound contract")
        object.__setattr__(self, "_body", deepcopy(body))
        object.__setattr__(self, "_contract", contract)

    def as_object(self) -> dict[str, object]:
        return deepcopy(self._body)


@dataclass(frozen=True, slots=True, init=False)
class SelectedContract(Generic[_ContractValue]):
    """Immutable typed input/provenance issued by one concrete decoding path."""
    schema_id: str
    value: _ContractValue
    _contract: BoundContract[_ContractValue] = field(repr=False, compare=False)

    def __init__(self):
        raise TypeError("selected inputs are issued by a bound contract")

    def belongs_to(self, contract: BoundContract[_ContractValue]) -> bool:
        return self._contract is contract and self.schema_id == contract.schema_id


@dataclass(frozen=True, slots=True)
class BoundContract(Generic[_ContractValue]):
    """Sealed schema/selection/decode/repair relationship from one frozen graph."""
    owner: ContractOwner[_ContractValue] = field(repr=False, compare=False)
    identity: ContractIdentity
    schema_id: str = field(init=False)
    rule_graph: SchemaRules = field(init=False)
    rule_binding_digest: str = field(init=False)
    host_checks: tuple[NamedHostCheck[_ContractValue], ...] = field(init=False, repr=False, compare=False)
    _source_graph: SchemaRules = field(init=False, repr=False, compare=False)
    _decode: TypedDecoder[_ContractValue] = field(init=False, repr=False, compare=False)
    _declaration_methods: tuple[object, ...] = field(init=False, repr=False, compare=False)

    def __init_subclass__(cls, **kwargs):
        raise TypeError("BoundContract relationships cannot be overridden")

    def __post_init__(self) -> None:
        if not isinstance(self.owner, ContractOwner) or type(self.identity) is not ContractIdentity:
            raise TypeError("a concrete bound contract requires its typed owner and identity")
        graph = self.owner.declare_rules()
        checks = self.owner.named_host_checks()
        decoder = self.owner.typed_decoder()
        if (type(graph) is not SchemaRules or graph.maximum_bytes != self.identity.maximum_schema_bytes
                or type(checks) is not tuple or any(type(item) is not NamedHostCheck for item in checks)
                or len({item.name for item in checks}) != len(checks)
                or any(type(value) is not str or not value for value in (self.owner.owner_id, self.owner.owner_revision))
                or type(decoder) is not TypedDecoder):
            raise ValueError("incomplete authoritative contract owner")
        schema = graph.schema_value()
        if schema.get("type") != "object" or "schema_id" in schema.get("properties", {}):
            raise ValueError("owner must declare one complete closed identity-free object")
        policy = strict_json_object(self.identity.canonical_bytes.decode("utf-8"))
        digest = hashlib.sha256(graph.canonical_bytes).hexdigest()
        provenance = {"policy": policy, "fingerprint_format": "swa-annotation-rules-v2",
            "owner": [self.owner.owner_id, self.owner.owner_revision,
            type(self.owner).__module__, type(self.owner).__qualname__], "rules": digest,
            "checks": [(item.name, item.revision, item.phase, item.context_type.__module__,
                item.context_type.__qualname__, hashlib.sha256(item.corrective_facts).hexdigest(), item.binding_fingerprint) for item in checks],
            "typed_decoder": (decoder.name, decoder.revision, decoder.binding_fingerprint),
            "repair": "848-v3:single-frozen-graph:32facts:32000bytes"}
        schema_id = "lead_v11_" + hashlib.sha256(json.dumps(provenance, sort_keys=True,
            ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")).hexdigest()[:32]
        schema["properties"]["schema_id"] = {"type": "string", "enum": [schema_id]}
        schema["required"].append("schema_id")
        object.__setattr__(self, "schema_id", schema_id)
        object.__setattr__(self, "rule_graph", SchemaRules.declare(schema, maximum_bytes=self.identity.maximum_schema_bytes))
        object.__setattr__(self, "rule_binding_digest", digest)
        object.__setattr__(self, "host_checks", checks)
        object.__setattr__(self, "_source_graph", graph)
        object.__setattr__(self, "_decode", decoder)
        object.__setattr__(self, "_declaration_methods", tuple(getattr(type(self.owner), name)
            for name in ("declare_rules", "typed_decoder", "named_host_checks")))

    @classmethod
    def bind(cls, owner: ContractOwner[_ContractValue], identity: ContractIdentity) -> BoundContract[_ContractValue]:
        return cls(owner, identity)

    def require_owner_provenance(self) -> None:
        if (tuple(getattr(type(self.owner), name) for name in ("declare_rules", "typed_decoder", "named_host_checks"))
                != self._declaration_methods
                or self.rule_binding_digest != hashlib.sha256(self._source_graph.canonical_bytes).hexdigest()
                or self.rule_graph.literal_at(("schema_id",)) != self.schema_id):
            raise ValueError("frozen contract owner provenance changed")
        self._decode.require_provenance()
        for check in self.host_checks:
            check.require_provenance()

    def schema_value(self) -> dict[str, object]:
        self.require_owner_provenance()
        return self.rule_graph.schema_value()

    def decode_selected(self, raw: object) -> SelectedContract[_ContractValue]:
        self.require_owner_provenance()
        try:
            value = strict_json_object(raw, exact_numbers=True) if type(raw) is str else raw
        except ValueError as failure:
            raise ContractRejection(
                "LEAD_JSON_REJECTED", None,
                failure_issues(failure) or ("$: expected strict JSON object",),
            ) from None
        if type(value) is not dict or value.get("schema_id") != self.schema_id:
            raise ContractRejection("LEAD_SCHEMA_ID_REJECTED", None, ("$.schema_id: required offered string identity",))
        requirements = self.rule_graph.findings(value)
        if requirements:
            raise ContractRejection("LEAD_SCHEMA_RULE_REJECTED", self.schema_id, requirements)
        body = deepcopy(value)
        del body["schema_id"]
        try:
            typed = self._decode.decode(ValidatedValue(body, self, seal=_CONTRACT_INPUT_SEAL))
            result = object.__new__(SelectedContract)
            object.__setattr__(result, "schema_id", self.schema_id)
            object.__setattr__(result, "value", typed)
            object.__setattr__(result, "_contract", self)
            self.require_host_checks(result, "selected", SelectedHostContext())
            return result
        except ContractRejection:
            raise
        except Exception as failure:
            findings = tuple(item.path + ": " + item.requirement for item in failure_findings(failure))
            raise ContractRejection("LEAD_OWNER_RULE_REJECTED", self.schema_id,
                findings or ("$: rejected by bound typed/domain/source owner check",)) from None

    def require_host_checks(self, selected: SelectedContract[_ContractValue], phase: str,
                            context: HostCheckContext) -> None:
        self.require_owner_provenance()
        if type(selected) is not SelectedContract or not selected.belongs_to(self) or phase not in {"selected", "authorized"}:
            raise TypeError("host checks require the exact selected typed contract")
        for check in self.host_checks:
            if check.phase == phase:
                if type(context) is not check.context_type:
                    raise TypeError("named owner check requires its exact context type")
                result = check.check(selected.value, context)
                if result is not None:
                    raise TypeError("named owner checks must return None")

    def corrective_requirements(self, requirements: tuple[str, ...]) -> dict[str, object]:
        self.require_owner_provenance()
        def physical(fact: dict[str, object]) -> dict[str, object]:
            return dict(fact, path=self.identity.wire_root_path + str(fact["path"])[1:])
        return {"binding_id": "lead.selected_rule_repair", "revision": "848-v3:single-frozen-graph",
            "rule_digest": hashlib.sha256(self.rule_graph.canonical_bytes).hexdigest(),
            "wire_root_path": self.identity.wire_root_path,
            "schema_requirements": tuple(physical(fact) for fact in self.rule_graph.corrective_facts(requirements)),
            "owner_requirements": tuple({"binding_id": check.name, "revision": check.revision,
                "phase": check.phase, "rule_digest": self.rule_binding_digest,
                "facts": tuple(physical(fact) for fact in strict_json_object(check.corrective_facts.decode("utf-8"))["requirements"])}
                for check in self.host_checks)}
