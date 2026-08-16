from google.protobuf.descriptor_pb2 import FileDescriptorProto, FileDescriptorSet


def emit_descriptor_set(descriptors: list[FileDescriptorProto]) -> bytes:
    result = FileDescriptorSet()
    for descriptor in descriptors:
        result.file.add().CopyFrom(descriptor)
    return result.SerializeToString()
