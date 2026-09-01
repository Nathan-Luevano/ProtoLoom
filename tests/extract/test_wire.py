from types import SimpleNamespace
from typing import Any

from protoloom.container.dex import AnnotationItem, DexClass, DexField
from protoloom.decode.wire import (
    decode_wire_adapter_fields,
    decode_wire_annotations,
    wire_dex_type,
)
from protoloom.extract.lite import _Instruction
from protoloom.extract.wire import (
    WireAdapterFinding,
    WireNameFinding,
    WireOneofFinding,
    _constant,
    extract_wire_annotations,
    extract_wire_messages,
    wire_adapter_type,
)


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


def test_extracts_empty_wire_message() -> None:
    dex = _wire_dex()

    assert extract_wire_messages(dex) == ("Lexample/Record;",)


def test_resolves_wire_adapter_types() -> None:
    assert wire_adapter_type("com.squareup.wire.ProtoAdapter#SINT64") == "sint64"
    assert wire_adapter_type("example.Outer$Inner#ADAPTER") == ".example.Outer.Inner"
    assert wire_adapter_type("example.Custom#OTHER") is None


def test_decodes_annotated_wire_message() -> None:
    dex = _wire_dex()
    schemas = decode_wire_annotations(dex, extract_wire_annotations(dex), "classes.dex")

    assert [(item.package, item.name) for item in schemas] == [
        ("example", "Record.proto")
    ]
    field = schemas[0].messages[0].fields[0]
    assert (field.name, field.number, field.type_name) == ("title", 7, "string")


def test_decodes_wire_constants() -> None:
    assert _constant(_Instruction(0, 0x12, (0xE312,))) == (3, -2)
    assert _constant(_Instruction(0, 0x13, (0x0213, 0xFFFE))) == (2, -2)


def test_resolves_wire_dex_types() -> None:
    fields = (DexField(0, 0, 0), DexField(1, 1, 1))
    dex: Any = SimpleNamespace(
        fields=fields,
        types=("Z", "Lcom/squareup/wire/ProtoAdapter;"),
        field_name=lambda field: "BOOL" if field is fields[1] else "value",
    )

    assert wire_dex_type(dex, 0, 1) == "bool"


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
