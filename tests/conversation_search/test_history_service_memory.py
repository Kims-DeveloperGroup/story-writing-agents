"""In-memory adapter for the existing collaboration history search contract."""

from __future__ import annotations

import unittest

from tests.conversation_search_contract import (
    CollaborationSearchContractMixin,
    MemoryCollaborationEventStore,
    MemoryConversationCatalog,
)


class InMemoryCollaborationSearchContractTests(
    CollaborationSearchContractMixin,
    unittest.TestCase,
):
    def make_adapters(
        self,
    ) -> tuple[MemoryConversationCatalog, MemoryCollaborationEventStore]:
        catalog = MemoryConversationCatalog()
        return catalog, MemoryCollaborationEventStore(catalog)
