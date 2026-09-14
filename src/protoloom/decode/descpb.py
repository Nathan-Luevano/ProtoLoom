from google.protobuf.descriptor_pb2 import (
    DescriptorProto,
    EnumDescriptorProto,
    FieldDescriptorProto,
    FileDescriptorProto,
)

from protoloom.model import (
    Confidence,
    EnumType,
    EnumValue,
    Evidence,
    Field,
    Message,
    RecoveredSchema,
)

_TYPE_NAMES = {
    value.number: value.name.removeprefix("TYPE_").lower()
    for value in FieldDescriptorProto.Type.DESCRIPTOR.values
}
_LABELS = {
    value.number: value.name.removeprefix("LABEL_").lower()
    for value in FieldDescriptorProto.Label.DESCRIPTOR.values
}
# matches emit_proto's own MAX_PROTO_ITEMS/MAX_PROTO_DEPTH: a descriptor too
# big to ever emit should be rejected here, before building Field/Message
# objects for it, not after (that "decode everything, reject at emit" path
# let a several-million-field descriptor spend seconds building objects
# doomed to be thrown away).
MAX_DECODE_ITEMS = 1_000_000
MAX_DECODE_DEPTH = 100


def _validate_descriptor_budget(
    raw: FileDescriptorProto, max_items: int, max_depth: int
) -> None:
    if max_items <= 0 or max_depth <= 0:
        raise ValueError("descriptor decode limits must be positive")
    count = len(raw.dependency) + len(raw.enum_type)
    count += sum(len(item.value) for item in raw.enum_type)
    if count > max_items:
        raise ValueError(f"descriptor decode exceeds {max_items} items")
    pending = [(item, 1) for item in raw.message_type]
    while pending:
        message, depth = pending.pop()
        if depth > max_depth:
            raise ValueError(f"descriptor decode exceeds message depth {max_depth}")
        count += 1 + len(message.field) + len(message.enum_type)
        count += sum(len(item.value) for item in message.enum_type)
        if count > max_items:
            raise ValueError(f"descriptor decode exceeds {max_items} items")
        pending.extend((child, depth + 1) for child in message.nested_type)


def _enum(raw: EnumDescriptorProto, evidence: Evidence) -> EnumType:
    return EnumType(
        name=raw.name,
        values=[EnumValue(value.name, value.number) for value in raw.value],
        confidence=Confidence.CERTAIN,
        evidence=[evidence],
    )


def _field(raw: FieldDescriptorProto, oneofs: list[str], evidence: Evidence) -> Field:
    type_name = raw.type_name if raw.type_name else _TYPE_NAMES[raw.type]
    oneof = oneofs[raw.oneof_index] if raw.HasField("oneof_index") else None
    return Field(
        name=raw.name,
        number=raw.number,
        type_name=type_name,
        label=_LABELS[raw.label],
        oneof=oneof,
        json_name=raw.json_name or None,
        default_value=raw.default_value or None,
        packed=raw.options.packed if raw.options.HasField("packed") else None,
        proto3_optional=raw.proto3_optional,
        # TYPE_GROUP is wire-incompatible with a plain message field on
        # recompile (start/end-group markers vs length-delimited); keep the
        # distinction so emit can round-trip the group syntax.
        is_group=raw.type == FieldDescriptorProto.TYPE_GROUP,
        confidence=Confidence.CERTAIN,
        evidence=[evidence],
    )


def _extension_lines(raw: DescriptorProto, prefix: str) -> list[str]:
    name = f"{prefix}{raw.name}"
    lines = []
    if raw.extension or raw.extension_range:
        lines.append(
            f"message {name}: {len(raw.extension)} extension field(s), "
            f"{len(raw.extension_range)} extension range(s) not modeled and dropped"
        )
    for nested in raw.nested_type:
        lines.extend(_extension_lines(nested, f"{name}."))
    return lines


def extension_diagnostics(raw: FileDescriptorProto) -> list[str]:
    # proto2 `extend` blocks/extension ranges are not modeled anywhere in this
    # decoder; surfacing them here lets callers warn instead of silently
    # emitting a schema that looks complete but has dropped real content.
    lines = [
        line for message in raw.message_type for line in _extension_lines(message, "")
    ]
    if raw.extension:
        lines.append(
            f"file {raw.name}: {len(raw.extension)} top-level extension field(s) "
            "not modeled and dropped"
        )
    return [f"{raw.name}: {line}" for line in lines]


def _message(raw: DescriptorProto, evidence: Evidence) -> Message:
    oneofs = [item.name for item in raw.oneof_decl]
    return Message(
        name=raw.name,
        fields=[_field(item, oneofs, evidence) for item in raw.field],
        messages=[_message(item, evidence) for item in raw.nested_type],
        enums=[_enum(item, evidence) for item in raw.enum_type],
        confidence=Confidence.CERTAIN,
        evidence=[evidence],
    )


def decode_file_descriptor(
    raw: FileDescriptorProto,
    source: str,
    location: str,
    *,
    max_items: int = MAX_DECODE_ITEMS,
    max_depth: int = MAX_DECODE_DEPTH,
) -> RecoveredSchema:
    _validate_descriptor_budget(raw, max_items, max_depth)
    evidence = Evidence(source, location, "serialized FileDescriptorProto")
    return RecoveredSchema(
        name=raw.name,
        package=raw.package,
        syntax=raw.syntax or "proto2",
        messages=[_message(item, evidence) for item in raw.message_type],
        enums=[_enum(item, evidence) for item in raw.enum_type],
        dependencies=list(raw.dependency),
        evidence=[evidence],
    )
