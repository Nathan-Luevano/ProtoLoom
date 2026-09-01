import re

from protoloom.model import EnumType, Field, Message, RecoveredSchema

_IDENTIFIER = re.compile(r"[^A-Za-z0-9_]")
_SCALARS = {
    "bool",
    "bytes",
    "double",
    "fixed32",
    "fixed64",
    "float",
    "int32",
    "int64",
    "sfixed32",
    "sfixed64",
    "sint32",
    "sint64",
    "string",
    "uint32",
    "uint64",
}


def _name(value: str, fallback: str) -> str:
    # protoc's grammar accepts any identifier, including its own directive
    # keywords, as a message/enum/field/oneof name (verified against protoc
    # 29.3: `message message { string message = 1; } enum enum {...}` and
    # `oneof oneof {...}` all compile). Only invalid-character and
    # leading-digit sanitizing is a real constraint here.
    cleaned = _IDENTIFIER.sub("_", value)
    if not cleaned or cleaned[0].isdigit():
        cleaned = f"_{cleaned}" if cleaned else fallback
    return cleaned


def _enum(item: EnumType, indent: str) -> list[str]:
    enum_name = _name(item.name, "RecoveredEnum")
    lines = [f"{indent}enum {enum_name} {{"]
    values = item.values or []
    numbers = [value.number for value in values]
    needs_synthetic_zero = bool(values and values[0].number != 0)
    if len(numbers) != len(set(numbers)):
        lines.append(f"{indent}  option allow_alias = true;")
    if needs_synthetic_zero:
        # C++ enum-value scoping makes every value a sibling of its
        # message, not just its enum -- an unqualified UNSPECIFIED can
        # collide with another enum in the same package, so scope it to
        # this enum's own name.
        lines.append(f"{indent}  {enum_name.upper()}_UNSPECIFIED = 0;")
    for value in values:
        lines.append(f"{indent}  {_name(value.name, 'VALUE')} = {value.number};")
    lines.append(f"{indent}}}")
    return lines


def _field(item: Field, syntax: str, indent: str) -> str:
    label = item.label
    if item.type_name.startswith("map<") or (
        item.oneof is not None and not item.proto3_optional
    ):
        label = ""
    elif syntax == "proto3" and label == "optional":
        label = "optional" if item.proto3_optional else ""
    prefix = f"{label} " if label else ""
    options: list[str] = []
    if item.default_value is not None and syntax == "proto2":
        options.append(f"default = {item.default_value}")
    if item.packed is not None:
        options.append(f"packed = {'true' if item.packed else 'false'}")
    suffix = f" [{', '.join(options)}]" if options else ""
    return (
        f"{indent}{prefix}{item.type_name} {_name(item.name, f'field_{item.number}')} "
        f"= {item.number}{suffix};"
    )


def _message(item: Message, syntax: str, indent: str = "") -> list[str]:
    lines = [f"{indent}message {_name(item.name, 'RecoveredMessage')} {{"]
    child_indent = f"{indent}  "
    for nested in item.messages:
        lines.extend(_message(nested, syntax, child_indent))
    for enum in item.enums:
        lines.extend(_enum(enum, child_indent))
    grouped = {
        field.oneof
        for field in item.fields
        if field.oneof is not None and not field.proto3_optional
    }
    for field in item.fields:
        if field.oneof is None or field.proto3_optional:
            lines.append(_field(field, syntax, child_indent))
    for group in sorted(grouped):
        if group is None:
            continue
        lines.append(f"{child_indent}oneof {_name(group, 'choice')} {{")
        for field in item.fields:
            if field.oneof == group:
                lines.append(_field(field, syntax, f"{child_indent}  "))
        lines.append(f"{child_indent}}}")
    lines.append(f"{indent}}}")
    return lines


def _declared_types(message: Message, prefix: str = "") -> set[str]:
    qualified = f"{prefix}.{message.name}" if prefix else message.name
    declared = {qualified}
    declared.update(f"{qualified}.{enum.name}" for enum in message.enums)
    for nested in message.messages:
        declared.update(_declared_types(nested, qualified))
    return declared


def emit_proto(schema: RecoveredSchema) -> str:
    lines = [f'syntax = "{schema.syntax}";', ""]
    if schema.package:
        lines.extend((f"package {schema.package};", ""))
    for dependency in schema.dependencies:
        lines.append(f'import "{dependency}";')
    if schema.dependencies:
        lines.append("")
    for enum in schema.enums:
        lines.extend(_enum(enum, ""))
        lines.append("")
    for message in schema.messages:
        lines.extend(_message(message, schema.syntax))
        lines.append("")
    declared = {enum.name for enum in schema.enums}
    for message in schema.messages:
        declared.update(_declared_types(message))
    referenced: set[str] = set()
    pending = list(schema.messages)
    while pending:
        message = pending.pop()
        pending.extend(message.messages)
        for field in message.fields:
            if field.type_name not in _SCALARS and not field.type_name.startswith(
                (".", "map<")
            ):
                referenced.add(field.type_name)
    for missing in sorted(referenced - declared):
        lines.extend((f"message {_name(missing, 'RecoveredType')} {{}}", ""))
    return "\n".join(lines).rstrip() + "\n"
