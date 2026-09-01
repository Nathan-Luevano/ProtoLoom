#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
work=$(mktemp -d "${TMPDIR:-/tmp}/protoloom-path2-cpp.XXXXXX")
trap 'rm -rf "$work"' EXIT HUP INT TERM

for command in c++ cmp pkg-config protoc uv; do
  command -v "$command" >/dev/null 2>&1 || {
    echo "missing required command: $command" >&2
    exit 2
  }
done
pkg-config --exists protobuf-lite || {
  echo "missing protobuf-lite development package" >&2
  exit 2
}

source_root="$root/benchmarks/corpora/tier-a-small/source"
for variant in a b; do
  generated="$work/$variant"
  mkdir -p "$generated"
  protoc -I "$source_root/cpp-lite-$variant" --cpp_out="$generated" \
    "$source_root/cpp-lite-$variant/schema.proto"
  c++ -std=c++17 -O2 -flto -ffunction-sections -fdata-sections \
    -ffile-prefix-map="$generated=/generated" -I"$generated" \
    "$source_root/path2_main.cc" "$generated/schema.pb.cc" \
    $(pkg-config --cflags --libs protobuf-lite) -pthread \
    -Wl,--gc-sections -Wl,--build-id=none -s -o "$generated/path2"
done

cmp "$work/a/path2" "$work/b/path2"
cd "$root"
uv run python - "$work/a/path2" "$work/b/path2" <<'PY'
import sys
from pathlib import Path

from protoloom.extract.descriptor import scan_descriptors

for value in sys.argv[1:]:
    findings = scan_descriptors(Path(value).read_bytes())
    if findings:
        raise SystemExit(f"expected no descriptor in {value}, found {len(findings)}")
print("embedded_descriptors: 0/2")
print("distinct_source_names_same_binary: 2/2")
PY
