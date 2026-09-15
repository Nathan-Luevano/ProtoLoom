from typing import Any

import pytest

from protoloom.container.dex import (
    CodeItem,
    DexClass,
    DexField,
    DexMethod,
    EncodedMethod,
)
from protoloom.decode.infostring import HAS_HAS_BIT, InfoField
from protoloom.decode.lite import (
    _enclosing_descriptor,
    _field_objects,
    _field_oneof,
    decode_lite_finding,
)
from protoloom.extract.lite import LiteFinding, LiteObject
from protoloom.model import Confidence


def _field(*, oneof_index: int | None, raw_type: int, type_id: int = 4) -> InfoField:
    return InfoField(
        number=1, type_id=type_id, raw_type=raw_type, oneof_index=oneof_index
    )


def test_real_oneof_index_wins_over_hasbit() -> None:
    field = _field(oneof_index=2, raw_type=HAS_HAS_BIT)
    assert _field_oneof(field, is_proto2=False, proto_type="int32") == "choice_2"


def test_proto3_hasbit_without_oneof_synthesizes_one() -> None:
    field = _field(oneof_index=None, raw_type=HAS_HAS_BIT)
    assert _field_oneof(field, is_proto2=False, proto_type="int32") == "synthetic_1"


def test_proto2_hasbit_does_not_synthesize_a_oneof() -> None:
    field = _field(oneof_index=None, raw_type=HAS_HAS_BIT)
    assert _field_oneof(field, is_proto2=True, proto_type="int32") is None


def test_no_presence_and_no_oneof_is_plain() -> None:
    field = _field(oneof_index=None, raw_type=0)
    assert _field_oneof(field, is_proto2=False, proto_type="int32") is None


def test_proto3_message_hasbit_has_implicit_presence_not_a_oneof() -> None:
    # Regression: gadgetbridge's WorkoutSummary (huami.proto) wrapped every
    # singular message field in a spurious "synthetic_N" oneof. protoc only
    # synthesizes oneofs for explicit "optional" scalars/enums in proto3 --
    # singular message fields always have implicit presence and are never
    # wrapped, so their hasbit must not trigger the synthetic-oneof path.
    field = _field(oneof_index=None, raw_type=HAS_HAS_BIT, type_id=9)
    assert _field_oneof(field, is_proto2=False, proto_type="message") is None


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

    def class_by_type_index(self, type_index: int) -> DexClass | None:
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
            "Lxy2;",
            "LOwner$Nested;",
        )
        self.strings = ("newMessageInfo", "items_")
        self.fields = (DexField(0, 1, 1),)


def test_repeated_field_declared_list_type_is_not_trusted_as_the_element_type() -> None:
    # A `repeated` field's declared Java type is a list wrapper, not the
    # element type; only the objects-array class literal names it here.
    info = _info_string(0, 1, 0, 0, 1, 1, 1, 0, 1, 0, 1, 27)
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


class _FakeFieldNumberConstantDex(_FakeDex):
    def __init__(self) -> None:
        super().__init__()
        self.types = ("LOwner;", "LOwner$FlatVariant;")
        self.fields = ()
        self._own_class = DexClass(0, 0, 0xFFFFFFFF, 0, 0xFFFFFFFF, 0, 1, 1)
        self._static_fields: tuple[DexField, ...] = (DexField(0, 4, 2),)
        self.strings = (*self.strings, "CUSTOM_FIELD_NUMBER")

    def class_by_type_index(self, type_index: int) -> DexClass:
        return self._own_class

    def class_static_fields(self, item: DexClass) -> tuple[DexField, ...]:
        return self._static_fields

    def static_field_values(self, item: DexClass) -> tuple[object, ...]:
        return (1,)

    def enclosing_class_index(self, item: DexClass) -> None:
        return None


def test_field_number_constant_wins_over_flat_oneof_guess() -> None:
    # The same flat single-`$`-level type the previous test leaves
    # speculative, but this class also carries protoc's generated
    # `NAME_FIELD_NUMBER` constant -- the authoritative source.
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
    decoded = decode_lite_finding(_FakeFieldNumberConstantDex(), finding, "test.dex")  # type: ignore[arg-type]
    field = decoded.schema.messages[0].fields[0]
    assert field.name == "custom"
    assert field.confidence.value == "high"


class _FakeShortAuthoritativeNameDex(_FakeFieldNumberConstantDex):
    def __init__(self) -> None:
        super().__init__()
        self.types = ("LOwner;", "I")
        self.strings = ("newMessageInfo", "id_", "ID_FIELD_NUMBER")


def test_authoritative_name_is_not_treated_as_obfuscated() -> None:
    # A single short field name ("id_") would otherwise trip the
    # obfuscation heuristic; the FIELD_NUMBER constant is immune to it.
    info = _info_string(0, 1, 0, 0, 1, 1, 1, 0, 0, 0, 1, 8)
    finding = LiteFinding(
        containing_method=0,
        code_offset=0,
        instruction_offset=0,
        info_string=info,
        objects=(LiteObject("string", "id_"),),
    )
    decoded = decode_lite_finding(
        _FakeShortAuthoritativeNameDex(),  # type: ignore[arg-type]
        finding,
        "test.dex",
    )
    field = decoded.schema.messages[0].fields[0]
    assert field.name == "id"
    assert field.confidence.value == "high"


class _FakeVerifierDex(_FakeDex):
    def __init__(self, *, singleton_name: str) -> None:
        super().__init__()
        self.types = ("LOwner;", "LOwner$Mode;", "LOwner$Mode$ModeVerifier;")
        self.strings = ("newMessageInfo", "mode_", singleton_name)
        self.fields = (DexField(2, 2, 2),)


def _enum_field_objects(dex: object) -> list[str | None]:
    info = _info_string(0, 1, 0, 0, 1, 1, 1, 0, 0, 0, 1, 12)
    finding = LiteFinding(
        containing_method=0,
        code_offset=0,
        instruction_offset=0,
        info_string=info,
        objects=(
            LiteObject("string", "mode_"),
            LiteObject("static_field", 0),
        ),
    )
    return _field_objects(dex, finding)[3]  # type: ignore[arg-type]


def test_unqualified_instance_singleton_names_the_verifier() -> None:
    verifiers = _enum_field_objects(_FakeVerifierDex(singleton_name="INSTANCE"))
    assert verifiers == ["LOwner$Mode$ModeVerifier;"]


def test_numbered_instance_singleton_is_not_trusted() -> None:
    # R8 can merge several distinct Verifier classes into one physical
    # class, telling their singletons apart only by field name (INSTANCE,
    # INSTANCE$1, ...); once merged, a numbered singleton's declaring class
    # no longer names the enum it actually belongs to.
    verifiers = _enum_field_objects(_FakeVerifierDex(singleton_name="INSTANCE$1"))
    assert verifiers == [None]


def test_negative_static_field_index_is_not_trusted() -> None:
    dex: Any = _FakeVerifierDex(singleton_name="INSTANCE")
    info = _info_string(0, 1, 0, 0, 1, 1, 1, 0, 0, 0, 1, 12)
    finding = LiteFinding(
        0,
        0,
        0,
        info,
        (LiteObject("string", "mode_"), LiteObject("static_field", -1)),
    )
    assert _field_objects(dex, finding)[3] == [None]


def test_lite_decoder_rejects_stale_method_index() -> None:
    dex: Any = _FakeDex()
    finding = LiteFinding(99, 0, 0, _info_string(0, 0), ())
    with pytest.raises(ValueError, match="method index"):
        decode_lite_finding(dex, finding, "test.dex")


def test_lite_decoder_rejects_stale_class_index() -> None:
    dex: Any = _FakeDex()
    dex.methods = (DexMethod(99, 0, 0),)
    finding = LiteFinding(0, 0, 0, _info_string(0, 0), ())
    with pytest.raises(ValueError, match="class index"):
        decode_lite_finding(dex, finding, "test.dex")


def test_class_literal_enum_object_names_the_verifier_directly() -> None:
    dex: Any = _FakeVerifierDex(singleton_name="INSTANCE")
    info = _info_string(0, 1, 0, 0, 1, 1, 1, 0, 0, 0, 1, 12)
    finding = LiteFinding(
        0,
        0,
        0,
        info,
        (
            LiteObject("string", "mode_"),
            LiteObject("class", "LOwner$Mode$ModeVerifier;"),
        ),
    )
    assert _field_objects(dex, finding)[3] == ["LOwner$Mode$ModeVerifier;"]


def test_call_result_enum_object_is_not_trusted_as_a_verifier() -> None:
    dex: Any = _FakeVerifierDex(singleton_name="INSTANCE")
    info = _info_string(0, 1, 0, 0, 1, 1, 1, 0, 0, 0, 1, 12)
    finding = LiteFinding(
        0,
        0,
        0,
        info,
        (LiteObject("string", "mode_"), LiteObject("call_result", 5)),
    )
    assert _field_objects(dex, finding)[3] == [None]


def test_map_field_reads_the_static_field_index_from_the_objects_array() -> None:
    dex: Any = _FakeDex()
    info = _info_string(0, 1, 0, 0, 1, 1, 1, 1, 0, 0, 1, 50)
    finding = LiteFinding(
        0,
        0,
        0,
        info,
        (LiteObject("static_field", 3),),
    )
    assert _field_objects(dex, finding)[2] == [3]


def test_enclosing_descriptor_returns_none_for_unknown_type() -> None:
    dex: Any = _FakeDex()
    assert _enclosing_descriptor(dex, "LUnknown;") is None


def test_enclosing_descriptor_falls_back_to_dollar_split_when_unannotated() -> None:
    dex: Any = _FakeDex()
    dex.types = ("LOwner;", "LOwner$Nested;")
    assert _enclosing_descriptor(dex, "LOwner$Nested;") == "LOwner;"


class _FakeNonIntStaticValueDex(_FakeFieldNumberConstantDex):
    def __init__(self) -> None:
        super().__init__()
        self._static_fields = (DexField(0, 4, 5), DexField(0, 4, 2))
        self.strings = (*self.strings, "SOME_OTHER_CONSTANT")

    def static_field_values(self, item: DexClass) -> tuple[object, ...]:
        # a non-int static (e.g. a String constant) must be skipped, not
        # crash the FIELD_NUMBER constant scan.
        return ("not-a-number", 1)


def test_non_int_static_field_value_is_skipped() -> None:
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
    decoded = decode_lite_finding(_FakeNonIntStaticValueDex(), finding, "test.dex")  # type: ignore[arg-type]
    field = decoded.schema.messages[0].fields[0]
    assert field.name == "custom"


def _enum_info_string() -> str:
    return _info_string(0, 1, 0, 0, 1, 1, 1, 0, 0, 0, 1, 12)


class _EnumMessageDex:
    def __init__(self, mode_type: str = "LOwner$Mode;") -> None:
        self.types: tuple[str, ...] = ("LOwner;", mode_type)
        self.strings: tuple[str, ...] = (
            "newMessageInfo",
            "mode_",
            "getMode",
            "<init>",
            "<clinit>",
            "MODE_UNSPECIFIED",
            "MODE_ACTIVE",
        )
        self.methods: tuple[DexMethod, ...] = (
            DexMethod(0, 0, 2),
            DexMethod(1, 0, 3),
            DexMethod(1, 0, 4),
        )
        self.fields: tuple[DexField, ...] = (DexField(1, 1, 5), DexField(1, 1, 6))
        instructions = (
            0x22,
            1,
            0x011A,
            5,
            0x0212,
            0x4070,
            1,
            0x2210,
            0x69,
            0,
            0x22,
            1,
            0x011A,
            6,
            0x1212,
            0x4070,
            1,
            0x2210,
            0x69,
            1,
            0x0E,
        )
        self._items: tuple[tuple[EncodedMethod, CodeItem], ...] = (
            (EncodedMethod(2, 0, 200), CodeItem(200, 3, 0, 4, 0, 0, instructions)),
        )

    def method_name(self, method: DexMethod) -> str:
        return self.strings[method.name_index]

    def method_return_type(self, method: DexMethod) -> str:
        return self.types[1]

    def method_parameter_types(self, method: DexMethod) -> tuple[str, ...]:
        return ("Ljava/lang/String;", "I", "I") if method.name_index == 3 else ()

    def field_name(self, field: DexField) -> str:
        return self.strings[field.name_index]

    def class_by_type_index(self, type_index: int) -> None:
        return None

    def iter_code_items(self) -> tuple[tuple[EncodedMethod, CodeItem], ...]:
        return self._items


def test_message_local_enum_is_fully_resolved_via_the_getter() -> None:
    dex: Any = _EnumMessageDex()
    finding = LiteFinding(
        0, 0, 0, _enum_info_string(), (LiteObject("string", "mode_"),)
    )

    decoded = decode_lite_finding(dex, finding, "test.dex")

    field = decoded.schema.messages[0].fields[0]
    assert field.type_name == "Mode"
    assert field.confidence.value == "high"
    assert [(v.name, v.number) for v in decoded.schema.messages[0].enums[0].values] == [
        ("MODE_UNSPECIFIED", 0),
        ("MODE_ACTIVE", 1),
    ]


def test_non_message_local_enum_records_its_own_enclosing_class() -> None:
    dex: Any = _EnumMessageDex(mode_type="LOther$Mode;")
    finding = LiteFinding(
        0, 0, 0, _enum_info_string(), (LiteObject("string", "mode_"),)
    )

    decoded = decode_lite_finding(dex, finding, "test.dex")

    field = decoded.schema.messages[0].fields[0]
    assert field.type_name == "Mode"
    assert decoded.schema.messages[0].enums == []
    assert [e.name for e in decoded.schema.enums] == ["Mode"]
    assert decoded.enum_enclosing == {"Mode": "LOther;"}


def test_unresolvable_enum_field_falls_back_to_int32() -> None:
    dex: Any = _EnumMessageDex(mode_type="LMode;")
    finding = LiteFinding(
        0, 0, 0, _enum_info_string(), (LiteObject("string", "mode_"),)
    )

    decoded = decode_lite_finding(dex, finding, "test.dex")

    field = decoded.schema.messages[0].fields[0]
    assert field.type_name == "int32"
    # We only recovered a lossy wire-compatible stand-in, not the field's
    # real (named enum) type -- confidence must reflect that it's a guess.
    assert field.confidence is not Confidence.HIGH


def test_enum_verifier_fallback_resolves_when_no_getter_name_is_known() -> None:
    dex: Any = _EnumMessageDex()
    dex.types = (*dex.types, "LOwner$Mode$ModeVerifier;")
    finding = LiteFinding(
        0,
        0,
        0,
        _enum_info_string(),
        (LiteObject("class", "LOwner$Mode$ModeVerifier;"),),
    )

    decoded = decode_lite_finding(dex, finding, "test.dex")

    field = decoded.schema.messages[0].fields[0]
    assert field.type_name == "Mode"


def test_well_known_auxiliary_class_resolves_type_and_dependency() -> None:
    dex: Any = _FakeDex()
    dex.types = ("LOwner;", "Lcom/google/protobuf/Timestamp;")
    info = _info_string(0, 1, 0, 0, 1, 1, 1, 0, 0, 0, 1, 9)
    finding = LiteFinding(
        0, 0, 0, info, (LiteObject("class", "Lcom/google/protobuf/Timestamp;"),)
    )

    decoded = decode_lite_finding(dex, finding, "test.dex")

    field = decoded.schema.messages[0].fields[0]
    assert field.type_name == ".google.protobuf.Timestamp"
    assert decoded.schema.dependencies == ["google/protobuf/timestamp.proto"]


def test_message_field_without_any_type_signal_guesses_from_its_name() -> None:
    dex: Any = _FakeDex()
    dex.fields = ()
    info = _info_string(0, 1, 0, 0, 1, 1, 1, 0, 0, 0, 1, 9)
    finding = LiteFinding(0, 0, 0, info, (LiteObject("string", "thing_"),))

    decoded = decode_lite_finding(dex, finding, "test.dex")

    field = decoded.schema.messages[0].fields[0]
    assert field.type_name == "Thing"
    assert field.confidence.value == "medium"


def test_message_field_with_no_signal_at_all_gets_a_numbered_placeholder() -> None:
    dex: Any = _FakeDex()
    dex.fields = ()
    info = _info_string(0, 1, 0, 0, 1, 1, 1, 0, 0, 0, 1, 9)
    finding = LiteFinding(0, 0, 0, info, ())

    decoded = decode_lite_finding(dex, finding, "test.dex")

    field = decoded.schema.messages[0].fields[0]
    assert field.type_name == "RecoveredField1"
    assert field.confidence.value == "speculative"


def test_map_field_without_recoverable_evidence_falls_back_to_bytes() -> None:
    dex: Any = _FakeDex()
    dex.fields = ()
    info = _info_string(0, 1, 0, 0, 1, 1, 1, 1, 0, 0, 1, 50)
    finding = LiteFinding(0, 0, 0, info, (LiteObject("static_field", 3),))

    decoded = decode_lite_finding(dex, finding, "test.dex")

    field = decoded.schema.messages[0].fields[0]
    assert field.type_name == "bytes"


def test_heuristic_finding_downgrades_a_resolved_field_to_medium() -> None:
    info = _info_string(0, 1, 0, 0, 1, 1, 1, 0, 0, 0, 1, 9)
    finding = LiteFinding(
        containing_method=0,
        code_offset=0,
        instruction_offset=0,
        info_string=info,
        objects=(LiteObject("string", "nested_"),),
        heuristic=True,
    )
    decoded = decode_lite_finding(_FakeDex(), finding, "test.dex")  # type: ignore[arg-type]
    field = decoded.schema.messages[0].fields[0]
    assert field.type_name == "Nested"
    assert field.confidence.value == "medium"
