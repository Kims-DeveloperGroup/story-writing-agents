"""Store ordinary provider thread records in the session manifest."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import story_writing_agents.provider_session_artifacts as provider_session_artifacts
import story_writing_agents.provider_thread_records as provider_thread_records

from .errors import BackendProtocolError
from .models import AgentDefinition
from .workspace import Workspace


def load_manifest_thread(
    workspace: Workspace,
    session_id: str,
    definition: AgentDefinition,
    *,
    fingerprint: str | None = None,
) -> dict[str, Any] | None:
    effective_fingerprint = (
        definition.fingerprint if fingerprint is None else fingerprint
    )
    key = provider_thread_records.thread_key(
        definition,
        fingerprint=effective_fingerprint,
    )
    manifest = provider_session_artifacts.read_manifest(workspace, session_id, missing_ok=False)
    assert manifest is not None
    return provider_thread_records.select_thread_record(
        manifest, key, effective_fingerprint,
    )


def store_manifest_thread(
    workspace: Workspace,
    session_id: str,
    definition: AgentDefinition,
    thread_id: str,
    *,
    tainted: bool,
    fingerprint: str | None = None,
    require_owned_access: Callable[[Workspace, str], None],
) -> None:
    if (
        not isinstance(thread_id, str)
        or provider_thread_records.PROVIDER_THREAD_ID_PATTERN.fullmatch(thread_id) is None
    ):
        provider_name = "Codex" if definition.backend == "codex" else "Antigravity"
        raise BackendProtocolError(f"{provider_name} thread ID is invalid")
    require_owned_access(workspace, session_id)
    effective_fingerprint = (
        definition.fingerprint if fingerprint is None else fingerprint
    )
    if provider_thread_records.PROVIDER_FINGERPRINT_PATTERN.fullmatch(effective_fingerprint) is None:
        raise BackendProtocolError("provider thread fingerprint is invalid")
    manifest = provider_session_artifacts.read_manifest(workspace, session_id, missing_ok=False)
    assert manifest is not None
    provider_thread_records.store_thread_record(
        manifest,
        definition,
        thread_id,
        tainted=tainted,
        effective_fingerprint=effective_fingerprint,
    )
    provider_session_artifacts.write_manifest(workspace, session_id, manifest)


def taint_manifest_thread(
    workspace: Workspace,
    session_id: str,
    definition: AgentDefinition,
    *,
    fingerprint: str | None = None,
) -> None:
    manifest = provider_session_artifacts.read_manifest(workspace, session_id, missing_ok=False)
    assert manifest is not None
    if not provider_thread_records.taint_thread_record(
        manifest, definition, fingerprint=fingerprint,
    ):
        return
    provider_session_artifacts.write_manifest(workspace, session_id, manifest)


__all__ = (
    "load_manifest_thread",
    "store_manifest_thread",
    "taint_manifest_thread",
)
