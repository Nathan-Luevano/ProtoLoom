import re
from dataclasses import dataclass

_PROTOBUF_TAG = re.compile(r'(?:^| )protobuf:"([^"]+)"(?: |$)')


@dataclass(frozen=True, slots=True)
class GoProtobufTag:
    encoding: str
    number: int
    label: str
    name: str
    packed: bool
    proto3: bool


def parse_protobuf_tag(value: str) -> GoProtobufTag | None:
    match = _PROTOBUF_TAG.search(value)
    if match is None:
        return None
    parts = match.group(1).split(",")
    if len(parts) < 3 or parts[2] not in {"opt", "req", "rep"}:
        return None
    try:
        number = int(parts[1])
    except ValueError:
        return None
    options = {item.split("=", 1)[0]: item for item in parts[3:]}
    name = options.get("name", "name=").removeprefix("name=")
    if not name or not 1 <= number < 2**29:
        return None
    return GoProtobufTag(
        parts[0],
        number,
        {"opt": "optional", "req": "required", "rep": "repeated"}[parts[2]],
        name,
        "packed" in options,
        "proto3" in options,
    )
