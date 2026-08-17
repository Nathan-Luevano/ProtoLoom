#!/usr/bin/env bash
set -euo pipefail

cast="docs/assets/protoloom-demo.cast"
dashboard="docs/assets/protoloom-dashboard.png"
output="docs/assets/protoloom-demo.gif"
temporary="$(mktemp -d)"
trap 'rm -rf -- "$temporary"' EXIT

agg \
  --quiet \
  --theme github-dark \
  --font-size 18 \
  --fps-cap 15 \
  --idle-time-limit 1.5 \
  --last-frame-duration 3 \
  --cols 94 \
  --rows 20 \
  "$cast" "$temporary/terminal.gif"

ffmpeg -y \
  -ignore_loop 1 -i "$temporary/terminal.gif" \
  -loop 1 -t 5 -i "$dashboard" \
  -filter_complex \
  "[0:v]fps=15,scale=1040:529,pad=1040:700:0:85:color=#0d1117,setsar=1,format=yuv420p[a];[1:v]fps=15,scale=1040:700,setsar=1,format=yuv420p[b];[a][b]concat=n=2:v=1:a=0[v]" \
  -map "[v]" -an "$temporary/demo.mp4" \
  -loglevel error

ffmpeg -y \
  -i "$temporary/demo.mp4" \
  -vf \
  "fps=15,split[s0][s1];[s0]palettegen=max_colors=128:stats_mode=diff[p];[s1][p]paletteuse=dither=bayer:bayer_scale=3:diff_mode=rectangle" \
  -loop 0 "$output" \
  -loglevel error

printf 'rendered %s\n' "$output"
