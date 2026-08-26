"""Capability-only SQLite prerequisite provisioning contracts."""

from __future__ import annotations

import io
import unittest
from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from unittest import mock

import story_writing_agents.conversation_storage.provisioning as provisioning_module
import story_writing_agents
import story_writing_agents.conversation_storage as storage_package
from story_writing_agents.conversation_storage import (
    ConversationStorageBackend,
    ConversationStorageCapability,
    ConversationStoragePreflight,
    WorkspaceConversationStorageMutationCoordinator,
)
from story_writing_agents.conversation_storage.provisioning import (
    ConversationStorageInstallationPlan,
    ConversationStorageProvisioner,
    ConversationStorageProvisioningFailure,
    ConversationStorageProvisioningResult,
)

from tests.helpers import initialized_workspace


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


def _preflight(
    capability: ConversationStorageCapability,
) -> ConversationStoragePreflight:
    return ConversationStoragePreflight(
        ConversationStorageBackend.SQLITE,
        capability,
    )


class ConversationStorageProvisioningTests(unittest.TestCase):
    """Exercise every host-sensitive branch with injected, inert seams."""

    def setUp(self) -> None:
        self.workspace = initialized_workspace(self)

    @staticmethod
    def _tripwire(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("a real installer process must never execute")

    def provisioner(self, **overrides: object) -> ConversationStorageProvisioner:
        seams: dict[str, object] = {
            "platform_system": lambda: "Linux",
            "os_release_reader": lambda: {"ID": "ubuntu"},
            "environment": {},
            "executable_lookup": lambda _name: None,
            "process_runner": self._tripwire,
        }
        seams.update(overrides)
        return ConversationStorageProvisioner(**seams)  # type: ignore[arg-type]

    def test_public_surface_values_and_allowlist_are_exact(self) -> None:
        self.assertEqual(
            tuple(provisioning_module.__all__),
            (
                "ConversationStorageInstallationPlan",
                "ConversationStorageProvisioningFailure",
                "ConversationStorageProvisioningResult",
                "ConversationStorageProvisioner",
            ),
        )
        for name in provisioning_module.__all__:
            self.assertNotIn(name, storage_package.__all__)
            self.assertNotIn(name, story_writing_agents.__all__)
            self.assertFalse(hasattr(storage_package, name))
            self.assertFalse(hasattr(story_writing_agents, name))
        self.assertEqual(
            tuple(item.value for item in ConversationStorageProvisioningFailure),
            (
                "installation_required",
                "installation_unsupported",
                "installation_disallowed",
                "installation_failed",
                "interpreter_unavailable",
                "provisioning_failed",
            ),
        )
        result = ConversationStorageProvisioningResult(
            installation="not_required",
            provisioning="not_required",
            preflight=_preflight(ConversationStorageCapability.READY),
        )
        self.assertTrue(result.completed)
        self.assertFalse(hasattr(result, "__dict__"))
        with self.assertRaises(FrozenInstanceError):
            result.installation = "failed"  # type: ignore[misc]
        for installation, provisioning in (
            ("unknown", "not_run"),
            ("not_run", "unknown"),
        ):
            with (
                self.subTest(
                    installation=installation,
                    provisioning=provisioning,
                ),
                self.assertRaises(ValueError),
            ):
                ConversationStorageProvisioningResult(
                    installation=installation,
                    provisioning=provisioning,
                )
        plans = (
            (("brew", "install", "sqlite"), "brew install sqlite"),
            (
                ("apt-get", "install", "sqlite3", "libsqlite3-dev"),
                "apt-get install sqlite3 libsqlite3-dev",
            ),
            (
                ("dnf", "install", "sqlite", "sqlite-devel"),
                "dnf install sqlite sqlite-devel",
            ),
        )
        for argv, command in plans:
            with self.subTest(argv=argv):
                plan = ConversationStorageInstallationPlan(argv)
                self.assertEqual(plan.command, command)
                self.assertFalse(hasattr(plan, "__dict__"))
                with self.assertRaises(FrozenInstanceError):
                    plan.argv = ()  # type: ignore[misc]
        for argv in (
            ("sudo", "apt-get", "install", "sqlite3"),
            ("apt-get", "install", "-y", "sqlite3"),
            ("sh", "-c", "brew install sqlite"),
            ("brew", "upgrade", "sqlite"),
            ["brew", "install", "sqlite"],
        ):
            with self.subTest(argv=argv), self.assertRaises(ValueError):
                ConversationStorageInstallationPlan(argv)  # type: ignore[arg-type]

    def test_platform_and_exact_os_id_matrix_never_uses_id_like(self) -> None:
        cases = (
            ("Darwin", {}, "/opt/homebrew/bin/brew", ("brew", "install", "sqlite")),
            ("Darwin", {}, None, None),
            (
                "Linux",
                {"ID": "debian"},
                None,
                ("apt-get", "install", "sqlite3", "libsqlite3-dev"),
            ),
            (
                "Linux",
                {"ID": "ubuntu"},
                None,
                ("apt-get", "install", "sqlite3", "libsqlite3-dev"),
            ),
            (
                "Linux",
                {"ID": "fedora"},
                None,
                ("dnf", "install", "sqlite", "sqlite-devel"),
            ),
            (
                "Linux",
                {"ID": "rhel"},
                None,
                ("dnf", "install", "sqlite", "sqlite-devel"),
            ),
            ("Linux", {"ID": "arch", "ID_LIKE": "debian"}, None, None),
            ("Linux", {"ID_LIKE": "rhel fedora"}, None, None),
            ("Windows", {"ID": "ubuntu"}, None, None),
        )
        for system, release, brew, expected in cases:
            with self.subTest(system=system, release=release, brew=brew):
                lookup = mock.Mock(return_value=brew)
                provisioner = self.provisioner(
                    platform_system=lambda: system,
                    os_release_reader=lambda: release,
                    executable_lookup=lookup,
                )
                plan = provisioner.plan_installation()
                self.assertEqual(None if plan is None else plan.argv, expected)
                if system == "Darwin":
                    lookup.assert_called_once_with("brew")
                else:
                    lookup.assert_not_called()

    def test_platform_collaborator_failures_are_unsupported_and_inert(self) -> None:
        failures = (
            {"platform_system": mock.Mock(side_effect=OSError("private"))},
            {
                "platform_system": lambda: "Darwin",
                "executable_lookup": mock.Mock(side_effect=OSError("private")),
            },
            {
                "os_release_reader": mock.Mock(side_effect=OSError("private")),
            },
            {"os_release_reader": lambda: object()},
        )
        for seams in failures:
            with self.subTest(seams=tuple(seams)):
                self.assertIsNone(self.provisioner(**seams).plan_installation())

    def test_exact_ci_marker_set_refuses_only_nonempty_listed_values(self) -> None:
        self.assertEqual(provisioning_module._CI_MARKERS, _CI_MARKERS)
        for marker in _CI_MARKERS:
            with self.subTest(marker=marker):
                self.assertTrue(
                    self.provisioner(environment={marker: "1"}).installation_disallowed
                )
                self.assertFalse(
                    self.provisioner(environment={marker: ""}).installation_disallowed
                )
        self.assertFalse(
            self.provisioner(
                environment={"UNRELATED_CI": "1", "CI": ""}
            ).installation_disallowed
        )
        self.assertFalse(
            self.provisioner(environment={"CI": True}).installation_disallowed
        )

    def test_executor_receives_only_exact_argv_and_inherited_process_contract(
        self,
    ) -> None:
        calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        diagnostic = io.StringIO()

        def recording_runner(*args: object, **kwargs: object) -> object:
            calls.append((args, kwargs))
            self.assertIs(kwargs["stdout"], diagnostic)
            self.assertIs(kwargs["stderr"], diagnostic)
            kwargs["stdout"].write("installer stdout\n")  # type: ignore[union-attr]
            kwargs["stderr"].write("installer stderr\n")  # type: ignore[union-attr]
            return SimpleNamespace(returncode=0)

        plan = ConversationStorageInstallationPlan(
            ("apt-get", "install", "sqlite3", "libsqlite3-dev")
        )
        result = self.provisioner(process_runner=recording_runner).install(
            self.workspace,
            plan,
            diagnostic_stream=diagnostic,
        )
        self.assertTrue(result.completed)
        self.assertEqual(
            (result.installation, result.provisioning), ("completed", "not_run")
        )
        self.assertEqual(
            calls,
            [
                (
                    (plan.argv,),
                    {
                        "stdout": diagnostic,
                        "stderr": diagnostic,
                        "shell": False,
                        "cwd": str(self.workspace.root),
                        "check": False,
                    },
                )
            ],
        )
        self.assertEqual(diagnostic.getvalue(), "installer stdout\ninstaller stderr\n")

    def test_executor_has_no_retry_and_maps_all_ordinary_failures(self) -> None:
        plan = ConversationStorageInstallationPlan(("brew", "install", "sqlite"))
        runners = (
            lambda *_args, **_kwargs: SimpleNamespace(returncode=1),
            lambda *_args, **_kwargs: SimpleNamespace(returncode=True),
            lambda *_args, **_kwargs: object(),
            mock.Mock(side_effect=OSError("private")),
        )
        for runner in runners:
            with self.subTest(runner=runner):
                observed = mock.Mock(side_effect=runner)
                result = self.provisioner(process_runner=observed).install(
                    self.workspace,
                    plan,
                    diagnostic_stream=io.StringIO(),
                )
                self.assertEqual(observed.call_count, 1)
                self.assertIs(
                    result.failure,
                    ConversationStorageProvisioningFailure.INSTALLATION_FAILED,
                )
                self.assertEqual(
                    (result.installation, result.provisioning), ("failed", "not_run")
                )

    def test_executor_propagates_interrupt_and_system_exit(self) -> None:
        plan = ConversationStorageInstallationPlan(("brew", "install", "sqlite"))
        for control in (KeyboardInterrupt("stop"), SystemExit(7)):
            with self.subTest(control=type(control).__name__):
                with self.assertRaises(type(control)) as caught:
                    self.provisioner(
                        process_runner=mock.Mock(side_effect=control)
                    ).install(
                        self.workspace,
                        plan,
                        diagnostic_stream=io.StringIO(),
                    )
                self.assertIs(caught.exception, control)

    def test_ci_refusal_never_reaches_process_runner(self) -> None:
        plan = ConversationStorageInstallationPlan(("brew", "install", "sqlite"))
        result = self.provisioner(environment={"CI": "1"}).install(
            self.workspace,
            plan,
            diagnostic_stream=io.StringIO(),
        )
        self.assertIs(
            result.failure,
            ConversationStorageProvisioningFailure.INSTALLATION_DISALLOWED,
        )
        self.assertEqual(
            (result.installation, result.provisioning), ("not_run", "not_run")
        )

    def test_probe_delegates_only_to_current_interpreter_runtime_probe(self) -> None:
        ready = _preflight(ConversationStorageCapability.READY)
        with mock.patch.object(
            provisioning_module,
            "probe_sqlite_conversation_storage_runtime",
            return_value=ready,
        ) as probe:
            self.assertIs(self.provisioner().probe_runtime(), ready)
        probe.assert_called_once_with()

    def test_ensure_ready_provisions_only_absent_database_under_supplied_lease(
        self,
    ) -> None:
        coordinator = WorkspaceConversationStorageMutationCoordinator(self.workspace)
        needed = _preflight(ConversationStorageCapability.PROVISIONING_NEEDED)
        ready = _preflight(ConversationStorageCapability.READY)
        with coordinator.borrow(timeout_seconds=2.0) as lease:
            with (
                mock.patch.object(
                    provisioning_module,
                    "preflight_sqlite_conversation_storage",
                    return_value=needed,
                ) as preflight,
                mock.patch.object(
                    provisioning_module,
                    "provision_sqlite_conversation_storage",
                    return_value=ready,
                ) as provision,
            ):
                result = self.provisioner().ensure_ready(
                    self.workspace,
                    lease=lease,
                )
        self.assertTrue(result.completed)
        self.assertEqual(result.provisioning, "completed")
        self.assertIs(result.preflight, ready)
        preflight.assert_called_once_with(self.workspace)
        provision.assert_called_once_with(self.workspace, lease=lease)

    def test_busy_or_corrupt_capability_never_provisions_or_installs(self) -> None:
        coordinator = WorkspaceConversationStorageMutationCoordinator(self.workspace)
        unavailable = _preflight(ConversationStorageCapability.UNAVAILABLE)
        for label in ("busy", "corrupt"):
            with self.subTest(label=label):
                with coordinator.borrow(timeout_seconds=2.0) as lease:
                    with (
                        mock.patch.object(
                            provisioning_module,
                            "preflight_sqlite_conversation_storage",
                            return_value=unavailable,
                        ),
                        mock.patch.object(
                            provisioning_module,
                            "provision_sqlite_conversation_storage",
                            side_effect=AssertionError("must not provision"),
                        ) as provision,
                    ):
                        result = self.provisioner().ensure_ready(
                            self.workspace,
                            lease=lease,
                        )
                provision.assert_not_called()
                self.assertIs(
                    result.failure,
                    ConversationStorageProvisioningFailure.PROVISIONING_FAILED,
                )


if __name__ == "__main__":
    unittest.main()
