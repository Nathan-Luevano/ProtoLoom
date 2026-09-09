import pytest
from google.protobuf.descriptor_pb2 import FileDescriptorProto, FileDescriptorSet

from protoloom.emit.descset import emit_descriptor_set


def test_descriptor_set_canonicalizes_file_order() -> None:
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

    encoded = emit_descriptor_set([second, first])
    decoded = FileDescriptorSet.FromString(encoded)

    assert list(decoded.file) == [first, second]


def test_descriptor_set_accepts_no_files() -> None:
    assert FileDescriptorSet.FromString(emit_descriptor_set([])).file == []


def test_descriptor_set_collapses_identical_files() -> None:
    descriptor = FileDescriptorProto(name="same.proto", package="demo")

    encoded = emit_descriptor_set([descriptor, descriptor])

    assert list(FileDescriptorSet.FromString(encoded).file) == [descriptor]


def test_descriptor_set_rejects_conflicting_files() -> None:
    first = FileDescriptorProto(name="same.proto", package="first")
    second = FileDescriptorProto(name="same.proto", package="second")

    try:
        emit_descriptor_set([first, second])
    except ValueError as error:
        assert str(error) == "conflicting descriptors named same.proto"
    else:
        raise AssertionError("conflicting descriptors were accepted")


def test_descriptor_set_rejects_missing_file_name() -> None:
    try:
        emit_descriptor_set([FileDescriptorProto(package="demo")])
    except ValueError as error:
        assert str(error) == "descriptor file name is required"
    else:
        raise AssertionError("unnamed descriptor was accepted")


def test_descriptor_set_bounds_file_count() -> None:
    descriptors = [
        FileDescriptorProto(name="first.proto"),
        FileDescriptorProto(name="second.proto"),
    ]
    with pytest.raises(ValueError, match="exceeds 1 files"):
        emit_descriptor_set(descriptors, max_files=1)


def test_descriptor_set_bounds_encoded_size() -> None:
    descriptor = FileDescriptorProto(name="schema.proto", package="large.package")
    with pytest.raises(ValueError, match="exceeds 8 bytes"):
        emit_descriptor_set([descriptor], max_size=8)


@pytest.mark.parametrize(("max_files", "max_size"), [(0, 1), (1, 0)])
def test_descriptor_set_rejects_nonpositive_limits(
    max_files: int, max_size: int
) -> None:
    with pytest.raises(ValueError, match="limits must be positive"):
        emit_descriptor_set([], max_files=max_files, max_size=max_size)
