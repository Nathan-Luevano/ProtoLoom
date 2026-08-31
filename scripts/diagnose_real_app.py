import argparse
import json
from pathlib import Path
from typing import Any

from google.protobuf.descriptor_pb2 import (
    DescriptorProto,
    EnumDescriptorProto,
    FieldDescriptorProto,
    FileDescriptorSet,
)

from protoloom.bench.metrics import (
    METRIC_NAMES,
    TYPE_FIDELITY_AMBIGUITIES,
    BenchmarkEnum,
    BenchmarkField,
    BenchmarkMessage,
    BenchmarkSchema,
    score_target,
    type_fidelity_ceiling,
)

_WIRE_TYPES = {
    FieldDescriptorProto.TYPE_DOUBLE: 1,
    FieldDescriptorProto.TYPE_FLOAT: 5,
    FieldDescriptorProto.TYPE_INT64: 0,
    FieldDescriptorProto.TYPE_UINT64: 0,
    FieldDescriptorProto.TYPE_INT32: 0,
    FieldDescriptorProto.TYPE_FIXED64: 1,
    FieldDescriptorProto.TYPE_FIXED32: 5,
    FieldDescriptorProto.TYPE_BOOL: 0,
    FieldDescriptorProto.TYPE_STRING: 2,
    FieldDescriptorProto.TYPE_GROUP: 3,
    FieldDescriptorProto.TYPE_MESSAGE: 2,
    FieldDescriptorProto.TYPE_BYTES: 2,
    FieldDescriptorProto.TYPE_UINT32: 0,
    FieldDescriptorProto.TYPE_ENUM: 0,
    FieldDescriptorProto.TYPE_SFIXED32: 5,
    FieldDescriptorProto.TYPE_SFIXED64: 1,
    FieldDescriptorProto.TYPE_SINT32: 0,
    FieldDescriptorProto.TYPE_SINT64: 0,
}
_SCALAR_WIRES = {
    "double": 1,
    "float": 5,
    "int64": 0,
    "uint64": 0,
    "int32": 0,
    "fixed64": 1,
    "fixed32": 5,
    "bool": 0,
    "string": 2,
    "bytes": 2,
    "uint32": 0,
    "sfixed32": 5,
    "sfixed64": 1,
    "sint32": 0,
    "sint64": 0,
}
_TYPE_NAMES = {
    value.number: value.name.removeprefix("TYPE_").lower()
    for value in FieldDescriptorProto.Type.DESCRIPTOR.values
}
_LABELS = {
    value.number: value.name.removeprefix("LABEL_").lower()
    for value in FieldDescriptorProto.Label.DESCRIPTOR.values
}


def _enum(raw: EnumDescriptorProto) -> BenchmarkEnum:
    return BenchmarkEnum(
        raw.name, tuple((value.name, value.number) for value in raw.value)
    )


def _truth_messages(
    raw: DescriptorProto,
    prefix: str,
    parent: str | None = None,
    package_map: tuple[str, str] | None = None,
) -> list[BenchmarkMessage]:
    name = f"{prefix}.{raw.name}" if prefix else raw.name
    fields = []
    for field in raw.field:
        type_name = _truth_field_type(raw, field, package_map)
        oneof = (
            raw.oneof_decl[field.oneof_index].name
            if field.HasField("oneof_index")
            else None
        )
        fields.append(
            BenchmarkField(
                field.number,
                field.name,
                type_name,
                _WIRE_TYPES[field.type],
                _LABELS[field.label],
                oneof,
            )
        )
    messages = [
        BenchmarkMessage(
            name,
            tuple(fields),
            parent,
            tuple(_enum(item) for item in raw.enum_type),
        )
    ]
    for nested in raw.nested_type:
        if not nested.options.map_entry:
            messages.extend(_truth_messages(nested, name, name, package_map))
    return messages


def _truth_field_type(
    raw: DescriptorProto,
    field: FieldDescriptorProto,
    package_map: tuple[str, str] | None = None,
) -> str:
    if field.type_name:
        target = field.type_name.rsplit(".", 1)[-1]
        entry = next(
            (
                item
                for item in raw.nested_type
                if item.name == target and item.options.map_entry
            ),
            None,
        )
        if entry is not None and len(entry.field) == 2:
            key = _truth_field_type(entry, entry.field[0], package_map)
            value = _truth_field_type(entry, entry.field[1], package_map)
            return f"map<{key}, {value}>"
        type_name = field.type_name.lstrip(".")
        if package_map is not None:
            source, target = package_map
            if not source or type_name == source or type_name.startswith(f"{source}."):
                suffix = type_name.removeprefix(source).lstrip(".")
                type_name = f"{target}.{suffix}" if target else suffix
        return type_name
    return _TYPE_NAMES[field.type]


def load_truth(
    path: Path, file_name: str, package_override: str | None = None
) -> BenchmarkSchema:
    descriptor_set = FileDescriptorSet.FromString(path.read_bytes())
    descriptor = next(
        (item for item in descriptor_set.file if item.name == file_name), None
    )
    if descriptor is None:
        raise ValueError(f"descriptor set does not contain {file_name}")
    package = descriptor.package if package_override is None else package_override
    package_map = (
        (descriptor.package, package) if package_override is not None else None
    )
    messages = []
    for message in descriptor.message_type:
        messages.extend(_truth_messages(message, package, package_map=package_map))
    return BenchmarkSchema(
        tuple(messages),
        enums=tuple(_enum(item) for item in descriptor.enum_type),
    )


def _recovered_message(
    raw: dict[str, Any], package: str, enum_names: set[str]
) -> BenchmarkMessage:
    local_enums = tuple(
        BenchmarkEnum(
            enum["name"],
            tuple((value["name"], value["number"]) for value in enum["values"]),
        )
        for enum in raw["enums"]
    )
    local_enum_names = {enum.name for enum in local_enums}
    fields = tuple(
        BenchmarkField(
            field["number"],
            field["name"],
            (
                f"{package}.{field['type_name']}"
                if field["type_name"] in enum_names
                else field["type_name"]
            ),
            0
            if field["type_name"] in enum_names | local_enum_names
            else _SCALAR_WIRES.get(field["type_name"], 2),
            field["label"],
            field["oneof"],
        )
        for field in raw["fields"]
    )
    return BenchmarkMessage(
        f"{package}.{raw['name']}" if package else raw["name"],
        fields,
        enums=local_enums,
    )


def load_recovered(
    path: Path, package: str, roots: set[str] | None = None
) -> BenchmarkSchema:
    if path.suffix == ".desc":
        descriptor_set = FileDescriptorSet()
        descriptor_set.ParseFromString(path.read_bytes())
        descriptor_messages: list[BenchmarkMessage] = []
        descriptor_enums: list[BenchmarkEnum] = []
        for descriptor in descriptor_set.file:
            if descriptor.package != package:
                continue
            for message in descriptor.message_type:
                if roots is not None and message.name not in roots:
                    continue
                descriptor_messages.extend(_truth_messages(message, descriptor.package))
            descriptor_enums.extend(_enum(item) for item in descriptor.enum_type)
        return BenchmarkSchema(
            tuple(descriptor_messages),
            enums=tuple(descriptor_enums),
            compiled=True,
        )
    document = json.loads(path.read_text(encoding="utf-8"))
    schemas = [item for item in document["schemas"] if item["package"] == package]
    enums = tuple(
        BenchmarkEnum(
            enum["name"],
            tuple((value["name"], value["number"]) for value in enum["values"]),
        )
        for schema in schemas
        for enum in schema["enums"]
    )
    enum_names = {enum.name for enum in enums}
    messages = tuple(
        _recovered_message(message, schema["package"], enum_names)
        for schema in schemas
        for message in schema["messages"]
        if roots is None or message["name"] in roots
    )
    return BenchmarkSchema(messages, enums=enums, compiled=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--truth", type=Path, required=True)
    parser.add_argument("--file", required=True)
    parser.add_argument("--recovered", type=Path, required=True)
    parser.add_argument("--package", required=True)
    parser.add_argument("--normalize-truth-package", action="store_true")
    parser.add_argument("--scope-to-truth-roots", action="store_true")
    parser.add_argument("--target", default="real-app")
    args = parser.parse_args()
    truth = load_truth(
        args.truth,
        args.file,
        args.package if args.normalize_truth_package else None,
    )
    roots = (
        {
            message.name.rsplit(".", 1)[-1]
            for message in truth.messages
            if message.name is not None and message.parent is None
        }
        if args.scope_to_truth_roots
        else None
    )
    recovered = load_recovered(args.recovered, args.package, roots)
    report = score_target(args.target, truth, recovered)
    print(f"truth messages: {len(truth.messages)}")
    print(f"recovered messages: {len(recovered.messages)}")
    for metric in METRIC_NAMES:
        score = report.scores[metric]
        if score.denominator:
            print(
                f"{metric}: {score.value:.2%} ({score.numerator}/{score.denominator})"
            )
        else:
            print(f"{metric}: n/a (0/0)")
    ceiling = type_fidelity_ceiling(truth, TYPE_FIDELITY_AMBIGUITIES)
    print(
        "type_fidelity_ceiling: "
        f"{ceiling.value:.2%} ({ceiling.numerator}/{ceiling.denominator})"
    )


if __name__ == "__main__":
    main()
