import json
import re

from protoloom.model import (
    Confidence,
    EnumType,
    Field,
    Message,
    RecoveredSchema,
    Service,
)

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
_CONFIDENCE_RANK = {
    Confidence.SPECULATIVE: 0,
    Confidence.MEDIUM: 1,
    Confidence.HIGH: 2,
    Confidence.CERTAIN: 3,
}
MAX_PROTO_ITEMS = 1_000_000
MAX_PROTO_DEPTH = 100
MAX_PROTO_OUTPUT_BYTES = 64 * 1024 * 1024


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
    next_suffix: dict[str, int] = {}
    result = []
    for value in values:
        base = _name(value, fallback)
        candidate = base
        if candidate in used:
            suffix = next_suffix.get(base, 2)
            while (candidate := f"{base}_{suffix}") in used:
                suffix += 1
            next_suffix[base] = suffix + 1
        else:
            next_suffix.setdefault(base, 2)
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
    raw_prefix: tuple[str, ...] = (),
    emitted_prefix: str = "",
) -> dict[tuple[str, ...], str]:
    # Keyed by the raw name *components* (not a "."-joined string): a raw
    # name can itself contain a literal "." (an adversarial or otherwise
    # unsanitized decoded name), and joining with "." would let a one-level
    # name collide with an unrelated multi-level nested path, silently
    # aliasing one declared symbol's rename onto another.
    message_names, enum_names = _declaration_names(messages, enums)
    result: dict[tuple[str, ...], str] = {}
    for item, name in zip(messages, message_names, strict=True):
        raw = (*raw_prefix, item.name)
        emitted = f"{emitted_prefix}.{name}" if emitted_prefix else name
        result[raw] = emitted
        result.update(_symbol_renames(item.messages, item.enums, raw, emitted))
    for enum, name in zip(enums, enum_names, strict=True):
        raw = (*raw_prefix, enum.name)
        result[raw] = f"{emitted_prefix}.{name}" if emitted_prefix else name
    return result


# protoc's field grammar reads a bare, non-absolute type reference whose
# first segment is one of these words as the start of that keyword's own
# statement (e.g. "message f = 1;" is parsed as an attempt to declare a
# nested message, not a field of type "message"), and fails with a parse
# error even though "message" is a perfectly legal message/enum name to
# declare. A leading "." always parses as an unambiguous absolute reference.
_STATEMENT_KEYWORDS = {
    "extend",
    "extensions",
    "group",
    "enum",
    "message",
    "oneof",
    "option",
    "reserved",
}


def _disambiguated(reference: str, package: str) -> str:
    if reference.startswith((".", "map<")):
        return reference
    head = reference.split(".", 1)[0]
    if head not in _STATEMENT_KEYWORDS:
        return reference
    return f".{_qualified_name(package)}.{reference}" if package else f".{reference}"


def _resolved_type(
    value: str, renames: dict[tuple[str, ...], str], package: str
) -> str:
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
    if (renamed := renames.get(tuple(local.split(".")))) is None:
        return _disambiguated(_type_name(value), package)
    if absolute and package:
        return f".{_qualified_name(package)}.{renamed}"
    return _disambiguated(f".{renamed}" if absolute else renamed, package)


def _deduplicated_fields(fields: list[Field]) -> list[Field]:
    result: dict[int, Field] = {}
    for item in fields:
        current = result.get(item.number)
        if (
            current is None
            or _CONFIDENCE_RANK[item.confidence] > _CONFIDENCE_RANK[current.confidence]
        ):
            result[item.number] = item
    return list(result.values())


def _fold_key(enum_name: str, value_name: str) -> str:
    # protoc rejects two enum values that collide once both are upper-cased,
    # stripped of underscores, and the (likewise folded) enum name is removed
    # as a common prefix -- verified against protoc 29.3/libprotoc 3.13:
    # `FOO_BAR` and `FOO_bar` in `enum Foo` both fold to `BAR` and fail to
    # compile even though they're distinct strings today's exact-match dedup
    # would let through.
    folded_enum = re.sub(r"_", "", enum_name.upper())
    folded_value = re.sub(r"_", "", value_name.upper())
    return folded_value.removeprefix(folded_enum)


def _defold_enum_values(
    enum_name: str, names: list[str], numbers: list[int], reserved: set[str]
) -> list[str]:
    seen: dict[str, int] = {}
    result = list(names)
    for index, (name, number) in enumerate(zip(names, numbers, strict=True)):
        key = _fold_key(enum_name, name)
        prior = seen.get(key)
        if prior is None:
            seen[key] = number
            continue
        if prior == number:
            continue  # same numeric value: protoc allows this as an alias
        suffix = 2
        candidate = f"{name}_{suffix}"
        while candidate in reserved or _fold_key(enum_name, candidate) in seen:
            suffix += 1
            candidate = f"{name}_{suffix}"
        reserved.add(candidate)
        result[index] = candidate
        seen[_fold_key(enum_name, candidate)] = number
    return result


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
    needs_synthetic_zero = not values or (syntax == "proto3" and values[0].number != 0)
    numbers = [value.number for value in values]
    if needs_synthetic_zero:
        numbers.append(0)
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
    value_names = _defold_enum_values(
        enum_name, value_names, [value.number for value in values], set(reserved)
    )
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
    renames: dict[tuple[str, ...], str],
    package: str,
) -> str:
    label = item.label
    if item.type_name.startswith("map<") or (
        item.oneof is not None and not item.proto3_optional
    ):
        label = ""
    elif syntax == "proto3":
        if label == "optional":
            label = "optional" if item.proto3_optional else ""
        elif label == "required":
            label = ""
    prefix = f"{label} " if label else ""
    options: list[str] = []
    default = _default_literal(item)
    if default is not None and syntax == "proto2":
        options.append(f"default = {default}")
    if item.packed is not None and label == "repeated":
        options.append(f"packed = {'true' if item.packed else 'false'}")
    suffix = f" [{', '.join(options)}]" if options else ""
    field_type = _resolved_type(item.type_name, renames, package)
    return f"{indent}{prefix}{field_type} {name} = {item.number}{suffix};"


def _group_message_target(item: Message, field: Field) -> Message | None:
    # protoc always declares a TYPE_GROUP field's message as a direct nested
    # type of its owner, named after the field's type_name's last segment.
    local = field.type_name.removeprefix(".").rsplit(".", 1)[-1]
    candidates = [nested for nested in item.messages if nested.name == local]
    return candidates[0] if len(candidates) == 1 else None


def _group_field(
    item: Message,
    field: Field,
    target: Message,
    syntax: str,
    indent: str,
    name: str,
    renames: dict[tuple[str, ...], str],
    package: str,
) -> list[str]:
    label = "" if syntax == "proto3" and field.label != "repeated" else field.label
    prefix = f"{label} " if label else ""
    group_name = _name(target.name, "RecoveredGroup")
    lines = [f"{indent}{prefix}group {group_name} = {field.number} {{"]
    lines.extend(_message_body(target, syntax, renames, package, f"{indent}  "))
    lines.append(f"{indent}}}")
    return lines


def _message_body(
    item: Message,
    syntax: str,
    renames: dict[tuple[str, ...], str],
    package: str,
    child_indent: str,
) -> list[str]:
    lines: list[str] = []
    message_names, enum_names = _declaration_names(item.messages, item.enums)
    fields = _deduplicated_fields(item.fields)
    inlined_groups: dict[int, Message] = {}
    for candidate in fields:
        if not candidate.is_group:
            continue
        target = _group_message_target(item, candidate)
        if target is not None:
            inlined_groups[id(target)] = target
    for nested, nested_name in zip(item.messages, message_names, strict=True):
        if id(nested) in inlined_groups:
            continue
        lines.extend(
            _message(nested, syntax, renames, package, child_indent, nested_name)
        )
    enum_scope = {*message_names, *enum_names}
    for enum, enum_name in zip(item.enums, enum_names, strict=True):
        lines.extend(_enum(enum, syntax, child_indent, enum_scope, enum_name))
    field_names = _unique_names([field.name for field in fields], "recovered_field")
    standalone: list[tuple[Field, str]] = []
    grouped_fields: dict[str, list[tuple[Field, str]]] = {}
    for field, field_name in zip(fields, field_names, strict=True):
        if field.oneof is None or field.proto3_optional:
            standalone.append((field, field_name))
        else:
            grouped_fields.setdefault(field.oneof, []).append((field, field_name))
    grouped = sorted(grouped_fields)
    group_names = dict(
        zip(
            grouped,
            _unique_names(grouped, "choice", set(field_names)),
            strict=True,
        )
    )
    for field, field_name in standalone:
        target = _group_message_target(item, field) if field.is_group else None
        if target is not None:
            lines.extend(
                _group_field(
                    item,
                    field,
                    target,
                    syntax,
                    child_indent,
                    field_name,
                    renames,
                    package,
                )
            )
        else:
            lines.append(
                _field(field, syntax, child_indent, field_name, renames, package)
            )
    for group in grouped:
        lines.append(f"{child_indent}oneof {group_names[group]} {{")
        for field, field_name in grouped_fields[group]:
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
    return lines


def _message(
    item: Message,
    syntax: str,
    renames: dict[tuple[str, ...], str],
    package: str,
    indent: str = "",
    name: str | None = None,
) -> list[str]:
    message_name = name or _name(item.name, "RecoveredMessage")
    lines = [f"{indent}message {message_name} {{"]
    child_indent = f"{indent}  "
    lines.extend(_message_body(item, syntax, renames, package, child_indent))
    lines.append(f"{indent}}}")
    return lines


def _service(
    item: Service,
    renames: dict[tuple[str, ...], str],
    package: str,
    name: str,
) -> list[str]:
    lines = [f"service {name} {{"]
    method_names = _unique_names([method.name for method in item.methods], "Method")
    for method, method_name in zip(item.methods, method_names, strict=True):
        input_type = _resolved_type(method.input_type, renames, package)
        output_type = _resolved_type(method.output_type, renames, package)
        client = "stream " if method.client_streaming else ""
        server = "stream " if method.server_streaming else ""
        lines.append(
            f"  rpc {method_name}({client}{input_type}) "
            f"returns ({server}{output_type}) {{}}"
        )
    lines.append("}")
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


def emit_proto(
    schema: RecoveredSchema,
    *,
    max_items: int = MAX_PROTO_ITEMS,
    max_depth: int = MAX_PROTO_DEPTH,
    max_bytes: int = MAX_PROTO_OUTPUT_BYTES,
) -> str:
    _validate_proto_budget(schema, max_items, max_depth, max_bytes)
    lines = [f'syntax = "{schema.syntax}";', ""]
    if schema.package:
        lines.extend((f"package {_qualified_name(schema.package)};", ""))
    for dependency in dict.fromkeys(schema.dependencies):
        lines.append(f"import {json.dumps(dependency)};")
    if schema.dependencies:
        lines.append("")
    message_names, enum_names = _declaration_names(schema.messages, schema.enums)
    service_names = _unique_names(
        [item.name for item in schema.services],
        "RecoveredService",
        {*message_names, *enum_names},
    )
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
    for service, service_name in zip(schema.services, service_names, strict=True):
        lines.extend(_service(service, renames, schema.package, service_name))
        lines.append("")
    declared = set(enum_names)
    for message, message_name in zip(schema.messages, message_names, strict=True):
        declared.update(_declared_types(message, name=message_name))
    referenced: set[str] = set()
    referenced_enums: set[str] = set()
    pending = list(schema.messages)
    while pending:
        message = pending.pop()
        pending.extend(message.messages)
        for field in _deduplicated_fields(message.fields):
            emitted_type = _resolved_type(field.type_name, renames, schema.package)
            if emitted_type not in _SCALARS and not emitted_type.startswith(
                (".", "map<")
            ):
                referenced.add(emitted_type)
                if field.type_is_enum:
                    referenced_enums.add(emitted_type)
    for service in schema.services:
        for method in service.methods:
            for raw_type in (method.input_type, method.output_type):
                emitted_type = _resolved_type(raw_type, renames, schema.package)
                if emitted_type not in _SCALARS and not emitted_type.startswith(
                    (".", "map<")
                ):
                    referenced.add(emitted_type)
    for missing in sorted(referenced - declared):
        # a field's Wire adapter can tell us it references an enum even
        # when the enum's own values couldn't be recovered - stub it as a
        # minimal enum, not a message, so the wire type (varint) still
        # matches the original data instead of silently flipping to
        # length-delimited.
        if missing in referenced_enums:
            lines.extend(
                (
                    *_enum(
                        EnumType(_name(missing, "RecoveredType")), schema.syntax, ""
                    ),
                    "",
                )
            )
        else:
            lines.extend((f"message {_name(missing, 'RecoveredType')} {{}}", ""))
    result = "\n".join(lines).rstrip() + "\n"
    if len(result.encode("utf-8")) > max_bytes:
        raise ValueError(f"proto output exceeds {max_bytes} bytes")
    return result


def _validate_proto_budget(
    schema: RecoveredSchema, max_items: int, max_depth: int, max_bytes: int
) -> None:
    if max_items <= 0 or max_depth <= 0 or max_bytes <= 0:
        raise ValueError("proto output limits must be positive")
    count = len(schema.dependencies) + len(schema.enums)
    count += sum(len(enum.values) for enum in schema.enums)
    count += len(schema.services) + sum(len(item.methods) for item in schema.services)
    if count > max_items:
        raise ValueError(f"proto output exceeds {max_items} items")
    pending = [(message, 1) for message in schema.messages]
    while pending:
        message, depth = pending.pop()
        if depth > max_depth:
            raise ValueError(f"proto output exceeds message depth {max_depth}")
        count += 1 + len(message.fields) + len(message.enums)
        count += sum(len(enum.values) for enum in message.enums)
        if count > max_items:
            raise ValueError(f"proto output exceeds {max_items} items")
        pending.extend((child, depth + 1) for child in message.messages)
    if count > max_items:
        raise ValueError(f"proto output exceeds {max_items} items")
