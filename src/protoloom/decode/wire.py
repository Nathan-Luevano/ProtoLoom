from collections import defaultdict

from protoloom.container.dex import DexFile
from protoloom.extract.wire import WireFieldFinding, wire_adapter_type
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
