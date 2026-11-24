"""Exact incomplete synchronization batch shared by the two journal adapters."""

from dataclasses import replace

from story_writing_agents.conversations import (
    CollaborationEventBatch,
    CollaborationEventKind,
    CollaborationMessagePayload,
    SynchronizationPayload,
    synchronization_event_ref,
)
from story_writing_agents.synchronization_assessment import (
    SynchronizationAssessment,
    SynchronizationBasis,
    SynchronizationCensus,
    SynchronizationDocument,
    SynchronizationFinding,
    SynchronizationJudgment,
)
from tests.collaboration_contract_fixture import DIGEST_A, DIGEST_B, LEAD, RUNTIME, USER, make_batch, make_draft


def synchronization_unread_batch(
    conversation_id: str, *, stale_request: bool = False,
) -> CollaborationEventBatch:
    manuscript = "manuscript/part-01/chapter-03.md"
    target = "core_documents/global_outline.md"
    request = replace(make_draft("sync_request", CollaborationEventKind.MESSAGE, USER, LEAD,
                                 CollaborationMessagePayload("Change the chapter motive.")),
                      conversation_id=conversation_id)
    basis = SynchronizationBasis("sync_task", 1, synchronization_event_ref(request), DIGEST_A, DIGEST_B,
                                 "3" * 64, "4" * 64)
    census = SynchronizationCensus((manuscript,),
                                   (SynchronizationDocument(manuscript, "5" * 64),
                                    SynchronizationDocument(target, "6" * 64)),
                                   False)
    unread = SynchronizationFinding(manuscript, target, SynchronizationJudgment.UNREAD,
                                     "The outline has not been read for this change.")
    assessment = SynchronizationAssessment(basis, census, (unread,))
    event = replace(make_draft("sync_assessment", CollaborationEventKind.SYNCHRONIZATION,
                               RUNTIME, USER, SynchronizationPayload(assessment=assessment)),
                    conversation_id=conversation_id)
    if stale_request:
        request = replace(request, payload=CollaborationMessagePayload("Keep the existing motive."))
    return make_batch((), "sync_unread_batch", request, event)
