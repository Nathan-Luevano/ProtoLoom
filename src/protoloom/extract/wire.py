from dataclasses import dataclass

from protoloom.container.dex import AnnotationItem, DexField, DexFile

_MESSAGE = "Lcom/squareup/wire/Message;"
_WIRE_FIELD = "Lcom/squareup/wire/WireField;"


@dataclass(frozen=True, slots=True)
class WireFieldFinding:
    owner: str
    field: DexField
    number: int
    adapter: str
    label: str
    oneof: str | None


def _elements(dex: DexFile, annotation: AnnotationItem) -> dict[str, object]:
    return {dex.strings[name]: value for name, value in annotation.elements}


def _string(dex: DexFile, value: object) -> str | None:
    if isinstance(value, int) and 0 <= value < len(dex.strings):
        return dex.strings[value]
    return None


def _label(dex: DexFile, value: object) -> str:
    if isinstance(value, int) and 0 <= value < len(dex.fields):
        name = dex.field_name(dex.fields[value]).lower()
        if name in {"optional", "required", "repeated", "packed"}:
            return "repeated" if name == "packed" else name
    return "optional"
