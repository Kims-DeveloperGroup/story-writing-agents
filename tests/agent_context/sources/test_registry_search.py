'Existing RegistrySearchTests scenarios, moved without new cases.'
from __future__ import annotations
from dataclasses import replace
from unittest import mock
from story_writing_agents.agent_context.actions import CONTEXT_ACTION_POLICY, ContextSearchQuery
from story_writing_agents.agent_context.manifest import ContextManifest, ContextManifestEntry, ContextSourceKind
from story_writing_agents.conversation_storage import compose_filesystem_conversation_storage
from story_writing_agents.agent_context.sources import active_debate as owner_active_debate
from story_writing_agents.agent_context.sources import capture_values as owner_capture_values
from story_writing_agents.agent_context.sources import focused_conversation as owner_focused_conversation
from story_writing_agents.agent_context.sources import own_agent_state as owner_own_agent_state
from story_writing_agents.agent_context.sources import registry as owner_registry
from story_writing_agents.agent_context.sources import writing_artifact as owner_writing_artifact
from tests.agent_context.sources import authority_fixture as fixture_authority_fixture
from tests.agent_context.sources import value_inputs as fixture_value_inputs


class RegistrySearchTests(fixture_authority_fixture.SourceAuthorityFixture):
    """Verify canonical inventory, literal search, dedupe, drift, and scan caps."""

    def test_registry_requires_exact_four_same_scope_adapters(self) -> None:
        scope = fixture_value_inputs.make_scope()
        registry = self.registry(scope)
        self.assertEqual(registry.scope, scope)
        other = fixture_value_inputs.make_scope(session_id="other")
        services = compose_filesystem_conversation_storage(self.workspace)
        with self.assertRaises(owner_capture_values.ContextSourceError) as caught:
            owner_registry.ContextSourceRegistry(
                owner_writing_artifact.WritingArtifactSourceAdapter(self.repository, scope),
                owner_focused_conversation.FocusedConversationSourceAdapter(services.catalog, services.event_store, other, None),
                owner_active_debate.ActiveDebateSourceAdapter(self.workspace, scope, None),
                owner_own_agent_state.OwnAgentStateSourceAdapter(self.workspace, scope),
            )
        self.assertEqual(caught.exception.code, owner_capture_values.ContextSourceErrorCode.SCOPE_MISMATCH)

    def test_search_normalizes_source_controls_orders_and_deduplicates_by_first_query(
        self,
    ) -> None:
        scope = fixture_value_inputs.make_scope()
        registry = self.registry(scope)
        snapshot = fixture_value_inputs.make_artifact_snapshot(scope, lines=("prefix\n", "ＮＥＥＤＬＥ\u200bgap\n", "needle tail\n"))
        manifest = ContextManifest(scope, (fixture_value_inputs.make_entry(snapshot.descriptor),))
        calls: list[str] = []

        def capture(
            _manifest: ContextManifest,
            entry: ContextManifestEntry,
            _policy: owner_capture_values.ContextSourcePolicy,
        ) -> owner_capture_values.ContextSourceSnapshot:
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
        scope = fixture_value_inputs.make_scope()
        registry = self.registry(scope)
        lines = tuple(
            ("needle 한🙂\n" if index == 15 else f"{index}\n") for index in range(1, 31)
        )
        snapshot = fixture_value_inputs.make_artifact_snapshot(scope, lines=lines)
        manifest = ContextManifest(scope, (fixture_value_inputs.make_entry(snapshot.descriptor),))
        registry.capture = lambda *_args: snapshot  # type: ignore[method-assign]
        matches, _, _ = registry.search(
            manifest,
            (ContextSearchQuery("needle", (ContextSourceKind.WRITING_ARTIFACT,)),),
        )
        self.assertEqual(
            (matches[0].record.boundary.start, matches[0].record.boundary.end), (3, 27)
        )
        lower = replace(CONTEXT_ACTION_POLICY, max_snippet_utf8_bytes=20, max_excerpt_utf8_bytes=20)
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
        scope = fixture_value_inputs.make_scope()
        registry = self.registry(scope)
        snapshot = fixture_value_inputs.make_artifact_snapshot(scope)
        manifest = ContextManifest(scope, (fixture_value_inputs.make_entry(snapshot.descriptor),))
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
        drifted = fixture_value_inputs.make_artifact_snapshot(scope, lines=("needle changed\n",), revision_seed=b"changed")
        registry.capture = mock.Mock(side_effect=(snapshot, drifted))  # type: ignore[method-assign]
        with self.assertRaises(owner_capture_values.ContextSourceError) as caught:
            registry.search(
                manifest,
                (ContextSearchQuery("needle", (ContextSourceKind.WRITING_ARTIFACT,)),),
            )
        self.assertEqual(caught.exception.code, owner_capture_values.ContextSourceErrorCode.SOURCE_DRIFT)
