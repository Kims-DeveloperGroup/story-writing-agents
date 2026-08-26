"""Disposable capability-probed SQLite FTS5 conversation-search index."""

from __future__ import annotations

import importlib
import os
import re
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable


SQLITE_CONVERSATION_SEARCH_INDEX_PATH = (
    "discussion_logs/.conversation_storage_fts5.sqlite3"
)
SQLITE_CONVERSATION_SEARCH_INDEX_SCHEMA_VERSION = 1

_APPLICATION_ID = 0x53574654
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
_SIDECAR_SUFFIXES = ("-journal", "-wal", "-shm")
_TEMPORARY_PREFIX = ".conversation_storage_fts5."
_TEMPORARY_SUFFIX = ".tmp"
_TEMPORARY_NAME = re.compile(r"\.conversation_storage_fts5\.[A-Za-z0-9_]{8,64}\.tmp")
_TOKENIZER = "unicode61 remove_diacritics 0"
_CREATE_IDENTITY_SQL = """
CREATE TABLE conversation_search_index_identity (
    singleton INTEGER NOT NULL PRIMARY KEY,
    schema_version INTEGER NOT NULL,
    source_generation INTEGER NOT NULL,
    normalization_version INTEGER NOT NULL,
    tokenizer TEXT NOT NULL,
    runtime_identity TEXT NOT NULL,
    CHECK (typeof(singleton) = 'integer' AND singleton = 1),
    CHECK (typeof(schema_version) = 'integer' AND schema_version = 1),
    CHECK (typeof(source_generation) = 'integer' AND source_generation >= 0),
    CHECK (typeof(normalization_version) = 'integer' AND normalization_version >= 1),
    CHECK (typeof(tokenizer) = 'text'),
    CHECK (typeof(runtime_identity) = 'text')
)
""".strip()
_CREATE_FTS_SQL = f"""
CREATE VIRTUAL TABLE conversation_events_fts USING fts5(
    session_id UNINDEXED,
    conversation_id UNINDEXED,
    sequence UNINDEXED,
    searchable_content,
    tokenize='{_TOKENIZER}'
)
""".strip()


class SQLiteConversationSearchIndexError(Exception):
    """Content-free failure of the optional disposable FTS resource."""

    def __init__(self) -> None:
        super().__init__("SQLite conversation search index is unavailable")


def _strict_utf8(value: str, *, label: str) -> str:
    """Return one exact strict-UTF-8 string."""

    if type(value) is not str:
        raise TypeError(f"{label} must be a string")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{label} must be valid UTF-8") from exc
    return value


def _identifier(value: str, *, label: str) -> str:
    """Return one exact safe identifier."""

    text = _strict_utf8(value, label=label)
    if _IDENTIFIER.fullmatch(text) is None:
        raise ValueError(f"{label} is invalid")
    return text


def _non_negative_integer(value: int, *, label: str) -> int:
    """Return one exact non-negative integer."""

    if type(value) is not int:
        raise TypeError(f"{label} must be an integer")
    if value < 0:
        raise ValueError(f"{label} must not be negative")
    return value


def _positive_integer(value: int, *, label: str) -> int:
    """Return one exact positive integer."""

    result = _non_negative_integer(value, label=label)
    if result < 1:
        raise ValueError(f"{label} must be positive")
    return result


@dataclass(frozen=True, slots=True)
class SQLiteConversationSearchIndexDocument:
    """One canonical visible event projection supplied by the SQLite adapter."""

    session_id: str
    conversation_id: str
    sequence: int
    searchable_content: str

    def __post_init__(self) -> None:
        _identifier(self.session_id, label="session ID")
        _identifier(self.conversation_id, label="conversation ID")
        _positive_integer(self.sequence, label="event sequence")
        _strict_utf8(
            self.searchable_content,
            label="searchable conversation event content",
        )


@dataclass(frozen=True, slots=True)
class _Runtime:
    module: Any
    identity: str
    manifest: frozenset[str]


class SQLiteConversationSearchIndex:
    """Own one fixed disposable companion index and its bounded connections.

    Construction pins an already-canonical absolute workspace key and performs
    path arithmetic only. Every operation lazily probes FTS5 with a live
    in-memory create/insert/query/drop cycle, owns and closes its connections,
    and maps resource details to ``SQLiteConversationSearchIndexError``.
    """

    __slots__ = ("_normalization_version", "_path", "_workspace_root")

    def __init__(self, workspace_key: str, *, normalization_version: int) -> None:
        key = _strict_utf8(workspace_key, label="workspace key")
        canonical_key = os.path.normpath(key)
        if not os.path.isabs(key) or key != canonical_key:
            raise ValueError("workspace key must be canonical and absolute")
        root = Path(canonical_key)
        self._normalization_version = _positive_integer(
            normalization_version,
            label="conversation literal normalization version",
        )
        self._workspace_root = root
        self._path = root.joinpath(SQLITE_CONVERSATION_SEARCH_INDEX_PATH)

    def candidates(
        self,
        *,
        source_generation: int,
        session_id: str,
        conversation_ids: tuple[str, ...],
        normalized_query: str,
        maximum: int,
    ) -> frozenset[tuple[str, int]]:
        """Return valid same-generation candidate coordinates.

        Missing, stale, busy, unsafe, unavailable, or malformed state raises
        only the content-free owner error. The adapter intentionally translates
        that category to its authoritative fallback.
        """

        generation = _non_negative_integer(
            source_generation,
            label="source lexical generation",
        )
        session = _identifier(session_id, label="session ID")
        if type(conversation_ids) is not tuple:
            raise TypeError("conversation IDs must be an exact tuple")
        conversations = tuple(
            _identifier(value, label="conversation ID") for value in conversation_ids
        )
        if len(set(conversations)) != len(conversations):
            raise ValueError("conversation IDs must be unique")
        query = _strict_utf8(
            normalized_query,
            label="normalized conversation search query",
        )
        limit = _positive_integer(maximum, label="candidate limit")
        if not conversations:
            return frozenset()

        runtime = _runtime()
        path = self._path
        identity = _require_owned_file(path)
        _reject_sidecars(path)
        connection: Any | None = None
        try:
            connection = _connect(runtime.module, path, read_only=True)
            _configure_connection(connection, read_only=True)
            connection.execute("BEGIN")
            _verify_index(
                connection,
                runtime=runtime,
                source_generation=generation,
                normalization_version=self._normalization_version,
            )
            placeholders = ", ".join("?" for _ in conversations)
            fts_query = '"' + query.replace('"', '""') + '"'
            rows = connection.execute(
                "SELECT conversation_id, sequence "
                "FROM conversation_events_fts "
                "WHERE conversation_events_fts MATCH ? AND session_id = ? "
                f"AND conversation_id IN ({placeholders}) "
                "ORDER BY conversation_id, sequence LIMIT ?",
                (fts_query, session, *conversations, limit),
            ).fetchall()
            result: set[tuple[str, int]] = set()
            for row in rows:
                if (
                    type(row) is not tuple
                    or len(row) != 2
                    or type(row[0]) is not str
                    or type(row[1]) is not int
                    or row[0] not in conversations
                    or row[1] < 1
                ):
                    raise SQLiteConversationSearchIndexError()
                result.add((row[0], row[1]))
            connection.execute("COMMIT")
        except (KeyboardInterrupt, SystemExit):
            raise
        except SQLiteConversationSearchIndexError:
            raise
        except Exception:
            raise SQLiteConversationSearchIndexError() from None
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass
        if _require_owned_file(path) != identity:
            raise SQLiteConversationSearchIndexError()
        _reject_sidecars(path)
        return frozenset(result)

    def rebuild(
        self,
        *,
        source_generation: int,
        documents: Iterable[SQLiteConversationSearchIndexDocument],
        validate_publication: Callable[[], None],
    ) -> None:
        """Atomically publish a complete committed-source FTS generation.

        A missing target is initialized in a private owner-only file and
        installed create-only. A recognized existing target is rebuilt inside
        one rollback-journal transaction. Unsafe or unrecognized names are
        preserved and rejected; temporary resources owned by this call are
        cleaned without touching another path.
        """

        generation = _non_negative_integer(
            source_generation,
            label="source lexical generation",
        )
        try:
            iterator = iter(documents)
        except TypeError:
            raise TypeError("index documents must be iterable") from None
        if not callable(validate_publication):
            raise TypeError("publication validator must be callable")
        try:
            validate_publication()
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            raise SQLiteConversationSearchIndexError() from None
        runtime = _runtime()
        _require_safe_parent(self._workspace_root, self._path.parent)
        _reject_sidecars(self._path)
        try:
            identity = _require_owned_file(self._path)
        except SQLiteConversationSearchIndexError:
            recovered = self._recover_interrupted_publication(
                runtime,
                validate_publication=validate_publication,
            )
            if recovered is not None:
                identity = recovered
            elif _path_is_missing(self._path):
                self._create_and_publish(
                    runtime,
                    generation=generation,
                    documents=iterator,
                    validate_publication=validate_publication,
                )
                return
            else:
                raise
        self._rebuild_existing(
            runtime,
            identity=identity,
            generation=generation,
            documents=iterator,
            validate_publication=validate_publication,
        )

    def _create_and_publish(
        self,
        runtime: _Runtime,
        *,
        generation: int,
        documents: Iterable[SQLiteConversationSearchIndexDocument],
        validate_publication: Callable[[], None],
    ) -> None:
        """Build one private generation and install it without overwrite."""

        temporary: Path | None = None
        temporary_identity: tuple[int, int] | None = None
        publication_linked = False
        try:
            descriptor, raw_path = tempfile.mkstemp(
                prefix=_TEMPORARY_PREFIX,
                suffix=_TEMPORARY_SUFFIX,
                dir=self._path.parent,
            )
            temporary = Path(raw_path)
            try:
                os.fchmod(descriptor, 0o600)
            finally:
                os.close(descriptor)
            temporary_identity = _require_owned_file(temporary)
            _populate_new_index(
                runtime,
                temporary,
                generation=generation,
                normalization_version=self._normalization_version,
                documents=documents,
            )
            _reject_sidecars(temporary)
            if _require_owned_file(temporary) != temporary_identity:
                raise SQLiteConversationSearchIndexError()
            _require_safe_parent(self._workspace_root, self._path.parent)
            _reject_sidecars(self._path)
            try:
                validate_publication()
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception:
                raise SQLiteConversationSearchIndexError() from None
            try:
                os.link(temporary, self._path, follow_symlinks=False)
            except FileExistsError:
                raise SQLiteConversationSearchIndexError() from None
            publication_linked = True
            if (
                _require_owned_file(self._path, expected_link_count=2)
                != temporary_identity
                or _require_owned_file(temporary, expected_link_count=2)
                != temporary_identity
            ):
                raise SQLiteConversationSearchIndexError()
            try:
                validate_publication()
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception:
                raise SQLiteConversationSearchIndexError() from None
            temporary.unlink()
            temporary = None
            if _require_owned_file(self._path) != temporary_identity:
                raise SQLiteConversationSearchIndexError()
            _synchronize_file(self._path, expected_identity=temporary_identity)
            _synchronize_directory(self._path.parent)
        except (KeyboardInterrupt, SystemExit):
            raise
        except SQLiteConversationSearchIndexError:
            raise
        except Exception:
            raise SQLiteConversationSearchIndexError() from None
        finally:
            if (
                temporary is not None
                and temporary_identity is not None
                and not publication_linked
            ):
                _unlink_unchanged_temporary(temporary, temporary_identity)

    def _recover_interrupted_publication(
        self,
        runtime: _Runtime,
        *,
        validate_publication: Callable[[], None],
    ) -> tuple[int, int] | None:
        """Remove only one verified private alias from a complete target."""

        try:
            identity = _require_owned_file(self._path, expected_link_count=2)
        except SQLiteConversationSearchIndexError:
            return None
        alias = _matching_temporary_alias(self._path, identity=identity)
        _reject_sidecars(self._path)
        _reject_sidecars(alias)
        connection: Any | None = None
        try:
            connection = _connect(runtime.module, self._path, read_only=True)
            _configure_connection(connection, read_only=True)
            connection.execute("BEGIN")
            _verify_recognized_index(
                connection,
                runtime=runtime,
                normalization_version=self._normalization_version,
            )
            connection.execute("COMMIT")
        except (KeyboardInterrupt, SystemExit):
            raise
        except SQLiteConversationSearchIndexError:
            raise
        except Exception:
            raise SQLiteConversationSearchIndexError() from None
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass
        _require_safe_parent(self._workspace_root, self._path.parent)
        if (
            _require_owned_file(self._path, expected_link_count=2) != identity
            or _require_owned_file(alias, expected_link_count=2) != identity
        ):
            raise SQLiteConversationSearchIndexError()
        try:
            validate_publication()
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            raise SQLiteConversationSearchIndexError() from None
        if (
            _require_owned_file(self._path, expected_link_count=2) != identity
            or _require_owned_file(alias, expected_link_count=2) != identity
        ):
            raise SQLiteConversationSearchIndexError()
        try:
            alias.unlink()
        except OSError:
            raise SQLiteConversationSearchIndexError() from None
        if _require_owned_file(self._path) != identity:
            raise SQLiteConversationSearchIndexError()
        _synchronize_directory(self._path.parent)
        return identity

    def _rebuild_existing(
        self,
        runtime: _Runtime,
        *,
        identity: tuple[int, int],
        generation: int,
        documents: Iterable[SQLiteConversationSearchIndexDocument],
        validate_publication: Callable[[], None],
    ) -> None:
        """Replace documents and marker in one recognized-file transaction."""

        connection: Any | None = None
        committed = False
        try:
            connection = _connect(runtime.module, self._path, read_only=False)
            if _require_owned_file(self._path) != identity:
                raise SQLiteConversationSearchIndexError()
            _configure_connection(connection, read_only=True)
            connection.execute("BEGIN")
            _verify_recognized_index(
                connection,
                runtime=runtime,
                normalization_version=self._normalization_version,
            )
            connection.execute("COMMIT")
            _configure_connection(connection, read_only=False)
            connection.execute("BEGIN IMMEDIATE")
            _require_safe_parent(self._workspace_root, self._path.parent)
            if _require_owned_file(self._path) != identity:
                raise SQLiteConversationSearchIndexError()
            _reject_sidecars(self._path)
            _verify_recognized_index(
                connection,
                runtime=runtime,
                normalization_version=self._normalization_version,
            )
            connection.execute("DELETE FROM conversation_events_fts")
            _insert_documents(connection, documents)
            connection.execute(
                "UPDATE conversation_search_index_identity SET "
                "source_generation = ?, normalization_version = ?, "
                "tokenizer = ?, runtime_identity = ? WHERE singleton = 1",
                (
                    generation,
                    self._normalization_version,
                    _TOKENIZER,
                    runtime.identity,
                ),
            )
            _reject_duplicate_documents(connection)
            connection.execute(
                "INSERT INTO conversation_events_fts(conversation_events_fts) "
                "VALUES('integrity-check')"
            )
            try:
                validate_publication()
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception:
                raise SQLiteConversationSearchIndexError() from None
            if _require_owned_file(self._path) != identity:
                raise SQLiteConversationSearchIndexError()
            connection.execute("COMMIT")
            committed = True
        except (KeyboardInterrupt, SystemExit):
            raise
        except SQLiteConversationSearchIndexError:
            raise
        except Exception:
            raise SQLiteConversationSearchIndexError() from None
        finally:
            if connection is not None:
                if not committed:
                    try:
                        connection.rollback()
                    except Exception:
                        pass
                try:
                    connection.close()
                except Exception:
                    pass
        if _require_owned_file(self._path) != identity:
            raise SQLiteConversationSearchIndexError()
        _reject_sidecars(self._path)
        _synchronize_file(self._path, expected_identity=identity)
        _synchronize_directory(self._path.parent)


def _runtime() -> _Runtime:
    """Live-probe FTS5 and return its exact runtime schema signature."""

    connection: Any | None = None
    try:
        module = importlib.import_module("sqlite3")
        connection = module.connect(":memory:", isolation_level=None)
        row = connection.execute("SELECT sqlite_version()").fetchone()
        if type(row) is not tuple or len(row) != 1 or type(row[0]) is not str:
            raise SQLiteConversationSearchIndexError()
        connection.execute(_CREATE_IDENTITY_SQL)
        connection.execute(_CREATE_FTS_SQL)
        connection.execute(
            "INSERT INTO conversation_events_fts "
            "(session_id, conversation_id, sequence, searchable_content) "
            "VALUES ('probe', 'probe', 1, 'capability probe')"
        )
        probe = connection.execute(
            "SELECT sequence FROM conversation_events_fts "
            "WHERE conversation_events_fts MATCH ?",
            ('"capability probe"',),
        ).fetchall()
        if probe != [(1,)]:
            raise SQLiteConversationSearchIndexError()
        connection.execute("DROP TABLE conversation_events_fts")
        connection.execute(_CREATE_FTS_SQL)
        manifest = _schema_manifest(connection)
        runtime_identity = f"sqlite-{row[0]}:{_TOKENIZER}"
        return _Runtime(module, runtime_identity, manifest)
    except (KeyboardInterrupt, SystemExit):
        raise
    except SQLiteConversationSearchIndexError:
        raise
    except Exception:
        raise SQLiteConversationSearchIndexError() from None
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass


def _populate_new_index(
    runtime: _Runtime,
    path: Path,
    *,
    generation: int,
    normalization_version: int,
    documents: Iterable[SQLiteConversationSearchIndexDocument],
) -> None:
    """Create one exact companion database in an already-owned private file."""

    connection: Any | None = None
    committed = False
    try:
        connection = runtime.module.connect(path, isolation_level=None)
        _configure_connection(connection, read_only=False)
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(_CREATE_IDENTITY_SQL)
        connection.execute(_CREATE_FTS_SQL)
        connection.execute(
            "INSERT INTO conversation_search_index_identity "
            "(singleton, schema_version, source_generation, "
            "normalization_version, tokenizer, runtime_identity) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                1,
                SQLITE_CONVERSATION_SEARCH_INDEX_SCHEMA_VERSION,
                generation,
                normalization_version,
                _TOKENIZER,
                runtime.identity,
            ),
        )
        _insert_documents(connection, documents)
        _reject_duplicate_documents(connection)
        connection.execute(f"PRAGMA application_id = {_APPLICATION_ID}")
        connection.execute(
            f"PRAGMA user_version = {SQLITE_CONVERSATION_SEARCH_INDEX_SCHEMA_VERSION}"
        )
        connection.execute("COMMIT")
        committed = True
        _verify_index(
            connection,
            runtime=runtime,
            source_generation=generation,
            normalization_version=normalization_version,
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except SQLiteConversationSearchIndexError:
        raise
    except Exception:
        raise SQLiteConversationSearchIndexError() from None
    finally:
        if connection is not None:
            if not committed:
                try:
                    connection.rollback()
                except Exception:
                    pass
            try:
                connection.close()
            except Exception:
                pass


def _insert_documents(
    connection: Any,
    documents: Iterable[SQLiteConversationSearchIndexDocument],
) -> None:
    """Insert canonical documents without retaining their text."""

    for document in documents:
        if type(document) is not SQLiteConversationSearchIndexDocument:
            raise TypeError(
                "index documents must be SQLiteConversationSearchIndexDocument"
            )
        connection.execute(
            "INSERT INTO conversation_events_fts "
            "(session_id, conversation_id, sequence, searchable_content) "
            "VALUES (?, ?, ?, ?)",
            (
                document.session_id,
                document.conversation_id,
                document.sequence,
                document.searchable_content,
            ),
        )


def _reject_duplicate_documents(connection: Any) -> None:
    """Reject duplicate authoritative coordinates in one derived generation."""

    row = connection.execute(
        "SELECT 1 FROM conversation_events_fts "
        "GROUP BY session_id, conversation_id, sequence "
        "HAVING COUNT(*) != 1 LIMIT 1"
    ).fetchone()
    if row is not None:
        raise SQLiteConversationSearchIndexError()


def _verify_index(
    connection: Any,
    *,
    runtime: _Runtime,
    source_generation: int,
    normalization_version: int,
) -> None:
    """Verify the complete companion identity and requested source marker."""

    _verify_index_schema(connection, runtime=runtime)
    rows = connection.execute(
        "SELECT singleton, schema_version, source_generation, "
        "normalization_version, tokenizer, runtime_identity "
        "FROM conversation_search_index_identity"
    ).fetchall()
    expected = [
        (
            1,
            SQLITE_CONVERSATION_SEARCH_INDEX_SCHEMA_VERSION,
            source_generation,
            normalization_version,
            _TOKENIZER,
            runtime.identity,
        )
    ]
    if rows != expected:
        raise SQLiteConversationSearchIndexError()
    _reject_duplicate_documents(connection)


def _verify_recognized_index(
    connection: Any,
    *,
    runtime: _Runtime,
    normalization_version: int,
) -> None:
    """Verify one complete owner generation without requiring current source."""

    _verify_index_schema(connection, runtime=runtime)
    rows = connection.execute(
        "SELECT singleton, schema_version, source_generation, "
        "normalization_version, tokenizer, runtime_identity "
        "FROM conversation_search_index_identity"
    ).fetchall()
    if len(rows) != 1 or type(rows[0]) is not tuple or len(rows[0]) != 6:
        raise SQLiteConversationSearchIndexError()
    (
        singleton,
        schema_version,
        source_generation,
        stored_normalization,
        tokenizer,
        identity,
    ) = rows[0]
    if (
        type(singleton) is not int
        or singleton != 1
        or type(schema_version) is not int
        or schema_version != SQLITE_CONVERSATION_SEARCH_INDEX_SCHEMA_VERSION
        or type(source_generation) is not int
        or source_generation < 0
        or type(stored_normalization) is not int
        or stored_normalization != normalization_version
        or type(tokenizer) is not str
        or tokenizer != _TOKENIZER
        or type(identity) is not str
        or identity != runtime.identity
    ):
        raise SQLiteConversationSearchIndexError()
    _reject_duplicate_documents(connection)


def _verify_index_schema(connection: Any, *, runtime: _Runtime) -> None:
    """Verify exact application identity, schema version, and runtime manifest."""

    application_id = connection.execute("PRAGMA application_id").fetchone()
    user_version = connection.execute("PRAGMA user_version").fetchone()
    if application_id != (_APPLICATION_ID,) or user_version != (
        SQLITE_CONVERSATION_SEARCH_INDEX_SCHEMA_VERSION,
    ):
        raise SQLiteConversationSearchIndexError()
    if _schema_manifest(connection) != runtime.manifest:
        raise SQLiteConversationSearchIndexError()


def _schema_manifest(connection: Any) -> frozenset[str]:
    """Return the whitespace-normalized exact companion schema manifest."""

    rows = connection.execute(
        "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL "
        "AND substr(name, 1, 7) != 'sqlite_' ORDER BY type, name"
    ).fetchall()
    if any(
        type(row) is not tuple or len(row) != 1 or type(row[0]) is not str
        for row in rows
    ):
        raise SQLiteConversationSearchIndexError()
    return frozenset(" ".join(row[0].split()) for row in rows)


def _connect(module: Any, path: Path, *, read_only: bool) -> Any:
    """Open one noncreating URI connection and map every DB-API failure."""

    mode = "ro" if read_only else "rw"
    try:
        return module.connect(
            f"{path.as_uri()}?mode={mode}",
            uri=True,
            timeout=0.05,
            isolation_level=None,
        )
    except Exception:
        raise SQLiteConversationSearchIndexError() from None


def _configure_connection(connection: Any, *, read_only: bool) -> None:
    """Apply the fixed companion connection policy."""

    try:
        connection.execute("PRAGMA busy_timeout = 50")
        if not read_only:
            journal = connection.execute("PRAGMA journal_mode = DELETE").fetchone()
            if (
                type(journal) is not tuple
                or len(journal) != 1
                or type(journal[0]) is not str
                or journal[0].casefold() != "delete"
            ):
                raise SQLiteConversationSearchIndexError()
            connection.execute("PRAGMA synchronous = FULL")
    except SQLiteConversationSearchIndexError:
        raise
    except Exception:
        raise SQLiteConversationSearchIndexError() from None


def _require_safe_parent(workspace_root: Path, parent: Path) -> None:
    """Require fixed existing nonsymlink workspace and companion parents."""

    for candidate in (workspace_root, parent):
        try:
            value = os.stat(candidate, follow_symlinks=False)
        except OSError:
            raise SQLiteConversationSearchIndexError() from None
        if stat.S_ISLNK(value.st_mode) or not stat.S_ISDIR(value.st_mode):
            raise SQLiteConversationSearchIndexError()


def _require_owned_file(
    path: Path,
    *,
    expected_link_count: int = 1,
) -> tuple[int, int]:
    """Return one exact regular, private, singly linked file identity."""

    try:
        value = os.stat(path, follow_symlinks=False)
    except OSError:
        raise SQLiteConversationSearchIndexError() from None
    if (
        stat.S_ISLNK(value.st_mode)
        or not stat.S_ISREG(value.st_mode)
        or value.st_nlink != expected_link_count
        or value.st_dev <= 0
        or value.st_ino <= 0
        or value.st_mode & 0o077
    ):
        raise SQLiteConversationSearchIndexError()
    if hasattr(os, "geteuid") and value.st_uid != os.geteuid():
        raise SQLiteConversationSearchIndexError()
    return (value.st_dev, value.st_ino)


def _path_is_missing(path: Path) -> bool:
    """Report exact path absence without accepting another unsafe shape."""

    try:
        os.stat(path, follow_symlinks=False)
    except FileNotFoundError:
        return True
    except OSError:
        raise SQLiteConversationSearchIndexError() from None
    return False


def _reject_sidecars(path: Path) -> None:
    """Reject every possible journal/WAL/shared-memory companion sidecar."""

    for suffix in _SIDECAR_SUFFIXES:
        candidate = Path(f"{path}{suffix}")
        try:
            os.stat(candidate, follow_symlinks=False)
        except FileNotFoundError:
            continue
        except OSError:
            raise SQLiteConversationSearchIndexError() from None
        raise SQLiteConversationSearchIndexError()


def _matching_temporary_alias(
    path: Path,
    *,
    identity: tuple[int, int],
) -> Path:
    """Return the sole exact owner-pattern hard-link alias for ``path``."""

    matches: list[Path] = []
    try:
        with os.scandir(path.parent) as entries:
            for entry in entries:
                if _TEMPORARY_NAME.fullmatch(entry.name) is None:
                    continue
                candidate = Path(entry.path)
                try:
                    value = entry.stat(follow_symlinks=False)
                except OSError:
                    raise SQLiteConversationSearchIndexError() from None
                if (
                    stat.S_ISREG(value.st_mode)
                    and not stat.S_ISLNK(value.st_mode)
                    and value.st_nlink == 2
                    and (value.st_dev, value.st_ino) == identity
                    and value.st_mode & 0o077 == 0
                    and (not hasattr(os, "geteuid") or value.st_uid == os.geteuid())
                ):
                    matches.append(candidate)
    except SQLiteConversationSearchIndexError:
        raise
    except OSError:
        raise SQLiteConversationSearchIndexError() from None
    if len(matches) != 1:
        raise SQLiteConversationSearchIndexError()
    return matches[0]


def _unlink_unchanged_temporary(
    path: Path,
    identity: tuple[int, int],
) -> None:
    """Best-effort remove only this call's unchanged sidecar-free private file."""

    try:
        if _require_owned_file(path) != identity:
            return
        _reject_sidecars(path)
        if _require_owned_file(path) != identity:
            return
        path.unlink()
    except (OSError, SQLiteConversationSearchIndexError):
        return


def _synchronize_file(
    path: Path,
    *,
    expected_identity: tuple[int, int],
) -> None:
    """Synchronize one completed companion generation."""

    descriptor: int | None = None
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        value = os.fstat(descriptor)
        if (
            not stat.S_ISREG(value.st_mode)
            or value.st_nlink != 1
            or (value.st_dev, value.st_ino) != expected_identity
        ):
            raise SQLiteConversationSearchIndexError()
        os.fsync(descriptor)
    except SQLiteConversationSearchIndexError:
        raise
    except OSError:
        raise SQLiteConversationSearchIndexError() from None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _synchronize_directory(path: Path) -> None:
    """Synchronize the fixed companion directory when supported."""

    descriptor: int | None = None
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise SQLiteConversationSearchIndexError()
        os.fsync(descriptor)
    except SQLiteConversationSearchIndexError:
        raise
    except OSError:
        raise SQLiteConversationSearchIndexError() from None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


__all__ = [
    "SQLITE_CONVERSATION_SEARCH_INDEX_PATH",
    "SQLITE_CONVERSATION_SEARCH_INDEX_SCHEMA_VERSION",
    "SQLiteConversationSearchIndex",
    "SQLiteConversationSearchIndexDocument",
    "SQLiteConversationSearchIndexError",
]
