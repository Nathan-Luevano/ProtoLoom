import argparse
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from google.protobuf.descriptor_pb2 import (
    DescriptorProto,
    EnumDescriptorProto,
    FieldDescriptorProto,
    FileDescriptorProto,
    FileDescriptorSet,
)

from protoloom.bench.upstream import (
    materialize_source,
    sha256,
    validate_source_manifest,
)
from protoloom.decode.descpb import decode_file_descriptor
from protoloom.emit.proto import emit_proto
from protoloom.extract.descriptor import scan_descriptors

_TYPES = {
    FieldDescriptorProto.TYPE_DOUBLE: "double",
    FieldDescriptorProto.TYPE_FLOAT: "float",
    FieldDescriptorProto.TYPE_INT64: "int64",
    FieldDescriptorProto.TYPE_UINT64: "uint64",
    FieldDescriptorProto.TYPE_INT32: "int32",
    FieldDescriptorProto.TYPE_FIXED64: "fixed64",
    FieldDescriptorProto.TYPE_FIXED32: "fixed32",
    FieldDescriptorProto.TYPE_BOOL: "bool",
    FieldDescriptorProto.TYPE_STRING: "string",
    FieldDescriptorProto.TYPE_GROUP: "group",
    FieldDescriptorProto.TYPE_MESSAGE: "message",
    FieldDescriptorProto.TYPE_BYTES: "bytes",
    FieldDescriptorProto.TYPE_UINT32: "uint32",
    FieldDescriptorProto.TYPE_ENUM: "enum",
    FieldDescriptorProto.TYPE_SFIXED32: "sfixed32",
    FieldDescriptorProto.TYPE_SFIXED64: "sfixed64",
    FieldDescriptorProto.TYPE_SINT32: "sint32",
    FieldDescriptorProto.TYPE_SINT64: "sint64",
}

_WIRES = {
    "double": 1,
    "float": 5,
    "int64": 0,
    "uint64": 0,
    "int32": 0,
    "fixed64": 1,
    "fixed32": 5,
    "bool": 0,
    "string": 2,
    "group": 3,
    "message": 2,
    "bytes": 2,
    "uint32": 0,
    "enum": 0,
    "sfixed32": 5,
    "sfixed64": 1,
    "sint32": 0,
    "sint64": 0,
}

_LABELS = {
    FieldDescriptorProto.LABEL_OPTIONAL: "optional",
    FieldDescriptorProto.LABEL_REQUIRED: "required",
    FieldDescriptorProto.LABEL_REPEATED: "repeated",
}


def _enum(value: EnumDescriptorProto, prefix: str) -> dict[str, Any]:
    return {
        "name": f"{prefix}.{value.name}" if prefix else value.name,
        "values": [[item.name, item.number] for item in value.value],
    }


def _messages(
    value: DescriptorProto, prefix: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    name = f"{prefix}.{value.name}" if prefix else value.name
    fields = []
    for field in value.field:
        proto_type = _TYPES[field.type]
        oneof = (
            value.oneof_decl[field.oneof_index].name
            if field.HasField("oneof_index")
            else None
        )
        fields.append(
            {
                "number": field.number,
                "name": field.name,
                "proto_type": proto_type,
                "wire_type": _WIRES[proto_type],
                "label": _LABELS[field.label],
                **({"oneof": oneof} if oneof is not None else {}),
            }
        )
    messages = [{"name": name, "fields": fields}]
    enums = [_enum(item, name) for item in value.enum_type]
    for nested in value.nested_type:
        nested_messages, nested_enums = _messages(nested, name)
        messages.extend(nested_messages)
        enums.extend(nested_enums)
    return messages, enums


def _schema(descriptor: FileDescriptorProto) -> dict[str, Any]:
    messages: list[dict[str, Any]] = []
    enums = [_enum(item, descriptor.package) for item in descriptor.enum_type]
    for message in descriptor.message_type:
        nested_messages, nested_enums = _messages(message, descriptor.package)
        messages.extend(nested_messages)
        enums.extend(nested_enums)
    return {
        "messages": messages,
        "enums": enums,
        "compiled": True,
        "round_trip": {"passed": 0, "total": 0},
        "type_fidelity_ambiguities": [],
    }


def _compile(
    protoc: Path, includes: list[Path], proto: str, output: Path
) -> FileDescriptorProto:
    subprocess.run(
        [
            str(protoc),
            *(f"--proto_path={include}" for include in includes),
            f"--descriptor_set_out={output}",
            "--include_imports",
            proto,
        ],
        check=True,
    )
    files = FileDescriptorSet.FromString(output.read_bytes()).file
    descriptor = next((item for item in files if item.name == proto), None)
    if descriptor is None:
        raise ValueError(f"protoc did not emit selected root {proto}")
    return descriptor


def _compile_recovered(
    protoc: Path,
    includes: list[Path],
    proto: str,
    descriptor: FileDescriptorProto,
    root: Path,
) -> None:
    source = root / proto
    source.parent.mkdir(parents=True, exist_ok=True)
    schema = decode_file_descriptor(descriptor, "tier-a-upstream", "embedded")
    source.write_text(emit_proto(schema), encoding="utf-8")
    subprocess.run(
        [
            str(protoc),
            f"--proto_path={root}",
            *(f"--proto_path={include}" for include in includes),
            f"--descriptor_set_out={root / 'recompiled.desc'}",
            proto,
        ],
        check=True,
    )


def _compile_cpp_object(
    protoc: Path,
    cxx: Path,
    protobuf_include: Path,
    includes: list[Path],
    proto: str,
    descriptor: FileDescriptorProto,
    root: Path,
) -> bool:
    generated = root / "generated"
    generated.mkdir(parents=True)
    subprocess.run(
        [
            str(protoc),
            *(f"--proto_path={include}" for include in includes),
            f"--cpp_out={generated}",
            proto,
        ],
        check=True,
    )
    source = generated / Path(proto).with_suffix(".pb.cc")
    output = root / "generated.o"
    subprocess.run(
        [
            str(cxx),
            "-std=c++17",
            "-O2",
            f"-I{generated}",
            f"-I{protobuf_include}",
            "-c",
            str(source),
            "-o",
            str(output),
        ],
        check=True,
    )
    recovered = next(
        (
            item.descriptor
            for item in scan_descriptors(output.read_bytes(), "cpp-object")
            if item.descriptor.name == proto
        ),
        None,
    )
    return (
        recovered is not None
        and recovered.SerializeToString() == descriptor.SerializeToString()
    )


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--sources",
        type=Path,
        default=Path("benchmarks/corpus/tier-a-upstream-sources.json"),
    )
    parser.add_argument(
        "--output", type=Path, default=Path("benchmarks/corpora/tier-a-upstream")
    )
    parser.add_argument("--cache", type=Path, default=Path(".cache/tier-a-upstream"))
    parser.add_argument("--protoc", type=Path, default=Path("protoc"))
    parser.add_argument("--cxx", type=Path, default=Path("c++"))
    parser.add_argument("--protobuf-include", type=Path)
    args = parser.parse_args()
    manifest = validate_source_manifest(
        json.loads(args.sources.read_text(encoding="utf-8"))
    )
    args.cache.mkdir(parents=True, exist_ok=True)
    args.output.mkdir(parents=True, exist_ok=True)
    protobuf_include = args.protobuf_include or args.protoc.parent.parent / "include"
    targets = []
    measurements = []
    with tempfile.TemporaryDirectory(prefix="protoloom-tier-a-") as temporary:
        extraction_root = Path(temporary)
        for source in manifest["sources"]:
            root = materialize_source(
                source, args.cache, extraction_root / source["name"]
            )
            includes = [root / item for item in source["includes"]]
            for target in source["targets"]:
                target_root = args.output / target["name"]
                target_root.mkdir(parents=True, exist_ok=True)
                descriptor = _compile(
                    args.protoc, includes, target["proto"], target_root / "truth.desc"
                )
                encoded = descriptor.SerializeToString()
                carrier = b"protoloom-tier-a\x00" + encoded + b"\xff"
                findings = scan_descriptors(carrier, target["name"])
                recovered = next(
                    (
                        item.descriptor
                        for item in findings
                        if item.descriptor.name == target["proto"]
                    ),
                    None,
                )
                if recovered is None:
                    raise ValueError(f"failed to recover {target['proto']}")
                _compile_recovered(
                    args.protoc,
                    includes,
                    target["proto"],
                    recovered,
                    extraction_root / "recovered" / target["name"],
                )
                truth_path = target_root / "truth.json"
                recovered_path = target_root / "recovered.json"
                _write_json(truth_path, _schema(descriptor))
                _write_json(recovered_path, _schema(recovered))
                exact = recovered.SerializeToString() == encoded
                compiled_object_exact = None
                if target.get("compiled_leg") == "cpp-object":
                    compiled_object_exact = _compile_cpp_object(
                        args.protoc,
                        args.cxx,
                        protobuf_include,
                        includes,
                        target["proto"],
                        descriptor,
                        extraction_root / "cpp" / target["name"],
                    )
                measurements.append(
                    {
                        "target": target["name"],
                        "source": source["name"],
                        "proto": target["proto"],
                        "descriptor_bytes": len(encoded),
                        "descriptor_exact": exact,
                        "recovered_proto_compiled": True,
                        "compiled_leg": target.get("compiled_leg"),
                        "compiled_object_exact": compiled_object_exact,
                    }
                )
                targets.append(
                    {
                        "name": target["name"],
                        "truth": {
                            "name": "truth.json",
                            "path": f"{target['name']}/truth.json",
                            "sha256": sha256(truth_path),
                        },
                        "recovered": {
                            "name": "recovered.json",
                            "path": f"{target['name']}/recovered.json",
                            "sha256": sha256(recovered_path),
                        },
                    }
                )
    _write_json(
        args.output / "manifest.json",
        {
            "name": manifest["name"],
            "matrix": {"runtime": ["embedded-descriptor"], "optimization": ["none"]},
            "targets": targets,
        },
    )
    _write_json(args.output / "measurements.json", measurements)
    for item in measurements:
        print(
            f"{item['target']}: {item['descriptor_bytes']} bytes; "
            f"exact={str(item['descriptor_exact']).lower()}"
        )


if __name__ == "__main__":
    main()
