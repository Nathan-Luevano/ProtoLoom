#!/bin/sh
set -eu

zero=0000000000000000000000000000000000000000
if [ -n "${BASE_SHA:-}" ] && [ "$BASE_SHA" != "$zero" ] && git cat-file -e "$BASE_SHA^{commit}" 2>/dev/null; then
  range="$BASE_SHA..$HEAD_SHA"
else
  range="$HEAD_SHA"
fi

bad_authors=$(git log --format='%ae' "$range" | grep -Fvx "$ALLOWED_EMAIL" || true)
if [ -n "$bad_authors" ]; then
  echo "blocked: commits use an unexpected author email"
  echo "$bad_authors"
  exit 1
fi

if git log --format='%B' "$range" | grep -qiE '^(co-authored-by|generated (with|by)):'; then
  echo "blocked: commit history carries a foreign attribution trailer"
  exit 1
fi

if git log --format='%B' "$range" | grep -i '^signed-off-by:' | grep -qiv 'Nathan Luevano'; then
  echo "blocked: commit history carries a foreign attribution trailer"
  exit 1
fi

