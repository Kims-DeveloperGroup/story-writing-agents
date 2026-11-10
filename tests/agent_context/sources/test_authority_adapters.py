'Existing RealAuthorityAdapterTests scenarios, moved without new cases.'
from __future__ import annotations
import json
import os
from dataclasses import replace
from pathlib import Path
from unittest import mock
from story_writing_agents.agent_context.actions import CONTEXT_ACTION_POLICY, ContextSearchQuery, ContextSourceBoundary
from story_writing_agents.agent_context.manifest import ContextManifest, ContextSourceKind, ContextSubjectKind
from story_writing_agents.agents import EXECUTION_SPECIALIST, LEAD_ORCHESTRATOR, ROSTER
from story_writing_agents.conversation_storage import compose_filesystem_conversation_storage
from story_writing_agents.conversations import select_bounded_collaboration_context
from story_writing_agents.models import AgentStateEntry, DebateMessage
from tests.collaboration_contract_fixture import contract_batches
from story_writing_agents.agent_context import actions as owner_actions
from story_writing_agents.agent_context.sources import active_debate as owner_active_debate
from story_writing_agents.agent_context.sources import capture_values as owner_capture_values
from story_writing_agents.agent_context.sources import focused_conversation as owner_focused_conversation
from story_writing_agents.agent_context.sources import own_agent_state as owner_own_agent_state
from story_writing_agents.agent_context.sources import registry as owner_registry
from story_writing_agents.agent_context.sources import writing_artifact as owner_writing_artifact
from story_writing_agents import errors as owner_errors
from story_writing_agents.writing_artifacts import catalog as owner_catalog
from story_writing_agents.writing_artifacts import filesystem_sources as owner_filesystem_sources
from story_writing_agents.writing_artifacts import references as owner_references
from tests.agent_context.sources import authority_fixture as fixture_authority_fixture
from tests.agent_context.sources import value_inputs as fixture_value_inputs


class RealAuthorityAdapterTests(fixture_authority_fixture.SourceAuthorityFixture):
    """Exercise each real canonical owner and exact isolation boundary."""

    def test_artifact_capture_preserves_crlf_lf_cr_and_classifies_paths(self) -> None:
        cores = owner_catalog.CORE_ARTIFACT_PATHS
        original = {path: self.workspace.path(path).read_bytes() for path in cores}
        for path in cores:
            self.workspace.path(path).unlink()
        adapter = owner_writing_artifact.WritingArtifactSourceAdapter(self.repository, fixture_value_inputs.make_scope())
        try:
            with mock.patch.object(self.repository, "are_sources_absent", wraps=self.repository.are_sources_absent) as absent, mock.patch.object(adapter, "_list_paths", wraps=adapter._list_paths) as listed:
                statuses = adapter.canonical_core_statuses(fixture_value_inputs.DIGEST, owner_capture_values.CONTEXT_SOURCE_POLICY)
            self.assertEqual(tuple(item.status for item in statuses), (owner_actions.ContextSourceStatusKind.ABSENT,) * 4)
            self.assertEqual(absent.call_args_list, [mock.call(cores, max_bytes=owner_capture_values.CONTEXT_SOURCE_POLICY.max_source_utf8_bytes)] * 2)
            self.assertEqual(listed.call_count, 3)
        finally:
            for path, body in original.items():
                self.workspace.path(path).write_bytes(body)
        with mock.patch.object(self.repository, "_locked_sources") as guard:
            for invalid in ((), [], (cores[0], cores[0]), ("manuscript/part-01/chapter-01.md",), (*cores, cores[0])):
                with self.assertRaises((TypeError, ValueError)):
                    self.repository.are_sources_absent(invalid)
            guard.assert_not_called()
        content = "one\r\ntwo\nthree\rfour 한🙂"
        path = "core_documents/global_outline.md"
        self.publish(core_updates={"global_outline.md": content})
        adapter = owner_writing_artifact.WritingArtifactSourceAdapter(self.repository, fixture_value_inputs.make_scope())
        snapshot = adapter.capture(path, owner_capture_values.CONTEXT_SOURCE_POLICY)
        self.assertEqual(snapshot.content, content.encode("utf-8"))
        self.assertEqual(tuple((record.content for record in snapshot.records)), ('one\r\n', 'two\n', 'three\r', 'four 한🙂'))
        self.assertEqual(snapshot.descriptor.source_id, fixture_value_inputs.make_artifact_id(path))
        for locator, code in (
            (
                "../core_documents/global_outline.md",
                owner_capture_values.ContextSourceErrorCode.SOURCE_UNSAFE,),
            ("core_documents\\global_outline.md", owner_capture_values.ContextSourceErrorCode.SOURCE_UNSAFE),
            ("/core_documents/global_outline.md", owner_capture_values.ContextSourceErrorCode.SOURCE_UNSAFE),
            ("discussion_logs/private.md", owner_capture_values.ContextSourceErrorCode.AUTHORITY_MISMATCH),
        ):
            with self.subTest(locator=locator):
                with self.assertRaises(owner_capture_values.ContextSourceError) as caught:
                    adapter.capture(locator, owner_capture_values.CONTEXT_SOURCE_POLICY)
                self.assertEqual(caught.exception.code, code)
        self.assertEqual(adapter.capture('core_documents/character_sheets.md', owner_capture_values.CONTEXT_SOURCE_POLICY).content, b'')

        cores = owner_catalog.CORE_ARTIFACT_PATHS
        bodies = {path: content if index % 2 == 0 else "" for index, path in enumerate(cores)}
        self.repository.commit_consensus(output_path="part-01/core-status.md", candidate="core", core_updates={Path(path).name: body for path, body in bodies.items()}, session_id="session", debate_id="core-status", revision_id="r0", candidate_author_agent_id=EXECUTION_SPECIALIST)
        expected = []
        for ordinal, (path, body) in enumerate(bodies.items(), 1):
            descriptor = adapter.capture(path, owner_capture_values.CONTEXT_SOURCE_POLICY).descriptor
            expected.append(owner_actions.ContextSourceStatusReceipt(evidence_id=f'status_{ordinal}', source_id=descriptor.source_id, locator=path, status=owner_actions.ContextSourceStatusKind.PRESENT if body else owner_actions.ContextSourceStatusKind.EMPTY, ordinal=ordinal, manifest_digest=fixture_value_inputs.DIGEST, revision=descriptor.revision, source_digest=descriptor.content_digest, source_utf8_bytes=descriptor.utf8_bytes))
        with mock.patch.object(self.repository, "read_current", wraps=self.repository.read_current) as read:
            self.assertEqual(adapter.canonical_core_statuses(fixture_value_inputs.DIGEST, owner_capture_values.CONTEXT_SOURCE_POLICY), tuple(expected))
        self.assertEqual(read.call_args_list, [mock.call(cores)] * 4)

    def test_assignment_local_artifact_allowlist_filters_inventory_and_capture(
        self,
    ) -> None:
        allowed = "core_documents/global_outline.md"
        blocked = "core_documents/character_sheets.md"
        self.publish(core_updates={"global_outline.md": "bounded outline"})
        adapter = owner_writing_artifact.WritingArtifactSourceAdapter(self.repository, fixture_value_inputs.make_scope(), allowed_paths=frozenset({allowed}))

        inventory = adapter.inventory(owner_capture_values.CONTEXT_SOURCE_POLICY)

        self.assertEqual(
            tuple(item.provenance.locator for item in inventory),
            (allowed,),
        )
        self.assertEqual(
            adapter.capture(allowed, owner_capture_values.CONTEXT_SOURCE_POLICY).descriptor,
            inventory[0],)
        with self.assertRaises(owner_capture_values.ContextSourceError) as caught:
            adapter.capture(blocked, owner_capture_values.CONTEXT_SOURCE_POLICY)
        self.assertIs(caught.exception.code, owner_capture_values.ContextSourceErrorCode.AUTHORITY_MISMATCH)
        for invalid in ({allowed}, frozenset({"../escape.md"})):
            with self.subTest(invalid=invalid), self.assertRaises(owner_capture_values.ContextSourceError):
                owner_writing_artifact.WritingArtifactSourceAdapter(self.repository, fixture_value_inputs.make_scope(), allowed_paths=invalid)

    def test_artifact_capture_retries_once_then_reports_persistent_drift(self) -> None:
        adapter = owner_writing_artifact.WritingArtifactSourceAdapter(self.repository, fixture_value_inputs.make_scope())
        first = fixture_value_inputs.make_artifact_snapshot(fixture_value_inputs.make_scope(), revision_seed=b"first")
        second = fixture_value_inputs.make_artifact_snapshot(fixture_value_inputs.make_scope(), revision_seed=b"second")
        stable = fixture_value_inputs.make_artifact_snapshot(fixture_value_inputs.make_scope(), revision_seed=b"stable")
        with mock.patch.object(
            adapter, "_capture_once", side_effect=(first, second, stable, stable)
        ) as capture:
            self.assertEqual(adapter.capture('core_documents/global_outline.md', owner_capture_values.CONTEXT_SOURCE_POLICY), stable)
        self.assertEqual(capture.call_count, 4)
        with mock.patch.object(
            adapter, "_capture_once", side_effect=(first, second, first, second)
        ):
            with self.assertRaises(owner_capture_values.ContextSourceError) as caught:
                adapter.capture(
                    "core_documents/global_outline.md", owner_capture_values.CONTEXT_SOURCE_POLICY)
        self.assertEqual(caught.exception.code, owner_capture_values.ContextSourceErrorCode.SOURCE_DRIFT)

        cores = owner_catalog.CORE_ARTIFACT_PATHS
        self.publish(core_updates={Path(path).name: "core" for path in cores})
        original = self.repository.read_current
        snapshots = original(cores)
        for bad, code in ((snapshots[::-1], owner_capture_values.ContextSourceErrorCode.SOURCE_DRIFT), (snapshots[:-1], owner_capture_values.ContextSourceErrorCode.SOURCE_MISSING), (snapshots + snapshots[:1], owner_capture_values.ContextSourceErrorCode.SOURCE_DRIFT)):
            with mock.patch.object(self.repository, "read_current", side_effect=lambda paths: bad if len(paths) > 1 else original(paths)):
                with self.assertRaises(owner_capture_values.ContextSourceError) as caught:
                    adapter._capture_core_sources(frozenset(cores), owner_capture_values.CONTEXT_SOURCE_POLICY)
            self.assertIs(caught.exception.code, code)
        with mock.patch.object(adapter, "_capture_source_group_once", side_effect=tuple((item,) * 4 for item in (first, second, stable, stable))) as capture:
            self.assertEqual(adapter._capture_core_sources(frozenset(cores), owner_capture_values.CONTEXT_SOURCE_POLICY), dict.fromkeys(cores, stable))
        self.assertEqual(capture.call_count, 4)
        with mock.patch.object(adapter, "_capture_source_group_once", side_effect=((first,) * 4, (second,) * 4) * 2):
            with self.assertRaises(owner_capture_values.ContextSourceError) as caught:
                adapter._capture_core_sources(frozenset(cores), owner_capture_values.CONTEXT_SOURCE_POLICY)
        self.assertIs(caught.exception.code, owner_capture_values.ContextSourceErrorCode.SOURCE_DRIFT)

    def test_artifact_inventory_is_complete_or_fails_custom_traversal_and_eligible_caps(
        self,
    ) -> None:
        self.publish(core_updates={'global_outline.md': 'outline', 'character_sheets.md': 'characters'})
        self.workspace.write_text("discussion_logs/internal.md", "internal")
        adapter = owner_writing_artifact.WritingArtifactSourceAdapter(self.repository, fixture_value_inputs.make_scope())
        with mock.patch.object(self.repository, "read_current", wraps=self.repository.read_current) as read:
            inventory = adapter.inventory(owner_capture_values.CONTEXT_SOURCE_POLICY)
        paths = tuple(sorted(item.provenance.locator for item in inventory))
        self.assertEqual(read.call_args_list, [mock.call(paths[:4])] * 2 + [mock.call(paths[4:])] * 2)
        singleton_policy = replace(owner_capture_values.CONTEXT_SOURCE_POLICY, max_inventory_body_utf8_bytes=owner_references.MAX_VIEWER_UTF8_BYTES)
        with mock.patch.object(self.repository, "read_current", wraps=self.repository.read_current) as read:
            self.assertEqual(adapter.inventory(singleton_policy), inventory)
        self.assertEqual(read.call_args_list, [mock.call((path,)) for path in paths for _ in range(2)])
        self.assertEqual({item.provenance.locator for item in inventory}, {'core_documents/global_outline.md', 'core_documents/character_sheets.md', 'core_documents/narrative_rules.md', 'core_documents/part_outlines.md', 'manuscript/part-01/chapter-01.md'})
        self.assertNotIn('discussion_logs/internal.md', {item.provenance.locator for item in inventory})
        with self.assertRaises(owner_capture_values.ContextSourceError) as eligible:
            adapter.inventory(replace(owner_capture_values.CONTEXT_SOURCE_POLICY, max_inventory_sources=2))
        self.assertEqual(eligible.exception.code, owner_capture_values.ContextSourceErrorCode.INVENTORY_LIMIT_EXCEEDED)
        with self.assertRaises(owner_capture_values.ContextSourceError) as traversal:
            adapter.inventory(replace(owner_capture_values.CONTEXT_SOURCE_POLICY, max_traversal_entries=1))
        self.assertEqual(traversal.exception.code, owner_capture_values.ContextSourceErrorCode.INVENTORY_LIMIT_EXCEEDED)

        with self.assertRaises(owner_capture_values.ContextSourceError) as limited:
            adapter.canonical_core_statuses(fixture_value_inputs.DIGEST, replace(owner_capture_values.CONTEXT_SOURCE_POLICY, max_source_utf8_bytes=1, max_source_scan_utf8_bytes=1))
        self.assertIs(limited.exception.code, owner_capture_values.ContextSourceErrorCode.SOURCE_LIMIT_EXCEEDED)

        original = self.repository.read_current
        with mock.patch.object(self.repository, "read_current", side_effect=lambda paths: (_ for _ in ()).throw(owner_filesystem_sources.ArtifactSourceInvalidUtf8Error("later private batch")) if len(paths) > 1 else original(paths)):
            with self.assertRaises(owner_capture_values.ContextSourceError) as earlier:
                adapter.inventory(replace(owner_capture_values.CONTEXT_SOURCE_POLICY, max_source_utf8_bytes=1, max_source_scan_utf8_bytes=1))
        self.assertIs(earlier.exception.code, owner_capture_values.ContextSourceErrorCode.SOURCE_LIMIT_EXCEEDED)
        with self.assertRaises(owner_capture_values.ContextSourceError) as aggregate:
            adapter.inventory(replace(owner_capture_values.CONTEXT_SOURCE_POLICY, max_source_utf8_bytes=10, max_source_scan_utf8_bytes=10, max_inventory_body_utf8_bytes=10))
        self.assertIs(aggregate.exception.code, owner_capture_values.ContextSourceErrorCode.INVENTORY_LIMIT_EXCEEDED)
        path = owner_catalog.CORE_ARTIFACT_PATHS[0]
        tiny = replace(owner_capture_values.CONTEXT_SOURCE_POLICY, max_source_utf8_bytes=1, max_source_scan_utf8_bytes=1)
        with self.assertRaises(owner_capture_values.ContextSourceError) as vector_cap:
            adapter._require_absent_cores((path,), tiny)
        with self.assertRaises(owner_capture_values.ContextSourceError) as single_cap:
            adapter._require_absent_core(path, tiny)
        self.assertIs(vector_cap.exception.code, owner_capture_values.ContextSourceErrorCode.SOURCE_UNAVAILABLE)
        self.assertIs(vector_cap.exception.code, single_cap.exception.code)

    def test_artifact_inventory_relist_drift_and_nonregular_sources_fail_completely(
        self,
    ) -> None:
        adapter = owner_writing_artifact.WritingArtifactSourceAdapter(self.repository, fixture_value_inputs.make_scope())
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
            with self.assertRaises(owner_capture_values.ContextSourceError) as drift:
                adapter.inventory(owner_capture_values.CONTEXT_SOURCE_POLICY)
        self.assertEqual(drift.exception.code, owner_capture_values.ContextSourceErrorCode.SOURCE_DRIFT)
        outside = self.workspace.root / "outside.md"
        outside.write_text("outside", encoding="utf-8")
        attacks = (('manuscript/symlink.md', lambda target: target.symlink_to(outside)), ('manuscript/hardlink.md', lambda target: os.link(outside, target)), ('manuscript/directory.md', lambda target: target.mkdir()))
        for locator, create in attacks:
            target = self.workspace.root / locator
            create(target)
            with self.subTest(locator=locator):
                with self.assertRaises(owner_capture_values.ContextSourceError) as caught:
                    adapter.capture(locator, owner_capture_values.CONTEXT_SOURCE_POLICY)
                self.assertEqual(caught.exception.code, owner_capture_values.ContextSourceErrorCode.SOURCE_MISSING)
            target.unlink() if not target.is_dir() or target.is_symlink() else target.rmdir()

        self.publish(core_updates={Path(path).name: "core" for path in owner_catalog.CORE_ARTIFACT_PATHS})
        with mock.patch.object(self.repository, "read_current", side_effect=owner_errors.WorkspaceSecurityError("private source")):
            with self.assertRaises(owner_capture_values.ContextSourceError) as unsafe:
                adapter.canonical_core_statuses(fixture_value_inputs.DIGEST, owner_capture_values.CONTEXT_SOURCE_POLICY)
        self.assertIs(unsafe.exception.code, owner_capture_values.ContextSourceErrorCode.SOURCE_UNSAFE)
        self.assertNotIn("private source", repr(unsafe.exception))
        first, later = owner_catalog.CORE_ARTIFACT_PATHS[:2]
        later_path = self.workspace.path(later); saved = later_path.read_bytes()
        later_path.unlink(); later_path.symlink_to(outside)
        try:
            self.assertFalse(self.repository.are_sources_absent((first, later)))
            with self.assertRaises(owner_capture_values.ContextSourceError) as real_unsafe:
                adapter._require_absent_cores((later, first), owner_capture_values.CONTEXT_SOURCE_POLICY)
            self.assertIs(real_unsafe.exception.code, owner_capture_values.ContextSourceErrorCode.SOURCE_UNSAFE)
        finally:
            later_path.unlink(); later_path.write_bytes(saved)

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
        captured_context = select_bounded_collaboration_context(history, event_ids=("proposal_event", "request_event"))
        scope = fixture_value_inputs.make_scope(subject_id=conversation_id)
        adapter = owner_focused_conversation.FocusedConversationSourceAdapter(
            services.catalog,
            services.event_store,
            scope,
            conversation_id,
            captured_context=captured_context,)
        snapshot = adapter.capture(
            f"conversation/{conversation_id}", owner_capture_values.CONTEXT_SOURCE_POLICY)
        self.assertEqual(
            tuple(record.record_id for record in snapshot.records),
            ("proposal_event", "request_event"),
        )
        self.assertIn("Consult specialists", snapshot.records[0].search_text)
        self.assertNotIn("route_fingerprint", snapshot.records[0].search_text)
        self.assertEqual(
            snapshot.descriptor.revision, snapshot.descriptor.content_digest)
        self.assertEqual(
            snapshot.descriptor.source_id, f"conversation:{conversation_id}")
        self.assertEqual(
            snapshot.descriptor.provenance.locator, f"conversation/{conversation_id}")
        with mock.patch.object(
            adapter,
            "_capture_once",
            side_effect=(snapshot, object(), snapshot, snapshot),
        ) as capture:
            self.assertEqual(
                adapter.capture(
                    f"conversation/{conversation_id}", owner_capture_values.CONTEXT_SOURCE_POLICY),
                snapshot,
            )
        self.assertEqual(capture.call_count, 4)
        with mock.patch.object(
            adapter,
            "_capture_once",
            side_effect=(snapshot, object(), snapshot, object()),
        ):
            with self.assertRaises(owner_capture_values.ContextSourceError) as drift:
                adapter.capture(
                    f"conversation/{conversation_id}", owner_capture_values.CONTEXT_SOURCE_POLICY
                )
        self.assertEqual(drift.exception.code, owner_capture_values.ContextSourceErrorCode.SOURCE_DRIFT)
        with self.assertRaises(owner_capture_values.ContextSourceError):
            owner_focused_conversation.FocusedConversationSourceAdapter(
                services.catalog,
                services.event_store,
                fixture_value_inputs.make_scope(subject_id="other"),
                conversation_id,)

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
        adapter = owner_focused_conversation.FocusedConversationSourceAdapter(
            services.catalog,
            services.event_store,
            fixture_value_inputs.make_scope(subject_id=metadata.conversation_id),
            metadata.conversation_id,)

        inventory = adapter.inventory(owner_capture_values.CONTEXT_SOURCE_POLICY)
        self.assertEqual(len(inventory), 1)
        snapshot = adapter.capture(
            f"conversation/{metadata.conversation_id}",
            owner_capture_values.CONTEXT_SOURCE_POLICY,)
        self.assertEqual(snapshot.descriptor, inventory[0])
        self.assertEqual(snapshot.records, ())
        value = json.loads(snapshot.content)
        self.assertEqual(value["records"], [])
        self.assertEqual(value["bootstrap"]["retained_records"], 0)
        self.assertEqual(value["bootstrap"]["total_records"], 5)

    def test_searchable_history_is_opt_in_same_session_active_and_frozen(self) -> None:
        from story_writing_agents import conversations as conversation
        from tests.collaboration_contract_fixture import USER, LEAD, make_batch, make_draft
        services = compose_filesystem_conversation_storage(self.workspace)
        focused = services.catalog.create("session", "Focused")
        sibling = services.catalog.create("session", "Sibling")
        archived = services.catalog.create("session", "Archived")
        cross_session = services.catalog.create("other-session", "Other")
        needle = "later USER retrospective 한🙂"
        for metadata in (focused, sibling, archived, cross_session):
            batch = contract_batches(metadata.conversation_id)["proposal"]
            if metadata is focused:
                messages = tuple(replace(make_draft(f"history_{number}", conversation.CollaborationEventKind.MESSAGE,
                    USER, LEAD, conversation.CollaborationMessagePayload(needle if number == 25 else
                        f"later USER retrospective {number}" if number in (27, 29, 33) else f"Earlier input {number}."),
                    turn_id=f"history_turn_{number}", step_id=f"history_step_{number}"),
                    session_id="session", conversation_id=metadata.conversation_id) for number in range(1, 34))
                for message in messages:
                    prefix = services.event_store.read("session", metadata.conversation_id)
                    services.event_store.append_batch(make_batch(prefix, "batch_" + message.event_id, message))
                batch = replace(batch, expected_last_sequence=33)
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
        adapter = owner_focused_conversation.FocusedConversationSourceAdapter(
            services.catalog,
            services.event_store,
            fixture_value_inputs.make_scope(subject_id=focused.conversation_id),
            focused.conversation_id,
            include_same_session_active=True,
            include_searchable_history=True,)

        inventory = adapter.inventory(owner_capture_values.CONTEXT_SOURCE_POLICY)

        self.assertEqual(
            tuple(item.provenance.locator for item in inventory),
            (
                f"conversation/{focused.conversation_id}",
                f"conversation/{sibling.conversation_id}",
            ),
        )
        focused_snapshot = adapter.capture(
            f"conversation/{focused.conversation_id}", owner_capture_values.CONTEXT_SOURCE_POLICY)
        self.assertEqual(
            tuple(record.record_id for record in focused_snapshot.records),
            tuple(f"history_{number}" for number in range(1, 34)) + (
                "prepared_call_lead",
                "submitted_call_lead",
                "judgment_event",
                "proposal_event",
                "request_event",
            ),
        )
        complete = json.loads(focused_snapshot.content)["bootstrap"]
        self.assertEqual((complete["total_records"], complete["retained_records"], complete["omitted_records"], complete["truncated"]),
            (38, 38, 0, False))
        scope = adapter.scope
        def registry_for(conversation_adapter):
            return owner_registry.ContextSourceRegistry(owner_writing_artifact.WritingArtifactSourceAdapter(self.repository, scope), conversation_adapter,
                owner_active_debate.ActiveDebateSourceAdapter(self.workspace, scope, None), owner_own_agent_state.OwnAgentStateSourceAdapter(self.workspace, scope))
        registry = registry_for(adapter)
        manifest = ContextManifest(scope, tuple(fixture_value_inputs.make_entry(item, number) for number, item in enumerate(inventory)))
        matches, truncated, scanned = registry.search(manifest, (ContextSearchQuery(needle, (ContextSourceKind.CONVERSATION,)),))
        self.assertFalse(truncated); self.assertEqual(len(matches), 1)
        self.assertEqual((matches[0].record.record_id, matches[0].record.boundary), ("history_25", ContextSourceBoundary.records(25, 25)))
        self.assertIn(needle, matches[0].record.content)
        self.assertEqual(scanned, sum(item.utf8_bytes for item in inventory))
        recent_query = ContextSearchQuery("later USER retrospective", (ContextSourceKind.CONVERSATION,))
        for count, policy in ((3, CONTEXT_ACTION_POLICY),
                (2, replace(CONTEXT_ACTION_POLICY, max_hits_per_source_query=2)),
                (1, replace(CONTEXT_ACTION_POLICY, max_hits_per_source_query=1, max_hits_per_query=1)),
                (1, replace(CONTEXT_ACTION_POLICY, max_hits_per_source_query=1, max_hits_per_query=1, max_hits_per_search=1))):
            recent, omitted, _ = registry.search(manifest, (recent_query,), action_policy=policy)
            self.assertEqual(tuple(hit.record.boundary.start for hit in recent), (33, 29, 27)[:count])
            self.assertTrue(omitted)
        prioritized, omitted, _ = registry.search(manifest,
            (ContextSearchQuery(needle, (ContextSourceKind.CONVERSATION,)), recent_query))
        self.assertEqual(tuple((hit.query_index, hit.record.boundary.start) for hit in prioritized),
            ((1, 25), (2, 33), (2, 29), (2, 27)))
        self.assertFalse(omitted)
        events = services.event_store.read("session", focused.conversation_id)
        partial = select_bounded_collaboration_context(events, event_ids=tuple(event.event_id for event in events))
        self.assertEqual((partial.retained_records, partial.omitted_records, partial.truncated), (24, 14, True))
        partial_adapter = owner_focused_conversation.FocusedConversationSourceAdapter(services.catalog, services.event_store, scope,
            focused.conversation_id, captured_context=partial)
        partial_snapshot = partial_adapter.capture(f"conversation/{focused.conversation_id}", owner_capture_values.CONTEXT_SOURCE_POLICY)
        partial_registry = registry_for(partial_adapter)
        partial_manifest = ContextManifest(scope, (fixture_value_inputs.make_entry(partial_snapshot.descriptor),))
        partial_hits, partial_truncated, _ = partial_registry.search(partial_manifest,
            (ContextSearchQuery(needle, (ContextSourceKind.CONVERSATION,)),))
        self.assertEqual(partial_hits, ()); self.assertTrue(partial_truncated)
        with self.assertRaises(owner_capture_values.ContextSourceError) as limited:
            adapter.capture(f"conversation/{focused.conversation_id}", replace(owner_capture_values.CONTEXT_SOURCE_POLICY, max_records_per_source=37))
        self.assertIs(limited.exception.code, owner_capture_values.ContextSourceErrorCode.SOURCE_LIMIT_EXCEEDED)
        for changes in ({"requested_records": True}, {"total_records": 37}, {"omitted_records": 1},
                        {"truncated": 0}, {"retained_utf8_bytes": complete["retained_utf8_bytes"] + 1}):
            malformed = json.loads(focused_snapshot.content); malformed["bootstrap"].update(changes)
            encoded = json.dumps(malformed, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
            descriptor = replace(focused_snapshot.descriptor, revision=fixture_value_inputs.digest_bytes(encoded), content_digest=fixture_value_inputs.digest_bytes(encoded), utf8_bytes=len(encoded))
            with self.assertRaises(owner_capture_values.ContextSourceError) as corrupt:
                owner_capture_values.ContextSourceSnapshot(descriptor, focused_snapshot.records, encoded)
            self.assertIs(corrupt.exception.code, owner_capture_values.ContextSourceErrorCode.ADAPTER_CONTRACT_VIOLATION)
        self.assertIs(
            adapter.capture(
                f"conversation/{focused.conversation_id}", owner_capture_values.CONTEXT_SOURCE_POLICY),
            focused_snapshot,
        )
        services.catalog.create("session", "Created after snapshot")
        self.assertEqual(adapter.inventory(owner_capture_values.CONTEXT_SOURCE_POLICY), inventory)
        for excluded in (archived, cross_session):
            with self.subTest(excluded=excluded.conversation_id):
                with self.assertRaises(owner_capture_values.ContextSourceError) as caught:
                    adapter.capture(
                        f"conversation/{excluded.conversation_id}",
                        owner_capture_values.CONTEXT_SOURCE_POLICY,
                    )
                self.assertEqual(
                    caught.exception.code,
                    owner_capture_values.ContextSourceErrorCode.AUTHORITY_MISMATCH,)

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
        debate_scope = fixture_value_inputs.make_scope(subject_kind=ContextSubjectKind.DEBATE, subject_id=debate_id)
        debate = owner_active_debate.ActiveDebateSourceAdapter(
            self.workspace, debate_scope, debate_id
        ).capture(f"debate/{debate_id}", owner_capture_values.CONTEXT_SOURCE_POLICY)
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
            state_scope = fixture_value_inputs.make_scope(agent_id=agent_id)
            snapshot = owner_own_agent_state.OwnAgentStateSourceAdapter(self.workspace, state_scope).capture(
                f"agent-state/{agent_id}", owner_capture_values.CONTEXT_SOURCE_POLICY
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
        missing = owner_active_debate.ActiveDebateSourceAdapter(
            self.workspace,
            fixture_value_inputs.make_scope(subject_kind=ContextSubjectKind.DEBATE, subject_id="missing"),
            "missing",
        )
        with self.assertRaises(owner_capture_values.ContextSourceError) as caught:
            missing.capture("debate/missing", owner_capture_values.CONTEXT_SOURCE_POLICY)
        self.assertEqual(caught.exception.code, owner_capture_values.ContextSourceErrorCode.SOURCE_MISSING)
