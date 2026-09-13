"""Stateful synchronous interactive shell coordination."""

from __future__ import annotations

import asyncio
import inspect
import sys
import uuid
from collections.abc import Callable
from dataclasses import replace
from types import TracebackType
from typing import Mapping, TypeVar

from ..agent_exchanges import (
    AgentExchangeFailure,
    AgentExchangeInventoryItem,
    AgentExchangeScopeKind,
    AgentExchangeSelection,
    AgentExchangeStorageError,
    WorkspaceAgentExchangeStore,
)
from ..agents import (
    AGENT_SPECS,
    CHARACTER_CUSTODIAN,
    EXECUTION_SPECIALIST,
    LEAD_ORCHESTRATOR,
    PLOT_ARCHITECT,
    ROSTER,
)
from ..backend import AgentBackend, backend_configuration_snapshot
from ..cli_context import (
    CliContext,
    load_default_conversation,
    save_default_conversation,
    save_default_session,
)
from ..conversation_search import (
    CONVERSATION_MEMORY_POLICY,
    CollaborationHistorySearchQuery,
    CollaborationHistorySearchScope,
    CollaborationHistoryService,
)
from ..conversation_storage.contracts import ConversationStorageServices
from ..conversation_storage.selection import (
    ConversationStorageSelectionFailure,
    select_conversation_storage,
)
from ..debug_log import (
    DebugComponent,
    DebugOperation,
    DebugOutcome,
    DebugPhase,
    WorkspaceDebugLog,
)
from ..conversations import (
    CollaborationHistoryProjection,
    CollaborationNextAction,
    CollaborationReadableFieldKey,
    CollaborationReadableRecord,
    CollaborationStepState,
    ConversationMetadata,
    ConversationStatus,
    normalize_conversation_subject,
)
from ..errors import (
    BackendProtocolError,
    ConfigurationError,
    ConversationNotFoundError,
    DirectiveError,
    SessionExistsError,
    SessionNotFoundError,
    WorkspaceFormatError,
    WorkspaceSecurityError,
    WorkspaceVersionUnsupportedError,
)
from ..interactive_auth import (
    InteractiveAuthCoordinator,
    InteractiveAuthResult,
    InteractiveAuthResultKind,
    select_default_provider,
)
from ..interactive_commands import (
    AGENT_ROUTES,
    ActionKind,
    InteractiveAction,
    parse_interactive_line,
)
from ..interactive_settings import (
    SETTINGS_LANGUAGES,
    SettingsError,
    SettingsErrorKind,
    SettingsState,
    establish_default_backend,
    establish_settings_default_backend,
    commit_prepared_settings_save,
    load_settings_state,
    prepare_settings_save,
    restore_prepared_settings_save,
    update_agent_antigravity_effort,
    update_agent_antigravity_model,
    update_agent_backend,
    update_agent_codex_model,
    update_agent_codex_reasoning_effort,
    update_conversation_rag_automatic,
    update_conversation_rag_provider,
    update_conversation_storage_backend,
    update_language,
    update_max_debate_rounds,
    update_persona,
    update_show_message_details,
)
from ..interactive_terminal import (
    AdvisoryProgressError,
    format_agent_reply_content,
    InteractiveAdvisoryProgress,
    InteractiveMainInput,
    InputPresentation,
    AttachmentKind,
    Choice,
    ConfirmationResult,
    ControlOutcome,
    ControlOutcomeKind,
    Field,
    InteractiveError,
    InteractiveErrorCode,
    InteractiveExternalViewer,
    InteractivePickerInput,
    InteractivePromptInput,
    InteractiveSemanticOutput,
    InteractiveTerminal,
    ListView,
    Message,
    Notice,
    Panel,
    Picker,
    Prompt,
    Record,
    ReferenceInputFailure,
    ReferenceInputFailureKind,
    Section,
    SemanticRole,
    SpeakerAccent,
    TerminalActionKind,
    ViewState,
)
from ..orchestrator import StoryRuntime
from ..provider_auth import ProviderAuthSnapshot
from ..provider_adapter import (
    AgentExecutionProfile,
    ExecutionValueSource,
)
from ..provider_catalog import (
    ProviderCatalogDiscovery,
    ProviderCatalogService,
    ProviderCatalogState,
    ProviderEffort,
    ProviderModel,
)
from ..session_lifecycle import (
    SessionAccessState,
    SessionDurableState,
    SessionLifecycleState,
    provider_work_is_ready,
)
from ..workspace import Workspace
from ..workspace.initialization import (
    SessionArtifactBindingError,
    compose_session_artifact_binding,
)
from ._conversation_policy import (
    ConversationCatalogProtocolFailure,
    classify_conversation_failure,
    classify_memory_catalog_or_reference_failure,
    classify_search_service_failure,
    classify_transcript_service_failure,
    normalize_conversation_input,
    resolve_conversation_reference,
    validate_catalog_entries,
    validate_catalog_metadata,
    validate_conversation_reference,
    validate_history_snapshot,
    validate_search_result_handles,
    validate_transcript_page,
    visible_conversation_handle,
)
from ._exchange_policy import (
    AgentExchangeProjectionError,
    ExchangeAgentChoice,
    ExchangeScopeChoice,
    project_exchange_agents,
    project_exchange_scopes,
)
from ._exchange_viewer import (
    AgentExchangeViewerError,
    prepare_agent_exchange_view,
)
from ._lead_policy import InteractiveLeadAdapter, project_lead_reference_scope
from ._runtime_policy import (
    DebateContextInspectionFacts,
    DebateInspectionFacts,
    classify_inspection_failure,
    validate_debate_context_inspection,
    validate_debate_inspection,
    validate_session_inspection,
)
from ._session_policy import (
    RECOVERABLE_SESSION_ERROR_CODES,
    SessionTransitionAssessment,
    SessionTransitionOperation,
    classify_session_failure,
    next_session_action,
    project_transition_assessment,
    require_correlated_state,
    require_lifecycle_state,
    require_ready_access_state,
    require_released_state,
    require_transition_assessment,
    transition_operation_is_allowed,
)


_PickerLoadT = TypeVar("_PickerLoadT")


_UNAVAILABLE_SUMMARY = "This command is not available in the session lobby."
_LOBBY_COMMAND_HINT = (
    "Use /help, /session list, /session create NAME, /session resume [NAME], "
    "/session pause, or /quit."
)
_DEBUG_LOG_NOTICE = "If available, workspace-relative: debug_logs/runtime.jsonl"
_SESSION_RESUME_COMMAND = "/session resume"
_CONVERSATION_RESUME_COMMAND = "/conversation resume"
_DIRECT_EXECUTION_UNAVAILABLE = (
    "Direct story execution is unavailable in this integration stage. "
    "Session, conversation, history, search, and inspection remain available."
)
_AGENT_SPEAKER_ACCENTS = {
    LEAD_ORCHESTRATOR: SpeakerAccent.CYAN,
    PLOT_ARCHITECT: SpeakerAccent.YELLOW,
    CHARACTER_CUSTODIAN: SpeakerAccent.GREEN,
    EXECUTION_SPECIALIST: SpeakerAccent.BLUE,
}
_CONVERSATION_CATALOG_OPERATIONS = (
    "create",
    "get",
    "list",
    "rename",
    "archive",
    "unarchive",
)
_AUTH_COORDINATOR_OPERATIONS = ("choices", "probe_all", "run")
_PROVIDER_CATALOG_OPERATIONS = ("discover",)
_SETTINGS_INHERIT_ID = "settings:inherit"
_SETTINGS_MODEL_ID_PREFIX = "settings:model:"
_SETTINGS_CURRENT_CUSTOM_MODEL_ID = "settings:model:current-custom"
_SETTINGS_CUSTOM_MODEL_ID = "settings:model:custom"
_SETTINGS_EFFORT_ID_PREFIX = "settings:effort:"
_SETTINGS_CURRENT_EFFORT_ID = "settings:effort:current-compatibility"
_SETTINGS_VALUE_SAVE_GUIDANCE = (
    "Entered changes remain pending in memory and are not saved; choose Apply "
    "Changes in Settings to persist them."
)
_SETTINGS_UNARMED = object()


_ExceptionState = tuple[BaseException, TracebackType | None]


def _effective_settings_provider(
    state: SettingsState,
    agent_id: str,
    *,
    pending: bool = True,
    provider_overrides: Mapping[str, str] | None = None,
) -> str:
    """Resolve one settings route through agent, workspace, then Codex."""

    configuration = state.pending if pending else state.baseline.configuration
    agent = configuration.agent_configs[agent_id]
    return (
        (provider_overrides or {}).get(agent_id)
        or agent.backend
        or configuration.system_defaults.default_backend
        or "codex"
    )


def _execution_source_label(source: ExecutionValueSource) -> str:
    """Return compact human-facing copy for an exact execution value source."""

    if not isinstance(source, ExecutionValueSource):
        raise TypeError("source must be an ExecutionValueSource")
    return source.value.replace("_", " ")


def _execution_value(value: str | None) -> str:
    """Render an omitted provider flag without claiming a hidden default."""

    return value if value is not None else "provider default"


def _provider_display_name(provider: str) -> str:
    """Return fixed display copy for one already-validated provider."""

    if provider == "codex":
        return "Codex"
    if provider == "antigravity":
        return "Antigravity"
    raise ValueError("provider must be codex or antigravity")


def _compact_execution_profile(profile: AgentExecutionProfile) -> str:
    """Render effective provider/model/effort values for a settings choice."""

    return (
        f"({_provider_display_name(profile.provider)}/"
        f"{_execution_value(profile.model)}/"
        f"{_execution_value(profile.effort)})"
    )


def _confirmation_role(confirmation: str) -> SemanticRole:
    """Color confirmed green and pending yellow without color-only meaning."""

    if confirmation == "confirmed":
        return SemanticRole.SUCCESS
    if confirmation == "pending":
        return SemanticRole.WARNING
    raise ValueError("confirmation must be pending or confirmed")


def _thread_status_role(status: str) -> SemanticRole:
    """Color reuse green, fresh/pending yellow, and unknown state gray."""

    if status == "reused":
        return SemanticRole.SUCCESS
    if status in {
        "fresh",
        "fresh_after_provider_switch",
        "fresh_pending",
        "reusable_pending",
    }:
        return SemanticRole.WARNING
    return SemanticRole.MUTED


def _settings_catalog_notice(
    provider: str,
    state: ProviderCatalogState,
    *,
    model_picker: bool,
) -> Notice | None:
    """Project one fixed discovery state without provider output or diagnostics."""

    if state is ProviderCatalogState.READY:
        return None
    provider_name = _provider_display_name(provider)
    state_copy = {
        ProviderCatalogState.UNAVAILABLE: "is unavailable",
        ProviderCatalogState.TIMED_OUT: "timed out",
        ProviderCatalogState.COMMAND_FAILED: "did not complete successfully",
        ProviderCatalogState.MALFORMED: "returned an unsupported format",
        ProviderCatalogState.EMPTY: "returned no visible models",
        ProviderCatalogState.OVERSIZED: "exceeded the safe output limit",
        ProviderCatalogState.FAILED: "could not be completed",
    }
    detail = state_copy.get(state, "could not be completed")
    controls = (
        "Inherit, keep the current custom value, or enter a custom model."
        if model_picker
        else "Inherit or keep the current compatibility value."
    )
    return Notice(
        f"{provider_name} catalog discovery {detail}. {controls} Retry later.",
        label="catalog discovery",
        role=SemanticRole.WARNING,
        state=ViewState.IDLE,
    )


def _settings_model_description(model: ProviderModel) -> str:
    """Render exact validated model identity plus optional safe description."""

    if model.description is None:
        return model.identity
    return f"{model.identity} — {model.description}"


def _settings_current_model_description(model: str) -> str:
    """Expose a bounded exact compatibility value when terminal-safe."""

    if not model.isspace() and len(model.encode("utf-8")) <= 256:
        return f"Exact pending model: {model}"
    return "Keep the pending model that is not listed in this catalog."


def _settings_effort_description(
    effort: ProviderEffort,
    *,
    is_default: bool,
) -> str | None:
    """Add model-default guidance without changing the provider projection."""

    if effort.description is None:
        return "Default for the selected model." if is_default else None
    if is_default:
        return f"{effort.description} Default for the selected model."
    return effort.description


def _settings_entered_notice(field: str, display_value: str) -> Notice:
    """Project one successful value result into the reopened editor."""

    return Notice(
        f"Pending {field} entered: {display_value}.",
        label="entered",
        role=SemanticRole.SUCCESS,
        state=ViewState.COMPLETED,
    )


def _settings_value_instruction(instruction: str) -> str:
    """Append save guidance to one retained settings value editor."""

    return f"{instruction} {_SETTINGS_VALUE_SAVE_GUIDANCE}"


def _settings_pending_save_notice(state: SettingsState) -> Notice | None:
    """Project unsaved pending changes onto the top-level Settings picker."""

    if state.pending == state.baseline.configuration:
        return None
    return Notice(
        "Choose Apply Changes to persist pending changes",
        label="save required",
        role=SemanticRole.WARNING,
        state=ViewState.IDLE,
    )


def _settings_projection(
    state: SettingsState,
    *,
    provider_overrides: Mapping[str, str] | None = None,
) -> tuple[tuple[str, str | None, str, str, str], ...]:
    """Project ordered root and agent identities with current/pending values."""

    current = state.baseline.configuration
    pending = state.pending
    rows: list[tuple[str, str | None, str, str, str]] = [
        (
            "language",
            None,
            "Language",
            current.system_defaults.language,
            pending.system_defaults.language,
        ),
        (
            "persona",
            None,
            "Persona",
            current.system_defaults.persona,
            pending.system_defaults.persona,
        ),
        (
            "max_debate_rounds",
            None,
            "Maximum debate rounds",
            str(current.system_defaults.max_debate_rounds),
            str(pending.system_defaults.max_debate_rounds),
        ),
        (
            "conversation_storage",
            None,
            "Conversation storage",
            current.system_defaults.conversation_storage_backend or "filesystem",
            pending.system_defaults.conversation_storage_backend or "filesystem",
        ),
        (
            "conversation_rag_provider",
            None,
            "Conversation RAG Provider",
            (
                "Local"
                if current.system_defaults.conversation_rag_provider == "local"
                else "Disabled"
            ),
            (
                "Local"
                if pending.system_defaults.conversation_rag_provider == "local"
                else "Disabled"
            ),
        ),
        (
            "conversation_rag_automatic",
            None,
            "Conversation RAG Automatic",
            "On" if current.system_defaults.conversation_rag_automatic else "Off",
            "On" if pending.system_defaults.conversation_rag_automatic else "Off",
        ),
        (
            "show_message_details",
            None,
            "Message details",
            "On" if current.system_defaults.show_message_details else "Off",
            "On" if pending.system_defaults.show_message_details else "Off",
        ),
    ]
    for agent_id in ROSTER:
        display_name = AGENT_SPECS[agent_id].display_name
        current_agent = current.agent_configs[agent_id]
        pending_agent = pending.agent_configs[agent_id]
        current_provider = _effective_settings_provider(
            state,
            agent_id,
            pending=False,
            provider_overrides=provider_overrides,
        )
        pending_provider = _effective_settings_provider(
            state,
            agent_id,
            provider_overrides=provider_overrides,
        )
        rows.extend(
            (
                (
                    "providers",
                    agent_id,
                    f"{display_name} Provider",
                    (
                        current_provider
                        if current_agent.backend is not None
                        else f"{current_provider} (default)"
                    ),
                    (
                        pending_provider
                        if pending_agent.backend is not None
                        else f"{pending_provider} (default)"
                    ),
                ),
                (
                    "providers",
                    agent_id,
                    f"{display_name} Codex model",
                    current_agent.codex_model or "inherit",
                    pending_agent.codex_model or "inherit",
                ),
                (
                    "providers",
                    agent_id,
                    f"{display_name} Codex reasoning",
                    current_agent.codex_reasoning_effort or "inherit",
                    pending_agent.codex_reasoning_effort or "inherit",
                ),
                (
                    "providers",
                    agent_id,
                    f"{display_name} Antigravity model",
                    current_agent.antigravity_model or "inherit",
                    pending_agent.antigravity_model or "inherit",
                ),
                (
                    "providers",
                    agent_id,
                    f"{display_name} Antigravity effort",
                    current_agent.antigravity_effort or "inherit",
                    pending_agent.antigravity_effort or "inherit",
                ),
            )
        )
    return tuple(rows)


def _remember_first_process_control(
    retained: _ExceptionState | None,
    failure: BaseException,
) -> _ExceptionState | None:
    """Retain only the first non-ordinary failure across cleanup stages."""

    if isinstance(failure, Exception) or retained is not None:
        return retained
    return failure, failure.__traceback__


def _raise_exception_state(state: _ExceptionState) -> None:
    """Raise one retained exception with its originating traceback chain."""

    failure, traceback = state
    raise failure.with_traceback(traceback)


class _ControllerAbort(Exception):
    """Carry one trusted fatal error to the controller's cleanup boundary."""

    def __init__(self, error: InteractiveError) -> None:
        """Store a typed display error without retaining raw failure prose."""

        super().__init__(error.code.value)
        self.error = error


class _SettingsFlowAbort(Exception):
    """Stop one open settings transaction after its fixed error is rendered."""


class InteractiveController:
    """Own one interactive process's attachment and synchronous shell loop.

    Construction borrows collaborators without I/O. ``run_session_lobby`` is
    single-use; once it starts, this controller closes the backend on every
    exit and releases any attachment it can still identify safely. The retained
    ``StoryRuntime`` owns management and inspection while one attachment-scoped
    collaboration-history service owns provider-free search/read access.
    """

    __slots__ = (
        "_attached_session_id",
        "_auth_coordinator",
        "_backend",
        "_closed",
        "_conversation_catalog",
        "_collaboration_history_service",
        "_conversation_storage_services",
        "_context",
        "_debug_log",
        "_error_write_count",
        "_external_viewer",
        "_main_input",
        "_picker_input",
        "_prompt_input",
        "_provider_catalog",
        "_advisory_progress",
        "_runner",
        "_runtime",
        "_semantic_output",
        "_show_message_details",
        "_focused_conversation",
        "_started",
        "_terminal",
        "_workspace",
    )

    def __init__(
        self,
        workspace: Workspace,
        context: CliContext,
        backend: AgentBackend,
        terminal: InteractiveTerminal,
        *,
        conversation_storage_services: ConversationStorageServices | None = None,
        auth_coordinator: InteractiveAuthCoordinator | None = None,
        provider_catalog: ProviderCatalogService | None = None,
    ) -> None:
        """Bind collaborators and construct one shared v2 storage bundle.

        The four positional arguments remain the established construction
        contract. An omitted keyword-only storage bundle creates the selected
        workspace-backed services; injected storage, authentication, and
        provider-catalog collaborators are retained as borrowed state and are
        never closed by this controller. An omitted provider catalog creates
        the command-free workspace-bound default. The collaboration event store
        is shared by the retained runtime and later attachment-scoped history
        services.
        Construction performs no provider call, filesystem write, or access
        claim.
        """

        if not isinstance(workspace, Workspace):
            raise TypeError("workspace must be a Workspace")
        if not isinstance(context, CliContext):
            raise TypeError("context must be a CliContext")
        if not isinstance(backend, AgentBackend):
            raise TypeError("backend must be an AgentBackend")
        if not isinstance(terminal, InteractiveTerminal):
            raise TypeError("terminal must be an InteractiveTerminal")
        if auth_coordinator is not None and any(
            not callable(getattr(auth_coordinator, operation, None))
            for operation in _AUTH_COORDINATOR_OPERATIONS
        ):
            raise TypeError("auth_coordinator must provide choices, probe_all, and run")
        resolved_provider_catalog = (
            ProviderCatalogService(workspace.root)
            if provider_catalog is None
            else provider_catalog
        )
        if any(
            not callable(getattr(resolved_provider_catalog, operation, None))
            for operation in _PROVIDER_CATALOG_OPERATIONS
        ):
            raise TypeError("provider_catalog must provide discover")
        self._workspace = workspace
        self._debug_log = WorkspaceDebugLog(
            workspace,
            defer_until_session=True,
        )
        self._error_write_count = 0
        self._context = context
        self._backend = backend
        self._show_message_details = backend_configuration_snapshot(
            backend
        ).system_defaults.show_message_details
        self._terminal = terminal
        self._auth_coordinator = auth_coordinator
        self._provider_catalog = resolved_provider_catalog
        self._semantic_output = InteractiveSemanticOutput(terminal)
        self._picker_input = InteractivePickerInput(terminal)
        self._prompt_input = InteractivePromptInput(terminal)
        self._external_viewer = InteractiveExternalViewer(terminal)
        self._advisory_progress = InteractiveAdvisoryProgress(terminal)
        self._main_input = InteractiveMainInput(terminal)
        if (
            conversation_storage_services is not None
            and type(conversation_storage_services) is not ConversationStorageServices
        ):
            raise TypeError(
                "conversation_storage_services must be ConversationStorageServices"
            )
        if conversation_storage_services is None:
            self._runtime = StoryRuntime(workspace, backend)
            conversation_storage_services = self._runtime.conversation_storage_services
            if type(conversation_storage_services) is not ConversationStorageServices:
                raise TypeError("runtime storage services are invalid")
        else:
            self._runtime = StoryRuntime(
                workspace,
                backend,
                conversation_storage_services=conversation_storage_services,
            )
        resolved_catalog = conversation_storage_services.catalog
        if any(
            not callable(getattr(resolved_catalog, operation, None))
            for operation in _CONVERSATION_CATALOG_OPERATIONS
        ):
            raise TypeError("conversation_catalog must implement ConversationCatalog")
        self._conversation_storage_services = conversation_storage_services
        self._conversation_catalog = resolved_catalog
        self._collaboration_history_service: CollaborationHistoryService | None = None
        self._attached_session_id: str | None = None
        self._focused_conversation: ConversationMetadata | None = None
        self._runner: asyncio.Runner | None = None
        self._started = False
        self._closed = False

    def _trace_debug(
        self,
        operation: DebugOperation,
        phase: DebugPhase,
        *,
        session_id: str | None = None,
        conversation_id: str | None = None,
        debate_id: str | None = None,
        agent_id: str | None = None,
        outcome: DebugOutcome | None = None,
    ) -> None:
        """Record a trace without allowing diagnostics to alter behavior."""

        try:
            self._debug_log.trace(
                component=DebugComponent.INTERACTIVE_CONTROLLER,
                operation=operation,
                phase=phase,
                session_id=session_id,
                conversation_id=conversation_id,
                debate_id=debate_id,
                agent_id=agent_id,
                outcome=outcome,
            )
        except BaseException:
            pass

    def _write_error(self, error: InteractiveError) -> None:
        """Render one post-workspace error with the fixed debug-log notice."""

        self._terminal.write_error(
            error,
            debug_log_path=_DEBUG_LOG_NOTICE,
        )
        self._error_write_count += 1

    def _record_debug_error(
        self,
        operation: DebugOperation,
        failure: BaseException,
        *,
        session_id: str | None = None,
        conversation_id: str | None = None,
        debate_id: str | None = None,
        agent_id: str | None = None,
        phase: DebugPhase = DebugPhase.FAILED,
        outcome: DebugOutcome = DebugOutcome.FAILED,
    ) -> None:
        """Record a controller failure without allowing it to be replaced."""

        if (
            phase is DebugPhase.FAILED
            and outcome is DebugOutcome.FAILED
            and isinstance(
                failure,
                (asyncio.CancelledError, KeyboardInterrupt, SystemExit),
            )
        ):
            phase = DebugPhase.CANCELLED
            outcome = DebugOutcome.INTERRUPTED
        try:
            self._debug_log.error(
                DebugComponent.INTERACTIVE_CONTROLLER,
                operation,
                failure,
                phase=phase,
                session_id=session_id,
                conversation_id=conversation_id,
                debate_id=debate_id,
                agent_id=agent_id,
                outcome=outcome,
            )
        except BaseException:
            pass

    def _write_notice(
        self,
        text: str,
        *,
        label: str,
        role: SemanticRole = SemanticRole.INFO,
        state: ViewState = ViewState.IDLE,
    ) -> None:
        """Write one controller-owned semantic notice through the narrow seam."""

        self._semantic_output.write_component(
            Notice(text, label=label, role=role, state=state)
        )

    def _write_counted_list_row(
        self,
        title: str,
        record: Record | Message,
        *,
        position: int,
        total_count: int,
    ) -> None:
        """Write one ordered row with its position in a known result set."""

        self._semantic_output.write_component(
            ListView(
                title,
                (record,),
                total_count=total_count,
                position=position,
                state=ViewState.COMPLETED,
            )
        )

    @staticmethod
    def _agent_display_name(agent_id: str) -> str:
        """Return one short display label for known and internal IDs."""

        if agent_id in AGENT_SPECS:
            return AGENT_SPECS[agent_id].display_name
        if agent_id == "runtime":
            return "runtime"
        if agent_id == "user":
            return "user"
        if agent_id == "filesystem":
            return "filesystem"
        if agent_id == "artifact_store":
            return "artifact_store"
        return agent_id.replace("_", " ")

    @staticmethod
    def _collaboration_record_view(record: CollaborationReadableRecord) -> Record:
        """Render one validated event identity and its closed readable fields."""

        sender = record.sender.agent_id or record.sender.kind.value
        recipient = record.recipient.agent_id or record.recipient.kind.value
        multiline_agent_keys = {
            CollaborationReadableFieldKey.LEAD_JUDGMENT_MESSAGE,
            CollaborationReadableFieldKey.SPECIALIST_REPLY_CONTENT,
            CollaborationReadableFieldKey.LEAD_SYNTHESIS_MESSAGE,
            CollaborationReadableFieldKey.ARTIFACT_REVIEW_MESSAGE,
        }
        readable_fields: list[Field] = []
        content: list[str] = []
        for field in record.fields:
            formatted = (
                format_agent_reply_content(field.text)
                if field.key in multiline_agent_keys
                else field.text
            )
            if field.key in multiline_agent_keys and "\n" in formatted:
                content.append(formatted)
                continue
            readable_fields.append(
                Field(
                    (
                        field.key.value
                        + (
                            ""
                            if field.assignment_ordinal is None
                            else f"[{field.assignment_ordinal}]"
                        )
                        + (
                            ""
                            if field.input_ordinal is None
                            else f"[{field.input_ordinal}]"
                        )
                    ),
                    formatted,
                )
            )
        return Record(
            f"event {record.event_id}",
            fields=(
                Field("Sequence", str(record.sequence)),
                Field("Turn", record.turn_id),
                Field("Step", record.step_id),
                Field("Kind", record.kind.value),
                Field("Direction", record.direction.value),
                Field("Actors", f"{sender} → {recipient}"),
                *readable_fields,
            ),
            content="\n".join(content) if content else None,
            state=ViewState.COMPLETED,
        )

    @staticmethod
    def _collaboration_state_meaning(
        projection: CollaborationHistoryProjection,
    ) -> tuple[str, str]:
        """Return the durable state and its direct user-facing meaning."""

        meanings = {
            CollaborationStepState.AWAITING_CONFIRMATION: (
                "A proposal is waiting for your confirmation."
            ),
            CollaborationStepState.RECONFIRMATION_REQUIRED: (
                "The earlier confirmation is no longer sufficient; review and "
                "confirm the updated proposal."
            ),
            CollaborationStepState.CONFIRMED: (
                "You confirmed the proposal; execution has not started yet."
            ),
            CollaborationStepState.RUNNING: (
                "The confirmed work is currently being executed."
            ),
            CollaborationStepState.COMPLETED: (
                "The confirmed work completed and its result is recorded."
            ),
            CollaborationStepState.REJECTED: (
                "You rejected the proposal; that work will not execute."
            ),
            CollaborationStepState.SUPERSEDED: (
                "A newer proposal replaced this proposal."
            ),
            CollaborationStepState.INTERRUPTED: (
                "Execution stopped before a complete outcome was recorded."
            ),
            CollaborationStepState.FAILED: (
                "Execution failed and the failure outcome is recorded."
            ),
            CollaborationStepState.OUTCOME_UNKNOWN: (
                "Execution may have occurred, but its outcome cannot be proven."
            ),
            CollaborationStepState.RECOVERY_REQUIRED: (
                "History requires operator inspection before any safe continuation."
            ),
        }
        state = projection.current_state
        if state is None:
            return "empty", "No collaboration event has been recorded yet."
        return state.value, meanings[state]

    @staticmethod
    def _collaboration_next_action_meaning(
        action: CollaborationNextAction,
    ) -> str:
        """Explain history-derived guidance without granting execution authority."""

        return {
            CollaborationNextAction.NONE: "No history-derived action is pending.",
            CollaborationNextAction.SUBMIT_CONFIRMATION: (
                "Review the proposal and submit a confirmation decision."
            ),
            CollaborationNextAction.START_OR_RECONFIRM: (
                "Start confirmed work, or reconfirm if its basis changed."
            ),
            CollaborationNextAction.FINALIZE_RESULT: (
                "Record the terminal result for the running work."
            ),
            CollaborationNextAction.CONTINUE_WITH_LEAD: (
                "Continue the conversation with the lead agent."
            ),
            CollaborationNextAction.INSPECT_AND_RESOLVE_OUTCOME: (
                "Inspect durable evidence and resolve the uncertain outcome."
            ),
            CollaborationNextAction.REPAIR_AUTHORITATIVE_HISTORY: (
                "Repair authoritative history before continuing."
            ),
            CollaborationNextAction.REPLY_TO_LEAD: (
                "Reply to the lead agent's question."
            ),
            CollaborationNextAction.RETRY_LEAD_TURN: (
                "Submit new input for a fresh Lead turn; the failed attempt "
                "will not be replayed."
            ),
            CollaborationNextAction.SUBMIT_APPLICATION_DECISION: (
                "Review the exact artifact application request and submit "
                "its separate Apply decision."
            ),
            CollaborationNextAction.SUBMIT_COMMIT_DECISION: (
                "Review the exact local commit request and submit "
                "its separate Commit decision."
            ),
            CollaborationNextAction.SUBMIT_RECOMMENDATION_DECISION: (
                "Review the exact Lead preparation and choose Confirm, "
                "Reject, or Revise."
            ),
            CollaborationNextAction.FINALIZE_RECOMMENDATION: (
                "Finalize the known preparation result locally without "
                "replaying provider work."
            ),
        }[action]

    @staticmethod
    def _agent_display_chain(agent_ids: tuple[str, ...]) -> str:
        """Render one compact textual sender/recipient chain."""

        return ", ".join(
            InteractiveController._agent_display_name(agent_id)
            for agent_id in agent_ids
        )

    @staticmethod
    def _debate_inspection_record(facts: DebateInspectionFacts) -> Record:
        """Project validated debate facts to stable ordered semantic fields."""

        if facts.status == "active":
            role = SemanticRole.INFO
            state = ViewState.LOADING
        elif facts.status == "consensus":
            role = SemanticRole.SUCCESS
            state = ViewState.COMPLETED
        elif facts.status == "interrupted":
            role = SemanticRole.WARNING
            state = ViewState.CANCELLED
        elif facts.status == "not_found":
            role = SemanticRole.MUTED
            state = ViewState.EMPTY
        else:
            role = SemanticRole.ERROR
            state = ViewState.ERROR
        return Record(
            f"run {facts.debate_id}",
            fields=(
                Field("Session", facts.session_id),
                Field("Status", facts.status),
                Field("Terminal", "yes" if facts.terminal else "no"),
                Field("Input", facts.input_kind or "none"),
                Field("Revision", facts.revision_id or "none"),
                Field("Rounds", str(facts.rounds_completed)),
                Field("Artifact", f"{facts.artifact or 'none'}."),
            ),
            role=role,
            state=state,
        )

    @staticmethod
    def _debate_context_component(
        facts: DebateContextInspectionFacts | None,
    ) -> Record | Notice:
        """Project an optional validated debate sidecar to one component."""

        if facts is None:
            return Notice(
                "Context sidecar: none.",
                label="context",
                role=SemanticRole.MUTED,
                state=ViewState.EMPTY,
            )
        return Record(
            "context",
            title=f"conversation {facts.conversation_id}",
            fields=(
                Field("Mode", facts.mode.value),
                Field("Events", str(facts.event_count)),
                Field("Complete turns", str(facts.complete_turns)),
                Field("Retained turns", str(facts.retained_turns)),
                Field("Omitted turns", str(facts.omitted_turns)),
                Field("UTF-8 bytes", str(facts.retained_utf8_bytes)),
                Field("Truncated", f"{'yes' if facts.truncated else 'no'}."),
            ),
            state=ViewState.COMPLETED,
        )

    def run_session_lobby(
        self,
        *,
        requested_session: str | None = None,
    ) -> int:
        """Run one interactive attachment lifecycle and return status 0 or 2.

        ``requested_session`` chooses explicit create/resume behavior; ``None``
        uses the saved default. Commands may mutate owned process state,
        established session/conversation persistence, or runtime-owned turn
        transcripts. One controller-owned ``asyncio.Runner`` serves every
        accepted provider coroutine and closes before session/backend cleanup.
        Every started exit releases known access and closes the borrowed
        backend; fatal uncertainty returns 2, and a second run raises
        ``RuntimeError`` without reacquiring resources.
        """

        if self._started:
            raise RuntimeError("interactive controller instances are single-use")
        if requested_session is not None and not isinstance(requested_session, str):
            raise TypeError("requested_session must be a string or None")
        self._started = True
        self._trace_debug(
            DebugOperation.INTERACTIVE_LOBBY,
            DebugPhase.STARTED,
        )

        exit_code = 2
        cleanup_error_reported = False
        body_control: _ExceptionState | None = None
        try:
            try:
                self._runner = asyncio.Runner()
                startup_requested_exit = self._enter_initial_session(requested_session)
                if startup_requested_exit:
                    exit_code = 0
                else:
                    if self._attached_session_id is None:
                        self._write_lobby_orientation()
                    exit_code = self._read_session_lobby_commands()
            except _ControllerAbort as abort:
                self._record_debug_error(
                    DebugOperation.INTERACTIVE_LOBBY,
                    abort,
                    session_id=self._attached_session_id,
                )
                diagnostic_written = self._write_error_best_effort(abort.error)
                cleanup_error_reported = (
                    abort.error.code is InteractiveErrorCode.CLEANUP_FAILED
                    and diagnostic_written
                )
                exit_code = 2
            except (
                asyncio.CancelledError,
                KeyboardInterrupt,
                OSError,
            ) as exc:
                self._record_debug_error(
                    DebugOperation.INTERACTIVE_LOBBY,
                    exc,
                    session_id=self._attached_session_id,
                    phase=DebugPhase.CANCELLED,
                    outcome=DebugOutcome.INTERRUPTED,
                )
                self._write_error_best_effort(
                    InteractiveError(InteractiveErrorCode.OPERATION_INTERRUPTED)
                )
                exit_code = 2
            except Exception as exc:
                self._record_debug_error(
                    DebugOperation.INTERACTIVE_LOBBY,
                    exc,
                    session_id=self._attached_session_id,
                )
                self._write_error_best_effort(
                    InteractiveError(InteractiveErrorCode.INTERNAL_ERROR)
                )
                exit_code = 2
        except BaseException as failure:
            body_control = (failure, failure.__traceback__)
        exit_code = self._release_attachment_and_close_backend(
            exit_code,
            cleanup_error_reported=cleanup_error_reported,
            body_control=body_control,
        )
        self._trace_debug(
            DebugOperation.INTERACTIVE_LOBBY,
            DebugPhase.SUCCEEDED if exit_code == 0 else DebugPhase.FAILED,
            outcome=(DebugOutcome.COMPLETED if exit_code == 0 else DebugOutcome.FAILED),
        )
        return exit_code

    def _enter_initial_session(self, requested_session: str | None) -> bool:
        """Resume an explicit or default session; return whether startup saw EOF.

        An explicit missing session is created. A paused default requires one
        confirmation before it is resumed. Recoverable failures leave the
        controller in the lobby; cleanup uncertainty reaches the outer boundary.
        """

        if requested_session is not None:
            self._enter_explicit_session(requested_session)
            return False

        default_session = self._context.default_session_id
        if default_session is None:
            return False
        try:
            state = self._backend.get_session_lifecycle(default_session)
            require_lifecycle_state(state, default_session)
        except Exception as exc:
            self._present_session_failure(exc, default_session)
            return False

        if state.durable_state is SessionDurableState.ABSENT:
            self._present_session_failure(
                SessionNotFoundError(f"session not found: {default_session}"),
                default_session,
            )
            return False
        if state.durable_state is SessionDurableState.PAUSED:
            decision = self._terminal.request_confirmation(
                f"Resume default session {default_session}?",
                default=True,
            )
            if decision is ConfirmationResult.EOF:
                return True
            if decision is not ConfirmationResult.YES:
                self._write_session_resume_cancelled_status()
                return False
        self._open_known_session(default_session)
        return False

    def _enter_explicit_session(self, session_id: str) -> None:
        """Create a missing explicit session or resume an existing one."""

        try:
            state = self._backend.get_session_lifecycle(session_id)
            require_lifecycle_state(state, session_id)
        except Exception as exc:
            self._present_session_failure(exc, session_id)
            return
        if state.durable_state is SessionDurableState.ABSENT:
            self._create_and_attach_destination(session_id)
            return
        self._open_known_session(session_id)

    def _create_and_attach_destination(self, session_id: str) -> None:
        """Validate configuration, create with access, and commit attachment."""

        if session_id == self._attached_session_id:
            self._write_error(InteractiveError(InteractiveErrorCode.SESSION_EXISTS))
            return
        access_acquired = False
        try:
            before = self._backend.get_session_lifecycle(session_id)
            require_lifecycle_state(before, session_id)
            if before.durable_state is not SessionDurableState.ABSENT:
                raise SessionExistsError(f"session already exists: {session_id}")
            configured = self._backend.validate_provider_configuration(session_id)
            if configured is not True:
                raise BackendProtocolError(
                    "provider configuration validation returned a non-true value"
                )
            self._backend.provision_session_resources(session_id)
            acquired = self._backend.create_session_with_access(session_id)
            access_acquired = True
            require_ready_access_state(acquired, session_id)
            observed = self._backend.get_session_lifecycle(session_id)
            require_correlated_state(
                None,
                acquired,
                observed,
                session_id,
            )
        except Exception as exc:
            if access_acquired:
                self._release_failed_new_destination(session_id)
            self._present_session_failure(exc, session_id)
            return
        self._accept_acquired_destination(
            acquired,
            AttachmentKind.CREATED,
        )

    def _open_default_or_named_session(
        self,
        requested_session: str | None,
    ) -> bool:
        """Resume an explicit session or select one persisted session to resume.

        Explicit identity preserves the established direct transition. An
        omitted identity reads the canonical lifecycle order into the generic
        picker; cancellation is mutation-free and EOF requests normal lobby
        cleanup. The committed identity is not trusted as lifecycle state and
        is freshly validated by ``_open_known_session``.
        """

        if requested_session is not None:
            if requested_session == self._attached_session_id:
                self._write_notice(
                    f"Already attached to {requested_session}.",
                    label="session",
                    role=SemanticRole.SUCCESS,
                    state=ViewState.COMPLETED,
                )
                return False
            self._open_known_session(requested_session)
            return False

        try:
            states = self._run_picker_preparation(
                self._validated_story_session_lifecycles
            )
        except _ControllerAbort:
            raise
        except Exception as exc:
            self._present_session_failure(
                exc,
                self._context.default_session_id,
            )
            return False
        if not states:
            self._write_notice(
                "No story sessions found.",
                label="session",
                role=SemanticRole.MUTED,
                state=ViewState.EMPTY,
            )
            return False

        session_ids = tuple(state.session_id for state in states)
        highlighted_id = self._attached_session_id
        if highlighted_id not in session_ids:
            highlighted_id = self._context.default_session_id
        if highlighted_id not in session_ids:
            highlighted_id = None
        outcome = self._picker_input.read_picker(
            Prompt(
                "Resume story session",
                instruction="Choose a persisted session to resume.",
            ),
            tuple(
                Choice(
                    state.session_id,
                    state.session_id,
                    description=(
                        f"Durable: {state.durable_state.value}; "
                        f"access: {state.access_state.value}"
                    ),
                )
                for state in states
            ),
            highlighted_id=highlighted_id,
        )
        if outcome.kind is ControlOutcomeKind.EOF:
            return True
        if outcome.kind in {
            ControlOutcomeKind.CANCEL,
            ControlOutcomeKind.INTERRUPT,
        }:
            self._write_session_resume_cancelled_status()
            return False
        if (
            outcome.kind is not ControlOutcomeKind.COMMIT
            or outcome.text is None
            or outcome.text not in session_ids
        ):
            raise _ControllerAbort(
                InteractiveError(InteractiveErrorCode.INTERNAL_ERROR)
            )
        session_id = outcome.text
        if session_id == self._attached_session_id:
            self._write_notice(
                f"Already attached to {session_id}.",
                label="session",
                role=SemanticRole.SUCCESS,
                state=ViewState.COMPLETED,
            )
            return False
        self._open_known_session(session_id)
        return False

    def _open_known_session(self, session_id: str) -> None:
        """Run validate, pre-read, acquire/reopen, and correlated post-read."""

        newly_acquired = False
        try:
            before = self._backend.get_session_lifecycle(session_id)
            require_lifecycle_state(before, session_id)
            if before.durable_state is SessionDurableState.ABSENT:
                raise SessionNotFoundError(f"session not found: {session_id}")
            configured = self._backend.validate_provider_configuration(session_id)
            if configured is not True:
                raise BackendProtocolError(
                    "provider configuration validation returned a non-true value"
                )
            newly_acquired = before.access_state is not SessionAccessState.HELD_HERE
            if before.durable_state is SessionDurableState.PAUSED:
                acquired = self._backend.reopen_session_with_access(session_id)
                attachment = AttachmentKind.REOPENED
            else:
                acquired = self._backend.acquire_session_access(session_id)
                attachment = AttachmentKind.OPENED
            require_ready_access_state(acquired, session_id)
            observed = self._backend.get_session_lifecycle(session_id)
            require_correlated_state(
                before,
                acquired,
                observed,
                session_id,
            )
        except Exception as exc:
            if newly_acquired:
                self._release_failed_new_destination(session_id)
            self._present_session_failure(exc, session_id)
            return
        self._accept_acquired_destination(acquired, attachment)

    def _accept_acquired_destination(
        self,
        state: SessionLifecycleState,
        attachment: AttachmentKind,
    ) -> None:
        """Load destination focus and commit an exact ready access state safely."""

        session_id = state.session_id
        try:
            require_ready_access_state(state, session_id)
        except (TypeError, ValueError, BackendProtocolError):
            self._release_uncommitted_destination(session_id)
            self._abandon_known_attachment_for_cleanup_abort()
        try:
            focused_conversation = self._load_destination_conversation(session_id)
        except Exception as exc:
            self._release_uncommitted_destination(session_id)
            self._present_destination_conversation_failure(exc)
            return
        try:
            assessment = self._assess_session_transition(
                state,
                SessionTransitionOperation.ENTER_DESTINATION,
                focused_conversation,
            )
        except Exception as exc:
            self._release_uncommitted_destination(session_id)
            self._present_session_failure(exc, session_id)
            return
        if not transition_operation_is_allowed(
            assessment,
            SessionTransitionOperation.ENTER_DESTINATION,
        ):
            self._release_uncommitted_destination(session_id)
            self._write_transition_denial(assessment)
            return
        try:
            history_service = CollaborationHistoryService(
                CollaborationHistorySearchScope(session_id),
                conversation_storage_services=self._conversation_storage_services,
            )
        except Exception:
            self._release_uncommitted_destination(session_id)
            raise _ControllerAbort(
                InteractiveError(InteractiveErrorCode.INTERNAL_ERROR)
            ) from None
        if self._attached_session_id is None:
            self._attach_destination_session(
                session_id,
                attachment,
                focused_conversation,
                history_service,
            )
            return
        self._switch_attached_session(
            session_id,
            attachment,
            focused_conversation,
            history_service,
        )

    def _assess_session_transition(
        self,
        lifecycle: SessionLifecycleState,
        operation: SessionTransitionOperation,
        conversation: ConversationMetadata | None,
    ) -> SessionTransitionAssessment:
        """Delegate exact active or idle transition evidence to the backend."""

        conversation_id = (
            conversation.conversation_id if conversation is not None else None
        )
        assessment = self._backend.assess_session_transition(
            lifecycle_state=lifecycle,
            requested_operation=operation,
            conversation_id=conversation_id,
            collaboration_store=(
                self._conversation_storage_services.event_store
                if conversation_id is not None
                else None
            ),
        )
        return require_transition_assessment(
            assessment,
            expected_session=lifecycle.session_id,
            expected_conversation=conversation_id,
            requested_operation=operation,
        )

    def _write_transition_denial(
        self,
        assessment: SessionTransitionAssessment,
    ) -> None:
        """Present only fixed content-safe transition assessment fields."""

        projection = project_transition_assessment(assessment)
        self._semantic_output.write_component(
            Record(
                "session transition",
                fields=(
                    Field("Scope", projection.scope),
                    Field("State", projection.state),
                    Field("Next action", projection.next_action),
                    Field("Owner", projection.owner),
                    Field("Failure", projection.failure_code),
                    Field(
                        "Allowed operations",
                        ", ".join(projection.allowed_operations) or "none",
                    ),
                ),
                state=ViewState.ERROR,
            )
        )

    def _load_destination_conversation(
        self,
        session_id: str,
    ) -> ConversationMetadata | None:
        """Load an existing active default for one acquired destination.

        Missing files, null choices, absent records, and archived records return
        ``None`` without rewriting persistence. Malformed default or catalog
        state propagates so the caller can release the uncommitted destination
        before presenting a safe error.
        """

        default_id = load_default_conversation(self._workspace, session_id)
        if default_id is None:
            return None
        try:
            metadata = self._conversation_catalog.get(session_id, default_id)
        except ConversationNotFoundError:
            return None
        validated = validate_catalog_metadata(
            metadata,
            session_id,
            expected_conversation_id=default_id,
        )
        if validated.status is ConversationStatus.ARCHIVED:
            return None
        return validated

    def _present_destination_conversation_failure(
        self,
        failure: Exception,
    ) -> None:
        """Render a released destination's typed catalog failure without prose.

        The caller has already returned destination access. Workspace
        failures render and preserve an existing source, but an initial
        attachment has no source and aborts. Protocol/internal failure always
        aborts so the outer boundary releases any known source and closes.
        """

        error = classify_conversation_failure(failure)
        if (
            error.code is InteractiveErrorCode.INTERNAL_ERROR
            or self._attached_session_id is None
        ):
            raise _ControllerAbort(error)
        self._write_error(error)

    def _attach_destination_session(
        self,
        session_id: str,
        attachment: AttachmentKind,
        focused_conversation: ConversationMetadata | None,
        history_service: CollaborationHistoryService,
    ) -> None:
        """Persist and commit one destination with its scoped history service.

        The default is written only after access acquisition. A write failure
        releases the destination before it is reported; failed release makes
        the remaining access state fatal and delegates cleanup to backend close.
        The validated candidate is retained only by a successful commit.
        """

        next_context = self._persist_destination_default(session_id)
        if next_context is None:
            return
        self._commit_attached_session(
            next_context,
            session_id,
            attachment,
            focused_conversation,
            history_service,
        )

    def _switch_attached_session(
        self,
        destination_session: str,
        attachment: AttachmentKind,
        focused_conversation: ConversationMetadata | None,
        history_service: CollaborationHistoryService,
    ) -> None:
        """Save the destination default before replacing source and read scope.

        Destination enter and source leave assessments have already succeeded.
        Default-write failure releases the destination and preserves the
        source. Source-release failure after the default write is not rolled
        back; owned resources are closed best effort and the process exits with
        ``cleanup_failed``. The candidate history service commits only after
        source access is released.
        """

        source_session = self._attached_session_id
        assert source_session is not None
        try:
            source_lifecycle = self._backend.get_session_lifecycle(source_session)
            require_ready_access_state(source_lifecycle, source_session)
            assessment = self._assess_session_transition(
                source_lifecycle,
                SessionTransitionOperation.LEAVE_SOURCE,
                self._focused_conversation,
            )
        except Exception as exc:
            self._release_uncommitted_destination(destination_session)
            self._present_session_failure(exc, source_session)
            return
        if not transition_operation_is_allowed(
            assessment,
            SessionTransitionOperation.LEAVE_SOURCE,
        ):
            self._release_uncommitted_destination(destination_session)
            self._write_transition_denial(assessment)
            return
        next_context = self._persist_destination_default(destination_session)
        if next_context is None:
            return
        try:
            released = self._backend.release_session_access(source_session)
            require_released_state(released, source_session)
        except Exception:
            self._abandon_known_attachment_for_cleanup_abort()
        self._collaboration_history_service = None
        self._commit_attached_session(
            next_context,
            destination_session,
            attachment,
            focused_conversation,
            history_service,
        )

    def _persist_destination_default(
        self,
        destination_session: str,
    ) -> CliContext | None:
        """Persist a changed default after acquiring access, or return current.

        On write failure uncommitted destination access is released. The
        failure is then reported when recoverable or escalated when workspace
        state is unusable; no controller attachment state is changed here.
        """

        if self._context.default_session_id == destination_session:
            return self._context
        try:
            return save_default_session(self._workspace, destination_session)
        except Exception as exc:
            self._release_uncommitted_destination(destination_session)
            self._present_session_failure(exc, destination_session)
            return None

    def _release_uncommitted_destination(self, session_id: str) -> None:
        """Release destination access after pre-commit failure or abort.

        A failed release makes all attachment bookkeeping uncertain, so the
        source marker is cleared and the outer boundary performs only backend
        close before returning ``cleanup_failed``.
        """

        try:
            released = self._backend.release_session_access(session_id)
            require_released_state(released, session_id)
        except Exception:
            self._abandon_known_attachment_for_cleanup_abort()

    def _release_failed_new_destination(self, session_id: str) -> None:
        """Compensate a failed newly acquired destination without source loss."""

        try:
            state = self._backend.get_session_lifecycle(session_id)
            require_lifecycle_state(state, session_id)
            if state.access_state is SessionAccessState.HELD_HERE:
                released = self._backend.release_session_access(session_id)
                require_released_state(released, session_id)
        except (SessionNotFoundError, WorkspaceVersionUnsupportedError):
            return
        except Exception:
            self._abandon_known_attachment_for_cleanup_abort()

    def _commit_attached_session(
        self,
        context: CliContext,
        session_id: str,
        attachment: AttachmentKind,
        focused_conversation: ConversationMetadata | None,
        history_service: CollaborationHistoryService,
    ) -> None:
        """Commit attachment, memory scope, and orientation together.

        Destination access and the story-session default are already durable.
        This method performs no backend or catalog lifecycle operation and
        retains the prebuilt read service plus validated destination metadata.
        """

        self._context = context
        self._attached_session_id = session_id
        self._focused_conversation = focused_conversation
        self._collaboration_history_service = history_service
        self._trace_debug(
            DebugOperation.ATTACHMENT,
            DebugPhase.SUCCEEDED,
            session_id=session_id,
            outcome=DebugOutcome.HELD_HERE,
        )
        if focused_conversation is not None:
            self._trace_debug(
                DebugOperation.CONVERSATION_FOCUS,
                DebugPhase.SUCCEEDED,
                session_id=session_id,
                conversation_id=focused_conversation.conversation_id,
                outcome=DebugOutcome.COMPLETED,
            )
        self._semantic_output.write_component(
            Record(
                "session",
                fields=(
                    Field("Workspace", str(self._workspace.root)),
                    Field("Session", f"{session_id} ({attachment.value})"),
                ),
                state=ViewState.COMPLETED,
            )
        )

    def _provider_work_is_ready(self, state: SessionLifecycleState) -> bool:
        """Derive readiness from a read-only configuration validation and state."""

        try:
            configured = self._backend.validate_provider_configuration(state.session_id)
        except (BackendProtocolError, ConfigurationError):
            configured = False
        if type(configured) is not bool:
            raise BackendProtocolError(
                "provider configuration validation returned a non-boolean value"
            )
        return provider_work_is_ready(
            state,
            provider_configuration_ready=configured,
        )

    def _read_session_lobby_commands(self) -> int:
        """Read and dispatch interactive actions until normal exit status 0.

        Main Ctrl-C clears only the current input operation. Main EOF, quit, or
        EOF during a call-local subject or confirmation read returns 0. Each
        attached input revalidates its current conversation scope and presents
        it only when its semantic snapshot changed. Fatal transition uncertainty
        reaches the outer backend cleanup boundary.
        """

        unpresented_scope = object()
        last_presented_scope: object | tuple[str, str | None, str | None] = (
            unpresented_scope
        )
        while True:
            if self._attached_session_id is None:
                last_presented_scope = unpresented_scope
            else:
                last_presented_scope = self._write_attached_conversation_scope(
                    last_presented_scope
                )
            if (
                self._attached_session_id is not None
                and self._focused_conversation is not None
            ):
                terminal_action = self._main_input.read_main_with_references(
                    self._main_resume_choices,
                    project_lead_reference_scope,
                    self._lead_reference_paths,
                    self._lead_reference_text,
                    session_id=self._attached_session_id,
                    agent_id=None,
                    header_provider=self._main_runtime_header,
                )
            else:
                terminal_action = self._main_input.read_main(
                    self._main_resume_choices,
                    session_id=self._attached_session_id,
                    agent_id=None,
                    header_provider=self._main_runtime_header,
                )
            if terminal_action.kind is TerminalActionKind.INTERRUPT:
                self._write_main_input_cancelled_status()
                continue
            if terminal_action.kind is TerminalActionKind.EOF:
                return 0
            if terminal_action.kind is not TerminalActionKind.LINE:
                raise _ControllerAbort(
                    InteractiveError(InteractiveErrorCode.INTERNAL_ERROR)
                )
            assert terminal_action.text is not None
            original_line = terminal_action.text
            action = parse_interactive_line(original_line)
            if self._dispatch_session_lobby_action(action, original_line):
                return 0

    def _lead_reference_paths(self) -> tuple[str, ...]:
        """List only current canonical writing sources for the main picker."""

        try:
            if self._attached_session_id is None:
                raise ReferenceInputFailure(ReferenceInputFailureKind.UNAVAILABLE)
            binding = compose_session_artifact_binding(
                self._workspace.root,
                self._attached_session_id,
            )
            with binding.open_ready_resources() as access:
                return access.repository.list_paths()
        except ReferenceInputFailure:
            raise
        except (
            OSError,
            SessionArtifactBindingError,
            WorkspaceFormatError,
            WorkspaceSecurityError,
        ):
            raise ReferenceInputFailure(
                ReferenceInputFailureKind.UNAVAILABLE
            ) from None

    def _lead_reference_text(self, path: str) -> str:
        """Read one selected canonical source for the independent range viewer."""

        try:
            if self._attached_session_id is None:
                raise ReferenceInputFailure(ReferenceInputFailureKind.UNAVAILABLE)
            binding = compose_session_artifact_binding(
                self._workspace.root,
                self._attached_session_id,
            )
            with binding.open_ready_resources() as access:
                return access.repository.read_text(path)
        except ReferenceInputFailure:
            raise
        except FileNotFoundError:
            raise ReferenceInputFailure(ReferenceInputFailureKind.STALE) from None
        except WorkspaceFormatError:
            raise ReferenceInputFailure(
                ReferenceInputFailureKind.UNREADABLE
            ) from None
        except (OSError, SessionArtifactBindingError, WorkspaceSecurityError):
            raise ReferenceInputFailure(
                ReferenceInputFailureKind.UNAVAILABLE
            ) from None

    def _main_runtime_header(self, buffer: str, cursor: int) -> Notice:
        """Project the focused lead-input boundary."""

        if not isinstance(buffer, str):
            raise TypeError("buffer must be a string")
        if not isinstance(cursor, int) or isinstance(cursor, bool):
            raise TypeError("cursor must be an integer")
        if cursor < 0 or cursor > len(buffer):
            raise ValueError("cursor must identify a buffer boundary")
        if self._attached_session_id is None:
            text = "Attach a story session before sending input to the lead."
        elif self._focused_conversation is None:
            text = "Focus a conversation before sending input to the lead."
        else:
            text = (
                "Send ordinary text to the lead. Slash-prefixed management "
                "commands remain commands."
            )
        return Notice(text, label="lead", role=SemanticRole.MUTED, state=ViewState.IDLE)

    def _main_resume_choices(
        self,
        buffer: str,
        cursor: int,
    ) -> Picker | None:
        """Snapshot canonical omitted resume choices for capable main input.

        The snapshot is read-only and deliberately disposable. Any failure
        suppresses live choices so committing the untouched command reaches
        the existing post-submit path, which performs its normal reporting.
        """

        if cursor != len(buffer):
            return None
        try:
            if buffer == _SESSION_RESUME_COMMAND:
                states = self._validated_story_session_lifecycles()
                session_ids = tuple(state.session_id for state in states)
                highlighted_id = self._attached_session_id
                if highlighted_id not in session_ids:
                    highlighted_id = self._context.default_session_id
                if highlighted_id not in session_ids:
                    highlighted_id = None
                return Picker(
                    Prompt(
                        "Resume story session",
                        instruction="Choose a persisted session to resume.",
                    ),
                    tuple(
                        Choice(
                            f"{_SESSION_RESUME_COMMAND} {state.session_id}",
                            state.session_id,
                            description=(
                                f"Durable: {state.durable_state.value}; "
                                f"access: {state.access_state.value}"
                            ),
                        )
                        for state in states
                    ),
                    highlighted_id=(
                        None
                        if highlighted_id is None
                        else f"{_SESSION_RESUME_COMMAND} {highlighted_id}"
                    ),
                )

            if (
                buffer != _CONVERSATION_RESUME_COMMAND
                or self._attached_session_id is None
            ):
                return None
            session_id = self._attached_session_id
            entries = self._catalog_entries(session_id)
            active_entries = tuple(
                entry for entry in entries if entry.status is ConversationStatus.ACTIVE
            )
            active_ids = tuple(entry.conversation_id for entry in active_entries)
            highlighted_id = (
                None
                if self._focused_conversation is None
                else self._focused_conversation.conversation_id
            )
            if highlighted_id not in active_ids:
                highlighted_id = load_default_conversation(
                    self._workspace,
                    session_id,
                )
            if highlighted_id not in active_ids:
                highlighted_id = None
            return Picker(
                Prompt(
                    "Resume conversation",
                    instruction="Choose an active conversation to resume.",
                ),
                tuple(
                    Choice(
                        (f"{_CONVERSATION_RESUME_COMMAND} {entry.conversation_id}"),
                        entry.subject,
                        description=(
                            f"Handle: {visible_conversation_handle(entry, entries)}"
                        ),
                    )
                    for entry in active_entries
                ),
                highlighted_id=(
                    None
                    if highlighted_id is None
                    else (f"{_CONVERSATION_RESUME_COMMAND} {highlighted_id}")
                ),
            )
        except Exception:
            return None

    def _dispatch_session_lobby_action(
        self,
        action: InteractiveAction,
        original_line: str,
    ) -> bool:
        """Record and execute one fixed parsed interactive command."""

        session_id = self._attached_session_id
        conversation_id = (
            None
            if self._focused_conversation is None
            else self._focused_conversation.conversation_id
        )
        self._trace_debug(
            DebugOperation.INTERACTIVE_COMMAND,
            DebugPhase.STARTED,
            session_id=session_id,
            conversation_id=conversation_id,
        )
        error_write_count = self._error_write_count
        try:
            should_exit = self._dispatch_session_lobby_action_unlogged(
                action,
                original_line,
            )
        except (
            asyncio.CancelledError,
            KeyboardInterrupt,
            OSError,
            SystemExit,
        ) as exc:
            self._record_debug_error(
                DebugOperation.INTERACTIVE_COMMAND,
                exc,
                session_id=session_id,
                conversation_id=conversation_id,
                phase=DebugPhase.CANCELLED,
                outcome=DebugOutcome.INTERRUPTED,
            )
            raise
        except Exception as exc:
            self._record_debug_error(
                DebugOperation.INTERACTIVE_COMMAND,
                exc,
                session_id=session_id,
                conversation_id=conversation_id,
            )
            raise
        rejected = self._error_write_count > error_write_count
        self._trace_debug(
            DebugOperation.INTERACTIVE_COMMAND,
            DebugPhase.FAILED if rejected else DebugPhase.SUCCEEDED,
            session_id=self._attached_session_id,
            conversation_id=(
                None
                if self._focused_conversation is None
                else self._focused_conversation.conversation_id
            ),
            outcome=(DebugOutcome.REJECTED if rejected else DebugOutcome.COMPLETED),
        )
        return should_exit

    def _dispatch_session_lobby_action_unlogged(
        self,
        action: InteractiveAction,
        original_line: str,
    ) -> bool:
        """Execute one parser action and return whether the loop should exit.

        ``original_line`` is used only to choose the trusted parse-error
        category. Global help/status, directives, inspection, focused history,
        roster, story-session navigation, search, and transcript reads remain
        provider-free. The returned boolean signals loop exit; command-specific
        state changes are delegated to their owners, and fatal failures reach
        outer cleanup without local resource acquisition.
        """

        if action.kind is ActionKind.IGNORED:
            return False
        if action.kind is ActionKind.LEAD_INPUT:
            if self._attached_session_id is None:
                self._write_error(
                    InteractiveError(InteractiveErrorCode.SESSION_NOT_ATTACHED)
                )
                return False
            if self._focused_conversation is None:
                self._write_error(
                    InteractiveError(InteractiveErrorCode.CONVERSATION_REQUIRED)
                )
                return False
            if self._runner is None:
                raise _ControllerAbort(
                    InteractiveError(InteractiveErrorCode.INTERNAL_ERROR)
                )
            assert action.text is not None
            adapter = InteractiveLeadAdapter(
                self._runtime.session(self._attached_session_id),
                self._runner,
                self._advisory_progress,
                self._picker_input,
                self._prompt_input,
                self._semantic_output,
                recover_authentication=(
                    getattr(self._auth_coordinator, "recover", None)
                ),
                read_events=lambda conversation_id: (
                    self._conversation_storage_services.event_store.read(
                        self._attached_session_id,
                        conversation_id,
                    )
                ),
            )
            return adapter.submit(
                self._focused_conversation.conversation_id,
                action.text,
            )
        if action.kind is ActionKind.HELP:
            self._terminal.write_help(action.argument)
            return False
        if action.kind is ActionKind.LIST_AGENTS:
            self._write_agent_roster()
            return False
        if action.kind is ActionKind.AUTH:
            self._run_auth()
            return False
        if action.kind is ActionKind.SETTINGS:
            self._run_settings()
            return False
        if action.kind is ActionKind.SESSION_LIST:
            self._list_story_sessions()
            return False
        if action.kind is ActionKind.SESSION_CREATE:
            assert action.argument is not None
            self._create_and_attach_destination(action.argument)
            return False
        if action.kind is ActionKind.SESSION_OPEN:
            return self._open_default_or_named_session(action.argument)
        if action.kind is ActionKind.SESSION_PAUSE:
            return self._pause_attached_session()
        if action.kind is ActionKind.QUIT:
            return True
        if action.kind is ActionKind.PARSE_ERROR:
            self._write_parser_error(action, original_line)
            return False
        if action.kind is ActionKind.STATUS:
            self._write_runtime_status()
            return False
        if action.kind in {
            ActionKind.SET_LANGUAGE,
            ActionKind.SET_PERSONA,
            ActionKind.RESET,
        }:
            self._apply_attached_runtime_directive(action)
            return False
        if action.kind is ActionKind.INSPECT:
            self._inspect_attached_runtime(action)
            return False
        if action.kind is ActionKind.EXCHANGES:
            self._view_attached_agent_exchanges()
            return False
        if action.kind is ActionKind.HISTORY:
            self._write_focused_conversation_history(action)
            return False
        if action.kind in {ActionKind.SEARCH, ActionKind.TRANSCRIPT}:
            if self._attached_session_id is None:
                self._write_error(
                    InteractiveError(InteractiveErrorCode.SESSION_NOT_ATTACHED)
                )
                return False
            if action.kind is ActionKind.SEARCH:
                self._search_attached_conversations(action)
            else:
                self._read_attached_conversation_transcript(action)
            return False
        if action.kind in {
            ActionKind.CONVERSATION_NEW,
            ActionKind.CONVERSATION_LIST,
            ActionKind.CONVERSATION_LIST_ALL,
            ActionKind.CONVERSATION_USE,
            ActionKind.CONVERSATION_SUBJECT,
            ActionKind.CONVERSATION_ARCHIVE,
        }:
            if self._attached_session_id is None:
                self._write_error(
                    InteractiveError(InteractiveErrorCode.SESSION_NOT_ATTACHED)
                )
                return False
            if action.kind is ActionKind.CONVERSATION_NEW:
                return self._create_subject_conversation(action.argument)
            if action.kind in {
                ActionKind.CONVERSATION_LIST,
                ActionKind.CONVERSATION_LIST_ALL,
            }:
                self._list_subject_conversations(
                    include_archived=(action.kind is ActionKind.CONVERSATION_LIST_ALL),
                    query=action.argument,
                )
                return False
            if action.kind is ActionKind.CONVERSATION_USE:
                return self._focus_subject_conversation(action.argument)
            if action.kind is ActionKind.CONVERSATION_SUBJECT:
                assert action.argument is not None
                self._rename_focused_conversation(action.argument)
                return False
            return self._archive_subject_conversation(action.argument)
        if self._attached_session_id is not None and self._focused_conversation is None:
            self._write_error(
                InteractiveError(InteractiveErrorCode.CONVERSATION_REQUIRED)
            )
            return False
        self._write_unavailable_lobby_command_error()
        return False

    def _run_auth(self) -> None:
        """Present live provider authentication without story-provider work."""

        coordinator = self._auth_coordinator
        if coordinator is None:
            self._write_notice(
                "Provider authentication is unavailable in this shell.",
                label="authentication",
                role=SemanticRole.ERROR,
                state=ViewState.ERROR,
            )
            return

        snapshot = self._run_auth_probe(coordinator)
        self._persist_missing_auth_default(snapshot)
        result = coordinator.run(snapshot)
        if result.kind in {
            InteractiveAuthResultKind.CANCEL,
            InteractiveAuthResultKind.INTERRUPT,
            InteractiveAuthResultKind.EOF,
        }:
            self._write_notice(
                (
                    "Authentication picker cancelled. Any authenticated "
                    "workspace default already established was kept."
                ),
                label="authentication",
                role=SemanticRole.WARNING,
                state=ViewState.CANCELLED,
            )
            return

        refreshed = self._run_auth_probe(coordinator)
        newly_authenticated = self._newly_authenticated_provider(
            snapshot,
            result,
            refreshed,
        )
        self._persist_missing_auth_default(
            refreshed,
            newly_authenticated_provider=newly_authenticated,
        )
        assert result.provider is not None
        if not refreshed.is_authenticated(result.provider):
            self._write_notice(
                (
                    f"{_provider_display_name(result.provider)} is not "
                    "authenticated. Run /auth to retry."
                ),
                label="authentication",
                role=SemanticRole.ERROR,
                state=ViewState.ERROR,
            )

    def _persist_missing_auth_default(
        self,
        snapshot: ProviderAuthSnapshot,
        *,
        newly_authenticated_provider: str | None = None,
    ) -> None:
        """Best-effort persist one authenticated default outside settings."""

        provider = select_default_provider(
            snapshot,
            newly_authenticated_provider=newly_authenticated_provider,
        )
        if provider is None:
            return
        try:
            establish_default_backend(self._workspace, provider)
        except ConfigurationError:
            self._write_error(
                InteractiveError(InteractiveErrorCode.CONFIGURATION_INVALID)
            )
        except WorkspaceSecurityError:
            self._write_error(InteractiveError(InteractiveErrorCode.WORKSPACE_UNSAFE))
        except (WorkspaceFormatError, OSError):
            self._write_error(InteractiveError(InteractiveErrorCode.WORKSPACE_INVALID))
        except SettingsError as failure:
            self._write_settings_error(failure.kind)

    def _rebase_missing_settings_default(
        self,
        state: SettingsState,
        snapshot: ProviderAuthSnapshot,
        *,
        newly_authenticated_provider: str | None = None,
    ) -> SettingsState:
        """Rebase a settings-local missing provider default in memory."""

        provider = select_default_provider(
            snapshot,
            newly_authenticated_provider=newly_authenticated_provider,
        )
        if provider is None:
            return state
        try:
            return establish_settings_default_backend(
                self._workspace,
                state,
                provider,
                provider_overrides=self._backend.backend_overrides,
            )
        except SettingsError as failure:
            self._write_settings_error(failure.kind)
            raise _SettingsFlowAbort from failure

    @staticmethod
    def _newly_authenticated_provider(
        before: ProviderAuthSnapshot,
        result: InteractiveAuthResult,
        after: ProviderAuthSnapshot,
    ) -> str | None:
        """Return the exact provider whose interaction gained fresh auth."""

        provider = result.provider
        if (
            result.kind is InteractiveAuthResultKind.AUTHENTICATED
            and provider is not None
            and not before.is_authenticated(provider)
            and after.is_authenticated(provider)
        ):
            return provider
        return None

    def _read_settings_picker(
        self,
        prompt: Prompt,
        choices: tuple[Choice, ...],
        *,
        transient_context: tuple[Panel | Section | Field | Notice, ...] = (),
        highlighted_id: str | None = None,
        selected_id: str | None = None,
        notice: Notice | None = None,
        state: ViewState = ViewState.OPEN,
        visible_choice_limit: int = 7,
        transient: bool = True,
    ) -> ControlOutcome:
        """Read one controller-owned Settings picker in form presentation."""

        return self._picker_input.read_picker(
            prompt,
            choices,
            presentation=InputPresentation.FORM,
            transient_context=transient_context,
            visible_choice_limit=visible_choice_limit,
            highlighted_id=highlighted_id,
            selected_id=selected_id,
            notice=notice,
            state=state,
            transient=transient,
        )

    def _read_settings_prompt(
        self,
        prompt: Prompt,
        *,
        context: tuple[Panel | Section | Field | Notice, ...] = (),
        initial_value: str | None = None,
        transient: bool = True,
    ) -> ControlOutcome:
        """Read one controller-owned Settings value in form presentation."""

        return self._prompt_input.read_prompt(
            prompt,
            presentation=InputPresentation.FORM,
            context=context,
            initial_value=initial_value,
            transient=transient,
        )

    def _run_settings(self) -> None:
        """Edit one isolated configuration candidate and save only on request."""

        try:
            state = load_settings_state(self._workspace)
        except ConfigurationError:
            self._write_error(
                InteractiveError(InteractiveErrorCode.CONFIGURATION_INVALID)
            )
            return
        except WorkspaceSecurityError:
            self._write_error(InteractiveError(InteractiveErrorCode.WORKSPACE_UNSAFE))
            return
        except (WorkspaceFormatError, OSError):
            self._write_error(InteractiveError(InteractiveErrorCode.WORKSPACE_INVALID))
            return

        top_highlight: str | None = "language"
        while True:
            dirty = state.pending != state.baseline.configuration
            projection = _settings_projection(
                state,
                provider_overrides=self._backend.backend_overrides,
            )
            root_values = {
                root_id: (current_value, pending_value)
                for root_id, agent_id, _label, current_value, pending_value in projection
                if agent_id is None
            }
            root_changed = {
                root_id: current_value != pending_value
                for root_id, (current_value, pending_value) in root_values.items()
            }
            agent_change_count = sum(
                current_value != pending_value
                for root_id, agent_id, _label, current_value, pending_value in projection
                if root_id == "providers" and agent_id is not None
            )
            current_rag = (
                f"Provider {root_values['conversation_rag_provider'][0]}; "
                f"Automatic {root_values['conversation_rag_automatic'][0]}"
            )
            pending_rag = (
                f"Provider {root_values['conversation_rag_provider'][1]}; "
                f"Automatic {root_values['conversation_rag_automatic'][1]}"
            )
            choices = (
                Choice(
                    "language",
                    "Language",
                    form_summary=(
                        f"Pending: {root_values['language'][1]}"
                        if root_changed["language"]
                        else f"Current: {root_values['language'][0]}"
                    ),
                    form_summary_role=(
                        SemanticRole.WARNING
                        if root_changed["language"]
                        else SemanticRole.MUTED
                    ),
                ),
                Choice(
                    "persona",
                    "Persona",
                    form_summary=(
                        f"Pending: {root_values['persona'][1]}"
                        if root_changed["persona"]
                        else f"Current: {root_values['persona'][0]}"
                    ),
                    form_summary_role=(
                        SemanticRole.WARNING
                        if root_changed["persona"]
                        else SemanticRole.MUTED
                    ),
                ),
                Choice(
                    "max_debate_rounds",
                    "Maximum debate rounds",
                    form_summary=(
                        f"Pending: {root_values['max_debate_rounds'][1]}"
                        if root_changed["max_debate_rounds"]
                        else f"Current: {root_values['max_debate_rounds'][0]}"
                    ),
                    form_summary_role=(
                        SemanticRole.WARNING
                        if root_changed["max_debate_rounds"]
                        else SemanticRole.MUTED
                    ),
                ),
                Choice(
                    "conversation_storage",
                    "Conversation storage",
                    form_summary=(
                        f"Pending: {root_values['conversation_storage'][1]}"
                        if root_values["conversation_storage"][0]
                        != root_values["conversation_storage"][1]
                        else f"Current: {root_values['conversation_storage'][0]}"
                    ),
                    form_summary_role=(
                        SemanticRole.WARNING
                        if root_changed["conversation_storage"]
                        else SemanticRole.MUTED
                    ),
                ),
                Choice(
                    "conversation_rag",
                    "Conversation RAG",
                    form_summary=(
                        f"Pending: {pending_rag}"
                        if current_rag != pending_rag
                        else f"Current: {current_rag}"
                    ),
                    form_summary_role=(
                        SemanticRole.WARNING
                        if current_rag != pending_rag
                        else SemanticRole.MUTED
                    ),
                ),
                Choice(
                    "show_message_details",
                    "Message details",
                    form_summary=(
                        f"Pending: {root_values['show_message_details'][1]}"
                        if root_changed["show_message_details"]
                        else f"Current: {root_values['show_message_details'][0]}"
                    ),
                    form_summary_role=(
                        SemanticRole.WARNING
                        if root_changed["show_message_details"]
                        else SemanticRole.MUTED
                    ),
                ),
                Choice(
                    "providers",
                    "Agents",
                    form_summary=(
                        None
                        if agent_change_count == 0
                        else (
                            "1 pending change"
                            if agent_change_count == 1
                            else f"{agent_change_count} pending changes"
                        )
                    ),
                    form_summary_role=(
                        SemanticRole.WARNING
                        if agent_change_count
                        else SemanticRole.DEFAULT
                    ),
                ),
            )
            if dirty:
                choices += (
                    Choice(
                        "save",
                        "Apply Changes",
                        warning_suffix="(*Required)",
                    ),
                    Choice(
                        "cancel",
                        "Discard Changes",
                        description="Pending changes will be discarded.",
                    ),
                )
            if top_highlight not in {choice.identity for choice in choices}:
                top_highlight = None
            outcome = self._read_settings_picker(
                Prompt(
                    "Settings",
                    instruction=(
                        "Choose a setting to edit, apply changes, or discard changes."
                        if dirty
                        else "Choose a setting to edit."
                    ),
                ),
                choices,
                highlighted_id=top_highlight,
                notice=_settings_pending_save_notice(state),
                visible_choice_limit=len(choices),
                transient=True,
            )
            if outcome.kind in {
                ControlOutcomeKind.INTERRUPT,
                ControlOutcomeKind.EOF,
            }:
                self._write_settings_cancelled(state)
                return
            if outcome.kind is ControlOutcomeKind.CANCEL:
                top_highlight = "cancel"
                if self._settings_cancel_action(state):
                    self._write_settings_cancelled(state)
                    return
                continue
            selected = self._require_settings_commit(outcome)
            top_highlight = selected
            if selected == "cancel":
                if self._settings_cancel_action(state):
                    self._write_settings_cancelled(state)
                    return
                continue
            if selected == "save":
                if state.pending == state.baseline.configuration:
                    continue
                confirmation = self._settings_save_confirmation(
                    state,
                    action="save",
                )
                if confirmation == "return":
                    continue
                if confirmation == "cancel":
                    self._write_settings_cancelled(state)
                    return
                if state.pending == state.baseline.configuration:
                    continue
                if self._settings_change_is_message_details_only(state):
                    save_outcome = self._save_message_details_setting(state)
                    if save_outcome == "retry":
                        continue
                    if save_outcome != "applied":
                        return
                    return
                if self._settings_change_is_conversation_rag_only(state):
                    save_outcome = self._save_conversation_rag_setting(state)
                    if save_outcome == "retry":
                        continue
                    if save_outcome != "saved":
                        return
                    return
                if self._auth_coordinator is not None:
                    try:
                        state, auth_outcome = self._authenticate_settings_routes(state)
                    except _SettingsFlowAbort:
                        return
                    if auth_outcome == "cancel":
                        self._write_settings_cancelled(state)
                        return
                    if auth_outcome == "return":
                        continue
                if state.pending == state.baseline.configuration:
                    continue
                save_outcome = self._save_and_apply_settings(state)
                if save_outcome == "retry":
                    continue
                if save_outcome != "applied":
                    return
                return
            try:
                state, cancelled = self._edit_settings_value(
                    state,
                    selected,
                )
            except _SettingsFlowAbort:
                return
            if cancelled:
                self._write_settings_cancelled(state)
                return

    @staticmethod
    def _settings_change_is_message_details_only(state: SettingsState) -> bool:
        """Classify one exact immutable presentation-only candidate."""

        baseline = state.baseline.configuration
        pending = state.pending
        expected = replace(
            baseline,
            system_defaults=replace(
                baseline.system_defaults,
                show_message_details=(pending.system_defaults.show_message_details),
            ),
        )
        return pending != baseline and pending == expected

    @staticmethod
    def _settings_change_is_conversation_rag_only(state: SettingsState) -> bool:
        """Classify one exact immutable next-shell RAG candidate."""

        baseline = state.baseline.configuration
        pending = state.pending
        expected = replace(
            baseline,
            system_defaults=replace(
                baseline.system_defaults,
                conversation_rag_provider=(
                    pending.system_defaults.conversation_rag_provider
                ),
                conversation_rag_automatic=(
                    pending.system_defaults.conversation_rag_automatic
                ),
            ),
        )
        return pending != baseline and pending == expected

    def _save_message_details_setting(self, state: SettingsState) -> str:
        """Persist and activate only the message presentation preference."""

        try:
            prepared = prepare_settings_save(
                self._workspace,
                state,
                provider_overrides=self._backend.backend_overrides,
            )
        except SettingsError as failure:
            self._write_settings_error(failure.kind)
            return "retry"
        if prepared.configuration != state.pending or prepared.switched_agents:
            raise _ControllerAbort(
                InteractiveError(InteractiveErrorCode.INTERNAL_ERROR)
            )
        try:
            commit_prepared_settings_save(self._workspace, prepared)
        except SettingsError as failure:
            self._write_settings_error(failure.kind)
            return "retry"

        self._show_message_details = (
            prepared.configuration.system_defaults.show_message_details
        )
        self._semantic_output.write_component(
            Panel(
                "Settings",
                (
                    Notice(
                        "Settings saved and applied to this running shell now.",
                        label="saved and applied",
                        role=SemanticRole.SUCCESS,
                        state=ViewState.COMPLETED,
                    ),
                ),
                state=ViewState.COMPLETED,
            )
        )
        return "applied"

    def _save_conversation_rag_setting(self, state: SettingsState) -> str:
        """Persist only startup-fixed RAG settings without live resources."""

        try:
            prepared = prepare_settings_save(
                self._workspace,
                state,
                provider_overrides=self._backend.backend_overrides,
            )
        except SettingsError as failure:
            self._write_settings_error(failure.kind)
            return "retry"
        if prepared.configuration != state.pending or prepared.switched_agents:
            raise _ControllerAbort(
                InteractiveError(InteractiveErrorCode.INTERNAL_ERROR)
            )
        try:
            commit_prepared_settings_save(self._workspace, prepared)
        except SettingsError as failure:
            self._write_settings_error(failure.kind)
            return "retry"

        self._semantic_output.write_component(
            Panel(
                "Settings",
                (
                    Notice(
                        (
                            "Conversation RAG settings were saved for the next "
                            "shell. This running shell retains its current "
                            "Conversation RAG service and automatic policy."
                        ),
                        label="saved for next shell",
                        role=SemanticRole.SUCCESS,
                        state=ViewState.COMPLETED,
                    ),
                ),
                state=ViewState.COMPLETED,
            )
        )
        return "saved"

    def _save_and_apply_settings(self, state: SettingsState) -> str:
        """Persist and activate one staged snapshot without false success."""

        correlation_id = uuid.uuid4().hex
        backend_candidate = None
        runtime_candidate = None
        prepared = None
        persisted = False
        storage_failure = None
        before_profiles = self._backend.execution_profiles()
        try:
            prepared = prepare_settings_save(
                self._workspace,
                state,
                provider_overrides=self._backend.backend_overrides,
            )
            backend_candidate = self._backend.stage_runtime_configuration(
                prepared.configuration,
                prepared.provider_routes,
                correlation_id=correlation_id,
            )
            runtime_candidate = self._run_runtime_configuration_coroutine(
                self._runtime.prepare_runtime_configuration(
                    prepared.configuration,
                    {
                        agent_id: prepared.provider_routes[agent_id].generation
                        for agent_id in ROSTER
                    },
                )
            )
            self._backend.validate_runtime_configuration_candidate(backend_candidate)
            report = select_conversation_storage(
                self._workspace,
                (
                    prepared.configuration.system_defaults.conversation_storage_backend
                    or "filesystem"
                ),
                installation_consent=self._confirm_storage_installation,
                diagnostic_stream=sys.stderr,
                configuration_baseline=state.baseline.configuration_baseline,
                configuration=prepared.configuration,
                precommit_guard=lambda: prepare_settings_save(
                    self._workspace,
                    state,
                    provider_overrides=self._backend.backend_overrides,
                ),
            )
            if report.outcome != "completed":
                assert report.failure is not None
                storage_failure = report.failure
                raise SettingsError(SettingsErrorKind.CONFLICT)
            persisted = report.configuration == "saved"
            commit_prepared_settings_save(self._workspace, prepared)
            persisted = True
            self._run_runtime_configuration_coroutine(
                self._runtime.commit_runtime_configuration(
                    runtime_candidate,
                    backend_candidate,
                )
            )
        except BaseException as failure:
            recovery_required = False
            restored = False
            if persisted and prepared is not None:
                try:
                    candidate_is_active = (
                        backend_candidate is not None
                        and self._backend.runtime_configuration_candidate_is_active(
                            backend_candidate
                        )
                    )
                except BaseException:
                    candidate_is_active = True
                if not candidate_is_active:
                    try:
                        restore_prepared_settings_save(self._workspace, prepared)
                    except BaseException:
                        recovery_required = True
                    else:
                        restored = True
                else:
                    recovery_required = True
            if backend_candidate is not None:
                if recovery_required:
                    self._backend.record_runtime_configuration_recovery_required(
                        backend_candidate
                    )
                try:
                    self._backend.discard_runtime_configuration(backend_candidate)
                except BaseException as cleanup_failure:
                    if isinstance(failure, Exception):
                        raise cleanup_failure from failure
            if runtime_candidate is not None:
                try:
                    self._runtime.discard_runtime_configuration(runtime_candidate)
                except BaseException as cleanup_failure:
                    if isinstance(failure, Exception):
                        raise cleanup_failure from failure
            if not isinstance(failure, Exception):
                raise
            if recovery_required:
                self._write_settings_error(SettingsErrorKind.RECOVERY_REQUIRED)
                return "recovery_required"
            if restored:
                self._semantic_output.write_component(
                    Notice(
                        (
                            "Runtime activation failed after persistence, so the "
                            "exact saved files were restored. The active runtime "
                            "is unchanged; review the settings and retry."
                        ),
                        label="restored; not applied",
                        role=SemanticRole.WARNING,
                        state=ViewState.CANCELLED,
                    )
                )
                return "retry"
            if storage_failure is not None:
                self._write_storage_selection_error(storage_failure)
            elif isinstance(failure, SettingsError):
                self._write_settings_error(failure.kind)
            else:
                self._semantic_output.write_component(
                    Notice(
                        (
                            "Settings were not saved or applied. Provider "
                            "preflight or runtime quiescence was not ready; the "
                            "active runtime is unchanged."
                        ),
                        label="not applied",
                        role=SemanticRole.ERROR,
                        state=ViewState.ERROR,
                    )
                )
            return "retry"

        assert prepared is not None
        active_profiles = self._backend.execution_profiles()
        before_defaults = state.baseline.configuration.system_defaults
        active_defaults = prepared.configuration.system_defaults
        live_settings_changed = (
            state.baseline.configuration.agent_configs
            != prepared.configuration.agent_configs
            or (
                before_defaults.language,
                before_defaults.persona,
                before_defaults.max_debate_rounds,
                before_defaults.codex_model,
                before_defaults.codex_reasoning_effort,
                before_defaults.antigravity_model,
                before_defaults.antigravity_effort,
                before_defaults.default_backend,
                before_defaults.show_message_details,
            )
            != (
                active_defaults.language,
                active_defaults.persona,
                active_defaults.max_debate_rounds,
                active_defaults.codex_model,
                active_defaults.codex_reasoning_effort,
                active_defaults.antigravity_model,
                active_defaults.antigravity_effort,
                active_defaults.default_backend,
                active_defaults.show_message_details,
            )
        )
        rag_changed = (
            before_defaults.conversation_rag_provider,
            before_defaults.conversation_rag_automatic,
        ) != (
            active_defaults.conversation_rag_provider,
            active_defaults.conversation_rag_automatic,
        )
        self._show_message_details = active_defaults.show_message_details
        self._write_settings_applied_result(
            before_profiles,
            active_profiles,
            switched_agents=prepared.switched_agents,
            live_settings_changed=live_settings_changed,
            storage_before=(
                before_defaults.conversation_storage_backend or "filesystem"
            ),
            storage_after=(
                active_defaults.conversation_storage_backend or "filesystem"
            ),
            rag_changed=rag_changed,
        )
        return "applied"

    def _run_runtime_configuration_coroutine(self, coroutine: object) -> object:
        """Run one configuration coroutine on the controller-owned event loop."""

        try:
            runner = self._runner
            if runner is not None:
                return runner.run(coroutine)
            with asyncio.Runner() as local_runner:
                return local_runner.run(coroutine)
        except BaseException:
            if inspect.iscoroutine(coroutine):
                coroutine.close()
            raise

    def _write_settings_applied_result(
        self,
        before_profiles: tuple[AgentExecutionProfile, ...],
        active_profiles: tuple[AgentExecutionProfile, ...],
        *,
        switched_agents: tuple[str, ...],
        live_settings_changed: bool,
        storage_before: str,
        storage_after: str,
        rag_changed: bool,
    ) -> None:
        """Render truthful current-shell and next-shell settings scopes."""

        if (
            len(before_profiles) != len(ROSTER)
            or len(active_profiles) != len(ROSTER)
            or type(live_settings_changed) is not bool
            or type(rag_changed) is not bool
            or storage_before not in {"filesystem", "sqlite"}
            or storage_after not in {"filesystem", "sqlite"}
        ):
            raise _ControllerAbort(
                InteractiveError(InteractiveErrorCode.INTERNAL_ERROR)
            )
        before = {profile.agent_id: profile for profile in before_profiles}
        active = {profile.agent_id: profile for profile in active_profiles}
        changed_agents = tuple(
            agent_id for agent_id in ROSTER if before[agent_id] != active[agent_id]
        )
        storage_changed = storage_before != storage_after
        items: list[Section | Notice] = []
        if live_settings_changed:
            items.append(
                Notice(
                    "Settings saved and applied to this running shell now.",
                    label="saved and applied",
                    role=SemanticRole.SUCCESS,
                    state=ViewState.COMPLETED,
                )
            )
        if rag_changed:
            items.append(
                Notice(
                    (
                        "Conversation RAG settings were saved for the next "
                        "shell. This running shell retains its current "
                        "Conversation RAG service and automatic policy."
                    ),
                    label="saved for next shell",
                    role=SemanticRole.SUCCESS,
                    state=ViewState.COMPLETED,
                )
            )
        if storage_changed:
            items.append(
                Notice(
                    f"Restart is required for {storage_after}.",
                    label="saved",
                    role=SemanticRole.SUCCESS,
                    state=ViewState.COMPLETED,
                )
            )
        if changed_agents:
            changed_count = len(changed_agents)
            profile_word = "profile" if changed_count == 1 else "profiles"
            summary = f"{changed_count} agent {profile_word} changed."
            if switched_agents:
                if len(switched_agents) == 1:
                    summary += (
                        " The provider switch starts a fresh thread with "
                        "story-session context on the next turn."
                    )
                else:
                    summary += (
                        " Provider switches start fresh threads with "
                        "story-session context on the next turn."
                    )
            summary += (
                " Use /agents for active values and confirmation, or /status "
                "for thread and source details."
            )
            items.append(
                Notice(
                    summary,
                    label="profiles changed",
                    role=SemanticRole.WARNING,
                )
            )
        self._semantic_output.write_component(
            Panel(
                "Settings",
                tuple(items),
                state=ViewState.COMPLETED,
            )
        )

    @staticmethod
    def _execution_profile_field(
        profile: AgentExecutionProfile,
        *,
        role: SemanticRole,
        label: str | None = None,
    ) -> Field:
        """Project one exact execution profile into a compact settings row."""

        return Field(
            label or AGENT_SPECS[profile.agent_id].display_name,
            (
                f"{_provider_display_name(profile.provider)} "
                f"[{_execution_source_label(profile.provider_source)}] · "
                f"model {_execution_value(profile.model)} "
                f"[{_execution_source_label(profile.model_source)}] · "
                f"effort {_execution_value(profile.effort)} "
                f"[{_execution_source_label(profile.effort_source)}]"
            ),
            role=role,
        )

    def _authenticate_settings_routes(
        self,
        state: SettingsState,
    ) -> tuple[SettingsState, str]:
        """Freshly authenticate every effective route before settings Save."""

        coordinator = self._auth_coordinator
        if coordinator is None:
            return state, "ready"
        attempted: set[str] = set()
        snapshot = self._run_auth_probe(coordinator)
        state = self._rebase_missing_settings_default(state, snapshot)
        while True:
            provider = next(
                (
                    _effective_settings_provider(
                        state,
                        agent_id,
                        provider_overrides=self._backend.backend_overrides,
                    )
                    for agent_id in ROSTER
                    if not snapshot.is_authenticated(
                        _effective_settings_provider(
                            state,
                            agent_id,
                            provider_overrides=self._backend.backend_overrides,
                        )
                    )
                ),
                None,
            )
            if provider is None:
                return state, "ready"
            if provider in attempted:
                status = snapshot.status_for(provider)
                if status.authenticated:
                    raise _ControllerAbort(
                        InteractiveError(InteractiveErrorCode.INTERNAL_ERROR)
                    )
                provider_name = _provider_display_name(provider)
                if status.state.value == "unauthenticated":
                    message = (
                        f"{provider_name} is not authenticated. Complete "
                        "native sign-in, then retry Apply Changes."
                    )
                elif status.state.value == "unavailable":
                    message = (
                        f"{provider_name} CLI is unavailable. Install it, "
                        "then retry Apply Changes."
                    )
                elif status.state.value == "indeterminate":
                    message = (
                        f"{provider_name} authentication could not be "
                        "confirmed. Check the native CLI, then retry Apply "
                        "Changes."
                    )
                else:
                    raise _ControllerAbort(
                        InteractiveError(InteractiveErrorCode.INTERNAL_ERROR)
                    )
                self._write_notice(
                    message,
                    label="authentication",
                    role=SemanticRole.ERROR,
                    state=ViewState.ERROR,
                )
                return state, "return"

            attempted.add(provider)
            before = snapshot
            result = coordinator.run(
                snapshot,
                presentation=InputPresentation.FORM,
                selected_provider=provider,
            )
            if result.kind is InteractiveAuthResultKind.CANCEL:
                return state, "return"
            if result.kind in {
                InteractiveAuthResultKind.INTERRUPT,
                InteractiveAuthResultKind.EOF,
            }:
                return state, "cancel"

            snapshot = self._run_auth_probe(coordinator)
            newly_authenticated = self._newly_authenticated_provider(
                before,
                result,
                snapshot,
            )
            state = self._rebase_missing_settings_default(
                state,
                snapshot,
                newly_authenticated_provider=newly_authenticated,
            )

    def _settings_save_confirmation(
        self,
        state: SettingsState,
        *,
        action: str,
    ) -> str:
        """Confirm one Save or Cancel action through a transient YES/NO picker."""

        if action not in {"save", "cancel"}:
            raise ValueError("action must be save or cancel")
        transient_context = (
            self._settings_panel(
                state,
                provider_overrides=self._backend.backend_overrides,
            ),
        )
        outcome = self._read_settings_picker(
            Prompt(
                ("Apply changes?" if action == "save" else "Discard changes?"),
                instruction=(
                    "Choose YES to apply changes, or NO to return."
                    if action == "save"
                    else "Choose YES to discard changes, or NO to return."
                ),
            ),
            (Choice("yes", "YES"), Choice("no", "NO")),
            transient_context=transient_context,
            highlighted_id="yes",
            transient=True,
        )
        if outcome.kind in {
            ControlOutcomeKind.INTERRUPT,
            ControlOutcomeKind.EOF,
        }:
            return "cancel"
        if outcome.kind is ControlOutcomeKind.CANCEL:
            return "return"
        if outcome.kind is not ControlOutcomeKind.COMMIT or outcome.text is None:
            raise _ControllerAbort(
                InteractiveError(InteractiveErrorCode.INTERNAL_ERROR)
            )
        if outcome.text == "no":
            return "return"
        if outcome.text == "yes":
            return "save" if action == "save" else "cancel"
        raise _ControllerAbort(InteractiveError(InteractiveErrorCode.INTERNAL_ERROR))

    def _settings_cancel_action(self, state: SettingsState) -> bool:
        """Return whether one root Cancel action should discard and exit."""

        if state.pending == state.baseline.configuration:
            return True
        return (
            self._settings_save_confirmation(
                state,
                action="cancel",
            )
            == "cancel"
        )

    def _edit_settings_value(
        self,
        state: SettingsState,
        selected: str,
    ) -> tuple[SettingsState, bool]:
        """Run one nested editor and report whole-settings cancellation."""

        if selected == "language":
            return self._edit_settings_language(state)
        if selected == "persona":
            return self._edit_settings_persona(state)
        if selected == "max_debate_rounds":
            return self._edit_settings_rounds(state)
        if selected == "conversation_storage":
            return self._edit_settings_storage(state)
        if selected == "show_message_details":
            return self._edit_settings_message_details(state)
        if selected == "conversation_rag":
            return self._edit_settings_conversation_rag(state)
        if selected == "providers":
            return self._edit_settings_provider(state)
        raise _ControllerAbort(InteractiveError(InteractiveErrorCode.INTERNAL_ERROR))

    def _edit_settings_language(
        self,
        state: SettingsState,
    ) -> tuple[SettingsState, bool]:
        """Select one of the fixed workspace language choices."""

        notice: Notice | None = None
        armed_value: object = _SETTINGS_UNARMED
        while True:
            pending = state.pending.system_defaults.language
            outcome = self._read_settings_picker(
                Prompt(
                    "Language",
                    instruction=_settings_value_instruction(
                        "Choose the workspace language. Enter applies the value "
                        "and keeps this editor open; press Esc to return to "
                        "Settings."
                    ),
                ),
                tuple(Choice(language, language) for language in SETTINGS_LANGUAGES),
                highlighted_id=pending,
                selected_id=pending,
                notice=notice,
                transient=True,
            )
            value, whole_cancel = self._nested_settings_value(outcome)
            if whole_cancel:
                return state, True
            if value is None:
                return state, False
            if armed_value == value:
                return state, False
            state = update_language(state, value)
            armed_value = state.pending.system_defaults.language
            notice = _settings_entered_notice("Language", value)

    def _edit_settings_persona(
        self,
        state: SettingsState,
    ) -> tuple[SettingsState, bool]:
        """Select one persona from the immutable opening catalog."""

        choices = tuple(
            Choice(
                entry.name,
                entry.name,
                description=entry.relative_path,
            )
            for entry in state.baseline.persona_catalog
        )
        notice: Notice | None = None
        armed_value: object = _SETTINGS_UNARMED
        while True:
            pending = state.pending.system_defaults.persona
            outcome = self._read_settings_picker(
                Prompt(
                    "Persona",
                    instruction=_settings_value_instruction(
                        "Choose a persona template. Enter applies the value and "
                        "keeps this editor open; press Esc to return to Settings."
                    ),
                ),
                choices,
                highlighted_id=pending,
                selected_id=pending,
                notice=notice,
                transient=True,
            )
            value, whole_cancel = self._nested_settings_value(outcome)
            if whole_cancel:
                return state, True
            if value is None:
                return state, False
            if armed_value == value:
                return state, False
            state = update_persona(state, value)
            armed_value = state.pending.system_defaults.persona
            notice = _settings_entered_notice("Persona", value)

    def _edit_settings_rounds(
        self,
        state: SettingsState,
    ) -> tuple[SettingsState, bool]:
        """Read and validate one pending nonnegative debate limit."""

        notice: Notice | None = None
        armed_value: object = _SETTINGS_UNARMED
        while True:
            outcome = self._read_settings_prompt(
                Prompt(
                    "Maximum debate rounds",
                    scope="settings",
                    instruction=_settings_value_instruction(
                        "Enter a nonnegative integer; 0 is audit-only. Enter "
                        "applies the value and keeps this editor open; press Esc "
                        "to return to Settings."
                    ),
                ),
                context=(notice,) if notice is not None else (),
                initial_value=str(state.pending.system_defaults.max_debate_rounds),
                transient=True,
            )
            value, whole_cancel = self._nested_settings_value(outcome)
            if whole_cancel:
                return state, True
            if value is None:
                return state, False
            if not value or any(character not in "0123456789" for character in value):
                notice = None
                armed_value = _SETTINGS_UNARMED
                self._write_settings_validation(
                    "Maximum debate rounds must be a nonnegative ASCII integer."
                )
                continue
            try:
                rounds = int(value)
            except ValueError:
                notice = None
                armed_value = _SETTINGS_UNARMED
                self._write_settings_validation("Maximum debate rounds is too large.")
                continue
            if armed_value == rounds:
                return state, False
            state = update_max_debate_rounds(state, rounds)
            armed_value = state.pending.system_defaults.max_debate_rounds
            notice = _settings_entered_notice(
                "Maximum debate rounds",
                str(rounds),
            )

    def _edit_settings_storage(
        self,
        state: SettingsState,
    ) -> tuple[SettingsState, bool]:
        """Select a desired next-shell storage backend without activating it."""

        notice: Notice | None = None
        armed_value: object = _SETTINGS_UNARMED
        while True:
            pending = (
                state.pending.system_defaults.conversation_storage_backend
                or "filesystem"
            )
            outcome = self._read_settings_picker(
                Prompt(
                    "Conversation storage",
                    instruction=_settings_value_instruction(
                        "Choose the desired backend for the next shell. Enter "
                        "applies the value and keeps this editor open; press Esc "
                        "to return to Settings."
                    ),
                ),
                (
                    Choice("filesystem", "Filesystem"),
                    Choice("sqlite", "SQLite"),
                ),
                highlighted_id=pending,
                selected_id=pending,
                notice=notice,
                transient=True,
            )
            value, whole_cancel = self._nested_settings_value(outcome)
            if whole_cancel:
                return state, True
            if value is None:
                return state, False
            if armed_value == value:
                return state, False
            try:
                state = update_conversation_storage_backend(state, value)
            except ConfigurationError:
                notice = None
                armed_value = _SETTINGS_UNARMED
                self._write_settings_validation(
                    "Conversation storage must be filesystem or sqlite."
                )
                continue
            armed_value = (
                state.pending.system_defaults.conversation_storage_backend
                or "filesystem"
            )
            notice = _settings_entered_notice(
                "conversation storage",
                armed_value,
            )

    def _edit_settings_conversation_rag(
        self,
        state: SettingsState,
    ) -> tuple[SettingsState, bool]:
        """Edit pending local RAG policy without probing optional resources."""

        highlighted = "provider"
        while True:
            current = state.baseline.configuration.system_defaults
            pending = state.pending.system_defaults
            current_provider = (
                "Local" if current.conversation_rag_provider == "local" else "Disabled"
            )
            pending_provider = (
                "Local" if pending.conversation_rag_provider == "local" else "Disabled"
            )
            current_automatic = "On" if current.conversation_rag_automatic else "Off"
            pending_automatic = "On" if pending.conversation_rag_automatic else "Off"
            outcome = self._read_settings_picker(
                Prompt(
                    "Conversation RAG",
                    instruction=(
                        "Choose Provider or Automatic to edit; press Esc to "
                        "return to Settings. Optional-package compatibility is "
                        "checked only when the next shell starts."
                    ),
                ),
                (
                    Choice(
                        "provider",
                        "Provider",
                        form_summary=(
                            f"Pending: {pending_provider}"
                            if current_provider != pending_provider
                            else f"Current: {current_provider}"
                        ),
                        form_summary_role=(
                            SemanticRole.WARNING
                            if current_provider != pending_provider
                            else SemanticRole.MUTED
                        ),
                    ),
                    Choice(
                        "automatic",
                        "Automatic",
                        form_summary=(
                            f"Pending: {pending_automatic}"
                            if current_automatic != pending_automatic
                            else f"Current: {current_automatic}"
                        ),
                        form_summary_role=(
                            SemanticRole.WARNING
                            if current_automatic != pending_automatic
                            else SemanticRole.MUTED
                        ),
                    ),
                ),
                highlighted_id=highlighted,
                transient=True,
            )
            selected, whole_cancel = self._nested_settings_value(outcome)
            if whole_cancel:
                return state, True
            if selected is None:
                return state, False
            highlighted = selected
            if selected == "provider":
                state, cancelled = self._edit_settings_rag_provider(state)
            elif selected == "automatic":
                state, cancelled = self._edit_settings_rag_automatic(state)
            else:
                raise _ControllerAbort(
                    InteractiveError(InteractiveErrorCode.INTERNAL_ERROR)
                )
            if cancelled:
                return state, True

    def _edit_settings_rag_provider(
        self,
        state: SettingsState,
    ) -> tuple[SettingsState, bool]:
        """Select Disabled or Local while retaining the pending transaction."""

        notice: Notice | None = None
        armed_value: object = _SETTINGS_UNARMED
        while True:
            pending = state.pending.system_defaults.conversation_rag_provider
            pending_id = "local" if pending == "local" else "disabled"
            outcome = self._read_settings_picker(
                Prompt(
                    "Conversation RAG Provider",
                    instruction=_settings_value_instruction(
                        "Choose Disabled or Local. Disabling also turns "
                        "Automatic off. Enter applies the value and keeps this "
                        "editor open; press Esc to return to Conversation RAG."
                    ),
                ),
                (
                    Choice("disabled", "Disabled"),
                    Choice("local", "Local"),
                ),
                highlighted_id=pending_id,
                selected_id=pending_id,
                notice=notice,
                transient=True,
            )
            value, whole_cancel = self._nested_settings_value(outcome)
            if whole_cancel:
                return state, True
            if value is None:
                return state, False
            if armed_value == value:
                return state, False
            if value not in {"disabled", "local"}:
                raise _ControllerAbort(
                    InteractiveError(InteractiveErrorCode.INTERNAL_ERROR)
                )
            state = update_conversation_rag_provider(
                state,
                "local" if value == "local" else None,
            )
            armed_value = value
            notice = _settings_entered_notice(
                "Conversation RAG provider",
                "Local" if value == "local" else "Disabled",
            )

    def _edit_settings_rag_automatic(
        self,
        state: SettingsState,
    ) -> tuple[SettingsState, bool]:
        """Select Off or On while enforcing the local-provider invariant."""

        notice: Notice | None = None
        armed_value: object = _SETTINGS_UNARMED
        while True:
            automatic = state.pending.system_defaults.conversation_rag_automatic
            pending_id = "on" if automatic else "off"
            outcome = self._read_settings_picker(
                Prompt(
                    "Conversation RAG Automatic",
                    instruction=_settings_value_instruction(
                        "Choose Off or On. On requires the pending Local "
                        "provider. Enter applies the value and keeps this editor "
                        "open; press Esc to return to Conversation RAG."
                    ),
                ),
                (Choice("off", "Off"), Choice("on", "On")),
                highlighted_id=pending_id,
                selected_id=pending_id,
                notice=notice,
                transient=True,
            )
            value, whole_cancel = self._nested_settings_value(outcome)
            if whole_cancel:
                return state, True
            if value is None:
                return state, False
            if armed_value == value:
                return state, False
            if value not in {"off", "on"}:
                raise _ControllerAbort(
                    InteractiveError(InteractiveErrorCode.INTERNAL_ERROR)
                )
            try:
                state = update_conversation_rag_automatic(
                    state,
                    value == "on",
                )
            except ConfigurationError:
                notice = None
                armed_value = _SETTINGS_UNARMED
                self._write_settings_validation(
                    "Automatic Conversation RAG requires the Local provider."
                )
                continue
            armed_value = value
            notice = _settings_entered_notice(
                "Conversation RAG automatic",
                "On" if value == "on" else "Off",
            )

    def _edit_settings_message_details(
        self,
        state: SettingsState,
    ) -> tuple[SettingsState, bool]:
        """Select compact or detailed message presentation for this shell."""

        notice: Notice | None = None
        armed_value: object = _SETTINGS_UNARMED
        while True:
            pending = (
                "on" if state.pending.system_defaults.show_message_details else "off"
            )
            outcome = self._read_settings_picker(
                Prompt(
                    "Message details",
                    instruction=_settings_value_instruction(
                        "Choose Off for compact messages or On for detailed "
                        "messages. Enter applies the value and keeps this "
                        "editor open; press Esc to return to Settings."
                    ),
                ),
                (
                    Choice("off", "Off"),
                    Choice("on", "On"),
                ),
                highlighted_id=pending,
                selected_id=pending,
                notice=notice,
                transient=True,
            )
            value, whole_cancel = self._nested_settings_value(outcome)
            if whole_cancel:
                return state, True
            if value is None:
                return state, False
            if value not in {"off", "on"}:
                raise _ControllerAbort(
                    InteractiveError(InteractiveErrorCode.INTERNAL_ERROR)
                )
            if armed_value == value:
                return state, False
            state = update_show_message_details(state, value == "on")
            armed_value = value
            notice = _settings_entered_notice(
                "message details",
                "On" if value == "on" else "Off",
            )

    def _confirm_storage_installation(
        self,
        plan: object,
    ) -> bool:
        """Preview the literal installer and require a separate exact YES."""

        command = getattr(plan, "command", None)
        if command not in {
            "brew install sqlite",
            "apt-get install sqlite3 libsqlite3-dev",
            "dnf install sqlite sqlite-devel",
        }:
            raise _ControllerAbort(
                InteractiveError(InteractiveErrorCode.INTERNAL_ERROR)
            )
        self._semantic_output.write_component(
            Panel(
                "SQLite prerequisite installation",
                (
                    Field("Command", command),
                    Notice(
                        "This command changes host packages and runs in the "
                        "workspace with the real terminal attached.",
                        label="installation",
                        role=SemanticRole.WARNING,
                    ),
                ),
                state=ViewState.OPEN,
            )
        )
        outcome = self._read_settings_picker(
            Prompt(
                "Install SQLite prerequisite?",
                instruction="Choose the exact YES to run the command.",
            ),
            (Choice("YES", "YES"), Choice("NO", "NO")),
            highlighted_id="NO",
            transient=True,
        )
        return outcome.kind is ControlOutcomeKind.COMMIT and outcome.text == "YES"

    def _edit_settings_provider(
        self,
        state: SettingsState,
    ) -> tuple[SettingsState, bool]:
        """Select one agent, then one provider/model/reasoning setting."""

        agent_highlight = ROSTER[0]
        field_highlights = {agent_id: "provider" for agent_id in ROSTER}
        while True:
            agent_choices: list[Choice] = []
            for agent_id in ROSTER:
                pending_profile = self._backend.preview_execution_profile(
                    state.pending,
                    agent_id,
                )
                active_profile = self._backend.execution_profile(agent_id)
                is_current = (
                    pending_profile.provider,
                    pending_profile.model,
                    pending_profile.effort,
                ) == (
                    active_profile.provider,
                    active_profile.model,
                    active_profile.effort,
                )
                agent_choices.append(
                    Choice(
                        agent_id,
                        AGENT_SPECS[agent_id].display_name,
                        form_summary=(
                            f"{'Current' if is_current else 'Pending'}: "
                            f"{_compact_execution_profile(pending_profile)}"
                        ),
                        form_summary_role=(
                            SemanticRole.MUTED if is_current else SemanticRole.WARNING
                        ),
                    )
                )
            agent_outcome = self._read_settings_picker(
                Prompt(
                    "Agents",
                    instruction=(
                        "Select an agent to configure; press Esc to return to Settings."
                    ),
                ),
                tuple(agent_choices),
                highlighted_id=agent_highlight,
                transient=True,
            )
            agent_id, whole_cancel = self._nested_settings_value(agent_outcome)
            if whole_cancel:
                return state, True
            if agent_id is None:
                return state, False
            agent_highlight = agent_id
            while True:
                pending_profile = self._backend.preview_execution_profile(
                    state.pending,
                    agent_id,
                )
                active_profile = self._backend.execution_profile(agent_id)
                provider_masked = (
                    pending_profile.provider_source
                    is ExecutionValueSource.LAUNCH_OVERRIDE
                )
                model_masked = (
                    pending_profile.model_source is ExecutionValueSource.LAUNCH_OVERRIDE
                )
                effort_masked = (
                    pending_profile.effort_source
                    is ExecutionValueSource.LAUNCH_OVERRIDE
                )
                provider_is_current = (
                    pending_profile.provider == active_profile.provider
                )
                model_is_current = pending_profile.model == active_profile.model
                effort_is_current = pending_profile.effort == active_profile.effort
                field_choices = (
                    Choice(
                        "provider",
                        "Provider",
                        form_summary=(
                            f"{'Current' if provider_is_current else 'Pending'}: "
                            f"{_provider_display_name(pending_profile.provider)}"
                        ),
                        form_summary_role=(
                            SemanticRole.MUTED
                            if provider_is_current
                            else SemanticRole.WARNING
                        ),
                        enabled=not provider_masked,
                        disabled_reason=(
                            "Set by a launch override; read-only in this shell."
                            if provider_masked
                            else None
                        ),
                    ),
                    Choice(
                        "model",
                        "Model",
                        form_summary=(
                            f"{'Current' if model_is_current else 'Pending'}: "
                            f"{_execution_value(pending_profile.model)}"
                        ),
                        form_summary_role=(
                            SemanticRole.MUTED
                            if model_is_current
                            else SemanticRole.WARNING
                        ),
                        enabled=not model_masked,
                        disabled_reason=(
                            "Set by a launch override; read-only in this shell."
                            if model_masked
                            else None
                        ),
                    ),
                    Choice(
                        "reasoning",
                        "Effort",
                        form_summary=(
                            f"{'Current' if effort_is_current else 'Pending'}: "
                            f"{_execution_value(pending_profile.effort)}"
                        ),
                        form_summary_role=(
                            SemanticRole.MUTED
                            if effort_is_current
                            else SemanticRole.WARNING
                        ),
                        enabled=not effort_masked,
                        disabled_reason=(
                            "Set by a launch override; read-only in this shell."
                            if effort_masked
                            else None
                        ),
                    ),
                )
                enabled_fields = tuple(
                    choice.identity for choice in field_choices if choice.enabled
                )
                highlighted_field = (
                    field_highlights[agent_id]
                    if field_highlights[agent_id] in enabled_fields
                    else enabled_fields[0]
                    if enabled_fields
                    else None
                )
                field_outcome = self._read_settings_picker(
                    Prompt(
                        "Agent settings",
                        scope=AGENT_SPECS[agent_id].display_name,
                        instruction=(
                            "Choose a provider, model, or effort setting; press "
                            "Esc to return to Agents."
                        ),
                    ),
                    field_choices,
                    highlighted_id=highlighted_field,
                    transient=True,
                )
                field, whole_cancel = self._nested_settings_value(field_outcome)
                if whole_cancel:
                    return state, True
                if field is None:
                    break
                field_highlights[agent_id] = field
                if field == "provider":
                    state, whole_cancel = self._edit_settings_agent_provider(
                        state,
                        agent_id,
                    )
                elif field == "model":
                    state, whole_cancel = self._edit_settings_agent_model(
                        state,
                        agent_id,
                    )
                else:
                    state, whole_cancel = self._edit_settings_agent_reasoning(
                        state,
                        agent_id,
                    )
                if whole_cancel:
                    return state, True

    def _edit_settings_agent_provider(
        self,
        state: SettingsState,
        agent_id: str,
    ) -> tuple[SettingsState, bool]:
        """Edit one agent's provider without changing either provider's settings."""

        if self._settings_field_is_launch_masked(state, agent_id, "provider"):
            self._write_settings_launch_override_mask("Provider")
            return state, False
        if self._auth_coordinator is not None:
            return self._edit_authenticated_settings_agent_provider(
                state,
                agent_id,
            )

        notice: Notice | None = None
        armed_value: object = _SETTINGS_UNARMED
        while True:
            current = _effective_settings_provider(
                state,
                agent_id,
                provider_overrides=self._backend.backend_overrides,
            )
            outcome = self._read_settings_picker(
                Prompt(
                    "Provider",
                    scope=AGENT_SPECS[agent_id].display_name,
                    instruction=_settings_value_instruction(
                        "Choose Codex or Antigravity. Enter applies the value "
                        "and keeps this editor open; press Esc to return to "
                        "Agent settings."
                    ),
                ),
                (
                    Choice(
                        "codex",
                        "Codex",
                        description=(
                            "Current saved provider."
                            if current == "codex"
                            else "Available provider."
                        ),
                    ),
                    Choice(
                        "antigravity",
                        "Antigravity",
                        description=(
                            "Current saved provider."
                            if current == "antigravity"
                            else "Available provider."
                        ),
                    ),
                ),
                highlighted_id=current,
                selected_id=current,
                notice=notice,
                transient=True,
            )
            provider, whole_cancel = self._nested_settings_value(outcome)
            if whole_cancel or provider is None:
                return state, whole_cancel
            if armed_value == provider:
                return state, False
            state = update_agent_backend(state, agent_id, provider)
            armed_value = _effective_settings_provider(
                state,
                agent_id,
                provider_overrides=self._backend.backend_overrides,
            )
            notice = _settings_entered_notice(
                "Provider",
                _provider_display_name(provider),
            )

    def _edit_authenticated_settings_agent_provider(
        self,
        state: SettingsState,
        agent_id: str,
    ) -> tuple[SettingsState, bool]:
        """Assign one route only after exact freshly probed authentication."""

        coordinator = self._auth_coordinator
        assert coordinator is not None
        notice: Notice | None = None
        armed_value: object = _SETTINGS_UNARMED
        while True:
            snapshot = self._run_auth_probe(coordinator)
            current = _effective_settings_provider(
                state,
                agent_id,
                provider_overrides=self._backend.backend_overrides,
            )
            choices = coordinator.choices(
                snapshot,
                selected_provider=current,
            )
            current_choice = next(
                choice for choice in choices if choice.identity == current
            )
            selected_id = current if current_choice.enabled else None
            highlighted_id = selected_id or next(
                (choice.identity for choice in choices if choice.enabled),
                None,
            )
            outcome = self._read_settings_picker(
                Prompt(
                    "Provider",
                    scope=AGENT_SPECS[agent_id].display_name,
                    instruction=_settings_value_instruction(
                        "Choose an authenticated provider, or select an available "
                        "provider to authenticate it first. Enter applies the "
                        "value and keeps this editor open; press Esc to return "
                        "to Agent settings."
                    ),
                ),
                choices,
                highlighted_id=highlighted_id,
                selected_id=selected_id,
                notice=notice,
                transient=True,
            )
            provider, whole_cancel = self._nested_settings_value(outcome)
            if whole_cancel or provider is None:
                return state, whole_cancel

            notice = None
            snapshot = self._run_auth_probe(coordinator)
            if snapshot.is_authenticated(provider):
                if armed_value == provider:
                    return state, False
                state = update_agent_backend(state, agent_id, provider)
                armed_value = _effective_settings_provider(
                    state,
                    agent_id,
                    provider_overrides=self._backend.backend_overrides,
                )
                notice = _settings_entered_notice(
                    "Provider",
                    _provider_display_name(provider),
                )
                continue

            result = coordinator.run(
                snapshot,
                presentation=InputPresentation.FORM,
                selected_provider=provider,
            )
            if result.kind is InteractiveAuthResultKind.CANCEL:
                armed_value = _SETTINGS_UNARMED
                continue
            if result.kind in {
                InteractiveAuthResultKind.INTERRUPT,
                InteractiveAuthResultKind.EOF,
            }:
                return state, True

            snapshot = self._run_auth_probe(coordinator)
            if (
                result.kind is InteractiveAuthResultKind.AUTHENTICATED
                and result.provider == provider
                and snapshot.is_authenticated(provider)
            ):
                if armed_value == provider:
                    return state, False
                state = update_agent_backend(state, agent_id, provider)
                armed_value = _effective_settings_provider(
                    state,
                    agent_id,
                    provider_overrides=self._backend.backend_overrides,
                )
                notice = _settings_entered_notice(
                    "Provider",
                    _provider_display_name(provider),
                )
                continue

            armed_value = _SETTINGS_UNARMED
            self._write_notice(
                (
                    f"{_provider_display_name(provider)} was not assigned because "
                    "fresh authentication was not confirmed."
                ),
                label="authentication required",
                role=SemanticRole.ERROR,
                state=ViewState.ERROR,
            )

    def _run_settings_option_load(
        self,
        operation: Callable[[], _PickerLoadT],
    ) -> _PickerLoadT:
        """Run one Settings option read under transient terminal progress."""

        return self._run_picker_preparation(operation)

    def _run_auth_probe(
        self,
        coordinator: InteractiveAuthCoordinator,
    ) -> ProviderAuthSnapshot:
        """Run one fresh auth probe through its single progress owner."""

        if isinstance(coordinator, InteractiveAuthCoordinator):
            return coordinator.probe_all()
        return self._run_picker_preparation(coordinator.probe_all)

    def _run_picker_preparation(
        self,
        operation: Callable[[], _PickerLoadT],
    ) -> _PickerLoadT:
        """Run one blocking data-to-picker read under transient progress."""

        if not callable(operation):
            raise TypeError("picker preparation operation must be callable")
        runner = self._runner
        if runner is None:
            return operation()

        async def load() -> _PickerLoadT:
            """Keep the Runner responsive while the bounded read blocks."""

            return await asyncio.to_thread(operation)

        try:
            return runner.run(self._advisory_progress.run(load()))
        except AdvisoryProgressError:
            raise _ControllerAbort(
                InteractiveError(InteractiveErrorCode.INTERNAL_ERROR)
            ) from None

    def _edit_settings_agent_model(
        self,
        state: SettingsState,
        agent_id: str,
    ) -> tuple[SettingsState, bool]:
        """Select one discovered active-provider model or a custom value."""

        if self._settings_field_is_launch_masked(state, agent_id, "model"):
            self._write_settings_launch_override_mask("Model")
            return state, False
        provider = _effective_settings_provider(
            state,
            agent_id,
            provider_overrides=self._backend.backend_overrides,
        )
        antigravity = provider == "antigravity"
        provider_name = _provider_display_name(provider)
        discovery = self._discover_settings_catalog(provider)
        catalog = discovery.catalog
        highlighted_id: str | None = None
        notice: Notice | None = None
        armed_value: object = _SETTINGS_UNARMED
        while True:
            agent = state.pending.agent_configs[agent_id]
            pending = agent.antigravity_model if antigravity else agent.codex_model
            choices: list[Choice] = [
                Choice(
                    _SETTINGS_INHERIT_ID,
                    "Inherit",
                    description=(
                        f"Use the workspace {provider_name} model, then the "
                        "provider default."
                    ),
                )
            ]
            discovered_values: dict[str, tuple[str, str]] = {}
            selected_id = _SETTINGS_INHERIT_ID
            matched_model: ProviderModel | None = None
            if catalog is not None:
                for index, model in enumerate(catalog.models):
                    choice_id = f"{_SETTINGS_MODEL_ID_PREFIX}{index}"
                    discovered_values[choice_id] = (
                        model.identity,
                        model.label,
                    )
                    choices.append(
                        Choice(
                            choice_id,
                            model.label,
                            description=_settings_model_description(model),
                        )
                    )
                    if pending == model.identity:
                        selected_id = choice_id
                        matched_model = model
            if pending is not None and matched_model is None:
                choices.append(
                    Choice(
                        _SETTINGS_CURRENT_CUSTOM_MODEL_ID,
                        "Current custom",
                        description=_settings_current_model_description(pending),
                    )
                )
                selected_id = _SETTINGS_CURRENT_CUSTOM_MODEL_ID
            choices.append(
                Choice(
                    _SETTINGS_CUSTOM_MODEL_ID,
                    "Custom model",
                    description=(f"Enter an exact safe {provider_name} model value."),
                )
            )
            choice_ids = {choice.identity for choice in choices}
            if highlighted_id not in choice_ids:
                highlighted_id = selected_id
            outcome = self._read_settings_picker(
                Prompt(
                    "Model",
                    scope=(f"{AGENT_SPECS[agent_id].display_name} · {provider_name}"),
                    instruction=_settings_value_instruction(
                        f"Choose a model for the pending {provider_name} "
                        "provider. Enter applies a value and keeps Model open; "
                        "Custom model opens its editor. Press Esc to return to "
                        "Agent settings."
                    ),
                ),
                tuple(choices),
                highlighted_id=highlighted_id,
                selected_id=selected_id,
                notice=(
                    notice
                    or _settings_catalog_notice(
                        provider,
                        discovery.state,
                        model_picker=True,
                    )
                ),
                transient=True,
            )
            value, whole_cancel = self._nested_settings_value(outcome)
            if whole_cancel:
                return state, True
            if value is None:
                return state, False
            highlighted_id = value
            notice = None
            updater = (
                update_agent_antigravity_model
                if antigravity
                else update_agent_codex_model
            )
            if value == _SETTINGS_CURRENT_CUSTOM_MODEL_ID:
                display_value = (
                    pending
                    if pending is not None
                    and not pending.isspace()
                    and len(pending.encode("utf-8")) <= 256
                    else "Current custom"
                )
                if armed_value == pending:
                    return state, False
                state = updater(state, agent_id, pending)
                armed_value = pending
                notice = _settings_entered_notice("Model", display_value)
                continue
            if value == _SETTINGS_CUSTOM_MODEL_ID:
                state, whole_cancel = self._edit_settings_custom_model(
                    state,
                    agent_id,
                    provider,
                )
                if whole_cancel:
                    return state, True
                armed_value = _SETTINGS_UNARMED
                continue
            if value == _SETTINGS_INHERIT_ID:
                if armed_value is None:
                    return state, False
                state = updater(state, agent_id, None)
                armed_value = None
                notice = _settings_entered_notice("Model", "Inherit")
                continue
            if value not in discovered_values:
                raise _ControllerAbort(
                    InteractiveError(InteractiveErrorCode.INTERNAL_ERROR)
                )
            model_value, display_value = discovered_values[value]
            if armed_value == model_value:
                return state, False
            state = updater(state, agent_id, model_value)
            agent = state.pending.agent_configs[agent_id]
            armed_value = agent.antigravity_model if antigravity else agent.codex_model
            notice = _settings_entered_notice("Model", display_value)

    def _edit_settings_custom_model(
        self,
        state: SettingsState,
        agent_id: str,
        provider: str,
    ) -> tuple[SettingsState, bool]:
        """Read safe custom models until return or whole-settings cancellation."""

        antigravity = provider == "antigravity"
        provider_name = _provider_display_name(provider)
        notice: Notice | None = None
        armed_value: object = _SETTINGS_UNARMED
        while True:
            agent = state.pending.agent_configs[agent_id]
            pending = agent.antigravity_model if antigravity else agent.codex_model
            outcome = self._read_settings_prompt(
                Prompt(
                    "Custom model",
                    scope=(f"{AGENT_SPECS[agent_id].display_name} · {provider_name}"),
                    instruction=_settings_value_instruction(
                        f"Enter an exact {provider_name} model, or leave blank "
                        "to inherit the workspace setting. Enter applies the "
                        "value and keeps this editor open; press Esc to return "
                        "to Model."
                    ),
                    default=(
                        pending
                        if pending is not None and not pending.isspace()
                        else None
                    ),
                ),
                context=(notice,) if notice is not None else (),
                transient=True,
            )
            value, whole_cancel = self._nested_settings_value(outcome)
            if whole_cancel:
                return state, True
            if value is None:
                return state, False
            normalized_value = None if value == "" else value
            if armed_value == normalized_value:
                return state, False
            try:
                updater = (
                    update_agent_antigravity_model
                    if antigravity
                    else update_agent_codex_model
                )
                state = updater(
                    state,
                    agent_id,
                    normalized_value,
                )
            except ConfigurationError:
                notice = None
                armed_value = _SETTINGS_UNARMED
                self._write_settings_validation(
                    f"{provider_name} model must be safe nonempty text, "
                    "or blank to inherit."
                )
                continue
            agent = state.pending.agent_configs[agent_id]
            armed_value = agent.antigravity_model if antigravity else agent.codex_model
            notice = _settings_entered_notice(
                "Custom model",
                "Inherit" if armed_value is None else str(armed_value),
            )

    def _edit_settings_agent_reasoning(
        self,
        state: SettingsState,
        agent_id: str,
    ) -> tuple[SettingsState, bool]:
        """Select exact discovered effort for the pending effective model."""

        if self._settings_field_is_launch_masked(state, agent_id, "effort"):
            self._write_settings_launch_override_mask("Effort")
            return state, False
        agent = state.pending.agent_configs[agent_id]
        defaults = state.pending.system_defaults
        provider = _effective_settings_provider(
            state,
            agent_id,
            provider_overrides=self._backend.backend_overrides,
        )
        antigravity = provider == "antigravity"
        provider_name = _provider_display_name(provider)
        pending = (
            agent.antigravity_effort if antigravity else agent.codex_reasoning_effort
        )
        inherited_effort = (
            defaults.antigravity_effort
            if antigravity
            else defaults.codex_reasoning_effort
        )
        effective_model = (
            (agent.antigravity_model or defaults.antigravity_model)
            if antigravity
            else (agent.codex_model or defaults.codex_model)
        )
        discovery = self._discover_settings_catalog(provider)
        catalog = discovery.catalog
        selected_model: ProviderModel | None = None
        efforts: tuple[ProviderEffort, ...] = ()
        if catalog is not None:
            if antigravity:
                efforts = catalog.models[0].efforts
            else:
                selected_model = catalog.model_for(effective_model)
                if selected_model is not None:
                    efforts = selected_model.efforts

        if inherited_effort is not None:
            inherit_description = (
                f"Use the workspace {provider_name} effort: {inherited_effort}."
            )
        elif selected_model is not None and selected_model.default_effort is not None:
            inherit_description = (
                f"Use the selected model default: {selected_model.default_effort}."
            )
        else:
            inherit_description = "Use the workspace setting, then provider default."

        catalog_notice = _settings_catalog_notice(
            provider,
            discovery.state,
            model_picker=False,
        )
        if discovery.ready and not antigravity and selected_model is None:
            catalog_notice = Notice(
                "The inherited or selected Codex model does not match this "
                "catalog. Choose a matching Model before selecting a model-"
                "specific effort; pending settings were preserved.",
                label="model-specific effort",
                role=SemanticRole.WARNING,
            )
        elif discovery.ready and not efforts:
            catalog_notice = Notice(
                f"The selected {provider_name} model reports no effort choices. "
                "Inherit or keep the current compatibility value.",
                label="effort catalog",
                role=SemanticRole.WARNING,
            )

        highlighted_id: str | None = None
        notice: Notice | None = None
        armed_value: object = _SETTINGS_UNARMED
        while True:
            agent = state.pending.agent_configs[agent_id]
            pending = (
                agent.antigravity_effort
                if antigravity
                else agent.codex_reasoning_effort
            )
            choices: list[Choice] = [
                Choice(
                    _SETTINGS_INHERIT_ID,
                    "Inherit",
                    description=inherit_description,
                )
            ]
            effort_values: dict[str, tuple[str, str]] = {}
            selected_id = _SETTINGS_INHERIT_ID
            for index, effort in enumerate(efforts):
                choice_id = f"{_SETTINGS_EFFORT_ID_PREFIX}{index}"
                effort_values[choice_id] = (
                    effort.identity,
                    effort.label,
                )
                choices.append(
                    Choice(
                        choice_id,
                        effort.label,
                        description=_settings_effort_description(
                            effort,
                            is_default=(
                                selected_model is not None
                                and selected_model.default_effort == effort.identity
                            ),
                        ),
                    )
                )
                if pending == effort.identity:
                    selected_id = choice_id
            if pending is not None and pending not in {
                effort_value for effort_value, _display_value in effort_values.values()
            }:
                choices.append(
                    Choice(
                        _SETTINGS_CURRENT_EFFORT_ID,
                        "Current compatibility",
                        description=f"Keep exact pending effort: {pending}",
                    )
                )
                selected_id = _SETTINGS_CURRENT_EFFORT_ID

            choice_ids = {choice.identity for choice in choices}
            if highlighted_id not in choice_ids:
                highlighted_id = selected_id
            outcome = self._read_settings_picker(
                Prompt(
                    "Effort",
                    scope=(f"{AGENT_SPECS[agent_id].display_name} · {provider_name}"),
                    instruction=_settings_value_instruction(
                        f"Choose effort for the pending {provider_name} "
                        "provider and effective model. Enter applies the value "
                        "and keeps this editor open; press Esc to return to "
                        "Agent settings."
                    ),
                ),
                tuple(choices),
                highlighted_id=highlighted_id,
                selected_id=selected_id,
                notice=notice or catalog_notice,
                transient=True,
            )
            value, whole_cancel = self._nested_settings_value(outcome)
            if whole_cancel:
                return state, True
            if value is None:
                return state, False
            highlighted_id = value
            notice = None
            updater = (
                update_agent_antigravity_effort
                if antigravity
                else update_agent_codex_reasoning_effort
            )
            if value == _SETTINGS_CURRENT_EFFORT_ID:
                assert pending is not None
                if armed_value == pending:
                    return state, False
                state = updater(state, agent_id, pending)
                armed_value = pending
                notice = _settings_entered_notice("Effort", pending)
                continue
            if value == _SETTINGS_INHERIT_ID:
                if armed_value is None:
                    return state, False
                state = updater(state, agent_id, None)
                armed_value = None
                notice = _settings_entered_notice("Effort", "Inherit")
                continue
            if value not in effort_values:
                raise _ControllerAbort(
                    InteractiveError(InteractiveErrorCode.INTERNAL_ERROR)
                )
            effort_value, display_value = effort_values[value]
            if armed_value == effort_value:
                return state, False
            state = updater(state, agent_id, effort_value)
            agent = state.pending.agent_configs[agent_id]
            armed_value = (
                agent.antigravity_effort
                if antigravity
                else agent.codex_reasoning_effort
            )
            notice = _settings_entered_notice("Effort", display_value)

    def _discover_settings_catalog(
        self,
        provider: str,
    ) -> ProviderCatalogDiscovery:
        """Contain collaborator failures behind one fixed discovery state."""

        try:
            discovery = self._run_settings_option_load(
                lambda: self._provider_catalog.discover(provider)
            )
        except _ControllerAbort:
            raise
        except Exception:
            return ProviderCatalogDiscovery(
                provider,
                ProviderCatalogState.FAILED,
            )
        if (
            type(discovery) is not ProviderCatalogDiscovery
            or discovery.provider != provider
        ):
            return ProviderCatalogDiscovery(
                provider,
                ProviderCatalogState.FAILED,
            )
        return discovery

    @staticmethod
    def _require_settings_commit(outcome: ControlOutcome) -> str:
        """Return a validated settings commit or abort on protocol drift."""

        if (
            type(outcome) is not ControlOutcome
            or outcome.kind is not ControlOutcomeKind.COMMIT
            or outcome.text is None
        ):
            raise _ControllerAbort(
                InteractiveError(InteractiveErrorCode.INTERNAL_ERROR)
            )
        return outcome.text

    def _nested_settings_value(
        self,
        outcome: ControlOutcome,
    ) -> tuple[str | None, bool]:
        """Classify a nested commit, return, or whole-settings cancel."""

        if type(outcome) is not ControlOutcome:
            raise _ControllerAbort(
                InteractiveError(InteractiveErrorCode.INTERNAL_ERROR)
            )
        if outcome.kind in {
            ControlOutcomeKind.INTERRUPT,
            ControlOutcomeKind.EOF,
        }:
            return None, True
        if outcome.kind is ControlOutcomeKind.CANCEL:
            return None, False
        return self._require_settings_commit(outcome), False

    @staticmethod
    def _settings_panel(
        state: SettingsState,
        *,
        notice: Notice | None = None,
        provider_overrides: Mapping[str, str] | None = None,
    ) -> Panel:
        """Project a stable current/pending settings diff for confirmation."""

        changed_rows = tuple(
            row
            for row in _settings_projection(
                state,
                provider_overrides=provider_overrides,
            )
            if row[3] != row[4]
        )
        current_fields = tuple(
            Field(
                label,
                f"{current_value} (Changed)",
                role=SemanticRole.DEFAULT,
            )
            for _root_id, _agent_id, label, current_value, _pending_value in changed_rows
        )
        pending_fields = tuple(
            Field(
                label,
                f"{pending_value} (Changed)",
                role=SemanticRole.WARNING,
            )
            for _root_id, _agent_id, label, _current_value, pending_value in changed_rows
        )
        if changed_rows:
            live_changes = any(
                root_id
                not in {
                    "conversation_storage",
                    "conversation_rag_provider",
                    "conversation_rag_automatic",
                }
                for root_id, *_rest in changed_rows
            )
            storage_changes = any(
                root_id == "conversation_storage" for root_id, *_rest in changed_rows
            )
            rag_changes = any(
                root_id
                in {
                    "conversation_rag_provider",
                    "conversation_rag_automatic",
                }
                for root_id, *_rest in changed_rows
            )
            activation_parts: list[str] = []
            if live_changes:
                activation_parts.append(
                    "Runtime changes apply to this shell after Apply Changes."
                )
            if storage_changes:
                activation_parts.append(
                    "Desired storage changes apply to the next shell."
                )
            if rag_changes:
                activation_parts.append(
                    "Conversation RAG changes apply to the next shell; this "
                    "running shell retains its current RAG service and "
                    "automatic policy."
                )
            panel_items: list[Section | Notice] = [
                Section("Current", current_fields),
                Section("Pending", pending_fields),
                Notice(
                    " ".join(activation_parts),
                    label="activation",
                    role=SemanticRole.INFO,
                ),
            ]
        else:
            panel_items = [
                Notice(
                    "No pending settings changes.",
                    label="no changes",
                    role=SemanticRole.MUTED,
                )
            ]
        if notice is not None:
            panel_items.append(notice)

        return Panel(
            "Settings",
            tuple(panel_items),
            state=ViewState.OPEN,
        )

    def _settings_field_is_launch_masked(
        self,
        state: SettingsState,
        agent_id: str,
        field: str,
    ) -> bool:
        """Return whether one pending effective value is fixed at launch."""

        profile = self._backend.preview_execution_profile(
            state.pending,
            agent_id,
        )
        sources = {
            "provider": profile.provider_source,
            "model": profile.model_source,
            "effort": profile.effort_source,
        }
        if field not in sources:
            raise ValueError("settings execution field is unknown")
        return sources[field] is ExecutionValueSource.LAUNCH_OVERRIDE

    def _write_settings_launch_override_mask(self, field: str) -> None:
        """Explain one disabled launch-owned execution value without mutation."""

        self._semantic_output.write_component(
            Notice(
                (
                    f"{field} is fixed by a launch override and is read-only in "
                    "this running shell. Remove the launch override to edit it."
                ),
                label="launch override",
                role=SemanticRole.MUTED,
                state=ViewState.DISABLED,
            )
        )

    def _write_settings_validation(self, text: str) -> None:
        """Render one trusted validation notice while retaining pending state."""

        self._semantic_output.write_component(
            Notice(
                text,
                label="validation",
                role=SemanticRole.ERROR,
                state=ViewState.ERROR,
            )
        )

    def _write_settings_error(self, kind: SettingsErrorKind) -> None:
        """Render one fixed persistence failure without raw diagnostics."""

        messages = {
            SettingsErrorKind.VALIDATION: (
                "Pending settings are invalid; correct them before saving."
            ),
            SettingsErrorKind.CONFLICT: (
                "Settings changed on disk; cancel and reopen /settings."
            ),
            SettingsErrorKind.WRITE: (
                "Settings could not be saved; pending values were retained."
            ),
            SettingsErrorKind.ROLLBACK: (
                "Settings rollback failed; inspect both configuration files."
            ),
            SettingsErrorKind.RECOVERY_REQUIRED: (
                "Settings were saved, but this shell could not confirm activation. "
                "Stop using this shell and restart it to recover the exact "
                "persisted runtime configuration."
            ),
        }
        self._semantic_output.write_component(
            Notice(
                messages[kind],
                label="settings error",
                role=SemanticRole.ERROR,
                state=ViewState.ERROR,
            )
        )

    def _write_storage_selection_error(
        self,
        failure: ConversationStorageSelectionFailure,
    ) -> None:
        """Render one sanitized storage readiness or persistence recovery."""

        messages = {
            ConversationStorageSelectionFailure.INVALID_REQUEST: (
                "The requested conversation storage selection is invalid."
            ),
            ConversationStorageSelectionFailure.WORKSPACE_INVALID: (
                "Conversation storage could not inspect this workspace."
            ),
            ConversationStorageSelectionFailure.COORDINATION_FAILED: (
                "Conversation storage coordination is unavailable or busy."
            ),
            ConversationStorageSelectionFailure.INSTALLATION_REQUIRED: (
                "SQLite installation was not confirmed; settings were preserved."
            ),
            ConversationStorageSelectionFailure.INSTALLATION_UNSUPPORTED: (
                "SQLite installation is not supported on this exact platform."
            ),
            ConversationStorageSelectionFailure.INSTALLATION_DISALLOWED: (
                "SQLite host installation is disabled in this environment."
            ),
            ConversationStorageSelectionFailure.INSTALLATION_FAILED: (
                "SQLite installation failed; inspect the installer diagnostics."
            ),
            ConversationStorageSelectionFailure.INTERPRETER_UNAVAILABLE: (
                "This Python interpreter still lacks functional SQLite; "
                "rebuild or reinstall Python with SQLite support."
            ),
            ConversationStorageSelectionFailure.PROVISIONING_FAILED: (
                "SQLite conversation storage is unavailable or invalid."
            ),
            ConversationStorageSelectionFailure.TRANSFER_REQUIRED: (
                "Transfer the active conversations to the requested backend first."
            ),
            ConversationStorageSelectionFailure.PARITY_MISMATCH: (
                "Fresh conversation archive parity does not match."
            ),
            ConversationStorageSelectionFailure.CONFIGURATION_INVALID: (
                "Pending settings are invalid; correct them before saving."
            ),
            ConversationStorageSelectionFailure.CONFIGURATION_CONFLICT: (
                "Settings changed on disk; cancel and reopen /settings."
            ),
            ConversationStorageSelectionFailure.CONFIGURATION_WRITE_FAILED: (
                "Settings could not be saved; prior configuration was preserved."
            ),
        }
        self._semantic_output.write_component(
            Notice(
                messages[failure],
                label="conversation storage",
                role=SemanticRole.ERROR,
                state=ViewState.ERROR,
            )
        )

    def _write_settings_cancelled(self, state: SettingsState) -> None:
        """Render one cancellation notice only when pending changes are discarded."""

        if state.pending == state.baseline.configuration:
            return

        self._semantic_output.write_component(
            Notice(
                "Pending changes discarded.",
                label="cancelled",
                role=SemanticRole.WARNING,
                state=ViewState.CANCELLED,
            )
        )

    def _write_agent_roster(self) -> None:
        """Render canonical read-only agent metadata and configuration status."""

        profiles = {
            profile.agent_id: profile for profile in self._backend.execution_profiles()
        }
        records = tuple(
            Record(
                route.agent_id,
                title=AGENT_SPECS[route.agent_id].display_name,
                fields=(
                    Field("ID", route.agent_id),
                    Field("Advisory domain", route.advisory_domain),
                    self._execution_profile_field(
                        profiles[route.agent_id],
                        role=SemanticRole.INFO,
                        label="Execution",
                    ),
                    Field(
                        "Confirmation",
                        self._backend.execution_profile_confirmation(route.agent_id),
                        role=(
                            SemanticRole.SUCCESS
                            if self._backend.execution_profile_confirmation(
                                route.agent_id
                            )
                            == "confirmed"
                            else SemanticRole.WARNING
                        ),
                    ),
                ),
                role=SemanticRole.AGENT,
                state=ViewState.COMPLETED,
            )
            for route in AGENT_ROUTES
        )
        self._semantic_output.write_component(
            ListView(
                "Agents",
                records,
                total_count=len(records),
                state=ViewState.COMPLETED,
            )
        )

    def _write_execution_roster_status(self) -> None:
        """Render full exact profiles, sources, confirmation, and thread state."""

        profiles = self._backend.execution_profiles()
        route_generations = self._backend.provider_route_generations()
        records: list[Record] = []
        for profile in profiles:
            confirmation = self._backend.execution_profile_confirmation(
                profile.agent_id
            )
            thread_status = self._backend.execution_profile_thread_status(
                profile.agent_id
            )
            records.append(
                Record(
                    profile.agent_id,
                    title=AGENT_SPECS[profile.agent_id].display_name,
                    fields=(
                        Field(
                            "Provider",
                            _provider_display_name(profile.provider),
                            role=SemanticRole.INFO,
                        ),
                        Field(
                            "Provider source",
                            _execution_source_label(profile.provider_source),
                            role=SemanticRole.MUTED,
                        ),
                        Field("Model", _execution_value(profile.model)),
                        Field(
                            "Model source",
                            _execution_source_label(profile.model_source),
                            role=SemanticRole.MUTED,
                        ),
                        Field("Effort", _execution_value(profile.effort)),
                        Field(
                            "Effort source",
                            _execution_source_label(profile.effort_source),
                            role=SemanticRole.MUTED,
                        ),
                        Field(
                            "Confirmation",
                            confirmation,
                            role=_confirmation_role(confirmation),
                        ),
                        Field(
                            "Thread",
                            thread_status.replace("_", " "),
                            role=_thread_status_role(thread_status),
                        ),
                        Field(
                            "Route generation",
                            str(route_generations[profile.agent_id]),
                            role=SemanticRole.MUTED,
                        ),
                    ),
                    role=SemanticRole.AGENT,
                    state=ViewState.COMPLETED,
                )
            )
        self._semantic_output.write_component(
            ListView(
                "Execution profiles",
                tuple(records),
                total_count=len(records),
                state=ViewState.COMPLETED,
            )
        )

    def _write_runtime_status(self) -> None:
        """Render a read-only lifecycle record followed by detailed scope facts."""

        default_session = self._context.default_session_id
        attached_session = self._attached_session_id
        candidate = attached_session or default_session
        if candidate is None:
            state = SessionLifecycleState(
                session_id="none",
                durable_state=SessionDurableState.ABSENT,
                access_state=SessionAccessState.CLAIMABLE,
                owner_hostname=None,
                owner_pid=None,
                created_at=None,
                updated_at=None,
            )
            provider_ready = False
            default_conversation = None
        else:
            try:
                state = self._backend.get_session_lifecycle(candidate)
                require_lifecycle_state(state, candidate)
                provider_ready = self._provider_work_is_ready(state)
                default_conversation = load_default_conversation(
                    self._workspace,
                    candidate,
                )
            except Exception as exc:
                self._present_session_failure(exc, candidate)
                return

        focus = self._focused_conversation
        focused_id = (
            focus.conversation_id
            if attached_session is not None and focus is not None
            else None
        )
        next_action = next_session_action(
            state if candidate is not None else None,
            attached=(attached_session == candidate and candidate is not None),
            focused=(focused_id is not None),
            provider_ready=provider_ready,
        )
        self._semantic_output.write_component(
            Record(
                "session",
                fields=(
                    Field("Session", candidate or "none"),
                    Field("Durable state", state.durable_state.value),
                    Field("Process access", state.access_state.value),
                    Field("Default session", default_session or "none"),
                    Field("Current attachment", attached_session or "none"),
                    Field("Default conversation", default_conversation or "none"),
                    Field("Current focus", focused_id or "none"),
                    Field("Provider readiness", "yes" if provider_ready else "no"),
                    Field("Next action", next_action),
                ),
            )
        )
        self._write_execution_roster_status()

        if attached_session is None:
            return
        if type(self._collaboration_history_service) is not CollaborationHistoryService:
            raise _ControllerAbort(
                InteractiveError(InteractiveErrorCode.INTERNAL_ERROR)
            )
        if focus is None:
            self._write_notice(
                "Conversation focus: none | Context: unavailable.",
                label="context",
                role=SemanticRole.MUTED,
                state=ViewState.EMPTY,
            )
            return
        try:
            current = self._conversation_catalog.get(
                attached_session,
                focus.conversation_id,
            )
            current = validate_catalog_metadata(
                current,
                attached_session,
                expected_conversation_id=focus.conversation_id,
            )
            if current.status is ConversationStatus.ARCHIVED:
                self._write_error(
                    InteractiveError(InteractiveErrorCode.CONVERSATION_ARCHIVED)
                )
                return
            records, projection = self._read_focused_history_snapshot(current)
        except ConversationNotFoundError as exc:
            self._present_conversation_failure(exc)
            return
        except _ControllerAbort:
            raise
        except Exception as exc:
            self._present_transcript_service_failure(exc)
            return
        self._semantic_output.write_component(
            Record(
                "conversation",
                fields=(
                    Field("Current focus", current.conversation_id),
                    Field("Subject", current.subject),
                    Field("Status", current.status.value),
                    Field("Revision", f"{current.subject_revision}."),
                ),
            ),
        )
        self._semantic_output.write_component(
            Record(
                "context",
                fields=(
                    Field("History records", str(len(records))),
                    Field("Last sequence", str(projection.last_sequence)),
                    Field(
                        "Current state",
                        self._collaboration_state_meaning(projection)[0],
                    ),
                    Field(
                        "User meaning", self._collaboration_state_meaning(projection)[1]
                    ),
                    Field("Next safe action", projection.next_safe_action.value),
                    Field(
                        "Next action meaning",
                        self._collaboration_next_action_meaning(
                            projection.next_safe_action
                        ),
                    ),
                ),
                state=(ViewState.EMPTY if not records else ViewState.COMPLETED),
            ),
        )
        self._semantic_output.write_component(
            Record(
                "result",
                fields=(Field("Search", "available, active-only"),),
                state=ViewState.COMPLETED,
            )
        )

    def _apply_attached_runtime_directive(
        self,
        action: InteractiveAction,
    ) -> None:
        """Persist one parsed directive and acknowledge only its changed field.

        Language, canonical per-agent persona, and full reset actions require an
        attachment but never a conversation focus or provider. A malformed
        runtime return fails closed; typed configuration and workspace failures
        retain their established safe interactive categories.
        """

        if self._attached_session_id is None:
            self._write_error(
                InteractiveError(InteractiveErrorCode.SESSION_NOT_ATTACHED)
            )
            return
        if action.kind is ActionKind.SET_LANGUAGE:
            assert action.argument is not None
            directive = f"[SYS: LANG={action.argument}]"
            acknowledgement = f"Language override set to {action.argument}."
        elif action.kind is ActionKind.SET_PERSONA:
            assert action.agent_id is not None
            assert action.argument is not None
            directive = f"[OVERRIDE: {action.agent_id} Persona={action.argument}]"
            acknowledgement = (
                f"Persona override for {action.agent_id} set to {action.argument}."
            )
        else:
            assert action.kind is ActionKind.RESET
            directive = "[RESET: ALL]"
            acknowledgement = "Runtime overrides reset."

        try:
            updated = self._runtime.apply_directive(
                self._attached_session_id,
                directive,
            )
        except (DirectiveError, ConfigurationError):
            self._write_error(
                InteractiveError(InteractiveErrorCode.CONFIGURATION_INVALID)
            )
            return
        except WorkspaceSecurityError:
            self._present_memory_error(
                InteractiveError(InteractiveErrorCode.WORKSPACE_UNSAFE)
            )
            return
        except WorkspaceVersionUnsupportedError:
            self._present_memory_error(
                InteractiveError(InteractiveErrorCode.WORKSPACE_VERSION_UNSUPPORTED)
            )
            return
        except (WorkspaceFormatError, OSError):
            self._present_memory_error(
                InteractiveError(InteractiveErrorCode.WORKSPACE_INVALID)
            )
            return
        except Exception:
            raise _ControllerAbort(
                InteractiveError(InteractiveErrorCode.INTERNAL_ERROR)
            ) from None
        if not self._directive_result_matches_action(updated, action):
            raise _ControllerAbort(
                InteractiveError(InteractiveErrorCode.INTERNAL_ERROR)
            )
        self._write_notice(
            acknowledgement,
            label="result",
            role=SemanticRole.SUCCESS,
            state=ViewState.COMPLETED,
        )

    @staticmethod
    def _directive_result_matches_action(
        result: object,
        action: InteractiveAction,
    ) -> bool:
        """Return whether a runtime directive result confirms the exact action.

        The validation accepts only the canonical override mapping shape and
        never renders or retains unrelated override values. It performs no I/O
        and treats malformed collaborator data as an internal protocol failure.
        """

        if type(result) is not dict or set(result) != {
            "language",
            "persona",
            "agents",
        }:
            return False
        if result["language"] is not None and not isinstance(result["language"], str):
            return False
        if result["persona"] is not None and not isinstance(result["persona"], str):
            return False
        agents = result["agents"]
        if type(agents) is not dict or set(agents).difference(ROSTER):
            return False
        for agent_id, values in agents.items():
            if (
                type(agent_id) is not str
                or type(values) is not dict
                or set(values) != {"language", "persona"}
                or any(
                    value is not None and not isinstance(value, str)
                    for value in values.values()
                )
            ):
                return False

        if action.kind is ActionKind.SET_LANGUAGE:
            return result["language"] == action.argument
        if action.kind is ActionKind.SET_PERSONA:
            assert action.agent_id is not None
            values = agents.get(action.agent_id)
            return type(values) is dict and values["persona"] == action.argument
        return (
            action.kind is ActionKind.RESET
            and result["language"] is None
            and result["persona"] is None
            and not agents
        )

    def _write_focused_conversation_history(
        self,
        action: InteractiveAction,
    ) -> None:
        """Render structured collaboration history and current durable state.

        The command requires an attached active focus. It reads the
        authoritative event store exactly once and validates the full history
        before rendering a requested suffix. No cursor, mutation, search, or
        provider operation occurs.
        """

        try:
            self._write_focused_conversation_history_rows(action)
        except KeyboardInterrupt:
            self._write_conversation_cancelled_status(
                "History cancelled; attached state is unchanged.",
            )

    def _write_focused_conversation_history_rows(
        self,
        action: InteractiveAction,
    ) -> None:
        """Read once and render history rows until completion or interruption."""

        assert action.kind is ActionKind.HISTORY
        if self._attached_session_id is None:
            self._write_error(
                InteractiveError(InteractiveErrorCode.SESSION_NOT_ATTACHED)
            )
            return
        focused = self._require_active_conversation_focus()
        if focused is None:
            return
        try:
            records, projection = self._read_focused_history_snapshot(focused)
        except _ControllerAbort:
            raise
        except Exception as exc:
            if isinstance(exc, ConversationNotFoundError):
                self._focused_conversation = None
            self._present_transcript_service_failure(exc)
            return

        displayed = records if action.count is None else records[-action.count :]
        total_count = len(displayed)
        for position, event in enumerate(displayed, start=1):
            self._write_counted_list_row(
                "History",
                self._collaboration_record_view(event),
                position=position,
                total_count=total_count,
            )
        self._semantic_output.write_component(
            Record(
                "context",
                fields=(
                    Field(
                        "History displayed",
                        f"{len(displayed)}/{len(records)} records",
                    ),
                    Field("Last sequence", str(projection.last_sequence)),
                    Field(
                        "Current state",
                        self._collaboration_state_meaning(projection)[0],
                    ),
                    Field(
                        "User meaning",
                        self._collaboration_state_meaning(projection)[1],
                    ),
                    Field("Next safe action", projection.next_safe_action.value),
                    Field(
                        "Next action meaning",
                        self._collaboration_next_action_meaning(
                            projection.next_safe_action
                        ),
                    ),
                ),
                state=(ViewState.EMPTY if not displayed else ViewState.COMPLETED),
            ),
        )

    def _read_focused_history_snapshot(
        self,
        focused: ConversationMetadata,
    ) -> tuple[
        tuple[CollaborationReadableRecord, ...],
        CollaborationHistoryProjection,
    ]:
        """Read and fully validate the focused v2 history exactly once.

        The selected v2 event store remains the authoritative read owner; the
        pure selector consumes the exact immutable tuple it returns. The method
        performs no append, provider call, search, or private reach-through.
        """

        session_id = self._attached_session_id
        assert session_id is not None
        events = self._conversation_storage_services.event_store.read(
            session_id,
            focused.conversation_id,
        )
        return validate_history_snapshot(
            events,
            session_id=session_id,
            conversation_id=focused.conversation_id,
        )

    def _inspect_attached_runtime(self, action: InteractiveAction) -> None:
        """Render a safe attached-session or debate inspection projection.

        Bare inspection lists only whitelisted persisted debate summaries.
        Explicit IDs are read exactly as supplied, while ``last`` resolves the
        final persisted debate summary for this attached session. Inspection
        never renders persisted content, errors, raw state, provider data, or
        candidate text.
        """

        assert action.kind is ActionKind.INSPECT
        session_id = self._attached_session_id
        if session_id is None:
            self._write_error(
                InteractiveError(InteractiveErrorCode.SESSION_NOT_ATTACHED)
            )
            return
        if action.argument is None:
            try:
                value = self._runtime.inspect_session(session_id)
                summaries = validate_session_inspection(
                    value,
                    session_id=session_id,
                )
            except Exception as exc:
                self._present_runtime_inspection_failure(exc)
                return
            self._write_notice(
                (
                    f"Session {session_id}: {len(summaries)} persisted "
                    f"{'debate' if len(summaries) == 1 else 'debates'}."
                ),
                label="result",
                role=(SemanticRole.MUTED if not summaries else SemanticRole.SUCCESS),
                state=(ViewState.EMPTY if not summaries else ViewState.COMPLETED),
            )
            for facts in summaries:
                self._semantic_output.write_component(
                    self._debate_inspection_record(facts)
                )
            return

        if action.argument == "last":
            try:
                summaries = validate_session_inspection(
                    self._runtime.inspect_session(session_id),
                    session_id=session_id,
                )
            except Exception as exc:
                self._present_runtime_inspection_failure(exc)
                return
            if not summaries:
                self._write_notice(
                    f"No persisted debates were found in session {session_id}.",
                    label="result",
                    role=SemanticRole.MUTED,
                    state=ViewState.EMPTY,
                )
                return
            debate_id = summaries[-1].debate_id
        else:
            debate_id = action.argument

        try:
            debate_facts, context_facts = self._read_safe_debate_inspection(
                session_id,
                debate_id,
                include_context=True,
            )
        except Exception as exc:
            self._present_runtime_inspection_failure(exc)
            return
        self._semantic_output.write_component(
            self._debate_inspection_record(debate_facts)
        )
        if debate_facts.status != "not_found":
            self._semantic_output.write_component(
                self._debate_context_component(context_facts)
            )

    def _view_attached_agent_exchanges(self) -> None:
        """Select and open one immutable attached-session exchange snapshot.

        Inventory, catalog, and debate reads are exact and provider-free. The
        two pickers retain no state after returning, an empty canonical-agent
        stream is represented by immutable empty bytes, and a stream that
        disappears after selection is refreshed once but never substituted
        with an empty snapshot.
        """

        session_id = self._attached_session_id
        if session_id is None:
            self._write_error(
                InteractiveError(InteractiveErrorCode.SESSION_NOT_ATTACHED)
            )
            return

        store = WorkspaceAgentExchangeStore(self._workspace)
        try:
            inventory = self._run_picker_preparation(
                lambda: store.inventory(session_id)
            )
        except _ControllerAbort:
            raise
        except AgentExchangeStorageError as failure:
            self._present_agent_exchange_storage_failure(failure)
            return
        if not inventory:
            self._write_notice(
                f"No agent exchanges are recorded for session {session_id}.",
                label="agent exchanges",
                role=SemanticRole.MUTED,
                state=ViewState.EMPTY,
            )
            return

        try:
            scope_choices = self._run_picker_preparation(
                lambda: self._load_agent_exchange_scope_choices(
                    session_id,
                    inventory,
                )
            )
        except _ControllerAbort:
            raise
        except Exception as failure:
            self._present_agent_exchange_projection_failure(failure)
            return
        if not scope_choices:
            self._present_agent_exchange_projection_failure(
                AgentExchangeProjectionError(
                    "nonempty exchange inventory produced no scope choices"
                )
            )
            return

        scope_choice = self._pick_agent_exchange_scope(scope_choices)
        if scope_choice is None:
            return
        try:
            agent_choices = project_exchange_agents(
                session_id,
                scope_choice.scope_kind,
                scope_choice.scope_id,
                inventory,
            )
        except Exception as failure:
            self._present_agent_exchange_projection_failure(failure)
            return
        agent_choice = self._pick_agent_exchange_agent(agent_choices)
        if agent_choice is None:
            return

        source: bytes | None
        if agent_choice.selection is None:
            source = b""
        else:
            try:
                source = self._read_agent_exchange_source(
                    store,
                    agent_choice.selection,
                )
            except _ControllerAbort:
                raise
            except AgentExchangeStorageError as failure:
                self._present_agent_exchange_storage_failure(failure)
                return
            except Exception as failure:
                self._present_agent_exchange_projection_failure(failure)
                return
            if source is None:
                self._write_notice(
                    (
                        "The selected agent exchanges are no longer available. "
                        "Retry /exchanges to refresh the picker."
                    ),
                    label="agent exchanges",
                    role=SemanticRole.WARNING,
                    state=ViewState.CANCELLED,
                )
                return

        try:
            operation = prepare_agent_exchange_view(
                source,
                session_id=session_id,
                scope_kind=scope_choice.scope_kind,
                scope_id=scope_choice.scope_id,
                scope_subject=scope_choice.subject,
                agent_id=agent_choice.agent_id,
            )
        except AgentExchangeViewerError as failure:
            self._present_agent_exchange_viewer_failure(failure)
            return
        except Exception as failure:
            self._present_agent_exchange_projection_failure(failure)
            return
        try:
            self._external_viewer.open(operation)
        except AgentExchangeViewerError as failure:
            self._present_agent_exchange_viewer_failure(failure)

    def _load_agent_exchange_scope_choices(
        self,
        session_id: str,
        inventory: tuple[AgentExchangeInventoryItem, ...],
    ) -> tuple[ExchangeScopeChoice, ...]:
        """Read authoritative subjects and project one inventory snapshot."""

        entries = self._catalog_entries(session_id)
        conversation_subjects = {
            entry.conversation_id: entry.subject for entry in entries
        }
        debate_ids = tuple(
            sorted(
                {
                    item.selection.scope_id
                    for item in inventory
                    if item.selection.scope_kind is AgentExchangeScopeKind.DEBATE
                }
            )
        )
        debate_messages = {
            debate_id: self._workspace.read_debate_messages(
                session_id,
                debate_id,
            )
            for debate_id in debate_ids
        }
        return project_exchange_scopes(
            session_id,
            inventory,
            conversation_subjects=conversation_subjects,
            debate_messages=debate_messages,
        )

    def _pick_agent_exchange_scope(
        self,
        choices: tuple[ExchangeScopeChoice, ...],
    ) -> ExchangeScopeChoice | None:
        """Return one transient exact scope selection or ``None`` on cancel."""

        outcome = self._picker_input.read_picker(
            Prompt(
                "View agent exchanges",
                instruction="Choose a conversation or debate.",
            ),
            tuple(
                Choice(
                    choice.identity,
                    choice.label,
                    description=(
                        f"{choice.record_count} recorded "
                        f"{'exchange' if choice.record_count == 1 else 'exchanges'}"
                    ),
                )
                for choice in choices
            ),
            transient=True,
        )
        if outcome.kind in {
            ControlOutcomeKind.CANCEL,
            ControlOutcomeKind.INTERRUPT,
            ControlOutcomeKind.EOF,
        }:
            return None
        if outcome.kind is not ControlOutcomeKind.COMMIT or outcome.text is None:
            raise _ControllerAbort(
                InteractiveError(InteractiveErrorCode.INTERNAL_ERROR)
            )
        selected = tuple(
            choice for choice in choices if choice.identity == outcome.text
        )
        if len(selected) != 1:
            raise _ControllerAbort(
                InteractiveError(InteractiveErrorCode.INTERNAL_ERROR)
            )
        return selected[0]

    def _pick_agent_exchange_agent(
        self,
        choices: tuple[ExchangeAgentChoice, ...],
    ) -> ExchangeAgentChoice | None:
        """Return one transient canonical-agent selection or cancel."""

        outcome = self._picker_input.read_picker(
            Prompt(
                "View agent exchanges",
                instruction="Choose a canonical agent.",
            ),
            tuple(
                Choice(
                    choice.agent_id,
                    f"{choice.display_name} ({choice.agent_id})",
                    description=(
                        "No recorded exchanges"
                        if not choice.has_records
                        else (
                            f"{choice.record_count} recorded "
                            f"{'exchange' if choice.record_count == 1 else 'exchanges'}"
                        )
                    ),
                )
                for choice in choices
            ),
            transient=True,
        )
        if outcome.kind in {
            ControlOutcomeKind.CANCEL,
            ControlOutcomeKind.INTERRUPT,
            ControlOutcomeKind.EOF,
        }:
            return None
        if outcome.kind is not ControlOutcomeKind.COMMIT or outcome.text is None:
            raise _ControllerAbort(
                InteractiveError(InteractiveErrorCode.INTERNAL_ERROR)
            )
        selected = tuple(
            choice for choice in choices if choice.agent_id == outcome.text
        )
        if len(selected) != 1:
            raise _ControllerAbort(
                InteractiveError(InteractiveErrorCode.INTERNAL_ERROR)
            )
        return selected[0]

    def _read_agent_exchange_source(
        self,
        store: WorkspaceAgentExchangeStore,
        selection: AgentExchangeSelection,
    ) -> bytes | None:
        """Read an exact snapshot, refreshing one disappeared stream once."""

        for attempt in range(2):
            try:
                snapshot = self._run_picker_preparation(
                    lambda: store.snapshot(selection)
                )
            except AgentExchangeStorageError as failure:
                if failure.failure is not AgentExchangeFailure.NOT_FOUND:
                    raise
                if attempt == 1:
                    return None
                inventory = self._run_picker_preparation(
                    lambda: store.inventory(selection.session_id)
                )
                matches = tuple(
                    item.selection for item in inventory if item.selection == selection
                )
                if not matches:
                    return None
                if len(matches) != 1:
                    raise AgentExchangeProjectionError(
                        "agent exchange inventory contains duplicate streams"
                    )
                continue
            if snapshot.selection != selection:
                raise AgentExchangeProjectionError(
                    "agent exchange snapshot changed its exact selection"
                )
            return snapshot.jsonl_bytes
        return None

    def _present_agent_exchange_storage_failure(
        self,
        failure: AgentExchangeStorageError,
    ) -> None:
        """Render one fixed content-free storage failure and safe next step."""

        self._record_debug_error(
            DebugOperation.INTERACTIVE_COMMAND,
            failure,
            session_id=self._attached_session_id,
        )
        if failure.failure is AgentExchangeFailure.CONTENTION:
            summary = "Agent exchange storage is busy."
            hint = "Retry /exchanges after the current exchange write completes."
        elif failure.failure is AgentExchangeFailure.UNSUPPORTED_PLATFORM:
            summary = "Agent exchange storage is unavailable on this platform."
            hint = "Use a supported local filesystem and retry /exchanges."
        elif failure.failure in {
            AgentExchangeFailure.CORRUPT_STORAGE,
            AgentExchangeFailure.UNSAFE_STORAGE,
        }:
            summary = "Agent exchange storage could not be read safely."
            hint = "Inspect the sanitized diagnostic before retrying /exchanges."
        else:
            summary = "Agent exchange records could not be read."
            hint = "Retry /exchanges; if it repeats, inspect the sanitized diagnostic."
        self._write_error(
            InteractiveError(
                InteractiveErrorCode.WORKSPACE_INVALID,
                summary=summary,
                hint=hint,
            )
        )

    def _present_agent_exchange_projection_failure(
        self,
        failure: Exception,
    ) -> None:
        """Render fixed correlation guidance without exposing stored content."""

        self._record_debug_error(
            DebugOperation.INTERACTIVE_COMMAND,
            failure,
            session_id=self._attached_session_id,
        )
        self._write_error(
            InteractiveError(
                InteractiveErrorCode.WORKSPACE_INVALID,
                summary="Agent exchange records could not be correlated safely.",
                hint=("Inspect the sanitized diagnostic before retrying /exchanges."),
            )
        )

    def _present_agent_exchange_viewer_failure(
        self,
        failure: AgentExchangeViewerError,
    ) -> None:
        """Render one fixed required-viewer failure without external prose."""

        self._record_debug_error(
            DebugOperation.INTERACTIVE_COMMAND,
            failure,
            session_id=self._attached_session_id,
        )
        if failure.code == "installation_required":
            code = InteractiveErrorCode.CONFIGURATION_INVALID
            summary = "The required agent exchange viewer is unavailable."
            hint = (
                "Repair or reinstall the standard story-writing-agents "
                "installation and retry /exchanges."
            )
        elif failure.code == "incompatible":
            code = InteractiveErrorCode.CONFIGURATION_INVALID
            summary = "The required agent exchange viewer is incompatible with Story."
            hint = (
                "Repair or reinstall the standard story-writing-agents "
                "installation and retry /exchanges."
            )
        else:
            code = InteractiveErrorCode.INTERNAL_ERROR
            summary = "The agent exchange viewer failed."
            hint = "Inspect the sanitized diagnostic before retrying /exchanges."
        self._write_error(InteractiveError(code, summary=summary, hint=hint))

    def _read_safe_debate_inspection(
        self,
        session_id: str,
        debate_id: str,
        *,
        include_context: bool,
    ) -> tuple[DebateInspectionFacts, DebateContextInspectionFacts | None]:
        """Read and whitelist one debate plus optional context-sidecar facts.

        The returned typed facts expose only debate identity, lifecycle, input,
        revision, round, and artifact metadata plus optional context
        identity/mode/count metadata. Raw messages, candidate/error/action
        values, state dictionaries, event content, and provider data are
        validated by their owners but never copied to output.
        """

        inspected = self._runtime.inspect_debate(session_id, debate_id)
        facts = validate_debate_inspection(
            inspected,
            session_id=session_id,
            debate_id=debate_id,
        )
        if not include_context or facts.status == "not_found":
            return facts, None
        context = self._runtime.inspect_debate_context(session_id, debate_id)
        context_facts = validate_debate_context_inspection(
            context,
            session_id=session_id,
            debate_id=debate_id,
        )
        return facts, context_facts

    def _present_runtime_inspection_failure(self, failure: Exception) -> None:
        """Render typed inspection failures or abort on collaborator tampering."""

        self._present_memory_error(classify_inspection_failure(failure))

    def _write_unavailable_lobby_command_error(self) -> None:
        """Render the stable later-feature error without mutating controller state."""

        self._write_error(
            InteractiveError(
                InteractiveErrorCode.COMMAND_INVALID,
                summary=_UNAVAILABLE_SUMMARY,
                hint=_LOBBY_COMMAND_HINT,
            )
        )

    def _search_attached_conversations(
        self,
        action: InteractiveAction,
    ) -> None:
        """Search the attached session and render ordered active-only hits.

        The parsed action supplies one query. Its validated normalized context
        is rendered before catalog and service work; expected failures retain
        attachment and routing state, while Ctrl-C acknowledges cancellation
        without cleanup.
        """

        assert action.kind is ActionKind.SEARCH
        assert action.text is not None
        session_id = self._attached_session_id
        assert session_id is not None

        try:
            try:
                query = CollaborationHistorySearchQuery(action.text)
            except (TypeError, ValueError):
                self._write_error(
                    InteractiveError(InteractiveErrorCode.COMMAND_INVALID)
                )
                return
            self._semantic_output.write_component(
                Record(
                    "context",
                    title="Search request",
                    fields=(
                        Field("Query", query.text),
                        Field("Scope", "active"),
                    ),
                    state=ViewState.COMPLETED,
                )
            )

            try:
                entries = self._catalog_entries(session_id)
            except Exception as exc:
                self._present_memory_catalog_or_reference_failure(exc)
                return

            history_service = self._collaboration_history_service
            if history_service is None:
                raise _ControllerAbort(
                    InteractiveError(InteractiveErrorCode.INTERNAL_ERROR)
                )
            try:
                result = history_service.search(query)
                handles = validate_search_result_handles(
                    result,
                    query=query,
                    session_id=session_id,
                    entries=entries,
                )
            except Exception as exc:
                self._present_search_service_failure(exc)
                return

            total_count = len(result.hits)
            for position, (handle, hit) in enumerate(
                zip(handles, result.hits, strict=True),
                start=1,
            ):
                citation = hit.citation
                metadata = next(
                    entry
                    for entry in entries
                    if entry.conversation_id == citation.conversation_id
                )
                citation_value = (
                    f"conversation {handle}, event {citation.event_id}, "
                    f"sequence {citation.sequence}, field {citation.field_key.value}, "
                    f"range {citation.start_codepoint}:{citation.end_codepoint}"
                )
                self._write_counted_list_row(
                    "Search results",
                    Record(
                        f"search {handle}",
                        fields=(
                            Field(
                                "Status",
                                "active",
                            ),
                            Field("Subject (display only)", metadata.subject),
                            Field("Citation", citation_value),
                            Field("Snippet", hit.snippet),
                        ),
                        role=SemanticRole.DEFAULT,
                        state=ViewState.COMPLETED,
                    ),
                    position=position,
                    total_count=total_count,
                )
            self._write_notice(
                (
                    f"Search returned {len(result.hits)} of "
                    f"{result.matched_fields} matching readable fields."
                ),
                label="result",
                role=(SemanticRole.MUTED if not result.hits else SemanticRole.SUCCESS),
                state=(ViewState.EMPTY if not result.hits else ViewState.COMPLETED),
            )
            if result.truncated:
                self._write_notice(
                    (
                        "Search was truncated by fixed scan or result "
                        "limits; refine the query."
                    ),
                    label="hint",
                    role=SemanticRole.INFO,
                    state=ViewState.COMPLETED,
                )
        except KeyboardInterrupt:
            self._write_conversation_cancelled_status(
                "Search cancelled; attached state is unchanged.",
            )

    def _read_attached_conversation_transcript(
        self,
        action: InteractiveAction,
    ) -> None:
        """Resolve and render one fresh bounded transcript page.

        The reference may name an active or archived conversation. The method
        retains no cursor and mutates no attachment, focus, routing, or
        persistence state; Ctrl-C returns to the same prompt after a status.
        """

        assert action.kind is ActionKind.TRANSCRIPT
        session_id = self._attached_session_id
        assert session_id is not None

        try:
            reference = action.argument
            if reference is None:
                try:
                    entries = self._run_picker_preparation(
                        lambda: self._catalog_entries(session_id)
                    )
                except _ControllerAbort:
                    raise
                except Exception as exc:
                    self._present_memory_catalog_or_reference_failure(exc)
                    return
                if not entries:
                    self._write_notice(
                        "No conversations found.",
                        label="conversation",
                        role=SemanticRole.MUTED,
                        state=ViewState.EMPTY,
                    )
                    return
                outcome = self._picker_input.read_picker(
                    Prompt(
                        "View transcript",
                        instruction="Choose a conversation.",
                    ),
                    tuple(
                        Choice(
                            entry.conversation_id,
                            f"{entry.subject} ({entry.conversation_id})",
                        )
                        for entry in entries
                    ),
                    transient=True,
                )
                if outcome.kind in {
                    ControlOutcomeKind.CANCEL,
                    ControlOutcomeKind.INTERRUPT,
                    ControlOutcomeKind.EOF,
                }:
                    self._write_conversation_cancelled_status(
                        "Transcript read cancelled; attached state is unchanged."
                    )
                    return
                if (
                    outcome.kind is not ControlOutcomeKind.COMMIT
                    or outcome.text is None
                    or outcome.text not in {
                        entry.conversation_id for entry in entries
                    }
                ):
                    raise _ControllerAbort(
                        InteractiveError(InteractiveErrorCode.INTERNAL_ERROR)
                    )
                reference = outcome.text
            try:
                metadata, entries = self._resolve_conversation_reference(
                    session_id,
                    reference,
                )
            except Exception as exc:
                self._present_memory_catalog_or_reference_failure(exc)
                return

            history_service = CollaborationHistoryService(
                CollaborationHistorySearchScope(
                    session_id,
                    include_archived=True,
                ),
                conversation_storage_services=self._conversation_storage_services,
            )
            start_sequence = action.start_sequence or 1
            page_limit = action.count or CONVERSATION_MEMORY_POLICY.default_page_events
            handle = visible_conversation_handle(metadata, entries)
            self._semantic_output.write_component(
                Record(
                    "context",
                    title="Transcript request",
                    fields=(
                        Field("Conversation", handle),
                        Field("Start", str(start_sequence)),
                        Field("Count", str(page_limit)),
                    ),
                    state=ViewState.COMPLETED,
                )
            )
            try:
                page = history_service.read_history(
                    metadata.conversation_id,
                    start_sequence=start_sequence,
                    max_records=page_limit,
                )
                page = validate_transcript_page(
                    page,
                    session_id=session_id,
                    conversation_id=metadata.conversation_id,
                    start_sequence=start_sequence,
                    page_limit=page_limit,
                )
            except Exception as exc:
                self._present_transcript_service_failure(exc)
                return

            total_count = len(page.records)
            for position, event in enumerate(page.records, start=1):
                self._write_counted_list_row(
                    "Transcript",
                    self._collaboration_record_view(event),
                    position=position,
                    total_count=total_count,
                )
            self._write_notice(
                f"Transcript page returned {len(page.records)} records.",
                label="result",
                role=(SemanticRole.MUTED if not page.records else SemanticRole.SUCCESS),
                state=(ViewState.EMPTY if not page.records else ViewState.COMPLETED),
            )
            if page.next_cursor is not None:
                self._write_notice(
                    (
                        f"Continue with /transcript {handle} "
                        f"{page.next_cursor.max_records} --from "
                        f"{page.next_cursor.next_sequence}."
                    ),
                    label="hint",
                    role=SemanticRole.INFO,
                    state=ViewState.COMPLETED,
                )
        except KeyboardInterrupt:
            self._write_conversation_cancelled_status(
                "Transcript read cancelled; attached state is unchanged.",
            )

    def _present_memory_catalog_or_reference_failure(
        self,
        failure: Exception,
    ) -> None:
        """Render safe reference/catalog failures under retained access.

        Resolution categories preserve their exact public code. Unsafe paths
        and invalid complete-catalog behavior are access-confirmed before
        rendering; unknown failures abort to final controller cleanup.
        """

        self._present_memory_error(
            classify_memory_catalog_or_reference_failure(failure)
        )

    def _present_search_service_failure(self, failure: Exception) -> None:
        """Classify a valid-query search failure without exposing its prose.

        Missing identities retain their conversation code; unsafe paths and
        invalid storage/collaborator results are access-confirmed workspace
        failures. Unknown failures abort through the controller lifecycle.
        """

        self._present_memory_error(classify_search_service_failure(failure))

    def _present_transcript_service_failure(self, failure: Exception) -> None:
        """Classify one post-catalog transcript failure without raw prose.

        Missing identities and unsafe paths keep their public categories.
        Transcript format/value failures are transcript-invalid, ordinary I/O
        or unsafe identifiers are workspace-invalid, and unknowns are fatal.
        """

        self._present_memory_error(classify_transcript_service_failure(failure))

    def _present_memory_error(self, error: InteractiveError) -> None:
        """Render one trusted read error after any required access proof.

        Workspace-invalid and unsafe outcomes confirm the same ready access
        attachment before output. Internal errors abort; other codes render
        without changing memory scope, focus, routing, or persistence.
        """

        if error.code is InteractiveErrorCode.INTERNAL_ERROR:
            raise _ControllerAbort(error)
        if error.code in {
            InteractiveErrorCode.WORKSPACE_INVALID,
            InteractiveErrorCode.WORKSPACE_UNSAFE,
        }:
            self._confirm_attachment_after_command_failure()
        self._write_error(error)

    def _write_attached_conversation_scope(
        self,
        last_presented: object | tuple[str, str | None, str | None],
    ) -> tuple[str, str | None, str | None]:
        """Revalidate current focus and present it only when semantically changed.

        The current metadata is revalidated against the complete catalog so a
        unique display handle can be derived across active and archived IDs.
        Missing or archived external state drops only in-memory focus and
        reports its typed category; the durable default is not rewritten here.
        The returned snapshot is produced only after any required terminal
        write succeeds, so callers never remember a failed presentation.
        """

        session_id = self._attached_session_id
        assert session_id is not None
        focused = self._focused_conversation
        if focused is None:
            handle = None
            subject = None
        else:
            try:
                entries = self._catalog_entries(session_id)
            except Exception as exc:
                self._present_conversation_failure(exc)
                handle = focused.conversation_id
                subject = focused.subject
            else:
                current = next(
                    (
                        entry
                        for entry in entries
                        if entry.conversation_id == focused.conversation_id
                    ),
                    None,
                )
                if current is None:
                    self._focused_conversation = None
                    self._write_error(
                        InteractiveError(InteractiveErrorCode.CONVERSATION_NOT_FOUND)
                    )
                    handle = None
                    subject = None
                elif current.status is ConversationStatus.ARCHIVED:
                    self._focused_conversation = None
                    self._write_error(
                        InteractiveError(InteractiveErrorCode.CONVERSATION_ARCHIVED)
                    )
                    handle = None
                    subject = None
                else:
                    self._focused_conversation = current
                    handle = visible_conversation_handle(current, entries)
                    subject = current.subject

        current_scope = (
            session_id,
            handle,
            subject,
        )
        if current_scope != last_presented:
            self._semantic_output.write_component(
                Record(
                    "conversation",
                    fields=(
                        Field("Session", session_id),
                        Field("Conversation", handle or "none"),
                        Field("Subject", subject or "none"),
                    ),
                    state=(ViewState.EMPTY if handle is None else ViewState.COMPLETED),
                )
            )
        return current_scope

    def _create_subject_conversation(self, subject: str | None) -> bool:
        """Create and focus one active conversation, returning EOF exit intent.

        A missing subject causes exactly one call-local terminal read. Interrupt
        cancels without mutation and EOF requests normal process cleanup. After
        catalog creation, the default is saved before focus changes; a save
        failure leaves the created entry and prior process state intact.
        """

        if subject is None:
            subject_action = self._terminal.request_conversation_subject()
            if subject_action.kind is TerminalActionKind.INTERRUPT:
                self._write_conversation_cancelled_status(
                    "Conversation creation cancelled."
                )
                return False
            if subject_action.kind is TerminalActionKind.EOF:
                return True
            if subject_action.kind is not TerminalActionKind.LINE:
                raise _ControllerAbort(
                    InteractiveError(InteractiveErrorCode.INTERNAL_ERROR)
                )
            assert subject_action.text is not None
            subject = subject_action.text

        session_id = self._attached_session_id
        assert session_id is not None
        try:
            normalize_conversation_input(subject)
            created = self._conversation_catalog.create(session_id, subject)
            created = validate_catalog_metadata(
                created,
                session_id,
            )
            if created.status is not ConversationStatus.ACTIVE:
                raise ConversationCatalogProtocolFailure(
                    "new conversation metadata is not active"
                )
        except Exception as exc:
            self._present_conversation_failure(exc, creating=True)
            return False

        try:
            save_default_conversation(
                self._workspace,
                session_id,
                created.conversation_id,
            )
        except Exception as exc:
            self._confirm_attachment_after_command_failure()
            error = classify_conversation_failure(exc)
            self._write_error(
                InteractiveError(
                    error.code,
                    summary=(
                        f"Conversation {created.conversation_id} was created "
                        "but did not become the default."
                    ),
                    hint=(
                        "Repair workspace storage, then use the full ID to "
                        "make it the current focus."
                    ),
                )
            )
            return False

        self._focused_conversation = created
        self._trace_debug(
            DebugOperation.CONVERSATION_FOCUS,
            DebugPhase.SUCCEEDED,
            session_id=session_id,
            conversation_id=created.conversation_id,
            outcome=DebugOutcome.COMPLETED,
        )
        handle = self._visible_handle_after_mutation(created)
        self._write_notice(
            f"Created and focused {handle} | Subject: {created.subject}",
            label="conversation",
            role=SemanticRole.SUCCESS,
            state=ViewState.COMPLETED,
        )
        return False

    def _list_subject_conversations(
        self,
        *,
        include_archived: bool,
        query: str | None,
    ) -> None:
        """Render a filtered catalog in storage order without durable mutation.

        Handles are derived from the complete active-and-archived ID set even
        when only active rows are requested. The optional query uses the
        established normalized-subject substring rule; external loss or
        archival of the in-memory focus clears memory but not its default file.
        """

        session_id = self._attached_session_id
        assert session_id is not None
        try:
            normalized_query = (
                None if query is None else normalize_conversation_input(query)
            )
        except Exception as exc:
            self._present_conversation_failure(exc)
            return
        if normalized_query is not None:
            self._semantic_output.write_component(
                Record(
                    "context",
                    title="Conversation list request",
                    fields=(
                        Field("Query", normalized_query),
                        Field(
                            "Scope",
                            "all" if include_archived else "active",
                        ),
                    ),
                    state=ViewState.COMPLETED,
                )
            )
        try:
            entries = self._catalog_entries(session_id)
        except Exception as exc:
            self._present_conversation_failure(exc)
            return

        self._reconcile_conversation_focus(entries)
        visible_entries = tuple(
            entry
            for entry in entries
            if (include_archived or entry.status is ConversationStatus.ACTIVE)
            and (
                normalized_query is None
                or normalized_query in normalize_conversation_subject(entry.subject)
            )
        )
        if not visible_entries:
            self._semantic_output.write_component(
                ListView(
                    "Conversations",
                    (),
                    total_count=0,
                    empty_notice=Notice(
                        "No conversations found.",
                        label="conversation",
                        role=SemanticRole.MUTED,
                        state=ViewState.EMPTY,
                    ),
                    state=ViewState.EMPTY,
                )
            )
            return

        focused_id = (
            None
            if self._focused_conversation is None
            else self._focused_conversation.conversation_id
        )
        total_count = len(visible_entries)
        for position, entry in enumerate(visible_entries, start=1):
            archived = entry.status is ConversationStatus.ARCHIVED
            focused = entry.conversation_id == focused_id
            handle = visible_conversation_handle(entry, entries)
            self._write_counted_list_row(
                "Conversations",
                Record(
                    f"conversation {handle}",
                    fields=(
                        Field(
                            "Status",
                            "archived" if archived else "active",
                        ),
                        Field(
                            "Focus",
                            "focused" if focused else "not focused",
                        ),
                        Field("Subject", entry.subject),
                    ),
                    role=(SemanticRole.MUTED if archived else SemanticRole.DEFAULT),
                    state=ViewState.COMPLETED,
                ),
                position=position,
                total_count=total_count,
            )

    def _focus_subject_conversation(self, reference: str | None) -> bool:
        """Select or resolve, save, and focus one active conversation.

        An omitted reference presents active rows in catalog order. Picker
        cancellation is mutation-free and EOF requests normal controller
        cleanup. The committed full identity then follows the same fresh
        resolution used by explicit exact IDs and accepted unique prefixes.
        Resolution includes archived records, which receive a typed rejection.
        Default persistence completes before in-memory focus and presentation
        change, and agent routing remains untouched.
        """

        session_id = self._attached_session_id
        assert session_id is not None
        if reference is None:

            def prepare_resume_choices() -> tuple[
                tuple[ConversationMetadata, ...],
                tuple[ConversationMetadata, ...],
                tuple[str, ...],
                str | None,
            ]:
                """Read catalog/default state before the picker is visible."""

                entries = self._catalog_entries(session_id)
                active_entries = tuple(
                    entry
                    for entry in entries
                    if entry.status is ConversationStatus.ACTIVE
                )
                active_ids = tuple(entry.conversation_id for entry in active_entries)
                highlighted_id = (
                    None
                    if self._focused_conversation is None
                    else self._focused_conversation.conversation_id
                )
                if highlighted_id not in active_ids:
                    highlighted_id = load_default_conversation(
                        self._workspace,
                        session_id,
                    )
                if highlighted_id not in active_ids:
                    highlighted_id = None
                return entries, active_entries, active_ids, highlighted_id

            try:
                (
                    entries,
                    active_entries,
                    active_ids,
                    highlighted_id,
                ) = self._run_picker_preparation(prepare_resume_choices)
            except _ControllerAbort:
                raise
            except Exception as exc:
                self._present_conversation_failure(exc)
                return False
            if not active_entries:
                self._write_notice(
                    "No active conversations found.",
                    label="conversation",
                    role=SemanticRole.MUTED,
                    state=ViewState.EMPTY,
                )
                return False

            outcome = self._picker_input.read_picker(
                Prompt(
                    "Resume conversation",
                    instruction="Choose an active conversation to resume.",
                ),
                tuple(
                    Choice(
                        entry.conversation_id,
                        entry.subject,
                        description=(
                            f"Handle: {visible_conversation_handle(entry, entries)}"
                        ),
                    )
                    for entry in active_entries
                ),
                highlighted_id=highlighted_id,
            )
            if outcome.kind is ControlOutcomeKind.EOF:
                return True
            if outcome.kind in {
                ControlOutcomeKind.CANCEL,
                ControlOutcomeKind.INTERRUPT,
            }:
                self._write_conversation_cancelled_status(
                    "Conversation focus cancelled."
                )
                return False
            if (
                outcome.kind is not ControlOutcomeKind.COMMIT
                or outcome.text is None
                or outcome.text not in active_ids
            ):
                raise _ControllerAbort(
                    InteractiveError(InteractiveErrorCode.INTERNAL_ERROR)
                )
            reference = outcome.text

        try:
            focused, entries = self._resolve_conversation_reference(
                session_id,
                reference,
            )
        except Exception as exc:
            self._present_conversation_failure(exc)
            return False
        if focused.status is ConversationStatus.ARCHIVED:
            self._write_error(
                InteractiveError(InteractiveErrorCode.CONVERSATION_ARCHIVED)
            )
            return False
        try:
            save_default_conversation(
                self._workspace,
                session_id,
                focused.conversation_id,
            )
        except Exception as exc:
            self._confirm_attachment_after_command_failure()
            self._write_error(classify_conversation_failure(exc))
            return False

        self._focused_conversation = focused
        self._trace_debug(
            DebugOperation.CONVERSATION_FOCUS,
            DebugPhase.SUCCEEDED,
            session_id=session_id,
            conversation_id=focused.conversation_id,
            outcome=DebugOutcome.COMPLETED,
        )
        self._write_notice(
            (
                "Current focus: "
                f"{visible_conversation_handle(focused, entries)} | "
                f"Subject: {focused.subject}"
            ),
            label="conversation",
            role=SemanticRole.SUCCESS,
            state=ViewState.COMPLETED,
        )
        return False

    def _rename_focused_conversation(self, subject: str) -> None:
        """Atomically rename the focused active conversation, then update memory.

        The exact display subject is validated without normalization replacement.
        Missing or externally archived focus enters the conversation lobby
        without rewriting the stale durable default; catalog failure leaves
        the previous process metadata intact until later revalidation.
        """

        current = self._require_active_conversation_focus()
        if current is None:
            return
        session_id = self._attached_session_id
        assert session_id is not None
        try:
            normalize_conversation_input(subject)
            renamed = self._conversation_catalog.rename(
                session_id,
                current.conversation_id,
                subject,
            )
            renamed = validate_catalog_metadata(
                renamed,
                session_id,
                expected_conversation_id=current.conversation_id,
            )
        except ConversationNotFoundError as exc:
            self._focused_conversation = None
            self._present_conversation_failure(exc)
            return
        except Exception as exc:
            self._present_conversation_failure(exc)
            return
        if renamed.status is ConversationStatus.ARCHIVED:
            self._focused_conversation = None
            self._write_error(
                InteractiveError(InteractiveErrorCode.CONVERSATION_ARCHIVED)
            )
            return

        self._focused_conversation = renamed
        handle = self._visible_handle_after_mutation(renamed)
        self._write_notice(
            f"Renamed {handle} | Subject: {renamed.subject}",
            label="conversation",
            role=SemanticRole.SUCCESS,
            state=ViewState.COMPLETED,
        )

    def _archive_subject_conversation(self, reference: str | None) -> bool:
        """Archive one resolved conversation and return confirmation EOF intent.

        Bare archive requires the focused active record. Explicit lookup can
        target any record; an already archived unfocused record is an acknowledged
        no-op. Archiving the current focus delegates to the compensated
        successor transition, while other targets require one catalog mutation.
        """

        session_id = self._attached_session_id
        assert session_id is not None
        if reference is None:
            target = self._require_active_conversation_focus()
            if target is None:
                return False
            try:
                entries = self._catalog_entries(session_id)
            except Exception as exc:
                self._present_conversation_failure(exc)
                return False
        else:
            try:
                target, entries = self._resolve_conversation_reference(
                    session_id,
                    reference,
                )
            except Exception as exc:
                self._present_conversation_failure(exc)
                return False

        focused_id = (
            None
            if self._focused_conversation is None
            else self._focused_conversation.conversation_id
        )
        is_focused = target.conversation_id == focused_id
        if target.status is ConversationStatus.ARCHIVED:
            if is_focused:
                self._focused_conversation = None
                self._write_error(
                    InteractiveError(InteractiveErrorCode.CONVERSATION_ARCHIVED)
                )
            else:
                self._write_notice(
                    (
                        "Already archived "
                        f"{visible_conversation_handle(target, entries)} | "
                        f"Subject: {target.subject}"
                    ),
                    label="conversation",
                    role=SemanticRole.MUTED,
                    state=ViewState.COMPLETED,
                )
            return False
        if is_focused:
            return self._archive_focused_conversation(target, entries)
        self._archive_unfocused_conversation(target, entries)
        return False

    def _archive_unfocused_conversation(
        self,
        target: ConversationMetadata,
        entries: tuple[ConversationMetadata, ...],
    ) -> None:
        """Archive one active unfocused conversation with one catalog mutation.

        The durable default and in-memory focus are never changed. A validated
        archived result is rendered using the pre-mutation all-record handle;
        failures follow command-time access confirmation rules.
        """

        session_id = self._attached_session_id
        assert session_id is not None
        try:
            archived = self._conversation_catalog.archive(
                session_id,
                target.conversation_id,
            )
            archived = validate_catalog_metadata(
                archived,
                session_id,
                expected_conversation_id=target.conversation_id,
            )
            if archived.status is not ConversationStatus.ARCHIVED:
                raise ConversationCatalogProtocolFailure(
                    "archived conversation metadata is not archived"
                )
            self._runtime.retire_archived_conversation(
                session_id,
                target.conversation_id,
            )
        except Exception as exc:
            self._present_conversation_failure(exc)
            return
        self._write_notice(
            (
                f"Archived {visible_conversation_handle(target, entries)} | "
                f"Subject: {archived.subject}"
            ),
            label="conversation",
            role=SemanticRole.SUCCESS,
            state=ViewState.COMPLETED,
        )

    def _archive_focused_conversation(
        self,
        target: ConversationMetadata,
        entries: tuple[ConversationMetadata, ...],
    ) -> bool:
        """Confirm, archive, and make the first active successor the default.

        No, blank, or interrupt cancels without mutation; EOF requests normal
        access release. After archive, successor/null persistence precedes memory. A
        failed default write best-effort unarchives and proves both original
        active metadata and original durable default before recovery is
        claimed; any uncertainty aborts through cleanup-failed.
        """

        session_id = self._attached_session_id
        assert session_id is not None
        successor = next(
            (
                entry
                for entry in entries
                if entry.conversation_id != target.conversation_id
                and entry.status is ConversationStatus.ACTIVE
            ),
            None,
        )
        target_handle = visible_conversation_handle(target, entries)
        decision = self._terminal.request_confirmation(
            (f'Archive current conversation "{target.subject}" ({target_handle})?'),
            default=False,
        )
        if decision is ConfirmationResult.EOF:
            return True
        if decision is not ConfirmationResult.YES:
            self._write_conversation_cancelled_status("Conversation archive cancelled.")
            return False

        try:
            archived = self._conversation_catalog.archive(
                session_id,
                target.conversation_id,
            )
            archived = validate_catalog_metadata(
                archived,
                session_id,
                expected_conversation_id=target.conversation_id,
            )
            if archived.status is not ConversationStatus.ARCHIVED:
                raise ConversationCatalogProtocolFailure(
                    "archived conversation metadata is not archived"
                )
        except ConversationNotFoundError as exc:
            self._focused_conversation = None
            self._present_conversation_failure(exc)
            return False
        except Exception as exc:
            self._present_conversation_failure(exc)
            return False

        try:
            save_default_conversation(
                self._workspace,
                session_id,
                (None if successor is None else successor.conversation_id),
            )
        except Exception as exc:
            self._compensate_failed_focused_archive(target, exc)
            return False

        self._focused_conversation = successor
        try:
            self._runtime.retire_archived_conversation(
                session_id,
                target.conversation_id,
            )
        except Exception as exc:
            self._present_conversation_failure(exc)
            return False
        status = f"Archived {target_handle} | Subject: {archived.subject}"
        if successor is None:
            status += "; current focus is none."
        else:
            status += (
                "; current focus: "
                f"{visible_conversation_handle(successor, entries)} | "
                f"Subject: {successor.subject}"
            )
        self._write_notice(
            status,
            label="conversation",
            role=SemanticRole.SUCCESS,
            state=ViewState.COMPLETED,
        )
        return False

    def _compensate_failed_focused_archive(
        self,
        original: ConversationMetadata,
        default_failure: Exception,
    ) -> None:
        """Restore and prove the original focus after a default-save failure.

        Compensation uses only public catalog and default APIs. It succeeds
        only when unarchive/get both show the original active metadata, the
        durable default still names it, and the current session remains ready
        with access held here. Otherwise controller bookkeeping
        is abandoned for best-effort backend close and ``cleanup_failed``.
        """

        session_id = self._attached_session_id
        assert session_id is not None
        try:
            restored = self._conversation_catalog.unarchive(
                session_id,
                original.conversation_id,
            )
            restored = validate_catalog_metadata(
                restored,
                session_id,
                expected_conversation_id=original.conversation_id,
            )
            confirmed = self._conversation_catalog.get(
                session_id,
                original.conversation_id,
            )
            confirmed = validate_catalog_metadata(
                confirmed,
                session_id,
                expected_conversation_id=original.conversation_id,
            )
            durable_default = load_default_conversation(
                self._workspace,
                session_id,
            )
            if (
                restored.status is not ConversationStatus.ACTIVE
                or confirmed.status is not ConversationStatus.ACTIVE
            ):
                raise ConversationCatalogProtocolFailure(
                    "unarchived conversation metadata is not active"
                )
            if (
                durable_default != original.conversation_id
                or not self._attachment_is_safely_retained(session_id)
            ):
                raise WorkspaceFormatError(
                    "focused archive compensation could not be confirmed"
                )
        except Exception:
            self._abandon_known_attachment_for_cleanup_abort()

        self._focused_conversation = confirmed
        self._write_error(classify_conversation_failure(default_failure))
        self._write_notice(
            "Archive did not complete; the original conversation remains focused.",
            label="conversation",
            role=SemanticRole.WARNING,
            state=ViewState.COMPLETED,
        )

    def _require_active_conversation_focus(
        self,
    ) -> ConversationMetadata | None:
        """Return the revalidated active focus or render its typed absence.

        Missing and archived records clear only effective in-memory focus,
        leaving the durable stale ID for safe restart behavior. Other failures
        preserve memory and follow command-time access confirmation rules.
        """

        focused = self._focused_conversation
        if focused is None:
            self._write_error(
                InteractiveError(InteractiveErrorCode.CONVERSATION_REQUIRED)
            )
            return None
        session_id = self._attached_session_id
        assert session_id is not None
        try:
            current = self._conversation_catalog.get(
                session_id,
                focused.conversation_id,
            )
            current = validate_catalog_metadata(
                current,
                session_id,
                expected_conversation_id=focused.conversation_id,
            )
        except ConversationNotFoundError as exc:
            self._focused_conversation = None
            self._present_conversation_failure(exc)
            return None
        except Exception as exc:
            self._present_conversation_failure(exc)
            return None
        if current.status is ConversationStatus.ARCHIVED:
            self._focused_conversation = None
            self._write_error(
                InteractiveError(InteractiveErrorCode.CONVERSATION_ARCHIVED)
            )
            return None
        self._focused_conversation = current
        return current

    def _reconcile_conversation_focus(
        self,
        entries: tuple[ConversationMetadata, ...],
    ) -> None:
        """Refresh, drop, and report current focus against one catalog read.

        The operation changes only effective in-memory metadata. Missing or
        archived durable defaults remain untouched so no silent rewrite occurs.
        """

        focused = self._focused_conversation
        if focused is None:
            return
        current = next(
            (
                entry
                for entry in entries
                if entry.conversation_id == focused.conversation_id
            ),
            None,
        )
        if current is None:
            self._focused_conversation = None
            self._write_error(
                InteractiveError(InteractiveErrorCode.CONVERSATION_NOT_FOUND)
            )
        elif current.status is ConversationStatus.ARCHIVED:
            self._focused_conversation = None
            self._write_error(
                InteractiveError(InteractiveErrorCode.CONVERSATION_ARCHIVED)
            )
        else:
            self._focused_conversation = current

    def _resolve_conversation_reference(
        self,
        session_id: str,
        reference: str,
    ) -> tuple[ConversationMetadata, tuple[ConversationMetadata, ...]]:
        """Resolve exact ID or one case-sensitive unique prefix across all rows.

        Exact identity wins at any valid length. Nonexact prefixes shorter than
        twelve characters, zero matches, and multiple matches raise private
        trusted categories; catalog/storage failures propagate unchanged.
        """

        safe_reference = validate_conversation_reference(reference)
        entries = self._catalog_entries(session_id)
        return resolve_conversation_reference(safe_reference, entries), entries

    def _catalog_entries(
        self,
        session_id: str,
    ) -> tuple[ConversationMetadata, ...]:
        """Read and validate the complete ordered catalog for one session.

        The method requests active and archived rows, rejects malformed return
        shapes, wrong owners, and duplicate IDs, and performs no sorting or
        persistence mutation so adapter order remains authoritative.
        """

        entries = self._conversation_catalog.list(
            session_id,
            include_archived=True,
        )
        return validate_catalog_entries(entries, session_id)

    def _visible_handle_after_mutation(
        self,
        metadata: ConversationMetadata,
    ) -> str:
        """Derive a post-mutation handle, falling back safely to the full ID.

        Catalog-read failure is reported under command-time access rules. The
        completed durable mutation is never rolled back merely for presentation.
        """

        session_id = self._attached_session_id
        assert session_id is not None
        try:
            entries = self._catalog_entries(session_id)
        except Exception as exc:
            self._present_conversation_failure(exc)
            return metadata.conversation_id
        return visible_conversation_handle(metadata, entries)

    def _confirm_attachment_after_command_failure(self) -> None:
        """Require public proof that ready access remains held after failure.

        The provider-free lifecycle read performs no mutation. Missing, foreign,
        paused, unowned, or unreadable state abandons explicit release and
        raises ``cleanup_failed`` so backend close remains best effort.
        """

        session_id = self._attached_session_id
        if session_id is None or not self._attachment_is_safely_retained(session_id):
            self._abandon_known_attachment_for_cleanup_abort()

    def _present_conversation_failure(
        self,
        failure: Exception,
        *,
        creating: bool = False,
    ) -> None:
        """Map one conversation failure and render it without exception prose.

        Invalid input and deterministic lookup categories are immediately safe.
        Workspace, I/O, and unexpected command-time failures require public
        proof that the session remains ready with access held here before the
        controller returns to its prompt; failed proof aborts cleanup.
        """

        error = classify_conversation_failure(
            failure,
            creating=creating,
        )
        if error.code is InteractiveErrorCode.INTERNAL_ERROR:
            raise _ControllerAbort(error)
        if error.code in {
            InteractiveErrorCode.WORKSPACE_INVALID,
            InteractiveErrorCode.WORKSPACE_UNSAFE,
        }:
            self._confirm_attachment_after_command_failure()
        self._write_error(error)

    def _write_conversation_cancelled_status(self, text: str) -> None:
        """Acknowledge one cancelled conversation action without state mutation."""

        self._write_notice(
            text,
            label="cancelled",
            role=SemanticRole.WARNING,
            state=ViewState.CANCELLED,
        )

    def _validated_story_session_lifecycles(
        self,
    ) -> tuple[SessionLifecycleState, ...]:
        """Return the backend's ordered persisted lifecycles after validation."""

        states = self._backend.list_session_lifecycles()
        if type(states) is not tuple:
            raise BackendProtocolError(
                "backend returned an invalid lifecycle collection"
            )
        return tuple(
            require_lifecycle_state(state, state.session_id)
            if type(state) is SessionLifecycleState
            else require_lifecycle_state(state, "invalid")
            for state in states
        )

    def _list_story_sessions(self) -> None:
        """Render canonical read-only lifecycle rows in backend order."""

        try:
            validated = self._validated_story_session_lifecycles()
        except Exception as exc:
            self._present_session_failure(exc, self._context.default_session_id)
            return
        if not validated:
            self._semantic_output.write_component(
                ListView(
                    "Story sessions",
                    (),
                    total_count=0,
                    empty_notice=Notice(
                        "No story sessions found.",
                        label="session",
                        role=SemanticRole.MUTED,
                        state=ViewState.EMPTY,
                    ),
                    state=ViewState.EMPTY,
                )
            )
            return
        total_count = len(validated)
        for position, state in enumerate(validated, start=1):
            try:
                provider_ready = self._provider_work_is_ready(state)
            except Exception as exc:
                self._present_session_failure(exc, state.session_id)
                return
            attached = self._attached_session_id == state.session_id
            focused = attached and self._focused_conversation is not None
            next_action = next_session_action(
                state,
                attached=attached,
                focused=focused,
                provider_ready=provider_ready,
            )
            self._write_counted_list_row(
                "Story sessions",
                Record(
                    "session",
                    fields=(
                        Field("Session", state.session_id),
                        Field("Durable state", state.durable_state.value),
                        Field("Process access", state.access_state.value),
                        Field(
                            "Default session",
                            (
                                "yes"
                                if self._context.default_session_id == state.session_id
                                else "no"
                            ),
                        ),
                        Field(
                            "Provider readiness",
                            "yes" if provider_ready else "no",
                        ),
                        Field("Next action", next_action),
                    ),
                    state=ViewState.COMPLETED,
                ),
                position=position,
                total_count=total_count,
            )

    def _pause_attached_session(self) -> bool:
        """Confirm, pause, release access, and return to the session lobby."""

        session_id = self._attached_session_id
        if session_id is None:
            self._write_error(
                InteractiveError(InteractiveErrorCode.SESSION_NOT_ATTACHED)
            )
            return False
        decision = self._terminal.request_confirmation(
            f"Pause session {session_id}?",
            default=False,
        )
        if decision is ConfirmationResult.EOF:
            return True
        if decision is not ConfirmationResult.YES:
            self._write_session_pause_cancelled_status()
            return False
        try:
            current = self._backend.get_session_lifecycle(session_id)
            require_ready_access_state(current, session_id)
            assessment = self._assess_session_transition(
                current,
                SessionTransitionOperation.PAUSE_SESSION,
                self._focused_conversation,
            )
            if not transition_operation_is_allowed(
                assessment,
                SessionTransitionOperation.PAUSE_SESSION,
            ):
                self._write_transition_denial(assessment)
                return False
            state = self._backend.pause_session_and_release_access(session_id)
            state = require_lifecycle_state(state, session_id)
            if (
                state.durable_state is not SessionDurableState.PAUSED
                or state.access_state is SessionAccessState.HELD_HERE
            ):
                raise BackendProtocolError(
                    "backend did not return a released paused session"
                )
        except Exception as exc:
            if not self._attachment_is_safely_retained(session_id):
                self._abandon_known_attachment_for_cleanup_abort()
            self._write_error(classify_session_failure(exc))
            return False

        self._attached_session_id = None
        self._focused_conversation = None
        self._collaboration_history_service = None
        self._trace_debug(
            DebugOperation.ATTACHMENT,
            DebugPhase.SUCCEEDED,
            session_id=session_id,
            outcome=DebugOutcome.PAUSED,
        )
        self._write_notice(
            f"Paused {session_id}; default retained.",
            label="session",
            role=SemanticRole.SUCCESS,
            state=ViewState.COMPLETED,
        )
        self._write_lobby_orientation()
        return False

    def _attachment_is_safely_retained(self, session_id: str) -> bool:
        """Return whether public state proves ready access is still held here."""

        try:
            state = self._backend.get_session_lifecycle(session_id)
            require_lifecycle_state(state, session_id)
        except (Exception, KeyboardInterrupt):
            return False
        return (
            state.durable_state is SessionDurableState.READY
            and state.access_state is SessionAccessState.HELD_HERE
        )

    def _write_parser_error(
        self,
        action: InteractiveAction,
        original_line: str,
    ) -> None:
        """Render a persona-specific or general error from original syntax."""

        assert action.kind is ActionKind.PARSE_ERROR
        assert action.error is not None
        code = (
            InteractiveErrorCode.AGENT_INVALID
            if self._parse_error_targets_agent(original_line)
            else InteractiveErrorCode.COMMAND_INVALID
        )
        hint = (
            f"Try {action.suggestion}, or type /help."
            if action.suggestion is not None
            else None
        )
        self._write_error(
            InteractiveError(
                code,
                summary=action.error,
                hint=hint,
            )
        )

    @staticmethod
    def _parse_error_targets_agent(original_line: str) -> bool:
        """Return whether a parse failure identifies an invalid persona agent."""

        first_delimiter = next(
            (
                index
                for index, character in enumerate(original_line)
                if character in " \t"
            ),
            len(original_line),
        )
        command = original_line[:first_delimiter]
        if first_delimiter == len(original_line):
            return False
        arguments = tuple(
            token
            for token in original_line[first_delimiter:].replace("\t", " ").split(" ")
            if token
        )
        if command == "/persona":
            agent_arguments = arguments
        elif command == "/override" and arguments[:1] == ("persona",):
            agent_arguments = arguments[1:]
        else:
            return False
        return bool(agent_arguments)

    def _present_session_failure(
        self,
        failure: Exception,
        _session_id: str | None,
    ) -> None:
        """Map a typed operation failure to safe output or a fatal abort.

        Mapping uses exception classes, never exception prose. Recoverable
        session/preflight categories return to the
        prompt; workspace, internal, and cleanup uncertainty abort the run.
        """

        error = classify_session_failure(failure)
        if error.code not in RECOVERABLE_SESSION_ERROR_CODES:
            raise _ControllerAbort(error)
        self._write_error(error)

    def _write_main_input_cancelled_status(self) -> None:
        """Acknowledge main-input Ctrl-C without changing controller state."""

        self._write_notice(
            "No action was taken.",
            label="cancelled",
            role=SemanticRole.WARNING,
            state=ViewState.CANCELLED,
        )

    def _write_session_resume_cancelled_status(self) -> None:
        """Acknowledge declined default-session resume without mutation."""

        self._write_notice(
            "Session resume cancelled.",
            label="cancelled",
            role=SemanticRole.WARNING,
            state=ViewState.CANCELLED,
        )

    def _write_session_pause_cancelled_status(self) -> None:
        """Acknowledge declined session pause while retaining attachment."""

        self._write_notice(
            "Session pause cancelled.",
            label="cancelled",
            role=SemanticRole.WARNING,
            state=ViewState.CANCELLED,
        )

    def _write_lobby_orientation(self) -> None:
        """Render workspace-scoped lobby help without acquiring resources."""

        self._terminal.write_lobby_orientation(str(self._workspace.root))

    def _abandon_known_attachment_for_cleanup_abort(self) -> None:
        """Clear uncertain bookkeeping and abort into backend-close cleanup.

        No further explicit release is attempted because a prior lifecycle
        failure made source/destination ownership ambiguous. Process attachment,
        focus, and memory scope are cleared while backend ``close`` remains
        the best-effort resource cleanup operation.
        """

        self._attached_session_id = None
        self._focused_conversation = None
        self._collaboration_history_service = None
        raise _ControllerAbort(InteractiveError(InteractiveErrorCode.CLEANUP_FAILED))

    def _write_error_best_effort(self, error: InteractiveError) -> bool:
        """Attempt one diagnostic without delaying lifecycle cleanup.

        Ordinary callback failures are suppressed after the attempted write.
        ``KeyboardInterrupt`` and every other ``BaseException`` retain their
        fatal process semantics while the caller's cleanup ``finally`` runs.
        """

        try:
            self._write_error(error)
        except Exception:
            return False
        return True

    def _release_attachment_and_close_backend(
        self,
        exit_code: int,
        *,
        cleanup_error_reported: bool,
        body_control: _ExceptionState | None = None,
    ) -> int:
        """Close Runner, release access, close backend, and rank failures.

        Normal quit/EOF releases access without pausing. Each cleanup stage runs
        after the preceding stage even when that stage fails. Attachment, focus,
        and memory state are always cleared before the method returns; one
        best-effort cleanup diagnostic accompanies status 2. An uncaught body
        process control outranks the first cleanup process control, which
        propagates only after every later stage runs. The Runner reference is
        retained only when close is interrupted by process control.
        """

        if self._closed:
            if body_control is not None:
                _raise_exception_state(body_control)
            return exit_code
        self._trace_debug(
            DebugOperation.CLEANUP,
            DebugPhase.STARTED,
            session_id=self._attached_session_id,
        )
        cleanup_failed = False
        cleanup_control: _ExceptionState | None = None
        runner = self._runner
        discard_runner_state_after_cleanup = runner is None
        attached_session = self._attached_session_id
        if runner is not None:
            try:
                runner.close()
            except Exception as exc:
                self._record_debug_error(
                    DebugOperation.CLEANUP,
                    exc,
                    session_id=attached_session,
                )
                cleanup_failed = True
                discard_runner_state_after_cleanup = True
            except BaseException as exc:
                self._record_debug_error(
                    DebugOperation.CLEANUP,
                    exc,
                    session_id=attached_session,
                    phase=DebugPhase.CANCELLED,
                    outcome=DebugOutcome.INTERRUPTED,
                )
                cleanup_control = _remember_first_process_control(
                    cleanup_control,
                    exc,
                )
            else:
                discard_runner_state_after_cleanup = True

        if attached_session is not None:
            try:
                released = self._backend.release_session_access(attached_session)
                require_released_state(
                    released,
                    attached_session,
                )
            except Exception as exc:
                self._record_debug_error(
                    DebugOperation.CLEANUP,
                    exc,
                    session_id=attached_session,
                )
                cleanup_failed = True
            except BaseException as exc:
                self._record_debug_error(
                    DebugOperation.CLEANUP,
                    exc,
                    session_id=attached_session,
                    phase=DebugPhase.CANCELLED,
                    outcome=DebugOutcome.INTERRUPTED,
                )
                cleanup_control = _remember_first_process_control(
                    cleanup_control,
                    exc,
                )

        try:
            self._backend.close()
        except Exception as exc:
            self._record_debug_error(
                DebugOperation.CLEANUP,
                exc,
                session_id=attached_session,
            )
            cleanup_failed = True
        except BaseException as exc:
            self._record_debug_error(
                DebugOperation.CLEANUP,
                exc,
                session_id=attached_session,
                phase=DebugPhase.CANCELLED,
                outcome=DebugOutcome.INTERRUPTED,
            )
            cleanup_control = _remember_first_process_control(
                cleanup_control,
                exc,
            )

        if discard_runner_state_after_cleanup:
            self._runner = None
        self._attached_session_id = None
        self._focused_conversation = None
        self._collaboration_history_service = None
        self._closed = True

        if cleanup_failed and not cleanup_error_reported:
            try:
                self._write_error_best_effort(
                    InteractiveError(InteractiveErrorCode.CLEANUP_FAILED)
                )
            except BaseException as exc:
                cleanup_control = _remember_first_process_control(
                    cleanup_control,
                    exc,
                )
        if not cleanup_failed and cleanup_control is None:
            self._trace_debug(
                DebugOperation.CLEANUP,
                DebugPhase.SUCCEEDED,
                session_id=attached_session,
                outcome=DebugOutcome.COMPLETED,
            )
            if attached_session is not None:
                self._trace_debug(
                    DebugOperation.ATTACHMENT,
                    DebugPhase.SUCCEEDED,
                    session_id=attached_session,
                    outcome=DebugOutcome.CLAIMABLE,
                )
        retained_control = body_control or cleanup_control
        if retained_control is not None:
            _raise_exception_state(retained_control)
        return 2 if cleanup_failed else exit_code


__all__ = ["InteractiveController"]
