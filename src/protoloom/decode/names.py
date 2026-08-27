import re
from collections.abc import Sequence
from dataclasses import dataclass

_OBFUSCATED = re.compile(r"^[a-z]{1,2}_?$")


@dataclass(frozen=True, slots=True)
class RecoveredName:
    java_name: str
    proto_name: str
    obfuscated: bool


def java_to_proto_name(name: str) -> str:
    name = name.rstrip("_")
    name = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    name = re.sub(r"[^A-Za-z0-9_]", "_", name).lower()
    name = re.sub(r"_+", "_", name).strip("_")
    if not name:
        name = "field"
    if name[0].isdigit():
        name = f"field_{name}"
    # protoc's grammar accepts any identifier, including its own directive
    # keywords, in a field-name position (verified: `string message = 1;`
    # and friends compile). Escaping is emit/proto.py's job at render time,
    # against the .proto text -- not the recovered model's job here, where
    # it would corrupt names.json and truth comparisons.
    return name


def names_are_obfuscated(names: Sequence[str]) -> bool:
    return (
        bool(names)
        and sum(bool(_OBFUSCATED.fullmatch(name)) for name in names) > len(names) / 2
    )


def recover_names(
    java_names: Sequence[str], field_numbers: Sequence[int]
) -> tuple[RecoveredName, ...]:
    if len(java_names) != len(field_numbers):
        raise ValueError("field names and numbers must have the same length")
    obfuscated = names_are_obfuscated(java_names)
    return tuple(
        RecoveredName(
            name,
            f"field_{number}" if obfuscated else java_to_proto_name(name),
            obfuscated,
        )
        for name, number in zip(java_names, field_numbers, strict=True)
    )


def unpack_field_names(
    objects: Sequence[object], expected_count: int
) -> tuple[str, ...]:
    if expected_count < 0:
        raise ValueError("expected count cannot be negative")
    if expected_count == 0:
        return ()
    if not objects or not isinstance(objects[0], str):
        raise ValueError("objects array does not start with field-name data")

    first = objects[0].split()
    if len(first) == expected_count + 1:
        return tuple(first[1:])
    names = []
    for value in objects:
        if not isinstance(value, str):
            break
        names.append(value)
        if len(names) == expected_count:
            return tuple(names)
    raise ValueError(
        f"objects array contains {len(names)} names, expected {expected_count}"
    )
