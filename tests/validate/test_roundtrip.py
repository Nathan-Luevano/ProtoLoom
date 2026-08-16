from google.protobuf import descriptor_pb2, descriptor_pool, message_factory

from protoloom.validate.roundtrip import roundtrip_descriptor_set, roundtrip_message


def descriptor_set() -> bytes:
    file = descriptor_pb2.FileDescriptorProto(
        name="sample.proto", package="sample", syntax="proto3"
    )
    message = file.message_type.add(name="Record")
    message.field.add(
        name="value",
        number=1,
        label=descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL,
        type=descriptor_pb2.FieldDescriptorProto.TYPE_UINT32,
    )
    files = descriptor_pb2.FileDescriptorSet()
    files.file.append(file)
    return files.SerializeToString()


def message_class() -> type:
    files = descriptor_pb2.FileDescriptorSet.FromString(descriptor_set())
    pool = descriptor_pool.DescriptorPool()
    pool.Add(files.file[0])
    return message_factory.GetMessageClass(pool.FindMessageTypeByName("sample.Record"))


def test_byte_identical_roundtrip() -> None:
    result = roundtrip_message(message_class(), b"\x08\x96\x01")
    assert result.decoded
    assert result.byte_identical
    assert result.semantically_equal
    assert result.output_size == 3


def test_semantic_success_does_not_hide_noncanonical_input() -> None:
    result = roundtrip_message(message_class(), b"\x08\x81\x00")
    assert result.decoded
    assert not result.byte_identical
    assert result.semantically_equal
    assert result.output_size == 2


def test_malformed_payload_is_reported_without_raising() -> None:
    result = roundtrip_message(message_class(), b"\x08")
    assert not result.decoded
    assert not result.byte_identical
    assert result.error


def test_builds_dynamic_message_from_descriptor_set() -> None:
    result = roundtrip_descriptor_set(descriptor_set(), ".sample.Record", b"\x08\x07")
    assert result.byte_identical


def test_missing_message_is_reported() -> None:
    result = roundtrip_descriptor_set(descriptor_set(), "sample.Missing", b"")
    assert not result.decoded
    assert "Missing" in (result.error or "")
