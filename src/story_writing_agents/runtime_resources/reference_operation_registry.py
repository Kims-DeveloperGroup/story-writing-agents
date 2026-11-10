"""Process-wide exact-workspace reference authority and cleanup registry."""
from __future__ import annotations
import os
import threading
from contextlib import AbstractContextManager
from ..errors import BackendProtocolError
from typing import Callable
from ..writing_artifacts import ReferenceAuthorizationErrorCode, ReferenceOperationFailure
from ..writing_artifacts.runtime_references import (
    RuntimeReferenceOperation,
    prepare_registered_guarded_runtime_reference_operation,
    reference_operation_belongs_to_session,
)
from ..workspace.initialization import SessionArtifactBinding
from ..workspace import Workspace
from ..writing_artifacts.repository import WritingArtifactRepository
from functools import partial
from . import session_worktree_access


_reference_operation_registry_lock = threading.RLock()

_reference_operation_registry_pid = os.getpid()

_reference_operation_registry: dict[
    tuple[str, str, tuple[int, int], str], RuntimeReferenceOperation
] = {}

def register_reference_operation(workspace: Workspace, operation: RuntimeReferenceOperation) -> None:
    """Retain exact cleanup authority before guarded handle publication."""

    _require_current_reference_registry()
    operation_id = operation.operation_id
    if operation_id is None:
        return
    if not reference_operation_belongs_to_session(
        operation_id,
        operation.session_id,
    ):
        raise BackendProtocolError("reference operation identity is invalid")
    physical_identity = operation.physical_workspace_identity
    if physical_identity is None:
        raise BackendProtocolError(
            "registered reference operation lacks physical workspace identity"
        )
    key = (
        str(workspace.root),
        operation.session_id,
        physical_identity,
        operation_id,
    )
    with _reference_operation_registry_lock:
        current = _reference_operation_registry.get(key)
        if current is not None and current is not operation:
            raise BackendProtocolError("reference operation is already registered")
        _reference_operation_registry[key] = operation

def unregister_terminal_reference_operation(workspace: Workspace,
    operation: RuntimeReferenceOperation
) -> None:
    _require_current_reference_registry()
    operation_id = operation.operation_id
    if operation_id is None or not operation.finished:
        return
    physical_identity = operation.physical_workspace_identity
    if physical_identity is None:
        return
    key = (
        str(workspace.root),
        operation.session_id,
        physical_identity,
        operation_id,
    )
    with _reference_operation_registry_lock:
        if _reference_operation_registry.get(key) is operation:
            _reference_operation_registry.pop(key)

def registered_reference_operation_ids(workspace: Workspace,
    session_id: str,
    physical_workspace_identity: tuple[int,
        int
    ]
) -> frozenset[str]:
    """Snapshot exact live IDs while the caller owns repository access."""

    _require_current_reference_registry()
    workspace_root = str(workspace.root)
    with _reference_operation_registry_lock:
        return frozenset(
            operation_id
            for (
                owned_root,
                owned_session,
                owned_identity,
                operation_id,
            ) in (
                _reference_operation_registry
            )
            if (
                owned_root == workspace_root
                and owned_session == session_id
                and owned_identity == physical_workspace_identity
            )
        )

def has_cleanup_pending_reference_operation(workspace: Workspace, session_id: str) -> bool:
    """Check a workspace/session snapshot without holding its registry lock."""

    _require_current_reference_registry()
    workspace_root = str(workspace.root)
    with _reference_operation_registry_lock:
        registered = tuple(
            operation
            for (
                owned_root,
                owned_session,
                _,
                _,
            ), operation in _reference_operation_registry.items()
            if owned_root == workspace_root and owned_session == session_id
        )
    return any(operation.cleanup_is_pending() for operation in registered)

def reconcile_reference_operations_at_boundary(workspace: Workspace,
    *,
    session_id: str,
    physical_workspace_identity: tuple[int,
        int
    ],
    repository_access: Callable[[],
        AbstractContextManager[WritingArtifactRepository]
    ]
) -> None:
    """Retry retained and restarted cleanup before new provider work."""

    _require_current_reference_registry()
    workspace_root = str(workspace.root)
    reconciled_retained: list[RuntimeReferenceOperation] = []
    with repository_access() as repository:
        with _reference_operation_registry_lock:
            registered = tuple(
                (key, operation)
                for key, operation in (
                    _reference_operation_registry.items()
                )
                if key[0] == workspace_root and key[1] == session_id
            )
        for key, operation in registered:
            if operation.defer_if_workspace_replaced(
                physical_workspace_identity
            ):
                with _reference_operation_registry_lock:
                    if (
                        _reference_operation_registry.get(key)
                        is operation
                    ):
                        _reference_operation_registry.pop(key)
                continue
            if not operation.cleanup_is_pending():
                continue
            operation.reconcile_cleanup(
                repository,
                physical_workspace_identity=physical_workspace_identity,
            )
            reconciled_retained.append(operation)
        repository.reconcile_incomplete_reference_operations(
            session_id,
            excluded_ids_supplier=partial(
                registered_reference_operation_ids,
                workspace,
                session_id,
                physical_workspace_identity,
            ),
        )
    for operation in reconciled_retained:
        operation.complete_reconciled_cleanup()
        unregister_terminal_reference_operation(workspace, operation)

def _require_current_reference_registry() -> None:
    """Discard inherited process-local handles before registry locking."""

    pid = os.getpid()
    if _reference_operation_registry_pid == pid:
        return
    reset_reference_registry_after_fork()

def reset_reference_registry_after_fork() -> None:
    """Install empty child-owned registry state without inherited locking."""

    global _reference_operation_registry_lock, _reference_operation_registry, _reference_operation_registry_pid
    _reference_operation_registry_lock = threading.RLock()
    _reference_operation_registry = {}
    _reference_operation_registry_pid = os.getpid()

def prepare_session_reference_operation(workspace: Workspace,
    repository_access: Callable[[],
        AbstractContextManager[WritingArtifactRepository]
    ],
    *,
    artifact_binding: SessionArtifactBinding,
    session_id: str,
    text: str,
    reconcile_existing: bool=True
) -> RuntimeReferenceOperation:
    if reconcile_existing:
        reconcile_reference_operations_at_boundary(
            workspace,
            session_id=session_id,
            physical_workspace_identity=artifact_binding.worktree_identity,
            repository_access=repository_access,
        )
    return prepare_registered_guarded_runtime_reference_operation(
        repository_access,
        session_id=session_id,
        text=text,
        physical_workspace_identity=artifact_binding.worktree_identity,
        physical_workspace_pin_factory=partial(
            session_worktree_access.duplicate_session_worktree_descriptor,
            artifact_binding,
        ),
        register=partial(register_reference_operation, workspace),
    )

def finish_reference_operation(workspace: Workspace, operation: RuntimeReferenceOperation) -> None:
    try:
        operation.finish()
    finally:
        unregister_terminal_reference_operation(workspace, operation)

def fail_reference_operation(workspace: Workspace,
    operation: RuntimeReferenceOperation,
    failure: BaseException,
    *,
    default: ReferenceAuthorizationErrorCode=ReferenceAuthorizationErrorCode.WORKFLOW_FAILURE
) -> ReferenceOperationFailure | None:
    try:
        return operation.failure(failure, default=default)
    finally:
        unregister_terminal_reference_operation(workspace, operation)

_register_at_fork = getattr(os, "register_at_fork", None)

if _register_at_fork is not None:
    _register_at_fork(
        after_in_child=reset_reference_registry_after_fork,
    )

del _register_at_fork
