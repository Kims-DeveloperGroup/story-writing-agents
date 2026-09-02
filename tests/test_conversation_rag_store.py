"""Identity-bound atomic conversation RAG generation-store verification."""

from __future__ import annotations

import errno
import hashlib
import inspect
import json
import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from dataclasses import FrozenInstanceError, fields
from pathlib import Path
from unittest import mock

from story_writing_agents import Workspace
from story_writing_agents import conversation_rag as rag
from story_writing_agents import conversation_rag_store as store_module
from story_writing_agents.conversation_rag_local import (
    LOCAL_CONVERSATION_RAG_PROVIDER_ID,
    LocalConversationRagEmbedder,
    LocalConversationRagVectorRanker,
)
from story_writing_agents.conversation_rag_store import (
    CONVERSATION_RAG_STORE_SCHEMA_ID,
    ConversationRagGenerationStore,
    ConversationRagStoreCounts,
    ConversationRagStoreError,
    ConversationRagStoreFailure,
    ConversationRagStoreSnapshot,
    ConversationRagStoreStatus,
)

from tests.helpers import initialize_test_workspace

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_GENERIC_SOURCE = _REPOSITORY_ROOT / "vendor" / "generic-rag" / "src"
if str(_GENERIC_SOURCE) not in sys.path:
    sys.path.insert(0, str(_GENERIC_SOURCE))

from story_writing_agents.generic_rag_adapter import (  # noqa: E402
    GenericRagConversationService,
)

_VISIBLE_TURN_SCHEMA_ID = "story-conversation-rag-visible-turn-v1"


class _EmptyLexicalRetriever:
    def search(
        self,
        _request: rag.ConversationRagRetrievalRequest,
        /,
    ) -> tuple[rag.ConversationRagProviderCandidate, ...]:
        return ()


def _missing_state() -> rag.ConversationRagProjectionState:
    return rag.ConversationRagProjectionState(
        rag.ConversationRagProjectionStateAvailability.MISSING,
        None,
    )


def _request(
    *,
    session_id: str = "session-a",
    schema_id: str = CONVERSATION_RAG_STORE_SCHEMA_ID,
    document_id: str = "document-a",
    revision_id: str = "revision-a",
    text: str = "Alpha 한글 🌙",
) -> rag.ConversationRagProjectionRequest:
    identity = rag.ConversationRagDocumentIdentity(
        rag.ConversationRagDocumentKey(session_id, document_id),
        f"conversation-{document_id.removeprefix('document-')}",
        "turn-a",
        "turn-b",
        1,
        2,
        revision_id,
    )
    return rag.ConversationRagProjectionRequest(
        session_id,
        schema_id,
        LocalConversationRagEmbedder().identity,
        (rag.ConversationRagSourceDocument(identity, text),),
    )


def _service(
    builder: object,
) -> GenericRagConversationService:
    return GenericRagConversationService(
        rag.ConversationRagCollaborators(
            LocalConversationRagEmbedder(),
            builder,
            builder,
            object(),
            _EmptyLexicalRetriever(),
        )
    )


def _publish(
    store: ConversationRagGenerationStore,
    request: rag.ConversationRagProjectionRequest,
    *,
    state: rag.ConversationRagProjectionState | None = None,
    rebuild: bool = True,
) -> tuple[rag.ConversationRagProjectionResult, ConversationRagStoreSnapshot]:
    supplied_state = _missing_state() if state is None else state
    with store.begin_generation(
        LOCAL_CONVERSATION_RAG_PROVIDER_ID,
        request,
        supplied_state,
        rebuild=rebuild,
    ) as builder:
        service = _service(builder)
        result = (
            service.rebuild_projection(request, builder.state)
            if rebuild
            else service.project_documents(request, builder.state)
        )
        return result, builder.publish(result)


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _length_framed_sha256(fields: tuple[str, ...]) -> str:
    digest = hashlib.sha256()
    for field in fields:
        encoded = field.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return f"sha256:{digest.hexdigest()}"


def _persist_oversized_float_exponent(
    session: Path,
    generation: Path,
) -> None:
    vectors_path = generation / "vectors.jsonl"
    vector_rows = vectors_path.read_text(encoding="utf-8").splitlines()
    if len(vector_rows) != 1:
        raise AssertionError("overflow fixture requires exactly one vector record")
    vector = json.loads(vector_rows[0])
    vector["values"][0] = "0x1p+" + "9" * 1_024
    vectors_payload = _canonical_json(vector)
    vectors_path.write_bytes(vectors_payload)

    manifest_payload = (generation / "generic-manifest.json").read_bytes()
    source_map_payload = (generation / "source-map.jsonl").read_bytes()
    manifest_digest = f"sha256:{hashlib.sha256(manifest_payload).hexdigest()}"
    source_map_digest = f"sha256:{hashlib.sha256(source_map_payload).hexdigest()}"
    vectors_digest = f"sha256:{hashlib.sha256(vectors_payload).hexdigest()}"
    generation_digest = _length_framed_sha256(
        (
            "story-conversation-rag-generation-v1",
            "generic_manifest_digest",
            manifest_digest,
            "source_map_digest",
            source_map_digest,
            "vectors_digest",
            vectors_digest,
        )
    )
    generation_id = f"gen-{generation_digest.removeprefix('sha256:')}"

    metadata_path = generation / "generation.json"
    metadata = json.loads(metadata_path.read_bytes())
    metadata.update(
        {
            "generation_digest": generation_digest,
            "generation_id": generation_id,
            "generic_manifest_digest": manifest_digest,
            "payload_bytes": (
                len(manifest_payload) + len(source_map_payload) + len(vectors_payload)
            ),
            "source_map_digest": source_map_digest,
            "vectors_digest": vectors_digest,
        }
    )
    metadata_path.write_bytes(_canonical_json(metadata))
    generation.rename(generation.with_name(generation_id))

    active_path = session / "active.json"
    active = json.loads(active_path.read_bytes())
    active["generation_digest"] = generation_digest
    active["generation_id"] = generation_id
    active_path.write_bytes(_canonical_json(active))
    status_path = session / "status.json"
    status = json.loads(status_path.read_bytes())
    status["generation_id"] = generation_id
    status_path.write_bytes(_canonical_json(status))


def _regular_bytes(path: Path) -> bytes:
    value = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(value.st_mode):
        raise AssertionError(f"not a regular fixture file: {path}")
    return path.read_bytes()


class ConversationRagStoreTests(unittest.TestCase):
    def new_store(
        self,
    ) -> tuple[Workspace, ConversationRagGenerationStore]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        workspace = initialize_test_workspace(temporary.name)
        return workspace, ConversationRagGenerationStore(workspace)

    @staticmethod
    def session_root(workspace: Workspace, session_id: str = "session-a") -> Path:
        return workspace.root.joinpath(
            "agent_states",
            "orchestrator_state",
            "conversation_rag",
            "sessions",
            session_id,
        )

    @classmethod
    def generation_root(
        cls,
        workspace: Workspace,
        snapshot: ConversationRagStoreSnapshot,
    ) -> Path:
        assert snapshot.generation_id is not None
        return cls.session_root(workspace, snapshot.session_id).joinpath(
            "generations",
            snapshot.generation_id,
        )

    def assertStoreFailure(
        self,
        error: ConversationRagStoreError,
        failure: ConversationRagStoreFailure,
    ) -> None:
        self.assertIs(error.failure, failure)
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)
        self.assertNotIn("secret", str(error).lower())
        self.assertNotIn("secret", repr(error).lower())

    def test_exact_surface_values_counts_snapshots_and_errors(self) -> None:
        self.assertEqual(
            store_module.__all__,
            [
                "CONVERSATION_RAG_STORE_SCHEMA_ID",
                "ConversationRagStoreStatus",
                "ConversationRagStoreFailure",
                "ConversationRagStoreCounts",
                "ConversationRagStoreSnapshot",
                "ConversationRagGenerationBuilder",
                "ConversationRagGenerationStore",
                "ConversationRagStoreError",
            ],
        )
        self.assertEqual(
            CONVERSATION_RAG_STORE_SCHEMA_ID,
            "story-conversation-rag-store-v1",
        )
        inspection = inspect.signature(ConversationRagGenerationStore.inspect)
        self.assertEqual(
            tuple(
                (parameter.name, parameter.kind, parameter.default)
                for parameter in inspection.parameters.values()
            ),
            (
                ("self", inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.empty),
                (
                    "session_id",
                    inspect.Parameter.POSITIONAL_ONLY,
                    inspect.Parameter.empty,
                ),
                (
                    "provider_id",
                    inspect.Parameter.POSITIONAL_ONLY,
                    inspect.Parameter.empty,
                ),
                (
                    "embedding",
                    inspect.Parameter.POSITIONAL_ONLY,
                    inspect.Parameter.empty,
                ),
                (
                    "expected_projection_schema_id",
                    inspect.Parameter.KEYWORD_ONLY,
                    CONVERSATION_RAG_STORE_SCHEMA_ID,
                ),
            ),
        )
        self.assertEqual(
            tuple(item.value for item in ConversationRagStoreStatus),
            ("missing", "ready", "stale", "corrupt", "incompatible", "provider_failed"),
        )
        self.assertEqual(
            tuple(item.value for item in ConversationRagStoreFailure),
            (
                "unsupported",
                "busy",
                "unsafe",
                "corrupt",
                "incompatible",
                "provider_failed",
                "confirmation_required",
                "operation_failed",
            ),
        )
        self.assertEqual(
            tuple(field.name for field in fields(ConversationRagStoreCounts)),
            (
                "conversations",
                "events",
                "documents",
                "fragments",
                "source_map_records",
                "vector_records",
                "generations",
                "orphan_generations",
                "source_utf8_bytes",
                "payload_bytes",
                "generation_bytes",
            ),
        )
        counts = ConversationRagStoreCounts()
        with self.assertRaises(FrozenInstanceError):
            counts.documents = 1  # type: ignore[misc]
        with self.assertRaisesRegex(ValueError, "record counts"):
            ConversationRagStoreCounts(fragments=1)
        with self.assertRaisesRegex(TypeError, "must be an integer"):
            ConversationRagStoreCounts(events=True)  # type: ignore[arg-type]
        for failure in ConversationRagStoreFailure:
            with self.subTest(failure=failure):
                self.assertStoreFailure(ConversationRagStoreError(failure), failure)

    def test_projection_schema_expectation_is_validated_before_storage_io(
        self,
    ) -> None:
        workspace, store = self.new_store()
        rag_root = workspace.root / "agent_states/orchestrator_state/conversation_rag"

        class DerivedString(str):
            pass

        cases: tuple[tuple[object, type[Exception], str], ...] = (
            (1, TypeError, "must be a string"),
            (DerivedString("derived"), TypeError, "must be a string"),
            ("", ValueError, "must not be empty"),
            (" \t", ValueError, "whitespace-only"),
            ("\ud800", ValueError, "must be valid UTF-8"),
        )
        for value, error_type, message in cases:
            with (
                self.subTest(value=repr(value)),
                mock.patch.object(store_module, "_open_session_scope") as opened,
                self.assertRaisesRegex(error_type, message),
            ):
                store.inspect(
                    "session-a",
                    "local",
                    LocalConversationRagEmbedder().identity,
                    expected_projection_schema_id=value,  # type: ignore[arg-type]
                )
            opened.assert_not_called()

        with (
            mock.patch.object(store_module, "_open_session_scope") as opened,
            self.assertRaises(TypeError),
        ):
            store.inspect(  # type: ignore[call-arg]
                "session-a",
                "local",
                LocalConversationRagEmbedder().identity,
                _VISIBLE_TURN_SCHEMA_ID,
            )
        opened.assert_not_called()
        self.assertFalse(rag_root.exists())

    def test_construction_and_missing_inspection_do_not_create_store_state(
        self,
    ) -> None:
        workspace, store = self.new_store()
        rag_root = workspace.root / "agent_states/orchestrator_state/conversation_rag"
        self.assertIs(store.workspace, workspace)
        self.assertFalse(rag_root.exists())
        snapshot = store.inspect(
            "session-a",
            LOCAL_CONVERSATION_RAG_PROVIDER_ID,
            LocalConversationRagEmbedder().identity,
        )
        self.assertEqual(
            (
                snapshot.status,
                snapshot.state.availability,
                snapshot.provider_id,
                snapshot.generation_id,
                snapshot.counts,
            ),
            (
                ConversationRagStoreStatus.MISSING,
                rag.ConversationRagProjectionStateAvailability.MISSING,
                None,
                None,
                ConversationRagStoreCounts(),
            ),
        )
        self.assertFalse(rag_root.exists())
        with self.assertRaisesRegex(TypeError, "requires a Workspace"):
            ConversationRagGenerationStore(Path("workspace"))  # type: ignore[arg-type]

    def test_deterministic_publish_topology_canonical_bytes_counts_and_reader(
        self,
    ) -> None:
        workspace, store = self.new_store()
        request = _request()
        result, published = _publish(store, request)
        self.assertEqual(
            published.generation_id,
            "gen-4b3c4b323ba86467a4248498876aba0b6842ced1dde54debe81539ca2411290c",
        )
        self.assertEqual(result.checkpoint, published.state.checkpoint)
        self.assertEqual(
            published.counts,
            ConversationRagStoreCounts(
                conversations=1,
                events=2,
                documents=1,
                fragments=1,
                source_map_records=1,
                vector_records=1,
                generations=1,
                source_utf8_bytes=17,
                payload_bytes=4404,
                generation_bytes=5438,
            ),
        )
        session = self.session_root(workspace)
        generation = self.generation_root(workspace, published)
        self.assertEqual(
            {path.name for path in session.iterdir()},
            {".lock", "active.json", "generations", "status.json"},
        )
        self.assertEqual(
            {path.name for path in generation.iterdir()},
            {
                "generation.json",
                "generic-manifest.json",
                "source-map.jsonl",
                "vectors.jsonl",
            },
        )
        for directory in (session, session / "generations", generation):
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
        for path in (
            session / ".lock",
            session / "active.json",
            session / "status.json",
            *generation.iterdir(),
        ):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(path.stat().st_nlink, 1)

        digests = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (
                session / "active.json",
                session / "status.json",
                *generation.iterdir(),
            )
        }
        self.assertEqual(
            digests,
            {
                "active.json": "e6714c9885dda516bb5b946dbd4b81cc4c1597bd75d71149ff800c673acf4df1",
                "status.json": "1d507581d59b5a48eb026844e36d60ed46c0d9e2fc73821dffd4636e7aea3eb5",
                "generation.json": "6ebd94f3ae70434eee12b59d90e50e56c9450d9abfea3c9387b5bd18c10ba792",
                "generic-manifest.json": "1cdedd0432878a9ed66c414d5a9806fb1ac46d2ce8554bd5f33132c5eb0f44b8",
                "source-map.jsonl": "5f5797d15e501486cf6cd536e12d21daf4d7431473b4aa5e7bfb55b0094af191",
                "vectors.jsonl": "6bf77693f88fdc52b5c2ae4305929f724e45730a376ca0062ba15ba337afdea6",
            },
        )
        active = json.loads((session / "active.json").read_bytes())
        self.assertEqual(
            set(active),
            {"generation_digest", "generation_id", "schema_id", "session_id"},
        )
        self.assertEqual(
            _canonical_json(active), (session / "active.json").read_bytes()
        )
        source_row = json.loads((generation / "source-map.jsonl").read_bytes())
        vector_row = json.loads((generation / "vectors.jsonl").read_bytes())
        self.assertEqual(source_row["text"], "Alpha 한글 🌙")
        self.assertEqual(len(vector_row["values"]), 256)
        self.assertTrue(
            all(
                float.hex(float.fromhex(value)) == value
                for value in vector_row["values"]
            )
        )
        self.assertNotIn("Alpha 한글 🌙", repr(published))

        reopened = ConversationRagGenerationStore(workspace).inspect(
            "session-a",
            "local",
            LocalConversationRagEmbedder().identity,
        )
        self.assertEqual(reopened, published)
        with store.open_active_reader(
            reopened,
            LocalConversationRagVectorRanker(),
        ) as reader:
            candidates = reader.search(
                rag.ConversationRagRetrievalRequest("session-a", "alpha", 3),
                LocalConversationRagEmbedder().embed(("alpha",))[0],
            )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source.document.key.document_id, "document-a")
        with self.assertRaises(ConversationRagStoreError) as closed:
            reader.search(
                rag.ConversationRagRetrievalRequest("session-a", "alpha", 1),
                LocalConversationRagEmbedder().embed(("alpha",))[0],
            )
        self.assertStoreFailure(
            closed.exception,
            ConversationRagStoreFailure.OPERATION_FAILED,
        )

    def test_visible_projection_preserves_envelope_reopens_and_reinspects_reader(
        self,
    ) -> None:
        workspace, store = self.new_store()
        secret = "VISIBLE_SOURCE_SENTINEL"
        request = _request(schema_id=_VISIBLE_TURN_SCHEMA_ID, text=secret)
        result, published = _publish(store, request)
        session = self.session_root(workspace)
        generation = self.generation_root(workspace, published)

        active = json.loads((session / "active.json").read_bytes())
        status = json.loads((session / "status.json").read_bytes())
        manifest = json.loads((generation / "generic-manifest.json").read_bytes())
        metadata = json.loads((generation / "generation.json").read_bytes())
        self.assertEqual(
            (
                active["schema_id"],
                status["schema_id"],
                manifest["schema_id"],
                metadata["schema_id"],
            ),
            (CONVERSATION_RAG_STORE_SCHEMA_ID,) * 4,
        )
        self.assertEqual(
            (
                manifest["projection_schema_id"],
                metadata["projection_schema_id"],
                result.checkpoint.schema_id,
            ),
            (_VISIBLE_TURN_SCHEMA_ID,) * 3,
        )

        reopened_store = ConversationRagGenerationStore(workspace)
        reopened = reopened_store.inspect(
            "session-a",
            "local",
            LocalConversationRagEmbedder().identity,
            expected_projection_schema_id=_VISIBLE_TURN_SCHEMA_ID,
        )
        self.assertEqual(reopened, published)
        legacy_view = reopened_store.inspect(
            "session-a",
            "local",
            LocalConversationRagEmbedder().identity,
        )
        self.assertEqual(
            (
                legacy_view.status,
                legacy_view.state.availability,
                legacy_view.state.checkpoint,
                legacy_view.provider_id,
                legacy_view.generation_id,
                legacy_view.counts,
            ),
            (
                ConversationRagStoreStatus.INCOMPATIBLE,
                rag.ConversationRagProjectionStateAvailability.PRESENT,
                published.state.checkpoint,
                published.provider_id,
                published.generation_id,
                published.counts,
            ),
        )
        self.assertNotIn(secret, repr(legacy_view))

        with reopened_store.open_active_reader(
            reopened,
            LocalConversationRagVectorRanker(),
        ) as reader:
            candidates = reader.search(
                rag.ConversationRagRetrievalRequest("session-a", "visible", 1),
                LocalConversationRagEmbedder().embed(("visible",))[0],
            )
        self.assertEqual(len(candidates), 1)

        _updated_result, updated = _publish(
            reopened_store,
            _request(
                schema_id=_VISIBLE_TURN_SCHEMA_ID,
                revision_id="revision-b",
                text="Updated visible source",
            ),
            state=reopened.state,
        )
        self.assertNotEqual(updated.generation_id, reopened.generation_id)
        with self.assertRaises(ConversationRagStoreError) as stale_reader:
            reopened_store.open_active_reader(
                reopened,
                LocalConversationRagVectorRanker(),
            )
        self.assertStoreFailure(
            stale_reader.exception,
            ConversationRagStoreFailure.INCOMPATIBLE,
        )

    def test_persisted_projection_schema_disagreement_is_corrupt(self) -> None:
        workspace, store = self.new_store()
        _result, ready = _publish(
            store,
            _request(schema_id=_VISIBLE_TURN_SCHEMA_ID),
        )
        metadata_path = self.generation_root(workspace, ready) / "generation.json"
        metadata = json.loads(metadata_path.read_bytes())
        metadata["projection_schema_id"] = "different-projection-v1"
        metadata_path.write_bytes(_canonical_json(metadata))

        observed = store.inspect(
            "session-a",
            "local",
            LocalConversationRagEmbedder().identity,
            expected_projection_schema_id=_VISIBLE_TURN_SCHEMA_ID,
        )
        self.assertEqual(
            (
                observed.status,
                observed.state.availability,
                observed.state.checkpoint,
                observed.provider_id,
                observed.generation_id,
                observed.counts,
            ),
            (
                ConversationRagStoreStatus.CORRUPT,
                rag.ConversationRagProjectionStateAvailability.CORRUPT,
                None,
                None,
                None,
                ConversationRagStoreCounts(),
            ),
        )

    def test_rebuild_is_deterministic_restart_not_resume_and_chunks_ranges(
        self,
    ) -> None:
        workspace, store = self.new_store()
        request = _request(text="a" * 900)
        _result, first = _publish(store, request)
        generation = self.generation_root(workspace, first)
        source_rows = [
            json.loads(line)
            for line in (generation / "source-map.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        self.assertEqual(
            tuple(
                (row["start_codepoint"], row["end_codepoint"]) for row in source_rows
            ),
            ((0, 800), (720, 900)),
        )
        self.assertEqual(first.counts.fragments, 2)
        active_before = (self.session_root(workspace) / "active.json").read_bytes()

        partial = self.session_root(workspace) / "generations" / ("build-" + "a" * 32)
        partial.mkdir(mode=0o700)
        (partial / ".source-map.build").write_bytes(b"partial secret")
        os.chmod(partial / ".source-map.build", 0o600)
        _again_result, again = _publish(store, request, state=first.state)
        self.assertEqual(again.generation_id, first.generation_id)
        self.assertEqual(
            (self.session_root(workspace) / "active.json").read_bytes(), active_before
        )
        self.assertFalse(partial.exists())
        self.assertEqual(again.counts.generations, 1)
        self.assertEqual(again.counts.orphan_generations, 0)

        _unchanged, unchanged = _publish(
            store,
            request,
            state=again.state,
            rebuild=False,
        )
        self.assertEqual(unchanged.generation_id, first.generation_id)
        self.assertEqual(unchanged.counts.generations, 1)

    def test_legacy_generation_requires_atomic_rebuild_for_visible_schema(
        self,
    ) -> None:
        workspace, store = self.new_store()
        _legacy_result, legacy = _publish(store, _request())
        session = self.session_root(workspace)
        active = session / "active.json"
        active_before = active.read_bytes()
        legacy_generation = self.generation_root(workspace, legacy)
        legacy_bytes = {
            path.name: _regular_bytes(path) for path in legacy_generation.iterdir()
        }
        incompatible = store.inspect(
            "session-a",
            "local",
            LocalConversationRagEmbedder().identity,
            expected_projection_schema_id=_VISIBLE_TURN_SCHEMA_ID,
        )
        self.assertEqual(
            (incompatible.status, incompatible.state, incompatible.counts),
            (
                ConversationRagStoreStatus.INCOMPATIBLE,
                legacy.state,
                legacy.counts,
            ),
        )
        visible_request = _request(
            schema_id=_VISIBLE_TURN_SCHEMA_ID,
            revision_id="revision-visible",
            text="Visible replacement",
        )
        with self.assertRaises(ConversationRagStoreError) as incremental:
            store.begin_generation(
                "local",
                visible_request,
                incompatible.state,
                rebuild=False,
            )
        self.assertStoreFailure(
            incremental.exception,
            ConversationRagStoreFailure.INCOMPATIBLE,
        )
        self.assertEqual(active.read_bytes(), active_before)

        _visible_result, visible = _publish(
            store,
            visible_request,
            state=incompatible.state,
        )
        self.assertNotEqual(active.read_bytes(), active_before)
        self.assertNotEqual(visible.generation_id, legacy.generation_id)
        self.assertEqual(
            {path.name: _regular_bytes(path) for path in legacy_generation.iterdir()},
            legacy_bytes,
        )
        legacy_manifest = json.loads(
            (legacy_generation / "generic-manifest.json").read_bytes()
        )
        self.assertEqual(
            legacy_manifest["projection_schema_id"],
            CONVERSATION_RAG_STORE_SCHEMA_ID,
        )
        self.assertEqual(
            (visible.counts.generations, visible.counts.orphan_generations),
            (2, 1),
        )
        reopened = ConversationRagGenerationStore(workspace)
        current = reopened.inspect(
            "session-a",
            "local",
            LocalConversationRagEmbedder().identity,
            expected_projection_schema_id=_VISIBLE_TURN_SCHEMA_ID,
        )
        self.assertEqual(current, visible)
        legacy_view = reopened.inspect(
            "session-a",
            "local",
            LocalConversationRagEmbedder().identity,
        )
        self.assertIs(legacy_view.status, ConversationRagStoreStatus.INCOMPATIBLE)
        self.assertEqual(legacy_view.state, visible.state)

    def test_status_is_advisory_active_is_sole_commit_and_content_free(self) -> None:
        workspace, store = self.new_store()
        secret = "PRIVATE_SOURCE_SENTINEL"
        _result, ready = _publish(store, _request(text=secret))
        session = self.session_root(workspace)
        for name in ("active.json", "status.json"):
            self.assertNotIn(secret.encode(), (session / name).read_bytes())

        store.set_status("session-a", ConversationRagStoreStatus.STALE)
        stale = store.inspect(
            "session-a", "local", LocalConversationRagEmbedder().identity
        )
        self.assertIs(stale.status, ConversationRagStoreStatus.STALE)
        self.assertEqual(stale.state, ready.state)
        store.set_status("session-a", ConversationRagStoreStatus.PROVIDER_FAILED)
        failed = store.inspect(
            "session-a", "local", LocalConversationRagEmbedder().identity
        )
        self.assertIs(failed.status, ConversationRagStoreStatus.PROVIDER_FAILED)
        self.assertEqual(failed.state, ready.state)
        self.assertNotIn(secret, repr(failed))
        with self.assertRaisesRegex(ValueError, "only stale or provider-failed"):
            store.set_status("session-a", ConversationRagStoreStatus.READY)

        (session / "status.json").unlink()
        self.assertIs(
            store.inspect(
                "session-a", "local", LocalConversationRagEmbedder().identity
            ).status,
            ConversationRagStoreStatus.READY,
        )
        misleading = {
            "generation_id": "gen-" + "f" * 64,
            "schema_id": CONVERSATION_RAG_STORE_SCHEMA_ID,
            "session_id": "session-a",
            "status": "ready",
        }
        (session / "status.json").write_bytes(_canonical_json(misleading))
        os.chmod(session / "status.json", 0o600)
        observed = store.inspect(
            "session-a", "local", LocalConversationRagEmbedder().identity
        )
        self.assertIs(observed.status, ConversationRagStoreStatus.STALE)
        self.assertEqual(observed.generation_id, ready.generation_id)

    def test_advisory_status_never_masks_identity_or_schema_incompatibility(
        self,
    ) -> None:
        _workspace, store = self.new_store()
        secret = "PRIVATE_COMPATIBILITY_SENTINEL"
        _result, ready = _publish(
            store,
            _request(schema_id=_VISIBLE_TURN_SCHEMA_ID, text=secret),
        )
        embedding = LocalConversationRagEmbedder().identity
        other_embedding = rag.ConversationRagEmbeddingIdentity(
            "different-local-model-v1",
            embedding.dimensions,
        )
        mismatches = (
            ("provider", "different-provider", embedding, _VISIBLE_TURN_SCHEMA_ID),
            ("embedding", "local", other_embedding, _VISIBLE_TURN_SCHEMA_ID),
            ("schema", "local", embedding, CONVERSATION_RAG_STORE_SCHEMA_ID),
        )
        advisories = (
            (None, ConversationRagStoreStatus.READY),
            (
                ConversationRagStoreStatus.STALE,
                ConversationRagStoreStatus.STALE,
            ),
            (
                ConversationRagStoreStatus.PROVIDER_FAILED,
                ConversationRagStoreStatus.PROVIDER_FAILED,
            ),
        )
        for advisory, compatible_status in advisories:
            if advisory is not None:
                store.set_status("session-a", advisory)
            compatible = store.inspect(
                "session-a",
                "local",
                embedding,
                expected_projection_schema_id=_VISIBLE_TURN_SCHEMA_ID,
            )
            self.assertIs(compatible.status, compatible_status)
            for label, provider, expected_embedding, schema_id in mismatches:
                with self.subTest(advisory=advisory, mismatch=label):
                    observed = store.inspect(
                        "session-a",
                        provider,
                        expected_embedding,
                        expected_projection_schema_id=schema_id,
                    )
                    self.assertEqual(
                        (
                            observed.status,
                            observed.state,
                            observed.provider_id,
                            observed.generation_id,
                            observed.counts,
                        ),
                        (
                            ConversationRagStoreStatus.INCOMPATIBLE,
                            ready.state,
                            ready.provider_id,
                            ready.generation_id,
                            ready.counts,
                        ),
                    )
                    self.assertNotIn(secret, repr(observed))

    def test_successful_active_rename_is_the_final_publish_operation(self) -> None:
        _workspace, store = self.new_store()
        builder = store.begin_generation(
            "local",
            _request(),
            _missing_state(),
            rebuild=True,
        )
        result = _service(builder).rebuild_projection(_request(), builder.state)
        operations: list[tuple[str, object]] = []
        real_close = store_module.os.close
        real_fsync = store_module.os.fsync
        real_rename = store_module.os.rename
        real_unlink = store_module.os.unlink

        def traced_close(descriptor: int) -> None:
            operations.append(("close", descriptor))
            real_close(descriptor)

        def traced_fsync(descriptor: int) -> None:
            operations.append(("fsync", stat.S_ISDIR(os.fstat(descriptor).st_mode)))
            real_fsync(descriptor)

        def traced_rename(
            source: str,
            target: str,
            *,
            src_dir_fd: int | None = None,
            dst_dir_fd: int | None = None,
        ) -> None:
            operations.append(("rename", target))
            real_rename(
                source,
                target,
                src_dir_fd=src_dir_fd,
                dst_dir_fd=dst_dir_fd,
            )

        def traced_unlink(path: str, *, dir_fd: int | None = None) -> None:
            operations.append(("unlink", path))
            real_unlink(path, dir_fd=dir_fd)

        try:
            with (
                mock.patch.object(store_module.os, "close", side_effect=traced_close),
                mock.patch.object(store_module.os, "fsync", side_effect=traced_fsync),
                mock.patch.object(
                    store_module.os,
                    "rename",
                    side_effect=traced_rename,
                ),
                mock.patch.object(
                    store_module.os,
                    "unlink",
                    side_effect=traced_unlink,
                ),
            ):
                snapshot = builder.publish(result)
        finally:
            builder.close()
        self.assertIs(snapshot.status, ConversationRagStoreStatus.READY)
        self.assertEqual(operations[-1], ("rename", "active.json"))
        self.assertLess(
            operations.index(("rename", "status.json")),
            operations.index(("rename", "active.json")),
        )
        self.assertEqual(
            [
                operation
                for operation in operations
                if operation == ("rename", "active.json")
            ],
            [("rename", "active.json")],
        )

    def test_final_precommit_failures_preserve_old_pointer_and_retry(self) -> None:
        for injected in (
            OSError(errno.EIO, "secret final durability failure"),
            KeyboardInterrupt("secret final durability control"),
            SystemExit(9),
        ):
            with self.subTest(injected=type(injected).__name__):
                workspace, store = self.new_store()
                _result, first = _publish(store, _request())
                session = self.session_root(workspace)
                active = session / "active.json"
                active_before = active.read_bytes()
                old_generation = self.generation_root(workspace, first)
                old_generation_before = {
                    path.name: _regular_bytes(path) for path in old_generation.iterdir()
                }
                updated = _request(
                    schema_id=_VISIBLE_TURN_SCHEMA_ID,
                    revision_id="revision-b",
                    text="Updated beta",
                )
                real_fsync = store_module.os.fsync
                real_rename = store_module.os.rename
                publication_state = {
                    "active_rename_attempted": False,
                    "precommit_failed": False,
                    "status_committed": False,
                }

                def track_rename(
                    source: str,
                    target: str,
                    *,
                    src_dir_fd: int | None = None,
                    dst_dir_fd: int | None = None,
                    rename: object = real_rename,
                    state: dict[str, bool] = publication_state,
                ) -> None:
                    if target == "status.json":
                        state["status_committed"] = True
                    elif target == "active.json":
                        state["active_rename_attempted"] = True
                    assert callable(rename)
                    rename(
                        source,
                        target,
                        src_dir_fd=src_dir_fd,
                        dst_dir_fd=dst_dir_fd,
                    )

                def fail_final_precommit(
                    descriptor: int,
                    *,
                    failure: BaseException = injected,
                    fsync: object = real_fsync,
                    state: dict[str, bool] = publication_state,
                ) -> None:
                    is_directory = stat.S_ISDIR(os.fstat(descriptor).st_mode)
                    if (
                        state["status_committed"]
                        and is_directory
                        and not state["precommit_failed"]
                    ):
                        state["precommit_failed"] = True
                        raise failure
                    assert callable(fsync)
                    fsync(descriptor)

                with store.begin_generation(
                    "local",
                    updated,
                    first.state,
                    rebuild=True,
                ) as builder:
                    result = _service(builder).rebuild_projection(
                        updated, builder.state
                    )
                    with (
                        mock.patch.object(
                            store_module.os,
                            "fsync",
                            side_effect=fail_final_precommit,
                        ),
                        mock.patch.object(
                            store_module.os,
                            "rename",
                            side_effect=track_rename,
                        ),
                    ):
                        if isinstance(injected, OSError):
                            with self.assertRaises(ConversationRagStoreError) as caught:
                                builder.publish(result)
                            self.assertStoreFailure(
                                caught.exception,
                                ConversationRagStoreFailure.OPERATION_FAILED,
                            )
                        else:
                            with self.assertRaises(type(injected)) as caught:
                                builder.publish(result)
                            self.assertIs(caught.exception, injected)
                self.assertTrue(publication_state["precommit_failed"])
                self.assertFalse(publication_state["active_rename_attempted"])
                self.assertEqual(active.read_bytes(), active_before)
                observed = store.inspect(
                    "session-a",
                    "local",
                    LocalConversationRagEmbedder().identity,
                )
                self.assertEqual(observed.generation_id, first.generation_id)
                self.assertIs(observed.status, ConversationRagStoreStatus.STALE)
                self.assertEqual(observed.state.checkpoint, first.state.checkpoint)
                self.assertEqual(
                    {
                        path.name: _regular_bytes(path)
                        for path in old_generation.iterdir()
                    },
                    old_generation_before,
                )
                _retry_result, retry = _publish(
                    store,
                    updated,
                    state=observed.state,
                )
                self.assertIs(retry.status, ConversationRagStoreStatus.READY)
                self.assertNotEqual(retry.generation_id, first.generation_id)
                self.assertEqual(
                    (retry.counts.generations, retry.counts.orphan_generations),
                    (2, 1),
                )
                visible = store.inspect(
                    "session-a",
                    "local",
                    LocalConversationRagEmbedder().identity,
                    expected_projection_schema_id=_VISIBLE_TURN_SCHEMA_ID,
                )
                self.assertEqual(visible, retry)
                legacy = store.inspect(
                    "session-a",
                    "local",
                    LocalConversationRagEmbedder().identity,
                )
                self.assertIs(
                    legacy.status,
                    ConversationRagStoreStatus.INCOMPATIBLE,
                )
                self.assertEqual(legacy.state, retry.state)

    def test_canonical_corruption_size_and_generation_parity_fail_closed(self) -> None:
        cases = ("bom", "pretty", "metadata_count", "jsonl_blank", "oversized")
        for case in cases:
            with self.subTest(case=case):
                workspace, store = self.new_store()
                _result, snapshot = _publish(store, _request())
                session = self.session_root(workspace)
                generation = self.generation_root(workspace, snapshot)
                if case == "bom":
                    active = session / "active.json"
                    active.write_bytes(b"\xef\xbb\xbf" + active.read_bytes())
                elif case == "pretty":
                    active = session / "active.json"
                    active.write_text(
                        json.dumps(json.loads(active.read_bytes()), indent=2),
                        encoding="utf-8",
                    )
                elif case == "metadata_count":
                    metadata_path = generation / "generation.json"
                    metadata = json.loads(metadata_path.read_bytes())
                    metadata["fragment_count"] = 2
                    metadata_path.write_bytes(_canonical_json(metadata))
                elif case == "jsonl_blank":
                    path = generation / "vectors.jsonl"
                    path.write_bytes(path.read_bytes() + b"\n")
                else:
                    active = session / "active.json"
                    with active.open("r+b") as handle:
                        handle.truncate(store_module._MAX_POINTER_BYTES + 1)
                corrupt = store.inspect(
                    "session-a",
                    "local",
                    LocalConversationRagEmbedder().identity,
                )
                self.assertIs(corrupt.status, ConversationRagStoreStatus.CORRUPT)
                self.assertIs(
                    corrupt.state.availability,
                    rag.ConversationRagProjectionStateAvailability.CORRUPT,
                )
                self.assertIsNone(corrupt.generation_id)

    def test_bounded_hostile_json_and_float_exponents_are_fixed_corruption(
        self,
    ) -> None:
        cases: tuple[tuple[str, bytes | None], ...] = (
            ("deep_nesting", b"[" * 2_048 + b"0" + b"]" * 2_048 + b"\n"),
            ("nan", b"NaN\n"),
            ("infinity", b"Infinity\n"),
            ("overflow_decimal", b"1e400\n"),
            ("oversized_float_hex", None),
        )
        for case, active_payload in cases:
            with self.subTest(case=case):
                workspace, store = self.new_store()
                _result, ready = _publish(store, _request())
                session = self.session_root(workspace)
                generation = self.generation_root(workspace, ready)
                if active_payload is None:
                    _persist_oversized_float_exponent(session, generation)
                    active = json.loads((session / "active.json").read_bytes())
                    mutated = session / "generations" / active["generation_id"]
                    self.assertLess(
                        (mutated / "vectors.jsonl").stat().st_size,
                        store_module._MAX_JSONL_RECORD_BYTES,
                    )
                else:
                    self.assertLess(
                        len(active_payload),
                        store_module._MAX_POINTER_BYTES,
                    )
                    (session / "active.json").write_bytes(active_payload)

                observed = store.inspect(
                    "session-a",
                    "local",
                    LocalConversationRagEmbedder().identity,
                )
                self.assertIs(observed.status, ConversationRagStoreStatus.CORRUPT)
                with self.assertRaises(ConversationRagStoreError) as caught:
                    store.open_active_reader(ready, LocalConversationRagVectorRanker())
                self.assertStoreFailure(
                    caught.exception,
                    ConversationRagStoreFailure.CORRUPT,
                )

    def test_request_bounds_are_checked_before_store_mutation(self) -> None:
        workspace, store = self.new_store()
        rag_root = workspace.root / "agent_states/orchestrator_state/conversation_rag"
        with (
            mock.patch.object(store_module, "_MAX_SOURCE_UTF8_BYTES", 5),
            self.assertRaisesRegex(ValueError, "source byte budget"),
        ):
            store.begin_generation(
                "local", _request(text="한글"), _missing_state(), rebuild=True
            )
        self.assertFalse(rag_root.exists())
        with (
            mock.patch.object(store_module, "_MAX_RECORDS", 1),
            self.assertRaisesRegex(ValueError, "vector record budget"),
        ):
            store.begin_generation(
                "local", _request(text="a" * 900), _missing_state(), rebuild=True
            )
        self.assertFalse(rag_root.exists())

    def test_symlink_hardlink_mode_namespace_and_identity_races_are_contained(
        self,
    ) -> None:
        for case in ("symlink", "hardlink", "mode", "unexpected", "identity_race"):
            with self.subTest(case=case):
                workspace, store = self.new_store()
                _result, snapshot = _publish(store, _request())
                session = self.session_root(workspace)
                active = session / "active.json"
                generation = self.generation_root(workspace, snapshot)
                outside = workspace.root.parent / f"outside-{case}-{id(store)}"
                outside.write_bytes(b"outside secret bytes")
                self.addCleanup(lambda path=outside: path.unlink(missing_ok=True))
                if case == "symlink":
                    active.unlink()
                    try:
                        active.symlink_to(outside)
                    except OSError as exc:
                        self.skipTest(f"file symlink creation is unsupported: {exc}")
                elif case == "hardlink":
                    active.unlink()
                    try:
                        os.link(outside, active)
                    except OSError as exc:
                        self.skipTest(f"hard-link creation is unsupported: {exc}")
                elif case == "mode":
                    generation.chmod(0o755)
                elif case == "unexpected":
                    (session / "generations" / "unexpected").write_bytes(b"secret")
                else:
                    replacement = outside.with_name(outside.name + "-replacement")
                    replacement.write_bytes(active.read_bytes())
                    replacement.chmod(0o600)
                    self.addCleanup(
                        lambda path=replacement: path.unlink(missing_ok=True)
                    )
                    real_verify = store_module._verify_regular
                    active_verifications = 0

                    def replace_between_open_and_verify(
                        parent: int,
                        name: str,
                        descriptor: int,
                        *,
                        replacement_path: Path = replacement,
                        active_path: Path = active,
                        verify_regular: object = real_verify,
                    ) -> os.stat_result:
                        nonlocal active_verifications
                        if name == "active.json":
                            active_verifications += 1
                            if active_verifications == 2:
                                os.replace(replacement_path, active_path)
                        assert callable(verify_regular)
                        return verify_regular(parent, name, descriptor)

                    with (
                        mock.patch.object(
                            store_module,
                            "_verify_regular",
                            side_effect=replace_between_open_and_verify,
                        ),
                        self.assertRaises(ConversationRagStoreError) as caught,
                    ):
                        store.inspect(
                            "session-a",
                            "local",
                            LocalConversationRagEmbedder().identity,
                        )
                    self.assertStoreFailure(
                        caught.exception, ConversationRagStoreFailure.UNSAFE
                    )
                    self.assertEqual(outside.read_bytes(), b"outside secret bytes")
                    continue
                with self.assertRaises(ConversationRagStoreError) as caught:
                    store.inspect(
                        "session-a", "local", LocalConversationRagEmbedder().identity
                    )
                self.assertStoreFailure(
                    caught.exception, ConversationRagStoreFailure.UNSAFE
                )
                self.assertEqual(outside.read_bytes(), b"outside secret bytes")

    def test_unsupported_primitives_and_control_cleanup_leave_no_held_lock(
        self,
    ) -> None:
        workspace, store = self.new_store()
        with (
            mock.patch.object(store_module.os, "O_NOFOLLOW", 0),
            self.assertRaises(ConversationRagStoreError) as unsupported,
        ):
            store.inspect("session-a", "local", LocalConversationRagEmbedder().identity)
        self.assertStoreFailure(
            unsupported.exception,
            ConversationRagStoreFailure.UNSUPPORTED,
        )
        self.assertFalse(
            (
                workspace.root / "agent_states/orchestrator_state/conversation_rag"
            ).exists()
        )

        for control in (KeyboardInterrupt("stop"), SystemExit(5)):
            with self.subTest(control=type(control).__name__):
                builder = store.begin_generation(
                    "local",
                    _request(),
                    _missing_state(),
                    rebuild=True,
                )
                with self.assertRaises(type(control)) as caught, builder:
                    raise control
                self.assertIs(caught.exception, control)
                recovered = store.begin_generation(
                    "local",
                    _request(),
                    _missing_state(),
                    rebuild=True,
                )
                recovered.close()

    @unittest.skipUnless(
        store_module._fcntl is not None,
        "cross-process store contention requires POSIX advisory locking",
    )
    def test_cross_process_session_contention_times_out_and_recovers(self) -> None:
        workspace, store = self.new_store()
        script = textwrap.dedent(
            f"""
            import sys
            sys.path.insert(0, {str(_REPOSITORY_ROOT / "src")!r})
            from story_writing_agents import Workspace
            from story_writing_agents import conversation_rag as rag
            from story_writing_agents.conversation_rag_local import LocalConversationRagEmbedder
            from story_writing_agents.conversation_rag_store import CONVERSATION_RAG_STORE_SCHEMA_ID, ConversationRagGenerationStore
            identity = rag.ConversationRagDocumentIdentity(
                rag.ConversationRagDocumentKey("session-a", "document-a"),
                "conversation-a", "turn-a", "turn-b", 1, 2, "revision-a"
            )
            request = rag.ConversationRagProjectionRequest(
                "session-a", CONVERSATION_RAG_STORE_SCHEMA_ID,
                LocalConversationRagEmbedder().identity,
                (rag.ConversationRagSourceDocument(identity, "alpha"),),
            )
            state = rag.ConversationRagProjectionState(
                rag.ConversationRagProjectionStateAvailability.MISSING, None
            )
            builder = ConversationRagGenerationStore(Workspace(sys.argv[1])).begin_generation(
                "local", request, state, rebuild=True
            )
            print("locked", flush=True)
            sys.stdin.read(1)
            builder.close()
            """
        )
        environment = os.environ.copy()
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        process = subprocess.Popen(
            [sys.executable, "-B", "-W", "error", "-c", script, str(workspace.root)],
            cwd=_REPOSITORY_ROOT,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        def cleanup() -> None:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()

        self.addCleanup(cleanup)
        assert process.stdout is not None
        self.assertEqual(process.stdout.readline(), "locked\n")
        with (
            mock.patch.object(store_module, "_LOCK_TIMEOUT_SECONDS", 0.05),
            self.assertRaises(ConversationRagStoreError) as busy,
        ):
            store.inspect("session-a", "local", LocalConversationRagEmbedder().identity)
        self.assertStoreFailure(busy.exception, ConversationRagStoreFailure.BUSY)
        assert process.stdin is not None
        process.stdin.write("x")
        process.stdin.flush()
        process.wait(timeout=5)
        stderr = process.stderr.read() if process.stderr is not None else ""
        self.assertEqual(process.returncode, 0, stderr)
        recovered = store.inspect(
            "session-a", "local", LocalConversationRagEmbedder().identity
        )
        self.assertIs(recovered.status, ConversationRagStoreStatus.MISSING)
        self.assertEqual(recovered.counts.orphan_generations, 1)

    def test_confirmed_purge_is_single_session_and_derived_only(self) -> None:
        workspace, store = self.new_store()
        _result_a, first = _publish(store, _request(session_id="session-a"))
        _result_b, second = _publish(store, _request(session_id="session-b"))
        config = workspace.root / "config/system_defaults.json"
        config_before = config.read_bytes()
        with self.assertRaises(ConversationRagStoreError) as confirmation:
            store.purge("session-a", confirmed=False)
        self.assertStoreFailure(
            confirmation.exception,
            ConversationRagStoreFailure.CONFIRMATION_REQUIRED,
        )
        self.assertEqual(
            store.inspect(
                "session-a", "local", LocalConversationRagEmbedder().identity
            ),
            first,
        )
        removed = store.purge("session-a", confirmed=True)
        self.assertEqual(removed.generations, 1)
        self.assertEqual(removed.orphan_generations, 0)
        self.assertIs(
            store.inspect(
                "session-a", "local", LocalConversationRagEmbedder().identity
            ).status,
            ConversationRagStoreStatus.MISSING,
        )
        self.assertEqual(
            store.inspect(
                "session-b", "local", LocalConversationRagEmbedder().identity
            ),
            second,
        )
        self.assertEqual(config.read_bytes(), config_before)
        self.assertTrue((workspace.root / "discussion_logs").is_dir())
        self.assertTrue((workspace.root / "manuscript").is_dir())


if __name__ == "__main__":
    unittest.main()
