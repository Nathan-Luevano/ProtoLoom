from google.protobuf.descriptor_pb2 import FieldDescriptorProto, FileDescriptorProto

from protoloom.decode.descpb import decode_file_descriptor
from protoloom.emit.proto import emit_proto


def _raw_type_name_field(type_name: str) -> FileDescriptorProto:
    raw = FileDescriptorProto()
    raw.name = "test.proto"
    raw.package = "pkg"
    raw.syntax = "proto3"
    message = raw.message_type.add()
    message.name = "Foo"
    field = message.field.add()
    field.name = "status"
    field.number = 1
    field.type = FieldDescriptorProto.TYPE_ENUM
    field.type_name = type_name
    field.label = FieldDescriptorProto.LABEL_OPTIONAL
    return raw


def test_enum_typed_field_carries_type_is_enum() -> None:
    schema = decode_file_descriptor(_raw_type_name_field(".pkg.Status"), "src", "0x0")
    assert schema.messages[0].fields[0].type_is_enum is True


def test_message_typed_field_is_not_marked_enum() -> None:
    raw = _raw_type_name_field(".pkg.Status")
    raw.message_type[0].field[0].type = FieldDescriptorProto.TYPE_MESSAGE
    schema = decode_file_descriptor(raw, "src", "0x0")
    assert schema.messages[0].fields[0].type_is_enum is False


def test_unresolved_relative_enum_reference_stubs_as_enum_not_message() -> None:
    # FieldDescriptorProto.type_name is always fully-qualified from real
    # protoc output, but the format allows a relative name too; an
    # unresolved reference of that shape must still stub as an enum, not
    # an empty message, to keep the wire type (varint) correct.
    schema = decode_file_descriptor(_raw_type_name_field("Status"), "src", "0x0")
    output = emit_proto(schema)
    assert "enum Status {" in output
    assert "message Status {}" not in output
