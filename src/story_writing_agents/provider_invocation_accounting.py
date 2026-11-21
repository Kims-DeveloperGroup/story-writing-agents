"""Provider invocation timing and context accounting."""

from __future__ import annotations

import inspect
import time
from collections.abc import Callable
from dataclasses import replace
from typing import Any, Mapping

from .agent_context import (
    ContextComponentKind,
    JsonPath,
    ProviderCallAccounting,
    ProviderCallKind,
    ProviderCallState,
)
from .provider_adapter import (
    ProviderAdapter,
    ProviderBinding,
    ProviderInvocation,
    ProviderResult,
    provider_failure_accounting,
    record_provider_failure_accounting,
)


async def timed_provider_invoke(
    adapter: ProviderAdapter, binding: ProviderBinding, invocation: ProviderInvocation,
) -> ProviderResult:
    """Observe actual adapter latency without changing its result or failure."""

    started = time.monotonic_ns()
    try:
        result = await adapter.invoke(binding, invocation)
    except BaseException as failure:
        facts = provider_failure_accounting(failure)
        if facts.state is ProviderCallState.SUBMITTED:
            record_provider_failure_accounting(
                failure, state=facts.state,
                actual_transport_utf8_bytes=facts.actual_transport_utf8_bytes,
                usage=facts.usage, usage_observation=facts.usage_observation,
                elapsed_ns=time.monotonic_ns() - started,
            )
        raise
    return replace(result, elapsed_ns=time.monotonic_ns() - started) if type(result) is ProviderResult else result


def provider_component_paths(
    payload: Mapping[str, Any],
    *,
    call_kind: ProviderCallKind,
) -> tuple[dict[JsonPath, ContextComponentKind], tuple[JsonPath, ...]]:
    """Attribute known payload values without changing their wire shape."""

    paths: dict[JsonPath, ContextComponentKind] = {}
    top_level = {
        "user_input_text": ContextComponentKind.CURRENT_INPUT,
        "candidate_text": ContextComponentKind.CANDIDATE,
        "debate_history": ContextComponentKind.DEBATE_MESSAGES,
        "prior_agent_outcomes": ContextComponentKind.AGENT_STATE,
        "conversation_history": ContextComponentKind.CONVERSATION_CONTEXT,
        "rag_context": ContextComponentKind.RAG_CONTEXT,
        "conversation_retrieval_observation": ContextComponentKind.RETRIEVED_OBSERVATION,
    }
    for key, kind in top_level.items():
        if key in payload:
            paths[(key,)] = kind

    story_context = payload.get("story_session_context")
    if isinstance(story_context, Mapping):
        for key, kind in (
            ("owner_outcomes", ContextComponentKind.AGENT_STATE),
            ("focused_context", ContextComponentKind.CONVERSATION_CONTEXT),
        ):
            if key in story_context:
                paths[("story_session_context", key)] = kind

    if (
        call_kind is ProviderCallKind.WRITING_ARTIFACT_REFERENCE_READ
        and "references" in payload
    ):
        paths[("references",)] = ContextComponentKind.MANDATORY_REFERENCE_DESCRIPTOR

    reference_root = "untrusted_attached_references"
    reference_context = payload.get(reference_root)
    mandatory_paths: list[JsonPath] = []
    if type(reference_context) is list:
        paths[(reference_root,)] = (
            ContextComponentKind.MANDATORY_REFERENCE_DESCRIPTOR
        )
        for index, item in enumerate(reference_context):
            if isinstance(item, Mapping) and "content_text" in item:
                content_path = (reference_root, index, "content_text")
                paths[content_path] = (
                    ContextComponentKind.MANDATORY_REFERENCE_CONTENT
                )
                mandatory_paths.append(content_path)
    return paths, tuple(mandatory_paths)


def observe_accounting(
    accounting: ProviderCallAccounting | None,
    sink: Callable[[ProviderCallAccounting], object] | None,
) -> None:
    """Offer one content-free call record without changing provider behavior."""

    if accounting is None or sink is None:
        return
    try:
        outcome = sink(accounting)
        if inspect.iscoroutine(outcome):
            outcome.close()
    except BaseException:
        pass


def complete_failure_accounting(
    invocation: ProviderInvocation,
    failure: BaseException,
) -> ProviderCallAccounting | None:
    """Complete prepared accounting from validated adapter failure facts."""

    accounting = invocation.call_accounting
    if accounting is None:
        return None
    failure_accounting = provider_failure_accounting(failure)
    return replace(
        accounting,
        state=failure_accounting.state,
        actual_transport_utf8_bytes=(
            None
        ),
        usage=failure_accounting.usage,
        usage_observation=failure_accounting.usage_observation,
        elapsed_ns=failure_accounting.elapsed_ns,
    )
