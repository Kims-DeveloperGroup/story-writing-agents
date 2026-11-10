'Existing SourceSurfacePolicyAndValueTests scenarios, moved without new cases.'
from __future__ import annotations
import unittest
from dataclasses import FrozenInstanceError, fields, replace
from story_writing_agents.agent_context.manifest import ContextAuthorityKind, ContextProvenance
from story_writing_agents.agent_context import sources as sources_module
from story_writing_agents.agent_context.sources import capture_values as owner_capture_values
from tests.agent_context.sources import value_inputs as fixture_value_inputs


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
                "SessionHistorySourceAdapter",
                "OwnAgentStateSourceAdapter",
                "WritingArtifactSourceAdapter",
            ),
        )
        self.assertEqual(
            tuple(item.value for item in owner_capture_values.ContextSourceErrorCode),
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
                getattr(owner_capture_values.CONTEXT_SOURCE_POLICY, field.name)
                for field in fields(owner_capture_values.ContextSourcePolicy)),
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
        for code in owner_capture_values.ContextSourceErrorCode:
            error = owner_capture_values.ContextSourceError(code)
            self.assertNotIn(fixture_value_inputs.PRIVATE_SOURCE, repr(error))
            self.assertIsNone(error.__cause__)

    def test_each_policy_cap_rejects_n_plus_one_and_custom_relationships(self) -> None:
        values = {
            field.name: getattr(owner_capture_values.CONTEXT_SOURCE_POLICY, field.name)
            for field in fields(owner_capture_values.ContextSourcePolicy)
        }
        for name, value in values.items():
            with self.subTest(name=name):
                with self.assertRaises(owner_capture_values.ContextSourceError):
                    owner_capture_values.ContextSourcePolicy(**{**values, name: value + 1})
        for invalid in (0, -1, True, 1.0, "1"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(owner_capture_values.ContextSourceError):
                    owner_capture_values.ContextSourcePolicy(**{**values, "max_records_per_source": invalid})  # type: ignore[arg-type]
        valid = owner_capture_values.ContextSourcePolicy(
            max_inventory_sources=2,
            max_traversal_entries=4,
            max_records_per_source=3,
            max_source_utf8_bytes=10,
            max_inventory_body_utf8_bytes=20,
            max_source_scan_utf8_bytes=5,
            max_cumulative_scan_utf8_bytes=10,)
        self.assertEqual(valid.max_inventory_sources, 2)
        for override in (
            {"max_source_scan_utf8_bytes": 11},
            {"max_cumulative_scan_utf8_bytes": 4},
            {"max_inventory_body_utf8_bytes": 9},
        ):
            with self.subTest(override=override):
                with self.assertRaises(owner_capture_values.ContextSourceError):
                    replace(valid, **override)

    def test_descriptor_snapshot_records_are_exact_immutable_and_content_hidden(
        self,
    ) -> None:
        snapshot = fixture_value_inputs.make_artifact_snapshot(fixture_value_inputs.make_scope())
        self.assertFalse(hasattr(snapshot, "__dict__"))
        self.assertNotIn("needle", repr(snapshot))
        self.assertNotIn("needle", repr(snapshot.records[0]))
        with self.assertRaises((FrozenInstanceError, TypeError)):
            snapshot.content = b"changed"  # type: ignore[misc]
        with self.assertRaises(owner_capture_values.ContextSourceError) as caught:
            replace(
                snapshot.descriptor,
                provenance=ContextProvenance(
                    ContextAuthorityKind.CONVERSATION_STORAGE,
                    "core_documents/global_outline.md",
                ),
            )
        self.assertEqual(
            caught.exception.code, owner_capture_values.ContextSourceErrorCode.AUTHORITY_MISMATCH)
        with self.assertRaises(owner_capture_values.ContextSourceError):
            replace(snapshot.records[0], content_digest=fixture_value_inputs.DIGEST)
        with self.assertRaises(owner_capture_values.ContextSourceError):
            replace(snapshot, content=b"substituted")
