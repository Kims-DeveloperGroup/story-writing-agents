'Existing LedgerBoundaryTests scenarios, moved without new cases.'
from __future__ import annotations
import ast
from importlib.util import resolve_name
import os
import story_writing_agents
from story_writing_agents import agent_context, writing_artifacts
from story_writing_agents.agent_context import ledger as ledger_module
from tests.agent_context.ledger import ledger_fixture as fixture_ledger_fixture


class LedgerBoundaryTests(fixture_ledger_fixture.LedgerTestCase):
    def test_facades_import_direction_compile_and_graph_contract_are_exact(self,
    ) -> None:
        self.assertEqual(len(story_writing_agents.__all__), 106)
        self.assertEqual(len(agent_context.__all__), 17)
        self.assertEqual(len(writing_artifacts.__all__), 80)
        self.assertTrue(
            set(ledger_module.__all__).isdisjoint(story_writing_agents.__all__))
        self.assertTrue(set(ledger_module.__all__).isdisjoint(agent_context.__all__))
        package_path = fixture_ledger_fixture.SOURCE_ROOT / "story_writing_agents/agent_context/ledger"
        trees = {}
        for source_path in sorted(package_path.glob("*.py")):
            source = source_path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=os.fspath(source_path))
            compile(tree, os.fspath(source_path), "exec")
            trees[source_path.stem] = tree
        relative_dependencies = {
            resolve_name("." * node.level + (node.module or ""), "story_writing_agents.agent_context.ledger")
            for tree in trees.values() for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.level}
        ledger_owners = ("record_values", "record_validation", "checkpoint_wire", "accounting_wire",
                         "record_wire", "atomic_files", "checkpoint_ledger")
        internal_dependencies = {"story_writing_agents.agent_context.ledger." + owner for owner in ledger_owners}
        self.assertEqual((set(trees), relative_dependencies - internal_dependencies), (
            {"__init__", *ledger_owners}, {
            "story_writing_agents.agent_context.accounting", "story_writing_agents.agent_context.manifest",
            "story_writing_agents.agent_context.contracts.checkpoints",
            "story_writing_agents.agent_context.contracts.planning_inputs",
            "story_writing_agents.agent_context.contracts.projection",
            "story_writing_agents.agent_context.contracts.projection_accounting",
            "story_writing_agents.runtime_state_paths", "story_writing_agents.workspace"}))
        self.assertFalse([
            node for tree in trees.values() for node in ast.walk(tree) if isinstance(node, ast.Import) and any(
                alias.name.startswith(("requests", "httpx", "subprocess", "socket")) for alias in node.names)
        ])
