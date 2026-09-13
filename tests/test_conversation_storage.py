"""Conversation-storage contracts, composition, state, and coordination tests."""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from copy import copy
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import get_type_hints
from unittest import mock

import story_writing_agents
import story_writing_agents.conversation_storage as storage
from story_writing_agents import (
    ConversationCatalog,
    FilesystemConversationCatalog,
    Workspace,
)
from story_writing_agents.conversation_search import (
    CollaborationHistorySearchQuery,
    CollaborationHistorySearchScope,
    ReferenceCollaborationHistorySearch,
)
from story_writing_agents.conversation_storage import (
    CONVERSATION_STORAGE_ACTIVATION_SCHEMA_VERSION,
    CONVERSATION_STORAGE_ACTIVATION_STATE_PATH,
    CONVERSATION_STORAGE_MUTATION_GUARD_PATH,
    CONVERSATION_STORAGE_MUTATION_MAX_TIMEOUT_SECONDS,
    ConversationArchiveImportReceipt,
    ConversationArchiveImportStatus,
    ConversationArchiveSink,
    ConversationArchiveSource,
    ConversationStorageActivationConflictError,
    ConversationStorageActivationError,
    ConversationStorageActivationOutcome,
    ConversationStorageActivationOutcomeKind,
    ConversationStorageActivationState,
    ConversationStorageActivationStateError,
    ConversationStorageActivationVersionError,
    ConversationStorageBackend,
    ConversationStorageCapability,
    ConversationStorageComponentBinding,
    ConversationStorageConfigurationError,
    ConversationStorageCoordinationError,
    ConversationStorageError,
    ConversationStorageFailure,
    ConversationStorageTransferReadiness,
    ConversationStorageTransferRequiredError,
    ConversationStorageMutationLease,
    ConversationStorageParity,
    ConversationStorageParityError,
    ConversationStoragePreflight,
    ConversationStorageProvisioningNeededError,
    ConversationStorageServices,
    ConversationStorageServicesError,
    ConversationStorageUnavailableError,
    WorkspaceConversationStorageMutationCoordinator,
    compare_and_set_conversation_storage_activation,
    compose_filesystem_conversation_storage,
    load_conversation_storage_activation_state,
    parse_conversation_storage_backend,
    validate_conversation_storage_mutation_timeout,
)
from story_writing_agents.conversation_storage import (
    archive as archive_module,
)
from story_writing_agents.conversation_storage import (
    composition as composition_module,
)
from story_writing_agents.conversation_storage import contracts as contracts_module
from story_writing_agents.conversation_storage import (
    filesystem_archive as filesystem_archive_module,
)
from story_writing_agents.conversation_storage import filesystem as filesystem_module
from story_writing_agents.conversation_storage.filesystem import (
    FilesystemCollaborationEventStore,
)
from story_writing_agents.conversations import CollaborationEventStore

from tests.collaboration_contract_fixture import contract_batches
from tests.helpers import initialize_test_workspace


def _initialized_workspace(test_case: unittest.TestCase) -> Workspace:
    temporary = tempfile.TemporaryDirectory()
    test_case.addCleanup(temporary.cleanup)
    return initialize_test_workspace(temporary.name)


def _namespace_snapshot(root: Path) -> dict[str, bytes | None]:
    return {
        path.relative_to(root).as_posix(): (
            None if path.is_dir() else path.read_bytes()
        )
        for path in sorted(root.rglob("*"))
    }


def _marker_path(workspace: Workspace) -> Path:
    return workspace.root / CONVERSATION_STORAGE_ACTIVATION_STATE_PATH


def _marker_temporaries(workspace: Workspace) -> list[Path]:
    marker = _marker_path(workspace)
    return sorted(marker.parent.glob(f".{marker.name}.*.tmp"))


def _readiness(
    source: ConversationStorageBackend,
    target: ConversationStorageBackend,
    *,
    generation: int = 0,
    source_is_empty: bool = True,
    parity: ConversationStorageParity = ConversationStorageParity.NOT_REQUIRED,
) -> ConversationStorageTransferReadiness:
    return ConversationStorageTransferReadiness(
        source_backend=source,
        target_backend=target,
        source_generation=generation,
        source_is_empty=source_is_empty,
        parity=parity,
    )


class ConversationStorageContractTests(unittest.TestCase):
    def test_backend_capability_parity_and_outcome_vocabularies_are_exact(
        self,
    ) -> None:
        self.assertEqual(
            tuple(item.value for item in ConversationStorageBackend),
            ("filesystem", "sqlite"),
        )
        self.assertEqual(
            tuple(item.value for item in ConversationStorageCapability),
            ("ready", "provisioning_needed", "unavailable"),
        )
        self.assertEqual(
            tuple(item.value for item in ConversationStorageParity),
            ("not_required", "unverified", "verified", "mismatch"),
        )
        self.assertEqual(
            tuple(item.value for item in ConversationStorageActivationOutcomeKind),
            ("unchanged", "activated"),
        )
        self.assertEqual(
            tuple(item.value for item in ConversationArchiveImportStatus),
            ("imported", "unchanged"),
        )
        self.assertEqual(CONVERSATION_STORAGE_ACTIVATION_SCHEMA_VERSION, 1)

    def test_backend_parser_preserves_default_and_rejects_invalid_raw_values(
        self,
    ) -> None:
        self.assertIs(
            parse_conversation_storage_backend(None),
            ConversationStorageBackend.FILESYSTEM,
        )
        self.assertIs(
            parse_conversation_storage_backend("filesystem"),
            ConversationStorageBackend.FILESYSTEM,
        )
        self.assertIs(
            parse_conversation_storage_backend("sqlite"),
            ConversationStorageBackend.SQLITE,
        )
        for value in (
            "",
            "SQLite",
            "mysql",
            True,
            1,
            ConversationStorageBackend.SQLITE,
        ):
            with self.subTest(value=value):
                with self.assertRaises(ConversationStorageConfigurationError) as raised:
                    parse_conversation_storage_backend(value)
                self.assertIs(
                    raised.exception.failure,
                    ConversationStorageFailure.INVALID_CONFIGURATION,
                )

    def test_failure_codes_and_exception_messages_are_fixed_and_content_free(
        self,
    ) -> None:
        expected_codes = (
            "invalid_configuration",
            "inconsistent_services",
            "backend_unavailable",
            "provisioning_needed",
            "transfer_required",
            "parity_mismatch",
            "activation_state_invalid",
            "activation_state_unsupported",
            "activation_conflict",
            "activation_failed",
            "coordination_failed",
        )
        self.assertEqual(
            tuple(item.value for item in ConversationStorageFailure),
            expected_codes,
        )
        cases = (
            (
                ConversationStorageConfigurationError,
                ConversationStorageFailure.INVALID_CONFIGURATION,
            ),
            (
                ConversationStorageServicesError,
                ConversationStorageFailure.INCONSISTENT_SERVICES,
            ),
            (
                ConversationStorageUnavailableError,
                ConversationStorageFailure.BACKEND_UNAVAILABLE,
            ),
            (
                ConversationStorageProvisioningNeededError,
                ConversationStorageFailure.PROVISIONING_NEEDED,
            ),
            (
                ConversationStorageTransferRequiredError,
                ConversationStorageFailure.TRANSFER_REQUIRED,
            ),
            (
                ConversationStorageParityError,
                ConversationStorageFailure.PARITY_MISMATCH,
            ),
            (
                ConversationStorageActivationStateError,
                ConversationStorageFailure.ACTIVATION_STATE_INVALID,
            ),
            (
                ConversationStorageActivationVersionError,
                ConversationStorageFailure.ACTIVATION_STATE_UNSUPPORTED,
            ),
            (
                ConversationStorageActivationConflictError,
                ConversationStorageFailure.ACTIVATION_CONFLICT,
            ),
            (
                ConversationStorageActivationError,
                ConversationStorageFailure.ACTIVATION_FAILED,
            ),
            (
                ConversationStorageCoordinationError,
                ConversationStorageFailure.COORDINATION_FAILED,
            ),
        )
        for error_type, failure in cases:
            with self.subTest(error=error_type.__name__):
                error = error_type()
                self.assertIsInstance(error, ConversationStorageError)
                self.assertIs(error.failure, failure)
                self.assertEqual(
                    str(error),
                    f"conversation storage failure: {failure.value}",
                )
        with self.assertRaises(TypeError):
            ConversationStorageError("coordination_failed")  # type: ignore[arg-type]

    def test_preflight_and_transfer_readiness_raise_typed_fixed_failures(
        self,
    ) -> None:
        ready = ConversationStoragePreflight(
            ConversationStorageBackend.FILESYSTEM,
            ConversationStorageCapability.READY,
        )
        self.assertIsNone(ready.require_ready())
        for capability, error_type in (
            (
                ConversationStorageCapability.PROVISIONING_NEEDED,
                ConversationStorageProvisioningNeededError,
            ),
            (
                ConversationStorageCapability.UNAVAILABLE,
                ConversationStorageUnavailableError,
            ),
        ):
            with self.subTest(capability=capability):
                with self.assertRaises(error_type):
                    ConversationStoragePreflight(
                        ConversationStorageBackend.SQLITE,
                        capability,
                    ).require_ready()

        for readiness in (
            _readiness(
                ConversationStorageBackend.FILESYSTEM,
                ConversationStorageBackend.FILESYSTEM,
                source_is_empty=False,
            ),
            _readiness(
                ConversationStorageBackend.FILESYSTEM,
                ConversationStorageBackend.SQLITE,
            ),
            _readiness(
                ConversationStorageBackend.FILESYSTEM,
                ConversationStorageBackend.SQLITE,
                source_is_empty=False,
                parity=ConversationStorageParity.VERIFIED,
            ),
        ):
            with self.subTest(readiness=readiness):
                self.assertTrue(readiness.can_activate)
                self.assertIsNone(readiness.require_activation_ready())

        for parity, error_type in (
            (
                ConversationStorageParity.UNVERIFIED,
                ConversationStorageTransferRequiredError,
            ),
            (ConversationStorageParity.MISMATCH, ConversationStorageParityError),
        ):
            readiness = _readiness(
                ConversationStorageBackend.FILESYSTEM,
                ConversationStorageBackend.SQLITE,
                source_is_empty=False,
                parity=parity,
            )
            with self.subTest(parity=parity):
                self.assertFalse(readiness.can_activate)
                with self.assertRaises(error_type):
                    readiness.require_activation_ready()

    def test_value_objects_are_strict_frozen_and_internally_consistent(self) -> None:
        state = ConversationStorageActivationState(
            ConversationStorageBackend.FILESYSTEM,
            0,
        )
        self.assertEqual(
            state.to_dict(),
            {"schema_version": 1, "backend": "filesystem", "generation": 0},
        )
        with self.assertRaises(FrozenInstanceError):
            state.generation = 1  # type: ignore[misc]
        for generation in (True, "0", None):
            with self.subTest(generation=generation):
                with self.assertRaises(TypeError):
                    ConversationStorageActivationState(  # type: ignore[arg-type]
                        ConversationStorageBackend.FILESYSTEM,
                        generation,
                    )
        with self.assertRaises(ValueError):
            ConversationStorageActivationState(
                ConversationStorageBackend.FILESYSTEM,
                -1,
            )
        with self.assertRaises(TypeError):
            ConversationStoragePreflight(  # type: ignore[arg-type]
                "filesystem",
                ConversationStorageCapability.READY,
            )
        with self.assertRaises(ValueError):
            _readiness(
                ConversationStorageBackend.FILESYSTEM,
                ConversationStorageBackend.SQLITE,
                source_is_empty=True,
                parity=ConversationStorageParity.VERIFIED,
            )
        with self.assertRaises(ValueError):
            _readiness(
                ConversationStorageBackend.FILESYSTEM,
                ConversationStorageBackend.SQLITE,
                source_is_empty=False,
                parity=ConversationStorageParity.NOT_REQUIRED,
            )
        with self.assertRaises(TypeError):
            ConversationArchiveImportReceipt("imported")  # type: ignore[arg-type]

    def test_activation_outcomes_validate_one_exact_transition(self) -> None:
        previous = ConversationStorageActivationState(
            ConversationStorageBackend.FILESYSTEM,
            0,
        )
        current = ConversationStorageActivationState(
            ConversationStorageBackend.SQLITE,
            1,
        )
        activated = ConversationStorageActivationOutcome(
            ConversationStorageActivationOutcomeKind.ACTIVATED,
            previous,
            current,
        )
        unchanged = ConversationStorageActivationOutcome(
            ConversationStorageActivationOutcomeKind.UNCHANGED,
            previous,
            previous,
        )
        self.assertIs(activated.current, current)
        self.assertIs(unchanged.current, previous)
        invalid = (
            (
                ConversationStorageActivationOutcomeKind.UNCHANGED,
                previous,
                current,
            ),
            (
                ConversationStorageActivationOutcomeKind.ACTIVATED,
                previous,
                ConversationStorageActivationState(
                    ConversationStorageBackend.FILESYSTEM,
                    1,
                ),
            ),
            (
                ConversationStorageActivationOutcomeKind.ACTIVATED,
                previous,
                ConversationStorageActivationState(
                    ConversationStorageBackend.SQLITE,
                    2,
                ),
            ),
        )
        for kind, before, after in invalid:
            with self.subTest(kind=kind, after=after):
                with self.assertRaises(ValueError):
                    ConversationStorageActivationOutcome(kind, before, after)

    def test_timeout_validation_is_finite_positive_and_bounded(self) -> None:
        self.assertEqual(validate_conversation_storage_mutation_timeout(1), 1.0)
        self.assertEqual(
            validate_conversation_storage_mutation_timeout(
                CONVERSATION_STORAGE_MUTATION_MAX_TIMEOUT_SECONDS
            ),
            CONVERSATION_STORAGE_MUTATION_MAX_TIMEOUT_SECONDS,
        )
        for value in (True, None, "1"):
            with self.subTest(value=value):
                with self.assertRaises(TypeError):
                    validate_conversation_storage_mutation_timeout(value)
        for value in (
            0,
            -1,
            float("inf"),
            float("-inf"),
            float("nan"),
            CONVERSATION_STORAGE_MUTATION_MAX_TIMEOUT_SECONDS + 0.1,
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    validate_conversation_storage_mutation_timeout(value)

    def test_archive_protocols_are_generic_and_do_not_widen_ordinary_ports(
        self,
    ) -> None:
        source_methods = {
            name
            for name, member in vars(ConversationArchiveSource).items()
            if not name.startswith("_") and callable(member)
        }
        sink_methods = {
            name
            for name, member in vars(ConversationArchiveSink).items()
            if not name.startswith("_") and callable(member)
        }
        self.assertEqual(source_methods, {"export_archive"})
        self.assertEqual(sink_methods, {"import_archive"})
        source_hints = get_type_hints(ConversationArchiveSource.export_archive)
        sink_hints = get_type_hints(ConversationArchiveSink.import_archive)
        self.assertIs(source_hints["lease"], ConversationStorageMutationLease)
        self.assertTrue(source_hints["return"].__covariant__)
        self.assertIs(sink_hints["lease"], ConversationStorageMutationLease)
        self.assertTrue(sink_hints["payload"].__contravariant__)
        self.assertIs(sink_hints["return"], ConversationArchiveImportReceipt)

        catalog_methods = {
            name
            for name, member in vars(ConversationCatalog).items()
            if not name.startswith("_") and callable(member)
        }
        event_methods = {
            name
            for name, member in vars(CollaborationEventStore).items()
            if not name.startswith("_") and callable(member)
        }
        self.assertEqual(
            catalog_methods,
            {"create", "get", "list", "rename", "archive", "unarchive"},
        )
        self.assertEqual(event_methods, {"append_batch", "read", "read_context"})
        self.assertNotIn("export_archive", catalog_methods | event_methods)
        self.assertNotIn("import_archive", catalog_methods | event_methods)

    def test_structural_archive_ports_use_an_active_workspace_lease(self) -> None:
        workspace = _initialized_workspace(self)
        coordinator = WorkspaceConversationStorageMutationCoordinator(workspace)
        calls: list[tuple[str, object]] = []

        class Source:
            def export_archive(self, lease: ConversationStorageMutationLease) -> str:
                lease.validate(workspace_key=coordinator.workspace_key)
                calls.append(("export", lease))
                return "exact-payload"

        class Sink:
            def import_archive(
                self,
                lease: ConversationStorageMutationLease,
                payload: str,
            ) -> ConversationArchiveImportReceipt:
                lease.validate(workspace_key=coordinator.workspace_key)
                calls.append(("import", payload))
                return ConversationArchiveImportReceipt(
                    ConversationArchiveImportStatus.IMPORTED
                )

        with coordinator.borrow(timeout_seconds=2.0) as lease:
            payload = Source().export_archive(lease)
            receipt = Sink().import_archive(lease, payload)
        self.assertEqual(payload, "exact-payload")
        self.assertIs(receipt.status, ConversationArchiveImportStatus.IMPORTED)
        self.assertEqual(calls, [("export", lease), ("import", payload)])


class ConversationStorageServicesTests(unittest.TestCase):
    def test_filesystem_composition_is_io_free_and_uses_one_exact_provenance(
        self,
    ) -> None:
        workspace = _initialized_workspace(self)
        before = _namespace_snapshot(workspace.root)

        services = compose_filesystem_conversation_storage(workspace)

        self.assertEqual(_namespace_snapshot(workspace.root), before)
        self.assertIs(services.backend, ConversationStorageBackend.FILESYSTEM)
        self.assertEqual(services.provenance.workspace_key, os.fspath(workspace.root))
        self.assertIs(
            services.catalog_binding.provenance,
            services.event_store_binding.provenance,
        )
        self.assertIs(
            services.catalog_binding.provenance,
            services.search_binding.provenance,
        )
        self.assertIs(services.catalog, services.catalog_binding.component)
        self.assertIs(services.event_store, services.event_store_binding.component)
        self.assertIs(services.search, services.search_binding.component)
        self.assertIsInstance(services.catalog, FilesystemConversationCatalog)
        self.assertIsInstance(services.event_store, FilesystemCollaborationEventStore)
        self.assertIsInstance(services.search, ReferenceCollaborationHistorySearch)
        self.assertIs(
            composition_module.FilesystemConversationCatalog,
            filesystem_module.FilesystemConversationCatalog,
        )
        self.assertIs(
            composition_module.FilesystemCollaborationEventStore,
            filesystem_module.FilesystemCollaborationEventStore,
        )
        self.assertIn(
            filesystem_module.FilesystemConversationCatalog,
            type(services.catalog).__mro__,
        )
        self.assertIn(
            filesystem_module.FilesystemCollaborationEventStore,
            type(services.event_store).__mro__,
        )
        self.assertEqual(
            services.mutation_coordinator.workspace_key,
            services.provenance.workspace_key,
        )
        self.assertFalse(_marker_path(workspace).exists())

    def test_composed_services_remain_concrete_and_use_only_pinned_workspace(
        self,
    ) -> None:
        workspace = _initialized_workspace(self)
        pinned_root = workspace.root
        other = _initialized_workspace(self)
        other_root = other.root
        other_services = compose_filesystem_conversation_storage(other)
        decoy = other_services.catalog.create("session_a", "Other workspace decoy")
        other_services.event_store.append_batch(
            contract_batches(decoy.conversation_id)["proposal"]
        )
        other_before = _namespace_snapshot(other_root)

        services = compose_filesystem_conversation_storage(workspace)
        catalog = services.catalog
        event_store = services.event_store
        search = services.search
        self.assertIsInstance(catalog, FilesystemConversationCatalog)
        self.assertIsInstance(event_store, FilesystemCollaborationEventStore)
        self.assertIsInstance(search, ReferenceCollaborationHistorySearch)
        pinned_workspace = getattr(catalog, "workspace")

        workspace.root = other_root
        setattr(catalog, "workspace", other)
        setattr(event_store, "workspace", other)
        setattr(event_store, "catalog", other_services.catalog)
        setattr(search, "_catalog", other_services.catalog)
        setattr(search, "_event_store", other_services.event_store)
        self.assertEqual(getattr(catalog, "workspace").root, pinned_root)
        self.assertEqual(getattr(event_store, "workspace").root, pinned_root)
        self.assertIs(getattr(event_store, "catalog"), catalog)

        metadata = catalog.create("session_a", "Pinned workspace witness")
        receipt = event_store.append_batch(
            contract_batches(metadata.conversation_id)["proposal"]
        )
        history = event_store.read("session_a", metadata.conversation_id)
        result = search.search(
            CollaborationHistorySearchScope("session_a"),
            CollaborationHistorySearchQuery("Consult specialists"),
        )

        self.assertEqual(len(result.hits), 1)
        proposal = next(
            event for event in history if event.event_id == "proposal_event"
        )
        self.assertEqual(
            result.hits[0].citation.conversation_id, metadata.conversation_id
        )
        self.assertEqual(result.hits[0].citation.sequence, proposal.sequence)
        self.assertEqual(
            event_store.read("session_a", metadata.conversation_id),
            history,
        )
        self.assertEqual(receipt.first_sequence, 1)
        self.assertEqual(
            FilesystemConversationCatalog(pinned_workspace).get(
                "session_a",
                metadata.conversation_id,
            ),
            metadata,
        )
        self.assertEqual(_namespace_snapshot(other_root), other_before)

    def test_filesystem_bundle_shares_authoritative_catalog_event_and_search(
        self,
    ) -> None:
        workspace = _initialized_workspace(self)
        services = compose_filesystem_conversation_storage(str(workspace.root))
        metadata = services.catalog.create("session_a", "Harbor timing")
        services.event_store.append_batch(
            contract_batches(metadata.conversation_id)["proposal"]
        )
        history = services.event_store.read("session_a", metadata.conversation_id)

        result = services.search.search(
            CollaborationHistorySearchScope("session_a"),
            CollaborationHistorySearchQuery("Consult specialists"),
        )

        self.assertEqual(len(result.hits), 1)
        proposal = next(
            event for event in history if event.event_id == "proposal_event"
        )
        self.assertEqual(
            result.hits[0].citation.conversation_id,
            metadata.conversation_id,
        )
        self.assertEqual(result.hits[0].citation.sequence, proposal.sequence)
        self.assertEqual(
            services.event_store.read("session_a", metadata.conversation_id)[0],
            history[0],
        )
        self.assertFalse(_marker_path(workspace).exists())

    def test_composed_filesystem_mutations_borrow_the_shared_bundle_coordinator(
        self,
    ) -> None:
        workspace = _initialized_workspace(self)
        services = compose_filesystem_conversation_storage(workspace)
        original_borrow = WorkspaceConversationStorageMutationCoordinator.borrow
        calls: list[tuple[object, float]] = []

        def recording_borrow(coordinator, *, timeout_seconds):
            calls.append((coordinator, timeout_seconds))
            return original_borrow(
                coordinator,
                timeout_seconds=timeout_seconds,
            )

        with mock.patch.object(
            WorkspaceConversationStorageMutationCoordinator,
            "borrow",
            new=recording_borrow,
        ):
            metadata = services.catalog.create("session_a", "Coordinated")
            services.event_store.append_batch(
                contract_batches(metadata.conversation_id)["proposal"]
            )
            services.catalog.rename(
                "session_a",
                metadata.conversation_id,
                "Renamed",
            )
            services.catalog.archive("session_a", metadata.conversation_id)
            services.catalog.unarchive("session_a", metadata.conversation_id)

        self.assertEqual(len(calls), 5)
        self.assertTrue(
            all(
                coordinator is services.mutation_coordinator and timeout == 30.0
                for coordinator, timeout in calls
            )
        )

    def test_composed_mutation_serializes_with_an_independent_workspace_lease(
        self,
    ) -> None:
        workspace = _initialized_workspace(self)
        services = compose_filesystem_conversation_storage(workspace)
        blocker = WorkspaceConversationStorageMutationCoordinator(workspace)
        attempted = threading.Event()
        completed = threading.Event()
        failures: list[BaseException] = []

        def create() -> None:
            attempted.set()
            try:
                services.catalog.create("session_a", "Serialized")
            except BaseException as exc:
                failures.append(exc)
            finally:
                completed.set()

        with blocker.borrow(timeout_seconds=2.0):
            thread = threading.Thread(target=create)
            thread.start()
            self.assertTrue(attempted.wait(timeout=1.0))
            self.assertFalse(completed.wait(timeout=0.1))
        self.assertTrue(completed.wait(timeout=2.0))
        thread.join(timeout=2.0)
        self.assertFalse(thread.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(len(services.catalog.list("session_a")), 1)

    def test_raw_v2_filesystem_adapters_work_without_bundle_wrapping(
        self,
    ) -> None:
        workspace = _initialized_workspace(self)
        catalog = FilesystemConversationCatalog(workspace)
        event_store = FilesystemCollaborationEventStore(
            workspace,
            catalog=catalog,
        )
        metadata = catalog.create("session_a", "Direct v2 adapter")
        receipt = event_store.append_batch(
            contract_batches(metadata.conversation_id)["proposal"]
        )
        history = event_store.read("session_a", metadata.conversation_id)
        self.assertEqual(receipt.last_sequence, len(history))
        self.assertFalse(
            workspace.path(CONVERSATION_STORAGE_MUTATION_GUARD_PATH).exists()
        )

    def test_services_reject_distinct_provenance_tokens_and_wrong_coordinator(
        self,
    ) -> None:
        workspace = _initialized_workspace(self)
        first = compose_filesystem_conversation_storage(workspace)
        second = compose_filesystem_conversation_storage(workspace)
        self.assertIsNot(first.provenance, second.provenance)
        with self.assertRaises(ConversationStorageServicesError):
            ConversationStorageServices(
                catalog_binding=first.catalog_binding,
                event_store_binding=first.event_store_binding,
                search_binding=second.search_binding,
                mutation_coordinator=first.mutation_coordinator,
            )

        other_workspace = _initialized_workspace(self)
        other = compose_filesystem_conversation_storage(other_workspace)
        with self.assertRaises(ConversationStorageServicesError):
            ConversationStorageServices(
                catalog_binding=first.catalog_binding,
                event_store_binding=first.event_store_binding,
                search_binding=first.search_binding,
                mutation_coordinator=other.mutation_coordinator,
            )

    def test_services_reject_malformed_bindings_and_missing_port_methods(
        self,
    ) -> None:
        workspace = _initialized_workspace(self)
        services = compose_filesystem_conversation_storage(workspace)
        with self.assertRaises(ConversationStorageServicesError):
            ConversationStorageComponentBinding(  # type: ignore[arg-type]
                provenance=object(),
                component=services.catalog,
            )
        bad_catalog = ConversationStorageComponentBinding(
            provenance=services.provenance,
            component=object(),
        )
        with self.assertRaises(ConversationStorageServicesError):
            ConversationStorageServices(  # type: ignore[arg-type]
                catalog_binding=bad_catalog,
                event_store_binding=services.event_store_binding,
                search_binding=services.search_binding,
                mutation_coordinator=services.mutation_coordinator,
            )


class ConversationStorageActivationStateTests(unittest.TestCase):
    def test_missing_state_is_unwritten_filesystem_generation_zero(self) -> None:
        workspace = _initialized_workspace(self)
        before = _namespace_snapshot(workspace.root)

        state = load_conversation_storage_activation_state(workspace)

        self.assertEqual(
            state,
            ConversationStorageActivationState(
                ConversationStorageBackend.FILESYSTEM,
                0,
            ),
        )
        self.assertEqual(_namespace_snapshot(workspace.root), before)
        self.assertFalse(_marker_path(workspace).exists())

    def test_activation_load_uses_one_pinned_workspace_during_root_mutation(
        self,
    ) -> None:
        workspace = _initialized_workspace(self)
        pinned_root = workspace.root
        other = _initialized_workspace(self)
        other_root = other.root
        expected = ConversationStorageActivationState(
            ConversationStorageBackend.SQLITE,
            7,
        )
        workspace.write_json(
            CONVERSATION_STORAGE_ACTIVATION_STATE_PATH,
            expected.to_dict(),
        )
        other_marker = _marker_path(other)
        other_bytes = b'{"decoy":"other activation bytes"}\n'
        other_marker.write_bytes(other_bytes)
        other_before = _namespace_snapshot(other_root)
        original_read_text = Workspace.read_text
        read_workspaces: list[Workspace] = []

        def mutate_during_read(
            operation_workspace: Workspace,
            relative: str | os.PathLike[str],
        ) -> str:
            read_workspaces.append(operation_workspace)
            workspace.root = other_root
            return original_read_text(operation_workspace, relative)

        with mock.patch.object(Workspace, "read_text", new=mutate_during_read):
            loaded = load_conversation_storage_activation_state(workspace)

        self.assertEqual(loaded, expected)
        self.assertEqual(workspace.root, other_root)
        self.assertEqual(len(read_workspaces), 1)
        self.assertIsNot(read_workspaces[0], workspace)
        self.assertEqual(read_workspaces[0].root, pinned_root)
        self.assertEqual(_namespace_snapshot(other_root), other_before)
        self.assertEqual(other_marker.read_bytes(), other_bytes)

    def test_wrong_root_cas_rejects_a_lease_before_workspace_materialization(
        self,
    ) -> None:
        workspace = _initialized_workspace(self)
        other = _initialized_workspace(self)
        other_marker = _marker_path(other)
        other_bytes = b'{"decoy":"wrong root activation bytes"}\n'
        other_marker.write_bytes(other_bytes)
        coordinator = WorkspaceConversationStorageMutationCoordinator(workspace)

        with coordinator.borrow(timeout_seconds=2.0) as lease:
            workspace_before = _namespace_snapshot(workspace.root)
            other_before = _namespace_snapshot(other.root)
            with (
                mock.patch.object(
                    Workspace,
                    "__init__",
                    side_effect=AssertionError("workspace materialized"),
                ) as materialize,
                mock.patch.object(
                    Workspace,
                    "path",
                    side_effect=AssertionError("filesystem path accessed"),
                ) as path,
                mock.patch.object(
                    Workspace,
                    "read_text",
                    side_effect=AssertionError("filesystem read attempted"),
                ) as read_text,
                mock.patch.object(
                    Workspace,
                    "write_json",
                    side_effect=AssertionError("filesystem write attempted"),
                ) as write_json,
            ):
                with self.assertRaises(ConversationStorageCoordinationError) as caught:
                    compare_and_set_conversation_storage_activation(
                        other,
                        lease=lease,
                        expected_generation=0,
                        readiness=_readiness(
                            ConversationStorageBackend.FILESYSTEM,
                            ConversationStorageBackend.SQLITE,
                        ),
                    )

            materialize.assert_not_called()
            path.assert_not_called()
            read_text.assert_not_called()
            write_json.assert_not_called()
            self.assertEqual(
                str(caught.exception),
                "conversation storage failure: coordination_failed",
            )
            self.assertIsNone(caught.exception.__cause__)
            self.assertEqual(_namespace_snapshot(workspace.root), workspace_before)
            self.assertEqual(_namespace_snapshot(other.root), other_before)
            self.assertEqual(other_marker.read_bytes(), other_bytes)

    def test_same_backend_cas_is_an_unwritten_noop(self) -> None:
        workspace = _initialized_workspace(self)
        coordinator = WorkspaceConversationStorageMutationCoordinator(workspace)
        with coordinator.borrow(timeout_seconds=2.0) as lease:
            outcome = compare_and_set_conversation_storage_activation(
                workspace,
                lease=lease,
                expected_generation=0,
                readiness=_readiness(
                    ConversationStorageBackend.FILESYSTEM,
                    ConversationStorageBackend.FILESYSTEM,
                    source_is_empty=False,
                ),
            )
        self.assertIs(
            outcome.kind,
            ConversationStorageActivationOutcomeKind.UNCHANGED,
        )
        self.assertEqual(outcome.previous, outcome.current)
        self.assertFalse(_marker_path(workspace).exists())

    def test_activation_round_trip_is_exact_and_increments_once(self) -> None:
        workspace = _initialized_workspace(self)
        coordinator = WorkspaceConversationStorageMutationCoordinator(workspace)
        with coordinator.borrow(timeout_seconds=2.0) as lease:
            outcome = compare_and_set_conversation_storage_activation(
                workspace,
                lease=lease,
                expected_generation=0,
                readiness=_readiness(
                    ConversationStorageBackend.FILESYSTEM,
                    ConversationStorageBackend.SQLITE,
                ),
            )

        expected = ConversationStorageActivationState(
            ConversationStorageBackend.SQLITE,
            1,
        )
        self.assertIs(
            outcome.kind,
            ConversationStorageActivationOutcomeKind.ACTIVATED,
        )
        self.assertEqual(outcome.previous.generation, 0)
        self.assertEqual(outcome.current, expected)
        expected_bytes = (
            json.dumps(expected.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)
            + "\n"
        ).encode("utf-8")
        self.assertEqual(_marker_path(workspace).read_bytes(), expected_bytes)
        self.assertEqual(
            load_conversation_storage_activation_state(workspace),
            expected,
        )

    def test_activation_cas_pins_read_revalidation_and_write_to_one_root(
        self,
    ) -> None:
        workspace = _initialized_workspace(self)
        pinned_workspace = copy(workspace)
        pinned_root = workspace.root
        other = _initialized_workspace(self)
        other_root = other.root
        current = ConversationStorageActivationState(
            ConversationStorageBackend.FILESYSTEM,
            1,
        )
        workspace.write_json(
            CONVERSATION_STORAGE_ACTIVATION_STATE_PATH,
            current.to_dict(),
        )
        other_marker = _marker_path(other)
        other_bytes = b'{"decoy":"preserve other activation"}\n'
        other_marker.write_bytes(other_bytes)
        other_before = _namespace_snapshot(other_root)
        coordinator = WorkspaceConversationStorageMutationCoordinator(workspace)
        original_read_text = Workspace.read_text
        original_write_json = Workspace.write_json
        read_roots: list[Path] = []
        write_roots: list[Path] = []

        def mutate_during_read(
            operation_workspace: Workspace,
            relative: str | os.PathLike[str],
        ) -> str:
            read_roots.append(operation_workspace.root)
            workspace.root = other_root
            return original_read_text(operation_workspace, relative)

        def record_write(
            operation_workspace: Workspace,
            relative: str | os.PathLike[str],
            value: object,
        ) -> Path:
            write_roots.append(operation_workspace.root)
            return original_write_json(operation_workspace, relative, value)

        with coordinator.borrow(timeout_seconds=2.0) as lease:
            with (
                mock.patch.object(Workspace, "read_text", new=mutate_during_read),
                mock.patch.object(Workspace, "write_json", new=record_write),
            ):
                outcome = compare_and_set_conversation_storage_activation(
                    workspace,
                    lease=lease,
                    expected_generation=1,
                    readiness=_readiness(
                        ConversationStorageBackend.FILESYSTEM,
                        ConversationStorageBackend.SQLITE,
                        generation=1,
                    ),
                )
            lease.validate(workspace_key=os.fspath(pinned_root))
            with self.assertRaises(ConversationStorageCoordinationError):
                lease.validate(workspace_key=os.fspath(other_root))

        expected = ConversationStorageActivationState(
            ConversationStorageBackend.SQLITE,
            2,
        )
        self.assertEqual(outcome.current, expected)
        self.assertEqual(read_roots, [pinned_root])
        self.assertEqual(write_roots, [pinned_root])
        self.assertEqual(
            load_conversation_storage_activation_state(pinned_workspace),
            expected,
        )
        self.assertEqual(_namespace_snapshot(other_root), other_before)
        self.assertEqual(other_marker.read_bytes(), other_bytes)

    def test_cas_rejects_stale_generation_source_generation_and_backend(
        self,
    ) -> None:
        cases = (
            (
                1,
                _readiness(
                    ConversationStorageBackend.FILESYSTEM,
                    ConversationStorageBackend.SQLITE,
                    generation=1,
                ),
            ),
            (
                0,
                _readiness(
                    ConversationStorageBackend.FILESYSTEM,
                    ConversationStorageBackend.SQLITE,
                    generation=1,
                ),
            ),
            (
                0,
                _readiness(
                    ConversationStorageBackend.SQLITE,
                    ConversationStorageBackend.FILESYSTEM,
                    generation=0,
                ),
            ),
        )
        for expected_generation, readiness in cases:
            with self.subTest(
                expected_generation=expected_generation,
                readiness=readiness,
            ):
                workspace = _initialized_workspace(self)
                coordinator = WorkspaceConversationStorageMutationCoordinator(workspace)
                with coordinator.borrow(timeout_seconds=2.0) as lease:
                    with self.assertRaises(ConversationStorageActivationConflictError):
                        compare_and_set_conversation_storage_activation(
                            workspace,
                            lease=lease,
                            expected_generation=expected_generation,
                            readiness=readiness,
                        )
                self.assertFalse(_marker_path(workspace).exists())

    def test_nonempty_switch_requires_verified_matching_parity(self) -> None:
        for parity, error_type in (
            (
                ConversationStorageParity.UNVERIFIED,
                ConversationStorageTransferRequiredError,
            ),
            (ConversationStorageParity.MISMATCH, ConversationStorageParityError),
        ):
            with self.subTest(parity=parity):
                workspace = _initialized_workspace(self)
                coordinator = WorkspaceConversationStorageMutationCoordinator(workspace)
                readiness = _readiness(
                    ConversationStorageBackend.FILESYSTEM,
                    ConversationStorageBackend.SQLITE,
                    source_is_empty=False,
                    parity=parity,
                )
                with coordinator.borrow(timeout_seconds=2.0) as lease:
                    with self.assertRaises(error_type):
                        compare_and_set_conversation_storage_activation(
                            workspace,
                            lease=lease,
                            expected_generation=0,
                            readiness=readiness,
                        )
                self.assertFalse(_marker_path(workspace).exists())

    def test_malformed_state_matrix_fails_closed_without_rewrite(self) -> None:
        malformed = (
            b"\xff",
            b"{",
            b"[]\n",
            b'{"schema_version":1,"backend":"filesystem"}\n',
            (
                b'{"schema_version":1,"backend":"filesystem",'
                b'"generation":0,"extra":true}\n'
            ),
            (
                b'{"schema_version":1,"backend":"filesystem",'
                b'"backend":"sqlite","generation":0}\n'
            ),
            b'{"schema_version":true,"backend":"filesystem","generation":0}\n',
            b'{"schema_version":1,"backend":"mysql","generation":0}\n',
            b'{"schema_version":1,"backend":1,"generation":0}\n',
            b'{"schema_version":1,"backend":"filesystem","generation":true}\n',
            b'{"schema_version":1,"backend":"filesystem","generation":"0"}\n',
            b'{"schema_version":1,"backend":"filesystem","generation":-1}\n',
        )
        for payload in malformed:
            with self.subTest(payload=payload):
                workspace = _initialized_workspace(self)
                marker = _marker_path(workspace)
                marker.write_bytes(payload)
                with self.assertRaises(
                    ConversationStorageActivationStateError
                ) as raised:
                    load_conversation_storage_activation_state(workspace)
                self.assertIs(
                    raised.exception.failure,
                    ConversationStorageFailure.ACTIVATION_STATE_INVALID,
                )
                self.assertEqual(marker.read_bytes(), payload)
                self.assertEqual(_marker_temporaries(workspace), [])

    def test_unsupported_activation_version_has_a_distinct_fixed_failure(
        self,
    ) -> None:
        workspace = _initialized_workspace(self)
        payload = {
            "schema_version": 2,
            "backend": "filesystem",
            "generation": 0,
        }
        marker = _marker_path(workspace)
        marker.write_text(json.dumps(payload) + "\n", encoding="utf-8")

        with self.assertRaises(ConversationStorageActivationVersionError) as raised:
            load_conversation_storage_activation_state(workspace)

        self.assertIs(
            raised.exception.failure,
            ConversationStorageFailure.ACTIVATION_STATE_UNSUPPORTED,
        )
        self.assertEqual(json.loads(marker.read_text(encoding="utf-8")), payload)

    def test_symlinked_activation_marker_is_rejected_without_outside_access(
        self,
    ) -> None:
        workspace = _initialized_workspace(self)
        outside_temporary = tempfile.TemporaryDirectory()
        self.addCleanup(outside_temporary.cleanup)
        outside = Path(outside_temporary.name) / "outside.json"
        outside_bytes = b'{"private":"unchanged"}\n'
        outside.write_bytes(outside_bytes)
        marker = _marker_path(workspace)
        try:
            marker.symlink_to(outside)
        except OSError as exc:
            self.skipTest(f"file symlink creation is unsupported: {exc}")

        with self.assertRaises(ConversationStorageActivationStateError):
            load_conversation_storage_activation_state(workspace)

        self.assertEqual(outside.read_bytes(), outside_bytes)
        self.assertTrue(marker.is_symlink())

    def test_public_write_failure_preserves_old_state_without_temporary(self) -> None:
        workspace = _initialized_workspace(self)
        coordinator = WorkspaceConversationStorageMutationCoordinator(workspace)
        with coordinator.borrow(timeout_seconds=2.0) as lease:
            compare_and_set_conversation_storage_activation(
                workspace,
                lease=lease,
                expected_generation=0,
                readiness=_readiness(
                    ConversationStorageBackend.FILESYSTEM,
                    ConversationStorageBackend.SQLITE,
                ),
            )
        before = _marker_path(workspace).read_bytes()
        readiness = _readiness(
            ConversationStorageBackend.SQLITE,
            ConversationStorageBackend.FILESYSTEM,
            generation=1,
        )

        with coordinator.borrow(timeout_seconds=2.0) as lease:
            with mock.patch.object(
                Workspace,
                "write_json",
                side_effect=OSError("simulated activation write failure"),
                autospec=True,
            ) as write_json:
                with self.assertRaises(ConversationStorageActivationError):
                    compare_and_set_conversation_storage_activation(
                        workspace,
                        lease=lease,
                        expected_generation=1,
                        readiness=readiness,
                    )

        write_json.assert_called_once_with(
            mock.ANY,
            CONVERSATION_STORAGE_ACTIVATION_STATE_PATH,
            ConversationStorageActivationState(
                ConversationStorageBackend.FILESYSTEM,
                2,
            ).to_dict(),
        )
        self.assertEqual(write_json.call_args.args[0].root, workspace.root)
        self.assertEqual(_marker_path(workspace).read_bytes(), before)
        self.assertEqual(_marker_temporaries(workspace), [])
        self.assertEqual(
            load_conversation_storage_activation_state(workspace).generation,
            1,
        )

    def test_public_write_postcommit_failure_leaves_complete_new_state(self) -> None:
        workspace = _initialized_workspace(self)
        coordinator = WorkspaceConversationStorageMutationCoordinator(workspace)
        with coordinator.borrow(timeout_seconds=2.0) as lease:
            compare_and_set_conversation_storage_activation(
                workspace,
                lease=lease,
                expected_generation=0,
                readiness=_readiness(
                    ConversationStorageBackend.FILESYSTEM,
                    ConversationStorageBackend.SQLITE,
                ),
            )
        original_write_json = Workspace.write_json

        def write_then_fail(
            operation_workspace: Workspace,
            relative: str | os.PathLike[str],
            value: object,
        ) -> None:
            original_write_json(operation_workspace, relative, value)
            raise OSError("simulated post-write activation failure")

        with coordinator.borrow(timeout_seconds=2.0) as lease:
            with mock.patch.object(
                Workspace,
                "write_json",
                side_effect=write_then_fail,
                autospec=True,
            ) as write_json:
                with self.assertRaises(ConversationStorageActivationError):
                    compare_and_set_conversation_storage_activation(
                        workspace,
                        lease=lease,
                        expected_generation=1,
                        readiness=_readiness(
                            ConversationStorageBackend.SQLITE,
                            ConversationStorageBackend.FILESYSTEM,
                            generation=1,
                        ),
                    )

        write_json.assert_called_once()
        self.assertEqual(write_json.call_args.args[0].root, workspace.root)
        self.assertEqual(
            load_conversation_storage_activation_state(workspace),
            ConversationStorageActivationState(
                ConversationStorageBackend.FILESYSTEM,
                2,
            ),
        )
        self.assertEqual(_marker_temporaries(workspace), [])


class ConversationStorageCoordinatorTests(unittest.TestCase):
    def test_construction_is_io_free_and_workspace_identity_is_canonical(self) -> None:
        workspace = _initialized_workspace(self)
        before = _namespace_snapshot(workspace.root)

        first = WorkspaceConversationStorageMutationCoordinator(workspace)
        second = WorkspaceConversationStorageMutationCoordinator(
            Workspace(workspace.root)
        )

        self.assertEqual(first.workspace_key, os.fspath(workspace.root))
        self.assertEqual(second.workspace_key, first.workspace_key)
        self.assertEqual(_namespace_snapshot(workspace.root), before)
        self.assertFalse(
            (workspace.root / CONVERSATION_STORAGE_MUTATION_GUARD_PATH).exists()
        )

    def test_borrow_uses_only_pinned_guard_after_root_mutation(self) -> None:
        workspace = _initialized_workspace(self)
        pinned_root = workspace.root
        other = _initialized_workspace(self)
        other_root = other.root
        coordinator = WorkspaceConversationStorageMutationCoordinator(workspace)
        pinned_guard = pinned_root / CONVERSATION_STORAGE_MUTATION_GUARD_PATH
        other_guard = other_root / CONVERSATION_STORAGE_MUTATION_GUARD_PATH
        other_before = _namespace_snapshot(other_root)
        real_open = os.open
        opened: list[Path] = []

        def record_open(
            path: str | os.PathLike[str],
            flags: int,
            mode: int = 0o777,
        ) -> int:
            opened.append(Path(path))
            return real_open(path, flags, mode)

        workspace.root = other_root
        with mock.patch.object(composition_module.os, "open", new=record_open):
            with coordinator.borrow(timeout_seconds=2.0) as lease:
                self.assertEqual(lease.workspace_key, os.fspath(pinned_root))
                lease.validate(workspace_key=os.fspath(pinned_root))
                with self.assertRaises(ConversationStorageCoordinationError) as caught:
                    lease.validate(workspace_key=os.fspath(other_root))

        workspace.root = pinned_root

        def mutate_during_open(
            path: str | os.PathLike[str],
            flags: int,
            mode: int = 0o777,
        ) -> int:
            opened.append(Path(path))
            workspace.root = other_root
            return real_open(path, flags, mode)

        with mock.patch.object(composition_module.os, "open", new=mutate_during_open):
            with coordinator.borrow(timeout_seconds=2.0) as lease:
                self.assertEqual(lease.workspace_key, os.fspath(pinned_root))
                lease.validate(workspace_key=os.fspath(pinned_root))

        self.assertEqual(opened, [pinned_guard, pinned_guard])
        self.assertEqual(
            str(caught.exception),
            "conversation storage failure: coordination_failed",
        )
        self.assertIsNone(caught.exception.__cause__)
        self.assertTrue(pinned_guard.is_file())
        self.assertFalse(other_guard.exists())
        self.assertEqual(_namespace_snapshot(other_root), other_before)

    def test_same_thread_nested_borrows_share_one_lease_and_expiry(self) -> None:
        workspace = _initialized_workspace(self)
        first = WorkspaceConversationStorageMutationCoordinator(workspace)
        second = WorkspaceConversationStorageMutationCoordinator(
            Workspace(workspace.root)
        )

        with first.borrow(timeout_seconds=2.0) as outer:
            self.assertTrue(outer.active)
            outer.validate(workspace_key=first.workspace_key)
            with second.borrow(timeout_seconds=1.0) as nested:
                self.assertIs(nested, outer)
                self.assertEqual(
                    nested.expires_at_monotonic,
                    outer.expires_at_monotonic,
                )
            self.assertTrue(outer.active)
        self.assertFalse(outer.active)
        with self.assertRaises(ConversationStorageCoordinationError):
            outer.validate(workspace_key=first.workspace_key)

    def test_wrong_workspace_thread_and_expired_lease_fail_closed(self) -> None:
        workspace = _initialized_workspace(self)
        other = _initialized_workspace(self)
        coordinator = WorkspaceConversationStorageMutationCoordinator(workspace)
        failures: list[BaseException] = []

        with coordinator.borrow(timeout_seconds=2.0) as lease:
            with self.assertRaises(ConversationStorageCoordinationError):
                lease.validate(workspace_key=os.fspath(other.root))

            def validate_from_wrong_thread() -> None:
                try:
                    lease.validate(workspace_key=coordinator.workspace_key)
                except BaseException as exc:
                    failures.append(exc)

            thread = threading.Thread(target=validate_from_wrong_thread)
            thread.start()
            thread.join(timeout=2.0)
            self.assertFalse(thread.is_alive())
            self.assertEqual(len(failures), 1)
            self.assertIsInstance(failures[0], ConversationStorageCoordinationError)

            with mock.patch.object(
                composition_module.time,
                "monotonic",
                return_value=lease.expires_at_monotonic,
            ):
                self.assertFalse(lease.active)
                with self.assertRaises(ConversationStorageCoordinationError):
                    lease.validate(workspace_key=coordinator.workspace_key)

    def test_body_exception_releases_resources_for_next_borrow(self) -> None:
        workspace = _initialized_workspace(self)
        coordinator = WorkspaceConversationStorageMutationCoordinator(workspace)

        with self.assertRaisesRegex(RuntimeError, "body failure"):
            with coordinator.borrow(timeout_seconds=2.0) as lease:
                lease.validate(workspace_key=coordinator.workspace_key)
                raise RuntimeError("body failure")

        with coordinator.borrow(timeout_seconds=2.0) as recovered:
            recovered.validate(workspace_key=coordinator.workspace_key)
            self.assertTrue(recovered.active)

    def test_same_workspace_coordinators_serialize_threads(self) -> None:
        workspace = _initialized_workspace(self)
        first = WorkspaceConversationStorageMutationCoordinator(workspace)
        second = WorkspaceConversationStorageMutationCoordinator(
            Workspace(workspace.root)
        )
        attempted = threading.Event()
        entered = threading.Event()
        failures: list[BaseException] = []

        def borrow_second() -> None:
            attempted.set()
            try:
                with second.borrow(timeout_seconds=2.0):
                    entered.set()
            except BaseException as exc:
                failures.append(exc)

        with first.borrow(timeout_seconds=2.0):
            thread = threading.Thread(target=borrow_second)
            thread.start()
            self.assertTrue(attempted.wait(timeout=1.0))
            self.assertFalse(entered.wait(timeout=0.1))
        self.assertTrue(entered.wait(timeout=2.0))
        thread.join(timeout=2.0)
        self.assertFalse(thread.is_alive())
        self.assertEqual(failures, [])

    def test_same_workspace_contention_times_out_and_recovers(self) -> None:
        workspace = _initialized_workspace(self)
        first = WorkspaceConversationStorageMutationCoordinator(workspace)
        second = WorkspaceConversationStorageMutationCoordinator(
            Workspace(workspace.root)
        )
        attempted = threading.Event()
        finished = threading.Event()
        failures: list[BaseException] = []

        def contend() -> None:
            attempted.set()
            try:
                with second.borrow(timeout_seconds=0.05):
                    failures.append(AssertionError("contended borrow entered"))
            except BaseException as exc:
                failures.append(exc)
            finally:
                finished.set()

        with first.borrow(timeout_seconds=2.0):
            thread = threading.Thread(target=contend)
            thread.start()
            self.assertTrue(attempted.wait(timeout=1.0))
            self.assertTrue(finished.wait(timeout=1.0))
            self.assertEqual(len(failures), 1)
            self.assertIsInstance(
                failures[0],
                ConversationStorageCoordinationError,
            )
            self.assertIs(
                failures[0].failure,  # type: ignore[union-attr]
                ConversationStorageFailure.COORDINATION_FAILED,
            )
        thread.join(timeout=2.0)
        self.assertFalse(thread.is_alive())

        with second.borrow(timeout_seconds=1.0) as recovered:
            recovered.validate(workspace_key=second.workspace_key)

    def test_different_workspaces_do_not_share_exclusion(self) -> None:
        first_workspace = _initialized_workspace(self)
        second_workspace = _initialized_workspace(self)
        first = WorkspaceConversationStorageMutationCoordinator(first_workspace)
        second = WorkspaceConversationStorageMutationCoordinator(second_workspace)
        entered = threading.Event()
        release = threading.Event()
        failures: list[BaseException] = []

        def borrow_second() -> None:
            try:
                with second.borrow(timeout_seconds=2.0):
                    entered.set()
                    release.wait(timeout=2.0)
            except BaseException as exc:
                failures.append(exc)

        with first.borrow(timeout_seconds=2.0):
            thread = threading.Thread(target=borrow_second)
            thread.start()
            self.assertTrue(entered.wait(timeout=1.0))
            release.set()
            thread.join(timeout=2.0)
            self.assertFalse(thread.is_alive())
        self.assertEqual(failures, [])

    def test_symlinked_guard_fails_closed_and_releases_local_exclusion(self) -> None:
        workspace = _initialized_workspace(self)
        outside_temporary = tempfile.TemporaryDirectory()
        self.addCleanup(outside_temporary.cleanup)
        outside = Path(outside_temporary.name) / "outside.lock"
        outside_bytes = b"outside guard bytes"
        outside.write_bytes(outside_bytes)
        guard = workspace.root / CONVERSATION_STORAGE_MUTATION_GUARD_PATH
        try:
            guard.symlink_to(outside)
        except OSError as exc:
            self.skipTest(f"file symlink creation is unsupported: {exc}")
        coordinator = WorkspaceConversationStorageMutationCoordinator(workspace)

        with self.assertRaises(ConversationStorageCoordinationError):
            with coordinator.borrow(timeout_seconds=1.0):
                self.fail("unsafe guard borrow entered")

        self.assertEqual(outside.read_bytes(), outside_bytes)
        self.assertTrue(guard.is_symlink())
        guard.unlink()
        with coordinator.borrow(timeout_seconds=1.0) as lease:
            lease.validate(workspace_key=coordinator.workspace_key)

    @unittest.skipUnless(os.name == "posix", "requires POSIX advisory locking")
    def test_public_borrow_serializes_independent_processes_and_recovers(
        self,
    ) -> None:
        workspace = _initialized_workspace(self)
        repository_root = Path(__file__).resolve().parents[1]
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(repository_root / "src")
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        script = """
import sys
from story_writing_agents import Workspace
from story_writing_agents.conversation_storage import (
    WorkspaceConversationStorageMutationCoordinator,
)

coordinator = WorkspaceConversationStorageMutationCoordinator(Workspace(sys.argv[1]))
with coordinator.borrow(timeout_seconds=5.0) as lease:
    lease.validate(workspace_key=coordinator.workspace_key)
    print("locked", flush=True)
    sys.stdin.read(1)
print("released", flush=True)
"""
        process = subprocess.Popen(
            [sys.executable, "-c", script, str(workspace.root)],
            cwd=repository_root,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        def cleanup_process() -> None:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5.0)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()

        self.addCleanup(cleanup_process)
        assert process.stdout is not None
        self.assertEqual(process.stdout.readline(), "locked\n")

        coordinator = WorkspaceConversationStorageMutationCoordinator(workspace)
        with self.assertRaises(ConversationStorageCoordinationError) as timed_out:
            with coordinator.borrow(timeout_seconds=0.05):
                self.fail("cross-process contended borrow entered")
        self.assertIs(
            timed_out.exception.failure,
            ConversationStorageFailure.COORDINATION_FAILED,
        )

        attempted = threading.Event()
        entered = threading.Event()
        failures: list[BaseException] = []

        def borrow_in_parent() -> None:
            attempted.set()
            try:
                with coordinator.borrow(timeout_seconds=3.0) as lease:
                    lease.validate(workspace_key=coordinator.workspace_key)
                    entered.set()
            except BaseException as exc:
                failures.append(exc)

        thread = threading.Thread(target=borrow_in_parent)
        thread.start()
        self.assertTrue(attempted.wait(timeout=1.0))
        self.assertFalse(entered.wait(timeout=0.15))
        assert process.stdin is not None
        process.stdin.write("x")
        process.stdin.flush()

        self.assertTrue(entered.wait(timeout=3.0))
        thread.join(timeout=3.0)
        self.assertFalse(thread.is_alive())
        process.wait(timeout=5.0)
        stderr = process.stderr.read() if process.stderr is not None else ""
        self.assertEqual(process.returncode, 0, stderr)
        self.assertEqual(process.stdout.readline(), "released\n")
        self.assertEqual(failures, [])

        with coordinator.borrow(timeout_seconds=1.0) as recovered:
            recovered.validate(workspace_key=coordinator.workspace_key)


class ConversationStorageBoundaryTests(unittest.TestCase):
    def test_package_exports_owning_modules_and_root_facade_are_exact(self) -> None:
        self.assertEqual(
            filesystem_module.__all__,
            [
                "FilesystemConversationCatalog",
                "FilesystemCollaborationEventStore",
            ],
        )
        self.assertEqual(
            composition_module.__all__,
            [
                "CONVERSATION_STORAGE_ACTIVATION_STATE_PATH",
                "CONVERSATION_STORAGE_MUTATION_GUARD_PATH",
                "WorkspaceConversationStorageMutationCoordinator",
                "compare_and_set_conversation_storage_activation",
                "compose_filesystem_conversation_storage",
                "load_conversation_storage_activation_state",
            ],
        )
        owning_modules = (
            archive_module,
            contracts_module,
            composition_module,
            filesystem_archive_module,
        )
        expected = set().union(*(module.__all__ for module in owning_modules))
        self.assertEqual(set(storage.__all__), expected)
        self.assertEqual(len(archive_module.__all__), 21)
        self.assertEqual(len(contracts_module.__all__), 34)
        self.assertEqual(len(storage.__all__), 63)
        for old_name in (
            "ConversationArchiveEvent",
            "ConversationStorageMigrationReadiness",
            "ConversationStorageMigrationRequiredError",
        ):
            self.assertFalse(hasattr(storage, old_name))
            self.assertFalse(hasattr(contracts_module, old_name))
            self.assertNotIn(old_name, storage.__all__)
        for module in owning_modules:
            for name in module.__all__:
                with self.subTest(module=module.__name__, name=name):
                    self.assertIs(getattr(storage, name), getattr(module, name))
        self.assertEqual(len(story_writing_agents.__all__), 106)
        for name in filesystem_module.__all__:
            with self.subTest(filesystem_owner_name=name):
                self.assertNotIn(name, storage.__all__)
                self.assertFalse(hasattr(storage, name))
        self.assertIn("FilesystemConversationCatalog", story_writing_agents.__all__)
        self.assertIs(
            story_writing_agents.FilesystemConversationCatalog,
            filesystem_module.FilesystemConversationCatalog,
        )
        self.assertNotIn(
            "FilesystemCollaborationEventStore",
            story_writing_agents.__all__,
        )
        self.assertFalse(
            hasattr(story_writing_agents, "FilesystemCollaborationEventStore")
        )
        for name in expected:
            with self.subTest(root_name=name):
                self.assertNotIn(name, story_writing_agents.__all__)
                self.assertFalse(hasattr(story_writing_agents, name))

    def test_filesystem_classes_are_defined_only_by_the_owner_module(self) -> None:
        package_root = Path(filesystem_module.__file__).parents[1]
        owner_names = set(filesystem_module.__all__)
        definition_files: dict[str, set[str]] = {}
        for source_path in sorted(package_root.rglob("*.py")):
            tree = ast.parse(
                source_path.read_text(encoding="utf-8"),
                filename=os.fspath(source_path),
            )
            definitions = {
                node.name
                for node in tree.body
                if isinstance(node, ast.ClassDef) and node.name in owner_names
            }
            if definitions:
                definition_files[source_path.relative_to(package_root).as_posix()] = (
                    definitions
                )
        self.assertEqual(
            definition_files,
            {
                "conversation_storage/filesystem.py": {
                    "FilesystemConversationCatalog",
                    "FilesystemCollaborationEventStore",
                }
            },
        )

    def test_internal_dependency_direction_is_minimal_and_acyclic(self) -> None:
        expected_dependencies = {
            archive_module: {"conversations", "errors"},
            contracts_module: {"conversation_search", "conversations", "errors"},
            composition_module: {
                "conversation_search",
                "workspace.core",
                "filesystem",
                "contracts",
            },
            filesystem_module: {
                "conversations",
                "conversations.events",
                "errors",
                "workspace.core",
            },
            filesystem_archive_module: {
                "archive",
                "contracts",
                "filesystem",
                "conversations",
                "errors",
                "workspace.core",
            },
        }
        for module, expected in expected_dependencies.items():
            source = Path(module.__file__).read_text(encoding="utf-8")
            tree = ast.parse(source)
            dependencies = {
                node.module
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.level > 0
            }
            with self.subTest(module=module.__name__):
                self.assertEqual(dependencies, expected)
                self.assertFalse(
                    any(
                        isinstance(node, ast.ImportFrom) and node.names[0].name == "*"
                        for node in ast.walk(tree)
                        if isinstance(node, ast.ImportFrom)
                    )
                )
        contracts_dependencies = expected_dependencies[contracts_module]
        self.assertNotIn("composition", contracts_dependencies)

    def test_package_imports_cleanly_without_io_or_optional_sqlite_loading(
        self,
    ) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        source_root = repository_root / "src"
        script = (
            "import sys; sys.dont_write_bytecode=True; "
            f"sys.path.insert(0, {str(source_root)!r}); "
            "from pathlib import Path; "
            "import story_writing_agents as root; "
            "import story_writing_agents.conversation_storage as package; "
            "import story_writing_agents.conversation_storage.archive as archive; "
            "import story_writing_agents.conversation_storage.contracts as contracts; "
            "import story_writing_agents.conversation_storage.composition "
            "as composition; "
            "import story_writing_agents.conversation_storage.filesystem "
            "as filesystem; "
            "import story_writing_agents.conversation_storage.filesystem_archive "
            "as filesystem_archive; "
            "expected=(set(archive.__all__) | set(contracts.__all__) | "
            "set(composition.__all__) | set(filesystem_archive.__all__)); "
            "assert set(package.__all__) == expected; "
            "assert len(archive.__all__) == 21; "
            "assert len(filesystem_archive.__all__) == 2; "
            "assert filesystem.__all__ == "
            "['FilesystemConversationCatalog', "
            "'FilesystemCollaborationEventStore']; "
            "assert len(contracts.__all__) == 34; "
            "assert len(package.__all__) == 63; "
            "assert all(name not in package.__all__ for name in filesystem.__all__); "
            "assert all(not hasattr(package, name) for name in filesystem.__all__); "
            "assert all(getattr(root, name) is getattr(filesystem, name) "
            "for name in filesystem.__all__[:1]); "
            "assert not hasattr(root, 'FilesystemCollaborationEventStore'); "
            "assert len(root.__all__) == 106; "
            "assert all(name not in root.__all__ for name in expected); "
            "assert all(not hasattr(root, name) for name in expected); "
            "assert 'sqlite3' not in sys.modules; "
            "assert list(Path.cwd().iterdir()) == []; "
            "print('ok')"
        )
        with tempfile.TemporaryDirectory() as temporary:
            completed = subprocess.run(
                [sys.executable, "-I", "-c", script],
                cwd=temporary,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout, "ok\n")
            self.assertEqual(list(Path(temporary).iterdir()), [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
