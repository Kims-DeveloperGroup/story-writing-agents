"""Shared fixtures for actual conversation RAG generation-store tests."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from story_writing_agents import Workspace
from story_writing_agents import conversation_rag as rag
from story_writing_agents.conversation_rag_local import (
    LOCAL_CONVERSATION_RAG_PROVIDER_ID,
    LocalConversationRagEmbedder,
)
from story_writing_agents.conversation_rag_store import (
    CONVERSATION_RAG_STORE_SCHEMA_ID,
    ConversationRagGenerationStore,
    ConversationRagStoreError,
    ConversationRagStoreFailure,
    ConversationRagStoreSnapshot,
)
from tests.helpers import pristine_initialized_workspace

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_GENERIC_SOURCE = _REPOSITORY_ROOT / "vendor" / "generic-rag" / "src"
if str(_GENERIC_SOURCE) not in sys.path:
    sys.path.insert(0, str(_GENERIC_SOURCE))

from story_writing_agents.generic_rag_adapter import (  # noqa: E402
    GenericRagConversationService,
)


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


class ConversationRagStoreFixture:
    def new_store(
        self,
    ) -> tuple[Workspace, ConversationRagGenerationStore]:
        workspace = pristine_initialized_workspace(self)
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
