"""Private interactive provider-authentication presentation coordination."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, TypeVar, cast

from .interactive_terminal import (
    Choice,
    ControlOutcomeKind,
    InteractiveAdvisoryProgress,
    InputPresentation,
    InteractivePickerInput,
    InteractiveStartupPresentation,
    InteractiveTerminal,
    Notice,
    Prompt,
    SemanticRole,
    ViewState,
)
from .provider_auth import (
    AUTH_PROVIDERS,
    ProviderAuthSnapshot,
    ProviderAuthState,
    ProviderAuthStatus,
    ProviderLoginResult,
)


_AuthProbeT = TypeVar("_AuthProbeT")


class InteractiveAuthResultKind(StrEnum):
    """One provider selection or terminal-control result."""

    AUTHENTICATED = "authenticated"
    NOT_AUTHENTICATED = "not_authenticated"
    CANCEL = "cancel"
    INTERRUPT = "interrupt"
    EOF = "eof"


@dataclass(frozen=True, slots=True)
class InteractiveAuthResult:
    """Immutable provider-neutral result from one auth-picker interaction."""

    kind: InteractiveAuthResultKind
    snapshot: ProviderAuthSnapshot
    provider: str | None = None
    status: ProviderAuthStatus | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, InteractiveAuthResultKind):
            raise TypeError("kind must be an InteractiveAuthResultKind")
        if not isinstance(self.snapshot, ProviderAuthSnapshot):
            raise TypeError("snapshot must be a ProviderAuthSnapshot")
        terminal_result = self.kind in {
            InteractiveAuthResultKind.CANCEL,
            InteractiveAuthResultKind.INTERRUPT,
            InteractiveAuthResultKind.EOF,
        }
        if terminal_result:
            if self.provider is not None or self.status is not None:
                raise ValueError("terminal auth results do not carry a provider")
            return
        _require_provider(self.provider)
        if not isinstance(self.status, ProviderAuthStatus):
            raise TypeError("provider auth results require a status")
        if self.status.provider != self.provider:
            raise ValueError("auth result provider and status must match")
        if self.snapshot.status_for(self.provider) != self.status:
            raise ValueError("auth result status must match its snapshot")
        if (
            self.kind is InteractiveAuthResultKind.AUTHENTICATED
        ) != self.status.authenticated:
            raise ValueError("auth result kind must match authentication status")


class _ProviderAuth(Protocol):
    """Narrow collaborator supplied by the native auth-process boundary."""

    def probe(self, provider: str) -> ProviderAuthStatus:
        """Return one selected provider's current status."""

    def probe_all(self) -> ProviderAuthSnapshot:
        """Return one ordered status for each supported provider."""

    def login(self, provider: str, *, recovery: bool = False) -> ProviderLoginResult:
        """Run native login and return its fresh post-login status."""


def select_default_provider(
    snapshot: ProviderAuthSnapshot,
    *,
    newly_authenticated_provider: str | None = None,
) -> str | None:
    """Choose a missing workspace default from exact live authentication.

    A provider authenticated by the immediately preceding interaction wins.
    Otherwise a sole authenticated provider is selected, both authenticated
    providers prefer Codex, and no authenticated provider yields no default.
    """

    _require_snapshot(snapshot)
    _require_optional_provider(newly_authenticated_provider)
    if newly_authenticated_provider is not None:
        if not snapshot.is_authenticated(newly_authenticated_provider):
            raise ValueError(
                "newly authenticated provider must be exactly authenticated"
            )
        return newly_authenticated_provider
    authenticated = snapshot.authenticated_providers
    if len(authenticated) == 1:
        return authenticated[0]
    if len(authenticated) == len(AUTH_PROVIDERS):
        return "codex"
    return None


class InteractiveAuthCoordinator:
    """Coordinate one provider-neutral auth picker and native-login handoff."""

    __slots__ = ("_auth", "_picker", "_presentation", "_progress")

    def __init__(
        self,
        auth_service: _ProviderAuth,
        terminal: InteractiveTerminal,
        *,
        sleeper: Callable[[float], object] = time.sleep,
    ) -> None:
        """Bind auth, picker, and startup presentation without performing I/O."""

        for name in ("probe", "probe_all", "login"):
            if not callable(getattr(auth_service, name, None)):
                raise TypeError("auth_service must provide probe, probe_all, and login")
        if not isinstance(terminal, InteractiveTerminal):
            raise TypeError("terminal must be an InteractiveTerminal")
        if not callable(sleeper):
            raise TypeError("sleeper must be callable")
        self._auth: _ProviderAuth = auth_service
        self._picker = InteractivePickerInput(terminal)
        self._progress = InteractiveAdvisoryProgress(terminal)
        self._presentation = InteractiveStartupPresentation(
            terminal,
            sleeper=sleeper,
        )

    def show_welcome(self) -> None:
        """Display the fixed accessible startup welcome ceremony."""

        self._presentation.show_welcome()

    def probe_all(self) -> ProviderAuthSnapshot:
        """Return one explicitly probed status for each supported provider."""

        snapshot = self._run_probe_progress(self._auth.probe_all)
        if not isinstance(snapshot, ProviderAuthSnapshot):
            raise TypeError("auth service must return a ProviderAuthSnapshot")
        return snapshot

    def _run_probe_progress(
        self,
        operation: Callable[[], _AuthProbeT],
    ) -> _AuthProbeT:
        """Run one blocking native-status probe under terminal progress."""

        if not callable(operation):
            raise TypeError("auth probe operation must be callable")

        async def probe() -> object:
            """Keep terminal animation responsive while the probe blocks."""

            return await self._progress.run(asyncio.to_thread(operation))

        with asyncio.Runner() as runner:
            return cast(_AuthProbeT, runner.run(probe()))

    def choices(
        self,
        snapshot: ProviderAuthSnapshot,
        *,
        selected_provider: str | None = None,
    ) -> tuple[Choice, ...]:
        """Project exact status text and optional visible current selection."""

        _require_snapshot(snapshot)
        _require_optional_provider(selected_provider)
        choices: list[Choice] = []
        for provider in AUTH_PROVIDERS:
            status = snapshot.status_for(provider)
            description, enabled, disabled_reason = _choice_status(status)
            if enabled and provider == selected_provider:
                description = f"{description} Current selection."
            choices.append(
                Choice(
                    provider,
                    _provider_label(provider),
                    description=description,
                    enabled=enabled,
                    disabled_reason=disabled_reason,
                )
            )
        return tuple(choices)

    def run(
        self,
        snapshot: ProviderAuthSnapshot | None = None,
        *,
        presentation: InputPresentation = InputPresentation.STANDARD,
        selected_provider: str | None = None,
    ) -> InteractiveAuthResult:
        """Read one picker and optionally perform one explicit native login."""

        if not isinstance(presentation, InputPresentation):
            raise TypeError("presentation must be an InputPresentation")
        if snapshot is None:
            snapshot = self.probe_all()
        else:
            _require_snapshot(snapshot)
        _require_optional_provider(selected_provider)
        return self._run_provider_picker(
            snapshot,
            presentation=presentation,
            highlighted_provider=selected_provider,
            selected_provider=selected_provider,
            prompt=Prompt(
                "Provider authentication",
                instruction=(
                    "Choose a provider. The picker cancel action returns without "
                    "signing in."
                ),
            ),
            notice=Notice(
                "Statuses come only from the native provider commands.",
                label="authentication",
                role=SemanticRole.INFO,
            ),
        )

    def select_default(
        self,
        snapshot: ProviderAuthSnapshot,
        *,
        selected_provider: str | None = None,
    ) -> InteractiveAuthResult:
        """Select and authenticate a missing workspace default provider."""

        _require_snapshot(snapshot)
        _require_optional_provider(selected_provider)
        return self._run_provider_picker(
            snapshot,
            presentation=InputPresentation.STANDARD,
            highlighted_provider=selected_provider,
            selected_provider=None,
            prompt=Prompt(
                "Default provider",
                instruction=(
                    "Choose the workspace default provider. Story saves this "
                    "choice for later shells; selecting an unauthenticated "
                    "provider starts native sign-in."
                ),
            ),
            notice=Notice(
                (
                    "Agents without their own provider setting inherit this "
                    "workspace default."
                ),
                label="default provider",
                role=SemanticRole.INFO,
            ),
        )

    def _run_provider_picker(
        self,
        snapshot: ProviderAuthSnapshot,
        *,
        presentation: InputPresentation,
        highlighted_provider: str | None,
        selected_provider: str | None,
        prompt: Prompt,
        notice: Notice,
    ) -> InteractiveAuthResult:
        """Run one provider picker with caller-owned purpose and selection state."""

        choices = self.choices(snapshot, selected_provider=selected_provider)
        enabled_identities = {
            choice.identity for choice in choices if choice.enabled
        }
        highlighted_id = (
            highlighted_provider
            if highlighted_provider in enabled_identities
            else None
        )
        selected_id = (
            selected_provider if selected_provider in enabled_identities else None
        )
        outcome = self._picker.read_picker(
            prompt,
            choices,
            presentation=presentation,
            highlighted_id=highlighted_id,
            selected_id=selected_id,
            notice=notice,
            state=ViewState.OPEN,
            transient=True,
        )
        if outcome.kind is ControlOutcomeKind.CANCEL:
            return _terminal_result(InteractiveAuthResultKind.CANCEL, snapshot)
        if outcome.kind is ControlOutcomeKind.INTERRUPT:
            return _terminal_result(InteractiveAuthResultKind.INTERRUPT, snapshot)
        if outcome.kind is ControlOutcomeKind.EOF:
            return _terminal_result(InteractiveAuthResultKind.EOF, snapshot)
        if outcome.kind is not ControlOutcomeKind.COMMIT or outcome.text is None:
            raise RuntimeError("auth picker returned an unsupported outcome")

        provider = outcome.text
        _require_provider(provider)
        current = snapshot.status_for(provider)
        if current.state is ProviderAuthState.AUTHENTICATED:
            return _provider_result(snapshot, current)
        if current.state is ProviderAuthState.UNAUTHENTICATED:
            return self._login(provider)
        if current.state is ProviderAuthState.INDETERMINATE:
            refreshed = self._run_probe_progress(
                lambda: self._auth.probe(provider)
            )
            _require_status(refreshed, provider)
            refreshed_snapshot = _snapshot_with_status(snapshot, refreshed)
            return _provider_result(refreshed_snapshot, refreshed)
        raise RuntimeError("unavailable auth provider was selected")

    def recover(self, provider: str) -> bool:
        """Run native sign-in for a failed request even if a status probe succeeds."""

        _require_provider(provider)
        return (
            self._login(provider, recovery=True).kind
            is InteractiveAuthResultKind.AUTHENTICATED
        )

    def _login(
        self,
        provider: str,
        *,
        recovery: bool = False,
    ) -> InteractiveAuthResult:
        """Clear guidance, run native login, then fully refresh both providers."""

        label = _provider_label(provider)
        self._presentation.show_notice(
            Notice(
                (
                    f"Opening {label} native sign-in. Complete its prompt, "
                    "then return here."
                    + (
                        " If sign-in completes silently and the native prompt stays open, "
                        "exit it normally to continue."
                        if recovery else ""
                    )
                ),
                label="authentication",
                role=SemanticRole.INFO,
                state=ViewState.LOADING,
            )
        )
        login_result = (
            self._auth.login(provider, recovery=True)
            if recovery else self._auth.login(provider)
        )
        if not isinstance(login_result, ProviderLoginResult):
            raise TypeError("auth service must return a ProviderLoginResult")
        if login_result.provider != provider:
            raise ValueError("auth service returned a different provider")
        _require_status(login_result.status, provider)
        refreshed_snapshot = self._auth.probe_all() if recovery else self.probe_all()
        if recovery and not login_result.authenticated:
            refreshed_snapshot = _snapshot_with_status(
                refreshed_snapshot, login_result.status,
            )
        refreshed_status = refreshed_snapshot.status_for(provider)
        self._presentation.show_result(_result_notice(refreshed_status))
        return _provider_result(refreshed_snapshot, refreshed_status)


def _choice_status(
    status: ProviderAuthStatus,
) -> tuple[str, bool, str | None]:
    """Return fixed visible status copy and picker availability."""

    if status.state is ProviderAuthState.AUTHENTICATED:
        return "Authenticated. Ready to use.", True, None
    if status.state is ProviderAuthState.UNAUTHENTICATED:
        return "Not authenticated. Select to start native sign-in.", True, None
    if status.state is ProviderAuthState.UNAVAILABLE:
        return (
            "Unavailable.",
            False,
            "Install the provider CLI, then retry.",
        )
    return "Status indeterminate. Select to check again.", True, None


def _result_notice(status: ProviderAuthStatus) -> Notice:
    """Project one fixed result without provider output or account details."""

    label = _provider_label(status.provider)
    if status.state is ProviderAuthState.AUTHENTICATED:
        return Notice(
            f"{label} authentication succeeded.",
            label="authentication",
            role=SemanticRole.SUCCESS,
            state=ViewState.COMPLETED,
        )
    if status.state is ProviderAuthState.UNAUTHENTICATED:
        text = f"{label} sign-in did not complete. Choose it to try again."
    elif status.state is ProviderAuthState.UNAVAILABLE:
        text = f"{label} CLI is unavailable. Install it, then retry."
    else:
        text = (
            f"{label} authentication could not be confirmed. "
            "Check the native CLI, then retry."
        )
    return Notice(
        text,
        label="authentication",
        role=SemanticRole.ERROR,
        state=ViewState.ERROR,
    )


def _snapshot_with_status(
    snapshot: ProviderAuthSnapshot,
    status: ProviderAuthStatus,
) -> ProviderAuthSnapshot:
    """Replace exactly one selected status in stable provider order."""

    return ProviderAuthSnapshot(
        tuple(
            status if current.provider == status.provider else current
            for current in snapshot.statuses
        )
    )


def _provider_result(
    snapshot: ProviderAuthSnapshot,
    status: ProviderAuthStatus,
) -> InteractiveAuthResult:
    """Return strict fresh authentication as the only success result."""

    kind = (
        InteractiveAuthResultKind.AUTHENTICATED
        if status.authenticated
        else InteractiveAuthResultKind.NOT_AUTHENTICATED
    )
    return InteractiveAuthResult(
        kind,
        snapshot,
        provider=status.provider,
        status=status,
    )


def _terminal_result(
    kind: InteractiveAuthResultKind,
    snapshot: ProviderAuthSnapshot,
) -> InteractiveAuthResult:
    """Return one picker control result without a selected provider."""

    return InteractiveAuthResult(kind, snapshot)


def _require_snapshot(snapshot: object) -> None:
    if not isinstance(snapshot, ProviderAuthSnapshot):
        raise TypeError("snapshot must be a ProviderAuthSnapshot")


def _require_status(status: object, provider: str) -> None:
    if not isinstance(status, ProviderAuthStatus):
        raise TypeError("auth service must return a ProviderAuthStatus")
    if status.provider != provider:
        raise ValueError("auth service returned a different provider status")


def _require_optional_provider(provider: object) -> None:
    if provider is None:
        return
    _require_provider(provider)


def _require_provider(provider: object) -> None:
    if provider not in AUTH_PROVIDERS:
        raise ValueError("provider must be codex or antigravity")


def _provider_label(provider: str) -> str:
    _require_provider(provider)
    return "Codex" if provider == "codex" else "Antigravity"
