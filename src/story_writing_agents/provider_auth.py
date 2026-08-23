"""Private native-provider authentication status and login lifecycle."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


AUTH_PROVIDERS = ("codex", "antigravity")
AUTH_STATUS_TIMEOUT_SECONDS = 5.0
AUTH_PROCESS_STOP_TIMEOUT_SECONDS = 2.0


class ProviderAuthState(StrEnum):
    """One content-free result from a native provider status probe."""

    AUTHENTICATED = "authenticated"
    UNAUTHENTICATED = "unauthenticated"
    UNAVAILABLE = "unavailable"
    INDETERMINATE = "indeterminate"


@dataclass(frozen=True)
class ProviderAuthSpec:
    """Immutable native command contract for one supported provider."""

    provider: str
    executable: str
    status_arguments: tuple[str, ...]
    login_arguments: tuple[str, ...]
    unauthenticated_returncodes: frozenset[int]

    @property
    def status_command(self) -> tuple[str, ...]:
        """Return the exact silent status-probe command."""

        return (self.executable, *self.status_arguments)

    @property
    def login_command(self) -> tuple[str, ...]:
        """Return the exact inherited-terminal login command."""

        return (self.executable, *self.login_arguments)


@dataclass(frozen=True)
class ProviderAuthStatus:
    """One provider's current content-free authentication state."""

    provider: str
    state: ProviderAuthState

    @property
    def authenticated(self) -> bool:
        """Return whether this exact status authorizes provider selection."""

        return self.state is ProviderAuthState.AUTHENTICATED


@dataclass(frozen=True)
class ProviderAuthSnapshot:
    """One ordered aggregate of all supported provider statuses."""

    statuses: tuple[ProviderAuthStatus, ...]

    def __post_init__(self) -> None:
        providers = tuple(status.provider for status in self.statuses)
        if providers != AUTH_PROVIDERS:
            raise ValueError("provider authentication snapshot is incomplete")

    def status_for(self, provider: str) -> ProviderAuthStatus:
        """Return the selected provider status or reject an unsupported name."""

        _require_provider(provider)
        for status in self.statuses:
            if status.provider == provider:
                return status
        raise AssertionError("validated provider is missing from snapshot")

    def is_authenticated(self, provider: str) -> bool:
        """Return whether the selected provider alone is authenticated."""

        return self.status_for(provider).authenticated

    @property
    def authenticated_providers(self) -> tuple[str, ...]:
        """Return only providers whose probes explicitly authenticated."""

        return tuple(
            status.provider for status in self.statuses if status.authenticated
        )

    @property
    def any_authenticated(self) -> bool:
        """Return whether at least one provider explicitly authenticated."""

        return any(status.authenticated for status in self.statuses)


@dataclass(frozen=True)
class ProviderLoginResult:
    """Safe local outcome from one native login and its fresh status probe."""

    provider: str
    returncode: int | None
    status: ProviderAuthStatus

    def __post_init__(self) -> None:
        if self.status.provider != self.provider:
            raise ValueError("provider login result status does not match provider")
        if isinstance(self.returncode, bool):
            raise TypeError("provider login return code must be an integer or None")

    @property
    def authenticated(self) -> bool:
        """Return fresh post-login authentication, never process-exit success."""

        return self.status.authenticated


ProcessFactory = Callable[..., subprocess.Popen[bytes]]


class ProviderAuthService:
    """Probe and run native authentication without reading provider output.

    Status probes are silent and bounded. Login processes inherit the current
    process's environment and terminal streams so the native provider remains
    the sole owner of browser, OAuth, keyring, and terminal interaction.
    """

    def __init__(
        self,
        workspace_root: Path,
        *,
        codex_executable: str = "codex",
        antigravity_executable: str = "agy",
        _process_factory: ProcessFactory = subprocess.Popen,
    ) -> None:
        if not isinstance(workspace_root, Path):
            raise TypeError("workspace_root must be a Path")
        if not workspace_root.is_dir():
            raise ValueError("workspace_root must be an existing directory")
        _require_executable(codex_executable)
        _require_executable(antigravity_executable)
        self._workspace_root = workspace_root
        self._process_factory = _process_factory
        self._specs = {
            "codex": ProviderAuthSpec(
                provider="codex",
                executable=codex_executable,
                status_arguments=("login", "status"),
                login_arguments=("login",),
                unauthenticated_returncodes=frozenset({1}),
            ),
            "antigravity": ProviderAuthSpec(
                provider="antigravity",
                executable=antigravity_executable,
                status_arguments=("models",),
                login_arguments=(),
                unauthenticated_returncodes=frozenset({1}),
            ),
        }

    def probe(self, provider: str) -> ProviderAuthStatus:
        """Run one bounded native status probe without capturing its output."""

        spec = self._spec_for(provider)
        try:
            process = self._process_factory(
                spec.status_command,
                cwd=str(self._workspace_root),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
            )
        except FileNotFoundError:
            return ProviderAuthStatus(provider, ProviderAuthState.UNAVAILABLE)
        except Exception:
            return ProviderAuthStatus(provider, ProviderAuthState.INDETERMINATE)

        try:
            returncode = process.wait(timeout=AUTH_STATUS_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            _stop_process(process)
            return ProviderAuthStatus(provider, ProviderAuthState.INDETERMINATE)
        except Exception:
            _stop_process(process)
            return ProviderAuthStatus(provider, ProviderAuthState.INDETERMINATE)
        except BaseException:
            _stop_process(process)
            raise

        if returncode == 0:
            state = ProviderAuthState.AUTHENTICATED
        elif returncode in spec.unauthenticated_returncodes:
            state = ProviderAuthState.UNAUTHENTICATED
        else:
            state = ProviderAuthState.INDETERMINATE
        return ProviderAuthStatus(provider, state)

    def probe_all(self) -> ProviderAuthSnapshot:
        """Run one explicit probe for each supported provider in stable order."""

        return ProviderAuthSnapshot(
            statuses=tuple(self.probe(provider) for provider in AUTH_PROVIDERS)
        )

    def login(self, provider: str) -> ProviderLoginResult:
        """Run one native login, then require a fresh authenticated probe.

        The login child inherits stdin, stdout, stderr, the environment, and
        the caller's process group. Python-side control exceptions are re-raised
        after bounded best-effort termination and reaping of that exact child.
        """

        spec = self._spec_for(provider)
        try:
            process = self._process_factory(
                spec.login_command,
                cwd=str(self._workspace_root),
                shell=False,
            )
        except FileNotFoundError:
            return ProviderLoginResult(
                provider=provider,
                returncode=None,
                status=ProviderAuthStatus(provider, ProviderAuthState.UNAVAILABLE),
            )
        except Exception:
            return ProviderLoginResult(
                provider=provider,
                returncode=None,
                status=ProviderAuthStatus(provider, ProviderAuthState.INDETERMINATE),
            )

        try:
            returncode = process.wait()
        except Exception:
            _stop_process(process)
            return ProviderLoginResult(
                provider=provider,
                returncode=None,
                status=ProviderAuthStatus(provider, ProviderAuthState.INDETERMINATE),
            )
        except BaseException:
            _stop_process(process)
            raise

        return ProviderLoginResult(
            provider=provider,
            returncode=returncode,
            status=self.probe(provider),
        )

    def _spec_for(self, provider: str) -> ProviderAuthSpec:
        _require_provider(provider)
        return self._specs[provider]


def _require_provider(provider: str) -> None:
    if provider not in AUTH_PROVIDERS:
        raise ValueError("provider must be codex or antigravity")


def _require_executable(executable: str) -> None:
    if not isinstance(executable, str) or not executable or "\x00" in executable:
        raise ValueError("provider executable must be a non-empty command path")


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    """Best-effort terminate, kill, and reap one exact child within bounds."""

    try:
        if process.poll() is not None:
            return
    except BaseException:
        pass

    try:
        process.terminate()
    except BaseException:
        pass
    try:
        process.wait(timeout=AUTH_PROCESS_STOP_TIMEOUT_SECONDS)
        return
    except subprocess.TimeoutExpired:
        pass
    except BaseException:
        pass

    try:
        if process.poll() is not None:
            return
    except BaseException:
        pass
    try:
        process.kill()
    except BaseException:
        pass
    try:
        process.wait(timeout=AUTH_PROCESS_STOP_TIMEOUT_SECONDS)
    except BaseException:
        pass
