"""Immutable premise-analysis, USER choice and cost-attribution contracts.

These values own no storage or workflow authority. Consumers validate their
references against the existing chronological conversation history.
"""

from __future__ import annotations

from .structured_output_validation import SchemaRules, closed_rule, array_rule, nonblank_text_rule

import hashlib
import json
import re
from dataclasses import dataclass, field, fields
from enum import StrEnum
from pathlib import PurePosixPath

from .specialist_routing import ArtifactReviewDomain


VISIBLE_BYTES = 32768
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
_MATERIAL = re.compile(r"[0-9a-f]{64}\Z")
_WORKFLOW = re.compile(r"sha256:[0-9a-f]{64}\Z")


class PremiseChoicePolicy(StrEnum):
    """Exact USER policy for one completed report."""

    USER_CHOICE = "user_choice"
    DELEGATED_MINOR = "delegated_minor"


class PremiseDecisionAction(StrEnum):
    """Choice, rejection and an intentionally unresolved decision."""

    CHOOSE = "choose"
    REJECT = "reject"
    DEFER = "defer"


def material_digest(text: str) -> str:
    """Hash exact whole UTF-8 bytes without normalizing text."""

    _text(text)
    return hashlib.sha256(text.encode("utf-8", errors="strict")).hexdigest()


def premise_value_to_dict(value: object) -> object:
    """Encode only the immutable values declared in this module."""

    if isinstance(value, StrEnum):
        return value.value
    if type(value) in _VALUE_TYPES:
        return {item.name: premise_value_to_dict(getattr(value, item.name))
                for item in fields(value)}
    if type(value) is tuple:
        return [premise_value_to_dict(item) for item in value]
    if value is None or type(value) in (str, int):
        return value
    raise TypeError("unsupported premise value")


def premise_digest(value: object) -> str:
    """Return the current workflow digest of one exact closed value."""

    encoded = json.dumps(premise_value_to_dict(value), ensure_ascii=False,
                         sort_keys=True, separators=(",", ":"), allow_nan=False)
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _identifier(value: object) -> None:
    if type(value) is not str or not _ID.fullmatch(value):
        raise ValueError("premise identity is invalid")


def _digest(value: object, *, material: bool = False) -> None:
    pattern = _MATERIAL if material else _WORKFLOW
    if type(value) is not str or not pattern.fullmatch(value):
        raise ValueError("premise digest is invalid")


def _text(value: object) -> None:
    if type(value) is not str or not value.strip():
        raise ValueError("premise text must be nonempty")
    if len(value.encode("utf-8", errors="strict")) > VISIBLE_BYTES:
        raise ValueError("premise text exceeds the visible-result limit")


def _texts(values: tuple[str, ...]) -> None:
    for value in values:
        _text(value)
    if sum(len(value.encode("utf-8")) for value in values) > VISIBLE_BYTES:
        raise ValueError("premise result exceeds the visible-result limit")


def _integer(value: object, minimum: int, maximum: int | None = None) -> None:
    if type(value) is not int or value < minimum or (
        maximum is not None and value > maximum
    ):
        raise ValueError("premise integer is invalid")


def _tuple(value: object, cls: type, maximum: int, *, minimum: int = 0) -> None:
    if type(value) is not tuple or not minimum <= len(value) <= maximum:
        raise ValueError("premise collection has an invalid size")
    if any(type(item) is not cls for item in value):
        raise TypeError("premise collection has an invalid member")


def _unique(values: tuple[object, ...]) -> None:
    if len(set(values)) != len(values):
        raise ValueError("premise collection has duplicate identities")


def _domains(value: tuple[ArtifactReviewDomain, ...]) -> None:
    _tuple(value, ArtifactReviewDomain, 3, minimum=1)
    _unique(value)
    if value != tuple(item for item in ArtifactReviewDomain if item in value):
        raise ValueError("premise domains must use canonical order")


def _targets(value: tuple[str, ...]) -> None:
    _tuple(value, str, 64)
    _unique(value)
    for path in value:
        if (not path or len(path) > 4096 or "\\" in path or
                PurePosixPath(path).is_absolute() or
                any(part in ("", ".", "..") for part in path.split("/"))):
            raise ValueError("premise target must be a canonical relative path")


@dataclass(frozen=True, slots=True)
class PremiseAuthoringTaskRef:
    task_id: str
    task_revision: int

    def __post_init__(self) -> None:
        _identifier(self.task_id)
        _integer(self.task_revision, 1)


@dataclass(frozen=True, slots=True)
class PremiseAdmissionRef:
    event_id: str
    digest: str

    def __post_init__(self) -> None:
        _identifier(self.event_id)
        _digest(self.digest)


@dataclass(frozen=True, slots=True)
class PremiseReportRef:
    source_conversation_id: str
    report_event_id: str
    report_digest: str
    question_id: str

    def __post_init__(self) -> None:
        for value in (self.source_conversation_id, self.report_event_id, self.question_id):
            _identifier(value)
        _digest(self.report_digest)


@dataclass(frozen=True, slots=True)
class PremiseDecisionCost:
    """Actual matched submissions, never a provider estimate or reservation."""

    step_id: str
    submitted_call_ids: tuple[str, ...]
    rounds_spent: int
    proposal_id: str | None = None
    proposal_revision: int | None = None
    proposal_digest: str | None = None
    execution_attempt_id: str | None = None

    def __post_init__(self) -> None:
        _identifier(self.step_id)
        _tuple(self.submitted_call_ids, str, 12)
        for value in self.submitted_call_ids:
            _identifier(value)
        _unique(self.submitted_call_ids)
        _integer(self.rounds_spent, 0, 2)
        identities = (self.proposal_id, self.proposal_revision,
                      self.proposal_digest, self.execution_attempt_id)
        if any(item is not None for item in identities):
            _identifier(self.proposal_id)
            _integer(self.proposal_revision, 1)
            _digest(self.proposal_digest)
            _identifier(self.execution_attempt_id)

    @property
    def physical_calls_spent(self) -> int:
        return len(self.submitted_call_ids)


@dataclass(frozen=True, slots=True)
class PremiseAuthoringSourceRef:
    task: PremiseAuthoringTaskRef
    cost: PremiseDecisionCost

    def __post_init__(self) -> None:
        if type(self.task) is not PremiseAuthoringTaskRef or type(self.cost) is not PremiseDecisionCost:
            raise TypeError("premise authoring source requires exact values")
        if self.cost.proposal_id is None or self.cost.rounds_spent < 1:
            raise ValueError("premise authoring source lacks completed source identity")


@dataclass(frozen=True, slots=True)
class PremiseFreshWorkAdmission:
    """Authority-free attribution published before its exact front invocation."""

    source_user_event_id: str
    source_user_event_digest: str
    provider_step_id: str
    provider_invocation_id: str
    front_physical_call_limit: int
    offered_question_id: str | None
    offered_authoring_tasks: tuple[PremiseAuthoringTaskRef, ...]
    selected_authoring_task: PremiseAuthoringTaskRef | None
    prior_admission_ref: PremiseAdmissionRef | None = None
    prior_analysis_report_ref: PremiseReportRef | None = None
    prior_authoring_source_ref: PremiseAuthoringSourceRef | None = None
    schema_version: int = 1
    ordinary_front_physical_call_limit: int = 0

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("premise admission version is invalid")
        for value in (self.source_user_event_id, self.provider_step_id, self.provider_invocation_id):
            _identifier(value)
        _digest(self.source_user_event_digest)
        _integer(self.front_physical_call_limit, 1, 2)
        _integer(self.ordinary_front_physical_call_limit, 0, 1)
        if self.offered_question_id is not None:
            _identifier(self.offered_question_id)
        if type(self.offered_authoring_tasks) is not tuple or any(
            type(item) is not PremiseAuthoringTaskRef
            for item in self.offered_authoring_tasks
        ):
            raise TypeError("premise admission offers must be exact task references")
        _unique(self.offered_authoring_tasks)
        if self.selected_authoring_task is not None:
            if type(self.selected_authoring_task) is not PremiseAuthoringTaskRef:
                raise TypeError("selected premise admission task is invalid")
            if self.offered_authoring_tasks != (self.selected_authoring_task,):
                raise ValueError("selected premise admission requires its exact singleton offer")
        if self.offered_question_id is None and not self.offered_authoring_tasks:
            raise ValueError("premise admission offers no fresh work")
        for value, cls in ((self.prior_admission_ref, PremiseAdmissionRef),
                           (self.prior_analysis_report_ref, PremiseReportRef),
                           (self.prior_authoring_source_ref, PremiseAuthoringSourceRef)):
            if value is not None and type(value) is not cls:
                raise TypeError("premise admission parent is invalid")
        if self.ordinary_front_physical_call_limit == 1 and (
            self.front_physical_call_limit != 2
            or self.offered_question_id is None
            or self.selected_authoring_task is not None
            or self.prior_authoring_source_ref is None
            or self.prior_authoring_source_ref.task not in self.offered_authoring_tasks
        ):
            raise ValueError(
                "ordinary front admission requires exact ordinary1 and fresh2 offers"
            )


@dataclass(frozen=True, slots=True)
class PremiseQuestionIntent:
    question_id: str
    slot_id: str
    question: str
    dependent_domains: tuple[ArtifactReviewDomain, ...]

    def __post_init__(self) -> None:
        _identifier(self.question_id)
        _identifier(self.slot_id)
        _text(self.question)
        _domains(self.dependent_domains)


@dataclass(frozen=True, slots=True)
class PremiseDecisionSpec:
    question_id: str
    slot_id: str
    question: str
    dependent_domains: tuple[ArtifactReviewDomain, ...]
    source_user_event_id: str
    source_authoring_task: PremiseAuthoringTaskRef | None = None
    admission_ref: PremiseAdmissionRef | None = None
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("premise specification is invalid")
        self.intent
        _identifier(self.source_user_event_id)
        if self.source_authoring_task is not None and type(self.source_authoring_task) is not PremiseAuthoringTaskRef:
            raise TypeError("premise source task is invalid")
        if self.admission_ref is not None and type(self.admission_ref) is not PremiseAdmissionRef:
            raise TypeError("premise specification admission is invalid")

    @property
    def intent(self) -> PremiseQuestionIntent:
        return PremiseQuestionIntent(self.question_id, self.slot_id, self.question, self.dependent_domains)

    @property
    def question_digest(self) -> str:
        return premise_digest(self)


@dataclass(frozen=True, slots=True)
class PremiseAlternative:
    alternative_key: str
    premise_text: str
    rationale: str
    tradeoffs: tuple[str, ...]
    basis_input_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _identifier(self.alternative_key)
        _tuple(self.tradeoffs, str, 4)
        _tuple(self.basis_input_ids, str, 64, minimum=1)
        for value in self.basis_input_ids:
            _identifier(value)
        _unique(self.basis_input_ids)
        _texts((self.premise_text, self.rationale, *self.tradeoffs))


@dataclass(frozen=True, slots=True)
class PremiseAnalysis:
    assessment: str
    message: str
    alternatives: tuple[PremiseAlternative, ...]

    def __post_init__(self) -> None:
        if self.assessment not in ("alternatives_assessed", "conflict", "insufficient_evidence"):
            raise ValueError("premise analysis assessment is invalid")
        _tuple(self.alternatives, PremiseAlternative, 4)
        _unique(tuple(item.alternative_key for item in self.alternatives))
        if self.assessment == "alternatives_assessed" and not self.alternatives:
            raise ValueError("successful premise analysis needs actual alternatives")
        _texts((self.message, *(text for item in self.alternatives
                               for text in (item.premise_text, item.rationale, *item.tradeoffs))))


@dataclass(frozen=True, slots=True)
class PremiseReplyDisposition:
    reply_event_id: str
    alternative_keys: tuple[str, ...]
    disposition: str
    reason: str
    residual_cost: str

    def __post_init__(self) -> None:
        _identifier(self.reply_event_id)
        _tuple(self.alternative_keys, str, 4)
        for value in self.alternative_keys:
            _identifier(value)
        _unique(self.alternative_keys)
        if self.disposition not in ("used", "modified", "deferred", "rejected", "unavailable"):
            raise ValueError("premise reply disposition is invalid")
        if self.disposition in ("used", "modified") and not self.alternative_keys:
            raise ValueError("used premise analysis needs an actual alternative")
        _texts((self.reason, self.residual_cost))


@dataclass(frozen=True, slots=True)
class PremiseOptionIntent:
    option_key: str
    premise_text: str
    rationale: str
    reply_dispositions: tuple[PremiseReplyDisposition, ...]

    def __post_init__(self) -> None:
        _identifier(self.option_key)
        _tuple(self.reply_dispositions, PremiseReplyDisposition, 3, minimum=1)
        _unique(tuple(item.reply_event_id for item in self.reply_dispositions))
        if not any(item.disposition in {"used", "modified"} and item.alternative_keys for item in self.reply_dispositions):
            raise ValueError("whole premise option requires an actual analyst alternative")
        _texts((self.premise_text, self.rationale, *(text for item in self.reply_dispositions
                                                  for text in (item.reason, item.residual_cost))))


@dataclass(frozen=True, slots=True)
class PremiseDecisionSummary:
    status: str
    message: str
    options: tuple[PremiseOptionIntent, ...]
    recommended_option_key: str | None

    def __post_init__(self) -> None:
        if self.status not in ("options", "clarification"):
            raise ValueError("premise summary status is invalid")
        _tuple(self.options, PremiseOptionIntent, 6)
        _unique(tuple(item.option_key for item in self.options))
        if (self.status == "options") != bool(self.options):
            raise ValueError("premise summary options do not match status")
        if self.recommended_option_key is not None and self.recommended_option_key not in {item.option_key for item in self.options}:
            raise ValueError("premise recommendation is not an actual option")
        _texts((self.message, *(text for option in self.options
                               for text in (option.premise_text, option.rationale,
                                            *(part for item in option.reply_dispositions
                                              for part in (item.reason, item.residual_cost))))))


@dataclass(frozen=True, slots=True)
class PremiseOption:
    option_id: str
    material_digest: str
    option_key: str
    premise_text: str
    rationale: str
    reply_dispositions: tuple[PremiseReplyDisposition, ...]

    def __post_init__(self) -> None:
        _identifier(self.option_id)
        _digest(self.material_digest, material=True)
        self.intent
        if self.material_digest != material_digest(self.premise_text):
            raise ValueError("premise option material does not match")

    @property
    def intent(self) -> PremiseOptionIntent:
        return PremiseOptionIntent(self.option_key, self.premise_text, self.rationale, self.reply_dispositions)


@dataclass(frozen=True, slots=True)
class PremiseReplyRef:
    event_id: str
    specialist_id: str
    reply_digest: str

    def __post_init__(self) -> None:
        _identifier(self.event_id)
        _identifier(self.specialist_id)
        _digest(self.reply_digest)


@dataclass(frozen=True, slots=True)
class PremiseDecisionReport:
    spec: PremiseDecisionSpec
    source_conversation_id: str
    proposal_id: str
    proposal_revision: int
    proposal_digest: str
    step_id: str
    execution_attempt_id: str
    source_reply_refs: tuple[PremiseReplyRef, ...]
    summary: PremiseDecisionSummary
    options: tuple[PremiseOption, ...]
    cost: PremiseDecisionCost
    prior_authoring_cost: PremiseDecisionCost | None = None
    prior_admission_ref: PremiseAdmissionRef | None = None
    schema_version: int = 1
    prior_analysis_report_ref: PremiseReportRef | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        if self.prior_analysis_report_ref is not None and type(self.prior_analysis_report_ref) is not PremiseReportRef:
            raise TypeError("prior analysis costs require an exact report reference")
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("premise report version is invalid")
        for value, cls in ((self.spec, PremiseDecisionSpec), (self.summary, PremiseDecisionSummary),
                           (self.cost, PremiseDecisionCost)):
            if type(value) is not cls:
                raise TypeError("premise report requires exact values")
        for value in (self.source_conversation_id, self.proposal_id, self.step_id, self.execution_attempt_id):
            _identifier(value)
        _integer(self.proposal_revision, 1)
        _digest(self.proposal_digest)
        _tuple(self.source_reply_refs, PremiseReplyRef, 3, minimum=1)
        _unique(tuple(item.event_id for item in self.source_reply_refs))
        _unique(tuple(item.specialist_id for item in self.source_reply_refs))
        _tuple(self.options, PremiseOption, 6)
        _unique(tuple(item.option_id for item in self.options))
        if tuple(item.intent for item in self.options) != self.summary.options:
            raise ValueError("premise report options differ from actual summary")
        ids = tuple(item.event_id for item in self.source_reply_refs)
        if any(tuple(item.reply_event_id for item in option.intent.reply_dispositions) != ids for option in self.options):
            raise ValueError("premise options must disclose every ordered analyst reply")
        if (self.cost.step_id, self.cost.proposal_id, self.cost.proposal_revision,
            self.cost.proposal_digest, self.cost.execution_attempt_id) != (
                self.step_id, self.proposal_id, self.proposal_revision,
                self.proposal_digest, self.execution_attempt_id):
            raise ValueError("premise report cost identity differs")
        if self.cost.rounds_spent != 1:
            raise ValueError("premise analysis has exactly one terminal analyst round")
        if self.prior_authoring_cost is not None and type(self.prior_authoring_cost) is not PremiseDecisionCost:
            raise TypeError("premise prior authoring cost is invalid")
        if self.prior_admission_ref is not None and type(self.prior_admission_ref) is not PremiseAdmissionRef:
            raise TypeError("premise prior admission is invalid")

    @property
    def report_digest(self) -> str:
        return premise_digest(self)

    @property
    def recommended_option_id(self) -> str | None:
        return next((item.option_id for item in self.options
                     if item.intent.option_key == self.summary.recommended_option_key), None)


@dataclass(frozen=True, slots=True)
class PremiseAllowedChoice:
    option_id: str
    material_digest: str

    def __post_init__(self) -> None:
        _identifier(self.option_id)
        _digest(self.material_digest, material=True)


@dataclass(frozen=True, slots=True)
class PremiseDecisionSelection:
    request_id: str
    action: PremiseDecisionAction
    source_conversation_id: str
    report_event_id: str
    report_digest: str
    question_id: str
    choice_policy: PremiseChoicePolicy | None = None
    option_id: str | None = None
    material_digest: str | None = None
    allowed_choices: tuple[PremiseAllowedChoice, ...] = ()
    target_paths: tuple[str, ...] = ()
    authoring_task_id: str | None = None
    expected_authoring_task_revision: int | None = None

    def __post_init__(self) -> None:
        for value in (self.request_id, self.source_conversation_id, self.report_event_id, self.question_id):
            _identifier(value)
        _digest(self.report_digest)
        if type(self.action) is not PremiseDecisionAction:
            raise TypeError("premise decision action is invalid")
        if self.action is PremiseDecisionAction.CHOOSE and self.choice_policy is None:
            object.__setattr__(self, "choice_policy", PremiseChoicePolicy.USER_CHOICE)
        _tuple(self.allowed_choices, PremiseAllowedChoice, 6)
        _unique(tuple(item.option_id for item in self.allowed_choices))
        _targets(self.target_paths)
        if self.authoring_task_id is not None:
            _identifier(self.authoring_task_id)
            _integer(self.expected_authoring_task_revision, 1)
        elif self.expected_authoring_task_revision is not None:
            raise ValueError("premise decision task pair is incomplete")
        if self.action is not PremiseDecisionAction.CHOOSE:
            if any(item is not None for item in (self.choice_policy, self.option_id,
                                                self.material_digest, self.authoring_task_id)) or self.allowed_choices or self.target_paths:
                raise ValueError("reject/defer cannot select material or authoring authority")
        elif self.choice_policy is PremiseChoicePolicy.USER_CHOICE:
            _identifier(self.option_id)
            _digest(self.material_digest, material=True)
            if self.allowed_choices:
                raise ValueError("direct USER choice cannot claim delegation")
        elif self.choice_policy is PremiseChoicePolicy.DELEGATED_MINOR:
            if self.option_id is not None or self.material_digest is not None or not self.allowed_choices:
                raise ValueError("minor delegation needs only an exact report-bound allowlist")
        else:
            raise ValueError("premise choice requires an explicit USER policy")


@dataclass(frozen=True, slots=True)
class PremiseCostLink:
    analysis: PremiseDecisionCost
    prior_authoring: PremiseDecisionCost | None = None
    prior_admission_ref: PremiseAdmissionRef | None = None

    def __post_init__(self) -> None:
        if type(self.analysis) is not PremiseDecisionCost:
            raise TypeError("premise cost link requires actual analysis cost")
        if self.prior_authoring is not None and type(self.prior_authoring) is not PremiseDecisionCost:
            raise TypeError("premise prior authoring cost is invalid")
        if self.prior_admission_ref is not None and type(self.prior_admission_ref) is not PremiseAdmissionRef:
            raise TypeError("premise prior admission is invalid")


@dataclass(frozen=True, slots=True)
class PremiseSelectionCapture:
    selection_event_id: str
    selection: PremiseDecisionSelection
    selected_text: str
    material_digest: str
    option_id: str
    authoring_task_id: str
    authoring_task_revision: int
    slot_id: str
    dependent_domains: tuple[ArtifactReviewDomain, ...]
    cost_link: PremiseCostLink
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("premise capture version is invalid")
        for value in (self.selection_event_id, self.option_id, self.authoring_task_id, self.slot_id):
            _identifier(value)
        if type(self.selection) is not PremiseDecisionSelection or self.selection.action is not PremiseDecisionAction.CHOOSE:
            raise ValueError("premise capture requires an exact choosing USER control")
        _integer(self.authoring_task_revision, 1)
        _domains(self.dependent_domains)
        if self.material_digest != material_digest(self.selected_text):
            raise ValueError("premise capture bytes differ from selected material")
        if type(self.cost_link) is not PremiseCostLink:
            raise TypeError("premise capture requires its actual cost link")
        expected = (1 if self.selection.expected_authoring_task_revision is None
                    else self.selection.expected_authoring_task_revision + 1)
        if self.authoring_task_revision != expected or (
            self.selection.authoring_task_id is not None
            and self.authoring_task_id != self.selection.authoring_task_id
        ):
            raise ValueError("premise choice requires a fresh task/revision")

    @property
    def capture_digest(self) -> str:
        return premise_digest(self)


@dataclass(frozen=True, slots=True)
class PremiseConflict:
    slot_id: str
    capture_digest: str
    reason: str

    def __post_init__(self) -> None:
        _identifier(self.slot_id)
        _digest(self.capture_digest)
        _text(self.reason)


def resolve_premise_choice(selection: PremiseDecisionSelection,
                          report: PremiseDecisionReport) -> PremiseOption:
    """Resolve only actual whole report material under exact USER policy."""

    if (selection.source_conversation_id, selection.report_digest, selection.question_id) != (
        report.source_conversation_id, report.report_digest, report.spec.question_id
    ) or selection.action is not PremiseDecisionAction.CHOOSE:
        raise ValueError("premise selection does not match the current report")
    if selection.choice_policy is PremiseChoicePolicy.USER_CHOICE:
        key = selection.option_id
        allowed = {(selection.option_id, selection.material_digest)}
    else:
        key = report.recommended_option_id
        allowed = {(item.option_id, item.material_digest) for item in selection.allowed_choices}
    actual = {(item.option_id, item.material_digest) for item in report.options}
    if not allowed <= actual:
        raise ValueError("premise allowed choice is not exact report material")
    option = next((item for item in report.options if item.option_id == key), None)
    if option is None or (option.option_id, option.material_digest) not in allowed:
        raise ValueError("supplied recommendation is unavailable or outside USER delegation")
    return option


def _decode(cls: type, value: object, conversions: dict[str, object] | None = None) -> object:
    if type(value) is not dict or set(value) != {item.name for item in fields(cls)}:
        raise ValueError("premise record must contain exactly its current fields")
    result = dict(value)
    for name, converter in (conversions or {}).items():
        if result[name] is not None:
            result[name] = converter(result[name])
    return cls(**result)


def _array(converter):
    def convert(value):
        if type(value) is not list:
            raise TypeError("premise array must be an exact JSON array")
        return tuple(converter(item) for item in value)
    return convert


def premise_question_intent_from_dict(value: object) -> PremiseQuestionIntent:
    if type(value) is dict and type(value.get("question_id")) is str:
        SchemaRules.declare(premise_question_schema(value["question_id"])).require(value)
    return _decode(PremiseQuestionIntent, value, {"dependent_domains": _array(ArtifactReviewDomain)})


def premise_spec_from_dict(value: object) -> PremiseDecisionSpec:
    return _decode(PremiseDecisionSpec, value, {
        "dependent_domains": _array(ArtifactReviewDomain),
        "source_authoring_task": lambda item: _decode(PremiseAuthoringTaskRef, item),
        "admission_ref": lambda item: _decode(PremiseAdmissionRef, item)})


def premise_analysis_from_dict(value: object) -> PremiseAnalysis:
    SchemaRules.declare(premise_analysis_schema(None)).require(value)
    def alternative(item):
        return _decode(PremiseAlternative, item, {
            "tradeoffs": _array(lambda text: text), "basis_input_ids": _array(lambda text: text)})
    return _decode(PremiseAnalysis, value, {"alternatives": _array(alternative)})


def premise_summary_from_dict(value: object) -> PremiseDecisionSummary:
    SchemaRules.declare(premise_summary_schema()).require(value)
    def disposition(item):
        return _decode(PremiseReplyDisposition, item, {"alternative_keys": _array(lambda key: key)})
    def option(item):
        return _decode(PremiseOptionIntent, item, {"reply_dispositions": _array(disposition)})
    return _decode(PremiseDecisionSummary, value, {"options": _array(option)})


def premise_cost_from_dict(value: object) -> PremiseDecisionCost:
    return _decode(PremiseDecisionCost, value, {"submitted_call_ids": _array(lambda key: key)})


def premise_admission_from_dict(value: object) -> PremiseFreshWorkAdmission:
    def source(item):
        return _decode(PremiseAuthoringSourceRef, item, {
            "task": lambda raw: _decode(PremiseAuthoringTaskRef, raw), "cost": premise_cost_from_dict})
    return _decode(PremiseFreshWorkAdmission, value, {
        "offered_authoring_tasks": _array(lambda item: _decode(PremiseAuthoringTaskRef, item)),
        "selected_authoring_task": lambda item: _decode(PremiseAuthoringTaskRef, item),
        "prior_admission_ref": lambda item: _decode(PremiseAdmissionRef, item),
        "prior_analysis_report_ref": lambda item: _decode(PremiseReportRef, item),
        "prior_authoring_source_ref": source})


def premise_report_from_dict(value: object) -> PremiseDecisionReport:
    def option(item):
        return _decode(PremiseOption, item, {"reply_dispositions": _array(
            lambda raw: _decode(PremiseReplyDisposition, raw, {"alternative_keys": _array(lambda key: key)}))})
    return _decode(PremiseDecisionReport, value, {
        "spec": premise_spec_from_dict, "source_reply_refs": _array(lambda item: _decode(PremiseReplyRef, item)),
        "summary": premise_summary_from_dict, "options": _array(option), "cost": premise_cost_from_dict,
        "prior_authoring_cost": premise_cost_from_dict,
        "prior_admission_ref": lambda item: _decode(PremiseAdmissionRef, item),
        "prior_analysis_report_ref": lambda item: _decode(PremiseReportRef, item)})


def premise_selection_from_dict(value: object) -> PremiseDecisionSelection:
    return _decode(PremiseDecisionSelection, value, {
        "action": PremiseDecisionAction, "choice_policy": PremiseChoicePolicy,
        "allowed_choices": _array(lambda item: _decode(PremiseAllowedChoice, item)),
        "target_paths": _array(lambda text: text)})


def premise_capture_from_dict(value: object) -> PremiseSelectionCapture:
    def cost_link(item):
        return _decode(PremiseCostLink, item, {"analysis": premise_cost_from_dict,
            "prior_authoring": premise_cost_from_dict,
            "prior_admission_ref": lambda raw: _decode(PremiseAdmissionRef, raw)})
    return _decode(PremiseSelectionCapture, value, {
        "selection": premise_selection_from_dict, "dependent_domains": _array(ArtifactReviewDomain),
        "cost_link": cost_link})


def premise_conflicts_from_dict(value: object) -> tuple[PremiseConflict, ...]:
    return _array(lambda item: _decode(PremiseConflict, item))(value)


_VALUE_TYPES = (
    PremiseAuthoringTaskRef, PremiseAdmissionRef, PremiseReportRef, PremiseDecisionCost,
    PremiseAuthoringSourceRef, PremiseFreshWorkAdmission, PremiseQuestionIntent,
    PremiseDecisionSpec, PremiseAlternative, PremiseAnalysis, PremiseReplyDisposition,
    PremiseOptionIntent, PremiseDecisionSummary, PremiseOption, PremiseReplyRef,
    PremiseDecisionReport, PremiseAllowedChoice, PremiseDecisionSelection,
    PremiseCostLink, PremiseSelectionCapture, PremiseConflict,
)


def _result_text_rule() -> dict[str, object]:
    return nonblank_text_rule(maximum_characters=VISIBLE_BYTES)


def _result_strings(values: tuple[str, ...] | None = None, *, minimum: int = 0, maximum: int | None = None) -> dict[str, object]:
    if values == ():
        return array_rule(_result_text_rule(), minimum=0, maximum=0)
    item = _result_text_rule() if values is None else {"type": "string", "enum": list(values)}
    return array_rule(item, minimum=minimum, maximum=maximum)


def _result_alternatives(variants: list[dict[str, object]]) -> dict[str, object]:
    return variants[0] if len(variants) == 1 else {"anyOf": variants}


def premise_question_schema(question_id: str) -> dict[str, object]:
    return closed_rule({
        "question_id": {"type": "string", "enum": [question_id]},
        "slot_id": _result_text_rule(),
        "question": _result_text_rule(),
        "dependent_domains": _result_strings(tuple(item.value for item in ArtifactReviewDomain), minimum=1, maximum=3),
    })


def premise_analysis_schema(input_ids: tuple[str, ...]) -> dict[str, object]:
    alternative = closed_rule({
        "alternative_key": {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}\z"}, "premise_text": _result_text_rule(),
        "rationale": _result_text_rule(), "tradeoffs": _result_strings(maximum=4),
        "basis_input_ids": (array_rule({"type": "string"}, minimum=1, maximum=64) if input_ids is None else _result_strings(input_ids, minimum=1, maximum=64)),
    })
    result = closed_rule({
        "assessment": {"type": "string", "enum": ["alternatives_assessed", "conflict", "insufficient_evidence"]},
        "message": _result_text_rule(),
        "alternatives": {"type": "array", "items": alternative, "minItems": 0, "maxItems": 4},
    })
    result["description"] = "Return provisional alternatives grounded in the selected actual inputs; separate tradeoffs and unknowns without drafting a candidate."
    assessed = {**result, "properties": {**result["properties"], "assessment": {"type": "string", "enum": ["alternatives_assessed"]},
                 "alternatives": {**result["properties"]["alternatives"], "minItems": 1}}}
    other = {**result, "properties": {**result["properties"], "assessment": {"type": "string", "enum": ["conflict", "insufficient_evidence"]}}}
    return {"anyOf": [assessed, other], "description": result["description"]}


def premise_summary_schema(reply_alternatives: tuple[tuple[str, tuple[str, ...]], ...] | None = None,
                           unavailable_reply_ids: tuple[str, ...] = ()) -> dict[str, object]:
    identifier = {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}\z"}
    rows = []
    for reply_id, keys in ((None, None),) if reply_alternatives is None else reply_alternatives:
        for status in ("used", "modified", "deferred", "rejected", "unavailable"):
            if status == "unavailable" and reply_alternatives is not None and reply_id not in unavailable_reply_ids:
                continue
            if status in {"used", "modified"} and keys == ():
                continue
            rows.append(closed_rule({"reply_event_id": identifier if reply_id is None else {"type": "string", "enum": [reply_id]},
                "alternative_keys": _result_strings(keys, minimum=1 if status in {"used", "modified"} else 0, maximum=4),
                "disposition": {"type": "string", "enum": [status]},
                "reason": _result_text_rule(), "residual_cost": _result_text_rule()},
                description=(
                    f"Record an analyst reply as {status}; cite selected alternatives "
                    "and retain its reason and residual cost."
                ),
            ))
    dispositions = array_rule(_result_alternatives(rows) if rows else closed_rule({}),
                              minimum=1 if reply_alternatives is None else len(reply_alternatives),
                              maximum=3 if reply_alternatives is None else len(reply_alternatives))
    option = closed_rule({"option_key": identifier, "premise_text": _result_text_rule(),
                          "rationale": _result_text_rule(), "reply_dispositions": dispositions})
    possible = reply_alternatives is None or any(keys for _, keys in reply_alternatives)
    branches = [closed_rule({"status": {"type": "string", "enum": ["clarification"]},
        "message": _result_text_rule(), "options": array_rule(option, maximum=0),
        "recommended_option_key": {
                "type": "null",
                "description": 'No recommendation available; retain USER choice authority.',
            }},
        description=(
            "Request clarification without premise options or a recommendation; "
            "retain USER choice authority."
        ),
    )]
    if possible:
        branches.append(closed_rule({"status": {"type": "string", "enum": ["options"]}, "message": _result_text_rule(),
            "options": array_rule(option, minimum=1, maximum=6),
            "recommended_option_key": {
                    "anyOf": [
                        {
                            **identifier,
                            "description": (
                                "Recommend one declared premise option key; "
                                "retain USER choice authority."
                            ),
                        },
                        {
                            "type": "null",
                            "description": 'Retain USER choice authority.',
                        },
                    ]
                }},
            description=(
                "Offer grounded premise options and an optional recommendation; "
                "retain USER choice authority."
            ),
        ))
    result = _result_alternatives(branches)
    result["description"] = "Integrate actual ordered analyst replies into provisional options. Disclose every reply disposition, reason and residual cost; preserve USER selection authority."
    return result
