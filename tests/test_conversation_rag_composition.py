"""Deferred optional conversation RAG composition verification."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import textwrap
import unittest
from importlib import metadata
from pathlib import Path
from unittest import mock

from story_writing_agents import Workspace
from story_writing_agents import conversation_rag as rag
from story_writing_agents import conversation_rag_composition as composition
from story_writing_agents import conversation_rag_local as local
from story_writing_agents import conversation_rag_store as rag_store

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_GENERIC_RAG_SOURCE = _REPOSITORY_ROOT / "vendor" / "generic-rag" / "src"
_VISIBLE_TURN_SCHEMA_ID = "story-collaboration-rag-readable-event-v2"


def _collaborators() -> rag.ConversationRagCollaborators:
    return rag.ConversationRagCollaborators(
        object(),
        object(),
        object(),
        object(),
        object(),
    )


class _EmptyLexicalRetriever:
    def search(
        self,
        _request: rag.ConversationRagRetrievalRequest,
        /,
    ) -> tuple[rag.ConversationRagProviderCandidate, ...]:
        return ()


def _projection_request(
    *,
    schema_id: str = _VISIBLE_TURN_SCHEMA_ID,
    revision_id: str = "revision-a",
    text: str = "Alpha beta 한글",
) -> rag.ConversationRagProjectionRequest:
    identity = rag.ConversationRagDocumentIdentity(
        rag.ConversationRagDocumentKey("session-a", "document-a"),
        "conversation-a",
        "turn-a",
        "turn-b",
        1,
        2,
        revision_id,
    )
    return rag.ConversationRagProjectionRequest(
        "session-a",
        schema_id,
        local.LocalConversationRagEmbedder().identity,
        (rag.ConversationRagSourceDocument(identity, text),),
    )


def _missing_state() -> rag.ConversationRagProjectionState:
    return rag.ConversationRagProjectionState(
        rag.ConversationRagProjectionStateAvailability.MISSING,
        None,
    )


class ConversationRagCompositionTests(unittest.TestCase):
    def assertCompositionFailure(
        self,
        error: composition.ConversationRagCompositionError,
        failure: composition.ConversationRagCompositionFailure,
    ) -> None:
        self.assertEqual(error.failure, failure)
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)
        self.assertNotIn("secret", str(error))
        self.assertNotIn("secret", repr(error))

    def test_exact_public_exports_allowlist_and_failure_messages(self) -> None:
        self.assertEqual(
            composition.__all__,
            [
                "CONVERSATION_RAG_PROVIDER_ALLOWLIST",
                "ConversationRagCompositionError",
                "ConversationRagCompositionFailure",
                "LocalConversationRagComposition",
                "LocalConversationRagRuntimeComposition",
                "LocalConversationRagRuntimeState",
                "compose_conversation_rag",
                "compose_local_conversation_rag",
                "compose_local_conversation_rag_runtime",
                "inspect_conversation_rag_compatibility",
            ],
        )
        self.assertEqual(composition.CONVERSATION_RAG_PROVIDER_ALLOWLIST, ("local",))
        self.assertEqual(
            tuple(
                member.value for member in composition.ConversationRagCompositionFailure
            ),
            (
                "provider_unsupported",
                "dependency_missing",
                "version_incompatible",
                "capability_incompatible",
            ),
        )
        expected_messages = {
            composition.ConversationRagCompositionFailure.PROVIDER_UNSUPPORTED: (
                "conversation RAG provider is not supported"
            ),
            composition.ConversationRagCompositionFailure.DEPENDENCY_MISSING: (
                "conversation RAG is unavailable; install the pinned 'rag' extra"
            ),
            composition.ConversationRagCompositionFailure.VERSION_INCOMPATIBLE: (
                "conversation RAG dependency version is incompatible; "
                "reinstall the pinned 'rag' extra"
            ),
            composition.ConversationRagCompositionFailure.CAPABILITY_INCOMPATIBLE: (
                "conversation RAG dependency capabilities are incompatible; "
                "reinstall the pinned 'rag' extra"
            ),
        }
        for failure, message in expected_messages.items():
            with self.subTest(failure=failure):
                error = composition.ConversationRagCompositionError(failure)
                self.assertEqual(str(error), message)
                self.assertCompositionFailure(error, failure)

    def test_module_import_does_not_probe_metadata_or_import_generic_rag(self) -> None:
        script = textwrap.dedent(
            f"""
            import importlib.metadata
            import sys

            sys.path.insert(0, {str(_REPOSITORY_ROOT / "src")!r})
            calls = []
            def forbidden_version(name):
                calls.append(name)
                raise AssertionError("metadata must stay deferred")
            importlib.metadata.version = forbidden_version
            import story_writing_agents.conversation_rag_composition as module
            assert module.CONVERSATION_RAG_PROVIDER_ALLOWLIST == ("local",)
            assert len(module.__all__) == 10
            assert calls == []
            assert not any(
                name == "generic_rag" or name.startswith("generic_rag.")
                for name in sys.modules
            )
            """
        )
        completed = subprocess.run(
            [sys.executable, "-I", "-W", "error", "-c", script],
            cwd=_REPOSITORY_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            0,
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}",
        )

    def test_unsupported_provider_and_wrong_collaborator_type_do_not_probe(
        self,
    ) -> None:
        for provider in ("", "remote", "LOCAL", 1):
            with self.subTest(provider=provider):
                with (
                    mock.patch.object(composition.metadata, "version") as version,
                    self.assertRaises(
                        composition.ConversationRagCompositionError
                    ) as caught,
                ):
                    composition.compose_conversation_rag(  # type: ignore[arg-type]
                        provider,
                        _collaborators(),
                    )
                version.assert_not_called()
                self.assertCompositionFailure(
                    caught.exception,
                    composition.ConversationRagCompositionFailure.PROVIDER_UNSUPPORTED,
                )

        class DerivedCollaborators(rag.ConversationRagCollaborators):
            pass

        with (
            mock.patch.object(composition.metadata, "version") as version,
            self.assertRaisesRegex(TypeError, "must be exactly"),
        ):
            composition.compose_conversation_rag(
                "local",
                DerivedCollaborators(
                    object(),
                    object(),
                    object(),
                    object(),
                    object(),
                ),
            )
        version.assert_not_called()

    def test_missing_wrong_and_broken_dependency_metadata_are_sanitized(self) -> None:
        cases = (
            (
                metadata.PackageNotFoundError("secret-package-detail"),
                composition.ConversationRagCompositionFailure.DEPENDENCY_MISSING,
            ),
            (
                "9.9.9-secret",
                composition.ConversationRagCompositionFailure.VERSION_INCOMPATIBLE,
            ),
            (
                RuntimeError("secret metadata failure"),
                composition.ConversationRagCompositionFailure.CAPABILITY_INCOMPATIBLE,
            ),
        )
        for value, expected in cases:
            with self.subTest(expected=expected):
                patcher = (
                    mock.patch.object(
                        composition.metadata,
                        "version",
                        side_effect=value,
                    )
                    if isinstance(value, BaseException)
                    else mock.patch.object(
                        composition.metadata,
                        "version",
                        return_value=value,
                    )
                )
                with (
                    patcher,
                    self.assertRaises(
                        composition.ConversationRagCompositionError
                    ) as caught,
                ):
                    composition.compose_conversation_rag(
                        "local",
                        _collaborators(),
                    )
                self.assertCompositionFailure(caught.exception, expected)

    def test_missing_capability_and_adapter_import_failures_are_sanitized(self) -> None:
        if str(_GENERIC_RAG_SOURCE) not in sys.path:
            sys.path.insert(0, str(_GENERIC_RAG_SOURCE))
        from story_writing_agents import generic_rag_adapter

        empty = generic_rag_adapter.GenericRagCapabilitySnapshot(())
        with (
            mock.patch.object(composition.metadata, "version", return_value="0.1.0"),
            mock.patch.object(
                generic_rag_adapter,
                "inspect_generic_rag_capabilities",
                return_value=empty,
            ),
            self.assertRaises(
                composition.ConversationRagCompositionError
            ) as capability_caught,
        ):
            composition.compose_conversation_rag("local", _collaborators())
        self.assertCompositionFailure(
            capability_caught.exception,
            composition.ConversationRagCompositionFailure.CAPABILITY_INCOMPATIBLE,
        )

        original_import = __import__

        def blocked_import(
            name: str,
            globals: dict[str, object] | None = None,
            locals: dict[str, object] | None = None,
            fromlist: tuple[str, ...] = (),
            level: int = 0,
        ) -> object:
            if name.endswith("generic_rag_adapter"):
                raise ImportError("secret adapter import detail")
            return original_import(name, globals, locals, fromlist, level)

        with (
            mock.patch.object(composition.metadata, "version", return_value="0.1.0"),
            mock.patch("builtins.__import__", side_effect=blocked_import),
            self.assertRaises(
                composition.ConversationRagCompositionError
            ) as import_caught,
        ):
            composition.compose_conversation_rag("local", _collaborators())
        self.assertCompositionFailure(
            import_caught.exception,
            composition.ConversationRagCompositionFailure.CAPABILITY_INCOMPATIBLE,
        )

    def test_exact_pinned_capabilities_compose_the_local_service(self) -> None:
        if str(_GENERIC_RAG_SOURCE) not in sys.path:
            sys.path.insert(0, str(_GENERIC_RAG_SOURCE))
        from story_writing_agents import generic_rag_adapter

        snapshot = generic_rag_adapter.inspect_generic_rag_capabilities()
        self.assertTrue(snapshot.supports(composition._GENERIC_RAG_REQUIREMENTS))
        with mock.patch.object(
            composition.metadata,
            "version",
            return_value="0.1.0",
        ) as version:
            service = composition.compose_conversation_rag(
                "local",
                _collaborators(),
            )
        version.assert_called_once_with("generic-rag")
        self.assertIs(type(service), generic_rag_adapter.GenericRagConversationService)

    def test_capability_and_construction_failures_drop_dependency_details(self) -> None:
        if str(_GENERIC_RAG_SOURCE) not in sys.path:
            sys.path.insert(0, str(_GENERIC_RAG_SOURCE))
        from story_writing_agents import generic_rag_adapter

        for target, failure in (
            (
                "inspect_generic_rag_capabilities",
                RuntimeError("secret capability detail"),
            ),
            (
                "GenericRagConversationService",
                RuntimeError("secret construction detail"),
            ),
        ):
            with self.subTest(target=target):
                with (
                    mock.patch.object(
                        composition.metadata,
                        "version",
                        return_value="0.1.0",
                    ),
                    mock.patch.object(
                        generic_rag_adapter,
                        target,
                        side_effect=failure,
                    ),
                    self.assertRaises(
                        composition.ConversationRagCompositionError
                    ) as caught,
                ):
                    composition.compose_conversation_rag(
                        "local",
                        _collaborators(),
                    )
                self.assertCompositionFailure(
                    caught.exception,
                    composition.ConversationRagCompositionFailure.CAPABILITY_INCOMPATIBLE,
                )

    def test_process_controls_propagate_from_every_deferred_phase(self) -> None:
        if str(_GENERIC_RAG_SOURCE) not in sys.path:
            sys.path.insert(0, str(_GENERIC_RAG_SOURCE))
        from story_writing_agents import generic_rag_adapter

        for control in (KeyboardInterrupt("metadata"), SystemExit(3)):
            with self.subTest(metadata=type(control).__name__):
                with (
                    mock.patch.object(
                        composition.metadata,
                        "version",
                        side_effect=control,
                    ),
                    self.assertRaises(type(control)) as caught,
                ):
                    composition.compose_conversation_rag(
                        "local",
                        _collaborators(),
                    )
                self.assertIs(caught.exception, control)

        for target, control in (
            ("inspect_generic_rag_capabilities", KeyboardInterrupt("capability")),
            ("GenericRagConversationService", SystemExit(4)),
        ):
            with self.subTest(target=target):
                with (
                    mock.patch.object(
                        composition.metadata,
                        "version",
                        return_value="0.1.0",
                    ),
                    mock.patch.object(
                        generic_rag_adapter,
                        target,
                        side_effect=control,
                    ),
                    self.assertRaises(type(control)) as caught,
                ):
                    composition.compose_conversation_rag(
                        "local",
                        _collaborators(),
                    )
                self.assertIs(caught.exception, control)

    def test_local_composition_exact_pin_projects_reopens_and_retrieves(self) -> None:
        if str(_GENERIC_RAG_SOURCE) not in sys.path:
            sys.path.insert(0, str(_GENERIC_RAG_SOURCE))
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Workspace(temporary).initialize()
            store = rag_store.ConversationRagGenerationStore(workspace)
            lexical = _EmptyLexicalRetriever()
            real_compose = composition.compose_conversation_rag
            with (
                mock.patch.object(
                    composition.metadata, "version", return_value="0.1.0"
                ),
                mock.patch.object(
                    composition,
                    "compose_conversation_rag",
                    wraps=real_compose,
                ) as composed_calls,
            ):
                composed = composition.compose_local_conversation_rag(store, lexical)
                self.assertIs(
                    type(composed), composition.LocalConversationRagComposition
                )
                self.assertIs(composed.service, composed)
                self.assertIs(composed.store, store)
                request = _projection_request()
                projected = composed.rebuild_projection(request, _missing_state())
                snapshot = store.inspect(
                    "session-a",
                    local.LOCAL_CONVERSATION_RAG_PROVIDER_ID,
                    local.LocalConversationRagEmbedder().identity,
                    expected_projection_schema_id=request.schema_id,
                )
                self.assertIs(
                    snapshot.status, rag_store.ConversationRagStoreStatus.READY
                )
                self.assertEqual(snapshot.state.checkpoint, projected.checkpoint)
                inspection_schemas: list[str] = []
                real_inspect = rag_store.ConversationRagGenerationStore.inspect

                def record_inspection(
                    current_store: rag_store.ConversationRagGenerationStore,
                    session_id: str,
                    provider_id: str,
                    embedding: rag.ConversationRagEmbeddingIdentity,
                    /,
                    *,
                    expected_projection_schema_id: str = (
                        rag_store.CONVERSATION_RAG_STORE_SCHEMA_ID
                    ),
                ) -> rag_store.ConversationRagStoreSnapshot:
                    inspection_schemas.append(expected_projection_schema_id)
                    return real_inspect(
                        current_store,
                        session_id,
                        provider_id,
                        embedding,
                        expected_projection_schema_id=(expected_projection_schema_id),
                    )

                with mock.patch.object(
                    rag_store.ConversationRagGenerationStore,
                    "inspect",
                    new=record_inspection,
                ):
                    semantic = composed.retrieve_semantic(
                        rag.ConversationRagRetrievalRequest("session-a", "alpha", 3),
                        snapshot.state,
                    )
                    fresh = composition.compose_local_conversation_rag(
                        rag_store.ConversationRagGenerationStore(
                            Workspace(workspace.root)
                        ),
                        lexical,
                    )
                    reopened = fresh.retrieve_semantic(
                        rag.ConversationRagRetrievalRequest("session-a", "beta", 3),
                        snapshot.state,
                    )
                self.assertEqual(
                    (semantic.outcome, semantic.reason, len(semantic.candidates)),
                    (
                        rag.ConversationRagOutcome.COMPLETE,
                        rag.ConversationRagReason.NONE,
                        1,
                    ),
                )
                self.assertEqual(
                    semantic.candidates[0].source.document.key.document_id,
                    "document-a",
                )
                self.assertEqual(reopened.candidates, semantic.candidates)
                self.assertEqual(
                    inspection_schemas,
                    [_VISIBLE_TURN_SCHEMA_ID, _VISIBLE_TURN_SCHEMA_ID],
                )

            # Construction, projection, retrieval, fresh construction, and
            # fresh retrieval each validate and create a call-local adapter.
            self.assertEqual(composed_calls.call_count, 5)
            collaborator_ids = [
                id(call.args[1]) for call in composed_calls.call_args_list
            ]
            self.assertEqual(len(collaborator_ids), len(set(collaborator_ids)))

    def test_local_projection_failure_is_sanitized_and_records_provider_status(
        self,
    ) -> None:
        if str(_GENERIC_RAG_SOURCE) not in sys.path:
            sys.path.insert(0, str(_GENERIC_RAG_SOURCE))
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Workspace(temporary).initialize()
            store = rag_store.ConversationRagGenerationStore(workspace)
            with mock.patch.object(
                composition.metadata, "version", return_value="0.1.0"
            ):
                composed = composition.compose_local_conversation_rag(
                    store,
                    _EmptyLexicalRetriever(),
                )
                with (
                    mock.patch.object(
                        local.LocalConversationRagEmbedder,
                        "embed",
                        side_effect=RuntimeError("secret provider detail"),
                    ),
                    self.assertRaises(rag.ConversationRagOperationError) as caught,
                ):
                    composed.rebuild_projection(_projection_request(), _missing_state())
            self.assertNotIn("secret", str(caught.exception))
            self.assertNotIn("secret", repr(caught.exception))
            self.assertIsNone(caught.exception.__cause__)
            observed = store.inspect(
                "session-a",
                "local",
                local.LocalConversationRagEmbedder().identity,
            )
            self.assertIs(
                observed.status,
                rag_store.ConversationRagStoreStatus.PROVIDER_FAILED,
            )
            self.assertIs(
                observed.state.availability,
                rag.ConversationRagProjectionStateAvailability.MISSING,
            )

    def test_local_process_controls_preserve_active_and_release_builder(self) -> None:
        if str(_GENERIC_RAG_SOURCE) not in sys.path:
            sys.path.insert(0, str(_GENERIC_RAG_SOURCE))
        for control in (KeyboardInterrupt("stop"), SystemExit(8)):
            with (
                self.subTest(control=type(control).__name__),
                tempfile.TemporaryDirectory() as temporary,
            ):
                workspace = Workspace(temporary).initialize()
                store = rag_store.ConversationRagGenerationStore(workspace)
                with mock.patch.object(
                    composition.metadata, "version", return_value="0.1.0"
                ):
                    composed = composition.compose_local_conversation_rag(
                        store,
                        _EmptyLexicalRetriever(),
                    )
                    original_request = _projection_request()
                    projected = composed.rebuild_projection(
                        original_request, _missing_state()
                    )
                    active = workspace.root.joinpath(
                        "agent_states/orchestrator_state/conversation_rag/sessions/"
                        "session-a/active.json"
                    )
                    active_before = active.read_bytes()
                    updated = _projection_request(
                        revision_id="revision-b", text="updated beta"
                    )
                    with (
                        mock.patch.object(
                            local.LocalConversationRagEmbedder,
                            "embed",
                            side_effect=control,
                        ),
                        self.assertRaises(type(control)) as caught,
                    ):
                        composed.rebuild_projection(
                            updated,
                            rag.ConversationRagProjectionState(
                                rag.ConversationRagProjectionStateAvailability.PRESENT,
                                projected.checkpoint,
                            ),
                        )
                    self.assertIs(caught.exception, control)
                    self.assertEqual(active.read_bytes(), active_before)
                    observed = store.inspect(
                        "session-a",
                        "local",
                        local.LocalConversationRagEmbedder().identity,
                        expected_projection_schema_id=_VISIBLE_TURN_SCHEMA_ID,
                    )
                    self.assertEqual(observed.state.checkpoint, projected.checkpoint)
                    # A later operation reacquires the same session lock.
                    store.set_status(
                        "session-a", rag_store.ConversationRagStoreStatus.STALE
                    )

    def test_local_retrieval_distinguishes_schema_mismatch_and_state_drift(
        self,
    ) -> None:
        if str(_GENERIC_RAG_SOURCE) not in sys.path:
            sys.path.insert(0, str(_GENERIC_RAG_SOURCE))
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Workspace(temporary).initialize()
            store = rag_store.ConversationRagGenerationStore(workspace)
            with mock.patch.object(
                composition.metadata, "version", return_value="0.1.0"
            ):
                composed = composition.compose_local_conversation_rag(
                    store,
                    _EmptyLexicalRetriever(),
                )
                legacy = composed.rebuild_projection(
                    _projection_request(
                        schema_id=rag_store.CONVERSATION_RAG_STORE_SCHEMA_ID,
                        revision_id="revision-legacy",
                        text="Legacy alpha",
                    ),
                    _missing_state(),
                )
                legacy_state = rag.ConversationRagProjectionState(
                    rag.ConversationRagProjectionStateAvailability.PRESENT,
                    legacy.checkpoint,
                )
                visible = composed.rebuild_projection(
                    _projection_request(),
                    legacy_state,
                )
                visible_state = rag.ConversationRagProjectionState(
                    rag.ConversationRagProjectionStateAvailability.PRESENT,
                    visible.checkpoint,
                )
                query = rag.ConversationRagRetrievalRequest("session-a", "alpha", 2)
                incompatible = composed.retrieve_semantic(query, legacy_state)
                self.assertEqual(
                    (
                        incompatible.outcome,
                        incompatible.reason,
                        incompatible.candidates,
                    ),
                    (
                        rag.ConversationRagOutcome.LITERAL_FALLBACK,
                        rag.ConversationRagReason.INCOMPATIBLE,
                        (),
                    ),
                )
                updated = composed.rebuild_projection(
                    _projection_request(
                        revision_id="revision-b",
                        text="Updated visible alpha",
                    ),
                    visible_state,
                )
                stale = composed.retrieve_semantic(query, visible_state)
                self.assertEqual(
                    (stale.outcome, stale.reason, stale.candidates),
                    (
                        rag.ConversationRagOutcome.LITERAL_FALLBACK,
                        rag.ConversationRagReason.STALE,
                        (),
                    ),
                )
                store.set_status(
                    "session-a", rag_store.ConversationRagStoreStatus.PROVIDER_FAILED
                )
                failed = composed.retrieve_semantic(
                    query,
                    rag.ConversationRagProjectionState(
                        rag.ConversationRagProjectionStateAvailability.PRESENT,
                        updated.checkpoint,
                    ),
                )
                self.assertEqual(
                    (failed.outcome, failed.reason, failed.candidates),
                    (
                        rag.ConversationRagOutcome.LITERAL_FALLBACK,
                        rag.ConversationRagReason.FAILED,
                        (),
                    ),
                )

    def test_local_retrieval_maps_corrupt_store_to_literal_fallback(self) -> None:
        if str(_GENERIC_RAG_SOURCE) not in sys.path:
            sys.path.insert(0, str(_GENERIC_RAG_SOURCE))
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Workspace(temporary).initialize()
            store = rag_store.ConversationRagGenerationStore(workspace)
            with mock.patch.object(
                composition.metadata,
                "version",
                return_value="0.1.0",
            ):
                composed = composition.compose_local_conversation_rag(
                    store,
                    _EmptyLexicalRetriever(),
                )
                projected = composed.rebuild_projection(
                    _projection_request(),
                    _missing_state(),
                )
                active = workspace.root.joinpath(
                    "agent_states",
                    "orchestrator_state",
                    "conversation_rag",
                    "sessions",
                    "session-a",
                    "active.json",
                )
                active.write_bytes(b"[" * 2_048 + b"0" + b"]" * 2_048 + b"\n")
                observed = composed.retrieve_semantic(
                    rag.ConversationRagRetrievalRequest("session-a", "alpha", 2),
                    rag.ConversationRagProjectionState(
                        rag.ConversationRagProjectionStateAvailability.PRESENT,
                        projected.checkpoint,
                    ),
                )
                self.assertEqual(
                    (observed.outcome, observed.reason, observed.candidates),
                    (
                        rag.ConversationRagOutcome.LITERAL_FALLBACK,
                        rag.ConversationRagReason.CORRUPT,
                        (),
                    ),
                )

    def test_local_missing_dependency_and_invalid_inputs_do_not_touch_storage(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Workspace(temporary).initialize()
            store = rag_store.ConversationRagGenerationStore(workspace)
            rag_root = (
                workspace.root / "agent_states/orchestrator_state/conversation_rag"
            )
            with (
                mock.patch.object(
                    composition.metadata,
                    "version",
                    side_effect=metadata.PackageNotFoundError("secret"),
                ),
                self.assertRaises(
                    composition.ConversationRagCompositionError
                ) as caught,
            ):
                composition.compose_local_conversation_rag(
                    store,
                    _EmptyLexicalRetriever(),
                )
            self.assertCompositionFailure(
                caught.exception,
                composition.ConversationRagCompositionFailure.DEPENDENCY_MISSING,
            )
            self.assertFalse(rag_root.exists())
            with self.assertRaisesRegex(TypeError, "exact type"):
                composition.compose_local_conversation_rag(  # type: ignore[arg-type]
                    object(),
                    _EmptyLexicalRetriever(),
                )
            with self.assertRaisesRegex(TypeError, "provide search"):
                composition.LocalConversationRagComposition(store, object())  # type: ignore[arg-type]

    def test_compatibility_probe_is_deferred_sanitized_and_resource_free(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Workspace(temporary).initialize()
            rag_root = (
                workspace.root / "agent_states/orchestrator_state/conversation_rag"
            )
            cases = (
                (
                    metadata.PackageNotFoundError("PRIVATE_PACKAGE_PATH"),
                    composition.ConversationRagCompositionFailure.DEPENDENCY_MISSING,
                ),
                (
                    None,
                    composition.ConversationRagCompositionFailure.VERSION_INCOMPATIBLE,
                ),
            )
            for failure, expected in cases:
                with self.subTest(expected=expected):
                    side_effect = failure if failure is not None else None
                    return_value = "9.9.9" if failure is None else None
                    with mock.patch.object(
                        composition.metadata,
                        "version",
                        side_effect=side_effect,
                        return_value=return_value,
                    ):
                        observed = composition.inspect_conversation_rag_compatibility(
                            "local"
                        )
                    self.assertIs(observed, expected)
                    self.assertFalse(rag_root.exists())
            self.assertIs(
                composition.inspect_conversation_rag_compatibility("remote"),
                composition.ConversationRagCompositionFailure.PROVIDER_UNSUPPORTED,
            )
            for control in (KeyboardInterrupt(), SystemExit("control")):
                with (
                    self.subTest(control=type(control).__name__),
                    mock.patch.object(
                        composition.metadata,
                        "version",
                        side_effect=control,
                    ),
                    self.assertRaises(type(control)),
                ):
                    composition.inspect_conversation_rag_compatibility("local")
            self.assertFalse(rag_root.exists())

    def test_concrete_runtime_state_maps_every_store_status_and_marks_stale(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Workspace(temporary).initialize()
            store = rag_store.ConversationRagGenerationStore(workspace)
            state = composition.LocalConversationRagRuntimeState(store)
            request = _projection_request()
            entry = rag.ConversationRagProjectionCheckpointEntry(
                document=request.documents[0].identity,
                source_digest="sha256:" + "1" * 64,
                fragment_count=1,
            )
            checkpoint = rag.ConversationRagProjectionCheckpoint(
                session_id="session-a",
                schema_id=_VISIBLE_TURN_SCHEMA_ID,
                embedding=state.embedding,
                max_fragment_codepoints=(
                    rag.CONVERSATION_RAG_POLICY.max_fragment_codepoints
                ),
                fragment_overlap_codepoints=(
                    rag.CONVERSATION_RAG_POLICY.fragment_overlap_codepoints
                ),
                entries=(entry,),
                token="sha256:" + "2" * 64,
            )
            present = rag.ConversationRagProjectionState(
                rag.ConversationRagProjectionStateAvailability.PRESENT,
                checkpoint,
            )
            missing = _missing_state()
            corrupt = rag.ConversationRagProjectionState(
                rag.ConversationRagProjectionStateAvailability.CORRUPT,
                None,
            )
            cases = (
                (
                    rag_store.ConversationRagStoreStatus.MISSING,
                    missing,
                    rag.ConversationRagReason.UNAVAILABLE,
                ),
                (
                    rag_store.ConversationRagStoreStatus.READY,
                    present,
                    rag.ConversationRagReason.NONE,
                ),
                (
                    rag_store.ConversationRagStoreStatus.STALE,
                    present,
                    rag.ConversationRagReason.STALE,
                ),
                (
                    rag_store.ConversationRagStoreStatus.PROVIDER_FAILED,
                    present,
                    rag.ConversationRagReason.FAILED,
                ),
                (
                    rag_store.ConversationRagStoreStatus.CORRUPT,
                    corrupt,
                    rag.ConversationRagReason.CORRUPT,
                ),
                (
                    rag_store.ConversationRagStoreStatus.INCOMPATIBLE,
                    present,
                    rag.ConversationRagReason.INCOMPATIBLE,
                ),
            )
            for status, projected, reason in cases:
                snapshot = rag_store.ConversationRagStoreSnapshot(
                    session_id="session-a",
                    status=status,
                    state=projected,
                    provider_id=("local" if projected is present else None),
                    generation_id=("gen-" + "a" * 64 if projected is present else None),
                    counts=rag_store.ConversationRagStoreCounts(),
                )
                with (
                    self.subTest(status=status),
                    mock.patch.object(
                        rag_store.ConversationRagGenerationStore,
                        "inspect",
                        return_value=snapshot,
                    ) as inspect_store,
                ):
                    observed = state.inspect_projection_state("session-a")
                self.assertIs(observed.reason, reason)
                self.assertIs(observed.state, projected)
                inspect_store.assert_called_once_with(
                    "session-a",
                    local.LOCAL_CONVERSATION_RAG_PROVIDER_ID,
                    state.embedding,
                    expected_projection_schema_id=_VISIBLE_TURN_SCHEMA_ID,
                )
            with mock.patch.object(
                rag_store.ConversationRagGenerationStore, "set_status"
            ) as set_status:
                self.assertIsNone(state.mark_projection_stale("session-a"))
            set_status.assert_called_once_with(
                "session-a", rag_store.ConversationRagStoreStatus.STALE
            )
            self.assertEqual(state.workspace_key, str(workspace.root))
            self.assertIs(state.store, store)
            with self.assertRaisesRegex(TypeError, "exact store type"):
                composition.LocalConversationRagRuntimeState(object())  # type: ignore[arg-type]

    def test_concrete_runtime_bundle_is_correlated_and_construction_has_no_io(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Workspace(temporary).initialize()
            rag_root = (
                workspace.root / "agent_states/orchestrator_state/conversation_rag"
            )

            class LowLevel:
                def project_documents(self, *args):  # type: ignore[no-untyped-def]
                    del args

                def rebuild_projection(self, *args):  # type: ignore[no-untyped-def]
                    del args

                def retrieve_semantic(self, *args):  # type: ignore[no-untyped-def]
                    del args

                def retrieve_hybrid(self, *args):  # type: ignore[no-untyped-def]
                    del args

            with mock.patch.object(
                composition,
                "compose_local_conversation_rag",
                return_value=LowLevel(),
            ) as compose_local:
                bundle = composition.compose_local_conversation_rag_runtime(
                    workspace,
                    automatic_enabled=True,
                )
            self.assertIsInstance(
                bundle, composition.LocalConversationRagRuntimeComposition
            )
            self.assertIs(bundle.state.store, bundle.store)
            self.assertTrue(bundle.service.automatic_enabled)
            self.assertFalse(rag_root.exists())
            compose_local.assert_called_once()
            with self.assertRaisesRegex(TypeError, "Workspace"):
                composition.compose_local_conversation_rag_runtime(object())  # type: ignore[arg-type]
            with self.assertRaisesRegex(TypeError, "boolean"):
                composition.compose_local_conversation_rag_runtime(  # type: ignore[arg-type]
                    workspace, automatic_enabled=1
                )


if __name__ == "__main__":
    unittest.main()
