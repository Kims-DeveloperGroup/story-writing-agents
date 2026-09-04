"""Disposable-repository helpers for the safe Git adapter tests."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import unittest
from collections.abc import Mapping, Sequence
from pathlib import Path

from story_writing_agents.git import (
    GitBaselineManifest,
    GitManifestEntry,
)


def digest(content: bytes) -> str:
    """Return the exact SHA-256 digest used by baseline manifests."""

    return hashlib.sha256(content).hexdigest()


def write_manifest(
    root: Path,
    files: Mapping[str, bytes],
) -> GitBaselineManifest:
    """Write exact fixture bytes and return their canonical manifest."""

    entries: list[GitManifestEntry] = []
    for relative, content in sorted(files.items(), key=lambda item: item[0].encode()):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        entries.append(GitManifestEntry(relative, digest(content)))
    return GitBaselineManifest(tuple(entries))


def exception_graph(error: BaseException) -> tuple[BaseException, ...]:
    """Traverse cause and context links without looping."""

    pending = [error]
    observed: list[BaseException] = []
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        observed.append(current)
        for linked in (current.__cause__, current.__context__):
            if linked is not None:
                pending.append(linked)
    return tuple(observed)


class DisposableGitTestCase(unittest.TestCase):
    """Guard every oracle Git process inside one disposable filesystem root."""

    temporary: tempfile.TemporaryDirectory[str]
    test_root: Path
    git: str

    def setUp(self) -> None:
        super().setUp()
        executable = shutil.which("git")
        if executable is None:
            self.skipTest("Git executable is required for integration coverage")
        self.git = executable
        self.temporary = tempfile.TemporaryDirectory(prefix="safe-git-adapter-")
        self.addCleanup(self.temporary.cleanup)
        self.test_root = Path(self.temporary.name).resolve()
        (self.test_root / "home").mkdir()
        (self.test_root / "xdg").mkdir()
        (self.test_root / "global.gitconfig").write_text("", encoding="utf-8")

    def repository(self, name: str = "workspace") -> Path:
        """Create one exact nonsymlink directory beneath the disposable root."""

        root = self.test_root / name
        root.mkdir(parents=True)
        return root.resolve()

    def git_environment(self, **updates: str) -> dict[str, str]:
        """Return a network- and host-config-isolated Git environment."""

        environment = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.fspath(self.test_root / "home"),
            "XDG_CONFIG_HOME": os.fspath(self.test_root / "xdg"),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.fspath(self.test_root / "global.gitconfig"),
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ASKPASS": os.devnull,
            "GIT_PAGER": "cat",
            "LC_ALL": "C",
            "LANG": "C",
        }
        environment.update(updates)
        return environment

    def run_git(
        self,
        root: Path,
        *arguments: str,
        input_bytes: bytes | None = None,
        check: bool = True,
        environment: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        """Run an oracle Git command only beneath the disposable root."""

        resolved = root.resolve()
        if not resolved.is_relative_to(self.test_root):
            raise AssertionError("oracle Git escaped its disposable root")
        command: Sequence[str] = (self.git, *arguments)
        return subprocess.run(
            command,
            cwd=resolved,
            env=dict(environment or self.git_environment()),
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            timeout=15,
            check=check,
        )

    def initialize(self, root: Path, *, branch: str = "main") -> None:
        """Initialize an isolated repository without inherited templates."""

        self.run_git(
            root, "init", "--quiet", f"--initial-branch={branch}", "--template="
        )

    def configure_identity(
        self,
        root: Path,
        *,
        name: str = "Fixture Author",
        email: str = "fixture@example.invalid",
    ) -> None:
        """Set repository-local oracle identity."""

        self.run_git(root, "config", "user.name", name)
        self.run_git(root, "config", "user.email", email)

    def commit_all(self, root: Path, message: str = "fixture") -> None:
        """Create one isolated oracle commit."""

        self.run_git(root, "add", "--all", "--")
        self.run_git(root, "commit", "--quiet", "--no-gpg-sign", "-m", message)


__all__ = [
    "DisposableGitTestCase",
    "digest",
    "exception_graph",
    "write_manifest",
]
