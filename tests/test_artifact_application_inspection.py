"""Focused read-only artifact-application inspection verification."""

from __future__ import annotations

import ast
from pathlib import Path
import unittest
from unittest import mock

import story_writing_agents
from story_writing_agents import writing_artifacts
from story_writing_agents.writing_artifacts import (ArtifactApplicationError,
    ArtifactApplicationErrorCode,
    ArtifactApplicationResult,
    ArtifactApplicationState,
)
from story_writing_agents.writing_artifacts import (_application_store as application_store_module,
)
from story_writing_agents.writing_artifacts.repository import (
    WritingArtifactRepository,
)
from story_writing_agents.writing_artifacts import repository as repository_module

from tests.helpers import pristine_initialized_workspace
from tests.test_artifact_application_store_lifecycle import (_apply,
    _five_operation_request,
    _seed_five_operation_state,
)
from tests.test_artifact_application_store_recovery import (_InjectedFault,
    _inject_once,
)


_INVALID_IDENTITY_MESSAGE = "artifact application identity is invalid"
_OPERATIONAL_MESSAGES = {ArtifactApplicationErrorCode.CORRUPTION: (
        "artifact application state is corrupt"
    ),
    ArtifactApplicationErrorCode.RECOVERY_REQUIRED: ("artifact application recovery is required"
    ),
}


class _StringSubclass(str):
    pass


def _tree_snapshot(root: Path) -> tuple[tuple[str, str, bytes | None], ...]:
    return tuple((
            path.relative_to(root).as_posix(),
            "directory" if path.is_dir() else "file",
            None if path.is_dir() else path.read_bytes(),
        )
        for path in sorted(root.rglob("*"), key=lambda item: item.as_posix())
    )


class ArtifactApplicationInspectionTests(unittest.TestCase):
    def new_repository(self) -> tuple[Path, WritingArtifactRepository]:
        root = pristine_initialized_workspace(self).root
        return root, WritingArtifactRepository(root)

    def assert_application_error(self,
        code: ArtifactApplicationErrorCode,
        callback,
    ) -> ArtifactApplicationError:
        with self.assertRaises(ArtifactApplicationError) as raised:
            callback()
        error = raised.exception
        self.assertIs(error.code, code)
        expected = (_INVALID_IDENTITY_MESSAGE
            if code is ArtifactApplicationErrorCode.INVALID_IDENTIFIER
            else _OPERATIONAL_MESSAGES[code]
        )
        self.assertEqual(str(error), expected)
        self.assertEqual(error.args, (expected,))
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)
        return error

    def test_malformed_type_and_subclass_identity_reject_before_lock_or_delegation(self,
    ) -> None:
        _root, repository = self.new_repository()
        invalid = (None,
            b"application",
            True,
            _StringSubclass("application"),
            "",
            "bad identity",
            "../application",
            "a" * 129,
        )
        with (
            mock.patch.object(repository,
                "_locked_repository",
                wraps=repository._locked_repository,
            ) as locked,
            mock.patch.object(
                repository._applications,
                "inspect_result",
                wraps=repository._applications.inspect_result,
            ) as delegated,
        ):
            for identity in invalid:
                with self.subTest(identity_type=type(identity).__name__):
                    error = self.assert_application_error(
                        ArtifactApplicationErrorCode.INVALID_IDENTIFIER,
                        lambda identity=identity: repository.inspect_artifact_application(identity
                        ),
                    )
                    self.assertNotIn(repr(identity), str(error))
            self.assertEqual(locked.call_count, 0)
            self.assertEqual(delegated.call_count, 0)

    def test_absent_safe_identity_returns_none_with_only_exact_lock_bootstrap_topology(self,
    ) -> None:
        root, repository = self.new_repository()
        state = root / application_store_module.ARTIFACT_STATE_DIRECTORY
        self.assertFalse(state.exists())

        self.assertIsNone(repository.inspect_artifact_application("missing"))
        expected = ((".artifacts.lock", "file", b""),
            (".artifacts.lock.anchor", "file", b""),
            (".artifacts.lock.ready", "file", b""),
            ("applications", "directory", None),
            ("input_snapshots", "directory", None),
            ("staging", "directory", None),
            ("transactions", "directory", None),
        )
        first = _tree_snapshot(state)
        self.assertEqual(first, expected)
        self.assertIsNone(repository.inspect_artifact_application("missing"))
        self.assertEqual(_tree_snapshot(state), first)

    def test_existing_identity_returns_original_applied_result_repeatedly_without_writes_or_reapply(
        self,
    ) -> None:
        root, repository = self.new_repository()
        _seed_five_operation_state(root)
        request = _five_operation_request(repository)
        applied = _apply(repository, request)
        self.assertIs(applied.state, ArtifactApplicationState.APPLIED)
        before = _tree_snapshot(root)

        with mock.patch.object(repository._applications,
            "apply",
            side_effect=AssertionError("inspection must not reapply"),
        ) as apply:
            first = repository.inspect_artifact_application(request.application_id)
            second = repository.inspect_artifact_application(request.application_id)
        self.assertEqual(apply.call_count, 0)
        self.assertIsInstance(first, ArtifactApplicationResult)
        self.assertEqual(first, applied)
        self.assertEqual(second, applied)
        assert first is not None and second is not None
        self.assertIs(first.state, ArtifactApplicationState.APPLIED)
        self.assertIs(second.state, ArtifactApplicationState.APPLIED)
        self.assertEqual(_tree_snapshot(root), before)
        rendered = repr(first)
        for content in ("outline-after",
            "move-content",
            "opening-after",
            "retired-content",
            "agent_states/orchestrator_state/writing_artifacts/applications",
        ):
            self.assertNotIn(content, rendered)

    def test_pending_journal_recovers_and_audits_before_returning_exact_applied_result(self,
    ) -> None:
        root, repository = self.new_repository()
        _seed_five_operation_state(root)
        request = _five_operation_request(repository)
        repository._applications._fault_injector = _inject_once("after_journal")
        with self.assertRaises(_InjectedFault):
            _apply(repository, request)
        transactions = root / application_store_module.ARTIFACT_TRANSACTIONS_DIRECTORY
        self.assertEqual(len(tuple(transactions.glob("*.json"))), 1)

        recovered = WritingArtifactRepository(root)
        result = recovered.inspect_artifact_application(request.application_id)
        self.assertIsInstance(result, ArtifactApplicationResult)
        assert result is not None
        self.assertIs(result.state, ArtifactApplicationState.APPLIED)
        self.assertEqual(result.application_id, request.application_id)
        self.assertFalse(tuple(transactions.glob("*.json")))
        self.assertEqual(recovered.inspect_artifact_application(request.application_id),
            result,
        )

    def test_corruption_and_recovery_required_are_distinct_content_safe_failures(self,
    ) -> None:
        secret = "UNIQUE-INSPECTION-STORY-SECRET"
        for case in ("catalog", "record", "marker", "journal"):
            with self.subTest(case=case):
                root, repository = self.new_repository()
                _seed_five_operation_state(root)
                request = _five_operation_request(repository,
                    application_id=f"application-{case}",
                )
                if case in {"catalog", "record"}:
                    _apply(repository, request)
                state = root / application_store_module.ARTIFACT_STATE_DIRECTORY
                if case == "catalog":
                    state.joinpath("catalog.json").write_text(secret,
                        encoding="utf-8",
                    )
                    code = ArtifactApplicationErrorCode.CORRUPTION
                elif case == "record":
                    state.joinpath("applications",
                        f"{request.application_id}.json",
                    ).write_text(secret, encoding="utf-8")
                    code = ArtifactApplicationErrorCode.CORRUPTION
                elif case == "marker":
                    state.mkdir(parents=True, exist_ok=True)
                    state.joinpath("recovery_blocked.json").write_text(secret,
                        encoding="utf-8",
                    )
                    code = ArtifactApplicationErrorCode.RECOVERY_REQUIRED
                else:
                    transaction = state / "transactions" / ("f" * 32 + ".json")
                    transaction.parent.mkdir(parents=True, exist_ok=True)
                    transaction.write_text(secret, encoding="utf-8")
                    code = ArtifactApplicationErrorCode.RECOVERY_REQUIRED

                error = self.assert_application_error(code,
                    lambda: repository.inspect_artifact_application(request.application_id
                    ),
                )
                rendered = "\n".join((str(error), repr(error), repr(error.args), repr(vars(error)))
                )
                self.assertNotIn(secret, rendered)

    def test_one_lock_one_delegation_and_no_mutation_or_external_owner_route(self,
    ) -> None:
        _root, repository = self.new_repository()
        lock = repository._storage.exclusive_lock
        inspect = repository._applications.inspect_result
        with (
            mock.patch.object(repository._storage,
                "exclusive_lock",
                wraps=lock,
            ) as acquire,
            mock.patch.object(
                repository._applications,
                "inspect_result",
                wraps=inspect,
            ) as delegate,
            mock.patch.object(
                repository._applications,
                "apply",
                side_effect=AssertionError("inspection must not mutate artifacts"),
            ) as apply,
        ):
            self.assertIsNone(repository.inspect_artifact_application("missing"))
        self.assertEqual(acquire.call_count, 1)
        delegate.assert_called_once_with("missing")
        self.assertEqual(apply.call_count, 0)
        for facade in (story_writing_agents, writing_artifacts):
            self.assertNotIn("WritingArtifactRepository", facade.__all__)
            self.assertFalse(hasattr(facade, "WritingArtifactRepository"))
            self.assertNotIn("inspect_artifact_application", facade.__all__)
            self.assertFalse(hasattr(facade, "inspect_artifact_application"))

        source_path = Path(repository_module.__file__)
        tree = ast.parse(source_path.read_text(encoding="utf-8"),
            filename=str(source_path),
        )
        repository_class = next(node
            for node in tree.body
            if isinstance(node, ast.ClassDef)
            and node.name == "WritingArtifactRepository"
        )
        seam = next(node
            for node in repository_class.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "inspect_artifact_application"
        )
        calls = [node.func.attr
            for node in ast.walk(seam)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        ]
        self.assertEqual(calls.count("inspect_result"), 1)
        self.assertFalse(set(calls)
            & {"apply",
                "apply_artifact_changes",
                "commit_consensus",
                "write_bytes",
                "write_text",
            }
        )
        imported = {alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        forbidden = ("agent_context",
            "provider",
            "confirmation",
            "session_",
            ".git",
        )
        self.assertFalse(any(marker in name for name in imported for marker in forbidden)
        )


if __name__ == "__main__":
    unittest.main()
