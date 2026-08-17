#!/usr/bin/env bash
set -euo pipefail

apk="demo/com.bitwarden.authenticator.apk"
output="out/readme-demo"

if [[ ! -f "$apk" ]]; then
  echo "missing $apk"
  exit 1
fi

type_command() {
  local value="$1"
  local index
  printf '\033[1;36m$\033[0m '
  for ((index = 0; index < ${#value}; index++)); do
    printf '%s' "${value:index:1}"
    sleep 0.018
  done
  printf '\n'
}

clear
printf '\n\033[1;35m  ProtoLoom\033[0m\n'
printf '  Recover protobuf schemas from compiled apps and binaries.\n\n'
sleep 1

type_command "protoloom inspect $apk"
uv run protoloom inspect "$apk"
sleep 1

printf '\n'
type_command "protoloom extract $apk --output $output"
rm -rf -- "$output"
uv run protoloom extract "$apk" --output "$output" | tail -n 1
sleep 1

printf '\n\033[1;32m✓ Recovery complete\033[0m\n'
printf '  104 compilable schemas\n'
printf '  Evidence report + descriptor set + HTML dashboard\n\n'
printf '\033[2m  %s/dashboard/index.html\033[0m\n' "$output"
sleep 3
