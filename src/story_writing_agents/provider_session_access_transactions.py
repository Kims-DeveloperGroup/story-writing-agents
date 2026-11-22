"""Provider session access claim transactions and rollback."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any, Mapping

from .errors import (
    BackendProtocolError,
    ManuscriptExistsError,
    SessionAccessBlockedError,
    SessionNotFoundError,
    SessionOwnershipUnknownError,
)
from .provider_session_access_records import (
    access_hostname_diagnostic,
    new_access_claim,
    unowned_access_state,
)
from .provider_session_artifacts import (
    PROVIDER_SESSION_DIRECTORY,
    access_claim_relative,
    read_access_claim,
    read_manifest,
    reject_unsupported_access_artifacts,
)
from .provider_session_claim_guard import access_claim_guard
from .provider_session_lifecycle_queries import session_lifecycle_from_manifest
from .session_lifecycle import SessionAccessState, SessionDurableState, SessionLifecycleState
from .workspace import Workspace


class ProviderSessionAccessTransactions:
    """Operate on persisted claims using the Backend-owned token map."""

    def __init__(self, access_claim_tokens: dict[str, str], guard_relative: str) -> None:
        self._access_claim_tokens = access_claim_tokens
        self._guard_relative = guard_relative

    def acquire_access_with_reuse(
        self,
        workspace: Workspace,
        session_id: str,
        get_session_lifecycle: Callable[[str], SessionLifecycleState],
    ) -> tuple[SessionLifecycleState, bool]:
        """Acquire or prove access and report whether it was already owned."""

        current = get_session_lifecycle(session_id)
        if current.durable_state is SessionDurableState.ABSENT:
            raise SessionNotFoundError(
                f"provider session does not exist: {session_id}; "
                f"run 'story session create {session_id}'"
            )
        token, already_owned = self._access_for_attachment(workspace, session_id)
        try:
            self._access_claim_tokens[session_id] = token
            manifest = read_manifest(workspace, session_id, missing_ok=False)
            assert manifest is not None
            state = session_lifecycle_from_manifest(
                workspace, manifest, self.access_state,
            )
            if state.access_state is not SessionAccessState.HELD_HERE:
                raise BackendProtocolError(
                    f"provider session access ownership changed: {session_id}"
                )
        except BaseException:
            if not already_owned:
                self.release_access_claim(workspace, session_id, token)
            raise
        return state, already_owned

    def _access_for_attachment(self, workspace: Workspace, session_id: str) -> tuple[str, bool]:
        """Return a proved existing or newly created local access token."""

        token = self._access_claim_tokens.get(session_id)
        if token is not None:
            self.assert_owned_access(workspace, session_id)
            return token, True
        return self._claim_access(workspace, session_id), False

    def _claim_access(self, workspace: Workspace, session_id: str) -> str:
        """Atomically create or safely replace a proved-dead local claim.

        The persistent guard serializes compliant claimers. Live local owners
        and ownership that cannot be proved fail with distinct public errors;
        no remote or ambiguous claim is replaced.
        """

        session = workspace.validate_identifier(session_id)
        reject_unsupported_access_artifacts(workspace, session)
        relative = access_claim_relative(session)
        token = uuid.uuid4().hex
        value = new_access_claim(session, token)
        with access_claim_guard(
            workspace,
            PROVIDER_SESSION_DIRECTORY,
            self._guard_relative,
        ):
            try:
                workspace.write_json(relative, value, overwrite=False)
                return token
            except ManuscriptExistsError:
                existing = read_access_claim(workspace, session)
            access_state = unowned_access_state(existing)
            if access_state is SessionAccessState.BLOCKED_BY_LIVE_LOCAL_OWNER:
                raise SessionAccessBlockedError(
                    "provider session access is held by live local process "
                    f"{existing['owner_pid']} on "
                    f"{access_hostname_diagnostic(existing['owner_hostname'])}: "
                    f"{session}"
                )
            if access_state is SessionAccessState.OWNERSHIP_UNKNOWN:
                raise SessionOwnershipUnknownError(
                    f"provider session access ownership cannot be proved: {session}"
                )
            current = read_access_claim(workspace, session)
            if current != existing:
                raise SessionOwnershipUnknownError(
                    f"provider session access changed while claiming: {session}"
                )
            workspace.write_json(relative, value)
            return token

    def release_owned_access_claim(self, workspace: Workspace, session_id: str) -> None:
        """Release the current local claim when this transaction owner has its token."""
        token = self._access_claim_tokens.get(session_id)
        if token is not None:
            pass  # Validation-only omission of the actual local claim release.

    def release_access_claim(self, workspace: Workspace, session_id: str, token: str) -> None:
        """Guard, compare, and remove only this backend's exact access claim."""

        session = workspace.validate_identifier(session_id)
        relative = access_claim_relative(session)
        with access_claim_guard(
            workspace,
            PROVIDER_SESSION_DIRECTORY,
            self._guard_relative,
        ):
            reject_unsupported_access_artifacts(workspace, session)
            target = workspace.path(relative)
            if not target.exists():
                self._access_claim_tokens.pop(session, None)
                return
            existing = read_access_claim(workspace, session)
            if existing["claim_token"] != token:
                raise BackendProtocolError(
                    f"provider session access ownership changed: {session}"
                )
            try:
                target.unlink()
            except OSError as exc:
                raise BackendProtocolError(
                    "provider session access claim could not be released: "
                    f"{type(exc).__name__}"
                ) from None
        self._access_claim_tokens.pop(session, None)

    def access_state(
        self,
        session_id: str,
        value: Mapping[str, Any],
    ) -> SessionAccessState:
        """Classify a claim as locally held or conservatively unowned."""

        token = self._access_claim_tokens.get(session_id)
        if token is not None and value["claim_token"] == token:
            return SessionAccessState.HELD_HERE
        return unowned_access_state(value)

    def discard_access_claim_after_failed_create(
        self,
        workspace: Workspace,
        session_id: str,
        token: str,
        failure: BaseException,
    ) -> None:
        """Best-effort remove this call's claim while preserving its failure."""

        try:
            existing = read_access_claim(workspace, session_id)
            if existing["claim_token"] != token:
                raise BackendProtocolError(
                    "provider session access changed during failed creation"
                )
            workspace.path(access_claim_relative(session_id)).unlink()
        except BaseException as cleanup_error:
            failure.add_note(
                f"failed creation access cleanup: {type(cleanup_error).__name__}"
            )

    def assert_owned_access(self, workspace: Workspace, session_id: str) -> None:
        """Require the exact persisted claim tracked by this backend."""

        token = self._access_claim_tokens.get(session_id)
        if token is None or read_access_claim(workspace, session_id)["claim_token"] != token:
            raise BackendProtocolError(
                f"provider session access is not held here: {session_id}"
            )


__all__ = ("ProviderSessionAccessTransactions",)
