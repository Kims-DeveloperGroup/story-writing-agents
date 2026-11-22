"""Codex-specific binding, command, event, and structured-output adapter."""

from __future__ import annotations
from .provider_adapter import (ProviderContinuationRejection,
    NativeContextCheck, NativeContextObservation, NativeContextCheckStage,
    NativeContextSourceStage, NativeContextCheckReason)
from .agent_context.accounting import ProviderUsageObservation, ProviderUsageScope

import asyncio
from dataclasses import replace
import time
import hashlib
import json
import math
import os
import re
import shutil
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from .agent_context import (
    ContextComponentBytes,
    ProviderCallState,
    ProviderUsage,
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
from .codex_event_decoding import (
    reject_duplicate_fields as _reject_duplicate_fields,
    reject_constant as _reject_constant,
    finite_float as _finite_float,
    codex_usage_observation as _codex_usage_observation,
)


_SAFE_THREAD_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")


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
                authentication = (
                    None if result.returncode == 0 and codex_invocation_files.regular_output_exists(output_path)
                    else self._authentication_failure(result.stdout)
                )
                if authentication is not None:
                    raise ProviderInvocationError(
                        str(authentication),
                        taint_existing_thread=True,
                        authentication_failure=authentication,
                    )
                if result.returncode != 0:
                    raise ProviderInvocationError(
                        f"Codex CLI exited with status {result.returncode}",
                        taint_existing_thread=True,
                    )

                events = self.parse_events(result.stdout)
                thread_id = invocation.thread_id
                reported_thread_ids = tuple(
                    event.get("thread_id") for event in events
                    if event.get("type") == "thread.started"
                )
                if thread_id is not None and any(
                    reported != thread_id for reported in reported_thread_ids
                ):
                    reported = next(value for value in reported_thread_ids if value != thread_id)
                    observed_thread = (reported if type(reported) is str
                                       and _SAFE_THREAD_ID.fullmatch(reported) is not None else None)
                    check = (NativeContextCheck(invocation.context_call_id,
                        None if invocation.context_witness is None else invocation.context_witness.call_id,
                        (() if native_precheck is None else native_precheck.observations) + (
                            NativeContextObservation(NativeContextCheckStage.POST_COMPLETE,
                                NativeContextSourceStage.RECORDS, NativeContextCheckReason.THREAD_MISMATCH),),
                        observed_thread_id=observed_thread, normal_exit_code=result.returncode)
                        if invocation.context_call_id is not None else None)
                    raise ProviderInvocationError(
                        "Codex CLI returned a different continuation identity",
                        taint_existing_thread=True,
                        native_context_check=check,
                    )
                if thread_id is None:
                    thread_id = self.started_thread(events)
                    if thread_id is None:
                        raise ProviderInvocationError(
                            "Codex CLI did not report a new thread ID",
                            taint_existing_thread=False,
                        )
                try:
                    execution_workspace.require_current()
                    response_text = codex_invocation_files.read_regular_output(output_path)
                except OSError as exc:
                    raise ProviderInvocationError(
                        "Codex CLI did not write its final response",
                        taint_existing_thread=True,
                    ) from exc
                except UnicodeError as exc:
                    raise ProviderInvocationError(
                        "Codex CLI returned malformed structured output",
                        taint_existing_thread=True,
                    ) from exc
                native_observation = (await codex_native_context.observe_completed_native_context(native_state, invocation, thread_id,
                    deadline=native_deadline, precheck=native_precheck,
                    initial_path=native_pre_source_path, discovery_used=native_pre_rediscovered)
                    if invocation.context_call_id is not None else None)
                raw_usage = self.provider_usage(events)
                provider_result = ProviderResult(
                    thread_id=thread_id,
                    response_text=response_text,
                    usage=raw_usage,
                    last_token_usage=(
                        None if native_observation is None or native_observation.witness is None
                        else native_observation.last_token_usage
                    ),
                    usage_observation=_codex_usage_observation(raw_usage, native_observation, invocation),
                    actual_transport_utf8_bytes=len(stdin),
                    context_witness=None if native_observation is None else native_observation.witness,
                    native_context_check=None if native_observation is None else replace(native_observation.check,
                        observed_thread_id=reported_thread_ids[0] if reported_thread_ids else None,
                        normal_exit_code=result.returncode),
                    native_context_cause=None if native_observation is None else native_observation.cause)
                execution_workspace.require_current()
                return provider_result
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

        try:
            events = cls.parse_events(stdout, strict=True)
        except ProviderInvocationError:
            return None
        if any(event.get("type") == "turn.completed" for event in events):
            return None
        for event in events:
            if event.get("type") not in {"error", "turn.failed"}:
                continue
            error = event.get("error", event)
            if type(error) is not dict:
                continue
            native_401 = any(
                type(error.get(key)) is int and error[key] == 401
                for key in ("status", "status_code", "code")
            )
            message = error.get("message")
            native_401 = native_401 or (
                type(message) is str
                and re.search(r"\b(?:HTTP(?: status)?|status(?: code)?)\s*:?\s*401\b|\b401 Unauthorized\b", message, re.IGNORECASE)
                is not None
            )
            if native_401:
                return ProviderAuthenticationRequiredError("codex")
        return None

    @staticmethod
    def parse_events(
        stdout: bytes, *, strict: bool = False,
    ) -> tuple[dict[str, Any], ...]:
        """Decode the Codex JSONL event envelope with fixed diagnostics."""

        try:
            text = stdout.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ProviderInvocationError(
                "Codex CLI event stream is not valid UTF-8",
                taint_existing_thread=True,
            ) from exc
        events: list[dict[str, Any]] = []
        for line_number, line in enumerate(text.splitlines(), 1):
            if not line:
                continue
            try:
                value = (
                    json.loads(
                        line,
                        object_pairs_hook=_reject_duplicate_fields,
                        parse_constant=_reject_constant,
                        parse_float=_finite_float,
                    )
                    if strict
                    else json.loads(line)
                )
            except (ValueError, RecursionError) as exc:
                raise ProviderInvocationError(
                    f"Codex CLI emitted malformed JSONL at line {line_number}",
                    taint_existing_thread=True,
                ) from exc
            if not isinstance(value, dict):
                raise ProviderInvocationError(
                    f"Codex CLI emitted a non-object event at line {line_number}",
                    taint_existing_thread=True,
                )
            events.append(value)
        return tuple(events)

    @staticmethod
    def started_thread(events: tuple[dict[str, Any], ...]) -> str | None:
        """Return the first syntactically safe Codex thread identifier."""

        for event in events:
            if event.get("type") == "thread.started":
                thread_id = event.get("thread_id")
                if (
                    isinstance(thread_id, str)
                    and _SAFE_THREAD_ID.fullmatch(thread_id) is not None
                ):
                    return thread_id
        return None

    @staticmethod
    def provider_usage(
        events: tuple[dict[str, Any], ...],
    ) -> ProviderUsage | None:
        """Return only exact counters from one recognized terminal event."""

        terminals = tuple(
            event for event in events if event.get("type") == "turn.completed"
        )
        if len(terminals) != 1:
            return None
        raw_usage = terminals[0].get("usage")
        if type(raw_usage) is not dict:
            return None
        assert isinstance(raw_usage, dict)
        values: dict[str, int | None] = {}
        for field in (
            "input_tokens",
            "output_tokens",
        ):
            if field not in raw_usage:
                values[field] = None
                continue
            value = raw_usage[field]
            if type(value) is not int or value < 0:
                values[field] = None
            else:
                values[field] = value
        if all(value is None for value in values.values()):
            return None
        return ProviderUsage(**values)

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
