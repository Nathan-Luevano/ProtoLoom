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
        confidence=Confidence.CERTAIN,
        evidence=[evidence],
    )


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
    raw: FileDescriptorProto, source: str, location: str
) -> RecoveredSchema:
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
