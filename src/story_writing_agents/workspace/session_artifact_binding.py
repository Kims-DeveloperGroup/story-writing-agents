"""Session artifact binding, scoped access, and generation pin lifecycle."""

from __future__ import annotations

import hashlib
import os
import threading
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from ..provider_execution_workspace import (
    ProviderExecutionWorkspace,
    mint_provider_execution_workspace,
)
from ..errors import (
    ConfigurationError,
    SessionArtifactGuardFailure,
    SessionIdError,
    WorkspaceFormatError,
    WorkspaceSecurityError,
    WorkspaceVersionUnsupportedError,
)
from ..git import GitBaselineManifest
from ..git.story_commits import (
    StoryCommitService,
    create_identity_bound_story_commit_service,
)
from ..git.repository_lock import (
    GitRepositoryLease,
    GitRepositoryLockFailure,
    current_thread_token,
    lock_git_repository,
)
from ..git.session_contracts import (
    GitSessionResourceError,
    GitSessionResourceInspection,
    GitSessionResourceState,
)
from ..git.session_generation import (
    GitSessionGenerationIdentity,
    GitSessionGenerationPin,
    acquire_git_session_generation_pin,
    capture_locked_git_session_generation,
    require_locked_git_session_untracked_prefix,
)
from ..git.session_resources import (
    require_ready_git_session_resources as _require_ready_git_session_resources,
    require_ready_locked_git_session_resources as _require_ready_locked_git_session_resources,
)
from ..writing_artifacts.repository import (
    WritingArtifactRepository,
    create_guarded_writing_artifact_repository,
)
from .session_role_projection import (
    SessionRoleProjectionPlan,
    install_session_role_projection,
)
from .policy_scaffold import known_baseline_manifest, validated_configuration_allowance
from .root_contracts import main_workspace_root
from .root_filesystem import (
    meaningful_directory_identity,
    open_existing_root,
    valid_identity,
    verify_named_identity,
)


@dataclass(frozen=True, slots=True)
class SessionArtifactBinding:
    """One call-local artifact repository bound to exact READY session resources."""

    resources: GitSessionResourceInspection
    worktree_identity: tuple[int, int]
    manifest: GitBaselineManifest = field(repr=False)
    allowed_worktree_changes: tuple[str, ...] = field(repr=False)
    _generation_identity: GitSessionGenerationIdentity | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    _generation_creator_pid: int = field(init=False, repr=False, compare=False)
    _generation_creator_thread_token: object = field(
        init=False,
        repr=False,
        compare=False,
    )
    _repository: WritingArtifactRepository = field(init=False, repr=False)
    _operation_lock: threading.RLock = field(init=False, repr=False, compare=False)
    _operation_tokens: dict[object, list[SessionArtifactAccess]] = field(
        init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        if type(self.resources) is not GitSessionResourceInspection:
            raise TypeError("resources must be a GitSessionResourceInspection")
        if self.resources.state is not GitSessionResourceState.READY:
            raise ValueError("session artifact resources must be READY")
        if not valid_identity(self.worktree_identity):
            raise TypeError("worktree_identity must be a physical directory identity")
        if type(self.manifest) is not GitBaselineManifest:
            raise TypeError("manifest must be a GitBaselineManifest")
        if type(self.allowed_worktree_changes) is not tuple or any(
            type(path) is not str or not path
            for path in self.allowed_worktree_changes
        ):
            raise TypeError("allowed_worktree_changes must be exact paths")
        if self._generation_identity is not None and (
            type(self._generation_identity) is not GitSessionGenerationIdentity
            or self._generation_identity.session != self.resources.identity
            or self._generation_identity.worktree_identity
            != self.worktree_identity
        ):
            raise TypeError("session artifact generation identity is invalid")
        object.__setattr__(self, "_generation_creator_pid", os.getpid())
        object.__setattr__(
            self,
            "_generation_creator_thread_token",
            current_thread_token(),
        )
        object.__setattr__(self, "_operation_lock", threading.RLock())
        object.__setattr__(self, "_operation_tokens", {})
        object.__setattr__(
            self,
            "_repository",
            create_guarded_writing_artifact_repository(
                self.resources.identity.worktree_root,
                expected_root_identity=self.worktree_identity,
                operation_access=self._open_repository_operation,
            ),
        )

    @contextmanager
    def open_ready_resources(self) -> Iterator[SessionArtifactAccess]:
        """Hold exact READY Git and physical-root authority for artifact I/O."""

        self._require_creator()
        nested = self._current_repository_access()
        if nested is not None:
            nested.require_active()
            try:
                yield nested
            except BaseException as failure:
                _postvalidate_session_artifact_access(nested, failure=failure)
                raise
            else:
                nested.require_active()
            return

        access: SessionArtifactAccess | None = None
        descriptor = -1
        stack = ExitStack()
        try:
            lease = stack.enter_context(
                lock_git_repository(self.resources.identity.main_root)
            )
            confirmed = _require_ready_locked_git_session_resources(
                lease,
                self.manifest,
                self.resources.identity.session_id,
                allowed_worktree_changes=self.allowed_worktree_changes,
            )
            if confirmed != self.resources:
                raise WorkspaceFormatError(
                    "session artifact resources changed before access"
                )
            expected_generation = self._generation_identity
            if expected_generation is None or (
                capture_locked_git_session_generation(
                    lease,
                    confirmed,
                    expected_worktree_identity=self.worktree_identity,
                )
                != expected_generation
            ):
                raise WorkspaceFormatError(
                    "session artifact generation changed before access"
                )
            descriptor = open_existing_root(
                self.resources.identity.worktree_root,
                self.worktree_identity,
            )
            access = SessionArtifactAccess(self, lease, descriptor)
            descriptor = -1
        except BaseException as failure:
            _close_untransferred_artifact_descriptor(descriptor, failure)
            try:
                stack.close()
            except BaseException as cleanup_failure:
                failure.add_note(
                    "session artifact lock cleanup failed: "
                    f"{type(cleanup_failure).__name__}"
                )
            if isinstance(failure, _SESSION_ARTIFACT_BINDING_FAILURES):
                raise SessionArtifactBindingError(
                    "session artifact resources are unavailable"
                ) from None
            raise

        body_failure: BaseException | None = None
        try:
            with stack:
                try:
                    try:
                        yield access
                    except BaseException as exc:
                        body_failure = exc
                        _postvalidate_session_artifact_access(
                            access,
                            failure=exc,
                        )
                        raise
                    else:
                        _postvalidate_session_artifact_access(access)
                except BaseException as failure:
                    try:
                        access._revoke()
                    except BaseException as cleanup_failure:
                        _add_session_artifact_cleanup_note(
                            failure,
                            cleanup_failure,
                        )
                    raise
                else:
                    access._revoke()
        except _SESSION_ARTIFACT_BINDING_FAILURES as exc:
            if exc is body_failure:
                raise
            if type(exc) is SessionArtifactBindingError:
                raise
            raise SessionArtifactBindingError(
                "session artifact resources are unavailable"
            ) from None

    def _activate_repository_access(self, access: SessionArtifactAccess) -> None:
        self._require_creator()
        with self._operation_lock:
            if current_thread_token() is not access._owner_thread_token:
                raise SessionArtifactBindingError(
                    "session artifact access token is invalid"
                )
            self._operation_tokens.setdefault(
                access._owner_thread_token,
                [],
            ).append(access)

    def acquire_generation_pin(self) -> SessionArtifactGenerationPin:
        """Acquire one opaque pin under a short physical-generation lock."""

        self._require_creator()
        identity = self._generation_identity
        if identity is None:
            raise SessionArtifactBindingError(
                "session artifact resources are unavailable"
            )
        git_pin: GitSessionGenerationPin | None = None
        try:
            git_pin = acquire_git_session_generation_pin(identity)
            pin = SessionArtifactGenerationPin(
                identity,
                git_pin,
            )
            git_pin = None
            return pin
        except BaseException as failure:
            primary: BaseException = failure
            if isinstance(failure, _SESSION_ARTIFACT_BINDING_FAILURES):
                primary = SessionArtifactBindingError(
                    "session artifact resources are unavailable"
                )
            if git_pin is not None:
                lower_pin = git_pin
                git_pin = None
                try:
                    lower_pin.close()
                except BaseException as cleanup_failure:
                    _add_session_artifact_cleanup_note(primary, cleanup_failure)
            if primary is not failure:
                raise primary from None
            raise

    @contextmanager
    def open_provider_execution_workspace(
        self,
    ) -> Iterator[ProviderExecutionWorkspace]:
        """Mint one call-local process workspace without retaining a Git lock."""

        self._require_creator()
        identity = self._generation_identity
        if identity is None:
            raise SessionArtifactBindingError(
                "session artifact resources are unavailable"
            )
        descriptor = -1
        pin: GitSessionGenerationPin | None = None
        execution_workspace: ProviderExecutionWorkspace | None = None
        try:
            pin = acquire_git_session_generation_pin(identity)
            pin.require_current()
            descriptor = open_existing_root(
                self.resources.identity.worktree_root,
                self.worktree_identity,
            )
            verify_named_identity(
                self.resources.identity.worktree_root,
                descriptor,
                self.worktree_identity,
            )
            execution_workspace = mint_provider_execution_workspace(
                session_id=self.resources.identity.session_id,
                worktree_root=self.resources.identity.worktree_root,
                root_identity=self.worktree_identity,
                generation_provenance=(
                    _provider_execution_generation_provenance(identity)
                ),
                root_descriptor=descriptor,
                generation_pin=pin,
                failure_factory=lambda: SessionArtifactBindingError(
                    "session artifact resources are unavailable"
                ),
            )
            descriptor = -1
            pin = None
            execution_workspace.require_current()
        except BaseException as failure:
            if execution_workspace is not None:
                execution_workspace.close(failure=failure)
                execution_workspace = None
            _close_untransferred_artifact_descriptor(descriptor, failure)
            if pin is not None:
                try:
                    pin.close()
                except BaseException as cleanup_failure:
                    _add_session_artifact_cleanup_note(failure, cleanup_failure)
            if isinstance(failure, _SESSION_ARTIFACT_BINDING_FAILURES):
                raise SessionArtifactBindingError(
                    "session artifact resources are unavailable"
                ) from None
            raise

        body_failure: BaseException | None = None
        try:
            try:
                yield execution_workspace
            except BaseException as failure:
                body_failure = failure
                raise
        finally:
            exit_failure = body_failure
            try:
                execution_workspace.require_current()
            except BaseException as validation_failure:
                if body_failure is None:
                    exit_failure = validation_failure
                else:
                    try:
                        body_failure.add_note(
                            "provider execution workspace exit validation failed: "
                            f"{type(validation_failure).__name__}"
                        )
                    except BaseException:
                        pass
            execution_workspace.close(failure=exit_failure)
            if body_failure is None and exit_failure is not None:
                raise exit_failure

    def transfer_to_current_thread(self) -> SessionArtifactBinding:
        """Recreate immutable binding facts for a new event-loop thread.

        The source binding owns no opened artifact or generation descriptors;
        this transfer deliberately copies only validated immutable topology.
        """

        identity = self._generation_identity
        if identity is None:
            raise SessionArtifactBindingError(
                "session artifact resources are unavailable"
            )
        transferred_identity = GitSessionGenerationIdentity(
            session=identity.session,
            administrative_root=identity.administrative_root,
            control_root_identity=identity.control_root_identity,
            common_git_identity=identity.common_git_identity,
            administrative_identity=identity.administrative_identity,
            worktree_identity=identity.worktree_identity,
            git_indirection_facts=identity.git_indirection_facts,
            common_indirection_facts=identity.common_indirection_facts,
            git_indirection_sha256=identity.git_indirection_sha256,
            common_indirection_sha256=identity.common_indirection_sha256,
        )
        return SessionArtifactBinding(
            resources=self.resources,
            worktree_identity=self.worktree_identity,
            manifest=self.manifest,
            allowed_worktree_changes=self.allowed_worktree_changes,
            _generation_identity=transferred_identity,
        )

    def require_generation_pin(self, pin: SessionArtifactGenerationPin) -> None:
        """Require one opaque pin to match this binding's current generation."""

        self._require_creator()
        if type(pin) is not SessionArtifactGenerationPin:
            raise TypeError("generation pin must be exact")
        identity = self._generation_identity
        if identity is None:
            raise SessionArtifactBindingError(
                "session artifact resources are unavailable"
            )
        try:
            pin._require_identity(identity)
        except _SESSION_ARTIFACT_BINDING_FAILURES:
            raise SessionArtifactBindingError(
                "session artifact resources are unavailable"
            ) from None

    def _require_creator(self) -> None:
        if (
            self._generation_creator_pid != os.getpid()
            or self._generation_creator_thread_token is not current_thread_token()
        ):
            raise SessionArtifactBindingError(
                "session artifact resources are unavailable"
            )

    def _deactivate_repository_access(self, access: SessionArtifactAccess) -> None:
        self._require_creator()
        with self._operation_lock:
            if current_thread_token() is not access._owner_thread_token:
                raise SessionArtifactBindingError(
                    "session artifact access token is invalid"
                )
            tokens = self._operation_tokens.get(access._owner_thread_token)
            if not tokens or tokens[-1] is not access:
                raise SessionArtifactBindingError(
                    "session artifact access token is invalid"
                )
            tokens.pop()
            if not tokens:
                self._operation_tokens.pop(access._owner_thread_token, None)

    def _revoke_repository_access(self, access: SessionArtifactAccess) -> None:
        if self._generation_creator_pid != os.getpid():
            return
        with self._operation_lock:
            tokens = self._operation_tokens.get(access._owner_thread_token)
            if tokens and access in tokens:
                tokens.remove(access)
                if not tokens:
                    self._operation_tokens.pop(access._owner_thread_token, None)

    def _current_repository_access(self) -> SessionArtifactAccess | None:
        self._require_creator()
        with self._operation_lock:
            tokens = self._operation_tokens.get(current_thread_token())
            return None if not tokens else tokens[-1]

    @contextmanager
    def _open_repository_operation(self) -> Iterator[None]:
        self._require_creator()
        with self._operation_lock:
            access = self._current_repository_access()
            if access is None:
                raise SessionArtifactBindingError(
                    "session artifact repository access is not active"
                )
            access.require_ready()
            try:
                yield
            except BaseException as failure:
                _postvalidate_session_artifact_access(access, failure=failure)
                raise
            else:
                access.require_ready()

    @contextmanager
    def open_story_commit_artifact_repository(
        self,
        validate_ready: Callable[[], GitSessionResourceInspection | None],
        *,
        expected_generation_identity: GitSessionGenerationIdentity,
        binding_failure_factory: Callable[[], BaseException] | None = None,
    ) -> Iterator[WritingArtifactRepository]:
        """Borrow one live history validator for exact artifact capture."""

        self._require_creator()
        if (
            type(expected_generation_identity) is not GitSessionGenerationIdentity
            or expected_generation_identity != self._generation_identity
            or (
                binding_failure_factory is not None
                and not callable(binding_failure_factory)
            )
        ):
            raise SessionArtifactBindingError(
                "session artifact generation is unavailable"
            )

        def validate_generation() -> GitSessionResourceInspection | None:
            return validate_ready()

        access: SessionArtifactAccess | None = None
        descriptor = -1
        try:
            confirmed = validate_generation()
            if confirmed != self.resources:
                raise WorkspaceFormatError(
                    "session artifact resources changed before capture"
                )
            descriptor = open_existing_root(
                self.resources.identity.worktree_root,
                self.worktree_identity,
            )
            access = SessionArtifactAccess(
                self,
                None,
                descriptor,
                ready_validator=validate_generation,
                binding_failure_factory=binding_failure_factory,
            )
            descriptor = -1
        except BaseException as failure:
            _close_untransferred_artifact_descriptor(descriptor, failure)
            if isinstance(failure, _SESSION_ARTIFACT_BINDING_FAILURES):
                if binding_failure_factory is not None:
                    raise binding_failure_factory() from None
                raise SessionArtifactBindingError(
                    "session artifact resources are unavailable"
                ) from None
            raise

        body_failure: BaseException | None = None
        try:
            try:
                try:
                    yield access.repository
                except BaseException as exc:
                    body_failure = exc
                    _postvalidate_session_artifact_access(access, failure=exc)
                    raise
                else:
                    _postvalidate_session_artifact_access(access)
            except BaseException as failure:
                try:
                    access._revoke()
                except BaseException as cleanup_failure:
                    _add_session_artifact_cleanup_note(failure, cleanup_failure)
                raise
            else:
                access._revoke()
        except _SESSION_ARTIFACT_BINDING_FAILURES as exc:
            if exc is body_failure:
                raise
            if type(exc) is SessionArtifactBindingError:
                raise
            raise SessionArtifactBindingError(
                "session artifact resources are unavailable"
            ) from None


class SessionArtifactAccess:
    """One live same-thread view of a guarded session artifact binding."""

    __slots__ = (
        "_active",
        "_binding",
        "_binding_failure_factory",
        "_descriptor",
        "_lease",
        "_owner_thread_token",
        "_ready_validator",
        "_translated_binding_failure",
    )

    def __init__(
        self,
        binding: SessionArtifactBinding,
        lease: GitRepositoryLease | None,
        descriptor: int,
        *,
        ready_validator: Callable[
            [], GitSessionResourceInspection | None
        ] | None = None,
        binding_failure_factory: Callable[[], BaseException] | None = None,
    ) -> None:
        if (lease is None) == (ready_validator is None):
            raise SessionArtifactBindingError(
                "session artifact access authority is invalid"
            )
        self._binding = binding
        self._binding_failure_factory = binding_failure_factory
        self._translated_binding_failure: BaseException | None = None
        self._lease = lease
        self._ready_validator = ready_validator
        self._descriptor = descriptor
        self._owner_thread_token = current_thread_token()
        self._active = True
        try:
            binding._activate_repository_access(self)
        except BaseException as failure:
            self._active = False
            if (
                binding._generation_creator_pid == os.getpid()
                and binding._generation_creator_thread_token
                is current_thread_token()
            ):
                try:
                    binding._revoke_repository_access(self)
                except BaseException as cleanup_failure:
                    failure.add_note(
                        "session artifact activation cleanup failed: "
                        f"{type(cleanup_failure).__name__}"
                    )
            raise

    @property
    def repository(self) -> WritingArtifactRepository:
        """Return the repository only while this exact access is live."""

        self.require_active()
        return self._binding._repository

    @property
    def resources(self) -> GitSessionResourceInspection:
        self.require_active()
        return self._binding.resources

    @property
    def worktree_identity(self) -> tuple[int, int]:
        self.require_active()
        return self._binding.worktree_identity

    def duplicate_worktree_descriptor(self) -> int:
        """Duplicate the guarded root descriptor for one bounded source read."""

        self.require_active()
        descriptor = -1
        try:
            descriptor = os.dup(self._descriptor)
            opened = os.fstat(descriptor)
            if meaningful_directory_identity(opened) != self.worktree_identity:
                raise OSError
            return descriptor
        except OSError as failure:
            _close_untransferred_artifact_descriptor(descriptor, failure)
            raise SessionArtifactBindingError(
                "session artifact resources are unavailable"
            ) from None
        except BaseException as failure:
            _close_untransferred_artifact_descriptor(descriptor, failure)
            raise

    def install_provider_role(
        self,
        plan: SessionRoleProjectionPlan,
    ) -> None:
        """Project one provider role through live Git and worktree authority."""

        self.require_active()
        if self._lease is None:
            raise SessionArtifactBindingError(
                "session artifact resources are unavailable"
            )
        try:
            expected_generation = self._binding._generation_identity
            if expected_generation is None:
                raise SessionArtifactBindingError(
                    "session artifact resources are unavailable"
                )
            require_locked_git_session_untracked_prefix(
                self._lease,
                self._binding.resources,
                expected_generation,
                prefix=".agents",
            )
            self.require_active()

            def require_effective_exclusion() -> None:
                self.require_active()
                require_locked_git_session_untracked_prefix(
                    self._lease,
                    self._binding.resources,
                    expected_generation,
                    prefix=".agents",
                    verify_exclusion=True,
                )
                self.require_active()

            install_session_role_projection(
                self._descriptor,
                self._lease.common_descriptor,
                plan=plan,
                require_git_exclusion=require_effective_exclusion,
            )
            self.require_active()
        except _SESSION_ARTIFACT_BINDING_FAILURES:
            raise SessionArtifactBindingError(
                "session artifact resources are unavailable"
            ) from None

    def require_active(self) -> None:
        """Require the live lease, descriptor, and named worktree identity."""

        self._binding._require_creator()
        if (
            not self._active
            or current_thread_token() is not self._owner_thread_token
        ):
            raise SessionArtifactBindingError(
                "session artifact access is no longer active"
            )
        try:
            if self._lease is not None:
                self._lease.require_active()
            verify_named_identity(
                self._binding.resources.identity.worktree_root,
                self._descriptor,
                self._binding.worktree_identity,
            )
        except _SESSION_ARTIFACT_BINDING_FAILURES:
            self._raise_binding_failure()

    def _is_translated_binding_failure(self, failure: BaseException) -> bool:
        return failure is self._translated_binding_failure

    def _raise_binding_failure(self) -> None:
        factory = self._binding_failure_factory
        if factory is not None:
            translated = factory()
            if isinstance(translated, BaseException):
                self._translated_binding_failure = translated
                raise translated from None
        raise SessionArtifactBindingError(
            "session artifact resources are unavailable"
        ) from None

    def require_ready(self) -> None:
        """Repeat exact READY inspection under the live repository lease."""

        self.require_active()
        try:
            if self._ready_validator is not None:
                confirmed = self._ready_validator()
            else:
                assert self._lease is not None
                confirmed = _require_ready_locked_git_session_resources(
                    self._lease,
                    self._binding.manifest,
                    self._binding.resources.identity.session_id,
                    allowed_worktree_changes=(
                        self._binding.allowed_worktree_changes
                    ),
                )
                expected_generation = self._binding._generation_identity
                if expected_generation is None or (
                    capture_locked_git_session_generation(
                        self._lease,
                        confirmed,
                        expected_worktree_identity=self._binding.worktree_identity,
                    )
                    != expected_generation
                ):
                    raise WorkspaceFormatError(
                        "session artifact generation changed during access"
                    )
            if confirmed != self._binding.resources:
                raise WorkspaceFormatError(
                    "session artifact resources changed during access"
                )
            self.require_active()
        except _SESSION_ARTIFACT_BINDING_FAILURES:
            self._raise_binding_failure()

    def _close(self) -> None:
        self.require_active()
        self._binding._deactivate_repository_access(self)
        self._active = False
        try:
            os.close(self._descriptor)
        except OSError:
            pass

    def _revoke(self) -> None:
        if not self._active:
            return
        failure: BaseException | None = None
        try:
            self._binding._revoke_repository_access(self)
        except BaseException as exc:
            failure = exc
            raise
        finally:
            self._active = False
            _close_session_artifact_access_descriptor(
                self._descriptor,
                failure=failure,
            )


class SessionArtifactGenerationPin:
    """Opaque ownership of one non-inheritable physical session generation."""

    __slots__ = (
        "_creator_pid",
        "_creator_thread_token",
        "_identity",
        "_lock",
        "_pin",
    )

    def __init__(
        self,
        identity: GitSessionGenerationIdentity,
        pin: GitSessionGenerationPin,
    ) -> None:
        if (
            type(identity) is not GitSessionGenerationIdentity
            or type(pin) is not GitSessionGenerationPin
        ):
            raise TypeError("session artifact generation pin is invalid")
        self._identity = identity
        self._pin = pin
        self._creator_pid = os.getpid()
        self._creator_thread_token = current_thread_token()
        self._lock = threading.Lock()

    def close(self) -> None:
        """Release this generation pin exactly once."""

        if self._creator_pid != os.getpid():
            pin = self._pin
            self._pin = None
            if pin is not None:
                pin.close()
            return
        with self._lock:
            pin = self._pin
            if pin is None:
                return
            self._pin = None
        pin.close()

    def _require_identity(self, identity: GitSessionGenerationIdentity) -> None:
        if (
            self._creator_pid != os.getpid()
            or self._creator_thread_token is not current_thread_token()
        ):
            raise SessionArtifactBindingError(
                "session artifact resources are unavailable"
            )
        with self._lock:
            pin = self._pin
            if pin is None or self._identity != identity:
                raise SessionArtifactBindingError(
                    "session artifact resources are unavailable"
                )
        pin.require_current()

    def __repr__(self) -> str:
        return "SessionArtifactGenerationPin(<opaque>)"

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("session artifact generation pins cannot be serialized")


def _postvalidate_session_artifact_access(
    access: SessionArtifactAccess,
    *,
    failure: BaseException | None = None,
) -> None:
    """Translate only a failed exit check, preserving an original body failure."""

    try:
        access.require_ready()
    except BaseException as cleanup_failure:
        translated = access._is_translated_binding_failure(cleanup_failure)
        binding_lost = translated or isinstance(
            cleanup_failure, _SESSION_ARTIFACT_BINDING_FAILURES
        )
        if not binding_lost:
            if failure is not None:
                _add_session_artifact_cleanup_note(failure, cleanup_failure)
                return
            raise
        authoritative = (
            cleanup_failure
            if translated
            else SessionArtifactBindingError(
                "session artifact resources are unavailable"
            )
        )
        if failure is not None:
            try:
                authoritative.add_note(
                    "yielded body failure superseded by binding loss: "
                    f"{type(failure).__name__}"
                )
            except BaseException:
                pass
        raise authoritative from None


def _add_session_artifact_cleanup_note(
    failure: BaseException,
    cleanup_failure: BaseException,
) -> None:
    try:
        failure.add_note(
            "session artifact cleanup failed: "
            f"{type(cleanup_failure).__name__}"
        )
    except BaseException:
        pass


def _close_session_artifact_access_descriptor(
    descriptor: int,
    *,
    failure: BaseException | None,
) -> None:
    try:
        os.close(descriptor)
    except BaseException as cleanup_failure:
        if failure is not None:
            _add_session_artifact_cleanup_note(failure, cleanup_failure)
        elif not isinstance(cleanup_failure, OSError):
            raise


def _close_untransferred_artifact_descriptor(
    descriptor: int,
    failure: BaseException,
) -> None:
    """Close factory-local authority while preserving its original failure."""

    if descriptor < 0:
        return
    try:
        os.close(descriptor)
    except BaseException as cleanup_failure:
        try:
            failure.add_note(
                "session artifact descriptor cleanup failed: "
                f"{type(cleanup_failure).__name__}"
            )
        except BaseException:
            pass


def _provider_execution_generation_provenance(
    identity: GitSessionGenerationIdentity,
) -> str:
    """Digest exact content-free generation facts without persisting them."""

    if type(identity) is not GitSessionGenerationIdentity:
        raise TypeError("session artifact generation identity is invalid")
    fields: tuple[object, ...] = (
        identity.session.session_id,
        *identity.control_root_identity,
        *identity.common_git_identity,
        *identity.administrative_identity,
        *identity.worktree_identity,
        *identity.git_indirection_facts,
        *identity.common_indirection_facts,
        identity.git_indirection_sha256,
        identity.common_indirection_sha256,
    )
    digest = hashlib.sha256(b"story-provider-execution-generation-v1\0")
    for field_value in fields:
        encoded = str(field_value).encode("ascii", errors="strict")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return f"sha256:{digest.hexdigest()}"


class SessionArtifactBindingError(SessionArtifactGuardFailure, RuntimeError):
    """A content-free failure to bind exact READY session artifact resources."""


_SESSION_ARTIFACT_BINDING_FAILURES = (
    ConfigurationError,
    GitRepositoryLockFailure,
    GitSessionResourceError,
    OSError,
    SessionArtifactBindingError,
    SessionIdError,
    WorkspaceFormatError,
    WorkspaceSecurityError,
    WorkspaceVersionUnsupportedError,
)


def compose_session_artifact_binding(
    root: Path,
    session_id: str,
) -> SessionArtifactBinding:
    """Bind artifacts to one freshly validated READY session worktree."""

    try:
        main_root = main_workspace_root(root)
        manifest = known_baseline_manifest(main_root)
        allowance = validated_configuration_allowance(main_root)
        with lock_git_repository(main_root) as lease:
            resources = _require_ready_locked_git_session_resources(
                lease,
                manifest,
                session_id,
                allowed_worktree_changes=allowance,
            )
            worktree_root = resources.identity.worktree_root
            observed = os.stat(worktree_root, follow_symlinks=False)
            worktree_identity = meaningful_directory_identity(observed)
            if worktree_identity is None:
                raise WorkspaceSecurityError(
                    "session worktree root is not a safe directory"
                )
            descriptor = -1
            descriptor = open_existing_root(worktree_root, worktree_identity)
            try:
                confirmed_resources = _require_ready_locked_git_session_resources(
                    lease,
                    manifest,
                    session_id,
                    allowed_worktree_changes=allowance,
                )
                if confirmed_resources != resources:
                    raise WorkspaceFormatError(
                        "session artifact resources changed during binding"
                    )
                verify_named_identity(
                    worktree_root,
                    descriptor,
                    worktree_identity,
                )
                generation_identity = capture_locked_git_session_generation(
                    lease,
                    confirmed_resources,
                    expected_worktree_identity=worktree_identity,
                )
            except BaseException as failure:
                _close_untransferred_artifact_descriptor(descriptor, failure)
                descriptor = -1
                raise
            else:
                closing_descriptor = descriptor
                descriptor = -1
                os.close(closing_descriptor)
    except _SESSION_ARTIFACT_BINDING_FAILURES:
        raise SessionArtifactBindingError(
            "session artifact resources are unavailable"
        ) from None
    return SessionArtifactBinding(
        resources=confirmed_resources,
        worktree_identity=worktree_identity,
        manifest=manifest,
        allowed_worktree_changes=allowance,
        _generation_identity=generation_identity,
    )


def compose_story_commit_service(
    root: Path,
    session_id: str,
    artifact_binding: SessionArtifactBinding | None = None,
) -> StoryCommitService:
    """Compose commit authority for one already-ready workspace session."""

    if artifact_binding is not None:
        if type(artifact_binding) is not SessionArtifactBinding:
            raise SessionArtifactBindingError(
                "story commit binding does not match the ready session"
            )
        artifact_binding._require_creator()
    main_root = main_workspace_root(root)
    manifest = known_baseline_manifest(main_root)
    allowance = validated_configuration_allowance(main_root)
    ready = _require_ready_git_session_resources(
        main_root,
        manifest,
        session_id,
        allowed_worktree_changes=allowance,
    )
    if artifact_binding is None:
        return StoryCommitService(
            main_root,
            manifest,
            session_id,
            allowed_worktree_changes=allowance,
        )
    if artifact_binding.resources != ready:
        raise SessionArtifactBindingError(
            "story commit binding does not match the ready session"
        )
    with artifact_binding.open_ready_resources() as access:
        if access.resources != ready:
            raise SessionArtifactBindingError(
                "story commit binding changed during composition"
            )
        return create_identity_bound_story_commit_service(
            main_root,
            manifest,
            session_id,
            access.worktree_identity,
            artifact_binding._generation_identity,
            allowed_worktree_changes=allowance,
            artifact_repository_access=(
                artifact_binding.open_story_commit_artifact_repository
            ),
        )
