from collections import defaultdict

from protoloom.container.dex import DexFile
from protoloom.extract.wire import (
    WireAdapterFinding,
    WireFieldFinding,
    WireNameFinding,
    wire_adapter_type,
)
from protoloom.model import Confidence, Evidence, Field, Message, RecoveredSchema


def _field(dex: DexFile, item: WireFieldFinding, source: str) -> Field | None:
    type_name = wire_adapter_type(item.adapter)
    if type_name is None:
        return None
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
    scalar = wire_adapter_type(f"adapter#{adapter_name}")
    if scalar is not None:
        return scalar
    if adapter.class_index == field.type_index and raw_type.startswith("L"):
        return f".{raw_type[1:-1].replace('/', '.').replace('$', '.')}"
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
            )
        )
    return fields


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
