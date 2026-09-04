"""Durable compare-and-append storage for consensus workflow events."""

from __future__ import annotations

import contextlib
import hashlib
import os
import stat
from collections.abc import Iterator

from ..errors import ManuscriptExistsError, WorkspaceFormatError, WorkspaceSecurityError
from ..workspace import Workspace
from .serialization import (
    ConsensusEvent,
    ConsensusInputKind,
    ConsensusRuntimeConflict,
    ConsensusRuntimeOutcomeUnknown,
    ConsensusStoredWorkflow,
    ConsensusWorkflowSubmission,
)

try:
    import fcntl
except ImportError:  # pragma: no cover - unsupported hosts fail closed at use.
    fcntl = None  # type: ignore[assignment]


CONSENSUS_EVENT_STORE_DIRECTORY = (
    "agent_states/orchestrator_state/consensus_workflows/v2"
)
_LOCK_PATH = f"{CONSENSUS_EVENT_STORE_DIRECTORY}/.store.lock"
class ConsensusEventStoreError(WorkspaceFormatError):
    """Stored consensus state is absent, malformed, or foreign."""


class ConsensusEventStoreConflict(ConsensusRuntimeConflict, ConsensusEventStoreError):
    """A create collision or expected-tail comparison failed."""


class ConsensusEventStoreOutcomeUnknown(
    ConsensusRuntimeOutcomeUnknown,
    ConsensusEventStoreError,
):
    """A durable mutation may have occurred and must not be replayed."""


class ConsensusEventStore:
    """Own serialized schema-v2 workflows beneath one ready workspace."""

    def __init__(self, workspace: Workspace) -> None:
        if type(workspace) is not Workspace:
            raise TypeError("workspace must be a Workspace")
        self._workspace = workspace.require_ready()

    def create_workflow(
        self,
        submission: ConsensusWorkflowSubmission,
        request_event: ConsensusEvent,
        /,
    ) -> None:
        """Create exactly one identity-bound workflow, rejecting collisions."""

        stored = ConsensusStoredWorkflow(submission, (request_event,))
        from .serialization import serialize_consensus_workflow
        path = self._path(
            submission.session_id,
            submission.conversation_id,
            submission.workflow_id,
        )
        content = serialize_consensus_workflow(stored).decode("utf-8", errors="strict")
        with self._locked():
            if self._exists(path):
                existing = self._load_path(path)
                self._require_identity(
                    existing,
                    submission.session_id,
                    submission.conversation_id,
                    submission.workflow_id,
                )
                raise ConsensusEventStoreConflict("consensus workflow already exists")
            try:
                self._workspace.write_text(path, content, overwrite=False)
            except (FileExistsError, ManuscriptExistsError) as exc:
                raise ConsensusEventStoreConflict(
                    "consensus workflow creation raced"
                ) from exc
            except BaseException as exc:
                raise ConsensusEventStoreOutcomeUnknown(
                    "consensus workflow creation outcome is unknown"
                ) from exc

    def load_workflow(
        self,
        session_id: str,
        conversation_id: str,
        workflow_id: str,
        /,
    ) -> ConsensusStoredWorkflow | None:
        """Load and revalidate an exact workflow identity."""

        path = self._path(session_id, conversation_id, workflow_id)
        with self._locked():
            if not self._exists(path):
                return None
            stored = self._load_path(path)
            self._require_identity(stored, session_id, conversation_id, workflow_id)
            return stored

    def append_event(
        self,
        session_id: str,
        conversation_id: str,
        workflow_id: str,
        expected_tail_sequence: int,
        event: ConsensusEvent,
        /,
    ) -> None:
        """Atomically append after an exact expected-tail comparison."""

        from .serialization import serialize_consensus_workflow

        if type(expected_tail_sequence) is not int or expected_tail_sequence < 1:
            raise ValueError("expected consensus tail sequence is invalid")
        if type(event) is not ConsensusEvent:
            raise TypeError("event must be ConsensusEvent")
        path = self._path(session_id, conversation_id, workflow_id)
        with self._locked():
            if not self._exists(path):
                raise ConsensusEventStoreError("consensus workflow does not exist")
            stored = self._load_path(path)
            self._require_identity(stored, session_id, conversation_id, workflow_id)
            actual_tail = stored.events[-1].sequence
            if actual_tail != expected_tail_sequence:
                raise ConsensusEventStoreConflict("consensus workflow tail changed")
            if event.sequence != actual_tail + 1:
                raise ConsensusEventStoreConflict(
                    "consensus event sequence does not extend the expected tail"
                )
            updated = ConsensusStoredWorkflow(
                stored.submission,
                stored.events + (event,),
            )
            content = serialize_consensus_workflow(updated).decode(
                "utf-8", errors="strict"
            )
            try:
                self._workspace.write_text(path, content, overwrite=True)
            except BaseException as exc:
                raise ConsensusEventStoreOutcomeUnknown(
                    "consensus append outcome is unknown"
                ) from exc

    def _path(self, session_id: str, conversation_id: str, workflow_id: str) -> str:
        for value, label in (
            (session_id, "session ID"),
            (conversation_id, "conversation ID"),
            (workflow_id, "workflow ID"),
        ):
            if not isinstance(value, str) or not value or "\0" in value:
                raise ValueError(f"consensus {label} is invalid")
        identity = "\0".join((session_id, conversation_id, workflow_id)).encode(
            "utf-8", errors="strict"
        )
        digest = hashlib.sha256(identity).hexdigest()
        return f"{CONSENSUS_EVENT_STORE_DIRECTORY}/{digest}.json"

    def _exists(self, relative: str) -> bool:
        target = self._workspace.path(relative)
        try:
            observed = os.stat(target, follow_symlinks=False)
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise ConsensusEventStoreError("cannot inspect consensus workflow") from exc
        if not stat.S_ISREG(observed.st_mode) or observed.st_nlink != 1:
            raise ConsensusEventStoreError("consensus workflow entry is unsafe")
        return True

    def _load_path(self, relative: str) -> ConsensusStoredWorkflow:
        from .serialization import deserialize_consensus_workflow
        try:
            encoded = self._workspace.read_text(relative).encode(
                "utf-8", errors="strict"
            )
            return deserialize_consensus_workflow(encoded)
        except (WorkspaceSecurityError, ConsensusEventStoreError):
            raise
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            raise ConsensusEventStoreError("consensus workflow is malformed") from exc

    @staticmethod
    def _require_identity(
        stored: ConsensusStoredWorkflow,
        session_id: str,
        conversation_id: str,
        workflow_id: str,
    ) -> None:
        submission = stored.submission
        if (
            submission.session_id != session_id
            or submission.conversation_id != conversation_id
            or submission.workflow_id != workflow_id
        ):
            raise ConsensusEventStoreError("consensus workflow identity is foreign")

    @contextlib.contextmanager
    def _locked(self) -> Iterator[None]:
        if fcntl is None or not hasattr(os, "O_NOFOLLOW"):
            raise ConsensusEventStoreError("secure consensus locking is unavailable")
        lock = self._workspace.path(_LOCK_PATH)
        lock.parent.mkdir(parents=True, exist_ok=True)
        self._workspace.path(lock.parent.relative_to(self._workspace.root))
        descriptor = os.open(
            lock,
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
            0o600,
        )
        try:
            observed = os.fstat(descriptor)
            named = os.stat(lock, follow_symlinks=False)
            if (
                not stat.S_ISREG(observed.st_mode)
                or observed.st_nlink != 1
                or (observed.st_dev, observed.st_ino)
                != (named.st_dev, named.st_ino)
            ):
                raise ConsensusEventStoreError("consensus lock is unsafe")
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        except ConsensusEventStoreError:
            raise
        except OSError as exc:
            raise ConsensusEventStoreError("consensus lock failed") from exc
        finally:
            os.close(descriptor)


__all__ = [
    "CONSENSUS_EVENT_STORE_DIRECTORY",
    "ConsensusInputKind",
    "ConsensusEvent",
    "ConsensusEventStore",
    "ConsensusEventStoreConflict",
    "ConsensusEventStoreError",
    "ConsensusEventStoreOutcomeUnknown",
    "ConsensusRuntimeConflict",
    "ConsensusRuntimeOutcomeUnknown",
    "ConsensusStoredWorkflow",
    "ConsensusWorkflowSubmission",
]
