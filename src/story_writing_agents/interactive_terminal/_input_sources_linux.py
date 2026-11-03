"""Optional Linux desktop source adapters using installed input-method tools."""

from __future__ import annotations

import configparser
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ._input_sources import InputSourceAdapter, InputSourceState


_HELPER_TIMEOUT_SECONDS = 0.25
_MAX_HELPER_OUTPUT = 65536
_ENGINE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_:().+@-]{0,255}\Z")


def _run_helper(executable: str, *arguments: str) -> str:
    result = subprocess.run(
        [executable, *arguments], stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, encoding="utf-8", errors="strict", check=True,
        timeout=_HELPER_TIMEOUT_SECONDS,
    )
    if len(result.stdout) > _MAX_HELPER_OUTPUT:
        raise OSError("input-method helper returned too much output")
    return result.stdout.strip()


def _english_ibus_engine(name: str) -> bool:
    return name.startswith("xkb:") and name.rsplit(":", 1)[-1] == "eng"


class IBusInputSourceAdapter:
    def __init__(self, executable: str) -> None:
        self._executable = executable

    def current_source(self) -> InputSourceState | None:
        name = _run_helper(self._executable, "engine")
        if _ENGINE_NAME.fullmatch(name) is None:
            return None
        return InputSourceState(("ibus", name), _english_ibus_engine(name))

    def default_source(self) -> InputSourceState | None:
        # Engine ordering/preload lists are not an independent saved default.
        return None

    def _engines(self) -> tuple[str, ...]:
        output = _run_helper(self._executable, "list-engine", "--name-only")
        return tuple(
            name for name in output.splitlines()
            if _ENGINE_NAME.fullmatch(name) is not None
        )

    def english_source(self) -> InputSourceState | None:
        current = self.current_source()
        if current is not None and current.english:
            return current
        engines = self._engines()
        if "xkb:us::eng" in engines:
            return InputSourceState(("ibus", "xkb:us::eng"), True)
        for name in sorted(engines):
            if _english_ibus_engine(name):
                return InputSourceState(("ibus", name), True)
        return None

    def select_source(self, source: InputSourceState) -> None:
        if (
            len(source.identity) != 2 or source.identity[0] != "ibus"
            or source.identity[1] not in self._engines()
        ):
            raise OSError("saved IBus input source is unavailable")
        _run_helper(self._executable, "engine", source.identity[1])


def _fcitx_english_keyboard(name: str) -> bool:
    # Only classify known English keyboard families, not arbitrary ASCII IMEs.
    return name in {"keyboard-us", "keyboard-gb"} or name.startswith(
        ("keyboard-us-", "keyboard-gb-")
    )


@dataclass(frozen=True, slots=True)
class _FcitxGroup:
    members: tuple[str, ...]
    english_keyboards: frozenset[str]


def _fcitx_groups() -> dict[str, _FcitxGroup]:
    """Read configured group membership without changing its saved defaults."""

    config_root = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    if not config_root.is_absolute():
        return {}
    profile = config_root / "fcitx5" / "profile"
    try:
        with profile.open("r", encoding="utf-8") as stream:
            text = stream.read(_MAX_HELPER_OUTPUT + 1)
        if len(text) > _MAX_HELPER_OUTPUT:
            return {}
        parser = configparser.RawConfigParser(interpolation=None)
        parser.optionxform = str
        parser.read_string(text)
        groups = {}
        for section in parser.sections():
            if re.fullmatch(r"Groups/[0-9]+", section) is None:
                continue
            name = parser.get(section, "Name", fallback="")
            if not name or any(ord(character) < 32 for character in name):
                continue
            members = []
            english_keyboards = set()
            default_layout = parser.get(section, "Default Layout", fallback="")
            index = 0
            while parser.has_section(f"{section}/Items/{index}"):
                member = parser.get(f"{section}/Items/{index}", "Name", fallback="")
                if _ENGINE_NAME.fullmatch(member) is None:
                    members = []
                    break
                members.append(member)
                layout = parser.get(
                    f"{section}/Items/{index}", "Layout", fallback="",
                ) or default_layout
                if _fcitx_english_keyboard(member) and re.fullmatch(
                    r"(?:us|gb)(?:\([A-Za-z0-9_+-]+\))?", layout,
                ):
                    english_keyboards.add(member)
                index += 1
            if name in groups:
                return {}
            if members:
                groups[name] = _FcitxGroup(tuple(members), frozenset(english_keyboards))
        return groups
    except (OSError, UnicodeError, configparser.Error):
        return {}


class Fcitx5InputSourceAdapter:
    def __init__(self, executable: str) -> None:
        self._executable = executable
        self._groups = _fcitx_groups()

    def _run(self, *arguments: str) -> str:
        # Avoid D-Bus activation of a different/unstarted desktop framework.
        return _run_helper(self._executable, "--check", *arguments)

    def current_source(self) -> InputSourceState | None:
        active = self._run()
        if active not in {"1", "2"}:
            return None
        group = self._run("-q")
        name = self._run("-n")
        configured = self._groups.get(group)
        if (
            not group or any(ord(character) < 32 for character in group)
            or _ENGINE_NAME.fullmatch(name) is None
            or configured is None or name not in configured.members
            or (active == "1" and name != configured.members[0])
        ):
            return None
        return InputSourceState(
            ("fcitx5", group, name, active), name in configured.english_keyboards,
        )

    def default_source(self) -> InputSourceState | None:
        # DefaultIM is also modified by ordinary engine selection; not a fixed
        # independent OS default. The owner uses the approved startup snapshot.
        return None

    def english_source(self) -> InputSourceState | None:
        current = self.current_source()
        if current is None:
            return None
        if current.english:
            return current
        group = current.identity[1]
        configured = self._groups[group]
        members = configured.members
        if members[0] not in configured.english_keyboards:
            return None
        # Fcitx's first group member is the inactive keyboard. Deactivation
        # keeps its remembered non-keyboard engine and group default intact.
        return InputSourceState(("fcitx5", group, members[0], "1"), True)

    def select_source(self, source: InputSourceState) -> None:
        identity = source.identity
        if len(identity) != 4 or identity[0] != "fcitx5":
            raise ValueError("invalid Fcitx5 input-source identity")
        _kind, group, name, active = identity
        configured = self._groups.get(group)
        members = configured.members if configured is not None else ()
        if (
            active not in {"1", "2"} or name not in members
            or (active == "1" and name != members[0])
        ):
            raise OSError("saved Fcitx5 input source is unavailable")
        current = self.current_source()
        if current is None:
            raise OSError("Fcitx5 has no active desktop input context")
        if current.identity[1] != group:
            self._run("-g", group)
            current = self.current_source()
            if current is None or current.identity[1] != group:
                raise OSError("Fcitx5 group selection was not accepted")
        if active == "1":
            self._run("-c")
        else:
            if current.identity[2] != name:
                self._run("-s", name)
                current = self.current_source()
            if current is None or current.identity[3] != "2":
                self._run("-o")


def linux_input_source_adapter() -> InputSourceAdapter | None:
    """Choose an explicitly configured or uniquely installed framework."""

    configured = set()
    for key in ("GTK_IM_MODULE", "QT_IM_MODULE", "XMODIFIERS"):
        value = os.environ.get(key, "").lower()
        if value in {"ibus", "@im=ibus"}:
            configured.add("ibus")
        if value in {"fcitx", "fcitx5", "@im=fcitx", "@im=fcitx5"}:
            configured.add("fcitx5")
    if len(configured) > 1:
        return None
    executables = {
        "ibus": shutil.which("ibus"),
        "fcitx5": shutil.which("fcitx5-remote"),
    }
    candidates = configured or {
        name for name, executable in executables.items() if executable is not None
    }
    if len(candidates) != 1:
        return None
    framework = next(iter(candidates))
    executable = executables[framework]
    if executable is None:
        return None
    if framework == "ibus":
        return IBusInputSourceAdapter(executable)
    return Fcitx5InputSourceAdapter(executable)
