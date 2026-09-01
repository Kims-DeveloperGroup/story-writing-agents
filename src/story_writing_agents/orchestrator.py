"""Management, configuration, and inspection for story runtimes."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .backend import (
    AgentBackend,
    BackendRuntimeConfigurationCandidate,
    BoundAgent,
    backend_configuration_snapshot,
    release_bound_agent,
)
from .config import ConfigurationSnapshot, ConfigManager
from .conversation_rag_runtime import ConversationRagRuntimeService
from .conversation_storage.composition import compose_filesystem_conversation_storage
from .conversation_storage.contracts import ConversationStorageServices
from .conversations import ConversationEventStore
from .debug_log import (
    DebugComponent,
    DebugOperation,
    DebugOutcome,
    DebugPhase,
    WorkspaceDebugLog,
)
from .directives import apply_directive as persist_directive
from .errors import BackendProtocolError
from .prompts import PromptAssembler
from .workspace import Workspace
from .writing_artifacts import ReferenceOperationHistory
from .writing_artifacts.repository import WritingArtifactRepository
from .writing_artifacts.runtime_references import (
    reference_operation_belongs_to_session,
)


@dataclass(frozen=True, slots=True)
class RuntimeConfigurationCandidate:
    """One quiescent runtime prompt/config candidate awaiting activation."""

    owner: StoryRuntime
    configuration: ConfigurationSnapshot
    prompts: PromptAssembler


class StoryRuntime:
    """Public management, configuration, and inspection runtime."""

    def __init__(
        self,
        workspace: Workspace | str | Path,
        backend: AgentBackend | None = None,
        *,
        conversation_event_store: ConversationEventStore | None = None,
        conversation_storage_services: ConversationStorageServices | None = None,
        conversation_rag_service: ConversationRagRuntimeService | None = None,
    ) -> None:
        workspace_literal = (
            str(workspace.root) if isinstance(workspace, Workspace) else str(workspace)
        )
        self.workspace = (
            workspace if isinstance(workspace, Workspace) else Workspace(workspace)
        )
        self._debug_log = WorkspaceDebugLog(self.workspace)
        self.backend = backend
        self.artifacts = WritingArtifactRepository(self.workspace.root)
        if (
            conversation_storage_services is not None
            and conversation_event_store is not None
        ):
            raise TypeError(
                "conversation_storage_services conflicts with conversation_event_store"
            )
        if (
            conversation_storage_services is not None
            and type(conversation_storage_services) is not ConversationStorageServices
        ):
            raise TypeError(
                "conversation_storage_services must be ConversationStorageServices"
            )
        if conversation_rag_service is not None and (
            type(conversation_rag_service) is not ConversationRagRuntimeService
        ):
            raise TypeError(
                "conversation_rag_service must be ConversationRagRuntimeService"
            )
        if (
            conversation_rag_service is not None
            and conversation_event_store is not None
        ):
            raise TypeError(
                "conversation_rag_service requires conversation_storage_services"
            )
        if conversation_storage_services is None and conversation_event_store is None:
            conversation_storage_services = compose_filesystem_conversation_storage(
                self.workspace
            )
        self.conversation_storage_services = conversation_storage_services
        self.conversation_rag_service = conversation_rag_service
        self.conversation_event_store = (
            conversation_storage_services.event_store
            if conversation_storage_services is not None
            else conversation_event_store
        )
        assert self.conversation_event_store is not None
        self._conversation_workspace_roots = tuple(
            dict.fromkeys((workspace_literal, str(self.workspace.root)))
        )
        backend_overrides = (
            getattr(backend, "backend_overrides", {}) if backend is not None else {}
        )
        self._configuration_snapshot = (
            backend_configuration_snapshot(backend)
            if isinstance(backend, AgentBackend)
            else None
        )
        route_generation_reader = (
            None
            if backend is None
            else getattr(backend, "provider_route_generations", None)
        )
        self.prompts = PromptAssembler(
            self.workspace,
            artifact_repository=self.artifacts,
            backend_overrides=backend_overrides,
            configuration_snapshot=self._configuration_snapshot,
            provider_route_generations=(
                {}
                if not callable(route_generation_reader)
                else route_generation_reader()
            ),
        )
        self.config = ConfigManager(self.workspace)
        self._bound_agents: dict[tuple[str, str], BoundAgent] = {}
        self._desired_binding_fingerprints: dict[str, str] = {}
        self._retiring_bound_agents: dict[tuple[str, str], BoundAgent] = {}
        self._bound_agent_uses: dict[int, tuple[BoundAgent, int]] = {}
        self._binding_tasks: dict[tuple[str, str], asyncio.Task[BoundAgent]] = {}
        self._binding_waiters: dict[tuple[str, str], int] = {}
        self._binding_lock = asyncio.Lock()

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
                component=DebugComponent.RUNTIME,
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
        """Record a runtime failure without allowing diagnostics to replace it."""

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
                DebugComponent.RUNTIME,
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

    def session(self, session_id: str) -> RuntimeSession:
        """Return a validated management view bound to one session."""

        self.workspace.validate_identifier(session_id)
        return RuntimeSession(self, session_id)

    def apply_directive(self, session_id: str, text: str) -> dict[str, object]:
        """Persist one supported runtime directive for a session."""

        try:
            result = persist_directive(self.workspace, session_id, text)
        except (KeyboardInterrupt, SystemExit) as exc:
            self._record_debug_error(
                DebugOperation.DIRECTIVE,
                exc,
                session_id=session_id,
            )
            raise
        except Exception as exc:
            self._record_debug_error(
                DebugOperation.DIRECTIVE,
                exc,
                session_id=session_id,
            )
            raise
        self._trace_debug(
            DebugOperation.DIRECTIVE,
            DebugPhase.SUCCEEDED,
            session_id=session_id,
            outcome=DebugOutcome.COMPLETED,
        )
        return result

    def set_runtime_overrides(
        self,
        session_id: str,
        overrides: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Replace one session's overrides only after complete validation."""

        try:
            validated = self.config.validate_runtime_overrides(overrides)
            self.workspace.save_session_overrides(session_id, validated)
        except (KeyboardInterrupt, SystemExit) as exc:
            self._record_debug_error(
                DebugOperation.CONFIGURATION,
                exc,
                session_id=session_id,
            )
            raise
        except Exception as exc:
            self._record_debug_error(
                DebugOperation.CONFIGURATION,
                exc,
                session_id=session_id,
            )
            raise
        self._trace_debug(
            DebugOperation.CONFIGURATION,
            DebugPhase.SUCCEEDED,
            session_id=session_id,
            outcome=DebugOutcome.COMPLETED,
        )
        return validated

    async def prepare_runtime_configuration(
        self,
        configuration: ConfigurationSnapshot,
        provider_route_generations: Mapping[str, int],
    ) -> RuntimeConfigurationCandidate:
        """Prepare an exact prompt snapshot after proving runtime quiescence."""

        if self.backend is None:
            raise BackendProtocolError(
                "runtime configuration activation requires an AgentBackend"
            )
        if type(configuration) is not ConfigurationSnapshot:
            raise TypeError("configuration must be a ConfigurationSnapshot")
        prompts = PromptAssembler(
            self.workspace,
            artifact_repository=self.artifacts,
            backend_overrides=self.backend.backend_overrides,
            configuration_snapshot=configuration,
            provider_route_generations=provider_route_generations,
        )
        releases: list[BoundAgent] = []
        async with self._binding_lock:
            if self._bound_agent_uses or self._binding_tasks or self._binding_waiters:
                raise BackendProtocolError(
                    "runtime configuration requires a quiescent provider boundary"
                )
            releases.extend(self._bound_agents.values())
            releases.extend(self._retiring_bound_agents.values())
            self._bound_agents.clear()
            self._retiring_bound_agents.clear()
            self._desired_binding_fingerprints.clear()
        self._release_provider_bindings(releases)
        return RuntimeConfigurationCandidate(self, configuration, prompts)

    async def commit_runtime_configuration(
        self,
        candidate: RuntimeConfigurationCandidate,
        backend_candidate: BackendRuntimeConfigurationCandidate,
    ) -> None:
        """Activate backend and prompt snapshots as one quiescent shell state."""

        if (
            type(candidate) is not RuntimeConfigurationCandidate
            or candidate.owner is not self
        ):
            raise TypeError("runtime candidate does not belong to this runtime")
        async with self._binding_lock:
            if (
                self._bound_agents
                or self._retiring_bound_agents
                or self._bound_agent_uses
                or self._binding_tasks
                or self._binding_waiters
            ):
                raise BackendProtocolError(
                    "runtime configuration activation lost quiescence"
                )
            assert self.backend is not None
            self.backend.commit_runtime_configuration(backend_candidate)
            self._configuration_snapshot = candidate.configuration
            self.prompts = candidate.prompts

    @staticmethod
    def discard_runtime_configuration(
        candidate: RuntimeConfigurationCandidate,
    ) -> None:
        """Discard an uncommitted prompt candidate; it owns no resources."""

        if type(candidate) is not RuntimeConfigurationCandidate:
            raise TypeError("candidate must be a RuntimeConfigurationCandidate")

    def inspect_session(self, session_id: str) -> dict[str, Any]:
        """Return persisted session state without mutation."""

        try:
            result = self.workspace.inspect_session(session_id)
        except (KeyboardInterrupt, SystemExit) as exc:
            self._record_debug_error(
                DebugOperation.INSPECTION,
                exc,
                session_id=session_id,
            )
            raise
        except Exception as exc:
            self._record_debug_error(
                DebugOperation.INSPECTION,
                exc,
                session_id=session_id,
            )
            raise
        self._trace_debug(
            DebugOperation.INSPECTION,
            DebugPhase.OBSERVED,
            session_id=session_id,
            outcome=DebugOutcome.COMPLETED,
        )
        return result

    def inspect_debate(self, session_id: str, debate_id: str) -> dict[str, Any]:
        """Return persisted debate state without mutation."""

        try:
            result = self.workspace.inspect_debate(session_id, debate_id)
        except (KeyboardInterrupt, SystemExit) as exc:
            self._record_debug_error(
                DebugOperation.INSPECTION,
                exc,
                session_id=session_id,
                debate_id=debate_id,
            )
            raise
        except Exception as exc:
            self._record_debug_error(
                DebugOperation.INSPECTION,
                exc,
                session_id=session_id,
                debate_id=debate_id,
            )
            raise
        self._trace_debug(
            DebugOperation.INSPECTION,
            DebugPhase.OBSERVED,
            session_id=session_id,
            debate_id=debate_id,
            outcome=DebugOutcome.COMPLETED,
        )
        return result

    def inspect_debate_context(
        self,
        session_id: str,
        debate_id: str,
    ) -> dict[str, Any] | None:
        """Return a strict context sidecar or ``None`` without mutating state."""

        try:
            result = self.workspace.inspect_debate_context(session_id, debate_id)
        except (KeyboardInterrupt, SystemExit) as exc:
            self._record_debug_error(
                DebugOperation.INSPECTION,
                exc,
                session_id=session_id,
                debate_id=debate_id,
            )
            raise
        except Exception as exc:
            self._record_debug_error(
                DebugOperation.INSPECTION,
                exc,
                session_id=session_id,
                debate_id=debate_id,
            )
            raise
        self._trace_debug(
            DebugOperation.INSPECTION,
            DebugPhase.OBSERVED,
            session_id=session_id,
            debate_id=debate_id,
            outcome=DebugOutcome.COMPLETED,
        )
        return result

    def inspect_reference_operation(
        self,
        operation_id: str,
    ) -> ReferenceOperationHistory | None:
        """Return validated content-free read history for one operation."""

        return self.artifacts.inspect_reference_operation(operation_id)

    @staticmethod
    def _release_provider_bindings(bindings: list[BoundAgent]) -> None:
        """Release each identity once outside the runtime binding lock."""

        released: set[int] = set()
        for bound_agent in bindings:
            identity = id(bound_agent)
            if identity in released:
                continue
            released.add(identity)
            release_bound_agent(bound_agent)


class RuntimeSession:
    """Bound-session management API; state remains filesystem-backed."""

    def __init__(self, runtime: StoryRuntime, session_id: str) -> None:
        self.runtime = runtime
        self.session_id = session_id

    def apply_directive(self, text: str) -> dict[str, object]:
        """Apply a supported directive to this session."""

        return self.runtime.apply_directive(self.session_id, text)

    def set_overrides(self, overrides: Mapping[str, Any]) -> dict[str, Any]:
        """Replace this session's validated runtime overrides."""

        return self.runtime.set_runtime_overrides(self.session_id, overrides)

    def inspect(self) -> dict[str, Any]:
        """Return persisted state for this session."""

        return self.runtime.inspect_session(self.session_id)

    def inspect_debate(self, debate_id: str) -> dict[str, Any]:
        """Return one persisted debate owned by this session."""

        return self.runtime.inspect_debate(self.session_id, debate_id)

    def inspect_debate_context(self, debate_id: str) -> dict[str, Any] | None:
        """Return this session's strict context sidecar without mutation."""

        return self.runtime.inspect_debate_context(self.session_id, debate_id)

    def inspect_reference_operation(
        self,
        operation_id: str,
    ) -> ReferenceOperationHistory | None:
        """Return read history only when the operation belongs to this session."""

        if not reference_operation_belongs_to_session(
            operation_id,
            self.session_id,
        ):
            raise ValueError("reference operation does not belong to this session")
        return self.runtime.inspect_reference_operation(operation_id)
