"""Pure bounded document plans; history and runtime owners qualify all authority."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import hashlib
import json

from .core_document_readiness import AuthoringTarget, DEPENDENCIES, targets_for_paths, ReadinessSource
from .editorial_scope import (EditorialScopeCoverage, EditorialScopeKind,
    editorial_coverage_to_dict, editorial_scope_from_dict, editorial_scope_target_from_dict)
from .lead_task_transitions import LeadTaskRef, task_value_to_dict, task_value_from_dict
from .specialist_routing import (AuthoringScopeRuleSet, AuthoringActivity, AuthoringIntent,
    ArtifactReviewDomain, AuthoringContribution, AuthoringContributionRole, SpecialistAssignmentMode)

MAX_DOCUMENT_PLAN_NODES = 5


def _text(value: object) -> None:
    if type(value) is not str or not value.strip() or len(value.encode("utf-8")) > 262144:
        raise ValueError("document plan requires bounded nonempty text")


def _texts(value: object) -> None:
    if type(value) is not tuple or len(value) > 256 or len(set(value)) != len(value):
        raise ValueError("document plan requires a bounded unique tuple")
    for item in value:
        _text(item)


def _revision(value: object) -> None:
    if type(value) is not int or value < 1:
        raise ValueError("document plan revision must be a positive exact integer")


def document_value_digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(value, ensure_ascii=False,
        sort_keys=True, allow_nan=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def _digest(value: object) -> None:
    _text(value)
    raw = value.removeprefix("sha256:")
    if len(raw) != 64 or any(c not in "0123456789abcdef" for c in raw):
        raise ValueError("document plan requires an exact SHA256 digest")


class DocumentNodeState(StrEnum):
    BLOCKED = "blocked"
    READY = "ready"
    IN_PROGRESS = "in_progress"
    FOLLOWUP_REQUIRED = "followup_required"
    COMPLETE = "complete"


class DocumentNodeNextAction(StrEnum):
    REQUEST_INFORMATION = "request_information"
    PREPARE_NODE = "prepare_node"
    CONFIRM_NODE = "confirm_node"
    RETURN_TO_WRITER = "return_to_writer"
    REVIEW_CANDIDATE = "review_candidate"
    APPLY_CANDIDATE = "apply_candidate"
    PREPARE_COMMIT = "prepare_commit"
    SELECT_NEXT_WINDOW = "select_next_window"
    BUDGET_EXHAUSTED = "budget_exhausted"
    COMPLETE = "complete"


@dataclass(frozen=True, slots=True)
class DocumentPlanRef:
    plan_id: str
    plan_revision: int
    plan_digest: str

    def __post_init__(self) -> None:
        _text(self.plan_id); _revision(self.plan_revision); _digest(self.plan_digest)


@dataclass(frozen=True, slots=True)
class DocumentNodeSpec:
    node_id: str
    target_kind: AuthoringTarget
    target_path: str
    coverage: EditorialScopeCoverage
    book_unit_ids: tuple[str, ...]
    predecessor_node_ids: tuple[str, ...]
    scope_rule: AuthoringScopeRuleSet
    source_input_ids: tuple[str, ...]
    required_condition_ids: tuple[str, ...]
    preservation_conditions: tuple[str, ...]
    expected_result: str

    def __post_init__(self) -> None:
        _text(self.node_id); _text(self.expected_result)
        if type(self.target_kind) is not AuthoringTarget or targets_for_paths((self.target_path,)) != (self.target_kind,):
            raise ValueError("document node requires one exact canonical artifact")
        if (type(self.coverage) is not EditorialScopeCoverage
                or self.coverage.scope.kind is not EditorialScopeKind.KNOWN
                or not self.coverage.scope.targets
                or any(x.path != self.target_path for x in (*self.coverage.scope.targets, *self.coverage.exclusions))):
            raise ValueError("document node coverage cannot combine different artifact paths")
        if type(self.scope_rule) is not AuthoringScopeRuleSet:
            raise TypeError("document node requires canonical prospective duties")
        for value in (self.book_unit_ids, self.predecessor_node_ids, self.source_input_ids,
                      self.required_condition_ids, self.preservation_conditions):
            _texts(value)


@dataclass(frozen=True, slots=True)
class DocumentPlanningGoalRef:
    session_id: str
    conversation_id: str
    user_event_id: str
    content_digest: str

    def __post_init__(self) -> None:
        _text(self.session_id); _text(self.conversation_id); _text(self.user_event_id)
        _digest(self.content_digest)


@dataclass(frozen=True, slots=True)
class DocumentPlan:
    ref: DocumentPlanRef
    goal_objective_id: str | None
    goal_facts_digest: str | None
    nodes: tuple[DocumentNodeSpec, ...]
    remaining_target_paths: tuple[str, ...]
    planning_goal: DocumentPlanningGoalRef | None = None
    unresolved_target: bool = False

    def __post_init__(self) -> None:
        if type(self.ref) is not DocumentPlanRef:
            raise TypeError("document plan requires exact plan identity")
        if type(self.unresolved_target) is not bool:
            raise TypeError("document target resolution must be an exact boolean")
        if self.planning_goal is not None and type(self.planning_goal) is not DocumentPlanningGoalRef:
            raise TypeError("planning goal requires an exact USER source reference")
        if self.goal_objective_id is None or self.goal_facts_digest is None:
            if (self.goal_objective_id is not None or self.goal_facts_digest is not None
                    or self.planning_goal is None or not self.unresolved_target):
                raise ValueError("unresolved literal goal cannot invent artifact objective authority")
        else:
            _text(self.goal_objective_id); _digest(self.goal_facts_digest)
        if (type(self.nodes) is not tuple or not 1 <= len(self.nodes) <= MAX_DOCUMENT_PLAN_NODES
                or any(type(x) is not DocumentNodeSpec for x in self.nodes)):
            raise ValueError("document plan requires one to five exact artifact nodes")
        ids = tuple(x.node_id for x in self.nodes)
        paths = tuple(x.target_path for x in self.nodes)
        kinds = tuple(x.target_kind for x in self.nodes)
        if len(set(ids)) != len(ids) or len(set(paths)) != len(paths) or len(set(kinds)) != len(kinds):
            raise ValueError("document nodes must be unique; at most one exact manuscript per window")
        by_kind = {x.target_kind: x.node_id for x in self.nodes}
        for node in self.nodes:
            dependencies = DEPENDENCIES[node.target_kind]
            if any(kind not in by_kind for kind in dependencies):
                raise ValueError("document plan omits a required predecessor")
            if node.predecessor_node_ids != tuple(by_kind[kind] for kind in dependencies):
                raise ValueError("document plan must retain the current document dependency law")
        _texts(self.remaining_target_paths)
        if any(not targets_for_paths((path,)) or path in paths for path in self.remaining_target_paths):
            raise ValueError("remaining artifacts must be exact distinct unplanned targets")
        value = document_plan_to_dict(self)
        value["ref"].pop("plan_digest")
        if document_value_digest(value) != self.ref.plan_digest:
            raise ValueError("document plan digest differs from its exact goal/window")

    @classmethod
    def create(cls, *, plan_id: str, plan_revision: int, goal_objective_id: str | None,
               goal_facts_digest: str | None, nodes: tuple[DocumentNodeSpec, ...],
               remaining_target_paths: tuple[str, ...] = (),
               planning_goal: DocumentPlanningGoalRef | None = None, unresolved_target: bool = False) -> DocumentPlan:
        value = {"ref": {"plan_id": plan_id, "plan_revision": plan_revision},
            "goal_objective_id": goal_objective_id, "goal_facts_digest": goal_facts_digest,
            "nodes": [document_node_to_dict(x) for x in nodes],
            "remaining_target_paths": list(remaining_target_paths),
            "planning_goal": None if planning_goal is None else planning_goal_to_dict(planning_goal),
            "unresolved_target": unresolved_target}
        return cls(DocumentPlanRef(plan_id, plan_revision, document_value_digest(value)),
                   goal_objective_id, goal_facts_digest, nodes, remaining_target_paths, planning_goal, unresolved_target)


@dataclass(frozen=True, slots=True)
class DocumentCandidateRef:
    candidate_event_id: str
    candidate_id: str
    candidate_revision: int
    candidate_digest: str

    def __post_init__(self) -> None:
        _text(self.candidate_event_id); _text(self.candidate_id)
        _revision(self.candidate_revision); _digest(self.candidate_digest)


@dataclass(frozen=True, slots=True)
class DocumentCandidateUseIntent:
    plan_ref: DocumentPlanRef
    predecessor_node_id: str
    consumer_node_id: str
    candidate: DocumentCandidateRef
    consumer_task_ref: LeadTaskRef
    permitted_scope: EditorialScopeCoverage
    purpose: str
    user_conditions_digest: str

    def __post_init__(self) -> None:
        if (type(self.plan_ref) is not DocumentPlanRef or type(self.candidate) is not DocumentCandidateRef
                or type(self.consumer_task_ref) is not LeadTaskRef
                or type(self.permitted_scope) is not EditorialScopeCoverage
                or self.permitted_scope.scope.kind is not EditorialScopeKind.KNOWN):
            raise TypeError("candidate use requires exact plan/candidate/task/coverage")
        for text in (self.predecessor_node_id, self.consumer_node_id, self.purpose):
            _text(text)
        if self.predecessor_node_id == self.consumer_node_id:
            raise ValueError("candidate input must belong to a distinct predecessor")
        _digest(self.user_conditions_digest)


@dataclass(frozen=True, slots=True)
class DocumentCandidateSelection:
    predecessor_node_id: str
    candidate: DocumentCandidateRef
    permitted_scope: EditorialScopeCoverage
    purpose: str

    def __post_init__(self) -> None:
        _text(self.predecessor_node_id); _text(self.purpose)
        if type(self.candidate) is not DocumentCandidateRef or type(self.permitted_scope) is not EditorialScopeCoverage:
            raise TypeError("candidate selection is a typed input intent, never authority")


@dataclass(frozen=True, slots=True)
class DocumentNodeSelection:
    plan_ref: DocumentPlanRef
    node_id: str | None = None
    target_path: str | None = None
    candidate_uses: tuple[DocumentCandidateSelection, ...] = ()

    def __post_init__(self) -> None:
        if type(self.plan_ref) is not DocumentPlanRef or (self.node_id is None) == (self.target_path is None):
            raise ValueError("select exactly one existing node or remaining exact target")
        _text(self.node_id if self.node_id is not None else self.target_path)
        if self.target_path is not None and not targets_for_paths((self.target_path,)):
            raise ValueError("window selection requires an actual canonical target")
        if (type(self.candidate_uses) is not tuple or len(self.candidate_uses) > MAX_DOCUMENT_PLAN_NODES
                or any(type(x) is not DocumentCandidateSelection for x in self.candidate_uses)):
            raise TypeError("candidate selections must be a bounded exact tuple")


@dataclass(frozen=True, slots=True)
class DocumentNodeBinding:
    plan_ref: DocumentPlanRef
    node_id: str
    task_ref: LeadTaskRef
    budget_lineage_ref: LeadTaskRef
    budget_origin_step_id: str
    candidate_uses: tuple[DocumentCandidateUseIntent, ...] = ()

    def __post_init__(self) -> None:
        if (type(self.plan_ref) is not DocumentPlanRef or type(self.task_ref) is not LeadTaskRef
                or type(self.budget_lineage_ref) is not LeadTaskRef):
            raise TypeError("document binding requires exact current task and budget lineage")
        _text(self.node_id); _text(self.budget_origin_step_id)
        if self.task_ref.session_id != self.budget_lineage_ref.session_id:
            raise ValueError("document budget lineage cannot cross sessions")
        if (type(self.candidate_uses) is not tuple or len(self.candidate_uses) > MAX_DOCUMENT_PLAN_NODES
                or any(type(x) is not DocumentCandidateUseIntent for x in self.candidate_uses)):
            raise ValueError("document candidate uses must be bounded exact intents")
        if any(x.plan_ref != self.plan_ref or x.consumer_node_id != self.node_id
               or x.consumer_task_ref != self.task_ref for x in self.candidate_uses):
            raise ValueError("candidate input permission cannot cross plan/node/task")
        if len({x.predecessor_node_id for x in self.candidate_uses}) != len(self.candidate_uses):
            raise ValueError("a predecessor cannot silently select multiple candidates")


@dataclass(frozen=True, slots=True)
class DocumentNodeView:
    node_id: str
    state: DocumentNodeState
    blocking_conditions: tuple[str, ...]
    required_inputs: tuple[str, ...]
    completion_event_ids: tuple[str, ...]
    provisional_input_confirmation_event_ids: tuple[str, ...]
    next_action: DocumentNodeNextAction
    candidate_refs: tuple[DocumentCandidateRef, ...] = field(default=(), kw_only=True)
    prerequisite_sources: tuple[ReadinessSource, ...] = field(default=(), kw_only=True)

    def __post_init__(self) -> None:
        if type(self.prerequisite_sources) is not tuple or any(type(x) is not ReadinessSource for x in self.prerequisite_sources):
            raise TypeError("prerequisite sources must be current typed READ facts")
        if type(self.candidate_refs) is not tuple or any(type(x) is not DocumentCandidateRef for x in self.candidate_refs):
            raise TypeError("candidate offers must be exact read-only references")
        _text(self.node_id)
        if type(self.state) is not DocumentNodeState or type(self.next_action) is not DocumentNodeNextAction:
            raise TypeError("document views require typed state and next action")
        for value in (self.blocking_conditions, self.required_inputs, self.completion_event_ids,
                      self.provisional_input_confirmation_event_ids):
            _texts(value)
        if self.state is DocumentNodeState.COMPLETE and (self.blocking_conditions or not self.completion_event_ids):
            raise ValueError("document completion requires exact evidence, never approval alone")


@dataclass(frozen=True, slots=True)
class DocumentPlanView:
    plan: DocumentPlan
    current_node_id: str | None
    nodes: tuple[DocumentNodeView, ...]
    physical_calls_spent: int
    rounds_spent: int
    cumulative_plan_physical_calls: int
    remaining_physical_calls: int
    remaining_rounds: int
    goal_text: str | None = field(default=None, kw_only=True)
    pending: bool = field(default=False, kw_only=True)

    def __post_init__(self) -> None:
        if type(self.pending) is not bool:
            raise TypeError("document view proposal state must be typed")
        if self.goal_text is not None and (type(self.goal_text) is not str or not self.goal_text):
            raise TypeError("goal text must be its actual nonempty source text")
        if type(self.plan) is not DocumentPlan or type(self.nodes) is not tuple:
            raise TypeError("document view requires exact plan and node views")
        if (any(type(x) is not DocumentNodeView for x in self.nodes)
                or tuple(x.node_id for x in self.nodes) != tuple(x.node_id for x in self.plan.nodes)
                or self.current_node_id is not None and self.current_node_id not in {x.node_id for x in self.nodes}):
            raise ValueError("document view differs from its exact active window")
        for count in (self.physical_calls_spent, self.rounds_spent, self.cumulative_plan_physical_calls,
                      self.remaining_physical_calls, self.remaining_rounds):
            if type(count) is not int or count < 0:
                raise ValueError("document cost projection requires actual nonnegative counters")


def _object(value: object, names: tuple[str, ...]) -> dict:
    if type(value) is not dict or set(value) != set(names):
        raise ValueError("document record has unknown or missing fields")
    return value


def _tuple(value: object) -> tuple:
    if type(value) is not list:
        raise ValueError("document record requires a JSON array")
    return tuple(value)


def _coverage_from_dict(value: object) -> EditorialScopeCoverage:
    v = _object(value, ("scope", "exclusions"))
    return EditorialScopeCoverage(editorial_scope_from_dict(v["scope"]),
        tuple(editorial_scope_target_from_dict(x) for x in _tuple(v["exclusions"])))


def document_plan_ref_to_dict(value: DocumentPlanRef) -> dict:
    return {"plan_id": value.plan_id, "plan_revision": value.plan_revision, "plan_digest": value.plan_digest}


def document_plan_ref_from_dict(value: object) -> DocumentPlanRef:
    return DocumentPlanRef(**_object(value, ("plan_id", "plan_revision", "plan_digest")))


def _rule_to_dict(value: AuthoringScopeRuleSet) -> dict:
    return {"activity": value.activity.value, "intent": value.intent.value,
        "domains": [x.value for x in value.domains],
        "contributions": [{"domain": x.domain.value, "role": x.role.value} for x in value.contributions],
        "required_domains": [x.value for x in value.required_domains],
        "required_specialists": list(value.required_specialists),
        "assignment_modes": [[a, b.value] for a, b in value.assignment_modes], "schema_version": value.schema_version}


def _rule_from_dict(value: object) -> AuthoringScopeRuleSet:
    v = _object(value, ("activity", "intent", "domains", "contributions", "required_domains",
                         "required_specialists", "assignment_modes", "schema_version"))
    return AuthoringScopeRuleSet(AuthoringActivity(v["activity"]), AuthoringIntent(v["intent"]),
        tuple(ArtifactReviewDomain(x) for x in _tuple(v["domains"])),
        tuple(AuthoringContribution(ArtifactReviewDomain(x["domain"]), AuthoringContributionRole(x["role"]))
              for x in _tuple(v["contributions"]) if _object(x, ("domain", "role"))),
        tuple(ArtifactReviewDomain(x) for x in _tuple(v["required_domains"])),
        _tuple(v["required_specialists"]),
        tuple((a, SpecialistAssignmentMode(b)) for a, b in _tuple(v["assignment_modes"])), v["schema_version"])


def document_node_to_dict(v: DocumentNodeSpec) -> dict:
    return {"node_id": v.node_id, "target_kind": v.target_kind.value, "target_path": v.target_path,
        "coverage": editorial_coverage_to_dict(v.coverage), "book_unit_ids": list(v.book_unit_ids),
        "predecessor_node_ids": list(v.predecessor_node_ids), "scope_rule": _rule_to_dict(v.scope_rule),
        "source_input_ids": list(v.source_input_ids), "required_condition_ids": list(v.required_condition_ids),
        "preservation_conditions": list(v.preservation_conditions), "expected_result": v.expected_result}


def document_node_from_dict(value: object) -> DocumentNodeSpec:
    v = _object(value, ("node_id", "target_kind", "target_path", "coverage", "book_unit_ids",
        "predecessor_node_ids", "scope_rule", "source_input_ids", "required_condition_ids",
        "preservation_conditions", "expected_result"))
    return DocumentNodeSpec(v["node_id"], AuthoringTarget(v["target_kind"]), v["target_path"],
        _coverage_from_dict(v["coverage"]), _tuple(v["book_unit_ids"]), _tuple(v["predecessor_node_ids"]),
        _rule_from_dict(v["scope_rule"]), _tuple(v["source_input_ids"]), _tuple(v["required_condition_ids"]),
        _tuple(v["preservation_conditions"]), v["expected_result"])


def document_plan_to_dict(v: DocumentPlan) -> dict:
    return {"ref": document_plan_ref_to_dict(v.ref), "goal_objective_id": v.goal_objective_id,
        "goal_facts_digest": v.goal_facts_digest, "nodes": [document_node_to_dict(x) for x in v.nodes],
        "remaining_target_paths": list(v.remaining_target_paths),
        "planning_goal": None if v.planning_goal is None else planning_goal_to_dict(v.planning_goal),
        "unresolved_target": v.unresolved_target}


def document_plan_from_dict(value: object) -> DocumentPlan:
    v = _object(value, ("ref", "goal_objective_id", "goal_facts_digest", "nodes", "remaining_target_paths", "planning_goal", "unresolved_target"))
    return DocumentPlan(document_plan_ref_from_dict(v["ref"]), v["goal_objective_id"], v["goal_facts_digest"],
        tuple(document_node_from_dict(x) for x in _tuple(v["nodes"])), _tuple(v["remaining_target_paths"]),
        None if v["planning_goal"] is None else planning_goal_from_dict(v["planning_goal"]), v["unresolved_target"])


def planning_goal_to_dict(v: DocumentPlanningGoalRef) -> dict:
    return {"session_id": v.session_id, "conversation_id": v.conversation_id,
            "user_event_id": v.user_event_id, "content_digest": v.content_digest}


def planning_goal_from_dict(value: object) -> DocumentPlanningGoalRef:
    return DocumentPlanningGoalRef(**_object(value, ("session_id", "conversation_id", "user_event_id", "content_digest")))


def document_candidate_use_to_dict(v: DocumentCandidateUseIntent) -> dict:
    c = v.candidate
    return {"plan_ref": document_plan_ref_to_dict(v.plan_ref), "predecessor_node_id": v.predecessor_node_id,
        "consumer_node_id": v.consumer_node_id, "candidate": {"candidate_event_id": c.candidate_event_id,
        "candidate_id": c.candidate_id, "candidate_revision": c.candidate_revision, "candidate_digest": c.candidate_digest},
        "consumer_task_ref": task_value_to_dict(v.consumer_task_ref),
        "permitted_scope": editorial_coverage_to_dict(v.permitted_scope), "purpose": v.purpose,
        "user_conditions_digest": v.user_conditions_digest}


def document_candidate_use_from_dict(value: object) -> DocumentCandidateUseIntent:
    v = _object(value, ("plan_ref", "predecessor_node_id", "consumer_node_id", "candidate",
                       "consumer_task_ref", "permitted_scope", "purpose", "user_conditions_digest"))
    c = _object(v["candidate"], ("candidate_event_id", "candidate_id", "candidate_revision", "candidate_digest"))
    return DocumentCandidateUseIntent(document_plan_ref_from_dict(v["plan_ref"]), v["predecessor_node_id"],
        v["consumer_node_id"], DocumentCandidateRef(**c), task_value_from_dict(LeadTaskRef, v["consumer_task_ref"]),
        _coverage_from_dict(v["permitted_scope"]), v["purpose"], v["user_conditions_digest"])


def document_node_binding_to_dict(v: DocumentNodeBinding) -> dict:
    return {"plan_ref": document_plan_ref_to_dict(v.plan_ref), "node_id": v.node_id,
        "task_ref": task_value_to_dict(v.task_ref), "budget_lineage_ref": task_value_to_dict(v.budget_lineage_ref),
        "budget_origin_step_id": v.budget_origin_step_id,
        "candidate_uses": [document_candidate_use_to_dict(x) for x in v.candidate_uses]}


def document_node_binding_from_dict(value: object) -> DocumentNodeBinding:
    v = _object(value, ("plan_ref", "node_id", "task_ref", "budget_lineage_ref", "budget_origin_step_id", "candidate_uses"))
    return DocumentNodeBinding(document_plan_ref_from_dict(v["plan_ref"]), v["node_id"],
        task_value_from_dict(LeadTaskRef, v["task_ref"]), task_value_from_dict(LeadTaskRef, v["budget_lineage_ref"]),
        v["budget_origin_step_id"], tuple(document_candidate_use_from_dict(x) for x in _tuple(v["candidate_uses"])))


def document_candidate_selection_to_dict(v: DocumentCandidateSelection) -> dict:
    c = v.candidate
    return {"predecessor_node_id": v.predecessor_node_id, "candidate": {"candidate_event_id": c.candidate_event_id,
        "candidate_id": c.candidate_id, "candidate_revision": c.candidate_revision, "candidate_digest": c.candidate_digest},
        "permitted_scope": editorial_coverage_to_dict(v.permitted_scope), "purpose": v.purpose}


def document_candidate_selection_from_dict(value: object) -> DocumentCandidateSelection:
    v = _object(value, ("predecessor_node_id", "candidate", "permitted_scope", "purpose"))
    c = _object(v["candidate"], ("candidate_event_id", "candidate_id", "candidate_revision", "candidate_digest"))
    return DocumentCandidateSelection(v["predecessor_node_id"], DocumentCandidateRef(**c),
        _coverage_from_dict(v["permitted_scope"]), v["purpose"])


def document_node_selection_to_dict(v: DocumentNodeSelection) -> dict:
    return {"plan_ref": document_plan_ref_to_dict(v.plan_ref), "node_id": v.node_id, "target_path": v.target_path,
        "candidate_uses": [document_candidate_selection_to_dict(x) for x in v.candidate_uses]}


def document_node_selection_from_dict(value: object) -> DocumentNodeSelection:
    v = _object(value, ("plan_ref", "node_id", "target_path", "candidate_uses"))
    return DocumentNodeSelection(document_plan_ref_from_dict(v["plan_ref"]), v["node_id"], v["target_path"],
        tuple(document_candidate_selection_from_dict(x) for x in _tuple(v["candidate_uses"])))
