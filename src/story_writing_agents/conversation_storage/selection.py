"""Desired conversation-storage selection and coordinated startup activation."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Callable, TextIO

from ..config import (
    ConfigurationBaseline,
    ConfigurationCommitError,
    ConfigurationCommitFailure,
    ConfigurationSnapshot,
    ConfigManager,
)
from ..workspace.core import Workspace
from .archive import (
    ConversationArchive,
    deserialize_conversation_archive,
    serialize_conversation_archive,
)
from .composition import (
    WorkspaceConversationStorageMutationCoordinator,
    compare_and_set_conversation_storage_activation,
    compose_filesystem_conversation_storage,
    load_conversation_storage_activation_state,
)
from .contracts import (
    ConversationStorageActivationOutcome,
    ConversationStorageActivationOutcomeKind,
    ConversationStorageBackend,
    ConversationStorageCapability,
    ConversationStorageCoordinationError,
    ConversationStorageMigrationReadiness,
    ConversationStorageMutationLease,
    ConversationStorageParity,
    ConversationStorageServices,
    parse_conversation_storage_backend,
)
from .filesystem_archive import compose_filesystem_conversation_archive
from .provisioning import (
    ConversationStorageInstallationPlan,
    ConversationStorageProvisioner,
    ConversationStorageProvisioningFailure,
)
from .sqlite import (
    compose_sqlite_conversation_archive,
    compose_sqlite_conversation_storage,
    preflight_sqlite_conversation_storage,
    probe_sqlite_conversation_storage_runtime,
)


CONVERSATION_STORAGE_SELECTION_SCHEMA_VERSION = 1
_SELECTION_TIMEOUT_SECONDS = 30.0


class ConversationStorageSelectionFailure(StrEnum):
    """Exact sanitized ``story storage use`` failure vocabulary."""

    INVALID_REQUEST = "invalid_request"
    WORKSPACE_INVALID = "workspace_invalid"
    COORDINATION_FAILED = "coordination_failed"
    INSTALLATION_REQUIRED = "installation_required"
    INSTALLATION_UNSUPPORTED = "installation_unsupported"
    INSTALLATION_DISALLOWED = "installation_disallowed"
    INSTALLATION_FAILED = "installation_failed"
    INTERPRETER_UNAVAILABLE = "interpreter_unavailable"
    PROVISIONING_FAILED = "provisioning_failed"
    MIGRATION_REQUIRED = "migration_required"
    PARITY_MISMATCH = "parity_mismatch"
    CONFIGURATION_INVALID = "configuration_invalid"
    CONFIGURATION_CONFLICT = "configuration_conflict"
    CONFIGURATION_WRITE_FAILED = "configuration_write_failed"


@dataclass(frozen=True, slots=True)
class ConversationStorageSelectionReport:
    """One counts-free schema-v1 result for desired backend selection."""

    operation: str
    outcome: str
    desired_backend: str | None
    installation: str
    provisioning: str
    parity: str
    configuration: str
    activation: str
    failure: ConversationStorageSelectionFailure | None = None
    schema_version: int = CONVERSATION_STORAGE_SELECTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != CONVERSATION_STORAGE_SELECTION_SCHEMA_VERSION:
            raise ValueError("selection schema version is invalid")
        if self.operation != "use":
            raise ValueError("selection operation is invalid")
        if self.outcome not in {"completed", "failed"}:
            raise ValueError("selection outcome is invalid")
        if self.desired_backend not in {None, "filesystem", "sqlite"}:
            raise ValueError("desired backend is invalid")
        if self.installation not in {
            "not_required",
            "completed",
            "not_run",
            "failed",
        }:
            raise ValueError("installation state is invalid")
        if self.provisioning not in {
            "not_required",
            "completed",
            "not_run",
            "failed",
        }:
            raise ValueError("provisioning state is invalid")
        if self.parity not in {
            "not_required",
            "verified",
            "unverified",
            "mismatch",
        }:
            raise ValueError("parity state is invalid")
        if self.configuration not in {"saved", "unchanged", "preserved"}:
            raise ValueError("configuration state is invalid")
        if self.activation not in {"next_shell", "unchanged"}:
            raise ValueError("activation state is invalid")
        if self.failure is not None and type(self.failure) is not (
            ConversationStorageSelectionFailure
        ):
            raise TypeError("selection failure is invalid")
        if self.outcome == "completed" and self.failure is not None:
            raise ValueError("completed selection cannot have a failure")
        if self.outcome == "failed" and self.failure is None:
            raise ValueError("failed selection requires a failure")

    @classmethod
    def failed(
        cls,
        failure: ConversationStorageSelectionFailure,
        *,
        desired_backend: ConversationStorageBackend | str | None = None,
        installation: str = "not_run",
        provisioning: str = "not_run",
        parity: str = "unverified",
    ) -> ConversationStorageSelectionReport:
        return cls(
            operation="use",
            outcome="failed",
            desired_backend=(
                None
                if desired_backend is None
                else (
                    desired_backend.value
                    if type(desired_backend) is ConversationStorageBackend
                    else desired_backend
                )
            ),
            installation=installation,
            provisioning=provisioning,
            parity=parity,
            configuration="preserved",
            activation="unchanged",
            failure=failure,
        )

    @property
    def exit_code(self) -> int:
        return 0 if self.outcome == "completed" else 2

    def to_dict(self) -> dict[str, object]:
        """Return the exact compact report schema without diagnostic content."""

        return {
            "schema_version": self.schema_version,
            "operation": self.operation,
            "outcome": self.outcome,
            "desired_backend": self.desired_backend,
            "installation": self.installation,
            "provisioning": self.provisioning,
            "parity": self.parity,
            "configuration": self.configuration,
            "activation": self.activation,
            "failure": None if self.failure is None else self.failure.value,
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )


@dataclass(frozen=True, slots=True)
class ConversationStorageStartup:
    """Already-composed immutable services and their activation observation."""

    services: ConversationStorageServices
    activation: ConversationStorageActivationOutcome

    def __post_init__(self) -> None:
        if type(self.services) is not ConversationStorageServices:
            raise TypeError("services must be ConversationStorageServices")
        if type(self.activation) is not ConversationStorageActivationOutcome:
            raise TypeError("activation must be ConversationStorageActivationOutcome")
        if self.services.backend is not self.activation.current.backend:
            raise ValueError("startup services do not match activation")


@dataclass(frozen=True, slots=True)
class _SelectionTopology:
    root: Path

    @classmethod
    def capture(cls, workspace: Workspace) -> _SelectionTopology:
        if not isinstance(workspace, Workspace):
            raise TypeError("workspace must be a Workspace")
        root = Path(os.fspath(workspace.root))
        if not root.is_absolute():
            root = Path(os.path.abspath(root))
        return cls(root)

    @property
    def workspace_key(self) -> str:
        return os.fspath(self.root)

    def workspace(self) -> Workspace:
        return Workspace(self.root)


def _coerce_backend(value: object) -> ConversationStorageBackend:
    if type(value) is ConversationStorageBackend:
        return value
    if type(value) is not str:
        raise ValueError("backend is invalid")
    try:
        return parse_conversation_storage_backend(value)
    except Exception:
        raise ValueError("backend is invalid") from None


def _validate_lease(
    lease: ConversationStorageMutationLease,
    workspace_key: str,
) -> None:
    try:
        lease.validate(workspace_key=workspace_key)
    except ConversationStorageCoordinationError:
        raise
    except Exception:
        raise ConversationStorageCoordinationError() from None


def _canonical_archive(
    adapter: object,
    lease: ConversationStorageMutationLease,
) -> tuple[ConversationArchive, bytes]:
    export = getattr(adapter, "export_archive", None)
    if not callable(export):
        raise TypeError("archive adapter must provide export_archive()")
    archive = export(lease)
    canonical = deserialize_conversation_archive(
        serialize_conversation_archive(archive)
    )
    return canonical, serialize_conversation_archive(canonical)


def _archive_is_empty(archive: ConversationArchive) -> bool:
    conversations = 0
    events = 0
    for session in archive.sessions:
        conversations += len(session.conversations)
        events += sum(
            len(conversation.events) for conversation in session.conversations
        )
    return conversations == 0 and events == 0


def _archive_adapter(
    workspace: Workspace,
    backend: ConversationStorageBackend,
) -> object:
    if backend is ConversationStorageBackend.FILESYSTEM:
        return compose_filesystem_conversation_archive(workspace)
    return compose_sqlite_conversation_archive(workspace)


def inspect_conversation_storage_parity(
    workspace: Workspace,
    *,
    lease: ConversationStorageMutationLease,
    source_backend: ConversationStorageBackend | str,
    target_backend: ConversationStorageBackend | str,
    source_generation: int,
) -> ConversationStorageMigrationReadiness:
    """Freshly compare public canonical archives under the caller's lease."""

    if not isinstance(workspace, Workspace):
        raise TypeError("workspace must be a Workspace")
    source = _coerce_backend(source_backend)
    target = _coerce_backend(target_backend)
    if type(source_generation) is not int or source_generation < 0:
        raise ValueError("source generation must be a nonnegative integer")
    topology = _SelectionTopology.capture(workspace)
    _validate_lease(lease, topology.workspace_key)
    operation_workspace = topology.workspace()
    source_archive, source_wire = _canonical_archive(
        _archive_adapter(operation_workspace, source),
        lease,
    )
    source_empty = _archive_is_empty(source_archive)
    if source is target or source_empty:
        parity = ConversationStorageParity.NOT_REQUIRED
    else:
        target_archive, target_wire = _canonical_archive(
            _archive_adapter(operation_workspace, target),
            lease,
        )
        if target_wire == source_wire:
            parity = ConversationStorageParity.VERIFIED
        elif _archive_is_empty(target_archive):
            parity = ConversationStorageParity.UNVERIFIED
        else:
            parity = ConversationStorageParity.MISMATCH
    _validate_lease(lease, topology.workspace_key)
    return ConversationStorageMigrationReadiness(
        source_backend=source,
        target_backend=target,
        source_generation=source_generation,
        source_is_empty=source_empty,
        parity=parity,
    )


def _provisioning_failure(
    failure: ConversationStorageProvisioningFailure,
) -> ConversationStorageSelectionFailure:
    return ConversationStorageSelectionFailure(failure.value)


def _selection_failure(
    failure: ConversationStorageSelectionFailure,
    *,
    desired: ConversationStorageBackend,
    installation: str,
    provisioning: str,
    parity: str = "unverified",
) -> ConversationStorageSelectionReport:
    return ConversationStorageSelectionReport.failed(
        failure,
        desired_backend=desired,
        installation=installation,
        provisioning=provisioning,
        parity=parity,
    )


def select_conversation_storage(
    workspace: Workspace,
    desired_backend: ConversationStorageBackend | str,
    *,
    install_missing: bool = False,
    yes: bool = False,
    installation_consent: (
        Callable[[ConversationStorageInstallationPlan], bool] | None
    ) = None,
    diagnostic_stream: TextIO | None = None,
    configuration_baseline: ConfigurationBaseline | None = None,
    configuration: ConfigurationSnapshot | None = None,
    precommit_guard: Callable[[], None] | None = None,
    provisioner: ConversationStorageProvisioner | None = None,
    mutation_timeout_seconds: float = _SELECTION_TIMEOUT_SECONDS,
) -> ConversationStorageSelectionReport:
    """Prepare and save one desired backend without changing activation."""

    try:
        desired = _coerce_backend(desired_backend)
    except (TypeError, ValueError):
        return ConversationStorageSelectionReport.failed(
            ConversationStorageSelectionFailure.INVALID_REQUEST
        )
    if (
        type(install_missing) is not bool
        or type(yes) is not bool
        or install_missing != yes
        or (
            desired is not ConversationStorageBackend.SQLITE
            and (install_missing or yes)
        )
        or (installation_consent is not None and not callable(installation_consent))
        or (precommit_guard is not None and not callable(precommit_guard))
        or ((configuration_baseline is None) != (configuration is None))
    ):
        return ConversationStorageSelectionReport.failed(
            ConversationStorageSelectionFailure.INVALID_REQUEST,
            desired_backend=desired,
        )
    try:
        topology = _SelectionTopology.capture(workspace)
        operation_workspace = topology.workspace()
    except Exception:
        return ConversationStorageSelectionReport.failed(
            ConversationStorageSelectionFailure.WORKSPACE_INVALID,
            desired_backend=desired,
        )
    manager = ConfigManager(operation_workspace)
    try:
        if configuration_baseline is None:
            baseline = manager.load_configuration_baseline()
            candidate = baseline.configuration
        else:
            if type(configuration_baseline) is not ConfigurationBaseline:
                raise TypeError("configuration baseline is invalid")
            if type(configuration) is not ConfigurationSnapshot:
                raise TypeError("configuration is invalid")
            baseline = configuration_baseline
            candidate = configuration
    except Exception:
        return ConversationStorageSelectionReport.failed(
            ConversationStorageSelectionFailure.WORKSPACE_INVALID,
            desired_backend=desired,
        )
    try:
        candidate = ConfigurationSnapshot(
            replace(
                candidate.system_defaults,
                conversation_storage_backend=desired.value,
            ),
            candidate.agent_configs,
        )
        manager.validate_configuration(
            manager.system_defaults_value(candidate.system_defaults),
            manager.agent_configs_value(candidate),
        )
    except Exception:
        return ConversationStorageSelectionReport.failed(
            ConversationStorageSelectionFailure.CONFIGURATION_INVALID,
            desired_backend=desired,
        )

    installation = (
        "not_required"
        if desired is ConversationStorageBackend.FILESYSTEM
        else "not_run"
    )
    provisioning = (
        "not_required"
        if desired is ConversationStorageBackend.FILESYSTEM
        else "not_run"
    )
    active_provisioner = (
        ConversationStorageProvisioner() if provisioner is None else provisioner
    )
    if type(active_provisioner) is not ConversationStorageProvisioner:
        return _selection_failure(
            ConversationStorageSelectionFailure.INVALID_REQUEST,
            desired=desired,
            installation=installation,
            provisioning=provisioning,
        )
    coordinator = WorkspaceConversationStorageMutationCoordinator(operation_workspace)
    try:
        with coordinator.borrow(
            timeout_seconds=mutation_timeout_seconds
        ) as proof_lease:
            _validate_lease(proof_lease, topology.workspace_key)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        return _selection_failure(
            ConversationStorageSelectionFailure.COORDINATION_FAILED,
            desired=desired,
            installation=installation,
            provisioning=provisioning,
        )

    if desired is ConversationStorageBackend.SQLITE:
        try:
            runtime = active_provisioner.probe_runtime()
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            runtime = None
        runtime_ready = (
            runtime is not None
            and runtime.capability is ConversationStorageCapability.READY
        )
        if runtime_ready:
            installation = "not_required"
        else:
            plan = active_provisioner.plan_installation()
            if plan is None:
                return _selection_failure(
                    ConversationStorageSelectionFailure.INSTALLATION_UNSUPPORTED,
                    desired=desired,
                    installation="not_run",
                    provisioning="not_run",
                )
            if active_provisioner.installation_disallowed:
                return _selection_failure(
                    ConversationStorageSelectionFailure.INSTALLATION_DISALLOWED,
                    desired=desired,
                    installation="not_run",
                    provisioning="not_run",
                )
            authorized = install_missing and yes
            if not authorized and installation_consent is not None:
                try:
                    consent = installation_consent(plan)
                except (KeyboardInterrupt, SystemExit):
                    raise
                except Exception:
                    consent = None
                if consent is False:
                    return _selection_failure(
                        ConversationStorageSelectionFailure.INSTALLATION_DISALLOWED,
                        desired=desired,
                        installation="not_run",
                        provisioning="not_run",
                    )
                authorized = consent is True
            if not authorized:
                return _selection_failure(
                    ConversationStorageSelectionFailure.INSTALLATION_REQUIRED,
                    desired=desired,
                    installation="not_run",
                    provisioning="not_run",
                )
            installed = active_provisioner.install(
                operation_workspace,
                plan,
                diagnostic_stream=(
                    sys.stderr if diagnostic_stream is None else diagnostic_stream
                ),
            )
            installation = installed.installation
            if not installed.completed:
                assert installed.failure is not None
                return _selection_failure(
                    _provisioning_failure(installed.failure),
                    desired=desired,
                    installation=installation,
                    provisioning=installed.provisioning,
                )

    try:
        with coordinator.borrow(timeout_seconds=mutation_timeout_seconds) as lease:
            _validate_lease(lease, topology.workspace_key)
            if desired is ConversationStorageBackend.SQLITE:
                try:
                    runtime = active_provisioner.probe_runtime()
                except (KeyboardInterrupt, SystemExit):
                    raise
                except Exception:
                    runtime = None
                if (
                    runtime is None
                    or runtime.capability is not ConversationStorageCapability.READY
                ):
                    return _selection_failure(
                        ConversationStorageSelectionFailure.INTERPRETER_UNAVAILABLE,
                        desired=desired,
                        installation=installation,
                        provisioning="not_run",
                    )
                prepared = active_provisioner.ensure_ready(
                    operation_workspace,
                    lease=lease,
                )
                provisioning = prepared.provisioning
                if not prepared.completed:
                    return _selection_failure(
                        ConversationStorageSelectionFailure.PROVISIONING_FAILED,
                        desired=desired,
                        installation=installation,
                        provisioning=provisioning,
                    )
                compose_sqlite_conversation_storage(operation_workspace)
            else:
                compose_filesystem_conversation_storage(operation_workspace)

            activation = load_conversation_storage_activation_state(operation_workspace)
            readiness = inspect_conversation_storage_parity(
                operation_workspace,
                lease=lease,
                source_backend=activation.backend,
                target_backend=desired,
                source_generation=activation.generation,
            )
            parity = readiness.parity.value
            if not readiness.can_activate:
                failure = (
                    ConversationStorageSelectionFailure.PARITY_MISMATCH
                    if readiness.parity is ConversationStorageParity.MISMATCH
                    else ConversationStorageSelectionFailure.MIGRATION_REQUIRED
                )
                return _selection_failure(
                    failure,
                    desired=desired,
                    installation=installation,
                    provisioning=provisioning,
                    parity=parity,
                )
            _validate_lease(lease, topology.workspace_key)
            if precommit_guard is not None:
                try:
                    precommit_guard()
                except (KeyboardInterrupt, SystemExit):
                    raise
                except Exception:
                    return _selection_failure(
                        ConversationStorageSelectionFailure.CONFIGURATION_CONFLICT,
                        desired=desired,
                        installation=installation,
                        provisioning=provisioning,
                        parity=parity,
                    )
            _validate_lease(lease, topology.workspace_key)
            try:
                changed = manager.commit_configuration(baseline, candidate)
            except ConfigurationCommitError as exc:
                if exc.failure is ConfigurationCommitFailure.VALIDATION:
                    failure = ConversationStorageSelectionFailure.CONFIGURATION_INVALID
                elif exc.failure is ConfigurationCommitFailure.CONFLICT:
                    failure = ConversationStorageSelectionFailure.CONFIGURATION_CONFLICT
                else:
                    failure = (
                        ConversationStorageSelectionFailure.CONFIGURATION_WRITE_FAILED
                    )
                return _selection_failure(
                    failure,
                    desired=desired,
                    installation=installation,
                    provisioning=provisioning,
                    parity=parity,
                )
    except (KeyboardInterrupt, SystemExit):
        raise
    except ConversationStorageCoordinationError:
        return _selection_failure(
            ConversationStorageSelectionFailure.COORDINATION_FAILED,
            desired=desired,
            installation=installation,
            provisioning=provisioning,
        )
    except Exception:
        return _selection_failure(
            ConversationStorageSelectionFailure.PROVISIONING_FAILED,
            desired=desired,
            installation=installation,
            provisioning=(
                "failed"
                if desired is ConversationStorageBackend.SQLITE
                else provisioning
            ),
        )

    return ConversationStorageSelectionReport(
        operation="use",
        outcome="completed",
        desired_backend=desired.value,
        installation=installation,
        provisioning=provisioning,
        parity=parity,
        configuration="saved" if changed else "unchanged",
        activation="next_shell",
    )


def _compose_active_services(
    workspace: Workspace,
    backend: ConversationStorageBackend,
) -> ConversationStorageServices:
    if backend is ConversationStorageBackend.FILESYSTEM:
        return compose_filesystem_conversation_storage(workspace)
    probe_sqlite_conversation_storage_runtime().require_ready()
    preflight_sqlite_conversation_storage(workspace).require_ready()
    return compose_sqlite_conversation_storage(workspace)


def compose_active_conversation_storage(
    workspace: Workspace,
    /,
) -> ConversationStorageServices:
    """Read-only compose the persisted active backend after optimistic recheck."""

    topology = _SelectionTopology.capture(workspace)
    operation_workspace = topology.workspace()
    before = load_conversation_storage_activation_state(operation_workspace)
    services = _compose_active_services(operation_workspace, before.backend)
    after = load_conversation_storage_activation_state(operation_workspace)
    if before != after or services.backend is not after.backend:
        raise ConversationStorageCoordinationError()
    return services


def activate_configured_conversation_storage(
    workspace: Workspace,
    *,
    configuration: ConfigurationSnapshot | None = None,
    mutation_timeout_seconds: float = _SELECTION_TIMEOUT_SECONDS,
) -> ConversationStorageStartup:
    """Resolve desired storage and atomically activate a complete ready bundle."""

    topology = _SelectionTopology.capture(workspace)
    operation_workspace = topology.workspace()
    if configuration is not None and type(configuration) is not ConfigurationSnapshot:
        raise TypeError("configuration must be a ConfigurationSnapshot")
    coordinator = WorkspaceConversationStorageMutationCoordinator(operation_workspace)
    with coordinator.borrow(timeout_seconds=mutation_timeout_seconds) as lease:
        _validate_lease(lease, topology.workspace_key)
        snapshot = ConfigManager(operation_workspace).load_configuration_snapshot()
        if configuration is not None and configuration != snapshot:
            raise ValueError("configuration changed before storage startup")
        desired = parse_conversation_storage_backend(
            snapshot.system_defaults.conversation_storage_backend
        )
        active = load_conversation_storage_activation_state(operation_workspace)
        active_services = _compose_active_services(
            operation_workspace,
            active.backend,
        )
        if desired is active.backend:
            outcome = ConversationStorageActivationOutcome(
                kind=ConversationStorageActivationOutcomeKind.UNCHANGED,
                previous=active,
                current=active,
            )
            return ConversationStorageStartup(active_services, outcome)

        if desired is ConversationStorageBackend.SQLITE:
            probe_sqlite_conversation_storage_runtime().require_ready()
            preflight_sqlite_conversation_storage(operation_workspace).require_ready()
        readiness = inspect_conversation_storage_parity(
            operation_workspace,
            lease=lease,
            source_backend=active.backend,
            target_backend=desired,
            source_generation=active.generation,
        )
        readiness.require_activation_ready()
        target_services = _compose_active_services(
            operation_workspace,
            desired,
        )
        _validate_lease(lease, topology.workspace_key)
        outcome = compare_and_set_conversation_storage_activation(
            operation_workspace,
            lease=lease,
            expected_generation=active.generation,
            readiness=readiness,
        )
        return ConversationStorageStartup(target_services, outcome)


__all__ = [
    "CONVERSATION_STORAGE_SELECTION_SCHEMA_VERSION",
    "ConversationStorageSelectionFailure",
    "ConversationStorageSelectionReport",
    "ConversationStorageStartup",
    "inspect_conversation_storage_parity",
    "select_conversation_storage",
    "compose_active_conversation_storage",
    "activate_configured_conversation_storage",
]
