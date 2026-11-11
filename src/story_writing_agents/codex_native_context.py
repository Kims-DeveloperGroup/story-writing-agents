"""Bounded native witness and usage observation for Codex context calls."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time
from urllib.parse import quote
import xml.etree.ElementTree as ElementTree

from .agent_context import ProviderUsage, canonical_provider_json
from .blocking_work import run_blocking
from .provider_adapter import (
    NativeContextCheck,
    NativeContextCheckReason,
    NativeContextCheckStage,
    NativeContextObservation,
    NativeContextSourceStage,
    ProviderContextWitness,
    ProviderInvocation,
)


CONTEXT_WITNESS_BYTES = 8 * 1024 * 1024
CONTEXT_WITNESS_LINES = 20000


def _native_file_facts(path: Path, *, directory: bool = False):
    value = path.lstat()
    if (not (stat.S_ISDIR if directory else stat.S_ISREG)(value.st_mode)
            or value.st_uid != os.geteuid() or stat.S_IMODE(value.st_mode) & 0o022):
        raise _NativeContextUnavailable(NativeContextCheckReason.SOURCE_UNSAFE)
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns


class _NativeContextUnavailable(Exception):
    def __init__(self, reason):
        super().__init__(reason.value)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class _CodexNativeObservation:
    witness: ProviderContextWitness | None
    check: NativeContextCheck
    usage_pair: tuple[ProviderUsage, ProviderUsage] | None = None
    previous_usage: ProviderUsage | None = None
    first_native_turn: bool = False
    cause: BaseException | None = None
    source_path: Path | None = field(default=None, repr=False)
    rediscovered: bool = False
    last_token_usage: ProviderUsage | None = None


def _native_observation_success(invocation, previous, before, witness,
                                usage_pair=None, previous_usage=None, first=False,
                                last_token_usage=None):
    check = NativeContextCheck(invocation.context_call_id,
        previous.call_id if type(previous) is ProviderContextWitness else None,
        (NativeContextObservation(NativeContextCheckStage.PRE_SUBMIT if before else NativeContextCheckStage.POST_COMPLETE,
            NativeContextSourceStage.COMPLETE, NativeContextCheckReason.VERIFIED),))
    return _CodexNativeObservation(witness, check, usage_pair, previous_usage, first,
                                   last_token_usage=last_token_usage)


def _native_observation_failure(invocation, previous, before, point, reason, cause):
    original = cause.__cause__ if cause.__cause__ is not None else cause
    exception_type = (None if type(original) is _NativeContextUnavailable else
                      re.sub(r"[^A-Za-z0-9_]", "_", type(original).__name__)[:80] or "Exception")
    code = getattr(original, "sqlite_errorcode", getattr(original, "errno", None))
    code = code if type(code) is int else None
    check = NativeContextCheck(invocation.context_call_id,
        previous.call_id if type(previous) is ProviderContextWitness else None,
        (NativeContextObservation(NativeContextCheckStage.PRE_SUBMIT if before else NativeContextCheckStage.POST_COMPLETE,
            point, reason, exception_type, code),))
    return _CodexNativeObservation(None, check, cause=cause)


def _codex_rollout_candidate(root: Path, thread_id: str) -> Path:
    """Discover one thread path; unrelated native writes do not invalidate it."""
    database = root / "state_5.sqlite"
    observed = _native_file_facts(database)
    if observed[2] > 256 * 1024 * 1024:
        raise _NativeContextUnavailable(NativeContextCheckReason.DATABASE_LIMIT)
    descriptor = os.open(database, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        opened = os.fstat(descriptor)
        if (not stat.S_ISREG(opened.st_mode) or opened.st_uid != os.geteuid()
                or stat.S_IMODE(opened.st_mode) & 0o022):
            raise _NativeContextUnavailable(NativeContextCheckReason.SOURCE_UNSAFE)
        if opened.st_size > 256 * 1024 * 1024:
            raise _NativeContextUnavailable(NativeContextCheckReason.DATABASE_LIMIT)
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            header = stream.read(100)
    finally:
        os.close(descriptor)
    if len(header) != 100 or header[:16] != b"SQLite format 3\x00":
        raise _NativeContextUnavailable(NativeContextCheckReason.DATABASE_HEADER_INVALID)
    if header[18:20] == b"\x02\x02":
        for suffix in ("-wal", "-shm"):
            sidecar = database.with_name(database.name + suffix)
            if not sidecar.exists():
                raise _NativeContextUnavailable(NativeContextCheckReason.WAL_NOT_VISIBLE)
            _native_file_facts(sidecar)
    try:
        import sqlite3
    except ImportError as failure:
        raise _NativeContextUnavailable(NativeContextCheckReason.DATABASE_MODULE_UNAVAILABLE) from failure
    try:
        connection = sqlite3.connect("file:" + quote(str(database)) + "?mode=ro", uri=True, timeout=0.05)
        try:
            connection.execute("PRAGMA query_only=ON")
            connection.set_progress_handler(lambda: 1, 10000)
            connection.execute("BEGIN")
            columns = {row[1]: row for row in connection.execute("PRAGMA table_info(threads)")}
            if (not {"id", "rollout_path", "archived"} <= columns.keys()
                    or columns["id"][5] != 1
                    or sum(bool(column[5]) for column in columns.values()) != 1
                    or any(columns[key][2].upper() != kind for key, kind in
                           (("id", "TEXT"), ("rollout_path", "TEXT"), ("archived", "INTEGER")))):
                raise _NativeContextUnavailable(NativeContextCheckReason.DATABASE_SCHEMA_UNSUPPORTED)
            rows = connection.execute(
                "SELECT rollout_path FROM threads WHERE id = ? AND archived = 0 LIMIT 2", (thread_id,)).fetchall()
        finally:
            connection.close()
    except sqlite3.Error as failure:
        code = getattr(failure, "sqlite_errorcode", None)
        reason = (NativeContextCheckReason.DATABASE_BUSY
                  if type(code) is int and code & 255 in {5, 6}
                  else NativeContextCheckReason.DATABASE_QUERY_FAILED)
        raise _NativeContextUnavailable(reason) from failure
    _native_file_facts(database)
    if not rows:
        raise _NativeContextUnavailable(NativeContextCheckReason.THREAD_NOT_VISIBLE)
    if len(rows) != 1 or type(rows[0][0]) is not str:
        raise _NativeContextUnavailable(NativeContextCheckReason.THREAD_RECORD_INVALID)
    return Path(rows[0][0])


@dataclass(frozen=True, slots=True)
class _CodexNativeInspectionInput:
    """Immutable scan inputs with no workspace capability or terminal sink."""

    session_id: str
    context_call_id: str
    payload_json: bytes
    worktree_root: Path
    generation_provenance: str


def _native_environment_prelude(text: str, worktree_root: Path) -> bool:
    """Recognize native metadata without accepting a second business request."""
    text = text.strip()
    if not text.startswith("<environment_context>") or not text.endswith("</environment_context>") or "<!" in text:
        return False
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError:
        return False
    children = list(root)
    names = tuple(child.tag for child in children)
    if (root.tag != "environment_context" or root.attrib
            or (root.text or "").strip() or (root.tail or "").strip()
            or len(set(names)) != len(names) or "cwd" not in names
            or not set(names) <= {"cwd", "shell", "current_date", "timezone", "filesystem"}):
        return False
    for child in children:
        if (child.tail or "").strip():
            return False
        if child.tag == "filesystem":
            allowed = {
                "filesystem": ({"workspace_roots", "permission_profile"}, set()),
                "workspace_roots": ({"root"}, set()),
                "permission_profile": ({"file_system"}, {"type"}),
                "file_system": ({"entry"}, {"type"}),
                "entry": ({"path", "special"}, {"access"}),
                "root": (set(), set()), "path": (set(), set()), "special": (set(), set()),
            }
            for node in child.iter():
                if node.tag not in allowed:
                    return False
                tags, attributes = allowed[node.tag]
                if (not set(node.attrib) <= attributes or not {item.tag for item in node} <= tags
                        or (node.tail or "").strip()):
                    return False
                if list(node):
                    if (node.text or "").strip():
                        return False
                elif node.tag in {"root", "path"}:
                    if not Path(node.text or "").is_absolute():
                        return False
                elif node.tag == "special":
                    if node.text != ":root":
                        return False
                elif (node.text or "").strip():
                    return False
                if any(re.fullmatch(r"[A-Za-z_-]+", value) is None for value in node.attrib.values()):
                    return False
            continue
        if child.attrib or list(child) or not child.text:
            return False
        if child.tag == "cwd":
            if (not Path(child.text).is_absolute()
                    or os.path.realpath(child.text) != os.path.realpath(worktree_root)):
                return False
        elif child.tag == "shell":
            if re.fullmatch(r"[A-Za-z0-9_./-]+", child.text) is None:
                return False
        elif child.tag == "current_date":
            if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", child.text) is None:
                return False
        elif re.fullmatch(r"[A-Za-z0-9_+./:-]+", child.text) is None:
            return False
    return True


def _prepare_codex_native_inspection(invocation, thread_id, *, previous, anchor, before):
    point = NativeContextSourceStage.INPUT
    path, rediscovered = None, False
    try:
        if (type(thread_id) is not str or not thread_id
                or previous is not None and type(previous) is not ProviderContextWitness
                or anchor is not None and type(anchor) is not ProviderContextWitness):
            raise _NativeContextUnavailable(NativeContextCheckReason.INPUT_INVALID)
        if (previous is None) != (anchor is None) or before and previous is None:
            raise _NativeContextUnavailable(NativeContextCheckReason.ANCHOR_MISSING)
        workspace = invocation.execution_workspace
        if workspace is None or invocation.context_call_id is None or invocation.payload_json is None:
            raise _NativeContextUnavailable(NativeContextCheckReason.WORKSPACE_UNAVAILABLE)
        selection = invocation.payload.get("response_contract", {}).get("schema_selection", {})
        if selection.get("issued_contract", {}).get("call_id") != invocation.context_call_id:
            raise _NativeContextUnavailable(NativeContextCheckReason.CALL_MISMATCH)
        point = NativeContextSourceStage.WORKSPACE
        workspace.require_current()
        return _CodexNativeInspectionInput(invocation.session_id, invocation.context_call_id,
            invocation.payload_json, workspace.worktree_root, workspace.generation_provenance)
    except _NativeContextUnavailable as failure:
        return replace(_native_observation_failure(invocation, previous, before, point, failure.reason, failure), source_path=path, rediscovered=rediscovered)
    except (OSError, ValueError, TypeError, KeyError, RecursionError) as failure:
        reason = NativeContextCheckReason.SOURCE_IO_UNAVAILABLE if isinstance(failure, OSError) else NativeContextCheckReason.INSPECTION_FAILED
        if isinstance(failure, OSError) and failure.errno == 2:
            reason = NativeContextCheckReason.THREAD_NOT_VISIBLE if point is NativeContextSourceStage.DATABASE else NativeContextCheckReason.ROLLOUT_NOT_VISIBLE
        return replace(_native_observation_failure(invocation, previous, before, point, reason, failure), source_path=path, rediscovered=rediscovered)
    except Exception as failure:
        return replace(_native_observation_failure(invocation, previous, before,
            NativeContextSourceStage.UNKNOWN, NativeContextCheckReason.INSPECTION_FAILED, failure),
            source_path=path, rediscovered=rediscovered)


def read_codex_native_observation(
    native_state: Path, facts: _CodexNativeInspectionInput, thread_id: str, *,
    previous: ProviderContextWitness | None = None, anchor: ProviderContextWitness | None = None,
    before: bool = False,
    rollout_path: Path | None = None, allow_discovery: bool = True,
) -> _CodexNativeObservation:
    """Read and interpret native records without accessing live authority."""
    point = NativeContextSourceStage.WORKSPACE
    path, rediscovered = None, False
    try:
        root = Path(os.path.realpath(native_state))
        if root != native_state or not root.is_absolute():
            raise _NativeContextUnavailable(NativeContextCheckReason.ROOT_INVALID)
        _native_file_facts(root, directory=True)
        scope = hashlib.sha256(canonical_provider_json({"session_id": facts.session_id,
            "cwd": str(facts.worktree_root), "generation": facts.generation_provenance})).hexdigest()
        if previous is not None and (previous.thread_id != thread_id or previous.scope_digest != scope):
            raise _NativeContextUnavailable(NativeContextCheckReason.SCOPE_MISMATCH)
        if previous is not None and not previous.has_lineage_anchor(anchor):
            raise _NativeContextUnavailable(NativeContextCheckReason.ANCHOR_MISMATCH)
        point = NativeContextSourceStage.DATABASE
        path = rollout_path if rollout_path is not None else (
            previous.rollout_path if previous is not None else _codex_rollout_candidate(root, thread_id))
        if previous is not None and rollout_path is None and not path.exists():
            if not allow_discovery:
                raise _NativeContextUnavailable(NativeContextCheckReason.ROLLOUT_NOT_VISIBLE)
            rediscovered = True
            path = _codex_rollout_candidate(root, thread_id)
        point = NativeContextSourceStage.ROLLOUT
        if path is None:
            raise _NativeContextUnavailable(NativeContextCheckReason.ROLLOUT_NOT_VISIBLE)
        if not path.is_absolute() or path != Path(os.path.realpath(path)):
            raise _NativeContextUnavailable(NativeContextCheckReason.ROLLOUT_PATH_INVALID)
        relative = path.relative_to(root / "sessions")
        parent = root / "sessions"
        _native_file_facts(parent, directory=True)
        for part in relative.parts[:-1]:
            parent = parent / part
            _native_file_facts(parent, directory=True)
        observed = _native_file_facts(path)
        if observed[2] > CONTEXT_WITNESS_BYTES:
            raise _NativeContextUnavailable(NativeContextCheckReason.ROLLOUT_LIMIT)
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        try:
            opened = os.fstat(descriptor)
            if (not stat.S_ISREG(opened.st_mode) or opened.st_uid != os.geteuid()
                    or stat.S_IMODE(opened.st_mode) & 0o022):
                raise _NativeContextUnavailable(NativeContextCheckReason.SOURCE_UNSAFE)
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                raw = stream.read(CONTEXT_WITNESS_BYTES + 1)
        finally:
            os.close(descriptor)
        if len(raw) > CONTEXT_WITNESS_BYTES:
            raise _NativeContextUnavailable(NativeContextCheckReason.ROLLOUT_LIMIT)
        if not raw.endswith(b"\n"):
            raise _NativeContextUnavailable(NativeContextCheckReason.ROLLOUT_FLUSH_PENDING)
        lines = raw.splitlines()
        if len(lines) > CONTEXT_WITNESS_LINES:
            raise _NativeContextUnavailable(NativeContextCheckReason.RECORD_LIMIT)
        point = NativeContextSourceStage.RECORDS
        metadata, active_turn = None, None
        completed, inputs, settings, usage_records, history_effects = set(), [], {}, [], []
        last_usage_by_turn: dict[str | None, ProviderUsage | None] = {}
        environment_inputs = []
        for position, line in enumerate(lines):
            record = json.loads(line)
            if type(record) is not dict:
                raise _NativeContextUnavailable(NativeContextCheckReason.RECORD_INVALID)
            kind, value = record.get("type"), record.get("payload")
            if kind not in {"session_meta", "event_msg", "response_item", "turn_context"}:
                if kind == "compacted":
                    history_effects.append(position)
                continue
            if type(value) is not dict:
                raise _NativeContextUnavailable(NativeContextCheckReason.RECORD_INVALID)
            if kind == "session_meta":
                if value.get("id") is None or type(value.get("cwd")) is not str:
                    raise _NativeContextUnavailable(NativeContextCheckReason.METADATA_MISMATCH)
                if value["id"] != thread_id:
                    raise _NativeContextUnavailable(NativeContextCheckReason.THREAD_MISMATCH)
                if (not Path(value["cwd"]).is_absolute()
                        or Path(os.path.realpath(value["cwd"])) != Path(os.path.realpath(facts.worktree_root))):
                    raise _NativeContextUnavailable(NativeContextCheckReason.SCOPE_MISMATCH)
                metadata = value
                if any(value.get(key) is not None for key in
                       ("history_base", "forked_from_id", "parent_thread_id", "forked_from_ordinal_exclusive")):
                    history_effects.append(position)
            elif kind == "event_msg":
                event = value.get("type")
                if event == "task_started":
                    if active_turn is not None and active_turn not in completed:
                        raise _NativeContextUnavailable(NativeContextCheckReason.CONCURRENT_TURN)
                    active_turn = value.get("turn_id")
                    if type(active_turn) is not str or not active_turn or active_turn in completed:
                        raise _NativeContextUnavailable(NativeContextCheckReason.TURN_ORDER_MISMATCH)
                elif event == "task_complete":
                    completed.add(value.get("turn_id"))
                elif event == "token_count":
                    info = value.get("info")
                    total = _native_usage(info.get("total_token_usage")) if type(info) is dict else None
                    last = _native_usage(info.get("last_token_usage")) if type(info) is dict else None
                    # A malformed latest observation supersedes earlier valid counters.
                    last_usage_by_turn[active_turn] = last
                    if total is not None and last is not None:
                        usage_records.append((active_turn, total, last))
                elif event == "thread_rolled_back":
                    history_effects.append(position)
            elif kind == "turn_context":
                settings[value.get("turn_id") or active_turn] = hashlib.sha256(canonical_provider_json(
                    {key: value.get(key) for key in ("model", "comp_hash", "multi_agent_version")})).hexdigest()
            elif value.get("type") == "message" and value.get("role") == "user":
                content = value.get("content")
                if (type(content) is not list or len(content) != 1
                        or type(content[0]) is not dict or type(content[0].get("text")) is not str
                        or type(active_turn) is not str):
                    raise _NativeContextUnavailable(NativeContextCheckReason.RECORD_INVALID)
                row = (active_turn, hashlib.sha256(content[0]["text"].encode()).hexdigest(), position)
                if _native_environment_prelude(content[0]["text"], facts.worktree_root):
                    environment_inputs.append(row)
                else:
                    inputs.append(row)
        if metadata is None:
            raise _NativeContextUnavailable(NativeContextCheckReason.METADATA_MISMATCH)
        if environment_inputs and (len(environment_inputs) != 1 or not inputs
                or environment_inputs[0][0] != inputs[0][0]
                or environment_inputs[0][2] >= inputs[0][2]):
            raise _NativeContextUnavailable(NativeContextCheckReason.REQUEST_MATCH_INVALID)

        def require_input(turn_id, payload_hash):
            matches = [row for row in inputs if row[0] == turn_id]
            if not matches:
                raise _NativeContextUnavailable(NativeContextCheckReason.REQUEST_NOT_VISIBLE)
            if len(matches) != 1:
                raise _NativeContextUnavailable(NativeContextCheckReason.REQUEST_MATCH_INVALID)
            if matches[0][1] != payload_hash:
                raise _NativeContextUnavailable(NativeContextCheckReason.INPUT_CHANGED)
            if turn_id not in completed:
                raise _NativeContextUnavailable(NativeContextCheckReason.TURN_FLUSH_PENDING)
            return matches[0]

        expected = hashlib.sha256(facts.payload_json).hexdigest()
        if before:
            target = require_input(previous.turn_id, previous.payload_sha256)
        else:
            matches = [row for row in inputs if row[1] == expected]
            if not matches:
                raise _NativeContextUnavailable(NativeContextCheckReason.REQUEST_NOT_VISIBLE)
            if len(matches) != 1:
                raise _NativeContextUnavailable(NativeContextCheckReason.REQUEST_MATCH_INVALID)
            target = require_input(matches[0][0], expected)
        if target[0] != active_turn or inputs[-1] != target:
            raise _NativeContextUnavailable(NativeContextCheckReason.TURN_ORDER_MISMATCH)
        bootstrap = (require_input(previous.bootstrap_turn_id, previous.bootstrap_payload_sha256)
                     if previous is not None else target)
        lineage = [row for row in inputs if bootstrap[2] <= row[2] <= target[2]]
        if history_effects:
            raise _NativeContextUnavailable(NativeContextCheckReason.HISTORY_EFFECT_UNKNOWN)
        digest = hashlib.sha256(b"native-user-input-lineage-v1").hexdigest()
        digests = {}
        for ordinal, row in enumerate(lineage, 1):
            if row[0] not in completed:
                raise _NativeContextUnavailable(NativeContextCheckReason.TURN_FLUSH_PENDING)
            digest = hashlib.sha256(canonical_provider_json({
                "previous": digest, "turn_id": row[0], "payload_sha256": row[1]})).hexdigest()
            digests[row[0]] = (digest, ordinal)
        if previous is not None:
            prior = require_input(previous.turn_id, previous.payload_sha256)
            accepted = require_input(anchor.turn_id, anchor.payload_sha256)
            if not bootstrap[2] <= accepted[2] <= prior[2] <= target[2] or not before and prior[2] >= target[2]:
                raise _NativeContextUnavailable(NativeContextCheckReason.TURN_ORDER_MISMATCH)
            for witness in (previous, anchor):
                logical = digests.get(witness.turn_id)
                if logical is None:
                    raise _NativeContextUnavailable(NativeContextCheckReason.LINEAGE_NOT_VISIBLE)
                if logical != (witness.input_lineage_sha256, witness.input_lineage_count):
                    raise _NativeContextUnavailable(NativeContextCheckReason.LINEAGE_MISMATCH)
            if len(lineage) != previous.input_lineage_count + int(not before):
                raise _NativeContextUnavailable(NativeContextCheckReason.TURN_ORDER_MISMATCH)
        if before:
            return replace(_native_observation_success(facts, previous, before, previous), source_path=path, rediscovered=rediscovered)
        name = re.fullmatch(r"rollout-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-([0-9a-f-]{36})(?:_([0-9a-f-]{36}))?\.jsonl", path.name)
        rollout_id = (name[2] or name[1]) if name else hashlib.sha256(path.name.encode()).hexdigest()
        witness = ProviderContextWitness(thread_id, rollout_id, path, scope,
            (opened.st_dev, opened.st_ino), len(raw), hashlib.sha256(raw).hexdigest(),
            expected, facts.context_call_id, target[0],
            settings.get(target[0], hashlib.sha256(b"{}").hexdigest()),
            str(metadata.get("cli_version") or "unknown")[:80],
            bootstrap_turn_id=bootstrap[0], bootstrap_payload_sha256=bootstrap[1],
            bootstrap_call_id=facts.context_call_id if previous is None else previous.bootstrap_call_id,
            input_lineage_sha256=digest, input_lineage_count=len(lineage))
        current = [row for row in usage_records if row[0] == target[0]]
        prior_usage = [row for row in usage_records if previous is not None and row[0] == previous.turn_id]
        return replace(_native_observation_success(facts, previous, before, witness,
            current[-1][1:] if current else None, prior_usage[-1][1] if prior_usage else None, len(completed) == 1,
            last_token_usage=last_usage_by_turn.get(target[0])), source_path=path, rediscovered=rediscovered)
    except _NativeContextUnavailable as failure:
        return replace(_native_observation_failure(facts, previous, before, point, failure.reason, failure), source_path=path, rediscovered=rediscovered)
    except (OSError, ValueError, TypeError, KeyError, RecursionError) as failure:
        reason = NativeContextCheckReason.SOURCE_IO_UNAVAILABLE if isinstance(failure, OSError) else NativeContextCheckReason.INSPECTION_FAILED
        if isinstance(failure, OSError) and failure.errno == 2:
            reason = NativeContextCheckReason.THREAD_NOT_VISIBLE if point is NativeContextSourceStage.DATABASE else NativeContextCheckReason.ROLLOUT_NOT_VISIBLE
        return replace(_native_observation_failure(facts, previous, before, point, reason, failure), source_path=path, rediscovered=rediscovered)
    except Exception as failure:
        return replace(_native_observation_failure(facts, previous, before,
            NativeContextSourceStage.UNKNOWN, NativeContextCheckReason.INSPECTION_FAILED, failure),
            source_path=path, rediscovered=rediscovered)


def _finish_codex_native_inspection(invocation, previous, before, observed):
    if observed.witness is None:
        return observed
    point = NativeContextSourceStage.RECORDS
    path, rediscovered = observed.source_path, observed.rediscovered
    try:
        invocation.execution_workspace.require_current()
    except _NativeContextUnavailable as failure:
        return replace(_native_observation_failure(invocation, previous, before, point, failure.reason, failure), source_path=path, rediscovered=rediscovered)
    except (OSError, ValueError, TypeError, KeyError, RecursionError) as failure:
        reason = NativeContextCheckReason.SOURCE_IO_UNAVAILABLE if isinstance(failure, OSError) else NativeContextCheckReason.INSPECTION_FAILED
        if isinstance(failure, OSError) and failure.errno == 2:
            reason = NativeContextCheckReason.THREAD_NOT_VISIBLE if point is NativeContextSourceStage.DATABASE else NativeContextCheckReason.ROLLOUT_NOT_VISIBLE
        return replace(_native_observation_failure(invocation, previous, before, point, reason, failure), source_path=path, rediscovered=rediscovered)
    except Exception as failure:
        return replace(_native_observation_failure(invocation, previous, before,
            NativeContextSourceStage.UNKNOWN, NativeContextCheckReason.INSPECTION_FAILED, failure),
            source_path=path, rediscovered=rediscovered)
    return observed


def inspect_codex_native_observation(
    native_state: Path, invocation: ProviderInvocation, thread_id: str, *,
    previous: ProviderContextWitness | None = None, anchor: ProviderContextWitness | None = None,
    before: bool = False,
    rollout_path: Path | None = None, allow_discovery: bool = True,
) -> _CodexNativeObservation:
    """Preserve synchronous inspection with creator-thread workspace checks."""
    facts = _prepare_codex_native_inspection(invocation, thread_id,
        previous=previous, anchor=anchor, before=before)
    if type(facts) is _CodexNativeObservation:
        return facts
    observed = read_codex_native_observation(native_state, facts, thread_id, previous=previous, anchor=anchor,
        before=before, rollout_path=rollout_path, allow_discovery=allow_discovery)
    return _finish_codex_native_inspection(invocation, previous, before, observed)


async def inspect_codex_native_observation_async(
    native_state: Path, invocation: ProviderInvocation, thread_id: str, *,
    previous: ProviderContextWitness | None = None, anchor: ProviderContextWitness | None = None,
    before: bool = False,
    rollout_path: Path | None = None, allow_discovery: bool = True,
) -> _CodexNativeObservation:
    """Join a bounded scan before rechecking or releasing creator authority."""
    facts = _prepare_codex_native_inspection(invocation, thread_id,
        previous=previous, anchor=anchor, before=before)
    if type(facts) is _CodexNativeObservation:
        return facts
    try:
        observed = await run_blocking(read_codex_native_observation,
            native_state, facts, thread_id, previous=previous, anchor=anchor,
        before=before, rollout_path=rollout_path, allow_discovery=allow_discovery)
    except BaseException as failure:
        try:
            invocation.execution_workspace.require_current()
        except BaseException as revalidation_failure:
            failure.add_note("native inspection revalidation failed: "
                f"{type(revalidation_failure).__name__}")
            raise failure from revalidation_failure
        raise
    return _finish_codex_native_inspection(invocation, previous, before, observed)


def _native_usage(value: object) -> ProviderUsage | None:
    if type(value) is not dict:
        return None
    names = ("input_tokens", "output_tokens", "cached_input_tokens")
    if any(type(value.get(name)) is not int or value[name] < 0 for name in names):
        return None
    return ProviderUsage(**{name: value[name] for name in names})


_POST_COMPLETE_TRANSIENT_REASONS = frozenset({
    NativeContextCheckReason.WAL_NOT_VISIBLE,
    NativeContextCheckReason.WAL_HEADER_UNSTABLE,
    NativeContextCheckReason.WAL_CHECKSUM_UNSTABLE,
    NativeContextCheckReason.DATABASE_BUSY,
    NativeContextCheckReason.DATABASE_CHANGED,
    NativeContextCheckReason.THREAD_NOT_VISIBLE,
    NativeContextCheckReason.ROLLOUT_NOT_VISIBLE,
    NativeContextCheckReason.ROLLOUT_CHANGED,
    NativeContextCheckReason.ROLLOUT_FLUSH_PENDING,
    NativeContextCheckReason.REQUEST_NOT_VISIBLE,
    NativeContextCheckReason.LINEAGE_NOT_VISIBLE,
    NativeContextCheckReason.TURN_FLUSH_PENDING,
})


async def observe_completed_native_context(native_state, invocation, thread_id, *, deadline, precheck=None,
                                          initial_path=None, discovery_used=False):
    """At most three observations and one safe rediscovery, with no native call."""
    observations = [] if precheck is None else list(precheck.observations)
    rediscovered, rollout_path = discovery_used, initial_path
    for ordinal in range(3):
        observed = await inspect_codex_native_observation_async(native_state, invocation, thread_id,
            previous=invocation.context_witness, anchor=invocation.context_anchor,
            rollout_path=rollout_path, allow_discovery=not rediscovered)
        observations.extend(observed.check.observations)
        if observed.rediscovered:
            rediscovered, rollout_path = True, observed.source_path
        if (observed.witness is not None or observed.check.reason not in _POST_COMPLETE_TRANSIENT_REASONS
                or ordinal == 2 or time.monotonic() >= deadline):
            break
        if (invocation.context_witness is not None and not rediscovered
                and observed.check.reason in {NativeContextCheckReason.REQUEST_NOT_VISIBLE,
                                              NativeContextCheckReason.ROLLOUT_NOT_VISIBLE}):
            rediscovered = True
            try:
                rollout_path = await run_blocking(_codex_rollout_candidate, native_state, thread_id)
            except (OSError, ValueError, TypeError, KeyError, RecursionError, _NativeContextUnavailable) as failure:
                reason = (failure.reason if type(failure) is _NativeContextUnavailable else
                          NativeContextCheckReason.SOURCE_IO_UNAVAILABLE if isinstance(failure, OSError) else
                          NativeContextCheckReason.INSPECTION_FAILED)
                observed = _native_observation_failure(invocation, invocation.context_witness, False,
                    NativeContextSourceStage.DATABASE, reason, failure)
                observations.extend(observed.check.observations)
                break
        await asyncio.sleep(min(0.01, max(0.0, deadline - time.monotonic())))
        if time.monotonic() >= deadline:
            break
    return replace(observed, check=NativeContextCheck(invocation.context_call_id,
        None if invocation.context_witness is None else invocation.context_witness.call_id,
        tuple(observations)))


def inspect_codex_context_witness(
    native_state: Path, invocation: ProviderInvocation, thread_id: str, *,
    previous: ProviderContextWitness | None = None,
    anchor: ProviderContextWitness | None = None, before: bool = False,
) -> ProviderContextWitness | None:
    """Preserve the existing native continuity contract and read guards."""
    observed = inspect_codex_native_observation(native_state, invocation, thread_id,
        previous=previous, anchor=anchor, before=before)
    return observed.witness
