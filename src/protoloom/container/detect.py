from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import BinaryIO
from zipfile import BadZipFile, ZipFile

from protoloom.container.apk import _is_dex_name


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
MAX_ZIP_DETECTION_ENTRIES = 100_000
MAX_ZIP_DETECTION_NAME_BYTES = 16 * 1024 * 1024


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
            return Detection(ContainerKind.UNKNOWN)
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
        prefix = stream.read(4096)
        result = detect_bytes(prefix)
        if result.kind is ContainerKind.UNKNOWN and prefix[:2] == b"MZ":
            result = _detect_pe(stream, prefix, source.stat().st_size)
    if result.kind is not ContainerKind.ZIP:
        return result
    try:
        with ZipFile(source) as archive:
            android_manifest = False
            root_dex = False
            bundle = False
            jar = False
            name_bytes = 0
            for index, info in enumerate(archive.infolist(), 1):
                name = info.filename
                name_bytes += len(name.encode("utf-8"))
                if (
                    index > MAX_ZIP_DETECTION_ENTRIES
                    or name_bytes > MAX_ZIP_DETECTION_NAME_BYTES
                ):
                    return Detection(
                        ContainerKind.ZIP, "archive metadata limit exceeded"
                    )
                android_manifest |= name == "AndroidManifest.xml"
                root_dex |= _is_root_dex(name)
                bundle |= name == "BundleConfig.pb" or name.endswith(
                    "/manifest/AndroidManifest.xml"
                )
                jar |= name == "META-INF/MANIFEST.MF" or name.endswith(".class")
    except (BadZipFile, OSError):
        return Detection(ContainerKind.UNKNOWN)
    if android_manifest and root_dex:
        return Detection(ContainerKind.APK)
    if bundle:
        return Detection(ContainerKind.AAB)
    if jar:
        return Detection(ContainerKind.JAR)
    return result


def _is_root_dex(name: str) -> bool:
    return "/" not in name and _is_dex_name(name)


def _detect_pe(stream: BinaryIO, prefix: bytes, file_size: int) -> Detection:
    if len(prefix) < 64:
        return Detection(ContainerKind.UNKNOWN)
    pe_offset = struct.unpack_from("<I", prefix, 0x3C)[0]
    if pe_offset + 4 > file_size:
        return Detection(ContainerKind.UNKNOWN)
    stream.seek(pe_offset)
    if stream.read(4) == b"PE\x00\x00":
        return Detection(ContainerKind.PE)
    return Detection(ContainerKind.UNKNOWN)
