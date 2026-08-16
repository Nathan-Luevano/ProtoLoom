#!/bin/sh
set -eu

[ "$#" -eq 2 ] || {
  echo "usage: $0 INPUT OUTPUT_DIR" >&2
  exit 64
}

command -v uvx >/dev/null 2>&1 || {
  echo "uvx is required" >&2
  exit 69
}

exec uvx --from pbtk==1.1.2 pbtk-jar-extract "$1" "$2"
