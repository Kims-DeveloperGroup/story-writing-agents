from __future__ import annotations
import ast
import inspect
import os
import subprocess
import sys
import tempfile
import story_writing_agents
from pathlib import Path
from story_writing_agents import agent_context, writing_artifacts
from story_writing_agents.agent_context import manifest as manifest_module, planning as planning_module
from unittest import mock
from story_writing_agents.agent_context.contracts import checkpoints as ctx_checkpoints
from story_writing_agents.agent_context.contracts import planning_inputs as ctx_planning_inputs
from story_writing_agents.agent_context.contracts import projection as ctx_projection
from story_writing_agents.agent_context.contracts import projection_accounting as ctx_projection_accounting
from story_writing_agents.agent_context import planning as planning_module
from tests.agent_context.planning import fixtures as planning_fixtures


class PlanningBoundaryAndPurityTests(planning_fixtures.PlanningTestCase):
    """Verify leaf imports, unchanged facades, clean import, and no I/O."""

    def test_planning_imports_only_stdlib_and_supported_manifest_names(self) -> None:
        root = Path(__file__).resolve().parents[3]
        planning_path = (root / "src" / "story_writing_agents" / "agent_context" / "planning.py"
        )
        manifest_path = (root / "src" / "story_writing_agents" / "agent_context" / "manifest.py"
        )
        source = planning_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(planning_path))
        compile(source, str(planning_path), "exec")
        relative_imports = [node
            for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.level
        ]
        allowed_owners = {
            'contracts.checkpoints': ctx_checkpoints,
            'contracts.planning_inputs': ctx_planning_inputs,
            'contracts.projection': ctx_projection,
            'contracts.projection_accounting': ctx_projection_accounting,
            'manifest': manifest_module,
        }
        self.assertEqual(len(relative_imports), len(allowed_owners))
        self.assertEqual({node.module for node in relative_imports}, set(allowed_owners))
        self.assertTrue(all(alias.name in allowed_owners[node.module].__all__
                            for node in relative_imports for alias in node.names))
        absolute_imports = {node.module
            for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
            and not node.level and node.module != "__future__"
        }
        absolute_imports.update(alias.name
            for node in ast.walk(tree) if isinstance(node, ast.Import)
            for alias in node.names)
        self.assertEqual(absolute_imports, {"hashlib", "dataclasses", "enum"})
        compile(manifest_path.read_text(encoding="utf-8"), str(manifest_path), "exec")

    def test_facades_remain_exact_and_clean_direct_import_works(self) -> None:
        self.assertEqual(len(story_writing_agents.__all__), 106)
        self.assertEqual(len(agent_context.__all__), 17)
        self.assertEqual(len(writing_artifacts.__all__), 80)
        self.assertFalse(hasattr(agent_context, "ContextManifest"))
        self.assertFalse(hasattr(agent_context, "ContextPlanningPolicy"))
        self.assertFalse(hasattr(story_writing_agents, "ContextManifest"))
        self.assertEqual(tuple(inspect.signature(planning_module.plan_context_projection).parameters),
            ("manifest",
                "materials", "policy",
                "conditions", "checkpoint",
                "active_provider_thread_id", "mandatory_references",
                "fresh",
            ),
        )
        source_root = Path(__file__).resolve().parents[3] / "src"
        environment = dict(os.environ,
            PYTHONPATH=str(source_root), PYTHONDONTWRITEBYTECODE="1",
            PYTHONWARNINGS="error",)
        script = ('import story_writing_agents.agent_context.manifest as manifest; import story_writing_agents.agent_context.planning as planning; assert len(manifest.__all__) == 16; assert len(planning.__all__) == 1'
        )
        with tempfile.TemporaryDirectory() as directory:
            completed = subprocess.run([sys.executable, "-B", "-c", script],
                cwd=directory, env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_planning_performs_no_filesystem_network_process_clock_or_state_io(self,
    ) -> None:
        entry, material = planning_fixtures.source("focused", "문맥🙂".encode("utf-8"))
        forbidden = AssertionError("pure planner attempted external access")
        with (mock.patch("builtins.open", side_effect=forbidden),
            mock.patch("subprocess.Popen", side_effect=forbidden),
            mock.patch("socket.socket", side_effect=forbidden),
            mock.patch("time.time", side_effect=forbidden),
            mock.patch("time.monotonic", side_effect=forbidden),
            mock.patch("sqlite3.connect", side_effect=forbidden),):
            projection = planning_fixtures.project((entry,), (material,))
        self.assertEqual(projection.units[0].body_utf8, material.body_utf8)
