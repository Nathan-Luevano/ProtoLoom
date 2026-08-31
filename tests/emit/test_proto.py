from protoloom.emit.proto import emit_proto
from protoloom.model import EnumType, EnumValue, Message, RecoveredSchema
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
