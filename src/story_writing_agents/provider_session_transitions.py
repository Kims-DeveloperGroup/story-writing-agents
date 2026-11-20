"""Durable provider session transitions and their access compensation."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

import story_writing_agents.provider_session_access_records as provider_session_access_records
import story_writing_agents.provider_session_artifacts as provider_session_artifacts
import story_writing_agents.provider_session_claim_guard as provider_session_claim_guard
import story_writing_agents.provider_session_lifecycle_queries as provider_session_lifecycle_queries
import story_writing_agents.provider_session_timestamps as provider_session_timestamps
import story_writing_agents.provider_thread_records as provider_thread_records

from .errors import SessionExistsError, WorkspaceFormatError
from .provider_session_access_transactions import ProviderSessionAccessTransactions
from .session_lifecycle import SessionDurableState, SessionLifecycleState
from .workspace import Workspace


class ProviderSessionTransitions:
    """Run durable transitions with the Backend-owned access state."""

    def __init__(
        self,
        access_transactions: ProviderSessionAccessTransactions,
        access_claim_tokens: dict[str, str],
        guard_relative: str,
    ) -> None:
        self._access_transactions = access_transactions
        self._access_claim_tokens = access_claim_tokens
        self._guard_relative = guard_relative

    def create_session_with_access(
        self,
        workspace: Workspace,
        session: str,
        get_session_lifecycle: Callable[[str], SessionLifecycleState],
    ) -> SessionLifecycleState:
        current = get_session_lifecycle(session)
        if current.durable_state is not SessionDurableState.ABSENT:
            raise SessionExistsError(f"provider session already exists: {session}")

        token = uuid.uuid4().hex
        claim = provider_session_access_records.new_access_claim(session, token)
        now = provider_session_timestamps.record_timestamp()
        provider_session_timestamps.parse_record_timestamp(now, label="provider session timestamp")
        manifest: dict[str, Any] = {
            "schema_version": provider_thread_records.PROVIDER_SESSION_SCHEMA_VERSION,
            "session_id": session,
            "durable_state": SessionDurableState.READY.value,
            "created_at": now,
            "updated_at": now,
            "provider_threads": {},
        }
        with provider_session_claim_guard.access_claim_guard(
            workspace,
            provider_session_artifacts.PROVIDER_SESSION_DIRECTORY,
            self._guard_relative,
        ):
            provider_session_artifacts.reject_unsupported_access_artifacts(workspace, session)
            if provider_session_artifacts.manifest_exists(workspace, session):
                provider_session_artifacts.read_manifest(workspace, session, missing_ok=False)
                raise SessionExistsError(
                    f"provider session already exists: {session}"
                )
            if provider_session_artifacts.read_access_claim_if_present(workspace, session) is not None:
                raise WorkspaceFormatError(
                    f"provider session access claim has no manifest: {session}"
                )
            workspace.write_json(
                provider_session_artifacts.access_claim_relative(session),
                claim,
                overwrite=False,
            )
            try:
                provider_session_artifacts.write_manifest(workspace, session, manifest, overwrite=False)
            except BaseException as exc:
                self._access_transactions.discard_access_claim_after_failed_create(
                workspace,
                    session,
                    token,
                    exc,
                )
                raise

        self._access_claim_tokens[session] = token
        state = provider_session_lifecycle_queries.session_lifecycle_from_manifest(
            workspace, manifest, self._access_transactions.access_state,
        )
        return state

    def reopen_session_with_access(
        self,
        workspace: Workspace,
        session: str,
        get_session_lifecycle: Callable[[str], SessionLifecycleState],
    ) -> SessionLifecycleState:
        _, already_owned = self._access_transactions.acquire_access_with_reuse(
            workspace, session, get_session_lifecycle,
        )
        try:
            manifest = provider_session_artifacts.read_manifest(workspace, session, missing_ok=False)
            assert manifest is not None
            if manifest["durable_state"] != SessionDurableState.READY.value:
                manifest["durable_state"] = SessionDurableState.READY.value
                manifest["updated_at"] = provider_session_timestamps.advance_record_timestamp(manifest["updated_at"])
                provider_session_artifacts.write_manifest(workspace, session, manifest)
            state = provider_session_lifecycle_queries.session_lifecycle_from_manifest(
                workspace, manifest, self._access_transactions.access_state,
            )
        except BaseException:
            if not already_owned:
                token = self._access_claim_tokens.get(session)
                if token is not None:
                    self._access_transactions.release_access_claim(workspace, session, token)
            raise
        return state

    def pause_session_and_release_access(
        self,
        workspace: Workspace,
        session: str,
        get_session_lifecycle: Callable[[str], SessionLifecycleState],
    ) -> SessionLifecycleState:
        _, already_owned = self._access_transactions.acquire_access_with_reuse(
            workspace, session, get_session_lifecycle,
        )
        try:
            manifest = provider_session_artifacts.read_manifest(workspace, session, missing_ok=False)
            assert manifest is not None
            if manifest["durable_state"] != SessionDurableState.PAUSED.value:
                manifest["durable_state"] = SessionDurableState.READY.value
                manifest["updated_at"] = provider_session_timestamps.advance_record_timestamp(manifest["updated_at"])
                provider_session_artifacts.write_manifest(workspace, session, manifest)
        except BaseException:
            if not already_owned:
                token = self._access_claim_tokens.get(session)
                if token is not None:
                    self._access_transactions.release_access_claim(workspace, session, token)
            raise
        token = self._access_claim_tokens.get(session)
        if token is not None:
            self._access_transactions.release_access_claim(workspace, session, token)
        state = get_session_lifecycle(session)
        return state


__all__ = ("ProviderSessionTransitions",)
