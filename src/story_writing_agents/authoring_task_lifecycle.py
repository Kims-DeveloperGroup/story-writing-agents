"""Pure host-owned task binding. No provider, persistence, or execution authority."""

from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
import hashlib
import re

from .specialist_routing import (AuthoringScope, AuthoringScopeSpec, bind_authoring_scope,
    stage_plan_spec_from_dict, bind_stage_plan, stage_plan_to_dict)
from .review_contracts import (preliminary_intent_from_value, bind_preliminary_review,
    preliminary_review_to_dict)


def task_identity_for_origin(origin: str) -> str:
    if type(origin) is not str or not origin:
        raise ValueError("task origin must be a runtime-issued identity")
    return "task_" + hashlib.sha256(origin.encode("utf-8")).hexdigest()[:48]


@dataclass(frozen=True, slots=True)
class TaskBindingContext:
    current_scope: AuthoringScope | None
    new_task_id: str
    pinned: bool = False
    locked: bool = False
    explicit_new: bool = False
    reserved_pair: tuple[str, int] | None = None
    material_changed: bool = False
    authority_free: bool = False
    prior_material_key: str | None = None
    proposed_material_key: str | None = None

    def __post_init__(self) -> None:
        if self.current_scope is not None and type(self.current_scope) is not AuthoringScope:
            raise TypeError("binding context requires a bound host scope")
        if any(type(value) is not bool for value in (self.pinned, self.locked, self.explicit_new, self.material_changed, self.authority_free)):
            raise TypeError("binding conditions must be host-qualified booleans")
        if self.reserved_pair is not None:
            if type(self.reserved_pair) is not tuple or len(self.reserved_pair) != 2:
                raise TypeError("reserved binding requires one exact pair")
            TaskBindingPlan(*self.reserved_pair, "reserved")
        TaskBindingPlan(self.new_task_id, 1, "new")
        for key in (self.prior_material_key, self.proposed_material_key):
            if key is not None and type(key) is not str:
                raise TypeError("capture comparison values must be canonical strings")


@dataclass(frozen=True, slots=True)
class TaskBindingPlan:
    task_id: str
    task_revision: int
    transition: str

    def __post_init__(self) -> None:
        if (type(self.task_id) is not str or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", self.task_id) is None
                or type(self.task_revision) is not int or self.task_revision < 1):
            raise ValueError("binding requires a runtime task identity and positive revision")
        if self.transition not in {"new", "reserved", "refine", "continue", "revise", "supersede", "locked", "comparison"}:
            raise ValueError("unknown task lifecycle transition")

    def bind(self, spec: AuthoringScopeSpec) -> AuthoringScope:
        return bind_authoring_scope(spec, self.task_id, self.task_revision)


def resolve_task_binding(spec: AuthoringScopeSpec, context: TaskBindingContext) -> TaskBindingPlan:
    """Resolve one logical operation; callers replay its origin and publish atomically."""
    if type(spec) is not AuthoringScopeSpec or type(context) is not TaskBindingContext:
        raise TypeError("task binding requires exact host inputs")
    current = context.current_scope
    if current is None or context.explicit_new or spec.intent is not current.intent:
        if context.reserved_pair is not None:
            return TaskBindingPlan(*context.reserved_pair, "reserved")
        return TaskBindingPlan(context.new_task_id, 1, "new")
    semantic_change = any(getattr(spec, name) != getattr(current, name) for name in
        ("target_paths", "domains", "contributions", "dependency_slots", "interpretation")) or context.material_changed
    if context.prior_material_key is not None and context.proposed_material_key is not None:
        semantic_change = semantic_change or context.prior_material_key != context.proposed_material_key
    # Activity is a workflow phase. Response prose is not the captured brief.
    if context.locked and semantic_change:
        return TaskBindingPlan(current.task_id, current.task_revision + 1, "supersede")
    if context.authority_free:
        return TaskBindingPlan(current.task_id, current.task_revision, "continue")
    if context.reserved_pair is not None:
        identity, revision = context.reserved_pair
        return TaskBindingPlan(identity, revision, "reserved")
    if context.pinned and semantic_change:
        return TaskBindingPlan(current.task_id, current.task_revision + 1, "revise")
    return TaskBindingPlan(current.task_id, current.task_revision,
        "refine" if semantic_change else "continue")


def bind_task_outcome(value: dict[str, object], binding: TaskBindingPlan, *,
                      preliminary_targets: tuple = ()) -> dict[str, object]:
    """Apply one host binding to every correlated nested result, without publishing."""
    result = deepcopy(value)
    scope = result.get("authoring_scope")
    if type(scope) is not dict:
        return result
    scope["task"] = f"{binding.task_id}@{binding.task_revision}"
    proposal = result.get("proposal", result.get("next_intent"))
    if type(proposal) is dict and "stage_plan" in proposal:
        proposal["stage_plan"] = stage_plan_to_dict(bind_stage_plan(
            stage_plan_spec_from_dict(proposal["stage_plan"]), binding.task_id, binding.task_revision))
    if "preliminary_review" in result:
        spec = preliminary_intent_from_value(result["preliminary_review"], preliminary_targets,
            target_paths=tuple(scope["target_paths"]))
        result["preliminary_review"] = preliminary_review_to_dict(
            bind_preliminary_review(spec, binding.task_id, binding.task_revision))
    return result
