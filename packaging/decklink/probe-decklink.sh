#!/usr/bin/env bash
set -euo pipefail

ffmpeg_bin="${FFMPEG_BIN:-/opt/scoresight-decklink/bin/ffmpeg}"
device="${DECKLINK_DEVICE:-DeckLink Mini Recorder HD}"

"$ffmpeg_bin" -hide_banner -f decklink -list_devices 1 -i dummy 2>&1 || true
echo
echo "Formats reported for: $device"
"$ffmpeg_bin" -hide_banner -f decklink -list_formats 1 -i "$device" 2>&1 || true

