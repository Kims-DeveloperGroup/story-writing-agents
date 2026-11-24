"""Own typed validation findings and bounded diagnostic chunks."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from itertools import islice
from typing import TypeVar

_MAX_CHUNK_BYTES = 32_000
MAX_VALIDATION_ISSUES = 2_048
OMITTED_VALIDATION_ISSUES_NOTICE = "$: additional validation issues omitted"
ValidationFailureT = TypeVar("_Failure", bound=BaseException)


@dataclass(frozen=True, slots=True)
class ValidationFinding:
    """A source-authored rule and path, never an exception's arbitrary text."""

    path: str
    requirement: str


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


def prefix_validation(failure: ValidationFailureT, path: str) -> ValidationFailureT:
    """Add a provider envelope to a source-owned property finding."""
    findings = failure_findings(failure)
    if findings:
        failure.provider_validation_findings = tuple(
            ValidationFinding(path + item.path[1:], item.requirement)
            for item in findings
        )
    return failure


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


def bounded_chunks(
    issues: Sequence[str], *, prefix: str = "Structured reply validation"
) -> tuple[str, ...]:
    """Package ordered safe issues into separately bounded display strings."""

    if not issues:
        return ()
    if len(issues) > MAX_VALIDATION_ISSUES and not (
        len(issues) == MAX_VALIDATION_ISSUES + 1
        and issues[-1] == OMITTED_VALIDATION_ISSUES_NOTICE
    ):
        issues = (*islice(issues, MAX_VALIDATION_ISSUES), OMITTED_VALIDATION_ISSUES_NOTICE)
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
