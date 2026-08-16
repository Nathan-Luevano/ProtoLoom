#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
work=$(mktemp -d "${TMPDIR:-/tmp}/protoloom-path1.XXXXXX")
trap 'rm -rf "$work"' EXIT HUP INT TERM

for command in c++ pkg-config protoc uv; do
  command -v "$command" >/dev/null 2>&1 || {
    echo "missing required command: $command" >&2
    exit 2
  }
done

source_root="$root/benchmarks/corpora/tier-a-small/source"
protoc -I "$source_root" \
  --cpp_out="$work" \
  --descriptor_set_out="$work/truth.desc" \
  "$source_root/path1.proto"
cp "$source_root/path1_main.cc" "$work/main.cc"
c++ -std=c++17 -O2 -s -I"$work" \
  "$work/main.cc" "$work/path1.pb.cc" \
  $(pkg-config --cflags --libs protobuf) \
  -Wl,-rpath,"$(pkg-config --variable=libdir protobuf)" \
  -o "$work/path1"

cd "$root"
uv run protoloom extract "$work/path1" --output "$work/out"
uv run python - "$work" <<'PY'
import sys
from pathlib import Path

from google.protobuf.descriptor_pb2 import FileDescriptorSet

from protoloom.extract.descriptor import scan_descriptors

root = Path(sys.argv[1])
embedded = scan_descriptors((root / "path1").read_bytes())
if len(embedded) != 1:
    raise SystemExit(f"expected one embedded descriptor, found {len(embedded)}")
expected = embedded[0].descriptor.SerializeToString()
recovered = FileDescriptorSet.FromString((root / "out" / "path1.desc").read_bytes())
if len(recovered.file) != 1:
    raise SystemExit(f"expected one recovered descriptor, found {len(recovered.file)}")
actual = recovered.file[0].SerializeToString()
if actual != expected:
    raise SystemExit("recovered descriptor is not byte-identical to the binary")
print(f"embedded_descriptor_identity: 100.00% ({len(actual)}/{len(expected)} bytes)")
PY
protoc -I "$work/out" --descriptor_set_out="$work/recompiled.desc" \
  "$work/out/path1.proto"
echo "compile_rate: 100.00% (1/1)"
