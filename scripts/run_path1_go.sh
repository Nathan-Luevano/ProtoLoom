#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
work=$(mktemp -d "${TMPDIR:-/tmp}/protoloom-go.XXXXXX")
trap 'rm -rf "$work"' EXIT HUP INT TERM

for command in go protoc uv; do
  command -v "$command" >/dev/null 2>&1 || {
    echo "missing required command: $command" >&2
    exit 2
  }
done

mkdir -p "$work/bin" "$work/src" "$work/out"
cp -R "$root/benchmarks/corpora/tier-a-small/source/go/." "$work/src/"
GOBIN="$work/bin" go install google.golang.org/protobuf/cmd/protoc-gen-go@v1.36.6
PATH="$work/bin:$PATH" protoc -I "$work/src" \
  --go_out="$work/src" \
  --go_opt=module=example.com/protoloomfixture \
  --descriptor_set_out="$work/truth.desc" \
  "$work/src/schema.proto"
(cd "$work/src" && go mod download && go build -ldflags='-s -w' -o "$work/path1go" .)

cd "$root"
uv run protoloom extract "$work/path1go" --output "$work/out"
uv run python - "$work" <<'PY'
import sys
from pathlib import Path

from google.protobuf.descriptor_pb2 import FileDescriptorSet

from protoloom.extract.descriptor import scan_descriptors

root = Path(sys.argv[1])
embedded = scan_descriptors((root / "path1go").read_bytes())
if len(embedded) != 1:
    raise SystemExit(f"expected one embedded descriptor, found {len(embedded)}")
expected = embedded[0].descriptor.SerializeToString()
recovered = FileDescriptorSet.FromString((root / "out" / "path1go.desc").read_bytes())
actual = recovered.file[0].SerializeToString()
if actual != expected:
    raise SystemExit("recovered Go descriptor is not byte-identical")
print(f"embedded_descriptor_identity: 100.00% ({len(actual)}/{len(expected)} bytes)")
PY
protoc -I "$work/out" --descriptor_set_out="$work/recompiled.desc" \
  "$work/out/schema.proto"
echo "compile_rate: 100.00% (1/1)"
