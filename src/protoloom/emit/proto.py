import json
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
_NUMERIC_DEFAULT = re.compile(
    r"[-+]?(?:inf|nan|0[xX][0-9A-Fa-f]+|0[0-7]+|(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)"
)


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


def _unique_names(
    values: list[str], fallback: str, reserved: set[str] | None = None
) -> list[str]:
    used = set(reserved or ())
    result = []
    for value in values:
        base = _name(value, fallback)
        candidate = base
        suffix = 2
        while candidate in used:
            candidate = f"{base}_{suffix}"
            suffix += 1
        used.add(candidate)
        result.append(candidate)
    return result


def _qualified_name(value: str) -> str:
    return ".".join(
        _name(component, "recovered") for component in value.split(".") if component
    )


def _type_name(value: str) -> str:
    if value in _SCALARS:
        return value
    if value.startswith("map<") and value.endswith(">"):
        key, separator, item = value[4:-1].partition(",")
        if separator:
            return f"map<{_type_name(key.strip())}, {_type_name(item.strip())}>"
    absolute = value.startswith(".")
    qualified = _qualified_name(value.removeprefix(".")) or "RecoveredType"
    return f".{qualified}" if absolute else qualified


def _default_literal(item: Field) -> str | None:
    value = item.default_value
    if value is None:
        return None
    if item.type_name in {"string", "bytes"}:
        return json.dumps(value)
    if item.type_name == "bool":
        return value.lower() if value.lower() in {"true", "false"} else None
    if item.type_name in _SCALARS:
        return value if _NUMERIC_DEFAULT.fullmatch(value) else None
    return _name(value, "VALUE")


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
    reserved = {f"{enum_name.upper()}_UNSPECIFIED"} if needs_synthetic_zero else set()
    value_names = _unique_names([value.name for value in values], "VALUE", reserved)
    for value, value_name in zip(values, value_names, strict=True):
        lines.append(f"{indent}  {value_name} = {value.number};")
    lines.append(f"{indent}}}")
    return lines


def _field(item: Field, syntax: str, indent: str, name: str) -> str:
    label = item.label
    if item.type_name.startswith("map<") or (
        item.oneof is not None and not item.proto3_optional
    ):
        label = ""
    elif syntax == "proto3" and label == "optional":
        label = "optional" if item.proto3_optional else ""
    prefix = f"{label} " if label else ""
    options: list[str] = []
    default = _default_literal(item)
    if default is not None and syntax == "proto2":
        options.append(f"default = {default}")
    if item.packed is not None:
        options.append(f"packed = {'true' if item.packed else 'false'}")
    suffix = f" [{', '.join(options)}]" if options else ""
    return (
        f"{indent}{prefix}{_type_name(item.type_name)} {name} = {item.number}{suffix};"
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
    field_names = _unique_names(
        [field.name for field in item.fields], "recovered_field"
    )
    group_names = dict(
        zip(
            sorted(grouped),
            _unique_names(sorted(grouped), "choice", set(field_names)),
            strict=True,
        )
    )
    for field, field_name in zip(item.fields, field_names, strict=True):
        if field.oneof is None or field.proto3_optional:
            lines.append(_field(field, syntax, child_indent, field_name))
    for group in sorted(grouped):
        if group is None:
            continue
        lines.append(f"{child_indent}oneof {group_names[group]} {{")
        for field, field_name in zip(item.fields, field_names, strict=True):
            if field.oneof == group:
                lines.append(_field(field, syntax, f"{child_indent}  ", field_name))
        lines.append(f"{child_indent}}}")
    lines.append(f"{indent}}}")
    return lines


def _declared_types(message: Message, prefix: str = "") -> set[str]:
    message_name = _name(message.name, "RecoveredMessage")
    qualified = f"{prefix}.{message_name}" if prefix else message_name
    declared = {qualified}
    declared.update(
        f"{qualified}.{_name(enum.name, 'RecoveredEnum')}" for enum in message.enums
    )
    for nested in message.messages:
        declared.update(_declared_types(nested, qualified))
    return declared


def emit_proto(schema: RecoveredSchema) -> str:
    lines = [f'syntax = "{schema.syntax}";', ""]
    if schema.package:
        lines.extend((f"package {_qualified_name(schema.package)};", ""))
    for dependency in schema.dependencies:
        lines.append(f"import {json.dumps(dependency)};")
    if schema.dependencies:
        lines.append("")
    for enum in schema.enums:
        lines.extend(_enum(enum, ""))
        lines.append("")
    for message in schema.messages:
        lines.extend(_message(message, schema.syntax))
        lines.append("")
    declared = {_name(enum.name, "RecoveredEnum") for enum in schema.enums}
    for message in schema.messages:
        declared.update(_declared_types(message))
    referenced: set[str] = set()
    pending = list(schema.messages)
    while pending:
        message = pending.pop()
        pending.extend(message.messages)
        for field in message.fields:
            emitted_type = _type_name(field.type_name)
            if emitted_type not in _SCALARS and not emitted_type.startswith(
                (".", "map<")
            ):
                referenced.add(emitted_type)
    for missing in sorted(referenced - declared):
        lines.extend((f"message {_name(missing, 'RecoveredType')} {{}}", ""))
    return "\n".join(lines).rstrip() + "\n"
