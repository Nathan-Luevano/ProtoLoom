from protoloom.model import (
    Confidence,
    EnumType,
    EnumValue,
    Evidence,
    Field,
    Message,
    RecoveredSchema,
)
from protoloom.reconcile import reconcile


def test_reconcile_prefers_higher_confidence_and_combines_evidence() -> None:
    low_evidence = Evidence("wire", "offset 10")
    high_evidence = Evidence("descriptor", "offset 20")
    low = RecoveredSchema(
        "record.proto",
        messages=[
            Message(
                "Record",
                [Field("field_1", 1, "bytes", Confidence.SPECULATIVE, [low_evidence])],
            )
        ],
    )
    high = RecoveredSchema(
        "record.proto",
        messages=[
            Message(
                "Record",
                [Field("identifier", 1, "string", Confidence.CERTAIN, [high_evidence])],
            )
        ],
    )

    result = reconcile([low, high])

    field = result.schemas[0].messages[0].fields[0]
    assert (field.name, field.type_name, field.confidence) == (
        "identifier",
        "string",
        Confidence.CERTAIN,
    )
    assert field.evidence == [low_evidence, high_evidence]
    assert {
        (item.attribute, item.kept, item.rejected) for item in result.conflicts
    } >= {
        ("name", "identifier", "field_1"),
        ("type_name", "string", "bytes"),
    }
    assert low.messages[0].fields[0].name == "field_1"


def test_reconcile_merges_nested_types_dependencies_and_enum_values() -> None:
    first = RecoveredSchema(
        "record.proto",
        dependencies=["first.proto"],
        messages=[
            Message(
                "Outer",
                messages=[Message("First")],
                enums=[EnumType("State", [EnumValue("UNKNOWN", 0)])],
            )
        ],
    )
    second = RecoveredSchema(
        "record.proto",
        dependencies=["first.proto", "second.proto"],
        messages=[
            Message(
                "Outer",
                messages=[Message("Second")],
                enums=[EnumType("State", [EnumValue("READY", 1)])],
            )
        ],
    )

    schema = reconcile([first, second]).schemas[0]

    assert schema.dependencies == ["first.proto", "second.proto"]
    assert [item.name for item in schema.messages[0].messages] == ["First", "Second"]
    assert schema.messages[0].enums[0].values == [
        EnumValue("UNKNOWN", 0),
        EnumValue("READY", 1),
    ]


def test_equal_confidence_keeps_first_result_deterministically() -> None:
    first = RecoveredSchema(
        "same.proto",
        messages=[Message("M", [Field("first", 1, "int32", Confidence.HIGH)])],
    )
    second = RecoveredSchema(
        "same.proto",
        messages=[Message("M", [Field("second", 1, "int64", Confidence.HIGH)])],
    )
    result = reconcile([first, second])
    assert result.schemas[0].messages[0].fields[0].name == "first"
    assert result.conflicts[0].kept_confidence is Confidence.HIGH
