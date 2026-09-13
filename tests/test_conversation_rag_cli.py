"""Fixed local conversation-RAG command domain and CLI verification."""

from __future__ import annotations

import contextlib
import io
import json
import unittest
from dataclasses import FrozenInstanceError, fields, replace
from importlib import metadata
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from story_writing_agents import cli as cli_module
from story_writing_agents.cli_context import save_default_session
from story_writing_agents.config import (
    ConfigurationCommitError,
    ConfigurationCommitFailure,
    ConfigurationSnapshot,
    ConfigManager,
)
from story_writing_agents.conversation_rag import (
    ConversationRagDocumentIdentity,
    ConversationRagDocumentKey,
    ConversationRagOutcome,
    ConversationRagReason,
    ConversationRagSourceReference,
)
from story_writing_agents import conversation_rag_cli as rag_cli
from story_writing_agents.conversation_rag_cli import (
    CONVERSATION_RAG_CLI_SCHEMA_VERSION,
    ConversationRagCliFailure,
    ConversationRagCliOperation,
    ConversationRagCliReport,
    ConversationRagCliRequest,
    ConversationRagCliState,
    dispatch_conversation_rag,
)
from story_writing_agents.conversation_rag_composition import (
    ConversationRagCompositionError,
    ConversationRagCompositionFailure,
)
from story_writing_agents.conversation_rag_runtime import (
    ConversationRagRuntimeInspection,
    ConversationRagRuntimeInspectionStatus,
)
from story_writing_agents.conversation_rag_store import (
    ConversationRagGenerationStore,
    ConversationRagStoreCounts,
    ConversationRagStoreError,
    ConversationRagStoreFailure,
    ConversationRagStoreStatus,
)
from story_writing_agents.conversation_retrieval import (
    ConversationRagAttemptOutcome,
    ConversationRagContext,
    ConversationRagExcerpt,
    ConversationRetrievalSource,
)
from story_writing_agents.conversation_search import CollaborationHistoryCitation
from story_writing_agents.conversation_storage import (
    ConversationStorageCoordinationError,
    ConversationStorageUnavailableError,
    compose_filesystem_conversation_storage,
)
from story_writing_agents.conversations import (
    CollaborationEventBatch,
    CollaborationReadableFieldKey,
)
from story_writing_agents.errors import ConfigurationError, SessionIdError

from tests.collaboration_contract_fixture import contract_batches
from tests.helpers import initialized_workspace


_SESSION = "rag_cli_session"
_CHECKPOINT = "sha256:" + "1" * 64
_INDEXED_CHECKPOINT = "sha256:" + "2" * 64


def _set_rag_configuration(
    workspace: object,
    *,
    provider: str | None,
    automatic: bool = False,
) -> None:
    manager = ConfigManager(workspace)  # type: ignore[arg-type]
    baseline = manager.load_configuration_baseline()
    manager.commit_configuration(
        baseline,
        ConfigurationSnapshot(
            replace(
                baseline.configuration.system_defaults,
                conversation_rag_provider=provider,
                conversation_rag_automatic=automatic,
            ),
            baseline.configuration.agent_configs,
        ),
    )


def _inspection(
    status: ConversationRagRuntimeInspectionStatus,
) -> ConversationRagRuntimeInspection:
    return ConversationRagRuntimeInspection(
        session_id=_SESSION,
        status=status,
        authoritative_checkpoint=_CHECKPOINT,
        indexed_checkpoint=(
            _INDEXED_CHECKPOINT
            if status is ConversationRagRuntimeInspectionStatus.READY
            else None
        ),
        authoritative_conversations=0,
        authoritative_events=0,
        authoritative_documents=0,
        authoritative_source_utf8_bytes=0,
        indexed_documents=0,
        rebuild_required=status is not ConversationRagRuntimeInspectionStatus.READY,
    )


def _cli_excerpt(content: str = "Alpha") -> rag_cli.ConversationRagCliExcerpt:
    return rag_cli.ConversationRagCliExcerpt(
        citation_id="g1",
        session_id=_SESSION,
        conversation_id="conversation-a",
        event_id="proposal_event",
        sequence=1,
        field_key=CollaborationReadableFieldKey.PROPOSAL_PURPOSE.value,
        field_start_codepoint=0,
        field_end_codepoint=len(content),
        assignment_ordinal=None,
        input_ordinal=None,
        document_id="document-a",
        revision_id="sha256:" + "3" * 64,
        fragment_id="sha256:" + "4" * 64,
        start_codepoint=0,
        end_codepoint=len(content),
        content=content,
    )


def _context(content: str) -> ConversationRagContext:
    identity = ConversationRagDocumentIdentity(
        ConversationRagDocumentKey(_SESSION, "document-a"),
        "conversation-a",
        "turn-a",
        "turn-a",
        1,
        2,
        "sha256:" + "3" * 64,
    )
    excerpt = ConversationRagExcerpt(
        citation_id="g1",
        source=ConversationRetrievalSource(
            citation=CollaborationHistoryCitation(
                session_id=_SESSION,
                conversation_id="conversation-a",
                event_id="proposal_event",
                sequence=1,
                field_key=CollaborationReadableFieldKey.PROPOSAL_PURPOSE,
                start_codepoint=0,
                end_codepoint=len(content),
            ),
        ),
        reference=ConversationRagSourceReference(
            identity,
            "sha256:" + "4" * 64,
            0,
            len(content),
        ),
        content=content,
    )
    return ConversationRagContext(
        outcome=ConversationRagAttemptOutcome(
            outcome=ConversationRagOutcome.COMPLETE,
            reason=ConversationRagReason.NONE,
        ),
        excerpts=(excerpt,),
        retained_utf8_bytes=len(content.encode("utf-8")),
        truncated=False,
    )


def _append_proposal(services: object, conversation_id: str):  # type: ignore[no-untyped-def]
    batch = contract_batches(conversation_id)["proposal"]
    batch = CollaborationEventBatch(
        batch_id=batch.batch_id,
        expected_last_sequence=batch.expected_last_sequence,
        events=tuple(replace(event, session_id=_SESSION) for event in batch.events),
    )
    services.event_store.append_batch(batch)
    return services.event_store.read(_SESSION, conversation_id)


def _tree_bytes(root: Path) -> tuple[tuple[str, str, bytes | None], ...]:
    result: list[tuple[str, str, bytes | None]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        result.append(
            (relative, "directory", None)
            if path.is_dir()
            else (relative, "file", path.read_bytes())
        )
    return tuple(result)


class ConversationRagCliContractTests(unittest.TestCase):
    def test_exports_enums_fields_freezing_and_request_repr_are_exact(self) -> None:
        self.assertEqual(CONVERSATION_RAG_CLI_SCHEMA_VERSION, 1)
        self.assertEqual(
            tuple(rag_cli.__all__),
            (
                "CONVERSATION_RAG_CLI_SCHEMA_VERSION",
                "ConversationRagCliFailure",
                "ConversationRagCliOperation",
                "ConversationRagCliReport",
                "ConversationRagCliRequest",
                "ConversationRagCliState",
                "dispatch_conversation_rag",
            ),
        )
        self.assertEqual(
            tuple(item.value for item in ConversationRagCliOperation),
            ("status", "enable", "rebuild", "search", "disable", "purge"),
        )
        self.assertEqual(
            tuple(item.value for item in ConversationRagCliState),
            (
                "disabled",
                "unavailable",
                "missing",
                "ready",
                "stale",
                "failed",
                "corrupt",
                "incompatible",
            ),
        )
        self.assertEqual(
            tuple(field.name for field in fields(ConversationRagCliRequest)),
            ("operation", "session_id", "provider", "query", "confirmed"),
        )
        query = "PRIVATE_QUERY_CREDENTIAL"
        request = ConversationRagCliRequest(
            ConversationRagCliOperation.SEARCH,
            session_id=_SESSION,
            query=query,
        )
        self.assertNotIn(query, repr(request))
        self.assertFalse(hasattr(request, "__dict__"))
        with self.assertRaises(FrozenInstanceError):
            request.query = "changed"  # type: ignore[misc]

    def test_request_shapes_reject_unsafe_or_inapplicable_values_before_io(
        self,
    ) -> None:
        valid = (
            ConversationRagCliRequest(ConversationRagCliOperation.STATUS, _SESSION),
            ConversationRagCliRequest(
                ConversationRagCliOperation.ENABLE, provider="local"
            ),
            ConversationRagCliRequest(ConversationRagCliOperation.REBUILD, _SESSION),
            ConversationRagCliRequest(
                ConversationRagCliOperation.SEARCH, _SESSION, query="needle"
            ),
            ConversationRagCliRequest(ConversationRagCliOperation.DISABLE),
            ConversationRagCliRequest(
                ConversationRagCliOperation.PURGE, _SESSION, confirmed=True
            ),
        )
        self.assertEqual(len(valid), 6)
        invalid = (
            lambda: ConversationRagCliRequest("status", _SESSION),  # type: ignore[arg-type]
            lambda: ConversationRagCliRequest(ConversationRagCliOperation.STATUS),
            lambda: ConversationRagCliRequest(
                ConversationRagCliOperation.STATUS, "../escape"
            ),
            lambda: ConversationRagCliRequest(
                ConversationRagCliOperation.ENABLE, provider="remote"
            ),
            lambda: ConversationRagCliRequest(
                ConversationRagCliOperation.DISABLE, session_id=_SESSION
            ),
            lambda: ConversationRagCliRequest(
                ConversationRagCliOperation.SEARCH, _SESSION, query=""
            ),
            lambda: ConversationRagCliRequest(
                ConversationRagCliOperation.SEARCH, _SESSION, query=" \t\n"
            ),
            lambda: ConversationRagCliRequest(
                ConversationRagCliOperation.SEARCH, _SESSION, query="\ud800"
            ),
            lambda: ConversationRagCliRequest(
                ConversationRagCliOperation.STATUS, _SESSION, query="secret"
            ),
            lambda: ConversationRagCliRequest(
                ConversationRagCliOperation.ENABLE,
                provider="local",
                confirmed=True,
            ),
            lambda: ConversationRagCliRequest(
                ConversationRagCliOperation.PURGE, _SESSION, confirmed=1
            ),
        )
        for construct in invalid:
            with (
                self.subTest(construct=construct),
                self.assertRaises(
                    (TypeError, ValueError, ConfigurationError, SessionIdError)
                ),
            ):
                construct()

    def test_counts_excerpts_and_reports_reject_malformed_direct_construction(
        self,
    ) -> None:
        empty = rag_cli.ConversationRagCliCounts()
        self.assertEqual(
            (
                empty.authoritative_events,
                empty.authoritative_documents,
                empty.indexed_events,
                empty.indexed_documents,
            ),
            (0, 0, 0, 0),
        )
        nonempty_values = {
            "authoritative_conversations": 1,
            "authoritative_events": 1,
            "authoritative_documents": 1,
            "authoritative_source_utf8_bytes": 1,
            "indexed_conversations": 1,
            "indexed_events": 1,
            "indexed_documents": 1,
            "indexed_fragments": 1,
            "indexed_source_utf8_bytes": 1,
            "indexed_payload_bytes": 1,
            "indexed_generation_bytes": 1,
            "indexed_generations": 1,
        }
        nonempty = rag_cli.ConversationRagCliCounts(**nonempty_values)
        self.assertEqual(
            (
                nonempty.authoritative_events,
                nonempty.authoritative_documents,
                nonempty.indexed_events,
                nonempty.indexed_documents,
            ),
            (1, 1, 1, 1),
        )

        invalid_counts = (
            ("negative", {"authoritative_conversations": -1}),
            ("boolean", {"authoritative_conversations": True}),
            ("non_integer", {"authoritative_conversations": 1.0}),
            (
                "conversation_budget",
                {
                    "authoritative_conversations": (
                        rag_cli.CONVERSATION_RAG_POLICY.max_conversations + 1
                    )
                },
            ),
            (
                "event_budget",
                {
                    "authoritative_conversations": 1,
                    "authoritative_events": rag_cli.CONVERSATION_RAG_POLICY.max_events
                    + 1,
                    "authoritative_documents": rag_cli.CONVERSATION_RAG_POLICY.max_events
                    + 1,
                    "authoritative_source_utf8_bytes": 1,
                },
            ),
            (
                "source_byte_budget",
                {
                    "authoritative_source_utf8_bytes": (
                        rag_cli.CONVERSATION_RAG_RUNTIME_POLICY.max_projected_source_utf8_bytes
                        + 1
                    )
                },
            ),
            (
                "authoritative_unequal",
                {
                    "authoritative_conversations": 1,
                    "authoritative_events": 2,
                    "authoritative_documents": 1,
                    "authoritative_source_utf8_bytes": 1,
                },
            ),
            (
                "authoritative_missing_conversation",
                {
                    "authoritative_events": 1,
                    "authoritative_documents": 1,
                    "authoritative_source_utf8_bytes": 1,
                },
            ),
            (
                "authoritative_empty_with_bytes",
                {"authoritative_source_utf8_bytes": 1},
            ),
            (
                "authoritative_nonempty_without_bytes",
                {
                    "authoritative_conversations": 1,
                    "authoritative_events": 1,
                    "authoritative_documents": 1,
                },
            ),
            ("indexed_empty_with_conversation", {"indexed_conversations": 1}),
            (
                "indexed_missing_conversation",
                {
                    **nonempty_values,
                    "indexed_conversations": 0,
                },
            ),
            (
                "indexed_conversations_exceed_documents",
                {
                    **nonempty_values,
                    "indexed_conversations": 2,
                },
            ),
            (
                "indexed_unequal",
                {
                    **nonempty_values,
                    "indexed_events": 2,
                },
            ),
            (
                "indexed_missing_fragment",
                {
                    **nonempty_values,
                    "indexed_fragments": 0,
                },
            ),
            (
                "indexed_missing_source_bytes",
                {
                    **nonempty_values,
                    "indexed_source_utf8_bytes": 0,
                },
            ),
            (
                "indexed_missing_generation",
                {
                    **nonempty_values,
                    "indexed_generations": 0,
                    "indexed_payload_bytes": 0,
                    "indexed_generation_bytes": 0,
                },
            ),
            (
                "indexed_payload_exceeds_generation",
                {
                    **nonempty_values,
                    "indexed_payload_bytes": 2,
                },
            ),
            (
                "indexed_generation_bytes_without_generation",
                {"indexed_generation_bytes": 1},
            ),
        )
        for label, values in invalid_counts:
            with self.subTest(label=label), self.assertRaises(ValueError):
                rag_cli.ConversationRagCliCounts(**values)

        excerpt = _cli_excerpt()
        malformed_excerpts = (
            lambda: replace(excerpt, session_id="../escape"),
            lambda: replace(excerpt, field_key="not_a_readable_field"),
            lambda: replace(excerpt, sequence=-1),
            lambda: replace(excerpt, start_codepoint=5, end_codepoint=1),
            lambda: replace(excerpt, end_codepoint=4),
            lambda: replace(excerpt, content="\ud800"),
        )
        for construct in malformed_excerpts:
            with (
                self.subTest(construct=construct),
                self.assertRaises((TypeError, ValueError, SessionIdError)),
            ):
                construct()

    def test_report_json_is_sorted_compact_stable_and_query_free(self) -> None:
        excerpt = _cli_excerpt("Alpha")
        report = ConversationRagCliReport(
            operation=ConversationRagCliOperation.SEARCH,
            outcome="completed",
            state=ConversationRagCliState.READY,
            provider="local",
            automatic=False,
            compatibility="compatible",
            retrieval_outcome="complete",
            retrieval_reason="none",
            truncated=False,
            excerpts=(excerpt,),
        )
        encoded = report.to_json()
        self.assertEqual(
            encoded,
            json.dumps(
                report.to_dict(),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
        )
        self.assertNotIn("PRIVATE_QUERY", encoded)
        self.assertNotIn("PRIVATE_PROVIDER_SUBJECT", encoded)
        self.assertEqual(report.exit_code, 0)
        self.assertEqual(
            set(report.to_dict()),
            {
                "automatic",
                "authoritative_checkpoint",
                "compatibility",
                "configuration",
                "counts",
                "excerpts",
                "failure",
                "indexed_checkpoint",
                "operation",
                "outcome",
                "provider",
                "purged",
                "rebuild_required",
                "retrieval_outcome",
                "retrieval_reason",
                "schema_version",
                "state",
                "truncated",
            },
        )
        failures = (
            dict(authoritative_checkpoint="not-a-checkpoint"),
            dict(retrieval_outcome="arbitrary", retrieval_reason="arbitrary"),
            dict(excerpts=(excerpt,)),
            dict(configuration="saved"),
        )
        for changes in failures:
            values = {
                "operation": ConversationRagCliOperation.STATUS,
                "outcome": "completed",
                "state": ConversationRagCliState.DISABLED,
                "automatic": False,
                "compatibility": "compatible",
                "authoritative_checkpoint": _CHECKPOINT,
                "rebuild_required": True,
            }
            values.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                ConversationRagCliReport(**values)  # type: ignore[arg-type]
        failed = ConversationRagCliReport.failed(
            ConversationRagCliOperation.SEARCH,
            ConversationRagCliFailure.OPERATION_FAILED,
        )
        self.assertEqual(failed.exit_code, 2)
        self.assertEqual(failed.excerpts, ())
        self.assertNotIn("PRIVATE", failed.to_json())


class ConversationRagCliWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = initialized_workspace(self)
        self.services = compose_filesystem_conversation_storage(self.workspace)

    def test_fresh_status_is_successful_content_free_and_truly_read_only(self) -> None:
        lock = self.workspace.path("config/.conversation_storage.lock")
        rag_root = self.workspace.path(
            "agent_states/orchestrator_state/conversation_rag"
        )
        self.assertFalse(lock.exists())
        self.assertFalse(rag_root.exists())
        before = _tree_bytes(self.workspace.root)
        with mock.patch.object(
            rag_cli,
            "inspect_conversation_rag_compatibility",
            return_value=ConversationRagCompositionFailure.DEPENDENCY_MISSING,
        ) as compatibility:
            report = dispatch_conversation_rag(
                self.workspace,
                ConversationRagCliRequest(ConversationRagCliOperation.STATUS, _SESSION),
                conversation_storage_services=self.services,
            )
        compatibility.assert_called_once_with("local")
        self.assertEqual(report.exit_code, 0)
        self.assertIs(report.state, ConversationRagCliState.DISABLED)
        self.assertIsNone(report.provider)
        self.assertEqual(report.compatibility, "dependency_missing")
        self.assertEqual(report.counts.to_dict()["authoritative"]["documents"], 0)  # type: ignore[index]
        self.assertEqual(_tree_bytes(self.workspace.root), before)
        self.assertFalse(lock.exists())
        self.assertFalse(rag_root.exists())

    def test_status_maps_every_observed_state_without_turning_nonready_into_failure(
        self,
    ) -> None:
        empty_store = SimpleNamespace(counts=ConversationRagStoreCounts())
        disabled = (
            None,
            ConversationRagCompositionFailure.DEPENDENCY_MISSING,
            ConversationRagRuntimeInspectionStatus.MISSING,
            ConversationRagCliState.DISABLED,
        )
        cases = (
            disabled,
            (
                "local",
                ConversationRagCompositionFailure.DEPENDENCY_MISSING,
                ConversationRagRuntimeInspectionStatus.MISSING,
                ConversationRagCliState.UNAVAILABLE,
            ),
            (
                "local",
                ConversationRagCompositionFailure.VERSION_INCOMPATIBLE,
                ConversationRagRuntimeInspectionStatus.MISSING,
                ConversationRagCliState.INCOMPATIBLE,
            ),
            (
                "local",
                None,
                ConversationRagRuntimeInspectionStatus.MISSING,
                ConversationRagCliState.MISSING,
            ),
            (
                "local",
                None,
                ConversationRagRuntimeInspectionStatus.READY,
                ConversationRagCliState.READY,
            ),
            (
                "local",
                None,
                ConversationRagRuntimeInspectionStatus.STALE,
                ConversationRagCliState.STALE,
            ),
            (
                "local",
                None,
                ConversationRagRuntimeInspectionStatus.FAILED,
                ConversationRagCliState.FAILED,
            ),
            (
                "local",
                None,
                ConversationRagRuntimeInspectionStatus.CORRUPT,
                ConversationRagCliState.CORRUPT,
            ),
            (
                "local",
                None,
                ConversationRagRuntimeInspectionStatus.INCOMPATIBLE,
                ConversationRagCliState.INCOMPATIBLE,
            ),
        )
        for provider, compatibility, runtime_status, expected in cases:
            with self.subTest(expected=expected):
                _set_rag_configuration(self.workspace, provider=provider)
                with (
                    mock.patch.object(
                        rag_cli,
                        "inspect_conversation_rag_compatibility",
                        return_value=compatibility,
                    ),
                    mock.patch.object(
                        rag_cli, "_store_snapshot", return_value=empty_store
                    ),
                    mock.patch.object(
                        rag_cli,
                        "inspect_authorized_conversation_rag",
                        return_value=_inspection(runtime_status),
                    ),
                ):
                    report = dispatch_conversation_rag(
                        self.workspace,
                        ConversationRagCliRequest(
                            ConversationRagCliOperation.STATUS, _SESSION
                        ),
                        conversation_storage_services=self.services,
                    )
                self.assertEqual(report.exit_code, 0)
                self.assertEqual(report.outcome, "completed")
                self.assertIs(report.state, expected)

    def test_enable_preflights_before_write_and_enable_disable_preserve_scope(
        self,
    ) -> None:
        system = self.workspace.path("config/system_defaults.json")
        before = system.read_bytes()
        request = ConversationRagCliRequest(
            ConversationRagCliOperation.ENABLE, provider="local"
        )
        with mock.patch.object(
            rag_cli,
            "inspect_conversation_rag_compatibility",
            return_value=ConversationRagCompositionFailure.DEPENDENCY_MISSING,
        ) as probe:
            missing = dispatch_conversation_rag(self.workspace, request)
        probe.assert_called_once_with("local")
        self.assertIs(missing.failure, ConversationRagCliFailure.DEPENDENCY_MISSING)
        self.assertEqual(system.read_bytes(), before)

        with mock.patch.object(
            rag_cli, "inspect_conversation_rag_compatibility", return_value=None
        ):
            enabled = dispatch_conversation_rag(self.workspace, request)
        self.assertEqual(
            (enabled.outcome, enabled.provider, enabled.configuration),
            ("completed", "local", "saved"),
        )
        _set_rag_configuration(self.workspace, provider="local", automatic=True)
        with mock.patch.object(
            rag_cli, "inspect_conversation_rag_compatibility", return_value=None
        ):
            unchanged = dispatch_conversation_rag(self.workspace, request)
        self.assertEqual(
            (unchanged.configuration, unchanged.automatic), ("unchanged", True)
        )
        derived = self.workspace.path(
            "agent_states/orchestrator_state/conversation_rag/sessions/keep.txt"
        )
        derived.parent.mkdir(parents=True)
        derived.write_text("derived sentinel", encoding="utf-8")
        authoritative = self.workspace.path("core_documents/global_outline.md")
        authoritative_before = authoritative.read_bytes()
        disabled = dispatch_conversation_rag(
            self.workspace,
            ConversationRagCliRequest(ConversationRagCliOperation.DISABLE),
        )
        self.assertIs(disabled.state, ConversationRagCliState.DISABLED)
        defaults = (
            ConfigManager(self.workspace).load_configuration_snapshot().system_defaults
        )
        self.assertEqual(
            (defaults.conversation_rag_provider, defaults.conversation_rag_automatic),
            (None, False),
        )
        self.assertEqual(derived.read_text(encoding="utf-8"), "derived sentinel")
        self.assertEqual(authoritative.read_bytes(), authoritative_before)

    def test_configuration_failures_are_fixed_content_free_and_preserve_bytes(
        self,
    ) -> None:
        before = self.workspace.path("config/system_defaults.json").read_bytes()
        request = ConversationRagCliRequest(
            ConversationRagCliOperation.ENABLE, provider="local"
        )
        expected = {
            ConfigurationCommitFailure.VALIDATION: ConversationRagCliFailure.CONFIGURATION_INVALID,
            ConfigurationCommitFailure.CONFLICT: ConversationRagCliFailure.CONFIGURATION_CONFLICT,
            ConfigurationCommitFailure.WRITE: ConversationRagCliFailure.CONFIGURATION_WRITE_FAILED,
            ConfigurationCommitFailure.ROLLBACK: ConversationRagCliFailure.CONFIGURATION_WRITE_FAILED,
        }
        for failure, cli_failure in expected.items():
            with (
                self.subTest(failure=failure),
                mock.patch.object(
                    rag_cli, "inspect_conversation_rag_compatibility", return_value=None
                ),
                mock.patch.object(
                    ConfigManager,
                    "commit_configuration",
                    side_effect=ConfigurationCommitError(failure),
                ),
            ):
                report = dispatch_conversation_rag(self.workspace, request)
            self.assertIs(report.failure, cli_failure)
            self.assertEqual(
                self.workspace.path("config/system_defaults.json").read_bytes(), before
            )
            self.assertNotIn("PRIVATE", report.to_json())

    def test_search_returns_only_authorized_excerpt_and_sanitizes_failure(self) -> None:
        _set_rag_configuration(self.workspace, provider="local")
        query = "PRIVATE_QUERY_CREDENTIAL"
        content = "authorized excerpt"
        service = mock.Mock()
        service.search_authorized.return_value = _context(content)
        bundle = SimpleNamespace(service=service)
        request = ConversationRagCliRequest(
            ConversationRagCliOperation.SEARCH,
            _SESSION,
            query=query,
        )
        with (
            mock.patch.object(
                rag_cli, "inspect_conversation_rag_compatibility", return_value=None
            ),
            mock.patch.object(
                rag_cli, "compose_local_conversation_rag_runtime", return_value=bundle
            ),
        ):
            report = dispatch_conversation_rag(
                self.workspace,
                request,
                conversation_storage_services=self.services,
            )
        service.search_authorized.assert_called_once_with(
            self.services, _SESSION, query
        )
        payload = report.to_json()
        self.assertEqual(report.exit_code, 0)
        self.assertEqual(report.excerpts[0].content, content)
        self.assertIn(content, payload)
        for prohibited in (
            query,
            "PRIVATE_PROVIDER_SUBJECT",
            "score",
            str(self.workspace.root),
        ):
            self.assertNotIn(prohibited, payload)

        service.search_authorized.side_effect = RuntimeError(
            f"PRIVATE_EXCEPTION {query} {self.workspace.root} token=secret"
        )
        with (
            mock.patch.object(
                rag_cli, "inspect_conversation_rag_compatibility", return_value=None
            ),
            mock.patch.object(
                rag_cli, "compose_local_conversation_rag_runtime", return_value=bundle
            ),
        ):
            failed = dispatch_conversation_rag(
                self.workspace,
                request,
                conversation_storage_services=self.services,
            )
        self.assertIs(failed.failure, ConversationRagCliFailure.OPERATION_FAILED)
        for prohibited in (
            query,
            "PRIVATE_EXCEPTION",
            str(self.workspace.root),
            "secret",
        ):
            self.assertNotIn(prohibited, failed.to_json())

    def test_rebuild_is_confirmed_restartable_and_uses_injected_services(self) -> None:
        _set_rag_configuration(self.workspace, provider="local")
        request = ConversationRagCliRequest(
            ConversationRagCliOperation.REBUILD,
            _SESSION,
            confirmed=False,
        )
        unconfirmed = dispatch_conversation_rag(
            self.workspace,
            request,
            conversation_storage_services=self.services,
        )
        self.assertIs(
            unconfirmed.failure, ConversationRagCliFailure.CONFIRMATION_REQUIRED
        )
        service = mock.Mock()
        bundle = SimpleNamespace(service=service)
        expected = ConversationRagCliReport(
            operation=ConversationRagCliOperation.REBUILD,
            outcome="completed",
            state=ConversationRagCliState.READY,
            provider="local",
            automatic=False,
            compatibility="compatible",
            authoritative_checkpoint=_CHECKPOINT,
            indexed_checkpoint=_INDEXED_CHECKPOINT,
            rebuild_required=False,
        )
        confirmed = replace(request, confirmed=True)
        with (
            mock.patch.object(
                rag_cli, "inspect_conversation_rag_compatibility", return_value=None
            ),
            mock.patch.object(
                rag_cli, "compose_local_conversation_rag_runtime", return_value=bundle
            ),
            mock.patch.object(rag_cli, "_status_report", return_value=expected),
        ):
            first = dispatch_conversation_rag(
                self.workspace,
                confirmed,
                conversation_storage_services=self.services,
            )
            second = dispatch_conversation_rag(
                self.workspace,
                confirmed,
                conversation_storage_services=self.services,
            )
        self.assertEqual(first, second)
        self.assertEqual(service.project_authorized.call_count, 2)
        service.project_authorized.assert_called_with(
            self.services, _SESSION, rebuild=True
        )

    def test_purge_requires_confirmation_and_is_contained_to_derived_session(
        self,
    ) -> None:
        conversation = self.services.catalog.create(_SESSION, "Authoritative sentinel")
        history = _append_proposal(self.services, conversation.conversation_id)
        store = ConversationRagGenerationStore(self.workspace)
        store.set_status(_SESSION, ConversationRagStoreStatus.STALE)
        store.set_status("other_session", ConversationRagStoreStatus.PROVIDER_FAILED)
        rag_sessions = self.workspace.path(
            "agent_states/orchestrator_state/conversation_rag/sessions"
        )
        other_before = _tree_bytes(rag_sessions / "other_session")
        config_before = self.workspace.path("config/system_defaults.json").read_bytes()
        authoritative = self.workspace.path("core_documents/global_outline.md")
        authoritative_before = authoritative.read_bytes()
        manuscript = self.workspace.path("manuscript/preserved.md")
        artifact = self.workspace.path("artifacts/preserved.txt")
        artifact.parent.mkdir(parents=True)
        manuscript.write_text("manuscript", encoding="utf-8")
        artifact.write_text("artifact", encoding="utf-8")
        external = self.workspace.root.parent / "rag-cli-external-sentinel"
        external.write_text("external", encoding="utf-8")
        self.addCleanup(lambda: external.unlink(missing_ok=True))

        unconfirmed = dispatch_conversation_rag(
            self.workspace,
            ConversationRagCliRequest(ConversationRagCliOperation.PURGE, _SESSION),
        )
        self.assertIs(
            unconfirmed.failure, ConversationRagCliFailure.CONFIRMATION_REQUIRED
        )
        self.assertTrue((rag_sessions / _SESSION / "status.json").exists())
        confirmed = dispatch_conversation_rag(
            self.workspace,
            ConversationRagCliRequest(
                ConversationRagCliOperation.PURGE, _SESSION, confirmed=True
            ),
        )
        self.assertEqual(confirmed.exit_code, 0)
        self.assertIs(confirmed.state, ConversationRagCliState.MISSING)
        self.assertFalse((rag_sessions / _SESSION / "status.json").exists())
        self.assertEqual(_tree_bytes(rag_sessions / "other_session"), other_before)
        self.assertEqual(
            self.workspace.path("config/system_defaults.json").read_bytes(),
            config_before,
        )
        self.assertEqual(authoritative.read_bytes(), authoritative_before)
        self.assertEqual(manuscript.read_text(encoding="utf-8"), "manuscript")
        self.assertEqual(artifact.read_text(encoding="utf-8"), "artifact")
        self.assertEqual(
            self.services.event_store.read(_SESSION, conversation.conversation_id),
            history,
        )
        self.assertEqual(external.read_text(encoding="utf-8"), "external")

    def test_real_local_workflow_when_the_pinned_optional_package_is_installed(
        self,
    ) -> None:
        try:
            installed = metadata.version("generic-rag")
        except metadata.PackageNotFoundError:
            self.skipTest("pinned optional generic-rag distribution is not installed")
        self.assertEqual(installed, "0.1.0")
        conversation = self.services.catalog.create(_SESSION, "Local workflow")
        _append_proposal(self.services, conversation.conversation_id)
        enabled = dispatch_conversation_rag(
            self.workspace,
            ConversationRagCliRequest(
                ConversationRagCliOperation.ENABLE, provider="local"
            ),
        )
        self.assertEqual(enabled.exit_code, 0)
        rebuild = dispatch_conversation_rag(
            self.workspace,
            ConversationRagCliRequest(
                ConversationRagCliOperation.REBUILD, _SESSION, confirmed=True
            ),
            conversation_storage_services=self.services,
        )
        self.assertIs(rebuild.state, ConversationRagCliState.READY)
        status = dispatch_conversation_rag(
            self.workspace,
            ConversationRagCliRequest(ConversationRagCliOperation.STATUS, _SESSION),
            conversation_storage_services=self.services,
        )
        self.assertIs(status.state, ConversationRagCliState.READY)
        search = dispatch_conversation_rag(
            self.workspace,
            ConversationRagCliRequest(
                ConversationRagCliOperation.SEARCH,
                _SESSION,
                query="Consult specialists",
            ),
            conversation_storage_services=self.services,
        )
        self.assertTrue(search.excerpts)
        self.assertEqual(
            dispatch_conversation_rag(
                self.workspace,
                ConversationRagCliRequest(ConversationRagCliOperation.DISABLE),
            ).exit_code,
            0,
        )
        self.assertEqual(
            dispatch_conversation_rag(
                self.workspace,
                ConversationRagCliRequest(
                    ConversationRagCliOperation.PURGE, _SESSION, confirmed=True
                ),
            ).exit_code,
            0,
        )

    def test_failure_vocabulary_and_controls_are_sanitized_at_dispatch_boundary(
        self,
    ) -> None:
        request = ConversationRagCliRequest(
            ConversationRagCliOperation.STATUS, _SESSION
        )
        failures = (
            (
                ConfigurationError("PRIVATE_CONFIG"),
                ConversationRagCliFailure.CONFIGURATION_INVALID,
            ),
            (
                ConversationRagCompositionError(
                    ConversationRagCompositionFailure.DEPENDENCY_MISSING
                ),
                ConversationRagCliFailure.DEPENDENCY_MISSING,
            ),
            (
                ConversationStorageCoordinationError(),
                ConversationRagCliFailure.COORDINATION_FAILED,
            ),
            (
                ConversationStorageUnavailableError(),
                ConversationRagCliFailure.STORAGE_UNAVAILABLE,
            ),
            (
                ConversationRagStoreError(ConversationRagStoreFailure.BUSY),
                ConversationRagCliFailure.COORDINATION_FAILED,
            ),
            (
                ConversationRagStoreError(ConversationRagStoreFailure.UNSAFE),
                ConversationRagCliFailure.UNSAFE,
            ),
            (
                ConversationRagStoreError(ConversationRagStoreFailure.CORRUPT),
                ConversationRagCliFailure.CORRUPT,
            ),
            (
                ConversationRagStoreError(ConversationRagStoreFailure.INCOMPATIBLE),
                ConversationRagCliFailure.INCOMPATIBLE,
            ),
            (
                ConversationRagStoreError(ConversationRagStoreFailure.PROVIDER_FAILED),
                ConversationRagCliFailure.PROVIDER_FAILED,
            ),
            (
                RuntimeError("PRIVATE_PATH token=secret"),
                ConversationRagCliFailure.OPERATION_FAILED,
            ),
        )
        for error, expected in failures:
            with (
                self.subTest(expected=expected),
                mock.patch.object(rag_cli, "_status_report", side_effect=error),
            ):
                report = dispatch_conversation_rag(
                    self.workspace,
                    request,
                    conversation_storage_services=self.services,
                )
            self.assertIs(report.failure, expected)
            self.assertEqual(report.exit_code, 2)
            for prohibited in ("PRIVATE", "secret", str(self.workspace.root)):
                self.assertNotIn(prohibited, report.to_json())
        for control in (KeyboardInterrupt(), SystemExit("PRIVATE_CONTROL")):
            with (
                self.subTest(control=type(control).__name__),
                mock.patch.object(rag_cli, "_status_report", side_effect=control),
                self.assertRaises(type(control)),
            ):
                dispatch_conversation_rag(
                    self.workspace,
                    request,
                    conversation_storage_services=self.services,
                )


class ConversationRagArgparseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = initialized_workspace(self)

    def _main(self, arguments: list[str]) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch.object(
                cli_module,
                "resolve_workspace",
                return_value=self.workspace,
            ),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            code = cli_module.main(arguments)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_parser_owns_exact_six_action_grammar_and_syntax_errors(self) -> None:
        parser = cli_module.build_parser()
        cases = (
            (["rag", "status"], "status", None, None, False),
            (["rag", "enable", "--provider", "local"], "enable", "local", None, False),
            (["rag", "rebuild", "--yes"], "rebuild", None, None, True),
            (["rag", "search", "needle"], "search", None, "needle", False),
            (["rag", "disable"], "disable", None, None, False),
            (["rag", "purge", "--yes"], "purge", None, None, True),
        )
        for arguments, action, provider, query, yes in cases:
            with self.subTest(arguments=arguments):
                parsed = parser.parse_args(arguments)
                self.assertEqual(parsed.command, "rag")
                self.assertEqual(parsed.rag_action, action)
                self.assertEqual(getattr(parsed, "provider", None), provider)
                self.assertEqual(getattr(parsed, "query", None), query)
                self.assertIs(bool(getattr(parsed, "yes", False)), yes)
        for arguments in (
            ["rag"],
            ["rag", "enable"],
            ["rag", "search"],
            ["rag", "disable", "--session", _SESSION],
            ["rag", "status", "--yes"],
        ):
            stderr = io.StringIO()
            with (
                self.subTest(arguments=arguments),
                contextlib.redirect_stderr(stderr),
                self.assertRaises(SystemExit) as raised,
            ):
                parser.parse_args(arguments)
            self.assertEqual(raised.exception.code, 2)
            self.assertIn("usage:", stderr.getvalue())

    def test_semantic_results_are_one_compact_json_line_without_debug_hint(
        self,
    ) -> None:
        actions = (
            ["rag", "status", "--session", _SESSION],
            ["rag", "enable", "--provider", "local"],
            ["rag", "rebuild", "--session", _SESSION],
            ["rag", "search", "needle", "--session", _SESSION],
            ["rag", "disable"],
            ["rag", "purge", "--session", _SESSION],
        )
        for arguments in actions:
            with self.subTest(arguments=arguments):
                code, stdout, stderr = self._main(
                    [*arguments, "--workspace", str(self.workspace.root)]
                )
                self.assertIn(code, {0, 2})
                self.assertEqual(stderr, "")
                self.assertEqual(len(stdout.splitlines()), 1)
                payload = json.loads(stdout)
                self.assertEqual(payload["schema_version"], 1)
                self.assertNotIn("debug_logs/runtime.jsonl", stdout)
                self.assertFalse(
                    self.workspace.path("debug_logs/runtime.jsonl").exists()
                )

    def test_blank_query_and_traversal_session_fail_before_storage_or_dispatch(
        self,
    ) -> None:
        with (
            mock.patch.object(
                cli_module,
                "activate_configured_conversation_storage",
                side_effect=AssertionError("storage activated for invalid request"),
            ) as activate,
            mock.patch.object(
                cli_module,
                "dispatch_conversation_rag",
                side_effect=AssertionError("invalid request was dispatched"),
            ) as dispatch,
        ):
            blank = self._main(
                [
                    "rag",
                    "search",
                    " \t ",
                    "--workspace",
                    str(self.workspace.root),
                    "--session",
                    _SESSION,
                ]
            )
            traversal = self._main(
                [
                    "rag",
                    "status",
                    "--workspace",
                    str(self.workspace.root),
                    "--session",
                    "../escape",
                ]
            )
            unsupported = self._main(
                [
                    "rag",
                    "enable",
                    "--provider",
                    "remote",
                    "--workspace",
                    str(self.workspace.root),
                ]
            )
        activate.assert_not_called()
        dispatch.assert_not_called()
        self.assertEqual(json.loads(blank[1])["failure"], "invalid_request")
        self.assertEqual(json.loads(traversal[1])["failure"], "session_invalid")
        self.assertEqual(json.loads(unsupported[1])["failure"], "invalid_request")
        for code, stdout, stderr in (blank, traversal, unsupported):
            self.assertEqual(code, 2)
            self.assertEqual(stderr, "")
            self.assertEqual(len(stdout.splitlines()), 1)

    def test_status_uses_existing_default_session_without_rewriting_it(self) -> None:
        save_default_session(self.workspace, _SESSION)
        context_path = self.workspace.path(
            "agent_states/orchestrator_state/cli_context.json"
        )
        before = context_path.read_bytes()
        code, stdout, stderr = self._main(
            ["rag", "status", "--workspace", str(self.workspace.root)]
        )
        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(json.loads(stdout)["operation"], "status")
        self.assertEqual(context_path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
