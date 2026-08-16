#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "usage: $0 CORPUS_DIR RESULTS_DIR" >&2
  echo "PBTK_BIN must be an adapter accepting INPUT OUTPUT_DIR" >&2
  exit 64
}

[ "$#" -eq 2 ] || usage

corpus_dir=$1
results_dir=$2
protoloom_bin=${PROTOLOOM_BIN:-protoloom}
pbtk_bin=${PBTK_BIN:-}
timeout_seconds=${TIMEOUT_SECONDS:-300}

[ -d "$corpus_dir" ] || {
  echo "corpus directory not found: $corpus_dir" >&2
  exit 66
}
[ -n "$pbtk_bin" ] || {
  echo "PBTK_BIN is required; point it at a pinned pbtk adapter" >&2
  exit 64
}
[ ! -e "$results_dir" ] || {
  echo "results path already exists: $results_dir" >&2
  exit 73
}
command -v "$protoloom_bin" >/dev/null 2>&1 || {
  echo "PROTOLOOM_BIN is not executable: $protoloom_bin" >&2
  exit 69
}
command -v "$pbtk_bin" >/dev/null 2>&1 || {
  echo "PBTK_BIN is not executable: $pbtk_bin" >&2
  exit 69
}
command -v timeout >/dev/null 2>&1 || {
  echo "GNU timeout is required" >&2
  exit 69
}

mkdir -p "$results_dir/runs"
manifest=$results_dir/results.tsv
printf 'id\tinput\tsha256\tprotoloom_status\tpbtk_status\n' >"$manifest"

find "$corpus_dir" -type f \( \
  -name '*.apk' -o -name '*.aab' -o -name '*.dex' -o -name '*.jar' \
  -o -name '*.so' -o -name '*.elf' -o -name '*.exe' \
\) -print0 | sort -z | while IFS= read -r -d '' artifact; do
  digest=$(sha256sum "$artifact" | cut -d ' ' -f 1)
  case_id=$(printf '%s' "$digest" | cut -c 1-16)
  run_dir=$results_dir/runs/$case_id
  protoloom_out=$run_dir/protoloom
  pbtk_out=$run_dir/pbtk
  mkdir -p "$protoloom_out" "$pbtk_out"

  if timeout "$timeout_seconds" "$protoloom_bin" extract \
    --output "$protoloom_out" "$artifact" >"$run_dir/protoloom.log" 2>&1; then
    protoloom_status=0
  else
    protoloom_status=$?
  fi
  if timeout "$timeout_seconds" "$pbtk_bin" \
    "$artifact" "$pbtk_out" >"$run_dir/pbtk.log" 2>&1; then
    pbtk_status=0
  else
    pbtk_status=$?
  fi

  relative=${artifact#"$corpus_dir"/}
  printf '%s\t%s\t%s\t%s\t%s\n' \
    "$case_id" "$relative" "$digest" "$protoloom_status" "$pbtk_status" \
    >>"$manifest"
done

cases=$(awk 'NR > 1 { count++ } END { print count + 0 }' "$manifest")
protoloom_ok=$(awk -F '\t' 'NR > 1 && $4 == 0 { count++ } END { print count + 0 }' "$manifest")
pbtk_ok=$(awk -F '\t' 'NR > 1 && $5 == 0 { count++ } END { print count + 0 }' "$manifest")
printf 'cases=%s protoloom_success=%s pbtk_success=%s\n' \
  "$cases" "$protoloom_ok" "$pbtk_ok"
printf 'raw comparison: %s\n' "$manifest"
