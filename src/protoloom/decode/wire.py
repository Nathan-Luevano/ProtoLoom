from collections import defaultdict

from protoloom.container.dex import DexFile
from protoloom.extract.wire import (
    WireAdapterFinding,
    WireEnumFinding,
    WireFieldFinding,
    WireNameFinding,
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


def _field(dex: DexFile, item: WireFieldFinding, source: str) -> Field | None:
    type_name = wire_adapter_type(item.adapter)
    if type_name is None:
        return None
    if type_name.startswith("."):
        type_name = type_name.rsplit(".", 1)[-1].replace("$", "_")
    location = f"{item.owner}->{dex.field_name(item.field)}"
    return Field(
        dex.field_name(item.field),
        item.number,
        type_name,
        Confidence.CERTAIN,
        [Evidence(source, location, item.adapter)],
        label=item.label,
        oneof=item.oneof,
        packed="PACKED" in item.adapter or None,
    )


def wire_dex_type(dex: DexFile, field_index: int, adapter_index: int) -> str | None:
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
        return obvious[raw_type]
    adapter_name = dex.field_name(adapter)
    adapter_owner = dex.types[adapter.class_index]
    if adapter_name == "ADAPTER" and adapter_owner.startswith("L"):
        return adapter_owner[1:-1].rsplit("/", 1)[-1].replace("$", "_")
    scalar = wire_adapter_type(f"adapter#{adapter_name}")
    if scalar is not None:
        return scalar
    return None


def decode_wire_adapter_fields(
    dex: DexFile,
    findings: tuple[WireAdapterFinding, ...],
    names: tuple[WireNameFinding, ...],
    source: str,
) -> dict[str, list[Field]]:
    recovered_names = {(item.owner, item.field.name_index): item.name for item in names}
    indexes = {field: index for index, field in enumerate(dex.fields)}
    fields: dict[str, list[Field]] = defaultdict(list)
    for item in findings:
        type_name = wire_dex_type(dex, indexes[item.field], indexes[item.adapter])
        if type_name is None:
            continue
        name = recovered_names.get(
            (item.owner, item.field.name_index), dex.field_name(item.field)
        )
        location = f"method {item.method_index} @ 0x{item.instruction_offset:x}"
        fields[item.owner].append(
            Field(
                name,
                item.number,
                type_name,
                Confidence.HIGH,
                [Evidence(source, location, "Square Wire tagged adapter write")],
                label=item.label,
                packed=item.packed or None,
            )
        )
    return fields


def decode_wire_adapters(
    dex: DexFile,
    findings: tuple[WireAdapterFinding, ...],
    names: tuple[WireNameFinding, ...],
    source: str,
) -> list[RecoveredSchema]:
    fields = decode_wire_adapter_fields(dex, findings, names, source)
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
                messages=[message],
                evidence=[evidence],
            )
        )
    return schemas


def decode_wire_enums(
    findings: tuple[WireEnumFinding, ...], source: str
) -> tuple[list[RecoveredSchema], dict[tuple[str, str], dict[str, str | None]]]:
    schemas = []
    lineage = {}
    for item in findings:
        path = item.descriptor.removeprefix("L").removesuffix(";")
        package, _, class_name = path.rpartition("/")
        enum_name = class_name.replace("$", "_")
        parent = f"L{path.rsplit('$', 1)[0]};" if "$" in path else None
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
            enums=[enum],
            evidence=[evidence],
        )
        schemas.append(schema)
        lineage[(schema.package, schema.name)] = {enum_name: parent}
    return schemas, lineage


def decode_wire_annotations(
    dex: DexFile, findings: tuple[WireFieldFinding, ...], source: str
) -> list[RecoveredSchema]:
    grouped: dict[str, list[WireFieldFinding]] = defaultdict(list)
    for item in findings:
        grouped[item.owner].append(item)
    schemas = []
    for owner, items in grouped.items():
        path = owner.removeprefix("L").removesuffix(";")
        package, _, class_name = path.rpartition("/")
        fields = [field for item in items if (field := _field(dex, item, source))]
        evidence = Evidence(source, owner, "retained Square Wire annotations")
        message = Message(class_name.replace("$", "_"), fields, evidence=[evidence])
        schemas.append(
            RecoveredSchema(
                f"{message.name}.proto",
                package.replace("/", "."),
                messages=[message],
                evidence=[evidence],
            )
        )
    return schemas
