"""Deferred optional conversation RAG composition verification."""

from __future__ import annotations

import subprocess
import sys
import textwrap
import unittest
from importlib import metadata
from pathlib import Path
from unittest import mock

from story_writing_agents import conversation_rag as rag
from story_writing_agents import conversation_rag_composition as composition

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_GENERIC_RAG_SOURCE = _REPOSITORY_ROOT / "vendor" / "generic-rag" / "src"


def _collaborators() -> rag.ConversationRagCollaborators:
    return rag.ConversationRagCollaborators(
        object(),
        object(),
        object(),
        object(),
        object(),
    )


class ConversationRagCompositionTests(unittest.TestCase):
    def assertCompositionFailure(
        self,
        error: composition.ConversationRagCompositionError,
        failure: composition.ConversationRagCompositionFailure,
    ) -> None:
        self.assertEqual(error.failure, failure)
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)
        self.assertNotIn("secret", str(error))
        self.assertNotIn("secret", repr(error))

    def test_exact_public_exports_allowlist_and_failure_messages(self) -> None:
        self.assertEqual(
            composition.__all__,
            [
                "CONVERSATION_RAG_PROVIDER_ALLOWLIST",
                "ConversationRagCompositionError",
                "ConversationRagCompositionFailure",
                "compose_conversation_rag",
            ],
        )
        self.assertEqual(composition.CONVERSATION_RAG_PROVIDER_ALLOWLIST, ("local",))
        self.assertEqual(
            tuple(
                member.value for member in composition.ConversationRagCompositionFailure
            ),
            (
                "provider_unsupported",
                "dependency_missing",
                "version_incompatible",
                "capability_incompatible",
            ),
        )
        expected_messages = {
            composition.ConversationRagCompositionFailure.PROVIDER_UNSUPPORTED: (
                "conversation RAG provider is not supported"
            ),
            composition.ConversationRagCompositionFailure.DEPENDENCY_MISSING: (
                "conversation RAG is unavailable; install the pinned 'rag' extra"
            ),
            composition.ConversationRagCompositionFailure.VERSION_INCOMPATIBLE: (
                "conversation RAG dependency version is incompatible; "
                "reinstall the pinned 'rag' extra"
            ),
            composition.ConversationRagCompositionFailure.CAPABILITY_INCOMPATIBLE: (
                "conversation RAG dependency capabilities are incompatible; "
                "reinstall the pinned 'rag' extra"
            ),
        }
        for failure, message in expected_messages.items():
            with self.subTest(failure=failure):
                error = composition.ConversationRagCompositionError(failure)
                self.assertEqual(str(error), message)
                self.assertCompositionFailure(error, failure)

    def test_module_import_does_not_probe_metadata_or_import_generic_rag(self) -> None:
        script = textwrap.dedent(
            f"""
            import importlib.metadata
            import sys

            sys.path.insert(0, {str(_REPOSITORY_ROOT / "src")!r})
            calls = []
            def forbidden_version(name):
                calls.append(name)
                raise AssertionError("metadata must stay deferred")
            importlib.metadata.version = forbidden_version
            import story_writing_agents.conversation_rag_composition as module
            assert module.CONVERSATION_RAG_PROVIDER_ALLOWLIST == ("local",)
            assert calls == []
            assert not any(
                name == "generic_rag" or name.startswith("generic_rag.")
                for name in sys.modules
            )
            """
        )
        completed = subprocess.run(
            [sys.executable, "-I", "-W", "error", "-c", script],
            cwd=_REPOSITORY_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            0,
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}",
        )

    def test_unsupported_provider_and_wrong_collaborator_type_do_not_probe(
        self,
    ) -> None:
        for provider in ("", "remote", "LOCAL", 1):
            with self.subTest(provider=provider):
                with (
                    mock.patch.object(composition.metadata, "version") as version,
                    self.assertRaises(
                        composition.ConversationRagCompositionError
                    ) as caught,
                ):
                    composition.compose_conversation_rag(  # type: ignore[arg-type]
                        provider,
                        _collaborators(),
                    )
                version.assert_not_called()
                self.assertCompositionFailure(
                    caught.exception,
                    composition.ConversationRagCompositionFailure.PROVIDER_UNSUPPORTED,
                )

        class DerivedCollaborators(rag.ConversationRagCollaborators):
            pass

        with (
            mock.patch.object(composition.metadata, "version") as version,
            self.assertRaisesRegex(TypeError, "must be exactly"),
        ):
            composition.compose_conversation_rag(
                "local",
                DerivedCollaborators(
                    object(),
                    object(),
                    object(),
                    object(),
                    object(),
                ),
            )
        version.assert_not_called()

    def test_missing_wrong_and_broken_dependency_metadata_are_sanitized(self) -> None:
        cases = (
            (
                metadata.PackageNotFoundError("secret-package-detail"),
                composition.ConversationRagCompositionFailure.DEPENDENCY_MISSING,
            ),
            (
                "9.9.9-secret",
                composition.ConversationRagCompositionFailure.VERSION_INCOMPATIBLE,
            ),
            (
                RuntimeError("secret metadata failure"),
                composition.ConversationRagCompositionFailure.CAPABILITY_INCOMPATIBLE,
            ),
        )
        for value, expected in cases:
            with self.subTest(expected=expected):
                patcher = (
                    mock.patch.object(
                        composition.metadata,
                        "version",
                        side_effect=value,
                    )
                    if isinstance(value, BaseException)
                    else mock.patch.object(
                        composition.metadata,
                        "version",
                        return_value=value,
                    )
                )
                with (
                    patcher,
                    self.assertRaises(
                        composition.ConversationRagCompositionError
                    ) as caught,
                ):
                    composition.compose_conversation_rag(
                        "local",
                        _collaborators(),
                    )
                self.assertCompositionFailure(caught.exception, expected)

    def test_missing_capability_and_adapter_import_failures_are_sanitized(self) -> None:
        if str(_GENERIC_RAG_SOURCE) not in sys.path:
            sys.path.insert(0, str(_GENERIC_RAG_SOURCE))
        from story_writing_agents import generic_rag_adapter

        empty = generic_rag_adapter.GenericRagCapabilitySnapshot(())
        with (
            mock.patch.object(composition.metadata, "version", return_value="0.1.0"),
            mock.patch.object(
                generic_rag_adapter,
                "inspect_generic_rag_capabilities",
                return_value=empty,
            ),
            self.assertRaises(
                composition.ConversationRagCompositionError
            ) as capability_caught,
        ):
            composition.compose_conversation_rag("local", _collaborators())
        self.assertCompositionFailure(
            capability_caught.exception,
            composition.ConversationRagCompositionFailure.CAPABILITY_INCOMPATIBLE,
        )

        original_import = __import__

        def blocked_import(
            name: str,
            globals: dict[str, object] | None = None,
            locals: dict[str, object] | None = None,
            fromlist: tuple[str, ...] = (),
            level: int = 0,
        ) -> object:
            if name.endswith("generic_rag_adapter"):
                raise ImportError("secret adapter import detail")
            return original_import(name, globals, locals, fromlist, level)

        with (
            mock.patch.object(composition.metadata, "version", return_value="0.1.0"),
            mock.patch("builtins.__import__", side_effect=blocked_import),
            self.assertRaises(
                composition.ConversationRagCompositionError
            ) as import_caught,
        ):
            composition.compose_conversation_rag("local", _collaborators())
        self.assertCompositionFailure(
            import_caught.exception,
            composition.ConversationRagCompositionFailure.CAPABILITY_INCOMPATIBLE,
        )

    def test_exact_pinned_capabilities_compose_the_local_service(self) -> None:
        if str(_GENERIC_RAG_SOURCE) not in sys.path:
            sys.path.insert(0, str(_GENERIC_RAG_SOURCE))
        from story_writing_agents import generic_rag_adapter

        snapshot = generic_rag_adapter.inspect_generic_rag_capabilities()
        self.assertTrue(snapshot.supports(composition._GENERIC_RAG_REQUIREMENTS))
        with mock.patch.object(
            composition.metadata,
            "version",
            return_value="0.1.0",
        ) as version:
            service = composition.compose_conversation_rag(
                "local",
                _collaborators(),
            )
        version.assert_called_once_with("generic-rag")
        self.assertIs(type(service), generic_rag_adapter.GenericRagConversationService)

    def test_capability_and_construction_failures_drop_dependency_details(self) -> None:
        if str(_GENERIC_RAG_SOURCE) not in sys.path:
            sys.path.insert(0, str(_GENERIC_RAG_SOURCE))
        from story_writing_agents import generic_rag_adapter

        for target, failure in (
            (
                "inspect_generic_rag_capabilities",
                RuntimeError("secret capability detail"),
            ),
            (
                "GenericRagConversationService",
                RuntimeError("secret construction detail"),
            ),
        ):
            with self.subTest(target=target):
                with (
                    mock.patch.object(
                        composition.metadata,
                        "version",
                        return_value="0.1.0",
                    ),
                    mock.patch.object(
                        generic_rag_adapter,
                        target,
                        side_effect=failure,
                    ),
                    self.assertRaises(
                        composition.ConversationRagCompositionError
                    ) as caught,
                ):
                    composition.compose_conversation_rag(
                        "local",
                        _collaborators(),
                    )
                self.assertCompositionFailure(
                    caught.exception,
                    composition.ConversationRagCompositionFailure.CAPABILITY_INCOMPATIBLE,
                )

    def test_process_controls_propagate_from_every_deferred_phase(self) -> None:
        if str(_GENERIC_RAG_SOURCE) not in sys.path:
            sys.path.insert(0, str(_GENERIC_RAG_SOURCE))
        from story_writing_agents import generic_rag_adapter

        for control in (KeyboardInterrupt("metadata"), SystemExit(3)):
            with self.subTest(metadata=type(control).__name__):
                with (
                    mock.patch.object(
                        composition.metadata,
                        "version",
                        side_effect=control,
                    ),
                    self.assertRaises(type(control)) as caught,
                ):
                    composition.compose_conversation_rag(
                        "local",
                        _collaborators(),
                    )
                self.assertIs(caught.exception, control)

        for target, control in (
            ("inspect_generic_rag_capabilities", KeyboardInterrupt("capability")),
            ("GenericRagConversationService", SystemExit(4)),
        ):
            with self.subTest(target=target):
                with (
                    mock.patch.object(
                        composition.metadata,
                        "version",
                        return_value="0.1.0",
                    ),
                    mock.patch.object(
                        generic_rag_adapter,
                        target,
                        side_effect=control,
                    ),
                    self.assertRaises(type(control)) as caught,
                ):
                    composition.compose_conversation_rag(
                        "local",
                        _collaborators(),
                    )
                self.assertIs(caught.exception, control)


if __name__ == "__main__":
    unittest.main()
