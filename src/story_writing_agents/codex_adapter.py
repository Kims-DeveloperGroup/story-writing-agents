"""Codex-specific binding, command, event, and structured-output adapter."""

from __future__ import annotations
from .provider_adapter import ProviderContinuationRejection

import asyncio
import time
import hashlib
import math
import os
import shutil
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from .agent_context import (
    ContextComponentBytes,
    ProviderCallState,
)
from .config import ConfigurationSnapshot
from .errors import ProviderAuthenticationRequiredError
from .git.session_contracts import GIT_SESSION_WORKTREE_SIBLING_SUFFIX
from .models import AgentDefinition
from .provider_adapter import (
    AgentExecutionProfile,
    ProviderBinding,
    ProviderInvocation,
    ProviderInvocationError,
    ProviderResult,
    ProviderResponseDiagnostic,
    record_provider_failure_accounting,
)
from .provider_process import (
    CreateProcess,
    ProcessExecutableNotFound,
    ProcessRequest,
    ProcessStartFailed,
    ProcessTimedOut,
    WaitFor,
    run_process,
)
from .provider_execution_workspace import ProviderExecutionWorkspace
import story_writing_agents.codex_command_preflight as codex_command_preflight
import story_writing_agents.codex_definition_sources as codex_definition_sources
import story_writing_agents.codex_invocation_files as codex_invocation_files
import story_writing_agents.codex_native_context as codex_native_context
import story_writing_agents.codex_stream_observation as codex_stream_observation
from .codex_native_outcome import (
    classify_native_authentication_failure as _classify_native_authentication_failure,
    interpret_completed_native_outcome as _interpret_completed_native_outcome,
)
from .codex_event_decoding import (
    parse_events as _codex_parse_events,
    provider_usage as _codex_provider_usage,
    started_thread as _codex_started_thread,
)


class CodexAdapter:
    """Own all Codex CLI resources and vendor-specific protocol handling."""

    vendor = "codex"

    def __init__(
        self,
        *,
        executable: str,
        timeout_seconds: float,
        configuration: ConfigurationSnapshot,
        explicit_model: str | None,
        control_root: Path,
        execution_profiles: Mapping[str, AgentExecutionProfile] | None = None,
        create_process: Callable[[], CreateProcess] = (
            lambda: asyncio.create_subprocess_exec
        ),
        wait_for: Callable[[], WaitFor] = lambda: asyncio.wait_for,
        make_temporary_directory: Callable[[], Callable[..., str]] = (
            lambda: tempfile.mkdtemp
        ),
        which: Callable[[], Callable[[str], str | None]] = lambda: shutil.which,
    ) -> None:
        if (
            not isinstance(control_root, Path)
            or not control_root.is_absolute()
            or Path(os.path.realpath(control_root)) != control_root
            or not control_root.is_dir()
        ):
            raise ValueError("Codex control root must be a canonical directory")
        self.executable = executable
        self.timeout_seconds = timeout_seconds
        self._control_root = control_root
        self._configuration = configuration
        self._explicit_model = explicit_model
        self._execution_profiles = (
            None
            if execution_profiles is None
            else dict(execution_profiles)
        )
        if self._execution_profiles is not None and any(
            profile.provider != self.vendor
            for profile in self._execution_profiles.values()
        ):
            raise ValueError("Codex execution profiles must use the Codex provider")
        self._create_process = create_process
        self._wait_for = wait_for
        self._make_temporary_directory = make_temporary_directory
        self._which = which
        self.staging_directories: set[Path] = set()
        self._staged_binding_components: dict[
            Path,
            tuple[ContextComponentBytes, ...],
        ] = {}

    @property
    def model(self) -> str | None:
        """Return the construction-time effective system Codex model."""

        return codex_command_preflight.model(self._explicit_model, self._configuration)

    @property
    def reasoning_effort(self) -> str | None:
        """Return the construction-time system Codex reasoning effort."""

        return codex_command_preflight.reasoning_effort(self._configuration)

    def bind(self, definition: AgentDefinition) -> ProviderBinding:
        """Read, stage, and revalidate one immutable Codex definition."""

        for reference in definition.file_references():
            reference.read_text()
        staging_directory = self.stage_definition(definition)
        provisional_binding = ProviderBinding(
            vendor=self.vendor,
            definition=definition,
            staging_directory=staging_directory,
        )
        try:
            binding_components = self._staged_binding_components.get(
                staging_directory
            )
            if binding_components is None:
                raise RuntimeError("Codex binding accounting is unavailable")
            binding = ProviderBinding(
                vendor=self.vendor,
                definition=definition,
                staging_directory=staging_directory,
                binding_components=binding_components,
            )
            for reference in definition.file_references():
                reference.read_text()
        except BaseException:
            self.release(provisional_binding)
            raise
        return binding

    def stage_definition(self, definition: AgentDefinition) -> Path:
        """Create one isolated Codex project containing the bound AGENTS file."""

        directory, components = self._stage_definition_with_components(definition)
        self._staged_binding_components[directory] = components
        return directory

    def _stage_definition_with_components(
        self,
        definition: AgentDefinition,
    ) -> tuple[Path, tuple[ContextComponentBytes, ...]]:
        """Stage bytes and return accounting derived from the same snapshot."""

        directory = Path(
            self._make_temporary_directory()(
                prefix=(
                    f"story-codex-{definition.agent_id}-"
                    f"{definition.fingerprint[:12]}-"
                )
            )
        )
        try:
            content, components = codex_definition_sources.read_definition_source(
                definition
            )
            (directory / "AGENTS.md").write_bytes(content)
        except Exception:
            shutil.rmtree(directory, ignore_errors=True)
            raise
        self.staging_directories.add(directory)
        return directory, components

    async def invoke(
        self,
        binding: ProviderBinding,
        invocation: ProviderInvocation,
    ) -> ProviderResult:
        """Run one Codex structured turn and validate its thread envelope."""

        execution_workspace = invocation.execution_workspace
        if (
            type(execution_workspace) is not ProviderExecutionWorkspace
            or execution_workspace.session_id != invocation.session_id
        ):
            raise ProviderInvocationError(
                "Codex execution workspace is unavailable",
                taint_existing_thread=False,
            )
        execution_workspace.require_current()

        native_deadline = time.monotonic() + self.timeout_seconds
        native_precheck = None
        native_pre_source_path, native_pre_rediscovered = None, False
        temporary_directory, schema_path, output_path = (
            codex_invocation_files.create_invocation_files(self._make_temporary_directory)
        )
        invocation_failure: BaseException | None = None
        submitted = False
        stdin: bytes | None = None
        try:
            if invocation.payload_json is None:
                raise ProviderInvocationError(
                    "Codex request could not be encoded",
                    taint_existing_thread=False,
                )
            codex_invocation_files.write_schema(schema_path, invocation)
            command = codex_command_preflight.execution_command(
                binding.staging_directory,
                schema_path,
                output_path,
                invocation.thread_id,
                binding.definition.agent_id,
                execution_workspace,
                which=lambda: self._which(),
                get_executable=lambda: self.executable,
                get_control_root=lambda: self._control_root,
                get_native_state_directory=lambda: self._native_state_directory,
                invocation_settings=lambda target: self.invocation_settings(target),
            )
            stdin = invocation.payload_json
            native_state = self._native_state_directory(
                session_root=execution_workspace.worktree_root,
                session_container=self._control_root.with_name(self._control_root.name + GIT_SESSION_WORKTREE_SIBLING_SUFFIX),
                staging=binding.staging_directory, output=output_path)
            if invocation.context_witness is not None:
                prior = await codex_native_context.inspect_codex_native_observation_async(native_state, invocation, invocation.thread_id,
                    previous=invocation.context_witness, anchor=invocation.context_anchor, before=True)
                native_precheck = prior.check
                native_pre_source_path, native_pre_rediscovered = prior.source_path, prior.rediscovered
                if prior.witness is None:
                    raise ProviderInvocationError("native context continuity is unavailable",
                        taint_existing_thread=True, continuation_rejection=ProviderContinuationRejection.UNAVAILABLE,
                        native_context_check=prior.check) from prior.cause
            create_process = self._create_process()
            agent_id = binding.definition.agent_id
            stream_sink = invocation.stream_sink

            handle_stream_line = codex_stream_observation.make_stream_line_handler(
                agent_id, stream_sink,
            )

            async def tracked_create_process(
                *arguments: object,
                **keywords: object,
            ) -> asyncio.subprocess.Process:
                nonlocal submitted
                process = await create_process(*arguments, **keywords)
                submitted = True
                return process

            try:
                result = await run_process(
                    ProcessRequest(
                        command=command,
                        stdin=stdin,
                        timeout_seconds=self.timeout_seconds,
                        on_stdout_line=(
                            handle_stream_line if stream_sink is not None else None
                        ),
                        execution_workspace=execution_workspace,
                    ),
                    create_process=tracked_create_process,
                    wait_for=self._wait_for(),
                )
            except ProcessExecutableNotFound as exc:
                raise ProviderInvocationError(
                    f"Codex CLI executable was not found: {self.executable}",
                    taint_existing_thread=False,
                ) from exc
            except ProcessStartFailed as exc:
                raise ProviderInvocationError(
                    f"Codex CLI could not be started: {exc.error_type}",
                    taint_existing_thread=False,
                ) from exc
            except ProcessTimedOut as exc:
                submitted = True
                failure = ProviderInvocationError(
                    f"Codex CLI timed out after {self.timeout_seconds:g} seconds",
                    taint_existing_thread=True,
                )
                record_provider_failure_accounting(
                    failure,
                    state=ProviderCallState.SUBMITTED,
                    actual_transport_utf8_bytes=len(stdin),
                )
                raise failure from exc
            except asyncio.CancelledError as exc:
                record_provider_failure_accounting(
                    exc,
                    state=(
                        ProviderCallState.SUBMITTED
                        if submitted
                        else ProviderCallState.PREPARED
                    ),
                    actual_transport_utf8_bytes=(len(stdin) if submitted else None),
                )
                raise
            except BaseException as exc:
                record_provider_failure_accounting(
                    exc,
                    state=(
                        ProviderCallState.SUBMITTED
                        if submitted
                        else ProviderCallState.PREPARED
                    ),
                    actual_transport_utf8_bytes=(len(stdin) if submitted else None),
                )
                raise

            submitted = True
            response_diagnostic = ProviderResponseDiagnostic(
                result.stdout, result.stderr, result.returncode,
            )

            try:
                return await _interpret_completed_native_outcome(
                    result,
                    invocation,
                    execution_workspace,
                    output_path,
                    stdin,
                    native_state=native_state,
                    native_deadline=native_deadline,
                    native_precheck=native_precheck,
                    native_pre_source_path=native_pre_source_path,
                    native_pre_rediscovered=native_pre_rediscovered,
                    get_authentication_failure=lambda: self._authentication_failure,
                    get_parse_events=lambda: self.parse_events,
                    get_started_thread=lambda: self.started_thread,
                    get_provider_usage=lambda: self.provider_usage,
                )
            except BaseException as exc:
                if isinstance(exc, ProviderInvocationError):
                    exc.record_response_diagnostic(response_diagnostic)
                record_provider_failure_accounting(
                    exc,
                    state=ProviderCallState.SUBMITTED,
                    actual_transport_utf8_bytes=len(stdin),
                )
                raise
        except BaseException as failure:
            invocation_failure = failure
            raise
        finally:
            codex_invocation_files.remove_invocation_directory(temporary_directory)
            try:
                execution_workspace.require_current()
            except BaseException as validation_failure:
                if invocation_failure is None:
                    record_provider_failure_accounting(
                        validation_failure,
                        state=(
                            ProviderCallState.SUBMITTED
                            if submitted
                            else ProviderCallState.PREPARED
                        ),
                        actual_transport_utf8_bytes=(
                            None if stdin is None else len(stdin)
                        ),
                    )
                    raise
                try:
                    invocation_failure.add_note(
                        "Codex execution workspace exit validation failed: "
                        f"{type(validation_failure).__name__}"
                    )
                except BaseException:
                    pass

    def _native_state_directory(
        self,
        *,
        session_root: Path,
        session_container: Path,
        staging: Path,
        output: Path,
    ) -> Path:
        """Return a narrow, owned Codex state root or fail before launch."""
        return codex_command_preflight.native_state_directory(
            session_root=session_root,
            session_container=session_container,
            staging=staging,
            output=output,
            get_control_root=lambda: self._control_root,
        )

    def command(
        self,
        staging_directory: Path,
        schema_path: Path,
        output_path: Path,
        thread_id: str | None,
        agent_id: str | None = None,
    ) -> list[str]:
        """Build the established value-preserving Codex command line."""
        return codex_command_preflight.command(
            self.executable,
            staging_directory,
            schema_path,
            output_path,
            thread_id,
            agent_id,
            invocation_settings=lambda target: self.invocation_settings(target),
        )

    def invocation_settings(
        self,
        agent_id: str | None,
    ) -> tuple[str | None, str | None]:
        """Resolve Codex flags from the construction snapshot for one agent."""

        if agent_id is None:
            return self.model, self.reasoning_effort
        return codex_command_preflight.invocation_settings(
            agent_id,
            execution_profiles=self._execution_profiles,
            explicit_model=self._explicit_model,
            configuration=self._configuration,
        )

    @classmethod
    def _authentication_failure(
        cls, stdout: bytes,
    ) -> ProviderAuthenticationRequiredError | None:
        """Classify only native terminal error fields, never model item text."""

        return _classify_native_authentication_failure(
            stdout, get_parse_events=lambda: cls.parse_events,
        )

    parse_events = staticmethod(_codex_parse_events)
    started_thread = staticmethod(_codex_started_thread)
    provider_usage = staticmethod(_codex_provider_usage)

    def executable_available(self) -> bool:
        """Return whether the configured Codex executable resolves."""

        return codex_command_preflight.executable_available(
            self._which,
            lambda: self.executable,
        )

    def release(self, binding: ProviderBinding) -> None:
        """Remove one exact ephemeral Codex binding idempotently."""

        if type(binding) is not ProviderBinding or binding.vendor != self.vendor:
            raise TypeError("binding must be a Codex ProviderBinding")
        self.discard_staging_directory(binding.staging_directory)

    def discard_staging_directory(self, directory: Path) -> None:
        """Remove one exact Codex staging directory and its accounting."""

        self._staged_binding_components.pop(directory, None)
        if directory not in self.staging_directories:
            return
        self.staging_directories.remove(directory)
        shutil.rmtree(directory, ignore_errors=True)

    def close(self) -> None:
        """Remove all Codex binding directories idempotently."""

        for directory in tuple(self.staging_directories):
            shutil.rmtree(directory, ignore_errors=True)
        self.staging_directories.clear()
        self._staged_binding_components.clear()
