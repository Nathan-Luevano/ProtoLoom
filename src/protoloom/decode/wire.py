from collections import defaultdict

from protoloom.container.dex import DexField, DexFile
from protoloom.extract.wire import (
    WireAdapterFinding,
    WireEnumFinding,
    WireFieldFinding,
    WireNameFinding,
    WireOneofFinding,
    wire_adapter_type,
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


def _field(
    dex: DexFile,
    item: WireFieldFinding,
    source: str,
    proto3: bool,
    null_defaults: frozenset[int] = frozenset(),
    known_enum_types: frozenset[int] = frozenset(),
) -> Field | None:
    type_name = wire_adapter_type(item.adapter)
    if type_name is None:
        return None
    if type_name.startswith("."):
        type_name = type_name.rsplit(".", 1)[-1].replace("$", "_")
    is_reference_type = item.adapter.endswith("#ADAPTER")
    type_is_enum = is_reference_type and (
        item.field.type_index in known_enum_types or _dex_class_is_enum(dex, item.field)
    )
    boxed_presence = _boxed_presence(dex, item.field, proto3, item.label, item.oneof)
    default_presence = (
        proto3
        and item.oneof is None
        and item.label != "repeated"
        and item.schema_index in null_defaults
        and _wire_presence_type(dex, item)
    )
    presence = boxed_presence or default_presence
    location = f"{item.owner}->{dex.field_name(item.field)}"
    return Field(
        dex.field_name(item.field),
        item.number,
        type_name,
        Confidence.CERTAIN,
        [Evidence(source, location, item.adapter)],
        label=item.label,
        oneof=item.oneof or (f"_field_{item.number}" if presence else None),
        packed="PACKED" in item.adapter or None,
        proto3_optional=presence,
        type_is_enum=type_is_enum,
    )


def _boxed_presence(
    dex: DexFile, field: DexField, proto3: bool, label: str, oneof: str | None
) -> bool:
    return (
        proto3
        and oneof is None
        and label != "repeated"
        and dex.types[field.type_index]
        in {
            "Ljava/lang/Boolean;",
            "Ljava/lang/Double;",
            "Ljava/lang/Float;",
            "Ljava/lang/Integer;",
            "Ljava/lang/Long;",
        }
    )


def _dex_class_is_enum(dex: DexFile, field: DexField) -> bool:
    # a reference-typed field whose declared dex class subclasses
    # java.lang.Enum is an enum regardless of whether the enum's values
    # could themselves be recovered (see decode_wire_enums) - this is
    # the only signal Wire's own #ADAPTER annotation doesn't carry.
    raw_type = dex.types[field.type_index]
    if raw_type not in dex.types:
        return False
    item_class = dex.class_by_type_index(dex.types.index(raw_type))
    return (
        item_class is not None
        and item_class.superclass_index != dex.NO_INDEX
        and dex.types[item_class.superclass_index] == "Ljava/lang/Enum;"
    )


def _wire_presence_type(dex: DexFile, item: WireFieldFinding) -> bool:
    if not item.adapter.endswith("#ADAPTER"):
        return True
    return _dex_class_is_enum(dex, item.field)


def wire_dex_type(dex: DexFile, field_index: int, adapter_index: int) -> str | None:
    type_name, _ = wire_dex_type_kind(dex, field_index, adapter_index)
    return type_name


def wire_dex_type_kind(
    dex: DexFile,
    field_index: int,
    adapter_index: int,
    known_enum_types: frozenset[int] = frozenset(),
) -> tuple[str | None, bool]:
    if not 0 <= field_index < len(dex.fields):
        return None, False
    if not 0 <= adapter_index < len(dex.fields):
        return None, False
    field = dex.fields[field_index]
    adapter = dex.fields[adapter_index]
    raw_type = dex.types[field.type_index]
    obvious = {
        "Z": "bool",
        "Ljava/lang/Boolean;": "bool",
        "D": "double",
        "Ljava/lang/Double;": "double",
        "F": "float",
        "Ljava/lang/Float;": "float",
        "Ljava/lang/String;": "string",
        "Lokio/ByteString;": "bytes",
    }
    if raw_type in obvious:
        return obvious[raw_type], False
    adapter_name = dex.field_name(adapter)
    adapter_owner = dex.types[adapter.class_index]
    if (
        adapter_name == "ADAPTER" or adapter.class_index == field.type_index
    ) and adapter_owner.startswith("L"):
        name = adapter_owner[1:-1].rsplit("/", 1)[-1].replace("$", "_")
        type_is_enum = field.type_index in known_enum_types or _dex_class_is_enum(
            dex, field
        )
        return name, type_is_enum
    scalar = wire_adapter_type(f"adapter#{adapter_name}")
    if scalar is not None:
        return scalar, False
    return None, False


def decode_wire_adapter_fields(
    dex: DexFile,
    findings: tuple[WireAdapterFinding, ...],
    names: tuple[WireNameFinding, ...],
    oneofs: tuple[WireOneofFinding, ...],
    source: str,
    syntaxes: dict[str, str] | None = None,
    known_enum_types: frozenset[int] = frozenset(),
) -> dict[str, list[Field]]:
    recovered_names = {(item.owner, item.field.name_index): item.name for item in names}
    indexes = {field: index for index, field in enumerate(dex.fields)}
    groups = {
        (item.owner, name): f"choice_{index}"
        for index, item in enumerate(oneofs)
        for name in item.fields
    }
    fields: dict[str, list[Field]] = defaultdict(list)
    for item in findings:
        field_index = indexes.get(item.field)
        adapter_index = indexes.get(item.adapter)
        if field_index is None or adapter_index is None:
            continue
        type_name, type_is_enum = wire_dex_type_kind(
            dex, field_index, adapter_index, known_enum_types
        )
        if type_name is None:
            continue
        name = recovered_names.get(
            (item.owner, item.field.name_index), dex.field_name(item.field)
        )
        location = f"method {item.method_index} @ 0x{item.instruction_offset:x}"
        oneof = groups.get((item.owner, name))
        presence = _boxed_presence(
            dex,
            item.field,
            (syntaxes or {}).get(item.owner) == "proto3",
            item.label,
            oneof,
        )
        fields[item.owner].append(
            Field(
                name,
                item.number,
                type_name,
                Confidence.HIGH,
                [Evidence(source, location, "Square Wire tagged adapter write")],
                label=item.label,
                oneof=oneof or (f"_field_{item.number}" if presence else None),
                packed=item.packed or None,
                proto3_optional=presence,
                type_is_enum=type_is_enum,
            )
        )
    return fields


def decode_wire_adapters(
    dex: DexFile,
    findings: tuple[WireAdapterFinding, ...],
    names: tuple[WireNameFinding, ...],
    oneofs: tuple[WireOneofFinding, ...],
    source: str,
    syntaxes: dict[str, str] | None = None,
    known_enum_types: frozenset[int] = frozenset(),
) -> list[RecoveredSchema]:
    fields = decode_wire_adapter_fields(
        dex, findings, names, oneofs, source, syntaxes, known_enum_types
    )
    names_by_owner = {item.owner: item.message_name for item in names}
    schemas = []
    for owner, items in fields.items():
        path = owner.removeprefix("L").removesuffix(";")
        package, _, class_name = path.rpartition("/")
        message_name = names_by_owner.get(owner) or class_name.replace("$", "_")
        evidence = Evidence(source, owner, "Square Wire adapter bytecode")
        message = Message(
            message_name,
            sorted(items, key=lambda item: item.number),
            confidence=Confidence.HIGH,
            evidence=[evidence],
        )
        schemas.append(
            RecoveredSchema(
                f"{message_name}.proto",
                package.replace("/", "."),
                syntax=(syntaxes or {}).get(owner, "proto2"),
                messages=[message],
                evidence=[evidence],
            )
        )
    return schemas


def decode_wire_messages(
    owners: tuple[str, ...], source: str, syntaxes: dict[str, str] | None = None
) -> list[RecoveredSchema]:
    schemas = []
    for owner in owners:
        path = owner.removeprefix("L").removesuffix(";")
        package, _, class_name = path.rpartition("/")
        message_name = class_name.replace("$", "_")
        evidence = Evidence(source, owner, "Square Wire message superclass")
        schemas.append(
            RecoveredSchema(
                f"{message_name}.proto",
                package.replace("/", "."),
                syntax=(syntaxes or {}).get(owner, "proto2"),
                messages=[Message(message_name, [], evidence=[evidence])],
                evidence=[evidence],
            )
        )
    return schemas


def decode_wire_enums(
    findings: tuple[WireEnumFinding, ...],
    source: str,
    syntaxes: dict[str, str] | None = None,
) -> tuple[list[RecoveredSchema], dict[tuple[str, str], dict[str, str | None]]]:
    schemas = []
    lineage = {}
    for item in findings:
        path = item.descriptor.removeprefix("L").removesuffix(";")
        package, _, class_name = path.rpartition("/")
        enum_name = class_name.replace("$", "_")
        parent = f"L{path.rsplit('$', 1)[0]};" if "$" in path else None
        package_prefix = f"L{package}/"
        package_syntaxes = {
            syntax
            for owner, syntax in (syntaxes or {}).items()
            if owner.startswith(package_prefix)
        }
        fallback_syntax = (
            package_syntaxes.pop() if len(package_syntaxes) == 1 else "proto2"
        )
        evidence = Evidence(source, item.descriptor, "Wire enum initializer")
        enum = EnumType(
            enum_name,
            [EnumValue(name, number) for name, number in item.values],
            Confidence.CERTAIN,
            [evidence],
        )
        schema = RecoveredSchema(
            f"{enum_name}.proto",
            package.replace("/", "."),
            syntax=(syntaxes or {}).get(parent or item.descriptor, fallback_syntax),
            enums=[enum],
            evidence=[evidence],
        )
        schemas.append(schema)
        lineage[(schema.package, schema.name)] = {enum_name: parent}
    return schemas, lineage


def decode_wire_annotations(
    dex: DexFile,
    findings: tuple[WireFieldFinding, ...],
    source: str,
    syntaxes: dict[str, str] | None = None,
    null_defaults: dict[str, frozenset[int]] | None = None,
    known_enum_types: frozenset[int] = frozenset(),
) -> list[RecoveredSchema]:
    grouped: dict[str, list[WireFieldFinding]] = defaultdict(list)
    for item in findings:
        grouped[item.owner].append(item)
    schemas = []
    for owner, items in grouped.items():
        path = owner.removeprefix("L").removesuffix(";")
        package, _, class_name = path.rpartition("/")
        syntax = (syntaxes or {}).get(owner, "proto2")
        fields = [
            field
            for item in items
            if (
                field := _field(
                    dex,
                    item,
                    source,
                    syntax == "proto3",
                    (null_defaults or {}).get(owner, frozenset()),
                    known_enum_types,
                )
            )
        ]
        evidence = Evidence(source, owner, "retained Square Wire annotations")
        message = Message(class_name.replace("$", "_"), fields, evidence=[evidence])
        schemas.append(
            RecoveredSchema(
                f"{message.name}.proto",
                package.replace("/", "."),
                syntax=syntax,
                messages=[message],
                evidence=[evidence],
            )
        )
    return schemas
