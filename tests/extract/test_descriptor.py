import gzip

import pytest
from google.protobuf.descriptor_pb2 import FileDescriptorProto

from protoloom.decode.descpb import decode_file_descriptor
from protoloom.emit.proto import emit_proto
from protoloom.extract.descriptor import _field_ends, scan_descriptors
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
