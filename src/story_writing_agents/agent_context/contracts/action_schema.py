"""Pure schemas for the closed context-action families."""

from __future__ import annotations

from ...provider_wire import (
    hoist_schema_definitions,
    project_action_schema,
)
from ...structured_output_validation import (
    SchemaRules,
    finite_selection_rule,
    finite_subset_rule,
)
from ..actions import (
    CONTEXT_ACTION_POLICY,
    artifact_directory_prefix_rule,
    search_text_rule,
)
from ..manifest import ContextSourceKind
from .call_offers import ContextJsonObjectResultContract
from .errors import (
    ContextExecutionErrorCode,
    execution_error,
)
from .result_schema import validate_provider_schema_node


CONTEXT_ACTION_ROOT_DESCRIPTION = (
    'Gather needed evidence, then return the task result. Choose one currently '
    'permitted agent_action; search finds sources, read obtains contents, and '
    'submit_result completes the task. The runtime owns correlation and action '
    'bookkeeping.'
)


def _closed_provider_object_schema(
    properties: dict[str, object],
    *,
    description: str | None = None,
) -> dict[str, object]:
    schema: dict[str, object] = {
        "additionalProperties": False,
        "properties": properties,
        "required": list(properties),
        "type": "object",
    }
    if description is not None:
        schema["description"] = description
    return schema


def context_action_schema(
    *,
    allowed_actions: tuple[str, ...],
    result_contract: ContextJsonObjectResultContract | SchemaRules,
    allowed_search_source_kinds: tuple[ContextSourceKind, ...],
    action_limits: list[int],
    issued_handle_aliases: tuple[str, ...] = (),
    preserve_action_definitions: bool = False,
) -> dict[str, object]:
    """Build one closed typed action envelope for the exact physical call."""

    if type(result_contract) is SchemaRules:
        wire_format = True
    elif isinstance(result_contract, ContextJsonObjectResultContract):
        wire_format = result_contract.wire_format
    else:
        raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)

    variants: list[dict[str, object]] = []
    if "search" in allowed_actions:
        query_limit = min(action_limits[3], action_limits[4], CONTEXT_ACTION_POLICY.max_queries_per_search)
        source_kind_description = (
            "Choose distinct allowed source kinds; provider order is "
            "not significant and the runtime canonicalizes it."
        )

        def source_kinds_rule(choices: tuple[str, ...]) -> dict[str, object]:
            selection = finite_subset_rule(choices)
            selection["description"] = source_kind_description
            for alternative in selection.get("anyOf", ()):
                alternative["description"] = source_kind_description
            return selection

        query_schema = _closed_provider_object_schema(
            {
                "text": search_text_rule(min(action_limits[7], CONTEXT_ACTION_POLICY.max_query_characters)),
                "source_kinds": (
                    source_kinds_rule(tuple(kind.value for kind in allowed_search_source_kinds))
                    if wire_format else {
                    "description": (
                        source_kind_description
                    ),
                    "items": {
                        "enum": [kind.value for kind in allowed_search_source_kinds],
                        "type": "string",
                    },
                    "maxItems": len(allowed_search_source_kinds),
                    "minItems": 1,
                    "type": "array",
                }),
            }
        )
        search_properties = {
            "kind": {"enum": ["search"], "type": "string"},
            "queries": {
                "items": query_schema,
                "maxItems": query_limit,
                "minItems": 1,
                "type": "array",
            },
        }
        variants.append(_closed_provider_object_schema(search_properties,
            description=(
                "Search authorized sources when evidence is missing and no artifact-directory "
                "restriction is needed; results issue handles for a later read_context action."
            ),
        ))
        if ContextSourceKind.WRITING_ARTIFACT in allowed_search_source_kinds:
            artifact_query = _closed_provider_object_schema({
                "text": search_text_rule(min(action_limits[7], CONTEXT_ACTION_POLICY.max_query_characters)),
                "source_kinds": (source_kinds_rule(("writing_artifact",)) if wire_format else {
                    "type": "array", "items": {"type": "string", "enum": ["writing_artifact"]},
                    "minItems": 1, "maxItems": 1,
                }),
            })
            variants.append(_closed_provider_object_schema({
                **search_properties,
                "queries": {"type": "array", "items": artifact_query,
                            "minItems": 1, "maxItems": query_limit},
                "path_prefix": artifact_directory_prefix_rule(),
            },
                description=(
                    "Search story documents when evidence gathering is restricted to one artifact"
                    " directory; results issue handles for a later read_context action."
                ),
            ))
    if "read" in allowed_actions:
        handle_limit = min(action_limits[5], action_limits[6], CONTEXT_ACTION_POLICY.max_handles_per_read)
        variants.append(
            _closed_provider_object_schema(
                {
                    "kind": {"enum": ["read"], "type": "string"},
                    "handles": (
                        finite_selection_rule(issued_handle_aliases, minimum=1,
                                              maximum=min(handle_limit, len(issued_handle_aliases)))
                        if wire_format else {
                        "items": {"type": "string"},
                        "maxItems": handle_limit,
                        "minItems": 1,
                        "type": "array",
                    }),
                },
                description=(
                    "Read invocation-issued search handles when source contents are needed to "
                    "complete the task; the runtime returns bounded content and evidence IDs."
                ),
            )
        )
    if "answer" in allowed_actions:
        variants.append(
            _closed_provider_object_schema(
                {
                    "kind": {"enum": ["answer"], "type": "string"},
                    "result": result_contract.schema_value(),
                },
                description=(
                    "Submit a terminal task_result permitted by its contract, including an "
                    "evidence limitation when allowed. The runtime attaches successful READ receipts."
                ),
            )
        )
    if not variants:
        raise execution_error(ContextExecutionErrorCode.INVALID_VALUE)
    action_schema: dict[str, object] = (
        variants[0] if len(variants) == 1 else {"anyOf": variants}
    )
    schema = _closed_provider_object_schema(
        {"action": action_schema},
        description=(
            CONTEXT_ACTION_ROOT_DESCRIPTION
        ),
    )
    projected = project_action_schema(schema)
    schema = hoist_schema_definitions(projected)
    validate_provider_schema_node(schema)
    return projected if preserve_action_definitions else schema


__all__ = [
    'CONTEXT_ACTION_ROOT_DESCRIPTION',
    'context_action_schema',
]
