from pathlib import PurePosixPath

from protoloom.container.dex import DexFile
from protoloom.decode.fieldtype import field_type
from protoloom.decode.infostring import decode_info_string
from protoloom.decode.names import recover_names, unpack_field_names
from protoloom.extract.lite import LiteFinding
from protoloom.model import Confidence, Evidence, Field, Message, RecoveredSchema


def _class_name(descriptor: str) -> str:
    normalized = descriptor.removeprefix("L").removesuffix(";")
    return PurePosixPath(normalized).name.replace("$", "_") or "RecoveredMessage"


def decode_lite_finding(
    dex: DexFile, finding: LiteFinding, source: str
) -> RecoveredSchema:
    info = decode_info_string(finding.info_string)
    method = dex.methods[finding.containing_method]
    descriptor = dex.types[method.class_index]
    raw_objects = tuple(item.value for item in finding.objects)
    java_names = unpack_field_names(raw_objects, info.header.field_count)
    names = recover_names(java_names, tuple(field.number for field in info.fields))
    evidence = Evidence(
        source,
        f"code_item@0x{finding.code_offset:x}+{finding.instruction_offset * 2}",
        "protobuf-lite newMessageInfo",
    )
    fields: list[Field] = []
    auxiliary_classes = [
        str(item.value).removeprefix("L").removesuffix(";").replace("/", ".")
        for item in finding.objects
        if item.kind == "class"
    ]
    auxiliary_index = 0
    obfuscated = any(item.obfuscated for item in names)
    for item, recovered in zip(info.fields, names, strict=True):
        kind = field_type(item.type_id)
        type_name = kind.proto_type
        if type_name in {"message", "group"}:
            if auxiliary_index < len(auxiliary_classes):
                type_name = auxiliary_classes[auxiliary_index].rsplit(".", 1)[-1]
                auxiliary_index += 1
            else:
                type_name = f"RecoveredField{item.number}"
        elif type_name == "enum":
            type_name = "int32"
        elif type_name == "map":
            type_name = "bytes"
        confidence = Confidence.SPECULATIVE if obfuscated else Confidence.HIGH
        fields.append(
            Field(
                name=recovered.proto_name,
                number=item.number,
                type_name=type_name,
                label="required" if item.required else kind.label,
                oneof=(
                    f"choice_{item.oneof_index}"
                    if item.oneof_index is not None
                    else None
                ),
                packed=kind.packed or None,
                confidence=confidence,
                evidence=[evidence],
            )
        )
    message = Message(
        name=_class_name(descriptor),
        fields=fields,
        confidence=Confidence.HIGH,
        evidence=[evidence],
    )
    package = descriptor.removeprefix("L").removesuffix(";").rsplit("/", 1)[0]
    return RecoveredSchema(
        name=f"{message.name}.proto",
        package=package.replace("/", "."),
        syntax="proto2" if info.header.is_proto2 else "proto3",
        messages=[message],
        evidence=[evidence],
    )
