"""Filesystem adapter for the existing collaboration history search contract."""

from __future__ import annotations

import unittest

from story_writing_agents.collaboration_history_search import ReferenceCollaborationHistorySearch
from story_writing_agents.conversation_search import CollaborationHistorySearch
from story_writing_agents.conversation_storage import compose_filesystem_conversation_storage
from story_writing_agents.conversation_storage.filesystem import (
    FilesystemCollaborationEventStore,
    FilesystemConversationCatalog,
)
from tests.conversation_search_contract import CollaborationSearchContractMixin
from tests.helpers import initialized_workspace


class FilesystemCollaborationSearchContractTests(
    CollaborationSearchContractMixin,
    unittest.TestCase,
):
    def make_adapters(
        self,
    ) -> tuple[FilesystemConversationCatalog, FilesystemCollaborationEventStore]:
        self.workspace = initialized_workspace(self)
        self.services = compose_filesystem_conversation_storage(self.workspace)
        self.assertIsInstance(self.services.catalog, FilesystemConversationCatalog)
        self.assertIsInstance(
            self.services.event_store,
            FilesystemCollaborationEventStore,
        )
        return self.services.catalog, self.services.event_store

    def make_search(
        self,
        catalog: object,
        store: object,
    ) -> CollaborationHistorySearch:
        del catalog, store
        self.assertIsInstance(
            self.services.search,
            ReferenceCollaborationHistorySearch,
        )
        return self.services.search
