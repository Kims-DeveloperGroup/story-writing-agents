"""Deterministic provider-neutral interactive authentication tests."""

from __future__ import annotations

import inspect
import subprocess
import sys
import tempfile
import threading
import unittest
from dataclasses import FrozenInstanceError, fields
from pathlib import Path

import story_writing_agents
from story_writing_agents import interactive_auth
from story_writing_agents.interactive_auth import (
    InteractiveAuthCoordinator,
    InteractiveAuthResult,
    InteractiveAuthResultKind,
    select_default_provider,
)
from story_writing_agents.interactive_terminal import (
    ControlOutcome,
    ControlOutcomeKind,
    InputPresentation,
    InteractiveTerminal,
    SemanticRole,
    ViewState,
)
from story_writing_agents.provider_auth import (
    AUTH_PROVIDERS,
    ProviderAuthSnapshot,
    ProviderAuthState,
    ProviderAuthStatus,
    ProviderLoginResult,
)


def status(provider: str, state: ProviderAuthState) -> ProviderAuthStatus:
    return ProviderAuthStatus(provider, state)


def snapshot(
    codex: ProviderAuthState,
    antigravity: ProviderAuthState,
) -> ProviderAuthSnapshot:
    return ProviderAuthSnapshot(
        (
            status("codex", codex),
            status("antigravity", antigravity),
        )
    )


class ScriptedAuthService:
    """Expose exact auth collaborator calls with queued typed results."""

    def __init__(self) -> None:
        self.events: list[tuple[str, object | None]] = []
        self.thread_ids: list[int] = []
        self.probe_results: list[object] = []
        self.probe_all_results: list[object] = []
        self.login_results: list[object] = []

    def probe(self, provider: str) -> ProviderAuthStatus:
        self.thread_ids.append(threading.get_ident())
        self.events.append(("probe", provider))
        result = self.probe_results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result  # type: ignore[return-value]

    def probe_all(self) -> ProviderAuthSnapshot:
        self.thread_ids.append(threading.get_ident())
        self.events.append(("probe_all", None))
        result = self.probe_all_results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result  # type: ignore[return-value]

    def login(self, provider: str) -> ProviderLoginResult:
        self.thread_ids.append(threading.get_ident())
        self.events.append(("login", provider))
        result = self.login_results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result  # type: ignore[return-value]


class TerminalHarness:
    """Build one callback terminal with recorded picker/presentation calls."""

    def __init__(self, *outcomes: object) -> None:
        self.outcomes = list(outcomes)
        self.picker_states: list[object] = []
        self.events: list[tuple[str, object]] = []
        self.writes: list[str] = []
        self.sleeps: list[float] = []
        self.terminal = InteractiveTerminal(
            lambda prompt: (_ for _ in ()).throw(
                AssertionError(f"unexpected line read: {prompt}")
            ),
            self.writes.append,
        )
        self.terminal._picker_read = self.read_picker
        self.terminal._startup_welcome = self.show_welcome
        self.terminal._startup_notice = self.show_notice
        self.terminal._startup_result_notice = self.show_result

    def read_picker(self, state: object, *, transient: bool = True) -> ControlOutcome:
        self.events.append(("picker", transient))
        self.picker_states.append(state)
        if not self.outcomes:
            raise AssertionError("unexpected auth picker read")
        result = self.outcomes.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result  # type: ignore[return-value]

    def show_welcome(self, sleeper: object) -> None:
        self.events.append(("welcome", sleeper))

    def show_notice(self, notice: object, sleeper: object) -> None:
        self.events.append(("notice", notice))

    def show_result(self, notice: object, sleeper: object) -> None:
        self.events.append(("result", notice))

    def coordinator(self, auth: object) -> InteractiveAuthCoordinator:
        return InteractiveAuthCoordinator(
            auth,
            self.terminal,
            sleeper=self.sleeps.append,
        )


class InteractiveAuthContractTests(unittest.TestCase):
    def test_default_selection_matrix_is_exact(self) -> None:
        states = tuple(ProviderAuthState)
        for codex in states:
            for antigravity in states:
                current = snapshot(codex, antigravity)
                authenticated = current.authenticated_providers
                expected = (
                    authenticated[0]
                    if len(authenticated) == 1
                    else "codex"
                    if len(authenticated) == 2
                    else None
                )
                with self.subTest(codex=codex, antigravity=antigravity):
                    self.assertEqual(select_default_provider(current), expected)

        both = snapshot(
            ProviderAuthState.AUTHENTICATED,
            ProviderAuthState.AUTHENTICATED,
        )
        self.assertEqual(
            select_default_provider(
                both,
                newly_authenticated_provider="antigravity",
            ),
            "antigravity",
        )
        for provider in AUTH_PROVIDERS:
            unavailable = snapshot(
                ProviderAuthState.UNAUTHENTICATED
                if provider == "codex"
                else ProviderAuthState.AUTHENTICATED,
                ProviderAuthState.UNAUTHENTICATED
                if provider == "antigravity"
                else ProviderAuthState.AUTHENTICATED,
            )
            with self.subTest(provider=provider), self.assertRaises(ValueError):
                select_default_provider(
                    unavailable,
                    newly_authenticated_provider=provider,
                )
        for malformed in ("gemini", "Codex", "", 17):
            with self.subTest(malformed=malformed), self.assertRaises(ValueError):
                select_default_provider(
                    both,
                    newly_authenticated_provider=malformed,  # type: ignore[arg-type]
                )

    def test_result_contract_is_exact_frozen_slotted_and_correlated(self) -> None:
        self.assertEqual(
            tuple((member.name, member.value) for member in InteractiveAuthResultKind),
            (
                ("AUTHENTICATED", "authenticated"),
                ("NOT_AUTHENTICATED", "not_authenticated"),
                ("CANCEL", "cancel"),
                ("INTERRUPT", "interrupt"),
                ("EOF", "eof"),
            ),
        )
        self.assertEqual(
            tuple(field.name for field in fields(InteractiveAuthResult)),
            ("kind", "snapshot", "provider", "status"),
        )
        current = snapshot(
            ProviderAuthState.AUTHENTICATED,
            ProviderAuthState.UNAUTHENTICATED,
        )
        result = InteractiveAuthResult(
            InteractiveAuthResultKind.AUTHENTICATED,
            current,
            provider="codex",
            status=current.status_for("codex"),
        )
        with self.assertRaises(FrozenInstanceError):
            result.provider = "antigravity"  # type: ignore[misc]
        self.assertFalse(hasattr(result, "__dict__"))

        malformed_arguments = (
            (
                InteractiveAuthResultKind.CANCEL,
                {"provider": "codex", "status": current.status_for("codex")},
            ),
            (
                InteractiveAuthResultKind.AUTHENTICATED,
                {
                    "provider": "antigravity",
                    "status": current.status_for("antigravity"),
                },
            ),
            (
                InteractiveAuthResultKind.NOT_AUTHENTICATED,
                {"provider": "codex", "status": current.status_for("codex")},
            ),
        )
        for kind, kwargs in malformed_arguments:
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                InteractiveAuthResult(kind, current, **kwargs)

    def test_choices_render_every_state_without_color_dependence(self) -> None:
        auth = ScriptedAuthService()
        harness = TerminalHarness()
        coordinator = harness.coordinator(auth)
        expected = {
            ProviderAuthState.AUTHENTICATED: (
                "Authenticated. Ready to use.",
                True,
                None,
            ),
            ProviderAuthState.UNAUTHENTICATED: (
                "Not authenticated. Select to start native sign-in.",
                True,
                None,
            ),
            ProviderAuthState.UNAVAILABLE: (
                "Unavailable.",
                False,
                "Install the provider CLI, then retry.",
            ),
            ProviderAuthState.INDETERMINATE: (
                "Status indeterminate. Select to check again.",
                True,
                None,
            ),
        }
        for codex_state in ProviderAuthState:
            for antigravity_state in ProviderAuthState:
                current = snapshot(codex_state, antigravity_state)
                choices = coordinator.choices(current, selected_provider="codex")
                self.assertEqual(tuple(choice.identity for choice in choices), AUTH_PROVIDERS)
                self.assertEqual(tuple(choice.label for choice in choices), ("Codex", "Antigravity"))
                for choice, state in zip(
                    choices,
                    (codex_state, antigravity_state),
                    strict=True,
                ):
                    description, enabled, disabled_reason = expected[state]
                    if choice.identity == "codex" and enabled:
                        description = f"{description} Current selection."
                    with self.subTest(
                        codex=codex_state,
                        antigravity=antigravity_state,
                        provider=choice.identity,
                    ):
                        self.assertEqual(choice.description, description)
                        self.assertIs(choice.enabled, enabled)
                        self.assertEqual(choice.disabled_reason, disabled_reason)
                        self.assertNotIn("\x1b", choice.description)


class InteractiveAuthCoordinatorTests(unittest.TestCase):
    def test_fresh_status_probes_use_terminal_progress_off_the_event_loop(
        self,
    ) -> None:
        """Bracket full and selected probes without blocking animation."""

        initial = snapshot(
            ProviderAuthState.INDETERMINATE,
            ProviderAuthState.UNAVAILABLE,
        )
        auth = ScriptedAuthService()
        auth.probe_all_results.append(initial)
        auth.probe_results.append(
            status("codex", ProviderAuthState.AUTHENTICATED)
        )
        harness = TerminalHarness(
            ControlOutcome(ControlOutcomeKind.COMMIT, text="codex")
        )
        progress_events: list[str] = []

        async def observe_progress(operation):
            progress_events.append("started")
            try:
                return await operation
            finally:
                progress_events.append("completed")

        harness.terminal._advisory_progress = observe_progress
        coordinator = harness.coordinator(auth)
        self.assertIs(coordinator.probe_all(), initial)
        result = coordinator.run(initial)

        self.assertEqual(result.kind, InteractiveAuthResultKind.AUTHENTICATED)
        self.assertEqual(progress_events, ["started", "completed"] * 2)
        self.assertEqual(
            auth.events,
            [("probe_all", None), ("probe", "codex")],
        )
        self.assertTrue(auth.thread_ids)
        self.assertTrue(
            all(thread_id != threading.get_ident() for thread_id in auth.thread_ids)
        )

    def test_terminal_controls_and_authenticated_selection_do_not_login(self) -> None:
        current = snapshot(
            ProviderAuthState.AUTHENTICATED,
            ProviderAuthState.UNAUTHENTICATED,
        )
        outcomes = (
            (ControlOutcome(ControlOutcomeKind.COMMIT, text="codex"), InteractiveAuthResultKind.AUTHENTICATED),
            (ControlOutcome(ControlOutcomeKind.CANCEL), InteractiveAuthResultKind.CANCEL),
            (ControlOutcome(ControlOutcomeKind.INTERRUPT), InteractiveAuthResultKind.INTERRUPT),
            (ControlOutcome(ControlOutcomeKind.EOF), InteractiveAuthResultKind.EOF),
        )
        for outcome, expected_kind in outcomes:
            with self.subTest(outcome=outcome.kind):
                auth = ScriptedAuthService()
                harness = TerminalHarness(outcome)
                result = harness.coordinator(auth).run(
                    current,
                    selected_provider="codex",
                )
                self.assertEqual(result.kind, expected_kind)
                self.assertIs(result.snapshot, current)
                self.assertEqual(auth.events, [])
                self.assertEqual(harness.events, [("picker", True)])
                state = harness.picker_states[0]
                picker = state.picker  # type: ignore[attr-defined]
                self.assertEqual(picker.prompt.name, "Provider authentication")
                self.assertEqual(picker.highlighted_id, "codex")
                self.assertEqual(picker.selected_id, "codex")
                self.assertEqual(picker.state, ViewState.OPEN)
                self.assertIs(
                    picker.presentation,
                    InputPresentation.STANDARD,
                )
                self.assertEqual(picker.notice.label, "authentication")
                self.assertEqual(picker.notice.role, SemanticRole.INFO)

    def test_default_picker_is_highlight_only_and_accepts_authenticated_alternate(
        self,
    ) -> None:
        current = snapshot(
            ProviderAuthState.AUTHENTICATED,
            ProviderAuthState.AUTHENTICATED,
        )
        auth = ScriptedAuthService()
        harness = TerminalHarness(
            ControlOutcome(ControlOutcomeKind.COMMIT, text="antigravity")
        )

        result = harness.coordinator(auth).select_default(
            current,
            selected_provider="codex",
        )

        self.assertIs(result.kind, InteractiveAuthResultKind.AUTHENTICATED)
        self.assertEqual(result.provider, "antigravity")
        self.assertIs(result.snapshot, current)
        self.assertEqual(auth.events, [])
        state = harness.picker_states[0]
        picker = state.picker  # type: ignore[attr-defined]
        self.assertEqual(picker.prompt.name, "Default provider")
        self.assertEqual(
            picker.prompt.instruction,
            "Choose the workspace default provider. Story saves this choice "
            "for later shells; selecting an unauthenticated provider starts "
            "native sign-in.",
        )
        self.assertEqual(picker.highlighted_id, "codex")
        self.assertIsNone(picker.selected_id)
        self.assertIs(picker.presentation, InputPresentation.STANDARD)
        self.assertEqual(picker.notice.label, "default provider")
        self.assertEqual(
            picker.notice.text,
            "Agents without their own provider setting inherit this workspace "
            "default.",
        )
        self.assertIs(picker.notice.role, SemanticRole.INFO)
        self.assertTrue(
            all("Current selection." not in choice.description for choice in picker.choices)
        )

    def test_default_picker_native_login_failure_can_retry_to_authentication(
        self,
    ) -> None:
        initial = snapshot(
            ProviderAuthState.AUTHENTICATED,
            ProviderAuthState.UNAUTHENTICATED,
        )
        refreshed_failure = snapshot(
            ProviderAuthState.AUTHENTICATED,
            ProviderAuthState.UNAUTHENTICATED,
        )
        refreshed_success = snapshot(
            ProviderAuthState.AUTHENTICATED,
            ProviderAuthState.AUTHENTICATED,
        )
        auth = ScriptedAuthService()
        auth.login_results.extend(
            (
                ProviderLoginResult(
                    "antigravity",
                    1,
                    status("antigravity", ProviderAuthState.UNAUTHENTICATED),
                ),
                ProviderLoginResult(
                    "antigravity",
                    0,
                    status("antigravity", ProviderAuthState.AUTHENTICATED),
                ),
            )
        )
        auth.probe_all_results.extend((refreshed_failure, refreshed_success))
        harness = TerminalHarness(
            ControlOutcome(ControlOutcomeKind.COMMIT, text="antigravity"),
            ControlOutcome(ControlOutcomeKind.COMMIT, text="antigravity"),
        )
        coordinator = harness.coordinator(auth)

        failed = coordinator.select_default(initial, selected_provider="codex")
        succeeded = coordinator.select_default(
            failed.snapshot,
            selected_provider=failed.provider,
        )

        self.assertIs(failed.kind, InteractiveAuthResultKind.NOT_AUTHENTICATED)
        self.assertEqual(failed.provider, "antigravity")
        self.assertIs(succeeded.kind, InteractiveAuthResultKind.AUTHENTICATED)
        self.assertEqual(succeeded.provider, "antigravity")
        self.assertIs(succeeded.snapshot, refreshed_success)
        self.assertEqual(
            auth.events,
            [
                ("login", "antigravity"),
                ("probe_all", None),
                ("login", "antigravity"),
                ("probe_all", None),
            ],
        )
        pickers = [state.picker for state in harness.picker_states]
        self.assertEqual(
            [(picker.highlighted_id, picker.selected_id) for picker in pickers],
            [("codex", None), ("antigravity", None)],
        )

    def test_explicit_form_presentation_is_forwarded_without_auth_side_effects(
        self,
    ) -> None:
        current = snapshot(
            ProviderAuthState.AUTHENTICATED,
            ProviderAuthState.UNAUTHENTICATED,
        )
        auth = ScriptedAuthService()
        harness = TerminalHarness(
            ControlOutcome(ControlOutcomeKind.CANCEL)
        )
        result = harness.coordinator(auth).run(
            current,
            presentation=InputPresentation.FORM,
            selected_provider="codex",
        )

        self.assertIs(result.kind, InteractiveAuthResultKind.CANCEL)
        self.assertEqual(auth.events, [])
        self.assertIs(
            harness.picker_states[0].picker.presentation,  # type: ignore[attr-defined]
            InputPresentation.FORM,
        )
        for malformed in ("form", ViewState.OPEN, object()):
            with self.subTest(malformed=malformed):
                with self.assertRaisesRegex(TypeError, "InputPresentation"):
                    harness.coordinator(auth).run(
                        current,
                        presentation=malformed,  # type: ignore[arg-type]
                    )

    def test_native_login_handoff_refreshes_both_and_cleans_transients(self) -> None:
        initial = snapshot(
            ProviderAuthState.UNAUTHENTICATED,
            ProviderAuthState.UNAVAILABLE,
        )
        refreshed = snapshot(
            ProviderAuthState.AUTHENTICATED,
            ProviderAuthState.UNAUTHENTICATED,
        )
        auth = ScriptedAuthService()
        auth.login_results.append(
            ProviderLoginResult(
                "codex",
                27,
                status("codex", ProviderAuthState.UNAUTHENTICATED),
            )
        )
        auth.probe_all_results.append(refreshed)
        harness = TerminalHarness(
            ControlOutcome(ControlOutcomeKind.COMMIT, text="codex")
        )
        coordinator = harness.coordinator(auth)
        result = coordinator.run(initial)

        self.assertEqual(result.kind, InteractiveAuthResultKind.AUTHENTICATED)
        self.assertIs(result.snapshot, refreshed)
        self.assertEqual(result.status, refreshed.status_for("codex"))
        self.assertEqual(
            auth.events,
            [("login", "codex"), ("probe_all", None)],
        )
        self.assertEqual([event[0] for event in harness.events], ["picker", "notice", "result"])
        notice = harness.events[1][1]
        self.assertEqual(notice.label, "authentication")  # type: ignore[attr-defined]
        self.assertEqual(notice.role, SemanticRole.INFO)  # type: ignore[attr-defined]
        self.assertEqual(notice.state, ViewState.LOADING)  # type: ignore[attr-defined]
        result_notice = harness.events[2][1]
        self.assertEqual(result_notice.role, SemanticRole.SUCCESS)  # type: ignore[attr-defined]
        self.assertEqual(result_notice.state, ViewState.COMPLETED)  # type: ignore[attr-defined]
        rendered = repr((notice, result_notice))
        for secret in ("27", "account", "token", "https://"):
            self.assertNotIn(secret, rendered)

        stale_success = snapshot(
            ProviderAuthState.UNAUTHENTICATED,
            ProviderAuthState.AUTHENTICATED,
        )
        auth = ScriptedAuthService()
        auth.login_results.append(
            ProviderLoginResult(
                "codex",
                0,
                status("codex", ProviderAuthState.AUTHENTICATED),
            )
        )
        auth.probe_all_results.append(stale_success)
        harness = TerminalHarness(
            ControlOutcome(ControlOutcomeKind.COMMIT, text="codex")
        )
        result = harness.coordinator(auth).run(initial)
        self.assertEqual(result.kind, InteractiveAuthResultKind.NOT_AUTHENTICATED)
        self.assertIsNotNone(result.status)
        self.assertFalse(result.status.authenticated)
        failure_notice = harness.events[-1][1]
        self.assertEqual(failure_notice.role, SemanticRole.ERROR)  # type: ignore[attr-defined]

    def test_indeterminate_unavailable_failures_and_bad_collaborators_are_safe(self) -> None:
        initial = snapshot(
            ProviderAuthState.INDETERMINATE,
            ProviderAuthState.UNAVAILABLE,
        )
        auth = ScriptedAuthService()
        auth.probe_results.append(
            status("codex", ProviderAuthState.AUTHENTICATED)
        )
        harness = TerminalHarness(
            ControlOutcome(ControlOutcomeKind.COMMIT, text="codex")
        )
        result = harness.coordinator(auth).run(initial)
        self.assertEqual(result.kind, InteractiveAuthResultKind.AUTHENTICATED)
        self.assertEqual(auth.events, [("probe", "codex")])
        self.assertEqual([event[0] for event in harness.events], ["picker"])

        unavailable = TerminalHarness(
            ControlOutcome(ControlOutcomeKind.COMMIT, text="antigravity")
        )
        with self.assertRaisesRegex(RuntimeError, "unavailable"):
            unavailable.coordinator(ScriptedAuthService()).run(initial)

        rogue = TerminalHarness(
            ControlOutcome(ControlOutcomeKind.COMMIT, text="gemini")
        )
        with self.assertRaisesRegex(ValueError, "codex or antigravity"):
            rogue.coordinator(ScriptedAuthService()).run(initial)

        malformed_cases = (
            ("probe_all", object(), TypeError),
            (
                "probe",
                status("antigravity", ProviderAuthState.AUTHENTICATED),
                ValueError,
            ),
            ("login", object(), TypeError),
            (
                "login",
                ProviderLoginResult(
                    "antigravity",
                    0,
                    status("antigravity", ProviderAuthState.AUTHENTICATED),
                ),
                ValueError,
            ),
        )
        for boundary, value, error in malformed_cases:
            with self.subTest(boundary=boundary):
                auth = ScriptedAuthService()
                if boundary == "probe_all":
                    auth.probe_all_results.append(value)
                    harness = TerminalHarness()
                    with self.assertRaises(error):
                        harness.coordinator(auth).probe_all()
                elif boundary == "probe":
                    auth.probe_results.append(value)
                    harness = TerminalHarness(
                        ControlOutcome(ControlOutcomeKind.COMMIT, text="codex")
                    )
                    with self.assertRaises(error):
                        harness.coordinator(auth).run(initial)
                else:
                    auth.login_results.append(value)
                    harness = TerminalHarness(
                        ControlOutcome(ControlOutcomeKind.COMMIT, text="codex")
                    )
                    login_initial = snapshot(
                        ProviderAuthState.UNAUTHENTICATED,
                        ProviderAuthState.UNAVAILABLE,
                    )
                    with self.assertRaises(error):
                        harness.coordinator(auth).run(login_initial)

    def test_welcome_and_implicit_probe_are_explicit_and_ordered(self) -> None:
        current = snapshot(
            ProviderAuthState.AUTHENTICATED,
            ProviderAuthState.UNAUTHENTICATED,
        )
        auth = ScriptedAuthService()
        auth.probe_all_results.append(current)
        harness = TerminalHarness(
            ControlOutcome(ControlOutcomeKind.COMMIT, text="codex")
        )
        coordinator = harness.coordinator(auth)
        coordinator.show_welcome()
        result = coordinator.run()
        self.assertEqual(result.kind, InteractiveAuthResultKind.AUTHENTICATED)
        self.assertEqual(auth.events, [("probe_all", None)])
        self.assertEqual([event[0] for event in harness.events], ["welcome", "picker"])


class InteractiveAuthBoundaryTests(unittest.TestCase):
    def test_contract_is_private_clean_source_isolated_and_acyclic(self) -> None:
        for private_name in (
            "InteractiveAuthCoordinator",
            "InteractiveAuthResult",
            "InteractiveAuthResultKind",
            "select_default_provider",
        ):
            self.assertNotIn(private_name, story_writing_agents.__all__)
            self.assertFalse(hasattr(story_writing_agents, private_name))

        self.assertEqual(
            tuple(inspect.signature(InteractiveAuthCoordinator).parameters),
            ("auth_service", "terminal", "sleeper"),
        )
        self.assertEqual(
            tuple(inspect.signature(InteractiveAuthCoordinator.run).parameters),
            (
                "self",
                "snapshot",
                "presentation",
                "selected_provider",
            ),
        )
        self.assertEqual(
            tuple(
                inspect.signature(
                    InteractiveAuthCoordinator.select_default
                ).parameters
            ),
            ("self", "snapshot", "selected_provider"),
        )
        repository_root = Path(__file__).resolve().parents[1]
        source_root = repository_root / "src"
        script = "\n".join(
            (
                "import subprocess, sys, time",
                f"sys.path.insert(0, {str(source_root)!r})",
                "class ForbiddenPopen:",
                "    @classmethod",
                "    def __class_getitem__(cls, item):",
                "        return cls",
                "    def __new__(cls, *args, **kwargs):",
                "        raise AssertionError('import launched a provider')",
                "subprocess.Popen = ForbiddenPopen",
                "time.sleep = lambda value: (_ for _ in ()).throw(AssertionError('import slept'))",
                "import story_writing_agents.interactive_auth as module",
                "assert module.AUTH_PROVIDERS == ('codex', 'antigravity')",
                "assert not hasattr(__import__('story_writing_agents'), 'InteractiveAuthCoordinator')",
            )
        )
        completed = subprocess.run(
            [sys.executable, "-I", "-c", script],
            cwd=tempfile.gettempdir(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=10,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(completed.stderr, "")

        source = inspect.getsource(interactive_auth)
        self.assertIn("from .provider_auth import", source)
        self.assertIn("from .interactive_terminal import", source)
        for forbidden in (
            "subprocess",
            "webbrowser",
            "requests",
            "keyring",
            "open(",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
