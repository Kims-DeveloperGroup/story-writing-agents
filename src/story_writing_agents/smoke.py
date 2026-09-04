"""Provider-free workspace and collaboration-contract smoke workflow."""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path
from typing import Any

from .agents import EXECUTION_SPECIALIST
from .conversation_search import (
    CollaborationHistorySearchQuery,
    CollaborationHistorySearchScope,
    CollaborationHistoryService,
)
from .conversation_storage.contracts import (
    ConversationStorageBackend,
    ConversationStorageServices,
)
from .conversations import (
    ConversationMetadata,
    ConversationStatus,
    select_bounded_collaboration_context,
)
from .debug_log import (
    DebugComponent,
    DebugOperation,
    DebugOutcome,
    DebugPhase,
    WorkspaceDebugLog,
)
from .orchestrator import StoryRuntime
from .workspace import Workspace, initialize_workspace


_SUBJECT = "Smoke collaboration contract"
_NOTE = "No LLM or external provider was called."
_CHECK_NAMES = (
    "workspace_initialized",
    "directive_persisted",
    "conversation_catalog_persisted",
    "collaboration_history_empty",
    "collaboration_readable_context_empty",
    "collaboration_search_empty",
    "collaboration_page_empty",
    "execution_artifacts_unchanged",
)


class _SmokeInvariantError(Exception):
    """Name one deterministic smoke invariant without hiding other failures."""

    def __init__(self, failed_check: str) -> None:
        super().__init__(failed_check)
        self.failed_check = failed_check


def _trace_smoke(
    debug_log: WorkspaceDebugLog | None,
    operation: DebugOperation,
    phase: DebugPhase,
    *,
    session_id: str | None = None,
    outcome: DebugOutcome | None = None,
) -> None:
    """Record one smoke trace without allowing diagnostics to alter behavior."""

    if debug_log is None:
        return
    try:
        debug_log.trace(
            component=DebugComponent.SMOKE,
            operation=operation,
            phase=phase,
            session_id=session_id,
            outcome=outcome,
        )
    except BaseException:
        pass


def _create_smoke_debug_log(
    workspace: Workspace,
) -> WorkspaceDebugLog | None:
    """Create one optional smoke logger without changing smoke behavior."""

    try:
        return WorkspaceDebugLog(workspace)
    except BaseException:
        return None


def run_smoke(workspace: Workspace | None = None) -> tuple[dict[str, Any], int]:
    """Run one provider-free smoke and return its report plus exit code.

    An omitted workspace creates and removes a temporary workspace; a supplied
    initialized workspace is borrowed and retained. Named invariant failures
    return code one, while workspace, storage, and setup failures propagate.
    """

    if workspace is None:
        with tempfile.TemporaryDirectory(
            prefix="story-writing-agents-smoke-"
        ) as temporary:
            temporary_workspace = initialize_workspace(temporary)
            return _run_provider_free_smoke_in_workspace(
                temporary_workspace,
                mode="temporary",
                retained=False,
            )
    if not isinstance(workspace, Workspace):
        raise TypeError("workspace must be a Workspace or None")
    return _run_read_only_smoke(workspace)


def _run_read_only_smoke(workspace: Workspace) -> tuple[dict[str, Any], int]:
    """Verify an explicit clean v2 root without creating diagnostic state."""

    Workspace(workspace.root).require_ready()
    return (
        _report(
            workspace=workspace,
            mode="persistent",
            retained=True,
            session_id=None,
            conversation_id=None,
            status="passed",
            checks=[_CHECK_NAMES[0], _CHECK_NAMES[7]],
        ),
        0,
    )


def _run_provider_free_smoke_in_workspace(
    workspace: Workspace,
    *,
    mode: str,
    retained: bool,
) -> tuple[dict[str, Any], int]:
    """Verify workspace, management, catalog, and pure schema-v2 boundaries."""

    debug_log = _create_smoke_debug_log(workspace)
    _trace_smoke(
        debug_log,
        DebugOperation.WORKSPACE_INITIALIZATION,
        DebugPhase.SUCCEEDED,
        outcome=DebugOutcome.READY,
    )
    session_id = f"smoke_session_{uuid.uuid4().hex}"
    completed_checks: list[str] = []
    _trace_smoke(
        debug_log,
        DebugOperation.CLI_COMMAND,
        DebugPhase.STARTED,
        session_id=session_id,
    )
    execution_before = _snapshot_execution_artifacts(workspace)

    try:
        _verify_workspace_initialized(workspace)
        completed_checks.append(_CHECK_NAMES[0])

        StoryRuntime(workspace).apply_directive(session_id, "[SYS: LANG=EN]")
        fresh_runtime = StoryRuntime(workspace)
        _verify_directive(fresh_runtime, session_id)
        completed_checks.append(_CHECK_NAMES[1])

        metadata = _verify_catalog(
            fresh_runtime.conversation_storage_services,
            session_id,
        )
        completed_checks.append(_CHECK_NAMES[2])

        active_runtime = StoryRuntime(workspace)
        services = active_runtime.conversation_storage_services
        _require(
            services.backend is ConversationStorageBackend.FILESYSTEM,
            _CHECK_NAMES[3],
        )
        events = services.event_store.read(session_id, metadata.conversation_id)
        history = services.event_store.read_context(
            session_id,
            metadata.conversation_id,
        )
        _require(
            events == ()
            and history.session_id is None
            and history.conversation_id is None
            and history.last_sequence == 0
            and history.current_turn_id is None
            and history.current_step_id is None
            and history.current_proposal is None
            and history.current_state is None,
            _CHECK_NAMES[3],
        )
        completed_checks.append(_CHECK_NAMES[3])

        context = select_bounded_collaboration_context(events)
        _require(
            context.records == ()
            and context.bootstrap.session_id is None
            and context.bootstrap.conversation_id is None
            and context.bootstrap.total_records == 0
            and context.bootstrap.last_sequence == 0
            and context.bootstrap.tail_batch_digest is None
            and context.requested_records == 0
            and context.retained_records == 0
            and context.omitted_records == 0
            and context.retained_utf8_bytes == 0
            and context.truncated is False,
            _CHECK_NAMES[4],
        )
        completed_checks.append(_CHECK_NAMES[4])

        history_service = CollaborationHistoryService(
            CollaborationHistorySearchScope(session_id),
            conversation_storage_services=services,
        )
        query = CollaborationHistorySearchQuery("smoke")
        search = history_service.search(query)
        _require(
            search.query == query
            and search.hits == ()
            and search.matched_fields == 0
            and search.scanned_records == 0
            and search.scanned_fields == 0
            and search.scan_truncated is False
            and search.truncated is False,
            _CHECK_NAMES[5],
        )
        completed_checks.append(_CHECK_NAMES[5])

        page = history_service.read_history(metadata.conversation_id)
        _require(
            page.session_id == session_id
            and page.conversation_id == metadata.conversation_id
            and page.records == ()
            and page.requested_start_sequence == 1
            and page.requested_end_sequence == 0
            and page.snapshot_last_sequence == 0
            and page.snapshot_tail_batch_digest is None
            and page.returned_utf8_bytes == 0
            and page.next_cursor is None
            and page.truncated is False,
            _CHECK_NAMES[6],
        )
        completed_checks.append(_CHECK_NAMES[6])

        _require(
            fresh_runtime.inspect_session(session_id)["debates"] == []
            and _snapshot_execution_artifacts(workspace) == execution_before,
            _CHECK_NAMES[7],
        )
        completed_checks.append(_CHECK_NAMES[7])
    except _SmokeInvariantError as exc:
        if debug_log is not None:
            try:
                debug_log.error(
                    DebugComponent.SMOKE,
                    DebugOperation.CLI_COMMAND,
                    exc,
                    session_id=session_id,
                    outcome=DebugOutcome.FAILED,
                )
            except BaseException:
                pass
        return (
            _report(
                workspace=workspace,
                mode=mode,
                retained=retained,
                session_id=session_id,
                conversation_id=None,
                status="failed",
                checks=completed_checks,
                failed_check=exc.failed_check,
            ),
            1,
        )
    except BaseException as exc:
        if debug_log is not None:
            try:
                interrupted = isinstance(exc, (KeyboardInterrupt, SystemExit))
                debug_log.error(
                    DebugComponent.SMOKE,
                    DebugOperation.CLI_COMMAND,
                    exc,
                    phase=(DebugPhase.CANCELLED if interrupted else DebugPhase.FAILED),
                    session_id=session_id,
                    outcome=(
                        DebugOutcome.INTERRUPTED if interrupted else DebugOutcome.FAILED
                    ),
                )
            except BaseException:
                pass
        raise

    _trace_smoke(
        debug_log,
        DebugOperation.CLI_COMMAND,
        DebugPhase.SUCCEEDED,
        session_id=session_id,
        outcome=DebugOutcome.COMPLETED,
    )
    return (
        _report(
            workspace=workspace,
            mode=mode,
            retained=retained,
            session_id=session_id,
            conversation_id=metadata.conversation_id,
            status="passed",
            checks=completed_checks,
        ),
        0,
    )


def _verify_workspace_initialized(workspace: Workspace) -> None:
    """Require the supplied instance's exact v2 readiness lease."""

    workspace.require_ready()


def _verify_directive(runtime: StoryRuntime, session_id: str) -> None:
    """Validate that one directive remains visible through fresh management."""

    overrides = runtime.workspace.load_session_overrides(session_id)
    resolved = runtime.config.resolve(EXECUTION_SPECIALIST, overrides)
    _require(
        overrides.get("language") == "EN" and resolved.language == "EN",
        _CHECK_NAMES[1],
    )


def _verify_catalog(
    services: ConversationStorageServices,
    session_id: str,
) -> ConversationMetadata:
    """Create and reread metadata through the active selected service."""

    if type(services) is not ConversationStorageServices:
        raise _SmokeInvariantError(_CHECK_NAMES[2])
    catalog = services.catalog
    metadata = catalog.create(session_id, _SUBJECT)
    listed = catalog.list(session_id)
    _require(
        type(metadata) is ConversationMetadata
        and metadata.session_id == session_id
        and metadata.subject == _SUBJECT
        and metadata.status is ConversationStatus.ACTIVE
        and catalog.get(session_id, metadata.conversation_id) == metadata
        and listed == (metadata,),
        _CHECK_NAMES[2],
    )
    return metadata


def _snapshot_execution_artifacts(
    workspace: Workspace,
) -> tuple[tuple[str, bytes], ...]:
    """Return exact bytes for every execution-owned artifact class."""

    root = workspace.root
    selected: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.name == ".gitkeep":
            continue
        relative = path.relative_to(root)
        parts = relative.parts
        if (
            path.name in {"transcript.jsonl", "collaboration-history.jsonl"}
            or "debates" in parts
            or "provider_sessions" in parts
            or parts[:1] == ("manuscript",)
            or parts[:2]
            in {
                ("agent_states", "plot_architect_state"),
                ("agent_states", "character_custodian_state"),
                ("agent_states", "execution_specialist_state"),
            }
            or parts[:2] == ("debug_logs", "agent_exchanges")
        ):
            selected.append(path)
    return tuple(
        (path.relative_to(root).as_posix(), path.read_bytes())
        for path in sorted(selected)
    )


def _require(condition: bool, failed_check: str) -> None:
    """Return when one invariant holds or raise its stable check name."""

    if not condition:
        raise _SmokeInvariantError(failed_check)


def _report(
    *,
    workspace: Workspace,
    mode: str,
    retained: bool,
    session_id: str | None,
    conversation_id: str | None,
    status: str,
    checks: list[str],
    failed_check: str | None = None,
) -> dict[str, Any]:
    """Return the content-free provider-free smoke report."""

    report: dict[str, Any] = {
        "command": "smoke",
        "status": status,
        "provider_tested": False,
        "workspace": {
            "mode": mode,
            "path": str(workspace.root),
            "retained": retained,
        },
        "session_id": session_id,
        "conversation_id": conversation_id,
        "checks": list(checks),
        "note": _NOTE,
    }
    if failed_check is not None:
        report["failed_check"] = failed_check
    return report
