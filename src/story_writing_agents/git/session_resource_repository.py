"""Leased Git commands and provisioning journal file ownership."""

from __future__ import annotations

import os
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - exercised on unsupported platforms
    _fcntl = None  # type: ignore[assignment]

from .contracts import GitFailureCode
from .process import GitProcessFailure, GitProcessResult, GitProcessRunner
from .repository_lock import (
    GitRepositoryLease,
    GitRepositoryLockFailure,
    GitRepositoryLockFailureCode,
    lock_git_repository,
)
from .session_contracts import (
    GIT_SESSION_PROVISIONING_DIRECTORY,
    GIT_SESSION_PROVISIONING_LOCK_NAME,
    GitSessionFailureCode,
    GitSessionIdentity,
    GitSessionOperationKind,
    GitSessionProvisioningJournal,
    GitSessionRecoveryAction,
    GitSessionResourceError,
    GitSessionResourceState,
    decode_session_provisioning_journal,
    encode_session_provisioning_journal,
)
from .session_resource_files import open_directory, read_file, write_all
from .session_resource_values import resource_error


class InspectionBatchOverflow(Exception):
    """An owned optimistic read exceeded its cap after verified child cleanup."""


class InspectionPrefetchUnsuitable(Exception):
    """A bounded, safely completed prefetch needs the original verifier."""

class SessionResourceRepository:
    """Run fixed commands and access journal files through one borrowed lease."""

    def __init__(
        self,
        main_root: Path,
        common_git: Path,
        common_descriptor: int,
        identity: tuple[int, int],
    ) -> None:
        self.main_root = main_root
        self.common_git = common_git
        self.common_descriptor = common_descriptor
        self.identity = identity
        self.runner = GitProcessRunner(main_root)

    def required(
        self,
        arguments: tuple[str, ...],
        *,
        stdin: bytes = b"",
        read_only: bool = False,
        identity_environment: dict[str, str] | None = None,
        allow_output_overflow: bool = False,
        allow_unsuitable_result: bool = False,
    ) -> GitProcessResult:
        try:
            result = self.runner.run(
                arguments,
                expected_identity=self.identity,
                stdin=stdin,
                read_only=read_only,
                identity_environment=identity_environment,
            )
        except GitProcessFailure as failure:
            if (
                read_only and allow_output_overflow
                and failure.code is GitFailureCode.PROCESS_OUTPUT_EXCEEDED
            ):
                raise InspectionBatchOverflow() from None
            if read_only:
                raise resource_error(
                    GitSessionFailureCode.RECOVERY_REQUIRED,
                    GitSessionOperationKind.INSPECT,
                    GitSessionResourceState.RECOVERY_REQUIRED,
                    GitSessionRecoveryAction.OPERATOR_RECOVERY,
                ) from None
            raise resource_error(
                GitSessionFailureCode.OUTCOME_UNKNOWN,
                GitSessionOperationKind.PROVISION,
                GitSessionResourceState.OUTCOME_UNKNOWN,
                GitSessionRecoveryAction.OPERATOR_RECOVERY,
            ) from None
        if result.returncode != 0:
            if read_only and allow_unsuitable_result:
                raise InspectionPrefetchUnsuitable
            if read_only:
                raise resource_error(
                    GitSessionFailureCode.RECOVERY_REQUIRED,
                    GitSessionOperationKind.INSPECT,
                    GitSessionResourceState.RECOVERY_REQUIRED,
                    GitSessionRecoveryAction.OPERATOR_RECOVERY,
                )
            raise resource_error(
                GitSessionFailureCode.MUTATION_FAILED,
                GitSessionOperationKind.PROVISION,
                GitSessionResourceState.OUTCOME_UNKNOWN,
                GitSessionRecoveryAction.OPERATOR_RECOVERY,
            )
        return result

    def journals(self) -> dict[str, GitSessionProvisioningJournal]:
        directory = self.open_provisioning_directory(create=False)
        if directory is None:
            return {}
        try:
            result: dict[str, GitSessionProvisioningJournal] = {}
            for name in sorted(os.listdir(directory)):
                if name == GIT_SESSION_PROVISIONING_LOCK_NAME:
                    continue
                if not name.endswith(".json"):
                    raise resource_error(
                        GitSessionFailureCode.JOURNAL_INVALID,
                        GitSessionOperationKind.INSPECT,
                        GitSessionResourceState.RECOVERY_REQUIRED,
                        GitSessionRecoveryAction.OPERATOR_RECOVERY,
                    )
                session_id = name[:-5]
                payload = read_file(directory, name)
                journal = decode_session_provisioning_journal(payload)
                if journal.session_id != session_id or session_id in result:
                    raise resource_error(
                        GitSessionFailureCode.JOURNAL_INVALID,
                        GitSessionOperationKind.INSPECT,
                        GitSessionResourceState.RECOVERY_REQUIRED,
                        GitSessionRecoveryAction.OPERATOR_RECOVERY,
                    )
                result[session_id] = journal
            return result
        finally:
            os.close(directory)

    def write_journal(self, journal: GitSessionProvisioningJournal) -> None:
        directory = self.open_provisioning_directory(create=True)
        assert directory is not None
        name = f"{journal.session_id}.json"
        payload = encode_session_provisioning_journal(journal)
        descriptor = -1
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(name, flags, 0o600, dir_fd=directory)
            write_all(descriptor, payload)
            os.fsync(descriptor)
            os.fsync(directory)
        except OSError:
            raise resource_error(
                GitSessionFailureCode.MUTATION_FAILED,
                GitSessionOperationKind.PROVISION,
                GitSessionResourceState.OUTCOME_UNKNOWN,
                GitSessionRecoveryAction.OPERATOR_RECOVERY,
            ) from None
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            os.close(directory)

    def remove_journal(self, identity: GitSessionIdentity) -> None:
        directory = self.open_provisioning_directory(create=False)
        if directory is None:
            raise resource_error(
                GitSessionFailureCode.OUTCOME_UNKNOWN,
                GitSessionOperationKind.PROVISION,
                GitSessionResourceState.OUTCOME_UNKNOWN,
                GitSessionRecoveryAction.OPERATOR_RECOVERY,
            )
        try:
            os.unlink(f"{identity.session_id}.json", dir_fd=directory)
            os.fsync(directory)
        except OSError:
            raise resource_error(
                GitSessionFailureCode.OUTCOME_UNKNOWN,
                GitSessionOperationKind.PROVISION,
                GitSessionResourceState.OUTCOME_UNKNOWN,
                GitSessionRecoveryAction.OPERATOR_RECOVERY,
            ) from None
        finally:
            os.close(directory)

    def open_provisioning_directory(self, *, create: bool) -> int | None:
        current = os.dup(self.common_descriptor)
        try:
            for part in GIT_SESSION_PROVISIONING_DIRECTORY.split("/"):
                try:
                    child = open_directory(current, part)
                except FileNotFoundError:
                    if not create:
                        os.close(current)
                        return None
                    os.mkdir(part, 0o700, dir_fd=current)
                    os.fsync(current)
                    child = open_directory(current, part)
                os.close(current)
                current = child
            return current
        except BaseException:
            try:
                os.close(current)
            except OSError:
                pass
            raise


@contextmanager
def lock_session_resources(root: Path, *, create: bool) -> Iterator[SessionResourceRepository]:
    if _fcntl is None:
        raise resource_error(
            GitSessionFailureCode.LOCK_UNAVAILABLE,
            GitSessionOperationKind.INSPECT,
            GitSessionResourceState.RECOVERY_REQUIRED,
            GitSessionRecoveryAction.OPERATOR_RECOVERY,
        )
    lock_descriptor = -1
    try:
        with lock_git_repository(root) as lease:
            repository = repository_from_lease(lease)
            try:
                directory = repository.open_provisioning_directory(create=create)
                if directory is not None:
                    try:
                        flags = (
                            os.O_RDWR
                            | (os.O_CREAT if create else 0)
                            | getattr(os, "O_CLOEXEC", 0)
                        )
                        flags |= getattr(os, "O_NOFOLLOW", 0)
                        try:
                            lock_descriptor = os.open(
                                GIT_SESSION_PROVISIONING_LOCK_NAME,
                                flags,
                                0o600,
                                dir_fd=directory,
                            )
                        except FileNotFoundError:
                            if create:
                                raise
                        if lock_descriptor >= 0:
                            opened = os.fstat(lock_descriptor)
                            if not stat.S_ISREG(opened.st_mode):
                                raise OSError
                            _fcntl.flock(lock_descriptor, _fcntl.LOCK_EX)
                            if create:
                                os.fsync(lock_descriptor)
                                os.fsync(directory)
                    finally:
                        os.close(directory)
                yield repository
            finally:
                if lock_descriptor >= 0:
                    try:
                        _fcntl.flock(lock_descriptor, _fcntl.LOCK_UN)
                    except OSError:
                        pass
                    os.close(lock_descriptor)
                    lock_descriptor = -1
    except GitSessionResourceError:
        raise
    except GitRepositoryLockFailure as exc:
        code = (
            GitSessionFailureCode.UNSAFE_ROOT
            if exc.code is GitRepositoryLockFailureCode.UNSAFE_ROOT
            else GitSessionFailureCode.LOCK_UNAVAILABLE
        )
        raise resource_error(
            code,
            GitSessionOperationKind.INSPECT,
            GitSessionResourceState.RECOVERY_REQUIRED,
            GitSessionRecoveryAction.OPERATOR_RECOVERY,
        ) from None
    except OSError:
        raise resource_error(
            GitSessionFailureCode.LOCK_UNAVAILABLE,
            GitSessionOperationKind.INSPECT,
            GitSessionResourceState.RECOVERY_REQUIRED,
            GitSessionRecoveryAction.OPERATOR_RECOVERY,
        ) from None
    finally:
        if lock_descriptor >= 0:
            os.close(lock_descriptor)


def repository_from_lease(lease: GitRepositoryLease) -> SessionResourceRepository:
    try:
        if type(lease) is not GitRepositoryLease:
            raise TypeError
        lease.require_active()
        return SessionResourceRepository(
            lease.main_root,
            lease.common_git,
            lease.common_descriptor,
            lease.root_identity,
        )
    except (GitRepositoryLockFailure, TypeError):
        raise resource_error(
            GitSessionFailureCode.LOCK_UNAVAILABLE,
            GitSessionOperationKind.INSPECT,
            GitSessionResourceState.RECOVERY_REQUIRED,
            GitSessionRecoveryAction.OPERATOR_RECOVERY,
        ) from None
