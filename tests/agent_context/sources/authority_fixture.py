'Owned authority_fixture.py declarations; mutable objects remain fresh per original call.'
from __future__ import annotations
import unittest
from story_writing_agents import Workspace
from story_writing_agents.agent_context.manifest import ContextScope
from story_writing_agents.agents import EXECUTION_SPECIALIST
from story_writing_agents.conversation_storage import compose_filesystem_conversation_storage
from story_writing_agents.writing_artifacts.repository import WritingArtifactRepository
from tests.helpers import pristine_initialized_workspace
from story_writing_agents.agent_context.sources import active_debate as owner_active_debate
from story_writing_agents.agent_context.sources import focused_conversation as owner_focused_conversation
from story_writing_agents.agent_context.sources import own_agent_state as owner_own_agent_state
from story_writing_agents.agent_context.sources import registry as owner_registry
from story_writing_agents.agent_context.sources import writing_artifact as owner_writing_artifact


class SourceAuthorityFixture(unittest.TestCase):
    workspace: Workspace
    repository: WritingArtifactRepository

    def setUp(self) -> None:
        self.workspace = pristine_initialized_workspace(self)
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
            candidate_author_agent_id=EXECUTION_SPECIALIST,)

    def registry(
        self,
        scope: ContextScope,
        *,
        conversation_id: str | None = None,
        debate_id: str | None = None,
    ) -> owner_registry.ContextSourceRegistry:
        services = compose_filesystem_conversation_storage(self.workspace)
        return owner_registry.ContextSourceRegistry(
            owner_writing_artifact.WritingArtifactSourceAdapter(self.repository, scope),
            owner_focused_conversation.FocusedConversationSourceAdapter(
                services.catalog,
                services.event_store,
                scope,
                conversation_id,),
            owner_active_debate.ActiveDebateSourceAdapter(self.workspace, scope, debate_id),
            owner_own_agent_state.OwnAgentStateSourceAdapter(self.workspace, scope),
        )
