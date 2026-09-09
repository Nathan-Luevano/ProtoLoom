from __future__ import annotations

from collections.abc import Collection, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from zipfile import BadZipFile, ZipFile, ZipInfo


class ArchiveError(ValueError):
    pass


MAX_ARCHIVE_MEMBER_SIZE = 256 * 1024 * 1024
MAX_ARCHIVE_SCAN_SIZE = 512 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 100_000
MAX_ARCHIVE_NAME_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ArchiveEntry:
    name: str
    size: int
    compressed_size: int
    kind: str


@dataclass(frozen=True, slots=True)
class ArchiveInventory:
    entries: tuple[ArchiveEntry, ...]

    @property
    def dex_files(self) -> tuple[ArchiveEntry, ...]:
        return tuple(entry for entry in self.entries if entry.kind == "dex")

    @property
    def native_libraries(self) -> tuple[ArchiveEntry, ...]:
        return tuple(entry for entry in self.entries if entry.kind == "native")

    def select(
        self,
        kinds: Collection[str],
        *,
        max_total_size: int = MAX_ARCHIVE_SCAN_SIZE,
    ) -> tuple[ArchiveEntry, ...]:
        if max_total_size <= 0:
            raise ValueError("archive selection limit must be positive")
        entries = tuple(entry for entry in self.entries if entry.kind in kinds)
        total_size = sum(entry.size for entry in entries)
        if total_size > max_total_size:
            raise ArchiveError(
                f"archive scan exceeds {max_total_size} uncompressed bytes"
            )
        return entries


class AndroidArchive:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def inventory(
        self,
        *,
        max_entries: int = MAX_ARCHIVE_ENTRIES,
        max_name_bytes: int = MAX_ARCHIVE_NAME_BYTES,
    ) -> ArchiveInventory:
        if max_entries <= 0 or max_name_bytes <= 0:
            raise ValueError("archive inventory limits must be positive")
        try:
            with ZipFile(self.path) as archive:
                infos = archive.infolist()
                if len(infos) > max_entries:
                    raise ArchiveError(
                        f"archive contains more than {max_entries} entries"
                    )
                entries: list[ArchiveEntry] = []
                names: set[str] = set()
                name_bytes = 0
                for info in infos:
                    name_bytes += len(info.filename.encode("utf-8"))
                    if name_bytes > max_name_bytes:
                        raise ArchiveError(
                            f"archive names exceed {max_name_bytes} bytes"
                        )
                    if info.is_dir():
                        continue
                    _validate_name(info.filename)
                    if info.filename in names:
                        raise ArchiveError(f"duplicate archive member: {info.filename}")
                    names.add(info.filename)
                    entries.append(_entry(info))
        except (BadZipFile, OSError) as error:
            raise ArchiveError(f"invalid archive: {self.path}") from error
        return ArchiveInventory(tuple(entries))

    def read(self, name: str, *, max_size: int = MAX_ARCHIVE_MEMBER_SIZE) -> bytes:
        _validate_member_limit(max_size)
        try:
            with ZipFile(self.path) as archive:
                return _read_member(archive, name, max_size)
        except (BadZipFile, NotImplementedError, OSError, RuntimeError) as error:
            raise ArchiveError(f"cannot read archive member: {name}") from error

    def iter_read(
        self,
        entries: Collection[ArchiveEntry],
        *,
        cached: Mapping[str, bytes] | None = None,
        max_size: int = MAX_ARCHIVE_MEMBER_SIZE,
    ) -> Iterator[tuple[ArchiveEntry, bytes]]:
        _validate_member_limit(max_size)
        values = cached or {}
        if all(entry.name in values for entry in entries):
            for entry in entries:
                yield entry, _cached_member(values, entry.name, max_size)
            return
        try:
            with ZipFile(self.path) as archive:
                for entry in entries:
                    data = (
                        _cached_member(values, entry.name, max_size)
                        if entry.name in values
                        else None
                    )
                    yield (
                        entry,
                        data
                        if data is not None
                        else _read_member(archive, entry.name, max_size),
                    )
        except (BadZipFile, NotImplementedError, OSError, RuntimeError) as error:
            raise ArchiveError(f"cannot read archive: {self.path}") from error

    def iter_dex(self) -> Iterator[tuple[ArchiveEntry, bytes]]:
        yield from self.iter_read(self.inventory().select({"dex"}))


def inventory(path: str | Path) -> ArchiveInventory:
    return AndroidArchive(path).inventory()


def _cached_member(values: Mapping[str, bytes], name: str, max_size: int) -> bytes:
    _validate_name(name)
    data = values[name]
    if len(data) > max_size:
        raise ArchiveError(f"archive member exceeds {max_size} bytes: {name}")
    return data


def _validate_member_limit(max_size: int) -> None:
    if max_size <= 0:
        raise ValueError("archive member limit must be positive")


def _read_member(archive: ZipFile, name: str, max_size: int) -> bytes:
    _validate_name(name)
    try:
        info = archive.getinfo(name)
    except KeyError as error:
        raise ArchiveError(f"archive member not found: {name}") from error
    if info.file_size > max_size:
        raise ArchiveError(f"archive member exceeds {max_size} bytes: {name}")
    with archive.open(info) as stream:
        data = stream.read(max_size + 1)
    if len(data) > max_size:
        raise ArchiveError(f"archive member exceeds {max_size} bytes: {name}")
    return data


def _entry(info: ZipInfo) -> ArchiveEntry:
    name = info.filename
    path = PurePosixPath(name)
    if _is_dex_name(path.name):
        kind = "dex"
    elif path.suffix == ".so" and ("lib" in path.parts or "jni" in path.parts):
        kind = "native"
    elif "assets" in path.parts:
        kind = "asset"
    elif path.suffix == ".class":
        kind = "class"
    else:
        kind = "resource"
    return ArchiveEntry(name, info.file_size, info.compress_size, kind)


def _is_dex_name(name: str) -> bool:
    if name == "classes.dex":
        return True
    if not name.startswith("classes") or not name.endswith(".dex"):
        return False
    suffix = name[7:-4]
    return (
        bool(suffix)
        and suffix[0] in "23456789"
        and suffix.isascii()
        and suffix.isdigit()
    )


def _validate_name(name: str) -> None:
    path = PurePosixPath(name)
    if not path.name or path.is_absolute() or ".." in path.parts or "\\" in name:
        raise ArchiveError(f"unsafe archive member: {name}")
