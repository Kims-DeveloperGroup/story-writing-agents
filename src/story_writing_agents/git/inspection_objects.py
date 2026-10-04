"""Bounded, inspection-local proofs of the narrow loose Git object layout."""

from __future__ import annotations

import hashlib
import os
import re
import stat
import time
import zlib
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


MAX_DESCRIPTORS = 128
MAX_NODES = 65_536
MAX_BATCH_BYTES = 8 * 1024 * 1024
MAX_METADATA_BYTES = 64 * 1024
DEADLINE_SECONDS = 1.0
_OID = re.compile(rb"(?:[0-9a-f]{40}|[0-9a-f]{64})")
_CHUNK_BYTES = 64 * 1024


class InspectionObjectFailureKind(Enum):
    ROOT_UNSAFE = "root_unsafe"
    CLEANUP_FAILED = "cleanup_failed"
    FRAMING_FAILED = "framing_failed"


class InspectionObjectProofError(Exception):
    """A fatal boundary failure, distinct from cleaned Git fallback."""

    def __init__(self, kind: InspectionObjectFailureKind) -> None:
        self.kind = kind
        super().__init__(kind.value)


class _Unsuitable(Exception):
    pass


def loose_object_format_width(config: bytes) -> int | None:
    """Admit only unambiguous local files-backend configuration."""
    values: dict[tuple[bytes, bytes], bytes] = {}
    sections: set[bytes] = set()
    section = b""
    for line in config.splitlines(keepends=True):
        if line in {b"[core]\n", b"[extensions]\n"}:
            section = line[1:-2]
            if section in sections:
                return None
            sections.add(section)
            continue
        match = re.fullmatch(rb"[ \t]+([a-z]+) = ([a-z0-9]+)\n", line)
        if match is None or not section:
            return None
        key, value = match.groups()
        if (section, key) in values:
            return None
        values[section, key] = value
    booleans = {
        b"bare", b"filemode", b"logallrefupdates", b"ignorecase",
        b"precomposeunicode", b"warnambiguousrefs",
    }
    for (section, key), value in values.items():
        if section == b"core" and key in booleans and value in {b"true", b"false"}:
            continue
        if (section, key) not in {
            (b"core", b"repositoryformatversion"), (b"extensions", b"objectformat")
        }:
            return None
    if values.get((b"core", b"bare")) != b"false":
        return None
    extensions = {key: value for (section, key), value in values.items() if section == b"extensions"}
    version = values.get((b"core", b"repositoryformatversion"))
    if version == b"0" and not extensions:
        return 40
    if version == b"1" and extensions == {b"objectformat": b"sha256"}:
        return 64
    return None


def _facts(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev, value.st_ino, value.st_mode, value.st_nlink,
        value.st_size, value.st_mtime_ns, value.st_ctime_ns,
    )


@dataclass(frozen=True, slots=True)
class _Binding:
    parent: int | None
    name: str | Path
    descriptor: int
    facts: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class _Object:
    kind: bytes
    raw: bytes
    body: memoryview


class _LooseProof:
    """Own descriptors, budgets and immutable object bytes for one attempt."""

    def __init__(
        self, main_root: Path, common_git: Path, root_identity: tuple[int, int],
        common_descriptor: int,
    ) -> None:
        self.main_root = main_root
        self.common_git = common_git
        self.root_identity = root_identity
        self.common_descriptor = common_descriptor
        self.common_identity: tuple[int, int] | None = None
        self.descriptors: list[int] = []
        self.bindings: list[_Binding] = []
        self.memberships: list[tuple[int, tuple[str, ...]]] = []
        self.absences: list[tuple[int, str]] = []
        self.objects: dict[bytes, _Object] = {}
        self.trees: dict[bytes, dict[bytes, tuple[bytes, bytes]]] = {}
        self.roots: dict[bytes, bytes] = {}
        self.fanouts: dict[bytes, int] = {}
        self.deadline = time.monotonic() + DEADLINE_SECONDS
        self.nodes = self.metadata_bytes = self.compressed_bytes = self.decoded_bytes = 0
        self.object_directory = -1
        self.width = 0

    def spend(self, nodes: int = 0, metadata: int = 0) -> None:
        self.nodes += nodes
        self.metadata_bytes += metadata
        if (self.nodes > MAX_NODES or self.metadata_bytes > MAX_BATCH_BYTES
                or time.monotonic() >= self.deadline):
            raise _Unsuitable()

    def guard_roots(self) -> None:
        try:
            common = os.fstat(self.common_descriptor)
            identity = common.st_dev, common.st_ino
            if self.common_identity is None:
                self.common_identity = identity
            root = os.stat(self.main_root, follow_symlinks=False)
            named_common = os.stat(self.common_git, follow_symlinks=False)
            if (not stat.S_ISDIR(root.st_mode) or not stat.S_ISDIR(common.st_mode)
                    or not stat.S_ISDIR(named_common.st_mode)
                    or (root.st_dev, root.st_ino) != self.root_identity
                    or identity != self.common_identity
                    or (named_common.st_dev, named_common.st_ino) != identity):
                raise OSError()
        except OSError:
            raise InspectionObjectProofError(InspectionObjectFailureKind.ROOT_UNSAFE) from None

    def open(self, parent: int | None, name: str | Path, *, directory: bool = False) -> int:
        self.spend(1, len(os.fsencode(name)) + 1)
        # Reserve one descriptor for scandir's owned duplicate.
        if len(self.descriptors) >= MAX_DESCRIPTORS - 1:
            raise _Unsuitable()
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
        descriptor = os.open(name, flags | (os.O_DIRECTORY if directory else 0), dir_fd=parent)
        self.descriptors.append(descriptor)
        os.set_inheritable(descriptor, False)
        value = os.fstat(descriptor)
        facts = _facts(value)
        if facts != _facts(os.stat(name, dir_fd=parent, follow_symlinks=False)):
            raise _Unsuitable()
        if directory:
            if not stat.S_ISDIR(value.st_mode):
                raise _Unsuitable()
        elif not stat.S_ISREG(value.st_mode) or value.st_nlink != 1:
            raise _Unsuitable()
        self.bindings.append(_Binding(parent, name, descriptor, facts))
        return descriptor

    def members(self, descriptor: int) -> tuple[str, ...]:
        self.spend()
        if os.lseek(descriptor, 0, os.SEEK_SET) != 0:
            raise _Unsuitable()
        names: list[str] = []
        iterator = os.scandir(descriptor)
        try:
            for entry in iterator:
                self.spend(1, len(entry.name.encode("ascii")) + 1)
                names.append(entry.name)
        finally:
            try:
                iterator.close()
            except BaseException:
                raise InspectionObjectProofError(InspectionObjectFailureKind.CLEANUP_FAILED) from None
        return tuple(sorted(names))

    def namespace(self, descriptor: int) -> tuple[str, ...]:
        names = self.members(descriptor)
        self.memberships.append((descriptor, names))
        return names

    def absent(self, parent: int, name: str) -> None:
        self.spend(1, len(name) + 1)
        try:
            os.stat(name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            self.absences.append((parent, name))
        else:
            raise _Unsuitable()

    def metadata(self, parent: int, name: str) -> bytes:
        descriptor = self.open(parent, name)
        size = os.fstat(descriptor).st_size
        if not 0 <= size <= MAX_METADATA_BYTES:
            raise _Unsuitable()
        content = bytearray()
        while True:
            self.spend()
            chunk = os.read(descriptor, min(_CHUNK_BYTES, size + 1 - len(content)))
            if not chunk:
                break
            self.spend(metadata=len(chunk))
            content.extend(chunk)
            if len(content) > size:
                raise _Unsuitable()
        if len(content) != size:
            raise _Unsuitable()
        return bytes(content)

    def read_object(self, oid: bytes) -> _Object:
        self.spend()
        if oid in self.objects:
            return self.objects[oid]
        prefix = oid[:2]
        if prefix not in self.fanouts:
            self.fanouts[prefix] = self.open(self.object_directory, prefix.decode("ascii"), directory=True)
        descriptor = self.open(self.fanouts[prefix], oid[2:].decode("ascii"))
        size = os.fstat(descriptor).st_size
        if not 0 < size <= MAX_BATCH_BYTES - self.compressed_bytes:
            raise _Unsuitable()
        content = bytearray()
        while True:
            self.spend()
            chunk = os.read(descriptor, min(_CHUNK_BYTES, size + 1 - len(content)))
            if not chunk:
                break
            content.extend(chunk)
            if len(content) > size:
                raise _Unsuitable()
        if len(content) != size:
            raise _Unsuitable()
        self.compressed_bytes += size
        inflater = zlib.decompressobj()
        raw = inflater.decompress(content, MAX_BATCH_BYTES - self.decoded_bytes + 1)
        self.spend()
        if (len(raw) > MAX_BATCH_BYTES - self.decoded_bytes or not inflater.eof
                or inflater.unused_data or inflater.unconsumed_tail):
            raise _Unsuitable()
        self.decoded_bytes += len(raw)
        separator = raw.find(b"\0")
        if not 0 < separator <= 127:
            raise _Unsuitable()
        fields = raw[:separator].split(b" ")
        if len(fields) != 2:
            raise _Unsuitable()
        kind, size_text = fields
        if (kind not in {b"commit", b"tree", b"blob", b"tag"} or not size_text.isdigit()
                or len(size_text) > len(str(MAX_BATCH_BYTES))
                or size_text != str(len(raw) - separator - 1).encode("ascii")):
            raise _Unsuitable()
        try:
            digest = hashlib.sha1(raw) if self.width == 40 else hashlib.sha256(raw)
        except ValueError:
            raise _Unsuitable() from None
        if digest.hexdigest().encode("ascii") != oid:
            raise _Unsuitable()
        result = _Object(kind, raw, memoryview(raw)[separator + 1:])
        self.objects[oid] = result
        return result

    def tree(self, oid: bytes) -> dict[bytes, tuple[bytes, bytes]]:
        if oid in self.trees:
            return self.trees[oid]
        value = self.read_object(oid)
        if value.kind != b"tree":
            raise _Unsuitable()
        raw = value.body.tobytes()
        children: dict[bytes, tuple[bytes, bytes]] = {}
        offset = 0
        previous: bytes | None = None
        while offset < len(raw):
            self.spend(1)
            space = raw.find(b" ", offset)
            end = raw.find(b"\0", space + 1)
            child_end = end + 1 + self.width // 2
            if space < offset or end <= space + 1 or child_end > len(raw):
                raise _Unsuitable()
            mode, name = raw[offset:space], raw[space + 1:end]
            key = name + (b"/" if mode == b"40000" else b"")
            if (mode not in {b"100644", b"40000"} or name in children or b"/" in name
                    or name in {b".", b".."} or (previous is not None and key <= previous)):
                raise _Unsuitable()
            children[name] = mode, raw[end + 1:child_end].hex().encode("ascii")
            previous, offset = key, child_end
        self.trees[oid] = children
        return children

    def resolve(self, query: bytes) -> tuple[bytes, _Object]:
        self.spend(1)
        oid, separator, path = query.partition(b":")
        if _OID.fullmatch(oid) is None:
            raise InspectionObjectProofError(InspectionObjectFailureKind.FRAMING_FAILED)
        if len(oid) != self.width:
            raise _Unsuitable()
        value = self.read_object(oid)
        if not separator:
            return oid, value
        if value.kind != b"commit":
            raise _Unsuitable()
        if oid not in self.roots:
            if len(value.body) > MAX_METADATA_BYTES:
                raise _Unsuitable()
            header, separator, _message = value.body.tobytes().partition(b"\n\n")
            lines = header.split(b"\n")
            self.spend(len(lines))
            roots = [line[5:] for line in lines if line.startswith(b"tree ")]
            if (not separator or len(roots) != 1 or not lines[0].startswith(b"tree ")
                    or _OID.fullmatch(roots[0]) is None or len(roots[0]) != self.width):
                raise _Unsuitable()
            self.roots[oid] = roots[0]
        current = self.roots[oid]
        if self.read_object(current).kind != b"tree":
            raise _Unsuitable()
        if path:
            parts = path.split(b"/")
            if any(part in {b"", b".", b".."} or re.fullmatch(rb"[A-Za-z0-9_.-]+", part) is None for part in parts):
                raise _Unsuitable()
            for index, name in enumerate(parts):
                self.spend(1)
                children = self.tree(current)
                if name not in children:
                    raise _Unsuitable()
                mode, current = children[name]
                if index < len(parts) - 1 and mode != b"40000":
                    raise _Unsuitable()
        return current, self.read_object(current)

    def recheck(self) -> None:
        for parent, name in self.absences:
            self.spend()
            try:
                os.stat(name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise _Unsuitable()
        for descriptor, names in self.memberships:
            if self.members(descriptor) != names:
                raise _Unsuitable()
        for binding in self.bindings:
            self.spend()
            if (binding.facts != _facts(os.fstat(binding.descriptor))
                    or binding.facts != _facts(os.stat(binding.name, dir_fd=binding.parent, follow_symlinks=False))
                    or os.get_inheritable(binding.descriptor)):
                raise _Unsuitable()

    def run(self, stdin: bytes) -> bytes:
        if type(stdin) is not bytes:
            raise InspectionObjectProofError(InspectionObjectFailureKind.FRAMING_FAILED)
        if len(stdin) > MAX_BATCH_BYTES:
            raise _Unsuitable()
        if not stdin or not stdin.endswith(b"\n"):
            raise InspectionObjectProofError(InspectionObjectFailureKind.FRAMING_FAILED)
        if stdin.startswith(b"\n") or b"\n\n" in stdin or b"\0" in stdin or b"\r" in stdin:
            raise InspectionObjectProofError(InspectionObjectFailureKind.FRAMING_FAILED)
        if stdin.count(b"\n") > MAX_NODES:
            raise _Unsuitable()
        queries = stdin[:-1].split(b"\n")
        if len(queries) > MAX_NODES or os.name != "posix" or not all(
            hasattr(os, name) for name in ("O_NOFOLLOW", "O_NONBLOCK", "O_DIRECTORY")
        ) or self.common_git != self.main_root / ".git":
            raise _Unsuitable()
        root = self.open(None, self.main_root, directory=True)
        common = self.open(root, ".git", directory=True)
        root_facts, common_facts = os.fstat(root), os.fstat(common)
        if (root_facts.st_dev, root_facts.st_ino) != self.root_identity or (
            common_facts.st_dev, common_facts.st_ino
        ) != self.common_identity:
            raise InspectionObjectProofError(InspectionObjectFailureKind.ROOT_UNSAFE)
        for name in ("packed-refs", "commondir", "config.worktree", "reftable", "shallow"):
            self.absent(common, name)
        self.width = loose_object_format_width(self.metadata(common, "config")) or 0
        if not self.width:
            raise _Unsuitable()
        refs = self.open(common, "refs", directory=True)
        if "replace" in self.namespace(refs):
            raise _Unsuitable()
        self.absent(refs, "replace")
        try:
            os.stat("info", dir_fd=common, follow_symlinks=False)
        except FileNotFoundError:
            self.absent(common, "info")
        else:
            common_info = self.open(common, "info", directory=True)
            self.namespace(common_info)
            self.absent(common_info, "grafts")
        self.object_directory = self.open(common, "objects", directory=True)
        names = self.namespace(self.object_directory)
        if any(name not in {"info", "pack"} and re.fullmatch(r"[0-9a-f]{2}", name) is None for name in names):
            raise _Unsuitable()
        for name in ("info", "pack"):
            directory = self.open(self.object_directory, name, directory=True)
            if self.namespace(directory):
                raise _Unsuitable()
        output = bytearray()
        for query in queries:
            oid, value = self.resolve(query)
            header = oid + b" " + value.kind + b" " + str(len(value.body)).encode("ascii") + b"\n"
            if len(output) + len(header) + len(value.body) + 1 > MAX_BATCH_BYTES:
                raise _Unsuitable()
            output.extend(header)
            output.extend(value.body)
            output.append(10)
        self.recheck()
        return bytes(output)

    def close(self) -> None:
        failed = False
        for descriptor in reversed(self.descriptors):
            try:
                os.close(descriptor)
            except BaseException:
                failed = True
        if failed:
            raise InspectionObjectProofError(InspectionObjectFailureKind.CLEANUP_FAILED)


def read_loose_inspection_batch(
    main_root: Path, common_git: Path, root_identity: tuple[int, int],
    common_descriptor: int, stdin: bytes,
) -> bytes | None:
    """Return a complete fresh proof, or None only after safe discard/cleanup."""
    proof = _LooseProof(main_root, common_git, root_identity, common_descriptor)
    output = None
    failure: BaseException | None = None
    try:
        proof.guard_roots()
        output = proof.run(stdin)
    except (_Unsuitable, OSError, UnicodeError, zlib.error):
        pass
    except (TypeError, ValueError, OverflowError):
        failure = InspectionObjectProofError(InspectionObjectFailureKind.FRAMING_FAILED)
    except BaseException as error:
        failure = error
    finally:
        try:
            proof.guard_roots()
        except BaseException as error:
            failure = error
        try:
            proof.close()
        except InspectionObjectProofError as error:
            if failure is None:
                failure = error
            else:
                failure.add_note("Loose object descriptor cleanup also failed.")
        try:
            proof.guard_roots()
        except BaseException as error:
            if failure is not None:
                error.add_note("Loose object proof or cleanup also failed.")
            failure = error
    if failure is not None:
        raise failure
    return output
