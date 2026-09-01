#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
work=$(mktemp -d "${TMPDIR:-/tmp}/protoloom-path2-go.XXXXXX")
trap 'rm -rf "$work"' EXIT HUP INT TERM

for command in go protoc uv; do
  command -v "$command" >/dev/null 2>&1 || {
    echo "missing required command: $command" >&2
    exit 2
  }
done
go version | grep -F "go1.24." >/dev/null || {
  echo "Path-2 Go fixture requires Go 1.24.x" >&2
  exit 2
}

mkdir -p "$work/src" "$work/out"
cp -R "$root/benchmarks/corpora/tier-a-small/source/go-tags/." "$work/src/"
(cd "$work/src" && go mod download && \
  go build -trimpath -ldflags='-s -w' -o "$work/path2go" .)

cd "$root"
uv run python - "$work/path2go" <<'PY'
import sys
from pathlib import Path

from protoloom.extract.descriptor import scan_descriptors

findings = scan_descriptors(Path(sys.argv[1]).read_bytes())
if findings:
    raise SystemExit(f"expected no embedded descriptor, found {len(findings)}")
print("embedded_descriptors: 0")
PY
uv run protoloom extract "$work/path2go" --output "$work/out"
protoc -I "$work/out" --descriptor_set_out="$work/recompiled.desc" \
  "$work/out/Record.proto"
uv run python - "$work/recompiled.desc" <<'PY'
import sys
from pathlib import Path

from google.protobuf.descriptor_pb2 import FieldDescriptorProto, FileDescriptorSet

descriptor = FileDescriptorSet.FromString(Path(sys.argv[1]).read_bytes()).file[0]
message = descriptor.message_type[0]
actual = [
    (field.name, field.number, field.type, field.label, field.options.packed)
    for field in message.field
]
expected = [
    ("id", 1, FieldDescriptorProto.TYPE_UINT64, FieldDescriptorProto.LABEL_OPTIONAL, False),
    ("label", 2, FieldDescriptorProto.TYPE_STRING, FieldDescriptorProto.LABEL_OPTIONAL, False),
    ("samples", 3, FieldDescriptorProto.TYPE_SINT32, FieldDescriptorProto.LABEL_REPEATED, True),
]
if actual != expected:
    raise SystemExit(f"unexpected recovered fields: {actual}")
print("field_recall: 100.00% (3/3)")
print("type_fidelity: 100.00% (3/3)")
print("label_accuracy: 100.00% (3/3)")
PY
echo "compile_rate: 100.00% (1/1)"
