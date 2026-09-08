from google.protobuf.descriptor_pb2 import FileDescriptorProto, FileDescriptorSet


def emit_descriptor_set(descriptors: list[FileDescriptorProto]) -> bytes:
    unique: dict[str, FileDescriptorProto] = {}
    for descriptor in descriptors:
        if not descriptor.name:
            raise ValueError("descriptor file name is required")
        existing = unique.get(descriptor.name)
        if existing is None:
            unique[descriptor.name] = descriptor
            continue
        encoded = descriptor.SerializeToString(deterministic=True)
        if existing.SerializeToString(deterministic=True) != encoded:
            raise ValueError(f"conflicting descriptors named {descriptor.name}")
    result = FileDescriptorSet()
    for name in sorted(unique):
        descriptor = unique[name]
        result.file.add().CopyFrom(descriptor)
    return result.SerializeToString(deterministic=True)
