from google.protobuf.descriptor_pb2 import (
    DescriptorProto,
    FileDescriptorProto,
    FileDescriptorSet,
)

MAX_DESCRIPTOR_FILES = 100_000
MAX_DESCRIPTOR_ITEMS = 1_000_000
MAX_DESCRIPTOR_DEPTH = 100
MAX_DESCRIPTOR_SET_SIZE = 256 * 1024 * 1024


def emit_descriptor_set(
    descriptors: list[FileDescriptorProto],
    *,
    max_files: int = MAX_DESCRIPTOR_FILES,
    max_items: int = MAX_DESCRIPTOR_ITEMS,
    max_depth: int = MAX_DESCRIPTOR_DEPTH,
    max_size: int = MAX_DESCRIPTOR_SET_SIZE,
) -> bytes:
    if min(max_files, max_items, max_depth, max_size) <= 0:
        raise ValueError("descriptor set limits must be positive")
    if len(descriptors) > max_files:
        raise ValueError(f"descriptor set exceeds {max_files} files")
    unique: dict[str, FileDescriptorProto] = {}
    unique_encoded: dict[str, bytes] = {}
    encoded_size = 0
    for descriptor in descriptors:
        if not descriptor.name:
            raise ValueError("descriptor file name is required")
        _validate_descriptor(descriptor, max_items, max_depth)
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


def _validate_descriptor(
    descriptor: FileDescriptorProto, max_items: int, max_depth: int
) -> None:
    count = len(descriptor.dependency) + len(descriptor.enum_type)
    count += len(descriptor.extension) + len(descriptor.service)
    count += sum(len(service.method) for service in descriptor.service)
    count += sum(len(enum.value) for enum in descriptor.enum_type)
    pending: list[tuple[DescriptorProto, int]] = [
        (message, 1) for message in descriptor.message_type
    ]
    while pending:
        message, depth = pending.pop()
        if depth > max_depth:
            raise ValueError(f"descriptor set exceeds message depth {max_depth}")
        count += 1 + len(message.field) + len(message.extension)
        count += len(message.enum_type) + len(message.oneof_decl)
        count += sum(len(enum.value) for enum in message.enum_type)
        if count > max_items:
            raise ValueError(f"descriptor set exceeds {max_items} items")
        pending.extend((child, depth + 1) for child in message.nested_type)
    if count > max_items:
        raise ValueError(f"descriptor set exceeds {max_items} items")
