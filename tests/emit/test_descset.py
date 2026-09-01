from google.protobuf.descriptor_pb2 import FileDescriptorProto, FileDescriptorSet

from protoloom.emit.descset import emit_descriptor_set


def test_descriptor_set_preserves_files_and_order() -> None:
    first = FileDescriptorProto(
        name="first.proto",
        package="demo.first",
        syntax="proto3",
    )
    first.message_type.add(name="First")
    second = FileDescriptorProto(
        name="second.proto",
        package="demo.second",
        dependency=["first.proto"],
    )
    second.message_type.add(name="Second")

    encoded = emit_descriptor_set([first, second])
    decoded = FileDescriptorSet.FromString(encoded)

    assert list(decoded.file) == [first, second]


def test_descriptor_set_accepts_no_files() -> None:
    assert FileDescriptorSet.FromString(emit_descriptor_set([])).file == []
