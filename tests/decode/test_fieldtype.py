import pytest

from protoloom.decode.fieldtype import FIELD_TYPES, field_type


def test_all_upstream_ids_are_mapped() -> None:
    assert set(FIELD_TYPES) == set(range(69))


@pytest.mark.parametrize(
    ("type_id", "proto_type", "label", "wire_type", "packed", "oneof"),
    [
        (0, "double", "optional", 1, False, False),
        (8, "string", "optional", 2, False, False),
        (17, "group", "optional", 3, False, False),
        (30, "enum", "repeated", 0, False, False),
        (35, "double", "repeated", 2, True, False),
        (43, "uint32", "repeated", 2, True, False),
        (48, "sint64", "repeated", 2, True, False),
        (50, "map", "repeated", 2, False, False),
        (60, "message", "optional", 2, False, True),
        (68, "group", "optional", 3, False, True),
    ],
)
def test_field_type_semantics(
    type_id: int, proto_type: str, label: str, wire_type: int, packed: bool, oneof: bool
) -> None:
    decoded = field_type(type_id)
    assert (decoded.proto_type, decoded.label, decoded.wire_type) == (
        proto_type,
        label,
        wire_type,
    )
    assert decoded.packed is packed
    assert decoded.oneof is oneof


def test_unknown_type_is_loud() -> None:
    with pytest.raises(ValueError, match="unknown"):
        field_type(69)
