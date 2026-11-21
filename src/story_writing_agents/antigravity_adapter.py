"""Antigravity-specific binding, command, and structured-JSON adapter."""

from __future__ import annotations

from .agent_context.accounting import ProviderUsageObservation, ProviderUsageScope
from .structured_output_validation import strict_json_member_text


import asyncio
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from uuid import uuid4

from .agent_context import (
    ProviderCallState,
    ProviderJsonEncodingError,
    ProviderStreamEvent,
    ProviderStreamEventKind,
    ProviderUsage,
    canonical_provider_json,
)
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
from .antigravity_response_decoding import (
    reject_duplicate_fields as _reject_duplicate_fields,
    reject_constant as _reject_constant,
    finite_float as _finite_float,
    parse_result as _decode_antigravity_response,
    malformed_output as _raise_antigravity_malformed_output,
)


_NATIVE_PARTIAL_TIMEOUT = re.compile(
    rb"^\[agy\] print timeout after [^\r\n]+ with turn in progress; "
    rb"returning partial output\r?$", re.MULTILINE,
)
_NATIVE_TRUNCATED = re.compile(
    rb"^error: [^\r\n]+ \(response may be truncated\)\r?$", re.MULTILINE,
)

CapabilityRun = antigravity_command.CapabilityRun


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
        captured_result_bytes: list[bytes] = []
        invalid_stream_line: str | None = None
        has_trailing_data_after_terminal: bool = False
        callback_lines_delivered: int = 0
        agent_id = binding.definition.agent_id
        stream_sink = invocation.stream_sink

        def emit_stream_event(event: ProviderStreamEvent) -> None:
            if stream_sink is not None:
                stream_sink(event)

        def _extract_string_message(value: object) -> str | None:
            """Extract validated string message from string or dict message field."""
            if isinstance(value, str):
                cleaned = value.strip()
                return cleaned if cleaned else None
            if isinstance(value, dict):
                msg = value.get("message")
                if isinstance(msg, str):
                    cleaned = msg.strip()
                    return cleaned if cleaned else None
            return None

        def handle_stream_line(raw_line: bytes) -> None:
            nonlocal callback_lines_delivered, invalid_stream_line, has_trailing_data_after_terminal
            callback_lines_delivered += 1
            stripped = raw_line.strip()
            if not stripped:
                return
            try:
                event = json.loads(
                    stripped.decode("utf-8", errors="strict"),
                    object_pairs_hook=_reject_duplicate_fields,
                    parse_constant=_reject_constant,
                    parse_float=_finite_float,
                )
            except (ValueError, RecursionError, UnicodeError):
                if invalid_stream_line is None:
                    invalid_stream_line = "malformed JSON line"
                return
            if type(event) is not dict:
                if invalid_stream_line is None:
                    invalid_stream_line = "non-object JSON line"
                return

            has_type = "type" in event
            has_event = "event" in event
            has_discriminator = has_type or has_event
            if has_discriminator:
                types_to_check: list[str] = []
                for key in ("type", "event"):
                    if key in event:
                        val = event.get(key)
                        if val is None or not isinstance(val, str):
                            if invalid_stream_line is None:
                                invalid_stream_line = "invalid event discriminator"
                            return
                        norm_val = val.strip().lower()
                        if not norm_val:
                            if invalid_stream_line is None:
                                invalid_stream_line = "unknown event type ''"
                            return
                        types_to_check.append(norm_val)
                if len(types_to_check) == 2 and types_to_check[0] != types_to_check[1]:
                    if invalid_stream_line is None:
                        invalid_stream_line = "conflicting event discriminator"
                    return
                event_type = types_to_check[0]
            else:
                event_type = ""

            is_terminal = False
            if not has_discriminator:
                is_terminal = (
                    "structured_output" in event
                    or (
                        "conversation_id" in event
                        and str(event.get("status", "")).upper() in {"SUCCESS", "ERROR"}
                    )
                )
            elif event_type == "result" and isinstance(event.get("result"), dict):
                is_terminal = True

            if is_terminal:
                captured_result_bytes.append(stripped)
                target_dict = None
                if event_type == "result":
                    res = event.get("result")
                    if isinstance(res, dict):
                        target_dict = res
                elif not has_discriminator:
                    target_dict = event
                if target_dict is not None and str(target_dict.get("status", "")).upper() == "ERROR":
                    err_text = _extract_string_message(target_dict.get("error"))
                    if err_text:
                        emit_stream_event(
                            ProviderStreamEvent(
                                ProviderStreamEventKind.STATUS,
                                agent_id,
                                f"Error: {err_text[:120]}",
                            )
                        )
                return

            if captured_result_bytes:
                has_trailing_data_after_terminal = True
                return

            if event_type in {
                "turn_start",
                "turn.started",
                "turn_started",
                "status",
            } or (not has_discriminator and str(event.get("status", "")).lower() == "running"):
                msg = None
                for key in ("text", "message", "status"):
                    extracted = _extract_string_message(event.get(key))
                    if extracted:
                        msg = extracted
                        break
                if msg is None:
                    msg = "Generating response..."
                emit_stream_event(
                    ProviderStreamEvent(
                        ProviderStreamEventKind.STATUS,
                        agent_id,
                        msg,
                    )
                )
            elif event_type in {
                "thought",
                "reasoning",
                "thought_delta",
                "reasoning_delta",
            }:
                text = str(
                    event.get("text")
                    or event.get("delta")
                    or event.get("thought")
                    or event.get("reasoning")
                    or ""
                )
                if text:
                    emit_stream_event(
                        ProviderStreamEvent(
                            ProviderStreamEventKind.THOUGHT_DELTA,
                            agent_id,
                            text,
                        )
                    )
            elif event_type in {
                "text_delta",
                "content_delta",
                "token",
                "agent_message",
                "agent_response",
                "text",
            }:
                is_agent_response = (
                    event_type in {"agent_message", "agent_response"}
                    and str(event.get("step_type") or "agent_response").strip().lower() == "agent_response"
                ) or (
                    str(event.get("step_type") or "").strip().lower() == "agent_response"
                )
                if is_agent_response:
                    raw_text = (
                        event.get("text")
                        if "text" in event
                        else event.get("delta")
                        if "delta" in event
                        else event.get("content")
                    )
                    if isinstance(raw_text, str) and raw_text:
                        emit_stream_event(
                            ProviderStreamEvent(
                                ProviderStreamEventKind.TEXT_DELTA,
                                agent_id,
                                raw_text,
                            )
                        )
            elif event_type == "init":
                init_data = event.get("init")
                if init_data is not None and type(init_data) is not dict:
                    if invalid_stream_line is None:
                        invalid_stream_line = "init payload is not an object"
                    return
                emit_stream_event(
                    ProviderStreamEvent(
                        ProviderStreamEventKind.STATUS,
                        agent_id,
                        "Generating response...",
                    )
                )
            elif event_type == "step_update":
                step_update = event.get("step_update")
                if type(step_update) is not dict:
                    if invalid_stream_line is None:
                        invalid_stream_line = "step_update payload is not an object"
                    return
                step_type_val = step_update.get("step_type")
                if step_type_val is not None and not isinstance(step_type_val, str):
                    if invalid_stream_line is None:
                        invalid_stream_line = "invalid step_type discriminator"
                    return
                step_type = str(step_type_val or "").strip().lower()

                raw_text_delta = step_update.get("text_delta")
                if raw_text_delta is not None and not isinstance(raw_text_delta, str):
                    if invalid_stream_line is None:
                        invalid_stream_line = "invalid text_delta type"
                    return
                raw_thought_delta = (
                    step_update.get("thought_delta")
                    or step_update.get("reasoning_delta")
                )
                if raw_thought_delta is not None and not isinstance(raw_thought_delta, str):
                    if invalid_stream_line is None:
                        invalid_stream_line = "invalid thought_delta type"
                    return

                text_delta = raw_text_delta or ""
                thought_delta = raw_thought_delta or ""
                if step_type in {"thought", "reasoning"}:
                    if text_delta and not thought_delta:
                        thought_delta = text_delta
                    text_delta = ""
                elif step_type != "agent_response":
                    text_delta = ""

                if thought_delta:
                    emit_stream_event(
                        ProviderStreamEvent(
                            ProviderStreamEventKind.THOUGHT_DELTA,
                            agent_id,
                            thought_delta,
                        )
                    )
                if text_delta:
                    emit_stream_event(
                        ProviderStreamEvent(
                            ProviderStreamEventKind.TEXT_DELTA,
                            agent_id,
                            text_delta,
                        )
                    )
                if step_type == "error_message":
                    error_text = _extract_string_message(step_update.get("error"))
                    if not error_text:
                        for key in ("message", "text"):
                            extracted = _extract_string_message(step_update.get(key))
                            if extracted:
                                error_text = extracted
                                break
                    if error_text:
                        emit_stream_event(
                            ProviderStreamEvent(
                                ProviderStreamEventKind.STATUS,
                                agent_id,
                                f"Error: {error_text[:120]}",
                            )
                        )
            else:
                if invalid_stream_line is None:
                    invalid_stream_line = f"unknown event type {event_type!r}"

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
        if invocation.stream_sink is not None:
            if callback_lines_delivered == 0 and result.stdout:
                for raw_line in result.stdout.splitlines(keepends=True):
                    handle_stream_line(raw_line)
            diagnostic_stdout = (
                result.stdout
                if len(result.stdout.splitlines()) <= 1
                else captured_result_bytes[0]
                if len(captured_result_bytes) == 1
                else b""
            )
        else:
            diagnostic_stdout = result.stdout

        partial_timeout = bool(_NATIVE_PARTIAL_TIMEOUT.search(result.stderr))
        incomplete_output = partial_timeout or bool(
            _NATIVE_TRUNCATED.search(result.stderr)
        )
        response_diagnostic = ProviderResponseDiagnostic(
            stdout=diagnostic_stdout,
            stderr=result.stderr,
            exit_code=result.returncode,
        )
        try:
            if invocation.stream_sink is not None:
                if invalid_stream_line is not None:
                    raise ProviderInvocationError(
                        f"Antigravity streaming response contained invalid stream line: {invalid_stream_line}",
                        taint_existing_thread=True,
                    )
            effective_stdout = (
                captured_result_bytes[0]
                if (invocation.stream_sink is not None and len(captured_result_bytes) == 1)
                else result.stdout
            )
            authentication = self._authentication_failure(
                effective_stdout, result.returncode,
            )
            if authentication is not None:
                raise ProviderInvocationError(
                    str(authentication),
                    taint_existing_thread=not authentication.replay_safe,
                    authentication_failure=authentication,
                )
            if result.returncode != 0:
                raise ProviderInvocationError(
                    f"Antigravity CLI exited with status {result.returncode}",
                    taint_existing_thread=True,
                )
            if invocation.stream_sink is not None:
                if len(captured_result_bytes) != 1:
                    raise ProviderInvocationError(
                        f"Antigravity streaming response contained {len(captured_result_bytes)} terminal result events; expected exactly 1",
                        taint_existing_thread=True,
                    )
                if has_trailing_data_after_terminal:
                    raise ProviderInvocationError(
                        "Antigravity streaming response contained trailing data after terminal result",
                        taint_existing_thread=True,
                    )
            provider_result = self._provider_result(
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
            )
            return ProviderResult(
                thread_id=provider_result.thread_id,
                response_text=provider_result.response_text,
                usage=provider_result.usage,
                usage_observation=provider_result.usage_observation,
                actual_transport_utf8_bytes=transport_utf8_bytes,
                response_diagnostic=provider_result.response_diagnostic,
            )
        except BaseException as exc:
            if isinstance(exc, ProviderInvocationError):
                if partial_timeout:
                    exc.timed_out = True
                exc.record_response_diagnostic(response_diagnostic)
            record_provider_failure_accounting(
                exc,
                state=ProviderCallState.SUBMITTED,
                actual_transport_utf8_bytes=transport_utf8_bytes,
            )
            raise

    @classmethod
    def _unwrap_result_envelope(cls, terminal: dict[str, Any]) -> dict[str, Any]:
        """Extract inner result payload if terminal is a result/event envelope."""
        has_type = "type" in terminal
        has_event = "event" in terminal
        if not (has_type or has_event):
            return terminal
        types_to_check: list[str] = []
        for key in ("type", "event"):
            if key in terminal:
                val = terminal.get(key)
                if not isinstance(val, str):
                    return terminal
                norm_val = val.strip().lower()
                if not norm_val:
                    return terminal
                types_to_check.append(norm_val)
        if len(types_to_check) == 2 and types_to_check[0] != types_to_check[1]:
            return terminal
        if types_to_check and types_to_check[0] == "result" and isinstance(terminal.get("result"), dict):
            return terminal["result"]
        return terminal

    @classmethod
    def _authentication_failure(
        cls, stdout: bytes, returncode: int,
    ) -> ProviderAuthenticationRequiredError | None:
        """Recognize native auth errors; only the supported exit-1 shape proves rejection."""

        try:
            terminal = cls._parse_result(stdout)
        except ProviderInvocationError:
            return None
        terminal = cls._unwrap_result_envelope(terminal)
        error = terminal.get("error")
        if terminal.get("status") != "ERROR":
            return None
        message = error.get("message") if type(error) is dict else error
        native_401 = (
            type(error) is dict
            and any(type(error.get(key)) is int and error[key] == 401
                    for key in ("status", "status_code", "code"))
        ) or (
            type(message) is str
            and re.search(r"\b(?:HTTP(?: status)?|status(?: code)?)\s*:?\s*401\b|\b401 Unauthorized\b", message, re.IGNORECASE)
            is not None
        )
        if message != "authentication failed or timed out" and not native_401:
            return None
        usage = terminal.get("usage")
        counters = {"input_tokens", "output_tokens", "thinking_tokens",
                    "cache_read_tokens", "total_tokens"}
        replay_safe = (
            returncode == 1
            and set(terminal) == {"conversation_id", "status", "response", "error",
                                  "duration_seconds", "num_turns", "usage"}
            and terminal["error"] == "authentication failed or timed out"
            and terminal["conversation_id"] == ""
            and terminal["response"] == ""
            and type(terminal["duration_seconds"]) is int
            and terminal["duration_seconds"] == 0
            and type(terminal["num_turns"]) is int and terminal["num_turns"] == 0
            and type(usage) is dict and set(usage) == counters
            and all(type(value) is int and value == 0 for value in usage.values())
        )
        return ProviderAuthenticationRequiredError("antigravity", replay_safe=replay_safe)

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

    @staticmethod
    def native_output_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
        """Copy an equivalent native schema without changing instance data.

        Prepare this schema before ProviderInvocation when full schema-byte
        accounting is required. Rendering is idempotent and never re-encodes
        the invocation's captured payload.
        """

        if not isinstance(schema, Mapping):
            raise TypeError("Antigravity output schema must be a mapping")
        # Copy JSON values, including immutable mappings, in their wire order.
        rendered = json.loads(canonical_provider_json(schema))
        schema_maps = {
            "properties",
            "patternProperties",
            "$defs",
            "definitions",
            "dependentSchemas",
            "dependencies",
        }
        schema_arrays = {"allOf", "anyOf", "oneOf", "prefixItems"}
        schema_values = {
            "additionalProperties",
            "additionalItems",
            "unevaluatedProperties",
            "unevaluatedItems",
            "items",
            "contains",
            "propertyNames",
            "not",
            "if",
            "then",
            "else",
            "contentSchema",
        }

        def visit(node: object) -> None:
            if type(node) is not dict:
                return
            enumeration = node.get("enum")
            if type(enumeration) is list and len(enumeration) == 1:
                value = enumeration[0]
                if node.get("type") == "null" and value is None:
                    del node["enum"]
                elif (
                    node.get("type") == "integer"
                    and type(value) is int
                    and abs(value) <= 2**53 - 1
                    and "minimum" not in node
                    and "maximum" not in node
                ):
                    entries = tuple(node.items())
                    node.clear()
                    for key, item in entries:
                        if key == "enum":
                            node["minimum"] = value
                            node["maximum"] = value
                        else:
                            node[key] = item

            for keyword, child in node.items():
                if keyword in schema_maps and type(child) is dict:
                    for nested in child.values():
                        visit(nested)
                elif (
                    keyword in schema_arrays or keyword == "items"
                ) and type(child) is list:
                    for nested in child:
                        visit(nested)
                elif keyword in schema_values:
                    visit(child)

        visit(rendered)
        return rendered

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

    @classmethod
    def _provider_result(
        cls,
        stdout: bytes,
        *,
        expected_thread_id: str | None,
        expected_schema: Mapping[str, Any],
        completion_check: Callable[[str, dict[str, Any], str], bool] | None = None,
        response_diagnostic: ProviderResponseDiagnostic | None = None,
        completion_required: bool = False,
    ) -> ProviderResult:
        """Decode one Antigravity JSON result with fixed diagnostics."""

        outer_terminal = cls._parse_result(stdout)
        terminal = cls._unwrap_result_envelope(outer_terminal)
        native_error = terminal.get("error")
        recoverable_error = (
            terminal.get("status") == "ERROR"
            and type(native_error) is str
            and antigravity_recovery_matching.MALFORMED_FUNCTION_CALL.fullmatch(
                native_error
            ) is not None
        )
        if terminal.get("status") != "SUCCESS" and not recoverable_error:
            raise ProviderInvocationError(
                "Antigravity CLI did not complete successfully",
                taint_existing_thread=True,
            )
        thread_id = terminal.get("conversation_id")
        if (
            not isinstance(thread_id, str)
            or antigravity_native_recovery.SAFE_CONVERSATION_ID.fullmatch(thread_id) is None
            or (
                expected_thread_id is not None
                and thread_id != expected_thread_id
            )
        ):
            raise ProviderInvocationError(
                "Antigravity CLI returned an invalid conversation ID",
                taint_existing_thread=True,
            )

        structured_output = terminal.get("structured_output")
        schema_echo = terminal.get("json_schema")
        if type(structured_output) is not dict or type(schema_echo) is not dict:
            cls._malformed_output()
        try:
            canonical_provider_json(structured_output)
            expected_schema_bytes = canonical_provider_json(
                cls.native_output_schema(expected_schema)
            )
            echoed_schema_bytes = canonical_provider_json(schema_echo)
            member_path = (("result",) if terminal is not outer_terminal else ()) + ("structured_output",)
            response_text = strict_json_member_text(stdout.decode("utf-8", "strict"), member_path)
        except (
            ProviderJsonEncodingError,
            UnicodeError,
            TypeError,
            ValueError,
            RecursionError,
        ):
            cls._malformed_output()
        if echoed_schema_bytes != expected_schema_bytes:
            cls._malformed_output()
        if (recoverable_error or completion_required) and (
            completion_check is None
            or not completion_check(thread_id, structured_output, native_error or "")
        ):
            raise ProviderInvocationError(
                "Antigravity CLI did not complete successfully",
                taint_existing_thread=True,
            )
        raw_usage = cls.provider_usage(terminal.get("usage"))
        return ProviderResult(
            thread_id=thread_id,
            response_text=response_text,
            usage=raw_usage,
            usage_observation=(None if raw_usage is None else ProviderUsageObservation(
                raw_usage, ProviderUsageScope.UNKNOWN, "antigravity.completed.usage")),
            response_diagnostic=(
                response_diagnostic if recoverable_error or completion_required else None
            ),
        )


    @staticmethod
    def provider_usage(value: object) -> ProviderUsage | None:
        """Return only exact counters from the documented terminal usage object."""

        if type(value) is not dict:
            return None
        assert isinstance(value, dict)
        fields = {
            "input_tokens": "input_tokens",
            "output_tokens": "output_tokens",
            "cache_read_tokens": "cached_input_tokens",
        }
        counters: dict[str, int | None] = {}
        for source, target in fields.items():
            if source not in value:
                counters[target] = None
                continue
            counter = value[source]
            if type(counter) is not int or counter < 0:
                counters[target] = None
            else:
                counters[target] = counter
        if all(counter is None for counter in counters.values()):
            return None
        return ProviderUsage(**counters)

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
