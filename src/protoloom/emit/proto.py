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


def _declaration_names(
    messages: list[Message], enums: list[EnumType]
) -> tuple[list[str], list[str]]:
    message_names = [_name(item.name, "RecoveredMessage") for item in messages]
    enum_names = [_name(item.name, "RecoveredEnum") for item in enums]
    allocated = _unique_names([*message_names, *enum_names], "RecoveredType")
    return allocated[: len(message_names)], allocated[len(message_names) :]


def _symbol_renames(
    messages: list[Message],
    enums: list[EnumType],
    raw_prefix: str = "",
    emitted_prefix: str = "",
) -> dict[str, str]:
    message_names, enum_names = _declaration_names(messages, enums)
    result: dict[str, str] = {}
    for item, name in zip(messages, message_names, strict=True):
        raw = f"{raw_prefix}.{item.name}" if raw_prefix else item.name
        emitted = f"{emitted_prefix}.{name}" if emitted_prefix else name
        result[raw] = emitted
        result.update(_symbol_renames(item.messages, item.enums, raw, emitted))
    for enum, name in zip(enums, enum_names, strict=True):
        raw = f"{raw_prefix}.{enum.name}" if raw_prefix else enum.name
        result[raw] = f"{emitted_prefix}.{name}" if emitted_prefix else name
    return result


def _resolved_type(value: str, renames: dict[str, str], package: str) -> str:
    if value in _SCALARS:
        return value
    if value.startswith("map<") and value.endswith(">"):
        key, separator, item = value[4:-1].partition(",")
        if separator:
            key_type = _resolved_type(key.strip(), renames, package)
            item_type = _resolved_type(item.strip(), renames, package)
            return f"map<{key_type}, {item_type}>"
    absolute = value.startswith(".")
    raw = value.removeprefix(".")
    local = raw.removeprefix(f"{package}.") if package else raw
    if (renamed := renames.get(local)) is None:
        return _type_name(value)
    if absolute and package:
        return f".{_qualified_name(package)}.{renamed}"
    return f".{renamed}" if absolute else renamed


def _enum(
    item: EnumType,
    syntax: str,
    indent: str,
    scope: set[str] | None = None,
    name: str | None = None,
) -> list[str]:
    used = scope if scope is not None else set()
    enum_name = name or _name(item.name, "RecoveredEnum")
    lines = [f"{indent}enum {enum_name} {{"]
    values = item.values or []
    numbers = [value.number for value in values]
    needs_synthetic_zero = not values or (syntax == "proto3" and values[0].number != 0)
    if len(numbers) != len(set(numbers)):
        lines.append(f"{indent}  option allow_alias = true;")
    reserved = {*used, enum_name}
    if needs_synthetic_zero:
        synthetic = _unique_names(
            [f"{enum_name.upper()}_UNSPECIFIED"], "UNSPECIFIED", reserved
        )[0]
        lines.append(f"{indent}  {synthetic} = 0;")
        reserved.add(synthetic)
    value_names = _unique_names([value.name for value in values], "VALUE", reserved)
    for value, value_name in zip(values, value_names, strict=True):
        lines.append(f"{indent}  {value_name} = {value.number};")
    used.update(value_names)
    used.update(reserved - used)
    lines.append(f"{indent}}}")
    return lines


def _field(
    item: Field,
    syntax: str,
    indent: str,
    name: str,
    renames: dict[str, str],
    package: str,
) -> str:
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
    field_type = _resolved_type(item.type_name, renames, package)
    return f"{indent}{prefix}{field_type} {name} = {item.number}{suffix};"


def _message(
    item: Message,
    syntax: str,
    renames: dict[str, str],
    package: str,
    indent: str = "",
    name: str | None = None,
) -> list[str]:
    message_name = name or _name(item.name, "RecoveredMessage")
    lines = [f"{indent}message {message_name} {{"]
    child_indent = f"{indent}  "
    message_names, enum_names = _declaration_names(item.messages, item.enums)
    for nested, nested_name in zip(item.messages, message_names, strict=True):
        lines.extend(
            _message(nested, syntax, renames, package, child_indent, nested_name)
        )
    enum_scope = {*message_names, *enum_names}
    for enum, enum_name in zip(item.enums, enum_names, strict=True):
        lines.extend(_enum(enum, syntax, child_indent, enum_scope, enum_name))
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
            lines.append(
                _field(field, syntax, child_indent, field_name, renames, package)
            )
    for group in sorted(grouped):
        if group is None:
            continue
        lines.append(f"{child_indent}oneof {group_names[group]} {{")
        for field, field_name in zip(item.fields, field_names, strict=True):
            if field.oneof == group:
                lines.append(
                    _field(
                        field,
                        syntax,
                        f"{child_indent}  ",
                        field_name,
                        renames,
                        package,
                    )
                )
        lines.append(f"{child_indent}}}")
    lines.append(f"{indent}}}")
    return lines


def _declared_types(
    message: Message, prefix: str = "", name: str | None = None
) -> set[str]:
    message_name = name or _name(message.name, "RecoveredMessage")
    qualified = f"{prefix}.{message_name}" if prefix else message_name
    declared = {qualified}
    message_names, enum_names = _declaration_names(message.messages, message.enums)
    declared.update(f"{qualified}.{name}" for name in enum_names)
    for nested, name in zip(message.messages, message_names, strict=True):
        declared.update(_declared_types(nested, qualified, name))
    return declared


def emit_proto(schema: RecoveredSchema) -> str:
    lines = [f'syntax = "{schema.syntax}";', ""]
    if schema.package:
        lines.extend((f"package {_qualified_name(schema.package)};", ""))
    for dependency in schema.dependencies:
        lines.append(f"import {json.dumps(dependency)};")
    if schema.dependencies:
        lines.append("")
    message_names, enum_names = _declaration_names(schema.messages, schema.enums)
    renames = _symbol_renames(schema.messages, schema.enums)
    enum_scope = {*message_names, *enum_names}
    for enum, enum_name in zip(schema.enums, enum_names, strict=True):
        lines.extend(_enum(enum, schema.syntax, "", enum_scope, enum_name))
        lines.append("")
    for message, message_name in zip(schema.messages, message_names, strict=True):
        lines.extend(
            _message(
                message,
                schema.syntax,
                renames,
                schema.package,
                name=message_name,
            )
        )
        lines.append("")
    declared = set(enum_names)
    for message, message_name in zip(schema.messages, message_names, strict=True):
        declared.update(_declared_types(message, name=message_name))
    referenced: set[str] = set()
    pending = list(schema.messages)
    while pending:
        message = pending.pop()
        pending.extend(message.messages)
        for field in message.fields:
            emitted_type = _resolved_type(field.type_name, renames, schema.package)
            if emitted_type not in _SCALARS and not emitted_type.startswith(
                (".", "map<")
            ):
                referenced.add(emitted_type)
    for missing in sorted(referenced - declared):
        lines.extend((f"message {_name(missing, 'RecoveredType')} {{}}", ""))
    return "\n".join(lines).rstrip() + "\n"
