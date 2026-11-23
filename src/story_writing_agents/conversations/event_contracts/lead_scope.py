"""Lead outcome scope and authoring evidence validation."""

from __future__ import annotations

from ...review_contracts import (
    LeadPreliminaryReview,
    ReviewTargetAvailability,
    ReviewTargetKind,
    preliminary_scope_eligible,
)
from ...specialist_routing import (
    AUTHORING_SCOPE_SCHEMA_VERSION,
    ArtifactReviewDomain,
    AuthoringActivity,
    AuthoringScope,
    artifact_domain,
    required_authoring_domains,
)
from ...structured_output_validation import validation_failure
from ...writing_artifacts import parse_artifact_references
from ..event_vocabulary import (
    CollaborationEvidenceAcquisition,
    CollaborationInputKind,
    CollaborationInputState,
    LeadEvidenceStatus,
    LeadOutcomeKind,
    LeadOutcomeScope,
)
from .evidence import CollaborationLeadEvidence


def lead_outcome_scope_paths(
    kind: LeadOutcomeKind,
    activity: AuthoringActivity,
    domains: tuple[ArtifactReviewDomain, ...],
    writing_read_facts: tuple[tuple[str, str, bool], ...],
    *,
    target_paths: tuple[str, ...] = (),
    ordinary_answer: bool = False,
) -> tuple[str, ...] | None:
    """Derive current Lead kind/scope eligibility without granting authority.

    None means unavailable; an empty tuple is a valid WORKFLOW result. Existing
    substantive QUESTION/PROPOSAL/RECOMMENDATION scopes retain their targets.
    Ordinary factual ANSWER requires REVIEW and returns canonical writing-READ
    paths in offered order. Whole and ranged READs may both support inspection.
    """
    if (type(kind) is not LeadOutcomeKind or type(ordinary_answer) is not bool
            or type(activity) is not AuthoringActivity
            or type(domains) is not tuple
            or any(type(domain) is not ArtifactReviewDomain for domain in domains)
            or len(set(domains)) != len(domains)
            or type(target_paths) is not tuple
            or any(type(path) is not str for path in target_paths)
            or len(set(target_paths)) != len(target_paths)
            or type(writing_read_facts) is not tuple
            or any(type(fact) is not tuple or len(fact) != 3
                   or type(fact[0]) is not str or not fact[0]
                   or type(fact[1]) is not str or not fact[1]
                   or type(fact[2]) is not bool for fact in writing_read_facts)):
        raise ValueError("Lead outcome scope requires exact current kind and READ facts")
    if activity is AuthoringActivity.WORKFLOW:
        return () if not domains and not target_paths else None
    if not domains:
        return None
    if kind in (LeadOutcomeKind.QUESTION, LeadOutcomeKind.PROPOSAL,
                LeadOutcomeKind.RECOMMENDATION):
        return target_paths
    if (kind is not LeadOutcomeKind.ANSWER or not ordinary_answer
            or activity is not AuthoringActivity.REVIEW):
        return None
    read_domains = {
        locator: artifact_domain(locator) for _, locator, _ in writing_read_facts
    }
    if not set(domains).issubset(set(read_domains.values())):
        return None
    paths = tuple(path for path, domain in read_domains.items() if domain in domains)
    if not set(target_paths).issubset(paths):
        return None
    return paths


def validate_lead_evidence_matrix(
    kind: LeadOutcomeKind,
    evidence: CollaborationLeadEvidence, *,
    authoring_scope: AuthoringScope | None = None,
    preliminary_review: LeadPreliminaryReview | None = None,
) -> None:
    if preliminary_review is not None:
        if (type(preliminary_review) is not LeadPreliminaryReview
                or kind is not LeadOutcomeKind.ANSWER or authoring_scope is None
                or not preliminary_scope_eligible(authoring_scope.activity.value,
                    authoring_scope.target_paths, preliminary_review.targets)
                or evidence.scope is not LeadOutcomeScope.STORY_SPECIFIC
                or evidence.status not in {LeadEvidenceStatus.VERIFIED, LeadEvidenceStatus.INCOMPLETE,
                                           LeadEvidenceStatus.CONFLICTING}):
            raise ValueError("invalid bounded preliminary judgment")
        receipts = {item.evidence_id: item for item in evidence.receipts if item.acquisition in {
            CollaborationEvidenceAcquisition.READ, CollaborationEvidenceAcquisition.ATTACHED_REFERENCE}}
        writing_kinds = {CollaborationInputKind.CORE_DOCUMENT, CollaborationInputKind.MANUSCRIPT,
                         CollaborationInputKind.WRITING_ARTIFACT}
        for target in preliminary_review.targets:
            if (target.input_id not in preliminary_review.inspected_input_ids
                    or target.kind not in {ReviewTargetKind.WRITING_ARTIFACT, ReviewTargetKind.READ_SOURCE}):
                continue
            receipt = receipts.get(target.input_id)
            if receipt is None:
                raise ValueError("preliminary READ target lacks actual current receipt")
            writing = receipt.source_kind in writing_kinds
            target_id = receipt.locator if writing else receipt.source_id
            if receipt.acquisition is CollaborationEvidenceAcquisition.ATTACHED_REFERENCE:
                references = parse_artifact_references(receipt.locator).references
                if len(references) != 1 or references[0].token != receipt.locator:
                    raise ValueError("preliminary attached reference must be exact")
                target_id = references[0].path
            if (target.kind is not (ReviewTargetKind.WRITING_ARTIFACT if writing else ReviewTargetKind.READ_SOURCE)
                    or target.availability is not ReviewTargetAvailability.COMPLETED_READ
                    or target.target_id != target_id
                    or target.revision != receipt.revision_digest
                    or target.content_digest != receipt.content_digest
                    or target.boundary_digest != receipt.content_digest
                    or (target.boundary_kind, target.boundary_start, target.boundary_end)
                        != (receipt.boundary_kind, receipt.boundary_start, receipt.boundary_end)
                    or (target.whole_source and (receipt.boundary_kind != "whole"
                        or target.source_digest != receipt.content_digest))
                    or (receipt.acquisition is CollaborationEvidenceAcquisition.ATTACHED_REFERENCE
                        and target.source_digest != receipt.revision_digest)):
                raise ValueError("preliminary READ target differs from its durable receipt")
        return
    receipts = evidence.receipts
    if evidence.scope is LeadOutcomeScope.PREMISE_ANALYSIS:
        if kind is not LeadOutcomeKind.PROPOSAL:
            raise validation_failure(
                ValueError("premise analysis evidence requires a proposal"),
                "$.kind",
                "premise analysis evidence requires a candidate-free proposal",
            )
        if evidence.status is LeadEvidenceStatus.NOT_APPLICABLE:
            if receipts:
                raise validation_failure(
                    ValueError("unobserved premise analysis cannot carry evidence"),
                    "$.authoring_scope",
                    "USER-only premise analysis has no artifact evidence receipts",
                )
            return
    if evidence.scope is LeadOutcomeScope.WORKFLOW_META:
        if (
            evidence.status is not LeadEvidenceStatus.NOT_APPLICABLE
            or receipts
            or kind is LeadOutcomeKind.PROPOSAL
        ):
            raise validation_failure(
                ValueError('workflow/meta judgment evidence is inconsistent'),
                '$.authoring_scope.activity',
                (
                    'workflow activity requires no story evidence receipts, evidence status '
                    'not_applicable, and a kind other than proposal'
                ),
            )
        return
    if evidence.status is LeadEvidenceStatus.NOT_APPLICABLE:
        raise validation_failure(
            ValueError('story-specific judgment requires an evidence status'),
            '$.authoring_scope.activity',
            'story activity requires a runtime evidence status other than not_applicable',
        )

    content = tuple(
        item
        for item in receipts
        if item.acquisition
        in {
            CollaborationEvidenceAcquisition.ATTACHED_REFERENCE,
            CollaborationEvidenceAcquisition.READ,
        }
        and item.source_state is CollaborationInputState.PRESENT
        and item.utf8_bytes > 0
    )
    statuses = tuple(
        item
        for item in receipts
        if item.acquisition is CollaborationEvidenceAcquisition.CORE_STATUS
    )
    if evidence.status is LeadEvidenceStatus.VERIFIED:
        valid = bool(content)
        allowed = set(LeadOutcomeKind)
    elif evidence.status is LeadEvidenceStatus.MISSING:
        valid = any(
            (
                item.acquisition is CollaborationEvidenceAcquisition.ZERO_HIT_SEARCH
                and CollaborationInputKind.WRITING_ARTIFACT
                in item.authorized_source_kinds
            )
            or (
                item.acquisition is CollaborationEvidenceAcquisition.CORE_STATUS
                and item.source_state is CollaborationInputState.ABSENT
            )
            for item in receipts
        )
        allowed = {
            LeadOutcomeKind.QUESTION,
            LeadOutcomeKind.RECOMMENDATION,
            LeadOutcomeKind.PROPOSAL,
        }
    elif evidence.status is LeadEvidenceStatus.EMPTY:
        valid = any(
            item.acquisition
            in {
                CollaborationEvidenceAcquisition.ATTACHED_REFERENCE,
                CollaborationEvidenceAcquisition.CORE_STATUS,
            }
            and item.source_state is CollaborationInputState.PRESENT
            and item.utf8_bytes == 0
            for item in receipts
        )
        allowed = {
            LeadOutcomeKind.QUESTION,
            LeadOutcomeKind.RECOMMENDATION,
            LeadOutcomeKind.PROPOSAL,
        }
    elif evidence.status is LeadEvidenceStatus.INCOMPLETE:
        valid = bool(content or statuses)
        allowed = {
            LeadOutcomeKind.QUESTION,
            LeadOutcomeKind.CRITIQUE,
            LeadOutcomeKind.RECOMMENDATION,
            LeadOutcomeKind.PROPOSAL,
        }
        if (kind is LeadOutcomeKind.ANSWER
                and type(authoring_scope) is AuthoringScope
                and authoring_scope.schema_version == AUTHORING_SCOPE_SCHEMA_VERSION
                and authoring_scope.activity is AuthoringActivity.REVIEW):
            writing_reads = evidence.factual_writing_read_facts()
            if writing_reads and lead_outcome_scope_paths(
                kind, authoring_scope.activity, required_authoring_domains(authoring_scope),
                writing_reads, target_paths=authoring_scope.target_paths, ordinary_answer=True,
            ) is not None:
                allowed.add(LeadOutcomeKind.ANSWER)
    else:
        distinct = {
            (
                item.source_id,
                item.locator,
                item.boundary_kind,
                item.boundary_start,
                item.boundary_end,
                item.start_line,
                item.end_line,
                item.content_digest,
            )
            for item in content
        }
        valid = len(distinct) >= 2
        allowed = {
            LeadOutcomeKind.QUESTION,
            LeadOutcomeKind.CRITIQUE,
            LeadOutcomeKind.RECOMMENDATION,
            LeadOutcomeKind.PROPOSAL,
        }
    if not valid or kind not in allowed:
        raise validation_failure(
            ValueError('lead judgment violates the closed evidence matrix'),
            '$' if not valid else '$.kind',
            (
                'runtime evidence status ' + evidence.status.value + ' requires ' + {LeadEvidenceStatus.VERIFIED: 'at least one nonempty attached-reference or READ receipt', LeadEvidenceStatus.MISSING: 'an authorized zero-hit artifact search or an absent core-document status', LeadEvidenceStatus.EMPTY: 'an attached-reference or core-document receipt proving zero bytes', LeadEvidenceStatus.INCOMPLETE: 'at least one nonempty content receipt or core-document status', LeadEvidenceStatus.CONFLICTING: 'at least two distinct nonempty content receipt identities'}[evidence.status]
                if not valid
                else 'expected one of ' + ', '.join((item.value for item in LeadOutcomeKind if item in allowed)) + ' for evidence status ' + evidence.status.value
            ),
        )


def validate_authoring_evidence_scope(
    scope: AuthoringScope | None,
    evidence_scope: LeadOutcomeScope,
) -> None:
    if scope is None:
        return
    if evidence_scope is LeadOutcomeScope.PREMISE_ANALYSIS:
        raise validation_failure(
            ValueError("premise analysis cannot carry authoring scope"),
            "$.authoring_scope",
            "premise analysis requires no authoring scope or candidate authority",
        )
    if type(scope) is not AuthoringScope:
        raise validation_failure(
            TypeError('authoring scope must be an AuthoringScope'),
            '$',
            'authoring scope must be an AuthoringScope',
        )
    if (
        scope.activity is not AuthoringActivity.WORKFLOW
        and evidence_scope is not LeadOutcomeScope.STORY_SPECIFIC
    ):
        raise validation_failure(
            ValueError('story authoring requires story-specific evidence scope'),
            '$',
            'story authoring requires story-specific evidence scope',
        )
