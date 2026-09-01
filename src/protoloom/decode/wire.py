from protoloom.container.dex import DexFile
from protoloom.extract.wire import WireFieldFinding, wire_adapter_type
from protoloom.model import Confidence, Evidence, Field


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
