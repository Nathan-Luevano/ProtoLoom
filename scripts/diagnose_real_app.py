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
    BenchmarkEnum,
    BenchmarkField,
    BenchmarkMessage,
    BenchmarkSchema,
    score_target,
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
    raw: DescriptorProto, prefix: str, parent: str | None = None
) -> list[BenchmarkMessage]:
    name = f"{prefix}.{raw.name}" if prefix else raw.name
    fields = []
    for field in raw.field:
        type_name = (
            field.type_name.lstrip(".") if field.type_name else _TYPE_NAMES[field.type]
        )
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
        messages.extend(_truth_messages(nested, name, name))
    return messages


def load_truth(path: Path, file_name: str) -> BenchmarkSchema:
    descriptor_set = FileDescriptorSet.FromString(path.read_bytes())
    descriptor = next(
        (item for item in descriptor_set.file if item.name == file_name), None
    )
    if descriptor is None:
        raise ValueError(f"descriptor set does not contain {file_name}")
    messages = []
    for message in descriptor.message_type:
        messages.extend(_truth_messages(message, descriptor.package))
    return BenchmarkSchema(
        tuple(messages),
        enums=tuple(_enum(item) for item in descriptor.enum_type),
    )


def _recovered_message(raw: dict[str, Any], package: str) -> BenchmarkMessage:
    fields = tuple(
        BenchmarkField(
            field["number"],
            field["name"],
            field["type_name"],
            _SCALAR_WIRES.get(field["type_name"], 2),
            field["label"],
            field["oneof"],
        )
        for field in raw["fields"]
    )
    return BenchmarkMessage(
        f"{package}.{raw['name']}" if package else raw["name"], fields
    )


def load_recovered(path: Path, package: str) -> BenchmarkSchema:
    document = json.loads(path.read_text(encoding="utf-8"))
    schemas = [item for item in document["schemas"] if item["package"] == package]
    messages = tuple(
        _recovered_message(message, schema["package"])
        for schema in schemas
        for message in schema["messages"]
    )
    return BenchmarkSchema(messages, compiled=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--truth", type=Path, required=True)
    parser.add_argument("--file", required=True)
    parser.add_argument("--recovered", type=Path, required=True)
    parser.add_argument("--package", required=True)
    parser.add_argument("--target", default="real-app")
    args = parser.parse_args()
    truth = load_truth(args.truth, args.file)
    recovered = load_recovered(args.recovered, args.package)
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


if __name__ == "__main__":
    main()
