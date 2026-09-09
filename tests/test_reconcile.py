from copy import deepcopy as real_deepcopy

import pytest

from protoloom.model import (
    Confidence,
    EnumType,
    EnumValue,
    Evidence,
    Field,
    Message,
    RecoveredSchema,
)
from protoloom.reconcile import (
    Conflict,
    _merge_fields,
    _merge_named_enums,
    reconcile,
)


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


def test_field_replacement_uses_number_positions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    comparisons = 0

    def compare(left: Field, right: object) -> bool:
        nonlocal comparisons
        comparisons += 1
        return left is right

    monkeypatch.setattr(Field, "__eq__", compare)
    target = [
        Field(f"old_{index}", index, "bytes", Confidence.SPECULATIVE)
        for index in range(1, 1001)
    ]
    source = [
        Field(f"new_{index}", index, "string", Confidence.CERTAIN)
        for index in range(1, 1001)
    ]
    conflicts: list[Conflict] = []

    _merge_fields(target, source, "Record", conflicts)

    assert comparisons == 0
    assert target[0].name == "new_1"
    assert target[-1].name == "new_1000"
    assert len(conflicts) == 2000


def test_equal_confidence_enum_merges_do_not_recopy_accumulated_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    copied_lists = 0

    def counted(value: object) -> object:
        nonlocal copied_lists
        if isinstance(value, list):
            copied_lists += 1
        return real_deepcopy(value)

    monkeypatch.setattr("protoloom.reconcile.deepcopy", counted)
    target = [EnumType("State", [EnumValue("UNKNOWN", 0)], Confidence.HIGH)]
    conflicts: list[Conflict] = []

    for number in range(1, 1001):
        incoming = EnumType(
            "State",
            [EnumValue(f"VALUE_{number}", number)],
            Confidence.HIGH,
        )
        _merge_named_enums(target, [incoming], "Schema", conflicts)

    assert copied_lists == 0
    assert len(target[0].values) == 1001
    assert target[0].values[-1] == EnumValue("VALUE_1000", 1000)


def test_higher_confidence_enum_replaces_order_without_mutating_source() -> None:
    target = [EnumType("State", [EnumValue("OLD", 1)], Confidence.MEDIUM)]
    incoming = EnumType(
        "State",
        [EnumValue("NEW", 2)],
        Confidence.CERTAIN,
    )

    _merge_named_enums(target, [incoming], "Schema", [])

    assert target[0].values == [EnumValue("NEW", 2), EnumValue("OLD", 1)]
    assert incoming.values == [EnumValue("NEW", 2)]


def test_reconcile_bounds_schema_count() -> None:
    schemas = [RecoveredSchema("a"), RecoveredSchema("b")]
    with pytest.raises(ValueError, match="exceeds 1 schemas"):
        reconcile(schemas, max_schemas=1)


def test_reconcile_bounds_total_items() -> None:
    schema = RecoveredSchema(
        "a",
        messages=[Message("M", [Field("f", 1, "string", Confidence.HIGH)])],
    )
    with pytest.raises(ValueError, match="exceeds 2 items"):
        reconcile([schema], max_items=2)


def test_reconcile_bounds_message_depth() -> None:
    schema = RecoveredSchema("deep", messages=[Message("one")])
    schema.messages[0].messages.append(Message("two"))
    with pytest.raises(ValueError, match="message depth 1"):
        reconcile([schema], max_depth=1)


def test_reconcile_bounds_generated_conflicts() -> None:
    first = RecoveredSchema(
        "same",
        messages=[Message("M", [Field("old", 1, "bytes", Confidence.HIGH)])],
    )
    second = RecoveredSchema(
        "same",
        messages=[Message("M", [Field("new", 1, "string", Confidence.HIGH)])],
    )

    with pytest.raises(ValueError, match="exceeds 1 conflicts"):
        reconcile([first, second], max_conflicts=1)


@pytest.mark.parametrize(
    "limits",
    [
        {"max_schemas": 0},
        {"max_items": 0},
        {"max_depth": 0},
        {"max_conflicts": 0},
    ],
)
def test_reconcile_rejects_nonpositive_limits(limits: dict[str, int]) -> None:
    with pytest.raises(ValueError, match="limits must be positive"):
        reconcile([], **limits)
