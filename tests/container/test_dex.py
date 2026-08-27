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


def _dex_with_enclosing_class() -> bytes:
    strings = (
        b"Outer",
        b"Outer$Inner",
        b"Ldalvik/annotation/EnclosingClass;",
        b"value",
    )
    header_size = 112
    string_ids_offset = header_size
    type_ids_offset = string_ids_offset + 4 * len(strings)
    class_defs_offset = type_ids_offset + 4 * 3
    data_offset = class_defs_offset + 32 * 2

    data = bytearray()

    def _place(chunk: bytes) -> int:
        offset = data_offset + len(data)
        data.extend(chunk)
        return offset

    string_offsets = []
    for value in strings:
        string_offsets.append(_place(bytes((len(value),)) + value + b"\x00"))

    annotation_item_offset = _place(
        bytes((2,))  # visibility: system
        + bytes((2,))  # encoded_annotation type_idx (uleb128): type 2
        + bytes((1,))  # element count (uleb128)
        + bytes((3,))  # element name_idx (uleb128): "value"
        + bytes((0x18,))  # encoded_value header: VALUE_TYPE, arg 0
        + bytes((0,))  # value: type index 0 (Outer)
    )
    annotation_set_offset = _place(
        struct.pack("<I", 1) + struct.pack("<I", annotation_item_offset)
    )
    annotations_directory_offset = _place(
        struct.pack("<IIII", annotation_set_offset, 0, 0, 0)
    )

    type_ids = struct.pack("<3I", 0, 1, 2)
    class_defs = struct.pack(
        "<8I", 0, 0, 0xFFFFFFFF, 0, 0xFFFFFFFF, 0, 0, 0
    ) + struct.pack(
        "<8I",
        1,
        0,
        0xFFFFFFFF,
        0,
        0xFFFFFFFF,
        annotations_directory_offset,
        0,
        0,
    )

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
        3,
        type_ids_offset,
        0,
        0,
        0,
        0,
        0,
        0,
        2,
        class_defs_offset,
        len(data),
        data_offset,
    ]
    struct.pack_into("<20I", header, 32, *values)
    string_id_table = struct.pack(f"<{len(string_offsets)}I", *string_offsets)
    return bytes(header) + string_id_table + type_ids + class_defs + bytes(data)


def test_reads_enclosing_class_annotation() -> None:
    dex = DexFile(_dex_with_enclosing_class())
    outer, inner = dex.classes
    assert dex.enclosing_class_index(outer) is None
    assert dex.enclosing_class_index(inner) == outer.class_index
