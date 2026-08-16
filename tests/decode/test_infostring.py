import pytest

from protoloom.decode.infostring import (
    CHECK_INITIALIZED_BIT,
    HAS_HAS_BIT,
    ONEOF_TYPE_OFFSET,
    REQUIRED_BIT,
    UTF8_CHECK_BIT,
    InfoStringError,
    decode_info_string,
    decode_integers,
)


def encode_int(value: int) -> str:
    chars = []
    while value >= 0xD800:
        chars.append(chr((value & 0x1FFF) | 0xE000))
        value >>= 13
    chars.append(chr(value))
    return "".join(chars)


def encode(*values: int) -> str:
    return "".join(encode_int(value) for value in values)


def test_decodes_boundaries_exactly_like_raw_message_info() -> None:
    values = (0, 0xD7FF, 0xD800, 0xFFFF, 0x10000, 0xFFFFFFFF)
    assert decode_integers(encode(*values)) == values


def test_decodes_header_scalar_presence_and_oneof() -> None:
    raw_scalar = 8 | REQUIRED_BIT | UTF8_CHECK_BIT | CHECK_INITIALIZED_BIT | HAS_HAS_BIT
    raw_oneof = ONEOF_TYPE_OFFSET + 9
    info = encode(
        1,
        2,
        1,
        1,
        1,
        7,
        2,
        2,
        0,
        1,
        1,
        raw_scalar,
        31,
        7,
        raw_oneof,
        0,
    )

    decoded = decode_info_string(info)

    assert decoded.header.is_proto2
    assert decoded.header.check_initialized_count == 1
    assert decoded.fields[0].required
    assert decoded.fields[0].check_utf8
    assert decoded.fields[0].check_initialized
    assert decoded.fields[0].hasbits_index == 31
    assert decoded.fields[1].base_type_id == 9
    assert decoded.fields[1].oneof_index == 0


def test_zero_field_encoding_stops_after_count() -> None:
    decoded = decode_info_string(encode(4, 0))
    assert decoded.header.is_edition
    assert decoded.fields == ()


@pytest.mark.parametrize(
    ("values", "match"),
    [
        ((0, 0, 1), "trailing"),
        ((0, 1, 0, 0, 1, 1, 1, 0, 0, 0), "truncated"),
        ((0, 1, 0, 0, 1, 1, 2, 0, 0, 0, 1, 4), "entry count"),
        ((0, 1, 0, 0, 1, 1, 1, 0, 0, 0, 0, 4), "field number"),
        ((0, 1, 0, 0, 1, 1, 1, 0, 0, 0, 1, 69), "field type"),
    ],
)
def test_rejects_malformed_streams(values: tuple[int, ...], match: str) -> None:
    with pytest.raises(InfoStringError, match=match):
        decode_info_string(encode(*values))


def test_rejects_unterminated_integer() -> None:
    with pytest.raises(InfoStringError, match="unterminated"):
        decode_integers(chr(0xE000))
