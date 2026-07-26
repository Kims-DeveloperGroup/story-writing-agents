"""Prompt assembly, least-context hydration, and payload separation tests."""

from __future__ import annotations

import unittest

from story_writing_agents import (
    AGENT_SPECS,
    DebateMessage,
    PLOT_ARCHITECT,
    Phase,
    ROSTER,
)
from story_writing_agents.prompts import PromptAssembler

from tests.helpers import initialized_workspace


class PromptAssemblerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = initialized_workspace(self)
        self.persona_path = (
            "config/persona_templates/alternative_templates/prompt_persona.md"
        )
        self.workspace.write_text(self.persona_path, "PERSONA_VERSION_1")
        configs = {
            agent_id: {"language": None, "persona": None}
            for agent_id in ROSTER
        }
        configs[PLOT_ARCHITECT] = {
            "language": "EN",
            "persona": "prompt_persona",
        }
        self.workspace.write_json("config/agent_configs.json", configs)
        for filename in (
            "global_outline.md",
            "part_outlines.md",
            "character_sheets.md",
            "narrative_rules.md",
        ):
            self.workspace.write_text(
                f"core_documents/{filename}",
                f"CONTEXT_TOKEN::{filename}",
            )
        self.assembler = PromptAssembler(self.workspace)

    def test_exact_block_order_resolved_values_and_payload_separation(self) -> None:
        visible_message = DebateMessage(
            sender="plot_architect",
            phase="audit",
            round=0,
            revision="r0",
            kind="approval",
            content="MESSAGE_PAYLOAD_TOKEN",
            timestamp="2026-01-01T00:00:00+00:00",
        )
        request = self.assembler.build_request(
            session_id="prompt_session",
            debate_id="prompt_debate",
            agent_id=PLOT_ARCHITECT,
            phase=Phase.AUDIT,
            round_number=0,
            revision_id="r0",
            task="Audit the candidate.",
            user_payload="USER_PAYLOAD_TOKEN",
            candidate_payload="CANDIDATE_PAYLOAD_TOKEN",
            messages=(visible_message,),
        )

        expected_context = (
            "## global_outline.md\nCONTEXT_TOKEN::global_outline.md\n\n"
            "## part_outlines.md\nCONTEXT_TOKEN::part_outlines.md"
        )
        expected_prompt = "\n\n".join(
            (
                "[Persona]\nPERSONA_VERSION_1",
                f"[Domain Rules]\n{AGENT_SPECS[PLOT_ARCHITECT].domain_rules}",
                "[Language Directive]\nUse EN for all visible responses.",
                f"[Context Documents]\n{expected_context}",
            )
        )
        self.assertEqual(request.system_prompt, expected_prompt)
        self.assertEqual(request.user_payload, "USER_PAYLOAD_TOKEN")
        self.assertEqual(request.candidate_payload, "CANDIDATE_PAYLOAD_TOKEN")
        self.assertEqual(request.messages, (visible_message,))
        self.assertTrue(request.task.startswith("Audit the candidate."))
        self.assertIn("Do not reveal", request.task)

        for payload_token in (
            "USER_PAYLOAD_TOKEN",
            "CANDIDATE_PAYLOAD_TOKEN",
            "MESSAGE_PAYLOAD_TOKEN",
        ):
            with self.subTest(payload_token=payload_token):
                self.assertNotIn(payload_token, request.system_prompt)

    def test_each_agent_receives_only_its_allowlisted_context(self) -> None:
        filenames = (
            "global_outline.md",
            "part_outlines.md",
            "character_sheets.md",
            "narrative_rules.md",
        )
        for agent_id in ROSTER:
            request = self.assembler.build_request(
                session_id="context_session",
                debate_id=f"context_{agent_id}",
                agent_id=agent_id,
                phase=Phase.AUDIT,
                round_number=0,
                revision_id="r0",
                task="Inspect context.",
                user_payload="neutral-input",
                candidate_payload="neutral-candidate",
            )
            expected = set(AGENT_SPECS[agent_id].context_documents)
            for filename in filenames:
                with self.subTest(agent_id=agent_id, filename=filename):
                    marker = f"CONTEXT_TOKEN::{filename}"
                    self.assertEqual(marker in request.system_prompt, filename in expected)

    def test_config_persona_and_context_are_rehydrated_from_disk_each_time(
        self,
    ) -> None:
        first = self.assembler.build_request(
            session_id="rehydration_session",
            debate_id="rehydration_debate",
            agent_id=PLOT_ARCHITECT,
            phase=Phase.AUDIT,
            round_number=0,
            revision_id="r0",
            task="First read.",
            user_payload="input",
            candidate_payload="candidate",
        )
        self.assertIn("PERSONA_VERSION_1", first.system_prompt)
        self.assertIn("CONTEXT_TOKEN::global_outline.md", first.system_prompt)
        self.assertIn("Use EN", first.system_prompt)

        self.workspace.write_text(self.persona_path, "PERSONA_VERSION_2")
        self.workspace.write_text(
            "core_documents/global_outline.md",
            "CONTEXT_TOKEN::global_outline-v2",
        )
        self.workspace.save_session_overrides(
            "rehydration_session",
            {"language": "FR", "persona": None, "agents": {}},
        )
        second = self.assembler.build_request(
            session_id="rehydration_session",
            debate_id="rehydration_debate",
            agent_id=PLOT_ARCHITECT,
            phase=Phase.AUDIT,
            round_number=1,
            revision_id="r1",
            task="Second read.",
            user_payload="input",
            candidate_payload="candidate",
        )

        self.assertIn("PERSONA_VERSION_2", second.system_prompt)
        self.assertNotIn("PERSONA_VERSION_1", second.system_prompt)
        self.assertIn("CONTEXT_TOKEN::global_outline-v2", second.system_prompt)
        self.assertNotIn("CONTEXT_TOKEN::global_outline.md", second.system_prompt)
        self.assertIn("Use FR", second.system_prompt)


if __name__ == "__main__":
    unittest.main()
