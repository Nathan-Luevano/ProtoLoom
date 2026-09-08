from __future__ import annotations

from collections.abc import Collection, Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from zipfile import BadZipFile, ZipFile, ZipInfo


class ArchiveError(ValueError):
    pass


MAX_ARCHIVE_MEMBER_SIZE = 256 * 1024 * 1024
MAX_ARCHIVE_SCAN_SIZE = 512 * 1024 * 1024


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

    def inventory(self) -> ArchiveInventory:
        try:
            with ZipFile(self.path) as archive:
                entries: list[ArchiveEntry] = []
                names: set[str] = set()
                for info in archive.infolist():
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
        _validate_name(name)
        try:
            with ZipFile(self.path) as archive:
                info = archive.getinfo(name)
                if info.file_size > max_size:
                    raise ArchiveError(
                        f"archive member exceeds {max_size} bytes: {name}"
                    )
                with archive.open(info) as stream:
                    data = stream.read(max_size + 1)
        except KeyError as error:
            raise ArchiveError(f"archive member not found: {name}") from error
        except (BadZipFile, OSError) as error:
            raise ArchiveError(f"cannot read archive member: {name}") from error
        if len(data) > max_size:
            raise ArchiveError(f"archive member exceeds {max_size} bytes: {name}")
        return data

    def iter_dex(self) -> Iterator[tuple[ArchiveEntry, bytes]]:
        for entry in self.inventory().select({"dex"}):
            yield entry, self.read(entry.name)


def inventory(path: str | Path) -> ArchiveInventory:
    return AndroidArchive(path).inventory()


def _entry(info: ZipInfo) -> ArchiveEntry:
    name = info.filename
    path = PurePosixPath(name)
    if path.name.startswith("classes") and path.suffix == ".dex":
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


def _validate_name(name: str) -> None:
    path = PurePosixPath(name)
    if not path.name or path.is_absolute() or ".." in path.parts or "\\" in name:
        raise ArchiveError(f"unsafe archive member: {name}")
