"""Derive legacy provider thread compatibility and initial reuse status."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Protocol

from .errors import BackendProtocolError, SessionNotFoundError
from .models import AgentDefinition
from .provider_adapter_generations import AdapterGeneration
from .provider_execution_workspace import ProviderExecutionWorkspace
from .provider_message_catalog import WIRE_VERSION


class _LegacyBinding(Protocol):
    @property
    def definition(self) -> AgentDefinition: ...

    @property
    def vendor(self) -> str: ...


def legacy_continuation_fingerprint(
    bound_agent: _LegacyBinding,
    execution_workspace: ProviderExecutionWorkspace | None,
) -> str:
    """Bind legacy thread compatibility to the exact execution workspace."""

    fingerprint = hashlib.sha256(
        (WIRE_VERSION + "\0" + bound_agent.definition.fingerprint).encode("utf-8")
    ).hexdigest()
    if bound_agent.vendor not in {"codex", "antigravity"}:
        return fingerprint
    if type(execution_workspace) is not ProviderExecutionWorkspace:
        raise BackendProtocolError("provider execution workspace is unavailable")
    return execution_workspace.continuation_digest(
        "legacy-definition-policy",
        fingerprint,
    ).removeprefix("sha256:")


def initial_legacy_thread_disposition(
    generation: AdapterGeneration | None,
    *,
    load_thread: Callable[[], object | None],
    agent_id: Callable[[], str],
) -> str:
    """Classify a legacy thread before a logical provider call."""

    try:
        stored = load_thread()
    except SessionNotFoundError:
        stored = None
    if (
        stored is None
        and generation is not None
        and agent_id() in generation.switched_agents
    ):
        return "fresh_after_provider_switch"
    return "fresh" if stored is None else "fresh"
