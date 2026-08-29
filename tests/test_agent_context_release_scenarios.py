"""Deterministic release scenarios for bounded agent-directed context."""

from __future__ import annotations

import hashlib
import sys
import unittest
from unittest import mock

from story_writing_agents import (
    AgentBackend,
    DebateContextMode,
    DebateContextRequest,
    DebateStatus,
    EXECUTION_SPECIALIST,
    PLOT_ARCHITECT,
    ROSTER,
    StoryRuntime,
)
from story_writing_agents.conversation_storage import (
    ConversationStorageServices,
    compose_filesystem_conversation_storage,
)
from story_writing_agents.conversations import (
    ConversationEventAppend,
    ConversationEventKind,
)
from story_writing_agents.errors import ManuscriptExistsError
from story_writing_agents.writing_artifacts import (
    ReferenceInvocationState,
    ReferenceOperationState,
)
from story_writing_agents.writing_artifacts.repository import (
    WritingArtifactRepository,
)

from tests.helpers import core_snapshot, initialized_workspace
from tests.test_agent_context_runtime_adoption import _ContextAdapterHarness


_PART_PREFIX = "manuscript/part_01/"


class AgentContextReleaseScenarioTests(unittest.IsolatedAsyncioTestCase):
    """Exercise the canonical modification and reference examples end to end."""

    def setUp(self) -> None:
        self.workspace = initialized_workspace(self)
        self.services: ConversationStorageServices = (
            compose_filesystem_conversation_storage(self.workspace)
        )
        self.backend = AgentBackend(self.workspace, executable=sys.executable)
        self.addCleanup(self.backend.close)
        self.provider = _ContextAdapterHarness()
        adapter_patch = mock.patch.object(
            self.backend._codex_adapter,
            "invoke",
            side_effect=self.provider.invoke,
        )
        adapter_patch.start()
        self.addCleanup(adapter_patch.stop)
        self.runtime = StoryRuntime(
            self.workspace,
            self.backend,
            conversation_storage_services=self.services,
        )
        self.repository = WritingArtifactRepository(self.workspace.root)

    def ready(self, session_id: str) -> None:
        self.backend.create_session_with_access(session_id)

    def conversation(self, session_id: str, subject: str) -> str:
        return self.services.catalog.create(session_id, subject).conversation_id

    def append_turn(
        self,
        session_id: str,
        conversation_id: str,
        turn_id: str,
        user_text: str,
        reply_text: str,
    ) -> None:
        for sender, recipient, kind, content in (
            (
                "user",
                PLOT_ARCHITECT,
                ConversationEventKind.USER_MESSAGE,
                user_text,
            ),
            (
                PLOT_ARCHITECT,
                "user",
                ConversationEventKind.AGENT_REPLY,
                reply_text,
            ),
        ):
            self.services.event_store.append(
                ConversationEventAppend(
                    session_id,
                    conversation_id,
                    turn_id,
                    sender,
                    recipient,
                    kind,
                    content,
                )
            )

    def seed_manuscript(
        self,
        output_path: str,
        content: str,
        *,
        identity: str,
    ) -> str:
        path, _updates = self.repository.commit_consensus(
            output_path=output_path,
            candidate=content,
            core_updates={},
            session_id=f"fixture_{identity}",
            debate_id=f"fixture_{identity}",
            revision_id="r0",
            candidate_author_agent_id=EXECUTION_SPECIALIST,
        )
        return path

    @staticmethod
    def evidence_locators(frame: dict[str, object]) -> tuple[str, ...]:
        evidence = frame["evidence"]
        assert isinstance(evidence, list)
        return tuple(item["locator"] for item in evidence)

    @staticmethod
    def hit_locators(frame: dict[str, object]) -> tuple[str, ...]:
        hits = frame["hits"]
        assert isinstance(hits, list)
        return tuple(item["locator"] for item in hits)

    async def test_missing_knowledge_outline_reads_both_sources_and_keeps_motive(
        self,
    ) -> None:
        session_id = "release_outline_session"
        debate_id = "release_outline_debate"
        needle = "MARA_KEY_REFUSAL_RELEASE_NEEDLE"
        motive = "accepting the key would identify Sera as Mara's accomplice"
        self.ready(session_id)
        conversation_id = self.conversation(session_id, "Earlier key decision")
        self.append_turn(
            session_id,
            conversation_id,
            "earlier_key_turn",
            "Why did Mara refuse the key?",
            f"{needle}: {motive}.",
        )
        manuscript_path = self.seed_manuscript(
            "part_01/chapter_03.md",
            f"{needle}: Mara closes her hand and protects Sera.\n",
            identity="outline_chapter",
        )
        old_outline = "The ending motive is unresolved.\n"
        replacement = (
            "Mara leaves the key in public view because "
            f"{motive}.\n"
        )
        self.workspace.path("core_documents/global_outline.md").write_text(
            old_outline,
            encoding="utf-8",
        )
        self.provider.active_debate_id = debate_id
        self.provider.candidates[debate_id] = replacement
        self.provider.final_updates[debate_id] = {
            "global_outline.md": replacement
        }
        self.provider.search_selector = lambda current: (
            (("writing_artifact", "conversation"), needle)
            if current.get("workflow") == "debate"
            and current["identity"]["debate_id"] == debate_id
            and current["phase"] == "draft"
            else None
        )

        result = await self.runtime.run_prompt(
            session_id,
            (
                "Why did Mara refuse the key earlier, and revise the outline "
                "so the ending preserves that reason?"
            ),
            debate_id=debate_id,
            output_path="part_01/outline_revision.md",
            conversation_context=DebateContextRequest(
                conversation_id=conversation_id,
                mode=DebateContextMode.INCLUDED,
            ),
        )

        self.assertIs(result.status, DebateStatus.CONSENSUS)
        self.assertEqual(result.core_updates, ("global_outline.md",))
        self.assertEqual(
            self.workspace.read_text("core_documents/global_outline.md"),
            replacement,
        )
        self.assertEqual(
            self.workspace.read_text("manuscript/part_01/outline_revision.md"),
            replacement,
        )
        self.assertIn(motive, result.final_candidate or "")
        self.assertEqual(len(self.provider.search_frames), 1)
        self.assertEqual(len(self.provider.read_frames), 1)
        search_locators = set(self.hit_locators(self.provider.search_frames[0]))
        read_locators = set(self.evidence_locators(self.provider.read_frames[0]))
        self.assertEqual(
            search_locators,
            {manuscript_path, f"conversation/{conversation_id}"},
        )
        self.assertEqual(read_locators, search_locators)
        used_handles = tuple(self.provider.answers[0]["u"])
        read_handles = tuple(
            item["handle"] for item in self.provider.read_frames[0]["evidence"]
        )
        self.assertEqual(used_handles, read_handles)
        self.assertEqual(len(used_handles), 2)

    async def test_character_sheet_replacement_retains_named_hard_constraint(
        self,
    ) -> None:
        session_id = "release_character_session"
        debate_id = "release_character_debate"
        needle = "SERA_OATH_RING_RELEASE_NEEDLE"
        constraint = "Sera cannot lie while wearing the oath ring"
        self.ready(session_id)
        self.workspace.path("core_documents/character_sheets.md").write_text(
            f"{needle}: {constraint}.\n",
            encoding="utf-8",
        )
        before = core_snapshot(self.workspace)
        replacement = (
            "Sera now trusts Mara, but the hard constraint remains: "
            f"{constraint}.\n"
        )
        self.provider.active_debate_id = debate_id
        self.provider.candidates[debate_id] = replacement
        self.provider.final_updates[debate_id] = {
            "character_sheets.md": replacement
        }
        self.provider.search_selector = lambda current: (
            (("writing_artifact",), needle)
            if current.get("workflow") == "debate"
            and current["identity"]["debate_id"] == debate_id
            and current["phase"] == "draft"
            else None
        )

        result = await self.runtime.run_prompt(
            session_id,
            "Revise Sera's character sheet without weakening her oath constraint.",
            debate_id=debate_id,
            output_path="part_01/character_revision.md",
        )

        self.assertIs(result.status, DebateStatus.CONSENSUS)
        self.assertEqual(result.core_updates, ("character_sheets.md",))
        after = core_snapshot(self.workspace)
        self.assertEqual(after["character_sheets.md"], replacement)
        self.assertIn(constraint, after["character_sheets.md"])
        self.assertEqual(
            {
                name: value
                for name, value in after.items()
                if name != "character_sheets.md"
            },
            {
                name: value
                for name, value in before.items()
                if name != "character_sheets.md"
            },
        )
        self.assertEqual(len(self.provider.read_frames), 1)
        self.assertEqual(
            self.evidence_locators(self.provider.read_frames[0]),
            ("core_documents/character_sheets.md",),
        )
        self.assertEqual(
            tuple(self.provider.answers[0]["u"]),
            tuple(
                item["handle"]
                for item in self.provider.read_frames[0]["evidence"]
            ),
        )

    async def test_part_prefix_reads_two_chapters_and_commits_one_revision(
        self,
    ) -> None:
        session_id = "release_narrative_session"
        debate_id = "release_narrative_debate"
        needle = "MEMORY_OBJECTIVE_FACT_RELEASE_NEEDLE"
        self.ready(session_id)
        part_one_sources = {
            "manuscript/part_01/chapter_01.md": (
                f"{needle}: Mara remembered the blue door as certain.\n"
            ),
            "manuscript/part_01/scenes/chapter_02.md": (
                f"{needle}: Sera knew the bell had rung in the memory.\n"
            ),
        }
        for index, (path, content) in enumerate(part_one_sources.items(), start=1):
            created = self.seed_manuscript(
                path.removeprefix("manuscript/"),
                content,
                identity=f"narrative_part_one_{index}",
            )
            self.assertEqual(created, path)
        part_two_path = self.seed_manuscript(
            "part_02/chapter_07.md",
            " ".join([needle] * 12) + " stronger out-of-prefix decoy\n",
            identity="narrative_part_two_decoy",
        )
        internal_path = self.workspace.path(
            "discussion_logs/internal_narrative_decoy.md"
        )
        internal_path.parent.mkdir(parents=True, exist_ok=True)
        internal_path.write_text(
            " ".join([needle] * 20) + " internal decoy\n",
            encoding="utf-8",
        )
        old_rule = "Memories may be narrated as objective fact.\n"
        new_rule = "Memories must always be framed as subjective recollection.\n"
        revised_section_one = (
            "Chapter 01 revision: Mara seems to remember the blue door."
        )
        revised_section_two = (
            "Chapter 02 revision: Sera believes she heard the bell."
        )
        combined_revision = f"{revised_section_one}\n\n{revised_section_two}\n"
        self.workspace.path("core_documents/narrative_rules.md").write_text(
            old_rule,
            encoding="utf-8",
        )
        source_bytes = {
            path: self.workspace.path(path).read_bytes()
            for path in (*part_one_sources, part_two_path)
        }
        before_manuscripts = {
            path
            for path in self.repository.list_paths()
            if path.startswith("manuscript/")
        }
        self.provider.active_debate_id = debate_id
        self.provider.candidates[debate_id] = combined_revision
        self.provider.final_updates[debate_id] = {
            "narrative_rules.md": new_rule
        }
        self.provider.search_selector = lambda current: (
            (_PART_PREFIX, ("writing_artifact",), needle)
            if current.get("workflow") == "debate"
            and current["identity"]["debate_id"] == debate_id
            and current["phase"] == "draft"
            else None
        )
        output_path = "manuscript/part_01/revised_memory_sections.md"

        result = await self.runtime.run_prompt(
            session_id,
            (
                "Change the narrative rule so memories are never shown as "
                "objective fact, then update affected chapters in Part 1."
            ),
            debate_id=debate_id,
            output_path=output_path.removeprefix("manuscript/"),
        )

        self.assertIs(result.status, DebateStatus.CONSENSUS)
        self.assertEqual(result.core_updates, ("narrative_rules.md",))
        self.assertEqual(result.manuscript_path, output_path)
        self.assertEqual(self.workspace.read_text(output_path), combined_revision)
        self.assertIn(revised_section_one, result.final_candidate or "")
        self.assertIn(revised_section_two, result.final_candidate or "")
        self.assertEqual(
            self.workspace.read_text("core_documents/narrative_rules.md"),
            new_rule,
        )
        for path, expected in source_bytes.items():
            self.assertEqual(self.workspace.path(path).read_bytes(), expected)
        after_manuscripts = {
            path
            for path in self.repository.list_paths()
            if path.startswith("manuscript/")
        }
        self.assertEqual(after_manuscripts - before_manuscripts, {output_path})
        self.assertEqual(len(self.provider.search_frames), 1)
        self.assertEqual(len(self.provider.read_frames), 1)
        expected_sources = set(part_one_sources)
        self.assertEqual(
            set(self.hit_locators(self.provider.search_frames[0])),
            expected_sources,
        )
        self.assertEqual(
            set(self.evidence_locators(self.provider.read_frames[0])),
            expected_sources,
        )
        self.assertNotIn(
            part_two_path,
            self.hit_locators(self.provider.search_frames[0]),
        )
        self.assertNotIn(
            str(internal_path.relative_to(self.workspace.root)),
            self.hit_locators(self.provider.search_frames[0]),
        )
        self.assertEqual(
            tuple(self.provider.answers[0]["u"]),
            tuple(
                item["handle"]
                for item in self.provider.read_frames[0]["evidence"]
            ),
        )
        catalog = self.repository.catalog()
        self.assertEqual(
            catalog.entries[output_path].commit_id,
            catalog.entries["core_documents/narrative_rules.md"].commit_id,
        )
        catalog_before_rejection = catalog
        with self.assertRaises(ManuscriptExistsError):
            self.repository.commit_consensus(
                output_path=output_path.removeprefix("manuscript/"),
                candidate="must not overwrite",
                core_updates={},
                session_id="rejected_narrative_overwrite",
                debate_id="rejected_narrative_overwrite",
                revision_id="r0",
                candidate_author_agent_id=EXECUTION_SPECIALIST,
            )
        self.assertEqual(self.workspace.read_text(output_path), combined_revision)
        self.assertEqual(self.repository.catalog(), catalog_before_rejection)

    async def test_create_only_revision_reads_prior_and_preserves_original_bytes(
        self,
    ) -> None:
        session_id = "release_manuscript_session"
        debate_id = "release_manuscript_debate"
        needle = "CREATE_ONLY_RELEASE_NEEDLE"
        self.ready(session_id)
        original = f"{needle}\r\nMara leaves the key behind.\r\n"
        original_path = self.seed_manuscript(
            "part_01/original_scene.md",
            original,
            identity="create_only_original",
        )
        original_bytes = self.workspace.path(original_path).read_bytes()
        revised = "Mara leaves the key behind in a quieter revised scene.\n"
        revised_path = "manuscript/part_01/revised_scene.md"
        self.provider.active_debate_id = debate_id
        self.provider.candidates[debate_id] = revised
        self.provider.search_selector = lambda current: (
            (("writing_artifact",), needle)
            if current.get("workflow") == "debate"
            and current["identity"]["debate_id"] == debate_id
            and current["phase"] == "draft"
            else None
        )

        result = await self.runtime.run_prompt(
            session_id,
            "Revise the prior scene as a distinct create-only artifact.",
            debate_id=debate_id,
            output_path=revised_path.removeprefix("manuscript/"),
        )

        self.assertIs(result.status, DebateStatus.CONSENSUS)
        self.assertEqual(result.manuscript_path, revised_path)
        self.assertEqual(
            self.workspace.path(original_path).read_bytes(),
            original_bytes,
        )
        self.assertEqual(self.workspace.read_text(revised_path), revised)
        self.assertEqual(
            self.evidence_locators(self.provider.read_frames[0]),
            (original_path,),
        )
        self.assertEqual(
            tuple(self.provider.answers[0]["u"]),
            tuple(
                item["handle"]
                for item in self.provider.read_frames[0]["evidence"]
            ),
        )
        with self.assertRaises(ManuscriptExistsError):
            self.repository.commit_consensus(
                output_path=revised_path.removeprefix("manuscript/"),
                candidate="overwrite attempt",
                core_updates={},
                session_id="rejected_create_only_overwrite",
                debate_id="rejected_create_only_overwrite",
                revision_id="r0",
                candidate_author_agent_id=EXECUTION_SPECIALIST,
            )
        self.assertEqual(
            self.workspace.path(original_path).read_bytes(),
            original_bytes,
        )
        self.assertEqual(self.workspace.read_text(revised_path), revised)

    async def test_three_references_are_exact_ordered_for_every_debate_invocation(
        self,
    ) -> None:
        session_id = "release_reference_session"
        debate_id = "release_reference_debate"
        self.ready(session_id)
        outline_path = "core_documents/global_outline.md"
        part_one_path = "manuscript/part_01/chapter_03.md"
        part_two_path = "manuscript/part_02/chapter_07.md"
        outline = "Opening\nMidpoint reveal\nQuiet ending\n"
        part_one = "".join(
            f"part one line {index:03d}\n" for index in range(1, 171)
        )
        part_two = "".join(
            f"part two line {index:03d}\n" for index in range(1, 91)
        )
        self.workspace.path(outline_path).write_text(outline, encoding="utf-8")
        self.assertEqual(
            self.seed_manuscript(
                part_one_path.removeprefix("manuscript/"),
                part_one,
                identity="reference_part_one",
            ),
            part_one_path,
        )
        self.assertEqual(
            self.seed_manuscript(
                part_two_path.removeprefix("manuscript/"),
                part_two,
                identity="reference_part_two",
            ),
            part_two_path,
        )
        selected_part_one = "".join(part_one.splitlines(keepends=True)[119:160])
        selected_part_two = "".join(part_two.splitlines(keepends=True)[43:82])
        prompt = (
            f"Review ${{{outline_path}}} against "
            f"${{{part_one_path}}}#L120-L160 and "
            f"${{{part_two_path}}}#L44-L82."
        )
        expected_references = [
            {"path": outline_path, "line_range": None},
            {
                "path": part_one_path,
                "line_range": {"start": 120, "end": 160},
            },
            {
                "path": part_two_path,
                "line_range": {"start": 44, "end": 82},
            },
        ]

        result = await self.runtime.run_prompt(
            session_id,
            prompt,
            debate_id=debate_id,
            output_path="part_01/reference_review.md",
        )

        self.assertIs(result.status, DebateStatus.CONSENSUS)
        self.assertIsNotNone(result.reference_operation_id)
        logical_calls = [
            payload
            for payload in self.provider.context_payloads
            if payload["q"][0]
            in {"draft", "audit", "propose_revision", "synthesize", "finalize"}
        ]
        self.assertGreater(len(logical_calls), len(ROSTER))
        self.assertEqual(len(self.provider.reference_payloads), len(logical_calls))
        for payload in self.provider.reference_payloads:
            with self.subTest(correlation=payload["correlation"]):
                self.assertEqual(payload["references"], expected_references)
                self.assertTrue(
                    all(
                        set(reference) == {"line_range", "path"}
                        for reference in payload["references"]
                    )
                )
                self.assertNotIn("content", payload)
        expected_contents = [outline, selected_part_one, selected_part_two]
        for payload in logical_calls:
            attached = payload["untrusted_writing_artifact_context"]
            self.assertEqual(
                [item["reference"] for item in attached],
                expected_references,
            )
            self.assertEqual(
                [item["content"] for item in attached],
                expected_contents,
            )

        history = self.runtime.inspect_reference_operation(
            result.reference_operation_id
        )
        self.assertIsNotNone(history)
        assert history is not None
        self.assertEqual(
            tuple(event.operation_state for event in history.operation_events),
            (
                ReferenceOperationState.REQUESTED,
                ReferenceOperationState.CAPTURED,
                ReferenceOperationState.ACCEPTED,
            ),
        )
        self.assertEqual(
            tuple(reference.path for reference in history.manifest.references),
            (outline_path, part_one_path, part_two_path),
        )
        proofs = history.operation_events[1].references
        self.assertEqual(tuple(proof.ordinal for proof in proofs), (1, 2, 3))
        self.assertEqual(
            tuple(proof.selected_utf8_bytes for proof in proofs),
            tuple(len(content.encode("utf-8")) for content in expected_contents),
        )
        self.assertEqual(
            tuple(proof.selected_sha256 for proof in proofs),
            tuple(
                hashlib.sha256(content.encode("utf-8")).hexdigest()
                for content in expected_contents
            ),
        )
        self.assertEqual(len(history.invocation_events), len(logical_calls))
        self.assertTrue(
            all(
                events[-1].invocation_state is ReferenceInvocationState.ACCEPTED
                and events[-1].references == proofs
                for events in history.invocation_events
            )
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
