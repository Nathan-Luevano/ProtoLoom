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


def test_rejects_excessive_table_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("protoloom.container.dex.MAX_DEX_TABLE_ENTRIES", 1)
    with pytest.raises(DexError, match="too many entries"):
        DexFile(_minimal_dex((b"first", b"second")))


def test_rejects_excessive_total_table_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("protoloom.container.dex.MAX_DEX_TOTAL_TABLE_ENTRIES", 3)
    with pytest.raises(DexError, match="too many entries"):
        DexFile(_minimal_dex((b"a", b"b", b"c", b"d")))


def test_rejects_invalid_populated_table_offset() -> None:
    malformed = bytearray(_minimal_dex(()))
    struct.pack_into("<II", malformed, 56, 1, 0)
    with pytest.raises(DexError, match="invalid offset"):
        DexFile(malformed)


def test_rejects_data_section_outside_file() -> None:
    malformed = bytearray(_minimal_dex(()))
    struct.pack_into("<II", malformed, 104, 4, len(malformed))
    with pytest.raises(DexError, match="outside the file"):
        DexFile(malformed)


def test_rejects_unterminated_string_data() -> None:
    raw = bytearray(_minimal_dex((b"ab",)))[:-1]
    struct.pack_into("<I", raw, 32, len(raw))
    struct.pack_into("<I", raw, 104, len(raw) - 116)
    with pytest.raises(DexError, match="unterminated"):
        DexFile(bytes(raw))


def test_rejects_string_utf16_length_mismatch() -> None:
    raw = bytearray(_minimal_dex((b"ab",)))
    raw[116] = 99
    with pytest.raises(DexError, match="UTF-16 length mismatch"):
        DexFile(bytes(raw))


def test_rejects_misaligned_data_section_offset() -> None:
    raw = bytearray(_minimal_dex(()))
    struct.pack_into("<II", raw, 104, 1, 113)
    with pytest.raises(DexError, match="invalid offset"):
        DexFile(bytes(raw))


def test_rejects_excessive_string_decode_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("protoloom.container.dex.MAX_DEX_STRING_DECODE_BYTES", 2)
    with pytest.raises(DexError, match="decode limit"):
        DexFile(_minimal_dex((b"value",)))


def test_reuses_duplicate_string_offsets() -> None:
    raw = bytearray(_minimal_dex((b"shared", b"unused")))
    (first_offset,) = struct.unpack_from("<I", raw, 112)
    struct.pack_into("<I", raw, 116, first_offset)
    assert DexFile(raw).strings == ("shared", "shared")


def test_rejects_excessive_code_units(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("protoloom.container.dex.MAX_DEX_COLLECTION_ENTRIES", 1)
    raw = bytearray(_minimal_dex(()))
    offset = len(raw)
    raw.extend(struct.pack("<HHHHII", 0, 0, 0, 0, 0, 2))
    struct.pack_into("<I", raw, 32, len(raw))
    struct.pack_into("<I", raw, 104, len(raw) - offset)
    dex = DexFile(raw)
    with pytest.raises(DexError, match="too many entries"):
        dex.code_item(offset)


def test_uleb128_rejects_truncated_value() -> None:
    raw = bytearray(_minimal_dex(()))
    offset = len(raw)
    raw.extend(bytes((0x80,)))
    struct.pack_into("<I", raw, 32, len(raw))
    struct.pack_into("<I", raw, 104, len(raw) - 112)
    dex = DexFile(raw)
    with pytest.raises(DexError, match="truncated"):
        dex._uleb128(offset)


def test_uleb128_rejects_value_exceeding_32_bits() -> None:
    raw = bytearray(_minimal_dex(()))
    offset = len(raw)
    raw.extend(bytes((0x80, 0x80, 0x80, 0x80, 0x10)))
    struct.pack_into("<I", raw, 32, len(raw))
    struct.pack_into("<I", raw, 104, len(raw) - 112)
    dex = DexFile(raw)
    with pytest.raises(DexError, match="exceeds 32 bits"):
        dex._uleb128(offset)


def test_uleb128_rejects_value_without_terminating_byte() -> None:
    raw = bytearray(_minimal_dex(()))
    offset = len(raw)
    raw.extend(bytes((0x80, 0x80, 0x80, 0x80, 0x80)))
    struct.pack_into("<I", raw, 32, len(raw))
    struct.pack_into("<I", raw, 104, len(raw) - 112)
    dex = DexFile(raw)
    with pytest.raises(DexError, match="invalid ULEB128"):
        dex._uleb128(offset)


def _dex_with_method_and_interface() -> bytes:
    strings = (b"I", b"Ljava/lang/String;", b"LOwner;", b"LIface;", b"f", b"name")
    string_ids_offset = 112
    type_ids_offset = string_ids_offset + 4 * len(strings)
    proto_ids_offset = type_ids_offset + 4 * 4
    field_ids_offset = proto_ids_offset + 12 * 1
    method_ids_offset = field_ids_offset + 8 * 1
    class_defs_offset = method_ids_offset + 8 * 1
    data_offset = class_defs_offset + 32 * 1

    data = bytearray()

    def _place(chunk: bytes) -> int:
        offset = data_offset + len(data)
        data.extend(chunk)
        return offset

    string_offsets = [
        _place(bytes((len(value),)) + value + b"\x00") for value in strings
    ]
    proto_params_offset = _place(struct.pack("<I", 1) + struct.pack("<H", 0))
    interfaces_offset = _place(struct.pack("<I", 1) + struct.pack("<H", 3))

    type_ids = struct.pack("<4I", 0, 1, 2, 3)
    proto_ids = struct.pack("<III", 0, 1, proto_params_offset)
    field_ids = struct.pack("<HHI", 2, 0, 4)
    method_ids = struct.pack("<HHI", 2, 0, 5)
    class_defs = struct.pack(
        "<8I", 2, 0, 0xFFFFFFFF, interfaces_offset, 0xFFFFFFFF, 0, 0, 0
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
        4,
        type_ids_offset,
        1,
        proto_ids_offset,
        1,
        field_ids_offset,
        1,
        method_ids_offset,
        1,
        class_defs_offset,
        len(data),
        data_offset,
    ]
    struct.pack_into("<20I", header, 32, *values)
    string_id_table = struct.pack(f"<{len(string_offsets)}I", *string_offsets)
    return (
        bytes(header)
        + string_id_table
        + type_ids
        + proto_ids
        + field_ids
        + method_ids
        + class_defs
        + bytes(data)
    )


def test_reads_prototype_parameters_and_interfaces() -> None:
    dex = DexFile(_dex_with_method_and_interface())
    (proto,) = dex.prototypes
    assert dex.types[proto.return_type_index] == "Ljava/lang/String;"
    assert tuple(dex.types[index] for index in proto.parameter_type_indexes) == ("I",)
    (method,) = dex.methods
    assert dex.method_name(method) == "name"
    assert dex.method_return_type(method) == "Ljava/lang/String;"
    assert dex.method_parameter_types(method) == ("I",)
    (field,) = dex.fields
    assert dex.field_name(field) == "f"


def test_rejects_out_of_range_method_identifier() -> None:
    malformed = bytearray(_dex_with_method_and_interface())
    struct.pack_into("<HHI", malformed, 172, 2, 0, 6)
    with pytest.raises(DexError, match="method identifier"):
        DexFile(malformed)


def test_rejects_out_of_range_field_identifier() -> None:
    malformed = bytearray(_dex_with_method_and_interface())
    struct.pack_into("<HHI", malformed, 164, 2, 9, 4)
    with pytest.raises(DexError, match="field identifier"):
        DexFile(malformed)


def test_rejects_out_of_range_class_type_identifier() -> None:
    malformed = bytearray(_dex_with_method_and_interface())
    struct.pack_into("<I", malformed, 180, 9)
    with pytest.raises(DexError, match="class type identifier"):
        DexFile(malformed)


def test_rejects_out_of_range_interface_identifier() -> None:
    malformed = bytearray(_dex_with_method_and_interface())
    struct.pack_into("<H", malformed, 268 + 4, 9)
    with pytest.raises(DexError, match="interface identifier"):
        DexFile(malformed)


def _uleb(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _dex_with_direct_method_code() -> bytes:
    strings = (b"I", b"Ljava/lang/String;", b"LOwner;", b"name")
    string_ids_offset = 112
    type_ids_offset = string_ids_offset + 4 * len(strings)
    proto_ids_offset = type_ids_offset + 4 * 3
    method_ids_offset = proto_ids_offset + 12 * 1
    class_defs_offset = method_ids_offset + 8 * 1
    data_offset = class_defs_offset + 32 * 1

    data = bytearray()

    def _place(chunk: bytes) -> int:
        offset = data_offset + len(data)
        data.extend(chunk)
        return offset

    string_offsets = [
        _place(bytes((len(value),)) + value + b"\x00") for value in strings
    ]
    # tries_size=1 with an odd instruction count exercises the 2-byte alignment pad.
    code_offset = _place(
        struct.pack("<HHHHII", 1, 0, 0, 1, 0, 1)
        + struct.pack("<1H", 0x000E)
        + b"\x00\x00"
        + b"\x00" * 8
    )
    class_data_offset = _place(
        _uleb(0)
        + _uleb(0)
        + _uleb(1)
        + _uleb(0)  # static=0, instance=0, direct=1, virtual=0
        + _uleb(0)
        + _uleb(0)
        + _uleb(code_offset)  # method_idx_diff, access_flags, code_off
    )

    type_ids = struct.pack("<3I", 0, 1, 2)
    proto_ids = struct.pack("<III", 0, 1, 0)
    method_ids = struct.pack("<HHI", 2, 0, 3)
    class_defs = struct.pack(
        "<8I", 2, 0, 0xFFFFFFFF, 0, 0xFFFFFFFF, 0, class_data_offset, 0
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
        1,
        proto_ids_offset,
        0,
        0,
        1,
        method_ids_offset,
        1,
        class_defs_offset,
        len(data),
        data_offset,
    ]
    struct.pack_into("<20I", header, 32, *values)
    string_id_table = struct.pack(f"<{len(string_offsets)}I", *string_offsets)
    return (
        bytes(header)
        + string_id_table
        + type_ids
        + proto_ids
        + method_ids
        + class_defs
        + bytes(data)
    )


def test_reads_direct_method_and_code_item() -> None:
    dex = DexFile(_dex_with_direct_method_code())
    (owner,) = dex.classes
    (method,) = dex.class_methods(owner)
    code = dex.code_item(method.code_offset)
    assert code.registers_size == 1
    assert code.tries_size == 1
    assert code.instructions == (0x000E,)
    assert dex.iter_code_items() == ((method, code),)
    assert dex.method_name(method) == "name"


def test_code_item_rejects_zero_offset() -> None:
    dex = DexFile(_dex_with_direct_method_code())
    with pytest.raises(DexError, match="no code item"):
        dex.code_item(0)


def test_class_static_fields_and_values_empty_when_no_class_data() -> None:
    dex = DexFile(_dex_with_method_and_interface())
    (owner,) = dex.classes
    assert dex.class_static_fields(owner) == ()
    assert dex.static_field_values(owner) == ()


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


def test_caches_immutable_dex_metadata() -> None:
    dex = DexFile(_dex_with_enclosing_class())
    _, inner = dex.classes
    annotations = dex.class_annotations(inner)
    assert dex.types is dex.types
    assert dex.class_by_type_index(inner.class_index) is inner
    assert dex.class_annotations(inner) is annotations


def test_rejects_duplicate_class_definitions() -> None:
    malformed = bytearray(_dex_with_enclosing_class())
    struct.pack_into("<I", malformed, 172, 0)
    with pytest.raises(DexError, match="duplicate class"):
        DexFile(malformed)


def test_rejects_invalid_type_descriptor_index() -> None:
    malformed = bytearray(_dex_with_enclosing_class())
    struct.pack_into("<I", malformed, 128, 4)
    with pytest.raises(DexError, match="descriptor string"):
        DexFile(malformed)


def test_rejects_invalid_class_superclass_index() -> None:
    malformed = bytearray(_dex_with_enclosing_class())
    struct.pack_into("<I", malformed, 148, 3)
    with pytest.raises(DexError, match="superclass"):
        DexFile(malformed)


def test_rejects_invalid_class_source_file_index() -> None:
    malformed = bytearray(_dex_with_enclosing_class())
    struct.pack_into("<I", malformed, 156, 4)
    with pytest.raises(DexError, match="source file"):
        DexFile(malformed)


def _dex_with_static_int_fields() -> bytes:
    strings = (b"Owner", b"I", b"A_FIELD_NUMBER", b"B_FIELD_NUMBER")
    header_size = 112
    string_ids_offset = header_size
    type_ids_offset = string_ids_offset + 4 * len(strings)
    field_ids_offset = type_ids_offset + 4 * 2
    class_defs_offset = field_ids_offset + 8 * 2
    data_offset = class_defs_offset + 32 * 1

    data = bytearray()

    def _place(chunk: bytes) -> int:
        offset = data_offset + len(data)
        data.extend(chunk)
        return offset

    string_offsets = [
        _place(bytes((len(value),)) + value + b"\x00") for value in strings
    ]

    class_data_offset = _place(
        bytes((2, 0, 0, 0))  # static=2, instance=0, direct=0, virtual=0 (uleb128)
        + bytes((0, 0x19))  # field_idx_diff=0 -> field 0, access_flags
        + bytes((1, 0x19))  # field_idx_diff=1 -> field 1, access_flags
    )
    static_values_offset = _place(
        bytes((2,))  # encoded_array size (uleb128)
        + bytes((0x04, 1))  # VALUE_INT, arg 0: value 1
        + bytes((0x04, 2))  # VALUE_INT, arg 0: value 2
    )

    type_ids = struct.pack("<2I", 0, 1)
    field_ids = struct.pack("<HHI", 0, 1, 2) + struct.pack("<HHI", 0, 1, 3)
    class_defs = struct.pack(
        "<8I",
        0,
        0,
        0xFFFFFFFF,
        0,
        0xFFFFFFFF,
        0,
        class_data_offset,
        static_values_offset,
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
        2,
        type_ids_offset,
        0,
        0,
        2,
        field_ids_offset,
        0,
        0,
        1,
        class_defs_offset,
        len(data),
        data_offset,
    ]
    struct.pack_into("<20I", header, 32, *values)
    string_id_table = struct.pack(f"<{len(string_offsets)}I", *string_offsets)
    return (
        bytes(header)
        + string_id_table
        + type_ids
        + field_ids
        + class_defs
        + bytes(data)
    )


def _dex_with_static_payload(payload: bytes) -> bytes:
    source = bytearray(_dex_with_static_int_fields())
    parsed = DexFile(source)
    offset = parsed.classes[0].static_values_offset
    data_offset = parsed.header.data_offset
    result = source[:offset] + payload
    struct.pack_into("<I", result, 32, len(result))
    struct.pack_into("<I", result, 104, len(result) - data_offset)
    return bytes(result)


def test_reads_static_field_number_constants() -> None:
    dex = DexFile(_dex_with_static_int_fields())
    (owner,) = dex.classes
    static_fields = dex.class_static_fields(owner)
    values = dex.static_field_values(owner)
    names = [dex.field_name(item) for item in static_fields]
    assert names == ["A_FIELD_NUMBER", "B_FIELD_NUMBER"]
    assert values == (1, 2)


def test_reads_negative_static_integer_constants() -> None:
    raw = bytearray(_dex_with_static_int_fields())
    raw[-3] = 0xFF
    dex = DexFile(raw)
    assert dex.static_field_values(dex.classes[0]) == (-1, 2)


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (bytes((1, 0x30, 0x80, 0x3F)), 1.0),
        (bytes((1, 0x31, 0xF0, 0x3F)), 1.0),
        (bytes((1, 0x70)) + struct.pack("<f", -2.5), -2.5),
        (bytes((1, 0xF1)) + struct.pack("<d", 3.25), 3.25),
    ],
)
def test_reads_static_floating_point_constants(
    payload: bytes,
    expected: float,
) -> None:
    dex = DexFile(_dex_with_static_payload(payload))
    assert dex.static_field_values(dex.classes[0]) == (expected,)


def test_rejects_invalid_encoded_value_width() -> None:
    raw = bytearray(_dex_with_static_int_fields())
    raw[-4] = 0xE4
    dex = DexFile(raw)
    with pytest.raises(DexError, match="invalid width"):
        dex.static_field_values(dex.classes[0])


def test_rejects_invalid_encoded_reference() -> None:
    raw = bytearray(_dex_with_static_int_fields())
    raw[-4] = 0x17
    raw[-3] = 0xFF
    dex = DexFile(raw)
    with pytest.raises(DexError, match="string index"):
        dex.static_field_values(dex.classes[0])


def test_rejects_excessive_encoded_collection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("protoloom.container.dex.MAX_DEX_COLLECTION_ENTRIES", 1)
    dex = DexFile(_dex_with_static_int_fields())
    with pytest.raises(DexError, match="too many entries"):
        dex.static_field_values(dex.classes[0])


def test_rejects_deeply_nested_encoded_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("protoloom.container.dex.MAX_DEX_ENCODED_VALUE_DEPTH", 1)
    raw = bytearray(_dex_with_static_int_fields())
    dex = DexFile(raw)
    offset = dex.classes[0].static_values_offset
    data_offset = dex.header.data_offset
    raw = raw[:offset] + bytes((1, 0x1C, 1, 0x1C, 1, 0x1E))
    struct.pack_into("<I", raw, 32, len(raw))
    struct.pack_into("<I", raw, 104, len(raw) - data_offset)
    nested = DexFile(raw)
    with pytest.raises(DexError, match="nesting is too deep"):
        nested.static_field_values(nested.classes[0])
