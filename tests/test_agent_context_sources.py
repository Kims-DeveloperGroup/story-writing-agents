"""Authorized bounded source adapters for agent-directed context."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from dataclasses import FrozenInstanceError, fields, replace
from unittest import mock

from story_writing_agents import Workspace
from story_writing_agents.agent_context.actions import (
    CONTEXT_ACTION_POLICY,
    ContextSearchQuery,
    ContextSourceBoundary,
)
from story_writing_agents.agent_context.manifest import (
    ContextAuthorityKind,
    ContextManifest,
    ContextManifestEntry,
    ContextPriority,
    ContextProvenance,
    ContextScope,
    ContextSemanticChannel,
    ContextSourceKind,
    ContextSubjectKind,
)
from story_writing_agents.agent_context import sources as sources_module
from story_writing_agents.agent_context.sources import (
    CONTEXT_SOURCE_POLICY,
    ActiveDebateSourceAdapter,
    ContextSourceDescriptor,
    ContextSourceError,
    ContextSourceErrorCode,
    ContextSourcePolicy,
    ContextSourceRecord,
    ContextSourceRegistry,
    ContextSourceSnapshot,
    FocusedConversationSourceAdapter,
    OwnAgentStateSourceAdapter,
    WritingArtifactSourceAdapter,
)
from story_writing_agents.agents import EXECUTION_SPECIALIST, LEAD_ORCHESTRATOR, ROSTER
from story_writing_agents.conversation_storage import (
    compose_filesystem_conversation_storage,
)
from story_writing_agents.conversations import select_bounded_collaboration_context
from story_writing_agents.models import AgentStateEntry, DebateMessage
from story_writing_agents.writing_artifacts.repository import (
    WritingArtifactRepository,
)

from tests.collaboration_contract_fixture import contract_batches


_PRIVATE_SOURCE = "PRIVATE_SOURCE_한🙂"
_DIGEST = "sha256:" + "1" * 64
_AUTHORITY = {
    ContextSourceKind.WRITING_ARTIFACT: ContextAuthorityKind.WRITING_ARTIFACT_REPOSITORY,
    ContextSourceKind.CONVERSATION: ContextAuthorityKind.CONVERSATION_STORAGE,
    ContextSourceKind.ACTIVE_DEBATE: ContextAuthorityKind.ACTIVE_DEBATE,
    ContextSourceKind.OWN_AGENT_STATE: ContextAuthorityKind.OWN_AGENT_STATE,
}
_CHANNEL = {
    ContextSourceKind.WRITING_ARTIFACT: ContextSemanticChannel.FOCUSED_EVIDENCE,
    ContextSourceKind.CONVERSATION: ContextSemanticChannel.HISTORY,
    ContextSourceKind.ACTIVE_DEBATE: ContextSemanticChannel.HARD_CONSTRAINT,
    ContextSourceKind.OWN_AGENT_STATE: ContextSemanticChannel.OPTIONAL_BACKGROUND,
}


def _digest(body: bytes) -> str:
    return f"sha256:{hashlib.sha256(body).hexdigest()}"


def _artifact_id(path: str) -> str:
    return "writing_artifact:sha256:" + hashlib.sha256(path.encode()).hexdigest()


def _scope(
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
        agent_id,
    )


def _entry(
    descriptor: ContextSourceDescriptor, ordinal: int = 0
) -> ContextManifestEntry:
    priority = (
        ContextPriority.P3_OPTIONAL
        if descriptor.source_kind is ContextSourceKind.OWN_AGENT_STATE
        else ContextPriority.P2_WORKING
    )
    return ContextManifestEntry(
        descriptor.source_id,
        descriptor.source_kind,
        _CHANNEL[descriptor.source_kind],
        priority,
        ordinal,
        descriptor.revision,
        descriptor.content_digest,
        descriptor.provenance,
        descriptor.utf8_bytes,
        searchable=True,
    )


def _artifact_snapshot(
    scope: ContextScope,
    *,
    path: str = "core_documents/global_outline.md",
    lines: tuple[str, ...] = ("needle\n",),
    revision_seed: bytes = b"revision",
) -> ContextSourceSnapshot:
    body = "".join(lines).encode("utf-8")
    records = tuple(
        ContextSourceRecord(
            f"line_{index}",
            ContextSourceBoundary.artifact_lines(index, index),
            line,
            line,
            _digest(line.encode("utf-8")),
            len(line.encode("utf-8")),
        )
        for index, line in enumerate(lines, start=1)
    )
    descriptor = ContextSourceDescriptor(
        scope,
        _artifact_id(path),
        ContextSourceKind.WRITING_ARTIFACT,
        _digest(revision_seed),
        ContextProvenance(
            ContextAuthorityKind.WRITING_ARTIFACT_REPOSITORY,
            path,
        ),
        _digest(body),
        len(body),
        len(records),
    )
    return ContextSourceSnapshot(descriptor, records, body)


class _SourceFixture(unittest.TestCase):
    workspace: Workspace
    repository: WritingArtifactRepository

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="context-sources-")
        self.addCleanup(temporary.cleanup)
        self.workspace = Workspace(temporary.name).initialize()
        self.repository = WritingArtifactRepository(self.workspace.root)

    def publish(
        self,
        *,
        candidate: str = "chapter",
        core_updates: dict[str, str] | None = None,
    ) -> None:
        self.repository.commit_consensus(
            output_path="part-01/chapter-01.md",
            candidate=candidate,
            core_updates={} if core_updates is None else core_updates,
            session_id="session",
            debate_id="publication",
            revision_id="r0",
            candidate_author_agent_id=EXECUTION_SPECIALIST,
        )

    def registry(
        self,
        scope: ContextScope,
        *,
        conversation_id: str | None = None,
        debate_id: str | None = None,
    ) -> ContextSourceRegistry:
        services = compose_filesystem_conversation_storage(self.workspace)
        return ContextSourceRegistry(
            WritingArtifactSourceAdapter(self.repository, scope),
            FocusedConversationSourceAdapter(
                services.catalog,
                services.event_store,
                scope,
                conversation_id,
            ),
            ActiveDebateSourceAdapter(self.workspace, scope, debate_id),
            OwnAgentStateSourceAdapter(self.workspace, scope),
        )


class SourceSurfacePolicyAndValueTests(unittest.TestCase):
    """Verify the exact adapter surface, closed failures, and hard bounds."""

    def test_exact_surface_error_vocabulary_and_defaults(self) -> None:
        self.assertEqual(
            tuple(sources_module.__all__),
            (
                "CONTEXT_SOURCE_POLICY",
                "ActiveDebateSourceAdapter",
                "ContextSourceDescriptor",
                "ContextSourceError",
                "ContextSourceErrorCode",
                "ContextSourcePolicy",
                "ContextSourceRecord",
                "ContextSourceRegistry",
                "ContextSourceSearchMatch",
                "ContextSourceSnapshot",
                "FocusedConversationSourceAdapter",
                "OwnAgentStateSourceAdapter",
                "WritingArtifactSourceAdapter",
            ),
        )
        self.assertEqual(
            tuple(item.value for item in ContextSourceErrorCode),
            (
                "invalid_value",
                "scope_mismatch",
                "authority_mismatch",
                "inventory_limit_exceeded",
                "source_limit_exceeded",
                "source_missing",
                "source_unsafe",
                "source_invalid_utf8",
                "source_nul",
                "source_drift",
                "source_unavailable",
                "adapter_contract_violation",
            ),
        )
        self.assertEqual(
            tuple(
                getattr(CONTEXT_SOURCE_POLICY, field.name)
                for field in fields(ContextSourcePolicy)
            ),
            (
                4096,
                8192,
                10000,
                8 * 1024 * 1024,
                64 * 1024 * 1024,
                8 * 1024 * 1024,
                32 * 1024 * 1024,
            ),
        )
        for code in ContextSourceErrorCode:
            error = ContextSourceError(code)
            self.assertNotIn(_PRIVATE_SOURCE, repr(error))
            self.assertIsNone(error.__cause__)

    def test_each_policy_cap_rejects_n_plus_one_and_custom_relationships(self) -> None:
        values = {
            field.name: getattr(CONTEXT_SOURCE_POLICY, field.name)
            for field in fields(ContextSourcePolicy)
        }
        for name, value in values.items():
            with self.subTest(name=name):
                with self.assertRaises(ContextSourceError):
                    ContextSourcePolicy(**{**values, name: value + 1})
        for invalid in (0, -1, True, 1.0, "1"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ContextSourceError):
                    ContextSourcePolicy(**{**values, "max_records_per_source": invalid})  # type: ignore[arg-type]
        valid = ContextSourcePolicy(
            max_inventory_sources=2,
            max_traversal_entries=4,
            max_records_per_source=3,
            max_source_utf8_bytes=10,
            max_inventory_body_utf8_bytes=20,
            max_source_scan_utf8_bytes=5,
            max_cumulative_scan_utf8_bytes=10,
        )
        self.assertEqual(valid.max_inventory_sources, 2)
        for override in (
            {"max_source_scan_utf8_bytes": 11},
            {"max_cumulative_scan_utf8_bytes": 4},
            {"max_inventory_body_utf8_bytes": 9},
        ):
            with self.subTest(override=override):
                with self.assertRaises(ContextSourceError):
                    replace(valid, **override)

    def test_descriptor_snapshot_records_are_exact_immutable_and_content_hidden(
        self,
    ) -> None:
        snapshot = _artifact_snapshot(_scope())
        self.assertFalse(hasattr(snapshot, "__dict__"))
        self.assertNotIn("needle", repr(snapshot))
        self.assertNotIn("needle", repr(snapshot.records[0]))
        with self.assertRaises((FrozenInstanceError, TypeError)):
            snapshot.content = b"changed"  # type: ignore[misc]
        with self.assertRaises(ContextSourceError) as caught:
            replace(
                snapshot.descriptor,
                provenance=ContextProvenance(
                    ContextAuthorityKind.CONVERSATION_STORAGE,
                    "core_documents/global_outline.md",
                ),
            )
        self.assertEqual(
            caught.exception.code, ContextSourceErrorCode.AUTHORITY_MISMATCH
        )
        with self.assertRaises(ContextSourceError):
            replace(snapshot.records[0], content_digest=_DIGEST)
        with self.assertRaises(ContextSourceError):
            replace(snapshot, content=b"substituted")


class RealAuthorityAdapterTests(_SourceFixture):
    """Exercise each real canonical owner and exact isolation boundary."""

    def test_artifact_capture_preserves_crlf_lf_cr_and_classifies_paths(self) -> None:
        content = "one\r\ntwo\nthree\rfour 한🙂"
        path = "core_documents/global_outline.md"
        self.publish(core_updates={"global_outline.md": content})
        adapter = WritingArtifactSourceAdapter(self.repository, _scope())
        snapshot = adapter.capture(path, CONTEXT_SOURCE_POLICY)
        self.assertEqual(snapshot.content, content.encode("utf-8"))
        self.assertEqual(
            tuple(record.content for record in snapshot.records),
            ("one\r\n", "two\n", "three\r", "four 한🙂"),
        )
        self.assertEqual(snapshot.descriptor.source_id, _artifact_id(path))
        for locator, code in (
            (
                "../core_documents/global_outline.md",
                ContextSourceErrorCode.SOURCE_UNSAFE,
            ),
            ("core_documents\\global_outline.md", ContextSourceErrorCode.SOURCE_UNSAFE),
            ("/core_documents/global_outline.md", ContextSourceErrorCode.SOURCE_UNSAFE),
            ("discussion_logs/private.md", ContextSourceErrorCode.AUTHORITY_MISMATCH),
            (
                "core_documents/character_sheets.md",
                ContextSourceErrorCode.SOURCE_MISSING,
            ),
        ):
            with self.subTest(locator=locator):
                with self.assertRaises(ContextSourceError) as caught:
                    adapter.capture(locator, CONTEXT_SOURCE_POLICY)
                self.assertEqual(caught.exception.code, code)

    def test_assignment_local_artifact_allowlist_filters_inventory_and_capture(
        self,
    ) -> None:
        allowed = "core_documents/global_outline.md"
        blocked = "core_documents/character_sheets.md"
        self.publish(core_updates={"global_outline.md": "bounded outline"})
        adapter = WritingArtifactSourceAdapter(
            self.repository,
            _scope(),
            allowed_paths=frozenset({allowed}),
        )

        inventory = adapter.inventory(CONTEXT_SOURCE_POLICY)

        self.assertEqual(
            tuple(item.provenance.locator for item in inventory),
            (allowed,),
        )
        self.assertEqual(
            adapter.capture(allowed, CONTEXT_SOURCE_POLICY).descriptor,
            inventory[0],
        )
        with self.assertRaises(ContextSourceError) as caught:
            adapter.capture(blocked, CONTEXT_SOURCE_POLICY)
        self.assertIs(caught.exception.code, ContextSourceErrorCode.AUTHORITY_MISMATCH)
        for invalid in ({allowed}, frozenset({"../escape.md"})):
            with self.subTest(invalid=invalid), self.assertRaises(ContextSourceError):
                WritingArtifactSourceAdapter(
                    self.repository,
                    _scope(),
                    allowed_paths=invalid,  # type: ignore[arg-type]
                )

    def test_artifact_capture_retries_once_then_reports_persistent_drift(self) -> None:
        adapter = WritingArtifactSourceAdapter(self.repository, _scope())
        first = _artifact_snapshot(_scope(), revision_seed=b"first")
        second = _artifact_snapshot(_scope(), revision_seed=b"second")
        stable = _artifact_snapshot(_scope(), revision_seed=b"stable")
        with mock.patch.object(
            adapter, "_capture_once", side_effect=(first, second, stable, stable)
        ) as capture:
            self.assertEqual(
                adapter.capture(
                    "core_documents/global_outline.md", CONTEXT_SOURCE_POLICY
                ),
                stable,
            )
        self.assertEqual(capture.call_count, 4)
        with mock.patch.object(
            adapter, "_capture_once", side_effect=(first, second, first, second)
        ):
            with self.assertRaises(ContextSourceError) as caught:
                adapter.capture(
                    "core_documents/global_outline.md", CONTEXT_SOURCE_POLICY
                )
        self.assertEqual(caught.exception.code, ContextSourceErrorCode.SOURCE_DRIFT)

    def test_artifact_inventory_is_complete_or_fails_custom_traversal_and_eligible_caps(
        self,
    ) -> None:
        self.publish(
            core_updates={
                "global_outline.md": "outline",
                "character_sheets.md": "characters",
            }
        )
        self.workspace.write_text("discussion_logs/internal.md", "internal")
        adapter = WritingArtifactSourceAdapter(self.repository, _scope())
        inventory = adapter.inventory(CONTEXT_SOURCE_POLICY)
        self.assertEqual(
            {item.provenance.locator for item in inventory},
            {
                "core_documents/global_outline.md",
                "core_documents/character_sheets.md",
                "manuscript/part-01/chapter-01.md",
            },
        )
        self.assertNotIn(
            "discussion_logs/internal.md",
            {item.provenance.locator for item in inventory},
        )
        with self.assertRaises(ContextSourceError) as eligible:
            adapter.inventory(replace(CONTEXT_SOURCE_POLICY, max_inventory_sources=2))
        self.assertEqual(
            eligible.exception.code, ContextSourceErrorCode.INVENTORY_LIMIT_EXCEEDED
        )
        with self.assertRaises(ContextSourceError) as traversal:
            adapter.inventory(replace(CONTEXT_SOURCE_POLICY, max_traversal_entries=1))
        self.assertEqual(
            traversal.exception.code, ContextSourceErrorCode.INVENTORY_LIMIT_EXCEEDED
        )

    def test_artifact_inventory_relist_drift_and_nonregular_sources_fail_completely(
        self,
    ) -> None:
        adapter = WritingArtifactSourceAdapter(self.repository, _scope())
        with mock.patch.object(
            adapter,
            "_list_paths",
            side_effect=(
                (),
                ("core_documents/global_outline.md",),
                (),
                ("core_documents/global_outline.md",),
            ),
        ):
            with self.assertRaises(ContextSourceError) as drift:
                adapter.inventory(CONTEXT_SOURCE_POLICY)
        self.assertEqual(drift.exception.code, ContextSourceErrorCode.SOURCE_DRIFT)
        outside = self.workspace.root / "outside.md"
        outside.write_text("outside", encoding="utf-8")
        attacks = (
            ("manuscript/symlink.md", lambda target: target.symlink_to(outside)),
            ("manuscript/hardlink.md", lambda target: os.link(outside, target)),
            ("manuscript/directory.md", lambda target: target.mkdir()),
        )
        for locator, create in attacks:
            target = self.workspace.root / locator
            create(target)
            with self.subTest(locator=locator):
                with self.assertRaises(ContextSourceError) as caught:
                    adapter.capture(locator, CONTEXT_SOURCE_POLICY)
                self.assertEqual(
                    caught.exception.code, ContextSourceErrorCode.SOURCE_MISSING
                )

    def test_focused_conversation_exposes_bounded_readable_v2_records(self) -> None:
        services = compose_filesystem_conversation_storage(self.workspace)
        metadata = services.catalog.create("session", "Focused conversation")
        conversation_id = metadata.conversation_id
        batch = contract_batches(conversation_id)["proposal"]
        batch = replace(
            batch,
            events=tuple(
                replace(event, session_id="session") for event in batch.events
            ),
        )
        services.event_store.append_batch(batch)
        history = services.event_store.read("session", conversation_id)
        captured_context = select_bounded_collaboration_context(
            history,
            event_ids=("proposal_event", "request_event"),
        )
        scope = _scope(subject_id=conversation_id)
        adapter = FocusedConversationSourceAdapter(
            services.catalog,
            services.event_store,
            scope,
            conversation_id,
            captured_context=captured_context,
        )
        snapshot = adapter.capture(
            f"conversation/{conversation_id}", CONTEXT_SOURCE_POLICY
        )
        self.assertEqual(
            tuple(record.record_id for record in snapshot.records),
            ("proposal_event", "request_event"),
        )
        self.assertIn("Consult specialists", snapshot.records[0].search_text)
        self.assertNotIn("route_fingerprint", snapshot.records[0].search_text)
        self.assertEqual(
            snapshot.descriptor.revision, snapshot.descriptor.content_digest
        )
        self.assertEqual(
            snapshot.descriptor.source_id, f"conversation:{conversation_id}"
        )
        self.assertEqual(
            snapshot.descriptor.provenance.locator, f"conversation/{conversation_id}"
        )
        with mock.patch.object(
            adapter,
            "_capture_once",
            side_effect=(snapshot, object(), snapshot, snapshot),
        ) as capture:
            self.assertEqual(
                adapter.capture(
                    f"conversation/{conversation_id}", CONTEXT_SOURCE_POLICY
                ),
                snapshot,
            )
        self.assertEqual(capture.call_count, 4)
        with mock.patch.object(
            adapter,
            "_capture_once",
            side_effect=(snapshot, object(), snapshot, object()),
        ):
            with self.assertRaises(ContextSourceError) as drift:
                adapter.capture(
                    f"conversation/{conversation_id}", CONTEXT_SOURCE_POLICY
                )
        self.assertEqual(drift.exception.code, ContextSourceErrorCode.SOURCE_DRIFT)
        with self.assertRaises(ContextSourceError):
            FocusedConversationSourceAdapter(
                services.catalog,
                services.event_store,
                _scope(subject_id="other"),
                conversation_id,
            )

    def test_zero_record_focused_conversation_exists_with_explicit_bootstrap(
        self,
    ) -> None:
        services = compose_filesystem_conversation_storage(self.workspace)
        metadata = services.catalog.create("session", "Fresh conversation")
        batch = contract_batches(metadata.conversation_id)["proposal"]
        services.event_store.append_batch(
            replace(
                batch,
                events=tuple(
                    replace(event, session_id="session") for event in batch.events
                ),
            )
        )
        adapter = FocusedConversationSourceAdapter(
            services.catalog,
            services.event_store,
            _scope(subject_id=metadata.conversation_id),
            metadata.conversation_id,
        )

        inventory = adapter.inventory(CONTEXT_SOURCE_POLICY)
        self.assertEqual(len(inventory), 1)
        snapshot = adapter.capture(
            f"conversation/{metadata.conversation_id}",
            CONTEXT_SOURCE_POLICY,
        )
        self.assertEqual(snapshot.descriptor, inventory[0])
        self.assertEqual(snapshot.records, ())
        value = json.loads(snapshot.content)
        self.assertEqual(value["records"], [])
        self.assertEqual(value["bootstrap"]["retained_records"], 0)
        self.assertEqual(value["bootstrap"]["total_records"], 2)

    def test_searchable_history_is_opt_in_same_session_active_and_frozen(self) -> None:
        services = compose_filesystem_conversation_storage(self.workspace)
        focused = services.catalog.create("session", "Focused")
        sibling = services.catalog.create("session", "Sibling")
        archived = services.catalog.create("session", "Archived")
        cross_session = services.catalog.create("other-session", "Other")
        for metadata in (focused, sibling, archived, cross_session):
            batch = contract_batches(metadata.conversation_id)["proposal"]
            services.event_store.append_batch(
                replace(
                    batch,
                    events=tuple(
                        replace(event, session_id=metadata.session_id)
                        for event in batch.events
                    ),
                )
            )
        services.catalog.archive("session", archived.conversation_id)
        adapter = FocusedConversationSourceAdapter(
            services.catalog,
            services.event_store,
            _scope(subject_id=focused.conversation_id),
            focused.conversation_id,
            include_same_session_active=True,
            include_searchable_history=True,
        )

        inventory = adapter.inventory(CONTEXT_SOURCE_POLICY)

        self.assertEqual(
            tuple(item.provenance.locator for item in inventory),
            (
                f"conversation/{focused.conversation_id}",
                f"conversation/{sibling.conversation_id}",
            ),
        )
        focused_snapshot = adapter.capture(
            f"conversation/{focused.conversation_id}", CONTEXT_SOURCE_POLICY
        )
        self.assertEqual(
            tuple(record.record_id for record in focused_snapshot.records),
            ("proposal_event", "request_event"),
        )
        self.assertIs(
            adapter.capture(
                f"conversation/{focused.conversation_id}", CONTEXT_SOURCE_POLICY
            ),
            focused_snapshot,
        )
        services.catalog.create("session", "Created after snapshot")
        self.assertEqual(adapter.inventory(CONTEXT_SOURCE_POLICY), inventory)
        for excluded in (archived, cross_session):
            with self.subTest(excluded=excluded.conversation_id):
                with self.assertRaises(ContextSourceError) as caught:
                    adapter.capture(
                        f"conversation/{excluded.conversation_id}",
                        CONTEXT_SOURCE_POLICY,
                    )
                self.assertEqual(
                    caught.exception.code,
                    ContextSourceErrorCode.AUTHORITY_MISMATCH,
                )

    def test_active_debate_and_all_four_owner_states_are_public_exact_and_isolated(
        self,
    ) -> None:
        debate_id = "debate"
        self.workspace.reserve_debate(
            "session", debate_id, input_kind="prompt", owner_token="PRIVATE_OWNER_TOKEN"
        )
        message = DebateMessage(
            LEAD_ORCHESTRATOR,
            "proposal",
            1,
            "r1",
            "proposal",
            "public debate needle",
            "2026-08-28T00:00:00+00:00",
        )
        self.workspace.append_debate_message("session", debate_id, message)
        debate_scope = _scope(
            subject_kind=ContextSubjectKind.DEBATE, subject_id=debate_id
        )
        debate = ActiveDebateSourceAdapter(
            self.workspace, debate_scope, debate_id
        ).capture(f"debate/{debate_id}", CONTEXT_SOURCE_POLICY)
        self.assertEqual(
            tuple(record.record_id for record in debate.records), ("state", "message_1")
        )
        self.assertNotIn("PRIVATE_OWNER_TOKEN", debate.content.decode())
        self.assertEqual(debate.records[1].search_text, "public debate needle")
        self.assertEqual(debate.descriptor.source_id, "active_debate:debate")
        self.assertEqual(debate.descriptor.provenance.locator, "debate/debate")
        self.assertEqual(debate.descriptor.revision, debate.descriptor.content_digest)
        for index, agent_id in enumerate(ROSTER):
            self.workspace.append_agent_state(
                AgentStateEntry(
                    agent_id,
                    "session",
                    "debate",
                    "audit",
                    index,
                    "r1",
                    "outcome",
                    f"state {agent_id} needle",
                    f"2026-08-28T00:00:0{index}+00:00",
                )
            )
        for agent_id in ROSTER:
            state_scope = _scope(agent_id=agent_id)
            snapshot = OwnAgentStateSourceAdapter(self.workspace, state_scope).capture(
                f"agent-state/{agent_id}", CONTEXT_SOURCE_POLICY
            )
            self.assertTrue(snapshot.records)
            self.assertEqual(
                snapshot.descriptor.source_id, f"own_agent_state:{agent_id}"
            )
            self.assertEqual(
                snapshot.descriptor.provenance.locator, f"agent-state/{agent_id}"
            )
            self.assertEqual(
                snapshot.descriptor.revision, snapshot.descriptor.content_digest
            )
            self.assertTrue(
                all(agent_id in record.content for record in snapshot.records)
            )
            self.assertTrue(
                all(
                    other not in snapshot.content.decode()
                    for other in ROSTER
                    if other != agent_id
                )
            )
        missing = ActiveDebateSourceAdapter(
            self.workspace,
            _scope(subject_kind=ContextSubjectKind.DEBATE, subject_id="missing"),
            "missing",
        )
        with self.assertRaises(ContextSourceError) as caught:
            missing.capture("debate/missing", CONTEXT_SOURCE_POLICY)
        self.assertEqual(caught.exception.code, ContextSourceErrorCode.SOURCE_MISSING)


class RegistrySearchTests(_SourceFixture):
    """Verify canonical inventory, literal search, dedupe, drift, and scan caps."""

    def test_registry_requires_exact_four_same_scope_adapters(self) -> None:
        scope = _scope()
        registry = self.registry(scope)
        self.assertEqual(registry.scope, scope)
        other = _scope(session_id="other")
        services = compose_filesystem_conversation_storage(self.workspace)
        with self.assertRaises(ContextSourceError) as caught:
            ContextSourceRegistry(
                WritingArtifactSourceAdapter(self.repository, scope),
                FocusedConversationSourceAdapter(
                    services.catalog, services.event_store, other, None
                ),
                ActiveDebateSourceAdapter(self.workspace, scope, None),
                OwnAgentStateSourceAdapter(self.workspace, scope),
            )
        self.assertEqual(caught.exception.code, ContextSourceErrorCode.SCOPE_MISMATCH)

    def test_search_normalizes_source_controls_orders_and_deduplicates_by_first_query(
        self,
    ) -> None:
        scope = _scope()
        registry = self.registry(scope)
        snapshot = _artifact_snapshot(
            scope,
            lines=("prefix\n", "ＮＥＥＤＬＥ\u200bgap\n", "needle tail\n"),
        )
        manifest = ContextManifest(scope, (_entry(snapshot.descriptor),))
        calls: list[str] = []

        def capture(
            _manifest: ContextManifest,
            entry: ContextManifestEntry,
            _policy: ContextSourcePolicy,
        ) -> ContextSourceSnapshot:
            calls.append(entry.source_id)
            return snapshot

        registry.capture = capture  # type: ignore[method-assign]
        queries = (
            ContextSearchQuery("needle gap", (ContextSourceKind.WRITING_ARTIFACT,)),
            ContextSearchQuery("needle", (ContextSourceKind.WRITING_ARTIFACT,)),
        )
        matches, truncated, scanned = registry.search(manifest, queries)
        self.assertFalse(truncated)
        self.assertEqual(tuple(match.query_index for match in matches), (1,))
        self.assertEqual(tuple(match.record.boundary.start for match in matches), (1,))
        self.assertIn("needle gap", matches[0].snippet)
        self.assertEqual(scanned, snapshot.descriptor.utf8_bytes)
        self.assertEqual(
            calls, [snapshot.descriptor.source_id, snapshot.descriptor.source_id]
        )

    def test_search_expands_artifact_at_most_twelve_lines_and_shrinks_for_multibyte_cap(
        self,
    ) -> None:
        scope = _scope()
        registry = self.registry(scope)
        lines = tuple(
            ("needle 한🙂\n" if index == 15 else f"{index}\n") for index in range(1, 31)
        )
        snapshot = _artifact_snapshot(scope, lines=lines)
        manifest = ContextManifest(scope, (_entry(snapshot.descriptor),))
        registry.capture = lambda *_args: snapshot  # type: ignore[method-assign]
        matches, _, _ = registry.search(
            manifest,
            (ContextSearchQuery("needle", (ContextSourceKind.WRITING_ARTIFACT,)),),
        )
        self.assertEqual(
            (matches[0].record.boundary.start, matches[0].record.boundary.end), (3, 27)
        )
        lower = replace(
            CONTEXT_ACTION_POLICY,
            max_snippet_utf8_bytes=20,
            max_excerpt_utf8_bytes=20,
        )
        matches, _, _ = registry.search(
            manifest,
            (ContextSearchQuery("needle", (ContextSourceKind.WRITING_ARTIFACT,)),),
            action_policy=lower,
        )
        self.assertLessEqual(matches[0].record.utf8_bytes, 20)
        self.assertIn("needle", matches[0].snippet)

    def test_scan_preflight_skips_oversized_without_io_and_post_search_drift_fails(
        self,
    ) -> None:
        scope = _scope()
        registry = self.registry(scope)
        snapshot = _artifact_snapshot(scope)
        manifest = ContextManifest(scope, (_entry(snapshot.descriptor),))
        capture = mock.Mock(return_value=snapshot)
        registry.capture = capture  # type: ignore[method-assign]
        matches, truncated, scanned = registry.search(
            manifest,
            (ContextSearchQuery("needle", (ContextSourceKind.WRITING_ARTIFACT,)),),
            remaining_scan_utf8_bytes=snapshot.descriptor.utf8_bytes - 1,
        )
        self.assertEqual(matches, ())
        self.assertTrue(truncated)
        self.assertEqual(scanned, 0)
        capture.assert_not_called()
        drifted = _artifact_snapshot(
            scope, lines=("needle changed\n",), revision_seed=b"changed"
        )
        registry.capture = mock.Mock(side_effect=(snapshot, drifted))  # type: ignore[method-assign]
        with self.assertRaises(ContextSourceError) as caught:
            registry.search(
                manifest,
                (ContextSearchQuery("needle", (ContextSourceKind.WRITING_ARTIFACT,)),),
            )
        self.assertEqual(caught.exception.code, ContextSourceErrorCode.SOURCE_DRIFT)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
