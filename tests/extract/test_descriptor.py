import gzip
import zlib

import pytest
from google.protobuf.descriptor_pb2 import FileDescriptorProto

from protoloom.decode.descpb import decode_file_descriptor
from protoloom.emit.proto import emit_proto
from protoloom.extract.descriptor import (
    _candidate_name,
    _field_ends,
    _valid,
    scan_descriptors,
)
from protoloom.extract.gozip import scan_gzip_descriptors
from protoloom.validate.compile import compile_proto


def _descriptor() -> FileDescriptorProto:
    descriptor = FileDescriptorProto(name="example/test.proto", package="demo")
    descriptor.syntax = "proto3"
    message = descriptor.message_type.add(name="Greeting")
    field = message.field.add(name="text", number=1)
    field.label = field.LABEL_OPTIONAL
    field.type = field.TYPE_STRING
    return descriptor


def test_decode_file_descriptor_rejects_oversized_descriptor_early() -> None:
    # decode used to have no budget of its own, so a huge descriptor was
    # fully decoded into Field/Message objects before emit_proto's own
    # budget ever got a chance to reject it -- reject here, cheaply, first.
    descriptor = FileDescriptorProto(name="huge.proto")
    descriptor.syntax = "proto3"
    message = descriptor.message_type.add(name="Huge")
    for index in range(1, 5):
        field = message.field.add(name=f"f{index}", number=index)
        field.label = field.LABEL_OPTIONAL
        field.type = field.TYPE_INT32

    with pytest.raises(ValueError, match="descriptor decode exceeds"):
        decode_file_descriptor(
            descriptor, "fixture", "fixture@0x0", max_items=3, max_depth=100
        )


def test_scan_recovers_legacy_proto2_group_field() -> None:
    # TYPE_GROUP is dead wire format but still legal; a raw embedded
    # FileDescriptorProto using it must round-trip through scan/decode/emit
    # without silently downgrading it to a wire-incompatible message field.
    descriptor = FileDescriptorProto(name="example/legacy.proto", package="demo")
    descriptor.syntax = "proto2"
    message = descriptor.message_type.add(name="Outer")
    nested = message.nested_type.add(name="ResultGroup")
    inner = nested.field.add(name="url", number=1)
    inner.label = inner.LABEL_OPTIONAL
    inner.type = inner.TYPE_STRING
    group_field = message.field.add(name="result", number=2)
    group_field.label = group_field.LABEL_REPEATED
    group_field.type = group_field.TYPE_GROUP
    group_field.type_name = ".demo.Outer.ResultGroup"
    payload = descriptor.SerializeToString()

    findings = scan_descriptors(payload, "fixture")
    assert len(findings) == 1
    schema = decode_file_descriptor(findings[0].descriptor, "fixture", "fixture@0x0")
    emitted = emit_proto(schema)
    assert "group ResultGroup = 2 {" in emitted
    result = compile_proto(emitted)
    assert result.success, result.stderr


def test_scan_recovers_exact_descriptor_from_noise() -> None:
    expected = _descriptor()
    payload = expected.SerializeToString()
    findings = scan_descriptors(b"noise\x00" + payload + b"\xfftrailer", "fixture")
    assert len(findings) == 1
    assert findings[0].descriptor.SerializeToString() == payload
    assert findings[0].offset == 6


def test_scan_rejects_proto_name_without_schema() -> None:
    descriptor = FileDescriptorProto(name="empty.proto")
    assert scan_descriptors(descriptor.SerializeToString()) == []


def test_scan_keeps_both_versions_of_a_same_named_descriptor() -> None:
    # Two genuinely different descriptors sharing a .proto name (e.g. two
    # APK modules bundling different versions of a shared dependency) must
    # both survive so reconcile() can report the conflict, rather than one
    # silently losing to whichever happened to serialize longer.
    old = _descriptor()
    new = FileDescriptorProto(name=old.name, package=old.package)
    new.syntax = "proto3"
    message = new.message_type.add(name="Greeting")
    field = message.field.add(name="text", number=1)
    field.label = field.LABEL_OPTIONAL
    field.type = field.TYPE_STRING
    extra = message.field.add(name="extra_field", number=2)
    extra.label = extra.LABEL_OPTIONAL
    extra.type = extra.TYPE_INT32

    old_bytes = old.SerializeToString()
    new_bytes = new.SerializeToString()
    assert old_bytes != new_bytes
    findings = scan_descriptors(old_bytes + b"\x00\x00\x00" + new_bytes, "fixture")

    assert len(findings) == 2
    field_counts = sorted(len(f.descriptor.message_type[0].field) for f in findings)
    assert field_counts == [1, 2]


def test_scan_dedupes_byte_identical_descriptor_copies() -> None:
    payload = _descriptor().SerializeToString()
    findings = scan_descriptors(payload + b"\x00\x00\x00" + payload, "fixture")
    assert len(findings) == 1


def test_scan_falls_back_past_an_editions_descriptor_instead_of_surfacing_it() -> None:
    # A real protoc-emitted editions file sets syntax="editions" (edition
    # comes after message_type in field-number order); the scanner must not
    # surface that boundary as a find, since decode_file_descriptor raises
    # on unsupported syntax -- it should fall back to the shorter boundary
    # that ends before the syntax field is written, if that one is valid.
    editions = _descriptor()
    editions.syntax = "editions"
    from google.protobuf.descriptor_pb2 import EDITION_2023

    editions.edition = EDITION_2023

    findings = scan_descriptors(editions.SerializeToString(), "fixture")

    assert all(f.descriptor.syntax != "editions" for f in findings)


def test_descriptor_scan_bounds_candidates_before_valid_schema() -> None:
    decoy = FileDescriptorProto(name="decoy.proto").SerializeToString()
    expected = _descriptor().SerializeToString()
    data = decoy * 3 + expected

    assert scan_descriptors(data, max_candidates=3) == []
    assert len(scan_descriptors(data, max_candidates=4)) == 1


def test_descriptor_scan_bounds_wire_boundaries() -> None:
    data = b"\x08\x01" * 10

    assert len(_field_ends(data, 0, len(data), 3)) == 3
    assert len(_field_ends(data, 0, len(data), 20)) == 10


def test_field_ends_advances_past_fixed64_and_fixed32_fields() -> None:
    # tag 0x09 = field 1, wire type 1 (fixed64); tag 0x0D = field 1, wire 5 (fixed32).
    fixed64 = b"\x09" + b"\x00" * 8
    fixed32 = b"\x0d" + b"\x00" * 4

    assert _field_ends(fixed64, 0, len(fixed64), 10) == [9]
    assert _field_ends(fixed32, 0, len(fixed32), 10) == [5]


def test_field_ends_stops_on_zero_tag_and_truncated_tag_byte() -> None:
    assert _field_ends(b"\x00", 0, 1, 10) == []
    # a lone continuation-bit byte never terminates as a varint tag.
    assert _field_ends(b"\xff", 0, 1, 10) == []


def test_field_ends_stops_on_missing_varint_and_length_values() -> None:
    # wire type 0 (varint) with no following byte.
    assert _field_ends(b"\x08", 0, 1, 10) == []
    # wire type 2 (length-delimited) whose length varint is itself truncated.
    assert _field_ends(b"\x0a\xff", 0, 2, 10) == []


def test_candidate_name_rejects_unterminated_length_varint() -> None:
    # offset+1 onward is a run of continuation-bit bytes with no terminator.
    assert _candidate_name(b"\x0a" + b"\xff" * 10, 0) is False


def test_candidate_name_rejects_out_of_range_length_and_truncated_name() -> None:
    # length 0 is rejected (must be 1..4096).
    assert _candidate_name(b"\x0a\x00", 0) is False
    # declared length longer than the remaining buffer.
    assert _candidate_name(b"\x0a\x05ab", 0) is False


def test_valid_rejects_missing_name_unprintable_name_and_bad_syntax() -> None:
    from google.protobuf.descriptor_pb2 import FileDescriptorProto

    no_name = FileDescriptorProto()
    assert _valid(no_name) is False

    wrong_suffix = FileDescriptorProto(name="not-a-schema.txt")
    assert _valid(wrong_suffix) is False

    unprintable = FileDescriptorProto(name="bad\x00.proto")
    assert _valid(unprintable) is False

    bad_syntax = _descriptor()
    bad_syntax.syntax = "proto4"
    assert _valid(bad_syntax) is False

    # RecoveredSchema only supports proto2/proto3; a real protoc-emitted
    # editions descriptor must not reach decode_file_descriptor and raise
    # there uncaught -- it should simply not be treated as a valid find.
    editions = _descriptor()
    editions.syntax = "editions"
    assert _valid(editions) is False


def test_valid_rejects_invalid_utf8_name_without_crashing() -> None:
    # upb stores an invalid-UTF-8 string field as raw bytes instead of
    # raising, so a malformed name must not reach str-only methods.
    raw_name = b"\xff\xfex.proto"
    payload = bytes([0x0A, len(raw_name)]) + raw_name
    descriptor = FileDescriptorProto()
    descriptor.ParseFromString(payload)

    assert _valid(descriptor) is False


def test_field_ends_stops_when_a_valid_value_overruns_the_boundary() -> None:
    # tag + value together read cleanly but exceed the caller's own limit.
    data = b"\x08\x01"
    assert _field_ends(data, 0, 1, 10) == []


def test_scan_descriptors_stops_trying_further_boundaries_once_attempts_exhausted() -> (
    None
):
    name_field = b"\x0a\x07" + b"a.proto"
    malformed_message_type = b"\x22\x03" + b"\x0a\x02\x41"
    data = name_field + malformed_message_type

    # Only one MergeFromString attempt is allowed; the first (longest)
    # boundary raises DecodeError, and the budget runs out before the
    # second (shorter, valid-but-empty) boundary is even tried.
    assert scan_descriptors(data, max_parse_attempts=1) == []


def test_scan_descriptors_recovers_from_decode_error_on_malformed_boundary() -> None:
    # Outer boundaries (per _field_ends) are wire-format-complete but the
    # nested message_type submessage is internally truncated -- its own
    # inner field declares a length longer than what's left inside it, so
    # protobuf's own parser raises DecodeError on the full-length attempt;
    # scan_descriptors must fall back to the next shorter boundary rather
    # than propagating that error.
    name_field = b"\x0a\x07" + b"a.proto"
    # message_type (field 4, wire 2), length 3, containing a "name" field
    # (tag 0x0a) declaring length 2 but only 1 byte follows it.
    malformed_message_type = b"\x22\x03" + b"\x0a\x02\x41"
    data = name_field + malformed_message_type

    assert scan_descriptors(data) == []


def test_descriptor_scan_rejects_nonpositive_limits() -> None:
    with pytest.raises(ValueError, match="scan limits"):
        scan_descriptors(b"", max_candidates=0)
    with pytest.raises(ValueError, match="scan limits"):
        scan_descriptors(b"", max_boundaries=0)
    with pytest.raises(ValueError, match="scan limits"):
        scan_descriptors(b"", max_parse_attempts=0)


def test_gzip_scan_recovers_go_blob() -> None:
    expected = _descriptor().SerializeToString()
    findings = scan_gzip_descriptors(b"prefix" + gzip.compress(expected))
    assert len(findings) == 1
    assert findings[0].descriptor.SerializeToString() == expected


def test_gzip_scan_ignores_false_magic_and_limits_inflation() -> None:
    assert scan_gzip_descriptors(b"prefix\x1f\x8bnot-gzip") == []
    compressed = gzip.compress(b"x" * 1024)
    assert scan_gzip_descriptors(compressed, max_inflated_size=100) == []


def test_gzip_scan_resumes_past_a_member_that_never_reaches_eof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A member whose declared/actual length never completes a stream (as
    # opposed to raising zlib.error outright) must still be skipped rather
    # than treated as a found descriptor or an infinite loop.
    class _NeverDoneDecompressor:
        eof = False

        def decompress(self, data: bytes, limit: int) -> bytes:
            return b"partial"

    calls = []
    real_decompressobj = zlib.decompressobj

    def fake_decompressobj(wbits: int) -> object:
        calls.append(1)
        if len(calls) == 1:
            return _NeverDoneDecompressor()
        return real_decompressobj(wbits=wbits)

    monkeypatch.setattr(
        "protoloom.extract.gozip.zlib.decompressobj", fake_decompressobj
    )
    expected = _descriptor().SerializeToString()
    real = gzip.compress(expected)
    data = b"\x1f\x8b" + real

    findings = scan_gzip_descriptors(data)

    assert len(findings) == 1
    assert findings[0].descriptor.SerializeToString() == expected
    assert len(calls) == 2


def test_gzip_scan_applies_inflation_budget_across_members() -> None:
    expected = _descriptor().SerializeToString()
    packed = gzip.compress(expected)

    findings = scan_gzip_descriptors(
        packed + packed,
        max_inflated_size=len(expected),
    )

    assert len(findings) == 1
    assert findings[0].offset == 0


def test_gzip_scan_bounds_candidate_members() -> None:
    expected = _descriptor().SerializeToString()
    packed = gzip.compress(expected)

    findings = scan_gzip_descriptors(packed + packed, max_members=1)

    assert len(findings) == 1
    assert findings[0].offset == 0


def test_gzip_scan_rejects_nonpositive_limits() -> None:
    with pytest.raises(ValueError, match="inflated size"):
        scan_gzip_descriptors(b"", max_inflated_size=0)
    with pytest.raises(ValueError, match="gzip members"):
        scan_gzip_descriptors(b"", max_members=0)
    with pytest.raises(ValueError, match="gzip depth"):
        scan_gzip_descriptors(b"", max_depth=0)


def test_gzip_scan_recovers_descriptor_nested_inside_gzip_inside_gzip() -> None:
    expected = _descriptor().SerializeToString()
    triple = gzip.compress(gzip.compress(gzip.compress(expected)))

    findings = scan_gzip_descriptors(triple)

    assert any(f.descriptor.SerializeToString() == expected for f in findings)


def test_gzip_scan_depth_limit_stops_recursion() -> None:
    expected = _descriptor().SerializeToString()
    triple = gzip.compress(gzip.compress(gzip.compress(expected)))

    assert scan_gzip_descriptors(triple, max_depth=1) == []


def test_descriptor_conversion_and_emission() -> None:
    schema = decode_file_descriptor(_descriptor(), "fixture", "0x6")
    emitted = emit_proto(schema)
    assert 'syntax = "proto3";' in emitted
    assert "message Greeting" in emitted
    assert "string text = 1;" in emitted
    assert compile_proto(emitted).success


def test_proto3_optional_emits_without_synthetic_oneof() -> None:
    descriptor = _descriptor()
    message = descriptor.message_type[0]
    message.oneof_decl.add(name="_nickname")
    field = message.field.add(name="nickname", number=2)
    field.label = field.LABEL_OPTIONAL
    field.type = field.TYPE_STRING
    field.oneof_index = 0
    field.proto3_optional = True
    emitted = emit_proto(decode_file_descriptor(descriptor, "fixture", "0"))
    assert "optional string nickname = 2;" in emitted
    assert "oneof _nickname" not in emitted
    assert compile_proto(emitted).success


def test_proto2_oneof_fields_have_no_label() -> None:
    descriptor = _descriptor()
    descriptor.syntax = "proto2"
    message = descriptor.message_type[0]
    message.oneof_decl.add(name="choice")
    field = message.field[0]
    field.oneof_index = 0
    emitted = emit_proto(decode_file_descriptor(descriptor, "fixture", "0"))
    assert "oneof choice" in emitted
    assert "optional string text" not in emitted
    assert compile_proto(emitted).success


def test_enum_aliases_emit_required_option() -> None:
    descriptor = _descriptor()
    enum = descriptor.enum_type.add(name="State")
    enum.options.allow_alias = True
    enum.value.add(name="UNKNOWN", number=0)
    enum.value.add(name="UNSPECIFIED", number=0)

    emitted = emit_proto(decode_file_descriptor(descriptor, "fixture", "0"))

    assert "option allow_alias = true;" in emitted
    assert compile_proto(emitted).success
