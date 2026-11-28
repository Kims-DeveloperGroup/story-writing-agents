"""Antigravity-specific binding, command, and structured-JSON adapter."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import shutil
import subprocess
from collections.abc import Callable, Mapping
from operator import attrgetter
from pathlib import Path
from typing import Any
from uuid import uuid4

from .agent_context import ProviderCallState, ProviderJsonEncodingError
from .models import AgentDefinition
from .errors import ProviderAuthenticationRequiredError
from .provider_adapter import (
    PROVIDER_TIMEOUT_GRACE_SECONDS,
    AgentExecutionProfile,
    ProviderBinding,
    ProviderInvocation,
    ProviderInvocationError,
    ProviderResponseDiagnostic,
    ProviderResult,
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
from .workspace.session_role_projection import (
    SessionRoleProjectionPlan,
    require_session_role_projection,
)
import story_writing_agents.antigravity_agent_definition as antigravity_agent_definition
import story_writing_agents.antigravity_command as antigravity_command
import story_writing_agents.antigravity_invocation_options as antigravity_invocation_options
import story_writing_agents.antigravity_native_recovery as antigravity_native_recovery
import story_writing_agents.antigravity_recovery_matching as antigravity_recovery_matching
import story_writing_agents.antigravity_state_directories as antigravity_state_directories
import story_writing_agents.antigravity_stream_observation as antigravity_stream_observation
from .antigravity_native_outcome import (
    classify_native_authentication_failure as _classify_native_authentication_failure,
    complete_native_outcome as _complete_native_outcome,
)
from .antigravity_native_output_schema import render_native_output_schema as _render_native_output_schema
from .antigravity_response_decoding import (
    ProviderResultHooks as _ProviderResultHooks,
    bind_provider_result as _bind_provider_result,
    parse_result as _decode_antigravity_response,
    malformed_output as _raise_antigravity_malformed_output,
    provider_usage as _antigravity_provider_usage,
    unwrap_result_envelope as _unwrap_antigravity_result_envelope,
)


CapabilityRun = antigravity_command.CapabilityRun


_PROVIDER_RESULT_HOOKS: _ProviderResultHooks[type[AntigravityAdapter]] = _ProviderResultHooks(
    parse_result=attrgetter("_parse_result"),
    unwrap_result_envelope=attrgetter("_unwrap_result_envelope"),
    malformed_output=attrgetter("_malformed_output"),
    native_output_schema=attrgetter("native_output_schema"),
    provider_usage=attrgetter("provider_usage"),
    malformed_call_pattern=lambda: antigravity_recovery_matching.MALFORMED_FUNCTION_CALL,
    safe_conversation_id_pattern=lambda: antigravity_native_recovery.SAFE_CONVERSATION_ID,
)


class AntigravityAdapter:
    """Own all Antigravity CLI resources and vendor protocol handling."""

    vendor = "antigravity"

    def __init__(
        self,
        *,
        executable: str,
        timeout_seconds: float,
        state_root: Path,
        explicit_model: str | None,
        explicit_effort: str | None,
        system_model: str | None = None,
        system_effort: str | None = None,
        agent_settings: Mapping[
            str,
            tuple[str | None, str | None],
        ] | None = None,
        execution_profiles: Mapping[str, AgentExecutionProfile] | None = None,
        create_process: Callable[[], CreateProcess] = (
            lambda: asyncio.create_subprocess_exec
        ),
        wait_for: Callable[[], WaitFor] = lambda: asyncio.wait_for,
        which: Callable[[], Callable[[str], str | None]] = lambda: shutil.which,
        capability_run: Callable[[], CapabilityRun] = lambda: subprocess.run,
    ) -> None:
        if not isinstance(executable, str) or not executable:
            raise ValueError("Antigravity executable must be non-empty text")
        self.executable = executable
        self.timeout_seconds = timeout_seconds
        self.state_root = state_root
        self._explicit_model = antigravity_invocation_options.optional_setting(
            explicit_model,
            label="Antigravity model",
        )
        self._explicit_effort = antigravity_invocation_options.optional_effort(explicit_effort)
        self._system_model = antigravity_invocation_options.optional_setting(
            system_model,
            label="Antigravity system model",
        )
        self._system_effort = antigravity_invocation_options.optional_effort(system_effort)
        self._agent_settings = antigravity_invocation_options.copy_agent_settings(
            agent_settings,
            agent_id_pattern=antigravity_native_recovery.SAFE_CONVERSATION_ID,
        )
        self._execution_profiles = antigravity_invocation_options.copy_execution_profiles(
            execution_profiles,
        )
        antigravity_invocation_options.require_execution_profile_provider(
            self._execution_profiles,
            get_vendor=lambda: self.vendor,
        )
        self._create_process = create_process
        self._wait_for = wait_for
        self._which = which
        self._capability_run = capability_run

    @property
    def model(self) -> str | None:
        """Return the construction-time effective system Antigravity model."""
        return antigravity_invocation_options.model(self._explicit_model, self._system_model)

    @property
    def effort(self) -> str | None:
        """Return the construction-time effective system Antigravity effort."""
        return antigravity_invocation_options.effort(self._explicit_effort, self._system_effort)












    def bind(self, definition: AgentDefinition) -> ProviderBinding:
        """Stage one stable, isolated Antigravity agent definition."""

        if (
            type(definition) is not AgentDefinition
            or definition.backend != self.vendor
            or not isinstance(definition.agent_id, str)
            or antigravity_native_recovery.SAFE_CONVERSATION_ID.fullmatch(definition.agent_id) is None
            or (
                self._agent_settings is not None
                and definition.agent_id not in self._agent_settings
            )
            or not isinstance(definition.fingerprint, str)
            or antigravity_agent_definition.SAFE_FINGERPRINT.fullmatch(definition.fingerprint) is None
        ):
            raise ValueError("Antigravity agent definition is invalid")
        for reference in definition.file_references():
            reference.read_text()
        staging_directory, binding_components = antigravity_agent_definition.install_agent_definition(
            definition, state_root=self.state_root,
        )
        for reference in definition.file_references():
            reference.read_text()
        return ProviderBinding(
            vendor=self.vendor,
            definition=definition,
            staging_directory=staging_directory,
            binding_components=binding_components,
        )

    @classmethod
    def session_role_projection_bytes(cls, binding: ProviderBinding) -> bytes:
        """Return exact adapter-owned role bytes for guarded host projection."""

        return cls.session_role_projection(binding).content

    @classmethod
    def session_role_projection(
        cls,
        binding: ProviderBinding,
    ) -> SessionRoleProjectionPlan:
        """Return one validated content-qualified native session role."""

        if type(binding) is not ProviderBinding or binding.vendor != cls.vendor:
            raise OSError("Antigravity provider binding is invalid")
        return antigravity_agent_definition.verify_installed_agent_definition(binding)

    async def invoke(
        self,
        binding: ProviderBinding,
        invocation: ProviderInvocation,
    ) -> ProviderResult:
        """Run one structured Antigravity turn and validate its JSON result."""

        self._validate_invocation(binding, invocation)
        execution_workspace = invocation.execution_workspace
        assert type(execution_workspace) is ProviderExecutionWorkspace
        generation_id = execution_workspace.generation_provenance.removeprefix(
            "sha256:"
        )
        runtime_root = (
            self.state_root
            / "sessions"
            / invocation.session_id
            / generation_id
            / binding.definition.agent_id
            / binding.definition.fingerprint
        )
        provider_state_directory = runtime_root / "provider"
        if invocation.payload_json is None:
            raise ProviderInvocationError(
                "Antigravity request could not be encoded",
                taint_existing_thread=False,
            ) from None
        try:
            staged_role = self.session_role_projection(binding)
        except (OSError, RuntimeError) as exc:
            failure = ProviderInvocationError(
                "Antigravity provider workspace could not be prepared",
                taint_existing_thread=False,
            )
            record_provider_failure_accounting(
                failure,
                state=ProviderCallState.PREPARED,
                actual_transport_utf8_bytes=None,
            )
            raise failure from exc
        try:
            transport_schema = self.native_output_schema(invocation.schema)
            output_format = (
                "stream-json"
                if invocation.stream_sink is not None
                else "json"
            )
            command = self.command(
                provider_state_directory=provider_state_directory,
                schema=transport_schema,
                thread_id=invocation.thread_id,
                agent_id=binding.definition.agent_id,
                output_format=output_format,
                native_agent_id=staged_role.native_agent_id,
            )
            fresh_command = (
                self.command(
                    provider_state_directory=provider_state_directory,
                    schema=transport_schema,
                    thread_id=None,
                    agent_id=binding.definition.agent_id,
                    output_format=output_format,
                    native_agent_id=staged_role.native_agent_id,
                )
                if invocation.thread_id is not None
                else command
            )
            prompt = invocation.payload_json.decode("utf-8", errors="strict")
            command.extend(["--print", prompt])
            if fresh_command is not command:
                fresh_command.extend(["--print", prompt])
            transport_utf8_bytes = len(invocation.payload_json)
        except (
            ProviderJsonEncodingError,
            UnicodeError,
            TypeError,
            ValueError,
        ):
            raise ProviderInvocationError(
                "Antigravity request could not be encoded",
                taint_existing_thread=False,
            ) from None
        try:
            antigravity_command.require_state_workspace_disjoint(
                provider_state_directory,
                execution_workspace,
            )
            antigravity_state_directories.prepare_owned_directory_chain(
                "sessions",
                invocation.session_id,
                generation_id,
                binding.definition.agent_id,
                binding.definition.fingerprint,
                state_root=self.state_root,
            )
            native_state_existed = antigravity_state_directories.prepare_owned_directory(
                provider_state_directory
            )
            effective_thread_id = (
                invocation.thread_id if native_state_existed else None
            )
            if effective_thread_id is None:
                command = fresh_command
            projected_role = require_session_role_projection(
                execution_workspace,
                plan=staged_role,
            )
            command = antigravity_command.execution_command(
                command,
                provider_state_directory=provider_state_directory,
                execution_workspace=execution_workspace,
                projected_role=projected_role.path,
                settings=self,
                which=lambda: self._which(),
            )
        except ProviderInvocationError as exc:
            record_provider_failure_accounting(
                exc,
                state=ProviderCallState.PREPARED,
                actual_transport_utf8_bytes=None,
            )
            raise
        except (OSError, RuntimeError) as exc:
            failure = ProviderInvocationError(
                "Antigravity provider workspace could not be prepared",
                taint_existing_thread=False,
            )
            record_provider_failure_accounting(
                failure,
                state=ProviderCallState.PREPARED,
                actual_transport_utf8_bytes=None,
            )
            raise failure from exc
        submitted = False
        create_process = self._create_process()
        recovery_baseline = antigravity_native_recovery.native_recovery_baseline(
            self,
            provider_state_directory,
            effective_thread_id,
        )
        handle_stream_line, finalize_stream_observation = (
            antigravity_stream_observation.make_stream_observation(
                binding.definition.agent_id,
                invocation.stream_sink,
            )
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
                    stdin=b"",
                    timeout_seconds=(
                        self.timeout_seconds + PROVIDER_TIMEOUT_GRACE_SECONDS
                    ),
                    on_stdout_line=(
                        handle_stream_line
                        if invocation.stream_sink is not None
                        else None
                    ),
                    execution_workspace=execution_workspace,
                ),
                create_process=tracked_create_process,
                wait_for=self._wait_for(),
            )
        except ProcessExecutableNotFound as exc:
            raise ProviderInvocationError(
                "Antigravity CLI executable was not found",
                taint_existing_thread=False,
            ) from exc
        except ProcessStartFailed as exc:
            raise ProviderInvocationError(
                f"Antigravity CLI could not be started: {exc.error_type}",
                taint_existing_thread=False,
            ) from exc
        except ProcessTimedOut as exc:
            failure = ProviderInvocationError(
                "Antigravity CLI timed out after "
                f"{self.timeout_seconds:g} seconds",
                taint_existing_thread=True,
            )
            record_provider_failure_accounting(
                failure,
                state=ProviderCallState.SUBMITTED,
                actual_transport_utf8_bytes=transport_utf8_bytes,
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
                actual_transport_utf8_bytes=(
                    transport_utf8_bytes if submitted else None
                ),
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
                actual_transport_utf8_bytes=(
                    transport_utf8_bytes if submitted else None
                ),
            )
            raise

        submitted = True
        try:
            require_session_role_projection(
                execution_workspace,
                plan=staged_role,
                expected=projected_role,
            )
        except (OSError, RuntimeError) as exc:
            failure = ProviderInvocationError(
                "Antigravity session role projection changed",
                taint_existing_thread=True,
            )
            failure.record_response_diagnostic(
                ProviderResponseDiagnostic(
                    stdout=result.stdout,
                    stderr=result.stderr,
                    exit_code=result.returncode,
                )
            )
            record_provider_failure_accounting(
                failure,
                state=ProviderCallState.SUBMITTED,
                actual_transport_utf8_bytes=transport_utf8_bytes,
            )
            raise failure from exc
        return _complete_native_outcome(
            result,
            invocation,
            finalize_stream_observation=finalize_stream_observation,
            transport_utf8_bytes=transport_utf8_bytes,
            get_authentication_failure=lambda: self._authentication_failure,
            decode_result=lambda effective_stdout, response_diagnostic, incomplete_output: self._provider_result(
                    effective_stdout,
                    expected_thread_id=effective_thread_id,
                    expected_schema=transport_schema,
                    completion_check=lambda thread_id, output, error: antigravity_native_recovery.has_current_finish(
                        self,
                        provider_state_directory,
                        recovery_baseline,
                        thread_id,
                        prompt,
                        output,
                        error,
                    ),
                    response_diagnostic=response_diagnostic,
                    completion_required=incomplete_output,
                ),
        )

    _unwrap_result_envelope = staticmethod(_unwrap_antigravity_result_envelope)

    @classmethod
    def _authentication_failure(
        cls, stdout: bytes, returncode: int,
    ) -> ProviderAuthenticationRequiredError | None:
        """Recognize native auth errors; only the supported exit-1 shape proves rejection."""

        return _classify_native_authentication_failure(
            stdout,
            returncode,
            parse_result=lambda stdout: cls._parse_result(stdout),
            unwrap_result_envelope=lambda terminal: cls._unwrap_result_envelope(terminal),
        )

    @staticmethod
    def _validate_invocation(
        binding: ProviderBinding,
        invocation: ProviderInvocation,
    ) -> None:
        """Reject inconsistent private values before filesystem or process work."""

        if (
            type(binding) is not ProviderBinding
            or binding.vendor != AntigravityAdapter.vendor
            or binding.definition.backend != AntigravityAdapter.vendor
        ):
            raise ProviderInvocationError(
                "Antigravity provider binding is invalid",
                taint_existing_thread=False,
            )
        if (
            not isinstance(invocation.session_id, str)
            or antigravity_native_recovery.SAFE_CONVERSATION_ID.fullmatch(invocation.session_id) is None
        ):
            raise ProviderInvocationError(
                "Antigravity session ID is invalid",
                taint_existing_thread=False,
            )
        if invocation.thread_id is not None and (
            not isinstance(invocation.thread_id, str)
            or antigravity_native_recovery.SAFE_CONVERSATION_ID.fullmatch(invocation.thread_id) is None
        ):
            raise ProviderInvocationError(
                "Antigravity conversation ID is invalid",
                taint_existing_thread=False,
            )
        execution_workspace = invocation.execution_workspace
        if (
            type(execution_workspace) is not ProviderExecutionWorkspace
            or execution_workspace.session_id != invocation.session_id
        ):
            raise ProviderInvocationError(
                "Antigravity execution workspace is unavailable",
                taint_existing_thread=False,
            )
        execution_workspace.require_current()

    native_output_schema = staticmethod(_render_native_output_schema)

    def command(
        self,
        *,
        provider_state_directory: Path,
        schema: Mapping[str, Any],
        thread_id: str | None,
        agent_id: str,
        output_format: str = "json",
        native_agent_id: str,
    ) -> list[str]:
        """Build Antigravity options for the already rendered transport schema."""
        return antigravity_command.command(
            self,
            provider_state_directory=provider_state_directory,
            schema=schema,
            thread_id=thread_id,
            agent_id=agent_id,
            output_format=output_format,
            native_agent_id=native_agent_id,
            agent_id_pattern=antigravity_native_recovery.SAFE_CONVERSATION_ID,
        )

    def invocation_settings(
        self,
        agent_id: str | None,
    ) -> tuple[str | None, str | None]:
        """Resolve Antigravity flags from immutable settings for one agent."""
        return antigravity_invocation_options.invocation_settings(
            agent_id,
            agent_settings=self._agent_settings,
            execution_profiles=self._execution_profiles,
            explicit_model=self._explicit_model,
            explicit_effort=self._explicit_effort,
            system_model=self._system_model,
            system_effort=self._system_effort,
            defaults=self,
        )

    _provider_result = classmethod(_bind_provider_result(_PROVIDER_RESULT_HOOKS))


    provider_usage = staticmethod(_antigravity_provider_usage)

    _parse_result = staticmethod(_decode_antigravity_response)

    _malformed_output = staticmethod(_raise_antigravity_malformed_output)

    def executable_available(self) -> bool:
        """Return whether the configured Antigravity executable resolves."""
        return antigravity_command.executable_available(self._which, self)

    def headless_capabilities_available(self) -> bool:
        """Run one bounded, provider-free check for the required headless flags."""
        return antigravity_command.headless_capabilities_available(
            self,
            state_root=self.state_root,
            capability_run=lambda: self._capability_run(),
        )

    def close(self) -> None:
        """Retain provider-native state for later backend instances."""

    def release(self, binding: ProviderBinding) -> None:
        """Retain one provider-native Antigravity binding."""

        if type(binding) is not ProviderBinding or binding.vendor != self.vendor:
            raise TypeError("binding must be an Antigravity ProviderBinding")
