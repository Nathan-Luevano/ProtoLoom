#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
work=$(mktemp -d "${TMPDIR:-/tmp}/protoloom-lite.XXXXXX")
if [ "${KEEP_WORK:-0}" = 1 ]; then
  printf 'work directory: %s\n' "$work"
else
  trap 'rm -rf "$work"' EXIT HUP INT TERM
fi

r8_version=8.3.37
r8_sha=59753e70a74f918389cc87f1b7d66b5c0862932559167425708ded159e3de439
matrix='21.12|3a4c1e5f2516c639d3079b1586e703fc7bcfa2136d58bda24d1d54f949c315e8|3.21.12|cf678ab3fbc992f4b4654a86c03557c21904d635202b67688fcb2eca442c2d40
29.3|3e866620c5be27664f3d2fa2d656b5f3e09b5152b42f1bedbf427b333e90021a|4.29.3|ace40fd343d182a7c3719b2cdf3b762ab8f86c491a21b597f7de906e799c80ff
35.1|6930ebf62bd4ea607b98fff052596c6ee564b9835b4ce172c75a3f53ae9d91b7|4.35.1|45d3769189888e491ab7d58125f2014c2a86fb8104c7aa3878c723943fce7a14'

for command in curl java javac unzip uv; do
  command -v "$command" >/dev/null 2>&1 || {
    echo "missing required command: $command" >&2
    exit 2
  }
done

mkdir -p "$work/tools"
curl --fail --location --retry 3 \
  "https://dl.google.com/dl/android/maven2/com/android/tools/r8/$r8_version/r8-$r8_version.jar" \
  -o "$work/tools/r8.jar"
printf '%s  %s\n' "$r8_sha" "$work/tools/r8.jar" | sha256sum --check --strict

source_proto="$root/benchmarks/corpora/tier-a-small/source/matrix.proto"
protoc_cache="$root/.protoc"
mkdir -p "$protoc_cache"
cd "$root"
printf '%s\n' "$matrix" | while IFS='|' read -r protoc_version protoc_sha protobuf_version protobuf_sha; do
  version_root="$work/$protobuf_version"
  mkdir -p "$version_root/generated" "$version_root/classes" \
    "$version_root/dex" "$version_root/out"
  cached_protoc="$protoc_cache/$protoc_version"
  cached_archive="$cached_protoc/protoc.zip"
  if [ ! -f "$cached_archive" ]; then
    mkdir -p "$cached_protoc"
    download="$cached_archive.part"
    curl --fail --location --retry 3 \
      "https://github.com/protocolbuffers/protobuf/releases/download/v$protoc_version/protoc-$protoc_version-linux-x86_64.zip" \
      -o "$download"
    printf '%s  %s\n' "$protoc_sha" "$download" \
      | sha256sum --check --strict
    mv "$download" "$cached_archive"
  fi
  printf '%s  %s\n' "$protoc_sha" "$cached_archive" \
    | sha256sum --check --strict
  if [ ! -x "$cached_protoc/bin/protoc" ]; then
    unzip -q "$cached_archive" -d "$cached_protoc"
  fi
  curl --fail --location --retry 3 \
    "https://repo1.maven.org/maven2/com/google/protobuf/protobuf-javalite/$protobuf_version/protobuf-javalite-$protobuf_version.jar" \
    -o "$version_root/protobuf-javalite.jar"
  printf '%s  %s\n' "$protobuf_sha" "$version_root/protobuf-javalite.jar" \
    | sha256sum --check --strict
  "$cached_protoc/bin/protoc" -I "$(dirname "$source_proto")" \
    --java_out="lite:$version_root/generated" \
    --descriptor_set_out="$version_root/truth.desc" \
    "$source_proto"
  javac -cp "$version_root/protobuf-javalite.jar" \
    -d "$version_root/classes" "$version_root/generated/matrix/MatrixProto.java"
  java -cp "$work/tools/r8.jar" com.android.tools.r8.D8 \
    --min-api 21 --output "$version_root/dex" \
    "$version_root/classes"/matrix/*.class "$version_root/protobuf-javalite.jar"
  uv run protoloom extract "$version_root/dex/classes.dex" \
    --allow-heuristic-lite \
    --output "$version_root/out"
  uv run python scripts/diagnose_real_app.py \
    --truth "$version_root/truth.desc" \
    --file matrix.proto \
    --recovered "$version_root/out/classes.desc" \
    --package matrix \
    --target "javalite-$protobuf_version-d8-$r8_version"
  uv run python scripts/check_matrix_roundtrip.py \
    --truth "$version_root/truth.desc" \
    --recovered "$version_root/out/classes.desc" \
    --package matrix
done

latest="$work/4.35.1"
for mode in default aggressive; do
  r8_root="$work/r8-$mode"
  mkdir -p "$r8_root/dex" "$r8_root/out"
  java -cp "$work/tools/r8.jar" com.android.tools.r8.R8 \
    --release --min-api 21 \
    --lib "${JAVA_HOME:?JAVA_HOME must point at a JDK}" \
    --output "$r8_root/dex" \
    --pg-conf "$root/benchmarks/corpora/tier-a-small/source/r8-$mode.pro" \
    --pg-map-output "$r8_root/mapping.txt" \
    "$latest/classes"/matrix/*.class "$latest/protobuf-javalite.jar"
  uv run protoloom extract "$r8_root/dex/classes.dex" \
    --allow-heuristic-lite --output "$r8_root/out"
  package=matrix
  [ "$mode" = aggressive ] && package=r
  uv run python scripts/diagnose_real_app.py \
    --truth "$latest/truth.desc" \
    --file matrix.proto \
    --recovered "$r8_root/out/classes.desc" \
    --package "$package" \
    --target "javalite-4.35.1-r8-$r8_version-$mode"
  uv run python scripts/check_matrix_roundtrip.py \
    --truth "$latest/truth.desc" \
    --recovered "$r8_root/out/classes.desc" \
    --package "$package"
done
