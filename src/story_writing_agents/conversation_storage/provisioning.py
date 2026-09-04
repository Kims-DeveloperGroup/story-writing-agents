"""Capability-aware, allowlisted SQLite prerequisite provisioning."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable, Mapping, TextIO

from ..workspace.core import Workspace
from .contracts import (
    ConversationStorageCapability,
    ConversationStorageMutationLease,
    ConversationStoragePreflight,
)
from .sqlite import (
    preflight_sqlite_conversation_storage,
    probe_sqlite_conversation_storage_runtime,
    provision_sqlite_conversation_storage,
)


_CI_MARKERS = (
    "CI",
    "GITHUB_ACTIONS",
    "GITLAB_CI",
    "CIRCLECI",
    "BUILDKITE",
    "TF_BUILD",
    "JENKINS_URL",
    "TEAMCITY_VERSION",
    "TRAVIS",
    "APPVEYOR",
    "BITBUCKET_BUILD_NUMBER",
    "CODEBUILD_BUILD_ID",
    "DRONE",
)
_ALLOWLISTED_INSTALLERS = frozenset(
    {
        ("brew", "install", "sqlite"),
        ("apt-get", "install", "sqlite3", "libsqlite3-dev"),
        ("dnf", "install", "sqlite", "sqlite-devel"),
    }
)


@dataclass(frozen=True, slots=True)
class ConversationStorageInstallationPlan:
    """One exact, displayable installer command from the fixed allowlist."""

    argv: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.argv) is not tuple or self.argv not in _ALLOWLISTED_INSTALLERS:
            raise ValueError("installation argv is not allowlisted")

    @property
    def command(self) -> str:
        """Return the literal preview form; allowlisted tokens need no quoting."""

        return " ".join(self.argv)


class ConversationStorageProvisioningFailure(StrEnum):
    """Content-free prerequisite and storage provisioning failures."""

    INSTALLATION_REQUIRED = "installation_required"
    INSTALLATION_UNSUPPORTED = "installation_unsupported"
    INSTALLATION_DISALLOWED = "installation_disallowed"
    INSTALLATION_FAILED = "installation_failed"
    INTERPRETER_UNAVAILABLE = "interpreter_unavailable"
    PROVISIONING_FAILED = "provisioning_failed"


@dataclass(frozen=True, slots=True)
class ConversationStorageProvisioningResult:
    """Stable state from one installer or leased database provisioning step."""

    installation: str
    provisioning: str
    failure: ConversationStorageProvisioningFailure | None = None
    preflight: ConversationStoragePreflight | None = None

    def __post_init__(self) -> None:
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
        if self.failure is not None and type(self.failure) is not (
            ConversationStorageProvisioningFailure
        ):
            raise TypeError("failure is invalid")
        if self.preflight is not None and type(self.preflight) is not (
            ConversationStoragePreflight
        ):
            raise TypeError("preflight is invalid")

    @property
    def completed(self) -> bool:
        return self.failure is None


def _read_os_release() -> Mapping[str, str]:
    """Read only exact keys from the conventional Linux release document."""

    try:
        text = Path("/etc/os-release").read_text(
            encoding="utf-8",
            errors="strict",
        )
    except (OSError, UnicodeError):
        return {}
    values: dict[str, str] = {}
    for line in text.splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        if not key:
            continue
        value = raw_value
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


class ConversationStorageProvisioner:
    """Detect the host, execute at most one allowlisted install, and provision.

    All host-sensitive collaborators are constructor seams. The process runner
    receives no custom environment, stdin, capture, timeout, retry, privilege,
    or shell behavior.
    """

    def __init__(
        self,
        *,
        platform_system: Callable[[], str] = platform.system,
        os_release_reader: Callable[[], Mapping[str, str]] = _read_os_release,
        environment: Mapping[str, str] | None = None,
        executable_lookup: Callable[[str], str | None] = shutil.which,
        process_runner: Callable[..., Any] = subprocess.run,
    ) -> None:
        for label, collaborator in (
            ("platform_system", platform_system),
            ("os_release_reader", os_release_reader),
            ("executable_lookup", executable_lookup),
            ("process_runner", process_runner),
        ):
            if not callable(collaborator):
                raise TypeError(f"{label} must be callable")
        if environment is not None and not isinstance(environment, Mapping):
            raise TypeError("environment must be a mapping")
        self._platform_system = platform_system
        self._os_release_reader = os_release_reader
        self._environment = os.environ if environment is None else environment
        self._executable_lookup = executable_lookup
        self._process_runner = process_runner

    def probe_runtime(self) -> ConversationStoragePreflight:
        """Exercise SQLite through the current Python interpreter only."""

        return probe_sqlite_conversation_storage_runtime()

    def plan_installation(self) -> ConversationStorageInstallationPlan | None:
        """Return the one exact command supported by the current exact OS ID."""

        try:
            system = self._platform_system()
        except Exception:
            return None
        if system == "Darwin":
            try:
                discovered = self._executable_lookup("brew")
            except Exception:
                return None
            if type(discovered) is not str or not discovered:
                return None
            return ConversationStorageInstallationPlan(("brew", "install", "sqlite"))
        if system != "Linux":
            return None
        try:
            release = self._os_release_reader()
        except Exception:
            return None
        if not isinstance(release, Mapping):
            return None
        os_id = release.get("ID")
        if os_id in {"debian", "ubuntu"}:
            return ConversationStorageInstallationPlan(
                ("apt-get", "install", "sqlite3", "libsqlite3-dev")
            )
        if os_id in {"fedora", "rhel"}:
            return ConversationStorageInstallationPlan(
                ("dnf", "install", "sqlite", "sqlite-devel")
            )
        return None

    @property
    def installation_disallowed(self) -> bool:
        """Refuse host mutation for any recognized nonempty CI marker."""

        return any(
            type(self._environment.get(marker)) is str
            and self._environment.get(marker) != ""
            for marker in _CI_MARKERS
        )

    def install(
        self,
        workspace: Workspace,
        plan: ConversationStorageInstallationPlan,
        *,
        diagnostic_stream: TextIO,
    ) -> ConversationStorageProvisioningResult:
        """Execute one exact inherited-terminal installer without retry."""

        if not isinstance(workspace, Workspace):
            raise TypeError("workspace must be a Workspace")
        if type(plan) is not ConversationStorageInstallationPlan:
            raise TypeError("plan must be a ConversationStorageInstallationPlan")
        if not callable(getattr(diagnostic_stream, "write", None)):
            raise TypeError("diagnostic_stream must provide write()")
        if self.installation_disallowed:
            return ConversationStorageProvisioningResult(
                installation="not_run",
                provisioning="not_run",
                failure=(
                    ConversationStorageProvisioningFailure.INSTALLATION_DISALLOWED
                ),
            )
        try:
            completed = self._process_runner(
                plan.argv,
                stdout=diagnostic_stream,
                stderr=diagnostic_stream,
                shell=False,
                cwd=os.fspath(workspace.root),
                check=False,
            )
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            return ConversationStorageProvisioningResult(
                installation="failed",
                provisioning="not_run",
                failure=(ConversationStorageProvisioningFailure.INSTALLATION_FAILED),
            )
        if type(getattr(completed, "returncode", None)) is not int or (
            completed.returncode != 0
        ):
            return ConversationStorageProvisioningResult(
                installation="failed",
                provisioning="not_run",
                failure=(ConversationStorageProvisioningFailure.INSTALLATION_FAILED),
            )
        return ConversationStorageProvisioningResult(
            installation="completed",
            provisioning="not_run",
        )

    def ensure_ready(
        self,
        workspace: Workspace,
        *,
        lease: ConversationStorageMutationLease,
    ) -> ConversationStorageProvisioningResult:
        """Preflight and, only when absent, provision under the caller lease."""

        if not isinstance(workspace, Workspace):
            raise TypeError("workspace must be a Workspace")
        try:
            preflight = preflight_sqlite_conversation_storage(workspace)
            if preflight.capability is ConversationStorageCapability.READY:
                return ConversationStorageProvisioningResult(
                    installation="not_required",
                    provisioning="not_required",
                    preflight=preflight,
                )
            if preflight.capability is not (
                ConversationStorageCapability.PROVISIONING_NEEDED
            ):
                return ConversationStorageProvisioningResult(
                    installation="not_required",
                    provisioning="failed",
                    failure=(
                        ConversationStorageProvisioningFailure.PROVISIONING_FAILED
                    ),
                    preflight=preflight,
                )
            ready = provision_sqlite_conversation_storage(
                workspace,
                lease=lease,
            )
            ready.require_ready()
            return ConversationStorageProvisioningResult(
                installation="not_required",
                provisioning="completed",
                preflight=ready,
            )
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            return ConversationStorageProvisioningResult(
                installation="not_required",
                provisioning="failed",
                failure=ConversationStorageProvisioningFailure.PROVISIONING_FAILED,
            )


__all__ = [
    "ConversationStorageInstallationPlan",
    "ConversationStorageProvisioningFailure",
    "ConversationStorageProvisioningResult",
    "ConversationStorageProvisioner",
]
