'Existing SearchAndAnswerLifecycleTests scenarios, moved without new cases.'
from __future__ import annotations
import json
import re
import unittest
from dataclasses import replace
from unittest import mock
from story_writing_agents.agent_context.actions import CONTEXT_ACTION_POLICY, ContextActionError, ContextActionErrorCode, ContextAnswerAction, ContextReadAction, ContextSearchAction, ContextSearchQuery, ContextSourceBoundary
from story_writing_agents.agent_context.manifest import ContextAuthorityKind, ContextManifest, ContextManifestEntry, ContextProvenance, ContextSourceKind, ContextSubjectKind
from story_writing_agents.agent_context.contracts import call_offers as ctx_call_offers, context_values as ctx_context_values
from story_writing_agents.agent_context.sources import capture_values as owner_capture_values
from story_writing_agents.agent_context.sources import registry as owner_registry
from tests.agent_context.invocation import session_fixture as fixture_session_fixture


class SearchAndAnswerLifecycleTests(unittest.TestCase):
    """Verify bounded SEARCH and terminal answer authorization facts."""

    def setUp(self) -> None:
        self.manifest, self.snapshots = fixture_session_fixture.manifest_fixture(); self.binding = fixture_session_fixture.binding(self.manifest)

    def test_search_preserves_query_match_order_mints_handles_and_rejects_reuse(self, ) -> None:
        registry = fixture_session_fixture.registry(fixture_session_fixture.SCOPE, self.snapshots); entries = tuple(self.manifest.entries)
        matches = tuple( owner_capture_values.ContextSourceSearchMatch(index, self.snapshots[entry.source_id].descriptor,
                self.snapshots[entry.source_id].records[0], f"needle {index}", len(f"needle {index}".encode()),)
            for index, entry in enumerate(reversed(entries), start=1))
        registry.search = mock.Mock( return_value=( matches, False,
                sum(item.descriptor.utf8_bytes for item in matches), ) )
        session = fixture_session_fixture.session(self.binding, registry)
        queries = ( ContextSearchQuery("needle", (ContextSourceKind.WRITING_ARTIFACT,)),
            ContextSearchQuery("private", (ContextSourceKind.WRITING_ARTIFACT,)), )
        observation = session.search(ContextSearchAction(fixture_session_fixture.correlation(self.binding, 1), queries))
        self.assertEqual(tuple(hit.query_index for hit in observation.hits), (1, 2))
        self.assertEqual( tuple(hit.source_id for hit in observation.hits),
            tuple(item.descriptor.source_id for item in matches), )
        self.assertTrue( all( re.fullmatch(r"ctxh_v1_[0-9a-f]{64}", hit.handle.value) for hit in observation.hits ) )
        self.assertEqual(session.next_action_index, 2); registry.search.assert_called_once()

    def test_prefixed_search_filters_before_one_capture_revalidation_pair(self) -> None:
        scoped_body = "needle" + "x" * 979 + "\n"; self.assertEqual(len(scoped_body.encode("utf-8")), 986)
        snapshots_tuple = (fixture_session_fixture.snapshot( fixture_session_fixture.SCOPE, "manuscript/part_01/chapter.md", scoped_body,), fixture_session_fixture.snapshot(
                fixture_session_fixture.SCOPE, "manuscript/part_010/chapter.md", "needle outside exact part prefix\n",), fixture_session_fixture.snapshot(
                fixture_session_fixture.SCOPE, "core_documents/global_outline.md", "needle outside manuscript prefix\n",),)
        manifest = ContextManifest( fixture_session_fixture.SCOPE, tuple( fixture_session_fixture.entry(snapshot, index)
                for index, snapshot in enumerate(snapshots_tuple)),)
        snapshots = { snapshot.descriptor.source_id: snapshot for snapshot in snapshots_tuple }
        registry = fixture_session_fixture.registry(fixture_session_fixture.SCOPE, snapshots); registry.search = owner_registry.ContextSourceRegistry.search.__get__(registry)
        session = fixture_session_fixture.session(fixture_session_fixture.binding(manifest), registry)
        query = ContextSearchQuery( "needle", (ContextSourceKind.WRITING_ARTIFACT,), "manuscript/part_01/", )

        observation = session.search( ContextSearchAction( fixture_session_fixture.correlation(session.binding, 1), (query,), ) )

        self.assertEqual(len(observation.hits), 1); self.assertEqual(observation.hits[0].query_index, 1)
        self.assertEqual( observation.hits[0].source_id, snapshots_tuple[0].descriptor.source_id, )
        self.assertEqual(registry.capture.call_count, 2)
        captured_manifests = tuple((call.args[0] for call in registry.capture.call_args_list))
        self.assertTrue( all( current.entries == (manifest.entries[0],) for current in captured_manifests ) )
        self.assertEqual( sum( snapshots[entry.source_id].descriptor.utf8_bytes
                for entry in captured_manifests[0].entries), 986, )
        self.assertEqual(session._scan_utf8_bytes, 986)

    def test_part_prefix_matrix_scans_nested_before_hit_limits_and_excludes_decoys(self, ) -> None:
        prefix = "manuscript/part_01/"
        eligible_paths = ( "manuscript/part_01/chapter_01.md", "manuscript/part_01/scenes/chapter_02.md",
            "manuscript/part_01/scenes/memories/chapter_03.md", )
        decoy_paths = ( "manuscript/part_02/chapter_07.md", "manuscript/part_010/chapter_99.md",
            "core_documents/narrative_rules.md", )
        internal_path = "discussion_logs/private_context.md"
        snapshots_tuple = tuple(fixture_session_fixture.snapshot( fixture_session_fixture.SCOPE, path, ("part one release needle\n" if path in eligible_paths
            else "part one release needle " * 20 + "decoy\n"),) for path in (*eligible_paths, *decoy_paths))
        manifest = ContextManifest( fixture_session_fixture.SCOPE, tuple( fixture_session_fixture.entry(snapshot, index)
                for index, snapshot in enumerate(snapshots_tuple)),)
        snapshots = { snapshot.descriptor.source_id: snapshot for snapshot in snapshots_tuple }
        registry = fixture_session_fixture.registry(fixture_session_fixture.SCOPE, snapshots); registry.search = owner_registry.ContextSourceRegistry.search.__get__(registry)
        policy = replace( CONTEXT_ACTION_POLICY, max_hits_per_search=1, max_hits_per_query=1,
            max_hits_per_source_query=1, )
        session = fixture_session_fixture.session( fixture_session_fixture.binding(manifest), registry, action_policy=policy, )
        query = ContextSearchQuery( "part one release needle", (ContextSourceKind.WRITING_ARTIFACT,), prefix, )

        observation = session.search( ContextSearchAction( fixture_session_fixture.correlation(session.binding, 1), (query,), ) )

        self.assertEqual(len(observation.hits), 1); self.assertTrue(observation.truncated)
        captured_entries = tuple((call.args[1] for call in registry.capture.call_args_list))
        captured_paths = tuple(entry.provenance.locator for entry in captured_entries)
        self.assertEqual( set(captured_paths), set(eligible_paths), )
        self.assertEqual( {path: captured_paths.count(path) for path in eligible_paths},
            {path: 2 for path in eligible_paths}, )
        self.assertTrue( all(call.args[0].entries == tuple(entry for entry in manifest.entries
                    if entry.provenance.locator in eligible_paths) for call in registry.capture.call_args_list))
        self.assertEqual( session._scan_utf8_bytes, sum( snapshots[fixture_session_fixture.artifact_id(path)].descriptor.utf8_bytes
                for path in eligible_paths ), )
        self.assertTrue(all(path not in captured_paths for path in decoy_paths))
        self.assertNotIn( internal_path, tuple(entry.provenance.locator for entry in manifest.entries), )
        self.assertNotIn(internal_path, captured_paths)
        self.assertIn( observation.hits[0].source_id, {fixture_session_fixture.artifact_id(path) for path in eligible_paths}, )

        invalid_matrix = (( "unsafe_parent", lambda: ContextSearchQuery( "needle",
            (ContextSourceKind.WRITING_ARTIFACT,), "manuscript/part_01/../",),), ("lookalike_without_separator",
            lambda: ContextSearchQuery( "needle", (ContextSourceKind.WRITING_ARTIFACT,), "manuscript/part_01",),),
            ("incompatible_source", lambda: ContextSearchQuery( "needle", (ContextSourceKind.CONVERSATION,),
            prefix,),), ("mixed_prefixes", lambda: ContextSearchAction(fixture_session_fixture.correlation(self.binding, 1), (
                        ContextSearchQuery("one", (ContextSourceKind.WRITING_ARTIFACT,), prefix,
            ), ContextSearchQuery( "two", (ContextSourceKind.WRITING_ARTIFACT,), "manuscript/part_02/",),),),),)
        for label, construct in invalid_matrix:
            with self.subTest(rejected=label), self.assertRaises(ContextActionError): construct()

    def test_compact_search_read_evidence_retains_all_authoritative_facts(self) -> None:
        scope = replace( fixture_session_fixture.SCOPE, subject_kind=ContextSubjectKind.DEBATE, subject_id="debate-one", )
        canonical = lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        conversation_snapshot = fixture_session_fixture.conversation_snapshot(scope, ("Earlier supplied text.",) * 24 + ("conversation needle",))
        conversation_content = conversation_snapshot.records[-1].content
        debate_state = {"session_id": scope.session_id, "debate_id": "debate-one", "status": "active", "terminal": False}
        debate_message = {"content": "active debate needle", "kind": "message", "phase": "review", "revision": 1,
            "round": 1, "sender": scope.agent_id, "timestamp": "2026-11-09T00:00:00+00:00"}
        state_entry = {"content": "own state needle", "agent_id": scope.agent_id, "session_id": scope.session_id,
            "debate_id": "debate-one", "kind": "note", "phase": "review", "revision": 1, "round": 1,
            "timestamp": "2026-11-09T00:00:00+00:00"}
        source_values = {ContextSourceKind.ACTIVE_DEBATE: {"state": debate_state, "messages": [debate_message]},
            ContextSourceKind.OWN_AGENT_STATE: [state_entry]}
        record_values = {ContextSourceKind.ACTIVE_DEBATE: (("state", 1, debate_state, canonical(debate_state)),
                ("message_1", 2, debate_message, debate_message["content"])),
            ContextSourceKind.OWN_AGENT_STATE: (("entry_1", 1, state_entry, state_entry["content"]),)}
        cases = ( ( ContextSourceKind.WRITING_ARTIFACT, ContextAuthorityKind.WRITING_ARTIFACT_REPOSITORY,
            "manuscript/part-01/chapter.md", ContextSourceBoundary.artifact_lines(1, 1), "artifact needle\n",),
            (ContextSourceKind.CONVERSATION, ContextAuthorityKind.CONVERSATION_STORAGE,
            "conversation/conversation-one", ContextSourceBoundary.records(25, 25), conversation_content,),
            (ContextSourceKind.ACTIVE_DEBATE, ContextAuthorityKind.ACTIVE_DEBATE, "debate/debate-one",
                ContextSourceBoundary.records(2, 2), canonical(debate_message),), (ContextSourceKind.OWN_AGENT_STATE,
                ContextAuthorityKind.OWN_AGENT_STATE, "agent-state/lead_orchestrator",
                ContextSourceBoundary.records(1, 1), canonical(state_entry),),)
        snapshots_list: list[owner_capture_values.ContextSourceSnapshot] = []
        for index, (kind, authority, locator, boundary, content) in enumerate(cases, start=1, ):
            if kind is ContextSourceKind.CONVERSATION:
                snapshots_list.append(conversation_snapshot); continue
            encoded = content.encode("utf-8")
            record = owner_capture_values.ContextSourceRecord(f'record-{index}', boundary, content, content, fixture_session_fixture.digest_bytes(encoded), len(encoded))
            records = (record,)
            if kind in source_values:
                records = tuple(owner_capture_values.ContextSourceRecord(identity, ContextSourceBoundary.records(number, number), canonical(value),
                    search_text, fixture_session_fixture.digest_bytes(canonical(value).encode()), len(canonical(value).encode()))
                    for identity, number, value, search_text in record_values[kind])
                encoded = canonical(source_values[kind]).encode()
            source_id = {ContextSourceKind.WRITING_ARTIFACT: fixture_session_fixture.artifact_id(locator),
                ContextSourceKind.CONVERSATION: "conversation:conversation-one",
                ContextSourceKind.ACTIVE_DEBATE: "active_debate:debate-one", ContextSourceKind.OWN_AGENT_STATE: (
                    "own_agent_state:lead_orchestrator"), }[kind]
            content_digest = fixture_session_fixture.digest_bytes(encoded)
            descriptor = owner_capture_values.ContextSourceDescriptor( scope, source_id, kind, ( fixture_session_fixture.digest_bytes(f"revision-{index}".encode())
                    if kind is ContextSourceKind.WRITING_ARTIFACT else content_digest),
                ContextProvenance(authority, locator), content_digest, len(encoded), len(records),)
            snapshots_list.append(owner_capture_values.ContextSourceSnapshot(descriptor, records, encoded))
        manifest = ContextManifest( scope, tuple(
                fixture_session_fixture.entry(snapshot, index) for index, snapshot in enumerate(snapshots_list) ), )
        snapshots = {snapshot.descriptor.source_id: snapshot for snapshot in snapshots_list}
        registry = fixture_session_fixture.registry(scope, snapshots); registry.search = owner_registry.ContextSourceRegistry.search.__get__(registry)
        aliases = { entry.source_id: f"s{index}" for index, entry in enumerate(manifest.entries, start=1) }
        session = fixture_session_fixture.session( fixture_session_fixture.binding(manifest), registry, source_aliases=aliases, )
        search = session.search(ContextSearchAction( fixture_session_fixture.correlation(session.binding, 1), (
                    ContextSearchQuery("needle", ctx_context_values.SEARCH_SOURCE_KIND_ORDER,), ),))
        search_serialized = session.serialize_observation(search); search_wire = json.loads(search_serialized)
        self.assertEqual(search_wire["observation_type"], "context_search_results")
        self.assertFalse(search_wire["truncated"]); self.assertIsNone(search_wire["zero_hit_receipt"])
        self.assertEqual(len(search_wire["search_results"]), 4)
        source_types = ("story_document", "conversation_history", "current_consensus_run", "agent_work_history")
        for expected_index, (hit, expected) in enumerate(zip(search_wire["search_results"], cases, strict=True), start=1):
            kind, authority, locator, boundary, content = expected; snapshot = snapshots_list[expected_index - 1]
            self.assertEqual(hit["query_index"], 1)
            self.assertEqual(hit["source"], {
                "source_reference_id": expected_index, "source_id": snapshot.descriptor.source_id,
                "source_type": source_types[expected_index - 1], "source_revision": snapshot.descriptor.revision,
                "source_digest": snapshot.descriptor.content_digest, "source_utf8_bytes": snapshot.descriptor.utf8_bytes})
            bounds = ({"start_line": boundary.start, "end_line": boundary.end} if kind is ContextSourceKind.WRITING_ARTIFACT else {
                "start_record_number": boundary.start, "end_record_number": boundary.end,
                "first_record_id": boundary.first_id, "last_record_id": boundary.last_id})
            self.assertEqual(hit["source_range"], {
                "range_type": "line_range" if kind is ContextSourceKind.WRITING_ARTIFACT else "record_range",
                "range_digest": fixture_session_fixture.digest_bytes(content.encode("utf-8")), "range_utf8_bytes": len(content.encode("utf-8")), **bounds})
            safe_locator = (f"{locator}/debate-one" if kind is ContextSourceKind.OWN_AGENT_STATE
                            else locator.removeprefix("manuscript/") if kind is ContextSourceKind.WRITING_ARTIFACT else locator)
            self.assertEqual(hit["provenance"], {"authority_type": authority.value, "source_locator": safe_locator})
            self.assertRegex(hit["read_handle"], r"\Ah[A-Za-z0-9_-]{16}\Z")
            self.assertEqual(hit["snippet_utf8_bytes"], len(hit["snippet_text"].encode("utf-8")))
        handles = tuple(hit.handle for hit in search.hits)
        read, receipt = session.read(ContextReadAction(fixture_session_fixture.correlation(session.binding, 2), handles))
        read_serialized = session.serialize_observation(read); read_wire = json.loads(read_serialized)
        self.assertEqual(read_wire["observation_type"], "context_read_results")
        self.assertEqual(len(read_wire["read_results"]), 4)
        for expected_index, (search_hit, evidence, expected) in enumerate(
            zip(search_wire["search_results"], read_wire["read_results"], cases, strict=True), start=1, ):
            self.assertEqual({key: evidence[key] for key in ("source", "source_range", "provenance", "read_handle")},
                             {key: search_hit[key] for key in ("source", "source_range", "provenance", "read_handle")})
            self.assertEqual(evidence["evidence_id"], f"read_2_{expected_index}")
            self.assertEqual(evidence["content_text"], expected[4])
        session.complete_read(receipt)
        authorization = session.authorize_answer( ContextAnswerAction( fixture_session_fixture.correlation(session.binding, 3),
                fixture_session_fixture.PRIVATE_ANSWER, handles, ) )
        self.assertEqual( tuple(item.handle for item in authorization.used_evidence), handles, )
        framed = json.dumps( [search_wire, read_wire], ensure_ascii=False, separators=(",", ":"), )
        self.assertNotIn("/Users/", framed); self.assertNotIn("character_custodian", framed)
        self.assertLessEqual( len(search_serialized.encode("utf-8")) + len(read_serialized.encode("utf-8")),
            CONTEXT_ACTION_POLICY.max_observation_utf8_bytes, )
        complete_session = fixture_session_fixture.session(fixture_session_fixture.binding(manifest), registry)
        absent_query = (ContextSearchQuery("genuinely absent", (ContextSourceKind.CONVERSATION,)),)
        complete_miss = complete_session.search(ContextSearchAction(fixture_session_fixture.correlation(complete_session.binding, 1), absent_query))
        self.assertEqual(complete_miss.hits, ()); self.assertFalse(complete_miss.truncated)
        self.assertIsNotNone(complete_miss.zero_hit_receipt)
        partial_value = json.loads(snapshots_list[1].content)
        partial_value["records"] = partial_value["records"][:24]
        partial_value["bootstrap"].update(retained_records=24, omitted_records=1, truncated=True,
            retained_utf8_bytes=sum(len(row["fields"][0]["text"].encode()) for row in partial_value["records"]))
        partial_bytes = canonical(partial_value).encode(); partial_digest = fixture_session_fixture.digest_bytes(partial_bytes)
        partial = owner_capture_values.ContextSourceSnapshot(replace(snapshots_list[1].descriptor, revision=partial_digest,
            content_digest=partial_digest, utf8_bytes=len(partial_bytes), record_count=24), snapshots_list[1].records[:24], partial_bytes)
        partial_manifest = ContextManifest(scope, (fixture_session_fixture.entry(partial, 0),))
        partial_registry = fixture_session_fixture.registry(scope, {partial.descriptor.source_id: partial})
        partial_registry.search = owner_registry.ContextSourceRegistry.search.__get__(partial_registry)
        partial_session = fixture_session_fixture.session(fixture_session_fixture.binding(partial_manifest), partial_registry)
        partial_miss = partial_session.search(ContextSearchAction(fixture_session_fixture.correlation(partial_session.binding, 1), absent_query))
        self.assertEqual(partial_miss.hits, ()); self.assertTrue(partial_miss.truncated)
        self.assertIsNone(partial_miss.zero_hit_receipt)

    def test_search_preflight_and_executed_failures_charge_differently(self) -> None:
        registry = fixture_session_fixture.registry(fixture_session_fixture.SCOPE, self.snapshots); session = fixture_session_fixture.session(self.binding, registry)
        wrong = replace(fixture_session_fixture.correlation(self.binding, 1), result_correlation_id="wrong")
        with self.assertRaises(ContextActionError) as preflight:
            session.search( ContextSearchAction(wrong, ( ContextSearchQuery(
                            "needle", (ContextSourceKind.WRITING_ARTIFACT,)),),))
        self.assertEqual(preflight.exception.code, ContextActionErrorCode.CORRELATION_MISMATCH)
        self.assertEqual(session.next_action_index, 1); registry.search.assert_not_called()
        registry = fixture_session_fixture.registry(fixture_session_fixture.SCOPE, self.snapshots)
        registry.search = mock.Mock(side_effect=owner_capture_values.ContextSourceError(owner_capture_values.ContextSourceErrorCode.SOURCE_DRIFT))
        session = fixture_session_fixture.session(self.binding, registry)
        with self.assertRaises(owner_capture_values.ContextSourceError):
            session.search( ContextSearchAction( fixture_session_fixture.correlation(self.binding, 1), ( ContextSearchQuery(
                            "needle", (ContextSourceKind.WRITING_ARTIFACT,)),),))
        self.assertEqual(session.next_action_index, 2); self.assertEqual(session.state, ctx_call_offers.ContextInvocationState.FAILED)

    def test_every_correlation_dimension_is_exact_and_preflight_noncharging(self, ) -> None:
        other_scope = replace(fixture_session_fixture.SCOPE, session_id="other")
        invalid = ( replace(fixture_session_fixture.correlation(self.binding, 1), scope=other_scope), replace(fixture_session_fixture.correlation(self.binding,
            1), operation_id="other"), replace(fixture_session_fixture.correlation(self.binding, 1), result_correlation_id="other"),
            replace(fixture_session_fixture.correlation(self.binding, 1), action_index=2), )
        query = ContextSearchQuery( "needle", (ContextSourceKind.WRITING_ARTIFACT,), )
        for correlation in invalid:
            with self.subTest(correlation=correlation):
                registry = fixture_session_fixture.registry(fixture_session_fixture.SCOPE, self.snapshots); session = fixture_session_fixture.session(self.binding, registry)
                with self.assertRaises(ContextActionError) as caught:
                    session.search(ContextSearchAction(correlation, (query,)))
                self.assertEqual( caught.exception.code, ContextActionErrorCode.CORRELATION_MISMATCH, )
                self.assertEqual(session.next_action_index, 1); registry.search.assert_not_called()

    def test_nonterminal_and_provider_call_caps_leave_the_terminal_slot_exact(self, ) -> None:
        policy = replace(CONTEXT_ACTION_POLICY, max_nonterminal_actions=2, max_provider_calls=3, max_search_actions=2)
        registry = fixture_session_fixture.registry(fixture_session_fixture.SCOPE, self.snapshots); session = fixture_session_fixture.session(self.binding, registry, action_policy=policy)
        query = ContextSearchQuery( "absent", (ContextSourceKind.WRITING_ARTIFACT,), )
        for index in (1, 2): session.search(ContextSearchAction(fixture_session_fixture.correlation(self.binding, index), (query,)))
        self.assertEqual(session.next_action_index, 3)
        authorization = session.authorize_answer(ContextAnswerAction(fixture_session_fixture.correlation(self.binding, 3), fixture_session_fixture.PRIVATE_ANSWER))
        self.assertEqual( authorization.correlation.action_index, policy.max_provider_calls, )

    def test_search_trims_tail_to_frame_budget_and_empty_frame_failure_is_terminal(self, ) -> None:
        snapshot = fixture_session_fixture.conversation_snapshot(fixture_session_fixture.SCOPE, ("needle",) * 3)
        manifest = ContextManifest(fixture_session_fixture.SCOPE, (fixture_session_fixture.entry(snapshot, 0),)); binding = fixture_session_fixture.binding(manifest)
        registry = fixture_session_fixture.registry(fixture_session_fixture.SCOPE, {snapshot.descriptor.source_id: snapshot})
        registry.search = owner_registry.ContextSourceRegistry.search.__get__(registry)
        policy = replace(CONTEXT_ACTION_POLICY, max_snippet_utf8_bytes=100, max_excerpt_utf8_bytes=2048,
            max_source_read_utf8_bytes=2048, max_read_utf8_bytes=2048, max_observation_utf8_bytes=2048, )
        session = fixture_session_fixture.session(binding, registry, action_policy=policy)
        observation = session.search( ContextSearchAction( fixture_session_fixture.correlation(binding, 1),
                (ContextSearchQuery("needle", (ContextSourceKind.CONVERSATION,)),), ) )
        self.assertTrue(observation.truncated); self.assertLess(len(observation.hits), len(snapshot.records))
        self.assertTrue(observation.hits)
        self.assertEqual(tuple(hit.boundary.start for hit in observation.hits), (3, 2, 1)[:len(observation.hits)])
        tiny = replace(CONTEXT_ACTION_POLICY, max_snippet_characters=1, max_snippet_utf8_bytes=1,
            max_excerpt_utf8_bytes=1, max_source_read_utf8_bytes=1, max_read_utf8_bytes=1,
            max_observation_utf8_bytes=1,)
        registry = fixture_session_fixture.registry(fixture_session_fixture.SCOPE, self.snapshots); session = fixture_session_fixture.session(self.binding, registry, action_policy=tiny)
        with self.assertRaises(ContextActionError) as caught:
            session.search( ContextSearchAction( fixture_session_fixture.correlation(self.binding, 1),
                    (ContextSearchQuery("x", (ContextSourceKind.WRITING_ARTIFACT,)),), ) )
        self.assertEqual(caught.exception.code, ContextActionErrorCode.OBSERVATION_LIMIT_EXCEEDED)
        self.assertEqual(session.next_action_index, 2)

    def test_immediate_answer_has_fresh_bound_content_free_authorization_and_cleans_session(self, ) -> None:
        references = ( fixture_session_fixture.reference(1, reference_id="whole", digest_character="4"),
            fixture_session_fixture.reference(2, reference_id="range-2-4", digest_character="5"),
            fixture_session_fixture.reference(3, reference_id="range-8-9", digest_character="6"), )
        binding = fixture_session_fixture.binding( self.manifest, reference_operation_id="reference-operation", references=references, )
        registry = fixture_session_fixture.registry(fixture_session_fixture.SCOPE, self.snapshots); session = fixture_session_fixture.session(binding, registry)
        authorization = session.authorize_answer(ContextAnswerAction(fixture_session_fixture.correlation(binding, 1), fixture_session_fixture.PRIVATE_ANSWER))
        self.assertRegex(authorization.authorization_id, r"\Actxa_v1_[0-9a-f]{64}\Z")
        self.assertEqual(authorization.answer_digest, fixture_session_fixture.digest_bytes(fixture_session_fixture.PRIVATE_ANSWER.encode('utf-8')))
        self.assertRegex(authorization.manifest_digest, r"\Asha256:[0-9a-f]{64}\Z")
        self.assertRegex(authorization.completed_read_digest, '\\Asha256:[0-9a-f]{64}\\Z')
        self.assertEqual(authorization.completed_evidence, ()); self.assertEqual(authorization.used_evidence, ())
        self.assertIs(authorization.mandatory_references, references)
        self.assertEqual( tuple(item.ordinal for item in authorization.mandatory_references), (1, 2, 3), )
        self.assertEqual(session.state, ctx_call_offers.ContextInvocationState.ANSWER_AUTHORIZED); registry.capture.assert_not_called()
        rendered = repr(authorization)
        for canary in (fixture_session_fixture.PRIVATE_ANSWER, fixture_session_fixture.PRIVATE_BODY_A, "ctxh_v1_"): self.assertNotIn(canary, rendered)
        alternate = fixture_session_fixture.session(binding, fixture_session_fixture.registry(fixture_session_fixture.SCOPE, self.snapshots))
        different = alternate.authorize_answer(
            ContextAnswerAction(fixture_session_fixture.correlation(binding, 1), fixture_session_fixture.PRIVATE_ANSWER + " changed") )
        self.assertNotEqual(different.authorization_id, authorization.authorization_id)

    def test_read_backed_answer_binds_all_completed_used_subset_and_final_canonical_revalidation( self, ) -> None:
        registry = fixture_session_fixture.registry(fixture_session_fixture.SCOPE, self.snapshots)
        seen: list[str] = []

        def capture( _manifest: ContextManifest, entry: ContextManifestEntry, _policy: owner_capture_values.ContextSourcePolicy,
        ) -> owner_capture_values.ContextSourceSnapshot:
            seen.append(entry.source_id)
            return self.snapshots[entry.source_id]

        registry.capture = mock.Mock(side_effect=capture); session = fixture_session_fixture.session(self.binding, registry)
        pairs = tuple(reversed(fixture_session_fixture.mint_handles(session, registry, self.snapshots)))
        observation, receipt = session.read( ContextReadAction(
                fixture_session_fixture.correlation(self.binding, 2), tuple(handle for _, handle in pairs) ) )
        session.complete_read(receipt)
        used = (pairs[0][1],)
        authorization = session.authorize_answer(
            ContextAnswerAction(fixture_session_fixture.correlation(self.binding, 3), fixture_session_fixture.PRIVATE_ANSWER, used) )
        requested_ids = tuple(entry.source_id for entry, _ in pairs)
        self.assertEqual( tuple(item.source_id for item in authorization.completed_evidence), requested_ids, )
        self.assertEqual(tuple((item.handle for item in authorization.used_evidence)), used)
        canonical_ids = [entry.source_id for entry in self.manifest.entries]
        self.assertEqual(seen, canonical_ids + canonical_ids + canonical_ids)
        self.assertEqual( tuple(item.receipt.source_id for item in observation.evidence), requested_ids, )

    def test_unused_or_substituted_handle_cannot_authorize_and_baseexception_cleans(self, ) -> None:
        registry = fixture_session_fixture.registry(fixture_session_fixture.SCOPE, self.snapshots); session = fixture_session_fixture.session(self.binding, registry)
        unused = fixture_session_fixture.mint_handles(session, registry, self.snapshots)[0][1]
        with self.assertRaises(ContextActionError) as unauthorized:
            session.authorize_answer( ContextAnswerAction( fixture_session_fixture.correlation(self.binding, 2), fixture_session_fixture.PRIVATE_ANSWER, (unused,) ) )
        self.assertEqual(unauthorized.exception.code, ContextActionErrorCode.ANSWER_NOT_AUTHORIZED)
        self.assertEqual(session.state, ctx_call_offers.ContextInvocationState.FAILED)
        for failure in (KeyboardInterrupt("PRIVATE_INTERRUPT"), SystemExit("PRIVATE_EXIT"),):
            with self.subTest(failure=type(failure).__name__):
                registry = fixture_session_fixture.registry(fixture_session_fixture.SCOPE, self.snapshots); registry.search = mock.Mock(side_effect=failure)
                session = fixture_session_fixture.session(self.binding, registry)
                with self.assertRaises(type(failure)):
                    session.search(ContextSearchAction( fixture_session_fixture.correlation(self.binding, 1), ( ContextSearchQuery(
                                    "needle", (ContextSourceKind.WRITING_ARTIFACT,)), ),))
                self.assertEqual(session.state, ctx_call_offers.ContextInvocationState.FAILED)
                self.assertEqual(session.next_action_index, 2)
