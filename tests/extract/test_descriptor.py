import gzip

from google.protobuf.descriptor_pb2 import FileDescriptorProto

from protoloom.decode.descpb import decode_file_descriptor
from protoloom.emit.proto import emit_proto
from protoloom.extract.descriptor import scan_descriptors
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


def test_gzip_scan_recovers_go_blob() -> None:
    expected = _descriptor().SerializeToString()
    findings = scan_gzip_descriptors(b"prefix" + gzip.compress(expected))
    assert len(findings) == 1
    assert findings[0].descriptor.SerializeToString() == expected


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
