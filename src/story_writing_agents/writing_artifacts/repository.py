"""Filesystem repository for agent-authored canonical writing artifacts."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Mapping
from uuid import uuid4

from ..agents import CORE_DOCUMENTS, EXECUTION_SPECIALIST, LEAD_ORCHESTRATOR
from ..errors import (
    ManuscriptExistsError,
    WorkspaceFormatError,
    WorkspaceSecurityError,
)
from ._storage import ArtifactEntrySecurityError, SecureWorkspaceStorage
from .catalog import (
    ArtifactCatalog,
    ArtifactCatalogEntry,
    ArtifactKind,
    canonical_artifact_kind,
    manuscript_artifact_path,
)
from .references import ArtifactContentSnapshot


_STATE_DIRECTORY = "agent_states/orchestrator_state/writing_artifacts"
_CATALOG_PATH = f"{_STATE_DIRECTORY}/catalog.json"
_COMMITS_DIRECTORY = f"{_STATE_DIRECTORY}/commits"
_TRANSACTIONS_DIRECTORY = f"{_STATE_DIRECTORY}/transactions"
_SNAPSHOTS_DIRECTORY = f"{_STATE_DIRECTORY}/input_snapshots"
_STAGING_DIRECTORY = f"{_STATE_DIRECTORY}/staging"
_LOCK_PATH = f"{_STATE_DIRECTORY}/.artifacts.lock"
_RECOVERY_BLOCK_PATH = f"{_STATE_DIRECTORY}/recovery_blocked.json"
_TRANSACTION_SCHEMA_VERSION = 1
_COMMIT_SCHEMA_VERSION = 1
_RECOVERY_SCHEMA_VERSION = 1
_SAFE_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")


class WritingArtifactRepository:
    """Own canonical artifact provenance, publication, and recovery.

    Construction performs no filesystem I/O. Every public read or commit owns
    both a repository-local lock and the workspace-local advisory lock, then
    completes or rejects pending transactions before consulting the catalog.
    """

    def __init__(self, workspace_root: str | Path) -> None:
        self._storage = SecureWorkspaceStorage(workspace_root)
        self._lock = threading.RLock()

    def catalog(self) -> ArtifactCatalog:
        """Return only current, provenance-backed canonical entries."""

        with self._locked_repository():
            catalog = self._load_catalog()
            current: dict[str, ArtifactCatalogEntry] = {}
            for path, entry in catalog.entries.items():
                try:
                    content = self._storage.read_bytes(path)
                except (FileNotFoundError, ArtifactEntrySecurityError):
                    continue
                if hashlib.sha256(content).hexdigest() == entry.sha256:
                    current[path] = entry
            return ArtifactCatalog(current)

    def list_paths(self) -> tuple[str, ...]:
        """Return sorted complete workspace-relative eligible identities."""

        return tuple(sorted(self.catalog().entries))

    def read_current(
        self,
        paths: tuple[str, ...],
    ) -> tuple[ArtifactContentSnapshot, ...]:
        """Return immutable current bytes for only the requested artifacts."""

        if type(paths) is not tuple:
            raise TypeError("artifact paths must be a tuple")
        if len(set(paths)) != len(paths):
            raise ValueError("artifact paths must be unique")
        for path in paths:
            if canonical_artifact_kind(path) is None:
                raise WorkspaceSecurityError(
                    "path is not a canonical writing artifact"
                )
        with self._locked_repository():
            catalog = self._load_catalog()
            snapshots: list[ArtifactContentSnapshot] = []
            for path in paths:
                entry = catalog.entries.get(path)
                if entry is None:
                    continue
                try:
                    content = self._storage.read_bytes(path)
                except (FileNotFoundError, ArtifactEntrySecurityError):
                    continue
                if hashlib.sha256(content).hexdigest() != entry.sha256:
                    continue
                snapshots.append(ArtifactContentSnapshot(entry, content))
            return tuple(snapshots)

    def read_text(self, path: str, *, max_bytes: int | None = None) -> str:
        """Read a current catalog entry only after hash and provenance checks."""

        if canonical_artifact_kind(path) is None:
            raise WorkspaceSecurityError("path is not a canonical writing artifact")
        with self._locked_repository():
            catalog = self._load_catalog()
            entry = catalog.entries.get(path)
            if entry is None:
                raise WorkspaceSecurityError("artifact has no repository provenance")
            content = self._storage.read_bytes(path, max_bytes=max_bytes)
            if hashlib.sha256(content).hexdigest() != entry.sha256:
                raise WorkspaceSecurityError("artifact provenance does not match current bytes")
            try:
                return content.decode("utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                raise WorkspaceFormatError("artifact is not valid UTF-8") from exc

    def commit_consensus(
        self,
        *,
        output_path: str,
        candidate: str,
        core_updates: Mapping[str, str],
        session_id: str,
        debate_id: str,
        revision_id: str,
        candidate_author_agent_id: str,
    ) -> tuple[str, tuple[str, ...]]:
        """Publish one unanimous story-agent result as a recoverable commit."""

        manuscript_path = manuscript_artifact_path(output_path)
        candidate_bytes = self._encode_agent_text(candidate, "approved candidate")
        updates = dict(core_updates)
        unknown = set(updates).difference(CORE_DOCUMENTS)
        if unknown:
            raise WorkspaceSecurityError(
                f"core document is not allowlisted: {sorted(unknown)[0]}"
            )
        encoded_updates = {
            filename: self._encode_agent_text(content, "core document update")
            for filename, content in updates.items()
        }
        for name, value in (
            ("session ID", session_id),
            ("debate ID", debate_id),
            ("revision ID", revision_id),
        ):
            self._validate_identity(value, name)
        expected_candidate_author = (
            EXECUTION_SPECIALIST if revision_id == "r0" else LEAD_ORCHESTRATOR
        )
        if candidate_author_agent_id != expected_candidate_author:
            raise WorkspaceSecurityError(
                "candidate author does not match the approved revision"
            )

        with self._locked_repository():
            if self._storage.read_bytes_if_exists(manuscript_path) is not None:
                raise ManuscriptExistsError(
                    f"refusing to overwrite {manuscript_path}"
                )
            catalog = self._load_catalog()
            commit_id = uuid4().hex
            committed_at = datetime.now(UTC).isoformat()
            target_specs: list[dict[str, object]] = []
            new_entries = dict(catalog.entries)
            ordered_updates = tuple(
                filename for filename in CORE_DOCUMENTS if filename in updates
            )
            artifacts = [
                (
                    manuscript_path,
                    ArtifactKind.MANUSCRIPT,
                    candidate_bytes,
                    candidate_author_agent_id,
                    True,
                )
            ]
            artifacts.extend(
                (
                    f"core_documents/{filename}",
                    ArtifactKind.CORE_DOCUMENT,
                    encoded_updates[filename],
                    LEAD_ORCHESTRATOR,
                    False,
                )
                for filename in ordered_updates
            )
            for path, kind, content, author, create_only in artifacts:
                previous = self._storage.read_bytes_if_exists(path)
                if create_only and previous is not None:
                    raise ManuscriptExistsError(f"refusing to overwrite {path}")
                content_hash = hashlib.sha256(content).hexdigest()
                entry = ArtifactCatalogEntry(
                    path=path,
                    kind=kind,
                    sha256=content_hash,
                    commit_id=commit_id,
                    committed_at=committed_at,
                    author_agent_id=author,
                    session_id=session_id,
                    debate_id=debate_id,
                    revision_id=revision_id,
                )
                new_entries[path] = entry
                target_specs.append(
                    {
                        "author_agent_id": author,
                        "create_only": create_only,
                        "kind": kind.value,
                        "new_content_base64": base64.b64encode(content).decode("ascii"),
                        "new_sha256": content_hash,
                        "old_exists": previous is not None,
                        "old_sha256": (
                            None
                            if previous is None
                            else hashlib.sha256(previous).hexdigest()
                        ),
                        "path": path,
                    }
                )

            commit_record = {
                "artifacts": [
                    {
                        "author_agent_id": spec["author_agent_id"],
                        "kind": spec["kind"],
                        "path": spec["path"],
                        "sha256": spec["new_sha256"],
                    }
                    for spec in target_specs
                ],
                "commit_id": commit_id,
                "committed_at": committed_at,
                "debate_id": debate_id,
                "revision_id": revision_id,
                "schema_version": _COMMIT_SCHEMA_VERSION,
                "session_id": session_id,
            }
            transaction = {
                "catalog_after": ArtifactCatalog(new_entries).to_dict(),
                "commit": commit_record,
                "schema_version": _TRANSACTION_SCHEMA_VERSION,
                "state": "prepared",
                "targets": target_specs,
                "transaction_id": commit_id,
            }
            transaction_path = f"{_TRANSACTIONS_DIRECTORY}/{commit_id}.json"
            self._write_new_json(transaction_path, transaction)
            self._complete_transaction(transaction_path, transaction)
            return manuscript_path, ordered_updates

    @staticmethod
    def _encode_agent_text(value: object, label: str) -> bytes:
        if type(value) is not str:
            raise WorkspaceFormatError(f"{label} must be text")
        try:
            return value.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            raise WorkspaceFormatError(f"{label} must be strict UTF-8") from exc

    @staticmethod
    def _validate_identity(value: object, label: str) -> str:
        if type(value) is not str or _SAFE_IDENTITY.fullmatch(value) is None:
            raise WorkspaceSecurityError(f"{label} is unsafe")
        return value

    def _locked_repository(self):
        return _RepositoryLock(self)

    def _ensure_state_directories(self) -> None:
        for path in (
            _STATE_DIRECTORY,
            _COMMITS_DIRECTORY,
            _TRANSACTIONS_DIRECTORY,
            _SNAPSHOTS_DIRECTORY,
            _STAGING_DIRECTORY,
        ):
            self._storage.ensure_directory(path)

    def _recover(self) -> None:
        if self._storage.read_bytes_if_exists(_RECOVERY_BLOCK_PATH) is not None:
            raise WorkspaceFormatError("writing-artifact recovery is blocked")
        try:
            names = self._storage.list_names(_TRANSACTIONS_DIRECTORY)
        except (WorkspaceFormatError, WorkspaceSecurityError):
            raise
        for name in names:
            if not name.endswith(".json") or len(name) != 37:
                self._block_recovery("unexpected transaction state entry")
            path = f"{_TRANSACTIONS_DIRECTORY}/{name}"
            try:
                transaction = self._read_json(path)
                self._validate_transaction(transaction, expected_id=name[:-5])
                self._complete_transaction(path, transaction)
            except Exception as exc:
                self._block_recovery(
                    f"transaction {name[:-5]} cannot be recovered",
                    cause=exc,
                )

    def _complete_transaction(
        self,
        transaction_path: str,
        transaction: dict[str, object],
    ) -> None:
        self._validate_transaction(
            transaction,
            expected_id=transaction["transaction_id"],
        )
        for raw_target in transaction["targets"]:
            assert isinstance(raw_target, dict)
            path = raw_target["path"]
            assert isinstance(path, str)
            expected_hash = raw_target["new_sha256"]
            assert isinstance(expected_hash, str)
            try:
                new_content = base64.b64decode(
                    raw_target["new_content_base64"],
                    validate=True,
                )
            except (TypeError, ValueError) as exc:
                raise WorkspaceFormatError("transaction content encoding is invalid") from exc
            if hashlib.sha256(new_content).hexdigest() != expected_hash:
                raise WorkspaceFormatError("transaction content hash is invalid")
            current = self._storage.read_bytes_if_exists(path)
            current_hash = (
                None if current is None else hashlib.sha256(current).hexdigest()
            )
            if current_hash == expected_hash:
                continue
            old_exists = raw_target["old_exists"]
            old_hash = raw_target["old_sha256"]
            if (current is not None) != old_exists or current_hash != old_hash:
                raise WorkspaceSecurityError("artifact diverged during recovery")
            self._storage.publish_bytes(
                path,
                new_content,
                expected_sha256=old_hash,
                expected_exists=old_exists,
                create_only=raw_target["create_only"],
                staging_directory=_STAGING_DIRECTORY,
            )
            published = self._storage.read_bytes(path)
            if hashlib.sha256(published).hexdigest() != expected_hash:
                raise WorkspaceSecurityError("artifact publication could not be verified")

        if transaction["state"] == "prepared":
            transaction = dict(transaction)
            transaction["state"] = "artifacts_published"
            self._replace_json(transaction_path, transaction)

        commit = transaction["commit"]
        assert isinstance(commit, dict)
        commit_id = commit["commit_id"]
        assert isinstance(commit_id, str)
        self._write_immutable_json(
            f"{_COMMITS_DIRECTORY}/{commit_id}.json",
            commit,
        )
        catalog_after = transaction["catalog_after"]
        assert isinstance(catalog_after, dict)
        ArtifactCatalog.from_dict(catalog_after)
        self._write_json_exact(_CATALOG_PATH, catalog_after)
        self._storage.unlink(
            transaction_path,
            expected_sha256=hashlib.sha256(
                self._json_bytes(transaction)
            ).hexdigest(),
            retirement_directory=_STAGING_DIRECTORY,
            missing_ok=True,
        )

    def _validate_transaction(
        self,
        value: object,
        *,
        expected_id: object,
    ) -> None:
        expected_fields = {
            "catalog_after",
            "commit",
            "schema_version",
            "state",
            "targets",
            "transaction_id",
        }
        if type(value) is not dict or set(value) != expected_fields:
            raise WorkspaceFormatError("transaction fields are invalid")
        if (
            type(value["schema_version"]) is not int
            or value["schema_version"] != _TRANSACTION_SCHEMA_VERSION
        ):
            raise WorkspaceFormatError("transaction schema is unsupported")
        transaction_id = value["transaction_id"]
        if (
            type(transaction_id) is not str
            or re.fullmatch(r"[0-9a-f]{32}", transaction_id) is None
            or transaction_id != expected_id
        ):
            raise WorkspaceFormatError("transaction identity is invalid")
        if value["state"] not in {"prepared", "artifacts_published"}:
            raise WorkspaceFormatError("transaction state is invalid")
        commit = value["commit"]
        self._validate_commit(commit, transaction_id=transaction_id)
        assert isinstance(commit, dict)
        targets = value["targets"]
        if type(targets) is not list or not targets:
            raise WorkspaceFormatError("transaction targets are invalid")
        target_fields = {
            "author_agent_id",
            "create_only",
            "kind",
            "new_content_base64",
            "new_sha256",
            "old_exists",
            "old_sha256",
            "path",
        }
        observed_paths: set[str] = set()
        for target in targets:
            if type(target) is not dict or set(target) != target_fields:
                raise WorkspaceFormatError("transaction target fields are invalid")
            path = target["path"]
            kind = target["kind"]
            try:
                parsed_kind = ArtifactKind(kind)
            except (TypeError, ValueError) as exc:
                raise WorkspaceFormatError("transaction artifact kind is invalid") from exc
            if canonical_artifact_kind(path) is not parsed_kind or path in observed_paths:
                raise WorkspaceFormatError("transaction artifact path is invalid")
            observed_paths.add(path)
            if type(target["create_only"]) is not bool or type(target["old_exists"]) is not bool:
                raise WorkspaceFormatError("transaction boolean field is invalid")
            if parsed_kind is ArtifactKind.MANUSCRIPT and target["create_only"] is not True:
                raise WorkspaceFormatError("manuscript transaction must be create-only")
            if parsed_kind is ArtifactKind.CORE_DOCUMENT and target["create_only"] is not False:
                raise WorkspaceFormatError("core-document transaction must replace")
            expected_author = (
                EXECUTION_SPECIALIST
                if parsed_kind is ArtifactKind.MANUSCRIPT
                and commit["revision_id"] == "r0"
                else LEAD_ORCHESTRATOR
            )
            if target["author_agent_id"] != expected_author:
                raise WorkspaceFormatError("transaction author is invalid")
            if (
                type(target["new_sha256"]) is not str
                or re.fullmatch(r"[0-9a-f]{64}", target["new_sha256"])
                is None
            ):
                raise WorkspaceFormatError("transaction new hash is invalid")
            old_hash = target["old_sha256"]
            if target["old_exists"]:
                if (
                    type(old_hash) is not str
                    or re.fullmatch(r"[0-9a-f]{64}", old_hash) is None
                ):
                    raise WorkspaceFormatError("transaction old hash is invalid")
            elif old_hash is not None:
                raise WorkspaceFormatError("absent transaction target has an old hash")
        catalog = ArtifactCatalog.from_dict(value["catalog_after"])
        target_metadata = {
            target["path"]: (
                target["kind"],
                target["author_agent_id"],
                target["new_sha256"],
            )
            for target in targets
        }
        commit_artifacts = commit["artifacts"]
        assert isinstance(commit_artifacts, list)
        commit_metadata = {
            artifact["path"]: (
                artifact["kind"],
                artifact["author_agent_id"],
                artifact["sha256"],
            )
            for artifact in commit_artifacts
        }
        if commit_metadata != target_metadata:
            raise WorkspaceFormatError(
                "transaction commit does not exactly match targets"
            )
        catalog_entries = {
            path: entry
            for path, entry in catalog.entries.items()
            if entry.commit_id == transaction_id
        }
        if set(catalog_entries) != set(target_metadata):
            raise WorkspaceFormatError(
                "transaction catalog commit set does not match targets"
            )
        for target in targets:
            entry = catalog.entries.get(target["path"])
            if (
                entry is None
                or entry.commit_id != transaction_id
                or entry.sha256 != target["new_sha256"]
                or entry.kind.value != target["kind"]
                or entry.author_agent_id != target["author_agent_id"]
                or entry.committed_at != commit["committed_at"]
                or entry.session_id != commit["session_id"]
                or entry.debate_id != commit["debate_id"]
                or entry.revision_id != commit["revision_id"]
            ):
                raise WorkspaceFormatError("transaction catalog does not match targets")

    @staticmethod
    def _validate_commit(value: object, *, transaction_id: str) -> None:
        fields = {
            "artifacts",
            "commit_id",
            "committed_at",
            "debate_id",
            "revision_id",
            "schema_version",
            "session_id",
        }
        if type(value) is not dict or set(value) != fields:
            raise WorkspaceFormatError("artifact commit fields are invalid")
        if (
            type(value["commit_id"]) is not str
            or type(value["schema_version"]) is not int
            or value["schema_version"] != _COMMIT_SCHEMA_VERSION
            or value["commit_id"] != transaction_id
        ):
            raise WorkspaceFormatError("artifact commit identity is invalid")
        for field in ("committed_at", "session_id", "debate_id", "revision_id"):
            if type(value[field]) is not str or not value[field]:
                raise WorkspaceFormatError("artifact commit metadata is invalid")
        try:
            timestamp = datetime.fromisoformat(value["committed_at"])
        except (TypeError, ValueError) as exc:
            raise WorkspaceFormatError("artifact commit timestamp is invalid") from exc
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise WorkspaceFormatError("artifact commit timestamp must be offset-aware")
        for field in ("session_id", "debate_id", "revision_id"):
            if _SAFE_IDENTITY.fullmatch(value[field]) is None:
                raise WorkspaceFormatError("artifact commit identity metadata is unsafe")
        artifacts = value["artifacts"]
        if type(artifacts) is not list or not artifacts:
            raise WorkspaceFormatError("artifact commit entries are invalid")
        artifact_fields = {"author_agent_id", "kind", "path", "sha256"}
        paths: set[str] = set()
        manuscript_count = 0
        for artifact in artifacts:
            if type(artifact) is not dict or set(artifact) != artifact_fields:
                raise WorkspaceFormatError("artifact commit entry fields are invalid")
            try:
                kind = ArtifactKind(artifact["kind"])
            except (TypeError, ValueError) as exc:
                raise WorkspaceFormatError("artifact commit kind is invalid") from exc
            path = artifact["path"]
            if canonical_artifact_kind(path) is not kind or path in paths:
                raise WorkspaceFormatError("artifact commit path is invalid")
            paths.add(path)
            expected_author = (
                EXECUTION_SPECIALIST
                if kind is ArtifactKind.MANUSCRIPT
                and value["revision_id"] == "r0"
                else LEAD_ORCHESTRATOR
            )
            if artifact["author_agent_id"] != expected_author:
                raise WorkspaceFormatError("artifact commit author is invalid")
            if (
                type(artifact["sha256"]) is not str
                or re.fullmatch(r"[0-9a-f]{64}", artifact["sha256"])
                is None
            ):
                raise WorkspaceFormatError("artifact commit hash is invalid")
            if kind is ArtifactKind.MANUSCRIPT:
                manuscript_count += 1
        if manuscript_count != 1:
            raise WorkspaceFormatError("artifact commit must contain one manuscript")

    def _load_catalog(self) -> ArtifactCatalog:
        raw = self._storage.read_bytes_if_exists(_CATALOG_PATH)
        if raw is None:
            return ArtifactCatalog.empty()
        try:
            catalog = ArtifactCatalog.from_dict(
                json.loads(raw.decode("utf-8", errors="strict"))
            )
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise WorkspaceFormatError("artifact catalog is invalid") from exc
        verified: dict[str, ArtifactCatalogEntry] = {}
        commits: dict[str, dict[str, object] | None] = {}
        for path, entry in catalog.entries.items():
            if entry.commit_id not in commits:
                commit_path = f"{_COMMITS_DIRECTORY}/{entry.commit_id}.json"
                raw_commit = self._storage.read_bytes_if_exists(commit_path)
                if raw_commit is None:
                    commits[entry.commit_id] = None
                else:
                    try:
                        parsed_commit = json.loads(
                            raw_commit.decode("utf-8", errors="strict")
                        )
                        self._validate_commit(
                            parsed_commit,
                            transaction_id=entry.commit_id,
                        )
                    except (
                        UnicodeDecodeError,
                        json.JSONDecodeError,
                        TypeError,
                        ValueError,
                    ) as exc:
                        raise WorkspaceFormatError(
                            "artifact commit provenance is invalid"
                        ) from exc
                    commits[entry.commit_id] = parsed_commit
            commit = commits[entry.commit_id]
            if commit is None:
                continue
            if (
                commit["committed_at"] != entry.committed_at
                or commit["session_id"] != entry.session_id
                or commit["debate_id"] != entry.debate_id
                or commit["revision_id"] != entry.revision_id
            ):
                continue
            artifacts = commit["artifacts"]
            assert isinstance(artifacts, list)
            if any(
                artifact["path"] == path
                and artifact["kind"] == entry.kind.value
                and artifact["sha256"] == entry.sha256
                and artifact["author_agent_id"] == entry.author_agent_id
                for artifact in artifacts
            ):
                verified[path] = entry
        return ArtifactCatalog(verified)

    def _read_json(self, relative: str) -> object:
        try:
            return json.loads(self._storage.read_bytes(relative).decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WorkspaceFormatError("artifact repository JSON is invalid") from exc

    @staticmethod
    def _json_bytes(value: object) -> bytes:
        try:
            return (
                json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
            ).encode("utf-8", errors="strict")
        except (TypeError, ValueError, UnicodeEncodeError) as exc:
            raise WorkspaceFormatError("artifact repository value is not JSON-safe") from exc

    def _write_new_json(self, relative: str, value: object) -> None:
        self._storage.publish_bytes(
            relative,
            self._json_bytes(value),
            expected_sha256=None,
            expected_exists=False,
            create_only=True,
            staging_directory=_STAGING_DIRECTORY,
        )

    def _replace_json(self, relative: str, value: object) -> None:
        current = self._storage.read_bytes(relative)
        self._storage.publish_bytes(
            relative,
            self._json_bytes(value),
            expected_sha256=hashlib.sha256(current).hexdigest(),
            expected_exists=True,
            create_only=False,
            staging_directory=_STAGING_DIRECTORY,
        )

    def _write_immutable_json(self, relative: str, value: object) -> None:
        desired = self._json_bytes(value)
        current = self._storage.read_bytes_if_exists(relative)
        if current == desired:
            return
        if current is not None:
            raise WorkspaceFormatError(
                "immutable artifact repository record conflicts with recovery"
            )
        self._storage.publish_bytes(
            relative,
            desired,
            expected_sha256=None,
            expected_exists=False,
            create_only=True,
            staging_directory=_STAGING_DIRECTORY,
        )

    def _write_json_exact(self, relative: str, value: object) -> None:
        desired = self._json_bytes(value)
        current = self._storage.read_bytes_if_exists(relative)
        if current == desired:
            return
        self._storage.publish_bytes(
            relative,
            desired,
            expected_sha256=(
                None if current is None else hashlib.sha256(current).hexdigest()
            ),
            expected_exists=current is not None,
            create_only=False,
            staging_directory=_STAGING_DIRECTORY,
        )

    def _block_recovery(self, reason: str, *, cause: Exception | None = None) -> None:
        marker = {
            "reason": reason,
            "schema_version": _RECOVERY_SCHEMA_VERSION,
        }
        try:
            self._write_json_exact(_RECOVERY_BLOCK_PATH, marker)
        except Exception as marker_error:
            error = WorkspaceFormatError("writing-artifact recovery failed closed")
            error.add_note(f"recovery marker failed: {type(marker_error).__name__}")
            if cause is not None:
                raise error from cause
            raise error from marker_error
        error = WorkspaceFormatError("writing-artifact recovery is blocked")
        if cause is not None:
            raise error from cause
        raise error


class _RepositoryLock:
    """Acquire the process and workspace repository locks in fixed order."""

    def __init__(self, repository: WritingArtifactRepository) -> None:
        self._repository = repository
        self._file_lock = None

    def __enter__(self) -> None:
        self._repository._lock.acquire()
        try:
            self._repository._ensure_state_directories()
            self._file_lock = self._repository._storage.exclusive_lock(_LOCK_PATH)
            self._file_lock.__enter__()
            self._repository._recover()
        except BaseException:
            try:
                if self._file_lock is not None:
                    self._file_lock.__exit__(None, None, None)
            finally:
                self._repository._lock.release()
            raise

    def __exit__(self, exc_type, exc, traceback) -> None:
        try:
            if self._file_lock is not None:
                self._file_lock.__exit__(exc_type, exc, traceback)
        finally:
            self._repository._lock.release()
