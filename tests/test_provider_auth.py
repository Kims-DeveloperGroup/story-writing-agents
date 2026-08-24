"""Deterministic native-provider authentication lifecycle tests."""

from __future__ import annotations

import inspect
import subprocess
import sys
import tempfile
import unittest
from dataclasses import FrozenInstanceError, fields
from pathlib import Path

import story_writing_agents
from story_writing_agents import provider_auth
from story_writing_agents.provider_auth import (
    AUTH_LOGIN_STATUS_POLL_SECONDS,
    AUTH_PROCESS_STOP_TIMEOUT_SECONDS,
    AUTH_PROVIDERS,
    AUTH_STATUS_TIMEOUT_SECONDS,
    ProviderAuthService,
    ProviderAuthSnapshot,
    ProviderAuthState,
    ProviderAuthStatus,
    ProviderLoginResult,
)


_MISSING = object()


class ScriptedProcess:
    """Record exact process lifecycle calls from one deterministic child."""

    def __init__(
        self,
        *wait_results: object,
        poll_results: tuple[object, ...] = (None,),
    ) -> None:
        self.wait_results = list(wait_results or (0,))
        self.poll_results = list(poll_results)
        self.wait_calls: list[object] = []
        self.poll_calls = 0
        self.terminate_calls = 0
        self.kill_calls = 0

    def wait(self, timeout: object = _MISSING) -> int:
        self.wait_calls.append(timeout)
        if not self.wait_results:
            raise AssertionError("unexpected process wait")
        result = self.wait_results.pop(0)
        if isinstance(result, BaseException):
            raise result
        if type(result) is not int:
            raise AssertionError("scripted process return code must be an int")
        return result

    def poll(self) -> int | None:
        self.poll_calls += 1
        if not self.poll_results:
            return None
        result = self.poll_results.pop(0)
        if isinstance(result, BaseException):
            raise result
        if result is not None and type(result) is not int:
            raise AssertionError("scripted poll must return int or None")
        return result

    def terminate(self) -> None:
        self.terminate_calls += 1

    def kill(self) -> None:
        self.kill_calls += 1


class ScriptedProcessFactory:
    """Return queued children or launch failures while retaining arguments."""

    def __init__(self, *results: object) -> None:
        self.results = list(results)
        self.calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    def __call__(self, command: tuple[str, ...], **kwargs: object) -> ScriptedProcess:
        self.calls.append((command, kwargs))
        if not self.results:
            raise AssertionError("unexpected native process launch")
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        if not isinstance(result, ScriptedProcess):
            raise AssertionError("scripted process factory needs a child")
        return result


def status(provider: str, state: ProviderAuthState) -> ProviderAuthStatus:
    """Build one content-free status value."""

    return ProviderAuthStatus(provider, state)


def snapshot(
    codex: ProviderAuthState,
    antigravity: ProviderAuthState,
) -> ProviderAuthSnapshot:
    """Build one exact ordered two-provider snapshot."""

    return ProviderAuthSnapshot(
        (
            status("codex", codex),
            status("antigravity", antigravity),
        )
    )


class ProviderAuthValueContractTests(unittest.TestCase):
    def test_provider_ids_states_queries_and_private_surface_are_exact(self) -> None:
        self.assertEqual(AUTH_PROVIDERS, ("codex", "antigravity"))
        self.assertEqual(
            tuple((member.name, member.value) for member in ProviderAuthState),
            (
                ("AUTHENTICATED", "authenticated"),
                ("UNAUTHENTICATED", "unauthenticated"),
                ("UNAVAILABLE", "unavailable"),
                ("INDETERMINATE", "indeterminate"),
            ),
        )
        self.assertEqual(
            tuple(field.name for field in fields(ProviderAuthStatus)),
            ("provider", "state"),
        )
        self.assertEqual(
            tuple(field.name for field in fields(ProviderAuthSnapshot)),
            ("statuses",),
        )
        self.assertEqual(
            tuple(field.name for field in fields(ProviderLoginResult)),
            ("provider", "returncode", "status"),
        )

        all_authenticated = snapshot(
            ProviderAuthState.AUTHENTICATED,
            ProviderAuthState.AUTHENTICATED,
        )
        self.assertEqual(all_authenticated.authenticated_providers, AUTH_PROVIDERS)
        self.assertTrue(all_authenticated.any_authenticated)
        self.assertTrue(all_authenticated.is_authenticated("codex"))
        self.assertEqual(
            all_authenticated.status_for("antigravity"),
            status("antigravity", ProviderAuthState.AUTHENTICATED),
        )
        none_authenticated = snapshot(
            ProviderAuthState.UNAUTHENTICATED,
            ProviderAuthState.INDETERMINATE,
        )
        self.assertEqual(none_authenticated.authenticated_providers, ())
        self.assertFalse(none_authenticated.any_authenticated)

        with self.assertRaises(FrozenInstanceError):
            all_authenticated.statuses = ()  # type: ignore[misc]
        for malformed in (
            (),
            (status("codex", ProviderAuthState.AUTHENTICATED),),
            (
                status("antigravity", ProviderAuthState.AUTHENTICATED),
                status("codex", ProviderAuthState.AUTHENTICATED),
            ),
            (
                status("codex", ProviderAuthState.AUTHENTICATED),
                status("codex", ProviderAuthState.AUTHENTICATED),
            ),
        ):
            with self.subTest(malformed=malformed), self.assertRaises(ValueError):
                ProviderAuthSnapshot(malformed)
        with self.assertRaises(ValueError):
            all_authenticated.status_for("gemini")

        successful = ProviderLoginResult(
            "codex",
            17,
            status("codex", ProviderAuthState.AUTHENTICATED),
        )
        self.assertTrue(successful.authenticated)
        failed = ProviderLoginResult(
            "codex",
            0,
            status("codex", ProviderAuthState.UNAUTHENTICATED),
        )
        self.assertFalse(failed.authenticated)
        with self.assertRaises(ValueError):
            ProviderLoginResult(
                "codex",
                0,
                status("antigravity", ProviderAuthState.AUTHENTICATED),
            )
        with self.assertRaises(TypeError):
            ProviderLoginResult(
                "codex",
                True,
                status("codex", ProviderAuthState.AUTHENTICATED),
            )

        for private_name in (
            "ProviderAuthService",
            "ProviderAuthState",
            "ProviderAuthStatus",
            "ProviderAuthSnapshot",
            "ProviderLoginResult",
            "AUTH_PROVIDERS",
        ):
            self.assertNotIn(private_name, story_writing_agents.__all__)
            self.assertFalse(hasattr(story_writing_agents, private_name))

    def test_service_signature_defaults_to_exact_native_executables(self) -> None:
        signature = inspect.signature(ProviderAuthService)
        self.assertEqual(
            tuple(signature.parameters),
            (
                "workspace_root",
                "codex_executable",
                "antigravity_executable",
                "_process_factory",
            ),
        )
        self.assertEqual(signature.parameters["codex_executable"].default, "codex")
        self.assertEqual(
            signature.parameters["antigravity_executable"].default,
            "agy",
        )
        self.assertIs(
            signature.parameters["_process_factory"].default,
            subprocess.Popen,
        )


class ProviderAuthProcessContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.workspace_root = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def service(self, factory: ScriptedProcessFactory) -> ProviderAuthService:
        return ProviderAuthService(
            self.workspace_root,
            _process_factory=factory,
        )

    def test_exact_native_vectors_cwd_environment_and_stdio(self) -> None:
        codex_probe = ScriptedProcess(0)
        antigravity_probe = ScriptedProcess(1)
        codex_login = ScriptedProcess(9)
        codex_fresh_probe = ScriptedProcess(0)
        antigravity_login = ScriptedProcess(0)
        antigravity_fresh_probe = ScriptedProcess(1)
        factory = ScriptedProcessFactory(
            codex_probe,
            antigravity_probe,
            codex_login,
            codex_fresh_probe,
            antigravity_login,
            antigravity_fresh_probe,
        )
        service = self.service(factory)

        self.assertEqual(
            service.probe("codex"),
            status("codex", ProviderAuthState.AUTHENTICATED),
        )
        self.assertEqual(
            service.probe("antigravity"),
            status("antigravity", ProviderAuthState.UNAUTHENTICATED),
        )
        codex_result = service.login("codex")
        antigravity_result = service.login("antigravity")
        self.assertEqual(codex_result.returncode, 9)
        self.assertTrue(codex_result.authenticated)
        self.assertEqual(antigravity_result.returncode, 0)
        self.assertFalse(antigravity_result.authenticated)

        expected_commands = (
            ("codex", "login", "status"),
            ("agy", "models"),
            ("codex", "login"),
            ("codex", "login", "status"),
            ("agy",),
            ("agy", "models"),
        )
        self.assertEqual(tuple(call[0] for call in factory.calls), expected_commands)
        for index, (_, kwargs) in enumerate(factory.calls):
            with self.subTest(index=index):
                self.assertEqual(kwargs["cwd"], str(self.workspace_root))
                self.assertIs(kwargs["shell"], False)
                self.assertNotIn("env", kwargs)
                if index in {0, 1, 3, 5}:
                    self.assertEqual(
                        kwargs,
                        {
                            "cwd": str(self.workspace_root),
                            "stdin": subprocess.DEVNULL,
                            "stdout": subprocess.DEVNULL,
                            "stderr": subprocess.DEVNULL,
                            "shell": False,
                        },
                    )
                else:
                    self.assertEqual(
                        kwargs,
                        {"cwd": str(self.workspace_root), "shell": False},
                    )
        self.assertEqual(codex_probe.wait_calls, [AUTH_STATUS_TIMEOUT_SECONDS])
        self.assertEqual(antigravity_probe.wait_calls, [AUTH_STATUS_TIMEOUT_SECONDS])
        self.assertEqual(
            codex_login.wait_calls,
            [AUTH_LOGIN_STATUS_POLL_SECONDS],
        )
        self.assertEqual(
            antigravity_login.wait_calls,
            [AUTH_LOGIN_STATUS_POLL_SECONDS],
        )

    def test_probe_state_timeout_and_cleanup_matrix_is_bounded(self) -> None:
        scenarios = (
            (0, ProviderAuthState.AUTHENTICATED),
            (1, ProviderAuthState.UNAUTHENTICATED),
            (2, ProviderAuthState.INDETERMINATE),
            (-9, ProviderAuthState.INDETERMINATE),
        )
        for returncode, expected_state in scenarios:
            with self.subTest(returncode=returncode):
                process = ScriptedProcess(returncode)
                factory = ScriptedProcessFactory(process)
                self.assertEqual(
                    self.service(factory).probe("codex").state,
                    expected_state,
                )
                self.assertEqual(process.wait_calls, [AUTH_STATUS_TIMEOUT_SECONDS])

        for failure, expected_state in (
            (FileNotFoundError("secret executable path"), ProviderAuthState.UNAVAILABLE),
            (OSError("secret launch detail"), ProviderAuthState.INDETERMINATE),
        ):
            with self.subTest(failure=type(failure).__name__):
                result = self.service(ScriptedProcessFactory(failure)).probe("codex")
                self.assertEqual(result, status("codex", expected_state))
                self.assertNotIn("secret", repr(result))

        timeout = subprocess.TimeoutExpired(("codex",), 5.0)
        stopped = ScriptedProcess(timeout, 0, poll_results=(None,))
        result = self.service(ScriptedProcessFactory(stopped)).probe("codex")
        self.assertEqual(result.state, ProviderAuthState.INDETERMINATE)
        self.assertEqual(
            stopped.wait_calls,
            [AUTH_STATUS_TIMEOUT_SECONDS, AUTH_PROCESS_STOP_TIMEOUT_SECONDS],
        )
        self.assertEqual(stopped.terminate_calls, 1)
        self.assertEqual(stopped.kill_calls, 0)

        escalation = ScriptedProcess(
            subprocess.TimeoutExpired(("agy",), 5.0),
            subprocess.TimeoutExpired(("agy",), 2.0),
            0,
            poll_results=(None, None),
        )
        result = self.service(ScriptedProcessFactory(escalation)).probe("antigravity")
        self.assertEqual(result.state, ProviderAuthState.INDETERMINATE)
        self.assertEqual(escalation.terminate_calls, 1)
        self.assertEqual(escalation.kill_calls, 1)
        self.assertEqual(
            escalation.wait_calls,
            [
                AUTH_STATUS_TIMEOUT_SECONDS,
                AUTH_PROCESS_STOP_TIMEOUT_SECONDS,
                AUTH_PROCESS_STOP_TIMEOUT_SECONDS,
            ],
        )

        wait_failure = ScriptedProcess(
            OSError("provider output secret"),
            0,
            poll_results=(None,),
        )
        result = self.service(ScriptedProcessFactory(wait_failure)).probe("codex")
        self.assertEqual(result.state, ProviderAuthState.INDETERMINATE)
        self.assertNotIn("secret", repr(result))
        self.assertEqual(wait_failure.terminate_calls, 1)

    def test_login_fresh_probe_is_the_only_authority(self) -> None:
        scenarios = (
            (0, 1, False),
            (17, 0, True),
            (0, 2, False),
        )
        for login_code, probe_code, authenticated in scenarios:
            with self.subTest(
                login_code=login_code,
                probe_code=probe_code,
            ):
                login = ScriptedProcess(login_code)
                probe = ScriptedProcess(probe_code)
                result = self.service(
                    ScriptedProcessFactory(login, probe)
                ).login("codex")
                self.assertEqual(result.returncode, login_code)
                self.assertIs(result.authenticated, authenticated)
                self.assertEqual(
                    login.wait_calls,
                    [AUTH_LOGIN_STATUS_POLL_SECONDS],
                )
                self.assertEqual(probe.wait_calls, [AUTH_STATUS_TIMEOUT_SECONDS])

        for failure, state in (
            (FileNotFoundError("account@example.test"), ProviderAuthState.UNAVAILABLE),
            (OSError("token=super-secret"), ProviderAuthState.INDETERMINATE),
        ):
            with self.subTest(failure=type(failure).__name__):
                result = self.service(ScriptedProcessFactory(failure)).login("codex")
                self.assertIsNone(result.returncode)
                self.assertEqual(result.status.state, state)
                self.assertNotIn("secret", repr(result))
                self.assertNotIn("example.test", repr(result))

    def test_process_controls_cleanup_and_privacy_are_exact(self) -> None:
        for control in (
            KeyboardInterrupt("token=secret"),
            SystemExit("https://oauth.invalid/private"),
        ):
            with self.subTest(control=type(control).__name__):
                process = ScriptedProcess(control, 0, poll_results=(None,))
                factory = ScriptedProcessFactory(process)
                with self.assertRaises(type(control)) as raised:
                    self.service(factory).login("antigravity")
                self.assertIs(raised.exception, control)
                self.assertEqual(process.terminate_calls, 1)
                self.assertEqual(process.kill_calls, 0)
                self.assertEqual(
                    process.wait_calls,
                    [
                        AUTH_LOGIN_STATUS_POLL_SECONDS,
                        AUTH_PROCESS_STOP_TIMEOUT_SECONDS,
                    ],
                )

        ordinary = ScriptedProcess(
            RuntimeError("provider account and token detail"),
            0,
            poll_results=(None,),
        )
        result = self.service(ScriptedProcessFactory(ordinary)).login("codex")
        self.assertIsNone(result.returncode)
        self.assertEqual(result.status.state, ProviderAuthState.INDETERMINATE)
        self.assertNotIn("provider account", repr(result))
        self.assertEqual(ordinary.terminate_calls, 1)

    def test_live_login_returns_after_fresh_authentication_for_both_providers(
        self,
    ) -> None:
        """Reclaim the terminal without waiting for a native CLI to exit itself."""

        for provider in AUTH_PROVIDERS:
            with self.subTest(provider=provider):
                timeout = subprocess.TimeoutExpired((provider,), 1.0)
                login = ScriptedProcess(
                    timeout,
                    143,
                    poll_results=(None,),
                )
                probe = ScriptedProcess(0)
                factory = ScriptedProcessFactory(login, probe)

                result = self.service(factory).login(provider)

                self.assertTrue(result.authenticated)
                self.assertEqual(result.returncode, 143)
                self.assertEqual(login.terminate_calls, 1)
                self.assertEqual(login.kill_calls, 0)
                self.assertEqual(
                    login.wait_calls,
                    [
                        AUTH_LOGIN_STATUS_POLL_SECONDS,
                        AUTH_PROCESS_STOP_TIMEOUT_SECONDS,
                    ],
                )
                self.assertEqual(
                    probe.wait_calls,
                    [AUTH_STATUS_TIMEOUT_SECONDS],
                )
                self.assertEqual(
                    factory.calls[0][1],
                    {"cwd": str(self.workspace_root), "shell": False},
                )
                self.assertEqual(
                    factory.calls[1][1],
                    {
                        "cwd": str(self.workspace_root),
                        "stdin": subprocess.DEVNULL,
                        "stdout": subprocess.DEVNULL,
                        "stderr": subprocess.DEVNULL,
                        "shell": False,
                    },
                )

    def test_live_login_continues_through_every_nonauthenticated_probe_state(
        self,
    ) -> None:
        """Keep native control until authentication or native exit is authoritative."""

        timeout = subprocess.TimeoutExpired(("agy",), 1.0)
        login = ScriptedProcess(timeout, timeout, timeout, 7)
        unauthenticated = ScriptedProcess(1)
        indeterminate = ScriptedProcess(2)
        final_authenticated = ScriptedProcess(0)
        factory = ScriptedProcessFactory(
            login,
            unauthenticated,
            indeterminate,
            FileNotFoundError("private executable path"),
            final_authenticated,
        )

        result = self.service(factory).login("antigravity")

        self.assertTrue(result.authenticated)
        self.assertEqual(result.returncode, 7)
        self.assertEqual(
            login.wait_calls,
            [AUTH_LOGIN_STATUS_POLL_SECONDS] * 4,
        )
        self.assertEqual(login.terminate_calls, 0)
        self.assertEqual(login.kill_calls, 0)
        self.assertEqual(unauthenticated.wait_calls, [AUTH_STATUS_TIMEOUT_SECONDS])
        self.assertEqual(indeterminate.wait_calls, [AUTH_STATUS_TIMEOUT_SECONDS])
        self.assertEqual(final_authenticated.wait_calls, [AUTH_STATUS_TIMEOUT_SECONDS])
        self.assertNotIn("private", repr(result))

    def test_authenticated_monitor_probe_escalates_and_reaps_stubborn_login(
        self,
    ) -> None:
        """Kill only after bounded graceful cleanup cannot stop the live child."""

        login_timeout = subprocess.TimeoutExpired(("codex", "login"), 1.0)
        stop_timeout = subprocess.TimeoutExpired(("codex", "login"), 2.0)
        login = ScriptedProcess(
            login_timeout,
            stop_timeout,
            -9,
            poll_results=(None, None),
        )
        probe = ScriptedProcess(0)

        result = self.service(ScriptedProcessFactory(login, probe)).login("codex")

        self.assertTrue(result.authenticated)
        self.assertEqual(result.returncode, -9)
        self.assertEqual(login.terminate_calls, 1)
        self.assertEqual(login.kill_calls, 1)
        self.assertEqual(
            login.wait_calls,
            [
                AUTH_LOGIN_STATUS_POLL_SECONDS,
                AUTH_PROCESS_STOP_TIMEOUT_SECONDS,
                AUTH_PROCESS_STOP_TIMEOUT_SECONDS,
            ],
        )

    def test_monitor_probe_timeout_is_silent_sanitized_and_does_not_stop_login(
        self,
    ) -> None:
        """Isolate status failures from the inherited native-login terminal."""

        login_timeout = subprocess.TimeoutExpired(("codex", "login"), 1.0)
        status_timeout = subprocess.TimeoutExpired(
            ("codex", "login", "status", "token=private"),
            5.0,
        )
        login = ScriptedProcess(login_timeout, 0)
        timed_out_probe = ScriptedProcess(
            status_timeout,
            0,
            poll_results=(None,),
        )
        final_probe = ScriptedProcess(1)
        factory = ScriptedProcessFactory(login, timed_out_probe, final_probe)

        result = self.service(factory).login("codex")

        self.assertFalse(result.authenticated)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(login.terminate_calls, 0)
        self.assertEqual(
            login.wait_calls,
            [AUTH_LOGIN_STATUS_POLL_SECONDS, AUTH_LOGIN_STATUS_POLL_SECONDS],
        )
        self.assertEqual(timed_out_probe.terminate_calls, 1)
        self.assertNotIn("private", repr(result))
        for _, kwargs in factory.calls[1:]:
            self.assertIs(kwargs["stdin"], subprocess.DEVNULL)
            self.assertIs(kwargs["stdout"], subprocess.DEVNULL)
            self.assertIs(kwargs["stderr"], subprocess.DEVNULL)

    def test_constructor_and_provider_validation_are_pre_process(self) -> None:
        factory = ScriptedProcessFactory()
        for root in ("not-a-path", self.workspace_root / "missing"):
            with self.subTest(root=root), self.assertRaises((TypeError, ValueError)):
                ProviderAuthService(root, _process_factory=factory)  # type: ignore[arg-type]
        for executable in ("", "bad\0name", None, 17):
            with self.subTest(executable=executable), self.assertRaises(ValueError):
                ProviderAuthService(
                    self.workspace_root,
                    codex_executable=executable,  # type: ignore[arg-type]
                    _process_factory=factory,
                )
        service = self.service(factory)
        for provider in ("gemini", "Codex", "", None):
            with self.subTest(provider=provider), self.assertRaises(ValueError):
                service.probe(provider)  # type: ignore[arg-type]
        self.assertEqual(factory.calls, [])


class ProviderAuthBoundaryTests(unittest.TestCase):
    def test_direct_import_is_clean_private_and_source_isolated(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        source_root = repository_root / "src"
        script = "\n".join(
            (
                "import subprocess, sys",
                f"sys.path.insert(0, {str(source_root)!r})",
                "class ForbiddenPopen:",
                "    @classmethod",
                "    def __class_getitem__(cls, item):",
                "        return cls",
                "    def __new__(cls, *args, **kwargs):",
                "        raise AssertionError('import launched a provider')",
                "subprocess.Popen = ForbiddenPopen",
                "import story_writing_agents.provider_auth as module",
                "assert module.AUTH_PROVIDERS == ('codex', 'antigravity')",
                "assert not hasattr(__import__('story_writing_agents'), 'ProviderAuthService')",
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

        source = inspect.getsource(provider_auth)
        for forbidden in (
            "capture_output=True",
            "stdout=subprocess.PIPE",
            "stderr=subprocess.PIPE",
            "shell=True",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)
        for forbidden_module in ("webbrowser", "requests", "urllib", "keyring"):
            with self.subTest(forbidden_module=forbidden_module):
                self.assertNotIn(f"import {forbidden_module}", source)


if __name__ == "__main__":
    unittest.main()
