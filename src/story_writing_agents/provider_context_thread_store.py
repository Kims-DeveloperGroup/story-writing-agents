"""Workspace storage for provider contextual continuation records."""

from __future__ import annotations

from collections.abc import Callable

from .agent_context.contracts.errors import ContextExecutionError, ContextExecutionErrorCode
from .agent_context.manifest import ContextScope
from .errors import WorkspaceFormatError
from .provider_thread_records import (
    PROVIDER_THREAD_ID_PATTERN,
    context_thread_id,
    context_thread_key_payload,
    context_thread_record,
    context_thread_relative,
    tainted_context_thread_record,
    validate_context_thread_key,
)
from .workspace import Workspace


def load_context_thread(
    workspace: Workspace,
    scope: ContextScope,
    provider_id: str,
    definition_fingerprint: str,
    policy_fingerprint: str,
    *,
    require_ready: Callable[[str], None],
) -> str | None:
    """Load only one usable exact scoped continuation; otherwise bootstrap."""

    validate_context_thread_key(
        scope,
        provider_id,
        definition_fingerprint,
        policy_fingerprint,
    )
    require_ready(scope.session_id)
    relative = context_thread_relative(
        scope,
        provider_id,
        definition_fingerprint,
        policy_fingerprint,
    )
    target = workspace.path(relative)
    if not target.exists():
        return None
    try:
        value = workspace.read_json(relative)
    except WorkspaceFormatError:
        return None
    return context_thread_id(
        value,
        scope=scope,
        provider_id=provider_id,
        definition_fingerprint=definition_fingerprint,
        policy_fingerprint=policy_fingerprint,
    )


def store_context_thread(
    workspace: Workspace,
    scope: ContextScope,
    provider_id: str,
    definition_fingerprint: str,
    policy_fingerprint: str,
    thread_id: str,
    *,
    tainted: bool,
    require_owned_access: Callable[[Workspace, str], None],
) -> None:
    """Persist a native continuation in the distinct contextual store."""

    validate_context_thread_key(
        scope,
        provider_id,
        definition_fingerprint,
        policy_fingerprint,
    )
    if (
        type(thread_id) is not str
        or PROVIDER_THREAD_ID_PATTERN.fullmatch(thread_id) is None
        or type(tainted) is not bool
    ):
        raise ContextExecutionError(ContextExecutionErrorCode.PROVIDER_CONTRACT)
    require_owned_access(workspace, scope.session_id)
    key = context_thread_key_payload(
        scope,
        provider_id,
        definition_fingerprint,
        policy_fingerprint,
    )
    workspace.write_json(
        context_thread_relative(
            scope,
            provider_id,
            definition_fingerprint,
            policy_fingerprint,
        ),
        context_thread_record(
            key=key,
            definition_fingerprint=definition_fingerprint,
            policy_fingerprint=policy_fingerprint,
            provider_id=provider_id,
            tainted=tainted,
            thread_id=thread_id,
        ),
    )


def taint_context_thread(
    workspace: Workspace,
    scope: ContextScope,
    provider_id: str,
    definition_fingerprint: str,
    policy_fingerprint: str,
    *,
    require_owned_access: Callable[[Workspace, str], None],
) -> None:
    """Mark one matching contextual continuation permanently unusable."""

    validate_context_thread_key(
        scope,
        provider_id,
        definition_fingerprint,
        policy_fingerprint,
    )
    require_owned_access(workspace, scope.session_id)
    relative = context_thread_relative(
        scope,
        provider_id,
        definition_fingerprint,
        policy_fingerprint,
    )
    target = workspace.path(relative)
    if not target.exists():
        return
    try:
        value = workspace.read_json(relative)
    except WorkspaceFormatError:
        return
    record = tainted_context_thread_record(
        value,
        scope=scope,
        provider_id=provider_id,
        definition_fingerprint=definition_fingerprint,
        policy_fingerprint=policy_fingerprint,
    )
    if record is not None:
        return
    workspace.write_json(relative, record)


__all__ = (
    "load_context_thread",
    "store_context_thread",
    "taint_context_thread",
)
