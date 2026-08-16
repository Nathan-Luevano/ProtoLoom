import struct

import pytest

from protoloom.container.dex import DexError, DexFile


def _minimal_dex(strings: tuple[bytes, ...]) -> bytes:
    string_ids_offset = 112
    data_offset = string_ids_offset + 4 * len(strings)
    data = bytearray()
    offsets: list[int] = []
    for value in strings:
        offsets.append(data_offset + len(data))
        data.extend((len(value),))
        data.extend(value)
        data.append(0)
    file_size = data_offset + len(data)
    header = bytearray(112)
    header[:8] = b"dex\n039\x00"
    values = [
        file_size,
        112,
        0x12345678,
        0,
        0,
        0,
        len(strings),
        string_ids_offset,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        len(data),
        data_offset,
    ]
    struct.pack_into("<20I", header, 32, *values)
    return bytes(header) + struct.pack(f"<{len(offsets)}I", *offsets) + bytes(data)


def test_reads_string_pool() -> None:
    dex = DexFile(_minimal_dex((b"hello", b"world")))
    assert dex.header.version == "039"
    assert dex.strings == ("hello", "world")
    assert dex.types == ()


def test_reads_modified_utf8_nul() -> None:
    raw = bytearray(_minimal_dex((b"a\xc0\x80b",)))
    raw[116] = 3
    assert DexFile(raw).strings == ("a\x00b",)


def test_rejects_truncated_or_inconsistent_files() -> None:
    with pytest.raises(DexError):
        DexFile(b"dex\n039\x00")
    malformed = bytearray(_minimal_dex((b"value",)))
    struct.pack_into("<I", malformed, 32, len(malformed) + 1)
    with pytest.raises(DexError, match="size"):
        DexFile(malformed)
