import pytest

from protoloom.decode.names import (
    java_to_proto_name,
    names_are_obfuscated,
    recover_names,
    unpack_field_names,
)


@pytest.mark.parametrize(
    ("java", "proto"),
    [
        ("userId_", "user_id"),
        ("URLValue_", "url_value"),
        ("class_", "class_"),
        ("message_", "message_"),
        ("9patch_", "field_9patch"),
        ("bad$name_", "bad_name"),
    ],
)
def test_java_name_normalization(java: str, proto: str) -> None:
    assert java_to_proto_name(java) == proto


def test_obfuscation_requires_strict_majority() -> None:
    assert names_are_obfuscated(("a_", "bc_", "realName_"))
    assert not names_are_obfuscated(("a_", "realName_"))
    assert not names_are_obfuscated(())


def test_obfuscated_names_get_numbered_placeholders() -> None:
    names = recover_names(("a_", "b_", "realName_"), (2, 7, 9))
    assert [name.proto_name for name in names] == ["field_2", "field_7", "field_9"]
    assert all(name.obfuscated for name in names)


def test_unpacks_packed_and_separate_object_layouts() -> None:
    assert unpack_field_names(("Thing first_ second_",), 2) == ("first_", "second_")
    assert unpack_field_names(("first_", "second_", object()), 2) == (
        "first_",
        "second_",
    )


def test_bad_object_layout_is_loud() -> None:
    with pytest.raises(ValueError, match="expected 2"):
        unpack_field_names(("only_", object()), 2)
