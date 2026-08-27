from collections import Counter
from dataclasses import dataclass
from pathlib import PurePosixPath

from protoloom.container.dex import DexFile
from protoloom.decode.fieldtype import field_type
from protoloom.decode.infostring import decode_info_string
from protoloom.decode.names import java_to_proto_name, names_are_obfuscated
from protoloom.extract.lite import (
    LiteFinding,
    recover_enum_evidence,
    recover_map_evidence,
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


def _class_name(descriptor: str) -> str:
    normalized = descriptor.removeprefix("L").removesuffix(";")
    parts = PurePosixPath(normalized).name.split("$")
    if len(parts) > 1:
        parts = parts[1:]
    return "_".join(parts) or "RecoveredMessage"


def _field_objects(
    finding: LiteFinding,
) -> tuple[list[str | None], list[str | None], list[int | None]]:
    info = decode_info_string(finding.info_string)
    cursor = info.header.oneof_count * 2 + info.header.hasbits_count
    names: list[str | None] = []
    classes: list[str | None] = []
    map_fields: list[int | None] = []
    objects = finding.objects
    for field in info.fields:
        kind = field_type(field.type_id)
        name: str | None = None
        class_name: str | None = None
        map_field: int | None = None
        if (
            field.oneof_index is None
            and cursor < len(objects)
            and objects[cursor].kind == "string"
        ):
            name = str(objects[cursor].value)
            cursor += 1
        if kind.proto_type in {"message", "group"}:
            if cursor < len(objects) and objects[cursor].kind == "class":
                class_name = _class_name(str(objects[cursor].value))
                cursor += 1
        elif kind.proto_type == "enum" and cursor < len(objects):
            if objects[cursor].kind in {"class", "static_field", "call_result"}:
                cursor += 1
        elif kind.proto_type == "map" and cursor < len(objects):
            if objects[cursor].kind == "static_field":
                map_field = int(objects[cursor].value)
            cursor += 1
        names.append(name)
        classes.append(class_name)
        map_fields.append(map_field)
    return names, classes, map_fields


@dataclass(frozen=True, slots=True)
class DecodedLite:
    schema: RecoveredSchema
    class_descriptor: str
    enclosing_descriptor: str | None


def decode_lite_finding(dex: DexFile, finding: LiteFinding, source: str) -> DecodedLite:
    info = decode_info_string(finding.info_string)
    method = dex.methods[finding.containing_method]
    descriptor = dex.types[method.class_index]
    java_names, auxiliary_classes, map_fields = _field_objects(finding)
    visible_names = [name for name in java_names if name is not None]
    obfuscated = names_are_obfuscated(visible_names)
    normalized_names = [
        java_to_proto_name(name) if name is not None else None for name in java_names
    ]
    duplicate_names = {
        name for name, count in Counter(normalized_names).items() if name and count > 1
    }
    evidence = Evidence(
        source,
        f"code_item@0x{finding.code_offset:x}+{finding.instruction_offset * 2}",
        "protobuf-lite newMessageInfo",
    )
    fields: list[Field] = []
    recovered_enums: dict[str, EnumType] = {}
    recovered_message_enums: dict[str, EnumType] = {}
    for item, java_name, normalized_name, auxiliary_class, map_field in zip(
        info.fields,
        java_names,
        normalized_names,
        auxiliary_classes,
        map_fields,
        strict=True,
    ):
        kind = field_type(item.type_id)
        type_name = kind.proto_type
        guessed_type = False
        map_evidence = None
        if type_name in {"message", "group"}:
            if auxiliary_class is not None:
                type_name = auxiliary_class
            elif normalized_name is not None:
                type_name = "".join(part.title() for part in normalized_name.split("_"))
                guessed_type = True
            else:
                type_name = f"RecoveredField{item.number}"
                guessed_type = True
        elif type_name == "enum":
            enum_evidence = (
                recover_enum_evidence(dex, descriptor, java_name)
                if java_name is not None
                else None
            )
            if enum_evidence is None:
                type_name = "int32"
            else:
                nested_prefix = descriptor.removesuffix(";") + "$"
                message_local = enum_evidence.descriptor.startswith(nested_prefix)
                type_name = (
                    enum_evidence.descriptor.removesuffix(";").rsplit("$", 1)[-1]
                    if message_local
                    else _class_name(enum_evidence.descriptor)
                )
                enum_evidence_record = Evidence(
                    source,
                    f"code_item@0x{enum_evidence.code_offset:x}",
                    "enum "
                    f"{enum_evidence.descriptor} initializer constructor/static stores "
                    + ", ".join(
                        f"+0x{offset * 2:x}"
                        for offset in enum_evidence.instruction_offsets
                    ),
                )
                target_enums = (
                    recovered_message_enums if message_local else recovered_enums
                )
                target_enums[type_name] = EnumType(
                    type_name,
                    [EnumValue(name, number) for name, number in enum_evidence.values],
                    Confidence.HIGH,
                    [enum_evidence_record],
                )
        elif type_name == "map":
            map_evidence = (
                recover_map_evidence(dex, map_field) if map_field is not None else None
            )
            type_name = (
                f"map<{map_evidence.key_type}, {map_evidence.value_type}>"
                if map_evidence is not None
                else "bytes"
            )
        speculative_name = (
            obfuscated or java_name is None or normalized_name in duplicate_names
        )
        if speculative_name:
            name = f"field_{item.number}"
        else:
            assert normalized_name is not None
            name = normalized_name
        if speculative_name:
            confidence = Confidence.SPECULATIVE
        elif finding.heuristic or guessed_type:
            confidence = Confidence.MEDIUM
        else:
            confidence = Confidence.HIGH
        fields.append(
            Field(
                name=name,
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
        enums=list(recovered_message_enums.values()),
        confidence=Confidence.MEDIUM if finding.heuristic else Confidence.HIGH,
        evidence=[evidence],
    )
    package = descriptor.removeprefix("L").removesuffix(";").rsplit("/", 1)[0]
    schema = RecoveredSchema(
        name=f"{message.name}.proto",
        package=package.replace("/", "."),
        syntax="proto2" if info.header.is_proto2 else "proto3",
        messages=[message],
        enums=list(recovered_enums.values()),
        evidence=[evidence],
    )
    own_class = dex.class_by_type_index(method.class_index)
    enclosing_index = dex.enclosing_class_index(own_class) if own_class else None
    enclosing_descriptor = (
        dex.types[enclosing_index]
        if enclosing_index is not None and enclosing_index < len(dex.types)
        else None
    )
    return DecodedLite(schema, descriptor, enclosing_descriptor)
