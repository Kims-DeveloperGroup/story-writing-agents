"""Shared inputs and handler for conversation retrieval wire tests."""
from __future__ import annotations

from story_writing_agents import (
    ConversationRetrievalAction,
    ConversationRetrievalAnswerAction,
    ConversationRetrievalObservation,
    ConversationRetrievalReadAction,
    ConversationRetrievalReadObservation,
    ConversationRetrievalReply,
    ConversationRetrievalRequest,
    ConversationRetrievalSearchAction,
    ConversationRetrievalSearchItem,
    ConversationRetrievalSearchObservation,
    ConversationRetrievalSource,
    PLOT_ARCHITECT,
)
from story_writing_agents.conversation_rag import ConversationRagDocumentIdentity, ConversationRagDocumentKey, ConversationRagOutcome, ConversationRagReason, ConversationRagSourceReference
from story_writing_agents.conversation_retrieval import ConversationRagAttemptOutcome, ConversationRagContext, ConversationRagExcerpt
from story_writing_agents.conversation_search import CollaborationHistoryCitation
from story_writing_agents.conversations import CollaborationReadableFieldKey, select_bounded_collaboration_context


def readable_context():  # type: ignore[no-untyped-def]
    """Return the canonical content-free active-v2 retrieval context."""

    return select_bounded_collaboration_context(())


def retrieval_request(*,
    session_id: str = "story_session",
    conversation_id: str = "conversation_a",
    turn_id: str = "turn_current",
    agent_id: str = PLOT_ARCHITECT, content: str = "visible retrieval request",
    context: object | None = None, rag_context: ConversationRagContext | None = None,
) -> ConversationRetrievalRequest:
    """Build one exact bounded-retrieval provider request."""

    return ConversationRetrievalRequest(session_id=session_id,
        conversation_id=conversation_id,
        turn_id=turn_id,
        agent_id=agent_id,
        content=content, context=readable_context() if context is None else context,
        rag_context=rag_context,)


def provider_rag_context() -> ConversationRagContext:
    """Build one attempted context whose full reference must reach call one."""

    identity = ConversationRagDocumentIdentity(key=ConversationRagDocumentKey(
            session_id="story_session",
            document_id="a" * 64,),
        conversation_id="source_conversation", start_turn_id="source_turn",
        end_turn_id="source_turn", start_sequence=1,
        end_sequence=2,
        revision_id="sha256:" + "b" * 64,)
    content = "authoritative excerpt 🙂"
    excerpt = ConversationRagExcerpt(citation_id="g1",
        source=ConversationRetrievalSource(citation=CollaborationHistoryCitation(
                session_id="story_session", conversation_id="source_conversation",
                event_id="source_event", sequence=2,
                field_key=CollaborationReadableFieldKey.SPECIALIST_REPLY_CONTENT, start_codepoint=0,
                end_codepoint=len(content),),
        ), reference=ConversationRagSourceReference(
            document=identity, fragment_id="fragment-authorized",
            start_codepoint=0,
            end_codepoint=len(content),),
        content=content,)
    return ConversationRagContext(outcome=ConversationRagAttemptOutcome(
            outcome=ConversationRagOutcome.COMPLETE,
            reason=ConversationRagReason.NONE,),
        excerpts=(excerpt,), retained_utf8_bytes=len(content.encode("utf-8")),
        truncated=False,)


class _RecordingRetrievalHandler:
    """Return exact typed observations and a reply while recording actions."""

    def __init__(self) -> None:
        self.actions: list[ConversationRetrievalAction] = []
        self.outcomes: list[ConversationRetrievalObservation | ConversationRetrievalReply
        ] = []
        self.source: ConversationRetrievalSource | None = None

    def handle_conversation_retrieval_action(
        self, action: ConversationRetrievalAction,
    ) -> ConversationRetrievalObservation | ConversationRetrievalReply:
        """Return the corresponding exact outcome for one decoded action."""

        self.actions.append(action)
        if type(action) is ConversationRetrievalSearchAction:
            self.source = ConversationRetrievalSource(citation=CollaborationHistoryCitation(
                    session_id=action.session_id, conversation_id="source_conversation",
                    event_id="source_event", sequence=1,
                    field_key=CollaborationReadableFieldKey.PROPOSAL_PURPOSE, start_codepoint=0,
                    end_codepoint=len("Needle source"),),
            )
            outcome: ConversationRetrievalObservation | ConversationRetrievalReply = (
                ConversationRetrievalSearchObservation(session_id=action.session_id,
                    conversation_id=action.conversation_id,
                    turn_id=action.turn_id,
                    action_index=action.action_index, hits=(
                        ConversationRetrievalSearchItem(citation_id=f"s{action.action_index}h1",
                            source=self.source,
                            snippet='Needle "source" 한🙂',),
                    ), truncated=False,
                )
            )
        elif type(action) is ConversationRetrievalReadAction:
            assert self.source is not None
            outcome = ConversationRetrievalReadObservation(session_id=action.session_id,
                conversation_id=action.conversation_id,
                turn_id=action.turn_id, action_index=action.action_index,
                source_citation_id=action.citation_id, source=self.source,
                records=(),
                truncated=False,)
        else:
            assert type(action) is ConversationRetrievalAnswerAction
            outcome = ConversationRetrievalReply(session_id=action.session_id,
                conversation_id=action.conversation_id,
                turn_id=action.turn_id, content=action.content,
                sources=() if self.source is None else (self.source,),)
        self.outcomes.append(outcome)
        return outcome
