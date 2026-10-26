"""Pure specialist ownership for explicit, versioned story-authoring facts.

These facts describe work, not evidence or execution authority. Callers own
current-task correlation, candidate availability, reads, and confirmation.
"""

from __future__ import annotations

import re
import unicodedata
from itertools import combinations, product
from .structured_output_validation import (
    ValidationFinding,
    unicode_scalar_classes,
    validation_failure,
    validation_call,
)
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import PurePosixPath


AUTHORING_SCOPE_SCHEMA_VERSION = 3
REVIEW_BASIS_SCHEMA_VERSION = 1
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")


class ArtifactReviewDomain(StrEnum):
    """Canonical ownership domains shared by authoring and artifact review."""

    PLOT_STRUCTURE = "plot_structure"
    CHARACTER_WORLD = "character_world"
    TONE_EXECUTION = "tone_execution"


class AuthoringIntent(StrEnum):
    """Exclusive review intent, independently of the current work activity."""

    AUTHORING = "authoring"
    REVIEW_ONLY = "review_only"


class AuthoringActivity(StrEnum):
    """The requested activity, independently of evidence acquisition."""

    WORKFLOW = "workflow"
    DRAFT = "draft"
    REVISE = "revise"
    REVIEW = "review"


class SpecialistAssignmentMode(StrEnum):
    """Whether a specialist produces material or assesses concrete material."""

    DRAFT = "draft"
    REVIEW = "review"
    CONSTRAINT_ASSESSMENT = "constraint_assessment"
    PREMISE_ANALYSIS = "premise_analysis"


class AuthoringContributionRole(StrEnum):
    """Authoring responsibility, independently of exact-candidate review."""
    DRAFT_OWNER = "draft_owner"
    CONSTRAINT_ASSESSOR = "constraint_assessor"


@dataclass(frozen=True, slots=True)
class AuthoringContribution:
    domain: ArtifactReviewDomain
    role: AuthoringContributionRole

    def __post_init__(self) -> None:
        if type(self.domain) is not ArtifactReviewDomain or type(self.role) is not AuthoringContributionRole:
            raise ValueError("authoring contribution requires an exact domain and role")


@dataclass(frozen=True, slots=True)
class AuthoringDependencySlot:
    slot_id: str
    dependent_domains: tuple[ArtifactReviewDomain, ...]

    def __post_init__(self) -> None:
        _identifier(self.slot_id)
        _domain_tuple(self.dependent_domains)
        if not self.dependent_domains:
            raise ValueError("dependency slot requires dependent authoring domains")


@dataclass(frozen=True, slots=True)
class AuthoringScopeRuleSet:
    """Pure finite duties shared by scope construction and Lead projection."""
    activity: AuthoringActivity
    intent: AuthoringIntent
    domains: tuple[ArtifactReviewDomain, ...]
    contributions: tuple[AuthoringContribution, ...]
    required_domains: tuple[ArtifactReviewDomain, ...]
    required_specialists: tuple[str, ...]
    assignment_modes: tuple[tuple[str, SpecialistAssignmentMode], ...]
    schema_version: int = AUTHORING_SCOPE_SCHEMA_VERSION

    @staticmethod
    def dependency_slot_id_rule(slot_ids: tuple[str, ...] | None = None) -> ValidationFinding | None:
        """Declare the residual ID rule, or locate its first duplicate occurrence."""
        requirement = "dependency slot IDs must be unique within one authoring scope"
        if slot_ids is None:
            return ValidationFinding("$.dependency_slots[*].slot_id", requirement)
        seen: set[str] = set()
        for index, slot_id in enumerate(slot_ids):
            if slot_id in seen:
                return ValidationFinding(f"$.dependency_slots[{index}].slot_id", requirement)
            seen.add(slot_id)
        return None

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int:
            raise TypeError("scope rule version must be an exact integer")
        if self.schema_version not in {1, 2, AUTHORING_SCOPE_SCHEMA_VERSION}:
            raise ValueError("scope rule version must be 1, 2 or 3")
        if type(self.activity) is not AuthoringActivity or type(self.intent) is not AuthoringIntent:
            raise TypeError("scope rules require exact activity and intent")
        _domain_tuple(self.domains)
        _domain_tuple(self.required_domains)
        if not set(self.domains).issubset(self.required_domains):
            raise ValueError("scope rules lose declared domains")
        canonical = tuple(domain for domain in ArtifactReviewDomain if domain in self.required_domains)
        if canonical != self.required_domains:
            raise ValueError("scope rule domains require canonical ownership order")
        if (self.activity is AuthoringActivity.WORKFLOW) != (not canonical):
            raise ValueError("scope rule activity/domain mismatch")
        if self.intent is AuthoringIntent.REVIEW_ONLY and self.activity is not AuthoringActivity.REVIEW:
            raise ValueError("review-only rule requires review activity")
        if type(self.contributions) is not tuple or any(type(row) is not AuthoringContribution for row in self.contributions):
            raise TypeError("scope rules require typed duties")
        roles = {row.domain: row.role for row in self.contributions}
        if self.contributions and (len(roles) != len(self.contributions) or set(roles) != set(canonical)):
            raise ValueError("scope rules require complete unique duties")
        if (self.contributions and self.activity in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE}
                and AuthoringContributionRole.DRAFT_OWNER not in roles.values()):
            raise ValueError("scope rules require a drafting owner")
        specialists = tuple(dict.fromkeys(_SPECIALIST_BY_DOMAIN[domain] for domain in canonical))
        modes = tuple((specialist,
            SpecialistAssignmentMode.REVIEW if self.activity is AuthoringActivity.REVIEW else
            SpecialistAssignmentMode.DRAFT if not self.contributions or any(
                _SPECIALIST_BY_DOMAIN[domain] == specialist and roles[domain] is AuthoringContributionRole.DRAFT_OWNER
                for domain in canonical) else SpecialistAssignmentMode.CONSTRAINT_ASSESSMENT)
            for specialist in specialists)
        if self.required_specialists != specialists or self.assignment_modes != modes:
            raise ValueError("scope rule duties differ from owner policy")


def authoring_scope_rules(
    activity: AuthoringActivity, intent: AuthoringIntent,
    domains: tuple[ArtifactReviewDomain, ...],
    contributions: tuple[AuthoringContribution, ...] = (), *,
    target_paths: tuple[str, ...] = (),
    dependency_slots: tuple[AuthoringDependencySlot, ...] = (),
    schema_version: int = AUTHORING_SCOPE_SCHEMA_VERSION,
) -> AuthoringScopeRuleSet:
    """Declare the existing role policy without acquiring task authority."""
    if type(activity) is not AuthoringActivity or type(intent) is not AuthoringIntent:
        raise TypeError("scope rule requires exact activity and intent")
    _domain_tuple(domains)
    if intent is AuthoringIntent.REVIEW_ONLY and activity is not AuthoringActivity.REVIEW:
        raise ValueError("review-only intent requires review activity")
    required = set(domains) | _path_domains(target_paths)
    if activity is AuthoringActivity.WORKFLOW and required:
        raise ValueError("workflow scope cannot carry story domains")
    if activity is not AuthoringActivity.WORKFLOW and not required:
        raise ValueError("story work requires an explicit affected domain")
    if type(contributions) is not tuple or any(type(item) is not AuthoringContribution for item in contributions):
        raise ValueError("contributions must be exact typed duties")
    if type(dependency_slots) is not tuple or any(type(item) is not AuthoringDependencySlot for item in dependency_slots):
        raise ValueError("dependency slots must be exact typed declarations")
    roles = {item.domain: item.role for item in contributions}
    if schema_version == AUTHORING_SCOPE_SCHEMA_VERSION:
        if len(roles) != len(contributions) or set(roles) != required:
            raise ValueError("contributions must cover every affected domain exactly once")
        specialist_roles: dict[str, AuthoringContributionRole] = {}
        for domain, role in roles.items():
            specialist = _SPECIALIST_BY_DOMAIN[domain]
            if specialist in specialist_roles and specialist_roles[specialist] is not role:
                raise ValueError("same-specialist domains require the same contribution role")
            specialist_roles[specialist] = role
        if activity in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE} and AuthoringContributionRole.DRAFT_OWNER not in roles.values():
            raise ValueError("authoring requires at least one substantive draft owner")
        slot_finding = AuthoringScopeRuleSet.dependency_slot_id_rule(tuple(item.slot_id for item in dependency_slots))
        if slot_finding is not None:
            raise validation_failure(ValueError("dependency slots must be unique"), slot_finding.path, slot_finding.requirement)
        if any(roles.get(domain) is not AuthoringContributionRole.DRAFT_OWNER
               for item in dependency_slots for domain in item.dependent_domains):
            raise ValueError("dependencies must bind declared draft owners")
    required_domains = tuple(domain for domain in ArtifactReviewDomain if domain in required)
    specialists = tuple(dict.fromkeys(_SPECIALIST_BY_DOMAIN[domain] for domain in required_domains))
    if activity is AuthoringActivity.REVIEW or intent is AuthoringIntent.REVIEW_ONLY:
        modes = tuple((agent, SpecialistAssignmentMode.REVIEW) for agent in specialists)
    elif schema_version < AUTHORING_SCOPE_SCHEMA_VERSION:
        modes = tuple((agent, SpecialistAssignmentMode.DRAFT) for agent in specialists)
    else:
        modes = tuple((agent, SpecialistAssignmentMode.DRAFT
                       if roles[next(domain for domain in required_domains if _SPECIALIST_BY_DOMAIN[domain] == agent)]
                       is AuthoringContributionRole.DRAFT_OWNER else SpecialistAssignmentMode.CONSTRAINT_ASSESSMENT)
                      for agent in specialists)
    return AuthoringScopeRuleSet(
        activity, intent, domains, contributions, required_domains, specialists, modes,
        schema_version,
    )


def assignment_rule(rules: AuthoringScopeRuleSet, specialist_id: str) -> SpecialistAssignmentMode:
    if (rules.activity is AuthoringActivity.REVIEW
            and any(specialist_id in roster for roster in legal_specialist_rosters(rules))):
        return SpecialistAssignmentMode.REVIEW
    if type(rules) is not AuthoringScopeRuleSet:
        raise TypeError("assignment rules require an immutable scope declaration")
    for agent, mode in rules.assignment_modes:
        if agent == specialist_id:
            return mode
    raise ValueError("specialist is outside the declared scope")


def legal_specialist_rosters(rules: AuthoringScopeRuleSet) -> tuple[tuple[str, ...], ...]:
    """Existing legal participation, including additional review specialists."""
    if type(rules) is not AuthoringScopeRuleSet:
        raise TypeError("rosters require a scope rule declaration")
    if (rules.schema_version == AUTHORING_SCOPE_SCHEMA_VERSION
            or rules.activity is not AuthoringActivity.REVIEW):
        return (rules.required_specialists,)
    canonical = tuple(dict.fromkeys(_SPECIALIST_BY_DOMAIN.values()))
    return tuple(
        roster for count in range(1, len(canonical) + 1)
        for roster in combinations(canonical, count)
        if set(rules.required_specialists).issubset(roster)
    )


def finite_authoring_scope_rules(
    *, intent: AuthoringIntent | None = None,
    target_paths: tuple[str, ...] = (),
) -> tuple[AuthoringScopeRuleSet, ...]:
    """All legal current finite duties; free text/identities remain separate."""
    required = _path_domains(target_paths)
    results: list[AuthoringScopeRuleSet] = []
    intents = tuple(AuthoringIntent) if intent is None else (intent,)
    for bound_intent in intents:
        activities = (AuthoringActivity.REVIEW,) if bound_intent is AuthoringIntent.REVIEW_ONLY else tuple(AuthoringActivity)
        for activity in activities:
            for count in range(len(ArtifactReviewDomain) + 1):
                for domains in combinations(tuple(ArtifactReviewDomain), count):
                    if not required.issubset(domains) or (activity is AuthoringActivity.WORKFLOW) != (not domains):
                        continue
                    for roles in product(tuple(AuthoringContributionRole), repeat=len(domains)):
                        contributions = tuple(AuthoringContribution(domain, role) for domain, role in zip(domains, roles))
                        try:
                            results.append(authoring_scope_rules(
                                activity, bound_intent, domains, contributions,
                                target_paths=target_paths,
                                schema_version=AUTHORING_SCOPE_SCHEMA_VERSION,
                            ))
                        except ValueError:
                            continue
    return tuple(results)


_SPECIALIST_BY_DOMAIN = {
    ArtifactReviewDomain.PLOT_STRUCTURE: "plot_architect",
    ArtifactReviewDomain.CHARACTER_WORLD: "character_custodian",
    ArtifactReviewDomain.TONE_EXECUTION: "execution_specialist",
}
_CORE_DOMAIN_BY_PATH = {
    "core_documents/global_outline.md": ArtifactReviewDomain.PLOT_STRUCTURE,
    "core_documents/part_outlines.md": ArtifactReviewDomain.PLOT_STRUCTURE,
    "core_documents/character_sheets.md": ArtifactReviewDomain.CHARACTER_WORLD,
    "core_documents/narrative_rules.md": ArtifactReviewDomain.TONE_EXECUTION,
}


def authoring_source_scope_is_eligible(
    source_scope: AuthoringScope | None,
    requested_scope: AuthoringScope,
    *,
    allow_prior_revision_input: bool = False,
) -> bool:
    """Separate exact current source identity from historical drafting input."""
    if type(requested_scope) is not AuthoringScope or type(allow_prior_revision_input) is not bool:
        raise TypeError("authoring source eligibility requires canonical scope and host policy")
    if source_scope is None:
        return True
    if type(source_scope) is not AuthoringScope:
        raise TypeError("authoring source scope must be canonical")
    if source_scope.activity is AuthoringActivity.WORKFLOW:
        return True
    if source_scope.task_id != requested_scope.task_id:
        return False
    if requested_scope.schema_version < 3:
        return True
    if source_scope.task_revision == requested_scope.task_revision:
        return True
    return (
        allow_prior_revision_input
        and requested_scope.schema_version == 3
        and requested_scope.intent is AuthoringIntent.AUTHORING
        and requested_scope.activity in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE}
        and source_scope.intent is AuthoringIntent.AUTHORING
        and source_scope.activity in {AuthoringActivity.DRAFT, AuthoringActivity.REVISE}
        and source_scope.task_revision < requested_scope.task_revision
    )


_ARTIFACT_PATH_CLASS, _ARTIFACT_PATH_NON_DOT = unicode_scalar_classes(
    ("Cc", "Cf"), ("/\\}\u2028\u2029", "/\\}\u2028\u2029."))


def artifact_path_rule(domains: tuple[ArtifactReviewDomain, ...]) -> dict[str, object]:
    """Declare the same normalized lexical path language used by artifact_domain."""
    choices = [re.escape(path) for path, domain in _CORE_DOMAIN_BY_PATH.items() if domain in domains]
    if ArtifactReviewDomain.TONE_EXECUTION in domains:
        segment = r"(?:\.{0,2}" + _ARTIFACT_PATH_NON_DOT + r"|\.\.\.)" + _ARTIFACT_PATH_CLASS + "*"
        filename = _ARTIFACT_PATH_CLASS + r"+\.md"
        choices.append("manuscript/(?:" + segment + "/)*" + filename)
    if not choices:
        raise ValueError("artifact path rule requires a declared domain")
    return {"type": "string", "pattern": "^(?:" + "|".join(choices) + r")\z",
            "description": "Normalized relative artifact path in the declared specialist domains; current physical-root qualification remains required."}


def artifact_domain(path: str) -> ArtifactReviewDomain | None:
    """Classify an artifact identity without inspecting its existence or content.

    Safe unrelated paths return ``None``; malformed paths raise ``TypeError``
    or ``ValueError``. Manuscript reference paths may be broader than publishable
    artifact paths. Classification grants neither read nor publication access.
    """

    if type(path) is not str:
        raise validation_failure(TypeError('routing artifact path must be exact text'), '$', 'expected text')
    if not path or "\\" in path or "\x00" in path:
        raise validation_failure(
            ValueError('routing artifact path must be a nonempty POSIX path'),
            '$',
            'routing artifact path must be a nonempty POSIX path',
        )
    if len(path.encode("utf-8", errors="strict")) > 1_024:
        raise validation_failure(
            ValueError('routing artifact path is too long'),
            '$',
            'expected at most 1024 UTF-8 bytes',
        )
    parsed = PurePosixPath(path)
    if (
        parsed.is_absolute()
        or parsed.as_posix() != path
        or any(part in {"", ".", ".."} for part in parsed.parts)
    ):
        raise validation_failure(
            ValueError('routing artifact path must be normalized and relative'),
            '$',
            'expected a normalized relative path without absolute roots or dot segments',
        )
    if path in _CORE_DOMAIN_BY_PATH:
        return _CORE_DOMAIN_BY_PATH[path]
    if (
        len(parsed.parts) >= 2
        and parsed.parts[0] == "manuscript"
        and parsed.parts[-1].endswith(".md")
        and parsed.parts[-1] != ".md"
        and not any(
            character in {"}", "\u2028", "\u2029"}
            or unicodedata.category(character) in {"Cc", "Cf", "Cs"}
            for character in path
        )
    ):
        return ArtifactReviewDomain.TONE_EXECUTION
    return None


@dataclass(frozen=True, slots=True)
class AuthoringScope:
    """An explicit interpretation of work, bound to caller-owned task revision.

    Targets may be absent or empty on disk. A task without an exact target must
    disclose its semantic domains. ``task_id`` and ``task_revision`` correlate
    existing runtime work; this value neither creates nor advances a task.
    """

    activity: AuthoringActivity
    target_paths: tuple[str, ...]
    domains: tuple[ArtifactReviewDomain, ...]
    interpretation: str = field(repr=False)
    task_id: str
    task_revision: int
    schema_version: int = 2
    intent: AuthoringIntent = field(default=AuthoringIntent.AUTHORING, kw_only=True)
    contributions: tuple[AuthoringContribution, ...] = field(default=(), kw_only=True)
    dependency_slots: tuple[AuthoringDependencySlot, ...] = field(default=(), kw_only=True)

    def __post_init__(self) -> None:
        if (
            type(self.schema_version) is not int
            or self.schema_version not in {1, 2, AUTHORING_SCOPE_SCHEMA_VERSION}
        ):
            raise validation_failure(
                ValueError('authoring scope version is unsupported'),
                '$.schema_version', 'expected authoring scope version 1, 2 or 3',
            )
        if type(self.intent) is not AuthoringIntent:
            raise validation_failure(
                TypeError('authoring intent must be an AuthoringIntent'),
                '$.intent', 'expected authoring or review_only',
            )
        if self.schema_version == 1 and self.intent is not AuthoringIntent.AUTHORING:
            raise validation_failure(
                ValueError('legacy scope cannot carry review-only intent'),
                '$.intent', 'review_only requires authoring scope version 2',
            )
        if self.intent is AuthoringIntent.REVIEW_ONLY and self.activity is not AuthoringActivity.REVIEW:
            raise validation_failure(
                ValueError('review-only scope cannot authorize authoring'),
                '$.activity', 'review_only requires review activity',
            )
        if type(self.activity) is not AuthoringActivity:
            raise validation_failure(
                TypeError('authoring activity must be an AuthoringActivity'),
                '$.activity',
                'expected activity from ' + ', '.join((activity.value for activity in AuthoringActivity)),
            )
        validation_call('$.target_paths', _path_domains, self.target_paths)
        validation_call('$.domains', _domain_tuple, self.domains)
        validation_call(
            '$.interpretation',
            _text,
            self.interpretation,
            label='authoring interpretation',
        )
        validation_call('$.task_id', _identifier, self.task_id)
        if type(self.task_revision) is not int or self.task_revision < 1:
            raise validation_failure(
                ValueError('authoring task revision must be a positive integer'),
                '$.task_revision',
                'expected a positive integer task revision',
            )
        required = set(self.domains) | validation_call('$.target_paths', _path_domains, self.target_paths)
        if type(self.contributions) is not tuple or any(type(item) is not AuthoringContribution for item in self.contributions):
            raise ValueError("authoring contributions must be an exact tuple")
        if type(self.dependency_slots) is not tuple or any(type(item) is not AuthoringDependencySlot for item in self.dependency_slots):
            raise ValueError("authoring dependency slots must be an exact tuple")
        if self.schema_version < 3 and (self.contributions or self.dependency_slots):
            raise ValueError("legacy authoring scopes cannot acquire contribution duties")
        if self.schema_version == 3:
            authoring_scope_rules(
                self.activity, self.intent, self.domains, self.contributions,
                target_paths=self.target_paths, dependency_slots=self.dependency_slots,
                schema_version=self.schema_version,
            )
        if self.activity is AuthoringActivity.WORKFLOW:
            if required:
                raise validation_failure(
                    ValueError('workflow scope cannot carry story domains or targets'),
                    '$',
                    'activity workflow requires domains and target_paths to both be empty',
                )
        elif not required:
            raise validation_failure(
                ValueError('story authoring requires explicit domains or targets'),
                '$',
                'story activity requires at least one domain or target_path',
            )


@dataclass(frozen=True, slots=True)
class ReviewBasis:
    """Concrete draft/candidate input aliases plus disclosed review criteria.

    An authoring brief alone is not a candidate. Runtime must verify that each
    alias contains concrete material, its availability, and its current revision.
    """

    candidate_input_ids: tuple[str, ...]
    criteria: str = field(repr=False)
    schema_version: int = REVIEW_BASIS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _version(self.schema_version, REVIEW_BASIS_SCHEMA_VERSION)
        validation_call('$.candidate_input_ids', _input_ids, self.candidate_input_ids)
        if not self.candidate_input_ids:
            raise validation_failure(
                ValueError('review basis requires concrete candidate inputs'),
                '$.candidate_input_ids',
                'review mode requires at least one concrete candidate in input_ids',
            )
        validation_call('$.criteria', _text, self.criteria, label='review criteria')


def specialists_for_domains(
    domains: tuple[ArtifactReviewDomain, ...],
) -> tuple[str, ...]:
    """Return the canonical specialist order for an exact domain tuple."""

    _domain_tuple(domains)
    return tuple(_SPECIALIST_BY_DOMAIN[domain] for domain in domains)


def required_authoring_domains(
    scope: AuthoringScope,
    *,
    affected_paths: tuple[str, ...] = (),
) -> tuple[ArtifactReviewDomain, ...]:
    """Union semantic targets with caller-resolved artifact facts.

    Affected paths may come from references, statuses, evidence, or current
    work. Their presence here proves no read. Their absence from input aliases
    cannot remove the domains already stated by the scope.
    """

    if type(scope) is not AuthoringScope:
        raise validation_failure(
            TypeError('authoring scope must be an AuthoringScope'),
            '$',
            'authoring scope must be an AuthoringScope',
        )
    domains = set(scope.domains)
    domains.update(_path_domains(scope.target_paths))
    domains.update(_path_domains(affected_paths))
    if scope.activity is AuthoringActivity.WORKFLOW and domains:
        raise validation_failure(
            ValueError('workflow scope cannot carry affected story artifacts'),
            '$',
            'workflow scope cannot carry affected story artifacts',
        )
    return tuple(domain for domain in ArtifactReviewDomain if domain in domains)


def required_authoring_specialists(
    scope: AuthoringScope,
    *,
    affected_paths: tuple[str, ...] = (),
) -> tuple[str, ...]:
    """Derive mandatory specialists independently of attached input aliases."""

    return specialists_for_domains(
        required_authoring_domains(scope, affected_paths=affected_paths)
    )


def validate_specialist_routing(
    scope: AuthoringScope,
    specialist_ids: tuple[str, ...],
    *,
    affected_paths: tuple[str, ...] = (),
) -> None:
    """Reject noncanonical participants or omitted mandatory specialists."""

    if type(specialist_ids) is not tuple or any(
        type(item) is not str or item not in _SPECIALIST_BY_DOMAIN.values()
        for item in specialist_ids
    ):
        raise validation_failure(
            TypeError('routing participants must be canonical specialist IDs'),
            '$',
            'expected specialist IDs from ' + ', '.join(_SPECIALIST_BY_DOMAIN.values()),
        )
    if len(set(specialist_ids)) != len(specialist_ids):
        raise validation_failure(
            ValueError('routing participants must be unique'),
            '$',
            'specialist IDs must be unique',
        )
    if specialist_ids != tuple(
        item for item in _SPECIALIST_BY_DOMAIN.values() if item in specialist_ids
    ):
        raise validation_failure(
            ValueError('routing participants must be in canonical order'),
            '$',
            'specialist IDs must follow this order: ' + ', '.join(_SPECIALIST_BY_DOMAIN.values()),
        )
    required = required_authoring_specialists(scope, affected_paths=affected_paths)
    if not set(required).issubset(specialist_ids):
        raise validation_failure(
            ValueError('routing omits a required story specialist'),
            '$',
            'must include every specialist required by authoring_scope and affected target_paths: ' + ', '.join(required),
        )


def validate_specialist_assignment(
    mode: SpecialistAssignmentMode,
    input_ids: tuple[str, ...],
    review_basis: ReviewBasis | None,
) -> None:
    """Validate explicit drafting or review against disclosed input aliases."""

    if type(mode) is not SpecialistAssignmentMode:
        raise validation_failure(
            TypeError('assignment mode must be a SpecialistAssignmentMode'),
            '$.mode',
            'assignment mode must be a SpecialistAssignmentMode',
        )
    validation_call("$.input_ids", _input_ids, input_ids)
    if mode in {SpecialistAssignmentMode.DRAFT, SpecialistAssignmentMode.CONSTRAINT_ASSESSMENT, SpecialistAssignmentMode.PREMISE_ANALYSIS}:
        if review_basis is not None:
            raise validation_failure(
                ValueError('draft assignment cannot carry a review basis'),
                '$.input_ids',
                'draft assignment cannot carry a review basis',
            )
    else:
        if type(review_basis) is not ReviewBasis:
            raise validation_failure(
                TypeError('review assignment requires a ReviewBasis'),
                '$.input_ids',
                'review assignment requires a ReviewBasis',
            )
        if not set(review_basis.candidate_input_ids).issubset(input_ids):
            raise validation_failure(
                ValueError('review candidates must be disclosed assignment inputs'),
                '$.input_ids',
                'every review candidate must be included in input_ids',
            )


def assignment_mode_for_specialist(scope: AuthoringScope, specialist_id: str) -> SpecialistAssignmentMode:
    """Return the declared current-phase obligation without shrinking participation."""
    if scope.activity is AuthoringActivity.REVIEW or scope.intent is AuthoringIntent.REVIEW_ONLY:
        return SpecialistAssignmentMode.REVIEW
    if scope.schema_version < 3:
        return SpecialistAssignmentMode.DRAFT
    return assignment_rule(authoring_scope_rules(
        scope.activity, scope.intent, scope.domains, scope.contributions,
        target_paths=scope.target_paths, dependency_slots=scope.dependency_slots,
        schema_version=scope.schema_version,
    ), specialist_id)


def required_drafting_specialists(scope: AuthoringScope) -> tuple[str, ...]:
    """Only declared authors can supply current-task draft proof."""
    if scope.schema_version < 3:
        return required_authoring_specialists(scope)
    authors = {item.domain for item in scope.contributions if item.role is AuthoringContributionRole.DRAFT_OWNER}
    return specialists_for_domains(tuple(domain for domain in ArtifactReviewDomain if domain in authors))


def _version(value: object, expected: int) -> None:
    if type(value) is not int or value != expected:
        raise validation_failure(
            ValueError('specialist routing schema version is unsupported'),
            '$',
            f'expected schema version {expected}',
        )


def _identifier(value: object) -> None:
    if type(value) is not str or _IDENTIFIER.fullmatch(value) is None:
        raise validation_failure(
            ValueError('specialist routing identifier is invalid'),
            '$',
            'expected identifier matching [A-Za-z0-9][A-Za-z0-9_-]{0,63}',
        )


def _text(value: object, *, label: str) -> None:
    if type(value) is not str:
        raise validation_failure(TypeError(f'{label} must be exact text'), '$', 'expected text')
    if (
        not value.strip()
        or "\x00" in value
        or len(value.encode("utf-8", errors="strict")) > 32_768
    ):
        raise validation_failure(
            ValueError(f'{label} must be nonempty bounded text'),
            '$',
            'expected nonempty text after trimming whitespace, without NUL, of at most 32768 UTF-8 bytes',
        )


def _input_ids(value: object) -> None:
    if type(value) is not tuple:
        raise validation_failure(
            TypeError('specialist input IDs must be an exact tuple'),
            '$',
            'expected an array of input identifiers',
        )
    for index, item in enumerate(value):
        validation_call(f"$[{index}]", _identifier, item)
    if len(set(value)) != len(value):
        raise validation_failure(
            ValueError('specialist input IDs must be unique'),
            '$',
            'specialist input IDs must be unique',
        )


def _domain_tuple(value: object) -> None:
    if type(value) is not tuple or any(
        type(item) is not ArtifactReviewDomain for item in value
    ):
        raise validation_failure(
            TypeError('routing domains must be an exact domain tuple'),
            '$',
            'expected domains from ' + ', '.join((domain.value for domain in ArtifactReviewDomain)),
        )
    if value != tuple(domain for domain in ArtifactReviewDomain if domain in value):
        raise validation_failure(
            ValueError('routing domains must be unique and in canonical order'),
            '$',
            'domains must be unique and follow this order: ' + ', '.join((domain.value for domain in ArtifactReviewDomain)),
        )


def _path_domains(paths: tuple[str, ...]) -> set[ArtifactReviewDomain]:
    if type(paths) is not tuple:
        raise validation_failure(
            TypeError('routing artifact paths must be an exact tuple'),
            '$',
            'expected an array of artifact paths',
        )
    result: set[ArtifactReviewDomain] = set()
    for index, path in enumerate(paths):
        domain = validation_call(f"$[{index}]", artifact_domain, path)
        if domain is None:
            raise validation_failure(
                ValueError('routing target must be a canonical artifact identity'),
                f'$[{index}]',
                'expected one of core_documents/global_outline.md, '
                'core_documents/part_outlines.md, core_documents/character_sheets.md, '
                'core_documents/narrative_rules.md, or manuscript/PART/PATH.md; '
                'PART must match [A-Za-z0-9][A-Za-z0-9_-]{0,63}; every remaining path '
                'component must match [A-Za-z0-9][A-Za-z0-9_.-]{0,127}, and the final '
                'component must end in lowercase .md; paths must be normalized '
                'workspace-relative POSIX text without backslash, NUL, . or .. '
                'components, and fit 1024 strict UTF-8 bytes',
            )
        result.add(domain)
    if len(set(paths)) != len(paths):
        raise validation_failure(
            ValueError('routing artifact paths must be unique'),
            '$',
            'target_paths must be unique',
        )
    return result


__all__ = [
    "legal_specialist_rosters",
    "assignment_rule",
    "finite_authoring_scope_rules",
    "authoring_scope_rules",
    "AuthoringScopeRuleSet",
    "AUTHORING_SCOPE_SCHEMA_VERSION",
    "REVIEW_BASIS_SCHEMA_VERSION",
    "ArtifactReviewDomain",
    "AuthoringActivity",
    "AuthoringIntent",
    "AuthoringScope",
    "AuthoringContributionRole",
    "AuthoringContribution",
    "AuthoringDependencySlot",
    "ReviewBasis",
    "SpecialistAssignmentMode",
    "artifact_domain",
    "assignment_mode_for_specialist",
    "required_drafting_specialists",
    "required_authoring_domains",
    "required_authoring_specialists",
    "specialists_for_domains",
    "validate_specialist_assignment",
    "validate_specialist_routing",
]
