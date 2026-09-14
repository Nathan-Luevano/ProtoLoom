from types import SimpleNamespace
from typing import Any

from protoloom.container.dex import AnnotationItem, DexClass, DexField
from protoloom.decode.wire import (
    decode_wire_adapter_fields,
    decode_wire_adapters,
    decode_wire_annotations,
    decode_wire_enums,
    decode_wire_messages,
    wire_dex_type,
)
from protoloom.extract.lite import _Instruction
from protoloom.extract.wire import (
    WireAdapterFinding,
    WireEnumFinding,
    WireFieldFinding,
    WireNameFinding,
    WireOneofFinding,
    _constant,
    _constructor_and,
    _constructor_arguments,
    _constructor_move,
    _constructor_value,
    _default_constructor_nulls,
    _default_constructor_state,
    _label,
    _move_register,
    _parameter_registers,
    _string,
    _wire_enum_method,
    extract_wire_annotations,
    extract_wire_messages,
    extract_wire_names,
    extract_wire_null_defaults,
    extract_wire_oneofs,
    extract_wire_syntaxes,
    wire_adapter_type,
)

_DEFAULT_MARKER = "Lkotlin/jvm/internal/DefaultConstructorMarker;"


def _wire_dex() -> Any:
    owner = DexClass(0, 0, 1, 0, 0, 1, 0, 0)
    field = DexField(0, 2, 1)
    label = DexField(3, 3, 2)
    annotation = AnnotationItem(
        1,
        3,
        (
            (3, 7),
            (4, 5),
            (6, 1),
            (8, 9),
            (10, 2),
        ),
    )
    return SimpleNamespace(
        NO_INDEX=0xFFFFFFFF,
        types=(
            "Lexample/Record;",
            "Lcom/squareup/wire/Message;",
            "Ljava/lang/String;",
            "Lcom/squareup/wire/WireField;",
        ),
        classes=(owner,),
        fields=(field, label),
        strings=(
            "Record",
            "title",
            "REPEATED",
            "tag",
            "adapter",
            "x#STRING",
            "label",
            "unused",
            "oneofName",
            "choice",
            "schemaIndex",
        ),
        field_annotations=lambda _: ((field, (annotation,)),),
        field_name=lambda item: "REPEATED" if item is label else "title",
    )


def test_extracts_retained_wire_field_annotation() -> None:
    finding = extract_wire_annotations(_wire_dex())[0]

    assert (finding.owner, finding.number, finding.adapter) == (
        "Lexample/Record;",
        7,
        "x#STRING",
    )
    assert (finding.label, finding.oneof) == ("repeated", "choice")
    assert finding.schema_index == 2


def test_extract_wire_annotations_skips_non_message_and_unannotated_fields() -> None:
    no_super = DexClass(0, 0, 0xFFFFFFFF, 0, 0, 0, 0, 0)
    wrong_super = DexClass(1, 0, 2, 0, 0, 0, 0, 0)
    message_owner = DexClass(2, 0, 1, 0, 0, 3, 0, 0)
    field = DexField(0, 2, 1)
    unannotated = DexField(0, 2, 4)
    other_annotation = AnnotationItem(1, 4, ())
    bad_tag_annotation = AnnotationItem(
        1,
        3,
        ((0, "not-an-int"), (1, 5)),
    )
    dex: Any = SimpleNamespace(
        NO_INDEX=0xFFFFFFFF,
        types=(
            "Lexample/Other;",
            "Lcom/squareup/wire/Message;",
            "Lexample/Record;",
            "Lcom/squareup/wire/WireField;",
            "Lcom/squareup/wire/OtherAnnotation;",
        ),
        classes=(no_super, wrong_super, message_owner),
        strings=("tag", "adapter"),
        field_annotations=lambda item: (
            ((unannotated, (other_annotation,)), (field, (bad_tag_annotation,)))
            if item is message_owner
            else ()
        ),
    )

    assert extract_wire_annotations(dex) == ()


def test_extracts_empty_wire_message() -> None:
    dex = _wire_dex()

    assert extract_wire_messages(dex) == ("Lexample/Record;",)


def _wire_dex_with_code(method_name: str, instructions: tuple[int, ...]) -> Any:
    owner = DexClass(0, 0, 0xFFFFFFFF, 0, 0, 0, 0, 0)
    method = SimpleNamespace(code_offset=1, method_index=0)
    return SimpleNamespace(
        classes=(owner,),
        types=("Lexample/Record;",),
        strings=(),
        fields=(),
        methods=(),
        class_methods=lambda _: (method,),
        method_name=lambda _: method_name,
        code_item=lambda _: SimpleNamespace(instructions=instructions),
    )


def test_wire_extractors_skip_invalid_string_operands() -> None:
    oneof_dex = _wire_dex_with_code("<init>", (0x001A, 99))
    names_dex = _wire_dex_with_code("toString", (0x001A, 99))
    assert extract_wire_oneofs(oneof_dex, {"Lexample/Record;"}) == ()
    assert extract_wire_names(names_dex) == ()


def test_wire_syntax_extraction_skips_invalid_field_operand() -> None:
    dex = _wire_dex_with_code("<clinit>", (0x0060, 99))
    assert extract_wire_syntaxes(dex, {"Lexample/Record;"}) == {}


def test_extract_wire_syntaxes_resolves_single_consistent_value() -> None:
    owner = DexClass(0, 0, 0xFFFFFFFF, 0, 0, 0, 0, 0)
    field = DexField(1, 1, 0)
    dex: Any = SimpleNamespace(
        classes=(owner,),
        types=("Lexample/Record;", "Lcom/squareup/wire/Syntax;"),
        strings=("PROTO2",),
        fields=(field,),
        class_methods=lambda _: (SimpleNamespace(code_offset=1, method_index=0),),
        method_name=lambda _: "<clinit>",
        code_item=lambda _: SimpleNamespace(instructions=(0x0060, 0)),
        field_name=lambda f: "PROTO2",
    )

    assert extract_wire_syntaxes(dex, {"Lexample/Record;"}) == {
        "Lexample/Record;": "proto2"
    }


def test_extract_wire_syntaxes_skips_ambiguous_values() -> None:
    owner = DexClass(0, 0, 0xFFFFFFFF, 0, 0, 0, 0, 0)
    field_a = DexField(1, 1, 0)
    field_b = DexField(1, 1, 1)
    dex: Any = SimpleNamespace(
        classes=(owner,),
        types=("Lexample/Record;", "Lcom/squareup/wire/Syntax;"),
        strings=("PROTO2", "PROTO3"),
        fields=(field_a, field_b),
        class_methods=lambda _: (SimpleNamespace(code_offset=1, method_index=0),),
        method_name=lambda _: "<clinit>",
        code_item=lambda _: SimpleNamespace(instructions=(0x0060, 0, 0x0161, 1)),
        field_name=lambda f: "PROTO2" if f is field_a else "PROTO3",
    )

    assert extract_wire_syntaxes(dex, {"Lexample/Record;"}) == {}


def test_extract_wire_oneofs_skips_unselected_owners_and_methods() -> None:
    owner = DexClass(0, 0, 0xFFFFFFFF, 0, 0, 0, 0, 0)
    other = SimpleNamespace(code_offset=1, method_index=0)
    no_code = SimpleNamespace(code_offset=0, method_index=0)
    dex: Any = SimpleNamespace(
        classes=(owner,),
        types=("Lexample/Record;",),
        strings=(),
        fields=(),
        class_methods=lambda _: (other, no_code),
        method_name=lambda m: "toString" if m is other else "<init>",
        code_item=lambda _: SimpleNamespace(instructions=()),
    )

    assert extract_wire_oneofs(dex, set()) == ()
    assert extract_wire_oneofs(dex, {"Lexample/Record;"}) == ()


def test_extract_wire_syntaxes_skips_unselected_owners_methods_and_opcodes() -> None:
    owner = DexClass(0, 0, 0xFFFFFFFF, 0, 0, 0, 0, 0)
    field = DexField(1, 1, 0)
    non_init = SimpleNamespace(code_offset=1, method_index=0)
    clinit = SimpleNamespace(code_offset=1, method_index=0)
    # a leading const (opcode 0x12) exercises the non-sput opcode skip.
    code = (0x0012, 0x0060, 0)
    dex: Any = SimpleNamespace(
        classes=(owner,),
        types=("Lexample/Record;", "Lcom/squareup/wire/Syntax;"),
        strings=("PROTO2",),
        fields=(field,),
        class_methods=lambda _: (non_init,),
        method_name=lambda m: "toString",
        code_item=lambda _: SimpleNamespace(instructions=code),
        field_name=lambda f: "PROTO2",
    )
    clinit_dex: Any = SimpleNamespace(
        classes=(owner,),
        types=("Lexample/Record;", "Lcom/squareup/wire/Syntax;"),
        strings=("PROTO2",),
        fields=(field,),
        class_methods=lambda _: (clinit,),
        method_name=lambda m: "<clinit>",
        code_item=lambda _: SimpleNamespace(instructions=code),
        field_name=lambda f: "PROTO2",
    )

    assert extract_wire_syntaxes(dex, set()) == {}
    assert extract_wire_syntaxes(dex, {"Lexample/Record;"}) == {}
    assert extract_wire_syntaxes(clinit_dex, {"Lexample/Record;"}) == {
        "Lexample/Record;": "proto2"
    }


def test_extract_wire_oneofs_requires_more_than_one_field() -> None:
    owner = DexClass(0, 0, 0xFFFFFFFF, 0, 0, 0, 0, 0)
    method = SimpleNamespace(code_offset=1, method_index=0)
    strings = (
        "At most one of a, b may be non-null",
        "At most one of a may be non-null",
    )
    dex: Any = SimpleNamespace(
        classes=(owner,),
        types=("Lexample/Record;",),
        strings=strings,
        fields=(),
        class_methods=lambda _: (method,),
        method_name=lambda _: "<init>",
        code_item=lambda _: SimpleNamespace(instructions=(0x001A, 0)),
    )
    single_field_dex: Any = SimpleNamespace(
        classes=(owner,),
        types=("Lexample/Record;",),
        strings=strings,
        fields=(),
        class_methods=lambda _: (method,),
        method_name=lambda _: "<init>",
        code_item=lambda _: SimpleNamespace(instructions=(0x011A, 1)),
    )

    matches = extract_wire_oneofs(dex, {"Lexample/Record;"})
    assert matches == (WireOneofFinding("Lexample/Record;", ("a", "b"), 0),)
    assert extract_wire_oneofs(single_field_dex, {"Lexample/Record;"}) == ()


def test_extract_wire_names_finds_nearest_preceding_field_read() -> None:
    owner = DexClass(0, 0, 0xFFFFFFFF, 0, 0, 0, 0, 0)
    method = SimpleNamespace(code_offset=1, method_index=0)
    other = SimpleNamespace(code_offset=2, method_index=1)
    field = DexField(0, 0, 0)
    code = (0x001A, 0, 0x0054, 0, 0x031A, 1)
    dex: Any = SimpleNamespace(
        classes=(owner,),
        types=("Lexample/Record;",),
        strings=("Record{", "title="),
        fields=(field,),
        methods=(),
        class_methods=lambda _: (other, method),
        method_name=lambda m: "toString" if m is method else "equals",
        code_item=lambda _: SimpleNamespace(instructions=code),
    )

    finding = extract_wire_names(dex)[0]

    assert (finding.field, finding.name, finding.message_name) == (
        field,
        "title",
        "Record",
    )


def test_resolves_wire_adapter_types() -> None:
    assert wire_adapter_type("com.squareup.wire.ProtoAdapter#SINT64") == "sint64"
    assert wire_adapter_type("example.Outer$Inner#ADAPTER") == ".example.Outer.Inner"
    assert wire_adapter_type("example.Custom#OTHER") is None
    assert wire_adapter_type("no-separator") is None


def test_string_rejects_non_index_and_out_of_range_values() -> None:
    dex: Any = SimpleNamespace(strings=("only",))
    assert _string(dex, "not-an-index") is None
    assert _string(dex, 5) is None
    assert _string(dex, 0) == "only"


def test_label_falls_back_to_optional_for_unknown_values() -> None:
    dex: Any = _wire_dex()
    assert _label(dex, "not-an-index") == "optional"
    assert _label(dex, 99) == "optional"
    assert _label(dex, 0) == "optional"  # dex.fields[0] is named "title"


def test_wire_dex_type_rejects_out_of_range_fields() -> None:
    dex: Any = _wire_dex()
    assert wire_dex_type(dex, -1, 1) is None
    assert wire_dex_type(dex, 0, 99) is None


def test_wire_decoder_skips_stale_field_findings() -> None:
    dex: Any = _wire_dex()
    stale = DexField(0, 2, 99)
    finding = WireAdapterFinding("Lexample/Record;", stale, 1, dex.fields[1], 0, 0)
    assert decode_wire_adapter_fields(dex, (finding,), (), (), "test.dex") == {}


def test_decodes_annotated_wire_message() -> None:
    dex = _wire_dex()
    schemas = decode_wire_annotations(dex, extract_wire_annotations(dex), "classes.dex")

    assert [(item.package, item.name) for item in schemas] == [
        ("example", "Record.proto")
    ]
    field = schemas[0].messages[0].fields[0]
    assert (field.name, field.number, field.type_name) == ("title", 7, "string")


def test_decodes_proven_wire_scalar_presence() -> None:
    dex = _wire_dex()
    finding = extract_wire_annotations(dex)[0]
    finding = WireFieldFinding(
        finding.owner, finding.field, 7, "x#STRING", "optional", None, 2
    )
    schema = decode_wire_annotations(
        dex,
        (finding,),
        "classes.dex",
        {finding.owner: "proto3"},
        {finding.owner: frozenset({2})},
    )[0]

    assert schema.messages[0].fields[0].proto3_optional


def test_decodes_empty_wire_message() -> None:
    schema = decode_wire_messages(("Lexample/Outer$Empty;",), "classes.dex")[0]

    assert (schema.package, schema.name) == ("example", "Outer_Empty.proto")
    assert schema.messages[0].name == "Outer_Empty"
    assert schema.messages[0].fields == []


def test_decodes_wire_constants() -> None:
    assert _constant(_Instruction(0, 0x12, (0xE312,))) == (3, -2)
    assert _constant(_Instruction(0, 0x13, (0x0213, 0xFFFE))) == (2, -2)
    assert _constant(_Instruction(0, 0x15, (0x0215, 0x0080))) == (2, 0x800000)
    assert _constant(_Instruction(0, 0x14, (0x0114, 1, 0))) == (1, 1)
    assert _constant(_Instruction(0, 0x14, (0x0214, 0xFFFF, 0xFFFF))) == (2, -1)


def test_maps_wide_constructor_parameters_to_registers() -> None:
    assert _parameter_registers(12, 8, ("I", "J", "Ljava/lang/String;", "I")) == (
        5,
        6,
        8,
        9,
    )


def test_tracks_constructor_moves() -> None:
    registers: dict[int, object] = {9: 7}
    assert _constructor_move(registers, _Instruction(0, 0x02, (0x0102, 9)))
    assert registers == {1: 7, 9: 7}


def test_tracks_constructor_masks() -> None:
    registers: dict[int, object] = {3: -1}
    instruction = _Instruction(0, 0xDD, (0x01DD, 0x0403))
    assert _constructor_and(registers, instruction)
    assert registers[1] == 4


def test_constructor_move_clears_destination_on_unknown_source() -> None:
    # dest=9 (reg bits 8-11), source=2 (reg bits 12-15) which has no known value.
    registers: dict[int, object] = {9: "stale"}
    assert _constructor_move(registers, _Instruction(0, 0x01, (0x2900,)))
    assert 9 not in registers


def test_constructor_move_handles_wide_16bit_form() -> None:
    registers: dict[int, object] = {2: 11}
    instruction = _Instruction(0, 0x03, (0, 1, 2))
    assert _constructor_move(registers, instruction)
    assert registers[1] == 11


def test_constructor_and_reads_register_operands() -> None:
    registers: dict[int, object] = {0: 6, 1: 3}
    instruction = _Instruction(0, 0x95, (0x0200, 0x0100))
    assert _constructor_and(registers, instruction)
    assert registers[2] == 2


def test_constructor_and_clears_destination_on_non_int_operand() -> None:
    registers: dict[int, object] = {2: 5, 3: 4}
    instruction = _Instruction(0, 0x95, (0x0500, 0x0302))
    del registers[2]
    assert _constructor_and(registers, instruction)
    assert 5 not in registers


def test_constructor_value_marks_non_null_string_and_type_loads() -> None:
    registers: dict[int, object] = {}
    assert _constructor_value(registers, _Instruction(0, 0x1A, (0x0100, 0)))
    assert registers[1] is not None


def test_constructor_arguments_rejects_out_of_range_and_default_delegate() -> None:
    target = object()
    default_target = object()
    dex: Any = SimpleNamespace(
        methods=(target, default_target),
        method_name=lambda m: "<init>",
        method_parameter_types=(
            lambda m: ("I",) if m is target else ("I", _DEFAULT_MARKER)
        ),
    )
    out_of_range = _Instruction(0, 0x70, (0x1070, 5, 0))
    to_default = _Instruction(0, 0x70, (0x1070, 1, 0))

    assert _constructor_arguments(dex, out_of_range, {}) is None
    assert _constructor_arguments(dex, to_default, {}) is None


def test_constructor_arguments_rejects_too_few_argument_registers() -> None:
    target = object()
    dex: Any = SimpleNamespace(
        methods=(target,),
        method_name=lambda m: "<init>",
        method_parameter_types=lambda m: ("Ljava/lang/String;", "I"),
    )
    # a single-argument invoke can't satisfy two constructor parameters.
    invoke = _Instruction(0, 0x70, (0x1070, 0, 0))

    assert _constructor_arguments(dex, invoke, {}) is None


def test_default_constructor_state_requires_marker_suffix() -> None:
    method: Any = SimpleNamespace(code_offset=1)
    dex: Any = SimpleNamespace(
        method_parameter_types=lambda _: ("I",),
        code_item=lambda _: SimpleNamespace(
            registers_size=4, ins_size=3, instructions=()
        ),
    )

    assert _default_constructor_state(dex, method) is None


def test_tracks_constructor_constants() -> None:
    registers: dict[int, object] = {}
    assert _constructor_value(registers, _Instruction(0, 0x12, (0x0012,)))
    assert registers[0] == 0


def test_reads_null_constructor_arguments() -> None:
    target = object()
    dex: Any = SimpleNamespace(
        methods=(target,),
        method_name=lambda _: "<init>",
        method_parameter_types=lambda _: ("Ljava/lang/String;", "I"),
    )
    invoke = _Instruction(0, 0x70, (0x3070, 0, 0x0210))

    assert _constructor_arguments(dex, invoke, {1: 0}) == (0,)


def test_seeds_default_constructor_masks() -> None:
    method: Any = SimpleNamespace(code_offset=1)
    dex: Any = SimpleNamespace(
        method_parameter_types=lambda _: (
            "I",
            "Lkotlin/jvm/internal/DefaultConstructorMarker;",
        ),
        code_item=lambda _: SimpleNamespace(
            registers_size=4, ins_size=3, instructions=()
        ),
    )

    assert _default_constructor_state(dex, method) == ((), {2: -1})


def test_resolves_wire_dex_types() -> None:
    fields = (DexField(0, 0, 0), DexField(1, 1, 1))
    dex: Any = SimpleNamespace(
        fields=fields,
        types=("Z", "Lcom/squareup/wire/ProtoAdapter;"),
        field_name=lambda field: "BOOL" if field is fields[1] else "value",
    )

    assert wire_dex_type(dex, 0, 1) == "bool"


def test_recovers_standard_enum_constructor() -> None:
    descriptor = "Lexample/Mode;"
    target = object()
    field = DexField(0, 0, 0)
    dex: Any = SimpleNamespace(
        methods=(target,),
        fields=(field,),
        types=(descriptor,),
        strings=("UNUSED",),
        code_item=lambda _: SimpleNamespace(
            instructions=(0x0022, 0, 0x011A, 0, 0x0212, 0x3070, 0, 0x0210, 0x0069, 0)
        ),
        method_name=lambda _: "<init>",
        method_parameter_types=lambda _: ("Ljava/lang/String;", "I"),
        field_name=lambda _: "UNUSED",
    )
    method: Any = SimpleNamespace(code_offset=1, method_index=7)

    finding = _wire_enum_method(dex, method, descriptor)

    assert finding is not None
    assert finding.values == (("UNUSED", 0),)


def test_move_register_handles_16bit_and_wide_forms() -> None:
    registers: dict[int, object] = {9: "value"}
    assert _move_register(registers, _Instruction(0, 0x02, (0x0100, 9)))
    assert registers[1] == "value"

    registers = {2: "wide"}
    assert _move_register(registers, _Instruction(0, 0x03, (0, 1, 2)))
    assert registers[1] == "wide"


def test_wire_enum_method_moves_registers_and_second_parameter_shape() -> None:
    descriptor = "Lexample/Mode;"
    target = object()
    field = DexField(9, 9, 0)
    code = (
        0x0507,  # move-object v5, v0 (unused, exercises _move_register)
        0x0022,
        0,  # new-instance v0, descriptor
        0x021B,
        0,
        0,  # const-string/jumbo v2, "FOO"
        0x9312,  # const/4 v3, unused
        0x7412,  # const/4 v4, #7 (number)
        0x4070,
        0,
        0x4320,  # invoke-direct {v0,v2,v3,v4}, target
        0x0069,
        0,  # sput-object v0, field@0
    )
    dex: Any = SimpleNamespace(
        methods=(target,),
        fields=(field,),
        types=(descriptor,),
        strings=("FOO",),
        code_item=lambda _: SimpleNamespace(instructions=code),
        method_name=lambda _: "<init>",
        method_parameter_types=lambda _: ("Ljava/lang/String;", "I", "I"),
        field_name=lambda _: "FOO",
    )
    method: Any = SimpleNamespace(code_offset=1, method_index=3)

    finding = _wire_enum_method(dex, method, descriptor)

    assert finding is not None
    assert finding.values == (("FOO", 7),)


def test_wire_enum_method_third_parameter_shape() -> None:
    descriptor = "Lexample/Mode;"
    target = object()
    field = DexField(9, 9, 0)
    code = (
        0x0022,
        0,  # new-instance v0
        0x9312,  # const/4 v3, unused
        0x7412,  # const/4 v4, #7 (number)
        0x011B,
        0,
        0,  # const-string/jumbo v1, "BAR" (name)
        0x4070,
        0,
        0x1430,  # invoke-direct {v0,v3,v4,v1}, target
        0x0069,
        0,  # sput-object v0, field@0
    )
    dex: Any = SimpleNamespace(
        methods=(target,),
        fields=(field,),
        types=(descriptor,),
        strings=("BAR",),
        code_item=lambda _: SimpleNamespace(instructions=code),
        method_name=lambda _: "<init>",
        method_parameter_types=lambda _: ("I", "I", "Ljava/lang/String;"),
        field_name=lambda _: "BAR",
    )
    method: Any = SimpleNamespace(code_offset=1, method_index=0)

    finding = _wire_enum_method(dex, method, descriptor)

    assert finding is not None
    assert finding.values == (("BAR", 7),)


def test_wire_enum_method_skips_unrecoverable_evidence() -> None:
    descriptor = "Lexample/Mode;"
    method: Any = SimpleNamespace(code_offset=1, method_index=0)

    # jumbo const-string index out of range.
    dex_bad_string: Any = SimpleNamespace(
        methods=(),
        fields=(),
        types=(descriptor,),
        strings=(),
        code_item=lambda _: SimpleNamespace(instructions=(0x021B, 99, 0)),
        method_name=lambda _: "<init>",
        method_parameter_types=lambda _: (),
    )
    assert _wire_enum_method(dex_bad_string, method, descriptor) is None

    # invoke method index out of range.
    dex_bad_method: Any = SimpleNamespace(
        methods=(),
        fields=(),
        types=(descriptor,),
        strings=(),
        code_item=lambda _: SimpleNamespace(instructions=(0x1070, 99, 0)),
        method_name=lambda _: "<init>",
        method_parameter_types=lambda _: (),
    )
    assert _wire_enum_method(dex_bad_method, method, descriptor) is None

    # invoke target is not a constructor.
    other_target = object()
    dex_not_init: Any = SimpleNamespace(
        methods=(other_target,),
        fields=(),
        types=(descriptor,),
        strings=(),
        code_item=lambda _: SimpleNamespace(instructions=(0x1070, 0, 0)),
        method_name=lambda _: "other",
        method_parameter_types=lambda _: (),
    )
    assert _wire_enum_method(dex_not_init, method, descriptor) is None

    # argument count doesn't match the constructor's parameters.
    mismatched_target = object()
    dex_mismatch: Any = SimpleNamespace(
        methods=(mismatched_target,),
        fields=(),
        types=(descriptor,),
        strings=(),
        code_item=lambda _: SimpleNamespace(instructions=(0x1070, 0, 0)),
        method_name=lambda _: "<init>",
        method_parameter_types=lambda _: ("I", "I"),
    )
    assert _wire_enum_method(dex_mismatch, method, descriptor) is None

    # constructor parameter shape isn't one of the recognized ones.
    unrecognized_target = object()
    dex_unrecognized: Any = SimpleNamespace(
        methods=(unrecognized_target,),
        fields=(),
        types=(descriptor,),
        strings=(),
        code_item=lambda _: SimpleNamespace(
            instructions=(0x0022, 0, 0x3070, 0, 0x0210)
        ),
        method_name=lambda _: "<init>",
        method_parameter_types=lambda _: ("I", "I"),
    )
    assert _wire_enum_method(dex_unrecognized, method, descriptor) is None

    # sput field index out of range.
    dex_bad_field: Any = SimpleNamespace(
        methods=(),
        fields=(),
        types=(descriptor,),
        strings=(),
        code_item=lambda _: SimpleNamespace(instructions=(0x0069, 99)),
        method_name=lambda _: "<init>",
        method_parameter_types=lambda _: (),
    )
    assert _wire_enum_method(dex_bad_field, method, descriptor) is None

    # sput to a field that isn't self-referential (class_index != type_index).
    other_field = DexField(1, 2, 0)
    dex_not_self_ref: Any = SimpleNamespace(
        methods=(),
        fields=(other_field,),
        types=(descriptor,),
        strings=(),
        code_item=lambda _: SimpleNamespace(instructions=(0x0069, 0)),
        method_name=lambda _: "<init>",
        method_parameter_types=lambda _: (),
    )
    assert _wire_enum_method(dex_not_self_ref, method, descriptor) is None


def test_decodes_adapter_write_evidence() -> None:
    model = DexField(0, 1, 0)
    adapter = DexField(2, 3, 1)
    dex: Any = SimpleNamespace(
        fields=(model, adapter),
        types=("Lexample/Record;", "Ljava/lang/String;", "Lwire/Adapters;"),
        strings=("value", "STRING"),
        field_name=lambda field: "value" if field is model else "STRING",
    )
    finding = WireAdapterFinding(
        "Lexample/Record;", model, 4, adapter, 7, 12, "repeated", True
    )
    names = (WireNameFinding("Lexample/Record;", model, "items", "Record"),)
    oneofs = (WireOneofFinding("Lexample/Record;", ("items", "other"), 8),)

    fields = decode_wire_adapter_fields(dex, (finding,), names, oneofs, "classes.dex")

    field = fields["Lexample/Record;"][0]
    assert (field.name, field.number, field.type_name) == ("items", 4, "string")
    assert (field.label, field.packed, field.oneof) == (
        "repeated",
        True,
        "choice_0",
    )


def test_decodes_wire_adapter_schema() -> None:
    model = DexField(0, 1, 0)
    adapter = DexField(2, 3, 1)
    owner = "Lexample/Record;"
    dex: Any = SimpleNamespace(
        fields=(model, adapter),
        types=(owner, "Ljava/lang/String;", "Lwire/Adapters;"),
        field_name=lambda field: "title" if field is model else "STRING",
    )
    finding = WireAdapterFinding(owner, model, 4, adapter, 7, 12)

    schemas = decode_wire_adapters(
        dex, (finding,), (), (), "classes.dex", {owner: "proto3"}
    )

    assert (schemas[0].package, schemas[0].syntax) == ("example", "proto3")
    assert schemas[0].messages[0].name == "Record"
    assert schemas[0].messages[0].fields[0].name == "title"


def test_decodes_wire_enum_with_uniform_package_syntax() -> None:
    finding = WireEnumFinding("Lexample/Mode;", (("UNKNOWN", 0), ("ON", 1)), 3)

    schemas, lineage = decode_wire_enums(
        (finding,), "classes.dex", {"Lexample/Other;": "proto3"}
    )

    assert (schemas[0].package, schemas[0].syntax) == ("example", "proto3")
    assert schemas[0].enums[0].name == "Mode"
    assert lineage[("example", "Mode.proto")] == {"Mode": None}


def test_decodes_wire_enum_falls_back_when_package_syntax_ambiguous() -> None:
    finding = WireEnumFinding("Lexample/Mode;", (("UNKNOWN", 0),), 3)

    schemas, _ = decode_wire_enums(
        (finding,),
        "classes.dex",
        {"Lexample/A;": "proto3", "Lexample/B;": "proto2"},
    )

    assert schemas[0].syntax == "proto2"


def test_decodes_nested_wire_enum_uses_parent_syntax() -> None:
    finding = WireEnumFinding("Lexample/Outer$Mode;", (("UNKNOWN", 0),), 3)

    schemas, lineage = decode_wire_enums(
        (finding,), "classes.dex", {"Lexample/Outer;": "proto3"}
    )

    assert schemas[0].syntax == "proto3"
    assert lineage[("example", "Outer_Mode.proto")] == {"Outer_Mode": "Lexample/Outer;"}


def test_default_constructor_nulls_falls_through_populated_default() -> None:
    # -1 mask (all defaults requested) plus a real invoke passed a null string arg.
    real_ctor = object()
    code = (0x0438, 0x0002, 0x0212, 0x2070, 0, 0x0023)
    dex: Any = SimpleNamespace(
        methods=(real_ctor,),
        method_name=lambda m: "<init>",
        method_parameter_types=(
            lambda m: (
                ("Ljava/lang/String;", "I", _DEFAULT_MARKER)
                if m is not real_ctor
                else ("Ljava/lang/String;",)
            )
        ),
        code_item=lambda off: SimpleNamespace(
            registers_size=6, ins_size=4, instructions=code
        ),
    )
    method: Any = SimpleNamespace(code_offset=1, method_index=0)

    assert _default_constructor_nulls(dex, method) == (0,)


def test_default_constructor_nulls_returns_empty_without_marker_suffix() -> None:
    dex: Any = SimpleNamespace(
        methods=(object(),),
        method_name=lambda m: "<init>",
        method_parameter_types=lambda m: ("Ljava/lang/String;",),
        code_item=lambda off: SimpleNamespace(
            registers_size=1, ins_size=1, instructions=()
        ),
    )
    method: Any = SimpleNamespace(code_offset=1, method_index=0)

    assert _default_constructor_nulls(dex, method) == ()


def test_default_constructor_nulls_gives_up_after_budget_exhausted() -> None:
    # a self-targeting goto keeps the scan looping until its budget runs out.
    dex: Any = SimpleNamespace(
        methods=(object(),),
        method_name=lambda m: "<init>",
        method_parameter_types=lambda m: (_DEFAULT_MARKER,),
        code_item=lambda off: SimpleNamespace(
            registers_size=1, ins_size=1, instructions=(0x0028,)
        ),
    )
    method: Any = SimpleNamespace(code_offset=1, method_index=0)

    assert _default_constructor_nulls(dex, method) == ()


def test_default_constructor_nulls_stops_on_non_int_mask() -> None:
    dex: Any = SimpleNamespace(
        methods=(object(),),
        method_name=lambda m: "<init>",
        method_parameter_types=lambda m: (_DEFAULT_MARKER,),
        code_item=lambda off: SimpleNamespace(
            registers_size=1, ins_size=1, instructions=(0x0038, 0x0000)
        ),
    )
    method: Any = SimpleNamespace(code_offset=1, method_index=0)

    assert _default_constructor_nulls(dex, method) == ()


def test_default_constructor_nulls_follows_branch_and_goto() -> None:
    real_ctor = object()
    code = (0x04DD, 0x0004, 0x0438, 0x0002, 0x0128, 0x0212, 0x2070, 0, 0x0023)
    dex: Any = SimpleNamespace(
        methods=(real_ctor,),
        method_name=lambda m: "<init>",
        method_parameter_types=(
            lambda m: (
                ("Ljava/lang/String;", "I", _DEFAULT_MARKER)
                if m is not real_ctor
                else ("Ljava/lang/String;",)
            )
        ),
        code_item=lambda off: SimpleNamespace(
            registers_size=6, ins_size=4, instructions=code
        ),
    )
    method: Any = SimpleNamespace(code_offset=1, method_index=0)

    assert _default_constructor_nulls(dex, method) == (0,)


def test_default_constructor_nulls_exhausts_without_a_match() -> None:
    dex: Any = SimpleNamespace(
        methods=(object(),),
        method_name=lambda m: "<init>",
        method_parameter_types=lambda m: (_DEFAULT_MARKER,),
        code_item=lambda off: SimpleNamespace(
            registers_size=1, ins_size=1, instructions=(0x000A,)
        ),
    )
    method: Any = SimpleNamespace(code_offset=1, method_index=0)

    assert _default_constructor_nulls(dex, method) == ()


def test_extract_wire_null_defaults_filters_by_owner_and_marker() -> None:
    real_ctor = object()
    code = (0x0438, 0x0002, 0x0212, 0x2070, 0, 0x0023)
    default_ctor: Any = SimpleNamespace(code_offset=1, method_index=0)
    plain_ctor: Any = SimpleNamespace(code_offset=2, method_index=1)
    owner = DexClass(0, 0, 0xFFFFFFFF, 0, 0, 0, 0, 0)
    other = DexClass(1, 0, 0xFFFFFFFF, 0, 0, 0, 0, 0)

    def code_item(offset: int) -> Any:
        if offset == 1:
            return SimpleNamespace(registers_size=6, ins_size=4, instructions=code)
        return SimpleNamespace(registers_size=1, ins_size=1, instructions=())

    dex: Any = SimpleNamespace(
        types=("Lexample/Record;", "Lexample/Other;"),
        classes=(owner, other),
        methods=(real_ctor,),
        class_methods=(
            lambda item: (default_ctor,) if item is owner else (plain_ctor,)
        ),
        method_name=lambda m: "<init>",
        method_parameter_types=(
            lambda m: (
                ("Ljava/lang/String;", "I", _DEFAULT_MARKER)
                if m is default_ctor
                else ("Ljava/lang/String;",)
            )
        ),
        code_item=code_item,
    )

    result = extract_wire_null_defaults(dex, {"Lexample/Record;"})

    assert result == {"Lexample/Record;": frozenset({0})}


def test_decodes_boxed_adapter_presence() -> None:
    model = DexField(0, 1, 0)
    adapter = DexField(2, 3, 1)
    owner = "Lexample/Record;"
    dex: Any = SimpleNamespace(
        fields=(model, adapter),
        types=(owner, "Ljava/lang/Integer;", "Lwire/Adapters;"),
        field_name=lambda field: "count" if field is model else "INT32",
    )
    finding = WireAdapterFinding(owner, model, 4, adapter, 7, 12)

    fields = decode_wire_adapter_fields(
        dex, (finding,), (), (), "classes.dex", {owner: "proto3"}
    )

    assert fields[owner][0].proto3_optional
