import re

from protoloom.model import EnumType, Field, Message, RecoveredSchema

_IDENTIFIER = re.compile(r"[^A-Za-z0-9_]")
_KEYWORDS = {
    "bool",
    "bytes",
    "double",
    "enum",
    "extend",
    "extensions",
    "false",
    "fixed32",
    "fixed64",
    "float",
    "group",
    "import",
    "int32",
    "int64",
    "map",
    "message",
    "oneof",
    "option",
    "optional",
    "package",
    "public",
    "repeated",
    "required",
    "reserved",
    "returns",
    "rpc",
    "service",
    "sfixed32",
    "sfixed64",
    "sint32",
    "sint64",
    "stream",
    "string",
    "syntax",
    "to",
    "true",
    "uint32",
    "uint64",
    "weak",
}
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
    cleaned = _IDENTIFIER.sub("_", value)
    if not cleaned or cleaned[0].isdigit():
        cleaned = f"_{cleaned}" if cleaned else fallback
    return f"{cleaned}_" if cleaned in _KEYWORDS else cleaned


def _enum(item: EnumType, indent: str) -> list[str]:
    lines = [f"{indent}enum {_name(item.name, 'RecoveredEnum')} {{"]
    values = item.values or []
    if values and values[0].number != 0:
        lines.append(f"{indent}  option allow_alias = true;")
        lines.append(f"{indent}  UNSPECIFIED = 0;")
    for value in values:
        lines.append(f"{indent}  {_name(value.name, 'VALUE')} = {value.number};")
    lines.append(f"{indent}}}")
    return lines


def _field(item: Field, syntax: str, indent: str) -> str:
    label = item.label
    if syntax == "proto3" and label == "optional":
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
    declared = {message.name for message in schema.messages}
    referenced: set[str] = set()
    pending = list(schema.messages)
    while pending:
        message = pending.pop()
        pending.extend(message.messages)
        for field in message.fields:
            if field.type_name not in _SCALARS and not field.type_name.startswith("."):
                referenced.add(field.type_name)
    for missing in sorted(referenced - declared):
        lines.extend((f"message {_name(missing, 'RecoveredType')} {{}}", ""))
    return "\n".join(lines).rstrip() + "\n"
