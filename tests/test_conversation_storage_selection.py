"""Desired storage selection, parity, and next-shell activation contracts."""

from __future__ import annotations

import ast
import io
import json
import time
import unittest
from contextlib import contextmanager
from dataclasses import FrozenInstanceError, fields, replace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import story_writing_agents.conversation_storage.composition as composition_module
import story_writing_agents.conversation_storage.selection as selection_module
import story_writing_agents
import story_writing_agents.conversation_storage as storage_package
from story_writing_agents.config import ConfigurationSnapshot, ConfigManager
from story_writing_agents.conversation_storage import (
    CONVERSATION_STORAGE_ACTIVATION_STATE_PATH,
    ConversationStorageActivationConflictError,
    ConversationStorageActivationOutcomeKind,
    ConversationStorageBackend,
    ConversationStorageCapability,
    ConversationStorageMigrationReadiness,
    ConversationStorageParity,
    ConversationStoragePreflight,
    ConversationStorageProvisioningNeededError,
    ConversationStorageUnavailableError,
    WorkspaceConversationStorageMutationCoordinator,
    compose_filesystem_conversation_archive,
    load_conversation_storage_activation_state,
)
from story_writing_agents.conversation_storage.provisioning import (
    ConversationStorageProvisioner,
)
from story_writing_agents.conversation_storage.selection import (
    CONVERSATION_STORAGE_SELECTION_SCHEMA_VERSION,
    ConversationStorageSelectionFailure,
    ConversationStorageSelectionReport,
    ConversationStorageStartup,
    activate_configured_conversation_storage,
    inspect_conversation_storage_parity,
    select_conversation_storage,
)
from story_writing_agents.conversation_storage.sqlite import (
    SQLITE_CONVERSATION_STORAGE_DATABASE_PATH,
    compose_sqlite_conversation_archive,
)

from tests.conversation_archive_fixture import (
    archive_conversation,
    archive_from_conversations,
    nonempty_archive,
)
from tests.helpers import initialized_workspace
from tests.sqlite_conversation_storage_fixture import provision


_SYSTEM_PATH = "config/system_defaults.json"
_AGENT_PATH = "config/agent_configs.json"


def _configuration_bytes(workspace: object) -> tuple[bytes, bytes]:
    return (
        workspace.path(_SYSTEM_PATH).read_bytes(),  # type: ignore[attr-defined]
        workspace.path(_AGENT_PATH).read_bytes(),  # type: ignore[attr-defined]
    )


def _sqlite_preflight(
    capability: ConversationStorageCapability,
) -> ConversationStoragePreflight:
    return ConversationStoragePreflight(
        ConversationStorageBackend.SQLITE,
        capability,
    )


def _readiness(
    source: ConversationStorageBackend,
    target: ConversationStorageBackend,
    parity: ConversationStorageParity,
    *,
    empty: bool = False,
    generation: int = 0,
) -> ConversationStorageMigrationReadiness:
    return ConversationStorageMigrationReadiness(
        source_backend=source,
        target_backend=target,
        source_generation=generation,
        source_is_empty=empty,
        parity=parity,
    )


class ConversationStorageSelectionSurfaceTests(unittest.TestCase):
    def test_surface_report_schema_vocabulary_and_json_are_exact(self) -> None:
        self.assertEqual(CONVERSATION_STORAGE_SELECTION_SCHEMA_VERSION, 1)
        self.assertEqual(
            tuple(selection_module.__all__),
            (
                "CONVERSATION_STORAGE_SELECTION_SCHEMA_VERSION",
                "ConversationStorageSelectionFailure",
                "ConversationStorageSelectionReport",
                "ConversationStorageStartup",
                "inspect_conversation_storage_parity",
                "select_conversation_storage",
                "activate_configured_conversation_storage",
            ),
        )
        for name in selection_module.__all__:
            self.assertNotIn(name, storage_package.__all__)
            self.assertNotIn(name, story_writing_agents.__all__)
            self.assertFalse(hasattr(storage_package, name))
            self.assertFalse(hasattr(story_writing_agents, name))
        self.assertEqual(
            tuple(item.value for item in ConversationStorageSelectionFailure),
            (
                "invalid_request",
                "workspace_invalid",
                "coordination_failed",
                "installation_required",
                "installation_unsupported",
                "installation_disallowed",
                "installation_failed",
                "interpreter_unavailable",
                "provisioning_failed",
                "migration_required",
                "parity_mismatch",
                "configuration_invalid",
                "configuration_conflict",
                "configuration_write_failed",
            ),
        )
        self.assertEqual(
            tuple(field.name for field in fields(ConversationStorageSelectionReport)),
            (
                "operation",
                "outcome",
                "desired_backend",
                "installation",
                "provisioning",
                "parity",
                "configuration",
                "activation",
                "failure",
                "schema_version",
            ),
        )
        report = ConversationStorageSelectionReport.failed(
            ConversationStorageSelectionFailure.CONFIGURATION_CONFLICT,
            desired_backend=ConversationStorageBackend.SQLITE,
            installation="completed",
            provisioning="completed",
            parity="verified",
        )
        expected = {
            "schema_version": 1,
            "operation": "use",
            "outcome": "failed",
            "desired_backend": "sqlite",
            "installation": "completed",
            "provisioning": "completed",
            "parity": "verified",
            "configuration": "preserved",
            "activation": "unchanged",
            "failure": "configuration_conflict",
        }
        self.assertEqual(report.to_dict(), expected)
        self.assertEqual(
            report.to_json(),
            json.dumps(
                expected, ensure_ascii=False, separators=(",", ":"), sort_keys=True
            ),
        )
        self.assertEqual(report.exit_code, 2)
        self.assertFalse(hasattr(report, "__dict__"))
        with self.assertRaises(FrozenInstanceError):
            report.outcome = "completed"  # type: ignore[misc]

    def test_selection_module_has_no_migration_or_checkpoint_dependency(self) -> None:
        tree = ast.parse(Path(selection_module.__file__).read_text(encoding="utf-8"))
        imported = {
            node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
        }
        self.assertNotIn("migration", imported)
        self.assertFalse(
            [
                node
                for node in ast.walk(tree)
                if isinstance(node, ast.Name) and "checkpoint" in node.id.lower()
            ]
        )


class ConversationStorageSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = initialized_workspace(self)

    @staticmethod
    def _installer_tripwire(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("a real installer process must never execute")

    def provisioner(
        self,
        *,
        environment: dict[str, str] | None = None,
        runner: object | None = None,
    ) -> ConversationStorageProvisioner:
        return ConversationStorageProvisioner(
            platform_system=lambda: "Linux",
            os_release_reader=lambda: {"ID": "ubuntu"},
            environment={} if environment is None else environment,
            executable_lookup=lambda _name: None,
            process_runner=(self._installer_tripwire if runner is None else runner),  # type: ignore[arg-type]
        )

    def test_invalid_requests_are_provider_free_preflight_free_and_byte_exact(
        self,
    ) -> None:
        before = _configuration_bytes(self.workspace)
        cases = (
            ("mysql", {}),
            (object(), {}),
            ("filesystem", {"install_missing": True, "yes": True}),
            ("sqlite", {"install_missing": True, "yes": False}),
            ("sqlite", {"install_missing": False, "yes": True}),
            ("sqlite", {"installation_consent": object()}),
            (
                "filesystem",
                {
                    "configuration": ConfigManager(
                        self.workspace
                    ).load_configuration_snapshot()
                },
            ),
        )
        with mock.patch.object(
            selection_module,
            "WorkspaceConversationStorageMutationCoordinator",
            side_effect=AssertionError("coordination must not start"),
        ) as coordinator:
            for desired, options in cases:
                with self.subTest(desired=desired, options=options):
                    report = select_conversation_storage(
                        self.workspace,
                        desired,
                        **options,  # type: ignore[arg-type]
                    )
                    self.assertIs(
                        report.failure,
                        ConversationStorageSelectionFailure.INVALID_REQUEST,
                    )
                    self.assertEqual(report.exit_code, 2)
        coordinator.assert_not_called()
        self.assertEqual(_configuration_bytes(self.workspace), before)

    def test_explicit_default_noop_and_legacy_save_are_canonical_nine_key(self) -> None:
        before = _configuration_bytes(self.workspace)
        report = select_conversation_storage(self.workspace, "filesystem")
        self.assertEqual(
            (report.outcome, report.configuration, report.activation),
            ("completed", "unchanged", "next_shell"),
        )
        self.assertEqual(_configuration_bytes(self.workspace), before)
        self.assertFalse(
            self.workspace.path(CONVERSATION_STORAGE_ACTIVATION_STATE_PATH).exists()
        )

        legacy = self.workspace.read_json(_SYSTEM_PATH)
        legacy.pop("conversation_storage_backend")
        self.workspace.write_text(
            _SYSTEM_PATH,
            json.dumps(legacy, separators=(",", ":")) + "\n",
        )
        agent_before = self.workspace.path(_AGENT_PATH).read_bytes()
        report = select_conversation_storage(self.workspace, "filesystem")
        self.assertEqual((report.outcome, report.configuration), ("completed", "saved"))
        persisted = self.workspace.read_json(_SYSTEM_PATH)
        self.assertEqual(persisted["conversation_storage_backend"], "filesystem")
        self.assertEqual(len(persisted), 9)
        self.assertEqual(self.workspace.path(_AGENT_PATH).read_bytes(), agent_before)
        self.assertFalse(
            self.workspace.path(CONVERSATION_STORAGE_ACTIVATION_STATE_PATH).exists()
        )

    def test_settings_candidate_saves_storage_and_other_pending_values_atomically(
        self,
    ) -> None:
        manager = ConfigManager(self.workspace)
        baseline = manager.load_configuration_baseline()
        agents = dict(baseline.configuration.agent_configs)
        first_agent = next(iter(agents))
        agents[first_agent] = replace(agents[first_agent], backend="antigravity")
        candidate = ConfigurationSnapshot(
            replace(baseline.configuration.system_defaults, language="EN"),
            agents,
        )
        report = select_conversation_storage(
            self.workspace,
            "filesystem",
            configuration_baseline=baseline,
            configuration=candidate,
        )
        self.assertEqual((report.outcome, report.configuration), ("completed", "saved"))
        loaded = manager.load_configuration_snapshot()
        self.assertEqual(loaded.system_defaults.language, "EN")
        self.assertEqual(
            loaded.system_defaults.conversation_storage_backend,
            "filesystem",
        )
        self.assertEqual(loaded.agent_configs[first_agent].backend, "antigravity")

    def test_consent_decline_uses_public_disallowed_mapping_and_preserves_bytes(
        self,
    ) -> None:
        """Authoritative mapping: an explicit interactive decline is disallowed."""

        before = _configuration_bytes(self.workspace)
        provisioner = self.provisioner()
        consent = mock.Mock(return_value=False)
        with mock.patch.object(
            provisioner,
            "probe_runtime",
            return_value=_sqlite_preflight(ConversationStorageCapability.UNAVAILABLE),
        ):
            report = select_conversation_storage(
                self.workspace,
                "sqlite",
                installation_consent=consent,
                provisioner=provisioner,
            )
        consent.assert_called_once()
        self.assertIs(
            report.failure,
            ConversationStorageSelectionFailure.INSTALLATION_DISALLOWED,
        )
        self.assertEqual(_configuration_bytes(self.workspace), before)

    def test_ci_refusal_is_disallowed_without_consent_or_process(self) -> None:
        before = _configuration_bytes(self.workspace)
        provisioner = self.provisioner(environment={"GITHUB_ACTIONS": "true"})
        consent = mock.Mock(return_value=True)
        with mock.patch.object(
            provisioner,
            "probe_runtime",
            return_value=_sqlite_preflight(ConversationStorageCapability.UNAVAILABLE),
        ):
            report = select_conversation_storage(
                self.workspace,
                "sqlite",
                installation_consent=consent,
                provisioner=provisioner,
            )
        consent.assert_not_called()
        self.assertIs(
            report.failure,
            ConversationStorageSelectionFailure.INSTALLATION_DISALLOWED,
        )
        self.assertEqual(_configuration_bytes(self.workspace), before)

    def test_consent_controls_propagate_and_preserve_configuration(self) -> None:
        for control in (KeyboardInterrupt("stop"), SystemExit(9)):
            with self.subTest(control=type(control).__name__):
                before = _configuration_bytes(self.workspace)
                provisioner = self.provisioner()
                with mock.patch.object(
                    provisioner,
                    "probe_runtime",
                    return_value=_sqlite_preflight(
                        ConversationStorageCapability.UNAVAILABLE
                    ),
                ):
                    with self.assertRaises(type(control)) as caught:
                        select_conversation_storage(
                            self.workspace,
                            "sqlite",
                            installation_consent=mock.Mock(side_effect=control),
                            provisioner=provisioner,
                        )
                self.assertIs(caught.exception, control)
                self.assertEqual(_configuration_bytes(self.workspace), before)

    def test_zero_exit_install_reprobe_failure_preserves_config(self) -> None:
        before = _configuration_bytes(self.workspace)
        runner = mock.Mock(return_value=SimpleNamespace(returncode=0))
        provisioner = self.provisioner(runner=runner)
        unavailable = _sqlite_preflight(ConversationStorageCapability.UNAVAILABLE)
        with mock.patch.object(
            provisioner,
            "probe_runtime",
            side_effect=(unavailable, unavailable),
        ) as probe:
            report = select_conversation_storage(
                self.workspace,
                "sqlite",
                install_missing=True,
                yes=True,
                diagnostic_stream=io.StringIO(),
                provisioner=provisioner,
            )
        self.assertEqual(probe.call_count, 2)
        self.assertEqual(runner.call_count, 1)
        self.assertIs(
            report.failure,
            ConversationStorageSelectionFailure.INTERPRETER_UNAVAILABLE,
        )
        self.assertEqual(report.installation, "completed")
        self.assertEqual(_configuration_bytes(self.workspace), before)

    def test_capability_unsupported_conflict_and_parity_failures_preserve_bytes(
        self,
    ) -> None:
        before = _configuration_bytes(self.workspace)
        provisioner = ConversationStorageProvisioner(
            platform_system=lambda: "Windows",
            os_release_reader=lambda: {},
            environment={},
            executable_lookup=lambda _name: None,
            process_runner=self._installer_tripwire,
        )
        with mock.patch.object(
            provisioner,
            "probe_runtime",
            return_value=_sqlite_preflight(ConversationStorageCapability.UNAVAILABLE),
        ):
            report = select_conversation_storage(
                self.workspace,
                "sqlite",
                provisioner=provisioner,
            )
        self.assertIs(
            report.failure,
            ConversationStorageSelectionFailure.INSTALLATION_UNSUPPORTED,
        )
        self.assertEqual(_configuration_bytes(self.workspace), before)

        baseline = ConfigManager(self.workspace).load_configuration_baseline()
        self.workspace.path(_SYSTEM_PATH).write_bytes(
            baseline.system_defaults_bytes + b" "
        )
        raced = _configuration_bytes(self.workspace)
        report = select_conversation_storage(
            self.workspace,
            "filesystem",
            configuration_baseline=baseline,
            configuration=baseline.configuration,
        )
        self.assertIs(
            report.failure,
            ConversationStorageSelectionFailure.CONFIGURATION_CONFLICT,
        )
        self.assertEqual(_configuration_bytes(self.workspace), raced)

        self.workspace.path(_SYSTEM_PATH).write_bytes(baseline.system_defaults_bytes)
        mismatch = _readiness(
            ConversationStorageBackend.FILESYSTEM,
            ConversationStorageBackend.SQLITE,
            ConversationStorageParity.MISMATCH,
        )
        provision(self.workspace)
        provisioner = self.provisioner()
        with (
            mock.patch.object(
                provisioner,
                "probe_runtime",
                return_value=_sqlite_preflight(ConversationStorageCapability.READY),
            ),
            mock.patch.object(
                selection_module,
                "inspect_conversation_storage_parity",
                return_value=mismatch,
            ),
        ):
            report = select_conversation_storage(
                self.workspace,
                "sqlite",
                provisioner=provisioner,
            )
        self.assertIs(
            report.failure, ConversationStorageSelectionFailure.PARITY_MISMATCH
        )
        self.assertEqual(_configuration_bytes(self.workspace), before)

    def test_configuration_write_failure_rolls_back_exact_pending_pair(self) -> None:
        manager = ConfigManager(self.workspace)
        baseline = manager.load_configuration_baseline()
        candidate = ConfigurationSnapshot(
            replace(baseline.configuration.system_defaults, language="EN"),
            baseline.configuration.agent_configs,
        )
        before = _configuration_bytes(self.workspace)
        workspace_type = type(self.workspace)
        real_write = workspace_type.write_text
        attempts = 0

        def fail_once(workspace: object, relative: str, text: str) -> None:
            nonlocal attempts
            if relative == _SYSTEM_PATH:
                attempts += 1
                if attempts == 1:
                    raise OSError("private write failure")
            real_write(workspace, relative, text)  # type: ignore[arg-type]

        with mock.patch.object(
            workspace_type,
            "write_text",
            autospec=True,
            side_effect=fail_once,
        ):
            report = select_conversation_storage(
                self.workspace,
                "filesystem",
                configuration_baseline=baseline,
                configuration=candidate,
            )
        self.assertIs(
            report.failure,
            ConversationStorageSelectionFailure.CONFIGURATION_WRITE_FAILED,
        )
        self.assertEqual(attempts, 2)
        self.assertEqual(_configuration_bytes(self.workspace), before)
        self.assertNotIn("private", report.to_json())

    def test_empty_sqlite_provisioning_may_remain_after_stale_config_cas(self) -> None:
        manager = ConfigManager(self.workspace)
        baseline = manager.load_configuration_baseline()
        self.workspace.path(_SYSTEM_PATH).write_bytes(
            baseline.system_defaults_bytes + b" "
        )
        raced = _configuration_bytes(self.workspace)
        database = self.workspace.path(SQLITE_CONVERSATION_STORAGE_DATABASE_PATH)
        self.assertFalse(database.exists())
        provisioner = self.provisioner()
        report = select_conversation_storage(
            self.workspace,
            "sqlite",
            configuration_baseline=baseline,
            configuration=baseline.configuration,
            provisioner=provisioner,
        )
        self.assertIs(
            report.failure,
            ConversationStorageSelectionFailure.CONFIGURATION_CONFLICT,
        )
        self.assertEqual(_configuration_bytes(self.workspace), raced)
        self.assertTrue(database.is_file())
        self.assertEqual(report.provisioning, "completed")

    def test_no_fcntl_rejects_before_probe_provision_config_or_activation(self) -> None:
        before = _configuration_bytes(self.workspace)
        marker = self.workspace.path(CONVERSATION_STORAGE_ACTIVATION_STATE_PATH)
        provisioner = self.provisioner()
        with (
            mock.patch.object(composition_module, "_fcntl", None),
            mock.patch.object(
                provisioner,
                "probe_runtime",
                side_effect=AssertionError("probe must not run"),
            ) as probe,
            mock.patch.object(
                ConfigManager,
                "commit_configuration",
                autospec=True,
                side_effect=AssertionError("commit must not run"),
            ) as commit,
            mock.patch.object(
                selection_module,
                "compose_filesystem_conversation_storage",
                side_effect=AssertionError("composition must not run"),
            ) as compose,
        ):
            report = select_conversation_storage(
                self.workspace,
                "filesystem",
                provisioner=provisioner,
            )
        self.assertIs(
            report.failure, ConversationStorageSelectionFailure.COORDINATION_FAILED
        )
        probe.assert_not_called()
        commit.assert_not_called()
        compose.assert_not_called()
        self.assertEqual(_configuration_bytes(self.workspace), before)
        self.assertFalse(marker.exists())

    def test_delayed_precommit_guard_expires_lease_before_cas_and_preserves_bytes(
        self,
    ) -> None:
        legacy = self.workspace.read_json(_SYSTEM_PATH)
        legacy.pop("conversation_storage_backend")
        self.workspace.write_text(
            _SYSTEM_PATH,
            json.dumps(legacy, separators=(",", ":")) + "\n",
        )
        before = _configuration_bytes(self.workspace)
        guard = mock.Mock(side_effect=lambda: time.sleep(0.05))
        report = select_conversation_storage(
            self.workspace,
            "filesystem",
            precommit_guard=guard,
            mutation_timeout_seconds=0.02,
        )
        guard.assert_called_once_with()
        self.assertIs(
            report.failure,
            ConversationStorageSelectionFailure.COORDINATION_FAILED,
        )
        self.assertEqual(_configuration_bytes(self.workspace), before)

    def test_successful_cas_has_no_post_commit_lease_validation_point(self) -> None:
        legacy = self.workspace.read_json(_SYSTEM_PATH)
        legacy.pop("conversation_storage_backend")
        self.workspace.write_text(
            _SYSTEM_PATH,
            json.dumps(legacy, separators=(",", ":")) + "\n",
        )
        real_validate = selection_module._validate_lease
        real_commit = ConfigManager.commit_configuration
        events: list[str] = []
        committed = False

        def validate(lease: object, workspace_key: str) -> None:
            if committed:
                raise AssertionError("lease validation occurred after CAS")
            events.append("validate")
            real_validate(lease, workspace_key)

        def guard() -> None:
            events.append("guard")

        def commit(
            manager: ConfigManager,
            baseline: object,
            configuration: object,
        ) -> bool:
            nonlocal committed
            events.append("commit")
            changed = real_commit(manager, baseline, configuration)  # type: ignore[arg-type]
            committed = True
            return changed

        readiness = _readiness(
            ConversationStorageBackend.FILESYSTEM,
            ConversationStorageBackend.FILESYSTEM,
            ConversationStorageParity.NOT_REQUIRED,
            empty=True,
        )
        with (
            mock.patch.object(
                selection_module,
                "_validate_lease",
                side_effect=validate,
            ),
            mock.patch.object(
                selection_module,
                "inspect_conversation_storage_parity",
                side_effect=lambda *_args, **_kwargs: (
                    events.append("parity") or readiness
                ),
            ),
            mock.patch.object(
                ConfigManager,
                "commit_configuration",
                autospec=True,
                side_effect=commit,
            ),
        ):
            report = select_conversation_storage(
                self.workspace,
                "filesystem",
                precommit_guard=guard,
            )
        self.assertEqual(report.outcome, "completed")
        self.assertEqual(report.configuration, "saved")
        self.assertEqual(
            events,
            [
                "validate",
                "validate",
                "parity",
                "validate",
                "guard",
                "validate",
                "commit",
            ],
        )
        self.assertEqual(
            self.workspace.read_json(_SYSTEM_PATH)["conversation_storage_backend"],
            "filesystem",
        )

    def test_busy_or_corrupt_database_never_reaches_installer(self) -> None:
        database = self.workspace.path(SQLITE_CONVERSATION_STORAGE_DATABASE_PATH)
        database.parent.mkdir(parents=True, exist_ok=True)
        database.write_bytes(b"not a sqlite database")
        before = _configuration_bytes(self.workspace)
        provisioner = self.provisioner()
        with mock.patch.object(
            provisioner,
            "probe_runtime",
            return_value=_sqlite_preflight(ConversationStorageCapability.READY),
        ):
            report = select_conversation_storage(
                self.workspace,
                "sqlite",
                provisioner=provisioner,
            )
        self.assertIs(
            report.failure,
            ConversationStorageSelectionFailure.PROVISIONING_FAILED,
        )
        self.assertEqual(_configuration_bytes(self.workspace), before)


class ConversationStorageParityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = initialized_workspace(self)
        provision(self.workspace)

    def import_archive(
        self,
        backend: ConversationStorageBackend,
        payload: object,
    ) -> None:
        adapter = (
            compose_filesystem_conversation_archive(self.workspace)
            if backend is ConversationStorageBackend.FILESYSTEM
            else compose_sqlite_conversation_archive(self.workspace)
        )
        coordinator = WorkspaceConversationStorageMutationCoordinator(self.workspace)
        with coordinator.borrow(timeout_seconds=2.0) as lease:
            adapter.import_archive(lease, payload)  # type: ignore[arg-type]

    def inspect(
        self,
        source: ConversationStorageBackend,
        target: ConversationStorageBackend,
    ) -> ConversationStorageMigrationReadiness:
        coordinator = WorkspaceConversationStorageMutationCoordinator(self.workspace)
        with coordinator.borrow(timeout_seconds=2.0) as lease:
            return inspect_conversation_storage_parity(
                self.workspace,
                lease=lease,
                source_backend=source,
                target_backend=target,
                source_generation=0,
            )

    def test_empty_source_and_same_backend_need_no_migration_or_checkpoint(
        self,
    ) -> None:
        self.import_archive(ConversationStorageBackend.SQLITE, nonempty_archive())
        empty = self.inspect(
            ConversationStorageBackend.FILESYSTEM,
            ConversationStorageBackend.SQLITE,
        )
        self.assertTrue(empty.source_is_empty)
        self.assertIs(empty.parity, ConversationStorageParity.NOT_REQUIRED)
        self.assertTrue(empty.can_activate)

        same = self.inspect(
            ConversationStorageBackend.SQLITE,
            ConversationStorageBackend.SQLITE,
        )
        self.assertIs(same.parity, ConversationStorageParity.NOT_REQUIRED)
        self.assertFalse(
            tuple(
                self.workspace.path("agent_states/orchestrator_state").rglob(
                    "*checkpoint*"
                )
            )
        )

    def test_conversation_without_events_is_nonempty_and_requires_parity(self) -> None:
        payload = archive_from_conversations(archive_conversation())
        self.import_archive(ConversationStorageBackend.FILESYSTEM, payload)
        readiness = self.inspect(
            ConversationStorageBackend.FILESYSTEM,
            ConversationStorageBackend.SQLITE,
        )
        self.assertFalse(readiness.source_is_empty)
        self.assertIs(readiness.parity, ConversationStorageParity.UNVERIFIED)
        self.assertFalse(readiness.can_activate)

    def test_equal_archives_verify_in_both_directions_and_mismatch_is_exact(
        self,
    ) -> None:
        payload = nonempty_archive()
        self.import_archive(ConversationStorageBackend.FILESYSTEM, payload)
        self.import_archive(ConversationStorageBackend.SQLITE, payload)
        for source, target in (
            (ConversationStorageBackend.FILESYSTEM, ConversationStorageBackend.SQLITE),
            (ConversationStorageBackend.SQLITE, ConversationStorageBackend.FILESYSTEM),
        ):
            with self.subTest(source=source):
                readiness = self.inspect(source, target)
                self.assertIs(readiness.parity, ConversationStorageParity.VERIFIED)
                self.assertTrue(readiness.can_activate)

        other = archive_from_conversations(
            archive_conversation(
                session_id="other_session",
                conversation_id="other_conversation",
            )
        )
        second = initialized_workspace(self)
        provision(second)
        fs = compose_filesystem_conversation_archive(second)
        sqlite = compose_sqlite_conversation_archive(second)
        coordinator = WorkspaceConversationStorageMutationCoordinator(second)
        with coordinator.borrow(timeout_seconds=2.0) as lease:
            fs.import_archive(lease, payload)
            sqlite.import_archive(lease, other)
            readiness = inspect_conversation_storage_parity(
                second,
                lease=lease,
                source_backend="filesystem",
                target_backend="sqlite",
                source_generation=4,
            )
        self.assertEqual(readiness.source_generation, 4)
        self.assertIs(readiness.parity, ConversationStorageParity.MISMATCH)
        self.assertFalse(readiness.can_activate)


class ConversationStorageStartupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = initialized_workspace(self)
        provision(self.workspace)

    def set_desired(self, backend: str) -> None:
        manager = ConfigManager(self.workspace)
        baseline = manager.load_configuration_baseline()
        manager.commit_configuration(
            baseline,
            ConfigurationSnapshot(
                replace(
                    baseline.configuration.system_defaults,
                    conversation_storage_backend=backend,
                ),
                baseline.configuration.agent_configs,
            ),
        )

    def test_startup_activates_empty_backends_in_both_directions(self) -> None:
        self.set_desired("sqlite")
        sqlite_startup = activate_configured_conversation_storage(self.workspace)
        self.assertIsInstance(sqlite_startup, ConversationStorageStartup)
        self.assertIs(
            sqlite_startup.services.backend, ConversationStorageBackend.SQLITE
        )
        self.assertIs(
            sqlite_startup.activation.kind,
            ConversationStorageActivationOutcomeKind.ACTIVATED,
        )
        self.assertEqual(sqlite_startup.activation.current.generation, 1)

        self.set_desired("filesystem")
        filesystem_startup = activate_configured_conversation_storage(self.workspace)
        self.assertIs(
            filesystem_startup.services.backend,
            ConversationStorageBackend.FILESYSTEM,
        )
        self.assertIs(
            filesystem_startup.activation.kind,
            ConversationStorageActivationOutcomeKind.ACTIVATED,
        )
        self.assertEqual(filesystem_startup.activation.current.generation, 2)

        unchanged = activate_configured_conversation_storage(self.workspace)
        self.assertIs(unchanged.services.backend, ConversationStorageBackend.FILESYSTEM)
        self.assertIs(
            unchanged.activation.kind,
            ConversationStorageActivationOutcomeKind.UNCHANGED,
        )
        self.assertEqual(unchanged.activation.current.generation, 2)

    def test_startup_uses_one_outer_lease_and_same_bundle_backend(self) -> None:
        self.set_desired("sqlite")
        real_type = WorkspaceConversationStorageMutationCoordinator
        borrows: list[float] = []

        class RecordingCoordinator:
            def __init__(self, workspace: object) -> None:
                self._real = real_type(workspace)  # type: ignore[arg-type]

            @contextmanager
            def borrow(self, *, timeout_seconds: float):
                borrows.append(timeout_seconds)
                with self._real.borrow(timeout_seconds=timeout_seconds) as lease:
                    yield lease

        with mock.patch.object(
            selection_module,
            "WorkspaceConversationStorageMutationCoordinator",
            RecordingCoordinator,
        ):
            startup = activate_configured_conversation_storage(self.workspace)
        self.assertEqual(borrows, [30.0])
        self.assertIs(startup.services.backend, startup.activation.current.backend)

    def test_startup_mismatch_and_target_failure_do_not_fallback_or_write_marker(
        self,
    ) -> None:
        self.set_desired("sqlite")
        marker = self.workspace.path(CONVERSATION_STORAGE_ACTIVATION_STATE_PATH)
        mismatch = _readiness(
            ConversationStorageBackend.FILESYSTEM,
            ConversationStorageBackend.SQLITE,
            ConversationStorageParity.MISMATCH,
        )
        with mock.patch.object(
            selection_module,
            "inspect_conversation_storage_parity",
            return_value=mismatch,
        ):
            with self.assertRaises(Exception):
                activate_configured_conversation_storage(self.workspace)
        self.assertFalse(marker.exists())
        self.assertIs(
            load_conversation_storage_activation_state(self.workspace).backend,
            ConversationStorageBackend.FILESYSTEM,
        )

        ready = _readiness(
            ConversationStorageBackend.FILESYSTEM,
            ConversationStorageBackend.SQLITE,
            ConversationStorageParity.NOT_REQUIRED,
            empty=True,
        )
        real_compose = selection_module._compose_active_services
        calls: list[ConversationStorageBackend] = []

        def fail_target(workspace: object, backend: ConversationStorageBackend):
            calls.append(backend)
            if backend is ConversationStorageBackend.SQLITE:
                raise OSError("private target failure")
            return real_compose(workspace, backend)  # type: ignore[arg-type]

        with (
            mock.patch.object(
                selection_module,
                "inspect_conversation_storage_parity",
                return_value=ready,
            ),
            mock.patch.object(
                selection_module,
                "_compose_active_services",
                side_effect=fail_target,
            ),
        ):
            with self.assertRaises(OSError):
                activate_configured_conversation_storage(self.workspace)
        self.assertEqual(
            calls,
            [ConversationStorageBackend.FILESYSTEM, ConversationStorageBackend.SQLITE],
        )
        self.assertFalse(marker.exists())

    def test_stale_activation_cas_propagates_without_fallback(self) -> None:
        self.set_desired("sqlite")
        marker = self.workspace.path(CONVERSATION_STORAGE_ACTIVATION_STATE_PATH)
        with mock.patch.object(
            selection_module,
            "compare_and_set_conversation_storage_activation",
            side_effect=ConversationStorageActivationConflictError(),
        ) as compare:
            with self.assertRaises(ConversationStorageActivationConflictError):
                activate_configured_conversation_storage(self.workspace)
        compare.assert_called_once()
        self.assertFalse(marker.exists())

    def test_missing_or_unavailable_sqlite_target_never_falls_back(self) -> None:
        missing = initialized_workspace(self)
        manager = ConfigManager(missing)
        baseline = manager.load_configuration_baseline()
        manager.commit_configuration(
            baseline,
            ConfigurationSnapshot(
                replace(
                    baseline.configuration.system_defaults,
                    conversation_storage_backend="sqlite",
                ),
                baseline.configuration.agent_configs,
            ),
        )
        marker = missing.path(CONVERSATION_STORAGE_ACTIVATION_STATE_PATH)
        with self.assertRaises(ConversationStorageProvisioningNeededError):
            activate_configured_conversation_storage(missing)
        self.assertFalse(marker.exists())
        self.assertIs(
            load_conversation_storage_activation_state(missing).backend,
            ConversationStorageBackend.FILESYSTEM,
        )

        unavailable = _sqlite_preflight(ConversationStorageCapability.UNAVAILABLE)
        with mock.patch.object(
            selection_module,
            "probe_sqlite_conversation_storage_runtime",
            return_value=unavailable,
        ) as probe:
            with self.assertRaises(ConversationStorageUnavailableError):
                activate_configured_conversation_storage(missing)
        probe.assert_called_once_with()
        self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
