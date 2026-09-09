from google.protobuf.descriptor_pb2 import FileDescriptorProto, FileDescriptorSet

MAX_DESCRIPTOR_FILES = 100_000
MAX_DESCRIPTOR_SET_SIZE = 256 * 1024 * 1024


def emit_descriptor_set(
    descriptors: list[FileDescriptorProto],
    *,
    max_files: int = MAX_DESCRIPTOR_FILES,
    max_size: int = MAX_DESCRIPTOR_SET_SIZE,
) -> bytes:
    if max_files <= 0 or max_size <= 0:
        raise ValueError("descriptor set limits must be positive")
    if len(descriptors) > max_files:
        raise ValueError(f"descriptor set exceeds {max_files} files")
    unique: dict[str, FileDescriptorProto] = {}
    unique_encoded: dict[str, bytes] = {}
    encoded_size = 0
    for descriptor in descriptors:
        if not descriptor.name:
            raise ValueError("descriptor file name is required")
        encoded = descriptor.SerializeToString(deterministic=True)
        encoded_size += len(encoded)
        if encoded_size > max_size:
            raise ValueError(f"descriptor set exceeds {max_size} bytes")
        existing = unique.get(descriptor.name)
        if existing is None:
            unique[descriptor.name] = descriptor
            unique_encoded[descriptor.name] = encoded
            continue
        if unique_encoded[descriptor.name] != encoded:
            raise ValueError(f"conflicting descriptors named {descriptor.name}")
    result = FileDescriptorSet()
    for name in sorted(unique):
        descriptor = unique[name]
        result.file.add().CopyFrom(descriptor)
    encoded = result.SerializeToString(deterministic=True)
    if len(encoded) > max_size:
        raise ValueError(f"descriptor set exceeds {max_size} bytes")
    return encoded
