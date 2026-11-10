'Owned value_inputs.py declarations; mutable objects remain fresh per original call.'
from __future__ import annotations
import hashlib
from story_writing_agents.agent_context.actions import ContextSourceBoundary
from story_writing_agents.agent_context.manifest import ContextAuthorityKind, ContextManifestEntry, ContextPriority, ContextProvenance, ContextScope, ContextSemanticChannel, ContextSourceKind, ContextSubjectKind
from story_writing_agents.agents import LEAD_ORCHESTRATOR
from story_writing_agents.agent_context.sources import capture_values as owner_capture_values


PRIVATE_SOURCE = "PRIVATE_SOURCE_한🙂"


DIGEST = "sha256:" + "1" * 64


AUTHORITY = {
    ContextSourceKind.WRITING_ARTIFACT: ContextAuthorityKind.WRITING_ARTIFACT_REPOSITORY,
    ContextSourceKind.CONVERSATION: ContextAuthorityKind.CONVERSATION_STORAGE,
    ContextSourceKind.ACTIVE_DEBATE: ContextAuthorityKind.ACTIVE_DEBATE,
    ContextSourceKind.OWN_AGENT_STATE: ContextAuthorityKind.OWN_AGENT_STATE,}


CHANNEL = {
    ContextSourceKind.WRITING_ARTIFACT: ContextSemanticChannel.FOCUSED_EVIDENCE,
    ContextSourceKind.CONVERSATION: ContextSemanticChannel.HISTORY,
    ContextSourceKind.ACTIVE_DEBATE: ContextSemanticChannel.HARD_CONSTRAINT,
    ContextSourceKind.OWN_AGENT_STATE: ContextSemanticChannel.OPTIONAL_BACKGROUND,}


def digest_bytes(body: bytes) -> str:
    return f"sha256:{hashlib.sha256(body).hexdigest()}"


def make_artifact_id(path: str) -> str:
    return "writing_artifact:sha256:" + hashlib.sha256(path.encode()).hexdigest()


def make_scope(
    *,
    subject_kind: ContextSubjectKind = ContextSubjectKind.CONVERSATION,
    subject_id: str = "conversation",
    agent_id: str = LEAD_ORCHESTRATOR,
    session_id: str = "session",
) -> ContextScope:
    return ContextScope(
        "workspace",
        session_id,
        subject_kind,
        subject_id,
        agent_id,)


def make_entry(
    descriptor: owner_capture_values.ContextSourceDescriptor, ordinal: int = 0
) -> ContextManifestEntry:
    priority = (
        ContextPriority.P3_OPTIONAL
        if descriptor.source_kind is ContextSourceKind.OWN_AGENT_STATE
        else ContextPriority.P2_WORKING
    )
    return ContextManifestEntry(
        descriptor.source_id,
        descriptor.source_kind,
        CHANNEL[descriptor.source_kind],
        priority,
        ordinal,
        descriptor.revision,
        descriptor.content_digest,
        descriptor.provenance,
        descriptor.utf8_bytes,
        searchable=True,)


def make_artifact_snapshot(
    scope: ContextScope,
    *,
    path: str = "core_documents/global_outline.md",
    lines: tuple[str, ...] = ("needle\n",),
    revision_seed: bytes = b"revision",
) -> owner_capture_values.ContextSourceSnapshot:
    body = "".join(lines).encode("utf-8")
    records = tuple(
        owner_capture_values.ContextSourceRecord(
            f"line_{index}",
            ContextSourceBoundary.artifact_lines(index, index),
            line,
            line,
            digest_bytes(line.encode("utf-8")),
            len(line.encode("utf-8")),
        )
        for index, line in enumerate(lines, start=1))
    descriptor = owner_capture_values.ContextSourceDescriptor(
        scope,
        make_artifact_id(path),
        ContextSourceKind.WRITING_ARTIFACT,
        digest_bytes(revision_seed),
        ContextProvenance(
            ContextAuthorityKind.WRITING_ARTIFACT_REPOSITORY,
            path,),
        digest_bytes(body),
        len(body),
        len(records),
    )
    return owner_capture_values.ContextSourceSnapshot(descriptor, records, body)
