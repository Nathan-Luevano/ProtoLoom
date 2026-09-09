import pytest

from protoloom.emit.proto import _unique_names, emit_proto
from protoloom.model import (
    Confidence,
    EnumType,
    EnumValue,
    Field,
    Message,
    RecoveredSchema,
)
from protoloom.validate.compile import compile_proto


def test_synthetic_zero_does_not_imply_allow_alias() -> None:
    # A proto2 enum recovered starting at a non-zero value needs a
    # synthetic zero member for proto3-style validity, but that synthetic
    # value doesn't collide with anything real -- allow_alias is only for
    # genuine number collisions.
    schema = RecoveredSchema(
        name="fixture",
        package="demo",
        syntax="proto3",
        enums=[EnumType("Mode", [EnumValue("ACTIVE", 1), EnumValue("IDLE", 2)])],
    )
    emitted = emit_proto(schema)

    assert "allow_alias" not in emitted
    assert "MODE_UNSPECIFIED = 0;" in emitted
    assert compile_proto(emitted).success


def test_proto2_enum_preserves_nonzero_first_value() -> None:
    schema = RecoveredSchema(
        name="fixture",
        syntax="proto2",
        enums=[EnumType("Mode", [EnumValue("ACTIVE", 1)])],
    )
    emitted = emit_proto(schema)
    assert "MODE_UNSPECIFIED" not in emitted
    assert "ACTIVE = 1;" in emitted
    assert compile_proto(emitted).success


@pytest.mark.parametrize("syntax", ["proto2", "proto3"])
def test_empty_enum_receives_compilable_zero_value(syntax: str) -> None:
    schema = RecoveredSchema(
        name="fixture",
        syntax=syntax,
        enums=[EnumType("Empty")],
    )
    emitted = emit_proto(schema)
    assert "EMPTY_UNSPECIFIED = 0;" in emitted
    assert compile_proto(emitted).success


def test_synthetic_zero_names_are_scoped_per_enum() -> None:
    # An unqualified UNSPECIFIED collides across sibling enums under
    # protoc's C++ scoping rules; scoping the name to the enum avoids it.
    schema = RecoveredSchema(
        name="fixture",
        package="demo",
        syntax="proto3",
        messages=[
            Message(
                "Card",
                enums=[
                    EnumType("Kind", [EnumValue("A", 1)]),
                    EnumType("Priority", [EnumValue("HIGH", 1)]),
                ],
            )
        ],
    )
    emitted = emit_proto(schema)

    assert "KIND_UNSPECIFIED = 0;" in emitted
    assert "PRIORITY_UNSPECIFIED = 0;" in emitted
    assert compile_proto(emitted).success


def test_sibling_enum_value_names_are_uniquified() -> None:
    schema = RecoveredSchema(
        name="fixture",
        package="demo",
        messages=[
            Message(
                "Card",
                enums=[
                    EnumType("Kind", [EnumValue("UNKNOWN", 0)]),
                    EnumType("Priority", [EnumValue("UNKNOWN", 0)]),
                ],
            )
        ],
    )
    emitted = emit_proto(schema)
    assert "UNKNOWN = 0;" in emitted
    assert "UNKNOWN_2 = 0;" in emitted
    assert compile_proto(emitted).success


def test_synthetic_zero_avoids_sibling_value_names() -> None:
    schema = RecoveredSchema(
        name="fixture",
        syntax="proto3",
        enums=[
            EnumType("Other", [EnumValue("STATE_UNSPECIFIED", 0)]),
            EnumType("State", [EnumValue("ACTIVE", 1)]),
        ],
    )
    emitted = emit_proto(schema)
    assert "STATE_UNSPECIFIED_2 = 0;" in emitted
    assert compile_proto(emitted).success


def test_sibling_declaration_names_are_uniquified() -> None:
    schema = RecoveredSchema(
        name="fixture",
        messages=[
            Message("Record-Type"),
            Message("Record_Type"),
            Message(
                "Outer",
                messages=[Message("Entry-Type"), Message("Entry_Type")],
                enums=[
                    EnumType("Entry Type", [EnumValue("FIRST", 0)]),
                    EnumType("Entry_Type", [EnumValue("SECOND", 0)]),
                ],
            ),
        ],
        enums=[EnumType("Record Type", [EnumValue("ROOT", 0)])],
    )
    emitted = emit_proto(schema)
    assert "message Record_Type {" in emitted
    assert "message Record_Type_2 {" in emitted
    assert "enum Record_Type_3 {" in emitted
    assert "message Entry_Type_2 {" in emitted
    assert "enum Entry_Type_3 {" in emitted
    assert "enum Entry_Type_4 {" in emitted
    assert compile_proto(emitted).success


def test_name_uniquification_skips_reserved_suffixes() -> None:
    names = _unique_names(
        ["Record", "Record", "Record", "Record_2"],
        "Recovered",
        {"Record_2", "Record_4"},
    )

    assert names == ["Record", "Record_3", "Record_5", "Record_2_2"]


def test_name_uniquification_scales_across_repeated_names() -> None:
    names = _unique_names(["Record"] * 5000, "Recovered")

    assert len(names) == 5000
    assert len(set(names)) == 5000
    assert names[:3] == ["Record", "Record_2", "Record_3"]
    assert names[-1] == "Record_5000"


def test_name_uniquification_tracks_sanitized_collisions() -> None:
    names = _unique_names(["A-B", "A_B", "A-B"], "Recovered")

    assert names == ["A_B", "A_B_2", "A_B_3"]


def test_many_oneof_groups_emit_in_stable_order() -> None:
    fields = [
        Field(
            f"field_{index}",
            index,
            "string",
            Confidence.HIGH,
            oneof=f"group_{1001 - index}",
        )
        for index in range(1, 1001)
    ]
    schema = RecoveredSchema("fixture", messages=[Message("Record", fields)])

    emitted = emit_proto(schema)

    assert emitted.count("  oneof group_") == 1000
    assert emitted.index("oneof group_1 {") < emitted.index("oneof group_2 {")
    assert "string field_1000 = 1000;" in emitted


def test_type_references_follow_uniquified_declarations() -> None:
    schema = RecoveredSchema(
        name="fixture",
        package="demo",
        messages=[
            Message(
                "Record-Type",
                fields=[
                    Field("second", 2, ".demo.Record_Type", Confidence.CERTAIN),
                ],
            ),
            Message("Record_Type"),
        ],
    )
    emitted = emit_proto(schema)
    assert ".demo.Record_Type_2 second = 2;" in emitted
    assert compile_proto(emitted).success


def test_duplicate_field_numbers_keep_highest_confidence() -> None:
    schema = RecoveredSchema(
        name="fixture",
        messages=[
            Message(
                "Record",
                fields=[
                    Field("guess", 1, "bytes", Confidence.SPECULATIVE),
                    Field("proven", 1, "string", Confidence.CERTAIN),
                    Field("stable", 2, "bool", Confidence.HIGH),
                ],
            )
        ],
    )
    emitted = emit_proto(schema)
    assert "string proven = 1;" in emitted
    assert "guess" not in emitted
    assert emitted.index("proven") < emitted.index("stable")
    assert compile_proto(emitted).success


def test_proto3_normalizes_invalid_field_options() -> None:
    schema = RecoveredSchema(
        name="fixture",
        syntax="proto3",
        messages=[
            Message(
                "Record",
                fields=[
                    Field(
                        "value",
                        1,
                        "int32",
                        Confidence.CERTAIN,
                        label="required",
                        packed=True,
                    )
                ],
            )
        ],
    )
    emitted = emit_proto(schema)
    assert "required" not in emitted
    assert "packed" not in emitted
    assert compile_proto(emitted).success


def test_duplicate_imports_are_emitted_once() -> None:
    schema = RecoveredSchema(
        name="fixture",
        dependencies=["google/protobuf/empty.proto", "google/protobuf/empty.proto"],
    )
    emitted = emit_proto(schema)
    assert emitted.count('import "google/protobuf/empty.proto";') == 1


def test_nested_references_do_not_create_placeholders() -> None:
    schema = RecoveredSchema(
        name="fixture",
        messages=[
            Message(
                "Outer",
                fields=[Field("kind", 1, "Outer.Kind", Confidence.CERTAIN)],
                enums=[EnumType("Kind", [EnumValue("UNKNOWN", 0)])],
            )
        ],
    )

    emitted = emit_proto(schema)

    assert "message Outer_Kind" not in emitted
    assert compile_proto(emitted).success


def test_sanitized_names_remain_unique_and_compilable() -> None:
    schema = RecoveredSchema(
        name="fixture",
        package="bad-package.2part",
        messages=[
            Message(
                "Record-Type",
                fields=[
                    Field("a-b", 1, "string", Confidence.CERTAIN),
                    Field("a_b", 2, "string", Confidence.CERTAIN),
                    Field("choice", 3, "string", Confidence.CERTAIN, oneof="choice"),
                ],
                enums=[
                    EnumType(
                        "State",
                        [EnumValue("A-B", 0), EnumValue("A_B", 1)],
                    )
                ],
            )
        ],
    )

    emitted = emit_proto(schema)

    assert "package bad_package._2part;" in emitted
    assert "string a_b = 1;" in emitted
    assert "string a_b_2 = 2;" in emitted
    assert "oneof choice_2" in emitted
    assert "A_B_2 = 1;" in emitted
    assert compile_proto(emitted).success


def test_emits_escaped_proto2_string_default() -> None:
    schema = RecoveredSchema(
        name="fixture",
        syntax="proto2",
        messages=[
            Message(
                "Settings",
                fields=[
                    Field(
                        "label",
                        1,
                        "string",
                        Confidence.CERTAIN,
                        default_value='line "one"\nline two',
                    )
                ],
            )
        ],
    )
    emitted = emit_proto(schema)
    assert 'default = "line \\"one\\"\\nline two"' in emitted


def test_sanitizes_field_type_references() -> None:
    schema = RecoveredSchema(
        name="fixture",
        messages=[
            Message(
                "Holder",
                fields=[Field("item", 1, "Bad-Type", Confidence.CERTAIN)],
            )
        ],
    )
    emitted = emit_proto(schema)
    assert "Bad_Type item = 1;" in emitted
    assert compile_proto(emitted).success


def test_omits_invalid_numeric_default() -> None:
    schema = RecoveredSchema(
        name="fixture",
        syntax="proto2",
        messages=[
            Message(
                "Settings",
                fields=[
                    Field(
                        "count",
                        1,
                        "int32",
                        Confidence.CERTAIN,
                        default_value="1; option deprecated = true",
                    )
                ],
            )
        ],
    )
    emitted = emit_proto(schema)
    assert compile_proto(emitted).success


def test_proto_output_bounds_total_items() -> None:
    schema = RecoveredSchema(
        "fixture",
        messages=[Message("Record", [Field("value", 1, "string", Confidence.HIGH)])],
    )

    with pytest.raises(ValueError, match="exceeds 1 items"):
        emit_proto(schema, max_items=1)


def test_proto_output_bounds_message_depth() -> None:
    schema = RecoveredSchema("fixture", messages=[Message("Outer")])
    schema.messages[0].messages.append(Message("Inner"))

    with pytest.raises(ValueError, match="message depth 1"):
        emit_proto(schema, max_depth=1)


def test_proto_output_bounds_encoded_size() -> None:
    schema = RecoveredSchema("fixture", messages=[Message("Record")])

    with pytest.raises(ValueError, match="exceeds 10 bytes"):
        emit_proto(schema, max_bytes=10)


@pytest.mark.parametrize(
    "limits",
    [
        {"max_items": 0},
        {"max_depth": 0},
        {"max_bytes": 0},
    ],
)
def test_proto_output_rejects_nonpositive_limits(limits: dict[str, int]) -> None:
    with pytest.raises(ValueError, match="limits must be positive"):
        emit_proto(RecoveredSchema("fixture"), **limits)
