from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from zipfile import BadZipFile, ZipFile


class ContainerKind(StrEnum):
    APK = "apk"
    AAB = "aab"
    DEX = "dex"
    ELF = "elf"
    MACHO = "mach-o"
    PE = "pe"
    JAR = "jar"
    ZIP = "zip"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Detection:
    kind: ContainerKind
    detail: str | None = None


_MACHO_MAGICS = {
    b"\xfe\xed\xfa\xce",
    b"\xce\xfa\xed\xfe",
    b"\xfe\xed\xfa\xcf",
    b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe",
    b"\xbe\xba\xfe\xca",
    b"\xca\xfe\xba\xbf",
    b"\xbf\xba\xfe\xca",
}


def detect_bytes(data: bytes | bytearray | memoryview) -> Detection:
    view = memoryview(data)
    if len(view) >= 8 and bytes(view[:4]) == b"dex\n" and bytes(view[7:8]) == b"\x00":
        version = bytes(view[4:7]).decode("ascii", errors="replace")
        return Detection(ContainerKind.DEX, version)
    if len(view) >= 4 and bytes(view[:4]) == b"\x7fELF":
        return Detection(ContainerKind.ELF)
    if len(view) >= 4 and bytes(view[:4]) in _MACHO_MAGICS:
        return Detection(ContainerKind.MACHO)
    if len(view) >= 2 and bytes(view[:2]) == b"MZ":
        if len(view) < 64:
            return Detection(ContainerKind.PE)
        pe_offset = struct.unpack_from("<I", view, 0x3C)[0]
        if (
            pe_offset + 4 <= len(view)
            and bytes(view[pe_offset : pe_offset + 4]) == b"PE\x00\x00"
        ):
            return Detection(ContainerKind.PE)
    if len(view) >= 4 and bytes(view[:4]) in {
        b"PK\x03\x04",
        b"PK\x05\x06",
        b"PK\x07\x08",
    }:
        return Detection(ContainerKind.ZIP)
    return Detection(ContainerKind.UNKNOWN)


def detect(path: str | Path) -> Detection:
    source = Path(path)
    with source.open("rb") as stream:
        result = detect_bytes(stream.read(4096))
    if result.kind is not ContainerKind.ZIP:
        return result
    try:
        with ZipFile(source) as archive:
            names = frozenset(archive.namelist())
    except (BadZipFile, OSError):
        return Detection(ContainerKind.UNKNOWN)
    if "AndroidManifest.xml" in names and any(_is_root_dex(name) for name in names):
        return Detection(ContainerKind.APK)
    if "BundleConfig.pb" in names or any(
        name.endswith("/manifest/AndroidManifest.xml") for name in names
    ):
        return Detection(ContainerKind.AAB)
    if "META-INF/MANIFEST.MF" in names or any(
        name.endswith(".class") for name in names
    ):
        return Detection(ContainerKind.JAR)
    return result


def _is_root_dex(name: str) -> bool:
    return "/" not in name and name.startswith("classes") and name.endswith(".dex")
