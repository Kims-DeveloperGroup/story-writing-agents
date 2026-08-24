"""Private bounded native-provider model and effort discovery."""

from __future__ import annotations

import json
import re
import subprocess
import threading
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import TracebackType


CATALOG_PROVIDERS = ("codex", "antigravity")
CATALOG_DISCOVERY_TIMEOUT_SECONDS = 5.0
CATALOG_PROCESS_STOP_TIMEOUT_SECONDS = 2.0
CATALOG_READER_JOIN_TIMEOUT_SECONDS = 2.0
CATALOG_MAX_STDOUT_BYTES = 1024 * 1024
CATALOG_MAX_MODELS = 512
CATALOG_MAX_EFFORTS_PER_MODEL = 32
CATALOG_MAX_IDENTITY_BYTES = 256
CATALOG_MAX_LABEL_BYTES = 512
CATALOG_MAX_DESCRIPTION_BYTES = 2048

_CATALOG_READ_CHUNK_BYTES = 64 * 1024
_CATALOG_WAIT_POLL_SECONDS = 0.05
_EFFORT_IDENTITY = re.compile(r"[a-z][a-z0-9_-]{0,31}")
_ANTIGRAVITY_ROW = re.compile(
    r"^(?P<slug>\S+)(?: {2,}| *\t[ \t]*)(?P<label>\S(?:.*\S)?)$"
)


class ProviderCatalogState(StrEnum):
    """Fixed content-free result categories for one discovery attempt."""

    READY = "ready"
    UNAVAILABLE = "unavailable"
    TIMED_OUT = "timed_out"
    COMMAND_FAILED = "command_failed"
    MALFORMED = "malformed"
    EMPTY = "empty"
    OVERSIZED = "oversized"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ProviderEffort:
    """One validated provider-neutral effort projection."""

    identity: str
    label: str
    description: str | None = None

    def __post_init__(self) -> None:
        _require_effort_identity(self.identity)
        _require_visible_text(
            "effort label",
            self.label,
            maximum_bytes=CATALOG_MAX_LABEL_BYTES,
        )
        _require_optional_visible_text(
            "effort description",
            self.description,
            maximum_bytes=CATALOG_MAX_DESCRIPTION_BYTES,
        )


@dataclass(frozen=True, slots=True)
class ProviderModel:
    """One validated provider-neutral model and its exact effort vocabulary."""

    identity: str
    label: str
    description: str | None
    default_effort: str | None
    efforts: tuple[ProviderEffort, ...]

    def __post_init__(self) -> None:
        _require_model_identity(self.identity)
        _require_visible_text(
            "model label",
            self.label,
            maximum_bytes=CATALOG_MAX_LABEL_BYTES,
        )
        _require_optional_visible_text(
            "model description",
            self.description,
            maximum_bytes=CATALOG_MAX_DESCRIPTION_BYTES,
        )
        if type(self.efforts) is not tuple or any(
            type(effort) is not ProviderEffort for effort in self.efforts
        ):
            raise TypeError("efforts must be a tuple of ProviderEffort values")
        if len(self.efforts) > CATALOG_MAX_EFFORTS_PER_MODEL:
            raise ValueError("model effort count exceeds the catalog capacity")
        identities = tuple(effort.identity for effort in self.efforts)
        if len(set(identities)) != len(identities):
            raise ValueError("model effort identities must be unique")
        if self.default_effort is not None:
            _require_effort_identity(self.default_effort)
            if self.default_effort not in identities:
                raise ValueError("model default effort must be supported")


@dataclass(frozen=True, slots=True)
class ProviderCatalog:
    """One ordered immutable provider catalog from a successful discovery."""

    provider: str
    models: tuple[ProviderModel, ...]

    def __post_init__(self) -> None:
        _require_provider(self.provider)
        if type(self.models) is not tuple or any(
            type(model) is not ProviderModel for model in self.models
        ):
            raise TypeError("models must be a tuple of ProviderModel values")
        if not self.models:
            raise ValueError("a ready provider catalog must contain models")
        if len(self.models) > CATALOG_MAX_MODELS:
            raise ValueError("model count exceeds the catalog capacity")
        identities = tuple(model.identity for model in self.models)
        if len(set(identities)) != len(identities):
            raise ValueError("provider model identities must be unique")

    def model_for(self, identity: str | None) -> ProviderModel | None:
        """Return an exact model match without normalizing opaque identity text."""

        if identity is None:
            return None
        for model in self.models:
            if model.identity == identity:
                return model
        return None


@dataclass(frozen=True, slots=True)
class ProviderCatalogDiscovery:
    """One provider's safe catalog or fixed content-free failure state."""

    provider: str
    state: ProviderCatalogState
    catalog: ProviderCatalog | None = None

    def __post_init__(self) -> None:
        _require_provider(self.provider)
        if not isinstance(self.state, ProviderCatalogState):
            raise TypeError("state must be a ProviderCatalogState")
        if self.state is ProviderCatalogState.READY:
            if type(self.catalog) is not ProviderCatalog:
                raise ValueError("ready discovery requires a provider catalog")
            if self.catalog.provider != self.provider:
                raise ValueError("discovery catalog provider must match")
        elif self.catalog is not None:
            raise ValueError("non-ready discovery must not carry a catalog")

    @property
    def ready(self) -> bool:
        """Return whether this attempt produced one validated catalog."""

        return self.state is ProviderCatalogState.READY


@dataclass(frozen=True, slots=True)
class _ProviderCatalogSpec:
    provider: str
    command: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _CapturedOutput:
    state: ProviderCatalogState
    stdout: bytes | None = None

    def __post_init__(self) -> None:
        if self.state is ProviderCatalogState.READY:
            if type(self.stdout) is not bytes:
                raise ValueError("ready capture requires exact bytes")
        elif self.stdout is not None:
            raise ValueError("failed capture must not retain output")


ProcessFactory = Callable[..., subprocess.Popen[bytes]]
_ProcessControl = tuple[BaseException, TracebackType | None]


class ProviderCatalogService:
    """Own synchronous bounded subprocess resources for fresh catalog discovery.

    Construction validates command and workspace facts only. Each ``discover``
    call launches exactly one provider command and retains neither its output nor
    parsed catalog after returning.
    """

    __slots__ = ("_process_factory", "_specs", "_workspace_root")

    def __init__(
        self,
        workspace_root: Path,
        *,
        codex_executable: str = "codex",
        antigravity_executable: str = "agy",
        _process_factory: ProcessFactory = subprocess.Popen,
    ) -> None:
        if not isinstance(workspace_root, Path):
            raise TypeError("workspace_root must be a Path")
        if not workspace_root.is_dir():
            raise ValueError("workspace_root must be an existing directory")
        _require_executable(codex_executable)
        _require_executable(antigravity_executable)
        if not callable(_process_factory):
            raise TypeError("_process_factory must be callable")
        self._workspace_root = workspace_root
        self._process_factory = _process_factory
        self._specs = {
            "codex": _ProviderCatalogSpec(
                provider="codex",
                command=(codex_executable, "debug", "models", "--bundled"),
            ),
            "antigravity": _ProviderCatalogSpec(
                provider="antigravity",
                command=(antigravity_executable, "models"),
            ),
        }

    def discover(self, provider: str) -> ProviderCatalogDiscovery:
        """Run one fresh bounded command and return only a sanitized projection."""

        _require_provider(provider)
        captured = self._capture(self._specs[provider])
        if captured.state is not ProviderCatalogState.READY:
            return ProviderCatalogDiscovery(provider, captured.state)
        assert captured.stdout is not None
        try:
            models = (
                _parse_codex_models(captured.stdout)
                if provider == "codex"
                else _parse_antigravity_models(captured.stdout)
            )
        except (TypeError, ValueError, UnicodeError, json.JSONDecodeError):
            return ProviderCatalogDiscovery(
                provider,
                ProviderCatalogState.MALFORMED,
            )
        except Exception:
            return ProviderCatalogDiscovery(
                provider,
                ProviderCatalogState.FAILED,
            )
        if not models:
            return ProviderCatalogDiscovery(provider, ProviderCatalogState.EMPTY)
        try:
            catalog = ProviderCatalog(provider, models)
        except (TypeError, ValueError):
            return ProviderCatalogDiscovery(
                provider,
                ProviderCatalogState.MALFORMED,
            )
        return ProviderCatalogDiscovery(
            provider,
            ProviderCatalogState.READY,
            catalog,
        )

    def _capture(self, spec: _ProviderCatalogSpec) -> _CapturedOutput:
        deadline = time.monotonic() + CATALOG_DISCOVERY_TIMEOUT_SECONDS
        try:
            process = self._process_factory(
                spec.command,
                cwd=str(self._workspace_root),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                shell=False,
            )
        except FileNotFoundError:
            return _CapturedOutput(ProviderCatalogState.UNAVAILABLE)
        except Exception:
            return _CapturedOutput(ProviderCatalogState.FAILED)

        stdout = process.stdout
        if stdout is None:
            _reaped, control = _stop_process(process)
            if control is not None:
                _raise_process_control(control)
            return _CapturedOutput(ProviderCatalogState.FAILED)

        retained = bytearray()
        oversized = threading.Event()
        reader_failed = threading.Event()
        reader_control: list[_ProcessControl] = []

        def read_stdout() -> None:
            try:
                while True:
                    chunk = stdout.read(_CATALOG_READ_CHUNK_BYTES)
                    if not chunk:
                        return
                    if type(chunk) is not bytes:
                        reader_failed.set()
                        return
                    remaining = CATALOG_MAX_STDOUT_BYTES - len(retained)
                    if remaining > 0:
                        retained.extend(chunk[:remaining])
                    if len(chunk) > remaining:
                        oversized.set()
            except BaseException as failure:
                if not isinstance(failure, Exception):
                    reader_control.append((failure, failure.__traceback__))
                reader_failed.set()

        try:
            reader = threading.Thread(
                target=read_stdout,
                name=f"{spec.provider}-catalog-reader",
                daemon=True,
            )
            reader.start()
        except Exception:
            _reaped, process_control = _stop_process(process)
            _closed, close_control = _close_stdout(stdout)
            control = _first_process_control(process_control, close_control)
            if control is not None:
                _raise_process_control(control)
            return _CapturedOutput(ProviderCatalogState.FAILED)
        except BaseException as failure:
            retained = (failure, failure.__traceback__)
            _stop_process(process)
            _close_stdout(stdout)
            _raise_process_control(retained)
        outcome: ProviderCatalogState | None = None
        returncode: int | None = None

        try:
            while outcome is None and returncode is None:
                if oversized.is_set():
                    outcome = ProviderCatalogState.OVERSIZED
                    break
                if reader_failed.is_set():
                    outcome = ProviderCatalogState.FAILED
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    outcome = ProviderCatalogState.TIMED_OUT
                    break
                try:
                    returncode = process.wait(
                        timeout=min(_CATALOG_WAIT_POLL_SECONDS, remaining)
                    )
                except subprocess.TimeoutExpired:
                    continue
                except Exception:
                    outcome = ProviderCatalogState.FAILED
                    break
        except BaseException as failure:
            retained = (failure, failure.__traceback__)
            _stop_process(process)
            _finish_reader(stdout, reader)
            _raise_process_control(retained)

        reaped = True
        cleanup_control: _ProcessControl | None = None
        if returncode is None:
            reaped, cleanup_control = _stop_process(process)
        reader_finished, reader_cleanup_control = _finish_reader(stdout, reader)
        cleanup_control = _first_process_control(
            cleanup_control,
            reader_cleanup_control,
        )
        if reader_control:
            cleanup_control = _first_process_control(
                reader_control[0],
                cleanup_control,
            )
        if cleanup_control is not None:
            _raise_process_control(cleanup_control)
        if not reaped or not reader_finished:
            return _CapturedOutput(ProviderCatalogState.FAILED)
        if oversized.is_set():
            return _CapturedOutput(ProviderCatalogState.OVERSIZED)
        if reader_failed.is_set():
            return _CapturedOutput(ProviderCatalogState.FAILED)
        if outcome is not None:
            return _CapturedOutput(outcome)
        if returncode != 0:
            return _CapturedOutput(ProviderCatalogState.COMMAND_FAILED)
        return _CapturedOutput(ProviderCatalogState.READY, bytes(retained))


def _parse_codex_models(value: bytes) -> tuple[ProviderModel, ...]:
    decoded = value.decode("utf-8", errors="strict")
    parsed = json.loads(
        decoded,
        object_pairs_hook=_reject_duplicate_object_keys,
        parse_constant=_reject_json_constant,
    )
    if type(parsed) is not dict:
        raise ValueError("Codex catalog must be an object")
    raw_models = parsed.get("models")
    if type(raw_models) is not list:
        raise ValueError("Codex catalog models must be a list")
    if len(raw_models) > CATALOG_MAX_MODELS:
        raise ValueError("Codex catalog exceeds the model capacity")

    projected: list[ProviderModel] = []
    seen_models: set[str] = set()
    for raw_model in raw_models:
        if type(raw_model) is not dict:
            raise ValueError("Codex catalog model must be an object")
        if raw_model.get("visibility") != "list":
            continue

        identity = raw_model.get("slug")
        label = raw_model.get("display_name")
        _require_model_identity(identity)
        _require_visible_text(
            "model label",
            label,
            maximum_bytes=CATALOG_MAX_LABEL_BYTES,
        )
        description = raw_model.get("description")
        _require_optional_visible_text(
            "model description",
            description,
            maximum_bytes=CATALOG_MAX_DESCRIPTION_BYTES,
        )
        if identity in seen_models:
            raise ValueError("Codex model slugs must be unique")
        seen_models.add(identity)

        raw_efforts = raw_model.get("supported_reasoning_levels")
        if type(raw_efforts) is not list:
            raise ValueError("Codex model efforts must be a list")
        if len(raw_efforts) > CATALOG_MAX_EFFORTS_PER_MODEL:
            raise ValueError("Codex model exceeds the effort capacity")
        efforts: list[ProviderEffort] = []
        seen_efforts: set[str] = set()
        for raw_effort in raw_efforts:
            if type(raw_effort) is not dict:
                raise ValueError("Codex effort must be an object")
            effort_identity = raw_effort.get("effort")
            _require_effort_identity(effort_identity)
            if effort_identity in seen_efforts:
                raise ValueError("Codex effort identities must be unique")
            seen_efforts.add(effort_identity)
            effort_description = raw_effort.get("description")
            _require_optional_visible_text(
                "effort description",
                effort_description,
                maximum_bytes=CATALOG_MAX_DESCRIPTION_BYTES,
            )
            efforts.append(
                ProviderEffort(
                    effort_identity,
                    effort_identity,
                    effort_description,
                )
            )

        default_effort = raw_model.get("default_reasoning_level")
        if default_effort is not None:
            _require_effort_identity(default_effort)
            if default_effort not in seen_efforts:
                raise ValueError("Codex default effort must be supported")
        projected.append(
            ProviderModel(
                identity,
                label,
                description,
                default_effort,
                tuple(efforts),
            )
        )
    return tuple(projected)


def _parse_antigravity_models(value: bytes) -> tuple[ProviderModel, ...]:
    decoded = value.decode("utf-8", errors="strict")
    efforts = tuple(
        ProviderEffort(identity, identity)
        for identity in ("low", "medium", "high")
    )
    projected: list[ProviderModel] = []
    seen_models: set[str] = set()
    for raw_line in decoded.splitlines():
        line = raw_line.strip(" \t")
        if not line:
            continue
        match = _ANTIGRAVITY_ROW.fullmatch(line)
        if match is None:
            raise ValueError("Antigravity catalog row is not aligned")
        identity = match.group("slug")
        label = match.group("label")
        _require_model_identity(identity)
        _require_visible_text(
            "model label",
            label,
            maximum_bytes=CATALOG_MAX_LABEL_BYTES,
        )
        if identity in seen_models:
            raise ValueError("Antigravity model slugs must be unique")
        seen_models.add(identity)
        if len(projected) >= CATALOG_MAX_MODELS:
            raise ValueError("Antigravity catalog exceeds the model capacity")
        projected.append(
            ProviderModel(identity, label, None, None, efforts)
        )
    return tuple(projected)


def _reject_duplicate_object_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    projected: dict[str, object] = {}
    for key, value in pairs:
        if key in projected:
            raise ValueError("provider catalog contains duplicate JSON keys")
        projected[key] = value
    return projected


def _reject_json_constant(value: str) -> object:
    raise ValueError("provider catalog contains a non-JSON numeric constant")


def _require_provider(provider: object) -> str:
    if type(provider) is not str or provider not in CATALOG_PROVIDERS:
        raise ValueError("provider must be codex or antigravity")
    return provider


def _require_executable(executable: object) -> str:
    if type(executable) is not str or not executable or "\x00" in executable:
        raise ValueError("provider executable must be a non-empty command path")
    return executable


def _require_model_identity(identity: object) -> str:
    _require_visible_text(
        "model identity",
        identity,
        maximum_bytes=CATALOG_MAX_IDENTITY_BYTES,
    )
    assert isinstance(identity, str)
    if identity.startswith("-") or any(
        character.isspace() for character in identity
    ):
        raise ValueError("model identity must be one safe opaque token")
    return identity


def _require_effort_identity(identity: object) -> str:
    if type(identity) is not str or _EFFORT_IDENTITY.fullmatch(identity) is None:
        raise ValueError("effort identity must be one safe lowercase token")
    return identity


def _require_optional_visible_text(
    name: str,
    value: object,
    *,
    maximum_bytes: int,
) -> str | None:
    if value is None:
        return None
    return _require_visible_text(name, value, maximum_bytes=maximum_bytes)


def _require_visible_text(
    name: str,
    value: object,
    *,
    maximum_bytes: int,
) -> str:
    if (
        type(value) is not str
        or not value
        or value.isspace()
        or value != value.strip()
    ):
        raise ValueError(f"{name} must be nonblank text")
    if len(value.encode("utf-8")) > maximum_bytes:
        raise ValueError(f"{name} exceeds the catalog capacity")
    if any(
        unicodedata.category(character) in {"Cc", "Cf", "Cs"}
        for character in value
    ):
        raise ValueError(f"{name} contains unsafe text")
    return value


def _finish_reader(
    stdout: object,
    reader: threading.Thread,
) -> tuple[bool, _ProcessControl | None]:
    """Boundedly join one reader, close its pipe, and confirm termination."""

    complete = True
    control: _ProcessControl | None = None
    try:
        reader.join(timeout=CATALOG_READER_JOIN_TIMEOUT_SECONDS)
    except BaseException as failure:
        complete = False
        control = _remember_process_control(control, failure)
    closed, close_control = _close_stdout(stdout)
    complete = complete and closed
    control = _first_process_control(control, close_control)
    try:
        reader.join(timeout=CATALOG_READER_JOIN_TIMEOUT_SECONDS)
    except BaseException as failure:
        complete = False
        control = _remember_process_control(control, failure)
    try:
        complete = complete and not reader.is_alive()
    except BaseException as failure:
        complete = False
        control = _remember_process_control(control, failure)
    return complete, control


def _close_stdout(stdout: object) -> tuple[bool, _ProcessControl | None]:
    """Close one captured stream while retaining Python process control."""

    try:
        close = getattr(stdout, "close")
        close()
    except BaseException as failure:
        return False, _remember_process_control(None, failure)
    return True, None


def _stop_process(
    process: subprocess.Popen[bytes],
) -> tuple[bool, _ProcessControl | None]:
    """Boundedly terminate, kill, and reap one exact discovery child."""

    control: _ProcessControl | None = None
    reaped = False
    try:
        if process.poll() is not None:
            reaped = True
    except BaseException as failure:
        control = _remember_process_control(control, failure)

    if not reaped:
        try:
            process.terminate()
        except BaseException as failure:
            control = _remember_process_control(control, failure)
        try:
            process.wait(timeout=CATALOG_PROCESS_STOP_TIMEOUT_SECONDS)
            reaped = True
        except subprocess.TimeoutExpired:
            pass
        except BaseException as failure:
            control = _remember_process_control(control, failure)

    if not reaped:
        try:
            if process.poll() is not None:
                reaped = True
        except BaseException as failure:
            control = _remember_process_control(control, failure)
    if not reaped:
        try:
            process.kill()
        except BaseException as failure:
            control = _remember_process_control(control, failure)
        try:
            process.wait(timeout=CATALOG_PROCESS_STOP_TIMEOUT_SECONDS)
            reaped = True
        except BaseException as failure:
            control = _remember_process_control(control, failure)
    return reaped, control


def _remember_process_control(
    retained: _ProcessControl | None,
    failure: BaseException,
) -> _ProcessControl | None:
    if isinstance(failure, Exception) or retained is not None:
        return retained
    return failure, failure.__traceback__


def _first_process_control(
    first: _ProcessControl | None,
    second: _ProcessControl | None,
) -> _ProcessControl | None:
    return first if first is not None else second


def _raise_process_control(control: _ProcessControl) -> None:
    failure, traceback = control
    raise failure.with_traceback(traceback)
