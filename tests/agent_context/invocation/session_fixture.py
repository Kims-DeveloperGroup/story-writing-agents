'Owned session_fixture.py declarations; mutable objects remain fresh per original call.'
from __future__ import annotations
import hashlib
import json
from story_writing_agents.agent_context.actions import CONTEXT_ACTION_POLICY, ContextActionCorrelation, ContextActionPolicy, ContextSearchAction, ContextSearchQuery, ContextSourceBoundary, ContextSourceHandle
from story_writing_agents.agent_context.manifest import ContextAuthorityKind, ContextManifest, ContextManifestEntry, ContextPriority, ContextProvenance, ContextScope, ContextSemanticChannel, ContextSourceKind, ContextSubjectKind
from unittest import mock
from story_writing_agents.agent_context.contracts import context_values as ctx_context_values, planning_inputs as ctx_planning_inputs, session as ctx_session
from story_writing_agents.agent_context.invocation import invocation_session as owner_invocation_session
from story_writing_agents.agent_context.sources import capture_values as owner_capture_values
from story_writing_agents.agent_context.sources import registry as owner_registry


PRIVATE_ANSWER = "PRIVATE_FINAL_ANSWER_한🙂"; PRIVATE_BODY_A = "PRIVATE_SOURCE_A needle 한🙂\n"


PRIVATE_BODY_B = "PRIVATE_SOURCE_B needle\n"


SCOPE = ContextScope("workspace", "session", ContextSubjectKind.CONVERSATION, "conversation", "lead_orchestrator", )


def digest_bytes(body: bytes) -> str: return f"sha256:{hashlib.sha256(body).hexdigest()}"


def artifact_id(path: str) -> str: return "writing_artifact:sha256:" + hashlib.sha256(path.encode()).hexdigest()


def closed_result_schema(properties: dict[str, object]) -> dict[str, object]:
    return {'additionalProperties': False, 'properties': properties, 'required': list(properties), 'type': 'object'}


def answer_wire(correlation: str, result: object, *, used_handles: tuple[str, ...] = (),) -> dict[str, object]:
    del correlation, used_handles
    return {'agent_action': {'action_type': 'submit_result', 'task_result': result}}


def search_wire(correlation: str, queries: list[tuple[str, list[str]]], *,
    path_prefix: str | None = None,) -> dict[str, object]:
    del correlation
    action: dict[str, object] = {"action_type": "search_context", "queries": [{"search_text": text, "source_types": [
            {"writing_artifact": "story_document", "conversation": "conversation_history",
             "active_debate": "current_consensus_run", "own_agent_state": "agent_work_history"}.get(kind, kind)
            for kind in kinds]} for text, kinds in queries], }
    if path_prefix is not None: action["artifact_directory_prefix"] = path_prefix
    return {'agent_action': action}


def read_wire(correlation: str, handles: list[object]) -> dict[str, object]:
    del correlation
    return {'agent_action': {'action_type': 'read_context', 'read_handles': handles}}


def snapshot(scope: ContextScope, path: str, body: str, *,
    revision_seed: bytes | None = None,) -> owner_capture_values.ContextSourceSnapshot:
    encoded = body.encode("utf-8")
    record = owner_capture_values.ContextSourceRecord("line_1", ContextSourceBoundary.artifact_lines(1, 1), body,
        body, digest_bytes(encoded), len(encoded), )
    descriptor = owner_capture_values.ContextSourceDescriptor(scope, artifact_id(path), ContextSourceKind.WRITING_ARTIFACT,
        digest_bytes(revision_seed or (path + "-revision").encode()), ContextProvenance(
            ContextAuthorityKind.WRITING_ARTIFACT_REPOSITORY, path, ), digest_bytes(encoded), len(encoded), 1, )
    return owner_capture_values.ContextSourceSnapshot(descriptor, (record,), encoded)


def entry(snapshot: owner_capture_values.ContextSourceSnapshot, ordinal: int) -> ContextManifestEntry:
    descriptor = snapshot.descriptor
    return ContextManifestEntry(descriptor.source_id, descriptor.source_kind, ContextSemanticChannel.FOCUSED_EVIDENCE,
        ContextPriority.P2_WORKING, ordinal, descriptor.revision, descriptor.content_digest,
        descriptor.provenance, descriptor.utf8_bytes, searchable=True, )


def manifest_fixture(scope: ContextScope = SCOPE, ) -> tuple[ContextManifest, dict[str, owner_capture_values.ContextSourceSnapshot]]:
    snapshots = (snapshot(scope, "core_documents/global_outline.md", PRIVATE_BODY_A),
        snapshot(scope, "core_documents/character_sheets.md", PRIVATE_BODY_B), )
    manifest = ContextManifest(scope, tuple(entry(snapshot, index) for index, snapshot in enumerate(snapshots)), )
    return manifest, {snapshot.descriptor.source_id: snapshot for snapshot in snapshots}


def registry(scope: ContextScope, snapshots: dict[str, owner_capture_values.ContextSourceSnapshot],) -> owner_registry.ContextSourceRegistry:
    registry = object.__new__(owner_registry.ContextSourceRegistry); registry.scope = scope
    registry.capture = mock.Mock( side_effect=lambda _manifest, entry, _policy: snapshots[entry.source_id] )
    registry.search = mock.Mock(return_value=((), False, 0)); return registry


def search_matches(manifest: ContextManifest,
    snapshots: dict[str, owner_capture_values.ContextSourceSnapshot],) -> tuple[owner_capture_values.ContextSourceSearchMatch, ...]:
    return tuple(owner_capture_values.ContextSourceSearchMatch( 1, snapshots[entry.source_id].descriptor,
            snapshots[entry.source_id].records[0], "needle", len(b"needle"), ) for entry in manifest.entries
        if entry.source_id in snapshots )


def mint_handles(session: owner_invocation_session.ContextInvocationSession, registry: owner_registry.ContextSourceRegistry, snapshots: dict[str,
    owner_capture_values.ContextSourceSnapshot], ) -> tuple[tuple[ContextManifestEntry, ContextSourceHandle], ...]:
    matches = search_matches(session.binding.manifest, snapshots)
    registry.search = mock.Mock(return_value=(matches, False, sum((match.descriptor.utf8_bytes for match in matches))))
    observation = session.search(ContextSearchAction( correlation(session.binding, session.next_action_index), (
                ContextSearchQuery( "needle", (ContextSourceKind.WRITING_ARTIFACT,), ), ), ) )
    by_id = {entry.source_id: entry for entry in session.binding.manifest.entries}
    return tuple((by_id[hit.source_id], hit.handle) for hit in observation.hits)


def binding(manifest: ContextManifest, *, operation_id: str = "operation",
    result_id: str = "result", reference_operation_id: str | None = None,
    references: tuple[ctx_planning_inputs.MandatoryReferenceReservation, ...] = (),) -> ctx_session.ContextInvocationBinding:
    return ctx_session.ContextInvocationBinding(manifest.scope, operation_id, result_id,
        manifest, reference_operation_id, references, )


def correlation(binding: ctx_session.ContextInvocationBinding, index: int, **overrides: object, ) -> ContextActionCorrelation:
    values: dict[str, object] = {"scope": binding.scope, "operation_id": binding.operation_id,
        "result_correlation_id": binding.result_correlation_id, "action_index": index, }
    values.update(overrides)
    return ContextActionCorrelation(**values)  # type: ignore[arg-type]


def reference( ordinal: int, *, reference_id: str | None = None,
    source_id: str = "same-artifact", locator: str = "core_documents/global_outline.md",
    digest_character: str = "4",) -> ctx_planning_inputs.MandatoryReferenceReservation: return ctx_planning_inputs.MandatoryReferenceReservation( ordinal,
        reference_id or f"range-{ordinal}", source_id,
        "revision-1", ContextProvenance(ContextAuthorityKind.REFERENCE_AUTHORIZATION, locator),
        "sha256:" + digest_character * 64, ordinal * 7, )


def session(binding: ctx_session.ContextInvocationBinding, registry: owner_registry.ContextSourceRegistry, *,
    secret: bytes = b"k" * 32, action_policy: ContextActionPolicy = CONTEXT_ACTION_POLICY,
    source_policy: owner_capture_values.ContextSourcePolicy = owner_capture_values.CONTEXT_SOURCE_POLICY, source_aliases: dict[str, str] | None = None,
    allowed_search_source_kinds: tuple[ContextSourceKind, ...] = (
        ctx_context_values.SEARCH_SOURCE_KIND_ORDER ),) -> owner_invocation_session.ContextInvocationSession:
    with mock.patch('story_writing_agents.agent_context.invocation.invocation_session.secrets.token_bytes', return_value=secret,):
        return owner_invocation_session.ContextInvocationSession(binding, registry, action_policy=action_policy,
            source_policy=source_policy, source_aliases=source_aliases,
            allowed_search_source_kinds=allowed_search_source_kinds, )


def conversation_snapshot(scope: ContextScope, texts: tuple[str, ...]) -> owner_capture_values.ContextSourceSnapshot:
    canonical = lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    values = [{"schema_version": 2, "session_id": scope.session_id, "conversation_id": "conversation-one",
        "turn_id": f"turn_{number}", "step_id": f"step_{number}", "event_id": f"conversation_{number}",
        "batch_id": f"batch_{number}", "batch_ordinal": 0, "batch_size": 1,
        "batch_digest": digest_bytes(f"batch_{number}".encode()), "sequence": number, "timestamp": "2026-11-09T00:00:00+00:00",
        "sender": {"kind": "user", "agent_id": None}, "recipient": {"kind": "lead", "agent_id": scope.agent_id},
        "direction": "user_to_lead", "kind": "message", "event_revision": digest_bytes(text.encode()),
        "fields": [{"key": "message.content", "text": text, "assignment_ordinal": None, "input_ordinal": None}]}
        for number, text in enumerate(texts, start=1)]
    records = tuple(owner_capture_values.ContextSourceRecord(row["event_id"], ContextSourceBoundary.records(row["sequence"], row["sequence"]),
        canonical(row), row["fields"][0]["text"], digest_bytes(canonical(row).encode()), len(canonical(row).encode())) for row in values)
    bootstrap = {"schema_version": 2, "session_id": scope.session_id, "conversation_id": "conversation-one",
        "total_records": len(values), "last_sequence": len(values), "tail_batch_digest": values[-1]["batch_digest"],
        "requested_records": len(values), "retained_records": len(values), "omitted_records": 0,
        "retained_utf8_bytes": sum(len(text.encode()) for text in texts), "truncated": False}
    encoded = canonical({"bootstrap": bootstrap, "records": values}).encode(); digest = digest_bytes(encoded)
    descriptor = owner_capture_values.ContextSourceDescriptor(scope, "conversation:conversation-one", ContextSourceKind.CONVERSATION, digest,
        ContextProvenance(ContextAuthorityKind.CONVERSATION_STORAGE, "conversation/conversation-one"), digest, len(encoded), len(records))
    return owner_capture_values.ContextSourceSnapshot(descriptor, records, encoded)
