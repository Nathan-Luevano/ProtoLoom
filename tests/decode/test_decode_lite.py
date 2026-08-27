from protoloom.container.dex import DexField, DexMethod
from protoloom.decode.infostring import HAS_HAS_BIT, InfoField
from protoloom.decode.lite import _field_oneof, decode_lite_finding
from protoloom.extract.lite import LiteFinding, LiteObject


def _field(*, oneof_index: int | None, raw_type: int) -> InfoField:
    return InfoField(number=1, type_id=9, raw_type=raw_type, oneof_index=oneof_index)


def test_real_oneof_index_wins_over_hasbit() -> None:
    field = _field(oneof_index=2, raw_type=HAS_HAS_BIT)
    assert _field_oneof(field, is_proto2=False) == "choice_2"


def test_proto3_hasbit_without_oneof_synthesizes_one() -> None:
    field = _field(oneof_index=None, raw_type=HAS_HAS_BIT)
    assert _field_oneof(field, is_proto2=False) == "synthetic_1"


def test_proto2_hasbit_does_not_synthesize_a_oneof() -> None:
    field = _field(oneof_index=None, raw_type=HAS_HAS_BIT)
    assert _field_oneof(field, is_proto2=True) is None


def test_no_presence_and_no_oneof_is_plain() -> None:
    field = _field(oneof_index=None, raw_type=0)
    assert _field_oneof(field, is_proto2=False) is None


def _encode_int(value: int) -> str:
    chars = []
    while value >= 0xD800:
        chars.append(chr((value & 0x1FFF) | 0xE000))
        value >>= 13
    chars.append(chr(value))
    return "".join(chars)


def _info_string(*values: int) -> str:
    return "".join(_encode_int(value) for value in values)


class _FakeDex:
    def __init__(self) -> None:
        self.types: tuple[str, ...] = ("LOwner;", "LOwner$Nested;")
        self.strings: tuple[str, ...] = ("newMessageInfo", "nested_")
        self.methods: tuple[DexMethod, ...] = (DexMethod(0, 0, 0),)
        self.fields: tuple[DexField, ...] = (DexField(0, 1, 1),)

    def field_name(self, item: DexField) -> str:
        return self.strings[item.name_index]

    def class_by_type_index(self, type_index: int) -> None:
        return None


def test_message_field_type_comes_from_the_declared_field_type() -> None:
    # header: flags, field_count, oneof_count, hasbits_count, min, max,
    # entry_count, map_field_count, repeated_field_count, check_initialized;
    # then one field: number=1, raw_type=9 (message, no hasbit/oneof).
    info = _info_string(0, 1, 0, 0, 1, 1, 1, 0, 0, 0, 1, 9)
    finding = LiteFinding(
        containing_method=0,
        code_offset=0,
        instruction_offset=0,
        info_string=info,
        objects=(LiteObject("string", "nested_"),),
    )
    decoded = decode_lite_finding(_FakeDex(), finding, "test.dex")  # type: ignore[arg-type]
    assert decoded.schema.messages[0].fields[0].type_name == "Nested"


class _FakeOneofDex(_FakeDex):
    def __init__(self) -> None:
        super().__init__()
        self.types = ("LOwner;", "LOwner$Group$Variant;")
        self.fields = ()


def test_oneof_message_field_name_comes_from_a_deeply_nested_type() -> None:
    # A real oneof member shares one storage field with its siblings, so it
    # never gets its own name string; only a class literal for its type.
    # header: oneof_count=1; one field: number=1, raw_type=9+ONEOF_TYPE_OFFSET,
    # oneof_index=0.
    info = _info_string(0, 1, 1, 0, 1, 1, 1, 0, 0, 0, 1, 60, 0)
    finding = LiteFinding(
        containing_method=0,
        code_offset=0,
        instruction_offset=0,
        info_string=info,
        objects=(
            LiteObject("string", "group_"),
            LiteObject("string", "groupCase_"),
            LiteObject("class", "LOwner$Group$Variant;"),
        ),
    )
    decoded = decode_lite_finding(_FakeOneofDex(), finding, "test.dex")  # type: ignore[arg-type]
    field = decoded.schema.messages[0].fields[0]
    assert field.type_name == "Group_Variant"
    assert field.name == "variant"


class _FakeWellKnownDex(_FakeDex):
    def __init__(self) -> None:
        super().__init__()
        self.types = ("LOwner;", "Lcom/google/protobuf/Timestamp;")
        self.strings = ("newMessageInfo", "expiry_")
        self.fields = (DexField(0, 1, 1),)


def test_well_known_type_field_gets_a_qualified_name_and_import() -> None:
    info = _info_string(0, 1, 0, 0, 1, 1, 1, 0, 0, 0, 1, 9)
    finding = LiteFinding(
        containing_method=0,
        code_offset=0,
        instruction_offset=0,
        info_string=info,
        objects=(LiteObject("string", "expiry_"),),
    )
    decoded = decode_lite_finding(_FakeWellKnownDex(), finding, "test.dex")  # type: ignore[arg-type]
    field = decoded.schema.messages[0].fields[0]
    assert field.type_name == ".google.protobuf.Timestamp"
    assert decoded.schema.dependencies == ["google/protobuf/timestamp.proto"]


class _FakeRepeatedFieldDex(_FakeDex):
    def __init__(self) -> None:
        super().__init__()
        self.types = (
            "LOwner;",
            "Lcom/google/protobuf/ProtobufArrayList;",
            "LOwner$Nested;",
        )
        self.strings = ("newMessageInfo", "items_")
        self.fields = (DexField(0, 1, 1),)


def test_repeated_field_declared_list_type_is_not_trusted_as_the_element_type() -> None:
    # A `repeated` field's declared Java type is a list wrapper, not the
    # element type; only the objects-array class literal names it here.
    info = _info_string(0, 1, 0, 0, 1, 1, 1, 0, 0, 0, 1, 9)
    finding = LiteFinding(
        containing_method=0,
        code_offset=0,
        instruction_offset=0,
        info_string=info,
        objects=(
            LiteObject("string", "items_"),
            LiteObject("class", "LOwner$Nested;"),
        ),
    )
    decoded = decode_lite_finding(_FakeRepeatedFieldDex(), finding, "test.dex")  # type: ignore[arg-type]
    field = decoded.schema.messages[0].fields[0]
    assert field.type_name == "Nested"


class _FakeFlatOneofDex(_FakeDex):
    def __init__(self) -> None:
        super().__init__()
        self.types = ("LOwner;", "LOwner$FlatVariant;")
        self.fields = ()


def test_oneof_message_field_name_stays_speculative_when_flat() -> None:
    # Only one "$" level: the bare class name may already be a generator's
    # own flattened compound name with no recoverable field-name relationship.
    info = _info_string(0, 1, 1, 0, 1, 1, 1, 0, 0, 0, 1, 60, 0)
    finding = LiteFinding(
        containing_method=0,
        code_offset=0,
        instruction_offset=0,
        info_string=info,
        objects=(
            LiteObject("string", "group_"),
            LiteObject("string", "groupCase_"),
            LiteObject("class", "LOwner$FlatVariant;"),
        ),
    )
    decoded = decode_lite_finding(_FakeFlatOneofDex(), finding, "test.dex")  # type: ignore[arg-type]
    field = decoded.schema.messages[0].fields[0]
    assert field.type_name == "FlatVariant"
    assert field.name == "field_1"
