"""Literal allowlist coverage for session checkpoint paths."""

from __future__ import annotations

import unittest

from story_writing_agents.git.session_checkpoint_contracts import (
    SessionCheckpointError,
    SessionCheckpointFailureCode,
    SessionCheckpointOwner,
    SessionCheckpointPathFamily,
    classify_session_checkpoint_path,
)


class SessionCheckpointClassifierTests(unittest.TestCase):
    def test_exact_eleven_path_grammars_and_all_debate_owners_are_accepted(
        self,
    ) -> None:
        h = "a" * 64
        cases = (
            (
                "agent_states/orchestrator_state/sessions/novel.json",
                SessionCheckpointOwner.WORKSPACE,
                SessionCheckpointPathFamily.SESSION_OVERRIDE,
            ),
            (
                "agent_states/orchestrator_state/conversation_defaults/novel.json",
                SessionCheckpointOwner.CLI_CONTEXT,
                SessionCheckpointPathFamily.CONVERSATION_DEFAULT,
            ),
            (
                "agent_states/orchestrator_state/provider_sessions/novel.json",
                SessionCheckpointOwner.BACKEND,
                SessionCheckpointPathFamily.PROVIDER_SESSION,
            ),
            (
                f"agent_states/orchestrator_state/provider_sessions/.contextual/v1/novel/{h}.json",
                SessionCheckpointOwner.BACKEND,
                SessionCheckpointPathFamily.CONTEXTUAL_PROVIDER_SESSION,
            ),
            (
                "discussion_logs/novel/debate_1.jsonl",
                SessionCheckpointOwner.WORKSPACE,
                SessionCheckpointPathFamily.DEBATE_LOG,
            ),
            (
                "discussion_logs/novel/debate_1.state.json",
                SessionCheckpointOwner.WORKSPACE,
                SessionCheckpointPathFamily.DEBATE_STATE,
            ),
            (
                "discussion_logs/novel/debate_1.result.json",
                SessionCheckpointOwner.WORKSPACE,
                SessionCheckpointPathFamily.DEBATE_RESULT,
            ),
            (
                "discussion_logs/novel/debate_1.context.json",
                SessionCheckpointOwner.WORKSPACE,
                SessionCheckpointPathFamily.DEBATE_CONTEXT,
            ),
            (
                "discussion_logs/novel/interactive/conversations/chat_1/metadata.json",
                SessionCheckpointOwner.CONVERSATION_CATALOG,
                SessionCheckpointPathFamily.CONVERSATION_METADATA,
            ),
            (
                "discussion_logs/novel/interactive/conversations/chat_1/collaboration-history.jsonl",
                SessionCheckpointOwner.COLLABORATION_HISTORY,
                SessionCheckpointPathFamily.COLLABORATION_HISTORY,
            ),
        )
        debate_template = "agent_states/{owner}/debates/novel/debate_1.jsonl"
        for owner in (
            "orchestrator_state",
            "plot_architect_state",
            "character_custodian_state",
            "execution_specialist_state",
        ):
            cases += (
                (
                    debate_template.format(owner=owner),
                    SessionCheckpointOwner.WORKSPACE,
                    SessionCheckpointPathFamily.AGENT_DEBATE_STATE,
                ),
            )
        for path, owner, family in cases:
            with self.subTest(path=path):
                classified = classify_session_checkpoint_path(path, "novel")
                self.assertIs(classified.owner, owner)
                self.assertIs(classified.family, family)

    def test_excluded_cross_session_private_temporary_and_lexical_paths_are_rejected(
        self,
    ) -> None:
        excluded = (
            "core_documents/global_outline.md",
            "manuscript/part-01/opening.md",
            "config/settings.json",
            "agent_states/orchestrator_state/sessions/other.json",
            "agent_states/orchestrator_state/provider_sessions/.antigravity/novel.json",
            "agent_states/orchestrator_state/provider_sessions/.contextual/v1/novel/"
            + "A" * 64
            + ".json",
            "agent_states/orchestrator_state/provider_sessions/.contextual/v1/novel/"
            + "٠" * 64
            + ".json",
            "agent_states/orchestrator_state/sessions/.gitkeep",
            "agent_states/orchestrator_state/sessions/novel.lock",
            "agent_states/orchestrator_state/transactions/novel.json",
            "debug_logs/novel.jsonl",
            "agent_states/orchestrator_state/cli_context.json",
            "discussion_logs/conversation_storage.sqlite3",
            "discussion_logs/.conversation_storage_fts5.sqlite3",
            "discussion_logs/novel/unknown.future",
            "/discussion_logs/novel/debate.jsonl",
            "discussion_logs/novel/../other/debate.jsonl",
            "discussion_logs\\novel\\debate.jsonl",
            "discussion_logs/novel/debate.jsonl\x00",
        )
        for path in excluded:
            with self.subTest(path=path[:80]):
                with self.assertRaises(SessionCheckpointError) as caught:
                    classify_session_checkpoint_path(path, "novel")
                self.assertIs(
                    caught.exception.code, SessionCheckpointFailureCode.PATH_NOT_ALLOWED
                )


if __name__ == "__main__":
    unittest.main()
