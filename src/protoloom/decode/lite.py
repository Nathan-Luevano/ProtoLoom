from collections import Counter
from dataclasses import dataclass
from pathlib import PurePosixPath

from protoloom.container.dex import DexFile
from protoloom.decode.fieldtype import field_type
from protoloom.decode.infostring import InfoField, decode_info_string
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


# protobuf's well-known types: real compilable protoc-shipped .proto files,
# not app schema. Recognizing them needs a fixed lookup, not name-guessing.
_WELL_KNOWN_TYPES: dict[str, tuple[str, str]] = {
    "Lcom/google/protobuf/Any;": ("google.protobuf.Any", "google/protobuf/any.proto"),
    "Lcom/google/protobuf/Empty;": (
        "google.protobuf.Empty",
        "google/protobuf/empty.proto",
    ),
    "Lcom/google/protobuf/Duration;": (
        "google.protobuf.Duration",
        "google/protobuf/duration.proto",
    ),
    "Lcom/google/protobuf/Timestamp;": (
        "google.protobuf.Timestamp",
        "google/protobuf/timestamp.proto",
    ),
    "Lcom/google/protobuf/FieldMask;": (
        "google.protobuf.FieldMask",
        "google/protobuf/field_mask.proto",
    ),
    "Lcom/google/protobuf/Struct;": (
        "google.protobuf.Struct",
        "google/protobuf/struct.proto",
    ),
    "Lcom/google/protobuf/Value;": (
        "google.protobuf.Value",
        "google/protobuf/struct.proto",
    ),
    "Lcom/google/protobuf/ListValue;": (
        "google.protobuf.ListValue",
        "google/protobuf/struct.proto",
    ),
    **{
        f"Lcom/google/protobuf/{name}Value;": (
            f"google.protobuf.{name}Value",
            "google/protobuf/wrappers.proto",
        )
        for name in (
            "Double",
            "Float",
            "Int64",
            "UInt64",
            "Int32",
            "UInt32",
            "Bool",
            "String",
            "Bytes",
        )
    },
}


def _resolve_message_type(descriptor: str) -> tuple[str, str | None]:
    known = _WELL_KNOWN_TYPES.get(descriptor)
    if known is None:
        return _class_name(descriptor), None
    name, import_path = known
    return f".{name}", import_path


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
                class_name = str(objects[cursor].value)
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


def _field_oneof(item: InfoField, is_proto2: bool) -> str | None:
    if item.oneof_index is not None:
        return f"choice_{item.oneof_index}"
    # proto3 gives every "optional" scalar its own synthetic one-member oneof
    # so HasField works; the hasbit is the only surviving signal for it.
    if not is_proto2 and item.has_presence:
        return f"synthetic_{item.number}"
    return None


def decode_lite_finding(dex: DexFile, finding: LiteFinding, source: str) -> DecodedLite:
    info = decode_info_string(finding.info_string)
    method = dex.methods[finding.containing_method]
    descriptor = dex.types[method.class_index]
    declared_field_types = {
        dex.field_name(item): dex.types[item.type_index]
        for item in dex.fields
        if item.class_index == method.class_index
    }
    java_names, auxiliary_classes, map_fields = _field_objects(finding)
    visible_names = [name for name in java_names if name is not None]
    obfuscated = names_are_obfuscated(visible_names)
    normalized_names = [
        java_to_proto_name(name) if name is not None else None for name in java_names
    ]
    evidence = Evidence(
        source,
        f"code_item@0x{finding.code_offset:x}+{finding.instruction_offset * 2}",
        "protobuf-lite newMessageInfo",
    )
    recovered_enums: dict[str, EnumType] = {}
    recovered_message_enums: dict[str, EnumType] = {}
    dependencies: set[str] = set()
    resolved_fields: list[tuple[str, bool, str | None]] = []
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
        derived_name: str | None = None
        if type_name in {"message", "group"}:
            # newMessageInfo's objects array almost never carries an explicit
            # class literal for a plain message field on real bytecode; the
            # generated getter/field's own declared type is the reliable
            # source (protobuf-lite's Java runtime resolves it the same way,
            # via reflection).
            declared = declared_field_types.get(java_name) if java_name else None
            resolved = (
                declared
                if declared is not None
                and declared.startswith("L")
                and declared.endswith(";")
                # A `repeated` field's declared Java type is a list wrapper
                # (e.g. ProtobufArrayList), not the element type. Runtime
                # protobuf-internal classes are never a field's real proto
                # type except the well-known ones we explicitly recognize.
                and (
                    not declared.startswith("Lcom/google/protobuf/")
                    or declared in _WELL_KNOWN_TYPES
                )
                else None
            )
            source_descriptor = resolved if resolved is not None else auxiliary_class
            if resolved is not None:
                type_name, import_path = _resolve_message_type(resolved)
                if import_path is not None:
                    dependencies.add(import_path)
            elif auxiliary_class is not None:
                type_name, import_path = _resolve_message_type(auxiliary_class)
                if import_path is not None:
                    dependencies.add(import_path)
            elif normalized_name is not None:
                type_name = "".join(part.title() for part in normalized_name.split("_"))
                guessed_type = True
            else:
                type_name = f"RecoveredField{item.number}"
                guessed_type = True
            if (
                java_name is None
                and not guessed_type
                and source_descriptor is not None
                # Below two "$" levels a bare class name is often already a
                # flattened compound (e.g. "CustomRelaySettings" as a direct
                # child of the outer wrapper) with no recoverable relationship
                # to the true field name; only trust real multi-level nesting.
                and source_descriptor.count("$") >= 2
            ):
                # A oneof member shares one storage field with its siblings
                # and never gets its own name string in the objects array;
                # generators name it after its message type instead.
                last_segment = source_descriptor.removesuffix(";").rsplit("$", 1)[-1]
                derived_name = java_to_proto_name(last_segment)
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
        effective_name = derived_name or normalized_name
        resolved_fields.append((type_name, guessed_type, effective_name))
    effective_names = [name for _, _, name in resolved_fields]
    duplicate_names = {
        name for name, count in Counter(effective_names).items() if name and count > 1
    }
    fields: list[Field] = []
    for item, (type_name, guessed_type, effective_name) in zip(
        info.fields, resolved_fields, strict=True
    ):
        kind = field_type(item.type_id)
        speculative_name = (
            obfuscated or effective_name is None or effective_name in duplicate_names
        )
        if speculative_name:
            name = f"field_{item.number}"
        else:
            assert effective_name is not None
            name = effective_name
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
                oneof=_field_oneof(item, info.header.is_proto2),
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
        dependencies=sorted(dependencies),
        evidence=[evidence],
    )
    own_class = dex.class_by_type_index(method.class_index)
    enclosing_index = dex.enclosing_class_index(own_class) if own_class else None
    enclosing_descriptor = (
        dex.types[enclosing_index]
        if enclosing_index is not None and enclosing_index < len(dex.types)
        else None
    )
    if enclosing_descriptor is None:
        # Some shrinkers strip EnclosingClass/InnerClass annotations while
        # leaving the "$"-joined class name itself untouched. Fall back to
        # that name shape; the combine step still only trusts it when the
        # guessed parent is itself a class recovered in the same run.
        normalized = descriptor.removeprefix("L").removesuffix(";")
        if "$" in normalized:
            enclosing_descriptor = f"L{normalized.rsplit('$', 1)[0]};"
    return DecodedLite(schema, descriptor, enclosing_descriptor)
