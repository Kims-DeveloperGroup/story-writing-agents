"""Identity-free agent views of declared runtime metadata; durable records stay complete."""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy

HOST_TASK_FIELDS = frozenset({
    "task_id", "task_revision", "task_revisions", "task_reference",
    "target_task_id", "target_task_revision", "origin_task_id", "origin_task_revision",
    "authoring_task_id", "authoring_task_revision", "expected_authoring_task_revision",
    "fresh_task_id", "offered_authoring_tasks", "selected_authoring_task",
    "source_authoring_task", "source_task_ref", "task_ref", "associated_task",
    "associated_task_ref", "authoring_tasks", "available_writing_tasks",
})


LITERAL_TASK_FIELDS = frozenset({"text", "content", "selected_text", "response_text", "message",
    "instruction", "user_input", "source_text", "candidate_text", "original_request"})

TASK_CONTEXT_SHAPES = frozenset("""runtime_disclosure authoring_scope writing_task_scope task_scope scope
confirmed_scope current_scope current_writing_scope stage_plan questions preliminary_review
final_review_evidence authoring_admission task_context task_transition selected_task_materials
exploration_adoption premise_selection premise_decision fresh_work_admission discussion_constraints admission capture
selection spec request decision start started basis report response prior_authoring_source_ref editorial_record editorial_records editorial_state
editorial_context records record_content details draft current_records selected_records revisions""".split())


def project_agent_task_metadata(value: object, shape: str = "") -> object:
    """Project declared runtime records only; unknown and source-owned bodies stay literal."""
    if shape in LITERAL_TASK_FIELDS:
        return deepcopy(value)
    if shape and shape not in TASK_CONTEXT_SHAPES and shape not in TASK_CONTEXT_SHAPES:
        return deepcopy(value)
    if isinstance(value, (list, tuple)):
        return [project_agent_task_metadata(item, shape) for item in value]
    if not isinstance(value, Mapping):
        return deepcopy(value)
    result = {}
    for name, item in value.items():
        if name in HOST_TASK_FIELDS or (name == "task" and shape in
                {"authoring_scope", "writing_task_scope", "task_scope", "scope", "confirmed_scope"}):
            continue
        result[name] = project_agent_task_metadata(item, name)
    return result
