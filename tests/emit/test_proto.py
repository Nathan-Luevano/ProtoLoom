from protoloom.emit.proto import emit_proto
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
