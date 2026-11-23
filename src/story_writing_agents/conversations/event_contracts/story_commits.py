"""Story Commit event contracts and payload codecs."""

from __future__ import annotations

import hashlib
import re
from dataclasses import (
    dataclass,
    field,
)

from ...git.story_commit_contracts import (
    StoryCommitCandidate,
    StoryCommitDecision,
    StoryCommitIdentitySource,
    StoryCommitPath,
    StoryCommitPathOperation,
    StoryCommitRecoveryAction,
    StoryCommitResult,
    StoryCommitState,
)
from ..event_value_validation import (
    require_array_value as _array,
    canonical_json as _canonical_json,
    require_enum_value as _enum,
    require_object_fields as _object,
    optional_string_value as _optional_string,
    require_enum as _require_enum,
    require_string_value as _string,
    validate_digest as _validate_digest,
    validate_identifier as _validate_identifier,
    validate_visible_text as _validate_visible_text,
)
from ..event_vocabulary import (
    CollaborationSideEffect,
    StoryCommitNextAction,
    StoryCommitOutcomeKind,
)


@dataclass(frozen=True, slots=True)
class StoryCommitRequestPayload:
    """Durable exact prepared local commit facts, never mutation authority."""

    confirmation_request_id: str
    application_result_event_id: str
    candidate: StoryCommitCandidate = field(repr=False)
    request_digest: str
    choices: tuple[StoryCommitDecision, ...]
    side_effects: tuple[CollaborationSideEffect, ...]
    prohibited_side_effects: tuple[CollaborationSideEffect, ...]

    def __post_init__(self) -> None:
        _validate_identifier(self.confirmation_request_id, label="commit request ID")
        _validate_identifier(
            self.application_result_event_id, label="application result event ID"
        )
        if type(self.candidate) is not StoryCommitCandidate:
            raise TypeError("story commit candidate is invalid")
        self.candidate.__post_init__()
        _validate_digest(self.request_digest, label="story commit request digest")
        if self.choices != (
            StoryCommitDecision.COMMIT,
            StoryCommitDecision.REJECT,
            StoryCommitDecision.REVISE,
        ):
            raise ValueError("story commit choices are not canonical")
        if self.side_effects != (
            CollaborationSideEffect.GIT_INDEX_MUTATION,
            CollaborationSideEffect.GIT_COMMIT,
        ):
            raise ValueError("story commit side effects are not exact")
        if self.prohibited_side_effects != (
            CollaborationSideEffect.ARTIFACT_MUTATION,
            CollaborationSideEffect.GIT_REMOTE_MUTATION,
            CollaborationSideEffect.GIT_MERGE,
        ):
            raise ValueError("story commit prohibited effects are not exact")
        if self.request_digest != _story_commit_request_digest(self):
            raise ValueError("story commit request digest is invalid")


@dataclass(frozen=True, slots=True)
class StoryCommitDecisionPayload:
    """Durable structured user decision for one exact prepared request."""

    confirmation_request_id: str
    confirmation_request_event_id: str
    application_result_event_id: str
    candidate_id: str
    request_digest: str
    confirmation_id: str
    decision: StoryCommitDecision
    revision_request: str | None = None

    def __post_init__(self) -> None:
        for value, label in (
            (self.confirmation_request_id, "commit request ID"),
            (self.confirmation_request_event_id, "commit request event ID"),
            (self.application_result_event_id, "application result event ID"),
            (self.confirmation_id, "commit confirmation ID"),
        ):
            _validate_identifier(value, label=label)
        if type(self.candidate_id) is not str or re.fullmatch(
            r"[0-9a-f]{64}", self.candidate_id
        ) is None:
            raise ValueError("story commit candidate ID is invalid")
        _validate_digest(self.request_digest, label="story commit request digest")
        _require_enum(self.decision, StoryCommitDecision, "story commit decision")
        if self.decision is StoryCommitDecision.REVISE:
            _validate_visible_text(self.revision_request, label="commit revision request")
        elif self.revision_request is not None:
            raise ValueError("only story commit revise may carry text")


@dataclass(frozen=True, slots=True)
class StoryCommitResultPayload:
    """Durable content-free result for one exact local commit decision."""

    confirmation_request_id: str
    decision_event_id: str
    candidate_id: str
    request_digest: str
    outcome: StoryCommitOutcomeKind
    next_action: StoryCommitNextAction
    result: StoryCommitResult | None = None

    def __post_init__(self) -> None:
        _validate_identifier(self.confirmation_request_id, label="commit request ID")
        _validate_identifier(self.decision_event_id, label="commit decision event ID")
        if type(self.candidate_id) is not str or re.fullmatch(
            r"[0-9a-f]{64}", self.candidate_id
        ) is None:
            raise ValueError("story commit candidate ID is invalid")
        _validate_digest(self.request_digest, label="story commit request digest")
        _require_enum(self.outcome, StoryCommitOutcomeKind, "story commit outcome")
        _require_enum(self.next_action, StoryCommitNextAction, "story commit next action")
        if self.result is not None:
            if type(self.result) is not StoryCommitResult:
                raise TypeError("story commit result is invalid")
            expected = {
                StoryCommitState.COMMITTED: StoryCommitOutcomeKind.COMMITTED,
            }.get(self.result.state)
            valid_rejection = self.result.state is StoryCommitState.REJECTED and self.outcome in {
                StoryCommitOutcomeKind.REJECTED,
                StoryCommitOutcomeKind.SUPERSEDED,
            }
            if (expected is not self.outcome and not valid_rejection) or self.result.candidate_id != self.candidate_id:
                raise ValueError("story commit terminal result is inconsistent")
        if self.outcome is StoryCommitOutcomeKind.COMMITTED:
            if self.result is None or self.next_action is not StoryCommitNextAction.CONTINUE_WITH_LEAD:
                raise ValueError("committed story result is incomplete")


def _story_commit_candidate_value(candidate: StoryCommitCandidate) -> dict[str, object]:
    return {
        "application_id": candidate.application_id,
        "author_identity_source": candidate.author_identity_source.value,
        "branch": candidate.branch,
        "branch_tip": candidate.branch_tip,
        "candidate_id": candidate.candidate_id,
        "committer_identity_source": candidate.committer_identity_source.value,
        "correlation_id": candidate.correlation_id,
        "expected_tree": candidate.expected_tree,
        "impact_boundary": list(candidate.impact_boundary),
        "paths": [{
            "after_sha256": item.after_sha256,
            "before_sha256": item.before_sha256,
            "operation": item.operation.value,
            "path": item.path,
        } for item in candidate.paths],
        "proposal_id": candidate.proposal_id,
        "proposed_message": candidate.proposed_message,
        "session_id": candidate.session_id,
        "worktree_identity_sha256": candidate.worktree_identity_sha256,
    }


def _decode_story_commit_candidate(value: object) -> StoryCommitCandidate:
    item = _object(value, {
        "application_id", "author_identity_source", "branch", "branch_tip",
        "candidate_id", "committer_identity_source", "correlation_id",
        "expected_tree", "impact_boundary", "paths", "proposal_id",
        "proposed_message", "session_id", "worktree_identity_sha256",
    }, label="story commit candidate")
    paths = []
    for raw_path in _array(item["paths"], label="story commit paths"):
        path = _object(raw_path, {
            "after_sha256", "before_sha256", "operation", "path",
        }, label="story commit path")
        paths.append(StoryCommitPath(
            path=_string(path["path"], label="story commit path"),
            operation=_enum(StoryCommitPathOperation, path["operation"], label="story commit path operation"),
            before_sha256=_optional_string(path["before_sha256"], label="before digest"),
            after_sha256=_optional_string(path["after_sha256"], label="after digest"),
        ))
    return StoryCommitCandidate(
        candidate_id=_string(item["candidate_id"], label="story commit candidate ID"),
        session_id=_string(item["session_id"], label="story commit session ID"),
        branch=_string(item["branch"], label="story commit branch"),
        branch_tip=_string(item["branch_tip"], label="story commit branch tip"),
        expected_tree=_string(item["expected_tree"], label="story commit expected tree"),
        worktree_identity_sha256=_string(item["worktree_identity_sha256"], label="story commit worktree identity"),
        application_id=_string(item["application_id"], label="story commit application ID"),
        proposal_id=_string(item["proposal_id"], label="story commit proposal ID"),
        correlation_id=_string(item["correlation_id"], label="story commit correlation ID"),
        paths=tuple(paths),
        proposed_message=_string(item["proposed_message"], label="story commit message"),
        impact_boundary=tuple(_string(value, label="story commit impact") for value in _array(item["impact_boundary"], label="story commit impacts")),
        author_identity_source=_enum(StoryCommitIdentitySource, item["author_identity_source"], label="author identity source"),
        committer_identity_source=_enum(StoryCommitIdentitySource, item["committer_identity_source"], label="committer identity source"),
    )


def _story_commit_request_digest(payload: StoryCommitRequestPayload) -> str:
    return _story_commit_request_digest_values(
        application_result_event_id=payload.application_result_event_id,
        candidate=payload.candidate,
        choices=payload.choices,
        side_effects=payload.side_effects,
        prohibited_side_effects=payload.prohibited_side_effects,
    )


def _story_commit_request_digest_values(
    *,
    application_result_event_id: str,
    candidate: StoryCommitCandidate,
    choices: tuple[StoryCommitDecision, ...],
    side_effects: tuple[CollaborationSideEffect, ...],
    prohibited_side_effects: tuple[CollaborationSideEffect, ...],
) -> str:
    value = {
        "application_result_event_id": application_result_event_id,
        "candidate": _story_commit_candidate_value(candidate),
        "choices": [item.value for item in choices],
        "prohibited_side_effects": [item.value for item in prohibited_side_effects],
        "side_effects": [item.value for item in side_effects],
    }
    return "sha256:" + hashlib.sha256(_canonical_json(value)).hexdigest()


def build_story_commit_request_payload(
    *,
    confirmation_request_id: str,
    application_result_event_id: str,
    candidate: StoryCommitCandidate,
) -> StoryCommitRequestPayload:
    """Build one canonical durable request from an engine candidate."""

    choices = (StoryCommitDecision.COMMIT, StoryCommitDecision.REJECT, StoryCommitDecision.REVISE)
    side_effects = (CollaborationSideEffect.GIT_INDEX_MUTATION, CollaborationSideEffect.GIT_COMMIT)
    prohibited = (CollaborationSideEffect.ARTIFACT_MUTATION, CollaborationSideEffect.GIT_REMOTE_MUTATION, CollaborationSideEffect.GIT_MERGE)
    return StoryCommitRequestPayload(
        confirmation_request_id=confirmation_request_id,
        application_result_event_id=application_result_event_id,
        candidate=candidate,
        choices=choices,
        side_effects=side_effects,
        prohibited_side_effects=prohibited,
        request_digest=_story_commit_request_digest_values(
            application_result_event_id=application_result_event_id,
            candidate=candidate,
            choices=choices,
            side_effects=side_effects,
            prohibited_side_effects=prohibited,
        ),
    )


def story_commit_request_payload_value(
    payload: StoryCommitRequestPayload,
) -> dict[str, object]:
    return {
        "application_result_event_id": payload.application_result_event_id,
        "candidate": _story_commit_candidate_value(payload.candidate),
        "choices": [item.value for item in payload.choices],
        "confirmation_request_id": payload.confirmation_request_id,
        "payload_type": "story_commit_request",
        "prohibited_side_effects": [item.value for item in payload.prohibited_side_effects],
        "request_digest": payload.request_digest,
        "side_effects": [item.value for item in payload.side_effects],
    }


def story_commit_decision_payload_value(
    payload: StoryCommitDecisionPayload,
) -> dict[str, object]:
    return {
        "application_result_event_id": payload.application_result_event_id,
        "candidate_id": payload.candidate_id,
        "confirmation_id": payload.confirmation_id,
        "confirmation_request_event_id": payload.confirmation_request_event_id,
        "confirmation_request_id": payload.confirmation_request_id,
        "decision": payload.decision.value,
        "payload_type": "story_commit_decision",
        "request_digest": payload.request_digest,
        "revision_request": payload.revision_request,
    }


def story_commit_result_payload_value(
    payload: StoryCommitResultPayload,
) -> dict[str, object]:
    return {
        "candidate_id": payload.candidate_id,
        "confirmation_request_id": payload.confirmation_request_id,
        "decision_event_id": payload.decision_event_id,
        "next_action": payload.next_action.value,
        "outcome": payload.outcome.value,
        "payload_type": "story_commit_result",
        "request_digest": payload.request_digest,
        "result": None if payload.result is None else {
            "branch": payload.result.branch,
            "branch_tip": payload.result.branch_tip,
            "candidate_id": payload.result.candidate_id,
            "commit_id": payload.result.commit_id,
            "correlation_id": payload.result.correlation_id,
            "operation_id": payload.result.operation_id,
            "recovery_action": payload.result.recovery_action.value,
            "session_id": payload.result.session_id,
            "state": payload.result.state.value,
        },
    }


def decode_story_commit_request_payload(value: object) -> StoryCommitRequestPayload:
    item = _object(value, {
        "application_result_event_id", "candidate", "choices",
        "confirmation_request_id", "payload_type", "prohibited_side_effects",
        "request_digest", "side_effects",
    }, label="story commit request payload")
    candidate = _decode_story_commit_candidate(item["candidate"])
    return StoryCommitRequestPayload(
        confirmation_request_id=_string(item["confirmation_request_id"], label="commit request ID"),
        application_result_event_id=_string(item["application_result_event_id"], label="application result event ID"),
        candidate=candidate,
        request_digest=_string(item["request_digest"], label="commit request digest"),
        choices=tuple(_enum(StoryCommitDecision, choice, label="commit choice") for choice in _array(item["choices"], label="commit choices")),
        side_effects=tuple(_enum(CollaborationSideEffect, effect, label="commit side effect") for effect in _array(item["side_effects"], label="commit side effects")),
        prohibited_side_effects=tuple(_enum(CollaborationSideEffect, effect, label="prohibited commit side effect") for effect in _array(item["prohibited_side_effects"], label="prohibited commit side effects")),
    )


def decode_story_commit_decision_payload(value: object) -> StoryCommitDecisionPayload:
    item = _object(value, {
        "application_result_event_id", "candidate_id", "confirmation_id",
        "confirmation_request_event_id", "confirmation_request_id", "decision",
        "payload_type", "request_digest", "revision_request",
    }, label="story commit decision payload")
    return StoryCommitDecisionPayload(
        confirmation_request_id=_string(item["confirmation_request_id"], label="commit request ID"),
        confirmation_request_event_id=_string(item["confirmation_request_event_id"], label="commit request event ID"),
        application_result_event_id=_string(item["application_result_event_id"], label="application result event ID"),
        candidate_id=_string(item["candidate_id"], label="commit candidate ID"),
        request_digest=_string(item["request_digest"], label="commit request digest"),
        confirmation_id=_string(item["confirmation_id"], label="commit confirmation ID"),
        decision=_enum(StoryCommitDecision, item["decision"], label="commit decision"),
        revision_request=_optional_string(item["revision_request"], label="commit revision request"),
    )


def decode_story_commit_result_payload(value: object) -> StoryCommitResultPayload:
    item = _object(value, {
        "candidate_id", "confirmation_request_id", "decision_event_id",
        "next_action", "outcome", "payload_type", "request_digest", "result",
    }, label="story commit result payload")
    raw_result = item["result"]
    result = None
    if raw_result is not None:
        result_item = _object(raw_result, {
            "branch", "branch_tip", "candidate_id", "commit_id", "correlation_id",
            "operation_id", "recovery_action", "session_id", "state",
        }, label="story commit result")
        result = StoryCommitResult(
            session_id=_string(result_item["session_id"], label="commit session ID"),
            candidate_id=_string(result_item["candidate_id"], label="commit candidate ID"),
            operation_id=_string(result_item["operation_id"], label="commit operation ID"),
            correlation_id=_string(result_item["correlation_id"], label="commit correlation ID"),
            state=_enum(StoryCommitState, result_item["state"], label="commit state"),
            recovery_action=_enum(StoryCommitRecoveryAction, result_item["recovery_action"], label="commit recovery action"),
            branch=_string(result_item["branch"], label="commit branch"),
            branch_tip=_string(result_item["branch_tip"], label="commit branch tip"),
            commit_id=_optional_string(result_item["commit_id"], label="commit ID"),
        )
    return StoryCommitResultPayload(
        confirmation_request_id=_string(item["confirmation_request_id"], label="commit request ID"),
        decision_event_id=_string(item["decision_event_id"], label="commit decision event ID"),
        candidate_id=_string(item["candidate_id"], label="commit candidate ID"),
        request_digest=_string(item["request_digest"], label="commit request digest"),
        outcome=_enum(StoryCommitOutcomeKind, item["outcome"], label="commit outcome"),
        next_action=_enum(StoryCommitNextAction, item["next_action"], label="commit next action"),
        result=result,
    )
