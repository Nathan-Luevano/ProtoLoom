from types import SimpleNamespace

from protoloom.container.dex import AnnotationItem, DexClass, DexField
from protoloom.decode.wire import decode_wire_annotations
from protoloom.extract.wire import extract_wire_annotations, wire_adapter_type


def _wire_dex() -> SimpleNamespace:
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
