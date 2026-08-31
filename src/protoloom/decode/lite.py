from collections import Counter
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from pathlib import PurePosixPath

from protoloom.container.dex import DexFile
from protoloom.decode.fieldtype import field_type
from protoloom.decode.infostring import InfoField, decode_info_string
from protoloom.decode.names import java_to_proto_name, names_are_obfuscated
from protoloom.extract.lite import (
    LiteFinding,
    recover_enum_evidence,
    recover_enum_evidence_from_verifier,
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
    dex: DexFile, finding: LiteFinding
) -> tuple[list[str | None], list[str | None], list[int | None], list[str | None]]:
    info = decode_info_string(finding.info_string)
    cursor = info.header.oneof_count * 2 + info.header.hasbits_count
    names: list[str | None] = []
    classes: list[str | None] = []
    map_fields: list[int | None] = []
    enum_verifiers: list[str | None] = []
    objects = finding.objects
    for field in info.fields:
        kind = field_type(field.type_id)
        name: str | None = None
        class_name: str | None = None
        map_field: int | None = None
        enum_verifier: str | None = None
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
            enum_object = objects[cursor]
            if enum_object.kind == "class":
                enum_verifier = str(enum_object.value)
                cursor += 1
            elif enum_object.kind == "static_field":
                # newMessageInfo carries the field's EnumVerifier.INSTANCE
                # singleton here, not the enum type -- its declaring class
                # is the verifier, a proof-of-nesting signal that survives
                # even when getters and setters are both stripped. R8 can
                # merge several distinct Verifier classes into one physical
                # class, keeping their singletons apart as INSTANCE,
                # INSTANCE$1, INSTANCE$2... -- once merged, only the plain
                # "INSTANCE" field still names the class it actually lives
                # in; the numbered ones belong to a different, absorbed
                # verifier and would misattribute the enum if trusted.
                field_index = int(enum_object.value)
                if field_index < len(dex.fields):
                    static_field = dex.fields[field_index]
                    if dex.field_name(static_field) == "INSTANCE":
                        enum_verifier = dex.types[static_field.class_index]
                cursor += 1
            elif enum_object.kind == "call_result":
                cursor += 1
        elif kind.proto_type == "map" and cursor < len(objects):
            if objects[cursor].kind == "static_field":
                map_field = int(objects[cursor].value)
            cursor += 1
        names.append(name)
        classes.append(class_name)
        map_fields.append(map_field)
        enum_verifiers.append(enum_verifier)
    return names, classes, map_fields, enum_verifiers


@dataclass(frozen=True, slots=True)
class DecodedLite:
    schema: RecoveredSchema
    class_descriptor: str
    enclosing_descriptor: str | None
    # File-scope (non-message-local) enum name -> the enum's own DEX
    # enclosing class, so the combine step can nest it under the right
    # message instead of leaving it at file scope when that message is
    # actually a different class than the one that referenced the enum.
    enum_enclosing: dict[str, str | None] = dataclass_field(default_factory=dict)


def _enclosing_descriptor(dex: DexFile, descriptor: str) -> str | None:
    if descriptor not in dex.types:
        return None
    dex_class = dex.class_by_type_index(dex.types.index(descriptor))
    enclosing_index = dex.enclosing_class_index(dex_class) if dex_class else None
    enclosing = (
        dex.types[enclosing_index]
        if enclosing_index is not None and enclosing_index < len(dex.types)
        else None
    )
    if enclosing is None:
        # Some shrinkers strip EnclosingClass/InnerClass annotations while
        # leaving the "$"-joined class name itself untouched.
        normalized = descriptor.removeprefix("L").removesuffix(";")
        if "$" in normalized:
            enclosing = f"L{normalized.rsplit('$', 1)[0]};"
    return enclosing


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
    own_class = dex.class_by_type_index(method.class_index)
    # protobuf-lite emits a `NAME_FIELD_NUMBER` static int constant per field,
    # including oneof members that otherwise never get a name string in the
    # objects array. Its own name is generated directly from the original
    # proto field name uppercased, so lowercasing it the true name losslessly
    # -- unlike reversing the getter's camelCase, which can't tell whether a
    # digit-letter transition in the original name had an underscore or not.
    field_number_names: dict[int, str] = {}
    if own_class is not None:
        static_fields = dex.class_static_fields(own_class)
        static_values = dex.static_field_values(own_class)
        for static_field, value in zip(static_fields, static_values, strict=False):
            if not isinstance(value, int):
                continue
            static_name = dex.field_name(static_field)
            if static_name.endswith("_FIELD_NUMBER"):
                field_number_names[value] = static_name.removesuffix(
                    "_FIELD_NUMBER"
                ).lower()
    java_names, auxiliary_classes, map_fields, enum_verifiers = _field_objects(
        dex, finding
    )
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
    enum_enclosing: dict[str, str | None] = {}
    dependencies: set[str] = set()
    resolved_fields: list[tuple[str, bool, str | None, bool]] = []
    for (
        item,
        java_name,
        normalized_name,
        auxiliary_class,
        map_field,
        enum_verifier,
    ) in zip(
        info.fields,
        java_names,
        normalized_names,
        auxiliary_classes,
        map_fields,
        enum_verifiers,
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
            if enum_evidence is None and enum_verifier is not None:
                enum_evidence = recover_enum_evidence_from_verifier(dex, enum_verifier)
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
                if not message_local:
                    # Not nested inside the field's own owning message, but
                    # very likely nested inside some other message in the
                    # same file (protobuf enums are rarely truly top-level).
                    enum_enclosing[type_name] = _enclosing_descriptor(
                        dex, enum_evidence.descriptor
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
        authoritative_name = field_number_names.get(item.number)
        effective_name = authoritative_name or derived_name or normalized_name
        resolved_fields.append(
            (type_name, guessed_type, effective_name, authoritative_name is not None)
        )
    effective_names = [name for _, _, name, _ in resolved_fields]
    duplicate_names = {
        name for name, count in Counter(effective_names).items() if name and count > 1
    }
    fields: list[Field] = []
    for item, (type_name, guessed_type, effective_name, authoritative) in zip(
        info.fields, resolved_fields, strict=True
    ):
        kind = field_type(item.type_id)
        speculative_name = (
            (obfuscated and not authoritative)
            or effective_name is None
            or effective_name in duplicate_names
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
    # The combine step still only trusts a name-shape fallback guess when
    # the guessed parent is itself a class recovered in the same run.
    enclosing_descriptor = _enclosing_descriptor(dex, descriptor)
    return DecodedLite(schema, descriptor, enclosing_descriptor, enum_enclosing)
